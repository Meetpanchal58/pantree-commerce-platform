# Roadmap: planned changes

Agreed plan for the next version of Pantree. Not built yet.

## 1. Use the `ecommerce-simulator` package for data

The data generator moves out of this repo into its own package:
[ecommerce-simulator](https://github.com/Meetpanchal58/ecommerce-simulator). It publishes the data nightly to
GitHub Releases and [Hugging Face](https://huggingface.co/datasets/Meet58/ecommerce-simulator)
(sizes small / full / large).

- Get data with `ecommerce-sim download --size large` instead of `generate_data.py`.
- Remove `generators/`, `validators/`, `generate_data.py`, the data-only tests
  (`test_data_correctness`, `test_order_status`, `test_point_in_time`) and the local 285 MB `data/`.
  That is about 2,200 lines (around 18% of the Python).
- Add a small adapter (around 100 lines) for the renamed tables:

| Old | New |
|---|---|
| `customer_master` | `customers` (+ `customer_pii`) |
| `transactions` + `orders` + `returns` | `sales` (one row per line, status updated in place) |
| `inventory` | `inventory_live` / `inventory_history` |
| `seller_master`, `agent_master` | `sellers`, `agents` |
| churn labels, affinity, MMM | `derived/` (`ecommerce-sim derive`) |
| `_state/customer_profiles` | `_ground_truth/customer_taste` |

## 2. Two kinds of data, two jobs

- **Package data (Parquet, about 22 months simulated)** is used for analytics, features and model
  training. The models learn product knowledge from it: similar items (ALS), bought together (FBT),
  trending (popularity), content similarity, and the ranker.
- **Website data (Postgres)** holds live activity from me, friends and interviewers. It is not
  used for training.
- **A recommendation** applies what the model learned from package data to the user's live
  activity in Postgres (recent views and cart), then filters by live stock and already bought, ranks,
  and logs impressions and clicks.

## 3. Move the live database from SQLite to Postgres on Neon

- Why: Render's disk is wiped on every redeploy, restart or sleep, so SQLite loses all test activity.
- Neon free plan: 1 GB storage, 100 compute hours a month, sleeps when idle, never pauses the
  project, and nothing is deleted at the limit. Expected use is about 100–300 MB, which is plenty.
- Setup (me): create a Neon project, copy the connection string, and add it in Render → Environment
  as `DATABASE_URL`. Never commit or paste it.
- Code: the connection in `webapp/core.py` uses Postgres when `DATABASE_URL` is set, SQLite
  otherwise (local runs and tests).
  - Port the 14 SQLite-only statements (`INSERT OR REPLACE`, `PRAGMA`, ...).
  - `seed_db.py` seeds only if the database is empty, so a restart never wipes activity.
  - The DuckDB analytics mirror reads from Postgres.

## 4. "Reset my recommendations"

On the profile page, with a confirmation popup. A user can only reset their own account.

- **Keep:** the account (`customers`, `customer_pii`), login credentials and sign-in history
  (`auth_events`).
- **Delete:** clickstream, impressions and recommendation logs, cart and wishlist, orders, returns
  and reviews (stock is put back for undelivered items), old sessions (the current login stays),
  their rows in the DuckDB mirror, and cached recommendations.
- Set a reset flag so serving ignores any trained model vector for that customer. For demo
  customers from the simulated dataset, whose history lives in Parquet, the flag makes the
  website treat them as new from the reset onward.
- Result: the user sees trending items like a new visitor, then gets personalised again from new clicks.
