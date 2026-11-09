"""Chronological offline evaluation of Pantree recommendations.

Time is split into three periods so nothing is learned from the future:

    A ─────────────── t_rank ─── B ─── t_test ─── C ─── end
    retrieval models   ranker trained on   everything is
    learn from here    candidate lists     scored here

* Candidate lists and the final ordering come from ``recommendations/policy.py``,
  the code the storefront serves. Model-like sources (ALS, co-purchases) only use
  data before each period's cut-off (with a 7-day gap so labels never overlap);
  trending, recent activity, product popularity and stock are taken as of each
  query time.
* Each query is a real order: "given everything before this order, does the
  system rank the products the customer actually bought?" (the job of the
  home-page "Recommended for you" row). The query time is one hour before the
  order, so the visit that led to the order is not visible.
* The ranker is trained on candidate lists for period-B orders and scored on
  period-C orders.
* Every ordering is compared on the *same* candidate lists, so differences come
  from ranking alone: random, popularity, retrieval score (production without a
  ranker), the ranker, and the production blend.

Results are printed and written to ``recommendations/reports/``.
"""
import argparse
import json
from bisect import bisect_left
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from recommendations.candidates.common import (  # noqa: E402
    DATA,
    read_products,
    read_realized_transactions,
)
from recommendations.policy import (  # noqa: E402  (the served policy)
    PER_SOURCE as POOL_PER_SOURCE,
    POOL_SIZE,
    CustomerState,
    Sources,
    blend,
    build_candidates,
    final_scores,
)
from recommendations.oracle import (  # noqa: E402  (evaluation-only ground truth)
    gap_closed_bootstrap,
    load_taste_inputs,
    oracle_scores,
    retrieval_ceiling,
)
from recommendations.ranker import (  # noqa: E402
    FEATURES,
    MODEL_PATH,
    candidate_features,
    customer_profile,
    train_ranker,
)

REPORT_DIR = Path(__file__).with_name("reports")
EMBARGO = pd.Timedelta(days=7)
MIN_POOL = 10
QUERY_OFFSET = pd.Timedelta(hours=1)


# --------------------------------------------------------------------------- metrics
def session_metrics(labels: np.ndarray, k: int, relevant: int | None = None) -> tuple[float, float, float]:
    """Recall@k, NDCG@k and MRR@k for one ranked list of 0/1 labels.

    ``relevant`` is the number of truly relevant items, including any the
    candidate list missed (end-to-end view). Defaults to the list's positives.
    """
    relevant = int(labels.sum()) if relevant is None else int(relevant)
    if relevant == 0:
        return 0.0, 0.0, 0.0
    top = labels[:k]
    discounts = 1.0 / np.log2(np.arange(2, k + 2))
    ideal_dcg = float(discounts[: min(relevant, k)].sum())
    dcg = float((top * discounts[: len(top)]).sum())
    recall = float(top.sum() / min(relevant, k))
    hits = np.flatnonzero(labels == 1)
    mrr = 1.0 / (hits[0] + 1) if len(hits) and hits[0] < k else 0.0
    return recall, dcg / ideal_dcg, mrr


def per_session_scores(frame: pd.DataFrame, column: str, k: int, end_to_end: bool = False) -> pd.DataFrame:
    rows = []
    for session_id, group in frame.groupby("session_id", sort=False):
        ordered = group.sort_values([column, "product_id"], ascending=[False, True], kind="mergesort")
        relevant = int(group["n_purchased"].iloc[0]) if end_to_end else None
        recall, ndcg, mrr = session_metrics(ordered["label"].to_numpy(), k, relevant)
        rows.append((session_id, recall, ndcg, mrr))
    return pd.DataFrame(rows, columns=["session_id", "recall", "ndcg", "mrr"])


def power_label(sessions: int) -> str:
    if sessions < 100:
        return "UNDERPOWERED"
    if sessions < 500:
        return "PROVISIONAL"
    return "ADEQUATELY POWERED"


def paired_bootstrap(a: np.ndarray, b: np.ndarray, iterations: int = 2000, seed: int = 7) -> dict:
    """95% CI for mean(a - b) over sessions, resampling sessions with replacement."""
    rng = np.random.default_rng(seed)
    diff = a - b
    if len(diff) == 0:
        return {"mean_diff": None, "ci_low": None, "ci_high": None}
    means = rng.choice(diff, size=(iterations, len(diff)), replace=True).mean(axis=1)
    return {
        "mean_diff": float(diff.mean()),
        "ci_low": float(np.quantile(means, 0.025)),
        "ci_high": float(np.quantile(means, 0.975)),
    }


# --------------------------------------------------------------------------- data helpers
def load_activity() -> pd.DataFrame:
    events = pd.read_parquet(
        DATA / "clickstream.parquet",
        columns=["customer_id", "session_id", "product_id", "event_type", "event_timestamp"],
    )
    events = events[events["event_type"].isin(["pdp_view", "add_to_cart", "wishlist_add"])]
    events = events.dropna(subset=["customer_id", "product_id", "event_timestamp"]).copy()
    events["event_timestamp"] = pd.to_datetime(events["event_timestamp"], errors="coerce")
    return events.dropna(subset=["event_timestamp"]).sort_values("event_timestamp")


class History:
    """Point-in-time look-ups: what a customer viewed/bought before a moment."""

    def __init__(self, activity: pd.DataFrame, transactions: pd.DataFrame):
        # Look-ups bisect on time, so each customer's rows must be in time order.
        activity = activity.sort_values("event_timestamp", kind="mergesort")
        self.views = {
            cid: (g["event_timestamp"].to_numpy(), g["product_id"].to_numpy())
            for cid, g in activity.groupby("customer_id", sort=False)
        }
        tx = transactions.sort_values("order_timestamp")
        self.buys = {
            cid: (g["order_timestamp"].to_numpy(), g["product_id"].to_numpy())
            for cid, g in tx.groupby("customer_id", sort=False)
        }

    def viewed_before(self, cid, ts, limit=200) -> list[str]:
        times, pids = self.views.get(cid, (np.array([], dtype="datetime64[ns]"), np.array([])))
        end = bisect_left(times, np.datetime64(ts))
        recent = pids[max(0, end - limit):end][::-1]
        return list(dict.fromkeys(recent.tolist()))

    def bought_before(self, cid, ts) -> set[str]:
        return set(self.bought_list(cid, ts))

    def bought_list(self, cid, ts) -> list[str]:
        """Products bought before ``ts``, most recent first."""
        times, pids = self.buys.get(cid, (np.array([], dtype="datetime64[ns]"), np.array([])))
        return list(dict.fromkeys(pids[: bisect_left(times, np.datetime64(ts))][::-1].tolist()))

    def profile_history(self, cid, ts) -> list[str]:
        return self.bought_list(cid, ts) + self.viewed_before(cid, ts)



def customer_state(history: History, cid, ts) -> CustomerState:
    """What serving would know about the customer at ``ts``."""
    return CustomerState(cid, history.bought_list(cid, ts), history.viewed_before(cid, ts))


def build_pools(queries, sources: Sources, history: History) -> tuple[pd.DataFrame, dict]:
    """Candidate list per order (the served policy's builder) + per-source hit statistics.

    ``queries`` has one row per order: customer_id, query_time, purchased (set).
    """
    rows = []
    source_hits = Counter()
    pool_hits = 0
    for q in queries.itertuples(index=False):
        cid, ts, purchased = q.customer_id, q.query_time, q.purchased
        state = customer_state(history, cid, ts)
        pool, lists = build_candidates(state, sources, as_of=ts)
        for name, items in lists.items():
            if any(c["product_id"] in purchased for c in items):
                source_hits[name] += 1
        excluded = set(state.bought)
        hit = False
        for c in pool:
            label = int(c["product_id"] in purchased)
            hit = hit or bool(label)
            rows.append({
                "session_id": q.order_id, "customer_id": cid, "anchor": state.anchor,
                "event_timestamp": ts, "product_id": c["product_id"], "source": c["source"],
                "retrieval_score": float(c["retrieval_score"]), "score": float(c["retrieval_score"]),
                "n_sources": c["n_sources"], "label": label,
                "n_purchased": len(purchased - excluded),
                "first_timer": state.first_timer,
            })
        pool_hits += int(hit)
    stats = {
        "queries": int(len(queries)),
        "pool_contains_purchase": int(pool_hits),
        "source_hits": {k: int(v) for k, v in source_hits.items()},
    }
    return pd.DataFrame(rows), stats


def order_queries(transactions, start, end) -> pd.DataFrame:
    """One query per paid order in [start, end): what did the customer buy?"""
    window = transactions[transactions["order_timestamp"].between(start, end)]
    grouped = window.groupby("order_id").agg(
        customer_id=("customer_id", "first"),
        order_timestamp=("order_timestamp", "min"),
        purchased=("product_id", lambda s: set(s)),
    ).reset_index()
    grouped["query_time"] = grouped["order_timestamp"] - QUERY_OFFSET
    return grouped.sort_values("query_time").reset_index(drop=True)


def add_features(pools, sources: Sources, history: History, products) -> pd.DataFrame:
    """Ranker features per candidate, with product popularity as of the query time."""
    parts = []
    # dropna=False keeps cold-start orders (no earlier activity, so no anchor); their
    # features take the documented empty-history defaults in ranker.candidate_features.
    for (_, cid, anchor, ts), group in pools.groupby(
        ["session_id", "customer_id", "anchor", "event_timestamp"], sort=False, dropna=False
    ):
        anchor = anchor if isinstance(anchor, str) else None
        state = customer_state(history, cid, ts)
        counts = sources.popularity(ts)
        profile = customer_profile(state.history, products, purchase_count=len(state.bought))
        featured = candidate_features(group, products, profile, counts, anchor)
        featured["product_event_count"] = featured["product_id"].map(counts).fillna(0).to_numpy()
        parts.append(featured)
    return pd.concat(parts, ignore_index=True)


def rankable(frame: pd.DataFrame) -> pd.DataFrame:
    stats = frame.groupby("session_id")["label"].agg(["size", "nunique"])
    keep = stats[(stats["size"] >= MIN_POOL) & (stats["nunique"] > 1)].index
    return frame[frame["session_id"].isin(keep)].copy()


# --------------------------------------------------------------------------- main
def main(use_content: bool = True) -> dict:
    products = read_products()
    transactions = read_realized_transactions()
    activity = load_activity()
    history = History(activity, transactions)

    times = transactions["order_timestamp"]
    t_test = times.quantile(0.8)
    t_rank = times.quantile(0.6)
    t_end = times.max()
    queries_b = order_queries(transactions, t_rank, t_test - EMBARGO)
    queries_c = order_queries(transactions, t_test, t_end)
    print(f"Periods: ranker orders {t_rank:%Y-%m-%d}..{t_test - EMBARGO:%Y-%m-%d} "
          f"({len(queries_b):,}), test orders {t_test:%Y-%m-%d}..{t_end:%Y-%m-%d} ({len(queries_c):,})")

    if use_content:
        from recommendations.candidates.content import warm_index

        warm_index()
    sources_b = Sources(model_as_of=t_rank - EMBARGO, use_content=use_content)
    pools_b, _ = build_pools(queries_b, sources_b, history)
    train_b = add_features(pools_b, sources_b, history, products)

    sources_c = Sources(model_as_of=t_test - EMBARGO, use_content=use_content)
    pools_c, retrieval_stats = build_pools(queries_c, sources_c, history)
    test_c = add_features(pools_c, sources_c, history, products)

    model = train_ranker(train_b, FEATURES)

    test_all = test_c[test_c["n_purchased"] > 0].copy()
    rng = np.random.default_rng(11)
    test_all["random"] = rng.random(len(test_all))
    test_all["popularity"] = test_all["product_event_count"]
    test_all["retrieval"] = test_all["retrieval_score"]
    orderings = ["random", "popularity", "retrieval"]
    if model is not None:
        raw = model.predict(np.ascontiguousarray(test_all[FEATURES].to_numpy(dtype=np.float32)))
        test_all["new_ranker"] = raw
        # Ranker blend for everyone (the served policy uses it for returning customers).
        test_all["production_blend"] = 0.0
        for _, idx in test_all.groupby("session_id", sort=False).indices.items():
            test_all.iloc[idx, test_all.columns.get_loc("production_blend")] = blend(
                test_all["retrieval_score"].to_numpy()[idx], raw[idx]
            )
        orderings += ["new_ranker", "production_blend"]
    # The served policy (policy.final_scores): popularity order for customers with
    # no purchases, the ranker blend for everyone else. Same function as serving.
    test_all["segment_aware"] = 0.0
    col = test_all.columns.get_loc("segment_aware")
    retrieval_arr = test_all["retrieval_score"].to_numpy()
    pop_arr = test_all["product_event_count"].to_numpy(dtype=float)
    first_arr = test_all["first_timer"].to_numpy()
    for _, idx in test_all.groupby("session_id", sort=False).indices.items():
        test_all.iloc[idx, col] = final_scores(
            retrieval_arr[idx], pop_arr[idx], bool(first_arr[idx][0]),
            None if model is None else test_all["new_ranker"].to_numpy()[idx],
        )
    orderings.append("segment_aware")
    # Oracle: rank by the simulator's true taste function (a ceiling, not deployable).
    profiles, taste_catalog, catalog_median = load_taste_inputs()
    test_all["oracle"] = oracle_scores(test_all, profiles, taste_catalog, catalog_median)
    orderings.append("oracle")
    test = rankable(test_all)

    first_time = set(queries_c.loc[[not history.bought_list(c, t) for c, t in
                                    zip(queries_c["customer_id"], queries_c["query_time"])], "order_id"])
    groups = {
        "all": set(test_all["session_id"]),
        "first_order": set(test_all["session_id"]) & first_time,
        "repeat_order": set(test_all["session_id"]) - first_time,
    }
    views = {
        "end_to_end": (test_all, True),   # every test order; purchases missing from the list count as misses
        "ranking_only": (test, False),    # only lists that contain a purchase: pure ordering quality
    }
    scores = {
        view: {o: {k: per_session_scores(frame_, o, k, e2e) for k in (5, 10)} for o in orderings}
        for view, (frame_, e2e) in views.items()
    }
    results = {}
    for view, (frame_, _) in views.items():
        results[view] = {}
        for group, sessions in groups.items():
            sessions = sessions & set(frame_["session_id"])
            res = {"orders": len(sessions), "power": power_label(len(sessions)), "orderings": {}}
            for o in orderings:
                res["orderings"][o] = {}
                for k in (5, 10):
                    sc = scores[view][o][k]
                    sc = sc[sc["session_id"].isin(sessions)]
                    res["orderings"][o][f"@{k}"] = (
                        {m: float(sc[m].mean()) for m in ("recall", "ndcg", "mrr")} if len(sc) else None
                    )
            results[view][group] = res

    comparisons = {}
    if "new_ranker" in orderings:
        base = scores["end_to_end"]["new_ranker"][10].set_index("session_id")["ndcg"]
        for other in ("retrieval", "popularity", "random"):
            if other in orderings:
                o = scores["end_to_end"][other][10].set_index("session_id")["ndcg"].reindex(base.index)
                comparisons[f"new_ranker_vs_{other}"] = paired_bootstrap(base.to_numpy(), o.to_numpy())
    seg = scores["end_to_end"]["segment_aware"][10].set_index("session_id")["ndcg"]
    for other in ("retrieval", "popularity", "production_blend"):
        if other in orderings:
            o = scores["end_to_end"][other][10].set_index("session_id")["ndcg"].reindex(seg.index)
            comparisons[f"segment_aware_vs_{other}"] = paired_bootstrap(seg.to_numpy(), o.to_numpy())
    # Relative lift of the served (segment-aware) policy over retrieval order, with the
    # same paired resampling of orders (ratio of means, so the denominator varies too).
    retrieval_ndcg = scores["end_to_end"]["retrieval"][10].set_index("session_id")["ndcg"].reindex(seg.index).to_numpy()
    served_ndcg = seg.to_numpy()
    resample = np.random.default_rng(7).integers(0, len(served_ndcg), size=(2000, len(served_ndcg)))
    rel = served_ndcg[resample].mean(axis=1) / retrieval_ndcg[resample].mean(axis=1) - 1
    relative_lift = {
        "served_ndcg10": float(served_ndcg.mean()),
        "retrieval_ndcg10": float(retrieval_ndcg.mean()),
        "value": float(served_ndcg.mean() / retrieval_ndcg.mean() - 1),
        "ci_low": float(np.quantile(rel, 0.025)),
        "ci_high": float(np.quantile(rel, 0.975)),
    }

    # Share of the achievable gain (random -> oracle) each ordering captures, NDCG@10.
    gap_closed = {}
    for view in views:
        gap_closed[view] = {}
        for group, sessions in groups.items():
            ndcg = {
                o: scores[view][o][10].set_index("session_id")["ndcg"]
                for o in orderings
            }
            keep = ndcg["random"].index[ndcg["random"].index.isin(sessions)]
            gap_closed[view][group] = {
                o: gap_closed_bootstrap(
                    ndcg[o].reindex(keep).to_numpy(),
                    ndcg["random"].reindex(keep).to_numpy(),
                    ndcg["oracle"].reindex(keep).to_numpy(),
                )
                for o in orderings
            }

    ceiling = retrieval_ceiling(queries_c, history, profiles, taste_catalog, catalog_median)
    per_order = test_c.groupby("session_id").agg(hits=("label", "sum"), n=("n_purchased", "first"))
    per_order = per_order[per_order["n"] > 0]
    ceiling["candidate_list_hit_rate"] = retrieval_stats["pool_contains_purchase"] / retrieval_stats["queries"]
    ceiling["candidate_list_recall"] = float((per_order["hits"] / per_order["n"].clip(upper=POOL_SIZE)).mean())

    # Why the order counts differ between sections.
    pool_sizes = test_c.groupby("session_id")["label"].agg(["size", "sum"])
    with_purchase = pool_sizes[pool_sizes["sum"] > 0]
    scored = set(test_c["session_id"])
    cold_start = set(pools_c.loc[pools_c["anchor"].isna(), "session_id"])
    order_counts = {
        "test_orders": int(len(queries_c)),
        "orders_with_new_products": int(test_all["session_id"].nunique()),
        "cold_start_orders": int(len(cold_start)),
        "orders_only_rebuying_previous_products": int(len(scored) - test_all["session_id"].nunique()),
        "orders_with_purchase_in_list": int(retrieval_stats["pool_contains_purchase"]),
        "dropped_list_under_min_pool": int(((with_purchase["size"] < MIN_POOL)).sum()),
        "dropped_list_all_purchased": int(((with_purchase["size"] >= MIN_POOL) & (with_purchase["sum"] == with_purchase["size"])).sum()),
        "ranking_only_orders": int(test["session_id"].nunique()),
    }

    importance = {}
    if model is not None:
        gains = model.booster_.feature_importance(importance_type="gain")
        total = float(gains.sum()) or 1.0
        importance = {f: round(float(g) / total, 4) for f, g in sorted(zip(FEATURES, gains), key=lambda x: -x[1])}
        model.booster_.save_model(str(MODEL_PATH))

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "settings": {
            "pool_size": POOL_SIZE, "per_source": POOL_PER_SOURCE,
            "min_pool": MIN_POOL, "embargo_days": EMBARGO.days, "content_source": use_content,
            "t_rank": str(t_rank), "t_test": str(t_test), "t_end": str(t_end),
            "ranker_training_rows": int(len(train_b)),
            "ranker_training_orders": int(train_b["session_id"].nunique()),
            "ranker_training_orders_with_purchase_in_list": int(train_b.groupby("session_id")["label"].max().sum()),
            "test_orders": int(len(queries_c)),
        },
        "retrieval": {
            **retrieval_stats,
            "pool_coverage": (retrieval_stats["pool_contains_purchase"] / retrieval_stats["queries"])
            if retrieval_stats["queries"] else None,
        },
        "results": results,
        "comparisons_ndcg10": comparisons,
        "relative_lift_served_vs_retrieval_ndcg10": relative_lift,
        "gap_closed_ndcg10": gap_closed,
        "retrieval_ceiling": ceiling,
        "order_counts": order_counts,
        "feature_importance": importance,
    }
    write_report(report)
    print_report(report)
    return report


NAMES = {
    "random": "Random order", "popularity": "Popularity (most viewed)",
    "retrieval": "Retrieval score (no ranker)",
    "new_ranker": "New ranker (11 features)", "production_blend": "Production blend (50/50)",
    "segment_aware": "Segment-aware (popular for first-timers, blend otherwise)",
    "oracle": "Oracle (true simulator taste, not deployable)",
}
VIEW_TEXT = {
    "end_to_end": "Every test order. A bought product missing from the candidate list counts as a miss.",
    "ranking_only": "Only orders whose candidate list contains a bought product: measures ordering alone.",
}


def print_report(report: dict) -> None:
    r = report["retrieval"]
    print(f"\nRetrieval: {r['pool_contains_purchase']:,}/{r['queries']:,} test orders have a bought item in the candidate list")
    for name, hits in sorted(r["source_hits"].items(), key=lambda x: -x[1]):
        print(f"  {name:16s} contains a bought item for {hits:,} orders")
    for view, groups in report["results"].items():
        for group, res in groups.items():
            print(f"\n[{view} / {group}] orders={res['orders']:,} ({res['power']})")
            for o, m in res["orderings"].items():
                if m["@10"]:
                    print(f"  {o:17s} Recall@10={m['@10']['recall']:.3f}  NDCG@10={m['@10']['ndcg']:.3f}  MRR@10={m['@10']['mrr']:.3f}")
    for name, c in report["comparisons_ndcg10"].items():
        if c["mean_diff"] is not None:
            print(f"{name}: ΔNDCG@10={c['mean_diff']:+.4f} (95% CI {c['ci_low']:+.4f} to {c['ci_high']:+.4f})")
    for name, g in report["gap_closed_ndcg10"]["end_to_end"]["all"].items():
        if g["value"] is not None:
            print(f"gap closed [{name}]: {g['value']:.1%} (95% CI {g['ci_low']:.1%} to {g['ci_high']:.1%})")
    c = report["retrieval_ceiling"]
    print(f"Retrieval ceiling: top-{c['k']} by true taste contains a bought product for {c['hit_rate']:.1%} "
          f"of orders vs {c['candidate_list_hit_rate']:.1%} for the candidate lists")
    print("Order counts:", report["order_counts"])
    if report["feature_importance"]:
        print("Ranker feature importance (gain share):", report["feature_importance"])


def write_report(report: dict) -> None:
    REPORT_DIR.mkdir(exist_ok=True)
    (REPORT_DIR / "evaluation_report.json").write_text(json.dumps(report, indent=2))
    st = report["settings"]
    lines = ["# Recommendation evaluation report", "",
             f"Generated {report['generated_at']} by `python -m recommendations.evaluate`.", "",
             "**Question asked:** for each real order in the held-out period, given everything known one hour before it, "
             "does the system rank the products the customer actually bought near the top?", "",
             f"Test orders: {st['test_orders']:,}. Candidate list: up to {st['pool_size']} products "
             f"({st['per_source']} per source, then filled by score). Products the customer already bought, and products "
             "that were out of stock, not yet launched or discontinued at the query time, are excluded. The candidate "
             "list and the segment-aware ordering come from `recommendations/policy.py`, the same code the storefront "
             "serves.", ""]
    r = report["retrieval"]
    lines += ["## 1. Retrieval: is the right product in the list at all?", "",
              f"{r['pool_contains_purchase']:,} of {r['queries']:,} test orders ({(r['pool_coverage'] or 0):.1%}) "
              "had a bought product somewhere in the candidate list.", "",
              "| Source | Orders where its list contained a bought product |", "|---|---|"]
    for name, hits in sorted(r["source_hits"].items(), key=lambda x: -x[1]):
        lines.append(f"| {name} | {hits:,} ({hits / r['queries']:.1%}) |")
    for i, (view, groups) in enumerate(report["results"].items(), start=2):
        lines += ["", f"## {i}. Ranking quality: {view.replace('_', ' ')}", "", VIEW_TEXT[view]]
        for group, res in groups.items():
            lines += ["", f"### {group.replace('_', ' ').title()} — {res['orders']:,} orders ({res['power']})", "",
                      "| Ordering | Recall@5 | NDCG@5 | Recall@10 | NDCG@10 | MRR@10 |", "|---|---|---|---|---|---|"]
            for o, m in res["orderings"].items():
                if m["@10"]:
                    lines.append(f"| {NAMES.get(o, o)} | {m['@5']['recall']:.3f} | {m['@5']['ndcg']:.3f} | "
                                 f"{m['@10']['recall']:.3f} | {m['@10']['ndcg']:.3f} | {m['@10']['mrr']:.3f} |")
    if report["comparisons_ndcg10"]:
        lines += ["", "## Are the differences real? (paired bootstrap over test orders, end-to-end NDCG@10)", "",
                  "| Comparison | Mean difference | 95% confidence interval |", "|---|---|---|"]
        for name, c in report["comparisons_ndcg10"].items():
            if c["mean_diff"] is not None:
                lines.append(f"| {name.replace('_', ' ')} | {c['mean_diff']:+.4f} | {c['ci_low']:+.4f} to {c['ci_high']:+.4f} |")
        lines += ["", "If the interval does not include 0, the difference is unlikely to be chance."]
    lines += ["", "## Share of achievable gain captured (NDCG@10)", "",
              "Because the data is synthetic, the true taste function is known. The oracle ranks each candidate "
              "list by it (ceiling, not deployable). Share captured = (ordering − random) ÷ (oracle − random), "
              "with a paired bootstrap 95% CI over orders. Random is 0% and the oracle 100% by construction; "
              "values above 100% mean the ordering beat the taste ceiling on realised purchases.", ""]
    for view, groups in report["gap_closed_ndcg10"].items():
        lines += [f"### {view.replace('_', ' ').title()}", "", "| Ordering | All | First order | Repeat order |", "|---|---|---|---|"]
        for o in groups["all"]:
            cells = []
            for group in ("all", "first_order", "repeat_order"):
                g = groups.get(group, {}).get(o, {})
                cells.append(f"{g['value']:.1%} ({g['ci_low']:.1%} to {g['ci_high']:.1%})" if g.get("value") is not None else "n/a")
            lines.append(f"| {NAMES.get(o, o)} | " + " | ".join(cells) + " |")
        lines.append("")
    c = report["retrieval_ceiling"]
    lines += ["## Retrieval ceiling", "",
              f"Ranking **all** eligible products by true taste, the top {c['k']} contains a product the customer bought "
              f"for **{c['hit_rate']:.1%}** of test orders (mean recall@{c['k']} {c['recall']:.1%}). The actual candidate lists "
              f"(also up to {report['settings']['pool_size']} products) contain one for **{c['candidate_list_hit_rate']:.1%}** "
              f"(mean recall {c['candidate_list_recall']:.1%}). The gap is how much better retrieval could get with perfect "
              "knowledge of taste.", ""]
    oc = report["order_counts"]
    lines += ["## Why the order counts differ", "",
              f"- {oc['test_orders']:,} paid orders fall in the test period.",
              f"- {oc['cold_start_orders']:,} came from customers with no earlier views or purchases (cold start); they are "
              "scored with popularity candidates and empty-history feature defaults.",
              f"- {oc['orders_only_rebuying_previous_products']:,} only re-bought products the customer already had; "
              f"those products are excluded from candidates, so the end-to-end tables use {oc['orders_with_new_products']:,} orders.",
              f"- {oc['orders_with_purchase_in_list']:,} orders had a bought product in the candidate list. Ranking-only tables "
              f"need at least {report['settings']['min_pool']} candidates and at least one product that wasn't bought: "
              f"{oc['dropped_list_under_min_pool']:,} dropped for a short list and {oc['dropped_list_all_purchased']:,} because every "
              f"candidate was bought, leaving {oc['ranking_only_orders']:,}.", ""]
    if report["feature_importance"]:
        lines += ["", "## What the new ranker relies on (share of total gain)", "", "| Feature | Share |", "|---|---|"]
        lines += [f"| {f} | {v:.1%} |" for f, v in report["feature_importance"].items()]
    lines += ["", "## Settings", "", "```json", json.dumps(st, indent=2), "```"]
    (REPORT_DIR / "evaluation_report.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-content", action="store_true",
                        help="Skip the sentence-transformer source (no model download).")
    args = parser.parse_args()
    main(use_content=not args.no_content)
