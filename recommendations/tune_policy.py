"""Choose the personalised policy's settings on the validation period.

Settings searched (and nothing else):

* trending window (days) for the trending source,
* per-source quotas (ALS 20/40/60/80, the rest of the pool split evenly) and pool size,
* the ranker's weight in the blend with the retrieval score,
* whether first-time buyers get popularity order.

Periods (all before the test period, which is never touched here):

    t_rank ── fit ── t_val ── validation ── t_test - 7 days │ test (evaluate.py only)

Retrieval models (ALS, co-purchase) use data before ``t_rank - 7 days``; trending,
recent activity, popularity and stock are taken at each query time, as in
evaluation. For each setting the ranker is trained on candidate lists for "fit"
orders and the served policy is scored on "validation" orders (end-to-end
NDCG@10). The search is staged: trending window first, then quotas x pool size,
then blend weight x first-timer rule (these two only re-order the same lists).
The winner is written to ``reports/policy_config.json``, which serving and
``evaluate.py`` read.
"""
import json
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from recommendations import evaluate as ev  # noqa: E402
from recommendations.candidates import popularity  # noqa: E402
from recommendations.candidates.common import read_products, read_realized_transactions  # noqa: E402
from recommendations.policy import DEFAULT_CONFIG, POLICY_CONFIG_PATH, PolicyConfig, Sources  # noqa: E402
from recommendations.ranker import FEATURES, train_ranker  # noqa: E402

REPORT_DIR = Path(__file__).with_name("reports")
VALIDATION_QUANTILE = 0.7
TRENDING_WINDOWS = (7, 14, 30, 60)
ALS_QUOTAS = (20, 40, 60, 80)
POOL_SIZES = (50, 100, 150)
BLEND_WEIGHTS = (0.0, 0.25, 0.5, 0.75, 1.0)
FIRST_TIMER_RULES = (True, False)


def with_trending(cached, window_days):
    """Same cached source lists with the trending source recomputed for a window."""
    out = []
    for q, state, lists in cached:
        lists = dict(lists)
        lists["trending"] = list(popularity.trending(q.query_time, window_days=window_days))
        out.append((q, state, lists))
    return out


class Evaluator:
    def __init__(self, use_content=True):
        self.products = read_products()
        transactions = read_realized_transactions()
        activity = ev.load_activity()
        self.history = ev.History(activity, transactions)
        times = transactions["order_timestamp"]
        t_rank, t_test = times.quantile(0.6), times.quantile(0.8)
        t_val = times.quantile(VALIDATION_QUANTILE)
        self.periods = {
            "retrieval_cutoff": str(t_rank - ev.EMBARGO),
            "fit": [str(t_rank), str(t_val - ev.EMBARGO)],
            "validation": [str(t_val), str(t_test - ev.EMBARGO)],
            "test_starts": str(t_test),
        }
        self.q_fit = ev.order_queries(transactions, t_rank, t_val - ev.EMBARGO)
        self.q_val = ev.order_queries(transactions, t_val, t_test - ev.EMBARGO)
        if use_content:
            from recommendations.candidates.content import warm_index

            warm_index()
        self.sources = Sources(model_as_of=t_rank - ev.EMBARGO, use_content=use_content)
        self.base_fit = ev.query_lists(self.q_fit, self.sources, self.history)
        self.base_val = ev.query_lists(self.q_val, self.sources, self.history)
        print(f"fit orders {len(self.q_fit):,}, validation orders {len(self.q_val):,}")
        self._lists = {}

    def lists(self, window):
        if window not in self._lists:
            self._lists[window] = (with_trending(self.base_fit, window), with_trending(self.base_val, window))
        return self._lists[window]

    def fit(self, config: PolicyConfig):
        """Candidate lists + ranker for one retrieval setting; returns scored validation frame."""
        fit_lists, val_lists = self.lists(config.trending_window_days)
        pools_fit, _ = ev.build_pools(self.q_fit, self.sources, self.history, config, fit_lists)
        pools_val, stats = ev.build_pools(self.q_val, self.sources, self.history, config, val_lists)
        model = train_ranker(ev.add_features(pools_fit, self.sources, self.history, self.products), FEATURES)
        val = ev.add_features(pools_val, self.sources, self.history, self.products)
        return val[val["n_purchased"] > 0], model, stats

    def score(self, val, model, config):
        per_order = ev.served_ndcg(ev.policy_scores(val, model, config))
        return per_order.reindex(self.all_val_orders()).fillna(0.0)

    def all_val_orders(self):
        if not hasattr(self, "_orders"):
            self._orders = sorted(
                q.order_id for q, state, _ in self.base_val if q.purchased - set(state.bought)
            )
        return self._orders


def row(stage, config, ndcg, stats=None):
    out = {"stage": stage, "config": config.to_json(), "val_ndcg10": float(ndcg.mean())}
    if stats:
        out["val_pool_recall"] = stats["pool_recall"]
        out["val_pool_contains_purchase"] = stats["pool_contains_purchase"] / max(stats["queries"], 1)
    return out


def main(use_content: bool = True) -> dict:
    e = Evaluator(use_content)
    rows, per_order = [], {}

    def run(stage, config, val=None, model=None, stats=None):
        if val is None:
            val, model, stats = e.fit(config)
        ndcg = e.score(val, model, config)
        per_order[config] = ndcg
        rows.append(row(stage, config, ndcg, stats))
        print(f"[{stage}] {config.to_json()} -> val NDCG@10 {ndcg.mean():.4f}")
        return val, model, ndcg

    # Stage 1: trending window, other settings at their defaults.
    best_window = max(
        TRENDING_WINDOWS,
        key=lambda w: (run("trending_window", replace(DEFAULT_CONFIG, trending_window_days=w))[2].mean(), w == DEFAULT_CONFIG.trending_window_days),
    )
    # Stage 2: quotas x pool size with the chosen window.
    fitted = {}
    for pool_size in POOL_SIZES:
        for als in ALS_QUOTAS:
            if als > pool_size:
                continue
            config = PolicyConfig.with_collaborative_quota(
                als, pool_size, trending_window_days=best_window,
                ranker_blend=DEFAULT_CONFIG.ranker_blend, first_timer_popularity=DEFAULT_CONFIG.first_timer_popularity,
            )
            val, model, ndcg = run("quotas_pool", config)
            fitted[config] = (val, model, float(ndcg.mean()))
    best_retrieval = max(fitted, key=lambda c: fitted[c][2])
    val, model, _ = fitted[best_retrieval]
    # Stage 3: blend weight x first-timer rule on the chosen lists and ranker.
    finals = {}
    for weight in BLEND_WEIGHTS:
        for first in FIRST_TIMER_RULES:
            config = replace(best_retrieval, ranker_blend=weight, first_timer_popularity=first)
            finals[config] = run("blend_first_timer", config, val, model)[2].mean()
    chosen = max(finals, key=finals.get)

    default_ndcg = per_order.get(DEFAULT_CONFIG)
    vs_default = ev.paired_bootstrap(per_order[chosen].to_numpy(), default_ndcg.to_numpy()) if default_ndcg is not None else None
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "periods": e.periods,
        "fit_orders": int(len(e.q_fit)),
        "validation_orders": int(len(e.q_val)),
        "validation_orders_with_new_products": len(e.all_val_orders()),
        "metric": "end-to-end NDCG@10 of the served policy on validation orders",
        "search": "staged: trending window -> quotas x pool size -> ranker blend x first-timer rule",
        "default": DEFAULT_CONFIG.to_json(),
        "chosen": chosen.to_json(),
        "chosen_val_ndcg10": float(per_order[chosen].mean()),
        "default_val_ndcg10": None if default_ndcg is None else float(default_ndcg.mean()),
        "chosen_vs_default_val": vs_default,
        "rows": rows,
    }
    write(report)
    return report


def fmt(quotas: dict) -> str:
    return "/".join(str(v) for v in quotas.values())


def write(report: dict) -> None:
    REPORT_DIR.mkdir(exist_ok=True)
    (REPORT_DIR / "policy_tuning.json").write_text(json.dumps(report, indent=2))
    POLICY_CONFIG_PATH.write_text(json.dumps({
        "chosen": report["chosen"], "chosen_on": "validation", "periods": report["periods"],
        "generated_by": "python -m recommendations.tune_policy",
    }, indent=2) + "\n")
    c, d = report["chosen"], report["default"]
    lines = ["# Policy tuning (validation period)", "",
             f"Generated {report['generated_at']} by `python -m recommendations.tune_policy`.", "",
             f"Ranker fitted on {report['fit_orders']:,} orders ({report['periods']['fit'][0][:10]} to "
             f"{report['periods']['fit'][1][:10]}); settings scored on {report['validation_orders']:,} validation orders "
             f"({report['periods']['validation'][0][:10]} to {report['periods']['validation'][1][:10]}). The test period "
             f"(from {report['periods']['test_starts'][:10]}) is not used.", "",
             f"Metric: {report['metric']}. Search: {report['search']}.", "",
             "| Setting | Default | Chosen |", "|---|---|---|",
             f"| Quotas ({'/'.join(d['quotas'])}) | {fmt(d['quotas'])} | {fmt(c['quotas'])} |",
             f"| Pool size | {d['pool_size']} | {c['pool_size']} |",
             f"| Ranker weight in blend | {d['ranker_blend']} | {c['ranker_blend']} |",
             f"| First-time buyers get popularity order | {d['first_timer_popularity']} | {c['first_timer_popularity']} |",
             f"| Trending window (days) | {d['trending_window_days']} | {c['trending_window_days']} |", ""]
    if report["default_val_ndcg10"] is not None:
        v = report["chosen_vs_default_val"]
        lines += [f"Validation NDCG@10: default {report['default_val_ndcg10']:.4f}, chosen {report['chosen_val_ndcg10']:.4f} "
                  f"(paired difference {v['mean_diff']:+.4f}, 95% CI {v['ci_low']:+.4f} to {v['ci_high']:+.4f}). "
                  "Because the chosen setting is the best of many on this same period, this difference is optimistic; "
                  "the test-period numbers in evaluation_report.md are the honest estimate.", ""]
    lines += ["## Every setting tried", "", "| Stage | Quotas | Pool | Blend | First-timer popularity | Trending days | "
              "Val pool recall | Val NDCG@10 |", "|---|---|---|---|---|---|---|---|"]
    for r in report["rows"]:
        cfg = r["config"]
        quotas = "/".join(str(v) for v in cfg["quotas"].values())
        recall = f"{r['val_pool_recall']:.4f}" if "val_pool_recall" in r else "same lists"
        lines.append(f"| {r['stage']} | {quotas} | {cfg['pool_size']} | {cfg['ranker_blend']} | "
                     f"{cfg['first_timer_popularity']} | {cfg['trending_window_days']} | {recall} | {r['val_ndcg10']:.4f} |")
    (REPORT_DIR / "policy_tuning.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-content", action="store_true", help="Skip the content-similarity source.")
    main(use_content=not parser.parse_args().no_content)
