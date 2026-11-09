# Data

The generated practice warehouse, its validation, and where the storefront saves activity.

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
[Measuring recommendations on the live site](recommender.md#measuring-recommendations-on-the-live-site)):
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
`pip install -r requirements-analysis.txt` then `python analysis/build_notebook.py`.

The findings (generated from `analysis/insights_summary.json`) are listed in the
[README](../README.md#analytics-and-churn).

The generated warehouse can also support RFM, customer lifetime value, churn (labels included)
and marketing-mix projects.
