"""Chronological offline evaluation of Pantree recommendations.

Time is split into three periods so nothing is learned from the future:

    A ─────────────── t_rank ─── B ─── t_test ─── C ─── end
    retrieval models   ranker trained on   everything is
    learn from here    candidate lists     scored here

* Retrieval sources (ALS, co-purchases, trending, recent activity) only use
  data before each period's cut-off (with a 7-day gap so labels never overlap).
* Each query is a real order: "given everything before this order, does the
  system rank the products the customer actually bought?" (the job of the
  home-page "Recommended for you" row). The query time is one hour before the
  order, so the visit that led to the order is not visible.
* The ranker is trained on candidate lists for period-B orders and scored on
  period-C orders.
* Every ordering is compared on the *same* candidate lists, so differences come
  from ranking alone: random, popularity, retrieval score (production without a
  ranker), the legacy 3-feature ranker, the new ranker, and the production blend.

Results are printed and written to ``recommendations/reports/``.
"""
import argparse
import json
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from recommendations.candidates.common import (  # noqa: E402
    DATA,
    gender_is_compatible,
    read_products,
    read_realized_transactions,
)
from recommendations.ranker import (  # noqa: E402
    FEATURES,
    LEGACY_FEATURES,
    MODEL_PATH,
    build_training_frame,
    candidate_features,
    customer_profile,
    train_ranker,
)

REPORT_DIR = Path(__file__).with_name("reports")
EMBARGO = pd.Timedelta(days=7)
POOL_PER_SOURCE = 20
POOL_SIZE = 100
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
        self.views = {
            cid: (g["event_timestamp"].to_numpy(), g["product_id"].to_numpy())
            for cid, g in activity.groupby("customer_id", sort=False)
        }
        tx = transactions.sort_values("order_timestamp")
        self.buys = {
            cid: (g["order_timestamp"].to_numpy(), g["product_id"].to_numpy())
            for cid, g in tx.groupby("customer_id", sort=False)
        }
        self.future = defaultdict(list)
        for cid, pid, ts in tx[["customer_id", "product_id", "order_timestamp"]].itertuples(index=False, name=None):
            self.future[(cid, pid)].append(ts)

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

    def is_positive(self, cid, pid, ts) -> bool:
        """Bought the same day, after the query, within seven days."""
        times = self.future.get((cid, pid), [])
        i = bisect_right(times, ts)
        return i < len(times) and times[i] <= ts + EMBARGO and times[i].normalize() == ts.normalize()


class Retrieval:
    """Candidate sources that only use information before ``cutoff``."""

    def __init__(self, cutoff, activity, transactions, products, use_content=True):
        from recommendations.candidates import collaborative

        self.cutoff = cutoff
        self.products = products
        self.collaborative = collaborative
        past = activity[activity["event_timestamp"] < cutoff]
        self.popularity = past.groupby("product_id").size().sort_values(ascending=False, kind="mergesort")
        top = self.popularity.head(100)
        peak = float(top.max()) if len(top) else 1.0
        self.trending = [
            {"product_id": pid, "score": float(n / peak), "source": "trending"} for pid, n in top.items()
        ]
        prior = transactions[transactions["order_timestamp"] < cutoff]
        self.co_purchases = defaultdict(Counter)
        for pids in prior.groupby("order_id")["product_id"].unique():
            for a, b in combinations(sorted(pids), 2):
                self.co_purchases[a][b] += 1
                self.co_purchases[b][a] += 1
        self.by_type = {cat: g.index.to_numpy() for cat, g in products.groupby("h3_category")}
        self.use_content = use_content
        if use_content:
            from recommendations.candidates.content import warm_index

            warm_index()
        self._als, self._content = {}, {}

    def als(self, cid):
        if cid not in self._als:
            self._als[cid] = self.collaborative.get_collaborative(cid, limit=100, as_of=self.cutoff)
        return self._als[cid]

    def content(self, pid):
        if not self.use_content:
            return []
        if pid not in self._content:
            from recommendations.candidates.content import get_similar_products

            self._content[pid] = get_similar_products(pid, limit=50)
        return self._content[pid]

    def fbt(self, pid):
        pairs = self.co_purchases.get(pid, Counter())
        peak = max(pairs.values(), default=0)
        return [
            {"product_id": p, "score": n / peak if peak else 0.0, "source": "fbt"}
            for p, n in pairs.most_common(50)
        ]

    def recent_activity(self, seeds):
        """Same product type and audience as the customer's recent items (mirrors serving)."""
        out = {}
        peak = float(self.popularity.max()) if len(self.popularity) else 1.0
        for seed in seeds[:5]:
            if seed not in self.products.index:
                continue
            seed_row = self.products.loc[seed]
            pool = [p for p in self.by_type.get(seed_row["h3_category"], []) if p != seed]
            pool = [
                p for p in pool
                if gender_is_compatible(seed_row["gender"], self.products.at[p, "gender"])
            ]
            scored = sorted(pool, key=lambda p: (-self.popularity.get(p, 0), p))[:20]
            for p in scored:
                score = 0.80 + 0.10 * self.popularity.get(p, 0) / peak
                if score > out.get(p, {"score": -1})["score"]:
                    out[p] = {"product_id": p, "score": score, "source": "recent_activity"}
        return sorted(out.values(), key=lambda c: (-c["score"], c["product_id"]))


def build_pools(queries, retrieval: Retrieval, history: History) -> tuple[pd.DataFrame, dict]:
    """Candidate list per order + per-source hit statistics.

    ``queries`` has one row per order: customer_id, query_time, purchased (set).
    """
    rows = []
    source_hits = Counter()
    pool_hits = 0
    for q in queries.itertuples(index=False):
        cid, ts, purchased = q.customer_id, q.query_time, q.purchased
        bought = history.bought_list(cid, ts)
        seen = history.viewed_before(cid, ts)
        anchor = bought[0] if bought else (seen[0] if seen else None)
        sources = {
            "collaborative": retrieval.als(cid),
            "content": retrieval.content(anchor) if anchor else [],
            "fbt": retrieval.fbt(anchor) if anchor else [],
            "trending": retrieval.trending,
            "recent_activity": retrieval.recent_activity(bought[:3] + seen[:2]),
        }
        excluded = set(bought)
        best, origin = {}, defaultdict(set)
        for name, items in sources.items():
            if any(c["product_id"] in purchased for c in items if c["product_id"] not in excluded):
                source_hits[name] += 1
            for c in items:
                pid = c["product_id"]
                if pid in excluded:
                    continue
                origin[pid].add(name)
                if c["score"] > best.get(pid, {"score": -1})["score"]:
                    best[pid] = {**c, "source": name}
        selected = {}
        for name, items in sources.items():
            taken = 0
            for c in items:
                pid = c["product_id"]
                if pid in best and pid not in selected:
                    selected[pid] = best[pid]
                    taken += 1
                if taken == POOL_PER_SOURCE or len(selected) == POOL_SIZE:
                    break
        for c in sorted(best.values(), key=lambda c: (-c["score"], c["product_id"])):
            if len(selected) >= POOL_SIZE:
                break
            selected.setdefault(c["product_id"], c)
        hit = False
        for pid, c in selected.items():
            label = int(pid in purchased)
            hit = hit or bool(label)
            rows.append({
                "session_id": q.order_id, "customer_id": cid, "anchor": anchor,
                "event_timestamp": ts, "product_id": pid, "source": c["source"],
                "retrieval_score": float(c["score"]), "score": float(c["score"]),
                "n_sources": len(origin[pid]), "label": label,
                "customer_event_count": q.customer_event_count, "surface_id": 0,
                "n_purchased": len(purchased - excluded),
            })
        pool_hits += int(hit)
    stats = {
        "queries": int(len(queries)),
        "pool_contains_purchase": int(pool_hits),
        "source_hits": {k: int(v) for k, v in source_hits.items()},
    }
    return pd.DataFrame(rows), stats


def order_queries(transactions, activity, start, end) -> pd.DataFrame:
    """One query per paid order in [start, end): what did the customer buy?"""
    window = transactions[transactions["order_timestamp"].between(start, end)]
    grouped = window.groupby("order_id").agg(
        customer_id=("customer_id", "first"),
        order_timestamp=("order_timestamp", "min"),
        purchased=("product_id", lambda s: set(s)),
    ).reset_index()
    grouped["query_time"] = grouped["order_timestamp"] - QUERY_OFFSET
    counts = {cid: g["event_timestamp"].to_numpy() for cid, g in activity.groupby("customer_id", sort=False)}
    grouped["customer_event_count"] = [
        bisect_left(counts.get(cid, np.array([], dtype="datetime64[ns]")), np.datetime64(ts))
        for cid, ts in zip(grouped["customer_id"], grouped["query_time"])
    ]
    return grouped.sort_values("query_time").reset_index(drop=True)


def add_features(pools, retrieval: Retrieval, history: History, products) -> pd.DataFrame:
    parts = []
    for (_, cid, anchor, ts), group in pools.groupby(
        ["session_id", "customer_id", "anchor", "event_timestamp"], sort=False
    ):
        profile = customer_profile(
            history.profile_history(cid, ts), products, purchase_count=len(history.bought_list(cid, ts))
        )
        parts.append(candidate_features(group, products, profile, retrieval.popularity, anchor))
    out = pd.concat(parts, ignore_index=True)
    out["product_event_count"] = out["product_id"].map(retrieval.popularity).fillna(0)
    return out


def rankable(frame: pd.DataFrame) -> pd.DataFrame:
    stats = frame.groupby("session_id")["label"].agg(["size", "nunique"])
    keep = stats[(stats["size"] >= MIN_POOL) & (stats["nunique"] > 1)].index
    return frame[frame["session_id"].isin(keep)].copy()


# --------------------------------------------------------------------------- main
def main(sample_limit: int = 400000, use_content: bool = True) -> dict:
    products = read_products()
    transactions = read_realized_transactions()
    activity = load_activity()
    history = History(activity, transactions)

    frame = build_training_frame(sample_limit=sample_limit)
    times = transactions["order_timestamp"]
    t_test = times.quantile(0.8)
    t_rank = times.quantile(0.6)
    t_end = times.max()
    queries_b = order_queries(transactions, activity, t_rank, t_test - EMBARGO)
    queries_c = order_queries(transactions, activity, t_test, t_end)
    print(f"Periods: ranker orders {t_rank:%Y-%m-%d}..{t_test - EMBARGO:%Y-%m-%d} "
          f"({len(queries_b):,}), test orders {t_test:%Y-%m-%d}..{t_end:%Y-%m-%d} ({len(queries_c):,})")

    retrieval_b = Retrieval(t_rank - EMBARGO, activity, transactions, products, use_content)
    pools_b, _ = build_pools(queries_b, retrieval_b, history)
    train_b = add_features(pools_b, retrieval_b, history, products)

    retrieval_c = Retrieval(t_test - EMBARGO, activity, transactions, products, use_content)
    pools_c, retrieval_stats = build_pools(queries_c, retrieval_c, history)
    test_c = add_features(pools_c, retrieval_c, history, products)

    model = train_ranker(train_b, FEATURES)
    legacy_train = frame[frame["event_timestamp"] < t_rank - EMBARGO]
    legacy = train_ranker(legacy_train, LEGACY_FEATURES)

    test_all = test_c[test_c["n_purchased"] > 0].copy()
    rng = np.random.default_rng(11)
    test_all["random"] = rng.random(len(test_all))
    test_all["popularity"] = test_all["product_event_count"]
    test_all["retrieval"] = test_all["retrieval_score"]
    orderings = ["random", "popularity", "retrieval"]
    if legacy is not None:
        test_all["legacy_ranker"] = legacy.predict(np.ascontiguousarray(test_all[LEGACY_FEATURES].to_numpy(dtype=np.float32)))
        orderings.append("legacy_ranker")
    if model is not None:
        raw = model.predict(np.ascontiguousarray(test_all[FEATURES].to_numpy(dtype=np.float32)))
        test_all["new_ranker"] = raw
        # Production blend: 0.5 retrieval + 0.5 min-max ranker, per list.
        norm = test_all.assign(raw=raw).groupby("session_id")["raw"].transform(
            lambda s: (s - s.min()) / (s.max() - s.min()) if s.max() > s.min() else 0.5
        )
        test_all["production_blend"] = 0.5 * test_all["retrieval_score"] + 0.5 * norm
        orderings += ["new_ranker", "production_blend"]
    # Segment-aware policy: popularity for customers with no purchase history,
    # the production blend for everyone else.
    first_flag = test_all["log_customer_purchases"].eq(0)
    base_col = "production_blend" if "production_blend" in test_all else "retrieval"
    pop_norm = test_all.groupby("session_id")["product_event_count"].transform(
        lambda s: s / s.max() if s.max() > 0 else 0.0
    )
    test_all["segment_aware"] = np.where(first_flag, 1.0 + pop_norm, test_all[base_col])
    orderings.append("segment_aware")
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
        for other in ("retrieval", "popularity", "legacy_ranker", "random"):
            if other in orderings:
                o = scores["end_to_end"][other][10].set_index("session_id")["ndcg"].reindex(base.index)
                comparisons[f"new_ranker_vs_{other}"] = paired_bootstrap(base.to_numpy(), o.to_numpy())
    seg = scores["end_to_end"]["segment_aware"][10].set_index("session_id")["ndcg"]
    for other in ("retrieval", "popularity", "production_blend"):
        if other in orderings:
            o = scores["end_to_end"][other][10].set_index("session_id")["ndcg"].reindex(seg.index)
            comparisons[f"segment_aware_vs_{other}"] = paired_bootstrap(seg.to_numpy(), o.to_numpy())

    importance = {}
    if model is not None:
        gains = model.booster_.feature_importance(importance_type="gain")
        total = float(gains.sum()) or 1.0
        importance = {f: round(float(g) / total, 4) for f, g in sorted(zip(FEATURES, gains), key=lambda x: -x[1])}
        model.booster_.save_model(str(MODEL_PATH))

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "settings": {
            "sample_limit": sample_limit, "pool_size": POOL_SIZE, "per_source": POOL_PER_SOURCE,
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
        "feature_importance": importance,
    }
    write_report(report)
    print_report(report)
    return report


NAMES = {
    "random": "Random order", "popularity": "Popularity (most viewed)",
    "retrieval": "Retrieval score (no ranker)", "legacy_ranker": "Old ranker (3 features)",
    "new_ranker": "New ranker (11 features)", "production_blend": "Production blend (50/50)",
    "segment_aware": "Segment-aware (popular for first-timers, blend otherwise)",
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
             f"({st['per_source']} per source, then filled by score). Products the customer already bought are excluded.", ""]
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
    if report["feature_importance"]:
        lines += ["", "## What the new ranker relies on (share of total gain)", "", "| Feature | Share |", "|---|---|"]
        lines += [f"| {f} | {v:.1%} |" for f, v in report["feature_importance"].items()]
    lines += ["", "## Settings", "", "```json", json.dumps(st, indent=2), "```"]
    (REPORT_DIR / "evaluation_report.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sample-limit", type=int, default=400000,
                        help="Approximate number of click events to sample; 0 uses all events.")
    parser.add_argument("--no-content", action="store_true",
                        help="Skip the sentence-transformer source (no model download).")
    args = parser.parse_args()
    main(args.sample_limit, use_content=not args.no_content)
