"""Tune the ALS interaction weights and hyperparameters on a validation period.

Protocol (no peeking at the test period while choosing):
  1. Validation: train ALS on data before ``t_val``; score how well each
     customer's top-K predicts the products they buy in [t_val, t_test).
     (Purchases only: in this dataset browse-only views and cart adds pick
     products at random, so they are not a preference signal to predict.)
  2. Stage 1 tunes the interaction weights (view, cart, purchase) with default
     hyperparameters; stage 2 tunes factors × regularisation with the best weights.
  3. Test: retrain the default and the best setting on data before ``t_test``
     and compare them once on [t_test, end).

Run: python -m recommendations.tune_als
"""
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from recommendations.candidates import collaborative  # noqa: E402
from recommendations.candidates.common import DATA, read_realized_transactions  # noqa: E402

K = 20
REPORT_DIR = Path(__file__).with_name("reports")
WEIGHT_GRID = [(1, 3, 5), (1, 1, 1), (1, 5, 10), (0.5, 3, 8), (0.2, 1, 10), (0.1, 0.5, 20), (0.0, 0.0, 1.0)]
FACTOR_GRID = [16, 32, 48, 64, 128]
REG_GRID = [0.01, 0.1, 1.0]
DEFAULT = {"factors": 48, "regularization": 0.1, "iterations": 15, "weights": (1, 3, 5)}


def targets(start, end) -> dict[str, set]:
    """Products each customer bought in [start, end)."""
    orders = read_realized_transactions()
    orders = orders[orders["order_timestamp"].between(start, end, inclusive="left")]
    out: dict[str, set] = {}
    for cid, pid in orders[["customer_id", "product_id"]].dropna().itertuples(index=False, name=None):
        out.setdefault(cid, set()).add(pid)
    return out


def score(params: dict, cutoff, truth: dict[str, set]) -> dict:
    fitted = collaborative._fit_model(
        cutoff.isoformat(), params["factors"], params["regularization"], params["iterations"], tuple(params["weights"])
    )
    model, matrix, customer_ids, products = fitted
    recalls, ndcgs, covered = [], [], 0
    discounts = 1 / np.log2(np.arange(2, K + 2))
    for cid, relevant in truth.items():
        if cid not in customer_ids:
            continue
        covered += 1
        row = customer_ids[cid]
        idx, _ = model.recommend(row, matrix[row], N=K, filter_already_liked_items=False)
        hits = np.array([products[i] in relevant for i in idx], dtype=float)
        recalls.append(hits.sum() / min(len(relevant), K))
        ideal = discounts[: min(len(relevant), K)].sum()
        ndcgs.append(float((hits * discounts[: len(hits)]).sum() / ideal))
    return {"customers": covered, f"recall@{K}": float(np.mean(recalls)) if recalls else 0.0,
            f"ndcg@{K}": float(np.mean(ndcgs)) if ndcgs else 0.0}


def main() -> dict:
    times = read_realized_transactions()["order_timestamp"]
    t_val, t_test, t_end = times.quantile(0.6), times.quantile(0.8), times.max()
    val_truth, test_truth = targets(t_val, t_test), targets(t_test, t_end + pd.Timedelta(seconds=1))
    print(f"Validation {t_val:%Y-%m-%d}..{t_test:%Y-%m-%d}: {len(val_truth):,} customers; "
          f"test {t_test:%Y-%m-%d}..{t_end:%Y-%m-%d}: {len(test_truth):,} customers")

    trials = []
    for weights in WEIGHT_GRID:
        params = {**DEFAULT, "weights": weights}
        res = score(params, t_val, val_truth)
        trials.append({"stage": "weights", **params, **res})
        print(f"weights={weights}: recall@{K}={res[f'recall@{K}']:.4f} ndcg@{K}={res[f'ndcg@{K}']:.4f}")
    best_weights = max((t for t in trials if t["stage"] == "weights"), key=lambda t: t[f"ndcg@{K}"])["weights"]
    for factors in FACTOR_GRID:
        for reg in REG_GRID:
            params = {**DEFAULT, "weights": best_weights, "factors": factors, "regularization": reg}
            res = score(params, t_val, val_truth)
            trials.append({"stage": "hyperparameters", **params, **res})
            print(f"factors={factors} reg={reg}: recall@{K}={res[f'recall@{K}']:.4f} ndcg@{K}={res[f'ndcg@{K}']:.4f}")
    best = max(trials, key=lambda t: t[f"ndcg@{K}"])
    best_params = {k: best[k] for k in ("factors", "regularization", "iterations", "weights")}

    test_default = score(DEFAULT, t_test, test_truth)
    test_best = score(best_params, t_test, test_truth)
    print(f"TEST default {DEFAULT}: {test_default}")
    print(f"TEST best    {best_params}: {test_best}")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "k": K, "validation_window": [str(t_val), str(t_test)], "test_window": [str(t_test), str(t_end)],
        "trials": [{**t, "weights": list(t["weights"])} for t in trials],
        "best_on_validation": {**best_params, "weights": list(best_params["weights"])},
        "test": {"default": test_default, "best": test_best},
    }
    REPORT_DIR.mkdir(exist_ok=True)
    (REPORT_DIR / "als_tuning.json").write_text(json.dumps(report, indent=2))
    lines = ["# ALS tuning report", "", f"Generated {report['generated_at']} by `python -m recommendations.tune_als`.", "",
             f"Metric: how well each customer's top {K} ALS products predict what they buy in the next period.", "",
             "## Validation grid", "", f"| Stage | Weights (view, cart, buy) | Factors | Reg | Recall@{K} | NDCG@{K} |", "|---|---|---|---|---|---|"]
    for t in trials:
        lines.append(f"| {t['stage']} | {tuple(t['weights'])} | {t['factors']} | {t['regularization']} | {t[f'recall@{K}']:.4f} | {t[f'ndcg@{K}']:.4f} |")
    lines += ["", "## Held-out test (checked once)", "", f"| Setting | Customers | Recall@{K} | NDCG@{K} |", "|---|---|---|---|",
              f"| Default {DEFAULT['factors']} factors, reg {DEFAULT['regularization']}, weights {DEFAULT['weights']} | {test_default['customers']:,} | {test_default[f'recall@{K}']:.4f} | {test_default[f'ndcg@{K}']:.4f} |",
              f"| Tuned {best_params['factors']} factors, reg {best_params['regularization']}, weights {tuple(best_params['weights'])} | {test_best['customers']:,} | {test_best[f'recall@{K}']:.4f} | {test_best[f'ndcg@{K}']:.4f} |"]
    (REPORT_DIR / "als_tuning.md").write_text("\n".join(lines) + "\n")
    return report


if __name__ == "__main__":
    main()
