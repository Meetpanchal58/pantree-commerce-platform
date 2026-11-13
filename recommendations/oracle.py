"""Evaluation-only oracle: the simulator's true taste (NOT deployable).

This is the ONLY module in recommendations/ or webapp/ allowed to read simulator
ground truth (ecommerce_sim.taste and data/source/_ground_truth/); a test enforces it.
It provides the oracle ordering, the share of achievable gain and the retrieval ceiling
used by recommendations/evaluate.py.
"""
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ecommerce_sim.taste import TasteModel  # noqa: E402  (the simulator's own taste function)
from recommendations.candidates.common import DATA  # noqa: E402

TRUTH = DATA / "source" / "_ground_truth"


class TasteCatalog:
    """The catalogue as the simulator saw it: taste model, launch dates and daily prices."""

    def __init__(self, products: pd.DataFrame, prices: pd.DataFrame | None = None):
        products = products.drop_duplicates("product_id").reset_index(drop=True)
        self.index = pd.Index(products["product_id"])
        self.position = {pid: i for i, pid in enumerate(self.index)}
        self.model = TasteModel(products)
        self.launch = pd.to_datetime(products["launch_date"]).to_numpy()
        self.discontinue = pd.to_datetime(products["discontinue_date"]).to_numpy()
        self.prices = prices  # price_history (valid_from, valid_to, selling_price); None = list price
        self._day_prices: dict = {}

    def prices_on(self, ts) -> np.ndarray | None:
        """Selling price of every product on the day of `ts` (NaN = list price)."""
        if self.prices is None:
            return None
        day = pd.Timestamp(ts).normalize()
        if day not in self._day_prices:
            ph = self.prices
            live = ph[(ph.valid_from <= day) & (ph.valid_to.isna() | (ph.valid_to >= day))]
            live = live.drop_duplicates("product_id", keep="last").set_index("product_id").selling_price
            self._day_prices[day] = live.reindex(self.index).to_numpy(dtype=float)
        return self._day_prices[day]

    def scores(self, profile: dict, positions: np.ndarray, ts) -> np.ndarray:
        prices = self.prices_on(ts)
        prices = None if prices is None else np.where(np.isnan(prices[positions]), self.model.mrp[positions],
                                                       prices[positions])
        return self.model.score(profile, positions, pd.Timestamp(ts).month, prices=prices)


def load_taste_inputs() -> tuple[pd.DataFrame, TasteCatalog, float]:
    """Hidden customer taste, the catalogue as the simulator scored it, catalogue median list price."""
    profiles = pd.read_parquet(TRUTH / "customer_taste.parquet").dropna(subset=["customer_id"])
    profiles = profiles.drop_duplicates("customer_id").set_index("customer_id")
    quality = pd.read_parquet(TRUTH / "product_quality.parquet")
    products = pd.read_parquet(DATA / "source" / "product_master.parquet").merge(quality, on="product_id")
    prices = pd.read_parquet(DATA / "source" / "price_history.parquet",
                             columns=["product_id", "valid_from", "valid_to", "selling_price"])
    for column in ("valid_from", "valid_to"):
        prices[column] = pd.to_datetime(prices[column])
    catalog = TasteCatalog(products, prices)
    return profiles, catalog, catalog.model.median_price


def oracle_scores(frame: pd.DataFrame, profiles, catalog: TasteCatalog, median_price=None) -> np.ndarray:
    """True simulator taste of every candidate at its query time (not deployable).

    Uses ecommerce_sim.taste.TasteModel with the day's real selling prices: the same score
    the simulated shoppers click and buy in proportion to. It is a ceiling on *expected*
    rank, not a perfect predictor: purchases are random draws from these scores, and
    position on the page, stock and complements also play a part.
    """
    out = np.zeros(len(frame))
    positions = {idx: i for i, idx in enumerate(frame.index)}
    for _, group in frame.groupby("session_id", sort=False):
        cid = group["customer_id"].iloc[0]
        if cid not in profiles.index:
            continue
        known = group[group["product_id"].isin(catalog.position)]
        if known.empty:
            continue
        product_positions = np.array([catalog.position[pid] for pid in known["product_id"]])
        scores = catalog.scores(profiles.loc[cid].to_dict(), product_positions, group["event_timestamp"].iloc[0])
        out[[positions[i] for i in known.index]] = scores
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
    launch, discontinue = catalog.launch, catalog.discontinue
    everything = np.arange(len(ids))
    position = catalog.position
    hits, recalls = 0, []
    for q in queries.itertuples(index=False):
        if q.customer_id not in profiles.index:
            continue
        ts = q.query_time
        scores = catalog.scores(profiles.loc[q.customer_id].to_dict(), everything, ts)
        eligible = (launch <= np.datetime64(ts)) & ~(discontinue < np.datetime64(ts))
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
