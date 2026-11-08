import pandas as pd
from functools import lru_cache
import math

from .common import (
    DATA,
    gender_is_compatible,
    read_clickstream_activity,
    read_products,
    read_realized_transactions,
    read_stock,
)


@lru_cache(maxsize=1)
def _popular_counts() -> pd.Series:
    """Aggregate recent order and product-view popularity from the warehouse."""
    transactions = read_realized_transactions()
    if not transactions.empty:
        cutoff = transactions["order_timestamp"].max() - pd.Timedelta(days=30)
        recent = transactions[transactions["order_timestamp"] >= cutoff]
        counts = recent.groupby("product_id")["quantity"].sum()
        if counts.empty:
            counts = transactions.groupby("product_id")["quantity"].sum()
    else:
        counts = pd.Series(dtype="float64")

    events = _activity_events()
    if not events.empty:
        cutoff = events["event_timestamp"].max() - pd.Timedelta(days=30)
        recent_views = events[events["event_timestamp"] >= cutoff]
        views = recent_views["product_id"].value_counts()
        counts = counts.add(views, fill_value=0)

    return counts.sort_index().sort_values(ascending=False, kind="mergesort")


def warm_popularity() -> None:
    """Aggregate warehouse popularity before it is needed by a request."""
    _popular_counts()


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


def get_popular(limit: int = 100) -> list[dict]:
    """Return recent interaction popularity or a fast catalog-quality fallback."""
    if _popular_counts.cache_info().currsize == 0:
        return _catalog_fallback(limit)
    counts = _popular_counts().head(limit)
    if counts.empty:
        return _catalog_fallback(limit)
    maximum = float(counts.max()) if not counts.empty else 0.0
    return [
        {
            "product_id": product_id,
            "score": 0.7 * float(count / maximum) if maximum else 0.0,
            "source": "trending",
            "reason": "Popular with shoppers recently",
        }
        for product_id, count in counts.items()
    ]


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


@lru_cache(maxsize=1)
def _activity_events() -> pd.DataFrame:
    return read_clickstream_activity()


@lru_cache(maxsize=1)
def _recent_product_scores() -> pd.Series:
    events = _activity_events()
    if events.empty:
        return pd.Series(dtype="float64")
    cutoff = events["event_timestamp"].max() - pd.Timedelta(days=30)
    return (
        events.loc[events["event_timestamp"] >= cutoff]
        .groupby("product_id")["weight"]
        .sum()
    )


def warm_activity_cache() -> None:
    """Load warehouse activity signals outside the request path."""
    _activity_events()
    _recent_product_scores()


def get_personalized(
    customer_id: str,
    limit: int = 100,
    recent_product_ids: list[str] | None = None,
) -> list[dict]:
    """Recommend items related to this customer's recent views and purchases."""
    online_seeds = list(dict.fromkeys(recent_product_ids or []))
    if online_seeds:
        seeds = pd.DataFrame({"product_id": online_seeds[:5]})
        seed_reason = "viewed"
    elif _activity_events.cache_info().currsize:
        events = _activity_events()
        viewed = events[events["customer_id"] == customer_id].sort_values(
            "event_timestamp", ascending=False
        ).drop_duplicates("product_id").head(5)
        seeds = viewed
        seed_reason = "viewed"
        if seeds.empty:
            orders = read_realized_transactions()
            purchases = orders[orders["customer_id"] == customer_id].sort_values(
                "order_timestamp", ascending=False
            ).drop_duplicates("product_id").head(5)
            if purchases.empty:
                return []
            seeds = purchases[["product_id"]].copy()
            seed_reason = "bought"
    else:
        return []
    if seeds.empty:
        if not online_seeds:
            return []

    products = read_products()
    global_scores = (
        _recent_product_scores()
        if _recent_product_scores.cache_info().currsize
        else pd.Series(dtype="float64")
    )
    recent_candidates: dict[str, dict] = {}
    deal_candidates: dict[str, dict] = {}
    for seed in seeds["product_id"]:
        if seed not in products.index:
            continue
        category = products.at[seed, "h3_category"]
        if pd.isna(category):
            continue
        seed_gender = products.at[seed, "gender"]
        seed_name = products.at[seed, "product_name"]
        category_products = products[products["h3_category"] == category].copy()
        category_products = category_products[category_products.index != seed]
        category_products = category_products[
            category_products["gender"].map(
                lambda gender: gender_is_compatible(seed_gender, gender)
            )
        ]
        category_products = category_products.reset_index(drop=True)
        category_products["_activity"] = global_scores.reindex(
            category_products["product_id"]
        ).to_numpy()
        activity_max = float(category_products["_activity"].max())
        if activity_max:
            category_products["_activity_score"] = (
                category_products["_activity"] / activity_max
            )
        else:
            category_products["_rating_signal"] = (
                category_products["avg_rating"].fillna(0).clip(0, 5) / 5
            )
            category_products["_review_signal"] = category_products[
                "review_count"
            ].fillna(0).map(math.log1p)
            review_max = float(category_products["_review_signal"].max())
            if review_max:
                category_products["_review_signal"] /= review_max
            category_products["_activity_score"] = (
                0.7 * category_products["_rating_signal"]
                + 0.3 * category_products["_review_signal"]
            )
        for _, row in category_products.sort_values(
            ["_activity_score", "product_id"], ascending=[False, True]
        ).head(limit).iterrows():
            product_id = row["product_id"]
            score = 0.80 + 0.10 * float(row["_activity_score"])
            candidate = {
                "product_id": product_id,
                "score": score,
                "source": "recent_activity",
                "reason": f"Because you {seed_reason} {seed_name}",
            }
            if (
                product_id not in recent_candidates
                or score > recent_candidates[product_id]["score"]
            ):
                recent_candidates[product_id] = candidate

        deal_rows = category_products[
            category_products["mrp"].gt(0)
            & category_products["current_price"].lt(category_products["mrp"])
        ].copy()
        if not deal_rows.empty:
            deal_rows["_discount"] = (
                (deal_rows["mrp"] - deal_rows["current_price"]) / deal_rows["mrp"]
            )
            for _, row in deal_rows.sort_values(
                ["_discount", "_activity_score"], ascending=False
            ).head(1).iterrows():
                product_id = row["product_id"]
                score = 0.90 + 0.08 * float(row["_discount"])
                candidate = {
                    "product_id": product_id,
                    "score": score,
                    "source": "deals_on_viewed",
                    "reason": f"On offer because you {seed_reason} {seed_name}",
                }
                if (
                    product_id not in deal_candidates
                    or score > deal_candidates[product_id]["score"]
                ):
                    deal_candidates[product_id] = candidate

    for product_id in deal_candidates:
        recent_candidates.pop(product_id, None)
    candidates = {**recent_candidates, **deal_candidates}
    return sorted(
        candidates.values(),
        key=lambda item: (-item["score"], item["product_id"]),
    )[:limit]
