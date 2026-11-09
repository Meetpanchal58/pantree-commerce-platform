"""Recommendation impression/click logging, A/B assignment and online metrics."""
import os
import sqlite3
import tempfile
import unittest
from itertools import count
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from recommendations import online_metrics
from webapp.rec_logging import assign_variant, log_click, log_impressions, mark_seen

_ids = count(1)


def uid(prefix):
    return f"{prefix}_{next(_ids)}"


ITEMS = [
    {"product_id": "p1", "score": 0.9, "source": "collaborative"},
    {"product_id": "p2", "score": 0.7, "source": "trending"},
    {"product_id": "p3", "score": 0.5, "source": "content"},
]


class VariantTests(unittest.TestCase):
    def test_everyone_is_default_without_the_experiment(self):
        with patch.dict(os.environ, {"PANTREE_AB_TEST": ""}):
            self.assertEqual(assign_variant("cust-1"), "default")

    def test_experiment_split_is_stable_and_roughly_even(self):
        with patch.dict(os.environ, {"PANTREE_AB_TEST": "ranker"}):
            first = [assign_variant(f"cust-{i}") for i in range(2000)]
            again = [assign_variant(f"cust-{i}") for i in range(2000)]
        self.assertEqual(first, again)
        share = first.count("treatment") / len(first)
        self.assertGreater(share, 0.45)
        self.assertLess(share, 0.55)


class LoggingTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.items = log_impressions(
            self.con, "req-1", "cust-1", "sess-1", "homepage", "personalized", "default",
            ITEMS, "2026-07-01T10:00:00", uid,
        )

    def test_served_items_are_logged_with_position(self):
        self.assertEqual([i["rec_position"] for i in self.items], [1, 2, 3])
        self.assertTrue(all(i["rec_request_id"] == "req-1" for i in self.items))
        rows = self.con.execute(
            "SELECT product_id, position, source, seen_at FROM recommendation_impressions ORDER BY position"
        ).fetchall()
        self.assertEqual(rows[0], ("p1", 1, "collaborative", None))
        self.assertEqual(len(rows), 3)

    def test_seen_only_updates_the_customers_own_impressions(self):
        updated = mark_seen(self.con, "cust-1", [{"request_id": "req-1", "position": 1},
                                                 {"request_id": "req-1", "position": "2"},
                                                 {"bad": "entry"}], "2026-07-01T10:00:05")
        self.assertEqual(updated, 2)
        self.assertEqual(mark_seen(self.con, "someone-else", [{"request_id": "req-1", "position": 3}], "t"), 0)
        self.assertEqual(mark_seen(self.con, "cust-1", [{"request_id": "req-1", "position": 1}], "t"), 0)

    def test_click_is_recorded_once_and_implies_seen(self):
        self.assertTrue(log_click(self.con, "req-1", "3", "cust-1", "p3", "2026-07-01T10:01:00", uid))
        self.assertFalse(log_click(self.con, "req-1", 3, "cust-1", "p3", "2026-07-01T10:02:00", uid))
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM recommendation_clicks").fetchone()[0], 1)
        seen = self.con.execute(
            "SELECT seen_at FROM recommendation_impressions WHERE position=3"
        ).fetchone()[0]
        self.assertEqual(seen, "2026-07-01T10:01:00")

    def test_tampered_links_are_ignored(self):
        self.assertFalse(log_click(self.con, "req-1", 1, "cust-1", "p2", "t", uid))   # wrong product
        self.assertFalse(log_click(self.con, "req-1", 1, "intruder", "p1", "t", uid))  # wrong customer
        self.assertFalse(log_click(self.con, "req-1", "x", "cust-1", "p1", "t", uid))  # bad position
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM recommendation_clicks").fetchone()[0], 0)


class OnlineMetricsTests(unittest.TestCase):
    def test_report_counts_ctr_and_post_click_outcomes(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "store.db"
            con = sqlite3.connect(db)
            con.execute("""CREATE TABLE clickstream (customer_id TEXT, product_id TEXT,
                           event_type TEXT, event_timestamp TEXT)""")
            con.execute("""CREATE TABLE transactions (customer_id TEXT, product_id TEXT,
                           order_timestamp TEXT, order_status TEXT, payment_status TEXT)""")
            with patch.dict(os.environ, {"PANTREE_AB_TEST": "ranker"}):
                for i in range(40):
                    cust = f"cust-{i}"
                    variant = assign_variant(cust)
                    log_impressions(con, f"req-{i}", cust, "s", "homepage", "personalized", variant,
                                    ITEMS, "2026-07-01T10:00:00", uid)
                    mark_seen(con, cust, [{"request_id": f"req-{i}", "position": p} for p in (1, 2, 3)], "t")
                    if variant == "treatment" or i % 4 == 0:
                        log_click(con, f"req-{i}", 1, cust, "p1", "2026-07-01T10:01:00", uid)
            con.execute("INSERT INTO clickstream VALUES ('cust-0','p1','add_to_cart','2026-07-01T10:05:00')")
            con.execute("INSERT INTO transactions VALUES ('cust-0','p1','2026-07-03T09:00:00','Placed','Success')")
            con.execute("INSERT INTO transactions VALUES ('cust-0','p1','2026-07-20T09:00:00','Placed','Success')")  # too late
            con.commit()
            con.close()

            frame = online_metrics.load(db)
            self.assertEqual(len(frame), 120)
            summary = online_metrics.summarise(frame, ["surface"]).iloc[0]
            self.assertEqual(summary["seen"], 120)
            self.assertEqual(summary["clicks"], int(frame["clicked"].sum()))
            self.assertEqual(summary["cart_after_click"], 1)
            self.assertEqual(summary["purchase_after_click"], 1)
            result = online_metrics.ab_test(frame)
            self.assertGreater(result["treatment_ctr"], result["control_ctr"])
            self.assertGreater(result["ci_low"], 0)
            report = online_metrics.main(db, Path(tmp) / "report.md")
            self.assertIn("A/B test", report)
            self.assertIn("CTR", report)

    def test_wilson_interval(self):
        low, high = online_metrics.wilson(10, 100)
        self.assertLess(low, 0.10)
        self.assertGreater(high, 0.10)
        self.assertEqual(online_metrics.wilson(0, 0), (0.0, 0.0))


class ServingControlGroupTests(unittest.TestCase):
    def test_control_group_never_loads_the_ranker(self):
        from recommendations import serve

        product = pd.DataFrame([{
            "product_id": "p1", "product_name": "Shirt", "image_url": None, "h1_category": "Apparel",
            "h2_category": "Topwear", "h3_category": "Shirts", "brand_name": "B", "current_price": 100,
            "gender": "Men", "is_active": True,
        }]).set_index("product_id", drop=False)
        config = {
            "candidate_sources": [("test", lambda **_: [{"product_id": "p1", "score": 0.5, "source": "test"}], 1)],
            "fusion_weights": {"test": 1.0}, "max_candidates": 5, "top_k": 5, "exclude_purchased": False,
        }
        with (
            patch.object(serve, "read_products", return_value=product),
            patch.object(serve, "read_stock", return_value=pd.Series({"p1": 3})),
            patch.object(serve, "event_counts_ready", return_value=True),
            patch.object(serve, "_load_ranker") as load_ranker,
        ):
            serve._recommend_from_config(None, "homepage", None, {"use_ranker": False}, config)
        load_ranker.assert_not_called()


if __name__ == "__main__":
    unittest.main()
