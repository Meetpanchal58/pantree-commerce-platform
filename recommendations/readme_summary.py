"""Rewrite the generated blocks in README.md from the report files.

* RESULTS  - "Results at a glance" (recommendations/reports/evaluation_report.json)
* EVAL     - full recommendation results tables (same report + als_tuning.json)
* INSIGHTS - data-analysis findings (recommendations/reports/insights_summary.json, written by
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
INSIGHTS = ROOT / "recommendations" / "reports" / "insights_summary.json"
README = ROOT / "README.md"
EVALUATION_DOC = ROOT / "docs" / "recommender.md"
START, END = "<!-- RESULTS:START -->", "<!-- RESULTS:END -->"
NAMES = {
    "random": "Random order", "popularity": "Popularity", "retrieval": "Retrieval score (previous production)",
    "new_ranker": "New ranker (11 features)",
    "production_blend": "Ranker blend (all customers)", "segment_aware": "**Segment-aware (served)**",
    "oracle": "Oracle (true taste, not deployable)",
}


def policy_summary(settings: dict) -> str:
    """One phrase describing the served ordering, from the settings the report was run with."""
    p = settings.get("policy") or {}
    weight = p.get("ranker_blend", 0.5)
    blend = f"{weight:.2f} ranker + {1 - weight:.2f} retrieval-score blend"
    if p.get("first_timer_popularity", True):
        return f"segment-aware policy (popularity for first-time buyers, {blend} otherwise)"
    return f"{blend} for every customer (settings chosen on validation)"


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
        f"bought among {catalog_size():,}, using only data known one hour before the order.",
        "- **Method:** 5 retrieval sources (implicit ALS, trending, recent activity, FAISS content similarity, "
        f"co-purchase) → 11-feature LightGBM LambdaRank → {policy_summary(st)}. "
        "The evaluated policy is the code the storefront serves (`recommendations/policy.py`).",
        f"- **Evaluation:** 3-period temporal split, {st['embargo_days']}-day embargo, {oc['orders_with_new_products']:,} "
        f"held-out orders (of {oc['test_orders']:,}; order counts in docs/recommender.md), paired-bootstrap 95% CIs.",
        f"- **Headline:** served policy NDCG@10 {served:.4f} vs {rel['retrieval_ndcg10']:.4f} for retrieval order: "
        f"{diff['mean_diff']:+.4f} absolute (95% CI {diff['ci_low']:+.4f} to {diff['ci_high']:+.4f}), "
        f"{rel['value']:+.0%} relative (95% CI {rel['ci_low']:+.0%} to {rel['ci_high']:+.0%}); "
        f"{served / random:.1f}× random. Within lists that contain a bought product "
        f"({oc['ranking_only_orders']:,} orders), NDCG@10 is {ranking['orderings']['segment_aware']['@10']['ndcg']:.3f}.",
        f"- **Oracle:** {oracle_lines(report)[0]}",
        f"- **Bottleneck:** {oracle_lines(report)[1]}",
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
        "[`evaluation_report.md`](../recommendations/reports/evaluation_report.md).*", "",
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
    quotas = report["settings"]["per_source"]
    quota_str = ", ".join(f"{k} {v}" for k, v in quotas.items()) if isinstance(quotas, dict) else f"{quotas} each"
    lines += ["", "Retrieval (is a bought product in the candidate list at all?). Per-source figures use each source's "
              f"full list; the combined candidate list takes a quota from each source ({quota_str}) and then fills "
              "by score, so one source alone can exceed the combined figure:", ""]
    for name, hits in sorted(ret["source_hits"].items(), key=lambda x: -x[1]):
        lines.append(f"- {name}: {hits / ret['queries']:.1%} of test orders")
    lines += [
        f"- all sources combined (candidate list of up to {report['settings']['pool_size']}): "
        f"{ceiling['candidate_list_hit_rate']:.1%}; **retrieval ceiling** (top {ceiling['k']} of all eligible products "
        f"by true taste): {ceiling['hit_rate']:.1%}",
        "",
        f"Order counts: {oc['test_orders']:,} paid orders in the test period. {oc['cold_start_orders']:,} came from "
        "customers with no earlier activity (cold start); they stay in and are scored with documented default "
        f"features. {oc['orders_only_rebuying_previous_products']:,} "
        f"only re-bought earlier products (excluded from candidates), leaving {oc['orders_with_new_products']:,} for the "
        f"end-to-end tables. {oc['orders_with_purchase_in_list']:,} had a bought product in the list; ranking-only tables "
        f"also need at least {report['settings']['min_pool']} candidates and one not-bought candidate "
        f"({oc['dropped_list_under_min_pool']:,} + {oc['dropped_list_all_purchased']:,} dropped), leaving "
        f"{oc['ranking_only_orders']:,}.",
    ]
    from recommendations.evaluate import ablation_lines, policy_text  # same wording as the report

    if report["settings"].get("policy"):
        lines += ["", policy_text(report["settings"])]
    lines += [line.replace("## Leave-one-source-out ablation (test period)", "**Leave-one-source-out ablation (test period).**")
              for line in ablation_lines(report)]
    if report.get("feature_importance"):
        top = list(report["feature_importance"].items())[:5]
        lines += ["", "Ranker features with the most gain: " + ", ".join(f"`{f}` ({v:.0%})" for f, v in top) + "."]
    if ALS_REPORT.exists():
        als = json.loads(ALS_REPORT.read_text())
        best, test, k = als["best_on_validation"], als["test"], als["k"]
        lines += ["", f"ALS tuning (`python -m recommendations.tune_als`, chosen on the validation period with a "
                  f"{als.get('embargo_days', 0)}-day embargo, checked once on the test period): {best['factors']} factors, "
                  f"regularisation {best['regularization']}, weights (view, cart, purchase) {tuple(best['weights'])}; "
                  f"held-out Recall@{k} {test['serving_before_run']['recall@' + str(k)]:.4f} with the previous serving "
                  f"settings → {test['best']['recall@' + str(k)]:.4f}. Serving and evaluation read these settings from "
                  "`als_tuning.json`. Full grid: [`als_tuning.md`](../recommendations/reports/als_tuning.md)."]
    return "\n".join(lines)


def oracle_lines(report: dict) -> tuple[str, str]:
    """The oracle and bottleneck sentences, worded for what the numbers actually show.

    The oracle ranks by each customer's true taste. In a shop where purchases also follow
    popularity, page position, stock and sales, a model can beat it, so it is a ceiling only
    when it actually scores higher."""
    e2e = report["results"]["end_to_end"]
    ndcg = lambda seg, o: e2e[seg]["orderings"][o]["@10"]["ndcg"]
    served, oracle = ndcg("all", "segment_aware"), ndcg("all", "oracle")
    gap = report["gap_closed_ndcg10"]["end_to_end"]["all"]["segment_aware"]
    ceiling = report["retrieval_ceiling"]
    if served < oracle:
        oracle_text = (f"the served policy captures {gap['value']:.0%} of the achievable gain from random to an oracle "
                       f"that knows each customer's true taste (95% CI {gap['ci_low']:.0%} to {gap['ci_high']:.0%}).")
    else:
        oracle_text = (f"the served policy matches an oracle that ranks by each customer's true taste alone "
                       f"(NDCG@10 {served:.4f} vs {oracle:.4f}): shoppers here also follow popularity, page position, "
                       "stock and sales, so taste alone is not a ceiling. For first-time buyers, where the model knows "
                       f"little, the oracle is still ahead ({ndcg('first_order', 'segment_aware'):.3f} vs "
                       f"{ndcg('first_order', 'oracle'):.3f}).")
    if ceiling["hit_rate"] > ceiling["candidate_list_hit_rate"]:
        bottleneck = (f"candidate lists contain a bought product for {ceiling['candidate_list_hit_rate']:.1%} of orders; "
                      f"the top {ceiling['k']} by true taste would contain one for {ceiling['hit_rate']:.1%}. Next step: "
                      "better retrieval (taste-aware candidate generation, e.g. a two-tower model), not more ranking features.")
    else:
        bottleneck = (f"only {ceiling['candidate_list_hit_rate']:.1%} of orders have a bought product anywhere in the "
                      f"{ceiling['k']}-product candidate list, so retrieval caps the score (the ranker cannot recover a "
                      "product that was never retrieved). Next step: better candidate generation, e.g. a two-tower "
                      "retriever trained on views, impressions and orders.")
    return oracle_text, bottleneck


def catalog_size() -> int:
    """Number of products in the catalogue the evaluation ran on."""
    import pyarrow.parquet as pq

    path = ROOT / "data" / "product_master.parquet"
    return pq.read_metadata(path).num_rows if path.exists() else 0


def build_insights(summary: dict) -> str:
    lines = ["*Generated from `recommendations/reports/insights_summary.json`, written when `analysis/insights.ipynb` runs.*", ""]
    lines.append(f"- **Conversion:** {summary['session_conversion']:.1%} of {summary['sessions']:,} sessions end in a "
                 f"purchase; {summary['cart_to_purchase']:.0%} of cart adds are bought.")
    if summary["prepaid_rto"] > 0:
        rto = (f"{summary['cod_rto']:.1%} of COD parcels come back undelivered (RTO) vs {summary['prepaid_rto']:.1%} "
               f"for prepaid ({summary['cod_rto'] / summary['prepaid_rto']:.1f}×)")
    else:
        rto = (f"{summary['cod_rto']:.1%} of COD parcels come back undelivered (RTO, the buyer refuses at the door); "
               "prepaid parcels never do")
    lines.append(f"- **Cash on delivery:** {rto}.")
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
    lines.append(f"- **Discounts:** shoppers pay {summary['paid_vs_mrp_event']:.0%} of MRP on sale-event days vs "
                 f"{summary['paid_vs_mrp_normal']:.0%} on normal days.")
    if summary.get("diwali_uplift"):
        diwali = ", ".join(f"{m} {u:+.0%}" for m, u in summary["diwali_uplift"].items())
        lines.append(f"- **Sale events:** daily revenue during {diwali} vs the two weeks before "
                     f"(big events overall {summary['festival_uplift_avg']:+.0%}); a local baseline controls for "
                     "the shop's growth.")
    churn = summary.get("churn")
    if churn:
        lines.append(f"- **Churn model:** on the latest held-out snapshots (temporal split, 90-day embargo), "
                     f"{churn['model'].lower()} (chosen on validation) reaches ROC-AUC {churn['test_auc']:.3f} "
                     f"(95% CI {churn['test_auc_ci']}) vs {churn['rule_auc']:.3f} for a days-since-last-order rule; "
                     f"targeting the top 10% catches {churn['top_decile_capture']:.0%} of churners at "
                     f"{churn['top_decile_lift']:.1f}× the base rate ({churn['base_rate']:.0%}). Weak, but leakage-free.")
    return "\n".join(lines)


LEAK_REPORT = ROOT / "recommendations" / "reports" / "churn_leak_before_fix.json"
POLICY_TUNING = ROOT / "recommendations" / "reports" / "policy_tuning.json"


def _ci(c: dict, fmt: str = "+.4f") -> str:
    return f"95% CI {c['ci_low']:{fmt}} to {c['ci_high']:{fmt}}"


def build_tldr(report: dict) -> str:
    """Three-line summary at the top of the README."""
    rel = report["relative_lift_served_vs_retrieval_ndcg10"]
    e2e = report["results"]["end_to_end"]["all"]["orderings"]
    served, random = e2e["segment_aware"]["@10"]["ndcg"], e2e["random"]["@10"]["ndcg"]
    gap = report["gap_closed_ndcg10"]["end_to_end"]["all"]["segment_aware"]
    ceiling = report["retrieval_ceiling"]
    return "\n".join([
        "*Generated by `python -m recommendations.readme_summary`.*", "",
        "- **What:** a fashion store (live storefront + 22 months of simulated history from the ecommerce-simulator package) with a hybrid recommender "
        "for the home-page \"Recommended for you\" row: five retrieval sources → LightGBM LambdaRank, evaluated on "
        "real held-out orders with only data known one hour before each order.",
        f"- **Result:** the served policy reaches NDCG@10 {served:.4f}, {rel['value']:+.0%} over retrieval order "
        f"({_ci(rel, '+.0%')}) and {served / random:.1f}× random"
        + (f", capturing {gap['value']:.0%} of the gap to an oracle that knows each customer's true taste."
           if gap["value"] < 1 else
           f", level with an oracle that ranks by true taste alone ({e2e['oracle']['@10']['ndcg']:.4f}): shoppers here "
           "also follow popularity and page position."),
        "- **Bottleneck:** " + oracle_lines(report)[1],
    ])


def build_leak(summary: dict) -> str:
    """The churn-leak story with numbers from recommendations/reports/churn_leak_before_fix.json."""
    if not LEAK_REPORT.exists():
        return ""
    before = json.loads(LEAK_REPORT.read_text())
    leaky_auc, clean_auc = before["leaky_feature_test_auc"], before["leakage_free_test_auc"]
    return (
        "*Generated from `recommendations/reports/churn_leak_before_fix.json`.*\n\n"
        f"**A leak I found in my own data.** In an earlier version of this project, the first churn model looked good: "
        f"{before['model'].lower()} reached test ROC-AUC **{leaky_auc:.3f}**. The old generator's monthly churn features counted the whole snapshot month, so "
        f"{before['stored_feature_leaky_rows']:,} of {before['stored_feature_rows']:,} feature rows included orders placed "
        f"after `snapshot_date`, overlapping the label window. Rebuilt strictly before the snapshot, the same model "
        f"scores **{clean_auc:.3f}**. The data now comes from the ecommerce-simulator package, whose features are "
        f"built point-in-time, and the notebook checks that before using them "
        f"({summary.get('churn_stored_feature_leaky_rows', 0):,} leaky rows)."
    )


def build_next(report: dict) -> str:
    """'What I'd do next', grounded in the evaluation and tuning reports."""
    ceiling, ret = report["retrieval_ceiling"], report["retrieval"]
    lines = [
        "*Generated from the evaluation and tuning reports by `python -m recommendations.readme_summary`.*", "",
        f"- **Improve candidate coverage.** The candidate list holds a bought product for "
        f"{ceiling['candidate_list_hit_rate']:.1%} of test orders (recall {ret['pool_recall']:.3f}); "
        + (f"the top {ceiling['k']} products by true taste would hold one for {ceiling['hit_rate']:.1%} (recall "
           f"{ceiling['recall']:.3f}). " if ceiling["hit_rate"] > ceiling["candidate_list_hit_rate"] else
           "every order without one is a guaranteed miss. ")
        + "A stronger retriever (e.g. a two-tower model trained on views, impressions and orders) is worth more "
        "than further ranking work.",
    ]
    abl = (report.get("ablation_leave_one_source_out") or {}).get("recent_activity")
    # Only worth re-checking when dropping the source *raised* NDCG on test.
    if abl and abl["ndcg10_change_vs_full"]["mean_diff"] > 0:
        c = abl["ndcg10_change_vs_full"]
        lines.append(
            f"- **Re-check the recent-activity source on validation.** In the test-period ablation, removing it moved "
            f"served NDCG@10 by {c['mean_diff']:+.4f} ({_ci(c)}) while candidate recall fell from "
            f"{ret['pool_recall']:.4f} to {abl['pool_recall']:.4f}. A decision made on test would be biased, so the "
            "source stays until a validation run says otherwise.")
    e2e = report["results"]["end_to_end"]["all"]["orderings"]
    ranker, served = e2e["new_ranker"]["@10"]["ndcg"], e2e["segment_aware"]["@10"]["ndcg"]
    weight = report["settings"].get("policy", {}).get("ranker_blend")
    if POLICY_TUNING.exists() and weight is not None:
        tuning = json.loads(POLICY_TUNING.read_text())
        chosen = tuning["chosen"]
        val = {r["config"]["ranker_blend"]: r["val_ndcg10"] for r in tuning["rows"]
               if r["stage"] == "blend_first_timer"
               and r["config"]["first_timer_popularity"] == chosen["first_timer_popularity"]}
        if 1.0 in val and weight in val:
            lines.append(
                f"- **Settle ranker alone vs blend.** Validation preferred a {weight:.2f} ranker weight over the ranker "
                f"alone by {val[weight] - val[1.0]:+.4f} NDCG@10 ({val[weight]:.4f} vs {val[1.0]:.4f}); on test the gap is "
                f"{served - ranker:+.4f} ({served:.4f} vs {ranker:.4f}). Both margins are small, so the A2 search did not "
                "really settle it; repeated validation splits (or a longer validation period) would.")
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
    text = replace_block(text, "TLDR", build_tldr(report))
    text = replace_block(text, "NEXT", build_next(report))
    summary = json.loads(INSIGHTS.read_text()) if INSIGHTS.exists() else {}
    if summary:
        text = replace_block(text, "INSIGHTS", build_insights(summary))
    text = replace_block(text, "LEAK", build_leak(summary))
    README.write_text(text)
    if EVALUATION_DOC.exists():
        doc = replace_block(EVALUATION_DOC.read_text(), "EVAL", build_eval(report))
        EVALUATION_DOC.write_text(replace_block(doc, "ABLATION", build_policy_block(report)))
    print(build(report))


def build_policy_block(report: dict) -> str:
    """Policy settings and the leave-one-source-out ablation, for docs/recommender.md."""
    from recommendations.evaluate import ablation_lines, policy_text

    lines = ["*Generated from `recommendations/reports/evaluation_report.json` by "
             "`python -m recommendations.readme_summary`.*", ""]
    if report["settings"].get("policy"):
        lines.append(policy_text(report["settings"]))
    lines += [line.replace("## Leave-one-source-out ablation (test period)", "### Leave-one-source-out ablation (test period)")
              for line in ablation_lines(report)]
    return "\n".join(lines).strip()


if __name__ == "__main__":
    main()
