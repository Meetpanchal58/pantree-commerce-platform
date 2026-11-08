"""The simulator's true preference function, importable on its own.

The generator draws purchases in proportion to ``true_taste_score`` (times a small
popularity boost, see ``simulator.product_weights``). Because the data is
synthetic this function is known exactly, so the evaluation can use it as an
"oracle" ranking: the best a model could do if it knew each customer's hidden
taste. No dependency on ``config`` so it can be imported from anywhere.
"""
import numpy as np
import pandas as pd


def product_demand_multiplier(row, ts):
    m = pd.Timestamp(ts).month
    art = str(row.h3_category)
    usage = str(row.usage)
    mult = 1.0
    if m in (4,5,6):
        if art in {"Tshirts","Tops","Dresses","Sandals","Flip Flops","Sunglasses"}: mult *= 1.35
        if art in {"Jackets","Sweaters","Sweatshirts"}: mult *= 0.75
    if m in (10,11):
        if usage == "Ethnic" or art in {"Kurtas","Kurtis","Sarees","Earrings","Handbags","Watches"}: mult *= 1.35
        if art in {"Tshirts","Track Pants"}: mult *= 1.05
    if m in (12,1,2):
        if art in {"Jackets","Sweaters","Sweatshirts","Boots"}: mult *= 1.35
    if usage == "Sports" and m in (1,2,3,4,5): mult *= 1.10
    return mult


def true_taste_score(
    profile,
    products: pd.DataFrame,
    ts,
    median_price: float | None = None,
    demand: np.ndarray | None = None,
) -> np.ndarray:
    """Hidden-taste score of each product for one customer at time ``ts``.

    rating x seasonal demand x category / colour / usage match x price-sensitivity
    term. ``profile`` needs category_focus, colour_focus, preferred_usage and
    price_sensitivity. The price term compares each price with ``median_price``;
    by default the median of ``products`` (what the simulator uses for its
    candidate set). ``demand`` optionally supplies precomputed seasonal multipliers
    for ``products`` at ``ts`` (a speed-up for scoring the whole catalogue; the
    simulator never passes it). The multiplication order matches the original
    simulator code exactly, so generated data is byte-identical.
    """
    score = np.ones(len(products), dtype=float)
    score *= np.clip((products.avg_rating.fillna(4.0).to_numpy() / 4.0) ** 1.2, .45, 1.5)
    if demand is None:
        demand = np.array([product_demand_multiplier(row, ts) for row in products.itertuples(index=False)])
    score *= demand
    score *= np.where(products.h1_category.to_numpy() == profile.category_focus, 2.8, .55)
    score *= np.where(products.h4_colour.to_numpy() == profile.colour_focus, 1.6, .85)
    score *= np.where(products.usage.to_numpy() == profile.preferred_usage, 1.45, .85)
    price = products.current_price.to_numpy(dtype=float)
    if median_price is None:
        median_price = float(np.median(price)) if len(price) else 1.0
    ps = float(profile.price_sensitivity)
    score *= np.exp(-ps * np.clip((price / max(median_price, 1.0)) - 1, -.8, 4))
    return score
