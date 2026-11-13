"""Get the shop's history from the ecommerce-simulator package and shape it for Pantree.

    python prepare_data.py                  # download the full shop and convert it
    python prepare_data.py --size small     # a smaller shop (laptops, CI, Render's free plan)
    python prepare_data.py --skip-download  # convert data/source again without downloading
    python prepare_data.py --size small --recent-months 6   # Render's free plan (512 MB of memory)

The data comes from https://github.com/Meetpanchal58/ecommerce-simulator, refreshed every
night (GitHub release `data-latest`, mirrored on Hugging Face `Meet58/ecommerce-simulator`).

    data/source/      the package's tables as published (sales, customers, clickstream, ...);
                      the analysis notebook reads these directly
    data/*.parquet    the tables the recommender and the web app read:
                      product_master, customer_master, transactions, orders,
                      clickstream (product interactions only), inventory

Status vocabulary. The recommender counts a line as a purchase unless its status is
RTO / CANCELLED / REFUNDED / CIC / LOST / UNDELIVERED, and only if payment is "Success"
(recommendations/candidates/common.py). The package's statuses are mapped onto that:
delivered and returned items are purchases; cancellations (CIC), returns to origin (RTO),
lost and undelivered parcels are not. Exchange replacement lines are not new purchases and
are left out.
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute  # noqa: F401  (pa.compute)
import pyarrow.dataset as ds
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
SOURCE = DATA / "source"
HF_REPO = "Meet58/ecommerce-simulator"

STATUS = {  # package order_status -> Pantree status
    "Delivered": "Delivered", "Exchanged": "CIR", "Refunded": "CIR", "Returned": "CIR",
    "Return Requested": "CIR", "Return Picked Up": "CIR", "Cancelled": "CIC",
    "Returned to Origin": "RTO", "Lost": "Lost", "Undelivered": "Undelivered",
    "Placed": "Placed", "Confirmed": "Confirmed", "Shipped": "Shipped", "Out for Delivery": "Shipped",
}
PAYMENT = {"Paid": "Success", "Refunded": "Success", "Pending": "Pending", "Not Collected": "Failed"}


# ----------------------------------------------------------------------------- download
def download(size: str) -> None:
    """Fetch the published shop into data/source (GitHub release, else Hugging Face)."""
    try:
        from ecommerce_sim.download import download as from_github

        from_github(SOURCE, size)
        return
    except Exception as error:  # e.g. a network that blocks GitHub's file storage
        print(f"GitHub download failed ({error}); trying Hugging Face ...")
    from huggingface_hub import snapshot_download

    staging = DATA / "_hf"
    snapshot_download(HF_REPO, repo_type="dataset", allow_patterns=[f"{size}/*", f"{size}/**"],
                      local_dir=staging, max_workers=8)
    shutil.rmtree(SOURCE, ignore_errors=True)
    shutil.move(str(staging / size), SOURCE)
    shutil.rmtree(staging, ignore_errors=True)


def read(name: str, columns=None) -> pd.DataFrame:
    path = SOURCE / f"{name}.parquet"
    if path.exists():
        return pd.read_parquet(path, columns=columns)
    return ds.dataset(SOURCE / name, format="parquet", partitioning="hive").to_table(columns=columns).to_pandas()


# ----------------------------------------------------------------------------- tables
def product_master() -> pd.DataFrame:
    p = read("product_master")
    prices = read("price_history")
    current = prices[prices.valid_to.isna()].drop_duplicates("product_id", keep="last").set_index("product_id")
    reviews = read("product_reviews")
    reviews = reviews[reviews.moderation_status.eq("Published")].groupby("product_id").rating.agg(["mean", "size"])
    out = pd.DataFrame({
        "product_id": p.product_id, "source_product_id": p.source_product_id, "product_name": p.product_name,
        "gender": p.gender, "h1_category": p.category, "h2_category": p.sub_category,
        "h3_category": p.article_type, "h4_colour": p.colour, "brand_name": p.brand_name,
        "season": p.season, "usage": p.usage, "seller_id": p.seller_id, "mrp": p.mrp,
        "current_price": p.product_id.map(current.selling_price).fillna(p.mrp),
        "unit_cost": p.unit_cost,
        "avg_rating": p.product_id.map(reviews["mean"]).round(1),   # empty when nobody reviewed it
        "review_count": p.product_id.map(reviews["size"]).fillna(0).astype(int),
        "product_description": p.description,
        "product_attributes": [json.dumps({"material": m, "fit": f, "size_type": s, "usage": u, "season": se})
                               for m, f, s, u, se in zip(p.material, p.fit, p.size_type, p.usage, p.season)],
        "launch_date": pd.to_datetime(p.launch_date), "discontinue_date": pd.to_datetime(p.discontinue_date),
        "is_active": p.is_active, "image_url": p.image_url,
    })
    out["discount_pct"] = ((1 - out.current_price / out.mrp) * 100).round(2)
    return out


def customer_master() -> pd.DataFrame:
    c = read("customers")
    return pd.DataFrame({
        "customer_id": c.customer_id, "signup_date": pd.to_datetime(c.signup_at).dt.normalize(),
        "signup_city": c.city, "signup_state": c.state, "signup_region": c.region,
        "acquisition_channel": c.signup_channel, "acquisition_campaign": c.signup_campaign_id,
        "age_bracket": c.age_bracket, "gender": c.gender, "is_prime_member": c.is_prime.astype(int),
        "prime_start_date": c.prime_start_date, "prime_tier": np.where(c.is_prime, "Prime", "Non-Prime"),
        "email_opt_in": c.email_opt_in.astype(int), "sms_opt_in": c.sms_opt_in.astype(int),
        "account_status": c.account_status,
    })


def transactions() -> pd.DataFrame:
    s = read("sales")
    s = s[s.line_type.eq("Sale")]
    addresses = read("customer_addresses", ["address_id", "state", "region"]).set_index("address_id")
    campaign_type = read("campaigns", ["campaign_id", "campaign_type"]).set_index("campaign_id").campaign_type
    paid = s.gross_amount - s.coupon_discount
    return pd.DataFrame({
        "transaction_id": s.transaction_id, "order_id": s.order_id, "customer_id": s.customer_id,
        "product_id": s.product_id, "seller_id": s.seller_id, "order_timestamp": s.placed_at,
        "quantity": s.quantity, "mrp": s.mrp, "listed_price": s.unit_price,
        "unit_selling_price": (paid / s.quantity).round(2), "unit_cost": s.unit_cost,
        "discount_amount": ((s.mrp - s.unit_price) * s.quantity + s.coupon_discount).round(2),
        "coupon_code": s.coupon_code, "campaign_type": s.campaign_id.map(campaign_type),
        "payment_method": s.payment_method,
        "order_status": s.order_status.map(STATUS).fillna(s.order_status),
        "payment_status": s.payment_status.map(PAYMENT).fillna(s.payment_status),
        "ship_city": s.ship_city, "ship_state": s.address_id.map(addresses.state),
        "ship_region": s.address_id.map(addresses.region), "delivered_timestamp": s.delivered_at,
        "gross_item_value": s.gross_amount, "net_item_value": paid.round(2),
        "gross_margin": (paid - s.unit_cost * s.quantity).round(2),
    })


def orders(tx: pd.DataFrame) -> pd.DataFrame:
    realized = tx.order_status.isin(["Delivered", "CIR"]) & tx.payment_status.eq("Success")
    g = tx.assign(realized=tx.net_item_value.where(realized, 0.0)).groupby("order_id", sort=False)
    out = g.agg(customer_id=("customer_id", "first"), order_timestamp=("order_timestamp", "min"),
                order_status=("order_status", "first"), payment_status=("payment_status", "first"),
                payment_method=("payment_method", "first"), ship_city=("ship_city", "first"),
                ship_state=("ship_state", "first"), ship_region=("ship_region", "first"),
                campaign_type=("campaign_type", "first"), order_line_count=("transaction_id", "size"),
                total_quantity=("quantity", "sum"), gross_order_value=("gross_item_value", "sum"),
                realized_revenue=("realized", "sum"), gross_margin=("gross_margin", "sum")).reset_index()
    out["is_realized"] = out.realized_revenue > 0
    return out


# The recommender learns from product interactions only; sessions, searches and page views
# (for funnel analysis) stay in data/source/clickstream, which the notebook reads directly.
INTERACTIONS = ["pdp_view", "add_to_cart", "remove_from_cart", "wishlist_add", "purchase"]


def clickstream(recent_months: int | None = None) -> pa.Table:
    """Product-interaction events, renamed to Pantree's column names (pyarrow: millions of rows)."""
    table = ds.dataset(SOURCE / "clickstream", format="parquet", partitioning="hive").to_table(
        filter=ds.field("event_type").isin(INTERACTIONS),
        columns=["event_id", "session_id", "customer_id", "order_id", "event_at", "event_type", "page_type",
                 "product_id", "search_query", "device", "traffic_source", "campaign_id"])
    table = table.rename_columns(["event_id", "session_id", "customer_id", "order_id", "event_timestamp",
                                  "event_type", "page_type", "product_id", "search_query", "device_type",
                                  "traffic_source", "campaign_id"])
    if recent_months:  # serving on a small host: recent browsing only (orders keep their full history)
        last = pd.Timestamp(json.loads((SOURCE / "manifest.json").read_text())["last_day"])
        cut = pa.scalar(last - pd.DateOffset(months=recent_months), type=table.schema.field("event_timestamp").type)
        table = table.filter(pa.compute.greater_equal(table["event_timestamp"], cut))
    return table


def inventory() -> pd.DataFrame:
    """Closing stock per product per day (the package records each size only on days it changes)."""
    h = read("inventory_history", ["date", "sku_id", "product_id", "closing_stock", "sold"])
    h["date"] = pd.to_datetime(h.date).dt.normalize()
    days = pd.date_range(h.date.min(), pd.Timestamp(json.loads((SOURCE / "manifest.json").read_text())["last_day"]))
    closing = (h.groupby(["date", "sku_id"]).closing_stock.last().unstack()
               .reindex(days).ffill().fillna(0))           # before a size's first delivery: 0 in stock
    product_of = h.drop_duplicates("sku_id").set_index("sku_id").product_id
    by_product = closing.T.groupby(product_of.reindex(closing.columns).to_numpy()).sum().T
    sold = h.groupby(["date", "product_id"]).sold.sum()
    out = by_product.stack().rename("closing_stock").rename_axis(["inventory_date", "product_id"]).reset_index()
    out["closing_stock"] = out.closing_stock.astype(int)
    out["units_sold"] = sold.reindex(pd.MultiIndex.from_frame(out[["inventory_date", "product_id"]])).fillna(0).astype(int).to_numpy()
    return out


def convert(recent_months: int | None = None) -> None:
    write = lambda name, frame: frame.reset_index(drop=True).to_parquet(DATA / f"{name}.parquet", index=False)
    print("Converting data/source -> data/*.parquet ...")
    write("product_master", product_master())
    write("customer_master", customer_master())
    tx = transactions()
    write("transactions", tx)
    write("orders", orders(tx))
    pq.write_table(clickstream(recent_months), DATA / "clickstream.parquet")
    write("inventory", inventory())
    manifest = json.loads((SOURCE / "manifest.json").read_text())
    print(f"Ready: {manifest['size']} shop, {manifest['first_day']} -> {manifest['last_day']}, "
          f"{len(tx):,} order lines")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--size", default="full", choices=["small", "medium", "full", "large"])
    parser.add_argument("--skip-download", action="store_true", help="convert the existing data/source")
    parser.add_argument("--recent-months", type=int,
                        help="keep only the last N months of browsing in data/clickstream.parquet (small hosts)")
    args = parser.parse_args(argv)
    DATA.mkdir(exist_ok=True)
    if not args.skip_download:
        download(args.size)
    convert(args.recent_months)


if __name__ == "__main__":
    main()
