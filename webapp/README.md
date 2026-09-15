<div align="center">

# 🌿 Pantree — E-commerce Data Platform & Storefront

**A production-shaped, end-to-end e-commerce platform: a stateful data simulator, a live Amazon-style storefront, and an OLTP → OLAP analytics stack — built to power real data-science and GenAI projects.**

[![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![DuckDB](https://img.shields.io/badge/DuckDB-FFF000?logo=duckdb&logoColor=black)](https://duckdb.org/)
[![SQLite](https://img.shields.io/badge/SQLite-003B57?logo=sqlite&logoColor=white)](https://www.sqlite.org/)
[![Pandas](https://img.shields.io/badge/Pandas-150458?logo=pandas&logoColor=white)](https://pandas.pydata.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

### 🔗 [**Live Demo →**](https://pantree-storefront.onrender.com/login)
*Hosted on Render's free tier — the first load after idle may take ~30–60s while the instance wakes.*

</div>

---

## Overview

**Pantree** is one seeded simulation of one company, one timeline — where **every table reconciles**. A stateful simulator generates a coherent, story-driven warehouse; a live FastAPI storefront reads and writes that data as real users shop; and a **SQLite (OLTP) → DuckDB (OLAP)** split separates transactions from analytics.

It's purpose-built so that **churn, CLTV, RFM, Marketing-Mix-Modelling, Voice-of-Customer, recommendation, and text-to-SQL** projects all sit on the same validated foundation.

---

## The Storefront

<div align="center">

**Login — device selection feeds the clickstream**
<img src="docs/screens/login.png" width="800"/>

**Home — real product catalogue across Apparel · Footwear · Accessories**
<img src="docs/screens/home.png" width="800"/>

**Product page — real images, attributes (colour / gender / type / usage / season), live stock**
<img src="docs/screens/pdp.png" width="800"/>

**Cart & Checkout — multi-payment, order created only on payment confirmation**
<img src="docs/screens/checkout.png" width="800"/>

**Orders — full lifecycle: Placed → Delivered → Return / Replace / Cancel, each writing back to inventory**
<img src="docs/screens/orders.png" width="800"/>

</div>

Every interaction — page views, searches, product views, cart adds, checkout, purchases, returns —
is logged to the **clickstream** in a production-realistic schema, and purchases decrement live inventory
(with a **Refresh Inventory** control to restock for demos).

---

## What this project demonstrates

| Area | What's shown |
|---|---|
| **Data engineering** | Stateful incremental simulator (`initialize` + daily `increment`), dimensional modelling (order-grain + line-grain), partitioned analytics facts, OLTP/OLAP split, a 40+ check cross-table + leakage validation suite |
| **ML readiness** | Leakage-safe, forward-window **churn labels scoped to the active cohort**; RFM/CLTV-ready aggregates; MMM weekly targets; a product-affinity graph for recommendations |
| **GenAI readiness** | Support transcripts + reviews for Voice-of-Customer; a schema knowledge base + verified NL→SQL eval set for an agentic text-to-SQL assistant |
| **Full-stack** | A working storefront (login → catalogue → PDP → cart → checkout → orders → returns) with PBKDF2 auth and live inventory |
| **MLOps / deploy** | Reproducible seeded builds, config-driven scale, and a one-click Render deployment (`render.yaml`) |

---

## Architecture

```
   product_details.csv ─▶ ┌────────────────────────┐   python generate_data.py
   (real fashion seed)    │  Stateful Simulator     │   (initialize / increment)
                          └───────────┬────────────┘
                                      │ writes
                                      ▼
                          ┌────────────────────────┐  masters · orders · transactions ·
                          │  Warehouse (Parquet)    │  clickstream · inventory · returns ·
                          │  dimensions + facts     │  reviews · support · marketing · MMM ·
                          └──────┬───────────┬──────┘  monthly features · churn labels
                     seeds ▼     │           │  reads ▼
             ┌───────────────────┐           └───────────────────────────┐
             │  Storefront       │ writes live                            │
             │  FastAPI + SQLite │ ─────────▶ SQLite (OLTP, truth)        │
             │  login → orders   │            analytics_sync              │
             └───────────────────┘ ─────────▶ DuckDB (OLAP mirror) ───────┘ ─▶ ML / LLM projects
```

- **SQLite** = transactional store the website writes to (carts, orders, clickstream, inventory).
- **DuckDB** = analytical mirror for fast reads and model training.
- Mirrors production: swap SQLite→Postgres and DuckDB→Redshift/Snowflake + dbt marts behind the same app at scale.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full design.

---

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

**Refresh data over time (incremental):**
```bash
python generate_data.py --mode increment   # append new days/customers/orders through today
python webapp/analytics_sync.py            # sync the DuckDB analytics mirror
cd webapp && python seed_db.py             # refresh the app DB
```

**Configure scale (env vars):**
```bash
PANTREE_CUSTOMERS=30000 PANTREE_PRODUCTS=10000 python generate_data.py --mode initialize
```

---

## The warehouse

| Table | Grain | Powers |
|---|---|---|
| `customer_master`, `product_master`, `seller_master`, `agent_master` | dimension | all |
| `orders` / `transactions` | order / order-line | revenue, CLTV, MMM |
| `clickstream` | event | churn behaviour, funnel, recommendations |
| `inventory` | product × day | stock, stockouts |
| `returns`, `product_reviews`, `support_contacts` | event | Voice-of-Customer |
| `marketing_spend`, `mmm_weekly_target` | day×channel / week | Marketing-Mix-Modelling |
| `product_affinity` | product pair | Frequently-Bought-Together |
| `customer_monthly_features` | customer × month | churn/CLTV features (as-of snapshot) |
| `customer_churn_labels` | customer × month | forward 90-day churn label (active cohort) |

`data/metrics.json` documents the canonical metric definitions.

---

## The story baked into the data (so models find signal, not noise)
- Seasonal / festival demand spikes and end-of-season sales.
- Acquisition-channel-driven customer quality (referral/organic customers are worth more than social/display).
- Customer lifecycle and gradual, partly-exogenous churn (some loyal-looking customers still leave).
- Real fashion co-purchase affinity for Frequently-Bought-Together.
- Realized-revenue discipline: cancelled / returned orders are **not** counted as revenue.

---

## Validation
- `validators/validate_dataset.py` — cross-table integrity, temporal order, funnel ordering, reconciliation, no-sale-before-launch, churn-leakage checks (40+ checks).
- `validators/preflight_checks.py` — the above **plus** a churn model-readiness smoke test (base rate + leakage-safe AUC + top-decile lift), so labels can't silently drift to an unmodelable rate.

---

## Deployment
Deployed on **Render** via [`render.yaml`](render.yaml) (Blueprint). The build generates the warehouse
(reduced scale for the free tier), seeds SQLite, then serves the FastAPI app. Live per-event DuckDB
mirroring is disabled on the server (`PANTREE_LIVE_MIRROR=0`) to avoid single-writer contention; the
analytical mirror is rebuilt on demand.

> **Hosted-demo notes:** on the free tier the instance sleeps after idle (first load ~30–60s), and data
> resets on redeploy (ephemeral disk). Run locally for persistent data.

---

## Projects built on this platform
See [`docs/PROJECTS.md`](docs/PROJECTS.md): RFM, CLTV, Churn, Marketing-Mix-Modelling, Voice-of-Customer,
Recommendation + Conversational Shopping Assistant, and the Agentic Analytics (text-to-SQL) assistant.

---

## Honest notes
- This is **realistic synthetic data** with deliberately engineered, documented signal — validated and
  leakage-checked, but simulated, not production data.
- Product catalogue sources from a real fashion dataset; on the hosted demo, some images fall back to
  category placeholders (source image URLs are hotlink-protected). Run locally for full images.

## License
MIT — see [LICENSE](LICENSE).

---

<div align="center">
Built by <a href="https://github.com/Meetpanchal58">Meet Panchal</a>
</div>