"""Catalog routes for the Pantree storefront."""

from fastapi import APIRouter

from ..core import (
    Cookie,
    HTMLResponse,
    RedirectResponse,
    Request,
    _catalog_price_bounds,
    _departments,
    _filter_options,
    _parse_optional_float,
    _price_range_bounds,
    _subcategories,
    base_ctx,
    db,
    ensure_wishlist,
    get_session,
    json,
    log_event,
    now,
    tpl,
    uid,
)

from ..rec_logging import log_click

router = APIRouter()


@router.get("/category/{cat}", response_class=HTMLResponse)
def plp(cat: str, request: Request, sid: str = Cookie(None)):
    return browse(request, sid, department=cat)


@router.get("/browse", response_class=HTMLResponse)
def browse(
    request: Request,
    sid: str = Cookie(None),
    department: str = "",
    subcategory: str = "",
    gender: str = "",
    brand: str = "",
    colour: str = "",
    usage: str = "",
    min_price: str = "",
    max_price: str = "",
    min_rating: str = "",
    stock: str = "",
    sort: str = "relevance",
    price_range: str = "",
    page: int = 1,
):
    con = db()
    s = get_session(con, sid)
    if not s:
        con.close()
        return RedirectResponse("/login", status_code=303)
    min_price_value = _parse_optional_float(min_price)
    max_price_value = _parse_optional_float(max_price)
    min_rating_value = _parse_optional_float(min_rating)
    range_min, range_max = _price_range_bounds(price_range)
    if range_min is not None:
        min_price_value = range_min
    if range_max is not None:
        max_price_value = range_max
    page = max(1, int(page))
    limit = 36
    offset = (page - 1) * limit
    where = ["1=1"]
    params = []
    filters = {
        "department": department,
        "subcategory": subcategory,
        "gender": gender,
        "brand": brand,
        "colour": colour,
        "usage": usage,
        "price_range": price_range,
        "min_price": min_price_value,
        "max_price": max_price_value,
        "min_rating": min_rating_value,
        "stock": stock,
        "sort": sort,
    }
    for value, column in [
        (department, "h1_category"),
        (subcategory, "h2_category"),
        (gender, "gender"),
        (brand, "brand_name"),
        (colour, "h4_colour"),
        (usage, "usage"),
    ]:
        if value:
            where.append(f"p.{column}=?")
            params.append(value)
    if min_price_value is not None:
        where.append("p.current_price>=?")
        params.append(min_price_value)
    if max_price_value is not None:
        where.append("p.current_price<=?")
        params.append(max_price_value)
    if min_rating_value is not None:
        where.append("COALESCE(p.avg_rating,0)>=?")
        params.append(min_rating_value)
    if stock == "in":
        where.append("i.current_stock>0")
    order_by = {
        "price_asc": "p.current_price ASC",
        "price_desc": "p.current_price DESC",
        "rating": "COALESCE(p.avg_rating,0) DESC",
        "newest": "p.launch_date DESC",
        "relevance": "COALESCE(p.avg_rating,0) DESC, p.review_count DESC",
    }.get(sort, "COALESCE(p.avg_rating,0) DESC")
    base = " FROM products p JOIN inventory i USING(product_id) WHERE " + " AND ".join(
        where
    )
    total = con.execute("SELECT COUNT(*)" + base, params).fetchone()[0]
    prods = con.execute(
        "SELECT p.*, i.current_stock" + base + f" ORDER BY {order_by} LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()
    pages = max(1, (total + limit - 1) // limit)
    title = next((x for x in [subcategory, department, gender, brand] if x), "Shop all")
    if not any(filters.values()):
        title = "Shop all"
    log_event(
        con,
        s["customer_id"],
        sid,
        s["device_type"],
        "category_view",
        "plp",
        search_query=title,
    )
    ctx = base_ctx(
        con, s, request, prods=prods, cats=_departments(con), title=title, heading=title
    )
    ctx.update(
        {
            "departments": _departments(con),
            "subcategories": _subcategories(con),
            "filter_options": _filter_options(con),
            "price_bounds": _catalog_price_bounds(con),
            "filters": filters,
            "result_count": total,
            "page": page,
            "pages": pages,
        }
    )
    con.commit()
    con.close()
    return tpl.TemplateResponse(request, "plp.html", ctx)


@router.get("/search", response_class=HTMLResponse)
def search(q: str, request: Request, sid: str = Cookie(None)):
    con = db()
    s = get_session(con, sid)
    if not s:
        con.close()
        return RedirectResponse("/login", status_code=303)
    log_event(
        con,
        s["customer_id"],
        sid,
        s["device_type"],
        "search",
        "search_results",
        search_query=q,
    )
    like = f"%{q}%"
    prods = con.execute(
        """SELECT p.*, i.current_stock FROM products p JOIN inventory i USING(product_id)
        WHERE p.product_name LIKE ? OR p.brand_name LIKE ? OR p.h3_category LIKE ? OR p.h4_colour LIKE ?
        ORDER BY COALESCE(p.avg_rating,0) DESC, p.review_count DESC LIMIT 48""",
        (like, like, like, like),
    ).fetchall()
    ctx = base_ctx(
        con,
        s,
        request,
        prods=prods,
        cats=_departments(con),
        title=f"Search: {q}",
        heading=f'Results for "{q}"',
    )
    ctx.update(
        {
            "departments": _departments(con),
            "subcategories": _subcategories(con),
            "filter_options": _filter_options(con),
            "price_bounds": _catalog_price_bounds(con),
            "filters": {"q": q},
            "result_count": len(prods),
            "page": 1,
            "pages": 1,
        }
    )
    con.commit()
    con.close()
    return tpl.TemplateResponse(request, "plp.html", ctx)


@router.get("/product/{pid}", response_class=HTMLResponse)
def pdp(
    pid: str,
    request: Request,
    sid: str = Cookie(None),
    rec: str | None = None,
    pos: int | None = None,
):
    con = db()
    s = get_session(con, sid)
    if not s:
        con.close()
        return RedirectResponse("/login", status_code=303)
    ensure_wishlist(con)
    p = con.execute(
        """SELECT p.*, i.current_stock FROM products p JOIN inventory i USING(product_id)
        WHERE p.product_id=?""",
        (pid,),
    ).fetchone()
    if not p:
        # Unknown product: log nothing (no view, no recommendation click).
        con.close()
        return RedirectResponse("/", status_code=303)
    log_event(
        con, s["customer_id"], sid, s["device_type"], "pdp_view", "pdp", product_id=pid
    )
    if rec and pos is not None:
        # Opened from a recommendation card: count the click (rec_logging.py).
        log_click(con, rec, pos, s["customer_id"], pid, now(), uid)
    wishlisted = (
        con.execute(
            "SELECT 1 FROM wishlist WHERE customer_id=? AND product_id=?",
            (s["customer_id"], pid),
        ).fetchone()
        is not None
    )
    cart_item = con.execute(
        "SELECT quantity FROM cart WHERE customer_id=? AND product_id=?",
        (s["customer_id"], pid),
    ).fetchone()
    try:
        attrs = json.loads(p["product_attributes"]) if p["product_attributes"] else {}
    except Exception:
        attrs = {}
    cats = [
        r["h1_category"]
        for r in con.execute("SELECT DISTINCT h1_category FROM products ORDER BY 1")
    ]
    ctx = base_ctx(con, s, request, cats=cats, title=p["product_name"])
    ctx.update(
        {
            "p": p,
            "attrs": attrs,
            "wishlisted": wishlisted,
            "cart_quantity": cart_item["quantity"] if cart_item else 0,
        }
    )
    con.commit()
    con.close()
    return tpl.TemplateResponse(request, "pdp.html", ctx)
