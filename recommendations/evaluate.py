"""Small chronological holdout evaluation using real warehouse events."""
import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from recommendations.candidates.common import DATA, read_realized_transactions
from recommendations.ranker import (
    FEATURES,
    MODEL_PATH,
    build_training_frame,
    train_ranker,
)


def _ranking_metrics(frame: pd.DataFrame, scores, k: int) -> dict | None:
    if frame.empty:
        return None
    ranked = frame[["session_id", "product_id", "label"]].copy()
    ranked["prediction"] = scores
    recalls = []
    ndcgs = []
    reciprocal_ranks = []
    for _, group in ranked.groupby("session_id"):
        if group["label"].nunique() < 2:
            continue
        ordered = group.sort_values("prediction", ascending=False, kind="mergesort")
        labels = ordered["label"].to_numpy()
        top = labels[:k]
        ideal = np.sort(group["label"].to_numpy())[::-1][:k]
        discounts = 1.0 / np.log2(np.arange(2, len(top) + 2))
        ideal_dcg = float((ideal * discounts[:len(ideal)]).sum())
        dcg = float((top * discounts).sum())
        recalls.append(float(top.sum() / group["label"].sum()))
        ndcgs.append(dcg / ideal_dcg if ideal_dcg else 0.0)
        positive_ranks = np.flatnonzero(labels == 1)
        rank = int(positive_ranks[0]) + 1
        reciprocal_ranks.append(1.0 / rank if rank <= k else 0.0)
    if not recalls:
        return None
    return {
        "queries": len(recalls),
        "recall": float(np.mean(recalls)),
        "ndcg": float(np.mean(ndcgs)),
        "mrr": float(np.mean(reciprocal_ranks)),
    }


def _build_candidate_rows(
    queries: pd.DataFrame,
    training: pd.DataFrame,
    split_time: pd.Timestamp,
) -> pd.DataFrame:
    from recommendations.candidates.collaborative import get_collaborative
    from recommendations.candidates.content import get_similar_products, warm_index

    warm_index()
    transactions = read_realized_transactions()
    training_cutoff = split_time - pd.Timedelta(days=7)
    prior_transactions = transactions[
        transactions["order_timestamp"] < training_cutoff
    ]

    popular_counts = training.groupby("product_id").size().sort_values(
        ascending=False, kind="mergesort"
    )
    max_popularity = float(popular_counts.max()) if not popular_counts.empty else 0.0
    popular = [
        {
            "product_id": product_id,
            "score": float(count / max_popularity) if max_popularity else 0.0,
            "source": "trending",
        }
        for product_id, count in popular_counts.head(100).items()
    ]

    co_purchases: dict[str, Counter] = defaultdict(Counter)
    for product_ids in prior_transactions.groupby("order_id")["product_id"].unique():
        for first, second in combinations(sorted(product_ids), 2):
            co_purchases[first][second] += 1
            co_purchases[second][first] += 1

    future_purchases: dict[tuple[str, str], list[pd.Timestamp]] = defaultdict(list)
    for customer_id, product_id, order_time in transactions[
        ["customer_id", "product_id", "order_timestamp"]
    ].itertuples(index=False, name=None):
        future_purchases[(customer_id, product_id)].append(order_time)
    for times in future_purchases.values():
        times.sort()

    sessions = queries.sort_values("event_timestamp").to_dict("records")
    bought_by_customer: dict[str, set[str]] = defaultdict(set)
    transaction_rows = transactions.sort_values("order_timestamp")[
        ["customer_id", "product_id", "order_timestamp"]
    ].itertuples(index=False, name=None)
    next_transaction = next(transaction_rows, None)
    content_cache: dict[str, list[dict]] = {}
    collaborative_cache: dict[str, list[dict]] = {}
    rows = []

    for session in sessions:
        customer_id = session["customer_id"]
        event_time = session["event_timestamp"]
        while next_transaction and next_transaction[2] < event_time:
            bought_by_customer[next_transaction[0]].add(next_transaction[1])
            next_transaction = next(transaction_rows, None)

        anchor = session["product_id"]
        if customer_id not in collaborative_cache:
            collaborative_cache[customer_id] = get_collaborative(
                customer_id, limit=100, as_of=training_cutoff
            )
        if anchor not in content_cache:
            content_cache[anchor] = get_similar_products(anchor, limit=50)
        candidates: dict[str, dict] = {}
        max_pair_count = max(co_purchases[anchor].values(), default=0)
        related = [
            {
                "product_id": product_id,
                "score": count / max_pair_count if max_pair_count else 0.0,
                "source": "fbt",
            }
            for product_id, count in co_purchases[anchor].most_common(50)
        ]
        source_candidates = (
            collaborative_cache[customer_id],
            content_cache[anchor],
            related,
            popular,
        )
        for source in source_candidates:
            for candidate in source:
                product_id = candidate["product_id"]
                if product_id == anchor or product_id in bought_by_customer[customer_id]:
                    continue
                current = candidates.get(product_id)
                if current is None or candidate["score"] > current["score"]:
                    candidates[product_id] = candidate
        selected: dict[str, dict] = {}
        for source in source_candidates:
            added_from_source = 0
            for candidate in source:
                product_id = candidate["product_id"]
                if product_id in candidates and product_id not in selected:
                    selected[product_id] = candidates[product_id]
                    added_from_source += 1
                if added_from_source == 5 or len(selected) == 20:
                    break
        for candidate in sorted(
            candidates.values(),
            key=lambda item: (-float(item["score"]), item["product_id"]),
        ):
            if len(selected) == 20:
                break
            selected.setdefault(candidate["product_id"], candidate)
        pool = list(selected.values())

        for candidate in pool:
            times = future_purchases.get((customer_id, candidate["product_id"]), [])
            next_index = bisect_right(times, event_time)
            is_positive = (
                next_index < len(times)
                and times[next_index] <= event_time + pd.Timedelta(days=7)
                and times[next_index].normalize() == event_time.normalize()
            )
            rows.append(
                {
                    "session_id": session["session_id"],
                    "customer_id": customer_id,
                    "product_id": candidate["product_id"],
                    "label": int(is_positive),
                    "event_timestamp": event_time,
                    "customer_event_count": session["customer_event_count"],
                    "surface_id": session["surface_id"],
                    "retrieval_score": candidate["score"],
                    "source": candidate["source"],
                }
            )

    return pd.DataFrame(
        rows,
        columns=[
            "session_id", "customer_id", "product_id", "label",
            "event_timestamp", "customer_event_count", "surface_id",
            "retrieval_score", "source",
        ],
    )


def _customer_cohorts(test: pd.DataFrame, split_time: pd.Timestamp) -> dict[str, set[str]]:
    transactions = read_realized_transactions()
    prior_orders = transactions[
        transactions["order_timestamp"] < split_time
    ].groupby("customer_id")["order_id"].nunique()

    clickstream = pd.read_parquet(
        DATA / "clickstream.parquet",
        columns=["customer_id", "event_timestamp"],
    )
    clickstream["event_timestamp"] = pd.to_datetime(
        clickstream["event_timestamp"], errors="coerce"
    )
    prior_activity = set(
        clickstream.loc[
            clickstream["event_timestamp"] < split_time, "customer_id"
        ].dropna()
    )
    test_customers = set(test["customer_id"])
    ordered_customers = set(prior_orders.index)
    return {
        "cold_start": test_customers - prior_activity - ordered_customers,
        "one_time": set(prior_orders[prior_orders == 1].index),
        "repeat_base": set(prior_orders[prior_orders >= 2].index),
        "browse_only": prior_activity - ordered_customers,
    }


def main(sample_limit: int = 400000) -> None:
    frame = build_training_frame(sample_limit=sample_limit)
    boundary = frame["event_timestamp"].quantile(0.8)
    train = frame[frame["event_timestamp"] < boundary - pd.Timedelta(days=7)]
    latest_complete_event = frame["event_timestamp"].max() - pd.Timedelta(days=7)
    test = frame[
        frame["event_timestamp"].between(boundary, latest_complete_event)
    ].copy()
    model = train_ranker(train)
    print(f"Training examples: {len(train):,}; holdout examples: {len(test):,}")
    training_rankable = int(
        train.groupby("session_id")["label"].nunique().gt(1).sum()
    )
    print(f"Mixed-label training sessions: {training_rankable:,}")
    if model is not None:
        queries = test.sort_values("event_timestamp").groupby(
            "session_id", as_index=False, sort=False
        ).first()
        candidates = _build_candidate_rows(queries, train, boundary)
        pool_sizes = candidates.groupby("session_id").size()
        positive_sessions = candidates.groupby("session_id")["label"].max()
        enough_pool = set(pool_sizes[pool_sizes >= 10].index)
        candidates = candidates[candidates["session_id"].isin(enough_pool)].copy()
        print(
            f"Holdout sessions: {len(queries):,}; retrieved candidate pool "
            f"median={pool_sizes.median():.0f}, p10={pool_sizes.quantile(.1):.0f}, "
            f"sessions with >=10 candidates={len(enough_pool):,}"
        )
        print(
            f"Sessions with a retrieved future-purchase candidate: "
            f"{int(positive_sessions.sum()):,}/{len(queries):,}"
        )
        print(
            f"Rankable sessions (>=10 candidates, both labels): "
            f"{int(candidates.groupby('session_id')['label'].nunique().gt(1).sum()):,}"
        )
        product_counts = train.groupby("product_id").size()
        candidates["product_event_count"] = (
            candidates["product_id"].map(product_counts).fillna(0)
        )
        cohorts = _customer_cohorts(test, boundary)
        for cohort, customer_ids in cohorts.items():
            subset = candidates[candidates["customer_id"].isin(customer_ids)]
            mixed_session_ids = subset.groupby("session_id")["label"].nunique()
            mixed_session_ids = mixed_session_ids[mixed_session_ids > 1].index
            rankable_subset = subset[subset["session_id"].isin(mixed_session_ids)]
            customers = rankable_subset["customer_id"].nunique()
            print(
                f"{cohort}: holdout sessions="
                f"{queries[queries['customer_id'].isin(customer_ids)]['session_id'].nunique():,}; "
                f"mixed-label sessions={len(mixed_session_ids):,}; "
                f"rankable customers={customers:,}"
            )
            if rankable_subset.empty:
                print("  Metrics unavailable: no mixed-label sessions with >=10 candidates.")
                continue
            rankable_subset = rankable_subset.copy()
            rankable_subset["prediction"] = model.predict(
                np.ascontiguousarray(
                    rankable_subset[FEATURES].to_numpy(dtype=np.float32)
                )
            )
            for k in (5, 10):
                metrics = _ranking_metrics(rankable_subset, rankable_subset["prediction"], k)
                if metrics is None:
                    continue
                if metrics["queries"] < 100:
                    status = "UNDERPOWERED"
                elif metrics["queries"] < 500:
                    status = "PROVISIONAL"
                else:
                    status = "ADEQUATELY POWERED"
                print(
                    f"  K={k}: sessions={metrics['queries']:,} ({status}); "
                    f"Recall@{k}={metrics['recall']:.4f}, "
                    f"NDCG@{k}={metrics['ndcg']:.4f}, MRR@{k}={metrics['mrr']:.4f}"
                )
        model.booster_.save_model(str(MODEL_PATH))
        print(f"Saved serving model: {MODEL_PATH}")
    else:
        print("Ranking metrics skipped: install dependencies from requirements.txt for LightGBM.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument(
        "--sample-limit",
        type=int,
        default=400000,
        help="Approximate number of click events to sample; use 0 for all events.",
    )
    main(parser.parse_args().sample_limit)
