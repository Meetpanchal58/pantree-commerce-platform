"""Seed the live web-app database and initialize its DuckDB analytical mirror.

Postgres (DATABASE_URL set, e.g. Neon): seeds only an empty database, so redeploys and
restarts never wipe live activity. Use --reset to delete everything and seed again.
SQLite (local): the file is rebuilt from scratch each run, as before.
"""
import argparse
import sys
import pandas as pd
import numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import database
from analytics_sync import sync_from_live

DATA = Path(__file__).resolve().parent.parent / "data"
DB = Path(__file__).resolve().parent / "pantree_app.db"
rng = np.random.default_rng(42)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true",
                        help="Postgres only: delete all live data (accounts, orders, activity) and seed again")
    args = parser.parse_args(argv)
    required = ["product_master.parquet", "customer_master.parquet", "inventory.parquet"]
    missing = [name for name in required if not (DATA / name).exists()]
    if missing:
        raise SystemExit(
            "Generated warehouse is incomplete. Missing: "
            + ", ".join(missing)
            + ". Run `python generate_data.py --mode initialize` first."
        )

    postgres = bool(database.database_url())
    if not postgres and DB.exists():
        DB.unlink()
    con = database.connect(DB)
    if postgres:
        existing = database.table_names(con)
        if not args.reset and "products" in existing and con.execute("SELECT COUNT(*) FROM products").fetchone()[0]:
            print("Live Postgres database is already seeded: keeping its data (use --reset to start over).")
            con.close()
            return
        for table in existing:  # empty or --reset: start from a clean schema
            con.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
        con.commit()

    prod = pd.read_parquet(DATA / "product_master.parquet")
    database.write_frame(con, "products", prod)

    cust = pd.read_parquet(DATA / "customer_master.parquet")
    database.write_frame(con, "customers", cust)

    inv_src = pd.read_parquet(DATA / "inventory.parquet")
    inv_src["inventory_date"] = pd.to_datetime(inv_src.inventory_date)
    inv = (inv_src.sort_values("inventory_date")
           .groupby(["product_id"], as_index=False).tail(1)[["product_id", "closing_stock"]]
           .rename(columns={"closing_stock": "current_stock"}))
    inv = prod[["product_id"]].merge(inv, on="product_id", how="left").fillna({"current_stock": 0})
    inv["base_stock"] = inv["current_stock"]
    database.write_frame(con, "inventory", inv)

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
    CREATE TABLE IF NOT EXISTS customer_pii (
        customer_id TEXT PRIMARY KEY, first_name TEXT, last_name TEXT,
        email TEXT UNIQUE, phone TEXT UNIQUE,
        home_address_line1 TEXT, home_address_line2 TEXT,
        home_postal_code TEXT, home_country TEXT,
        created_at TEXT, updated_at TEXT
    );
    CREATE TABLE IF NOT EXISTS pending_signups (
        device_id TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at REAL NOT NULL
    );
    CREATE UNIQUE INDEX IF NOT EXISTS ux_products ON products(product_id);
    CREATE UNIQUE INDEX IF NOT EXISTS ux_inventory ON inventory(product_id);
    CREATE UNIQUE INDEX IF NOT EXISTS ux_customers ON customers(customer_id);
    CREATE INDEX IF NOT EXISTS ix_click_cust ON clickstream(customer_id);
    CREATE INDEX IF NOT EXISTS ix_click_session ON clickstream(session_id);
    CREATE INDEX IF NOT EXISTS ix_click_customer_recent_product
        ON clickstream(customer_id,event_timestamp DESC,product_id);
    CREATE INDEX IF NOT EXISTS ix_txn_cust ON transactions(customer_id);
    CREATE INDEX IF NOT EXISTS ix_txn_order ON transactions(order_id);
    CREATE INDEX IF NOT EXISTS ix_orders_cust ON orders(customer_id);
    CREATE INDEX IF NOT EXISTS ix_returns_txn ON returns(transaction_id);
    """)
    con.commit(); con.close()
    sync_from_live(DB)
    print(f"seeded {'Postgres' if postgres else DB.name}: {len(prod)} products, {len(cust)} customers, inventory ready")
    print(f"DuckDB mirror: {(DB.parent/'pantree_analytics.duckdb').name}")

if __name__ == "__main__":
    main()
