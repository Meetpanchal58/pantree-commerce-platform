import asyncio
import importlib
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiosmtplib.errors import SMTPException
import httpx
from fastapi.testclient import TestClient
from supertokens_python.recipe.passwordless.interfaces import (
    ConsumeCodeExpiredUserInputCodeError,
    ConsumeCodeIncorrectUserInputCodeError,
    ConsumeCodeOkResult,
    ConsumeCodeRestartFlowError,
    ConsumedDevice,
    CreateNewCodeForDeviceOkResult,
)
from supertokens_python.types import RecipeUserId, User
from starlette.responses import Response
from webapp.auth import initialize_supertokens, reserve_otp_request
from webapp.auth import OpenSMSSMSService

storefront = importlib.import_module("webapp.app")


def request(method="POST", path="/login/otp/request", cookies=None):
    headers = [(b"host", b"127.0.0.1:8000")]
    if cookies:
        headers.append(
            (b"cookie", "; ".join(f"{key}={value}" for key, value in cookies.items()).encode())
        )
    return storefront.Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 54321),
            "server": ("127.0.0.1", 8000),
            "state": {},
        }
    )


def open_test_db(path):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS customers (
            customer_id TEXT PRIMARY KEY, signup_date TEXT, acquisition_channel TEXT,
            gender TEXT, is_prime_member INTEGER, prime_tier TEXT, email_opt_in INTEGER,
            sms_opt_in INTEGER, account_status TEXT, first_name TEXT, last_name TEXT,
            phone TEXT, home_address_line1 TEXT, home_address_line2 TEXT,
            home_postal_code TEXT, home_country TEXT
        );
        CREATE TABLE IF NOT EXISTS auth (
            customer_id TEXT PRIMARY KEY, email TEXT UNIQUE, password TEXT
        );
        CREATE TABLE IF NOT EXISTS clickstream (
            event_id TEXT PRIMARY KEY, session_id TEXT, customer_id TEXT, order_id TEXT,
            event_timestamp TEXT, event_type TEXT, page_type TEXT, product_id TEXT,
            search_query TEXT, device_type TEXT, traffic_source TEXT, campaign_id TEXT
        );
        CREATE TABLE IF NOT EXISTS cart (
            customer_id TEXT, product_id TEXT, quantity INTEGER, added_at TEXT
        );
        CREATE TABLE IF NOT EXISTS orders (
            order_id TEXT PRIMARY KEY, customer_id TEXT, order_timestamp TEXT,
            order_status TEXT
        );
        CREATE TABLE IF NOT EXISTS transactions (
            transaction_id TEXT PRIMARY KEY, order_id TEXT
        );
        """
    )
    return con


class SuperTokensAuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / "storefront.db"
        self.environment = patch.dict(
            os.environ,
            {
                "PANTREE_RATE_LIMIT_SECRET": "test-rate-limit-secret-" * 2,
                "SMTP_HOST": "smtp.example.test",
                "SMTP_FROM_EMAIL": "store@example.test",
                "SMTP_USERNAME": "store@example.test",
                "SMTP_PASSWORD": "test-password",
                "OPENSMS_API_URL": "http://127.0.0.1:9292/sms/",
                "OPENSMS_API_KEY": "test-opensms-key",
            },
            clear=False,
        )
        self.environment.start()
        self.db_patches = [
            patch.object(module, "db", side_effect=lambda: open_test_db(self.database))
            for module in (storefront, storefront.auth)
        ]
        for db_patch in self.db_patches:
            db_patch.start()

    def tearDown(self):
        for db_patch in reversed(self.db_patches):
            db_patch.stop()
        self.environment.stop()
        self.tempdir.cleanup()

    def test_login_template_shows_code_step_while_request_is_pending(self):
        template = (
            Path(__file__).resolve().parents[1]
            / "webapp"
            / "templates"
            / "login.html"
        ).read_text(encoding="utf-8")
        self.assertIn('id="verify-submit"', template)
        self.assertIn("<h2>Verify your sign-in</h2>", template)
        self.assertNotIn("Check your messages", template)
        self.assertIn('placeholder="------"', template)
        self.assertNotIn('"Enter the code sent to "+identifier', template)
        self.assertIn('document.getElementById("code-step").hidden=false;', template)
        self.assertIn('document.getElementById("verify-submit").disabled=true;', template)
        self.assertIn('document.getElementById("verify-submit").disabled=false;', template)
        self.assertIn("show(requestedView);", template)

    def test_request_creates_super_tokens_otp_without_returning_code(self):
        result = SimpleNamespace(
            device_id="device-1",
            pre_auth_session_id="preauth-1",
            user_input_code="123456",
            code_life_time=300000,
        )
        with (
            patch.object(
                storefront.passwordless_async, "create_code", new=AsyncMock(return_value=result)
            ) as create_code,
            patch.object(
                storefront.passwordless_async, "send_email", new=AsyncMock()
            ) as send_email,
        ):
            response = asyncio.run(
                storefront.login_otp_request(
                    request(), identifier="USER@example.com", device="Desktop Web"
                )
            )

        payload = json.loads(response.body)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["device_id"], "device-1")
        self.assertEqual(payload["expires_in"], 300)
        self.assertNotIn("user_input_code", payload)
        self.assertNotIn("123456", response.body.decode())
        create_code.assert_awaited_once_with(
            tenant_id=storefront.TENANT_ID,
            email="user@example.com",
            phone_number=None,
        )
        send_email.assert_awaited_once()
        email_template = send_email.await_args.args[0]
        self.assertEqual(email_template.email, "user@example.com")
        self.assertEqual(email_template.user_input_code, "123456")
        self.assertEqual(email_template.tenant_id, storefront.TENANT_ID)

    def test_resend_cooldown_limits_provider_calls(self):
        result = CreateNewCodeForDeviceOkResult(
            pre_auth_session_id="preauth-2",
            code_id="code-2",
            device_id="device-1",
            user_input_code="123456",
            link_code="link",
            code_life_time=300000,
            time_created=0,
        )
        with (
            patch.object(
                storefront.passwordless_async,
                "create_new_code_for_device",
                new=AsyncMock(return_value=result),
            ) as resend,
            patch.object(
                storefront.passwordless_async,
                "list_codes_by_device_id",
                new=AsyncMock(
                    return_value=SimpleNamespace(
                        email="user@example.com", phone_number=None
                    )
                ),
            ),
            patch.object(
                storefront.passwordless_async, "send_email", new=AsyncMock()
            ) as send_email,
        ):
            first = asyncio.run(
                storefront.login_otp_resend(request(), device_id="device-1")
            )
            second = asyncio.run(
                storefront.login_otp_resend(request(), device_id="device-1")
            )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second.headers["retry-after"], "30")
        resend.assert_awaited_once()
        send_email.assert_awaited_once()

    def test_mobile_identifier_uses_normalized_phone_number(self):
        result = SimpleNamespace(
            device_id="phone-device",
            pre_auth_session_id="phone-preauth",
            user_input_code="123456",
            code_life_time=300000,
        )
        with (
            patch.object(
                storefront.passwordless_async, "create_code", new=AsyncMock(return_value=result)
            ) as create_code,
            patch.object(
                storefront.passwordless_async, "send_sms", new=AsyncMock()
            ) as send_sms,
        ):
            response = asyncio.run(
                storefront.login_otp_request(
                    request(), identifier="9876543210", device="Mobile Web"
                )
            )
        self.assertEqual(response.status_code, 200)
        create_code.assert_awaited_once_with(
            tenant_id=storefront.TENANT_ID,
            email=None,
            phone_number="+919876543210",
        )
        send_sms.assert_awaited_once()
        sms_template = send_sms.await_args.args[0]
        self.assertEqual(sms_template.phone_number, "+919876543210")
        self.assertEqual(sms_template.user_input_code, "123456")

    def test_opensms_sends_super_tokens_code_with_bearer_auth(self):
        response = unittest.mock.Mock()
        response.json.return_value = {"stat": True}
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = False
        client.post.return_value = response
        template = SimpleNamespace(
            phone_number="+919876543210",
            user_input_code="123456",
            code_life_time=300000,
        )

        with patch("webapp.auth.httpx.AsyncClient", return_value=client) as client_factory:
            asyncio.run(OpenSMSSMSService().send_sms(template, {}))

        client_factory.assert_called_once()
        client.post.assert_awaited_once_with(
            "http://127.0.0.1:9292/sms/",
            headers={"Authorization": "Bearer test-opensms-key"},
            json={
                "number": "+919876543210",
                "text": "Your Pantree sign-in code is 123456. It expires in 5 minutes.",
            },
        )
        self.assertEqual(
            client_factory.call_args.kwargs["timeout"],
            httpx.Timeout(5.0, connect=2.0),
        )

    def test_opensms_rejects_stat_false(self):
        response = unittest.mock.Mock()
        response.json.return_value = {"stat": False}
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = False
        client.post.return_value = response
        template = SimpleNamespace(
            phone_number="+919876543210",
            user_input_code="123456",
            code_life_time=300000,
        )

        with patch("webapp.auth.httpx.AsyncClient", return_value=client):
            with self.assertRaisesRegex(RuntimeError, "delivery failed"):
                asyncio.run(OpenSMSSMSService().send_sms(template, {}))

    def test_opensms_http_error_is_treated_as_delivery_failure(self):
        response = unittest.mock.Mock()
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "unauthorized",
            request=httpx.Request("POST", "http://127.0.0.1:9292/sms/"),
            response=httpx.Response(401),
        )
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = False
        client.post.return_value = response
        template = SimpleNamespace(
            phone_number="+919876543210",
            user_input_code="123456",
            code_life_time=300000,
        )

        with patch("webapp.auth.httpx.AsyncClient", return_value=client):
            with self.assertRaises(httpx.HTTPStatusError):
                asyncio.run(OpenSMSSMSService().send_sms(template, {}))

    def test_opensms_timeout_returns_service_unavailable_without_code(self):
        with (
            patch.object(
                storefront.passwordless_async,
                "create_code",
                new=AsyncMock(
                    return_value=SimpleNamespace(
                        device_id="device-1",
                        pre_auth_session_id="preauth-1",
                        user_input_code="123456",
                        code_life_time=300000,
                    )
                ),
            ),
            patch.object(
                storefront.auth,
                "_deliver_passwordless_code",
                new=AsyncMock(side_effect=httpx.TimeoutException("provider timeout")),
            ),
        ):
            response = asyncio.run(
                storefront.login_otp_request(
                    request(), identifier="+919876543210", device="Mobile Web"
                )
            )

        self.assertEqual(response.status_code, 503)
        self.assertNotIn("123456", response.body.decode())
        self.assertNotIn("provider timeout", response.body.decode())

    def test_opensms_delivery_failure_logs_safe_reason_only(self):
        with (
            patch.object(
                storefront.passwordless_async,
                "create_code",
                new=AsyncMock(
                    return_value=SimpleNamespace(
                        device_id="device-1",
                        pre_auth_session_id="preauth-1",
                        user_input_code="123456",
                        code_life_time=300000,
                    )
                ),
            ),
            patch.object(
                storefront.auth,
                "_deliver_passwordless_code",
                new=AsyncMock(
                    side_effect=RuntimeError(
                        "OpenSMS reported that SMS delivery failed."
                    )
                ),
            ),
            self.assertLogs("webapp.app", level="ERROR") as captured,
        ):
            response = asyncio.run(
                storefront.login_otp_request(
                    request(), identifier="+919876543210", device="Mobile Web"
                )
            )

        self.assertEqual(response.status_code, 503)
        self.assertIn("failure_reason=opensms_rejected_delivery", captured.output[0])
        self.assertNotIn("123456", captured.output[0])
        self.assertNotIn("test-opensms-key", captured.output[0])

    def test_unavailable_supertokens_core_returns_service_unavailable(self):
        with patch.object(
            storefront.passwordless_async,
            "create_code",
            new=AsyncMock(side_effect=Exception("No SuperTokens core available to query")),
        ):
            response = asyncio.run(
                storefront.login_otp_request(
                    request(), identifier="user@example.com", device="Desktop Web"
                )
            )

        self.assertEqual(response.status_code, 503)
        self.assertIn("Sign-in service is temporarily unavailable", response.body.decode())
        self.assertNotIn("No SuperTokens core", response.body.decode())

    def test_supertokens_core_server_error_does_not_leak_response_body(self):
        core_error = Exception(
            "SuperTokens core threw an error for a POST request to path: "
            "'/recipe/signinup/code' with status code: 500 and message: private detail"
        )
        with patch.object(
            storefront.passwordless_async,
            "create_code",
            new=AsyncMock(side_effect=core_error),
        ):
            response = asyncio.run(
                storefront.login_otp_request(
                    request(), identifier="user@example.com", device="Desktop Web"
                )
            )

        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private detail", response.body.decode())

    def test_unrelated_unexpected_exception_is_not_hidden(self):
        with (
            patch.object(
                storefront.passwordless_async,
                "create_code",
                new=AsyncMock(side_effect=Exception("unexpected bug")),
            ),
            self.assertRaisesRegex(Exception, "unexpected bug"),
        ):
            asyncio.run(
                storefront.login_otp_request(
                    request(), identifier="user@example.com", device="Desktop Web"
                )
            )

    def test_international_phone_numbers_are_validated_and_normalized(self):
        self.assertEqual(
            storefront._normalize_login_identifier("+1 (415) 555-2671"),
            ("phone", "+14155552671"),
        )
        self.assertEqual(
            storefront._normalize_login_identifier("0044 20 7946 0018"),
            ("phone", "+442079460018"),
        )
        with self.assertRaises(ValueError):
            storefront._normalize_login_identifier("+1 123")

    def test_smtp_configuration_rejects_plaintext_fallback_port(self):
        with patch.dict(
            os.environ,
            {
                "APP_ENV": "development",
                "PUBLIC_APP_URL": "http://127.0.0.1:8000",
                "SMTP_PORT": "587",
            },
        ), patch("webapp.auth.init") as supertokens_init:
            with self.assertRaisesRegex(RuntimeError, "implicit TLS"):
                initialize_supertokens()
        supertokens_init.assert_not_called()

    def test_gmail_host_can_be_configured_before_credentials_are_entered(self):
        with patch.dict(
            os.environ,
            {
                "APP_ENV": "development",
                "PUBLIC_APP_URL": "http://127.0.0.1:8000",
                "SMTP_HOST": "smtp.gmail.com",
                "SMTP_PORT": "465",
                "SMTP_FROM_EMAIL": "",
                "SMTP_USERNAME": "",
                "SMTP_PASSWORD": "",
            },
        ), patch("webapp.auth.init") as supertokens_init:
            initialize_supertokens()

        supertokens_init.assert_called_once()

    def test_contact_and_ip_window_limits_are_enforced(self):
        con = sqlite3.connect(":memory:")
        self.assertIsNone(
            reserve_otp_request(
                con, "email:user@example.test", "127.0.0.1",
                cooldown_seconds=0, max_identifier_requests=2,
            )
        )
        self.assertIsNone(
            reserve_otp_request(
                con, "email:user@example.test", "127.0.0.1",
                cooldown_seconds=0, max_identifier_requests=2,
            )
        )
        self.assertEqual(
            reserve_otp_request(
                con, "email:user@example.test", "127.0.0.1",
                cooldown_seconds=0, max_identifier_requests=2,
            ),
            900,
        )
        con.close()

    def test_failed_delivery_returns_explicit_error_and_never_exposes_code(self):
        result = SimpleNamespace(
            device_id="device-1",
            pre_auth_session_id="preauth-1",
            user_input_code="123456",
            code_life_time=300000,
        )
        with patch.object(
            storefront.passwordless_async,
            "create_code",
            new=AsyncMock(return_value=result),
        ), patch.object(
            storefront.passwordless_async,
            "send_email",
            new=AsyncMock(side_effect=SMTPException("provider unavailable")),
        ):
            response = asyncio.run(
                storefront.login_otp_request(
                    request(), identifier="user@example.com", device="Desktop Web"
                )
            )

        self.assertEqual(response.status_code, 503)
        self.assertNotIn("123456", response.body.decode())

    def test_smtp_delivery_exceptions_return_service_unavailable(self):
        with patch.object(
            storefront.passwordless_async,
            "create_code",
            new=AsyncMock(side_effect=SMTPException("provider unavailable")),
        ):
            response = asyncio.run(
                storefront.login_otp_request(
                    request(), identifier="user@example.com", device="Desktop Web"
                )
            )

        self.assertEqual(response.status_code, 503)
        self.assertNotIn("provider unavailable", response.body.decode())

    def test_missing_opensms_provider_is_reported_before_super_tokens_request(self):
        with patch.dict(os.environ, {"OPENSMS_API_KEY": ""}):
            with patch.object(
                storefront.passwordless_async, "create_code", new=AsyncMock()
            ) as create_code:
                response = asyncio.run(
                    storefront.login_otp_request(
                        request(), identifier="+919876543210", device="Mobile Web"
                    )
                )

        self.assertEqual(response.status_code, 503)
        self.assertIn("Phone sign-in is unavailable here", response.body.decode())
        create_code.assert_not_awaited()

    def test_partial_opensms_configuration_is_rejected(self):
        with patch.dict(
            os.environ,
            {
                "APP_ENV": "development",
                "PUBLIC_APP_URL": "http://127.0.0.1:8000",
                "OPENSMS_API_URL": "http://127.0.0.1:9292/sms/",
                "OPENSMS_API_KEY": "",
            },
        ), patch("webapp.auth.init") as supertokens_init:
            with self.assertRaisesRegex(RuntimeError, "Configure OPENSMS_API_URL"):
                initialize_supertokens()
        supertokens_init.assert_not_called()

    def test_opensms_timeout_is_treated_as_delivery_failure(self):
        with patch.object(
            storefront.auth,
            "_deliver_passwordless_code",
            new=AsyncMock(side_effect=httpx.ConnectTimeout("provider timeout")),
        ):
            with patch.object(
                storefront.passwordless_async,
                "create_code",
                new=AsyncMock(
                    return_value=SimpleNamespace(
                        device_id="device-1",
                        pre_auth_session_id="preauth-1",
                        user_input_code="123456",
                        code_life_time=300000,
                    )
                ),
            ):
                response = asyncio.run(
                    storefront.login_otp_request(
                        request(), identifier="+14155552671", device="Mobile Web"
                    )
                )

        self.assertEqual(response.status_code, 503)
        self.assertNotIn("provider timeout", response.body.decode())


    def test_missing_email_provider_is_reported_before_super_tokens_request(self):
        with patch.dict(os.environ, {"SMTP_HOST": ""}):
            with patch.object(
                storefront.passwordless_async, "create_code", new=AsyncMock()
            ) as create_code:
                response = asyncio.run(
                    storefront.login_otp_request(
                        request(), identifier="user@example.com", device="Desktop Web"
                    )
                )

        self.assertEqual(response.status_code, 503)
        self.assertIn("SMTP delivery is not configured", response.body.decode())
        create_code.assert_not_awaited()

    def test_verified_first_login_creates_customer_and_super_tokens_session(self):
        user = User(
            user_id="st-user-1",
            is_primary_user=True,
            tenant_ids=["public"],
            emails=["new@example.com"],
            phone_numbers=[],
            third_party=[],
            webauthn=None,
            login_methods=[],
            time_joined=0,
        )
        consumed = ConsumeCodeOkResult(
            created_new_recipe_user=True,
            user=user,
            recipe_user_id=RecipeUserId("st-user-1"),
            consumed_device=ConsumedDevice(
                pre_auth_session_id="preauth-1",
                failed_code_input_attempt_count=0,
                email="new@example.com",
            ),
        )
        auth_session = SimpleNamespace(get_handle=lambda: "st-session-handle")
        with (
            patch.object(
                storefront.passwordless_async,
                "consume_code",
                new=AsyncMock(return_value=consumed),
            ),
            patch.object(
                storefront.session_async,
                "create_new_session",
                new=AsyncMock(return_value=auth_session),
            ),
            patch.object(storefront.auth, "mirror_customer") as mirror_customer,
            patch.object(storefront.auth, "log_event"),
        ):
            with patch.dict(os.environ, {"PUBLIC_APP_URL": "https://shop.example"}):
                response = asyncio.run(
                    storefront.login_otp_verify(
                        request(),
                        device_id="device-1",
                        pre_auth_session_id="preauth-1",
                        code="123456",
                        device="Desktop Web",
                    )
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.body)["redirect_url"], "/")
        mirrored_customer = mirror_customer.call_args.args[0]
        self.assertTrue(
            {
                "acquisition_campaign",
                "age_bracket",
                "home_address_line1",
                "home_address_line2",
                "home_country",
                "home_postal_code",
                "prime_start_date",
                "signup_city",
                "signup_region",
                "signup_state",
            }.issubset(mirrored_customer)
        )
        cookie = response.headers["set-cookie"].lower()
        self.assertIn("sid=", cookie)
        self.assertIn("httponly", cookie)
        self.assertIn("secure", cookie)
        con = sqlite3.connect(self.database)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM customers").fetchone()[0], 1)
        self.assertEqual(
            con.execute("SELECT email FROM auth").fetchone()[0], "new@example.com"
        )
        self.assertEqual(
            con.execute("SELECT supertokens_user_id FROM supertokens_customers").fetchone()[0],
            "st-user-1",
        )
        self.assertEqual(
            con.execute("SELECT supertokens_session_handle FROM sessions").fetchone()[0],
            "st-session-handle",
        )
        con.close()

    def test_existing_email_maps_to_customer_without_duplicate_row(self):
        con = open_test_db(self.database)
        con.execute(
            """INSERT INTO customers
               (customer_id, signup_date, phone, first_name, account_status)
               VALUES ('customer-1', '2025-01-01', '+919999999999', 'Existing', 'active')"""
        )
        con.execute(
            "INSERT INTO auth (customer_id, email, password) VALUES (?, ?, NULL)",
            ("customer-1", "existing@example.com"),
        )
        con.execute(
            "INSERT INTO cart (customer_id, product_id, quantity, added_at) VALUES (?, ?, ?, ?)",
            ("customer-1", "product-1", 2, "2025-01-01"),
        )
        con.execute(
            "INSERT INTO orders (order_id, customer_id, order_timestamp, order_status) "
            "VALUES (?, ?, ?, ?)",
            ("order-1", "customer-1", "2025-01-01", "Delivered"),
        )
        con.commit()
        with patch.object(storefront.auth, "mirror_customer"):
            first = storefront._customer_for_verified_identity(
                con, "st-user-email", "existing@example.com", None
            )
            second = storefront._customer_for_verified_identity(
                con, "st-user-phone", None, "+919999999999"
            )
        self.assertEqual(first, "customer-1")
        self.assertEqual(second, "customer-1")
        self.assertEqual(con.execute("SELECT COUNT(*) FROM customers").fetchone()[0], 1)
        self.assertEqual(
            con.execute("SELECT customer_id FROM cart").fetchone()[0], "customer-1"
        )
        self.assertEqual(
            con.execute("SELECT customer_id FROM orders").fetchone()[0], "customer-1"
        )
        con.close()

    def test_inactive_customer_account_cannot_be_linked(self):
        con = open_test_db(self.database)
        con.execute(
            """INSERT INTO customers
               (customer_id, signup_date, phone, first_name, account_status)
               VALUES ('blocked-customer', '2025-01-01', NULL, 'Blocked', 'suspended')"""
        )
        con.execute(
            "INSERT INTO auth (customer_id, email, password) VALUES (?, ?, NULL)",
            ("blocked-customer", "blocked@example.com"),
        )
        with self.assertRaisesRegex(ValueError, "not active"):
            storefront._customer_for_verified_identity(
                con, "new-supertokens-user", "blocked@example.com", None
            )
        self.assertEqual(
            con.execute("SELECT COUNT(*) FROM supertokens_customers").fetchone()[0], 0
        )
        con.close()

    def test_wrong_and_expired_codes_are_rejected(self):
        for error in (
            ConsumeCodeIncorrectUserInputCodeError(1, 5),
            ConsumeCodeExpiredUserInputCodeError(1, 5),
            ConsumeCodeRestartFlowError(),
        ):
            with self.subTest(error=type(error).__name__), patch.object(
                storefront.passwordless_async,
                "consume_code",
                new=AsyncMock(return_value=error),
            ):
                response = asyncio.run(
                    storefront.login_otp_verify(
                        request(),
                        device_id="device-1",
                        pre_auth_session_id="preauth-1",
                        code="123456",
                        device="Desktop Web",
                    )
                )
                self.assertEqual(response.status_code, 400)

    def test_logout_revokes_supertokens_session_and_clears_storefront_cookie(self):
        auth_session = SimpleNamespace(revoke_session=AsyncMock())
        with patch.object(
            storefront.session_async,
            "get_session",
            new=AsyncMock(return_value=auth_session),
        ):
            response = asyncio.run(storefront.logout(request()))

        auth_session.revoke_session.assert_awaited_once()
        self.assertIn("sid=", response.headers["set-cookie"])
        self.assertIn("Max-Age=0", response.headers["set-cookie"])

    def test_login_page_has_signup_and_account_routes_require_valid_supertokens(self):
        client = TestClient(storefront.app)
        login_page = client.get("/login")
        self.assertEqual(login_page.status_code, 200)
        self.assertIn("Create your account", login_page.text)
        self.assertIn('headers:{"st-auth-mode":"cookie"}', login_page.text)
        self.assertNotIn('name="password"', login_page.text)

        with patch.object(
            storefront.session_async, "get_session", new=AsyncMock(return_value=None)
        ):
            protected = client.get(
                "/account", cookies={"sid": "forged"}, follow_redirects=False
            )
        self.assertEqual(protected.status_code, 401)

    def test_login_page_offers_email_only_when_sms_is_not_configured(self):
        client = TestClient(storefront.app)
        with patch.object(
            storefront.auth,
            "delivery_channel_configured",
            side_effect=lambda channel: channel == "email",
        ):
            login_page = client.get("/login")

        self.assertEqual(login_page.status_code, 200)
        self.assertIn('<label for="identifier">Email address</label>', login_page.text)
        self.assertIn('id="identifier" name="identifier" type="email"', login_page.text)
        self.assertIn("code sent to your email", login_page.text)
        self.assertNotIn("Email or mobile number", login_page.text)
        self.assertNotIn("+1 415 555 2671", login_page.text)

    def test_healthcheck_requires_the_durable_storefront_schema(self):
        con = open_test_db(self.database)
        con.close()
        original_db = storefront.DB
        storefront.DB = self.database
        try:
            response = storefront.healthcheck()
        finally:
            storefront.DB = original_db
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.body), {"status": "ok"})

    def test_healthcheck_rejects_a_missing_database_file(self):
        original_db = storefront.DB
        storefront.DB = self.database
        try:
            response = storefront.healthcheck()
        finally:
            storefront.DB = original_db
        self.assertEqual(response.status_code, 503)
        self.assertEqual(json.loads(response.body), {"status": "not_ready"})

    def test_valid_supertokens_session_is_accepted_for_mapped_customer(self):
        con = open_test_db(self.database)
        storefront.ensure_auth_tables(con)
        con.execute(
            """INSERT INTO customers
               (customer_id, signup_date, first_name, account_status)
               VALUES ('customer-1', '2025-01-01', 'Existing', 'active')"""
        )
        con.execute(
            """INSERT INTO supertokens_customers
               (supertokens_user_id, customer_id, created_at)
               VALUES ('st-user-1', 'customer-1', '2025-01-01')"""
        )
        con.execute(
            """INSERT INTO sessions
               (session_id, customer_id, device_type, created_at, supertokens_session_handle)
               VALUES ('local-session', 'customer-1', 'Desktop Web', '2025-01-01', 'st-handle')"""
        )
        con.commit()
        con.close()

        auth_session = SimpleNamespace(
            get_handle=lambda: "st-handle",
            get_user_id=lambda: "st-user-1",
        )
        with patch.object(
            storefront.session_async,
            "get_session",
            new=AsyncMock(return_value=auth_session),
        ) as get_session:
            response = asyncio.run(
                storefront.protect_storefront_sessions(
                    request("GET", "/account", {"sid": "local-session"}),
                    lambda _: asyncio.sleep(0, result=Response("ok")),
                )
            )

        self.assertEqual(response.body, b"ok")
        get_session.assert_awaited_once()

    def test_expired_access_session_is_refreshed_before_protected_request(self):
        con = open_test_db(self.database)
        storefront.ensure_auth_tables(con)
        con.execute(
            """INSERT INTO customers
               (customer_id, signup_date, first_name, account_status)
               VALUES ('customer-1', '2025-01-01', 'Existing', 'active')"""
        )
        con.execute(
            """INSERT INTO supertokens_customers
               (supertokens_user_id, customer_id, created_at)
               VALUES ('st-user-1', 'customer-1', '2025-01-01')"""
        )
        con.execute(
            """INSERT INTO sessions
               (session_id, customer_id, device_type, created_at, supertokens_session_handle)
               VALUES ('local-session', 'customer-1', 'Desktop Web', '2025-01-01', 'st-handle')"""
        )
        con.commit()
        con.close()

        auth_session = SimpleNamespace(
            get_handle=lambda: "st-handle",
            get_user_id=lambda: "st-user-1",
        )
        with (
            patch.object(
                storefront.session_async,
                "get_session",
                new=AsyncMock(side_effect=storefront.TryRefreshTokenError()),
            ),
            patch.object(
                storefront.session_async,
                "refresh_session",
                new=AsyncMock(return_value=auth_session),
            ) as refresh_session,
        ):
            response = asyncio.run(
                storefront.protect_storefront_sessions(
                    request("GET", "/account", {"sid": "local-session"}),
                    lambda _: asyncio.sleep(0, result=Response("ok")),
                )
            )

        self.assertEqual(response.body, b"ok")
        refresh_session.assert_awaited_once()

    def test_unthrottled_direct_passwordless_endpoints_are_disabled(self):
        response = TestClient(storefront.app).post(
            "/auth/signinup/code", json={"email": "user@example.test"}
        )
        self.assertEqual(response.status_code, 404)

    def test_cross_origin_mutation_is_rejected(self):
        response = TestClient(storefront.app).post(
            "/login/otp/request",
            data={"identifier": "user@example.com", "device": "Desktop Web"},
            headers={"Origin": "https://evil.example"},
        )
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
