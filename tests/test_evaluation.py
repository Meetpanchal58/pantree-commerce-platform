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
    def test_first_time_buyer_list_is_ordered_by_popularity(self):
        from recommendations.policy import final_scores

        scores = final_scores(np.array([0.9, 0.1, 0.5]), np.array([1, 10, 5]), first_timer=True,
                              ranker_scores=np.array([3.0, 1.0, 2.0]))
        self.assertEqual(list(np.argsort(-scores)), [1, 2, 0])

    def test_returning_buyer_gets_the_ranker_blend(self):
        from recommendations.policy import final_scores

        scores = final_scores(np.array([0.2, 0.4]), np.array([100, 0]), first_timer=False,
                              ranker_scores=np.array([5.0, 1.0]))
        np.testing.assert_allclose(scores, [0.5 * 0.2 + 0.5 * 1.0, 0.5 * 0.4 + 0.0])

    def test_without_a_ranker_returning_buyers_keep_retrieval_order(self):
        from recommendations.policy import final_scores

        np.testing.assert_allclose(final_scores(np.array([0.3, 0.6]), np.array([9, 1]), False, None), [0.3, 0.6])

    def test_builder_applies_quotas_then_fills_by_score(self):
        from unittest.mock import patch

        from recommendations import policy

        lists = {name: [] for name in policy.SOURCES}
        lists["collaborative"] = [{"product_id": f"a{i}", "score": 1 - i / 100} for i in range(30)]
        lists["trending"] = [{"product_id": f"t{i}", "score": 0.5 - i / 100} for i in range(30)]
        lists["content"] = [{"product_id": "a0", "score": 0.2}]
        state = policy.CustomerState("c", bought=["t0"], viewed=[])
        config = policy.PolicyConfig(pool_size=45)
        with patch.object(policy, "available", side_effect=lambda pids, as_of: [True] * len(pids)):
            pool, _ = policy.build_candidates(state, sources=None, lists=lists, config=config)
        ids = [c["product_id"] for c in pool]
        self.assertNotIn("t0", ids)  # already bought
        self.assertEqual(ids[:20], [f"a{i}" for i in range(20)])  # collaborative quota
        self.assertEqual(ids[20:40], [f"t{i}" for i in range(1, 21)])  # trending quota
        self.assertEqual(ids[40:], [f"a{i}" for i in range(20, 25)])  # fill by best score
        by_id = {c["product_id"]: c for c in pool}
        self.assertEqual(by_id["a0"]["n_sources"], 2)
        self.assertEqual(by_id["a0"]["source"], "collaborative")


class OracleTests(unittest.TestCase):
    def _fixture(self):
        from recommendations.evaluate import oracle_scores

        catalog = pd.DataFrame(
            [
                ("match", "Apparel", "Tshirts", "Black", "Casual", 4.5, 800.0),
                ("half", "Apparel", "Jeans", "Blue", "Casual", 4.0, 1200.0),
                ("miss", "Footwear", "Heels", "Red", "Formal", 3.5, 4000.0),
                ("miss2", "Accessories", "Watches", "Red", "Formal", 3.0, 9000.0),
            ],
            columns=["product_id", "h1_category", "h3_category", "h4_colour", "usage", "avg_rating", "current_price"],
        ).set_index("product_id", drop=False)
        profiles = pd.DataFrame(
            [{"customer_id": "c1", "category_focus": "Apparel", "colour_focus": "Black",
              "preferred_usage": "Casual", "price_sensitivity": 0.8}]
        ).set_index("customer_id")
        frame = pd.DataFrame({
            "session_id": "order-1", "customer_id": "c1",
            "event_timestamp": pd.Timestamp("2026-03-01 12:00"),
            "product_id": ["miss2", "miss", "half", "match"], "label": [0, 0, 0, 1],
        })
        frame["oracle"] = oracle_scores(frame, profiles, catalog, float(catalog["current_price"].median()))
        return frame, profiles, catalog

    def test_oracle_ranks_the_true_favourite_first_and_beats_random(self):
        frame, _, _ = self._fixture()
        ordered = frame.sort_values("oracle", ascending=False)
        self.assertEqual(ordered["product_id"].iloc[0], "match")
        oracle_ndcg = session_metrics(ordered["label"].to_numpy(), k=10)[1]
        random_ndcg = session_metrics(frame["label"].to_numpy(), k=10)[1]  # bought item last
        self.assertGreaterEqual(oracle_ndcg, random_ndcg)
        self.assertEqual(oracle_ndcg, 1.0)

    def test_true_taste_score_is_deterministic(self):
        from generators.taste import true_taste_score

        _, profiles, catalog = self._fixture()
        first = true_taste_score(profiles.loc["c1"], catalog, pd.Timestamp("2026-10-15"))
        again = true_taste_score(profiles.loc["c1"], catalog, pd.Timestamp("2026-10-15"))
        np.testing.assert_array_equal(first, again)
        # Precomputed seasonal demand gives the identical result.
        from generators.taste import product_demand_multiplier
        demand = np.array([product_demand_multiplier(r, pd.Timestamp("2026-10-15")) for r in catalog.itertuples(index=False)])
        np.testing.assert_array_equal(first, true_taste_score(profiles.loc["c1"], catalog, pd.Timestamp("2026-10-15"), demand=demand))

    def test_gap_closed_is_zero_for_random_and_one_for_oracle(self):
        from recommendations.evaluate import gap_closed_bootstrap

        rng = np.random.default_rng(1)
        random = rng.uniform(0.0, 0.1, 300)
        oracle = random + rng.uniform(0.2, 0.4, 300)
        model = random + 0.5 * (oracle - random)
        self.assertAlmostEqual(gap_closed_bootstrap(random, random, oracle)["value"], 0.0)
        self.assertAlmostEqual(gap_closed_bootstrap(oracle, random, oracle)["value"], 1.0)
        half = gap_closed_bootstrap(model, random, oracle)
        self.assertAlmostEqual(half["value"], 0.5)
        self.assertLess(half["ci_low"], 0.5 + 1e-9)
        self.assertGreater(half["ci_high"], 0.5 - 1e-9)


class DeadCodeRemovedTests(unittest.TestCase):
    def test_legacy_ranker_and_unused_helpers_are_gone(self):
        import inspect

        from recommendations import evaluate, ranker

        self.assertFalse(hasattr(evaluate.History, "is_positive"))
        self.assertFalse(hasattr(ranker, "build_training_frame"))
        self.assertFalse(hasattr(ranker, "LEGACY_FEATURES"))
        self.assertNotIn("sample_limit", inspect.signature(evaluate.main).parameters)
        self.assertNotIn("--sample-limit", inspect.getsource(evaluate))
