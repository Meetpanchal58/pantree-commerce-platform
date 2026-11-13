"""Auth routes for the Pantree storefront."""

from fastapi import APIRouter

from ..core import (
    ConsumeCodeExpiredUserInputCodeError,
    ConsumeCodeIncorrectUserInputCodeError,
    ConsumeCodeOkResult,
    ConsumeCodeRestartFlowError,
    Cookie,
    CreateNewCodeForDeviceOkResult,
    Form,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Request,
    SMTPException,
    SuperTokensError,
    TENANT_ID,
    _deliver_passwordless_code,
    _departments,
    _is_supertokens_core_error,
    _log_otp_delivery_failure,
    _normalize_login_identifier,
    base_ctx,
    database,
    db,
    forget_customer_activity,
    reset_customer_activity,
    delivery_channel_configured,
    ensure_auth_tables,
    get_session,
    GEO,
    httpx,
    json,
    log_event,
    logger,
    mirror_customer,
    now,
    os,
    passwordless_async,
    re,
    reserve_otp_request,
    session_async,
    sqlite3,
    time,
    tpl,
    uid,
    urlsplit,
)

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, msg: str = ""):
    return tpl.TemplateResponse(
        request,
        "login.html",
        {
            "msg": msg,
            "email_enabled": delivery_channel_configured("email"),
            "phone_enabled": delivery_channel_configured("phone"),
            "cities": CITY_CHOICES,
            "genders": GENDER_CHOICES,
            "age_brackets": AGE_BRACKETS,
            "heard_from_choices": HEARD_FROM_CHOICES,
        },
    )


ALLOWED_DEVICES = {"Desktop Web", "Android App", "iOS App", "Mobile Web"}
PENDING_SIGNUP_TTL_SECONDS = 30 * 60

# Sign-up form choices. Values match the generated warehouse so real and
# simulated customers can be analysed together.
GENDER_CHOICES = {"F": "Female", "M": "Male", "Other": "Other", "Prefer not to say": "Prefer not to say"}
AGE_BRACKETS = ["18-24", "25-34", "35-44", "45-54", "55+"]
HEARD_FROM_CHOICES = {
    "Organic": "Search engine (Google, Bing…)",
    "Social Ads": "Social media",
    "Referral": "A friend or family member",
    "Paid Search": "An online ad",
    "Email/CRM": "Email or newsletter",
    "Direct": "I typed the website address",
    "Other": "Other",
}
CITY_CHOICES = {
    city: (state, region)
    for region, states in GEO.items()
    for state, cities in states.items()
    for city in cities
}


class AccountNotFoundError(ValueError):
    """Sign-in for a contact that has no Pantree account."""


class AccountExistsError(ValueError):
    """Sign-up for a contact that already has a Pantree account."""


def _customers_with_contact(con, email=None, phone=None) -> set[str]:
    """Customer IDs whose stored email or phone matches (normalised)."""
    matches = set()
    if email:
        row = con.execute(
            "SELECT customer_id FROM customer_pii WHERE lower(email)=?", (email.strip().lower(),)
        ).fetchone()
        if row:
            matches.add(row[0])
    if phone:
        for customer_id, stored in con.execute(
            "SELECT customer_id, phone FROM customer_pii WHERE phone IS NOT NULL"
        ).fetchall():
            try:
                if _normalize_login_identifier(stored)[1] == phone:
                    matches.add(customer_id)
            except ValueError:
                continue
    return matches


def _channel_unavailable(channel):
    if channel == "phone" and delivery_channel_configured("email"):
        message = "Phone sign-in is unavailable here. Please use your email address."
    else:
        missing_provider = "SMTP" if channel == "email" else "OpenSMS"
        message = f"{missing_provider} delivery is not configured for this sign-in method."
    return JSONResponse({"ok": False, "message": message}, status_code=503)


async def _send_code(request, con, channel, normalized, precheck=None, after_create=None):
    """Rate-limit, run the account check, create a SuperTokens code and deliver it.

    The rate limit runs before the account check, so probing which emails have
    accounts is throttled like any other code request.
    """
    try:
        wait = reserve_otp_request(
            con,
            f"{channel}:{normalized}",
            request.client.host if request.client else None,
        )
        if wait is not None:
            return JSONResponse(
                {
                    "ok": False,
                    "message": f"Please wait {wait} seconds before requesting another code.",
                },
                status_code=429,
                headers={"Retry-After": str(wait)},
            )
        if precheck is not None:
            rejection = precheck()
            if rejection is not None:
                return rejection
        result = await passwordless_async.create_code(
            tenant_id=TENANT_ID,
            email=normalized if channel == "email" else None,
            phone_number=normalized if channel == "phone" else None,
        )
        await _deliver_passwordless_code(channel, normalized, result)
        if after_create is not None:
            after_create(result)
    except (
        SuperTokensError,
        OSError,
        RuntimeError,
        ValueError,
        SMTPException,
        httpx.HTTPError,
    ) as exc:
        _log_otp_delivery_failure("send", exc)
        return JSONResponse(
            {
                "ok": False,
                "message": "Could not send a sign-in code. Please try again later.",
            },
            status_code=503,
        )
    except Exception as exc:
        if not _is_supertokens_core_error(exc):
            raise
        _log_otp_delivery_failure("send", exc)
        return JSONResponse(
            {
                "ok": False,
                "message": "Sign-in service is temporarily unavailable. Please try again later.",
            },
            status_code=503,
        )

    return JSONResponse(
        {
            "ok": True,
            "device_id": result.device_id,
            "pre_auth_session_id": result.pre_auth_session_id,
            "channel": channel,
            "message": "A sign-in code was sent if the delivery provider accepted the request.",
            "expires_in": max(1, int(result.code_life_time / 1000)),
        }
    )


@router.post("/login/otp/request")
async def login_otp_request(
    request: Request,
    identifier: str = Form(...),
    device: str = Form("Desktop Web"),
):
    """Send a sign-in code to an existing account. Never creates accounts."""
    try:
        channel, normalized = _normalize_login_identifier(identifier)
    except ValueError as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=422)
    if device not in ALLOWED_DEVICES:
        return JSONResponse(
            {"ok": False, "message": "Choose a valid device type."}, status_code=422
        )
    if not delivery_channel_configured(channel):
        return _channel_unavailable(channel)

    con = db()
    try:
        ensure_auth_tables(con)

        def account_must_exist():
            found = _customers_with_contact(
                con,
                email=normalized if channel == "email" else None,
                phone=normalized if channel == "phone" else None,
            )
            if found:
                return None
            what = "email address" if channel == "email" else "mobile number"
            return JSONResponse(
                {
                    "ok": False,
                    "code": "account_not_found",
                    "message": f"No account found for this {what}. Create an account first.",
                },
                status_code=404,
            )

        return await _send_code(request, con, channel, normalized, precheck=account_must_exist)
    finally:
        con.close()


def _validate_signup(form: dict) -> tuple[dict, list[str]]:
    """Clean the sign-up form; return (details, errors)."""
    errors = []
    first_name = (form.get("first_name") or "").strip()
    last_name = (form.get("last_name") or "").strip()
    if not first_name or len(first_name) > 60:
        errors.append("Enter your first name.")
    if len(last_name) > 60:
        errors.append("Last name is too long.")
    email = None
    try:
        kind, email = _normalize_login_identifier(form.get("email") or "")
        if kind != "email":
            raise ValueError
    except ValueError:
        errors.append("Enter a valid email address.")
        email = None
    phone = None
    if (form.get("phone") or "").strip():
        try:
            kind, phone = _normalize_login_identifier(form["phone"])
            if kind != "phone":
                raise ValueError
        except ValueError:
            errors.append("Enter a valid mobile number, or leave it empty.")
            phone = None
    city = form.get("city") or ""
    if city not in CITY_CHOICES and city != "Other":
        errors.append("Choose your city.")
    gender = form.get("gender") or "Prefer not to say"
    if gender not in GENDER_CHOICES:
        errors.append("Choose a valid gender option.")
    age_bracket = form.get("age_bracket") or None
    if age_bracket and age_bracket not in AGE_BRACKETS:
        errors.append("Choose a valid age range.")
    heard_from = form.get("heard_from") or None
    if heard_from and heard_from not in HEARD_FROM_CHOICES:
        errors.append("Choose a valid option for how you heard about us.")
    if not form.get("accept_terms"):
        errors.append("Please accept the terms and privacy policy.")
    state, region = CITY_CHOICES.get(city, (None, None))
    details = {
        "first_name": first_name,
        "last_name": last_name,
        "email": email,
        "phone": phone,
        "signup_city": city,
        "signup_state": state,
        "signup_region": region,
        "gender": gender,
        "age_bracket": age_bracket,
        "acquisition_channel": heard_from or "Unknown",
        "email_opt_in": bool(form.get("email_opt_in")),
        "sms_opt_in": bool(form.get("sms_opt_in")),
    }
    return details, errors


@router.post("/signup/otp/request")
async def signup_otp_request(request: Request):
    """Start sign-up: validate details, reject existing contacts, send a code.

    Details are kept server-side in pending_signups until the code is verified;
    only then are the customer rows created.
    """
    form = await request.form()
    details, errors = _validate_signup(dict(form))
    device = form.get("device") or "Desktop Web"
    if device not in ALLOWED_DEVICES:
        errors.append("Choose a valid device type.")
    if errors:
        return JSONResponse({"ok": False, "message": " ".join(errors), "errors": errors}, status_code=422)

    # Verify the email when email delivery works; otherwise the mobile number.
    if delivery_channel_configured("email"):
        channel, normalized = "email", details["email"]
    elif delivery_channel_configured("phone") and details["phone"]:
        channel, normalized = "phone", details["phone"]
    elif delivery_channel_configured("phone"):
        return JSONResponse(
            {"ok": False, "message": "Enter your mobile number so we can send a code."},
            status_code=422,
        )
    else:
        return _channel_unavailable("email")

    con = db()
    try:
        ensure_auth_tables(con)
        con.execute(
            "DELETE FROM pending_signups WHERE created_at < ?",
            (time.time() - PENDING_SIGNUP_TTL_SECONDS,),
        )
        con.commit()

        def contact_must_be_new():
            if _customers_with_contact(con, email=details["email"]):
                return JSONResponse(
                    {
                        "ok": False,
                        "code": "account_exists",
                        "message": "An account with this email already exists. Please sign in instead.",
                    },
                    status_code=409,
                )
            if details["phone"] and _customers_with_contact(con, phone=details["phone"]):
                return JSONResponse(
                    {
                        "ok": False,
                        "code": "account_exists",
                        "message": "An account with this mobile number already exists. Please sign in instead.",
                    },
                    status_code=409,
                )
            return None

        def remember_signup(result):
            con.execute(
                """INSERT INTO pending_signups (device_id, payload, created_at) VALUES (?, ?, ?)
                   ON CONFLICT(device_id) DO UPDATE SET payload=excluded.payload, created_at=excluded.created_at""",
                (result.device_id, json.dumps({**details, "accepted_terms_at": now()}), time.time()),
            )
            con.commit()

        return await _send_code(
            request, con, channel, normalized,
            precheck=contact_must_be_new, after_create=remember_signup,
        )
    finally:
        con.close()


@router.post("/login/otp/resend")
async def login_otp_resend(
    request: Request,
    device_id: str = Form(...),
):
    con = db()
    try:
        wait = reserve_otp_request(
            con,
            f"device:{device_id}",
            request.client.host if request.client else None,
        )
        if wait is not None:
            return JSONResponse(
                {
                    "ok": False,
                    "message": f"Please wait {wait} seconds before requesting another code.",
                },
                status_code=429,
                headers={"Retry-After": str(wait)},
            )
        device = await passwordless_async.list_codes_by_device_id(
            tenant_id=TENANT_ID,
            device_id=device_id,
        )
        if device is None:
            return JSONResponse(
                {"ok": False, "message": "This code request has expired. Start again."},
                status_code=400,
            )
        if device.email:
            channel, recipient = "email", device.email
        elif device.phone_number:
            channel, recipient = "phone", device.phone_number
        else:
            raise RuntimeError("Could not resolve the OTP delivery destination.")
        if not delivery_channel_configured(channel):
            raise RuntimeError(f"{channel} delivery is not configured.")

        result = await passwordless_async.create_new_code_for_device(
            tenant_id=TENANT_ID,
            device_id=device_id,
        )
        if isinstance(result, CreateNewCodeForDeviceOkResult):
            await _deliver_passwordless_code(channel, recipient, result)
    except (
        SuperTokensError,
        OSError,
        RuntimeError,
        ValueError,
        SMTPException,
        httpx.HTTPError,
    ) as exc:
        _log_otp_delivery_failure("resend", exc)
        return JSONResponse(
            {"ok": False, "message": "Could not send a new code. Please start again."},
            status_code=503,
        )
    except Exception as exc:
        if not _is_supertokens_core_error(exc):
            raise
        _log_otp_delivery_failure("resend", exc)
        return JSONResponse(
            {
                "ok": False,
                "message": "Sign-in service is temporarily unavailable. Please try again later.",
            },
            status_code=503,
        )
    finally:
        con.close()

    if not isinstance(result, CreateNewCodeForDeviceOkResult):
        return JSONResponse(
            {"ok": False, "message": "This code request has expired. Start again."},
            status_code=400,
        )
    return JSONResponse(
        {
            "ok": True,
            "device_id": result.device_id,
            "pre_auth_session_id": result.pre_auth_session_id,
            "expires_in": max(1, int(result.code_life_time / 1000)),
        }
    )


def _customer_for_verified_identity(con, user_id, email, phone, signup=None):
    """Return the customer for a verified SuperTokens identity.

    * Sign-in (``signup`` is None): the verified email/phone must belong to an
      existing account, otherwise AccountNotFoundError. Nothing is created.
    * Sign-up (``signup`` holds the form details): the contact must still be
      unused, otherwise AccountExistsError; then the customer dimension row
      and the customer_pii row are created together.
    """
    ensure_auth_tables(con)
    mapped = con.execute(
        "SELECT customer_id FROM supertokens_customers WHERE supertokens_user_id=?",
        (user_id,),
    ).fetchone()
    normalized_email = email.strip().lower() if email else None
    normalized_phone = _normalize_login_identifier(phone)[1] if phone else None
    if mapped and signup is None:
        return mapped[0]

    matches = _customers_with_contact(con, email=normalized_email, phone=normalized_phone)
    if len(matches) > 1:
        raise ValueError("This verified contact matches more than one Pantree customer record.")
    customer_id = next(iter(matches), None) or (mapped[0] if mapped else None)

    if signup is not None:
        if customer_id is not None:
            raise AccountExistsError("An account with this contact already exists.")
        if normalized_email and signup.get("email") not in (None, normalized_email):
            raise ValueError("The verified email does not match the sign-up form.")
        customer_id = uid("cust")
        created = now()
        dim_row = {
            "customer_id": customer_id,
            "signup_date": created[:10],
            "signup_city": signup.get("signup_city"),
            "signup_state": signup.get("signup_state"),
            "signup_region": signup.get("signup_region"),
            "acquisition_channel": signup.get("acquisition_channel") or "Unknown",
            "acquisition_campaign": "website_signup",
            "age_bracket": signup.get("age_bracket"),
            "gender": signup.get("gender") or "Prefer not to say",
            "is_prime_member": 0,
            "prime_start_date": None,
            "prime_tier": "Non-Prime",
            "email_opt_in": int(bool(signup.get("email_opt_in"))),
            "sms_opt_in": int(bool(signup.get("sms_opt_in"))),
            "account_status": "active",
            "terms_accepted_at": signup.get("accepted_terms_at") or created,
        }
        existing_columns = set(database.table_columns(con, "customers"))
        columns = [c for c in dim_row if c in existing_columns]
        con.execute(
            f"INSERT INTO customers ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
            [dim_row[c] for c in columns],
        )
        con.execute(
            """INSERT INTO customer_pii
               (customer_id, first_name, last_name, email, phone, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                customer_id,
                signup.get("first_name"),
                signup.get("last_name") or None,
                normalized_email or signup.get("email"),
                normalized_phone or signup.get("phone"),
                created,
                created,
            ),
        )
        mirror_customer(dim_row)
    else:
        if customer_id is None:
            raise AccountNotFoundError("No account found for this contact.")
        account = con.execute(
            "SELECT account_status FROM customers WHERE customer_id=?",
            (customer_id,),
        ).fetchone()
        if account and account[0] not in (None, "active"):
            raise ValueError("This Pantree account is not active.")
        if normalized_phone:
            con.execute(
                "UPDATE customer_pii SET phone=? WHERE customer_id=? AND phone IS NULL",
                (normalized_phone, customer_id),
            )

    if not mapped:
        con.execute(
            """INSERT INTO supertokens_customers
               (supertokens_user_id, customer_id, created_at) VALUES (?, ?, ?)""",
            (user_id, customer_id, now()),
        )
    return customer_id


@router.post("/login/otp/verify")
async def login_otp_verify(
    request: Request,
    device_id: str = Form(...),
    pre_auth_session_id: str = Form(...),
    code: str = Form(...),
    device: str = Form("Desktop Web"),
):
    allowed_devices = {"Desktop Web", "Android App", "iOS App", "Mobile Web"}
    if device not in allowed_devices:
        return JSONResponse(
            {"ok": False, "message": "Choose a valid device type."}, status_code=422
        )
    if not re.fullmatch(r"\d{6}", code):
        return JSONResponse(
            {"ok": False, "message": "That code is incorrect or expired."},
            status_code=400,
        )
    try:
        consumed = await passwordless_async.consume_code(
            tenant_id=TENANT_ID,
            device_id=device_id,
            pre_auth_session_id=pre_auth_session_id,
            user_input_code=code,
        )
    except SuperTokensError as exc:
        logger.error(
            "SuperTokens OTP verification failed (error_type=%s)", type(exc).__name__
        )
        return JSONResponse(
            {
                "ok": False,
                "message": "That code is incorrect or expired. Request a new code.",
            },
            status_code=400,
        )
    if isinstance(
        consumed,
        (
            ConsumeCodeIncorrectUserInputCodeError,
            ConsumeCodeExpiredUserInputCodeError,
            ConsumeCodeRestartFlowError,
        ),
    ) or not isinstance(consumed, ConsumeCodeOkResult):
        return JSONResponse(
            {
                "ok": False,
                "message": "That code is incorrect or expired. Request a new code.",
            },
            status_code=400,
        )

    con = db()
    try:
        ensure_auth_tables(con)
        pending = con.execute(
            "SELECT payload, created_at FROM pending_signups WHERE device_id=?", (device_id,)
        ).fetchone()
        signup = None
        if pending and time.time() - float(pending[1]) <= PENDING_SIGNUP_TTL_SECONDS:
            signup = json.loads(pending[0])
        con.execute("DELETE FROM pending_signups WHERE device_id=?", (device_id,))
        customer_id = _customer_for_verified_identity(
            con,
            consumed.user.id,
            consumed.consumed_device.email,
            consumed.consumed_device.phone_number,
            signup=signup,
        )
        con.commit()
        auth_session = await session_async.create_new_session(
            request=request,
            tenant_id=TENANT_ID,
            recipe_user_id=consumed.recipe_user_id,
        )
        sid = uid("sess")
        created = now()
        con.execute(
            """INSERT INTO sessions
               (session_id, customer_id, device_type, created_at, supertokens_session_handle)
               VALUES (?, ?, ?, ?, ?)""",
            (sid, customer_id, device, created, auth_session.get_handle()),
        )
        log_event(con, customer_id, sid, device, "app_open", "home")
        con.commit()
    except AccountNotFoundError:
        con.rollback()
        return JSONResponse(
            {
                "ok": False,
                "code": "account_not_found",
                "message": "No account found for this contact. Create an account first.",
            },
            status_code=404,
        )
    except AccountExistsError:
        con.rollback()
        return JSONResponse(
            {
                "ok": False,
                "code": "account_exists",
                "message": "An account with this contact already exists. Please sign in instead.",
            },
            status_code=409,
        )
    except (SuperTokensError, ValueError, *database.IntegrityError) as exc:
        con.rollback()
        logger.error(
            "Could not finish Pantree account sign-in (error_type=%s)",
            type(exc).__name__,
        )
        return JSONResponse(
            {"ok": False, "message": "Could not finish signing in. Please try again."},
            status_code=503,
        )
    finally:
        con.close()

    response = JSONResponse({"ok": True, "redirect_url": "/"})
    public_url = os.getenv("PUBLIC_APP_URL", "http://127.0.0.1:8000")
    response.set_cookie(
        "sid",
        sid,
        httponly=True,
        secure=urlsplit(public_url).scheme == "https",
        samesite="lax",
        max_age=60 * 60 * 24 * 7,
        path="/",
    )
    return response


@router.post("/logout")
async def logout(request: Request):
    try:
        auth_session = await session_async.get_session(
            request,
            session_required=False,
            anti_csrf_check=bool(request.headers.get("anti-csrf")),
        )
    except SuperTokensError:
        auth_session = None
    if auth_session:
        await auth_session.revoke_session()
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("sid", path="/")
    return response


@router.get("/account", response_class=HTMLResponse)
def account_page(request: Request, sid: str = Cookie(None), msg: str = ""):
    con = db()
    s = get_session(con, sid)
    if not s:
        con.close()
        return RedirectResponse("/login", status_code=303)
    customer = con.execute(
        """SELECT c.*, p.first_name, p.last_name, p.email, p.phone, p.home_address_line1,
                  p.home_address_line2, p.home_postal_code, p.home_country
           FROM customers c LEFT JOIN customer_pii p USING(customer_id) WHERE c.customer_id=?""",
        (s["customer_id"],),
    ).fetchone()
    orders = con.execute(
        """SELECT o.*, t.ship_address_line1, t.ship_address_line2,
        t.ship_city, t.ship_state, t.ship_region, t.ship_postal_code, t.ship_country
        FROM orders o LEFT JOIN transactions t ON t.transaction_id = (
            SELECT MIN(t2.transaction_id) FROM transactions t2 WHERE t2.order_id = o.order_id)
        WHERE o.customer_id=? ORDER BY o.order_timestamp DESC""",
        (s["customer_id"],),
    ).fetchall()
    ctx = base_ctx(con, s, request, cats=_departments(con), title="Your account")
    ctx.update({"customer": customer, "orders": orders, "msg": msg})
    con.close()
    return tpl.TemplateResponse(request, "account.html", ctx)


@router.post("/account/reset-recommendations")
def reset_recommendations(sid: str = Cookie(None)):
    """Delete this customer's activity (keeps the account and login) and start fresh."""
    con = db()
    try:
        s = get_session(con, sid)
        if not s:
            return RedirectResponse("/login", status_code=303)
        customer_id = s["customer_id"]
        reset_customer_activity(con, customer_id, keep_session_id=sid, now=now(), new_id=uid)
        con.commit()
    except Exception:
        con.rollback()
        logger.exception("Account reset failed")
        return RedirectResponse("/account?msg=Could+not+reset+your+recommendations.+Please+try+again.", status_code=303)
    finally:
        con.close()
    forget_customer_activity(customer_id)
    logger.info("Customer reset their recommendations")
    return RedirectResponse("/account?msg=Your+recommendations+were+reset.", status_code=303)
