# Architecture

## Overview
Pantree is a stateful e-commerce data platform. A seeded simulator generates a coherent,
story-driven warehouse; a FastAPI storefront reads/writes it live; SQLite is the
transactional (OLTP) store and DuckDB is the analytics (OLAP) mirror.

## Components
- **Simulator** (`generators/simulator.py`, entry `generate_data.py`)
  - `initialize` — builds the full seeded timeline through today (reproducible).
  - `increment` — appends new days/customers/orders through today; idempotent with
    crash-safe window cleanup. State lives in `data/_state/`.
- **Finalizer** (`generators/finalize_dataset.py`) — order-grain table, customer lifecycle
  (NEW/ACTIVATED/REPEAT/LOYAL/AT_RISK/CHURNED), monthly features, forward-looking churn
  labels (scoped to the active cohort), metric definitions, partitioned facts.
- **Storefront** (`webapp/`) — FastAPI + Jinja templates; SQLite source of truth;
  PBKDF2 auth; live inventory with a refresh-to-base control.
- **Analytics sync** (`webapp/analytics_sync.py`) — mirrors SQLite → DuckDB for OLAP.
- **Validation** (`validators/`) — cross-table integrity + churn model-readiness.

## Data model
- Dimensions: customer, product, seller, agent masters.
- Facts: `orders` (order grain) and `transactions` (order-line grain), `clickstream`,
  `inventory` (product×day), `returns`, `product_reviews`, `support_contacts`,
  `marketing_spend`, `mmm_weekly_target`, `product_affinity`.
- ML layers: `customer_monthly_features` (as-of snapshot) and `customer_churn_labels`
  (forward 90-day, active cohort, censored tail).

## Story baked into the data (why models find signal, not noise)
- Seasonal / festival demand multipliers and end-of-season sale spikes.
- Acquisition-channel-driven customer quality (referral/organic > social/display on CLV).
- Customer lifecycle and gradual, partly-exogenous churn (some loyal-looking customers still leave).
- Real fashion co-purchase affinity for Frequently-Bought-Together.
- Realized-revenue discipline: RTO/refunded orders are not counted as revenue or purchases.

## Design decisions
- **OLTP/OLAP split** — SQLite handles concurrent small writes from the website; DuckDB
  handles fast analytical reads and model training. Mirrors production (swap SQLite→Postgres,
  DuckDB→Redshift/Snowflake + dbt marts behind the same app at scale).
- **Seeded determinism** — reproducible datasets so model metrics are comparable across runs.
- **Leakage-safe churn** — features strictly as-of the snapshot; label from the forward window;
  cohort scoped to recently-active customers so the base rate is modelable.
- **Performance** — NumPy vectorization, cached indexes, bounded candidate scoring, vectorized
  inventory, date-partitioned Parquet analytics. Stateful order/inventory/lifecycle simulation
  is intentionally not naively parallelized (careless parallelism would change the story).

## Scaling to production (how this maps to real infra)
- Raw events → dbt marts → semantic metric layer → serving.
- Query pre-aggregated marts for common questions; partition-pruned push-down for ad-hoc
  heavy scans; async/sampled for genuinely large scans; promote recurring ad-hoc patterns to
  scheduled marts. The DuckDB-over-Parquet layer here mirrors the marts + fast-engine tier.
