"""A shopper's whole journey through the storefront, on SQLite and on Postgres.

Postgres runs when PANTREE_TEST_DATABASE_URL points at a disposable database (CI starts
one); its tables are dropped and seeded again. The journey: browse, search, view a
product, add to cart and wishlist, check out, see orders and account, get recommendations,
then reset recommendations and check what is kept and what is deleted.
"""
import asyncio
import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
WEBAPP = ROOT / "webapp"
sys.path.insert(0, str(WEBAPP))
os.environ.setdefault("APP_ENV", "test")

from fastapi.testclient import TestClient  # noqa: E402

import webapp.app as storefront  # noqa: E402
from webapp import analytics_sync, core, database  # noqa: E402

PG_URL = os.getenv("PANTREE_TEST_DATABASE_URL", "")
HAS_DATA = (ROOT / "data" / "product_master.parquet").exists()


def _reset_pools():
    for module in (database, sys.modules.get("database")):
        if module is not None and getattr(module, "_POOL", None) is not None:
            module._POOL.close()
            module._POOL = None
    core._SCHEMA_CHECKED.clear()


@unittest.skipUnless(HAS_DATA, "needs generated data (python generate_data.py --mode initialize)")
class JourneyMixin:
    backend = "sqlite"

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.env = patch.dict(os.environ, {"DATABASE_URL": PG_URL if cls.backend == "postgres" else ""})
        cls.env.start()
        _reset_pools()
        cls.patches = [
            patch.object(core, "DB", Path(cls.tmp.name) / "live.db"),
            patch.object(storefront, "DB", Path(cls.tmp.name) / "live.db"),
            patch.object(analytics_sync, "DUCKDB", Path(cls.tmp.name) / "mirror.duckdb"),
        ]
        for p in cls.patches:
            p.start()
        seed_db = importlib.import_module("seed_db")
        sys.modules["analytics_sync"].DUCKDB = Path(cls.tmp.name) / "mirror.duckdb"
        with patch.object(seed_db, "DB", Path(cls.tmp.name) / "live.db"):
            seed_db.main(["--reset"] if cls.backend == "postgres" else [])

    @classmethod
    def tearDownClass(cls):
        for p in cls.patches:
            p.stop()
        _reset_pools()
        cls.env.stop()
        cls.tmp.cleanup()

    def setUp(self):
        con = core.db()
        core.ensure_auth_tables(con)
        self.customer_id = con.execute("SELECT customer_id FROM customers ORDER BY customer_id LIMIT 1").fetchone()[0]
        self.product_id = con.execute(
            "SELECT product_id FROM inventory WHERE current_stock >= 5 ORDER BY product_id LIMIT 1"
        ).fetchone()[0]
        # each test starts from a customer with no activity
        core.reset_customer_activity(con, self.customer_id, keep_session_id=None, now=core.now(), new_id=core.uid)
        con.execute("UPDATE customers SET recommendations_reset_at=NULL WHERE customer_id=?", (self.customer_id,))
        con.execute("DELETE FROM sessions WHERE session_id IN ('journey-sid', 'other-sid')")
        con.execute("DELETE FROM supertokens_customers WHERE supertokens_user_id='st-journey'")
        con.execute(
            "INSERT INTO customer_pii (customer_id, first_name, email) VALUES (?, 'Journey', ?) ON CONFLICT DO NOTHING",
            (self.customer_id, f"{self.customer_id.lower()}@example.com"),
        )
        con.execute(
            "INSERT INTO supertokens_customers (supertokens_user_id, customer_id, created_at) VALUES (?, ?, ?)",
            ("st-journey", self.customer_id, core.now()),
        )
        for sid in ("journey-sid", "other-sid"):
            con.execute(
                """INSERT INTO sessions (session_id, customer_id, device_type, created_at, supertokens_session_handle)
                   VALUES (?, ?, 'Desktop Web', ?, 'st-handle')""",
                (sid, self.customer_id, core.now()),
            )
        con.commit()
        con.close()
        auth = SimpleNamespace(get_handle=lambda: "st-handle", get_user_id=lambda: "st-journey")
        self.session_patch = patch.object(storefront.session_async, "get_session", new=AsyncMock(return_value=auth))
        self.session_patch.start()
        storefront.app.state.homepage_feed_responses = 0  # normally set at app start-up
        storefront.app.state.homepage_feeds_ready = asyncio.Event()
        self.client = TestClient(storefront.app)
        self.client.cookies.set("sid", "journey-sid")

    def tearDown(self):
        self.session_patch.stop()

    def count(self, sql, *params):
        con = core.db()
        try:
            return con.execute(sql, params).fetchone()[0]
        finally:
            con.close()

    def test_shopper_journey_then_reset(self):
        c, cid, pid = self.client, self.customer_id, self.product_id
        stock_before = self.count("SELECT current_stock FROM inventory WHERE product_id=?", pid)

        for path in ("/", "/search?q=shirt", f"/product/{pid}", "/browse"):
            self.assertEqual(c.get(path).status_code, 200, path)
        self.assertTrue(c.post("/cart/add", data={"product_id": pid, "qty": 2}).json()["ok"])
        self.assertTrue(c.post("/cart/add", data={"product_id": pid, "qty": 1}).json()["ok"])
        self.assertEqual(self.count("SELECT quantity FROM cart WHERE customer_id=? AND product_id=?", cid, pid), 3)
        self.assertEqual(c.post("/wishlist/toggle", data={"product_id": pid}).status_code, 200)
        for path in ("/cart", "/checkout"):
            self.assertEqual(c.get(path).status_code, 200, path)
        paid = c.post("/checkout/pay", follow_redirects=False, data={
            "payment_method": "UPI", "address_line1": "1 Test Road", "city": "Pune", "state": "Maharashtra",
            "region": "West", "postal_code": "411001", "country": "India", "phone": "+919000000000"})
        self.assertEqual(paid.status_code, 303)
        self.assertEqual(self.count("SELECT COUNT(*) FROM orders WHERE customer_id=?", cid), 1)
        self.assertEqual(self.count("SELECT current_stock FROM inventory WHERE product_id=?", pid), stock_before - 3)
        for path in ("/orders", "/account"):
            self.assertEqual(c.get(path).status_code, 200, path)
        recs = c.get("/api/recommendations/homepage?intent=trending")
        self.assertEqual(recs.status_code, 200)
        self.assertGreater(self.count("SELECT COUNT(*) FROM clickstream WHERE customer_id=?", cid), 0)

        reset = c.post("/account/reset-recommendations", follow_redirects=False)
        self.assertEqual(reset.status_code, 303)
        self.assertIn("reset", reset.headers["location"])
        con = core.db()
        existing = database.table_names(con)
        con.close()
        for table in ("clickstream", "cart", "wishlist", "orders", "transactions", "recommendation_impressions"):
            if table in existing:
                self.assertEqual(self.count(f"SELECT COUNT(*) FROM {table} WHERE customer_id=?", cid), 0, table)
        # the open order's units went back on the shelf, recorded as a movement
        self.assertEqual(self.count("SELECT current_stock FROM inventory WHERE product_id=?", pid), stock_before)
        self.assertEqual(self.count(
            "SELECT COUNT(*) FROM inventory_movements WHERE movement_type='ACCOUNT_RESET' AND product_id=?", pid), 1)
        # account, sign-in link and the current login are kept; other sessions are ended
        self.assertEqual(self.count("SELECT COUNT(*) FROM customers WHERE customer_id=?", cid), 1)
        self.assertEqual(self.count("SELECT COUNT(*) FROM customer_pii WHERE customer_id=?", cid), 1)
        self.assertEqual(self.count("SELECT COUNT(*) FROM supertokens_customers WHERE customer_id=?", cid), 1)
        self.assertEqual(self.count("SELECT COUNT(*) FROM sessions WHERE session_id='journey-sid'"), 1)
        self.assertEqual(self.count("SELECT COUNT(*) FROM sessions WHERE session_id='other-sid'"), 0)
        self.assertIsNotNone(self.count("SELECT recommendations_reset_at FROM customers WHERE customer_id=?", cid))
        self.assertEqual(c.get("/account").status_code, 200)

    def test_cart_edits_order_actions_and_recommendation_rows(self):
        c, cid, pid = self.client, self.customer_id, self.product_id
        address = {"payment_method": "COD", "address_line1": "2 Test Road", "city": "Pune", "state": "Maharashtra",
                   "region": "West", "postal_code": "411001", "country": "India", "phone": "+919000000000"}
        c.post("/cart/add", data={"product_id": pid, "qty": 3})
        self.assertEqual(c.post("/cart/update", data={"product_id": pid, "delta": -1}).status_code, 200)
        self.assertEqual(self.count("SELECT quantity FROM cart WHERE customer_id=? AND product_id=?", cid, pid), 2)
        self.assertEqual(c.post("/cart/remove", data={"product_id": pid}).status_code, 200)
        self.assertEqual(self.count("SELECT COUNT(*) FROM cart WHERE customer_id=?", cid), 0)

        orders = []
        for _ in range(2):
            c.post("/cart/add", data={"product_id": pid, "qty": 1})
            self.assertEqual(c.post("/checkout/pay", data=address, follow_redirects=False).status_code, 303)
        con = core.db()
        orders = [r[0] for r in con.execute(
            "SELECT order_id FROM orders WHERE customer_id=? ORDER BY order_id", (cid,)).fetchall()]
        con.close()
        self.assertEqual(len(orders), 2)
        stock = self.count("SELECT current_stock FROM inventory WHERE product_id=?", pid)
        self.assertEqual(c.post(f"/orders/{orders[0]}/status", data={"action": "cancel"}).status_code, 200)
        self.assertEqual(self.count("SELECT current_stock FROM inventory WHERE product_id=?", pid), stock + 1)
        self.assertEqual(c.post(f"/orders/{orders[1]}/status", data={"action": "deliver"}).status_code, 200)
        self.assertEqual(c.post(f"/orders/{orders[1]}/status", data={"action": "return"}).status_code, 200)
        statuses = {self.count("SELECT order_status FROM orders WHERE order_id=?", o) for o in orders}
        self.assertEqual(statuses, {"Cancelled", "Returned"})

        row = c.get("/api/recommendations/homepage?intent=personalized&limit=6")
        self.assertEqual(row.status_code, 200)
        items = row.json()
        if items:  # cards carry the ids the page sends back when they scroll into view
            seen = c.post("/api/rec-events/seen", json={"seen": [
                {"request_id": items[0]["rec_request_id"], "position": items[0]["rec_position"]}]})
            self.assertEqual(seen.json()["updated"], 1)
        self.assertEqual(c.get(f"/product/{pid}").status_code, 200)
        for path in ("/orders", "/account"):
            self.assertEqual(c.get(path).status_code, 200, path)

    def test_reset_requires_login(self):
        self.client.cookies.clear()
        response = self.client.post("/account/reset-recommendations", follow_redirects=False)
        self.assertIn(response.status_code, (303, 401))


class SQLiteJourneyTests(JourneyMixin, unittest.TestCase):
    backend = "sqlite"


@unittest.skipUnless(PG_URL, "set PANTREE_TEST_DATABASE_URL to run against Postgres")
class PostgresJourneyTests(JourneyMixin, unittest.TestCase):
    backend = "postgres"


class TranslateTests(unittest.TestCase):
    def test_placeholders_like_and_types(self):
        sql = database.translate("SELECT * FROM t WHERE a=? AND b LIKE ? AND c='?' AND d REAL", True)
        self.assertEqual(sql, "SELECT * FROM t WHERE a=%s AND b ILIKE %s AND c='?' AND d DOUBLE PRECISION")

    def test_percent_escaped_only_with_params(self):
        self.assertEqual(database.translate("SELECT '5%' , ?", True), "SELECT '5%%' , %s")
        self.assertEqual(database.translate("SELECT '5%'", False), "SELECT '5%'")

    def test_sqlite_only_statements(self):
        self.assertIsNone(database.translate("PRAGMA foreign_keys=ON;", False))
        self.assertIn("pg_advisory_xact_lock", database.translate("BEGIN IMMEDIATE", False))


if __name__ == "__main__":
    unittest.main()
