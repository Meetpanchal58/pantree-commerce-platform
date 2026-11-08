from functools import lru_cache

import pandas as pd

from generators.config import COMPLEMENTS

from .common import (
    DATA,
    gender_is_compatible,
    read_products,
    read_realized_transactions,
    read_stock,
    realized_transactions_ready,
)


@lru_cache(maxsize=1)
def _affinity() -> pd.DataFrame:
    return pd.read_parquet(
        DATA / "product_affinity.parquet",
        columns=[
            "product_id", "related_product_id", "affinity_type",
            "affinity_score", "co_purchase_count", "co_view_count",
        ],
    )


def _ranked_candidates(
    matches: pd.DataFrame,
    source: str,
    reason: str,
    exclude: set[str],
    limit: int,
) -> list[dict]:
    if matches.empty:
        return []
    grouped = matches.groupby("related_product_id")["score"].max()
    grouped = grouped.sort_index().sort_values(ascending=False, kind="mergesort")
    maximum = float(grouped.max()) if not grouped.empty else 0.0
    return [
        {
            "product_id": product_id,
            "score": float(score / maximum) if maximum else 0.0,
            "source": source,
            "reason": reason,
        }
        for product_id, score in grouped.items()
        if product_id not in exclude
    ][:limit]


def _order_pair_candidates(
    product_ids: list[str], exclude: set[str], limit: int
) -> list[dict]:
    if not realized_transactions_ready():
        return []
    orders = read_realized_transactions()
    if orders.empty:
        return []
    anchor_lines = orders[orders["product_id"].isin(product_ids)]
    if anchor_lines.empty:
        return []
    order_lines = orders[orders["order_id"].isin(anchor_lines["order_id"].unique())]
    matches = order_lines[~order_lines["product_id"].isin(exclude)].copy()
    if matches.empty:
        return []
    counts = (
        matches.groupby("product_id")["order_id"]
        .nunique()
        .rename("score")
        .reset_index()
        .rename(columns={"product_id": "related_product_id"})
    )
    return _ranked_candidates(
        counts, "fbt", "Related product pairing", exclude, limit
    )


def _session_pair_candidates(
    product_ids: list[str], exclude: set[str], limit: int
) -> list[dict]:
    affinity = _affinity()
    matches = affinity[
        affinity["product_id"].isin(product_ids)
        & affinity["co_view_count"].fillna(0).gt(0)
    ].copy()
    if matches.empty:
        return []
    matches["score"] = matches["affinity_score"].fillna(0) + (
        matches["co_view_count"].fillna(0)
        .clip(lower=0)
        .map(lambda value: value / (value + 10))
    )
    return _ranked_candidates(
        matches,
        "session_pairing",
        "Often viewed together",
        exclude,
        limit,
    )


def _curated_candidates(
    product_ids: list[str], exclude: set[str], limit: int
) -> list[dict]:
    products = read_products()
    target_categories = {
        category
        for product_id in product_ids
        if product_id in products.index
        and isinstance(products.at[product_id, "h3_category"], str)
        for category in COMPLEMENTS.get(products.at[product_id, "h3_category"], [])
    }
    if not target_categories:
        return []

    related = products[products["h3_category"].isin(target_categories)].copy()
    related = related[~related.index.isin(exclude)]
    if related.empty:
        return []
    related["_rating"] = related.get("avg_rating", pd.Series(0, index=related.index)).fillna(0)
    related["_reviews"] = related.get("review_count", pd.Series(0, index=related.index)).fillna(0)
    related["_product_key"] = related.index.astype(str)
    related = related.sort_values(
        ["_rating", "_reviews", "_product_key"],
        ascending=[False, False, True],
        kind="mergesort",
    )
    return [
        {
            "product_id": product_id,
            "score": 0.7,
            "source": "curated_complement",
            "reason": "A curated pairing for this product type",
        }
        for product_id in related["_product_key"].head(limit)
    ]


def _eligible(
    candidates: list[dict], product_ids: list[str], exclude: set[str]
) -> list[dict]:
    products = read_products()
    stock = read_stock()
    anchors = [
        products.loc[product_id]
        for product_id in product_ids
        if product_id in products.index
    ]
    if not anchors:
        return []
    eligible = []
    for candidate in candidates:
        product_id = candidate["product_id"]
        if (
            product_id in exclude
            or product_id not in products.index
            or product_id not in stock.index
            or float(stock.loc[product_id]) <= 0
        ):
            continue
        product = products.loc[product_id]
        if pd.isna(product["is_active"]) or not bool(product["is_active"]):
            continue
        if any(
            product["h3_category"] != anchor["h3_category"]
            and gender_is_compatible(anchor["gender"], product["gender"])
            for anchor in anchors
        ):
            eligible.append(candidate)
    return eligible


def _category_fallback(
    product_ids: list[str], exclude: set[str], limit: int
) -> list[dict]:
    products = read_products()
    anchors = [
        products.loc[product_id]
        for product_id in product_ids
        if product_id in products.index
    ]
    if not anchors:
        return []
    categories = {anchor["h1_category"] for anchor in anchors}
    related = products[products["h1_category"].isin(categories)].copy()
    related = related[~related.index.isin(exclude)]
    related["_product_key"] = related.index.astype(str)
    related = related.sort_values(
        ["avg_rating", "review_count", "_product_key"],
        ascending=[False, False, True],
        kind="mergesort",
    )
    return [
        {
            "product_id": product_id,
            "score": 0.5,
            "source": "category_fallback",
            "reason": "More options from the same department",
        }
        for product_id in related["_product_key"].head(limit)
    ]


def get_frequently_bought_together(
    product_ids: list[str],
    limit: int = 50,
    exclude_product_ids: set[str] | None = None,
) -> list[dict]:
    """Prefer same-order evidence, then co-view evidence, then curated pairings."""
    if not product_ids:
        return []
    excluded = set(product_ids) | (exclude_product_ids or set())
    sources = (
        _order_pair_candidates,
        _session_pair_candidates,
        _curated_candidates,
        _category_fallback,
    )
    for source in sources:
        candidates = source(product_ids, excluded, limit * 10)
        eligible = _eligible(candidates, product_ids, excluded)
        if eligible:
            return eligible[:limit]
    return []
