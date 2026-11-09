import json
import logging
from functools import lru_cache
from pathlib import Path
from threading import Lock

import pandas as pd

from .common import DATA, read_realized_transactions


logger = logging.getLogger(__name__)
# Single source of truth for ALS settings: the parameters tune_als.py chose on the
# validation period (recommendations/reports/als_tuning.json, "best_on_validation").
# Serving and evaluation both resolve them through als_params().
ALS_TUNING_PATH = Path(__file__).resolve().parents[1] / "reports" / "als_tuning.json"
FALLBACK_ALS_PARAMS = {"factors": 32, "regularization": 0.01, "iterations": 15, "weights": (0.5, 3.0, 8.0)}
_WARM_LOCK = Lock()
_MODEL_READY = False


@lru_cache(maxsize=1)
def als_params() -> dict:
    """ALS factors, regularisation, iterations and (view, cart, purchase) weights."""
    try:
        best = json.loads(ALS_TUNING_PATH.read_text())["best_on_validation"]
        return {
            "factors": int(best["factors"]),
            "regularization": float(best["regularization"]),
            "iterations": int(best["iterations"]),
            "weights": tuple(float(w) for w in best["weights"]),
        }
    except (OSError, KeyError, ValueError, TypeError) as exc:
        logger.warning("Using fallback ALS params; could not read %s (%s)", ALS_TUNING_PATH, exc)
        return dict(FALLBACK_ALS_PARAMS)


def build_interactions(events, orders, weights, as_of=None) -> pd.Series:
    """(customer, product) -> implicit-feedback weight for ALS.

    Views and cart adds come from the clickstream; purchases come only from order
    lines (quantity x purchase weight, plus a small bonus for same-day browsing).
    The clickstream "purchase" event is ignored so a purchase is not counted twice.
    """
    view_w, cart_w, purchase_w = weights
    events = events.copy()
    events["event_timestamp"] = pd.to_datetime(events["event_timestamp"], errors="coerce")
    if as_of is not None:
        events = events[events["event_timestamp"] < pd.Timestamp(as_of)]
    browse_weights = {"pdp_view": view_w, "add_to_cart": cart_w}
    events = events[events["event_type"].isin(browse_weights)].dropna(
        subset=["customer_id", "product_id", "event_timestamp"]
    )
    events["weight"] = events["event_type"].map(browse_weights)
    interactions = events.groupby(["customer_id", "product_id"])["weight"].sum()

    orders = orders.copy()
    if as_of is not None:
        orders = orders[orders["order_timestamp"] < pd.Timestamp(as_of)]
    events["event_day"] = events["event_timestamp"].dt.normalize()
    orders["order_day"] = pd.to_datetime(orders["order_timestamp"]).dt.normalize()
    event_days = events[["customer_id", "event_day"]].drop_duplicates()
    # Softly associate order lines with same-customer, same-day browsing; no order_id FK.
    same_day_orders = orders.merge(
        event_days,
        left_on=["customer_id", "order_day"],
        right_on=["customer_id", "event_day"],
        how="inner",
    )
    purchases = orders.groupby(["customer_id", "product_id"])["quantity"].sum() * purchase_w
    same_day_bonus = (
        same_day_orders.groupby(["customer_id", "product_id"])["quantity"].sum() * 0.5
    )
    purchases = purchases.add(same_day_bonus, fill_value=0)
    return interactions.add(purchases, fill_value=0)


@lru_cache(maxsize=8)
def _fit_model(
    as_of: str | None = None,
    factors: int | None = None,
    regularization: float | None = None,
    iterations: int | None = None,
    weights: tuple[float, float, float] | None = None,
):
    """Fit ALS once per (cut-off, settings) and retain the sparse training matrix.

    Interactions: product views and cart adds from the clickstream, plus purchases
    from order lines (quantity-aware). Clickstream "purchase" events are NOT used,
    so a purchase isn't counted twice. Unspecified settings come from als_params().
    """
    defaults = als_params()
    factors = factors or defaults["factors"]
    regularization = defaults["regularization"] if regularization is None else regularization
    iterations = iterations or defaults["iterations"]
    view_w, cart_w, purchase_w = weights or defaults["weights"]
    try:
        import implicit
        from scipy.sparse import coo_matrix
    except ImportError:
        logger.warning("ALS recommendations disabled; install dependencies from requirements.txt")
        return None

    events = pd.read_parquet(
        DATA / "clickstream.parquet",
        columns=["customer_id", "product_id", "event_type", "event_timestamp"],
    )
    interactions = build_interactions(
        events, read_realized_transactions(), (view_w, cart_w, purchase_w), as_of
    )

    customers = interactions.index.get_level_values("customer_id").unique()
    products = interactions.index.get_level_values("product_id").unique()
    customer_ids = {value: index for index, value in enumerate(customers)}
    product_ids = {value: index for index, value in enumerate(products)}
    rows = interactions.index.get_level_values("customer_id").map(customer_ids)
    columns = interactions.index.get_level_values("product_id").map(product_ids)
    matrix = coo_matrix(
        (interactions.to_numpy(dtype="float32"), (rows, columns)),
        shape=(len(customers), len(products)),
    ).tocsr()

    from threadpoolctl import threadpool_limits

    with threadpool_limits(limits=1, user_api="blas"):
        model = implicit.als.AlternatingLeastSquares(
            factors=factors, regularization=regularization, iterations=iterations, random_state=17,
            num_threads=1,
        )
        model.fit(matrix, show_progress=False)
    return model, matrix, customer_ids, products


def get_collaborative(
    customer_id: str,
    limit: int = 100,
    as_of: pd.Timestamp | None = None,
    params: dict | None = None,
) -> list[dict]:
    """Retrieve ALS recommendations for a known customer."""
    if as_of is None and not _MODEL_READY:
        return []
    params = params or {}
    fitted = _fit_model(
        as_of.isoformat() if as_of is not None else None,
        params.get("factors"),
        params.get("regularization"),
        params.get("iterations"),
        tuple(params["weights"]) if params.get("weights") else None,
    )
    if fitted is None:
        return []
    model, matrix, customer_ids, products = fitted
    if customer_id not in customer_ids:
        return []
    user_index = customer_ids[customer_id]
    indices, scores = model.recommend(user_index, matrix[user_index], N=limit)
    maximum = float(max(scores)) if len(scores) else 0.0
    return [
        {
            "product_id": products[index],
            "score": float(score / maximum) if maximum else 0.0,
            "source": "collaborative",
            "reason": "Based on your browsing and purchases",
        }
        for index, score in zip(indices, scores)
    ]


def warm_model() -> None:
    """Train/cache the serving model outside recommendation requests."""
    global _MODEL_READY
    with _WARM_LOCK:
        if _MODEL_READY:
            return
        _fit_model()
        _MODEL_READY = True
