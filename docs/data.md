# Data

Where the shop's data comes from, how it is checked, and where the storefront saves live activity.

## Where the data comes from

The shop's history (22 months from 1 January 2025: catalogue, customers, browsing, impressions,
orders, stock, prices, sale events, marketing, reviews, support) comes from the
[ecommerce-simulator](https://github.com/Meetpanchal58/ecommerce-simulator) package. Its nightly
GitHub Action adds the new day and publishes the data (GitHub release `data-latest`, mirrored on
Hugging Face `Meet58/ecommerce-simulator`). `python prepare_data.py [--size small|medium|full|large]`
downloads it and converts it:

| Pantree table (`data/`) | Built from (`data/source/`) |
|---|---|
| `product_master` | `product_master` (style level) + current price from `price_history` + published `product_reviews` (products nobody reviewed have no rating) |
| `customer_master` | `customers` (the PII stays in `customer_pii`, which Pantree does not use) |
| `transactions`, `orders` | `sales`, one row per order line; exchange replacement lines are left out |
| `clickstream` | `clickstream`, product interactions only (views, cart, wishlist, purchases) |
| `inventory` | `inventory_history` (per size, on days stock changed) → closing stock per product per day; the last day equals `inventory_live` |

**What counts as a purchase.** The recommender counts an order line unless its status is RTO,
cancelled (CIC), lost or undelivered, and only when payment succeeded. The package's statuses map
onto that: delivered and returned items are purchases (a returned item still shows interest);
cancellations, returns to origin, lost and undelivered parcels are not. COD orders still on their
way have no payment yet and do not count.

## Checks

- The package checks every published day: unique keys and references, the order lifecycle in
  time order, nothing before sign-up or launch, money adding up, prices matching `price_history`,
  stock flows balancing, receipts matching purchase orders, and more (16 checks).
- `tests/test_ground_truth_guard.py`: the recommender and the web app never read the simulator's
  ground truth (`_ground_truth/`, the taste model, the complement table, `product_affinity`); only
  the evaluation's oracle reads the true taste.
- The notebook checks that the package's ready-made churn features use only data from before each
  snapshot (0 leaky rows) before using them.

## Live data

### Customer data and personal information

Customer data is split into two live-database tables joined by `customer_id`:

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

The live database (Postgres when `DATABASE_URL` is set, otherwise `webapp\pantree_app.db`) is the shop's source of truth. The table below describes
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
To look at the records yourself, open the live database (a SQLite viewer locally, or the Neon SQL editor
when hosted) and browse the table named in the right-hand column above. Search phrases are saved in `clickstream`, so
avoid entering private information into the shop's search box.

Pantree also copies selected live changes—customers, events, orders, transactions, inventory,
returns, and stock movements—to `webapp\pantree_analytics.duckdb`. That copy is for reports and
analysis. The live database remains the shop's source of truth. The Parquet files in `data` are the
shop's simulated history; they are not rewritten when someone uses the shop.

Recommendations are logged separately so they can be measured (see
[Measuring recommendations on the live site](recommender.md#measuring-recommendations-on-the-live-site)):
every served product goes to `recommendation_impressions`, it is marked seen once at least half
of the card is on screen, and opening it adds a row to `recommendation_clicks`.

### Architecture

```
   ecommerce-simulator (nightly)        python prepare_data.py
   GitHub release / Hugging Face  ───►  data/source  ──►  data/*.parquet
                                              │                 │
                       analysis notebook ◄────┘                 │ trains / serves
                                                                ▼
   visitors ──► Storefront (FastAPI) ──► live database ──► recommender (ALS, content, FBT,
                   seed_db.py ▲          Postgres / SQLite   trending → LightGBM ranker)
                              └── catalogue, customers,  │
                                  stock from data/       └──► DuckDB analytics mirror
```

## Words you may see

| Word | Meaning |
|---|---|
| Parquet | A compact file format used for the tables in `data`. |
| Postgres / SQLite | The live database: Postgres (Neon) when hosted, a SQLite file locally. |
| DuckDB | A separate copy of live data for analysis. |
| OTP | A one-time sign-in code sent to your email or phone. |
| Cold start | A shopper or product with too little history to personalise recommendations yet. |
| Candidate | A product considered as a possible recommendation before ranking. |
| ALS / FAISS | Collaborative filtering on views, carts and purchases / fast search for similar product descriptions. |

## Data analysis

`analysis/insights.ipynb` explores the shop's data with charts; re-run it with
`pip install -r requirements-analysis.txt` then `jupyter execute --inplace analysis/insights.ipynb`. The findings
(generated from `recommendations/reports/insights_summary.json`) are listed in the
[README](../README.md#analytics-and-churn).
