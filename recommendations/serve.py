"""Personalized product recommendations for the Pantree warehouse."""

from __future__ import annotations

import logging
import os
from functools import lru_cache
import time
from typing import Any

import pandas as pd

from . import policy
from .candidates import collaborative, content, fbt, fusion, popularity
from .candidates.common import (
    customer_histories,
    customer_histories_ready,
    purchased_products,
    event_counts_ready,
    gender_is_compatible,
    read_event_counts,
    read_products,
    read_realized_transactions,
    read_stock,
)
from .ranker import customer_profile, model_uses_current_features, score_candidates

logger = logging.getLogger("recommendations")
PERSONALIZED_TOP_K = 30
HISTORY_LIMIT = 200  # viewed products used for the profile (matches customer_histories)


def _context_product_ids(context: dict[str, Any]) -> list[str]:
    product_ids = context.get("cart", [])
    if isinstance(product_ids, dict):
        product_ids = list(product_ids)
    if not isinstance(product_ids, (list, tuple, set)):
        product_ids = []
    normalized_ids = []
    for product in product_ids:
        product_id = (
            product.get("product_id", product.get("id"))
            if isinstance(product, dict)
            else product
        )
        if product_id:
            normalized_ids.append(str(product_id))
    product_ids = normalized_ids
    product_id = context.get("product_id")
    if product_id:
        product_ids.append(str(product_id))
    return list(dict.fromkeys(product_ids))


def _excluded_product_ids(context: dict[str, Any]) -> set[str]:
    excluded = context.get("exclude_product_ids", [])
    if isinstance(excluded, str):
        excluded = [value for value in excluded.split(",") if value]
    if not isinstance(excluded, (list, tuple, set)):
        return set()
    return {str(product_id) for product_id in excluded if product_id}


def _get_collaborative(
    customer_id: str | None = None,
    limit: int = 100,
    **_: Any,
) -> list[dict]:
    return collaborative.get_collaborative(customer_id, limit=limit) if customer_id else []


def _get_content(
    product_id: str | None = None,
    context: dict[str, Any] | None = None,
    limit: int = 100,
    **_: Any,
) -> list[dict]:
    context = context or {}
    product_id = product_id or context.get("product_id")
    if not product_id:
        return []
    excluded = _excluded_product_ids(context)
    return content.get_similar_products(
        str(product_id), limit=limit, exclude_product_ids=excluded
    )


def _get_fbt(
    product_id: str | None = None,
    context: dict[str, Any] | None = None,
    limit: int = 100,
    **_: Any,
) -> list[dict]:
    product_ids = _context_product_ids(context or {})
    if product_id:
        product_ids.append(str(product_id))
    return fbt.get_frequently_bought_together(
        list(dict.fromkeys(product_ids)),
        limit=limit,
        exclude_product_ids=_excluded_product_ids(context or {}),
    )


def _get_popular(limit: int = 100, **_: Any) -> list[dict]:
    # Same trending window as the personalised row's trending source.
    return popularity.get_popular(limit=limit, window_days=policy.load_config().trending_window_days)


def _get_deals(limit: int = 100, **_: Any) -> list[dict]:
    return popularity.get_deals(limit=limit)


# ---------------------------------------------------------------------------
# Surface configuration
# ---------------------------------------------------------------------------

SURFACE_CONFIG: dict[str, dict[str, Any]] = {
    "homepage": {
        "candidate_sources": [
            ("collaborative", _get_collaborative, 100),
            ("content", _get_content, 100),
            ("popularity", _get_popular, 100),
        ],
        "fusion_weights": {
            "collaborative": 0.40,
            "content": 0.25,
            "popularity": 0.35,
            "trending": 0.35,
            "deals": 0.35,
        },
        "max_candidates": 300,
        "top_k": 30,
        "exclude_purchased": True,
    },
    "product": {
        "candidate_sources": [
            ("content", _get_content, 100),
            ("fbt", _get_fbt, 50),
            ("collaborative", _get_collaborative, 50),
        ],
        "fusion_weights": {
            "content": 0.40,
            "fbt": 0.30,
            "collaborative": 0.30,
        },
        "max_candidates": 200,
        "top_k": 100,
        "exclude_purchased": True,
    },
    "cart": {
        "candidate_sources": [
            ("fbt", _get_fbt, 80),
            ("collaborative", _get_collaborative, 80),
            ("content", _get_content, 80),
        ],
        "fusion_weights": {
            "fbt": 0.50,
            "collaborative": 0.50,
            "content": 0.50,
        },
        "max_candidates": 200,
        "top_k": 8,
        "exclude_purchased": True,
    },
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _catalog() -> pd.DataFrame:
    """Return the full product catalog."""
    return read_products()


def _id_list(values) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    return [str(value) for value in values if value]


def customer_state(customer_id: str | None, context: dict[str, Any]) -> policy.CustomerState:
    """Purchases and viewed products, newest first: live store rows, then warehouse."""
    stored = (
        customer_histories().get(customer_id, {})
        if customer_id and customer_histories_ready()
        else {}
    )
    bought = list(dict.fromkeys(_id_list(context.get("purchased_product_ids")) + stored.get("bought", [])))
    viewed = list(dict.fromkeys(_id_list(context.get("recent_product_ids")) + stored.get("viewed", [])))
    return policy.CustomerState(customer_id, bought, viewed[:HISTORY_LIMIT])


def _personalized(customer_id: str | None, context: dict[str, Any]) -> list[dict[str, Any]]:
    """The "Recommended for you" row: the shared policy that the evaluation scores."""
    limit = int(context.get("limit", PERSONALIZED_TOP_K))
    if limit < 1:
        raise ValueError("Recommendation limit must be positive")
    catalog = _catalog()
    segment, ordering = "first_order", "catalog"
    if not popularity.activity_ready():
        # Warm-up only: activity counts are still loading in the background.
        logger.info("[homepage] activity warming; serving the catalog fallback")
        pool = popularity.get_popular(limit=limit)
        ranked = sorted(pool, key=lambda item: (-item["score"], item["product_id"]))
    else:
        config = policy.load_config()  # chosen on validation by tune_policy.py
        state = customer_state(customer_id, context)
        sources = policy.Sources(
            model_as_of=None, use_content=heavy_models_enabled(),
            trending_window_days=config.trending_window_days,
        )
        exclude = _excluded_product_ids(context) | set(_context_product_ids(context))
        pool, _ = policy.build_candidates(state, sources, as_of=None, exclude=exclude, config=config)
        model = _load_ranker() if context.get("use_ranker", True) else None  # False: A/B control
        ranked = policy.rank(state, pool, sources, as_of=None, model=model, products=catalog, config=config)
        segment = "first_order" if state.first_timer else "repeat"
        if state.first_timer and config.first_timer_popularity:
            ordering = "popularity"
        else:
            ordering = "ranker_blend" if model is not None else "retrieval"
    results = []
    for item in ranked:
        if item["product_id"] not in catalog.index:
            continue
        product = catalog.loc[item["product_id"]]
        results.append({
            **item,
            "retrieval_score": float(item.get("retrieval_score", item["score"])),
            "name": product["product_name"],
            "image_url": product["image_url"],
            "category": product["h3_category"],
            "brand_name": product["brand_name"],
            "current_price": product["current_price"],
            "segment": segment,
            "ordering": ordering,  # how the row was ordered (drives the UI heading)
        })
        if len(results) >= min(limit, PERSONALIZED_TOP_K):
            break
    logger.info(
        "[homepage] personalized result_count=%d sources=%s",
        len(results), sorted({item.get("source", "unknown") for item in results}),
    )
    return results


def _exclude_purchased(
    customer_id: str | None, context: dict[str, Any]
) -> set[str]:
    """Return the set of product IDs already purchased by *customer_id*."""
    live_purchases = context.get("purchased_product_ids", [])
    if not isinstance(live_purchases, (list, tuple, set)):
        live_purchases = []
    excluded = {str(product_id) for product_id in live_purchases if product_id}
    if customer_id is None:
        return excluded
    if not customer_histories_ready():
        logger.info("Warehouse purchases are warming; using live purchase history")
        return excluded
    excluded.update(purchased_products().get(customer_id, ()))
    return excluded


@lru_cache(maxsize=1)
def _load_ranker() -> Any | None:
    """Return a trained LightGBM booster, or None if unavailable."""
    from .ranker import MODEL_PATH

    booster_path = MODEL_PATH
    if not booster_path.exists():
        return None
    import lightgbm as lgb

    return lgb.Booster(model_file=str(booster_path))


def _similar_fallback(
    product_id: str,
    catalog: pd.DataFrame,
    stock: pd.Series,
    excluded: set[str],
    limit: int,
) -> list[dict[str, Any]]:
    if product_id not in catalog.index:
        return []
    anchor = catalog.loc[product_id]
    anchor_category = anchor["h3_category"]
    if pd.isna(anchor_category):
        return []
    candidates = catalog[
        (catalog["h3_category"] == anchor_category)
        & catalog["gender"].map(
            lambda gender: gender_is_compatible(anchor["gender"], gender)
        )
        & catalog["is_active"].fillna(False).astype(bool)
    ].copy()
    candidates = candidates[
        candidates.index.isin(stock.index)
        & stock.reindex(candidates.index).gt(0)
        & ~candidates.index.isin(excluded)
    ].copy()
    candidates["_product_key"] = candidates.index.astype(str)
    candidates = candidates.sort_values(
        ["avg_rating", "review_count", "_product_key"],
        ascending=[False, False, True],
        kind="mergesort",
    )
    results = []
    for candidate_id in candidates.index[:limit]:
        product = candidates.loc[candidate_id]
        results.append({
            "product_id": candidate_id,
            "score": 0.65,
            "retrieval_score": 0.65,
            "source": "similar_fallback",
            "reason": "Same product type and compatible audience",
            "name": product["product_name"],
            "image_url": product["image_url"],
            "category": product["h3_category"],
            "brand_name": product["brand_name"],
            "current_price": product["current_price"],
        })
    return results


def warm_recommendation_caches() -> None:
    """Prepare expensive warehouse/model caches outside API request handling."""
    read_products()
    read_stock()
    read_realized_transactions()
    read_event_counts()
    customer_histories()
    purchased_products()
    popularity.warm_popularity()
    popularity.warm_activity_cache()
    _get_popular(100)
    if not heavy_models_enabled():
        logger.info(
            "Skipping collaborative and content models (PANTREE_HEAVY_RECOMMENDATIONS=0)"
        )
        return
    collaborative.warm_model()
    content.warm_index()


def heavy_models_enabled() -> bool:
    """Whether to load the memory-hungry ALS and sentence-transformer models.

    Set PANTREE_HEAVY_RECOMMENDATIONS=0 on small hosts (e.g. a 512 MB instance);
    recommendations then fall back to popularity, frequently-bought-together,
    and similar-category results.
    """
    return os.getenv("PANTREE_HEAVY_RECOMMENDATIONS", "1").strip() != "0"


def warm_homepage_caches() -> None:
    """Load only the small catalog data needed for an immediate homepage response."""
    read_products()
    read_stock()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _recommend_from_config(
    customer_id: str | None,
    surface: str,
    product_id: str | None,
    context: dict[str, Any],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    requested_limit = int(context.get("limit", config.get("top_k", 10)))
    if requested_limit < 1:
        raise ValueError("Recommendation limit must be positive")

    candidate_list: list[dict[str, Any]] = []
    for source_name, fn, source_limit in config["candidate_sources"]:
        kwargs: dict[str, Any] = {"limit": source_limit}
        if customer_id:
            kwargs["customer_id"] = customer_id
        if product_id:
            kwargs["product_id"] = product_id
        kwargs["context"] = context
        source_started = time.perf_counter()
        cands = fn(**kwargs)
        for candidate in cands:
            candidate.setdefault("source", source_name)
        candidate_list.extend(cands)
        logger.info(
            "[%s] source=%s candidates=%d elapsed_ms=%.1f",
            surface,
            source_name,
            len(cands),
            (time.perf_counter() - source_started) * 1000,
        )

    if not candidate_list:
        if surface != "product":
            candidate_list = _get_popular(limit=config.get("top_k", 10))
            if candidate_list:
                logger.info(
                    "[%s] fallback=%s",
                    surface,
                    candidate_list[0].get("source", "unknown"),
                )
        elif not (context.get("intent") == "similar" and product_id):
            return []
        if not candidate_list and not (
            surface == "product"
            and context.get("intent") == "similar"
            and product_id
        ):
            return []

    merged = fusion.merge_candidates(
        candidate_list,
        limit=config["max_candidates"],
        weights=config["fusion_weights"],
    )
    if config.get("exclude_purchased"):
        excluded = _exclude_purchased(customer_id, context)
    else:
        excluded = set()
    excluded.update(_excluded_product_ids(context))
    excluded.update(_context_product_ids(context))
    if product_id:
        excluded.add(product_id)
    merged = [item for item in merged if item["product_id"] not in excluded]

    catalog = _catalog()
    stock = read_stock()
    anchor_category = None
    if product_id in catalog.index:
        anchor_category = catalog.at[product_id, "h3_category"]
        if pd.isna(anchor_category):
            anchor_category = None
    intent = context.get("intent") if surface == "product" else None
    category_filter = context.get("category")
    anchor_gender = (
        catalog.at[product_id, "gender"]
        if product_id in catalog.index
        else None
    )
    filtered = []
    for candidate in merged:
        product_id_value = candidate["product_id"]
        if product_id_value not in catalog.index or product_id_value not in stock.index:
            continue
        product = catalog.loc[product_id_value]
        if pd.isna(product["is_active"]) or not bool(product["is_active"]):
            continue
        if float(stock.loc[product_id_value]) <= 0:
            continue
        category = product["h3_category"]
        if intent == "similar" and (
            anchor_category is None or pd.isna(category) or category != anchor_category
        ):
            continue
        if intent == "complementary" and (
            anchor_category is None or pd.isna(category) or category == anchor_category
        ):
            continue
        if surface == "product" and intent in {"similar", "complementary"} and (
            not gender_is_compatible(anchor_gender, product["gender"])
        ):
            continue
        product_categories = {
            value
            for value in (
                product["h1_category"],
                product["h2_category"],
                product["h3_category"],
            )
            if isinstance(value, str)
        }
        if category_filter and category_filter not in product_categories:
            continue
        candidate.update(
            {
                "name": product["product_name"],
                "image_url": product["image_url"],
                "category": category,
                "brand_name": product["brand_name"],
                "current_price": product["current_price"],
                "reason": candidate.get("reason", "Recommended for you"),
            }
        )
        candidate["score"] = float(candidate.get("score", 0.0))
        candidate["retrieval_score"] = float(
            candidate.get("retrieval_score", candidate["score"])
        )
        filtered.append(candidate)

    if intent == "similar" and len(filtered) < requested_limit and product_id:
        existing_ids = {item["product_id"] for item in filtered}
        fallback = _similar_fallback(
            product_id,
            catalog,
            stock,
            excluded | existing_ids,
            requested_limit - len(filtered),
        )
        filtered.extend(fallback)
        if fallback:
            logger.info(
                "[%s] fallback=same_category_gender count=%d",
                surface,
                len(fallback),
            )

    merged = filtered
    use_ranker = context.get("use_ranker", True)  # False for the A/B control group
    booster = _load_ranker() if use_ranker and event_counts_ready() else None
    if booster and len(merged) > 1 and model_uses_current_features(booster):
        _, product_counts = read_event_counts()
        live_history = [str(pid) for pid in context.get("recent_product_ids", []) or []]
        live_bought = [str(pid) for pid in context.get("purchased_product_ids", []) or []]
        stored = (
            customer_histories().get(customer_id, {})
            if customer_id and customer_histories_ready()
            else {}
        )
        bought = list(dict.fromkeys(live_bought + stored.get("bought", [])))
        profile = customer_profile(
            bought + live_history + stored.get("viewed", []), catalog, purchase_count=len(bought)
        )
        profile["event_count"] = max(
            profile["event_count"], int(context.get("online_event_count", 0))
        )
        merged = score_candidates(
            merged,
            model=booster,
            products=catalog,
            profile=profile,
            product_event_counts=product_counts,
            anchor_product_id=product_id,
        )

    merged.sort(
        key=lambda item: (-float(item.get("score", 0.0)), item["product_id"])
    )
    results = merged[: min(requested_limit, config.get("top_k", requested_limit))]
    logger.info(
        "[%s] result_count=%d result_sources=%s",
        surface,
        len(results),
        sorted({item.get("source", "unknown") for item in results}),
    )
    return results


def recommend(
    customer_id: str | None,
    surface: str,
    context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return ranked recommendations for a customer and storefront surface."""
    context = dict(context or {})
    config = SURFACE_CONFIG.get(surface)
    if config is None:
        raise ValueError(f"Unknown surface: {surface!r}")

    product_id = context.get("product_id")
    intent = context.get("intent")
    if surface == "homepage" and intent == "personalized":
        return _personalized(customer_id, context)
    elif surface == "homepage" and intent == "trending":
        config = {
            **config,
            "candidate_sources": [("trending", _get_popular, 100)],
            "fusion_weights": {"trending": 1.0},
            "exclude_purchased": False,
        }
    elif surface == "homepage" and intent is not None:
        raise ValueError(f"Unknown homepage recommendation intent: {intent!r}")

    if surface == "product":
        if intent == "similar":
            config = {
                **config,
                "candidate_sources": [("content", _get_content, 100)],
                "fusion_weights": {"content": 1.0},
            }
        elif intent == "complementary":
            config = {
                **config,
                "candidate_sources": [("fbt", _get_fbt, 50)],
                "fusion_weights": {"fbt": 1.0},
            }
        elif intent is not None:
            raise ValueError(f"Unknown product recommendation intent: {intent!r}")
    return _recommend_from_config(customer_id, surface, product_id, context, config)
