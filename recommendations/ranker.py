"""Point-in-time training and optional LightGBM ranking."""
import logging
from pathlib import Path

import numpy as np
import pandas as pd


logger = logging.getLogger(__name__)
FEATURES = [
    "customer_event_count", "product_event_count", "surface_id",
]
MODEL_PATH = Path(__file__).with_name("ranker.txt")
SURFACE_IDS = {"homepage": 0, "product": 1, "cart": 2}


def build_training_frame(sample_limit: int = 400000, seed: int = 17) -> pd.DataFrame:
    """Create click-time features with same-day post-event purchase labels."""
    from .candidates.common import DATA, read_realized_transactions

    events = pd.read_parquet(
        DATA / "clickstream.parquet",
        columns=[
            "customer_id", "session_id", "product_id", "event_timestamp",
            "event_type", "page_type",
        ],
    )
    events = events[
        events["event_type"].isin(["pdp_view", "add_to_cart", "wishlist_add"])
    ].dropna(subset=["customer_id", "product_id", "event_timestamp"]).copy()
    events["event_timestamp"] = pd.to_datetime(events["event_timestamp"], errors="coerce")
    events = events.dropna(subset=["event_timestamp"]).sort_values("event_timestamp")

    customer_times = events.groupby(["customer_id", "event_timestamp"]).size()
    product_times = events.groupby(["product_id", "event_timestamp"]).size()
    customer_prior = (customer_times.groupby(level=0).cumsum() - customer_times).rename(
        "customer_event_count"
    )
    product_prior = (product_times.groupby(level=0).cumsum() - product_times).rename(
        "product_event_count"
    )
    events = events.join(customer_prior, on=["customer_id", "event_timestamp"])
    events = events.join(product_prior, on=["product_id", "event_timestamp"])
    if sample_limit > 0:
        session_sizes = events.groupby("session_id").size()
        session_count = min(len(session_sizes), max(1, sample_limit // 4))
        selected_sessions = session_sizes.sample(
            n=session_count, random_state=seed
        ).index
        events = events[events["session_id"].isin(selected_sessions)]

    transactions = read_realized_transactions()
    transactions = transactions.sort_values("order_timestamp").drop_duplicates(
        ["customer_id", "product_id", "order_timestamp"]
    )
    transactions["purchase_day"] = transactions["order_timestamp"].dt.normalize()
    events["event_day"] = events["event_timestamp"].dt.normalize()
    purchases = transactions[
        ["customer_id", "product_id", "purchase_day", "order_timestamp"]
    ].rename(columns={"order_timestamp": "purchase_timestamp"})
    events["_example_id"] = range(len(events))
    events = events.merge(
        purchases,
        left_on=["customer_id", "product_id", "event_day"],
        right_on=["customer_id", "product_id", "purchase_day"],
        how="left",
    )
    events["label"] = (
        (events["purchase_timestamp"] > events["event_timestamp"])
        & (
            events["purchase_timestamp"]
            <= events["event_timestamp"] + pd.Timedelta(days=7)
        )
    ).astype("int8")
    events = events.groupby("_example_id", as_index=False, sort=False).agg(
        {
            "customer_id": "first",
            "session_id": "first",
            "product_id": "first",
            "event_timestamp": "first",
            "event_day": "first",
            "event_type": "first",
            "page_type": "first",
            "customer_event_count": "first",
            "product_event_count": "first",
            "label": "max",
        }
    )

    events["surface_id"] = events["page_type"].map(
        {"home": 0, "pdp": 1, "cart": 2}
    ).fillna(0)
    return events.dropna(subset=["customer_id", "product_id"])


def train_ranker(training_frame: pd.DataFrame):
    """Train LightGBM by session; return None if the optional extra is absent."""
    try:
        from lightgbm import LGBMRanker
    except ImportError:
        logger.warning("LightGBM ranking disabled; install dependencies from requirements.txt")
        return None

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
        n_estimators=120,
        learning_rate=0.05,
        num_leaves=15,
        random_state=17,
        n_jobs=1,
        deterministic=True,
        force_col_wise=True,
        verbosity=-1,
    )
    feature_values = np.ascontiguousarray(frame[FEATURES].to_numpy(dtype=np.float32))
    labels = np.ascontiguousarray(frame["label"].to_numpy(dtype=np.int32))
    model.fit(feature_values, labels, group=groups)
    return model


def score_candidates(
    candidates: list[dict],
    model=None,
    customer_event_count: int = 0,
    product_event_counts: pd.Series | None = None,
    surface: str = "homepage",
) -> list[dict]:
    """Apply the trained ranker when available, otherwise retain retrieval scores."""
    if not candidates:
        return []
    frame = pd.DataFrame(candidates)
    frame["customer_event_count"] = customer_event_count
    if product_event_counts is None:
        frame["product_event_count"] = 0
    else:
        frame["product_event_count"] = (
            product_event_counts.reindex(frame["product_id"]).fillna(0).to_numpy()
        )
    frame["surface_id"] = SURFACE_IDS[surface]
    if model is not None:
        rank_scores = model.predict(frame[FEATURES]).astype(float)
        score_range = float(np.ptp(rank_scores))
        if score_range:
            rank_scores = (rank_scores - rank_scores.min()) / score_range
        else:
            rank_scores = np.full(len(frame), 0.5)
        frame["score"] = 0.5 * frame["score"].astype(float) + 0.5 * rank_scores
    return frame.to_dict("records")
