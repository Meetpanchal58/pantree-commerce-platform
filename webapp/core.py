"""Shared storefront configuration, database access, and view helpers."""

"""Pantree storefront — FastAPI backend.
SQLite is the live OLTP source of truth. Every user activity is written to SQLite
and mirrored to DuckDB for analytics/model training.
"""

import asyncio, json, logging, os, re, sqlite3, sys, uuid

import datetime as dt

from contextlib import asynccontextmanager

from pathlib import Path

from urllib.parse import urlsplit

from fastapi import FastAPI, Request, Form, Cookie

from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse

from fastapi.staticfiles import StaticFiles

from fastapi.templating import Jinja2Templates

from dotenv import load_dotenv

from aiosmtplib.errors import SMTPException

import httpx

import phonenumbers

from supertokens_python.exceptions import SuperTokensError

from supertokens_python.framework.fastapi import get_middleware

from supertokens_python.recipe.passwordless import asyncio as passwordless_async

from supertokens_python.recipe.passwordless.interfaces import (
    ConsumeCodeExpiredUserInputCodeError,
    ConsumeCodeIncorrectUserInputCodeError,
    ConsumeCodeOkResult,
    ConsumeCodeRestartFlowError,
    CreateNewCodeForDeviceOkResult,
)

from supertokens_python.recipe.passwordless.types import (
    PasswordlessLoginEmailTemplateVars,
    PasswordlessLoginSMSTemplateVars,
)

from supertokens_python.recipe.session import asyncio as session_async

from supertokens_python.recipe.session.exceptions import TryRefreshTokenError

from starlette.concurrency import run_in_threadpool

try:
    from .analytics_sync import (
        mirror_event,
        mirror_customer,
        mirror_order,
        mirror_transaction,
        mirror_inventory,
        mirror_inventory_movement,
        mirror_return,
    )
    from .auth import (
        TENANT_ID,
        delivery_channel_configured,
        initialize_supertokens,
        reserve_otp_request,
    )
except ImportError:
    from analytics_sync import (
        mirror_event,
        mirror_customer,
        mirror_order,
        mirror_transaction,
        mirror_inventory,
        mirror_inventory_movement,
        mirror_return,
    )
    from auth import (
        TENANT_ID,
        delivery_channel_configured,
        initialize_supertokens,
        reserve_otp_request,
    )

BASE = Path(__file__).resolve().parent

ROOT = BASE.parent

load_dotenv(ROOT / ".env")

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from recommendations.serve import (
    recommend as ml_recommend,
    warm_homepage_caches,
    warm_recommendation_caches,
)

DB = Path(os.getenv("PANTREE_DB_PATH", str(BASE / "pantree_app.db"))).expanduser()

logger = logging.getLogger("webapp.app")

initialize_supertokens()


def _warm_recommendation_worker():
    try:
        if DB.exists():
            con = sqlite3.connect(DB, timeout=60)
            try:
                exists = con.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='clickstream'"
                ).fetchone()
                if exists:
                    con.execute(
                        """CREATE INDEX IF NOT EXISTS ix_click_customer_recent_product
                           ON clickstream(customer_id,event_timestamp DESC,product_id)"""
                    )
                    con.commit()
            finally:
                con.close()
        warm_recommendation_caches()
    except Exception:
        logger.exception("Recommendation cache warmup failed")


tpl = Jinja2Templates(directory=str(BASE / "templates"))


def db():
    con = sqlite3.connect(DB, timeout=60)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=60000;")
    con.execute("PRAGMA foreign_keys=ON;")
    _ensure_customer_schema(con)
    return con


def now():
    return dt.datetime.now().isoformat(timespec="seconds")


def uid(p):
    return f"{p}_{uuid.uuid4().hex[:12]}"


def _ensure_customer_schema(con):
    columns = {
        "first_name": "TEXT",
        "last_name": "TEXT",
        "phone": "TEXT",
        "home_address_line1": "TEXT",
        "home_address_line2": "TEXT",
        "home_postal_code": "TEXT",
        "home_country": "TEXT",
    }
    existing = {row[1] for row in con.execute("PRAGMA table_info(customers)")}
    for name, kind in columns.items():
        if name not in existing:
            con.execute(f"ALTER TABLE customers ADD COLUMN {name} {kind}")
    (
        con.execute("ALTER TABLE transactions ADD COLUMN ship_address_line1 TEXT")
        if "ship_address_line1"
        not in {row[1] for row in con.execute("PRAGMA table_info(transactions)")}
        else None
    )
    (
        con.execute("ALTER TABLE transactions ADD COLUMN ship_address_line2 TEXT")
        if "ship_address_line2"
        not in {row[1] for row in con.execute("PRAGMA table_info(transactions)")}
        else None
    )
    (
        con.execute("ALTER TABLE transactions ADD COLUMN ship_postal_code TEXT")
        if "ship_postal_code"
        not in {row[1] for row in con.execute("PRAGMA table_info(transactions)")}
        else None
    )
    (
        con.execute("ALTER TABLE transactions ADD COLUMN ship_country TEXT")
        if "ship_country"
        not in {row[1] for row in con.execute("PRAGMA table_info(transactions)")}
        else None
    )
    (
        con.execute("ALTER TABLE transactions ADD COLUMN ship_phone TEXT")
        if "ship_phone"
        not in {row[1] for row in con.execute("PRAGMA table_info(transactions)")}
        else None
    )


def log_event(
    con,
    customer_id,
    session_id,
    device_type,
    event_type,
    page_type,
    product_id=None,
    search_query=None,
    traffic_source="direct",
):
    ts = now()
    event_id = uid("evt")
    row = (
        event_id,
        session_id,
        customer_id,
        None,
        ts,
        event_type,
        page_type,
        product_id,
        search_query,
        device_type,
        traffic_source,
        None,
    )
    con.execute(
        """INSERT INTO clickstream
        (event_id, session_id, customer_id, order_id, event_timestamp, event_type, page_type,
         product_id, search_query, device_type, traffic_source, campaign_id)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        row,
    )
    mirror_event(
        dict(
            zip(
                [
                    "event_id",
                    "session_id",
                    "customer_id",
                    "order_id",
                    "event_timestamp",
                    "event_type",
                    "page_type",
                    "product_id",
                    "search_query",
                    "device_type",
                    "traffic_source",
                    "campaign_id",
                ],
                row,
            )
        )
    )
    return event_id


def table_exists(con, name):
    return (
        con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        is not None
    )


def ensure_sessions(con):
    con.execute("""CREATE TABLE IF NOT EXISTS sessions (
        session_id TEXT PRIMARY KEY, customer_id TEXT, device_type TEXT, created_at TEXT,
        supertokens_session_handle TEXT)""")
    columns = {row[1] for row in con.execute("PRAGMA table_info(sessions)")}
    if "supertokens_session_handle" not in columns:
        con.execute("ALTER TABLE sessions ADD COLUMN supertokens_session_handle TEXT")
    con.commit()


def ensure_auth_tables(con):
    ensure_sessions(con)
    con.execute("""CREATE TABLE IF NOT EXISTS supertokens_customers (
            supertokens_user_id TEXT PRIMARY KEY,
            customer_id TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""")
    con.execute(
        "CREATE INDEX IF NOT EXISTS ix_supertokens_customers_customer "
        "ON supertokens_customers(customer_id)"
    )
    con.commit()


def ensure_wishlist(con):
    con.execute("""CREATE TABLE IF NOT EXISTS wishlist (
        customer_id TEXT, product_id TEXT, added_at TEXT,
        PRIMARY KEY (customer_id, product_id)
    )""")
    con.commit()


def _normalize_login_identifier(value):
    value = value.strip()
    if "@" in value:
        if len(value) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            raise ValueError("Enter a valid email address or phone number.")
        return "email", value.lower()
    phone_value = value
    if phone_value.startswith("00"):
        phone_value = "+" + phone_value[2:]
    region = None if phone_value.startswith("+") else "IN"
    try:
        parsed_phone = phonenumbers.parse(phone_value, region)
    except phonenumbers.NumberParseException as exc:
        raise ValueError(
            "Enter a valid international phone number with country code."
        ) from exc
    if not phonenumbers.is_valid_number(parsed_phone):
        raise ValueError("Enter a valid email address or phone number.")
    return "phone", phonenumbers.format_number(
        parsed_phone, phonenumbers.PhoneNumberFormat.E164
    )


def get_session(con, sid):
    ensure_sessions(con)
    if not sid:
        return None
    return con.execute("SELECT * FROM sessions WHERE session_id=?", (sid,)).fetchone()


async def _deliver_passwordless_code(channel, recipient, result):
    template_values = {
        "tenant_id": TENANT_ID,
        "code_life_time": result.code_life_time,
        "pre_auth_session_id": result.pre_auth_session_id,
        "is_first_factor": True,
        "user_input_code": result.user_input_code,
        "url_with_link_code": None,
    }
    if channel == "email":
        await passwordless_async.send_email(
            PasswordlessLoginEmailTemplateVars(email=recipient, **template_values)
        )
    else:
        await passwordless_async.send_sms(
            PasswordlessLoginSMSTemplateVars(phone_number=recipient, **template_values)
        )


def _log_otp_delivery_failure(operation: str, exc: Exception) -> None:
    provider_status = (
        exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
    )
    failure_reason = None
    if isinstance(exc, RuntimeError):
        failure_reasons = {
            "OpenSMS returned an invalid response.": "opensms_invalid_response",
            "OpenSMS reported that SMS delivery failed.": "opensms_rejected_delivery",
            "PANTREE_RATE_LIMIT_SECRET must contain at least 32 characters.": "rate_limit_secret_invalid",
            "The local OTP rate-limit key is invalid.": "rate_limit_key_invalid",
        }
        failure_reason = failure_reasons.get(str(exc), "runtime_error")
    logger.error(
        "SuperTokens could not %s an OTP "
        "(error_type=%s, provider_status=%s, failure_reason=%s)",
        operation,
        type(exc).__name__,
        provider_status,
        failure_reason,
    )


def _is_supertokens_core_error(exc: Exception) -> bool:
    message = str(exc)
    return message == "No SuperTokens core available to query" or message.startswith(
        "SuperTokens core threw an error for a "
    )


def cart_count(con, cid):
    r = con.execute(
        "SELECT COALESCE(SUM(quantity),0) n FROM cart WHERE customer_id=?", (cid,)
    ).fetchone()
    return r["n"] if r else 0


def _departments(con):
    return [
        r["h1_category"]
        for r in con.execute("SELECT DISTINCT h1_category FROM products ORDER BY 1")
    ]


def _subcategories(con):
    return [
        r["h2_category"]
        for r in con.execute("SELECT DISTINCT h2_category FROM products ORDER BY 1")
    ]


def _filter_options(con):
    def values(column, limit=None):
        sql = f"SELECT {column} value, COUNT(*) n FROM products WHERE {column} IS NOT NULL AND TRIM({column})<>'' GROUP BY {column} ORDER BY n DESC, value"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [r["value"] for r in con.execute(sql)]

    return {
        "genders": values("gender"),
        "brands": values("brand_name", 40),
        "colours": values("h4_colour"),
        "usages": values("usage"),
    }


def _catalog_stats(con):
    row = con.execute(
        "SELECT COUNT(*) products, COUNT(DISTINCT brand_name) brands, COUNT(DISTINCT h1_category) departments FROM products"
    ).fetchone()
    return dict(row)


def _catalog_price_bounds(con):
    row = con.execute(
        "SELECT FLOOR(MIN(current_price)) minimum, CEIL(MAX(current_price)) maximum FROM products"
    ).fetchone()
    return {
        "minimum": int(row["minimum"] or 0),
        "maximum": int(row["maximum"] or 25000),
    }


def _parse_optional_float(value):
    if value is None or not str(value).strip():
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _price_range_bounds(value):
    ranges = {
        "under-1000": (None, 1000),
        "1000-2500": (1000, 2500),
        "2500-5000": (2500, 5000),
        "5000-10000": (5000, 10000),
        "10000-plus": (10000, None),
    }
    return ranges.get(str(value).strip(), (None, None))


def base_ctx(con, s, request, prods=None, cats=None, title="Pantree", heading=None):
    customer = con.execute(
        "SELECT first_name FROM customers WHERE customer_id=?", (s["customer_id"],)
    ).fetchone()
    cart_quantities = {
        row["product_id"]: row["quantity"]
        for row in con.execute(
            "SELECT product_id,quantity FROM cart WHERE customer_id=?",
            (s["customer_id"],),
        )
    }
    return {
        "title": title,
        "heading": heading,
        "prods": prods or [],
        "cats": cats or [],
        "cart_n": cart_count(con, s["customer_id"]),
        "customer_id": s["customer_id"],
        "first_name": (
            customer["first_name"]
            if customer and customer["first_name"]
            else "Customer"
        ),
        "cart_quantities": cart_quantities,
    }
