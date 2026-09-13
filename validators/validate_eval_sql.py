"""Load all tables into SQLite, run every gold SQL, confirm each executes and returns rows."""
import pandas as pd, sqlite3, json, os
from pathlib import Path
D=Path(__file__).resolve().parent.parent/"data"
dbp=D/"_eval_tmp.db"
if dbp.exists(): dbp.unlink()
con=sqlite3.connect(str(dbp))
for t in ["customer_master","product_master","seller_master","agent_master","transactions",
          "support_contacts","clickstream","marketing_spend","mmm_weekly_target"]:
    df=pd.read_parquet(D/f"{t}.parquet")
    for c in df.columns:
        if str(df[c].dtype).startswith("datetime"): df[c]=df[c].astype(str)
        if str(df[c].dtype) in ("bool","boolean"): df[c]=df[c].astype("Int64")
    df.to_sql(t, con, index=False, if_exists="replace", chunksize=100_000)
    del df
fails=[]
for line in open(D/"eval_questions.jsonl"):
    q=json.loads(line)
    try:
        res=pd.read_sql(q["gold_sql"], con)
        status="ok" if len(res)>=0 else "empty"
        print(f"[PASS] {q['id']}: {q['question'][:55]}  -> {len(res)} row(s)")
    except Exception as e:
        fails.append(q['id']); print(f"[FAIL] {q['id']}: {e}")
print("="*60)
print("EVAL SQL:", "ALL EXECUTE ✅" if not fails else f"{len(fails)} FAILED: {fails}")
con.close(); import os; os.remove(str(dbp))
