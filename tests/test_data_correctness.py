"""A1a: one purchase definition, ALS inputs and settings, cold-start orders."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from recommendations.candidates import collaborative
from recommendations.candidates.common import (
    NON_PURCHASE_STATUSES,
    purchase_mask,
    purchase_sql,
)

LINES = pd.DataFrame(
    [
        ("Delivered", "Success"),
        ("CIR", "Success"),       # delivered, later returned by the customer: a purchase
        ("Delivered", "Failed"),  # payment failed: not a purchase
        ("RTO", "Success"),
        ("CANCELLED", "Success"),
        ("CIC", "Success"),
        ("Lost", "Success"),
        ("Undelivered", "Success"),
        ("Placed", "SUCCESS"),
        (None, "Success"),
        ("Delivered", None),
    ],
    columns=["order_status", "payment_status"],
)
EXPECTED = [True, True, False, False, False, False, False, False, True, True, False]


class PurchaseDefinitionTests(unittest.TestCase):
    def test_cir_counts_and_failed_payments_do_not(self):
        self.assertEqual(purchase_mask(LINES).tolist(), EXPECTED)

    def test_sql_matches_the_pandas_rule(self):
        con = sqlite3.connect(":memory:")
        con.execute("CREATE TABLE t (i INTEGER, order_status TEXT, payment_status TEXT)")
        con.executemany(
            "INSERT INTO t VALUES (?, ?, ?)",
            [(i, *row) for i, row in enumerate(LINES.itertuples(index=False))],
        )
        kept = {i for (i,) in con.execute("SELECT i FROM t WHERE " + purchase_sql())}
        self.assertEqual(kept, {i for i, keep in enumerate(EXPECTED) if keep})

    def test_every_consumer_uses_the_shared_rule(self):
        root = Path(__file__).resolve().parents[1]
        for rel in ("webapp/routes/home.py", "recommendations/online_metrics.py"):
            source = (root / rel).read_text()
            self.assertIn("purchase_sql()", source, rel)
            for status in NON_PURCHASE_STATUSES:
                self.assertNotIn(f"'{status}'", source, f"{rel} hard-codes {status}")


class AlsInteractionTests(unittest.TestCase):
    def test_clickstream_purchase_events_are_not_counted(self):
        events = pd.DataFrame(
            [
                ("c1", "p1", "pdp_view", "2026-01-01 10:00"),
                ("c1", "p1", "purchase", "2026-01-01 10:05"),
                ("c1", "p2", "add_to_cart", "2026-01-02 10:00"),
            ],
            columns=["customer_id", "product_id", "event_type", "event_timestamp"],
        )
        orders = pd.DataFrame(
            [("c1", "p1", 2, pd.Timestamp("2026-01-01 10:05"))],
            columns=["customer_id", "product_id", "quantity", "order_timestamp"],
        )
        weights = collaborative.build_interactions(events, orders, (1.0, 3.0, 8.0))
        # view 1 + 2 units x 8 + same-day bonus 2 x 0.5; the purchase event adds nothing.
        self.assertEqual(weights[("c1", "p1")], 1.0 + 16.0 + 1.0)
        self.assertEqual(weights[("c1", "p2")], 3.0)

    def test_as_of_drops_later_events_and_orders(self):
        events = pd.DataFrame(
            [("c1", "p1", "pdp_view", "2026-02-01")],
            columns=["customer_id", "product_id", "event_type", "event_timestamp"],
        )
        orders = pd.DataFrame(
            [("c1", "p2", 1, pd.Timestamp("2026-02-01"))],
            columns=["customer_id", "product_id", "quantity", "order_timestamp"],
        )
        self.assertTrue(collaborative.build_interactions(events, orders, (1, 3, 8), "2026-01-15").empty)


class AlsParamsTests(unittest.TestCase):
    def tearDown(self):
        collaborative.als_params.cache_clear()

    def test_params_come_from_the_tuning_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "als_tuning.json"
            path.write_text(json.dumps({"best_on_validation": {
                "factors": 64, "regularization": 0.05, "iterations": 20, "weights": [1, 4, 10]}}))
            with patch.object(collaborative, "ALS_TUNING_PATH", path):
                collaborative.als_params.cache_clear()
                self.assertEqual(
                    collaborative.als_params(),
                    {"factors": 64, "regularization": 0.05, "iterations": 20, "weights": (1.0, 4.0, 10.0)},
                )

    def test_missing_report_falls_back_with_a_warning(self):
        with patch.object(collaborative, "ALS_TUNING_PATH", Path("/nonexistent/als.json")):
            collaborative.als_params.cache_clear()
            with self.assertLogs(collaborative.logger, "WARNING"):
                self.assertEqual(collaborative.als_params(), collaborative.FALLBACK_ALS_PARAMS)

    def test_committed_report_is_what_serving_uses(self):
        collaborative.als_params.cache_clear()
        best = json.loads(collaborative.ALS_TUNING_PATH.read_text())["best_on_validation"]
        params = collaborative.als_params()
        self.assertEqual(params["factors"], best["factors"])
        self.assertEqual(list(params["weights"]), [float(w) for w in best["weights"]])

    def test_no_second_copy_of_als_settings(self):
        source = Path(collaborative.__file__).read_text()
        self.assertNotIn("ALS_PARAMS =", source.replace("FALLBACK_ALS_PARAMS =", ""))
        self.assertNotIn("EVENT_WEIGHTS", source)


class ColdStartOrderTests(unittest.TestCase):
    def test_orders_without_history_are_kept_with_default_features(self):
        from recommendations import evaluate

        products = pd.DataFrame(
            [("p1", "Tshirts", "Men", 500.0, 1000.0, 4.5), ("p2", "Jeans", "Men", 900.0, 900.0, 4.0)],
            columns=["product_id", "h3_category", "gender", "current_price", "mrp", "avg_rating"],
        ).set_index("product_id", drop=False)
        ts = pd.Timestamp("2026-03-01")
        pools = pd.DataFrame([
            {"session_id": "warm", "customer_id": "c1", "anchor": "p1", "event_timestamp": ts,
             "product_id": "p2", "score": 0.5, "source": "content"},
            {"session_id": "cold", "customer_id": "c2", "anchor": None, "event_timestamp": ts,
             "product_id": "p1", "score": 0.4, "source": "trending"},
        ])

        class FakeHistory:
            def viewed_before(self, cid, ts):
                return ["p1"] if cid == "c1" else []

            def bought_list(self, cid, ts):
                return []

        class FakeSources:
            @staticmethod
            def popularity(ts):
                return pd.Series({"p1": 3, "p2": 1})

        out = evaluate.add_features(pools, FakeSources(), FakeHistory(), products)
        self.assertEqual(set(out["session_id"]), {"warm", "cold"})
        cold = out[out["session_id"] == "cold"].iloc[0]
        self.assertEqual(cold["category_affinity"], 0.0)
        self.assertEqual(cold["log_price_ratio"], 0.0)
        self.assertEqual(cold["same_type_as_anchor"], 0.0)


if __name__ == "__main__":
    unittest.main()
