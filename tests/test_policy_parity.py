"""Serving and evaluation run the same personalised policy (A1b).

On a small fixture, serve.recommend(intent="personalized") and the evaluation's
candidate builder + scorer must produce the same products, in the same order,
with the same scores, for the same customer at the same moment.
"""
import unittest
from contextlib import ExitStack
from unittest.mock import patch

import numpy as np
import pandas as pd

from recommendations import evaluate, policy, serve
from recommendations.candidates import common, content, fbt, popularity
from recommendations.ranker import FEATURES

T0 = pd.Timestamp("2026-03-01 09:00")


def fixture_products():
    rows = [
        ("p1", "Tshirts", "Men"), ("p2", "Tshirts", "Men"), ("p3", "Tshirts", "Men"),
        ("p4", "Jeans", "Men"), ("p5", "Jeans", "Men"), ("p6", "Jeans", "Unisex"),
        ("p7", "Tshirts", "Women"), ("p8", "Jeans", "Women"),
    ]
    frame = pd.DataFrame(rows, columns=["product_id", "h3_category", "gender"])
    frame["h1_category"] = "Apparel"
    frame["h2_category"] = "Topwear"
    frame["product_name"] = frame["product_id"].str.upper()
    frame["brand_name"] = "Brand"
    frame["image_url"] = ""
    frame["current_price"] = [500.0, 600, 700, 1200, 1300, 1100, 650, 1250]
    frame["mrp"] = frame["current_price"] * 1.2
    frame["avg_rating"] = [4.0, 4.5, 3.5, 4.2, 3.9, 4.8, 4.1, 3.0]
    frame["review_count"] = 10
    frame["is_active"] = True
    return frame.set_index("product_id", drop=False)


def fixture_activity():
    rows = [
        ("c1", "p1", "pdp_view", T0),
        ("c1", "p2", "add_to_cart", T0 + pd.Timedelta(hours=1)),
        ("c2", "p4", "pdp_view", T0 + pd.Timedelta(hours=2)),
        ("c2", "p5", "pdp_view", T0 + pd.Timedelta(hours=3)),
        ("c3", "p5", "pdp_view", T0 + pd.Timedelta(hours=4)),
        ("c3", "p6", "wishlist_add", T0 + pd.Timedelta(hours=5)),
        ("c4", "p7", "pdp_view", T0 + pd.Timedelta(hours=6)),
    ]
    events = pd.DataFrame(rows, columns=["customer_id", "product_id", "event_type", "event_timestamp"])
    events["session_id"] = events["customer_id"] + "-s"
    events["weight"] = 1.0
    return events


def fixture_orders():
    rows = [
        ("c1", "p3", "o1", T0 - pd.Timedelta(days=3)),
        ("c2", "p3", "o2", T0 - pd.Timedelta(days=2)),
        ("c2", "p4", "o2", T0 - pd.Timedelta(days=2)),
        ("c3", "p3", "o3", T0 - pd.Timedelta(days=1)),
        ("c3", "p6", "o3", T0 - pd.Timedelta(days=1)),
    ]
    orders = pd.DataFrame(rows, columns=["customer_id", "product_id", "order_id", "order_timestamp"])
    orders["quantity"] = 1
    return orders


class FakeRanker:
    """Deterministic stand-in for the LightGBM booster."""

    def feature_name(self):
        return list(FEATURES)

    def predict(self, matrix):
        weights = np.linspace(1.0, 2.0, matrix.shape[1])
        return matrix @ weights


def fake_als(customer_id, limit=100, as_of=None, params=None):
    lists = {"c1": [("p5", 1.0), ("p6", 0.8), ("p2", 0.4)], "c4": [("p8", 1.0)]}
    return [{"product_id": p, "score": s, "source": "collaborative"} for p, s in lists.get(customer_id, [])][:limit]


def fake_similar(product_id, limit=30, exclude_product_ids=None):
    return [{"product_id": p, "score": s, "source": "content"} for p, s in (("p1", 0.9), ("p2", 0.7), ("p7", 0.5))
            if p != product_id][:limit]


class PolicyParityTests(unittest.TestCase):
    def setUp(self):
        self.products = fixture_products()
        self.activity = fixture_activity()
        self.orders = fixture_orders()
        self.stock = pd.Series({pid: 5 for pid in self.products.index})
        self.stock["p6"] = 0  # out of stock: must be dropped by both paths
        self.as_of = self.activity["event_timestamp"].max() + pd.Timedelta(microseconds=1)
        stack = ExitStack()
        self.addCleanup(stack.close)
        history = evaluate.History(self.activity, self.orders)
        histories = {
            cid: {"bought": history.bought_list(cid, self.as_of), "viewed": history.viewed_before(cid, self.as_of)}
            for cid in ("c1", "c2", "c3", "c4")
        }
        self.history = history
        for target, name, value in [
            (popularity, "read_clickstream_activity", self.activity),
            (popularity, "read_products", self.products),
            (fbt, "read_realized_transactions", self.orders),
            (fbt, "realized_transactions_ready", True),
            (fbt, "read_products", self.products),
            (common, "read_products", self.products),
            (common, "read_stock", self.stock),
            (serve, "read_products", self.products),
            (serve, "customer_histories", histories),
            (serve, "customer_histories_ready", True),
        ]:
            stack.enter_context(patch.object(target, name, return_value=value))
        stack.enter_context(patch.object(policy.collaborative, "get_collaborative", side_effect=fake_als))
        stack.enter_context(patch.object(content, "get_similar_products", side_effect=fake_similar))
        stack.enter_context(patch.object(serve, "_load_ranker", return_value=FakeRanker()))
        # Point-in-time availability at as_of agrees with "now" on this fixture.
        stock = self.stock

        class StockOnly:
            def available(self, product_ids, as_of):
                return [bool(stock.get(pid, 0) > 0) for pid in product_ids]

        stack.enter_context(patch.object(common, "availability_index", return_value=StockOnly()))
        popularity.clear_caches()
        self.addCleanup(popularity.clear_caches)
        fbt.clear_caches()
        self.addCleanup(fbt.clear_caches)

    def evaluated(self, customer_id):
        queries = pd.DataFrame([{
            "order_id": "q", "customer_id": customer_id, "query_time": self.as_of,
            "purchased": {"p4"}, "customer_event_count": 0,
        }])
        config = policy.load_config()  # the same chosen settings serving reads
        sources = policy.Sources(model_as_of=self.as_of, use_content=True,
                                 trending_window_days=config.trending_window_days)
        pools, _ = evaluate.build_pools(queries, sources, self.history, config)
        featured = evaluate.add_features(pools, sources, self.history, self.products)
        raw = FakeRanker().predict(np.ascontiguousarray(featured[FEATURES].to_numpy(dtype=np.float32)))
        scores = policy.final_scores(
            featured["retrieval_score"].to_numpy(), featured["product_event_count"].to_numpy(),
            bool(featured["first_timer"].iloc[0]), raw, config,
        )
        ranked = sorted(zip(featured["product_id"], scores), key=lambda x: (-x[1], x[0]))
        return [(p, round(float(s), 9)) for p, s in ranked]

    def served(self, customer_id):
        with patch.object(popularity, "activity_ready", return_value=True):
            items = serve.recommend(customer_id, "homepage", {"intent": "personalized", "limit": 30})
        return [(item["product_id"], round(float(item["score"]), 9)) for item in items]

    def test_returning_customer_gets_identical_lists_and_scores(self):
        served = self.served("c1")
        self.assertTrue(served)
        self.assertNotIn("p3", [p for p, _ in served])  # already bought
        self.assertNotIn("p6", [p for p, _ in served])  # out of stock
        self.assertEqual(served, self.evaluated("c1"))

    def test_first_time_customer_gets_identical_lists_and_scores(self):
        served = self.served("c4")
        self.assertTrue(served)
        self.assertEqual(served, self.evaluated("c4"))

    def test_parity_holds_for_non_default_settings(self):
        config = policy.PolicyConfig(quotas=(2, 1, 1, 1, 1), pool_size=5, ranker_blend=0.8,
                                     first_timer_popularity=False, trending_window_days=7)
        with patch.object(policy, "load_config", return_value=config):
            self.assertEqual(self.served("c1"), self.evaluated("c1"))
            self.assertEqual(self.served("c4"), self.evaluated("c4"))
            self.assertLessEqual(len(self.served("c1")), 5)

    def test_first_timer_order_is_popularity(self):
        with patch.object(policy, "load_config", return_value=policy.DEFAULT_CONFIG):
            served = self.served("c4")
        counts = popularity.activity_counts(None)
        pops = [counts.get(p, 0) for p, _ in served]
        self.assertEqual(pops, sorted(pops, reverse=True))


if __name__ == "__main__":
    unittest.main()
