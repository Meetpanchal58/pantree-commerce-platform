"""Reset my recommendations: delete a customer's activity but keep their account.

Kept:    the account (customers, customer_pii), the SuperTokens link and the current login.
Deleted: clickstream, recommendation impressions and clicks, cart, wishlist, orders,
         order lines and returns, and other login sessions.
Stock:   units of orders that were placed but not delivered, cancelled or returned go back
         on the shelf, recorded as an inventory movement like a cancellation.
Model:   customers.recommendations_reset_at is set, so serving ignores anything learned
         about this customer before the reset (warehouse history and stored model vectors).
"""
from __future__ import annotations

try:
    from . import database
except ImportError:
    import database

# (table, column holding the customer id), deleted in this order
ACTIVITY_TABLES = [
    ("recommendation_clicks", "customer_id"),
    ("recommendation_impressions", "customer_id"),
    ("clickstream", "customer_id"),
    ("cart", "customer_id"),
    ("wishlist", "customer_id"),
    ("returns", "customer_id"),
    ("transactions", "customer_id"),
    ("orders", "customer_id"),
]


def reset_customer_activity(con, customer_id: str, keep_session_id: str | None, now: str, new_id) -> dict:
    """Delete the customer's activity in one transaction; returns rows deleted per table.

    `now` is the reset timestamp and `new_id(prefix)` makes ids for inventory movements.
    The caller commits.
    """
    tables = database.table_names(con)
    restocked = []
    if {"transactions", "inventory"} <= tables:
        open_lines = con.execute(
            """SELECT transaction_id, order_id, product_id, quantity FROM transactions
               WHERE customer_id=? AND order_status='Placed'""",
            (customer_id,),
        ).fetchall()
        for line in open_lines:
            con.execute(
                "UPDATE inventory SET current_stock = current_stock + ? WHERE product_id=?",
                (int(line["quantity"]), line["product_id"]),
            )
            if "inventory_movements" in tables:
                con.execute(
                    """INSERT INTO inventory_movements
                       (movement_id, movement_timestamp, product_id, quantity_delta, movement_type,
                        order_id, transaction_id, reason) VALUES (?,?,?,?,?,?,?,?)""",
                    (new_id("mov"), now, line["product_id"], int(line["quantity"]), "ACCOUNT_RESET",
                     line["order_id"], line["transaction_id"], "open order removed by account reset"),
                )
            restocked.append(line["product_id"])

    deleted = {}
    for table, column in ACTIVITY_TABLES:
        if table in tables:
            deleted[table] = con.execute(f"DELETE FROM {table} WHERE {column}=?", (customer_id,)).rowcount
    if "sessions" in tables:
        deleted["sessions"] = con.execute(
            "DELETE FROM sessions WHERE customer_id=? AND session_id <> ?", (customer_id, keep_session_id or "")
        ).rowcount
    con.execute("UPDATE customers SET recommendations_reset_at=? WHERE customer_id=?", (now, customer_id))
    deleted["restocked_lines"] = len(restocked)
    return deleted
