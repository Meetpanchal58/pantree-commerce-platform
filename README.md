# Pantree — E-commerce Data, Storefront & Recommendations

Pantree is a simulated online fashion shop built end to end: a stateful data generator, a live storefront with
passwordless sign-in, and a hybrid recommender evaluated against baselines and a known-truth oracle. This README
leads with results; setup is under [How to run](#how-to-run).

<!-- RESULTS:START -->
## Results at a glance

*Generated from `recommendations/reports/evaluation_report.json` by `python -m recommendations.readme_summary`.*

- **Problem:** rank products for the home-page "Recommended for you" row, predicting the exact product bought among 10,000, using only data known one hour before the order.
- **Method:** 5 retrieval sources (implicit ALS, trending, recent activity, FAISS content similarity, co-purchase) → 11-feature LightGBM LambdaRank → segment-aware policy (popularity for first-time buyers). The evaluated policy is the code the storefront serves (`recommendations/policy.py`).
- **Evaluation:** 3-period temporal split, 7-day embargo, 5,647 held-out orders (of 5,831; see order counts below), paired-bootstrap 95% CIs.
- **Headline:** served policy NDCG@10 0.0371 vs 0.0239 for retrieval order: +0.0132 absolute (95% CI +0.0108 to +0.0156), +55% relative (95% CI +44% to +69%); 7.1× random. Within lists that contain a bought product (900 orders), NDCG@10 is 0.263.
- **Ceiling:** the served policy captures 82% of the achievable gain from random to an oracle that knows each customer's true taste (95% CI 72% to 92%).
- **Bottleneck:** candidate lists contain a bought product for 15.4% of orders; the top 100 by true taste would contain one for 39.6%. Next step: better retrieval (taste-aware candidate generation, e.g. a two-tower model), not more ranking features.
- **Caveat:** synthetic data with known ground truth; online impression/click logging and an A/B split are built, but there is no real traffic yet.
<!-- RESULTS:END -->

### [Live Demo](https://pantree-storefront.onrender.com/login)

Hosted on Render's free tier; the first request after inactivity may take 30-60 seconds.

---

## Recommendations: method

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
| **Recommended for you** (homepage) | Runs the shared policy in `recommendations/policy.py`, the same code the offline evaluation scores. Five sources: seeded implicit ALS (views < cart additions < order quantity), products similar to the anchor (the latest purchase, else the latest viewed product), products bought in the same orders as the anchor, trending products (last 30 days of views, cart additions and wishlists), and popular items of the same type and audience as the customer's recent products. Recent signed-in views and purchases from SQLite are merged with warehouse history. Already-purchased and unavailable (inactive or out-of-stock) products are dropped; up to 20 come from each source, then the list is filled to 100 by score. Customers with no purchases see the list ordered by popularity (the UI labels it “Popular while we learn your taste”), a rule adopted when an earlier evaluation found popularity beat the ranker for first purchases (in the current test period the ranker alone scores higher for first-time buyers; the rule will be re-chosen on the validation period, not changed on test); everyone else gets a 50/50 blend of retrieval score and the LightGBM ranker. While activity data is still loading at start-up the row shows highly rated in-stock products. |
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
  items like the ones they looked at). Shoppers who have not bought anything yet see the most
  popular of those first; everyone else gets them ordered by the ranking model.
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

## Recommendations: evaluation

`python -m recommendations.evaluate` answers one question for every real order in the held-out
final period: **given everything known one hour before the order, does the system rank the
products the customer actually bought near the top?** That is the job of the home-page
"Recommended for you" row.

- **Three time periods, no peeking.** ALS and co-purchases learn only from data before each
  period's cut-off, with a 7-day gap; trending, recent activity, popularity and stock are taken as
  of each query time. The ranker is
  trained on candidate lists for orders in the middle period (B) and everything is scored on the
  final period (C).
- **Same lists, different orderings.** For each order a candidate list of up to 100 products is
  built (20 per source, then filled by score; already-bought and then-unavailable products
  excluded) by the same code the storefront serves. Random order,
  popularity, retrieval score (production without a ranker), the
  11-feature ranker, the production 50/50 blend and a segment-aware policy all rank the *same*
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
*Generated from `recommendations/reports/evaluation_report.json`; full detail in [`evaluation_report.md`](recommendations/reports/evaluation_report.md).*

End-to-end NDCG@10 (every held-out order with a new product; a bought product missing from the candidate list counts as a miss), and the share of the random → oracle gap each ordering captures:

| Ordering | All (5,647) | First-time buyers (1,521) | Repeat buyers (4,126) | Ranking-only, all | Share of achievable gain |
|---|---|---|---|---|---|
| Random order | 0.0052 | 0.0042 | 0.0056 | 0.039 | – |
| Popularity | 0.0166 | 0.0228 | 0.0142 | 0.116 | 29% (23% to 36%) |
| Retrieval score (previous production) | 0.0239 | 0.0104 | 0.0289 | 0.169 | 48% (40% to 57%) |
| New ranker (11 features) | 0.0390 | 0.0259 | 0.0438 | 0.278 | 87% (77% to 97%) |
| Production blend (50/50) | 0.0369 | 0.0221 | 0.0424 | 0.263 | 82% (72% to 92%) |
| **Segment-aware (served)** | 0.0371 | 0.0228 | 0.0424 | 0.263 | 82% (72% to 92%) |
| Oracle (true taste, not deployable) | 0.0440 | 0.0380 | 0.0462 | 0.325 | – |

Paired bootstrap over orders (end-to-end NDCG@10):

- new ranker vs retrieval: +0.0151 (95% CI +0.0125 to +0.0178, significant)
- new ranker vs popularity: +0.0224 (95% CI +0.0193 to +0.0257, significant)
- new ranker vs random: +0.0337 (95% CI +0.0298 to +0.0376, significant)
- segment aware vs retrieval: +0.0132 (95% CI +0.0108 to +0.0156, significant)
- segment aware vs popularity: +0.0206 (95% CI +0.0177 to +0.0236, significant)
- segment aware vs production blend: +0.0002 (95% CI -0.0011 to +0.0016, not significant)

Retrieval (is a bought product in the candidate list at all?). Per-source figures use each source's full list; the combined candidate list keeps only 20 per source, which is why one source alone can exceed the combined figure (and why keeping more ALS candidates is a cheap retrieval improvement to try):

- collaborative: 14.4% of test orders
- trending: 6.3% of test orders
- recent_activity: 6.2% of test orders
- content: 4.5% of test orders
- fbt: 2.6% of test orders
- all sources combined (candidate list of up to 100): 15.4%; **retrieval ceiling** (top 100 of all eligible products by true taste): 39.6%

Order counts: 5,831 paid orders in the test period. 25 came from customers with no earlier activity (cold start); they stay in and are scored with documented default features. 184 only re-bought earlier products (excluded from candidates), leaving 5,647 for the end-to-end tables. 900 had a bought product in the list; ranking-only tables also need at least 10 candidates and one not-bought candidate (0 + 0 dropped), leaving 900.

Ranker features with the most gain: `n_sources` (22%), `retrieval_score` (15%), `log_price_ratio` (13%), `source_id` (13%), `log_product_popularity` (12%).

ALS tuning (`python -m recommendations.tune_als`, chosen on the validation period with a 7-day embargo, checked once on the test period): 32 factors, regularisation 0.1, weights (view, cart, purchase) (1, 3, 5); held-out Recall@20 0.0900 with the previous serving settings → 0.0900. Serving and evaluation read these settings from `als_tuning.json`. Full grid: [`als_tuning.md`](recommendations/reports/als_tuning.md).
<!-- EVAL:END -->

To reproduce: generate with `PANTREE_TODAY=2026-06-30`, then run `python -m recommendations.tune_als`,
`python -m recommendations.evaluate` and `python -m recommendations.readme_summary` (which rewrites the
generated blocks in this README). Absolute numbers are low because the task is predicting
the exact product out of 10,000; compare orderings with each other, not with published benchmarks.

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

To extend an initialized warehouse and explicitly grow the customer population, set
`PANTREE_TODAY` to the desired end date and `PANTREE_INCREMENT_TARGET_CUSTOMERS` to the desired
final customer count before running `python generate_data.py --mode increment`. Increment
cohorts are distributed across the appended months; the target does not alter existing
customers or products.

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

## Validation
- `validators/validate_dataset.py` — cross-table integrity, temporal order, funnel ordering,
  reconciliation, no-sale-before-launch, and churn-leakage checks, including a point-in-time check
  that every churn feature row counts only orders before its `snapshot_date` (added after this
  check would have caught a real leak; see Data analysis).
- `validators/preflight_checks.py` — the above **plus** a churn model-readiness smoke test
  (base rate + AUC band + top-decile lift), so labels can't silently drift to an unmodelable
  rate. It uses a random split as a quick smoke test; the notebook's churn model uses a temporal
  split with an embargo.
- The evaluation's oracle depends on `generators/taste.py`, the simulator's own scoring function;
  regenerating with the default seed reproduces `transactions.parquet` byte for byte.

## Data analysis

`analysis/pantree_insights.ipynb` explores the generated warehouse with charts; rebuild it with
`pip install -r requirements-analysis.txt` then `python analysis/build_notebook.py`. Findings on the default dataset:

<!-- INSIGHTS:START -->
*Generated from `analysis/insights_summary.json`, written when the notebook runs.*

- **Conversion:** 3.2% of 821,728 sessions end in a purchase; 10% of cart adds are bought.
- **Cash on delivery:** 6.4% of COD parcels come back undelivered (RTO) vs 2.6% for prepaid (2.5×).
- **Returns:** 17.0% of delivered items are returned (footwear 19.7%, accessories 12.5%); size and fit problems explain 40% of returns.
- **Channels:** a Referral customer is worth ₹4,425 on average vs ₹1,534 for Social Ads (2.9×).
- **Repeat buyers:** 56% of buyers order again and bring 83% of revenue.
- **Price change:** after 1 Feb 2025 customers paid +8.6% relative to list price.
- **Festivals:** Diwali months show Oct 2025 +83%, Nov 2025 +75% revenue vs the neighbouring normal months (festival months overall +39%); comparing with neighbours controls for the shop's growth.
- **Churn model:** on the latest held-out snapshots (temporal split, 90-day embargo), logistic regression (chosen on validation) reaches ROC-AUC 0.605 (95% CI 0.590–0.620) vs 0.545 for a days-since-last-order rule; targeting the top 10% catches 12% of churners at 1.2× the base rate (56%). Weak, but leakage-free.
- **Leak found and fixed:** the generator's monthly churn features used to count the whole snapshot month, i.e. orders after `snapshot_date` (26,729 of 567,540 rows). Trained on them, gradient boosting reported test AUC 0.751 vs 0.580 without the leak. Features are now built strictly before the snapshot (0 leaky rows now), and `validators/validate_dataset.py` fails if that ever regresses.
<!-- INSIGHTS:END -->

The generated warehouse can also support RFM, customer lifetime value, churn (labels included)
and marketing-mix projects.

## Data and architecture

### What is in the generated practice data?

| Table or files | In everyday language |
|---|---|
| `customer_master`, `product_master`, `seller_master`, `agent_master` | Basic details about shoppers, products, sellers, and support staff. |
| `orders`, `transactions` | Orders and the products inside each order. |
| `clickstream` | The generated activity diary: visits, searches, product views, carts, and purchases. |
| `inventory` | How many units were available for each product over time. |
| `returns`, `product_reviews`, `support_contacts` | Example returns, customer reviews, and support requests. |
| `marketing_spend`, `mmm_weekly_target` | Example marketing costs and weekly sales totals. |
| `product_affinity` | Pairs of products often bought or viewed together. |
| `customer_monthly_features`, `customer_churn_labels` | Monthly customer summaries and whether a customer later stopped buying. |

`data/metrics.json` explains how the project calculates its main measurements.

### Customer data and personal information

Customer data is split into two SQLite tables joined by `customer_id`:

| Table | Holds | Copied to DuckDB analytics? |
|---|---|---|
| `customers` (customer dimension) | signup date, city/state/region, gender, age range, acquisition channel, Prime status, email/SMS opt-in, account status, terms acceptance time | Yes |
| `customer_pii` (personal data) | first and last name, verified email, mobile, saved delivery address | **No** |

Analysis and recommendation code only needs the dimension, so names, emails, phone numbers and
addresses never leave the live database. Older databases are migrated automatically on start-up:
personal columns are moved out of `customers` (and emails out of the legacy `auth` table) into
`customer_pii`. Telling a visitor that an email "already exists" or "has no account" reveals
whether it is registered; the OTP rate limits (applied before that check) throttle bulk probing.

### Where shopping activity is saved

The shop database, `webapp\pantree_app.db`, is the main live database. The table below describes
what Pantree currently records. It records these meaningful shop actions, not every mouse movement
or time spent on a page.

| What the shopper does | What Pantree saves |
|---|---|
| Creates an account | Non-personal profile in `customers` (signup date, city/state/region, gender, age range, acquisition channel, marketing opt-ins, terms acceptance time); personal details in `customer_pii` (name, verified email, mobile); the sign-in link in `supertokens_customers`. Details wait in `pending_signups` until the code is verified (expires after 30 minutes). |
| Signs in successfully | The sign-in link in `supertokens_customers` (first time on a device identity), a local session in `sessions`, and an `app_open` event. SuperTokens also stores the sign-in identity and session. |
| Places an order | The delivery address is saved to `customer_pii` for the next checkout. |
| Opens the home page | A `page_view` event in `clickstream`. |
| Opens a category or browse page | A `category_view` event in `clickstream`. Filter and sort changes are not currently saved as separate events. |
| Searches | A `search` event and the search text in `clickstream`. |
| Opens a product | A `pdp_view` event with its product ID in `clickstream`. |
| Adds or removes a cart item, or changes its quantity | The current cart in `cart`, plus an `add_to_cart`, `remove_from_cart`, or `cart_quantity_update` event. |
| Adds or removes a wishlist item | The current choice in `wishlist`, plus a `wishlist_add` or `wishlist_remove` event. |
| Opens the cart or starts checkout | A `page_view` or `checkout_start` event in `clickstream`. |
| Completes the demo checkout | An `orders` row, one `transactions` row per product, lower stock in `inventory`, a `SALE` row in `inventory_movements`, and `payment_attempt`/`purchase` events in `clickstream`. The cart is then cleared. |
| Marks an order delivered | The order and its transactions are updated, and an `order_delivered` event is recorded in `clickstream`. |
| Cancels a placed order | The order is marked cancelled/refunded, transaction revenue is removed, stock is restored, a `CANCELLATION` movement is saved, and an `order_cancelled` event is recorded. |
| Requests a return | The transaction/order status changes, a row is added to `returns`, stock is returned to `inventory`, an `inventory_movements` row is added, and a `return_completed` event is recorded. |
| Requests a replacement | The original transaction status changes, stock movements are recorded, and a `replacement_completed` event is added. |

`clickstream` is Pantree's activity diary: one row per recorded event, with a customer, session,
time, action, and (when relevant) product or search text. The current cart and wishlist tables
store the shopper's latest selections; they are not a full history of every change.
To look at the records yourself, open `webapp\pantree_app.db` with a SQLite viewer and browse
the table named in the right-hand column above. Search phrases are saved in `clickstream`, so
avoid entering private information into the shop's search box.

Pantree also copies selected live changes—customers, events, orders, transactions, inventory,
returns, and stock movements—to `webapp\pantree_analytics.duckdb`. That copy is for reports and
analysis. SQLite remains the live shop's source of truth. The generated Parquet files in `data`
are the starting practice dataset; they are not rewritten every time someone uses the shop.

Recommendations are logged separately so they can be measured (see
[Measuring recommendations on the live site](#measuring-recommendations-on-the-live-site)):
every served product goes to `recommendation_impressions`, it is marked seen once at least half
of the card is on screen, and opening it adds a row to `recommendation_clicks`.

### Architecture

```
                         ┌─────────────────────────────┐
   product_details.csv → │   Stateful Simulator         │  python generate_data.py
   (real fashion seed)   │   (initialize / increment)   │
                         └──────────────┬──────────────┘
                                        │ writes
                                        ▼
                         ┌─────────────────────────────┐
                         │   Warehouse (Parquet)        │  masters, orders, transactions,
                         │   dimensional + fact tables  │  clickstream, inventory, returns,
                         └──────┬───────────────┬───────┘  reviews, support, marketing, MMM,
                                │               │          monthly features, churn labels
                     seeds ▼    │               │  reads ▼
             ┌──────────────────┐               └────────────────────────────┐
             │  Storefront app  │  writes live                                │
             │  FastAPI + SQLite│ ───────────► SQLite (OLTP, source of truth) │
             │  (login→checkout)│              analytics_sync.py               │
             └──────────────────┘ ───────────► DuckDB (OLAP mirror) ──────────┘ → ML / LLM projects
```

- **SQLite** is the transactional store the website writes to (carts, orders, clickstream, inventory).
- **DuckDB** mirrors it for fast analytics and model training (`ATTACH` the SQLite file, or query the partitioned Parquet facts).

### A few words you may see

| Word | In everyday language |
|---|---|
| Dataset / warehouse | The collection of generated tables about customers, products, orders, and activity. |
| Parquet | A compact file format used for the generated tables in `data`. |
| SQLite | The local database that the live shop reads and updates. |
| DuckDB | A separate local database used to explore and analyze a copy of selected shop data. |
| OTP | A one-time sign-in code sent to your email or phone. |
| Cold start | A shopper or product with too little history to personalize recommendations yet. |
| Candidate | A product considered as a possible recommendation before filtering and sorting. |
| SMTP | The standard way an app connects to an email service to send messages. |
| SuperTokens | The sign-in service that creates/verifies codes and remembers sign-in sessions. |
| FastAPI | The Python web framework used to run the shop. You do not need to use it directly. |
| OLTP | A technical name for the database that handles everyday shop activity. |
| OLAP | A technical name for the separate copy used to study or report on activity. |
| ALS | A way to find products based on patterns in what shoppers view, add, or buy. |
| FAISS | A tool that quickly finds products with descriptions/details similar to another product. |

For a deeper explanation of the generator, simulator settings, and output files, see
[Data generation and refresh](#refreshing-data-over-time-incremental) and
[the data folder guide](data/README.md). For deployment on a public server, use the
[production setup](docs/deployment.md#production).

## How to run

### What you need

- Windows, macOS, or Linux; the commands below are for Windows PowerShell.
- Python 3.11 or newer and an internet connection to install Python packages.
- Docker Desktop running, to start the sign-in service.
- To receive a real sign-in code: an email sending account (SMTP), or an Android phone running
  OpenSMS on the same trusted network. You can set up one or both.

### 1. Install Pantree

Open PowerShell in the folder containing this README, then run:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

The recommendation packages include machine-learning libraries and can take a while to install.
You only need to install them once for this environment.

### 2. Create the practice dataset

From the same project folder, run:

```powershell
.\.venv\Scripts\python.exe generate_data.py --mode initialize
.\.venv\Scripts\python.exe validators\preflight_checks.py
```

The first command creates the shop catalog, customers, shopping activity, orders, stock history,
and other practice data in the `data` folder. The second checks that the generated files agree
with each other. Read [the data folder guide](data/README.md) for what is created.

**Important:** `initialize` replaces the previously generated Parquet files and simulator state
in `data`. Do not run it if you need to keep an existing generated dataset. To advance an existing
dataset without starting over, use:

```powershell
.\.venv\Scripts\python.exe generate_data.py --mode increment
```

### 3. Create the storefront database

Run this once, after generating the dataset:

```powershell
.\.venv\Scripts\python.exe webapp\seed_db.py
```

This loads products, customers, and starting stock into the live shop database at
`webapp\pantree_app.db`. **Seeding replaces that database.** If it already contains shopping
activity you want to keep, stop the app and back it up before running the seeder.

### 4. Set up email or phone-code sign-in

Copy the example settings file and open it:

```powershell
Copy-Item .env.example .env
notepad .env
```

In `.env`, provide values for the SuperTokens Core and its database:

- `SUPERTOKENS_DB_USER` and `SUPERTOKENS_DB_NAME` already have example values.
- Set a private, strong value for `SUPERTOKENS_DB_PASSWORD`.
- Set a private API key for `SUPERTOKENS_API_KEY`.

Use a different random value for each password/key. You can make one with:

```powershell
py -c "import secrets; print(secrets.token_hex(32))"
```

Copy the result into the matching field in `.env`; do not post it in chat or commit it to GitHub.

For a local test, configure at least one way to receive codes:

- **Email:** fill in `SMTP_HOST`, `SMTP_FROM_EMAIL`, `SMTP_USERNAME`, and `SMTP_PASSWORD`.
  Gmail needs an App Password; Resend is another supported SMTP option. Keep credentials private.
- **SMS:** set `OPENSMS_API_URL` to your Android phone's trusted LAN address ending in `/sms/`,
  and set `OPENSMS_API_KEY` to the key from OpenSMS. Do not expose the phone's endpoint to the
  public internet.

The supplied Docker production setup requires both email and SMS settings. On Render, you can
use email OTP alone: configure all four SMTP values above, and the sign-in page will offer email
only when OpenSMS is not configured. A phone's local Wi-Fi address is not reachable from Render;
do not expose the OpenSMS API or its key publicly. For production, also set
`PANTREE_RATE_LIMIT_SECRET` to a private random value of at least 32 characters. Do not put real
passwords or keys in GitHub. On a 512 MB instance such as Render's free tier, set
`PANTREE_HEAVY_RECOMMENDATIONS=0` so the app skips the collaborative-filtering and
sentence-transformer models, which otherwise exceed the memory limit; recommendations fall back to
lighter sources. `render.yaml` installs `requirements-render.txt`, which leaves out those
models' packages (including PyTorch) for faster builds.
See the detailed
[email and phone sign-in setup](docs/deployment.md#passwordless-authentication) if you need provider-specific help.

Make sure Docker Desktop is running. Start the sign-in service from the project folder:

```powershell
docker compose --env-file .env -f docker-compose.auth.yml up -d
```

Wait until the SuperTokens and PostgreSQL services are healthy. They store and verify codes and
sessions; the shop itself still stores profiles and shopping activity in SQLite. After a
successful code check, Pantree remembers the sign-in in your browser. A shopper can only use
their own cart and orders; there are no separate administrator accounts in this demo.

To check that the services have started, run:

```powershell
docker compose --env-file .env -f docker-compose.auth.yml ps
```

### 5. Start the shop

From the project folder, run:

```powershell
.\.venv\Scripts\python.exe -m uvicorn webapp.app:app --reload --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/login`. You can also launch from inside the `webapp` folder. In
`app:app`, the first `app` means “the file `app.py` here” and the second means “the FastAPI
application inside it”:

```powershell
Set-Location webapp
..\.venv\Scripts\python.exe -m uvicorn app:app --reload
```

Or activate `.venv` first and run `python -m uvicorn app:app --reload`.

### 6. Try the shop

1. New shoppers open **Create account**, fill in their details (name, email, optional mobile, city,
   optional gender, age range and "how did you hear about us", marketing choices, and terms) and
   verify the code sent to their email. The account is created only after the code is verified.
   Signing up again with the same email or mobile shows "An account with this email already exists".
2. Returning shoppers use **Sign in** with their email or mobile and the code they receive. Sign-in
   never creates an account; an unknown email shows "No account found. Create an account first".
3. Browse departments, search, open a product, add products to your cart or wishlist, and try a
   checkout.
4. Visit **Your Orders** to mark the demo order delivered or cancel it. After marking it
   delivered, you can also try a return or replacement.
5. Use **Sign out** when you are finished.

Checkout is a demonstration: it does not charge a real card. It creates a paid demo order in
**Placed** status. In **Your Orders**, mark it delivered to try a return or replacement, or cancel
it to see the payment marked refunded and the items returned to stock.

## Short launch command (after setup)

If you have already generated the data, seeded the shop database, configured sign-in, and started
Docker, launch the shop from the project folder with:

```powershell
.\.venv\Scripts\python.exe -m uvicorn webapp.app:app --reload --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/login`. For the complete first-time steps, use
[Start here: setup and use Pantree](#how-to-run) above.

### Refreshing data over time (incremental)

This advances the generated practice data; it does not update the live shop database. The live
shop's seeder replaces its database, so do not run it on a database with activity you want to
keep. There is not currently a safe command that merges newly generated customers and products
into an already-used shop database.

```bash
.\.venv\Scripts\python.exe generate_data.py --mode increment
```

## Change the dataset size (optional)

The generator normally creates about 30,000 starting customers and 10,000 products. If you want
a smaller or larger practice dataset, set these values in PowerShell before running
`initialize`. Starting over replaces the generated files, so only do this when that is okay.

```powershell
$env:PANTREE_CUSTOMERS = "30000"
$env:PANTREE_PRODUCTS = "10000"
.\.venv\Scripts\python.exe generate_data.py --mode initialize
```
Change the two numbers to the starting customer and product counts you want. The default end
date is today, and the random seed is fixed by default so the same settings produce repeatable
practice data.

Sign-in internals (OTP flow, rate limits, sessions), production Docker Compose, backups and the
pre-launch checklist are in [docs/deployment.md](docs/deployment.md).

## Honest notes
- This is **realistic synthetic data** with deliberately engineered, documented signal — it is
  validated and leakage-checked, but it is simulated, not production data.
- Browsing in the simulator follows each customer's hidden taste with 20% exploration, so views
  and cart adds carry preference signal; the strength of that signal is a modelling choice.
- The headline evaluation is an offline replay on historical orders. The site now logs
  recommendation impressions and clicks and supports an A/B split, but those numbers need real
  traffic, which the demo does not have yet.
- Product images source from a real fashion catalogue (`generators/product_details.csv`);
  for the live site, images are served locally so they always load.

## License
MIT — see [LICENSE](LICENSE).
