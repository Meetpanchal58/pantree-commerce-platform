"""Strong cross-table business, temporal and leakage validation for Pantree."""
import sys
from pathlib import Path
import pandas as pd
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'generators'))
import config as C
DATA=Path(__file__).resolve().parent.parent/'data'; fails=[]
def chk(name,ok,detail=''):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" -> {detail}" if not ok and detail else ''))
    if not ok: fails.append(name)
def read(n):
    p=DATA/f'{n}.parquet'; return pd.read_parquet(p) if p.exists() else pd.DataFrame()
p,c,s,t,o,cs,inv,ret,rev,sup=[read(x) for x in ['product_master','customer_master','seller_master','transactions','orders','clickstream','inventory','returns','product_reviews','support_contacts']]
chk('required tables present',all(not x.empty for x in [p,c,s,t,o,cs,inv]))
for df,key in [(p,'product_id'),(c,'customer_id'),(s,'seller_id'),(t,'transaction_id'),(o,'order_id'),(cs,'event_id')]: chk(f'{key} unique', key in df and df[key].is_unique)
if not t.empty:
    chk('transaction customers exist',t.customer_id.isin(set(c.customer_id)).all()); chk('transaction products exist',t.product_id.isin(set(p.product_id)).all()); chk('transaction sellers exist',t.seller_id.isin(set(s.seller_id)).all())
    chk('transaction quantity positive',(t.quantity>0).all()); chk('success has positive revenue',t.loc[t.payment_status.eq('Success'),'net_item_value'].gt(0).all()); chk('non-success zero realized revenue',t.loc[~t.payment_status.eq('Success'),'net_item_value'].eq(0).all())
    expected_margin=np.where(t.net_item_value.gt(0),t.net_item_value-t.unit_cost*t.quantity,0.0)
    chk('margin consistent',(t.gross_margin-expected_margin).abs().lt(.02).all())
    t.order_timestamp=pd.to_datetime(t.order_timestamp); c.signup_date=pd.to_datetime(c.signup_date); chk('orders never precede signup',t.merge(c[['customer_id','signup_date']],on='customer_id').eval('order_timestamp >= signup_date').all())
    chk('no future transactions beyond DATA_END',t.order_timestamp.le(pd.Timestamp(C.DATA_END)+pd.Timedelta(days=1)-pd.Timedelta(microseconds=1)).all())
if not o.empty and not t.empty:
    rec=t.groupby('order_id').agg(lines=('transaction_id','nunique'),qty=('quantity','sum'),gross=('gross_item_value','sum'),net=('net_item_value','sum'),margin=('gross_margin','sum')).reset_index().merge(o,on='order_id')
    chk('orders reconcile line count',rec.lines.eq(rec.order_line_count).all()); chk('orders reconcile quantity',rec.qty.eq(rec.total_quantity).all()); chk('orders reconcile gross', (rec.gross-rec.gross_order_value).abs().lt(.02).all()); chk('orders reconcile realized revenue',(rec.net-rec.realized_revenue).abs().lt(.02).all()); chk('orders reconcile margin',(rec.margin-rec.gross_margin).abs().lt(.02).all())
if not cs.empty:
    chk('clickstream customers exist',cs.customer_id.isin(set(c.customer_id)).all()); chk('clickstream products valid',cs.loc[cs.product_id.notna(),'product_id'].isin(set(p.product_id)).all())
    badseq=0
    for sid,g in cs.groupby('session_id',sort=False):
        ev=g.sort_values('event_timestamp').event_type.tolist()
        if not ev or ev[0] != 'session_start' or ev[-1] != 'session_end': badseq+=1
        pos={e:i for i,e in enumerate(ev)}
        if 'pdp_view' in pos and 'plp_view' not in pos: badseq+=1
        if 'checkout_start' in pos and 'add_to_cart' not in pos: badseq+=1
        if 'payment_attempt' in pos and 'checkout_start' not in pos: badseq+=1
    chk('session funnel ordering',badseq==0,f'{badseq} invalid sessions')
    if 'order_id' in cs.columns and not o.empty:
        purch=cs[cs.event_type.eq('purchase') & cs.order_id.notna()]
        chk('purchase event order ids are realized',purch.order_id.isin(set(o.loc[o.is_realized,'order_id'])).all())
        chk('campaign ids populated on traffic events',cs.campaign_id.notna().all())
if not inv.empty:
    chk('inventory nonnegative',inv.closing_stock.ge(0).all());
    lhs=inv.opening_stock+inv.units_received-inv.units_sold-inv.damaged_units+inv.units_returned
    chk('inventory stock equation',lhs.eq(inv.closing_stock).all())
    chk('inventory units sold nonnegative',inv.units_sold.ge(0).all()); chk('stockout status consistent',inv.stock_status.eq(np.where(inv.closing_stock.eq(0),'OUT_OF_STOCK',np.where(inv.closing_stock.le(inv.reorder_point),'LOW_STOCK','HEALTHY'))).all())
if not ret.empty:
    chk('returns transactions exist',ret.transaction_id.isin(set(t.transaction_id)).all()); chk('returned quantity positive',ret.returned_quantity.gt(0).all());
    qty=t.set_index('transaction_id').quantity; net=t.set_index('transaction_id').net_item_value
    chk('return qty <= purchased qty',(ret.returned_quantity <= ret.transaction_id.map(qty).fillna(0)).all()); chk('refund <= realized line value',(ret.refund_amount <= ret.transaction_id.map(net).fillna(0)+.02).all())
if not rev.empty:
    chk('review orders exist',rev.order_id.isin(set(t.order_id)).all())
    review_delivery=rev.merge(t[['transaction_id','delivered_timestamp']],on='transaction_id',how='left')
    rd=review_delivery.dropna(subset=['delivered_timestamp'])
    chk('review after delivery',(pd.to_datetime(rd.review_timestamp)>=pd.to_datetime(rd.delivered_timestamp)).all())
if not sup.empty: chk('support orders exist',sup.order_id.isin(set(t.order_id)).all())
if not p.empty and not t.empty:
    pp=p.set_index('product_id'); chk('no sales before product launch',t.apply(lambda x: pd.Timestamp(x.order_timestamp)>=pd.Timestamp(pp.loc[x.product_id,'launch_date']),axis=1).all())
if (DATA/'customer_churn_labels.parquet').exists() and (DATA/'customer_monthly_features.parquet').exists():
    snap=read('customer_monthly_features'); lab=read('customer_churn_labels'); chk('churn labels are future-window based', (pd.to_datetime(lab.label_window_end)>pd.to_datetime(lab.snapshot_date)).all()); chk('churn label snapshot join valid',lab.merge(snap,on=['customer_id','snapshot_date']).shape[0]==len(lab))
if (DATA/'metrics.json').exists(): chk('metric definitions present',True)
print('\nRESULT:', 'ALL GREEN' if not fails else f'{len(fails)} FAILED: {fails}'); sys.exit(1 if fails else 0)
