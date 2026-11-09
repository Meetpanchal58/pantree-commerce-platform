"""Popularity-based sources: trending products and "because you viewed" items.

All counts come from one activity index (product views, cart adds and wishlist
adds) and are point in time: ``as_of`` counts only events strictly before that
moment; ``None`` means everything loaded (serving). The same counts feed the
ranker's product-popularity feature.
"""
from functools import lru_cache

import numpy as np
import pandas as pd

from .common import (
    gender_is_compatible,
    read_clickstream_activity,
    read_products,
    read_stock,
)

TRENDING_WINDOW_DAYS = 30
TRENDING_LIMIT = 100
RECENT_MAX_SEEDS = 5
RECENT_PER_SEED = 20


class ActivityIndex:
    """Event counts per product before any moment, in O(products) per query."""

    def __init__(self, events: pd.DataFrame):
        events = events.sort_values("event_timestamp", kind="mergesort")
        codes, self.product_ids = pd.factorize(events["product_id"], sort=True)
        self.times = events["event_timestamp"].to_numpy(dtype="datetime64[ns]")
        self.codes = codes.astype(np.int32)
        n = len(self.product_ids)
        if len(self.times):
            self.start = self.times[0].astype("datetime64[D]")
            days = (self.times.astype("datetime64[D]") - self.start).astype(int)
            per_day = np.zeros((days.max() + 1, n), dtype=np.int32)
            np.add.at(per_day, (days, self.codes), 1)
            # cumulative[d] = events before the start of day d
            self.cumulative = np.vstack(
                [np.zeros((1, n), dtype=np.int32), per_day.cumsum(axis=0, dtype=np.int32)]
            )
        else:
            self.start = None
            self.cumulative = np.zeros((1, n), dtype=np.int32)
        self.total = self.cumulative[-1]
        self.last_event = pd.Timestamp(self.times[-1]) if len(self.times) else None

    def counts_before(self, as_of) -> np.ndarray:
        if as_of is None or self.start is None:
            return self.total
        moment = np.datetime64(pd.Timestamp(as_of), "ns")
        day_start = moment.astype("datetime64[D]")
        day = int((day_start - self.start).astype(int))
        if day < 0:
            return np.zeros_like(self.total)
        if day >= len(self.cumulative) - 1:
            return self.total
        lo = np.searchsorted(self.times, day_start.astype("datetime64[ns]"), side="left")
        hi = np.searchsorted(self.times, moment, side="left")
        base = self.cumulative[day]
        if hi <= lo:
            return base
        return base + np.bincount(self.codes[lo:hi], minlength=len(self.product_ids)).astype(np.int32)


@lru_cache(maxsize=1)
def activity_index() -> ActivityIndex:
    return ActivityIndex(read_clickstream_activity())


def activity_ready() -> bool:
    return activity_index.cache_info().currsize > 0


@lru_cache(maxsize=64)
def activity_counts(as_of=None) -> pd.Series:
    """Events per product before ``as_of`` (all events when ``None``)."""
    index = activity_index()
    return pd.Series(index.counts_before(as_of), index=index.product_ids)


def warm_popularity() -> None:
    activity_counts(None)


def warm_activity_cache() -> None:
    activity_index()


@lru_cache(maxsize=512)
def trending(as_of=None, limit: int = TRENDING_LIMIT, window_days: int = TRENDING_WINDOW_DAYS) -> tuple:
    """Most-interacted products in the ``window_days`` before ``as_of`` (before the
    latest event when ``None``), scored relative to the top product."""
    index = activity_index()
    if as_of is None:
        if index.last_event is None:
            return ()
        end = index.last_event + pd.Timedelta(microseconds=1)
    else:
        end = pd.Timestamp(as_of)
    recent = index.counts_before(end) - index.counts_before(end - pd.Timedelta(days=window_days))
    counts = pd.Series(recent, index=index.product_ids)
    counts = counts[counts > 0].sort_index().sort_values(ascending=False, kind="mergesort").head(limit)
    peak = float(counts.max()) if len(counts) else 0.0
    return tuple(
        {"product_id": pid, "score": float(n / peak) if peak else 0.0, "source": "trending",
         "reason": "Popular with shoppers recently"}
        for pid, n in counts.items()
    )


@lru_cache(maxsize=1)
def _products_by_type() -> dict:
    products = read_products()
    return {cat: group.index.to_numpy() for cat, group in products.groupby("h3_category")}


@lru_cache(maxsize=1024)
def _same_type_pool(category, gender) -> np.ndarray:
    """Products of ``category`` an audience of ``gender`` can be shown, sorted by id."""
    products = read_products()
    ids = _products_by_type().get(category, np.array([], dtype=object))
    genders = products.loc[ids, "gender"].tolist() if len(ids) else []
    keep = [pid for pid, g in zip(ids, genders) if gender_is_compatible(gender, g)]
    return np.array(sorted(keep), dtype=object)


def recent_activity(seeds: list[str], as_of=None) -> list[dict]:
    """Items of the same type and audience as the customer's recent products,
    most popular first (popularity as of ``as_of``)."""
    products = read_products()
    counts = activity_counts(as_of)
    peak = float(counts.max()) if len(counts) else 0.0
    peak = peak or 1.0
    out: dict[str, dict] = {}
    for seed in list(dict.fromkeys(seeds))[:RECENT_MAX_SEEDS]:
        if seed not in products.index:
            continue
        category, gender = products.at[seed, "h3_category"], products.at[seed, "gender"]
        if not isinstance(category, str):
            continue
        pool = _same_type_pool(category, gender if isinstance(gender, str) else None)
        pool = pool[pool != seed]
        if not len(pool):
            continue
        popularity = counts.reindex(pool).fillna(0).to_numpy(dtype=float)
        # Most popular first; ties by product id (pool is already sorted by id).
        order = np.argsort(-popularity, kind="stable")[:RECENT_PER_SEED]
        name = products.at[seed, "product_name"] if "product_name" in products.columns else seed
        for i in order:
            p = pool[i]
            score = 0.80 + 0.10 * float(popularity[i]) / peak
            if score > out.get(p, {"score": -1.0})["score"]:
                out[p] = {"product_id": p, "score": score, "source": "recent_activity",
                          "reason": f"Because you looked at {name}"}
    return sorted(out.values(), key=lambda c: (-c["score"], c["product_id"]))


def _catalog_fallback(limit: int) -> list[dict]:
    products = read_products()
    products = products[products["is_active"].fillna(False).astype(bool)].copy()
    stock = read_stock()
    products = products[
        products.index.isin(stock.index) & stock.reindex(products.index).gt(0)
    ]
    products["_rating"] = products["avg_rating"].fillna(0)
    products["_reviews"] = products["review_count"].fillna(0)
    products["_product_key"] = products.index.astype(str)
    products = products.sort_values(
        ["_rating", "_reviews", "_product_key"],
        ascending=[False, False, True],
        kind="mergesort",
    )
    return [
        {
            "product_id": product_id,
            "score": 0.5,
            "source": "catalog_fallback",
            "reason": "Highly rated products to get started",
        }
        for product_id in products["_product_key"].head(limit)
    ]


def get_popular(limit: int = 100, window_days: int = TRENDING_WINDOW_DAYS) -> list[dict]:
    """Trending products now; a catalog-quality list while activity is loading."""
    if not activity_ready():
        return _catalog_fallback(limit)
    items = [dict(item) for item in trending(None, max(limit, TRENDING_LIMIT), window_days)][:limit]
    return items or _catalog_fallback(limit)


def get_deals(limit: int = 100) -> list[dict]:
    """Return products with the largest current markdowns."""
    products = read_products()
    deals = products[
        products["mrp"].gt(0)
        & products["current_price"].ge(0)
        & products["current_price"].lt(products["mrp"])
    ].copy().reset_index(drop=True)
    deals["score"] = (deals["mrp"] - deals["current_price"]) / deals["mrp"]
    deals = deals.sort_values(["score", "product_id"], ascending=[False, True]).head(limit)
    return [
        {
            "product_id": product_id,
            "score": 0.7 * float(row["score"]),
            "source": "deals",
            "reason": "Currently on offer",
        }
        for product_id, row in deals.iterrows()
    ]


def clear_caches() -> None:
    for cached in (activity_index, activity_counts, trending, _products_by_type, _same_type_pool):
        cached.cache_clear()
