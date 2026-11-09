"""Nothing from the query hour or after it reaches the evaluation (pre-A2 check).

Each test order is queried at query_time = order time - 1 hour. Candidate lists,
ranker features and the availability filter must be identical whether or not the
data also contains events between query_time and the order (including the target
order itself), and inventory after the query must not matter.
"""
import unittest
from unittest.mock import patch

import pandas as pd

from recommendations import evaluate, policy
from recommendations.candidates import common
from tests import test_policy_parity as fixture

ORDER_TIME = fixture.T0 + pd.Timedelta(hours=9)
TARGET = "p4"


def target_order(customer="c1"):
    return pd.DataFrame([{"customer_id": customer, "product_id": TARGET, "order_id": "target",
                          "order_timestamp": ORDER_TIME, "quantity": 1}])


def query_hour_noise():
    """Views, cart adds and purchases of the target between query_time and the order."""
    inside = [ORDER_TIME - pd.Timedelta(minutes=m) for m in (59, 30, 5, 1)]
    events = pd.DataFrame(
        [("c1", TARGET, "pdp_view", inside[0]), ("c1", TARGET, "add_to_cart", inside[1]),
         ("c2", TARGET, "pdp_view", inside[2]), ("c3", TARGET, "wishlist_add", inside[3]),
         ("c1", "p5", "pdp_view", inside[3])],
        columns=["customer_id", "product_id", "event_type", "event_timestamp"],
    )
    events["session_id"] = events["customer_id"] + "-late"
    events["weight"] = 1.0
    orders = pd.DataFrame([{"customer_id": "c2", "product_id": TARGET, "order_id": "other",
                            "order_timestamp": inside[2], "quantity": 2},
                           {"customer_id": "c2", "product_id": "p1", "order_id": "other",
                            "order_timestamp": inside[2], "quantity": 1}])
    return events, orders


class QueryHourLeakageTests(unittest.TestCase):
    def features(self, with_noise: bool) -> pd.DataFrame:
        activity = fixture.fixture_activity()
        orders = pd.concat([fixture.fixture_orders(), target_order()], ignore_index=True)
        if with_noise:
            late_events, late_orders = query_hour_noise()
            activity = pd.concat([activity, late_events], ignore_index=True)
            orders = pd.concat([orders, late_orders], ignore_index=True)
        with (
            patch.object(fixture, "fixture_activity", return_value=activity),
            patch.object(fixture, "fixture_orders", return_value=orders),
        ):
            case = fixture.PolicyParityTests("test_returning_customer_gets_identical_lists_and_scores")
            case.setUp()
            try:
                queries = evaluate.order_queries(orders[orders["order_id"] == "target"],
                                                 ORDER_TIME - pd.Timedelta(days=1), ORDER_TIME)
                self.assertEqual(queries["query_time"].iloc[0], ORDER_TIME - pd.Timedelta(hours=1))
                # Worst case for the model-like sources: their snapshot is the query time itself.
                sources = policy.Sources(model_as_of=queries["query_time"].iloc[0], use_content=True)
                pools, stats = evaluate.build_pools(queries, sources, case.history)
                featured = evaluate.add_features(pools, sources, case.history, case.products)
            finally:
                case.doCleanups()
        return featured.sort_values("product_id").reset_index(drop=True), stats

    def test_query_hour_events_and_the_target_order_change_nothing(self):
        clean, clean_stats = self.features(with_noise=False)
        noisy, noisy_stats = self.features(with_noise=True)
        self.assertGreater(len(clean), 3)
        self.assertIn(TARGET, set(clean["product_id"]))  # the target is a candidate
        pd.testing.assert_frame_equal(clean, noisy)
        self.assertEqual(clean_stats, noisy_stats)

    def test_noise_would_change_features_if_it_were_visible(self):
        # Sanity check that the test can detect a leak: count the same data one hour later.
        activity = pd.concat([fixture.fixture_activity(), query_hour_noise()[0]], ignore_index=True)
        index = __import__("recommendations.candidates.popularity", fromlist=["x"]).ActivityIndex(activity)
        before = dict(zip(index.product_ids, index.counts_before(ORDER_TIME - pd.Timedelta(hours=1))))
        after = dict(zip(index.product_ids, index.counts_before(ORDER_TIME)))
        self.assertLess(before[TARGET], after[TARGET])


class AvailabilityAsOfQueryTests(unittest.TestCase):
    def index(self, stock_rows):
        stock = pd.DataFrame(stock_rows, columns=["inventory_date", "product_id", "closing_stock"])
        catalog = pd.DataFrame(
            [("sold-out-by-order", "2025-01-01", None), ("restocked-today", "2025-01-01", None),
             ("launched-after-query", "2026-03-10 12:00", None),
             ("discontinued-after-query", "2025-01-01", "2026-03-10 12:00")],
            columns=["product_id", "launch_date", "discontinue_date"],
        )
        with patch.object(common.pd, "read_parquet", side_effect=[stock, catalog]):
            return common.AvailabilityIndex()

    def test_uses_inventory_known_at_query_time_not_after_the_order(self):
        query = pd.Timestamp("2026-03-10 10:00")  # order at 11:00 the same day
        index = self.index([
            ("2026-03-09", "sold-out-by-order", 1), ("2026-03-10", "sold-out-by-order", 0),
            ("2026-03-11", "sold-out-by-order", 0),
            ("2026-03-09", "restocked-today", 0), ("2026-03-10", "restocked-today", 5),
            ("2026-03-09", "launched-after-query", 9), ("2026-03-09", "discontinued-after-query", 9),
        ])
        result = dict(zip(
            ["sold-out-by-order", "restocked-today", "launched-after-query", "discontinued-after-query"],
            index.available(["sold-out-by-order", "restocked-today", "launched-after-query",
                             "discontinued-after-query"], query),
        ))
        # The order empties the shelf on 2026-03-10, but at query time the last known
        # close (2026-03-09) had stock: still available.
        self.assertTrue(result["sold-out-by-order"])
        # A restock recorded at today's close is not known at 10:00.
        self.assertFalse(result["restocked-today"])
        # Launch later today: not yet sellable. Discontinued later today: still sellable.
        self.assertFalse(result["launched-after-query"])
        self.assertTrue(result["discontinued-after-query"])

    def test_query_just_before_midnight_uses_the_day_before(self):
        index = self.index([("2026-03-08", "sold-out-by-order", 0), ("2026-03-09", "sold-out-by-order", 3)])
        # Query 23:30 on 03-09 (order 00:30 on 03-10): last full day known is 03-08.
        self.assertEqual(index.available(["sold-out-by-order"], pd.Timestamp("2026-03-09 23:30")), [False])
        self.assertEqual(index.available(["sold-out-by-order"], pd.Timestamp("2026-03-10 00:30")), [True])


if __name__ == "__main__":
    unittest.main()
