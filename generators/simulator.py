"""Stateful, relational ecommerce simulator.

The simulator is intentionally not a Faker/random row generator. It maintains customer,
product, inventory and sequence state and creates clickstream -> order -> transaction ->
inventory -> return/review/support relationships. Run initialize once, then increment.
"""
from __future__ import annotations
import json, math, os, time
from pathlib import Path
from collections import defaultdict
import numpy as np
import pandas as pd
import config as C

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"; DATA.mkdir(exist_ok=True)
STATE_DIR = DATA / "_state"; STATE_DIR.mkdir(exist_ok=True)
SOURCE = Path(__file__).resolve().parent / "product_details.csv"


def rng_for(day_or_seed):
    return np.random.default_rng(C.SEED + int(day_or_seed))


def load_state():
    p = STATE_DIR / "state.json"
    if not p.exists(): return {"next_customer": 1, "next_order": 1, "next_txn": 1, "next_event": 1, "next_session": 1, "next_return": 1, "next_review": 1, "next_support": 1, "last_date": None}
    return json.loads(p.read_text())


def save_state(s): (STATE_DIR / "state.json").write_text(json.dumps(s, indent=2))


def geo_rows():
    return [(reg, st, city) for reg, states in C.GEO.items() for st, cities in states.items() for city in cities]


def choose_geo(r, n):
    rows = geo_rows(); w = np.array([3 if city in {"Mumbai","Bengaluru","New Delhi","Hyderabad","Chennai","Pune","Kolkata"} else 1 for _,_,city in rows], float); w /= w.sum()
    return [rows[i] for i in r.choice(len(rows), n, p=w)]


def season_info(ts):
    key = pd.Timestamp(ts).strftime("%Y-%m")
    festival, fm = C.FESTIVALS.get(key, (None, 1.0))
    month = pd.Timestamp(ts).month
    base = 1.0
    if month in (4,5,6): base *= 1.08
    if month in (10,11,12): base *= 1.10
    return base * fm, festival


def product_demand_multiplier(row, ts):
    m = pd.Timestamp(ts).month
    art = str(row.h3_category)
    usage = str(row.usage)
    mult = 1.0
    if m in (4,5,6):
        if art in {"Tshirts","Tops","Dresses","Sandals","Flip Flops","Sunglasses"}: mult *= 1.35
        if art in {"Jackets","Sweaters","Sweatshirts"}: mult *= 0.75
    if m in (10,11):
        if usage == "Ethnic" or art in {"Kurtas","Kurtis","Sarees","Earrings","Handbags","Watches"}: mult *= 1.35
        if art in {"Tshirts","Track Pants"}: mult *= 1.05
    if m in (12,1,2):
        if art in {"Jackets","Sweaters","Sweatshirts","Boots"}: mult *= 1.35
    if usage == "Sports" and m in (1,2,3,4,5): mult *= 1.10
    return mult


def build_products(r, n):
    src = pd.read_csv(SOURCE)
    src.columns = [c.strip() for c in src.columns]
    src = src.dropna(subset=["id","productDisplayName","masterCategory","subCategory","articleType","baseColour","season","usage"]).copy()
    src["id"] = src["id"].astype(int)
    # Prefer the main commerce categories; fall back to full catalog if a requested sample is larger.
    preferred = src[src.masterCategory.isin(["Apparel","Footwear","Accessories"])].copy()
    pool = preferred if len(preferred) >= n else src
    if n >= len(pool): cat = pool.copy()
    else:
        # stratified sampling by master category and article type to retain recommendation coverage.
        target = min(n, len(pool)); groups = pool.groupby(["masterCategory","articleType"], group_keys=False)
        frac = target / len(pool)
        parts = []
        for _, g in groups:
            k = max(1, min(len(g), int(round(len(g)*frac))))
            parts.append(g.sample(k, random_state=int(r.integers(1, 2_000_000))))
        cat = pd.concat(parts, ignore_index=True)
        if len(cat) > target: cat = cat.sample(target, random_state=C.SEED).reset_index(drop=True)
    cat = cat.reset_index(drop=True)
    n = len(cat)
    cat["product_id"] = [f"prod_{i:06d}" for i in range(1, n+1)]
    # Brand is inferred from product text because the source has no dedicated brand field.
    cat["brand_name"] = cat.productDisplayName.str.split().str[0].replace({"Nan":"Generic"}).fillna("Generic")
    base_by_cat = {"Apparel": 1100, "Footwear": 2300, "Accessories": 1600, "Personal Care": 700, "Free Items": 300, "Sporting Goods": 1800, "Home": 1000}
    anchor = np.array([base_by_cat.get(x, 900) for x in cat.masterCategory], float)
    premium = np.array([3.0 if x=="Watches" else 2.3 if x=="Handbags" else 1.9 if x in {"Sports Shoes","Formal Shoes"} else 1.5 if x in {"Heels","Sunglasses"} else 1.0 for x in cat.articleType])
    mrp = np.clip(np.round(anchor*premium*r.uniform(.65,1.9,n)/10)*10, 149, 24999)
    cost = np.round(mrp*r.uniform(.42,.68,n),2)
    mop = np.round(np.maximum(cost*1.08, mrp*r.uniform(.65,.95,n)),2); mop = np.minimum(mop,mrp)
    launch = pd.Timestamp("2024-01-01") + pd.to_timedelta(r.integers(0, 650, n), unit="D")
    discontinue = launch + pd.to_timedelta(r.integers(420, 900, n), unit="D")
    ratings = np.round(np.clip(r.normal(4.05,.42,n),2.3,5),1)
    review_lambda = np.maximum(5, 65 + (ratings - 3.5) * 100)
    review_count = r.poisson(review_lambda, n)
    sellers = [f"sell_{i:04d}" for i in r.integers(1,C.N_SELLERS+1,n)]
    desc = [f"{nm}. {col} {art} for {gender or 'Unisex'}; {usage} use; {season} season." for nm,col,art,gender,usage,season in zip(cat.productDisplayName,cat.baseColour,cat.articleType,cat.gender,cat.usage,cat.season)]
    attrs = [json.dumps({"colour":c,"gender":g,"article_type":a,"usage":u,"season":s}) for c,g,a,u,s in zip(cat.baseColour,cat.gender,cat.articleType,cat.usage,cat.season)]
    pm = pd.DataFrame({
        "product_id":cat.product_id, "source_product_id":cat.id, "product_name":cat.productDisplayName.astype(str),
        "gender":cat.gender.astype(str), "h1_category":cat.masterCategory.astype(str), "h2_category":cat.subCategory.astype(str),
        "h3_category":cat.articleType.astype(str), "h4_colour":cat.baseColour.astype(str), "brand_name":cat.brand_name.astype(str),
        "season":cat.season.astype(str), "usage":cat.usage.astype(str), "catalog_year":cat.year.astype(int), "seller_id":sellers,
        "mrp":mrp, "current_price":mop, "unit_cost":cost, "discount_pct":np.round((1-mop/mrp)*100,2),
        "avg_rating":ratings, "review_count":review_count, "product_description":desc, "product_attributes":attrs,
        "launch_date":launch.date, "discontinue_date":discontinue.date, "is_active":(discontinue >= pd.Timestamp(C.DATA_END)), "is_prime_eligible":r.choice([True,False],n,p=[.72,.28]), "image_url":cat.link.fillna("").astype(str)
    })
    pm.to_parquet(DATA/"product_master.parquet", index=False)
    return pm


def build_sellers(r, products):
    existing = DATA/"seller_master.parquet"
    if existing.exists(): return pd.read_parquet(existing)
    geo=choose_geo(r,C.N_SELLERS); st=r.choice(C.SELLER_TYPES,C.N_SELLERS,p=C.SELLER_TYPE_WEIGHTS)
    df=pd.DataFrame({"seller_id":[f"sell_{i:04d}" for i in range(1,C.N_SELLERS+1)],"seller_name":[f"{r.choice(['Urban','Metro','Prime','Global','Nova','Royal'])} {r.choice(['Retail','Traders','Store','Enterprises','Fashion'])}" for _ in range(C.N_SELLERS)],"seller_type":st,"seller_city":[x[2] for x in geo],"seller_state":[x[1] for x in geo],"seller_region":[x[0] for x in geo],"onboarding_date":(pd.Timestamp("2020-01-01")+pd.to_timedelta(r.integers(0,1800,C.N_SELLERS),unit="D")).date,"seller_rating":np.round(np.clip(r.normal(4.1,.35,C.N_SELLERS),2.5,5),1),"fulfillment_score":np.round(np.clip(r.normal(.93,.05,C.N_SELLERS),.70,.995),3),"return_rate":np.round(np.clip(r.normal(.11,.04,C.N_SELLERS),.02,.30),3),"cancellation_rate":np.round(np.clip(r.normal(.025,.012,C.N_SELLERS),.002,.10),3),"is_active":True})
    df.to_parquet(existing,index=False); return df


def build_agents(r):
    path=DATA/"agent_master.parquet"
    if path.exists(): return pd.read_parquet(path)
    n=100
    df=pd.DataFrame({"agent_id":[f"agent_{i:03d}" for i in range(1,n+1)],"agent_name":[f"Agent {i:03d}" for i in range(1,n+1)],"team":r.choice(["Orders","Returns","Payments","Product Support"],n),"location":r.choice(["Mumbai","Bengaluru","Delhi","Hyderabad"],n),"hire_date":(pd.Timestamp("2021-01-01")+pd.to_timedelta(r.integers(0,1500,n),unit="D")).date,"language":r.choice(["English","Hindi","Marathi","Tamil","Bengali"],n,p=[.35,.30,.12,.12,.11]),"tenure_months":r.integers(6,60,n)})
    df.to_parquet(path,index=False); return df


def build_customers(r, n, state, start_id=1, start_date=None, end_date=None):
    path=DATA/"customer_master.parquet"
    geo=choose_geo(r,n)
    start_date=pd.Timestamp(start_date or C.DATA_START); end_date=pd.Timestamp(end_date or C.DATA_END)
    # Initial cohort represents customers already acquired at launch; incremental acquisition is added by run().
    signup_window = min(max((end_date-start_date).days+1,1), 30)
    signup=start_date+pd.to_timedelta(r.integers(0,signup_window,n),unit="D")
    acq=r.choice(C.ACQUISITION_CHANNELS,n,p=C.ACQ_WEIGHTS); gender=r.choice(C.GENDERS,n,p=C.GENDER_WEIGHTS)
    prime=r.random(n)<.42; prime_start=np.where(prime,signup+pd.to_timedelta(r.integers(0,180,n),unit="D"),np.datetime64("NaT")); prime_start=pd.to_datetime(prime_start); prime_start=np.minimum(prime_start.values,np.datetime64(C.DATA_END)).astype("datetime64[ns]")
    df=pd.DataFrame({"customer_id":[f"cust_{i:07d}" for i in range(start_id,start_id+n)],"signup_date":signup.date,"signup_city":[x[2] for x in geo],"signup_state":[x[1] for x in geo],"signup_region":[x[0] for x in geo],"acquisition_channel":acq,"acquisition_campaign":[f"{str(a).lower().replace('/','_').replace(' ','_')}_always_on" for a in acq],"age_bracket":r.choice(C.AGE_BRACKETS,n,p=C.AGE_WEIGHTS),"gender":gender,"is_prime_member":prime,"prime_start_date":pd.to_datetime(prime_start).date,"prime_tier":np.where(prime,"Prime","Non-Prime"),"email_opt_in":r.random(n)<.72,"sms_opt_in":r.random(n)<.48,"account_status":"active"})
    if not path.exists() or start_id == 1:
        df.to_parquet(path,index=False)
    state["next_customer"]=max(int(state.get("next_customer",1)), start_id+n)
    return df


def latent_customer_profiles(r, cust):
    p=DATA/"_state"/"customer_profiles.parquet"; existing=pd.read_parquet(p) if p.exists() else pd.DataFrame()
    have=set(existing.customer_id) if not existing.empty else set(); missing=cust[~cust.customer_id.isin(have)].copy()
    if missing.empty: return existing
    n=len(missing); q=missing.acquisition_channel.map(C.CHANNEL_QUALITY).to_numpy(); prime=missing.is_prime_member.to_numpy().astype(float)
    add=pd.DataFrame({"customer_id":missing.customer_id,"price_sensitivity":np.clip(r.beta(2.2,2.0,n),.03,.98),"discount_affinity":np.clip(r.beta(2.0,3.0,n),.02,.98),"purchase_propensity":np.clip(r.gamma(2.0,.32,n)*q*(1+.15*prime),.03,4.0),"engagement_level":np.clip(r.gamma(2.2,.45,n)*q*(1+.12*prime),.03,4.0),"return_tendency":np.clip(r.beta(1.7,10,n),.01,.40),"category_focus":r.choice(["Apparel","Footwear","Accessories"],n,p=[.56,.25,.19]),"colour_focus":r.choice(["Black","Blue","White","Brown","Red","Grey","Navy Blue","Pink"],n,p=[.25,.16,.12,.10,.10,.10,.09,.08]),"preferred_usage":r.choice(["Casual","Formal","Sports","Ethnic","Smart Casual"],n,p=[.52,.12,.12,.15,.09]),"device_pref":r.choice(C.DEVICES,n,p=C.DEVICE_WEIGHTS)})
    out=pd.concat([existing,add],ignore_index=True); out.to_parquet(p,index=False); return out

def product_weights(products, profile, ts, history_counts=None, candidate_idx=None):
    """Return normalized product weights.

    Performance note: the legacy implementation scored every catalog SKU for every
    order. That is O(orders * products). The simulator now optionally scores only a
    preference-filtered candidate set while preserving the same preference signals.
    """
    if candidate_idx is None:
        x = products
        idx = np.arange(len(x))
    else:
        idx = np.asarray(candidate_idx, dtype=np.int64)
        x = products.iloc[idx]
    score = np.ones(len(x), dtype=float)
    score *= np.clip((x.avg_rating.fillna(4.0).to_numpy() / 4.0) ** 1.2, .45, 1.5)
    score *= np.array([product_demand_multiplier(row, ts) for row in x.itertuples(index=False)])
    score *= np.where(x.h1_category.to_numpy() == profile.category_focus, 2.8, .55)
    score *= np.where(x.h4_colour.to_numpy() == profile.colour_focus, 1.6, .85)
    score *= np.where(x.usage.to_numpy() == profile.preferred_usage, 1.45, .85)
    price = x.current_price.to_numpy(dtype=float)
    med = float(np.median(price)) if len(price) else 1.0
    ps = float(profile.price_sensitivity)
    score *= np.exp(-ps * np.clip((price / max(med, 1.0)) - 1, -.8, 4))
    if history_counts:
        score *= np.array([1 + min(history_counts.get(pid, 0), 5) * .08 for pid in x.product_id])
    score = np.nan_to_num(score, nan=1.0, posinf=1e6)
    score = np.maximum(score, 1e-9)
    score_sum = score.sum()
    return idx, score / score_sum if score_sum > 0 else np.full(len(score), 1 / len(score))


def simulate_orders(r, dates, cust, prof, products, sellers, state):
    txn_path=DATA/"transactions.parquet"; old=pd.read_parquet(txn_path) if txn_path.exists() else pd.DataFrame()
    last_purchase={}
    if not old.empty:
        realized_old=old[(old.payment_status == "Success") & (old.net_item_value > 0)]
        if not realized_old.empty:
            last_purchase=realized_old.groupby("customer_id").order_timestamp.max().to_dict()
    prof_idx=prof.set_index("customer_id"); cust_geo=cust.set_index("customer_id")[["signup_city","signup_state","signup_region"]]; seller_idx=sellers.set_index("seller_id")
    history=defaultdict(int)
    if not old.empty:
        history.update(old.product_id.value_counts().to_dict())
    order_no=int(state["next_order"]); txn_no=int(state["next_txn"]); txn_rows=[]; order_events=[]
    date_start=pd.Timestamp(dates.min()); date_end=pd.Timestamp(dates.max())
    product_ids=products.product_id.to_numpy()
    cat_pools={k:g.index.to_numpy() for k,g in products.groupby("h1_category")}
    art_pools={k:g.index.to_numpy() for k,g in products.groupby("h3_category")}
    usage_pools={k:g.index.to_numpy() for k,g in products.groupby("usage")}
    # Precompute daily marketing index once; never read parquet inside customer loops.
    mkt_path=DATA/"marketing_spend.parquet"
    if mkt_path.exists():
        md=pd.read_parquet(mkt_path); md["date"]=pd.to_datetime(md.date); daily_sp=md.groupby("date").spend.sum(); base_sp=float(daily_sp.median()) if len(daily_sp) else 1.0
    else: daily_sp=pd.Series(dtype=float); base_sp=1.0
    order_start_time = time.perf_counter()
    total_customers = len(cust)
    for customer_no, c in enumerate(cust.itertuples(index=False), 1):
        cid=c.customer_id; p=prof_idx.loc[cid]; signup=max(pd.Timestamp(c.signup_date),date_start)
        if customer_no == 1 or customer_no % max(1, total_customers // 10) == 0 or customer_no == total_customers:
            print(f"[ORDERS] customers {customer_no:,}/{total_customers:,} | {time.perf_counter()-order_start_time:.1f}s", flush=True)
        if signup>date_end: continue
        months=pd.period_range(signup.to_period("M"),date_end.to_period("M"),freq="M")
        for per in months:
            mstart=max(per.start_time,date_start,signup); mend=min(per.end_time,date_end)
            sm,_=season_info(mstart); days=max((mend-mstart).days+1,1)
            rate=float(p.purchase_propensity)*0.22*sm*(1.0 if mend.weekday()>=4 else .98)
            # Marketing lift: daily channel spend is generated for the same window and acts as an acquisition/traffic driver.
            mkt=float(daily_sp.get(mstart,base_sp))/max(base_sp,1.0) if len(daily_sp) else 1.0; rate*=max(.75,min(1.35,0.92+0.10*mkt))
            lp=last_purchase.get(cid); recency=(mstart-pd.Timestamp(lp)).days if lp is not None else 999
            if recency>45: rate*=max(.20,1-(recency-45)/240)
            if mstart>=pd.Timestamp(C.SHOCK_DATE): rate*=max(.60,1-.32*float(p.price_sensitivity))
            n_orders=int(r.poisson(max(rate,0.001)))
            if n_orders==0: continue
            for _ in range(n_orders):
                day=mstart+pd.Timedelta(days=int(r.integers(0,days))); ts=day+pd.Timedelta(seconds=int(r.integers(8*3600,23*3600)))
                k=int(r.choice([1,2,3,4],p=[.60,.25,.11,.04]))
                pool=cat_pools.get(p.category_focus, np.arange(len(products)))
                # Hierarchical preference sampling: category is strongest, usage/colour are soft modifiers.
                if r.random()<.65 and p.preferred_usage in usage_pools:
                    up=np.intersect1d(pool,usage_pools[p.preferred_usage],assume_unique=False); pool=up if len(up) else pool
                if r.random()<.55 and p.colour_focus in products.h4_colour.unique():
                    cp=np.intersect1d(pool,products.index[products.h4_colour==p.colour_focus].to_numpy(),assume_unique=False); pool=cp if len(cp) else pool
                # Score a bounded candidate set instead of the full catalog for every order.
                # Preference pools keep category/usage/colour quality while reducing CPU substantially.
                available_mask=(pd.to_datetime(products.iloc[pool].launch_date).to_numpy() <= np.datetime64(ts)) & (pd.to_datetime(products.iloc[pool].discontinue_date).to_numpy() >= np.datetime64(ts))
                if not available_mask.any():
                    continue
                pool=pool[available_mask]
                candidate_n=min(320, len(pool))
                if len(pool) > candidate_n:
                    candidate_idx=r.choice(pool, size=candidate_n, replace=False)
                else:
                    candidate_idx=pool
                score_idx, score_w=product_weights(products, p, mstart, history_counts=history, candidate_idx=candidate_idx)
                anchor_idx=int(r.choice(score_idx,p=score_w)); chosen=[anchor_idx]; anchor=products.iloc[anchor_idx]
                comp=C.COMPLEMENTS.get(anchor.h3_category,[])
                for _ in range(k-1):
                    if comp and r.random()<.68:
                        target=r.choice(comp); cp=art_pools.get(target)
                        if cp is not None and len(cp):
                            cp_mask=(pd.to_datetime(products.iloc[cp].launch_date).to_numpy() <= np.datetime64(ts)) & (pd.to_datetime(products.iloc[cp].discontinue_date).to_numpy() >= np.datetime64(ts))
                            cp=cp[cp_mask]
                            if len(cp):
                                chosen.append(int(r.choice(cp))); continue
                    chosen.append(int(r.choice(pool)))
                oid=f"ord_{order_no:09d}"; order_no+=1; plist=[]
                for pi in chosen:
                    pr=products.iloc[pi]; qty=int(r.choice([1,2,3],p=[.84,.13,.03])); disc=float(np.clip(r.normal(.10 if p.discount_affinity>.55 else .05,.05),0,.38)); price=max(float(pr.unit_cost)*1.05,float(pr.current_price)*(1-disc))
                    if ts>=pd.Timestamp(C.SHOCK_DATE): price=min(float(pr.mrp),price*float(r.uniform(1.06,1.11)))
                    sm=seller_idx.loc[pr.seller_id]
                    seller_cancel=float(sm.cancellation_rate); seller_return=float(sm.return_rate); fulfill=float(sm.fulfillment_score)
                    payment_method=r.choice(["UPI","COD","Credit Card","Debit Card","Net Banking","Amazon Pay"],p=[.36,.20,.17,.10,.08,.09])
                    fail_p={"UPI":.035,"COD":.018,"Credit Card":.028,"Debit Card":.032,"Net Banking":.045,"Amazon Pay":.025}[payment_method]
                    fail_p += max(0,.95-fulfill)*.20
                    rto_p=.008 + (.045 if payment_method=="COD" else .006) + max(0,.90-fulfill)*.08 + .08*float(p.return_tendency)
                    u=r.random()
                    if u<seller_cancel: status="CIC"
                    elif u<seller_cancel+rto_p: status="RTO"
                    elif u<seller_cancel+rto_p+max(.005,.025*(1-fulfill)): status="Undelivered"
                    elif u<seller_cancel+rto_p+max(.005,.025*(1-fulfill))+.01: status="Lost"
                    else: status="Delivered" if r.random()<.91 else "CIR"
                    pay_status=("Refunded" if status=="RTO" else ("Failed" if r.random()<fail_p else ("Success" if status in {"Delivered","CIR"} else "Pending")))
                    delivered_candidate=ts+pd.Timedelta(days=int(r.integers(2,8))) if status in {"Delivered","CIR"} else pd.NaT
                    delivered=delivered_candidate if pd.isna(delivered_candidate) or delivered_candidate <= date_end else pd.NaT
                    coupon=r.choice(["DIWALI10","BANK500","PRIME15","SAVE20","FIRST100"]) if disc>.15 and r.random()<.5 else None
                    geo=cust_geo.loc[cid]
                    ship_idx=int(r.choice([0,1,2],p=[.82,.12,.06])); ship=choose_geo(r,1)[0] if ship_idx else (geo.signup_city,geo.signup_state,geo.signup_region)
                    festival=season_info(ts)[1]; campaign=f"{str(c.acquisition_channel).lower().replace('/','_').replace(' ','_')}_{ts.strftime('%Y%m')}"
                    gross=round(price*qty,2); discount=round(max(float(pr.mrp)-price,0)*qty,2)
                    net=gross if pay_status=="Success" and status not in {"RTO","Undelivered","Lost"} else 0.0
                    margin=round(net-float(pr.unit_cost)*qty,2) if net>0 else 0.0
                    txn_rows.append((f"txn_{txn_no:010d}",oid,cid,pr.product_id,pr.seller_id,ts,qty,float(pr.mrp),float(pr.current_price),round(price,2),float(pr.unit_cost),discount,coupon,campaign,payment_method,status,pay_status,ship[0],ship[1],ship[2],delivered,gross,net,margin)); txn_no+=1; history[pr.product_id]+=1; plist.append(pr.product_id)
                order_events.append((oid,cid,ts,plist))
                if any((row[1] == oid and row[16] == "Success" and float(row[22]) > 0) for row in txn_rows[-len(chosen):]):
                    last_purchase[cid]=ts
    cols=["transaction_id","order_id","customer_id","product_id","seller_id","order_timestamp","quantity","mrp","listed_price","unit_selling_price","unit_cost","discount_amount","coupon_code","campaign_type","payment_method","order_status","payment_status","ship_city","ship_state","ship_region","delivered_timestamp","gross_item_value","net_item_value","gross_margin"]
    df=pd.DataFrame(txn_rows,columns=cols)
    if not old.empty: df=pd.concat([old,df],ignore_index=True)
    df.to_parquet(txn_path,index=False); state["next_order"]=order_no; state["next_txn"]=txn_no
    return pd.DataFrame(txn_rows,columns=cols),order_events

def simulate_clickstream(r, dates, cust, prof, products, orders, state):
    path=DATA/"clickstream.parquet"; old=pd.read_parquet(path) if path.exists() else pd.DataFrame(); rows=[]; event_no=int(state["next_event"]); sess_no=int(state["next_session"])
    txn_all=pd.read_parquet(DATA/"transactions.parquet") if (DATA/"transactions.parquet").exists() else pd.DataFrame()
    txn_statuses_for_order={}
    if not txn_all.empty:
        txn_statuses_for_order=txn_all.groupby("order_id").payment_status.apply(lambda x: bool((x == "Success").all())).to_dict()
    pids=products.product_id.to_numpy(); prof_idx=prof.set_index("customer_id")
    for oid,cid,ts,plist in orders:
        p=prof_idx.loc[cid]; sid=f"sess_{sess_no:09d}"; sess_no+=1; dev=p.device_pref; tr=r.choice(C.TRAFFIC_SOURCES); t=pd.Timestamp(ts)-pd.Timedelta(seconds=int(r.integers(120,900)))
        def add(et,page=None,pid=None,q=None):
            nonlocal t,event_no; rows.append((f"evt_{event_no:011d}",sid,cid,oid if et in {"checkout_start","payment_attempt","payment_failed","purchase"} else None,t,et,page,pid,q,dev,tr, f"cmp_{tr}_{t.strftime('%Y%m')}")); event_no+=1; t+=pd.Timedelta(seconds=int(r.integers(4,65)))
        add("session_start","home"); add("home_view","home")
        if r.random()<.68: add("search","search_results",None,f"{p.colour_focus.lower()} {p.category_focus.lower()}")
        add("plp_view","plp"); add("pdp_view","pdp",plist[0])
        if len(plist)>1 and r.random()<.35: add("pdp_view","pdp",plist[1])
        add("add_to_cart","cart",plist[0]);
        if r.random()<.20: add("wishlist_add","pdp",plist[0])
        add("checkout_start","checkout")
        successful = bool(txn_statuses_for_order.get(oid, True))
        add("payment_attempt","payment")
        if successful: add("purchase","confirmation",plist[0])
        else: add("payment_failed","payment")
        add("session_end",None)
    # Non-converting browsing sessions are generated by customer-month engagement, not daily row loops.
    start=pd.Timestamp(dates.min()); end=pd.Timestamp(dates.max())
    for _,c in cust.iterrows():
        if pd.Timestamp(c.signup_date)>end: continue
        p=prof_idx.loc[c.customer_id]; first=max(pd.Timestamp(c.signup_date),start); months=pd.period_range(first.to_period("M"),end.to_period("M"),freq="M")
        for per in months:
            mstart=max(per.start_time,first); mend=min(per.end_time,end); n=int(r.poisson(max(.05,float(p.engagement_level)*1.35)))
            for _ in range(n):
                t=mstart+pd.Timedelta(days=int(r.integers(0,max((mend-mstart).days+1,1))))+pd.Timedelta(seconds=int(r.integers(7*3600,23*3600)))
                sid=f"sess_{sess_no:09d}"; sess_no+=1; dev=p.device_pref; tr=r.choice(C.TRAFFIC_SOURCES)
                def add2(et,page=None,pid=None,q=None):
                    nonlocal t,event_no; rows.append((f"evt_{event_no:011d}",sid,c.customer_id,None,t,et,page,pid,q,dev,tr, f"cmp_{tr}_{t.strftime('%Y%m')}")); event_no+=1; t+=pd.Timedelta(seconds=int(r.integers(5,80)))
                add2("session_start","home")
                if r.random()<.55: add2("home_view","home")
                if r.random()<.60: add2("search","search_results",None,r.choice([f"{p.colour_focus.lower()} {p.category_focus.lower()}",f"{p.preferred_usage.lower()} wear",f"{p.preferred_usage.lower()} {p.category_focus.lower()}"]))
                add2("plp_view","plp")
                nviews=int(r.choice([1,2,3],p=[.55,.30,.15]))
                for _ in range(nviews): add2("pdp_view","pdp",r.choice(pids))
                if r.random()<.16: add2("filter_apply","plp")
                if r.random()<.10: add2("sort_apply","plp")
                if r.random()<.28: add2("add_to_cart","cart",rows[-1][7] if rows[-1][7] else r.choice(pids))
                if r.random()<.10: add2("wishlist_add","pdp",rows[-1][7] if rows[-1][7] else r.choice(pids))
                if r.random()<.18: add2("remove_from_cart","cart",rows[-1][7] if rows[-1][7] else r.choice(pids))
                add2("session_end",None)
    cols=["event_id","session_id","customer_id","order_id","event_timestamp","event_type","page_type","product_id","search_query","device_type","traffic_source","campaign_id"]
    df=pd.DataFrame(rows,columns=cols)
    if not old.empty: df=pd.concat([old,df],ignore_index=True)
    df.to_parquet(path,index=False); state["next_event"]=event_no; state["next_session"]=sess_no; return df

def simulate_inventory(r, dates, products, transactions, returns_df=None):
    """Generate inventory history with vectorized daily calculations.

    Inventory is path-dependent, so days remain sequential, but the product dimension
    is fully vectorized. This removes millions of Python-level product loops while
    preserving the same stock-flow semantics.
    """
    path = DATA / "inventory.parquet"
    old = pd.read_parquet(path) if path.exists() else pd.DataFrame()
    wh = np.array(["WH_MUM", "WH_DEL", "WH_BLR", "WH_HYD"], dtype=object)

    pids = products.product_id.to_numpy()
    sellers = products.seller_id.to_numpy()
    costs = products.unit_cost.to_numpy(dtype=float)

    # Stable warehouse assignment, computed once rather than once per product/day.
    import hashlib
    wh_idx = np.array([
        int(hashlib.md5(str(pid).encode()).hexdigest(), 16) % len(wh)
        for pid in pids
    ], dtype=np.int8)
    warehouses = wh[wh_idx]

    latest = np.zeros(len(products), dtype=np.int64)
    initialized = np.zeros(len(products), dtype=bool)
    key_to_idx = {(pid, sid, warehouse): i for i, (pid, sid, warehouse) in enumerate(zip(pids, sellers, warehouses))}

    if not old.empty:
        z = old.sort_values("inventory_date").groupby(
            ["product_id", "seller_id", "warehouse_id"], as_index=False
        ).tail(1)
        for x in z.itertuples(index=False):
            i = key_to_idx.get((x.product_id, x.seller_id, x.warehouse_id))
            if i is not None:
                latest[i] = int(x.closing_stock)
                initialized[i] = True

    valid_sales = (
        transactions[(transactions.payment_status == "Success") & (transactions.net_item_value > 0)]
        if not transactions.empty else pd.DataFrame()
    )
    if not valid_sales.empty:
        sold = (
            valid_sales.assign(inventory_date=pd.to_datetime(valid_sales.order_timestamp).dt.normalize())
            .groupby(["inventory_date", "product_id", "seller_id"], as_index=False)
            .quantity.sum()
        )
    else:
        sold = pd.DataFrame(columns=["inventory_date", "product_id", "seller_id", "quantity"])

    if returns_df is not None and not returns_df.empty:
        rt = returns_df.copy()
        rt["return_requested_at"] = pd.to_datetime(rt.return_requested_at)
        txmeta = transactions[["transaction_id", "product_id", "seller_id"]].drop_duplicates("transaction_id")
        rt = rt.drop(columns=["product_id", "seller_id"], errors="ignore")
        rt = rt.merge(txmeta, on="transaction_id", how="left")
        returned = (
            rt.assign(inventory_date=rt.return_requested_at.dt.normalize())
            .groupby(["inventory_date", "product_id", "seller_id"], as_index=False)
            .size()
            .rename(columns={"size": "returned"})
        )
    else:
        returned = pd.DataFrame(columns=["inventory_date", "product_id", "seller_id", "returned"])

    sold_lookup = sold.set_index(["inventory_date", "product_id", "seller_id"])["quantity"].to_dict()
    return_lookup = returned.set_index(["inventory_date", "product_id", "seller_id"])["returned"].to_dict()

    rows = []
    progress_every = max(1, len(dates) // 12)
    start_time = time.perf_counter()

    for day_no, day in enumerate(dates, 1):
        day_ts = pd.Timestamp(day).normalize()
        day_key = day_ts.date()
        opening = np.where(initialized, latest, r.integers(35, 220, len(products))).astype(np.int64)

        reorder = np.maximum(10, (opening * .25).astype(np.int64))
        rec_lambda = np.where(
            opening <= reorder,
            np.maximum(1, reorder * 2 - opening),
            0
        )
        received = r.poisson(rec_lambda).astype(np.int64)

        sold_arr = np.fromiter(
            (sold_lookup.get((day_ts, pid, sid), 0) for pid, sid in zip(pids, sellers)),
            dtype=np.int64,
            count=len(products)
        )
        returned_arr = np.fromiter(
            (return_lookup.get((day_ts, pid, sid), 0) for pid, sid in zip(pids, sellers)),
            dtype=np.int64,
            count=len(products)
        )

        demand_arr = sold_arr.copy()
        capacity = opening + received + returned_arr
        sold_arr = np.minimum(demand_arr, capacity).astype(np.int64)
        lost_sales = np.maximum(0, demand_arr - sold_arr).astype(np.int64)
        damaged = r.binomial(sold_arr, .01).astype(np.int64)
        closing = np.maximum(0, capacity - sold_arr - damaged).astype(np.int64)
        reorder_qty = np.maximum(0, reorder * 2 - closing)
        status = np.where(
            closing == 0, "OUT_OF_STOCK",
            np.where(closing <= reorder, "LOW_STOCK", "HEALTHY")
        )
        inv_value = np.round(closing * costs, 2)

        rows.append(pd.DataFrame({
            "inventory_date": day_key,
            "product_id": pids,
            "seller_id": sellers,
            "warehouse_id": warehouses,
            "opening_stock": opening,
            "units_received": received,
            "demand_units": demand_arr,
            "units_sold": sold_arr,
            "lost_sales_units": lost_sales,
            "units_returned": returned_arr,
            "damaged_units": damaged,
            "closing_stock": closing,
            "reorder_point": reorder,
            "reorder_quantity": reorder_qty,
            "stock_status": status,
            "inventory_value": inv_value,
        }))

        latest = closing
        initialized[:] = True

        if day_no == 1 or day_no % progress_every == 0 or day_no == len(dates):
            elapsed = time.perf_counter() - start_time
            print(f"[INVENTORY] {day_key} ({day_no:,}/{len(dates):,}) | {elapsed:.1f}s", flush=True)

    df = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    if not old.empty:
        df = pd.concat([old, df], ignore_index=True)
    df.to_parquet(path, index=False)
    return df


def simulate_returns_reviews_support(r, transactions, state):
    retp=DATA/"returns.parquet"; revp=DATA/"product_reviews.parquet"; supp=DATA/"support_contacts.parquet"
    oldret=pd.read_parquet(retp) if retp.exists() else pd.DataFrame(); oldrev=pd.read_parquet(revp) if revp.exists() else pd.DataFrame(); olds=pd.read_parquet(supp) if supp.exists() else pd.DataFrame()
    if transactions.empty: return
    delivered=transactions[transactions.order_status.isin(["Delivered","CIR"]) & transactions.delivered_timestamp.notna()].copy()
    delivered["delivered_timestamp"]=pd.to_datetime(delivered["delivered_timestamp"])
    rr=int(state["next_return"]); rv=int(state["next_review"]); sp=int(state["next_support"])
    product_master=pd.read_parquet(DATA/"product_master.parquet") if (DATA/"product_master.parquet").exists() else pd.DataFrame()
    product_meta=product_master.set_index("product_id").to_dict("index") if not product_master.empty else {}
    profile_path=STATE_DIR/"customer_profiles.parquet"; profile_df=pd.read_parquet(profile_path) if profile_path.exists() else pd.DataFrame()
    return_map=profile_df.set_index("customer_id").return_tendency.to_dict() if not profile_df.empty else {}
    retrows=[]; revrows=[]; srows=[]
    for x in delivered.itertuples():
        art=str(x.product_id)
        # Customer/product-aware return propensity. The simulator profile is not exposed as a feature in the warehouse.
        base=.10
        if str(x.product_id).lower().startswith("prod_"): base += .00
        # Footwear/apparel generally have more size/fit-driven returns.
        pmeta=product_meta.get(x.product_id,{})
        if pmeta.get("h1_category") in {"Apparel","Footwear"}: base += .05
        if pmeta.get("h3_category") in {"Shoes","Sports Shoes","Casual Shoes","Heels","Sandals"}: base += .04
        base=float(np.clip(base + 0.22*float(return_map.get(x.customer_id, .10)), .04, .40))
        if r.random()<base:
            reason=r.choice(["Size Issue","Fit Issue","Colour Mismatch","Quality Issue","Wrong Product","Damaged","Changed Mind"],p=[.24,.16,.10,.18,.08,.07,.17]); req=pd.Timestamp(x.delivered_timestamp)+pd.Timedelta(days=int(r.integers(1,14)))
            end_ts=pd.Timestamp(C.DATA_END).normalize()+pd.Timedelta(days=1)-pd.Timedelta(microseconds=1)
            if req <= end_ts:
                received=req+pd.Timedelta(days=int(r.integers(1,6)))
                received=min(received,end_ts)
                retrows.append((f"ret_{rr:09d}",x.transaction_id,x.order_id,x.customer_id,x.product_id,x.seller_id,req,received,reason,"refund",round(x.net_item_value,2),int(x.quantity),"completed")); rr+=1
        # Reviews only after delivery, rating correlated loosely with seller/customer experience.
        if r.random()<.42:
            rating=int(np.clip(round(r.normal(4.1,.9)),1,5)); texts={5:"Loved the product and quality.",4:"Good product, as expected.",3:"Decent product, but could be better.",2:"Not fully satisfied with the quality or fit.",1:"Disappointed with the product."}
            review_ts=pd.Timestamp(x.delivered_timestamp)+pd.Timedelta(days=int(r.integers(1,8)))
            end_ts=pd.Timestamp(C.DATA_END).normalize()+pd.Timedelta(days=1)-pd.Timedelta(microseconds=1)
            review_ts=min(review_ts,end_ts)
            if review_ts >= pd.Timestamp(x.delivered_timestamp):
                revrows.append((f"rev_{rv:09d}",x.customer_id,x.product_id,x.order_id,x.transaction_id,review_ts,rating,texts[rating],rating>=4, None)); rv+=1
        if r.random()<.045:
            issue=r.choice(["Return","Exchange","Size","Fit","Delivery","Refund","Product Quality","Stock Availability"]); srows.append((f"sup_{sp:09d}",x.customer_id,x.order_id,x.transaction_id,x.product_id,x.delivered_timestamp,r.choice(["chat","email","phone"]),issue,r.choice(["Resolved","Pending","Escalated"],p=[.78,.17,.05]),round(float(r.gamma(2,2)),1),f"agent_{int(r.integers(1,101)):03d}",r.choice(["positive","neutral","negative"],p=[.55,.25,.20]),int(r.integers(1,6)))); sp+=1
    rc=["return_id","transaction_id","order_id","customer_id","product_id","seller_id","return_requested_at","return_received_at","return_reason","return_type","refund_amount","returned_quantity","return_status"]
    vc=["review_id","customer_id","product_id","order_id","transaction_id","review_timestamp","rating","review_text","verified_purchase","return_after_review"]
    sc=["contact_id","customer_id","order_id","transaction_id","product_id","contact_timestamp","contact_channel","issue_category","resolution_status","resolution_time_hours","agent_id","customer_sentiment","satisfaction_score"]
    if retrows:
        d=pd.DataFrame(retrows,columns=rc); d=pd.concat([oldret,d],ignore_index=True) if not oldret.empty else d; d.to_parquet(retp,index=False)
    elif not retp.exists(): pd.DataFrame(columns=rc).to_parquet(retp,index=False)
    if revrows:
        d=pd.DataFrame(revrows,columns=vc); d=pd.concat([oldrev,d],ignore_index=True) if not oldrev.empty else d; d.to_parquet(revp,index=False)
    elif not revp.exists(): pd.DataFrame(columns=vc).to_parquet(revp,index=False)
    if srows:
        d=pd.DataFrame(srows,columns=sc); d=pd.concat([olds,d],ignore_index=True) if not olds.empty else d; d.to_parquet(supp,index=False)
    elif not supp.exists(): pd.DataFrame(columns=sc).to_parquet(supp,index=False)
    state["next_return"]=rr; state["next_review"]=rv; state["next_support"]=sp
    return (pd.DataFrame(retrows,columns=rc) if retrows else pd.DataFrame(columns=rc))


def generate_marketing(r, dates, state):
    path=DATA/"marketing_spend.parquet"; old=pd.read_parquet(path) if path.exists() else pd.DataFrame()
    channels=[("Paid Search","Google Search",90000,.20),("Paid Search","Google Shopping",55000,.20),("Social Ads","Meta",65000,.45),("Social Ads","Instagram",70000,.50),("Display/DSP","DSP",70000,.55),("Affiliate","Affiliate Network",30000,.20),("Email/CRM","Email",7000,.10)]
    rows=[]
    channel_index={ch:i for i,ch in enumerate(channels)}
    for day in dates:
        sm,_=season_info(day)
        for grp,name,base,theta in channels:
            # independent flighting prevents perfectly collinear spend.
            ci=channel_index[(grp,name,base,theta)]
            phase=1+.18*np.sin(day.dayofyear/(7+ci*3)+ci)
            if grp=="Email/CRM": phase*=2.0 if day.dayofweek==2 else .35
            if grp=="Social Ads": phase*=1.25 if day.dayofweek in (4,5) else .9
            spend=base*phase*sm*r.uniform(.86,1.14); imps=int(spend/(65 if grp=="Social Ads" else 45)*1000); clicks=int(imps*r.uniform(.008,.04)); orders=int(clicks*r.uniform(.01,.07))
            rows.append((day.date(),grp,name,f"cmp_{name.lower().replace(' ','_').replace('/','_')}_{day.strftime('%Y%m')}","West",round(spend*.25,2),int(imps*.25),int(clicks*.25),int(orders*.25))); rows.append((day.date(),grp,name,f"cmp_{name.lower().replace(' ','_').replace('/','_')}_{day.strftime('%Y%m')}","North",round(spend*.25,2),int(imps*.25),int(clicks*.25),int(orders*.25))); rows.append((day.date(),grp,name,f"cmp_{name.lower().replace(' ','_').replace('/','_')}_{day.strftime('%Y%m')}","South",round(spend*.25,2),int(imps*.25),int(clicks*.25),int(orders*.25))); rows.append((day.date(),grp,name,f"cmp_{name.lower().replace(' ','_').replace('/','_')}_{day.strftime('%Y%m')}","East",round(spend*.25,2),int(imps*.25),int(clicks*.25),int(orders*.25)))
    cols=["date","channel_group","channel_name","campaign_id","region","spend","impressions","clicks","orders_attributed"]
    d=pd.DataFrame(rows,columns=cols); d=pd.concat([old,d],ignore_index=True) if not old.empty else d; d.to_parquet(path,index=False)
    return d

def refresh_mmm():
    spend=pd.read_parquet(DATA/"marketing_spend.parquet"); spend["date"]=pd.to_datetime(spend.date)
    txn=pd.read_parquet(DATA/"transactions.parquet"); txn["order_timestamp"]=pd.to_datetime(txn.order_timestamp)
    weekly=txn.assign(revenue=txn.net_item_value).set_index("order_timestamp").revenue.resample("W-MON").sum().reset_index().rename(columns={"order_timestamp":"week_start","revenue":"observed_revenue"})
    wide=spend.groupby([pd.Grouper(key="date",freq="W-MON"),"channel_name"]).spend.sum().reset_index().pivot(index="date",columns="channel_name",values="spend").fillna(0).reset_index()
    tgt=weekly.merge(wide,left_on="week_start",right_on="date",how="left").drop(columns=["date"],errors="ignore"); tgt.to_parquet(DATA/"mmm_weekly_target.parquet",index=False)



def generate_product_affinity(r, products):
    """Build a compact product graph using cached catalog indexes and observed behavior."""
    path = DATA / "product_affinity.parquet"
    txn_path = DATA / "transactions.parquet"
    tx = pd.read_parquet(txn_path) if txn_path.exists() else pd.DataFrame()
    pmeta = products.set_index("product_id")

    pair_counts = defaultdict(int)
    if not tx.empty:
        for _, g in tx.loc[tx.payment_status.eq("Success")].groupby("order_id", sort=False):
            ids = list(dict.fromkeys(g.product_id.tolist()))
            for i, a in enumerate(ids):
                for b in ids[i + 1:]:
                    pair_counts[(a, b)] += 1
                    pair_counts[(b, a)] += 1

    view_counts = defaultdict(int)
    cs_path = DATA / "clickstream.parquet"
    if cs_path.exists():
        cs = pd.read_parquet(cs_path, columns=["session_id", "product_id"])
        cs = cs[cs.product_id.notna()]
        for _, g in cs.groupby("session_id", sort=False):
            ids = list(dict.fromkeys(g.product_id.tolist()))
            for i, a in enumerate(ids):
                for b in ids[i + 1:]:
                    view_counts[(a, b)] += 1
                    view_counts[(b, a)] += 1

    # Cache article-type pools once. The old version filtered the entire product
    # dataframe for every product, which is unnecessarily O(products^2).
    art_pools = {k: g.product_id.to_numpy() for k, g in products.groupby("h3_category", sort=False)}
    rows = []
    seen = set()

    for p in products.itertuples(index=False):
        comp = C.COMPLEMENTS.get(p.h3_category, [])
        candidates = []

        for art in comp:
            pool = art_pools.get(art)
            if pool is not None and len(pool):
                candidates.extend(r.choice(pool, size=min(3, len(pool)), replace=False).tolist())

        # Similar products: use the cached same-article pool and then score for
        # usage/colour similarity instead of filtering the full dataframe again.
        sim = art_pools.get(p.h3_category)
        if sim is not None and len(sim) > 1:
            sample_n = min(8, len(sim))
            sample = r.choice(sim, size=sample_n, replace=False)
            scored = []
            for q in sample:
                if q == p.product_id:
                    continue
                qr = pmeta.loc[q]
                sim_score = (2 if qr.usage == p.usage else 0) + (1 if qr.h4_colour == p.h4_colour else 0)
                scored.append((sim_score, q))
            scored.sort(reverse=True)
            candidates.extend([q for _, q in scored[:2]])

        for q in candidates:
            if q == p.product_id or (p.product_id, q) in seen:
                continue
            pc = pair_counts.get((p.product_id, q), 0)
            vc = view_counts.get((p.product_id, q), 0)
            qrow = pmeta.loc[q]

            if qrow.h3_category in comp:
                atype, base = "complementary_product", .72
            elif qrow.h3_category == p.h3_category:
                atype, base = "similar_product", .55
            else:
                atype, base = "frequently_bought_together", .45

            score = float(np.clip(
                base + min(pc, 50) * .006 + min(vc, 100) * .0008
                + (.08 if qrow.usage == p.usage else 0),
                .05, .99
            ))
            rows.append((p.product_id, q, atype, round(score, 4), pc, vc, "rule+observed"))
            seen.add((p.product_id, q))

    cols = ["product_id", "related_product_id", "affinity_type", "affinity_score",
            "co_purchase_count", "co_view_count", "source"]
    df = pd.DataFrame(rows, columns=cols)
    df.to_parquet(path, index=False)
    return df


def run(mode="initialize", days=None):
    total_start = time.perf_counter()

    def log(msg):
        print(f"[{time.perf_counter()-total_start:8.1f}s] {msg}", flush=True)

    def stage(name, fn):
        t = time.perf_counter()
        log(f"▶ {name}")
        result = fn()
        log(f"✓ {name} | {time.perf_counter()-t:.1f}s")
        return result

    log("=" * 72)
    log(f"Pantree Data Generator | mode={mode}")
    log("=" * 72)

    state = load_state()
    r = rng_for(C.SEED)
    products_path = DATA / "product_master.parquet"
    today = pd.Timestamp(C.DATA_END).normalize()

    if mode == "initialize":
        stage("Cleaning previous generated data", lambda: [p.unlink() for p in DATA.glob("*.parquet")])
        stage("Cleaning simulator state", lambda: [p.unlink() for p in STATE_DIR.glob("*.parquet")])
        import shutil
        stage("Cleaning partitioned facts", lambda: shutil.rmtree(DATA/"_partitioned", ignore_errors=True))
        state = {"next_customer":1,"next_order":1,"next_txn":1,"next_event":1,"next_session":1,"next_return":1,"next_review":1,"next_support":1,"last_date":None}

        products = stage(f"Building products ({C.N_PRODUCTS:,})", lambda: build_products(r, C.N_PRODUCTS))
        sellers = stage(f"Building sellers ({C.N_SELLERS:,})", lambda: build_sellers(r, products))
        stage("Building support agents", lambda: build_agents(r))
        cust = stage(f"Building customers ({C.N_CUSTOMERS:,})", lambda: build_customers(r, C.N_CUSTOMERS, state, start_date=C.DATA_START, end_date=today))
        prof = stage("Building latent customer profiles", lambda: latent_customer_profiles(r, cust))
        # Continuous acquisition: add a smaller new-customer cohort at the start of each month.
        acq_rows=[]; month_starts=pd.date_range(pd.Timestamp(C.DATA_START).normalize()+pd.offsets.MonthBegin(1), today, freq="MS")
        for ms in month_starts:
            nnew=max(50,int(C.N_CUSTOMERS*0.006))
            tmp=build_customers(r,nnew,state,start_id=int(state["next_customer"]),start_date=ms,end_date=min(ms+pd.offsets.MonthEnd(0),today))
            acq_rows.append(tmp)
        if acq_rows:
            added=pd.concat(acq_rows,ignore_index=True)
            cust=pd.concat([cust,added],ignore_index=True).drop_duplicates("customer_id")
            cust.to_parquet(DATA/"customer_master.parquet",index=False)
            prof=latent_customer_profiles(r,cust)
        start = pd.Timestamp(C.DATA_START).normalize()
        end = today
    else:
        if not products_path.exists() or not (DATA / "customer_master.parquet").exists():
            raise SystemExit("No initialized dataset. Run: python generate_data.py --mode initialize")

        def load_existing():
            products_ = pd.read_parquet(products_path)
            sellers_ = pd.read_parquet(DATA / "seller_master.parquet")
            build_agents(r)
            cust_ = pd.read_parquet(DATA / "customer_master.parquet")
            prof_ = latent_customer_profiles(r, cust_)
            return products_, sellers_, cust_, prof_

        products, sellers, cust, prof = stage("Loading existing dataset", load_existing)
        if state.get("last_date"):
            start = pd.Timestamp(state["last_date"]).normalize() + pd.Timedelta(days=1)
        else:
            start = pd.Timestamp(C.DATA_START).normalize()

        if start > today:
            log(f"Dataset already up to date through {today.date()}; nothing to generate.")
            return

        end = today
        sim_days = max((end - start).days + 1, 1)
        log(f"Increment window: {start.date()} -> {end.date()} ({sim_days:,} days)")

        nnew = max(0, int(len(cust) * 0.006 * sim_days / 30))
        if nnew:
            def add_customers():
                start_id = int(state["next_customer"])
                tmp = build_customers(r, nnew, state, start_id=start_id, start_date=start, end_date=end)
                out = pd.concat([cust, tmp], ignore_index=True).drop_duplicates("customer_id")
                out.to_parquet(DATA / "customer_master.parquet", index=False)
                state["next_customer"] = start_id + nnew
                return out, latent_customer_profiles(r, out)
            cust, prof = stage(f"Adding new customers ({nnew:,})", add_customers)

    # Idempotent increment: if a prior run crashed before state.json was committed,
    # remove the uncommitted window and rebuild it deterministically from the prior state.
    if mode == "increment":
        def purge_window():
            start_ts=pd.Timestamp(start); order_ids=set()
            tp=DATA/"transactions.parquet"
            if tp.exists():
                x=pd.read_parquet(tp); x["order_timestamp"]=pd.to_datetime(x.order_timestamp); order_ids=set(x.loc[x.order_timestamp>=start_ts,"order_id"]); x=x[x.order_timestamp<start_ts]; x.to_parquet(tp,index=False)
            cp=DATA/"clickstream.parquet"
            if cp.exists():
                x=pd.read_parquet(cp); x["event_timestamp"]=pd.to_datetime(x.event_timestamp); x=x[x.event_timestamp<start_ts]; x.to_parquet(cp,index=False)
            mp=DATA/"marketing_spend.parquet"
            if mp.exists():
                x=pd.read_parquet(mp); x["date"]=pd.to_datetime(x.date); x=x[x.date<start_ts]; x.to_parquet(mp,index=False)
            ip=DATA/"inventory.parquet"
            if ip.exists():
                x=pd.read_parquet(ip); x["inventory_date"]=pd.to_datetime(x.inventory_date); x=x[x.inventory_date<start_ts]; x.to_parquet(ip,index=False)
            for name,col in [("returns","return_requested_at"),("product_reviews","review_timestamp"),("support_contacts","contact_timestamp")]:
                q=DATA/f"{name}.parquet"
                if q.exists():
                    x=pd.read_parquet(q)
                    if name=="returns" and order_ids: x=x[~x.order_id.isin(order_ids)]
                    else:
                        x[col]=pd.to_datetime(x[col]); x=x[x[col]<start_ts]
                    x.to_parquet(q,index=False)
        stage("Preparing idempotent increment window", purge_window)

    dates = pd.date_range(start, end, freq="D")
    if len(dates) == 0:
        log("Nothing to generate.")
        return
    log(f"Simulation period: {dates[0].date()} -> {dates[-1].date()} ({len(dates):,} days)")

    stage(f"Generating marketing ({len(dates):,} days)", lambda: generate_marketing(r, dates, state))

    def orders_stage():
        return simulate_orders(r, dates, cust, prof, products, sellers, state)
    tx, orders = stage(f"Simulating orders ({len(dates):,} days)", orders_stage)
    log(f"    orders={len(orders):,} | transaction_lines={len(tx):,}")

    stage("Generating clickstream", lambda: simulate_clickstream(r, dates, cust, prof, products, orders, state))
    ret_batch = stage("Generating returns / reviews / support", lambda: simulate_returns_reviews_support(r, tx, state))
    stage("Generating inventory (vectorized)", lambda: simulate_inventory(r, dates, products, tx, ret_batch))
    affinity = stage("Generating product affinity", lambda: generate_product_affinity(r, products))
    log(f"    affinity_relationships={len(affinity):,}")
    stage("Building MMM weekly target", refresh_mmm)
    from finalize_dataset import finalize
    stage("Finalizing orders / lifecycle / churn / metrics / partitions", finalize)

    state["last_date"] = str(end.date())
    stage("Saving generator state", lambda: save_state(state))

    elapsed = time.perf_counter() - total_start
    log("=" * 72)
    log(f"✓ SIMULATION COMPLETE | total runtime {elapsed/60:.2f} minutes")
    log("=" * 72)

    for name in ["product_master","customer_master","seller_master","transactions","orders","clickstream","inventory","returns","product_reviews","support_contacts","product_affinity","marketing_spend","mmm_weekly_target","customer_monthly_features","customer_churn_labels","metrics"]:
        p = DATA / f"{name}.parquet"
        if p.exists():
            if p.suffix == ".parquet": log(f"{name:24s} {len(pd.read_parquet(p)):>12,} rows")
            else: log(f"{name:24s} generated")

if __name__=="__main__":
    import argparse
    ap=argparse.ArgumentParser(); ap.add_argument("--mode",choices=["initialize","increment"],default="initialize"); ap.add_argument("--days",type=int,default=30); a=ap.parse_args(); run(a.mode,a.days)
