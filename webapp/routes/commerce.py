"""Commerce routes for the Pantree storefront."""

from fastapi import APIRouter

from ..core import (
    Cookie,
    Form,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Request,
    _departments,
    base_ctx,
    cart_count,
    db,
    ensure_wishlist,
    get_session,
    log_event,
    mirror_inventory,
    mirror_inventory_movement,
    mirror_order,
    mirror_return,
    mirror_transaction,
    now,
    tpl,
    uid,
)

router = APIRouter()


@router.post("/cart/add")
def cart_add(product_id: str = Form(...), qty: int = Form(1), sid: str = Cookie(None)):
    con = db()
    s = get_session(con, sid)
    if not s:
        con.close()
        return JSONResponse({"ok": False}, status_code=401)
    cid = s["customer_id"]
    qty = max(1, min(int(qty), 20))
    stock = con.execute(
        "SELECT current_stock FROM inventory WHERE product_id=?", (product_id,)
    ).fetchone()
    if not stock or stock["current_stock"] <= 0 or qty > stock["current_stock"]:
        con.close()
        return JSONResponse(
            {"ok": False, "message": "Not enough stock"}, status_code=409
        )
    con.execute(
        """INSERT INTO cart (customer_id,product_id,quantity,added_at) VALUES (?,?,?,?)
        ON CONFLICT(customer_id,product_id) DO UPDATE SET quantity=MIN(quantity+excluded.quantity, ?)""",
        (cid, product_id, qty, now(), int(stock["current_stock"])),
    )
    log_event(
        con, cid, sid, s["device_type"], "add_to_cart", "pdp", product_id=product_id
    )
    con.commit()
    n = cart_count(con, cid)
    quantity = con.execute(
        "SELECT quantity FROM cart WHERE customer_id=? AND product_id=?",
        (cid, product_id),
    ).fetchone()["quantity"]
    con.close()
    return JSONResponse({"ok": True, "cart_count": n, "quantity": quantity})


@router.post("/wishlist/toggle")
def wishlist_toggle(product_id: str = Form(...), sid: str = Cookie(None)):
    con = db()
    s = get_session(con, sid)
    if not s:
        con.close()
        return JSONResponse(
            {"ok": False, "message": "Authentication required"}, status_code=401
        )
    ensure_wishlist(con)
    cid = s["customer_id"]
    existing = con.execute(
        "SELECT 1 FROM wishlist WHERE customer_id=? AND product_id=?", (cid, product_id)
    ).fetchone()
    if existing:
        con.execute(
            "DELETE FROM wishlist WHERE customer_id=? AND product_id=?",
            (cid, product_id),
        )
        event = "wishlist_remove"
        wished = False
    else:
        con.execute(
            "INSERT INTO wishlist (customer_id,product_id,added_at) VALUES (?,?,?)",
            (cid, product_id, now()),
        )
        event = "wishlist_add"
        wished = True
    log_event(con, cid, sid, s["device_type"], event, "pdp", product_id=product_id)
    con.commit()
    con.close()
    return JSONResponse({"ok": True, "wishlisted": wished})


@router.post("/cart/remove")
def cart_remove(product_id: str = Form(...), sid: str = Cookie(None)):
    con = db()
    s = get_session(con, sid)
    if not s:
        con.close()
        return RedirectResponse("/login", status_code=303)
    con.execute(
        "DELETE FROM cart WHERE customer_id=? AND product_id=?",
        (s["customer_id"], product_id),
    )
    log_event(
        con,
        s["customer_id"],
        sid,
        s["device_type"],
        "remove_from_cart",
        "cart",
        product_id=product_id,
    )
    con.commit()
    con.close()
    return RedirectResponse("/cart", status_code=303)


@router.post("/cart/update")
def cart_update(
    product_id: str = Form(...), delta: int = Form(...), sid: str = Cookie(None)
):
    con = db()
    s = get_session(con, sid)
    if not s:
        con.close()
        return JSONResponse(
            {"ok": False, "message": "Authentication required"}, status_code=401
        )
    cid = s["customer_id"]
    item = con.execute(
        "SELECT quantity FROM cart WHERE customer_id=? AND product_id=?",
        (cid, product_id),
    ).fetchone()
    stock = con.execute(
        "SELECT current_stock FROM inventory WHERE product_id=?", (product_id,)
    ).fetchone()
    if not item or not stock:
        con.close()
        return JSONResponse(
            {"ok": False, "message": "Cart item not found"}, status_code=404
        )
    quantity = int(item["quantity"]) + int(delta)
    if quantity <= 0:
        con.execute(
            "DELETE FROM cart WHERE customer_id=? AND product_id=?", (cid, product_id)
        )
        quantity = 0
    elif quantity > int(stock["current_stock"]):
        con.close()
        return JSONResponse(
            {
                "ok": False,
                "message": f"Only {stock['current_stock']} available",
                "quantity": item["quantity"],
            },
            status_code=409,
        )
    else:
        con.execute(
            "UPDATE cart SET quantity=? WHERE customer_id=? AND product_id=?",
            (quantity, cid, product_id),
        )
    log_event(
        con,
        cid,
        sid,
        s["device_type"],
        "cart_quantity_update",
        "cart",
        product_id=product_id,
    )
    con.commit()
    count = cart_count(con, cid)
    con.close()
    return JSONResponse({"ok": True, "quantity": quantity, "cart_count": count})


@router.get("/cart", response_class=HTMLResponse)
def cart_page(request: Request, sid: str = Cookie(None)):
    con = db()
    s = get_session(con, sid)
    if not s:
        con.close()
        return RedirectResponse("/login", status_code=303)
    log_event(con, s["customer_id"], sid, s["device_type"], "page_view", "cart")
    items = con.execute(
        """SELECT c.quantity,p.*,i.current_stock FROM cart c JOIN products p USING(product_id)
        JOIN inventory i USING(product_id) WHERE c.customer_id=?""",
        (s["customer_id"],),
    ).fetchall()
    total = sum(r["current_price"] * r["quantity"] for r in items)
    cats = [
        r["h1_category"]
        for r in con.execute("SELECT DISTINCT h1_category FROM products ORDER BY 1")
    ]
    ctx = base_ctx(con, s, request, cats=cats, title="Cart")
    ctx.update({"items": items, "total": total})
    con.commit()
    con.close()
    return tpl.TemplateResponse(request, "cart.html", ctx)


@router.get("/checkout", response_class=HTMLResponse)
def checkout_page(request: Request, sid: str = Cookie(None), msg: str = ""):
    con = db()
    s = get_session(con, sid)
    if not s:
        con.close()
        return RedirectResponse("/login", status_code=303)
    items = con.execute(
        """SELECT c.quantity,p.*,i.current_stock FROM cart c JOIN products p USING(product_id)
        JOIN inventory i USING(product_id) WHERE c.customer_id=?""",
        (s["customer_id"],),
    ).fetchall()
    if not items:
        con.close()
        return RedirectResponse("/cart", status_code=303)
    log_event(
        con, s["customer_id"], sid, s["device_type"], "checkout_start", "checkout"
    )
    total = sum(
        min(r["quantity"], max(r["current_stock"], 0)) * r["current_price"]
        for r in items
    )
    address = con.execute(
        "SELECT * FROM customers WHERE customer_id=?", (s["customer_id"],)
    ).fetchone()
    ctx = base_ctx(con, s, request, cats=_departments(con), title="Checkout")
    ctx.update({"items": items, "total": total, "msg": msg, "address": address})
    con.commit()
    con.close()
    return tpl.TemplateResponse(request, "checkout.html", ctx)


@router.post("/checkout/pay")
def checkout(
    payment_method: str = Form(...),
    address_line1: str = Form(...),
    address_line2: str = Form(""),
    city: str = Form(...),
    state: str = Form(...),
    region: str = Form(...),
    postal_code: str = Form(...),
    country: str = Form(...),
    phone: str = Form(...),
    sid: str = Cookie(None),
):
    con = db()
    s = get_session(con, sid)
    if not s:
        con.close()
        return RedirectResponse("/login", status_code=303)
    cid = s["customer_id"]
    items = con.execute(
        """SELECT c.quantity,p.* FROM cart c JOIN products p USING(product_id)
        WHERE c.customer_id=?""",
        (cid,),
    ).fetchall()
    if not items:
        con.close()
        return RedirectResponse("/cart", status_code=303)
    ts = now()
    oid = uid("ord")
    total = 0.0
    tx_rows = []
    inv_rows = []
    payment_methods = {
        "UPI",
        "Credit Card",
        "Debit Card",
        "Net Banking",
        "COD",
        "Amazon Pay",
    }
    if payment_method not in payment_methods:
        con.close()
        return RedirectResponse(
            "/checkout?msg=Select+a+valid+payment+method", status_code=303
        )
    # Checkout creates a pending demo order; the shopper can deliver or cancel it from Orders.
    for it in items:
        stock = con.execute(
            "SELECT current_stock FROM inventory WHERE product_id=?",
            (it["product_id"],),
        ).fetchone()
        buy = (
            min(int(it["quantity"]), max(int(stock["current_stock"]), 0))
            if stock
            else 0
        )
        if buy <= 0:
            continue
        price = float(it["current_price"])
        gross = round(price * buy, 2)
        discount = round(max(float(it["mrp"]) - price, 0) * buy, 2)
        net = round(gross, 2)
        txid = uid("txn")
        vals = (
            txid,
            oid,
            cid,
            it["product_id"],
            it["seller_id"],
            ts,
            buy,
            float(it["mrp"]),
            price,
            price,
            float(it["unit_cost"]),
            discount,
            None,
            "BAU",
            payment_method,
            "Placed",
            "Success",
            city,
            state,
            region,
            address_line1,
            address_line2,
            postal_code,
            country,
            phone,
            None,
            gross,
            net,
            round(net - float(it["unit_cost"]) * buy, 2),
            None,
        )
        con.execute(
            f"""INSERT INTO transactions
            (transaction_id,order_id,customer_id,product_id,seller_id,order_timestamp,quantity,mrp,listed_price,
             unit_selling_price,unit_cost,discount_amount,coupon_code,campaign_type,payment_method,order_status,
               payment_status,ship_city,ship_state,ship_region,ship_address_line1,ship_address_line2,ship_postal_code,
               ship_country,ship_phone,delivered_timestamp,gross_item_value,net_item_value,gross_margin,
             replacement_for_transaction_id) VALUES ({','.join('?' for _ in vals)})""",
            vals,
        )
        newstock = int(stock["current_stock"]) - buy
        con.execute(
            "UPDATE inventory SET current_stock=? WHERE product_id=?",
            (newstock, it["product_id"]),
        )
        tx_rows.append(
            dict(
                zip(
                    [
                        "transaction_id",
                        "order_id",
                        "customer_id",
                        "product_id",
                        "seller_id",
                        "order_timestamp",
                        "quantity",
                        "mrp",
                        "listed_price",
                        "unit_selling_price",
                        "unit_cost",
                        "discount_amount",
                        "coupon_code",
                        "campaign_type",
                        "payment_method",
                        "order_status",
                        "payment_status",
                        "ship_city",
                        "ship_state",
                        "ship_region",
                        "ship_address_line1",
                        "ship_address_line2",
                        "ship_postal_code",
                        "ship_country",
                        "ship_phone",
                        "delivered_timestamp",
                        "gross_item_value",
                        "net_item_value",
                        "gross_margin",
                        "replacement_for_transaction_id",
                    ],
                    vals,
                )
            )
        )
        inv_rows.append(
            {
                "product_id": it["product_id"],
                "current_stock": newstock,
                "base_stock": None,
            }
        )
        mirror_transaction(tx_rows[-1])
        mirror_inventory(
            {
                "product_id": it["product_id"],
                "current_stock": newstock,
                "base_stock": None,
            }
        )
        movement = {
            "movement_id": uid("mov"),
            "movement_timestamp": ts,
            "product_id": it["product_id"],
            "quantity_delta": -buy,
            "movement_type": "SALE",
            "order_id": oid,
            "transaction_id": txid,
            "reason": "website checkout",
        }
        con.execute(
            "INSERT INTO inventory_movements VALUES (?,?,?,?,?,?,?,?)",
            tuple(movement.values()),
        )
        mirror_inventory_movement(movement)
        log_event(
            con,
            cid,
            sid,
            s["device_type"],
            "payment_attempt",
            "payment",
            product_id=it["product_id"],
        )
        log_event(
            con,
            cid,
            sid,
            s["device_type"],
            "purchase",
            "confirmation",
            product_id=it["product_id"],
        )
        total += net
    if not tx_rows:
        con.close()
        return RedirectResponse("/cart?msg=Out+of+stock", status_code=303)
    order_vals = (
        oid,
        cid,
        ts,
        "Placed",
        "Success",
        round(total, 2),
        ts,
        None,
        ts,
    )
    con.execute("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?)", order_vals)
    mirror_order(
        dict(
            zip(
                [
                    "order_id",
                    "customer_id",
                    "order_timestamp",
                    "order_status",
                    "payment_status",
                    "total_amount",
                    "created_at",
                    "delivered_timestamp",
                    "updated_at",
                ],
                order_vals,
            )
        )
    )
    con.execute("DELETE FROM cart WHERE customer_id=?", (cid,))
    con.commit()
    con.close()
    return RedirectResponse("/orders", status_code=303)


@router.get("/orders", response_class=HTMLResponse)
def orders_page(request: Request, sid: str = Cookie(None)):
    con = db()
    s = get_session(con, sid)
    if not s:
        con.close()
        return RedirectResponse("/login", status_code=303)
    orders = con.execute(
        "SELECT * FROM orders WHERE customer_id=? ORDER BY order_timestamp DESC",
        (s["customer_id"],),
    ).fetchall()
    lines = con.execute(
        """SELECT t.*,p.product_name,p.image_url,p.h3_category FROM transactions t JOIN products p USING(product_id)
        WHERE t.customer_id=? ORDER BY t.order_timestamp DESC""",
        (s["customer_id"],),
    ).fetchall()
    grouped = {}
    for row in lines:
        grouped.setdefault(row["order_id"], []).append(row)
    cats = [
        r["h1_category"]
        for r in con.execute("SELECT DISTINCT h1_category FROM products ORDER BY 1")
    ]
    ctx = base_ctx(con, s, request, cats=cats, title="Your Orders")
    ctx.update({"orders": orders, "lines": grouped})
    con.commit()
    con.close()
    return tpl.TemplateResponse(request, "orders.html", ctx)


@router.post("/orders/{order_id}/status")
def update_order_status(
    order_id: str, action: str = Form(...), sid: str = Cookie(None)
):
    con = db()
    s = get_session(con, sid)
    if not s:
        con.close()
        return RedirectResponse("/login", status_code=303)
    order = con.execute(
        "SELECT * FROM orders WHERE order_id=? AND customer_id=?",
        (order_id, s["customer_id"]),
    ).fetchone()
    if not order:
        con.close()
        return RedirectResponse("/orders", status_code=303)
    if action not in {"deliver", "cancel", "return", "replace"}:
        con.close()
        return RedirectResponse("/orders", status_code=303)
    lines = con.execute(
        "SELECT * FROM transactions WHERE order_id=? AND customer_id=?",
        (order_id, s["customer_id"]),
    ).fetchall()
    ts = now()
    if action in {"deliver", "cancel"}:
        if order["order_status"] != "Placed":
            con.close()
            return RedirectResponse("/orders", status_code=303)
        changed = 0
        for x in lines:
            if x["order_status"] != "Placed":
                continue
            if action == "deliver":
                con.execute(
                    """UPDATE transactions
                       SET order_status='Delivered', delivered_timestamp=?
                       WHERE transaction_id=?""",
                    (ts, x["transaction_id"]),
                )
                log_event(
                    con,
                    s["customer_id"],
                    sid,
                    s["device_type"],
                    "order_delivered",
                    "orders",
                    x["product_id"],
                )
            else:
                con.execute(
                    """UPDATE transactions
                       SET order_status='Cancelled', payment_status='Refunded',
                           net_item_value=0, gross_margin=0
                       WHERE transaction_id=?""",
                    (x["transaction_id"],),
                )
                stock = con.execute(
                    "SELECT current_stock FROM inventory WHERE product_id=?",
                    (x["product_id"],),
                ).fetchone()
                newstock = int(stock["current_stock"]) + int(x["quantity"])
                con.execute(
                    "UPDATE inventory SET current_stock=? WHERE product_id=?",
                    (newstock, x["product_id"]),
                )
                mirror_inventory(
                    {
                        "product_id": x["product_id"],
                        "current_stock": newstock,
                        "base_stock": None,
                    }
                )
                movement = {
                    "movement_id": uid("mov"),
                    "movement_timestamp": ts,
                    "product_id": x["product_id"],
                    "quantity_delta": int(x["quantity"]),
                    "movement_type": "CANCELLATION",
                    "order_id": order_id,
                    "transaction_id": x["transaction_id"],
                    "reason": "customer order cancelled",
                }
                con.execute(
                    "INSERT INTO inventory_movements VALUES (?,?,?,?,?,?,?,?)",
                    tuple(movement.values()),
                )
                mirror_inventory_movement(movement)
                log_event(
                    con,
                    s["customer_id"],
                    sid,
                    s["device_type"],
                    "order_cancelled",
                    "orders",
                    x["product_id"],
                )
            mirror_transaction(
                dict(
                    con.execute(
                        "SELECT * FROM transactions WHERE transaction_id=?",
                        (x["transaction_id"],),
                    ).fetchone()
                )
            )
            changed += 1
        if changed:
            if action == "deliver":
                con.execute(
                    """UPDATE orders SET order_status='Delivered',
                       delivered_timestamp=?,updated_at=? WHERE order_id=?""",
                    (ts, ts, order_id),
                )
            else:
                con.execute(
                    """UPDATE orders SET order_status='Cancelled',
                       payment_status='Refunded',updated_at=? WHERE order_id=?""",
                    (ts, order_id),
                )
            mirror_order(
                dict(
                    con.execute(
                        "SELECT * FROM orders WHERE order_id=?", (order_id,)
                    ).fetchone()
                )
            )
        con.commit()
        con.close()
        return RedirectResponse("/orders", status_code=303)
    if order["order_status"] not in {"Delivered", "Partially Returned"}:
        con.close()
        return RedirectResponse("/orders", status_code=303)
    if action == "return":
        changed = 0
        for x in lines:
            if x["order_status"] not in {"Delivered", "Partially Returned"}:
                continue
            con.execute(
                "UPDATE transactions SET order_status='Returned' WHERE transaction_id=?",
                (x["transaction_id"],),
            )
            mirror_transaction(
                dict(
                    con.execute(
                        "SELECT * FROM transactions WHERE transaction_id=?",
                        (x["transaction_id"],),
                    ).fetchone()
                )
            )
            rid = uid("ret")
            rv = (
                rid,
                x["transaction_id"],
                order_id,
                x["customer_id"],
                x["product_id"],
                x["seller_id"],
                ts,
                ts,
                "Customer Return",
                "refund",
                x["net_item_value"],
                x["quantity"],
                "completed",
            )
            con.execute("INSERT INTO returns VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rv)
            mirror_return(
                dict(
                    zip(
                        [
                            "return_id",
                            "transaction_id",
                            "order_id",
                            "customer_id",
                            "product_id",
                            "seller_id",
                            "return_requested_at",
                            "return_received_at",
                            "return_reason",
                            "return_type",
                            "refund_amount",
                            "returned_quantity",
                            "return_status",
                        ],
                        rv,
                    )
                )
            )
            stock = con.execute(
                "SELECT current_stock FROM inventory WHERE product_id=?",
                (x["product_id"],),
            ).fetchone()
            newstock = int(stock["current_stock"]) + int(x["quantity"])
            con.execute(
                "UPDATE inventory SET current_stock=? WHERE product_id=?",
                (newstock, x["product_id"]),
            )
            mirror_inventory(
                {
                    "product_id": x["product_id"],
                    "current_stock": newstock,
                    "base_stock": None,
                }
            )
            movement = {
                "movement_id": uid("mov"),
                "movement_timestamp": ts,
                "product_id": x["product_id"],
                "quantity_delta": int(x["quantity"]),
                "movement_type": "RETURN",
                "order_id": order_id,
                "transaction_id": x["transaction_id"],
                "reason": "customer return accepted",
            }
            con.execute(
                "INSERT INTO inventory_movements VALUES (?,?,?,?,?,?,?,?)",
                tuple(movement.values()),
            )
            mirror_inventory_movement(movement)
            log_event(
                con,
                s["customer_id"],
                sid,
                s["device_type"],
                "return_completed",
                "orders",
                x["product_id"],
            )
            changed += 1
        if changed:
            status = "Returned"
            con.execute(
                "UPDATE orders SET order_status=?,updated_at=? WHERE order_id=?",
                (status, ts, order_id),
            )
            mirror_order(
                dict(
                    con.execute(
                        "SELECT * FROM orders WHERE order_id=?", (order_id,)
                    ).fetchone()
                )
            )
    else:
        # Same-SKU replacement: return the old unit into stock, then consume a replacement unit.
        changed = 0
        for x in lines:
            if x["order_status"] != "Delivered":
                continue
            stock = con.execute(
                "SELECT current_stock FROM inventory WHERE product_id=?",
                (x["product_id"],),
            ).fetchone()
            available = int(stock["current_stock"])
            if available < int(x["quantity"]):
                continue
            con.execute(
                "UPDATE transactions SET order_status='Replaced' WHERE transaction_id=?",
                (x["transaction_id"],),
            )
            mirror_transaction(
                dict(
                    con.execute(
                        "SELECT * FROM transactions WHERE transaction_id=?",
                        (x["transaction_id"],),
                    ).fetchone()
                )
            )
            # Return old unit first, then issue replacement: net inventory is unchanged.
            returned = available + int(x["quantity"])
            con.execute(
                "UPDATE inventory SET current_stock=? WHERE product_id=?",
                (returned, x["product_id"]),
            )
            mirror_inventory(
                {
                    "product_id": x["product_id"],
                    "current_stock": returned,
                    "base_stock": None,
                }
            )
            m1 = {
                "movement_id": uid("mov"),
                "movement_timestamp": ts,
                "product_id": x["product_id"],
                "quantity_delta": int(x["quantity"]),
                "movement_type": "REPLACEMENT_RETURN",
                "order_id": order_id,
                "transaction_id": x["transaction_id"],
                "reason": "old unit returned for replacement",
            }
            con.execute(
                "INSERT INTO inventory_movements VALUES (?,?,?,?,?,?,?,?)",
                tuple(m1.values()),
            )
            mirror_inventory_movement(m1)
            newstock = returned - int(x["quantity"])
            con.execute(
                "UPDATE inventory SET current_stock=? WHERE product_id=?",
                (newstock, x["product_id"]),
            )
            mirror_inventory(
                {
                    "product_id": x["product_id"],
                    "current_stock": newstock,
                    "base_stock": None,
                }
            )
            m2 = {
                "movement_id": uid("mov"),
                "movement_timestamp": ts,
                "product_id": x["product_id"],
                "quantity_delta": -int(x["quantity"]),
                "movement_type": "REPLACEMENT_ISSUE",
                "order_id": order_id,
                "transaction_id": x["transaction_id"],
                "reason": "replacement unit issued",
            }
            con.execute(
                "INSERT INTO inventory_movements VALUES (?,?,?,?,?,?,?,?)",
                tuple(m2.values()),
            )
            mirror_inventory_movement(m2)
            log_event(
                con,
                s["customer_id"],
                sid,
                s["device_type"],
                "replacement_completed",
                "orders",
                x["product_id"],
            )
            changed += 1
        if changed:
            con.execute(
                "UPDATE orders SET order_status='Replaced',updated_at=? WHERE order_id=?",
                (ts, order_id),
            )
            mirror_order(
                dict(
                    con.execute(
                        "SELECT * FROM orders WHERE order_id=?", (order_id,)
                    ).fetchone()
                )
            )
    con.commit()
    con.close()
    return RedirectResponse("/orders", status_code=303)


@router.post("/inventory/refresh")
def refresh_inventory(sid: str = Cookie(None)):
    con = db()
    s = get_session(con, sid)
    if not s:
        con.close()
        return JSONResponse(
            {"ok": False, "message": "Authentication required"}, status_code=401
        )
    rows = con.execute("SELECT product_id,base_stock FROM inventory").fetchall()
    ts = now()
    for r in rows:
        con.execute(
            "UPDATE inventory SET current_stock=? WHERE product_id=?",
            (r["base_stock"], r["product_id"]),
        )
        mirror_inventory(
            {
                "product_id": r["product_id"],
                "current_stock": r["base_stock"],
                "base_stock": r["base_stock"],
            }
        )
    con.commit()
    con.close()
    return JSONResponse({"ok": True, "message": "Inventory restocked to base levels"})
