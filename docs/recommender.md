# Recommender

How the storefront's recommendation rows are built, how they are measured on the live site, and how
the recommender is evaluated offline (method, full results, and why choices that look like leaks are not).

## Method

The recommendation service reads the existing Parquet files without regenerating them. It is
available on the storefront home page and at `/api/recommendations/homepage`,
`/api/recommendations/product`, and `/api/recommendations/cart` (all require a signed-in session).
The homepage displays “Recommended for you”
and “Trending products”; product detail pages display “Frequently bought together” and “Similar
products”. Candidate retrieval, filtering, and optional ranking are performed by
`recommendations/serve.py`. The API returns product details plus `score`, `source`, and `reason`
so each result can be traced to its retrieval signal; the storefront also uses source metadata
to adjust the related-products heading.

### What each storefront row means

| Storefront row | Selection logic |
|---|---|
| **Recommended for you** (homepage) | Runs the shared policy in `recommendations/policy.py`, the same code the offline evaluation scores. Five sources: seeded implicit ALS (views < cart additions < order quantity), products similar to the anchor (the latest purchase, else the latest viewed product), products bought in the same orders as the anchor, trending products (last 30 days of views, cart additions and wishlists), and popular items of the same type and audience as the customer's recent products. Recent signed-in views and purchases from SQLite are merged with warehouse history. Already-purchased and unavailable (inactive or out-of-stock) products are dropped; each source fills its quota (set in `recommendations/reports/policy_config.json`, chosen on the validation period), then the list is filled to the pool size by score. The list is ordered by a blend of the LightGBM ranker score and the retrieval score; the blend weight and whether customers with no purchases get popularity order instead are also chosen on the validation period (the current values are listed under the results). When popularity order is used, the UI labels the row “Popular while we learn your taste”. While activity data is still loading at start-up the row shows highly rated in-stock products. |
| **Frequently bought together** (product page) | Tries evidence in order: products that appeared in the same realized orders as the anchor; products viewed in the same browsing sessions; product types that shoppers buy together more often than chance (learned from orders); then other highly rated products from the same department. It moves to the next source if the earlier source has no eligible results. Candidates must be active, in stock, a different product type from the anchor, and audience-compatible; the anchor and already-purchased products are removed. Duplicate items across the two displayed product-page rows are removed. The heading changes when results come solely from co-view, learned product-type, or department-fallback evidence. |
| **Similar products** (product page) | Searches a FAISS index of Sentence Transformer embeddings built from product name, description, and attributes. Results are filtered to the same product type and a compatible gender/audience (including universal categories), with inactive, out-of-stock, purchased, anchor, and duplicate-row products removed. If there are too few embedding matches, it fills from the same type/audience using rating, review count, and product ID as deterministic tie-breaks. |

These are retrieval rules, not a guarantee of purchase or user-specific preference. Source scores are
normalized retrieval scores and may be blended with the optional LightGBM ranker; they should be
used to compare/rank candidates for this service, not interpreted as purchase probabilities.
Trending is a separate homepage row: it counts warehouse product interactions (views, cart
additions, and wishlists) over the latest 30 days in the dataset (the same trending source the
personalised row uses), and falls back to highly rated in-stock catalog products while those
counts are loading. All rows filter against the current SQLite stock before returning results.

The recommendation API accepts the `homepage`, `product`, and `cart` surfaces. Product-page
requests use `product_id` and the `similar` or `complementary` intent; homepage requests use the
`personalized` or `trending` intent. The cart surface uses the customer's current cart as context.
The cart endpoint is available, but the current cart template does not render a recommendation
row.
For example, the storefront calls
`GET /api/recommendations/homepage?intent=personalized&limit=30` and
`GET /api/recommendations/product?product_id=<id>&intent=similar&limit=100`; requests without a
valid storefront session receive `401`. Each response item includes `product_id`, `name`, `score`,
`source`, and `reason`, along with the product and live cart/stock details needed to render a card.
The async route runs recommendation work in a thread pool. Expensive ALS and content-index setup
is warmed outside the request path; the content index is persisted under the ignored
`recommendations/.cache/` directory and rebuilt when the product catalog changes. Sentence
Transformers/FAISS, implicit ALS, and LightGBM are declared in `requirements.txt`; without
optional model dependencies, the corresponding source is unavailable and eligible fallback
sources keep recommendations usable.

For a clean, isolated install on Windows:

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m recommendations.evaluate
```

The recommendation dependencies in `requirements.txt` support seeded implicit ALS,
Sentence Transformers + FAISS, and a LightGBM ranker. The ranker is optional: until
`recommendations/ranker.txt` exists (written by the evaluation), the service sorts by fused
retrieval scores. Without the optional packages, those sources log a warning and
trending, recent activity, frequently-bought-together and deals still work.

### How the three recommendation rows work

- **Recommended for you (home page):** looks at what the signed-in shopper viewed, added to the
  cart, wishlisted, or bought, and gathers candidates from five sources (shopping patterns of
  similar customers, similar products, products bought together, trending products, and popular
  items like the ones they looked at), then orders them with the ranking model blended with
  each source's own score. How many candidates each source contributes, and how much weight the
  ranking model gets, were chosen by testing settings on an earlier period of data.
- **Frequently bought together (product page):** first looks for products purchased in the same
  orders as the current product. If there is not enough order evidence, it tries products viewed
  together, then product types shoppers often buy together, then other well-rated products from
  the department.
- **Similar products (product page):** compares the product's name, description, and details with
  other catalog items. It keeps items of the same product type and a compatible audience; if
  there are too few close matches, it fills the row with well-rated items of that type.

All three rows leave out unavailable products, the product already being viewed, and products
the customer has already bought where applicable. Recommendation scores are sorting signals, not
the probability that someone will buy. The recommendation API includes the reason and source
for each result. The current cards mainly show product details; the product-page heading may
also change when a fallback source is used. The cart recommendation API exists, but the current
cart page does not display a recommendation row.

### Measuring recommendations on the live site

The offline evaluation replays history; the live site also records how shoppers react:

| Table | One row per | Main columns |
|---|---|---|
| `recommendation_impressions` | product served in a recommendation row | request id, customer, surface/intent, A/B variant, product, position, source, score, served time, seen time |
| `recommendation_clicks` | recommended product opened | impression, request id, customer, product, position, click time |

- **Seen** means at least half of the card was on screen (`webapp/static/rec_tracking.js` uses an
  IntersectionObserver and batches the reports); served-but-never-visible cards don't count.
- **Clicks** come from recommendation links carrying `?rec=<request id>&pos=<position>`; links that
  don't match the customer and product are ignored, and each impression counts one click at most.
- **A/B test:** set `PANTREE_AB_TEST=ranker` to split customers 50/50 by a hash of their ID
  (stable per customer). `control` sees retrieval order without the ranker; `treatment` sees the ranker.
- **Report:** `python -m recommendations.online_metrics` writes
  `recommendations/reports/online_metrics.md` with served/seen/clicks, click-through rate with a
  95% Wilson interval, add-to-cart within 24 hours and purchase within 7 days of a click, per
  surface, source and variant, plus the A/B difference bootstrapped by customer (impressions from the
  same customer are not independent).

To get newer data, run `python prepare_data.py` again: it downloads the latest published days
(the package adds one every night).

### Recommendation code modules (for developers)

- `recommendations/candidates/collaborative.py` combines weighted views, carts, and realized
  purchases into a seeded implicit-ALS user-item model; unknown users return no ALS candidates.
- `recommendations/candidates/content.py` embeds product names, descriptions, and attributes,
  persists the FAISS index, and over-retrieves similar products for post-filtering.
- `recommendations/candidates/fbt.py` prioritizes same-order purchases, then same-session co-views,
  then product types bought together more often than chance, all learned from data before the cut-off.
- `recommendations/candidates/popularity.py` counts product activity as of any moment, for
  trending, recent-activity candidates and the ranker's popularity feature; it also ranks
  markdowns for deal candidates.
- `recommendations/policy.py` is the personalised policy shared by serving and evaluation.
- `recommendations/candidates/fusion.py` deduplicates source results while preserving the
  winning source tag and reason.
- `recommendations/ranker.py` builds the 11 point-in-time candidate features (shared by training
  and serving, so they cannot drift apart) and trains/scores the LightGBM LambdaRank model.
- `recommendations/serve.py` routes homepage, product, and cart requests, applies stock,
  category, gender, cart, and already-bought filters, then returns ranked explanations.
- `recommendations/evaluate.py` runs the three-period order-level evaluation described above,
  writes the reports and saves the serving ranker.
- `recommendations/tune_als.py` tunes ALS interaction weights and hyperparameters.

## Evaluation

How the recommender is evaluated offline, the full results, and why choices that look like leaks are not.
The headline numbers are summarised in the [README](../README.md#results-at-a-glance).

### Method

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

### Results (default dataset)

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

To reproduce: `python prepare_data.py` (full shop), then `python -m recommendations.tune_als`,
`python -m recommendations.tune_policy`, `python -m recommendations.evaluate` and
`python -m recommendations.readme_summary` (which rewrites the generated blocks here and in the README).
The data refreshes nightly, so a later download adds days and moves the numbers slightly. Absolute
numbers are low because the task is predicting the exact product out of thousands; compare orderings
with each other, not with published benchmarks.

### What counts as a purchase

One rule, defined once in `recommendations/candidates/common.py`
(`NON_PURCHASE_STATUSES`, `purchase_mask`, `purchase_sql`) and used by the
recommenders, the evaluation, the storefront and the online-metrics report:

- the payment succeeded, and
- the order line was not cancelled (`CANCELLED`, or `CIC`: cancelled by the seller), returned to sender
  (`RTO`), refunded, lost or undelivered.

A line the customer received and later returned (`CIR`) **is** a purchase: the
customer chose and paid for the product, which is what a recommender is trying to
predict. Lines whose payment failed are not purchases, whatever their status.

### Customers with no history (cold start)

Test orders from customers with no earlier activity stay in the evaluation. They
are scored by the same ranker with documented default features
(`recommendations/ranker.py`, `candidate_features`): activity and purchase counts
0, category affinity 0, price ratio 0 (neutral) and no anchor product. The
report counts them as "cold-start orders".

### Product rating (`avg_rating`) is a catalog feature

The ranker and the popularity fallbacks use the product's average rating from the
catalog (`product_master.avg_rating`). This is the value a shopper sees on the
product page, and a production ranker would read the same field at request time,
so it is a legitimate feature rather than a leak. It is a catalog snapshot, not a
point-in-time history, so early queries see a rating that includes later
reviews; ratings move slowly, and the effect is the same for every model compared.

Prices (`current_price`, `mrp`) used by the price-ratio and discount features are the
same kind of catalog snapshot: price history is not recorded, so every query sees
today's price.

### Nothing from the query hour reaches the evaluation

Each test order is queried one hour before it was placed. Customer history
(`History.viewed_before` / `bought_list`), product popularity
(`ActivityIndex.counts_before`), trending, recent activity and the co-purchase and
ALS training data all use events strictly before their time argument, and the
availability filter uses the last full day's inventory close before the query.
`tests/test_leakage.py` adds views, cart adds and purchases of the target product
inside that hour (and the target order itself) and checks that the candidate list
and every feature value stay identical, and that a restock or sell-out recorded
after the query does not change availability.

### The simulator's ground truth is off limits

The simulator (the ecommerce-simulator package) decides clicks and purchases from hidden
inputs: each customer's true taste (`ecommerce_sim.taste`,
`data/source/_ground_truth/customer_taste.parquet`, product quality in
`_ground_truth/product_quality.parquet`) and a hand-written table of complementary product
types (`ecommerce_sim.reference.COMPLEMENTS`). A model that reads them is given the answer.

- `recommendations/oracle.py` is the only module that reads the taste inputs, and
  only to build the oracle upper bound and the retrieval ceiling.
- Complementary products (`recommendations/candidates/fbt.py`) are learned from
  orders and browsing sessions before the cut-off: same-order pairs, same-session
  co-views, and product types bought together more often than chance (lift > 1,
  at least 5 orders).
- `tests/test_ground_truth_guard.py` fails if any file under `recommendations/` or
  `webapp/` imports anything from the package (only the oracle may import
  `ecommerce_sim.taste`), or names `_ground_truth`, `customer_taste`,
  `product_quality`, `COMPLEMENTS` or `product_affinity`.

### ALS settings

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

### What is evaluated is what is served

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

### Policy settings and the source ablation

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

#### Leave-one-source-out ablation (test period)

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
