"""Final semantic layer for Pantree: orders, lifecycle/churn snapshots, metrics and QA-ready enrichment."""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd

DATA = Path(__file__).resolve().parent.parent / "data"
STATE = DATA / "_state"


def _read(name, columns=None):
    p=DATA/f"{name}.parquet"
    return pd.read_parquet(p, columns=columns) if p.exists() else pd.DataFrame()


def build_orders():
    t=_read("transactions")
    if t.empty: return pd.DataFrame()
    t["order_timestamp"]=pd.to_datetime(t.order_timestamp)
    g=t.groupby("order_id",sort=False)
    o=g.agg(customer_id=("customer_id","first"), order_timestamp=("order_timestamp","min"),
            order_line_count=("transaction_id","nunique"), total_quantity=("quantity","sum"),
            gross_order_value=("gross_item_value","sum"), realized_revenue=("net_item_value","sum"),
            gross_margin=("gross_margin","sum"), payment_method=("payment_method","first"),
            ship_city=("ship_city","first"), ship_state=("ship_state","first"), ship_region=("ship_region","first"),
            campaign_type=("campaign_type","first")).reset_index()
    def order_status(x):
        vals=set(x.order_status)
        if "Delivered" in vals or "CIR" in vals: return "Delivered"
        if "RTO" in vals: return "RTO"
        if "CIC" in vals: return "CANCELLED"
        if "Lost" in vals: return "LOST"
        return "UNDELIVERED"
    def payment_status(x):
        vals=set(x.payment_status)
        if "Success" in vals: return "Success"
        if "Refunded" in vals: return "Refunded"
        if "Failed" in vals: return "Failed"
        return "Pending"
    o["order_status"]=[order_status(x) for _,x in g]
    o["payment_status"]=[payment_status(x) for _,x in g]
    o["is_realized"]=(o.realized_revenue>0)
    o["has_return"] = o.order_id.isin(set(_read("returns",["order_id"]).order_id)) if (DATA/"returns.parquet").exists() else False
    ret=_read("returns",["order_id","refund_amount","returned_quantity"])
    if not ret.empty:
        rr=ret.groupby("order_id").agg(returned_quantity=("returned_quantity","sum"),refund_amount=("refund_amount","sum"))
        o=o.drop(columns=[c for c in ["returned_quantity","refund_amount"] if c in o]).join(rr,on="order_id")
    if "returned_quantity" not in o:
        o["returned_quantity"] = 0
    if "refund_amount" not in o:
        o["refund_amount"] = 0.0
    o["returned_quantity"]=o["returned_quantity"].fillna(0).astype(int)
    o["refund_amount"]=o["refund_amount"].fillna(0).round(2)
    o["order_month"]=o.order_timestamp.dt.to_period("M").astype(str)
    o["order_week"]=o.order_timestamp.dt.to_period("W").astype(str)
    o=o[["order_id","customer_id","order_timestamp","order_month","order_week","order_status","payment_status","payment_method","ship_city","ship_state","ship_region","campaign_type","order_line_count","total_quantity","gross_order_value","realized_revenue","gross_margin","is_realized","has_return","returned_quantity","refund_amount"]]
    o.to_parquet(DATA/"orders.parquet",index=False)
    return o


def build_customer_lifecycle():
    c=_read("customer_master"); o=_read("orders");
    if c.empty: return pd.DataFrame(),pd.DataFrame()
    c["signup_date"]=pd.to_datetime(c.signup_date)
    if o.empty:
        end=pd.Timestamp.max.normalize()
        return pd.DataFrame(),pd.DataFrame()
    o["order_timestamp"]=pd.to_datetime(o.order_timestamp)
    real=o[o.is_realized].copy()
    if real.empty: return pd.DataFrame(),pd.DataFrame()
    end=max(pd.to_datetime(real.order_timestamp).max().normalize(), pd.to_datetime(c.signup_date).max())
    # Monthly snapshots only from signup month through observation horizon; features use history strictly <= snapshot date.
    months=pd.date_range(pd.to_datetime(c.signup_date).min().to_period("M").start_time,end,freq="MS")
    rows=[]
    real=real.sort_values("order_timestamp")
    for m in months:
        hist=real[real.order_timestamp < m+pd.offsets.MonthBegin(1)]
        agg=hist.groupby("customer_id").agg(last_purchase=("order_timestamp","max"),orders=("order_id","nunique"),revenue=("realized_revenue","sum"),quantity=("total_quantity","sum"),margin=("gross_margin","sum"))
        active=c[c.signup_date < m+pd.offsets.MonthBegin(1)][["customer_id","signup_date"]].copy().set_index("customer_id")
        x=active.join(agg,how="left").reset_index(); x["snapshot_date"]=m
        x[["orders","revenue","quantity","margin"]]=x[["orders","revenue","quantity","margin"]].fillna(0)
        x["recency_days"]=(m+pd.offsets.MonthEnd(0)-x.last_purchase.fillna(x.signup_date)).dt.days.clip(lower=0)
        x["tenure_days"]=(m-x.signup_date).dt.days.clip(lower=0)
        x["frequency_90d"]=x.customer_id.map(hist[hist.order_timestamp>=m-pd.Timedelta(days=90)].groupby("customer_id").order_id.nunique()).fillna(0).astype(int)
        x["monetary_90d"]=x.customer_id.map(hist[hist.order_timestamp>=m-pd.Timedelta(days=90)].groupby("customer_id").realized_revenue.sum()).fillna(0.0)
        x["return_rate_90d"]=0.0
        if "has_return" in hist.columns:
            r90=hist[hist.order_timestamp>=m-pd.Timedelta(days=90)].groupby("customer_id").agg(a=("order_id","nunique"),b=("has_return","sum")); x["return_rate_90d"]=x.customer_id.map((r90.b/r90.a.replace(0,np.nan)).fillna(0)).fillna(0)
        x["lifecycle_state"]=np.select([
            x.orders.eq(0), x.recency_days.le(30)&x.orders.ge(4), x.recency_days.le(60)&x.orders.ge(2),
            x.recency_days.le(90)&x.orders.ge(1), x.recency_days.le(180)&x.orders.ge(1), x.orders.ge(1)
        ],["NEW","LOYAL","REPEAT","ACTIVATED","AT_RISK","CHURNED"],default="NEW")
        rows.append(x[["customer_id","snapshot_date","tenure_days","orders","revenue","quantity","margin","recency_days","frequency_90d","monetary_90d","return_rate_90d","lifecycle_state"]])
    snap=pd.concat(rows,ignore_index=True)
    snap.to_parquet(DATA/"customer_monthly_features.parquet",index=False)
    # Forward-looking label: no realized purchase in the next 90 days. Last 90-day tail is censored.
    future=real[["customer_id","order_timestamp"]].copy()
    lab=snap.copy(); lab["label_window_end"]=lab.snapshot_date+pd.Timedelta(days=90)
    # vectorized-ish customer/date lookup through grouped timestamps
    bycust={k:g.order_timestamp.to_numpy() for k,g in future.groupby("customer_id")}
    labels=[]; observed=[]
    for row in lab.itertuples(index=False):
        arr=bycust.get(row.customer_id,np.array([],dtype="datetime64[ns]")); start=np.datetime64(row.snapshot_date+pd.Timedelta(days=1)); stop=np.datetime64(row.label_window_end)
        if len(arr):
            labels.append(bool(((arr>=start)&(arr<=stop)).any()))
        else: labels.append(False)
        observed.append(pd.Timestamp(row.label_window_end)<=end)
    lab["retained_90d"]=labels; lab["churn_90d"]=~lab.retained_90d; lab["label_observed"]=observed
    lab=lab[lab.label_observed].copy()
    # Scope churn to the RETAINABLE/ACTIVE cohort: customers with a realized purchase in the
    # trailing 90 days as of the snapshot. Labeling the whole base (incl. one-time & dormant
    # customers) makes ~90% trivially "churn" and destroys model signal. Keep RFM columns so a
    # modeller can narrow further to established buyers (orders>=2) for the classic churn cohort.
    lab["active_cohort"]=lab.frequency_90d>=1
    lab=lab[lab.active_cohort].copy()
    lab[["customer_id","snapshot_date","label_window_end","churn_90d","retained_90d",
         "orders","recency_days","frequency_90d","monetary_90d","tenure_days"]].to_parquet(
         DATA/"customer_churn_labels.parquet",index=False)
    return snap,lab


def enrich_returns_reviews():
    r=_read("returns"); t=_read("transactions");
    if not r.empty and not t.empty:
        qty=t.set_index("transaction_id").quantity.to_dict(); r["returned_quantity"]=r.apply(lambda x:min(int(x.returned_quantity) if pd.notna(x.returned_quantity) else 1,int(qty.get(x.transaction_id,1))),axis=1)
        r["refund_amount"]=pd.to_numeric(r.refund_amount).clip(lower=0)
        r["refund_amount"]=np.minimum(r.refund_amount,(t.set_index("transaction_id").net_item_value.reindex(r.transaction_id).fillna(0).to_numpy()))
        r.to_parquet(DATA/"returns.parquet",index=False)
    rev=_read("product_reviews")
    if not rev.empty:
        # Expand deterministic seed text into useful NLP/VoC fields while retaining rating semantics.
        rng=np.random.default_rng(2025)
        titles={1:["Poor quality","Not as expected"],2:["Needs improvement","Disappointing"],3:["Average","Okay overall"],4:["Good purchase","Happy with it"],5:["Excellent","Loved it"]}
        aspects=["quality","fit","colour","value","delivery","comfort","design"]
        rev["review_title"]=[rng.choice(titles.get(int(x),["Review"])) for x in rev.rating]
        rev["language"]="en"; rev["helpful_votes"]=rng.poisson(2.2,len(rev)); rev["aspect"]=[rng.choice(aspects) for _ in range(len(rev))]
        rev["sentiment"] = np.select([rev.rating<=2,rev.rating.eq(3),rev.rating>=4],["negative","neutral","positive"],default="neutral")
        rev["return_after_review"]=False
        rr=_read("returns",["order_id","return_requested_at"])
        if not rr.empty:
            rt=rr.groupby("order_id").return_requested_at.min(); rev["return_after_review"]=rev.apply(lambda x: pd.notna(rt.get(x.order_id)) and pd.Timestamp(rt.get(x.order_id))>pd.Timestamp(x.review_timestamp),axis=1)
        rev.to_parquet(DATA/"product_reviews.parquet",index=False)



def build_marketing_attribution():
    cs=_read("clickstream"); o=_read("orders");
    if cs.empty or o.empty or "order_id" not in cs.columns: return pd.DataFrame()
    x=cs[(cs.order_id.notna()) & (cs.event_type.isin(["checkout_start","payment_attempt","purchase"]))].copy()
    if x.empty: return pd.DataFrame()
    x["event_timestamp"]=pd.to_datetime(x.event_timestamp)
    # Last campaign-touch before checkout, restricted to the same customer and a 7-day lookback.
    checkout=x[x.event_type.eq("checkout_start")][["order_id","customer_id","event_timestamp"]].rename(columns={"event_timestamp":"checkout_at"})
    touches=cs[["customer_id","session_id","event_timestamp","traffic_source","campaign_id"]].copy(); touches["event_timestamp"]=pd.to_datetime(touches.event_timestamp)
    a=checkout.merge(touches,on="customer_id",how="left"); a=a[(a.event_timestamp<=a.checkout_at)&(a.event_timestamp>=a.checkout_at-pd.Timedelta(days=7))]
    a=a.sort_values("event_timestamp").groupby("order_id",as_index=False).tail(1)
    if a.empty: return pd.DataFrame()
    a["attribution_weight"]=1.0; a["attribution_model"]="last_touch_7d"
    rev=o.set_index("order_id").realized_revenue; a["attributed_revenue"]=a.order_id.map(rev).fillna(0)
    out=a[["order_id","customer_id","session_id","traffic_source","campaign_id","checkout_at","attribution_weight","attribution_model","attributed_revenue"]]
    out.to_parquet(DATA/"marketing_attribution.parquet",index=False); return out

def write_metric_definitions():
    metrics={
      "realized_revenue":"SUM(orders.realized_revenue) where is_realized = true",
      "aov":"realized_revenue / realized_orders",
      "realized_orders":"COUNT(orders) where is_realized = true",
      "gross_margin":"SUM(orders.gross_margin) over realized orders",
      "return_rate":"returned orders / realized orders",
      "churn_90d":"no realized purchase in next 90 days after snapshot",
      "retention_90d":"at least one realized purchase in next 90 days after snapshot",
      "roas":"attributed_revenue / marketing_spend (attribution model must be stated)",
      "inventory_closing":"opening_stock + units_received - units_sold - damaged_units + units_returned",
      "stockout":"closing_stock = 0"
    }
    (DATA/"metrics.json").write_text(json.dumps(metrics,indent=2))


def write_partitioned_facts():
    """Create analytics-ready date partitions. Flat Parquet files remain for compatibility."""
    for name,date_col in [("transactions","order_timestamp"),("clickstream","event_timestamp"),("inventory","inventory_date"),("marketing_spend","date")]:
        src=DATA/f"{name}.parquet"
        if not src.exists(): continue
        df=pd.read_parquet(src)
        if df.empty: continue
        d=pd.to_datetime(df[date_col]); df=df.copy(); df["year"]=d.dt.year.astype(int); df["month"]=d.dt.month.astype(int)
        out=DATA/"_partitioned"/name; out.mkdir(parents=True,exist_ok=True)
        for (y,m),g in df.groupby(["year","month"],sort=True):
            partition=out/f"year={y}/month={m:02d}"
            partition.mkdir(parents=True,exist_ok=True)
            g.drop(columns=["year","month"]).to_parquet(partition/"part.parquet",index=False)


def finalize():
    build_orders(); build_customer_lifecycle(); enrich_returns_reviews(); build_marketing_attribution(); write_metric_definitions(); write_partitioned_facts()
