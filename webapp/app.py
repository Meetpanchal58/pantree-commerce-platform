"""FastAPI application assembly and middleware."""

import faulthandler
import sys
import time

_BOOT = time.monotonic()


def _boot_log(step: str) -> None:
    """Start-up progress in the host's logs (Render shows nothing until the port opens)."""
    print(f"[startup {time.monotonic() - _BOOT:5.1f}s] {step}", file=sys.stderr, flush=True)


_boot_log("importing the app")
# If start-up stalls, print where every thread is stuck every 60 s until the app is ready.
faulthandler.dump_traceback_later(60, repeat=True, file=sys.stderr)

import asyncio
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from supertokens_python.exceptions import SuperTokensError
from supertokens_python.recipe.session.exceptions import TryRefreshTokenError

_boot_log("loading web framework, database layer, sign-in and recommender")
from webapp import core, database
from webapp.core import (
    DB,
    TENANT_ID,
    _normalize_login_identifier,
    _warm_recommendation_worker,
    db,
    ensure_auth_tables,
    ensure_customer_pii,
    initialize_supertokens,
    logger,
    os,
    passwordless_async,
    session_async,
    warm_homepage_caches,
)
from webapp.routes import auth, catalog, commerce, home


@asynccontextmanager
async def lifespan(_: FastAPI):
    homepage_feeds_ready = asyncio.Event()
    app.state.homepage_feed_responses = 0
    app.state.homepage_feeds_ready = homepage_feeds_ready

    async def warm_secondary_recommendations():
        # Warm in the background so uvicorn binds its port immediately; slow
        # hosts (e.g. Render's free tier) otherwise time out the port scan.
        try:
            await asyncio.to_thread(warm_homepage_caches)
        except Exception:
            logger.exception("Homepage recommendation cache warmup failed")
        try:
            await asyncio.wait_for(homepage_feeds_ready.wait(), timeout=30)
        except asyncio.TimeoutError:
            pass
        else:
            await asyncio.sleep(2)
        await asyncio.to_thread(_warm_recommendation_worker)

    warmup_task = asyncio.create_task(warm_secondary_recommendations())
    try:
        yield
    finally:
        warmup_task.cancel()
        await asyncio.gather(warmup_task, return_exceptions=True)


app = FastAPI(title="Pantree Storefront", lifespan=lifespan)
_boot_log("app created; the server will now open its port")
faulthandler.cancel_dump_traceback_later()
app.add_middleware(core.get_middleware())
static_dir = core.BASE / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/healthz")
def healthcheck():
    if not database.database_url() and not DB.is_file():
        return JSONResponse({"status": "not_ready"}, status_code=503)
    try:
        con = database.connect(DB, timeout=2)
        try:
            tables = database.table_names(con)
        finally:
            con.close()
    except Exception:
        logger.exception("Health check could not reach the database")
        return JSONResponse({"status": "not_ready"}, status_code=503)
    required_tables = {"customers", "cart", "orders", "transactions"}
    if not required_tables.issubset(tables):
        return JSONResponse({"status": "not_ready"}, status_code=503)
    return JSONResponse({"status": "ok"})


@app.middleware("http")
async def protect_storefront_sessions(request: Request, call_next):
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        public_url = os.getenv("PUBLIC_APP_URL")
        expected_origin = f"{request.url.scheme}://{request.headers.get('host', '')}"
        origin = request.headers.get("origin")
        referer = request.headers.get("referer")
        supplied_origin = origin or (
            f"{urlsplit(referer).scheme}://{urlsplit(referer).netloc}"
            if referer
            else ""
        )
        configured_origin = (
            f"{urlsplit(public_url).scheme}://{urlsplit(public_url).netloc}"
            if public_url
            else ""
        )
        allowed_origin = configured_origin or expected_origin
        if supplied_origin and supplied_origin != allowed_origin:
            return JSONResponse(
                {"ok": False, "message": "Invalid request origin."}, status_code=403
            )
        if not supplied_origin and os.getenv("APP_ENV", "").lower() in {
            "prod",
            "production",
        }:
            return JSONResponse(
                {"ok": False, "message": "Request origin is required."}, status_code=403
            )

    path = request.url.path
    if (
        path.startswith("/auth")
        or path.startswith("/login")
        or path.startswith("/signup")
        or path.startswith("/static/")
    ):
        return await call_next(request)

    sid = request.cookies.get("sid")
    if sid:
        try:
            auth_session = await session_async.get_session(
                request,
                session_required=True,
                anti_csrf_check=False,
            )
        except TryRefreshTokenError:
            try:
                auth_session = await session_async.refresh_session(request)
            except SuperTokensError:
                auth_session = None
        except SuperTokensError:
            auth_session = None
        if auth_session is None:
            response = (
                RedirectResponse("/login", status_code=303)
                if "text/html" in request.headers.get("accept", "")
                else JSONResponse(
                    {"ok": False, "message": "Please sign in again."}, status_code=401
                )
            )
            response.delete_cookie("sid", path="/")
            return response
        con = db()
        try:
            ensure_auth_tables(con)
            local_session = con.execute(
                """SELECT sc.customer_id FROM sessions s
                   JOIN supertokens_customers sc
                     ON sc.customer_id=s.customer_id
                   WHERE s.session_id=? AND s.supertokens_session_handle=?
                     AND sc.supertokens_user_id=?""",
                (sid, auth_session.get_handle(), auth_session.get_user_id()),
            ).fetchone()
        finally:
            con.close()
        if not local_session:
            response = (
                RedirectResponse("/login", status_code=303)
                if "text/html" in request.headers.get("accept", "")
                else JSONResponse(
                    {"ok": False, "message": "Please sign in again."}, status_code=401
                )
            )
            response.delete_cookie("sid", path="/")
            return response
        request.state.pantree_customer_id = local_session["customer_id"]
    return await call_next(request)


for route_module in (auth, home, catalog, commerce):
    app.include_router(route_module.router)

from webapp.routes.auth import (
    _customer_for_verified_identity,
    account_page,
    login_otp_request,
    login_otp_resend,
    login_otp_verify,
    login_page,
    logout,
    signup_otp_request,
)
from webapp.routes.catalog import browse, pdp, plp, search
from webapp.routes.commerce import (
    cart_add,
    cart_page,
    cart_remove,
    cart_update,
    checkout,
    checkout_page,
    orders_page,
    refresh_inventory,
    update_order_status,
    wishlist_toggle,
)
from webapp.routes.home import home, recommendations_api
from webapp.core import (
    _catalog_price_bounds,
    _catalog_stats,
    _departments,
    _filter_options,
    _is_supertokens_core_error,
    _log_otp_delivery_failure,
    _normalize_login_identifier,
    _parse_optional_float,
    _price_range_bounds,
    _subcategories,
)
