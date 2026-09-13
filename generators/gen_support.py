"""
Generates agent_master + support_contacts (VoC).
Grain: 1 row per contact_id. Links to real customers/orders/transactions (order/txn nullable).
transcript_text: natural, varied, sometimes multi-issue (NOT keyword-templated).
issue_bucket: EMPTY (your LLM writes it back).
issue_bucket_true: HIDDEN ground truth so you can measure LLM accuracy.
Signal: low NPS + Unresolved contacts skew toward churn-prone customers, placed before they go silent.
"""
import numpy as np, pandas as pd
from pathlib import Path
import config as C

OUT = Path(__file__).resolve().parent.parent / "data"
r = np.random.default_rng(C.SEED + 11)

BUCKETS = ["Delivery/Late","Damaged/Defective","Refund/Return","Payment/Billing",
           "Product-Not-As-Described","App/Website Bug","Seller Issue","Cancellation","Other"]

# messy human agent tags per bucket (inconsistent, lowercase, abbreviated)
RAW_TAGS = {
 "Delivery/Late": ["late del","delay","not delivered","shipping issue","where is order","del delay"],
 "Damaged/Defective": ["damaged","broken item","defective","not working","dead on arrival","doa"],
 "Refund/Return": ["refund","return","money not received","refund pending","return pickup"],
 "Payment/Billing": ["payment failed","double charge","billing","emi issue","charged twice","refund to card"],
 "Product-Not-As-Described": ["wrong item","fake","not as shown","diff product","mismatch","size issue"],
 "App/Website Bug": ["app crash","cant login","website error","app bug","otp not coming","page not loading"],
 "Seller Issue": ["seller rude","seller no response","3p issue","seller","bad seller"],
 "Cancellation": ["cancel","cancel order","want to cancel","order cancel"],
 "Other": ["general","query","info","misc","feedback","other"],
}

# transcript template banks — several natural variants each, no bucket keyword leakage
T = {
"Delivery/Late":[
 "Hi, I placed order {oid} {days} days back and it still hasn't reached me. The tracking hasn't moved in a while. Can you check what's happening?",
 "It's been over a week since I ordered the {prod} and there's no update. I needed it urgently, this delay is really frustrating.",
 "My package was supposed to arrive by now but nothing yet. I keep seeing 'out for delivery' but no one comes. Please help.",
 "The {prod} I ordered to {city} is stuck somewhere. Every time I call the courier they say tomorrow. It never comes.",
 "Order {oid} shows delivered but I never received anything. I checked with my neighbours and security also, nothing.",
],
"Damaged/Defective":[
 "The {prod} arrived with the screen cracked. The box itself was dented. I want a replacement immediately.",
 "I opened order {oid} and the item is not working at all. Switched it on and nothing happens. This is unacceptable for the price.",
 "Received a defective piece. It was making a strange noise from the first use and then just stopped. Very disappointed.",
 "The product I got is damaged, looks like it was already used. There are scratches all over the {prod}.",
 "Bought the {prod} and within two days it stopped functioning. Clearly a faulty unit, I need this sorted.",
],
"Refund/Return":[
 "I returned the {prod} last week and the pickup was done, but my refund of Rs {amt} still hasn't come. When will I get my money?",
 "It's been 10 days since the return for order {oid} was completed and there's no sign of the refund. Please process it.",
 "I want to return this item, it doesn't suit my need. How do I initiate the return and how long for the refund?",
 "The return pickup keeps getting cancelled by the agent. I've rescheduled three times now. Just take the item back and refund me.",
 "My refund shows processed on your side but nothing has hit my account. It's been almost two weeks now.",
],
"Payment/Billing":[
 "I was charged twice for order {oid}. Two amounts of Rs {amt} were deducted but I only placed one order. Reverse one immediately.",
 "My payment failed during checkout but the money got deducted from my account. The order didn't even go through.",
 "There's an EMI showing on my card for something I cancelled. Why am I still being billed for this?",
 "I paid via UPI and the amount went but the order status still says payment pending. Please confirm and fix.",
 "The final amount charged is more than what was shown at checkout. There are some extra charges I didn't agree to.",
],
"Product-Not-As-Described":[
 "The {prod} I received is completely different from what was shown in the photos. The colour and size are not matching at all.",
 "This looks like a duplicate product, not the original brand. The quality is nowhere close to what was advertised.",
 "I ordered a specific variant but got a totally different one in order {oid}. This is not what I paid for.",
 "The listing said it comes with accessories but the box had none of them. Feels misleading.",
 "Size is completely wrong. I selected large and got something that fits like a small. The description was misleading.",
],
"App/Website Bug":[
 "Your app keeps crashing every time I try to open my orders page. I've reinstalled it twice, still the same.",
 "I can't log in to my account. The OTP is just not arriving even after multiple tries. Very annoying.",
 "The checkout page throws an error whenever I apply a coupon. It just reloads and my cart empties.",
 "The website is not loading product images at all on my phone. Half the app is broken right now.",
 "Every time I add something to cart it disappears. Something is clearly wrong with the app today.",
],
"Seller Issue":[
 "The seller for order {oid} is not responding to any of my messages about the {prod}. It's been days.",
 "The third-party seller was very rude when I asked about a replacement. This reflects badly on your platform.",
 "Seller shipped a wrong item and now refuses to take responsibility. Please intervene on my behalf.",
 "I've been trying to reach the seller regarding a warranty claim and there's zero response from their side.",
],
"Cancellation":[
 "I want to cancel order {oid} immediately. I ordered by mistake and it hasn't shipped yet.",
 "Please cancel my order for the {prod}. I found it cheaper elsewhere and changed my mind.",
 "I need to cancel this order, I no longer need the item. Make sure I'm not charged.",
 "Cancel the order please, delivery is taking too long and I can't wait anymore.",
],
"Other":[
 "I just wanted to know if the {prod} will be back in stock soon. Any idea when?",
 "How do I update my saved address? I moved to {city} recently and want deliveries there.",
 "Can you tell me more about the warranty on order {oid}? Just want to understand the coverage.",
 "I have some general feedback about the app experience, overall it's been decent lately.",
 "Wanted to check my Prime membership renewal date and the benefits I currently have.",
],
}

# multi-issue combiners: pick a secondary bucket and stitch
def multi(primary_txt, sec_txt):
    joiners = [" Also, separately, ", " And on top of that, ", " One more thing — ", " Additionally, "]
    return primary_txt + r.choice(joiners) + sec_txt[0].lower() + sec_txt[1:]

def gen_agents():
    n = 30
    teams = ["Delivery","Returns/Refunds","Payments","Product/Seller","Tech Support","General"]
    langs = ["English","Hindi","Tamil","Telugu","Marathi","Bengali"]
    cities = ["Bengaluru","Hyderabad","Pune","Gurugram","Chennai","Kolkata"]
    hire = pd.to_datetime("2021-01-01") + pd.to_timedelta(r.integers(0,365*4,n),unit="D")
    df = pd.DataFrame({
        "agent_id":[f"agt_{i:03d}" for i in range(1,n+1)],
        "agent_name":[f"Agent {i}" for i in range(1,n+1)],
        "team": r.choice(teams,n),
        "location": r.choice(cities,n),
        "hire_date": hire.date,
        "language": r.choice(langs,n),
    })
    df["tenure_months"] = ((pd.to_datetime(C.DATA_END)-pd.to_datetime(df.hire_date)).dt.days//30)
    return df

def main():
    cust = pd.read_parquet(OUT/"customer_master.parquet")
    txn  = pd.read_parquet(OUT/"transactions.parquet")
    prodname = pd.read_parquet(OUT/"product_master.parquet").set_index("product_id").product_name
    agents = gen_agents()
    agents.to_parquet(OUT/"agent_master.parquet", index=False)

    # per-customer signals
    last_order = txn.groupby("customer_id").order_timestamp.max()
    churn_silent = (pd.to_datetime(C.DATA_END) - last_order).dt.days > 90

    # problem transactions attract contacts
    problem = txn[txn.order_status.isin(["CIR","RTO","Undelivered","Lost","CIC"])
                  | (txn.payment_status.isin(["Failed","Pending"]))].copy()

    rows = []
    cid_counter = 1
    def new_id():
        nonlocal cid_counter
        s = f"ctc_{cid_counter:07d}"; cid_counter += 1; return s

    # map problem status -> likely bucket (with realistic reason variety)
    def bucket_from_status(st, pst):
        if pst in ("Failed","Pending"): return "Payment/Billing"
        if st == "CIR":   # a return can be for several underlying reasons
            return r.choice(["Refund/Return","Damaged/Defective","Product-Not-As-Described","Seller Issue"],
                            p=[0.45,0.28,0.20,0.07])
        if st == "CIC":
            return r.choice(["Cancellation","Payment/Billing"], p=[0.85,0.15])
        return {"RTO":"Delivery/Late","Undelivered":"Delivery/Late",
                "Lost":"Delivery/Late"}.get(st,"Other")

    # 1) contacts tied to problem transactions (~55% of a sampled subset)
    prob_sample = problem.sample(frac=0.45, random_state=3)
    for _, row in prob_sample.iterrows():
        primary = bucket_from_status(row.order_status, row.payment_status)
        rows.append(build_contact(row.customer_id, row.order_id, row.transaction_id,
                                  row.order_timestamp, primary, prodname.get(row.product_id,"item"),
                                  cust, churn_silent, agents, new_id, tied=True))

    # 2) general contacts not tied to a specific problem (random customers)
    gen_customers = cust.sample(int(len(cust)*0.15), random_state=5).customer_id
    for cid in gen_customers:
        primary = r.choice(BUCKETS, p=[0.20,0.12,0.15,0.10,0.10,0.10,0.06,0.07,0.10])
        # pick one of their orders if any
        their = txn[txn.customer_id==cid]
        if len(their) and r.random()<0.6:
            o = their.sample(1, random_state=int(cid[-4:]) if cid[-4:].isdigit() else 1).iloc[0]
            oid, tid, ts, pn = o.order_id, o.transaction_id, o.order_timestamp, prodname.get(o.product_id,"item")
        else:
            oid, tid, ts, pn = None, None, last_order.get(cid, pd.Timestamp("2024-06-01")), "item"
        rows.append(build_contact(cid, oid, tid, ts, primary, pn, cust, churn_silent,
                                  agents, new_id, tied=False))

    df = pd.DataFrame(rows)
    # shuffle & reassign sequential ids so order isn't grouped by type
    df = df.sample(frac=1, random_state=9).reset_index(drop=True)
    df["contact_id"] = [f"ctc_{i:07d}" for i in range(1,len(df)+1)]
    df.to_parquet(OUT/"support_contacts.parquet", index=False)
    print(f"agent_master     : {len(agents):>7,} rows")
    print(f"support_contacts : {len(df):>7,} rows")
    print("bucket mix (true):"); print(df.issue_bucket_true.value_counts().to_string())
    print("multi-issue share:", round(df.is_multi_issue.mean(),3))
    print("median NPS by resolution:")
    print(df.groupby('resolution_status').nps_score.median().to_string())

def build_contact(cid, oid, tid, base_ts, primary, prodname, cust, churn_silent,
                  agents, new_id, tied):
    # contact timestamp: a few days after the order/base
    ts = pd.to_datetime(base_ts) + pd.to_timedelta(int(r.integers(0,10)), unit="D")
    ts = max(ts, pd.to_datetime(base_ts))                       # never before the order/base
    ts = min(ts, pd.to_datetime(C.DATA_END) + pd.Timedelta(hours=23, minutes=59))
    # never before signup
    su = cust.loc[cust.customer_id==cid, "signup_date"].values
    if len(su):
        ts = max(ts, pd.to_datetime(su[0]))
    days = int(r.integers(3, 15)); amt = int(r.integers(200, 8000))
    city = cust.loc[cust.customer_id==cid, "signup_city"].values
    city = city[0] if len(city) else "your city"
    txt = r.choice(T[primary]).format(oid=oid or "your order", prod=prodname, days=days,
                                      amt=amt, city=city)
    # multi-issue ~12%
    is_multi = r.random() < 0.12
    secondary = None
    if is_multi:
        secondary = r.choice([b for b in BUCKETS if b!=primary])
        sec_txt = r.choice(T[secondary]).format(oid=oid or "your order", prod=prodname,
                                                days=days, amt=amt, city=city)
        txt = multi(txt, sec_txt)

    # NPS: problem buckets skew detractor; resolution lifts it
    resolution = r.choice(["Resolved","Unresolved","Pending","Escalated"],
                          p=[0.55,0.20,0.15,0.10])
    base_nps = {"Delivery/Late":3,"Damaged/Defective":2,"Refund/Return":3,"Payment/Billing":3,
                "Product-Not-As-Described":3,"App/Website Bug":4,"Seller Issue":3,
                "Cancellation":5,"Other":7}[primary]
    if resolution=="Resolved": base_nps += 3
    if resolution in ("Unresolved","Escalated"): base_nps -= 2
    # churn-prone customers report worse
    if churn_silent.get(cid, False): base_nps -= 1
    nps = int(np.clip(base_nps + r.integers(-2,3), 0, 10))

    csat = None if r.random()<0.25 else int(np.clip(round(nps/2)+r.integers(-1,2),1,5))
    channel = r.choice(["Call","Chat","Email"], p=[0.45,0.40,0.15])
    ag = agents.sample(1, random_state=int(r.integers(0,1_000_000))).iloc[0]
    handle = int(r.integers(45, 1800))

    return {
        "contact_id": new_id(),
        "customer_id": cid,
        "order_id": oid,
        "transaction_id": tid,
        "contact_timestamp": ts,
        "contact_channel": channel,
        "agent_id": ag.agent_id,
        "agent_team": ag.team,
        "language": ag.language,
        "issue_category_raw": r.choice(RAW_TAGS[primary]),
        "transcript_text": txt,
        "nps_score": nps,
        "csat_score": csat,
        "handle_time_sec": handle,
        "resolution_status": resolution,
        "issue_bucket": None,                 # <-- your LLM fills this
        "issue_bucket_true": primary,          # <-- hidden ground truth
        "is_multi_issue": is_multi,
        "secondary_bucket_true": secondary,
    }

if __name__ == "__main__":
    main()
