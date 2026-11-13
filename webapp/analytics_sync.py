"""Mirror live SQLite OLTP changes into a DuckDB OLAP database.
SQLite is the source of truth for the web app. DuckDB is an analytical mirror.
"""
from pathlib import Path
import os
import pandas as pd

BASE = Path(__file__).resolve().parent
DUCKDB = BASE / "pantree_analytics.duckdb"

# Live per-event mirroring to DuckDB is best-effort analytics only. On a shared/served
# environment (e.g. Render) set PANTREE_LIVE_MIRROR=0 to disable it and avoid DuckDB
# single-writer lock contention; rebuild the analytical mirror on demand via
# sync_from_sqlite(). It must NEVER break a web request.
MIRROR_ENABLED = os.getenv("PANTREE_LIVE_MIRROR", "1") == "1"

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
    # Imported lazily: the web app only needs DuckDB when live mirroring is on.
    import duckdb

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
    """Upsert a small changed batch into DuckDB. Best-effort: never raises to the caller."""
    if not MIRROR_ENABLED or df is None or df.empty:
        return
    try:
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
    except Exception as e:
        # analytics mirror is best-effort; log and continue so the web request succeeds
        print(f"[analytics_sync] mirror skipped for {table}: {e}")


def forget_customer_activity(customer_id: str):
    """Remove a customer's activity from the DuckDB mirror after an account reset (best-effort)."""
    if not MIRROR_ENABLED:
        return
    try:
        con = ensure_duckdb()
        try:
            existing = {row[0] for row in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
            for table in ("clickstream", "returns", "transactions", "orders"):
                if table in existing:
                    con.execute(f"DELETE FROM {table} WHERE customer_id=?", [customer_id])
            con.commit()
        finally:
            con.close()
    except Exception as e:
        print(f"[analytics_sync] reset cleanup skipped: {e}")


def mirror_event(row: dict):
    mirror_rows("clickstream", pd.DataFrame([row]))


# Identifying fields stay in SQLite's customer_pii table and are never mirrored.
PII_FIELDS = {
    "first_name", "last_name", "email", "phone", "home_address_line1",
    "home_address_line2", "home_postal_code", "home_country",
}


def mirror_customer(row: dict):
    mirror_rows("customers", pd.DataFrame([{k: v for k, v in row.items() if k not in PII_FIELDS}]))


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


def sync_from_live(db_path: Path | None = None):
    """Full rebuild of the DuckDB analytical mirror from the live database
    (Postgres when DATABASE_URL is set, otherwise the SQLite file at db_path)."""
    try:
        from . import database
    except ImportError:
        import database
    live = database.connect(db_path)
    try:
        existing = database.table_names(live)
        duck = ensure_duckdb()
        try:
            for table in ["customers", "orders", "transactions", "clickstream", "inventory", "returns", "inventory_movements"]:
                if table not in existing:
                    continue
                df = database.read_frame(live, f"SELECT * FROM {table}")
                duck.execute(f"DROP TABLE IF EXISTS {table}")
                df = _duckdb_frame(df)
                duck.register("_df", df)
                duck.execute(f"CREATE TABLE {table} AS SELECT * FROM _df")
                duck.unregister("_df")
            duck.commit()
        finally:
            duck.close()
    finally:
        live.close()


sync_from_sqlite = sync_from_live  # earlier name, kept for existing callers


if __name__ == "__main__":
    sync_from_live(BASE / "pantree_app.db")
    print(f"Synced SQLite into {DUCKDB.name}")