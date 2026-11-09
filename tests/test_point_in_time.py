"""Point-in-time inputs shared by serving and evaluation (A1b)."""
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from recommendations.candidates import common, popularity


def events(rows):
    return pd.DataFrame(
        [(pid, pd.Timestamp(ts)) for pid, ts in rows], columns=["product_id", "event_timestamp"]
    )


class ActivityIndexTests(unittest.TestCase):
    def setUp(self):
        self.index = popularity.ActivityIndex(events([
            ("a", "2026-01-01 10:00"), ("b", "2026-01-01 12:00"), ("a", "2026-01-02 09:00"),
            ("a", "2026-01-02 15:00"), ("b", "2026-02-15 08:00"),
        ]))

    def counts(self, as_of):
        return dict(zip(self.index.product_ids, self.index.counts_before(as_of).tolist()))

    def test_counts_only_events_strictly_before(self):
        self.assertEqual(self.counts("2026-01-01 00:00"), {"a": 0, "b": 0})
        self.assertEqual(self.counts("2026-01-01 12:00"), {"a": 1, "b": 0})
        self.assertEqual(self.counts("2026-01-02 12:00"), {"a": 2, "b": 1})
        self.assertEqual(self.counts("2027-01-01"), {"a": 3, "b": 2})
        self.assertEqual(self.counts(None), {"a": 3, "b": 2})

    def test_matches_a_brute_force_count(self):
        rng = np.random.default_rng(3)
        times = pd.Timestamp("2026-01-01") + pd.to_timedelta(rng.integers(0, 60 * 24 * 40, 500), unit="min")
        frame = pd.DataFrame({"product_id": rng.choice(list("pqrst"), 500), "event_timestamp": times})
        index = popularity.ActivityIndex(frame)
        for moment in pd.to_datetime(["2026-01-03 05:17", "2026-01-20 23:59", "2026-02-05 00:00"]):
            expected = frame[frame["event_timestamp"] < moment]["product_id"].value_counts()
            got = pd.Series(index.counts_before(moment), index=index.product_ids)
            self.assertEqual(got[got > 0].sort_index().to_dict(), expected.sort_index().to_dict())

    def test_trending_uses_the_30_days_before_as_of(self):
        with patch.object(popularity, "activity_index", return_value=self.index):
            popularity.trending.cache_clear()
            self.addCleanup(popularity.trending.cache_clear)
            early = popularity.trending(pd.Timestamp("2026-01-03"))
            late = popularity.trending(pd.Timestamp("2026-02-16"))
        self.assertEqual([c["product_id"] for c in early], ["a", "b"])
        self.assertEqual([c["product_id"] for c in late], ["b"])  # January is out of the window
        self.assertEqual(early[0]["score"], 1.0)


class AvailabilityTests(unittest.TestCase):
    def index(self):
        stock = pd.DataFrame(
            [("2026-01-01", "p", 0), ("2026-01-02", "p", 4), ("2026-01-01", "q", 3), ("2026-01-02", "q", 3)],
            columns=["inventory_date", "product_id", "closing_stock"],
        )
        catalog = pd.DataFrame(
            [("p", "2025-06-01", None), ("q", "2025-06-01", "2026-01-02"), ("r", "2026-03-01", None)],
            columns=["product_id", "launch_date", "discontinue_date"],
        )
        with patch.object(common.pd, "read_parquet", side_effect=[stock, catalog]):
            return common.AvailabilityIndex()

    def test_stock_is_the_previous_days_close(self):
        index = self.index()
        # On Jan 2 the latest known close is Jan 1 (0 units); on Jan 3 it is Jan 2 (4 units).
        self.assertEqual(index.available(["p"], pd.Timestamp("2026-01-02 10:00")), [False])
        self.assertEqual(index.available(["p"], pd.Timestamp("2026-01-03 10:00")), [True])

    def test_launch_and_discontinue_dates(self):
        index = self.index()
        self.assertEqual(index.available(["q"], pd.Timestamp("2026-01-01 23:00")), [True])
        self.assertEqual(index.available(["q"], pd.Timestamp("2026-01-02 10:00")), [False])  # discontinued
        self.assertEqual(index.available(["r"], pd.Timestamp("2026-01-03")), [False])  # not launched yet
        self.assertEqual(index.available(["unknown"], pd.Timestamp("2026-01-03")), [False])


if __name__ == "__main__":
    unittest.main()
