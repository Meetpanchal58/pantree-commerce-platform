"""Evaluation-only oracle: the simulator's true taste (NOT deployable).

This is the ONLY module in recommendations/ or webapp/ allowed to read simulator
ground truth (generators.taste and data/_state/customer_profiles.parquet); a test
enforces it. It provides the oracle ordering, the share of achievable gain and the
retrieval ceiling used by recommendations/evaluate.py.
"""
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from generators.taste import product_demand_multiplier, true_taste_score  # noqa: E402
from recommendations.candidates.common import DATA  # noqa: E402


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


def retrieval_ceiling(queries, history, profiles, catalog, median_price, k: int = 100) -> dict:
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
