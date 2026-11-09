from pathlib import Path
import json
from functools import lru_cache

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
# One definition of "a purchase" for every recommender, evaluation and webapp query:
# the payment succeeded and the order line was not cancelled, returned to sender,
# lost or undelivered. CIR lines (paid and delivered, later returned by the
# customer) ARE purchases. Matches the warehouse's realized-revenue definition.
NON_PURCHASE_STATUSES = ("RTO", "CANCELLED", "REFUNDED", "CIC", "LOST", "UNDELIVERED")
PURCHASE_PAYMENT_STATUS = "SUCCESS"


def purchase_mask(frame: pd.DataFrame) -> pd.Series:
    """True for order lines that count as purchases (see NON_PURCHASE_STATUSES)."""
    status = frame["order_status"].fillna("").astype(str).str.upper()
    payment = frame["payment_status"].fillna("").astype(str).str.upper()
    return ~status.isin(NON_PURCHASE_STATUSES) & payment.eq(PURCHASE_PAYMENT_STATUS)


def purchase_sql(status_col: str = "order_status", payment_col: str = "payment_status") -> str:
    """The same rule as a SQL condition, for the live SQLite store."""
    statuses = ",".join(f"'{s}'" for s in NON_PURCHASE_STATUSES)
    return (f"UPPER(COALESCE({status_col},'')) NOT IN ({statuses}) "
            f"AND UPPER(COALESCE({payment_col},'')) = '{PURCHASE_PAYMENT_STATUS}'")
UNIVERSAL_GENDERS = {"unisex", "all", "all genders", "unisex clothing"}


def gender_is_compatible(anchor_gender, candidate_gender) -> bool:
    """Keep known-gender recommendations within that audience or universal items."""
    if pd.isna(anchor_gender) or not str(anchor_gender).strip():
        return True
    if pd.isna(candidate_gender) or not str(candidate_gender).strip():
        return False
    anchor = str(anchor_gender).strip().lower()
    candidate = str(candidate_gender).strip().lower()
    aliases = {"male": "men", "female": "women"}
    anchor = aliases.get(anchor, anchor)
    candidate = aliases.get(candidate, candidate)
    return candidate in UNIVERSAL_GENDERS or candidate == anchor


@lru_cache(maxsize=1)
def read_products() -> pd.DataFrame:
    columns = [
        "product_id", "product_name", "h1_category", "h2_category",
        "h3_category", "h4_colour", "brand_name", "gender", "mrp",
        "current_price", "avg_rating", "review_count", "product_description",
        "product_attributes", "image_url", "is_active",
    ]
    products = pd.read_parquet(DATA / "product_master.parquet", columns=columns)
    products["product_attributes"] = products["product_attributes"].map(
        lambda value: json.loads(value) if isinstance(value, str) else {}
    )
    return products.drop_duplicates("product_id").set_index("product_id", drop=False)


@lru_cache(maxsize=1)
def read_realized_transactions() -> pd.DataFrame:
    transactions = pd.read_parquet(
        DATA / "transactions.parquet",
        columns=[
            "customer_id", "product_id", "order_id", "order_timestamp",
            "order_status", "payment_status", "quantity", "unit_selling_price",
        ],
    )
    transactions = transactions.loc[purchase_mask(transactions)].copy()
    transactions["order_timestamp"] = pd.to_datetime(
        transactions["order_timestamp"], errors="coerce"
    )
    return transactions.dropna(subset=["customer_id", "product_id", "order_timestamp"])


def realized_transactions_ready() -> bool:
    """Report whether warehouse transactions have been loaded by background warmup."""
    return read_realized_transactions.cache_info().currsize > 0


@lru_cache(maxsize=1)
def read_stock() -> pd.Series:
    stock = pd.read_parquet(
        DATA / "inventory.parquet",
        columns=["inventory_date", "product_id", "closing_stock"],
    )
    stock["inventory_date"] = pd.to_datetime(stock["inventory_date"], errors="coerce")
    latest_dates = stock.groupby("product_id", sort=False)[
        "inventory_date"
    ].transform("max")
    latest = stock.loc[stock["inventory_date"].eq(latest_dates)].drop_duplicates(
        "product_id", keep="last"
    )
    missing_date_products = stock.loc[latest_dates.isna(), "product_id"].drop_duplicates()
    if not missing_date_products.empty:
        missing_date_rows = stock[stock["product_id"].isin(missing_date_products)]
        latest = pd.concat(
            [
                latest,
                missing_date_rows.drop_duplicates("product_id", keep="last"),
            ],
            ignore_index=True,
        )
    return latest.set_index("product_id")["closing_stock"]


@lru_cache(maxsize=1)
def read_clickstream_activity() -> pd.DataFrame:
    """Read the interaction events shared by popularity and ranking features."""
    events = pd.read_parquet(
        DATA / "clickstream.parquet",
        columns=["customer_id", "product_id", "event_type", "event_timestamp"],
    )
    events = events[
        events["event_type"].isin(["pdp_view", "add_to_cart", "wishlist_add"])
    ].dropna(subset=["customer_id", "product_id", "event_timestamp"]).copy()
    events["event_timestamp"] = pd.to_datetime(
        events["event_timestamp"], errors="coerce"
    )
    events = events.dropna(subset=["event_timestamp"])
    events["weight"] = events["event_type"].map(
        {"pdp_view": 1.0, "add_to_cart": 3.0, "wishlist_add": 2.0}
    )
    return events


@lru_cache(maxsize=1)
def read_event_counts() -> tuple[pd.Series, pd.Series]:
    events = read_clickstream_activity()
    customer_counts = events.groupby("customer_id").size()
    product_counts = events.groupby("product_id").size()
    return customer_counts, product_counts


def event_counts_ready() -> bool:
    """Report whether clickstream ranking features are ready for request-time use."""
    return read_event_counts.cache_info().currsize > 0


@lru_cache(maxsize=1)
def customer_histories(limit: int = 200) -> dict[str, dict]:
    """Per customer: products bought and products interacted with, newest first.

    Mirrors how the evaluation builds a customer's profile (purchases first).
    """
    events = read_clickstream_activity().sort_values("event_timestamp", ascending=False)
    viewed = {
        customer_id: list(dict.fromkeys(group["product_id"].head(limit)))
        for customer_id, group in events.groupby("customer_id", sort=False)
    }
    orders = read_realized_transactions().sort_values("order_timestamp", ascending=False)
    bought = {
        customer_id: list(dict.fromkeys(group["product_id"]))
        for customer_id, group in orders.groupby("customer_id", sort=False)
    }
    return {
        customer_id: {"bought": bought.get(customer_id, []), "viewed": viewed.get(customer_id, [])}
        for customer_id in set(viewed) | set(bought)
    }


@lru_cache(maxsize=1)
def purchased_products() -> dict[str, frozenset]:
    """Customer -> products they bought, precomputed once at load (no per-request scans)."""
    return {cid: frozenset(h["bought"]) for cid, h in customer_histories().items() if h["bought"]}


def customer_histories_ready() -> bool:
    return customer_histories.cache_info().currsize > 0


class AvailabilityIndex:
    """Was a product sellable at a moment? In stock at the previous day's close,
    launched, and not yet discontinued. ``as_of=None`` means now (latest stock
    and the catalog's ``is_active`` flag)."""

    def __init__(self):
        stock = pd.read_parquet(
            DATA / "inventory.parquet", columns=["inventory_date", "product_id", "closing_stock"]
        )
        stock["inventory_date"] = pd.to_datetime(stock["inventory_date"], errors="coerce").dt.normalize()
        stock = stock.dropna(subset=["inventory_date"])
        in_stock = (
            stock.assign(ok=stock["closing_stock"].gt(0))
            .pivot_table(index="inventory_date", columns="product_id", values="ok", aggfunc="max")
            .sort_index()
        )
        self.days = in_stock.index.to_numpy()
        self.in_stock = in_stock.fillna(False).to_numpy(dtype=bool)
        self.column = {pid: i for i, pid in enumerate(in_stock.columns)}
        catalog = pd.read_parquet(
            DATA / "product_master.parquet", columns=["product_id", "launch_date", "discontinue_date"]
        ).drop_duplicates("product_id").set_index("product_id")
        self.launch = pd.to_datetime(catalog["launch_date"], errors="coerce").to_dict()
        self.discontinue = pd.to_datetime(catalog["discontinue_date"], errors="coerce").to_dict()

    def available(self, product_ids, as_of) -> list[bool]:
        as_of = pd.Timestamp(as_of)
        # Stock known at as_of: the close of the last full day before it.
        row = int(np.searchsorted(self.days, np.datetime64(as_of.normalize()), side="left")) - 1
        out = []
        for pid in product_ids:
            launch, stop = self.launch.get(pid), self.discontinue.get(pid)
            if launch is None or pd.isna(launch) or launch > as_of:
                out.append(False)
            elif stop is not None and not pd.isna(stop) and stop <= as_of:
                out.append(False)
            else:
                col = self.column.get(pid)
                # Before the first inventory snapshot, launched products count as stocked.
                out.append(True if row < 0 else bool(col is not None and self.in_stock[row, col]))
        return out


@lru_cache(maxsize=1)
def availability_index() -> AvailabilityIndex:
    return AvailabilityIndex()


def available(product_ids, as_of=None) -> list[bool]:
    """Sellable at ``as_of`` (point in time) or now (``None``)."""
    if as_of is not None:
        return availability_index().available(product_ids, as_of)
    products, stock = read_products(), read_stock()
    out = []
    for pid in product_ids:
        active = pid in products.index and not pd.isna(products.at[pid, "is_active"]) and bool(products.at[pid, "is_active"])
        out.append(active and pid in stock.index and float(stock.loc[pid]) > 0)
    return out
