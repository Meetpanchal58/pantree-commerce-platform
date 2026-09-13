# Pantree Storefront — FastAPI + SQLite + DuckDB

The web app is an OLTP demo backed by SQLite. **SQLite is the source of truth for live user activity.** Every clickstream event, successful website order, order-status change, return, replacement, and inventory movement is mirrored to `webapp/pantree_analytics.duckdb` for OLAP/model-training workloads.

## 1. Install

```bash
pip install -r ../requirements.txt
```

## 2. Generate/seed the warehouse

From the project root:

```bash
python generate_data.py --mode initialize
cd webapp
python seed_db.py
```

Stop any running Uvicorn process before running `seed_db.py`; seeding replaces the SQLite database file.

`seed_db.py` loads the generated product/customer/inventory data into SQLite and creates an initial DuckDB mirror.

## 3. Run

```bash
python -m uvicorn app:app --reload
```

Open `http://127.0.0.1:8000/login`.

## Live data flow

```text
Browser activity
      ↓
SQLite (OLTP source of truth)
      ↓
DuckDB mirror (OLAP / ML / LLM)
```

The app does not regenerate synthetic rows when a user interacts with it. It records the actual activity performed in the web app.

### Examples

- Open/home/category/search/PDP/cart actions → `clickstream`
- Checkout → `orders` + `transactions` + inventory decrement + `purchase` event
- Return → transaction status becomes `Returned`, `returns` row is created, inventory is increased
- Same-SKU replacement → original transaction becomes `Replaced`; inventory records a return and a replacement issue
- All inventory changes → `inventory_movements`

## Orders

Every website checkout starts as a successful, already-delivered order for demo purposes. The Orders page then allows the user to request:

- **Return Order**
- **Replace Order**

The database changes accordingly instead of merely changing the UI.

## DuckDB

The analytical mirror is:

```text
webapp/pantree_analytics.duckdb
```

Query it directly:

```python
import duckdb
con = duckdb.connect("webapp/pantree_analytics.duckdb")
print(con.execute("SELECT event_type, COUNT(*) FROM clickstream GROUP BY 1 ORDER BY 2 DESC").df())
print(con.execute("SELECT order_status, COUNT(*) FROM orders GROUP BY 1").df())
```

For production, SQLite would normally be replaced by PostgreSQL and DuckDB by a warehouse/lakehouse, but the source-of-truth vs analytics separation remains the same.
