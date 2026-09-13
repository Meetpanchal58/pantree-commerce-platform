"""Mirror live SQLite OLTP changes into a DuckDB OLAP database.
SQLite is the source of truth for the web app. DuckDB is an analytical mirror.
"""
from pathlib import Path
import duckdb
import pandas as pd

BASE = Path(__file__).resolve().parent
DUCKDB = BASE / "pantree_analytics.duckdb"

TABLE_KEYS = {
    "clickstream": "event_id",
    "transactions": "transaction_id",
    "orders": "order_id",
    "inventory": "product_id",
    "customers": "customer_id",
    "returns": "return_id",
    "inventory_movements": "movement_id",
}


def _safe(name: str) -> str:
    if not name.replace("_", "").isalnum():
        raise ValueError("unsafe table name")
    return name


def ensure_duckdb():
    con = duckdb.connect(str(DUCKDB))
    return con


def _duckdb_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Keep nullable text columns textual when DuckDB infers a schema."""
    out = df.copy()
    for column in out.columns:
        if out[column].dtype == "object":
            out[column] = out[column].astype("string")
    return out


def mirror_rows(table: str, df: pd.DataFrame):
    """Upsert a small changed batch into DuckDB."""
    if df is None or df.empty:
        return
    table = _safe(table)
    pk = TABLE_KEYS[table]
    con = ensure_duckdb()
    try:
        df = _duckdb_frame(df)
        if not con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name=?", [table]).fetchone()[0]:
            con.register("_incoming", df)
            con.execute(f"CREATE TABLE {table} AS SELECT * FROM _incoming")
            con.unregister("_incoming")
        else:
            columns = [row[0] for row in con.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name=? ORDER BY ordinal_position",
                [table],
            ).fetchall()]
            for column in df.columns:
                if column not in columns:
                    con.execute(f"ALTER TABLE {table} ADD COLUMN {column} VARCHAR")
            columns = [row[0] for row in con.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name=? ORDER BY ordinal_position",
                [table],
            ).fetchall()]
            missing = [column for column in columns if column not in df.columns]
            if missing:
                raise ValueError(f"Missing columns for DuckDB mirror {table}: {missing}")
            con.register("_incoming", df)
            con.execute(f"DELETE FROM {table} WHERE {pk} IN (SELECT {pk} FROM _incoming)")
            names = ", ".join(columns)
            con.execute(f"INSERT INTO {table} ({names}) SELECT {names} FROM _incoming")
            con.unregister("_incoming")
        con.commit()
    finally:
        con.close()


def mirror_event(row: dict):
    mirror_rows("clickstream", pd.DataFrame([row]))


def mirror_customer(row: dict):
    mirror_rows("customers", pd.DataFrame([row]))


def mirror_order(row: dict):
    mirror_rows("orders", pd.DataFrame([row]))


def mirror_transaction(row: dict):
    mirror_rows("transactions", pd.DataFrame([row]))


def mirror_return(row: dict):
    mirror_rows("returns", pd.DataFrame([row]))


def mirror_inventory(row: dict):
    mirror_rows("inventory", pd.DataFrame([row]))


def mirror_inventory_movement(row: dict):
    mirror_rows("inventory_movements", pd.DataFrame([row]))


def sync_from_sqlite(db_path: Path):
    """One-time/full rebuild of the analytical mirror from the live SQLite DB."""
    import sqlite3
    sqlite = sqlite3.connect(db_path)
    try:
        duck = ensure_duckdb()
        try:
            for table in ["customers", "orders", "transactions", "clickstream", "inventory", "returns", "inventory_movements"]:
                try:
                    df = pd.read_sql_query(f"SELECT * FROM {table}", sqlite)
                except Exception:
                    continue
                duck.execute(f"DROP TABLE IF EXISTS {table}")
                df = _duckdb_frame(df)
                duck.register("_df", df)
                duck.execute(f"CREATE TABLE {table} AS SELECT * FROM _df")
                duck.unregister("_df")
            duck.commit()
        finally:
            duck.close()
    finally:
        sqlite.close()


if __name__ == "__main__":
    sync_from_sqlite(BASE / "pantree_app.db")
    print(f"Synced SQLite into {DUCKDB.name}")
