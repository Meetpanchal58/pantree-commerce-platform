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
from generators.taste import product_demand_multiplier, true_taste_score  # noqa: E402
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


# --------------------------------------------------------------------------- oracle / ceilings
def load_taste_inputs() -> tuple[pd.DataFrame, pd.DataFrame, float]:
    """Hidden customer profiles, catalogue columns the taste function needs, catalogue median price."""
    profiles = pd.read_parquet(DATA / "_state" / "customer_profiles.parquet").set_index("customer_id")
    catalog = pd.read_parquet(
        DATA / "product_master.parquet",
        columns=["product_id", "h1_category", "h3_category", "h4_colour", "usage", "avg_rating",
                 "current_price", "launch_date", "discontinue_date"],
    ).drop_duplicates("product_id").set_index("product_id", drop=False)
    return profiles, catalog, float(catalog["current_price"].median())


def oracle_scores(frame: pd.DataFrame, profiles, catalog, median_price) -> np.ndarray:
    """True simulator taste of every candidate at its query time (not deployable).

    Uses generators.taste.true_taste_score, the same function the simulator draws
    purchases from. Two deliberate choices:
    * The simulator also multiplies by a small popularity boost (1 + 0.08 x prior
      purchase count, capped at 5). It is left out: the simulator accumulates those
      counts in customer-iteration order rather than in time order, so it is not a
      well-defined function of the query time, and it is popularity rather than taste.
    * The price term uses the catalogue median price as the reference. The simulator
      uses the median of a random 320-product candidate sample, which can't be
      reproduced per query.
    The oracle is therefore a ceiling on *expected* rank: purchases are random draws
    from these scores (and basket add-ons come from complement rules), so it is not
    a perfect predictor.
    """
    out = np.zeros(len(frame))
    positions = {idx: i for i, idx in enumerate(frame.index)}
    for _, group in frame.groupby("session_id", sort=False):
        cid = group["customer_id"].iloc[0]
        if cid not in profiles.index:
            continue
        scores = true_taste_score(
            profiles.loc[cid], catalog.loc[group["product_id"]], group["event_timestamp"].iloc[0],
            median_price=median_price,
        )
        out[[positions[i] for i in group.index]] = scores
    return out


def gap_closed_bootstrap(model, random, oracle, iterations: int = 2000, seed: int = 7) -> dict:
    """(model - random) / (oracle - random) on mean NDCG, with a paired bootstrap over orders.

    Same seed and iteration count as paired_bootstrap; orders are resampled
    together for all three orderings.
    """
    model, random, oracle = (np.asarray(x, dtype=float) for x in (model, random, oracle))
    n = len(model)
    if n == 0:
        return {"value": None, "ci_low": None, "ci_high": None}

    def ratio(m, r, o):
        gap = o - r
        return np.where(gap != 0, (m - r) / np.where(gap != 0, gap, 1), np.nan)

    point = float(ratio(model.mean(), random.mean(), oracle.mean()))
    idx = np.random.default_rng(seed).integers(0, n, size=(iterations, n))
    samples = ratio(model[idx].mean(axis=1), random[idx].mean(axis=1), oracle[idx].mean(axis=1))
    samples = samples[~np.isnan(samples)]
    return {
        "value": point,
        "ci_low": float(np.quantile(samples, 0.025)) if len(samples) else None,
        "ci_high": float(np.quantile(samples, 0.975)) if len(samples) else None,
        "oracle_minus_random_ndcg10": float(oracle.mean() - random.mean()),
    }


def retrieval_ceiling(queries, history: "History", profiles, catalog, median_price, k: int = 100) -> dict:
    """Rank ALL eligible products by true taste; how often is a bought product in the top k?

    Eligible = launched and not discontinued at the query time, and not bought by
    the customer before (the same exclusion as the candidate lists). The
    denominator matches candidate-list coverage: every test order.
    """
    ids = catalog.index.to_numpy()
    launch = pd.to_datetime(catalog["launch_date"]).to_numpy()
    discontinue = pd.to_datetime(catalog["discontinue_date"]).to_numpy()
    demand_by_month = {
        m: np.array([product_demand_multiplier(row, pd.Timestamp(2025, m, 15))
                     for row in catalog.itertuples(index=False)])
        for m in range(1, 13)
    }
    position = {pid: i for i, pid in enumerate(ids)}
    hits, recalls = 0, []
    for q in queries.itertuples(index=False):
        if q.customer_id not in profiles.index:
            continue
        ts = q.query_time
        scores = true_taste_score(
            profiles.loc[q.customer_id], catalog, ts, median_price=median_price,
            demand=demand_by_month[ts.month],
        )
        eligible = (launch <= np.datetime64(ts)) & (discontinue >= np.datetime64(ts))
        bought_before = history.bought_before(q.customer_id, ts)
        for pid in bought_before:
            if pid in position:
                eligible[position[pid]] = False
        scores = np.where(eligible, scores, -np.inf)
        top = set(ids[np.argpartition(-scores, k)[:k]])
        new_purchases = q.purchased - bought_before
        found = len(new_purchases & top)
        hits += int(found > 0)
        if new_purchases:
            recalls.append(found / min(len(new_purchases), k))
    n = len(queries)
    return {
        "k": k,
        "orders": int(n),
        "hit_rate": hits / n if n else None,
        "recall": float(np.mean(recalls)) if recalls else None,
    }


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
        for other in ("retrieval", "popularity", "legacy_ranker", "random"):
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
    order_counts = {
        "test_orders": int(len(queries_c)),
        "orders_with_new_products": int(test_all["session_id"].nunique()),
        "orders_only_rebuying_previous_products": int(len(queries_c) - test_all["session_id"].nunique()),
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
    "retrieval": "Retrieval score (no ranker)", "legacy_ranker": "Old ranker (3 features)",
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
              f"- {oc['orders_only_rebuying_previous_products']:,} of them only re-bought products the customer already had; "
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
    parser.add_argument("--sample-limit", type=int, default=400000,
                        help="Approximate number of click events to sample; 0 uses all events.")
    parser.add_argument("--no-content", action="store_true",
                        help="Skip the sentence-transformer source (no model download).")
    args = parser.parse_args()
    main(args.sample_limit, use_content=not args.no_content)
