# Evaluation notes

How the offline evaluation (`recommendations/evaluate.py`) defines its inputs, and
why some choices that look like leaks are not. Numbers live in
`recommendations/reports/evaluation_report.md`; this page has none.

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
<!-- ABLATION:END -->
