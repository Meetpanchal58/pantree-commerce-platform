# Pantree — Synthetic E-commerce Data Platform & Storefront

A production-shaped, end-to-end e-commerce platform built to power real data-science and
GenAI projects: a **stateful data simulator** that generates a coherent, story-driven
warehouse, a **live Amazon-style storefront** (FastAPI) that reads and writes that data,
and a **SQLite (OLTP) → DuckDB (OLAP)** analytics split.

> One seeded simulation of one company, one timeline — every table reconciles. Built so that
> churn, CLTV, RFM, Marketing-Mix-Modelling, Voice-of-Customer, recommendation, and
> text-to-SQL projects all sit on the same validated foundation.

---

## What this demonstrates

- **Data engineering** — a stateful, incremental simulator (initialize + daily increment),
  dimensional modelling (order-grain + line-grain), partitioned analytics facts, an
  OLTP/OLAP architecture, and a strong cross-table + leakage validation suite.
- **Machine learning readiness** — leakage-safe, forward-window churn labels scoped to the
  active cohort; RFM/CLTV-ready customer aggregates; MMM weekly targets with adstock/
  saturation-friendly spend; a product-affinity graph for recommendations.
- **GenAI readiness** — support transcripts + reviews for Voice-of-Customer classification,
  and a schema knowledge base + verified NL→SQL eval set for an agentic text-to-SQL assistant.
- **Full-stack** — a working storefront (login, catalogue, PDP, cart, checkout, orders,
  live inventory) that generates real behavioural data into the warehouse.

## Architecture

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

## Quickstart

```bash
# 1. install
pip install -r requirements.txt

# 2. generate the full warehouse up to today (seeded, reproducible)
python generate_data.py --mode initialize

# 3. validate (cross-table integrity + churn model-readiness)
python validators/preflight_checks.py

# 4. run the storefront
cd webapp
python seed_db.py
python -m uvicorn app:app --reload      # http://127.0.0.1:8000/login
```

### Refreshing data over time (incremental)
```bash
python generate_data.py --mode increment   # appends new days/customers/orders through today
python webapp/analytics_sync.py            # sync the DuckDB analytics mirror
cd webapp && python seed_db.py             # refresh the app DB
```

## Scale (configurable via env vars)
```bash
PANTREE_CUSTOMERS=30000 PANTREE_PRODUCTS=10000 python generate_data.py --mode initialize
```
Defaults: 30,000 customers, 10,000 real fashion products (Apparel / Footwear / Accessories).

## The warehouse (key tables)

| Table | Grain | Powers |
|---|---|---|
| `customer_master`, `product_master`, `seller_master`, `agent_master` | dimension | all |
| `orders` / `transactions` | order / order-line | revenue, CLTV, MMM |
| `clickstream` | event | churn behaviour, funnel, recommendations |
| `inventory` | product × day | stock, stockouts |
| `returns`, `product_reviews`, `support_contacts` | event | Voice-of-Customer |
| `marketing_spend`, `mmm_weekly_target` | day×channel / week | Marketing-Mix-Modelling |
| `product_affinity` | product pair | Frequently-Bought-Together / recommendations |
| `customer_monthly_features` | customer × month | churn/CLTV features (as-of snapshot) |
| `customer_churn_labels` | customer × month | forward 90-day churn label (active cohort) |

`data/metrics.json` documents the canonical metric definitions.

## Validation
- `validators/validate_dataset.py` — cross-table integrity, temporal order, funnel ordering,
  reconciliation, no-sale-before-launch, and churn-leakage checks.
- `validators/preflight_checks.py` — the above **plus** a churn model-readiness smoke test
  (base rate + leakage-safe AUC + top-decile lift), so labels can't silently drift to an
  unmodelable rate.

## Projects built on this platform
See [`docs/PROJECTS.md`](docs/PROJECTS.md) for the per-project guide (data used, approach,
ML/LLM workflow, evaluation): RFM, CLTV, Churn, MMM, Voice-of-Customer, Recommendation +
Conversational Shopping Assistant, and the Agentic Analytics (text-to-SQL) assistant.

## Honest notes
- This is **realistic synthetic data** with deliberately engineered, documented signal — it is
  validated and leakage-checked, but it is simulated, not production data.
- Product images source from a real fashion catalogue (`generators/product_details.csv`);
  for the live site, images are served locally so they always load.

## License
MIT — see [LICENSE](LICENSE).
