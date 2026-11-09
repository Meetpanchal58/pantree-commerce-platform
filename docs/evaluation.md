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
