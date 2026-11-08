"""Tests for evaluation metrics, ranker features and fusion bookkeeping."""
import math
import unittest

import numpy as np
import pandas as pd

from recommendations.candidates.fusion import merge_candidates
from recommendations.evaluate import paired_bootstrap, power_label, session_metrics
from recommendations.ranker import (
    FEATURES,
    candidate_features,
    customer_profile,
    model_uses_current_features,
    score_candidates,
)


def catalog():
    return pd.DataFrame(
        [
            ("p1", "Tshirts", "Men", 500.0, 1000.0, 4.5),
            ("p2", "Tshirts", "Men", 1000.0, 1000.0, 4.0),
            ("p3", "Jeans", "Men", 2000.0, 2500.0, 3.5),
        ],
        columns=["product_id", "h3_category", "gender", "current_price", "mrp", "avg_rating"],
    ).set_index("product_id", drop=False)


class MetricTests(unittest.TestCase):
    def test_hit_in_second_place(self):
        recall, ndcg, mrr = session_metrics(np.array([0, 1, 0, 0, 0]), k=5)
        self.assertEqual(recall, 1.0)
        self.assertAlmostEqual(ndcg, 1 / math.log2(3))
        self.assertEqual(mrr, 0.5)

    def test_end_to_end_counts_items_missing_from_the_list(self):
        # Two products were bought but only one made it into the list.
        recall, ndcg, _ = session_metrics(np.array([1, 0, 0]), k=10, relevant=2)
        self.assertEqual(recall, 0.5)
        self.assertLess(ndcg, 1.0)

    def test_no_relevant_items_scores_zero(self):
        self.assertEqual(session_metrics(np.array([0, 0]), k=5, relevant=0), (0.0, 0.0, 0.0))

    def test_hit_outside_top_k_gets_no_credit(self):
        self.assertEqual(session_metrics(np.array([0, 0, 1]), k=2), (0.0, 0.0, 0.0))

    def test_power_labels(self):
        self.assertEqual(power_label(99), "UNDERPOWERED")
        self.assertEqual(power_label(100), "PROVISIONAL")
        self.assertEqual(power_label(500), "ADEQUATELY POWERED")

    def test_bootstrap_interval_contains_true_difference(self):
        rng = np.random.default_rng(0)
        a = rng.normal(0.3, 0.1, 400)
        b = a - 0.05 + rng.normal(0, 0.05, 400)
        result = paired_bootstrap(a, b)
        observed = float((a - b).mean())
        self.assertAlmostEqual(result["mean_diff"], observed)
        self.assertLess(result["ci_low"], observed)
        self.assertGreater(result["ci_high"], observed)
        self.assertGreater(result["ci_low"], 0)  # a real +0.05 gap is detected


class FeatureTests(unittest.TestCase):
    def test_profile_and_features(self):
        products = catalog()
        profile = customer_profile(["p1", "p2", "unknown"], products, purchase_count=2)
        self.assertEqual(profile["category_counts"]["Tshirts"], 2)
        self.assertEqual(profile["median_price"], 750.0)
        frame = pd.DataFrame(
            [{"product_id": "p3", "score": 0.6, "source": "session_pairing", "n_sources": 2}]
        )
        featured = candidate_features(frame, products, profile, pd.Series({"p3": 9}), anchor_product_id="p1")
        row = featured.iloc[0]
        self.assertEqual(row["source_id"], 2)  # mapped to the "bought together" family
        self.assertEqual(row["category_affinity"], 0.0)
        self.assertAlmostEqual(row["log_price_ratio"], math.log(2000 / 750))
        self.assertAlmostEqual(row["discount_pct"], 0.2)
        self.assertEqual(row["same_type_as_anchor"], 0.0)
        self.assertAlmostEqual(row["log_product_popularity"], math.log1p(9))
        self.assertAlmostEqual(row["log_customer_purchases"], math.log1p(2))
        self.assertTrue(set(FEATURES) <= set(featured.columns))

    def test_new_customer_gets_neutral_features(self):
        products = catalog()
        frame = pd.DataFrame([{"product_id": "p1", "score": 0.5, "source": "trending"}])
        row = candidate_features(frame, products, customer_profile([], products), None).iloc[0]
        self.assertEqual(row["log_price_ratio"], 0.0)
        self.assertEqual(row["category_affinity"], 0.0)
        self.assertEqual(row["n_sources"], 1.0)

    def test_scoring_without_a_compatible_model_keeps_retrieval_scores(self):
        candidates = [{"product_id": "p1", "score": 0.9, "source": "content"}]
        self.assertEqual(score_candidates(candidates, model=None)[0]["score"], 0.9)

        class OldModel:
            def feature_name(self):
                return ["customer_event_count", "product_event_count", "surface_id"]

        self.assertFalse(model_uses_current_features(OldModel()))
        self.assertEqual(score_candidates(candidates, model=OldModel(), products=catalog())[0]["score"], 0.9)


class FusionTests(unittest.TestCase):
    def test_fusion_counts_agreeing_sources_and_keeps_best(self):
        merged = merge_candidates(
            [
                {"product_id": "p1", "score": 0.4, "source": "content"},
                {"product_id": "p1", "score": 0.9, "source": "collaborative"},
                {"product_id": "p2", "score": 0.5, "source": "content"},
            ],
            weights={"content": 1.0, "collaborative": 0.5},
        )
        by_id = {item["product_id"]: item for item in merged}
        self.assertEqual(by_id["p1"]["n_sources"], 2)
        self.assertEqual(by_id["p1"]["source"], "collaborative")
        self.assertAlmostEqual(by_id["p1"]["score"], 0.45)
        self.assertEqual(by_id["p2"]["n_sources"], 1)


if __name__ == "__main__":
    unittest.main()


class SegmentAwarePolicyTests(unittest.TestCase):
    def _captured_sources(self, context):
        from unittest.mock import patch

        from recommendations import serve

        captured = {}

        def fake(customer_id, surface, product_id, ctx, config):
            captured.update(config)
            return []

        empty = pd.DataFrame(columns=["customer_id", "product_id", "order_timestamp"])
        with (
            patch.object(serve, "_recommend_from_config", side_effect=fake),
            patch.object(serve, "read_realized_transactions", return_value=empty),
        ):
            serve.recommend("cust-1", "homepage", {"intent": "personalized", **context})
        return [name for name, _, _ in captured["candidate_sources"]], captured["fusion_weights"]

    def test_first_time_buyer_sees_trending_first(self):
        names, weights = self._captured_sources({})
        self.assertIn("trending", names)
        self.assertGreater(weights["trending"], weights["recent_activity"])

    def test_returning_buyer_keeps_personal_sources_only(self):
        names, _ = self._captured_sources({"purchased_product_ids": ["p1"]})
        self.assertNotIn("trending", names)
