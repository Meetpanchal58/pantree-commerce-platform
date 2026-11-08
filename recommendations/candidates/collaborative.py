import logging
from functools import lru_cache
from threading import Lock

import pandas as pd

from .common import DATA, read_realized_transactions


logger = logging.getLogger(__name__)
EVENT_WEIGHTS = {"pdp_view": 1.0, "add_to_cart": 3.0, "purchase": 5.0}
# Serving hyperparameters (see recommendations/tune_als.py for how they were chosen).
ALS_PARAMS = {"factors": 48, "regularization": 0.1, "iterations": 15}
_WARM_LOCK = Lock()
_MODEL_READY = False


@lru_cache(maxsize=8)
def _fit_model(
    as_of: str | None = None,
    factors: int | None = None,
    regularization: float | None = None,
    iterations: int | None = None,
    weights: tuple[float, float, float] | None = None,
):
    """Fit ALS once per (cut-off, settings) and retain the sparse training matrix.

    ``weights`` is (view, add_to_cart, purchase); defaults come from EVENT_WEIGHTS.
    """
    factors = factors or ALS_PARAMS["factors"]
    regularization = ALS_PARAMS["regularization"] if regularization is None else regularization
    iterations = iterations or ALS_PARAMS["iterations"]
    event_weights = (
        dict(zip(("pdp_view", "add_to_cart", "purchase"), weights)) if weights else EVENT_WEIGHTS
    )
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
    events["event_timestamp"] = pd.to_datetime(events["event_timestamp"], errors="coerce")
    if as_of is not None:
        events = events[events["event_timestamp"] < pd.Timestamp(as_of)]
    events = events[events["event_type"].isin(event_weights)].dropna(
        subset=["customer_id", "product_id", "event_timestamp"]
    )
    events["weight"] = events["event_type"].map(event_weights)
    interactions = events.groupby(["customer_id", "product_id"])["weight"].sum()

    orders = read_realized_transactions().copy()
    if as_of is not None:
        orders = orders[orders["order_timestamp"] < pd.Timestamp(as_of)]
    events["event_day"] = pd.to_datetime(events["event_timestamp"]).dt.normalize()
    orders["order_day"] = orders["order_timestamp"].dt.normalize()
    event_days = events[["customer_id", "event_day"]].drop_duplicates()
    # Softly associate order lines with same-customer, same-day browsing; no order_id FK.
    same_day_orders = orders.merge(
        event_days,
        left_on=["customer_id", "order_day"],
        right_on=["customer_id", "event_day"],
        how="inner",
    )
    purchases = orders.groupby(["customer_id", "product_id"])["quantity"].sum() * event_weights["purchase"]
    same_day_bonus = (
        same_day_orders.groupby(["customer_id", "product_id"])["quantity"].sum() * 0.5
    )
    purchases = purchases.add(same_day_bonus, fill_value=0)
    interactions = interactions.add(purchases, fill_value=0)

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
