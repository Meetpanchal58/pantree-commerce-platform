# Evaluation

How the recommender is evaluated offline, the full results, and why choices that look like leaks are not.
The headline numbers are summarised in the [README](../README.md#results-at-a-glance).

## Method

`python -m recommendations.evaluate` answers one question for every real order in the held-out
final period: **given everything known one hour before the order, does the system rank the
products the customer actually bought near the top?** That is the job of the home-page
"Recommended for you" row.

- **Three time periods, no peeking.** ALS and co-purchases learn only from data before each
  period's cut-off, with a 7-day gap; trending, recent activity, popularity and stock are taken as
  of each query time. The ranker is
  trained on candidate lists for orders in the middle period (B) and everything is scored on the
  final period (C).
- **Same lists, different orderings.** For each order a candidate list is built (a quota per
  source, then filled by score to the pool size; already-bought and then-unavailable products
  excluded) by the same code the storefront serves, using the settings chosen on validation. Random order,
  popularity, retrieval score (production without a ranker), the
  11-feature ranker, the ranker blend and the served policy all rank the *same*
  lists, so differences come from ranking alone.
- **Two views.** *End to end* uses every test order (a bought product missing from the list counts
  as a miss). *Ranking only* uses orders whose list contains a bought product.
- **Segments and uncertainty.** Results are reported for first-time and repeat buyers, with a
  power label (under 100 orders = underpowered) and paired-bootstrap 95% confidence intervals.
- Reports are written to `recommendations/reports/evaluation_report.md` and `.json`.

> **Why the evaluation was rebuilt.** The earlier version used the first product a shopper viewed in
> a visit as the query and counted a candidate as correct only if bought later that day. The viewed
> product was usually the one bought, but it is excluded from the candidates, so almost no
> candidate list contained a correct answer and the metrics measured almost nothing.

## Results (default dataset)

<!-- EVAL:START -->
*Generated from `recommendations/reports/evaluation_report.json`; full detail in [`evaluation_report.md`](../recommendations/reports/evaluation_report.md).*

End-to-end NDCG@10 (every held-out order with a new product; a bought product missing from the candidate list counts as a miss), and the share of the random → oracle gap each ordering captures:

| Ordering | All (5,647) | First-time buyers (1,521) | Repeat buyers (4,126) | Ranking-only, all | Share of achievable gain |
|---|---|---|---|---|---|
| Random order | 0.0062 | 0.0038 | 0.0071 | 0.033 | – |
| Popularity | 0.0164 | 0.0226 | 0.0142 | 0.086 | 26% (19% to 33%) |
| Retrieval score (previous production) | 0.0239 | 0.0104 | 0.0289 | 0.127 | 45% (37% to 54%) |
| New ranker (11 features) | 0.0384 | 0.0248 | 0.0434 | 0.203 | 82% (72% to 93%) |
| Ranker blend (all customers) | 0.0386 | 0.0253 | 0.0435 | 0.205 | 83% (73% to 94%) |
| **Segment-aware (served)** | 0.0386 | 0.0253 | 0.0435 | 0.205 | 83% (73% to 94%) |
| Oracle (true taste, not deployable) | 0.0452 | 0.0380 | 0.0478 | 0.249 | – |

Paired bootstrap over orders (end-to-end NDCG@10):

- new ranker vs retrieval: +0.0145 (95% CI +0.0118 to +0.0171, significant)
- new ranker vs popularity: +0.0219 (95% CI +0.0187 to +0.0251, significant)
- new ranker vs random: +0.0321 (95% CI +0.0281 to +0.0359, significant)
- segment aware vs retrieval: +0.0147 (95% CI +0.0122 to +0.0172, significant)
- segment aware vs popularity: +0.0221 (95% CI +0.0190 to +0.0253, significant)
- segment aware vs production blend: +0.0000 (95% CI +0.0000 to +0.0000, not significant)

Retrieval (is a bought product in the candidate list at all?). Per-source figures use each source's full list; the combined candidate list takes a quota from each source (collaborative 80, content 17, fbt 17, trending 17, recent_activity 17) and then fills by score, so one source alone can exceed the combined figure:

- collaborative: 14.4% of test orders
- trending: 6.3% of test orders
- recent_activity: 6.2% of test orders
- content: 4.5% of test orders
- fbt: 2.6% of test orders
- all sources combined (candidate list of up to 150): 20.4%; **retrieval ceiling** (top 150 of all eligible products by true taste): 46.2%

Order counts: 5,831 paid orders in the test period. 25 came from customers with no earlier activity (cold start); they stay in and are scored with documented default features. 184 only re-bought earlier products (excluded from candidates), leaving 5,647 for the end-to-end tables. 1,191 had a bought product in the list; ranking-only tables also need at least 10 candidates and one not-bought candidate (0 + 0 dropped), leaving 1,191.

Policy settings (chosen on the validation period by `python -m recommendations.tune_policy`): pool of 150, quotas collaborative 80, content 17, fbt 17, trending 17, recent_activity 17, ranker weight 0.75 in the blend with the retrieval score, first-time buyers get the same ranker blend, trending window 30 days.

**Leave-one-source-out ablation (test period).**

Each row rebuilds the candidate lists without one source, retrains the ranker without it on period B and scores the served policy on the same test orders. The interval is the paired-bootstrap 95% CI of the change in NDCG@10 versus the full system.

| Without | Pool recall | Served NDCG@10 | Change vs full (95% CI) |
|---|---|---|---|
| (nothing: full system) | 0.1667 | 0.0386 | – |
| collaborative | 0.1153 | 0.0294 | -0.0092 (-0.0119 to -0.0066) |
| content | 0.1609 | 0.0384 | -0.0001 (-0.0015 to +0.0012) |
| fbt | 0.1655 | 0.0376 | -0.0010 (-0.0023 to +0.0003) |
| trending | 0.1609 | 0.0340 | -0.0045 (-0.0062 to -0.0030) |
| recent_activity | 0.1596 | 0.0400 | +0.0014 (+0.0002 to +0.0026) |

**Recall acceptance check (passed):** combined candidate list recall 0.1667 vs 0.1178 for the best single source (collaborative, its own top list of the same size).

Ranker features with the most gain: `n_sources` (23%), `retrieval_score` (21%), `log_product_popularity` (12%), `log_price_ratio` (12%), `source_id` (10%).

ALS tuning (`python -m recommendations.tune_als`, chosen on the validation period with a 7-day embargo, checked once on the test period): 32 factors, regularisation 0.1, weights (view, cart, purchase) (1, 3, 5); held-out Recall@20 0.0900 with the previous serving settings → 0.0900. Serving and evaluation read these settings from `als_tuning.json`. Full grid: [`als_tuning.md`](../recommendations/reports/als_tuning.md).
<!-- EVAL:END -->

To reproduce: generate with `PANTREE_TODAY=2026-06-30`, then run `python -m recommendations.tune_als`,
`python -m recommendations.evaluate` and `python -m recommendations.readme_summary` (which rewrites the
generated blocks in this README). Absolute numbers are low because the task is predicting
the exact product out of 10,000; compare orderings with each other, not with published benchmarks.

## What counts as a purchase

One rule, defined once in `recommendations/candidates/common.py`
(`NON_PURCHASE_STATUSES`, `purchase_mask`, `purchase_sql`) and used by the
recommenders, the evaluation, the storefront and the online-metrics report:

- the payment succeeded, and
- the order line was not cancelled (`CANCELLED`, or `CIC`: cancelled by the seller), returned to sender
  (`RTO`), refunded, lost or undelivered.

A line the customer received and later returned (`CIR`) **is** a purchase: the
customer chose and paid for the product, which is what a recommender is trying to
predict. Lines whose payment failed are not purchases, whatever their status.

## Customers with no history (cold start)

Test orders from customers with no earlier activity stay in the evaluation. They
are scored by the same ranker with documented default features
(`recommendations/ranker.py`, `candidate_features`): activity and purchase counts
0, category affinity 0, price ratio 0 (neutral) and no anchor product. The
report counts them as "cold-start orders".

## Product rating (`avg_rating`) is a catalog feature

The ranker and the popularity fallbacks use the product's average rating from the
catalog (`product_master.avg_rating`). This is the value a shopper sees on the
product page, and a production ranker would read the same field at request time,
so it is a legitimate feature rather than a leak. It is a catalog snapshot, not a
point-in-time history, so early queries see a rating that includes later
reviews; ratings move slowly, and the effect is the same for every model compared.

Prices (`current_price`, `mrp`) used by the price-ratio and discount features are the
same kind of catalog snapshot: price history is not recorded, so every query sees
today's price.

## Nothing from the query hour reaches the evaluation

Each test order is queried one hour before it was placed. Customer history
(`History.viewed_before` / `bought_list`), product popularity
(`ActivityIndex.counts_before`), trending, recent activity and the co-purchase and
ALS training data all use events strictly before their time argument, and the
availability filter uses the last full day's inventory close before the query.
`tests/test_leakage.py` adds views, cart adds and purchases of the target product
inside that hour (and the target order itself) and checks that the candidate list
and every feature value stay identical, and that a restock or sell-out recorded
after the query does not change availability.

## The simulator's ground truth is off limits

The simulator decides purchases from hidden inputs: each customer's true taste
(`generators/taste.py`, `data/_state/customer_profiles.parquet`) and a hand-written
table of complementary product types (`generators.config.COMPLEMENTS`, which also
shapes the generator's `product_affinity` table). A model that reads them is given
the answer.

- `recommendations/oracle.py` is the only module that reads the taste inputs, and
  only to build the oracle upper bound and the retrieval ceiling.
- Complementary products (`recommendations/candidates/fbt.py`) are learned from
  orders and browsing sessions before the cut-off: same-order pairs, same-session
  co-views, and product types bought together more often than chance (lift > 1,
  at least 5 orders).
- `tests/test_ground_truth_guard.py` fails if any file under `recommendations/` or
  `webapp/` imports `generators.taste` (except the oracle) or
  `generators.config.COMPLEMENTS`, or names `customer_profiles` or
  `product_affinity`.

## ALS settings

`recommendations/tune_als.py` writes `recommendations/reports/als_tuning.json`.
Serving and evaluation both read its `best_on_validation` entry through
`collaborative.als_params()`; if the file is missing they fall back to the
defaults in `collaborative.FALLBACK_ALS_PARAMS` and log a warning. Tuning uses the
same 7-day embargo as the evaluation (the model is trained on data up to 7 days
before the validation and test periods start) and compares each setting with the
settings serving used before the run.

ALS learns from product views and cart adds in the clickstream, plus order lines
weighted by quantity. The clickstream `purchase` event is not used, because the
order line already records the same purchase.

## What is evaluated is what is served

`recommendations/policy.py` holds the personalised "Recommended for you" policy:
the five candidate sources, the candidate-list rules (dedup, availability, a
quota per source, then filled to the pool size by score) and the ordering (a
blend of the retrieval score and the min-max-scaled ranker score, with optional
popularity order for customers with no purchases). The quotas, pool size, blend
weight, first-timer rule and trending window are read from
`recommendations/reports/policy_config.json`; see "Policy settings" below. `serve.recommend(..., intent="personalized")` and
`evaluate.py` both call it, and `tests/test_policy_parity.py` checks that they
return identical lists and scores for the same customer and moment.

Each source has one implementation that takes a time:

| Source | Serving | Evaluation |
|---|---|---|
| ALS, co-purchase | fitted on all data | fitted on data before the period cut-off (7-day embargo) |
| Trending (last 30 days), recent activity, product popularity | now | the query time |
| Content similarity | catalog text (no time dependence) | same |

The evaluation also drops candidates that were not sellable at the query time:
out of stock at the previous day's close (`inventory.parquet`), not yet launched,
or already discontinued. Serving applies the same rule to current stock and the
catalog's active flag.

Two serving-only extras were removed from the personalised row rather than added
to the evaluation:

- **Deals on viewed items** ranked the biggest markdown in each viewed category.
  Discounts are only recorded as today's price, so there is no point-in-time
  price to evaluate it with; keeping it would put an unevaluated source in the
  served row.
- **The empty-list popularity fallback** is no longer needed: trending is one of
  the five sources, so the list is never empty once activity has loaded. While
  activity is still loading at start-up, the row shows the catalog fallback
  (highly rated products); this is a warm-up state, not part of the policy.

## Policy settings and the source ablation

`python -m recommendations.tune_policy` chooses the per-source quotas, pool size,
ranker blend weight, first-timer rule and trending window on a validation slice of
period B (the ranker is fitted on the earlier part of B); the test period is not
used. `recommendations/reports/policy_tuning.md` lists every setting tried.

The ablation below is reported once, on the test period, and is **not** used to
change the policy: a source whose removal looks helpful on test would have to be
re-checked on the validation period first.

<!-- ABLATION:START -->
*Generated from `recommendations/reports/evaluation_report.json` by `python -m recommendations.readme_summary`.*

Policy settings (chosen on the validation period by `python -m recommendations.tune_policy`): pool of 150, quotas collaborative 80, content 17, fbt 17, trending 17, recent_activity 17, ranker weight 0.75 in the blend with the retrieval score, first-time buyers get the same ranker blend, trending window 30 days.

### Leave-one-source-out ablation (test period)

Each row rebuilds the candidate lists without one source, retrains the ranker without it on period B and scores the served policy on the same test orders. The interval is the paired-bootstrap 95% CI of the change in NDCG@10 versus the full system.

| Without | Pool recall | Served NDCG@10 | Change vs full (95% CI) |
|---|---|---|---|
| (nothing: full system) | 0.1667 | 0.0386 | – |
| collaborative | 0.1153 | 0.0294 | -0.0092 (-0.0119 to -0.0066) |
| content | 0.1609 | 0.0384 | -0.0001 (-0.0015 to +0.0012) |
| fbt | 0.1655 | 0.0376 | -0.0010 (-0.0023 to +0.0003) |
| trending | 0.1609 | 0.0340 | -0.0045 (-0.0062 to -0.0030) |
| recent_activity | 0.1596 | 0.0400 | +0.0014 (+0.0002 to +0.0026) |

**Recall acceptance check (passed):** combined candidate list recall 0.1667 vs 0.1178 for the best single source (collaborative, its own top list of the same size).
<!-- ABLATION:END -->
