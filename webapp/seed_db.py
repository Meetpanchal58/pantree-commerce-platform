"""Seed the live SQLite web-app database and initialize its DuckDB analytical mirror."""
import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path
from analytics_sync import sync_from_sqlite

DATA = Path(__file__).resolve().parent.parent / "data"
DB = Path(__file__).resolve().parent / "pantree_app.db"
rng = np.random.default_rng(42)


def main():
    required = ["product_master.parquet", "customer_master.parquet", "inventory.parquet"]
    missing = [name for name in required if not (DATA / name).exists()]
    if missing:
        raise SystemExit(
            "Generated warehouse is incomplete. Missing: "
            + ", ".join(missing)
            + ". Run `python generate_data.py --mode initialize` first."
        )

    if DB.exists(): DB.unlink()
    con = sqlite3.connect(DB)

    prod = pd.read_parquet(DATA / "product_master.parquet")
    prod.to_sql("products", con, index=False, if_exists="replace")

    cust = pd.read_parquet(DATA / "customer_master.parquet")
    cust.to_sql("customers", con, index=False, if_exists="replace")

    inv_src = pd.read_parquet(DATA / "inventory.parquet")
    inv_src["inventory_date"] = pd.to_datetime(inv_src.inventory_date)
    inv = (inv_src.sort_values("inventory_date")
           .groupby(["product_id"], as_index=False).tail(1)[["product_id", "closing_stock"]]
           .rename(columns={"closing_stock": "current_stock"}))
    inv = prod[["product_id"]].merge(inv, on="product_id", how="left").fillna({"current_stock": 0})
    inv["base_stock"] = inv["current_stock"]
    inv.to_sql("inventory", con, index=False, if_exists="replace")

    con.executescript("""
    CREATE TABLE IF NOT EXISTS clickstream (
        event_id TEXT PRIMARY KEY, session_id TEXT, customer_id TEXT, order_id TEXT,
        event_timestamp TEXT, event_type TEXT, page_type TEXT,
        product_id TEXT, search_query TEXT, device_type TEXT, traffic_source TEXT, campaign_id TEXT
    );
    CREATE TABLE IF NOT EXISTS orders (
        order_id TEXT PRIMARY KEY, customer_id TEXT, order_timestamp TEXT,
        order_status TEXT, payment_status TEXT, total_amount REAL,
        created_at TEXT, delivered_timestamp TEXT, updated_at TEXT
    );
    CREATE TABLE IF NOT EXISTS transactions (
        transaction_id TEXT PRIMARY KEY, order_id TEXT, customer_id TEXT,
        product_id TEXT, seller_id TEXT, order_timestamp TEXT, quantity INTEGER,
        mrp REAL, listed_price REAL, unit_selling_price REAL, unit_cost REAL, discount_amount REAL,
        coupon_code TEXT, campaign_type TEXT, payment_method TEXT,
        order_status TEXT, payment_status TEXT,
        ship_city TEXT, ship_state TEXT, ship_region TEXT, delivered_timestamp TEXT,
        gross_item_value REAL, net_item_value REAL, gross_margin REAL,
        replacement_for_transaction_id TEXT
    );
    CREATE TABLE IF NOT EXISTS returns (
        return_id TEXT PRIMARY KEY, transaction_id TEXT, order_id TEXT, customer_id TEXT,
        product_id TEXT, seller_id TEXT, return_requested_at TEXT, return_received_at TEXT,
        return_reason TEXT, return_type TEXT, refund_amount REAL, returned_quantity INTEGER, return_status TEXT
    );
    CREATE TABLE IF NOT EXISTS inventory_movements (
        movement_id TEXT PRIMARY KEY, movement_timestamp TEXT, product_id TEXT,
        quantity_delta INTEGER, movement_type TEXT, order_id TEXT, transaction_id TEXT,
        reason TEXT
    );
    CREATE TABLE IF NOT EXISTS cart (
        customer_id TEXT, product_id TEXT, quantity INTEGER,
        added_at TEXT, PRIMARY KEY (customer_id, product_id)
    );
    CREATE TABLE IF NOT EXISTS wishlist (
        customer_id TEXT, product_id TEXT, added_at TEXT,
        PRIMARY KEY (customer_id, product_id)
    );
    CREATE TABLE IF NOT EXISTS sessions (
        session_id TEXT PRIMARY KEY, customer_id TEXT, device_type TEXT, created_at TEXT
    );
    CREATE TABLE IF NOT EXISTS auth (
        customer_id TEXT PRIMARY KEY, email TEXT UNIQUE, password TEXT
    );
    CREATE INDEX IF NOT EXISTS ix_click_cust ON clickstream(customer_id);
    CREATE INDEX IF NOT EXISTS ix_click_session ON clickstream(session_id);
    CREATE INDEX IF NOT EXISTS ix_txn_cust ON transactions(customer_id);
    CREATE INDEX IF NOT EXISTS ix_txn_order ON transactions(order_id);
    CREATE INDEX IF NOT EXISTS ix_orders_cust ON orders(customer_id);
    CREATE INDEX IF NOT EXISTS ix_returns_txn ON returns(transaction_id);
    """)
    con.commit(); con.close()
    sync_from_sqlite(DB)
    print(f"seeded {DB.name}: {len(prod)} products, {len(cust)} customers, inventory ready")
    print(f"DuckDB mirror: {(DB.parent/'pantree_analytics.duckdb').name}")

if __name__ == "__main__": main()
