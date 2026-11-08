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
    db,
    delivery_channel_configured,
    ensure_auth_tables,
    get_session,
    httpx,
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
        },
    )


@router.post("/login/otp/request")
async def login_otp_request(
    request: Request,
    identifier: str = Form(...),
    device: str = Form("Desktop Web"),
):
    con = db()
    try:
        channel, normalized = _normalize_login_identifier(identifier)
    except ValueError as exc:
        con.close()
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=422)
    allowed_devices = {"Desktop Web", "Android App", "iOS App", "Mobile Web"}
    if device not in allowed_devices:
        con.close()
        return JSONResponse(
            {"ok": False, "message": "Choose a valid device type."}, status_code=422
        )
    if not delivery_channel_configured(channel):
        con.close()
        if channel == "phone" and delivery_channel_configured("email"):
            message = "Phone sign-in is unavailable here. Please use your email address."
        else:
            missing_provider = "SMTP" if channel == "email" else "OpenSMS"
            message = f"{missing_provider} delivery is not configured for this sign-in method."
        return JSONResponse(
            {"ok": False, "message": message},
            status_code=503,
        )

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
        result = await passwordless_async.create_code(
            tenant_id=TENANT_ID,
            email=normalized if channel == "email" else None,
            phone_number=normalized if channel == "phone" else None,
        )
        await _deliver_passwordless_code(channel, normalized, result)
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
    finally:
        con.close()

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


def _customer_for_verified_identity(con, user_id, email, phone):
    ensure_auth_tables(con)
    mapped = con.execute(
        "SELECT customer_id FROM supertokens_customers WHERE supertokens_user_id=?",
        (user_id,),
    ).fetchone()
    if mapped:
        return mapped["customer_id"]

    normalized_email = email.strip().lower() if email else None
    normalized_phone = _normalize_login_identifier(phone)[1] if phone else None
    matches = set()
    rows = con.execute("""SELECT a.customer_id, a.email, c.phone FROM auth a
           JOIN customers c USING(customer_id)""").fetchall()
    for row in rows:
        if (
            normalized_email
            and row["email"]
            and row["email"].strip().lower() == normalized_email
        ):
            matches.add(row["customer_id"])
        if normalized_phone and row["phone"]:
            try:
                existing_phone = _normalize_login_identifier(row["phone"])[1]
            except ValueError:
                continue
            if existing_phone == normalized_phone:
                matches.add(row["customer_id"])
    if len(matches) > 1:
        raise ValueError(
            "This verified contact matches more than one Pantree customer record."
        )

    customer_id = next(iter(matches), None)
    if customer_id is not None:
        account = con.execute(
            "SELECT account_status FROM customers WHERE customer_id=?",
            (customer_id,),
        ).fetchone()
        if account and account["account_status"] not in (None, "active"):
            raise ValueError("This Pantree account is not active.")
    if customer_id is None:
        customer_id = uid("cust")
        created = now()
        con.execute(
            """INSERT INTO customers
               (customer_id, signup_date, acquisition_channel, gender,
                is_prime_member, prime_tier, email_opt_in, sms_opt_in,
                account_status, first_name, last_name, phone)
               VALUES (?, ?, 'Passwordless', 'Prefer not to say', 0,
                       'Non-Prime', 0, 0, 'active', 'Customer', '', ?)""",
            (customer_id, created[:10], normalized_phone),
        )
        mirror_customer(
            {
                "customer_id": customer_id,
                "signup_date": created[:10],
                "acquisition_channel": "Passwordless",
                "acquisition_campaign": None,
                "gender": "Prefer not to say",
                "age_bracket": None,
                "is_prime_member": 0,
                "prime_tier": "Non-Prime",
                "prime_start_date": None,
                "email_opt_in": 0,
                "sms_opt_in": 0,
                "account_status": "active",
                "first_name": "Customer",
                "last_name": "",
                "phone": normalized_phone,
                "signup_city": None,
                "signup_region": None,
                "signup_state": None,
                "home_address_line1": None,
                "home_address_line2": None,
                "home_postal_code": None,
                "home_country": None,
            }
        )

    auth_row = con.execute(
        "SELECT email FROM auth WHERE customer_id=?", (customer_id,)
    ).fetchone()
    if auth_row is None:
        con.execute(
            "INSERT INTO auth (customer_id, email, password) VALUES (?, ?, NULL)",
            (customer_id, normalized_email),
        )
    elif normalized_email and not auth_row["email"]:
        con.execute(
            "UPDATE auth SET email=? WHERE customer_id=?",
            (normalized_email, customer_id),
        )
    if normalized_phone:
        con.execute(
            "UPDATE customers SET phone=? WHERE customer_id=? AND COALESCE(phone,'')=''",
            (normalized_phone, customer_id),
        )
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
        customer_id = _customer_for_verified_identity(
            con,
            consumed.user.id,
            consumed.consumed_device.email,
            consumed.consumed_device.phone_number,
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
    except (SuperTokensError, ValueError, sqlite3.IntegrityError) as exc:
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
def account_page(request: Request, sid: str = Cookie(None)):
    con = db()
    s = get_session(con, sid)
    if not s:
        con.close()
        return RedirectResponse("/login", status_code=303)
    customer = con.execute(
        "SELECT c.*, a.email FROM customers c JOIN auth a USING(customer_id) WHERE c.customer_id=?",
        (s["customer_id"],),
    ).fetchone()
    orders = con.execute(
        """SELECT o.*, t.ship_address_line1, t.ship_address_line2,
        t.ship_city, t.ship_state, t.ship_region, t.ship_postal_code, t.ship_country
        FROM orders o LEFT JOIN transactions t ON t.order_id=o.order_id
        WHERE o.customer_id=? GROUP BY o.order_id ORDER BY o.order_timestamp DESC""",
        (s["customer_id"],),
    ).fetchall()
    ctx = base_ctx(con, s, request, cats=_departments(con), title="Your account")
    ctx.update({"customer": customer, "orders": orders})
    con.close()
    return tpl.TemplateResponse(request, "account.html", ctx)
