"""Recommendation impression and click logging, plus A/B variant assignment.

Every recommendation the API serves is stored in ``recommendation_impressions``
with the request it came from, its position, the source that produced it and
the experiment variant. The browser marks a card as *seen* once at least half
of it is on screen (``seen_at``), and opening a recommended product records a
row in ``recommendation_clicks``. ``recommendations/online_metrics.py`` turns
these tables into click-through and conversion reports.
"""
import hashlib
import os

MAX_SEEN_PER_REQUEST = 200
AB_SALT = "pantree-ranker-ab-v1"


def ensure_rec_tables(con) -> None:
    con.execute(
        """CREATE TABLE IF NOT EXISTS recommendation_impressions (
            impression_id TEXT PRIMARY KEY,
            request_id TEXT NOT NULL,
            customer_id TEXT NOT NULL,
            session_id TEXT,
            surface TEXT NOT NULL,
            intent TEXT,
            variant TEXT NOT NULL,
            product_id TEXT NOT NULL,
            position INTEGER NOT NULL,
            source TEXT,
            score REAL,
            served_at TEXT NOT NULL,
            seen_at TEXT
        )"""
    )
    con.execute(
        """CREATE TABLE IF NOT EXISTS recommendation_clicks (
            click_id TEXT PRIMARY KEY,
            impression_id TEXT NOT NULL,
            request_id TEXT NOT NULL,
            customer_id TEXT NOT NULL,
            product_id TEXT NOT NULL,
            position INTEGER NOT NULL,
            clicked_at TEXT NOT NULL
        )"""
    )
    con.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_rec_impression_request_position "
        "ON recommendation_impressions(request_id, position)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS ix_rec_impression_customer "
        "ON recommendation_impressions(customer_id, served_at)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS ix_rec_click_impression ON recommendation_clicks(impression_id)"
    )


def assign_variant(customer_id: str) -> str:
    """Experiment group for a customer.

    With ``PANTREE_AB_TEST=ranker`` customers are split 50/50, deterministically
    by a hash of their ID (the same customer always lands in the same group):
    ``control`` sees retrieval order without the ranker, ``treatment`` sees the
    ranker. Otherwise everyone is in ``default`` (ranker used when available).
    """
    if os.getenv("PANTREE_AB_TEST", "").strip().lower() != "ranker":
        return "default"
    digest = hashlib.sha256(f"{AB_SALT}:{customer_id}".encode()).digest()
    return "treatment" if digest[0] % 2 else "control"


def log_impressions(con, request_id, customer_id, session_id, surface, intent, variant, items, served_at, uid):
    """Store every served recommendation; returns items with request id and position."""
    ensure_rec_tables(con)
    tagged = []
    for position, item in enumerate(items, start=1):
        con.execute(
            """INSERT INTO recommendation_impressions
               (impression_id, request_id, customer_id, session_id, surface, intent, variant,
                product_id, position, source, score, served_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                uid("imp"), request_id, customer_id, session_id, surface, intent, variant,
                item["product_id"], position, item.get("source"),
                float(item["score"]) if item.get("score") is not None else None, served_at,
            ),
        )
        tagged.append({**item, "rec_request_id": request_id, "rec_position": position})
    return tagged


def mark_seen(con, customer_id, seen, seen_at) -> int:
    """Mark (request_id, position) pairs as seen for this customer; returns rows updated."""
    ensure_rec_tables(con)
    updated = 0
    for entry in seen[:MAX_SEEN_PER_REQUEST]:
        try:
            request_id = str(entry["request_id"])
            position = int(entry["position"])
        except (KeyError, TypeError, ValueError):
            continue
        cursor = con.execute(
            """UPDATE recommendation_impressions SET seen_at=?
               WHERE request_id=? AND position=? AND customer_id=? AND seen_at IS NULL""",
            (seen_at, request_id, position, customer_id),
        )
        updated += cursor.rowcount
    return updated


def log_click(con, request_id, position, customer_id, product_id, clicked_at, uid) -> bool:
    """Record a click on a served recommendation. Ignores links that don't match."""
    ensure_rec_tables(con)
    try:
        position = int(position)
    except (TypeError, ValueError):
        return False
    row = con.execute(
        """SELECT impression_id FROM recommendation_impressions
           WHERE request_id=? AND position=? AND customer_id=? AND product_id=?""",
        (request_id, position, customer_id, product_id),
    ).fetchone()
    if row is None:
        return False
    impression_id = row[0]
    already = con.execute(
        "SELECT 1 FROM recommendation_clicks WHERE impression_id=?", (impression_id,)
    ).fetchone()
    if already:
        return False
    con.execute(
        """INSERT INTO recommendation_clicks
           (click_id, impression_id, request_id, customer_id, product_id, position, clicked_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (uid("clk"), impression_id, request_id, customer_id, product_id, position, clicked_at),
    )
    # A click implies the card was seen, even if the visibility beacon was lost.
    con.execute(
        "UPDATE recommendation_impressions SET seen_at=COALESCE(seen_at, ?) WHERE impression_id=?",
        (clicked_at, impression_id),
    )
    return True
