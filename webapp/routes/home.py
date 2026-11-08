"""Home routes for the Pantree storefront."""

from fastapi import APIRouter

from ..core import (
    Cookie,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Request,
    _catalog_stats,
    _departments,
    _subcategories,
    base_ctx,
    db,
    ensure_wishlist,
    get_session,
    json,
    log_event,
    ml_recommend,
    run_in_threadpool,
    tpl,
)

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def home(request: Request, sid: str = Cookie(None)):
    con = db()
    s = get_session(con, sid)
    if not s:
        con.close()
        return RedirectResponse("/login", status_code=303)
    log_event(con, s["customer_id"], sid, s["device_type"], "page_view", "home")
    ctx = base_ctx(
        con,
        s,
        request,
        prods=con.execute(
            """SELECT p.*, i.current_stock FROM products p JOIN inventory i USING(product_id)
        WHERE i.current_stock > 0 ORDER BY p.avg_rating DESC, p.review_count DESC LIMIT 24"""
        ).fetchall(),
        cats=_departments(con),
        title="Home",
    )
    ctx.update(
        {
            "departments": _departments(con),
            "subcategories": _subcategories(con),
            "home_stats": _catalog_stats(con),
        }
    )
    con.commit()
    con.close()
    return tpl.TemplateResponse(request, "home.html", ctx)


@router.get("/api/recommendations/{surface}", operation_id="recommendations_api_get")
@router.post("/api/recommendations/{surface}", operation_id="recommendations_api_post")
async def recommendations_api(surface: str, request: Request, sid: str = Cookie(None)):
    """Return personalized recommendations for the authenticated storefront customer."""
    con = db()
    session = get_session(con, sid)
    if not session:
        con.close()
        return JSONResponse({"detail": "Authentication required"}, status_code=401)

    try:
        body = await request.json() if request.method == "POST" else {}
    except json.JSONDecodeError:
        con.close()
        return JSONResponse(
            {"detail": "Request body must be valid JSON"}, status_code=422
        )
    if not isinstance(body, dict):
        con.close()
        return JSONResponse(
            {"detail": "Request body must be an object"}, status_code=422
        )
    context = body.get("context", {})
    if not isinstance(context, dict):
        con.close()
        return JSONResponse({"detail": "context must be an object"}, status_code=422)
    context = dict(context)
    for key in ("product_id", "intent", "category", "limit"):
        if key in request.query_params:
            context[key] = request.query_params[key]
    if "exclude_product_ids" in request.query_params:
        context["exclude_product_ids"] = [
            item
            for item in request.query_params["exclude_product_ids"].split(",")
            if item
        ]
    if "product_id" in body:
        context["product_id"] = body["product_id"]

    customer_id = session["customer_id"]
    if surface == "cart" and "cart" not in context:
        context["cart"] = [
            row["product_id"]
            for row in con.execute(
                "SELECT product_id FROM cart WHERE customer_id=? ORDER BY added_at",
                (customer_id,),
            ).fetchall()
        ]
    recent_events = con.execute(
        """SELECT product_id,event_type FROM clickstream
           WHERE customer_id=? AND product_id IS NOT NULL
             AND event_type IN ('pdp_view','add_to_cart','wishlist_add','purchase')
           ORDER BY event_timestamp DESC LIMIT 100""",
        (customer_id,),
    ).fetchall()
    context["recent_product_ids"] = list(
        dict.fromkeys(row["product_id"] for row in recent_events)
    )[:10]
    context["online_event_count"] = len(recent_events)
    context["purchased_product_ids"] = [
        row["product_id"]
        for row in con.execute(
            """SELECT DISTINCT product_id FROM transactions
               WHERE customer_id=? AND product_id IS NOT NULL
                 AND UPPER(COALESCE(order_status,'')) NOT IN
                    ('RTO','CANCELLED','REFUNDED','CIC','CIR','LOST','UNDELIVERED')""",
            (customer_id,),
        ).fetchall()
    ]

    try:
        try:
            items = await run_in_threadpool(ml_recommend, customer_id, surface, context)
        except ValueError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=422)

        if items:
            ensure_wishlist(con)
            product_ids = [item["product_id"] for item in items]
            placeholders = ",".join("?" for _ in product_ids)
            product_rows = con.execute(
                f"""SELECT p.product_id,p.mrp,p.current_price,p.avg_rating,p.review_count,
                    i.current_stock,
                    EXISTS(SELECT 1 FROM wishlist w
                        WHERE w.customer_id=? AND w.product_id=p.product_id) AS wishlisted,
                    COALESCE((SELECT c.quantity FROM cart c
                        WHERE c.customer_id=? AND c.product_id=p.product_id),0) AS cart_quantity
                    FROM products p JOIN inventory i USING(product_id)
                    WHERE p.product_id IN ({placeholders})""",
                [customer_id, customer_id, *product_ids],
            ).fetchall()
            product_details = {
                row["product_id"]: {
                    **dict(row),
                    "wishlisted": bool(row["wishlisted"]),
                }
                for row in product_rows
                if row["current_stock"] is not None and row["current_stock"] > 0
            }
            items = [
                {**item, **product_details[item["product_id"]]}
                for item in items
                if item["product_id"] in product_details
            ]
        if surface == "homepage":
            request.app.state.homepage_feed_responses += 1
            if request.app.state.homepage_feed_responses >= 2:
                request.app.state.homepage_feeds_ready.set()
        return items
    finally:
        con.close()
