"""Rewrite the generated blocks in README.md from the report files.

* RESULTS  - "Results at a glance" (recommendations/reports/evaluation_report.json)
* EVAL     - full recommendation results tables (same report + als_tuning.json)
* INSIGHTS - data-analysis findings (analysis/insights_summary.json, written by
             the executed notebook)

Every number in these blocks comes from a generated file; nothing is typed by
hand. Run after `python -m recommendations.evaluate` (and the notebook build):

    python -m recommendations.readme_summary
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = Path(__file__).with_name("reports") / "evaluation_report.json"
ALS_REPORT = Path(__file__).with_name("reports") / "als_tuning.json"
INSIGHTS = ROOT / "analysis" / "insights_summary.json"
README = ROOT / "README.md"
START, END = "<!-- RESULTS:START -->", "<!-- RESULTS:END -->"
NAMES = {
    "random": "Random order", "popularity": "Popularity", "retrieval": "Retrieval score (previous production)",
    "legacy_ranker": "Old ranker (3 features)", "new_ranker": "New ranker (11 features)",
    "production_blend": "Production blend (50/50)", "segment_aware": "**Segment-aware (served)**",
    "oracle": "Oracle (true taste, not deployable)",
}


def pct(x, digits=1):
    return f"{x * 100:.{digits}f}%"


def build(report: dict) -> str:
    st, oc = report["settings"], report["order_counts"]
    e2e = report["results"]["end_to_end"]["all"]["orderings"]
    ranking = report["results"]["ranking_only"]["all"]
    rel = report["relative_lift_served_vs_retrieval_ndcg10"]
    diff = report["comparisons_ndcg10"]["segment_aware_vs_retrieval"]
    served, random = e2e["segment_aware"]["@10"]["ndcg"], e2e["random"]["@10"]["ndcg"]
    gap = report["gap_closed_ndcg10"]["end_to_end"]["all"]["segment_aware"]
    ceiling = report["retrieval_ceiling"]
    lines = [
        "## Results at a glance",
        "",
        "*Generated from `recommendations/reports/evaluation_report.json` by `python -m recommendations.readme_summary`.*",
        "",
        "- **Problem:** rank products for the home-page \"Recommended for you\" row, predicting the exact product "
        "bought among 10,000, using only data known one hour before the order.",
        "- **Method:** 5 retrieval sources (implicit ALS, trending, recent activity, FAISS content similarity, "
        "co-purchase) → 11-feature LightGBM LambdaRank → segment-aware policy (popularity for first-time buyers).",
        f"- **Evaluation:** 3-period temporal split, {st['embargo_days']}-day embargo, {oc['orders_with_new_products']:,} "
        f"held-out orders (of {oc['test_orders']:,}; see order counts below), paired-bootstrap 95% CIs.",
        f"- **Headline:** served policy NDCG@10 {served:.4f} vs {rel['retrieval_ndcg10']:.4f} for retrieval order: "
        f"{diff['mean_diff']:+.4f} absolute (95% CI {diff['ci_low']:+.4f} to {diff['ci_high']:+.4f}), "
        f"{rel['value']:+.0%} relative (95% CI {rel['ci_low']:+.0%} to {rel['ci_high']:+.0%}); "
        f"{served / random:.1f}× random. Within lists that contain a bought product "
        f"({oc['ranking_only_orders']:,} orders), NDCG@10 is {ranking['orderings']['segment_aware']['@10']['ndcg']:.3f}.",
        f"- **Ceiling:** the served policy captures {pct(gap['value'], 0)} of the achievable gain from random to an oracle "
        f"that knows each customer's true taste (95% CI {pct(gap['ci_low'], 0)} to {pct(gap['ci_high'], 0)}).",
        f"- **Bottleneck:** candidate lists contain a bought product for {pct(ceiling['candidate_list_hit_rate'])} of orders; "
        f"the top {ceiling['k']} by true taste would contain one for {pct(ceiling['hit_rate'])}. Next step: better "
        "retrieval (taste-aware candidate generation, e.g. a two-tower model), not more ranking features.",
        "- **Caveat:** synthetic data with known ground truth; online impression/click logging and an A/B split are "
        "built, but there is no real traffic yet.",
    ]
    return "\n".join(lines)


def build_eval(report: dict) -> str:
    """Full results section: tables by segment, significance, gap closed, retrieval, counts."""
    e2e, rank = report["results"]["end_to_end"], report["results"]["ranking_only"]
    gaps = report["gap_closed_ndcg10"]["end_to_end"]
    oc, ret, ceiling = report["order_counts"], report["retrieval"], report["retrieval_ceiling"]
    groups = [("all", "All"), ("first_order", "First-time buyers"), ("repeat_order", "Repeat buyers")]
    lines = [
        "*Generated from `recommendations/reports/evaluation_report.json`; full detail in "
        "[`evaluation_report.md`](recommendations/reports/evaluation_report.md).*", "",
        "End-to-end NDCG@10 (every held-out order with a new product; a bought product missing from the candidate "
        "list counts as a miss), and the share of the random → oracle gap each ordering captures:", "",
        "| Ordering | " + " | ".join(f"{label} ({e2e[g]['orders']:,})" for g, label in groups)
        + " | Ranking-only, all | Share of achievable gain |",
        "|---|" + "---|" * (len(groups) + 2),
    ]
    for o in e2e["all"]["orderings"]:
        cells = [f"{e2e[g]['orderings'][o]['@10']['ndcg']:.4f}" for g, _ in groups]
        rk = rank["all"]["orderings"][o]["@10"]
        gap = gaps["all"].get(o, {})
        gap_text = (f"{gap['value']:.0%} ({gap['ci_low']:.0%} to {gap['ci_high']:.0%})"
                    if gap.get("value") is not None and o not in ("random", "oracle") else "–")
        lines.append(f"| {NAMES.get(o, o)} | " + " | ".join(cells) + f" | {rk['ndcg']:.3f} | {gap_text} |")
    lines += ["", "Paired bootstrap over orders (end-to-end NDCG@10):", ""]
    for name, c in report["comparisons_ndcg10"].items():
        if c["mean_diff"] is None:
            continue
        verdict = "significant" if c["ci_low"] > 0 or c["ci_high"] < 0 else "not significant"
        lines.append(f"- {name.replace('_vs_', ' vs ').replace('_', ' ')}: {c['mean_diff']:+.4f} "
                     f"(95% CI {c['ci_low']:+.4f} to {c['ci_high']:+.4f}, {verdict})")
    lines += ["", "Retrieval (is a bought product in the candidate list at all?):", ""]
    for name, hits in sorted(ret["source_hits"].items(), key=lambda x: -x[1]):
        lines.append(f"- {name}: {hits / ret['queries']:.1%} of test orders")
    lines += [
        f"- all sources combined (candidate list of up to {report['settings']['pool_size']}): "
        f"{ceiling['candidate_list_hit_rate']:.1%}; **retrieval ceiling** (top {ceiling['k']} of all eligible products "
        f"by true taste): {ceiling['hit_rate']:.1%}",
        "",
        f"Order counts: {oc['test_orders']:,} paid orders in the test period. {oc['orders_dropped_no_history']:,} came from "
        "customers with no earlier activity and are dropped by a known feature-building issue (pandas drops groups with a "
        f"missing history anchor; left unchanged so results stay comparable). {oc['orders_only_rebuying_previous_products']:,} "
        f"only re-bought earlier products (excluded from candidates), leaving {oc['orders_with_new_products']:,} for the "
        f"end-to-end tables. {oc['orders_with_purchase_in_list']:,} had a bought product in the list "
        f"({oc['orders_with_purchase_in_list_dropped_no_history']:,} of them no-history orders); ranking-only tables "
        f"also need at least {report['settings']['min_pool']} candidates and one not-bought candidate "
        f"({oc['dropped_list_under_min_pool']:,} + {oc['dropped_list_all_purchased']:,} dropped), leaving "
        f"{oc['ranking_only_orders']:,}.",
    ]
    if report.get("feature_importance"):
        top = list(report["feature_importance"].items())[:5]
        lines += ["", "Ranker features with the most gain: " + ", ".join(f"`{f}` ({v:.0%})" for f, v in top) + "."]
    if ALS_REPORT.exists():
        als = json.loads(ALS_REPORT.read_text())
        best, test = als["best_on_validation"], als["test"]
        from recommendations.candidates.collaborative import ALS_PARAMS, EVENT_WEIGHTS

        lines += ["", f"ALS tuning (`python -m recommendations.tune_als`, chosen on the validation period, checked once "
                  f"on the test period): best {best['factors']} factors, regularisation {best['regularization']}, "
                  f"weights (view, cart, purchase) {tuple(best['weights'])}; held-out Recall@{als['k']} "
                  f"{test['default']['recall@' + str(als['k'])]:.4f} (original 48 factors, 1/3/5) → "
                  f"{test['best']['recall@' + str(als['k'])]:.4f}. Serving currently uses {ALS_PARAMS['factors']} factors, "
                  f"regularisation {ALS_PARAMS['regularization']}, weights "
                  f"{tuple(EVENT_WEIGHTS[k] for k in ('pdp_view', 'add_to_cart', 'purchase'))}. "
                  "Full grid: [`als_tuning.md`](recommendations/reports/als_tuning.md)."]
    return "\n".join(lines)


def build_insights(summary: dict) -> str:
    lines = ["*Generated from `analysis/insights_summary.json`, written when the notebook runs.*", ""]
    lines.append(f"- **Conversion:** {summary['session_conversion']:.1%} of {summary['sessions']:,} sessions end in a "
                 f"purchase; {summary['cart_to_purchase']:.0%} of cart adds are bought.")
    lines.append(f"- **Cash on delivery:** {summary['cod_rto']:.1%} of COD parcels come back undelivered (RTO) vs "
                 f"{summary['prepaid_rto']:.1%} for prepaid ({summary['cod_rto'] / summary['prepaid_rto']:.1f}×).")
    by_cat = summary["return_rate_by_category"]
    hi, lo = max(by_cat, key=by_cat.get), min(by_cat, key=by_cat.get)
    lines.append(f"- **Returns:** {summary['return_rate']:.1%} of delivered items are returned ({hi.lower()} "
                 f"{by_cat[hi]:.1%}, {lo.lower()} {by_cat[lo]:.1%}); size and fit problems explain "
                 f"{summary['size_fit_share']:.0%} of returns.")
    lines.append(f"- **Channels:** a {summary['best_channel']} customer is worth ₹{summary['best_channel_value']:,.0f} on "
                 f"average vs ₹{summary['worst_channel_value']:,.0f} for {summary['worst_channel']} "
                 f"({summary['best_channel_value'] / summary['worst_channel_value']:.1f}×).")
    lines.append(f"- **Repeat buyers:** {summary['repeat_buyer_share']:.0%} of buyers order again and bring "
                 f"{summary['repeat_revenue_share']:.0%} of revenue.")
    lines.append(f"- **Price change:** after 1 Feb 2025 customers paid {summary['price_change_paid_vs_list']:+.1%} "
                 "relative to list price.")
    if summary.get("diwali_uplift"):
        diwali = ", ".join(f"{m} {u:+.0%}" for m, u in summary["diwali_uplift"].items())
        lines.append(f"- **Festivals:** Diwali months show {diwali} revenue vs the neighbouring normal months "
                     f"(festival months overall {summary['festival_uplift_avg']:+.0%}); comparing with neighbours "
                     "controls for the shop's growth.")
    churn = summary.get("churn")
    if churn:
        lines.append(f"- **Churn model:** on the latest held-out snapshots (temporal split, 90-day embargo), "
                     f"{churn['model'].lower()} (chosen on validation) reaches ROC-AUC {churn['test_auc']:.3f} "
                     f"(95% CI {churn['test_auc_ci']}) vs {churn['rule_auc']:.3f} for a days-since-last-order rule; "
                     f"targeting the top 10% catches {churn['top_decile_capture']:.0%} of churners at "
                     f"{churn['top_decile_lift']:.1f}× the base rate ({churn['base_rate']:.0%}). Weak, but leakage-free.")
        lines.append(f"- **Leakage found:** the generator's `customer_monthly_features` count orders from the whole "
                     f"snapshot month, after `snapshot_date` ({summary['churn_stored_feature_leaky_rows']:,} rows); "
                     f"trained on them the same model would report AUC {churn['leaky_auc']:.3f}. The notebook rebuilds "
                     "every feature strictly before the snapshot.")
    return "\n".join(lines)


def replace_block(text: str, name: str, body: str) -> str:
    start, end = f"<!-- {name}:START -->", f"<!-- {name}:END -->"
    if start not in text or end not in text:
        return text
    return re.sub(re.escape(start) + r".*?" + re.escape(end), lambda _: f"{start}\n{body}\n{end}", text, flags=re.S)


def main() -> None:
    report = json.loads(REPORT.read_text())
    text = README.read_text()
    block = f"{START}\n{build(report)}\n{END}"
    if START in text and END in text:
        text = re.sub(re.escape(START) + r".*?" + re.escape(END), lambda _: block, text, flags=re.S)
    else:
        # First run: insert directly under the H1.
        first_line_end = text.index("\n") + 1
        text = text[:first_line_end] + "\n" + block + "\n" + text[first_line_end:]
    text = replace_block(text, "EVAL", build_eval(report))
    if INSIGHTS.exists():
        text = replace_block(text, "INSIGHTS", build_insights(json.loads(INSIGHTS.read_text())))
    README.write_text(text)
    print(build(report))


if __name__ == "__main__":
    main()
