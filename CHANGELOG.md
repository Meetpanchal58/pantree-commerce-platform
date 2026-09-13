# Changelog

## Data-quality & modelling fixes
- Churn labels scoped to the **active/retainable cohort** (purchased in trailing 90 days),
  with RFM columns retained — fixes an unmodelable ~90% base rate; added a churn
  model-readiness smoke test (base rate + leakage-safe AUC + top-decile lift) to preflight.
- Reviews tied to `transaction_id` so the review-after-delivery check joins on a unique key.
- RTO orders marked `Refunded`; refunds capped at realized line value; delivery/return
  timestamps censored to the simulation horizon; last-purchase state updates only on realized
  revenue; inventory units-sold based only on successful sales.
- Product demand uses `h3_category`; review-count Poisson lambda clamped; customer usage
  probabilities normalized; MD5-stable warehouse bucketing; unique marketing campaign IDs.

## Platform
- Stateful simulator with `initialize` and idempotent `increment` modes; monthly acquisition.
- Order-grain `orders` + line-grain `transactions`; customer lifecycle states.
- Partitioned analytics facts; SQLite (OLTP) → DuckDB (OLAP) mirror.
- Storefront with PBKDF2 auth and live inventory.
