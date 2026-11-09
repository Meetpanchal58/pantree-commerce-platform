"""Complementary products ("frequently bought together") learned from behaviour.

Every pairing here is learned from data observed before ``as_of``:

1. ``fbt`` - products bought in the same order as the anchor (co-purchase).
2. ``session_pairing`` - products viewed in the same browsing session (co-view).
3. ``learned_complement`` - product *types* that are bought together more often
   than chance (lift > 1 with minimum support), filled with well-rated items of
   that type. This replaces the hand-written pairing table, which was the
   simulator's own ground truth.
4. ``category_fallback`` - more items from the anchor's department.

``as_of=None`` (serving) uses everything loaded; evaluation passes the query
cut-off so no pairing is learned from the future.
"""
from functools import lru_cache

import pandas as pd

from .common import (
    DATA,
    gender_is_compatible,
    read_products,
    read_realized_transactions,
    read_stock,
    realized_transactions_ready,
)

MAX_PRODUCTS_PER_BASKET = 20  # very long sessions/orders add noise, not signal
MAX_RELATED_PER_PRODUCT = 50  # keeps the co-view table small on 512 MB hosts
MIN_TYPE_PAIR_SUPPORT = 5  # orders containing both types
MIN_TYPE_PAIR_LIFT = 1.0
TYPE_COMPLEMENTS_PER_TYPE = 5


def _key(as_of) -> pd.Timestamp | None:
    return None if as_of is None else pd.Timestamp(as_of)


def _before(frame: pd.DataFrame, column: str, as_of) -> pd.DataFrame:
    if as_of is None or frame.empty:
        return frame
    return frame[pd.to_datetime(frame[column]) < as_of]


@lru_cache(maxsize=1)
def read_pdp_views() -> pd.DataFrame:
    """Session, product and time of every product-page view."""
    views = pd.read_parquet(
        DATA / "clickstream.parquet",
        columns=["session_id", "product_id", "event_type", "event_timestamp"],
        filters=[("event_type", "==", "pdp_view")],
    )
    views = views.dropna(subset=["session_id", "product_id", "event_timestamp"])
    views["event_timestamp"] = pd.to_datetime(views["event_timestamp"], errors="coerce")
    views = views.dropna(subset=["event_timestamp"])[["session_id", "product_id", "event_timestamp"]]
    views["session_id"] = views["session_id"].astype("category")
    views["product_id"] = views["product_id"].astype("category")
    return views.reset_index(drop=True)


def _pair_counts(baskets: pd.DataFrame, basket_col: str) -> pd.DataFrame:
    """Number of baskets containing each ordered pair of distinct products."""
    columns = ["product_id", "related_product_id", "count"]
    if baskets.empty:
        return pd.DataFrame(columns=columns)
    items = baskets[[basket_col, "product_id"]].drop_duplicates()
    sizes = items.groupby(basket_col)["product_id"].transform("size")
    items = items[(sizes > 1) & (sizes <= MAX_PRODUCTS_PER_BASKET)]
    if items.empty:
        return pd.DataFrame(columns=columns)
    pairs = items.merge(items, on=basket_col, suffixes=("", "_related"))
    pairs = pairs[pairs["product_id"] != pairs["product_id_related"]]
    counts = (
        pairs.groupby(["product_id", "product_id_related"], sort=True, observed=True)
        .size()
        .rename("count")
        .reset_index()
        .rename(columns={"product_id_related": "related_product_id"})
    )
    counts["product_id"] = counts["product_id"].astype(str)
    counts["related_product_id"] = counts["related_product_id"].astype(str)
    counts = counts.sort_values(
        ["product_id", "count", "related_product_id"], ascending=[True, False, True], kind="mergesort"
    )
    return counts.groupby("product_id", sort=False).head(MAX_RELATED_PER_PRODUCT).reset_index(drop=True)


@lru_cache(maxsize=4)
def co_purchase_pairs(as_of=None) -> pd.DataFrame:
    if not realized_transactions_ready():
        return pd.DataFrame(columns=["product_id", "related_product_id", "count"])
    orders = _before(read_realized_transactions(), "order_timestamp", as_of)
    return _pair_counts(orders, "order_id")


@lru_cache(maxsize=4)
def co_view_pairs(as_of=None) -> pd.DataFrame:
    return _pair_counts(_before(read_pdp_views(), "event_timestamp", as_of), "session_id")


@lru_cache(maxsize=4)
def type_complements(as_of=None) -> dict[str, list[str]]:
    """Product types bought together more often than chance, best lift first."""
    if not realized_transactions_ready():
        return {}
    orders = _before(read_realized_transactions(), "order_timestamp", as_of)
    products = read_products()
    if orders.empty:
        return {}
    types = orders[["order_id", "product_id"]].copy()
    types["h3_category"] = types["product_id"].map(products["h3_category"])
    types = types.dropna(subset=["h3_category"])[["order_id", "h3_category"]].drop_duplicates()
    n_orders = types["order_id"].nunique()
    if not n_orders:
        return {}
    type_orders = types.groupby("h3_category")["order_id"].nunique()
    pairs = types.merge(types, on="order_id", suffixes=("", "_other"))
    pairs = pairs[pairs["h3_category"] != pairs["h3_category_other"]]
    if pairs.empty:
        return {}
    support = pairs.groupby(["h3_category", "h3_category_other"]).size().rename("support").reset_index()
    support = support[support["support"] >= MIN_TYPE_PAIR_SUPPORT]
    support["lift"] = (
        support["support"] * n_orders
        / (support["h3_category"].map(type_orders) * support["h3_category_other"].map(type_orders))
    )
    support = support[support["lift"] > MIN_TYPE_PAIR_LIFT]
    support = support.sort_values(
        ["h3_category", "lift", "h3_category_other"], ascending=[True, False, True], kind="mergesort"
    )
    return {
        category: group["h3_category_other"].head(TYPE_COMPLEMENTS_PER_TYPE).tolist()
        for category, group in support.groupby("h3_category", sort=True)
    }


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


def _pair_candidates(pairs, product_ids, exclude, limit, source, reason):
    matches = pairs[pairs["product_id"].isin(product_ids)]
    if matches.empty:
        return []
    matches = matches.groupby("related_product_id", as_index=False)["count"].sum()
    return _ranked_candidates(matches.rename(columns={"count": "score"}), source, reason, exclude, limit)


def _order_pair_candidates(product_ids, exclude, limit, as_of=None) -> list[dict]:
    return _pair_candidates(
        co_purchase_pairs(_key(as_of)), product_ids, exclude, limit, "fbt", "Related product pairing"
    )


def _session_pair_candidates(product_ids, exclude, limit, as_of=None) -> list[dict]:
    return _pair_candidates(
        co_view_pairs(_key(as_of)), product_ids, exclude, limit, "session_pairing", "Often viewed together"
    )


def _learned_complement_candidates(product_ids, exclude, limit, as_of=None) -> list[dict]:
    products = read_products()
    complements = type_complements(_key(as_of))
    targets: list[str] = []
    for product_id in product_ids:
        if product_id in products.index and isinstance(products.at[product_id, "h3_category"], str):
            for category in complements.get(products.at[product_id, "h3_category"], []):
                if category not in targets:
                    targets.append(category)
    if not targets:
        return []
    related = products[products["h3_category"].isin(targets)].copy()
    related = related[~related.index.isin(exclude)]
    if related.empty:
        return []
    related["_type_rank"] = related["h3_category"].map({c: i for i, c in enumerate(targets)})
    related["_rating"] = related.get("avg_rating", pd.Series(0, index=related.index)).fillna(0)
    related["_reviews"] = related.get("review_count", pd.Series(0, index=related.index)).fillna(0)
    related["_product_key"] = related.index.astype(str)
    related = related.sort_values(
        ["_type_rank", "_rating", "_reviews", "_product_key"],
        ascending=[True, False, False, True],
        kind="mergesort",
    )
    return [
        {
            "product_id": product_id,
            "score": 0.7,
            "source": "learned_complement",
            "reason": "Often bought with this product type",
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


def _category_fallback(product_ids, exclude, limit, as_of=None) -> list[dict]:
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
    as_of=None,
) -> list[dict]:
    """Prefer co-purchase, then co-view, then learned type complements."""
    if not product_ids:
        return []
    excluded = set(product_ids) | (exclude_product_ids or set())
    sources = (
        _order_pair_candidates,
        _session_pair_candidates,
        _learned_complement_candidates,
        _category_fallback,
    )
    for source in sources:
        candidates = source(product_ids, excluded, limit * 10, as_of=as_of)
        eligible = _eligible(candidates, product_ids, excluded)
        if eligible:
            return eligible[:limit]
    return []


def clear_caches() -> None:
    for cached in (co_purchase_pairs, co_view_pairs, type_complements, read_pdp_views):
        cached.cache_clear()
