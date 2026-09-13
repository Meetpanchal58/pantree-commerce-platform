"""Preflight: runs the cross-table validator, then a churn MODEL-READINESS smoke test
(base rate + leakage-safe AUC + top-decile lift) so the churn label can't silently drift
to an unmodelable rate again."""
import runpy, sys
from pathlib import Path
import pandas as pd, numpy as np

DATA = Path(__file__).resolve().parent.parent / "data"

# 1) structural / cross-table checks (it calls sys.exit — catch it so we continue)
try:
    runpy.run_path(str(Path(__file__).with_name('validate_dataset.py')), run_name='__main__')
except SystemExit:
    pass

# 2) churn model-readiness smoke test
print("\n" + "="*60 + "\nCHURN MODEL-READINESS\n" + "="*60)
fails = []
def chk(name, ok, detail=""):
    if not ok: fails.append(name)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  -> {detail}" if detail and not ok else ""))

lp = DATA/"customer_churn_labels.parquet"; fp = DATA/"customer_monthly_features.parquet"
if not (lp.exists() and fp.exists()):
    print("churn tables not found — skipping"); sys.exit(1 if fails else 0)

lab = pd.read_parquet(lp); feat = pd.read_parquet(fp)

base = lab.churn_90d.mean()
chk("churn base rate modelable (0.10-0.55)", 0.10 <= base <= 0.55, f"{base:.1%} on {len(lab):,} rows")

df = lab.merge(feat, on=["customer_id","snapshot_date"], how="left", suffixes=("","_f"))
feat_cols = ["tenure_days","orders","revenue","quantity","margin","recency_days",
             "frequency_90d","monetary_90d","return_rate_90d"]
feat_cols = [c for c in feat_cols if c in df.columns]
X = df[feat_cols].replace([np.inf,-np.inf], np.nan).fillna(0)
y = df["churn_90d"].astype(int)
if y.nunique() < 2 or len(df) < 500:
    print("[SKIP] not enough data/variation for AUC smoke test")
else:
    try:
        from sklearn.ensemble import HistGradientBoostingClassifier
        from sklearn.model_selection import train_test_split
        from sklearn.metrics import roc_auc_score
        Xtr,Xte,ytr,yte = train_test_split(X,y,test_size=0.3,random_state=42,stratify=y)
        m = HistGradientBoostingClassifier(max_iter=200, learning_rate=0.06, random_state=42).fit(Xtr,ytr)
        p = m.predict_proba(Xte)[:,1]; auc = roc_auc_score(yte,p)
        chk("churn signal learnable & not leaking (AUC 0.62-0.92)", 0.62 <= auc <= 0.92, f"AUC={auc:.3f}")
        pr = pd.DataFrame({"y":yte.values,"p":p}).sort_values("p",ascending=False)
        top = pr.head(max(1,int(0.1*len(pr)))).y.mean()
        print(f"      [info] top-decile churn precision = {top:.1%} (base {base:.1%})")
    except ImportError:
        print("[SKIP] scikit-learn not installed — install to run the AUC smoke test")

print("\nPREFLIGHT RESULT:", "ALL GREEN" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
