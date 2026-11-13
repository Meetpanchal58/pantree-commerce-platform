"""A2: one set of tunable policy settings, chosen on validation, read by serving and evaluation."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from recommendations import evaluate, policy


class PolicyConfigTests(unittest.TestCase):
    def tearDown(self):
        policy.load_config.cache_clear()

    def test_collaborative_quota_splits_the_rest_evenly(self):
        config = policy.PolicyConfig.with_collaborative_quota(60, 100)
        self.assertEqual(config.quotas, (60, 10, 10, 10, 10))
        self.assertEqual(policy.PolicyConfig.with_collaborative_quota(20, 100), policy.DEFAULT_CONFIG)

    def test_json_round_trip_and_loading(self):
        config = policy.PolicyConfig(quotas=(40, 15, 15, 15, 15), pool_size=100, ranker_blend=0.75,
                                     first_timer_popularity=False, trending_window_days=14)
        self.assertEqual(policy.PolicyConfig.from_json(json.loads(json.dumps(config.to_json()))), config)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "policy_config.json"
            path.write_text(json.dumps({"chosen": config.to_json()}))
            with patch.object(policy, "POLICY_CONFIG_PATH", path):
                policy.load_config.cache_clear()
                self.assertEqual(policy.load_config(), config)

    def test_missing_file_falls_back_to_defaults_with_a_warning(self):
        with patch.object(policy, "POLICY_CONFIG_PATH", Path("/nonexistent/policy.json")):
            policy.load_config.cache_clear()
            with self.assertLogs(policy.logger, "WARNING"):
                self.assertEqual(policy.load_config(), policy.DEFAULT_CONFIG)

    def test_committed_config_is_valid(self):
        if policy.POLICY_CONFIG_PATH.exists():
            data = json.loads(policy.POLICY_CONFIG_PATH.read_text())
            self.assertEqual(data["chosen_on"], "validation")
            config = policy.PolicyConfig.from_json(data["chosen"])
            self.assertLessEqual(sum(config.quotas), config.pool_size)

    def test_blend_weight_and_first_timer_rule(self):
        retrieval, pop, raw = np.array([0.2, 0.4]), np.array([1.0, 9.0]), np.array([5.0, 1.0])
        pure_ranker = policy.PolicyConfig(ranker_blend=1.0)
        np.testing.assert_allclose(policy.final_scores(retrieval, pop, False, raw, pure_ranker), [1.0, 0.0])
        no_ranker = policy.PolicyConfig(ranker_blend=0.0)
        np.testing.assert_allclose(policy.final_scores(retrieval, pop, False, raw, no_ranker), retrieval)
        # First-timer rule off: first-time buyers get the blend like everyone else.
        off = policy.PolicyConfig(first_timer_popularity=False, ranker_blend=1.0)
        np.testing.assert_allclose(policy.final_scores(retrieval, pop, True, raw, off), [1.0, 0.0])
        on = policy.PolicyConfig(first_timer_popularity=True)
        self.assertGreater(policy.final_scores(retrieval, pop, True, raw, on)[1], 1.0)


class AblationAndRecallTests(unittest.TestCase):
    def cached(self):
        q = pd.Series({"order_id": "o1", "customer_id": "c", "query_time": pd.Timestamp("2026-01-01"),
                       "purchased": {"b", "x"}})
        state = policy.CustomerState("c", bought=[], viewed=["v"])
        lists = {name: [] for name in policy.SOURCES}
        lists["collaborative"] = [{"product_id": p, "score": 1 - i / 10} for i, p in enumerate("abcd")]
        lists["trending"] = [{"product_id": p, "score": 0.5} for p in "xyz"]
        return [(q, state, lists)]

    def pools(self, **kwargs):
        with patch.object(policy, "available", side_effect=lambda pids, as_of: [True] * len(pids)):
            return evaluate.build_pools(None, None, None, cached=self.cached(), **kwargs)

    def test_dropping_a_source_removes_its_candidates(self):
        pools, stats = self.pools(drop=("trending",))
        self.assertFalse({"x", "y", "z"} & set(pools["product_id"]))
        self.assertNotIn("trending", stats["single_source_recall"])
        self.assertEqual(stats["pool_recall"], 0.5)  # "b" found, "x" lost with trending

    def test_pool_and_single_source_recall(self):
        config = policy.PolicyConfig(quotas=(2, 0, 0, 2, 0), pool_size=4)
        pools, stats = self.pools(config=config)
        self.assertEqual(list(pools["product_id"]), ["a", "b", "x", "y"])
        self.assertEqual(stats["pool_recall"], 1.0)
        self.assertEqual(stats["single_source_recall"]["collaborative"], 0.5)
        self.assertEqual(stats["single_source_recall"]["trending"], 0.5)


if __name__ == "__main__":
    unittest.main()


class ReportingTests(unittest.TestCase):
    def test_retrieval_ceiling_uses_the_pool_size(self):
        import inspect

        self.assertIn("k=config.pool_size", inspect.getsource(evaluate.main))

    def test_method_line_describes_the_chosen_ordering(self):
        from recommendations.readme_summary import policy_summary

        off = policy.PolicyConfig(ranker_blend=0.75, first_timer_popularity=False).to_json()
        on = policy.PolicyConfig(ranker_blend=0.5, first_timer_popularity=True).to_json()
        self.assertIn("for every customer", policy_summary({"policy": off}))
        self.assertIn("0.75 ranker", policy_summary({"policy": off}))
        self.assertIn("popularity for first-time buyers", policy_summary({"policy": on}))

    def test_evaluation_doc_has_the_generated_ablation_block(self):
        doc = (Path(__file__).resolve().parents[1] / "docs" / "recommender.md").read_text()
        self.assertIn("<!-- ABLATION:START -->", doc)
        self.assertIn("<!-- ABLATION:END -->", doc)
