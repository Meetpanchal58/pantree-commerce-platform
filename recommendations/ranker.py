"""Point-in-time candidate features and the optional LightGBM ranker.

The ranker re-orders a *retrieved* candidate list. It is trained on candidate
lists built for historical queries (see ``recommendations/evaluate.py``), so
the rows it learns from look exactly like the rows it scores in production.

``candidate_features`` is the single feature builder used by both training and
serving, which keeps the two from drifting apart (training/serving skew).
"""
import logging
import math
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd


logger = logging.getLogger(__name__)
MODEL_PATH = Path(__file__).with_name("ranker.txt")

FEATURES = [
    "retrieval_score",       # normalised score from the source that proposed it
    "source_id",             # which family of source proposed it
    "n_sources",             # how many sources proposed the same product
    "log_product_popularity",
    "log_customer_activity",
    "log_customer_purchases",  # how many products the customer bought before
    "category_affinity",     # share of the customer's history in this product type
    "log_price_ratio",       # candidate price vs the customer's typical price
    "avg_rating",
    "discount_pct",
    "same_type_as_anchor",
]
# 11 features in total; see candidate_features for how each is computed.

# Serving has more source names than the evaluation pool; map each to the
# family the ranker was trained on so it never sees an unknown value.
SOURCE_FAMILIES = {
    "collaborative": 0,
    "content": 1, "similar_fallback": 1,
    "fbt": 2, "session_pairing": 2, "learned_complement": 2, "category_fallback": 2,
    "trending": 3, "popularity": 3, "deals": 3, "catalog_fallback": 3,
    "recent_activity": 4, "deals_on_viewed": 4, "personalized": 4,
}


def customer_profile(history: list[str], products: pd.DataFrame, purchase_count: int = 0) -> dict:
    """Summarise a customer's prior interactions (most recent first)."""
    known = [pid for pid in history if pid in products.index]
    categories = Counter(products.loc[known, "h3_category"].dropna()) if known else Counter()
    prices = products.loc[known, "current_price"].dropna() if known else pd.Series(dtype=float)
    return {
        "event_count": len(history),
        "purchase_count": int(purchase_count),
        "category_counts": categories,
        "category_total": sum(categories.values()),
        "median_price": float(prices.median()) if len(prices) else None,
        "anchor_type": (
            products.at[known[0], "h3_category"] if known else None
        ),
    }


def candidate_features(
    frame: pd.DataFrame,
    products: pd.DataFrame,
    profile: dict,
    product_event_counts: pd.Series | None,
    anchor_product_id: str | None = None,
) -> pd.DataFrame:
    """Add ranker features to a candidate frame (one row per candidate).

    Cold-start defaults (customer with no earlier views or purchases, so an empty
    profile and no anchor): log_customer_activity = log_customer_purchases = 0,
    category_affinity = 0, log_price_ratio = 0 (i.e. price equal to the customer's
    typical price), same_type_as_anchor = 0. Product-side features are unaffected.
    """
    frame = frame.copy()
    ids = frame["product_id"]
    meta = products.reindex(ids)
    frame["retrieval_score"] = frame.get("retrieval_score", frame.get("score", 0.0)).astype(float)
    frame["source_id"] = frame["source"].map(SOURCE_FAMILIES).fillna(3).astype(int)
    frame["n_sources"] = frame.get("n_sources", pd.Series(1, index=frame.index)).fillna(1).astype(float)
    counts = (
        product_event_counts.reindex(ids).fillna(0).to_numpy()
        if product_event_counts is not None else np.zeros(len(frame))
    )
    frame["log_product_popularity"] = np.log1p(counts)
    frame["log_customer_activity"] = math.log1p(profile.get("event_count", 0))
    frame["log_customer_purchases"] = math.log1p(profile.get("purchase_count", 0))
    total = profile.get("category_total", 0)
    category_counts = profile.get("category_counts", {})
    frame["category_affinity"] = [
        (category_counts.get(cat, 0) / total) if total else 0.0
        for cat in meta["h3_category"].to_numpy()
    ]
    median_price = profile.get("median_price")
    prices = meta["current_price"].astype(float).to_numpy()
    if median_price:
        frame["log_price_ratio"] = np.log(np.clip(prices / median_price, 0.1, 10))
    else:
        frame["log_price_ratio"] = 0.0
    frame["log_price_ratio"] = frame["log_price_ratio"].fillna(0.0)
    frame["avg_rating"] = meta["avg_rating"].astype(float).fillna(0).to_numpy()
    mrp = meta["mrp"].astype(float).to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        discount = np.where(mrp > 0, (mrp - prices) / mrp, 0.0)
    frame["discount_pct"] = np.nan_to_num(discount)
    anchor_type = None
    if anchor_product_id and anchor_product_id in products.index:
        anchor_type = products.at[anchor_product_id, "h3_category"]
    elif profile.get("anchor_type"):
        anchor_type = profile["anchor_type"]
    frame["same_type_as_anchor"] = (
        meta["h3_category"].to_numpy() == anchor_type
    ).astype(float) if anchor_type else 0.0
    return frame


def train_ranker(training_frame: pd.DataFrame, features: list[str] | None = None):
    """Train a LambdaRank model grouped by session; None if LightGBM is absent."""
    try:
        from lightgbm import LGBMRanker
    except ImportError:
        logger.warning("LightGBM ranking disabled; install dependencies from requirements.txt")
        return None

    features = features or FEATURES
    frame = training_frame.copy()
    if frame["label"].nunique() < 2:
        raise ValueError("Ranker training needs both positive and negative future-purchase labels")
    group_labels = frame.groupby("session_id")["label"].agg(["size", "nunique"])
    useful_sessions = group_labels[
        (group_labels["size"] > 1) & (group_labels["nunique"] > 1)
    ].index
    frame = frame[frame["session_id"].isin(useful_sessions)]
    if frame.empty:
        raise ValueError("Ranker training needs sessions with both positive and negative labels")
    frame = frame.sort_values("session_id")
    groups = frame.groupby("session_id", sort=False).size().to_numpy(dtype=np.int32)
    model = LGBMRanker(
        objective="lambdarank",
        n_estimators=150,
        learning_rate=0.05,
        num_leaves=7,
        min_child_samples=50,
        subsample=0.8,
        subsample_freq=1,
        colsample_bytree=0.8,
        reg_lambda=1.0,
        random_state=17,
        n_jobs=1,
        deterministic=True,
        force_col_wise=True,
        verbosity=-1,
    )
    feature_values = np.ascontiguousarray(frame[features].to_numpy(dtype=np.float32))
    labels = np.ascontiguousarray(frame["label"].to_numpy(dtype=np.int32))
    model.fit(feature_values, labels, group=groups, feature_name=list(features))
    return model


def model_uses_current_features(model) -> bool:
    """True when a loaded booster was trained on ``FEATURES``."""
    try:
        names = model.feature_name()
    except AttributeError:
        return False
    return list(names) == FEATURES


def score_candidates(
    candidates: list[dict],
    model=None,
    products: pd.DataFrame | None = None,
    profile: dict | None = None,
    product_event_counts: pd.Series | None = None,
    anchor_product_id: str | None = None,
) -> list[dict]:
    """Blend the ranker with retrieval scores; keep retrieval order without a model."""
    if not candidates:
        return []
    frame = pd.DataFrame(candidates)
    if model is None or products is None or not model_uses_current_features(model):
        return frame.to_dict("records")
    featured = candidate_features(
        frame, products, profile or {}, product_event_counts, anchor_product_id
    )
    rank_scores = model.predict(
        np.ascontiguousarray(featured[FEATURES].to_numpy(dtype=np.float32))
    ).astype(float)
    score_range = float(np.ptp(rank_scores))
    if score_range:
        rank_scores = (rank_scores - rank_scores.min()) / score_range
    else:
        rank_scores = np.full(len(frame), 0.5)
    frame["score"] = 0.5 * frame["score"].astype(float) + 0.5 * rank_scores
    return frame.to_dict("records")
