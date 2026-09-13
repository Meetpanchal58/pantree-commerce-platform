# data/

This folder is intentionally (almost) empty in the repository — the warehouse is **generated**,
not committed, so clones stay small and the data is always reproducible.

Generate it:
```
python generate_data.py --mode initialize
```
This writes all Parquet tables here (masters, orders, transactions, clickstream, inventory,
returns, reviews, support, marketing, MMM, monthly features, churn labels), `metrics.json`,
partitioned facts under `_partitioned/`, and simulator state under `_state/`.

Refresh forward in time:
```
python generate_data.py --mode increment
```
