"""The live (OLTP) database: Postgres in production, SQLite for local runs and tests.

Set DATABASE_URL (for example a Neon connection string) to use Postgres; without it the
app uses the SQLite file at PANTREE_DB_PATH. The rest of the app writes plain SQLite-style
SQL with `?` placeholders. On Postgres this module adapts it:

  - `?` placeholders become `%s`; LIKE becomes ILIKE (SQLite's LIKE ignores case)
  - REAL columns become DOUBLE PRECISION; PRAGMA statements are skipped
  - `BEGIN IMMEDIATE` becomes a transaction-level advisory lock
  - rows can be read by position or by column name, like sqlite3.Row
  - NUMERIC results come back as float and Python bools are stored as 0/1, as in SQLite

Statements that differ between the two databases (column lists, table lists) go through the
helpers below instead of PRAGMA or sqlite_master.
"""
from __future__ import annotations

import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

try:
    import psycopg
    from psycopg.adapt import Dumper
    from psycopg.types.numeric import FloatLoader
except ImportError:  # SQLite-only installs do not need the Postgres driver
    psycopg = None

IntegrityError: tuple = (sqlite3.IntegrityError,)
OperationalError: tuple = (sqlite3.OperationalError,)
if psycopg is not None:
    IntegrityError += (psycopg.errors.IntegrityError,)
    OperationalError += (psycopg.OperationalError,)


def database_url() -> str:
    return os.getenv("DATABASE_URL", "").strip()


def connect(sqlite_path=None, timeout: float = 60):
    """A connection to the live database (Postgres if DATABASE_URL is set, else SQLite)."""
    if database_url():
        return PgConnection(_pool().getconn())
    con = sqlite3.connect(sqlite_path, timeout=timeout)
    con.row_factory = sqlite3.Row
    return con


def is_postgres(con) -> bool:
    return isinstance(con, PgConnection)


# ----------------------------------------------------------------------------- schema helpers
def table_names(con) -> set[str]:
    if is_postgres(con):
        rows = con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()"
        ).fetchall()
    else:
        rows = con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {row[0] for row in rows}


def table_exists(con, name: str) -> bool:
    return name in table_names(con)


def table_columns(con, name: str) -> list[str]:
    """Column names of a table, in order (empty if the table does not exist)."""
    if is_postgres(con):
        rows = con.execute(
            """SELECT column_name FROM information_schema.columns
               WHERE table_schema = current_schema() AND table_name = ? ORDER BY ordinal_position""",
            (name,),
        ).fetchall()
        return [row[0] for row in rows]
    return [row[1] for row in con.execute(f"PRAGMA table_info({_identifier(name)})").fetchall()]


@contextmanager
def savepoint(con, name: str = "pantree_sp"):
    """Run a few statements that may fail without aborting the surrounding transaction.

    Postgres refuses every statement after an error until a rollback; a savepoint limits the
    rollback to the failed part, as SQLite does by default.
    """
    con.execute(f"SAVEPOINT {name}")
    try:
        yield
    except BaseException:
        con.execute(f"ROLLBACK TO SAVEPOINT {name}")
        con.execute(f"RELEASE SAVEPOINT {name}")
        raise
    con.execute(f"RELEASE SAVEPOINT {name}")


def read_frame(con, sql: str, params=None):
    """Query result as a pandas DataFrame (works for both databases)."""
    import pandas as pd

    cursor = con.execute(sql, params or ())
    columns = [d[0] for d in cursor.description] if cursor.description else []
    return pd.DataFrame([tuple(row) for row in cursor.fetchall()], columns=columns)


def _postgres_frame(frame):
    """Column types for Postgres, and values as plain Python objects (as pandas stores them in SQLite)."""
    import pandas as pd

    frame = frame.copy()
    types = {}
    for column in frame.columns:
        series = frame[column]
        if pd.api.types.is_bool_dtype(series):
            frame[column] = series.astype(int)
            types[column] = "BIGINT"
        elif pd.api.types.is_integer_dtype(series):
            types[column] = "BIGINT"
        elif pd.api.types.is_float_dtype(series):
            types[column] = "DOUBLE PRECISION"
        elif pd.api.types.is_datetime64_any_dtype(series):
            # stored as text, exactly as pandas writes datetimes to SQLite
            frame[column] = series.dt.strftime("%Y-%m-%d %H:%M:%S")
            types[column] = "TEXT"
        else:
            frame[column] = series.map(lambda v: v if v is None or isinstance(v, str) else str(v))
            types[column] = "TEXT"
    return frame.astype(object).where(frame.notna(), None), types


def write_frame(con, name: str, frame) -> None:
    """Replace table `name` with the rows of a DataFrame (used when seeding)."""
    if not is_postgres(con):
        frame.to_sql(name, con, index=False, if_exists="replace")
        return
    frame, types = _postgres_frame(frame)
    table = _identifier(name)
    columns = ", ".join(f"{_identifier(c)} {kind}" for c, kind in types.items())
    con.execute(f"DROP TABLE IF EXISTS {table}")
    con.execute(f"CREATE TABLE {table} ({columns})")
    names = ", ".join(_identifier(c) for c in frame.columns)
    with con.raw.cursor().copy(f"COPY {table} ({names}) FROM STDIN") as copy:
        for row in frame.itertuples(index=False, name=None):
            copy.write_row(row)


def append_frame(con, name: str, frame) -> int:
    """Insert DataFrame rows into an existing table (columns the table lacks are dropped)."""
    if frame.empty:
        return 0
    existing = table_columns(con, name)
    frame = frame[[c for c in frame.columns if c in existing]]
    if is_postgres(con):
        frame, _ = _postgres_frame(frame)
    else:
        frame = frame.astype(object).where(frame.notna(), None)
        for column in frame.columns:
            frame[column] = frame[column].map(lambda v: str(v) if hasattr(v, "isoformat") else v)
    names = ", ".join(_identifier(c) for c in frame.columns)
    marks = ", ".join("?" for _ in frame.columns)
    con.executemany(f"INSERT INTO {_identifier(name)} ({names}) VALUES ({marks})",
                    [tuple(row) for row in frame.itertuples(index=False, name=None)])
    return len(frame)


def _identifier(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise ValueError(f"unsafe SQL identifier: {name!r}")
    return name


# ----------------------------------------------------------------------------- Postgres adapter
def translate(sql: str, has_params: bool) -> str | None:
    """SQLite-style SQL -> Postgres SQL (None means: skip this statement)."""
    stripped = sql.lstrip()
    if re.match(r"PRAGMA\b", stripped, re.IGNORECASE):
        return None
    if re.match(r"BEGIN\s+IMMEDIATE\b", stripped, re.IGNORECASE):
        return "SELECT pg_advisory_xact_lock(4242)"
    out, i, quote = [], 0, None
    while i < len(sql):
        ch = sql[i]
        if ch == "%" and has_params:  # psycopg reads % everywhere once parameters are passed
            out.append("%%")
        elif quote:
            out.append(ch)
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
            out.append(ch)
        elif ch == "?":
            out.append("%s")
        else:
            out.append(ch)
        i += 1
    sql = "".join(out)
    sql = re.sub(r"\bLIKE\b", "ILIKE", sql)
    sql = re.sub(r"\bREAL\b", "DOUBLE PRECISION", sql)
    return sql


class Row(tuple):
    """A result row readable by index or column name, like sqlite3.Row."""

    def __new__(cls, values, index):
        row = super().__new__(cls, values)
        row._index = index
        return row

    def __getitem__(self, key):
        if isinstance(key, str):
            return tuple.__getitem__(self, self._index[key])
        return tuple.__getitem__(self, key)

    def keys(self):
        return list(self._index)


def _row_factory(cursor):
    index: dict[str, int] = {}
    for position, column in enumerate(cursor.description or []):
        index.setdefault(column.name, position)  # first wins, like sqlite3.Row
    return lambda values: Row(values, index)


class _NoRows:
    description = None
    rowcount = 0

    def fetchone(self):
        return None

    def fetchall(self):
        return []

    def __iter__(self):
        return iter(())


class PgConnection:
    """A pooled psycopg connection with the small part of the sqlite3 API the app uses."""

    def __init__(self, raw):
        self.raw = raw

    def execute(self, sql, params=None):
        params = tuple(params) if params else None
        query = translate(sql, has_params=params is not None)
        if query is None:
            return _NoRows()
        cursor = self.raw.cursor()
        cursor.execute(query, params)
        return cursor

    def executemany(self, sql, rows):
        cursor = self.raw.cursor()
        cursor.executemany(translate(sql, has_params=True), [tuple(r) for r in rows])
        return cursor

    def executescript(self, script):
        for statement in script.split(";"):
            if statement.strip():
                self.execute(statement)

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    def close(self):
        if self.raw is None:
            return
        raw, self.raw = self.raw, None
        if raw.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
            raw.rollback()  # never hand an open transaction back to the pool
        _pool().putconn(raw)

    def __del__(self):
        # Safety net: a request that forgot close() still returns its connection to the pool.
        try:
            self.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        (self.rollback if kind else self.commit)()
        return False


_POOL = None
_POOL_LOCK = threading.Lock()


def _configure(raw) -> None:
    raw.row_factory = _row_factory
    raw.adapters.register_loader("numeric", FloatLoader)  # SUM/AVG -> float, not Decimal
    raw.adapters.register_dumper(bool, _BoolAsInt)        # SQLite stores booleans as 0/1


def _pool():
    """A small connection pool, so a request does not pay for a new TLS connection to Neon."""
    global _POOL
    if psycopg is None:
        raise RuntimeError("DATABASE_URL is set but psycopg is not installed: pip install 'psycopg[binary,pool]'")
    with _POOL_LOCK:
        if _POOL is None:
            from psycopg_pool import ConnectionPool

            _POOL = ConnectionPool(
                database_url(), min_size=1, max_size=int(os.getenv("PANTREE_DB_POOL_SIZE", "5")),
                # Neon's pooler (PgBouncer) does not support server-side prepared statements
                kwargs={"prepare_threshold": None}, configure=_configure,
                check=ConnectionPool.check_connection, open=True,
            )
    return _POOL


if psycopg is not None:
    class _BoolAsInt(Dumper):
        oid = psycopg.adapters.types["int4"].oid

        def dump(self, obj):
            return b"1" if obj else b"0"


def sqlite_path_default() -> Path:
    return Path(os.getenv("PANTREE_DB_PATH", str(Path(__file__).resolve().parent / "pantree_app.db"))).expanduser()
