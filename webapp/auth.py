"""SuperTokens Passwordless configuration and request throttling."""

from __future__ import annotations

import hashlib
import hmac
import os
import sqlite3
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from supertokens_python import InputAppInfo, SupertokensConfig, init
from supertokens_python.ingredients.emaildelivery.types import (
    EmailDeliveryConfig,
    EmailDeliveryInterface,
    SMTPSettings,
    SMTPSettingsFrom,
)
from supertokens_python.ingredients.smsdelivery.types import (
    SMSDeliveryConfig,
    SMSDeliveryInterface,
)
from supertokens_python.recipe import passwordless, session
from supertokens_python.recipe.passwordless import InputOverrideConfig

LOCAL_RATE_LIMIT_KEY = Path(__file__).resolve().parent / ".local" / "rate-limit.key"
TENANT_ID = "public"
IMPLICIT_TLS_SMTP_PORTS = {465, 2465}


class _UnconfiguredEmailService(EmailDeliveryInterface[Any]):
    async def send_email(self, template_vars: Any, user_context: dict[str, Any]) -> None:
        raise RuntimeError("Email OTP delivery requires SMTP_HOST and SMTP_FROM_EMAIL.")


class _UnconfiguredSMSService(SMSDeliveryInterface[Any]):
    async def send_sms(self, template_vars: Any, user_context: dict[str, Any]) -> None:
        raise RuntimeError(
            "SMS OTP delivery requires OPENSMS_API_URL and OPENSMS_API_KEY."
        )


class OpenSMSSMSService(SMSDeliveryInterface[Any]):
    async def send_sms(self, template_vars: Any, user_context: dict[str, Any]) -> None:
        endpoint = os.getenv("OPENSMS_API_URL")
        api_key = os.getenv("OPENSMS_API_KEY")
        if not endpoint or not api_key:
            raise RuntimeError(
                "SMS OTP delivery requires OPENSMS_API_URL and OPENSMS_API_KEY."
            )

        lifetime_minutes = max(1, (int(template_vars.code_life_time) + 59_999) // 60_000)
        message = (
            f"Your Pantree sign-in code is {template_vars.user_input_code}. "
            f"It expires in {lifetime_minutes} minutes."
        )
        timeout = httpx.Timeout(5.0, connect=2.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                endpoint,
                headers={"Authorization": f"Bearer {api_key}"},
                json={"number": template_vars.phone_number, "text": message},
            )
            response.raise_for_status()
            try:
                result = response.json()
            except ValueError as exc:
                raise RuntimeError("OpenSMS returned an invalid response.") from exc
        if not isinstance(result, dict) or result.get("stat") is not True:
            raise RuntimeError("OpenSMS reported that SMS delivery failed.")


def _disable_direct_passwordless_apis(api: Any) -> Any:
    api.disable_create_code_post = True
    api.disable_resend_code_post = True
    api.disable_consume_code_post = True
    api.disable_email_exists_get = True
    api.disable_phone_number_exists_get = True
    return api


def delivery_channel_configured(channel: str) -> bool:
    if channel == "email":
        required = ("SMTP_HOST", "SMTP_FROM_EMAIL", "SMTP_USERNAME", "SMTP_PASSWORD")
    elif channel == "phone":
        required = ("OPENSMS_API_URL", "OPENSMS_API_KEY")
    else:
        raise ValueError(f"Unknown OTP delivery channel: {channel}")
    return all(os.getenv(name) for name in required)


def initialize_supertokens() -> None:
    app_environment = os.getenv("APP_ENV", "development").strip().lower()
    public_url = os.getenv("PUBLIC_APP_URL", "http://127.0.0.1:8000").rstrip("/")
    parsed_url = urlsplit(public_url)
    if (
        parsed_url.scheme not in {"http", "https"}
        or not parsed_url.netloc
        or parsed_url.path not in {"", "/"}
        or parsed_url.query
        or parsed_url.fragment
    ):
        raise RuntimeError("PUBLIC_APP_URL must be an HTTP(S) origin without a path.")
    connection_uri = os.getenv(
        "SUPERTOKENS_CONNECTION_URI", "http://127.0.0.1:3567"
    ).rstrip("/")
    parsed_connection_uri = urlsplit(connection_uri)
    if parsed_connection_uri.scheme not in {"http", "https"} or not parsed_connection_uri.netloc:
        raise RuntimeError("SUPERTOKENS_CONNECTION_URI must be an absolute HTTP(S) URL.")
    if app_environment in {"production", "prod"}:
        missing = [
            name
            for name in (
                "SUPERTOKENS_API_KEY",
                "SUPERTOKENS_CONNECTION_URI",
                "PANTREE_RATE_LIMIT_SECRET",
                "SMTP_HOST",
                "SMTP_FROM_EMAIL",
                "SMTP_USERNAME",
                "SMTP_PASSWORD",
                "OPENSMS_API_URL",
                "OPENSMS_API_KEY",
            )
            if not os.getenv(name)
        ]
        if missing:
            raise RuntimeError(
                "Production authentication configuration is incomplete: "
                + ", ".join(missing)
                + "."
            )
        if len(os.environ["PANTREE_RATE_LIMIT_SECRET"]) < 32:
            raise RuntimeError(
                "PANTREE_RATE_LIMIT_SECRET must contain at least 32 characters."
            )
        if parsed_url.scheme != "https":
            raise RuntimeError("PUBLIC_APP_URL must use HTTPS in production.")

    smtp_host = os.getenv("SMTP_HOST")
    email_credentials = (
        os.getenv("SMTP_FROM_EMAIL"),
        os.getenv("SMTP_USERNAME"),
        os.getenv("SMTP_PASSWORD"),
    )
    if any(email_credentials) and (
        not smtp_host or not all(email_credentials)
    ):
        raise RuntimeError(
            "Configure SMTP_HOST, SMTP_FROM_EMAIL, SMTP_USERNAME, and SMTP_PASSWORD together."
        )

    opensms_url = os.getenv("OPENSMS_API_URL")
    opensms_key = os.getenv("OPENSMS_API_KEY")
    if bool(opensms_url) != bool(opensms_key):
        raise RuntimeError(
            "Configure OPENSMS_API_URL and OPENSMS_API_KEY together."
        )
    if opensms_url:
        parsed_opensms_url = urlsplit(opensms_url)
        if (
            parsed_opensms_url.scheme not in {"http", "https"}
            or not parsed_opensms_url.netloc
            or parsed_opensms_url.username
            or parsed_opensms_url.password
            or parsed_opensms_url.query
            or parsed_opensms_url.fragment
            or not parsed_opensms_url.path.rstrip("/").endswith("/sms")
        ):
            raise RuntimeError(
                "OPENSMS_API_URL must be an HTTP(S) OpenSMS /sms/ endpoint without credentials or query parameters."
            )

    if smtp_host and all(email_credentials):
        smtp_port = int(os.getenv("SMTP_PORT", "465"))
        # 2465 is Resend's implicit-TLS alternative for hosts that block 465.
        if smtp_port not in IMPLICIT_TLS_SMTP_PORTS:
            raise RuntimeError(
                "SMTP_PORT must be 465 or 2465 so OTP mail uses implicit TLS."
            )
        email_service = passwordless.SMTPService(
            smtp_settings=SMTPSettings(
                host=smtp_host,
                port=smtp_port,
                from_=SMTPSettingsFrom(
                    name=os.getenv("SMTP_FROM_NAME", "Pantree"),
                    email=email_credentials[0],
                ),
                username=email_credentials[1],
                password=email_credentials[2],
                secure=True,
            )
        )
    else:
        email_service = _UnconfiguredEmailService()

    if opensms_url and opensms_key:
        sms_service = OpenSMSSMSService()
    else:
        sms_service = _UnconfiguredSMSService()

    init(
        app_info=InputAppInfo(
            app_name="Pantree",
            api_domain=public_url,
            website_domain=public_url,
            api_base_path="/auth",
            website_base_path="/auth",
        ),
        supertokens_config=SupertokensConfig(
            connection_uri=connection_uri,
            api_key=os.getenv("SUPERTOKENS_API_KEY") or None,
        ),
        framework="fastapi",
        mode="asgi",
        recipe_list=[
            session.init(
                cookie_secure=parsed_url.scheme == "https",
                cookie_same_site="lax",
                anti_csrf="VIA_CUSTOM_HEADER",
            ),
            passwordless.init(
                contact_config=passwordless.ContactEmailOrPhoneConfig(),
                flow_type="USER_INPUT_CODE",
                override=InputOverrideConfig(
                    apis=_disable_direct_passwordless_apis
                ),
                email_delivery=EmailDeliveryConfig(service=email_service),
                sms_delivery=SMSDeliveryConfig(service=sms_service),
            ),
        ],
    )


def ensure_rate_limit_table(con: sqlite3.Connection) -> None:
    con.execute(
        """CREATE TABLE IF NOT EXISTS auth_rate_limits (
            bucket_hash TEXT NOT NULL,
            created_at REAL NOT NULL
        )"""
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS ix_auth_rate_limits_bucket_time "
        "ON auth_rate_limits(bucket_hash, created_at)"
    )


def _rate_limit_key() -> bytes:
    configured_key = os.getenv("PANTREE_RATE_LIMIT_SECRET")
    if configured_key:
        if len(configured_key) < 32:
            raise RuntimeError("PANTREE_RATE_LIMIT_SECRET must contain at least 32 characters.")
        return configured_key.encode()
    LOCAL_RATE_LIMIT_KEY.parent.mkdir(parents=True, exist_ok=True)
    try:
        with LOCAL_RATE_LIMIT_KEY.open("xb") as key_file:
            key_file.write(os.urandom(48))
    except FileExistsError:
        pass
    key = LOCAL_RATE_LIMIT_KEY.read_bytes()
    if len(key) < 32:
        raise RuntimeError("The local OTP rate-limit key is invalid.")
    return key


def _bucket_hash(bucket_type: str, bucket_value: str) -> str:
    value = f"{bucket_type}:{bucket_value}".encode()
    return hmac.new(_rate_limit_key(), value, hashlib.sha256).hexdigest()


def reserve_otp_request(
    con: sqlite3.Connection,
    identifier: str,
    request_ip: str | None,
    *,
    cooldown_seconds: int = 30,
    window_seconds: int = 900,
    max_identifier_requests: int = 5,
    max_ip_requests: int = 20,
) -> int | None:
    """Reserve a send attempt, returning seconds to wait when a limit is hit."""
    ensure_rate_limit_table(con)
    now = time.time()
    identifier_hash = _bucket_hash("identifier", identifier)
    ip_hash = _bucket_hash("ip", request_ip) if request_ip else None
    cutoff = now - window_seconds
    con.execute("BEGIN IMMEDIATE")
    con.execute("DELETE FROM auth_rate_limits WHERE created_at < ?", (cutoff,))
    last_request = con.execute(
        "SELECT MAX(created_at) FROM auth_rate_limits WHERE bucket_hash=?",
        (identifier_hash,),
    ).fetchone()[0]
    if last_request is not None and now - last_request < cooldown_seconds:
        con.rollback()
        return max(1, cooldown_seconds - int(now - last_request))

    identifier_count = con.execute(
        "SELECT COUNT(*) FROM auth_rate_limits WHERE bucket_hash=?",
        (identifier_hash,),
    ).fetchone()[0]
    ip_count = (
        con.execute(
            "SELECT COUNT(*) FROM auth_rate_limits WHERE bucket_hash=?", (ip_hash,)
        ).fetchone()[0]
        if ip_hash
        else 0
    )
    if identifier_count >= max_identifier_requests or ip_count >= max_ip_requests:
        con.rollback()
        return window_seconds

    con.execute(
        "INSERT INTO auth_rate_limits (bucket_hash, created_at) VALUES (?, ?)",
        (identifier_hash, now),
    )
    if ip_hash:
        con.execute(
            "INSERT INTO auth_rate_limits (bucket_hash, created_at) VALUES (?, ?)",
            (ip_hash, now),
        )
    con.commit()
    return None


def release_otp_request(con, identifier: str) -> None:
    """Give back the per-contact cooldown when no code was sent.

    Used when the account check rejects the request (no account / account exists) or
    delivery fails, so the shopper can switch to sign-up or retry straight away. The
    network-address row stays, so bulk probing is still limited per IP.
    """
    bucket = _bucket_hash("identifier", identifier)
    con.execute(
        """DELETE FROM auth_rate_limits WHERE bucket_hash=? AND created_at=(
               SELECT MAX(created_at) FROM auth_rate_limits WHERE bucket_hash=?)""",
        (bucket, bucket),
    )
    con.commit()
