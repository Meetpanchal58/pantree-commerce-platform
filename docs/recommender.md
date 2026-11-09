# Recommender

How the storefront's recommendation rows are built and how they are measured on the live site.
Evaluation method and results: [evaluation.md](evaluation.md).

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
