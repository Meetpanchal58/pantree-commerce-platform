"""Generate verified Text-to-SQL evaluation questions for the current warehouse."""
import json
from pathlib import Path
import duckdb
D=Path(__file__).resolve().parent.parent/'data'; OUT=Path(__file__).resolve().parent.parent/'artifacts'; OUT.mkdir(exist_ok=True)
TABLES=['customer_master','seller_master','product_master','transactions','clickstream','inventory','returns','product_reviews','support_contacts','agent_master','marketing_spend','mmm_weekly_target']
EVAL=[
('easy','How many customers are there?','SELECT COUNT(*) FROM customer_master'),
('easy','How many products are in the catalog?','SELECT COUNT(*) FROM product_master'),
('easy','How many orders were placed?','SELECT COUNT(DISTINCT order_id) FROM transactions'),
('easy','What are the top product categories by SKU count?','SELECT h1_category, COUNT(*) n FROM product_master GROUP BY 1 ORDER BY n DESC'),
('easy','What is total net item value?','SELECT SUM(net_item_value) FROM transactions'),
('easy','How many customers are Prime members?','SELECT COUNT(*) FROM customer_master WHERE is_prime_member=TRUE'),
('medium','What is average order value?','SELECT SUM(net_item_value)/COUNT(DISTINCT order_id) FROM transactions'),
('medium','Which article types generate the most revenue?','SELECT p.h3_category,SUM(t.net_item_value) revenue FROM transactions t JOIN product_master p USING(product_id) GROUP BY 1 ORDER BY revenue DESC LIMIT 10'),
('medium','What is the repeat purchase rate?','WITH x AS (SELECT customer_id,COUNT(DISTINCT order_id) n FROM transactions GROUP BY 1) SELECT AVG(CASE WHEN n>=2 THEN 1.0 ELSE 0 END) FROM x'),
('medium','Which acquisition channels have the highest customer count?','SELECT acquisition_channel,COUNT(*) n FROM customer_master GROUP BY 1 ORDER BY n DESC'),
('medium','What is the cart abandonment rate?','WITH x AS (SELECT session_id,MAX(CASE WHEN event_type=\'add_to_cart\' THEN 1 ELSE 0 END) cart,MAX(CASE WHEN event_type=\'purchase\' THEN 1 ELSE 0 END) bought FROM clickstream GROUP BY 1) SELECT AVG(CASE WHEN bought=0 THEN 1.0 ELSE 0 END) FROM x WHERE cart=1'),
('medium','Which products have the most units sold?','SELECT product_id,SUM(quantity) units FROM transactions GROUP BY 1 ORDER BY units DESC LIMIT 10'),
('medium','Which sellers have the highest net revenue?','SELECT seller_id,SUM(net_item_value) revenue FROM transactions GROUP BY 1 ORDER BY revenue DESC LIMIT 10'),
('medium','What percentage of inventory is low or out of stock on the latest day?','WITH x AS (SELECT *,ROW_NUMBER() OVER(PARTITION BY product_id,seller_id,warehouse_id ORDER BY inventory_date DESC) rn FROM inventory) SELECT AVG(CASE WHEN stock_status<>\'HEALTHY\' THEN 1.0 ELSE 0 END) FROM x WHERE rn=1'),
('medium','Which products have the highest return counts?','SELECT product_id,COUNT(*) returns FROM returns GROUP BY 1 ORDER BY returns DESC LIMIT 10'),
('hard','Which acquisition channel has the highest historical revenue per customer?','WITH r AS (SELECT customer_id,SUM(net_item_value) revenue FROM transactions GROUP BY 1) SELECT c.acquisition_channel,AVG(r.revenue) avg_revenue FROM r JOIN customer_master c USING(customer_id) GROUP BY 1 ORDER BY avg_revenue DESC'),
('hard','Which article types have both high revenue and high return rate?','WITH s AS (SELECT p.h3_category,SUM(t.net_item_value) revenue,COUNT(*) lines FROM transactions t JOIN product_master p USING(product_id) GROUP BY 1),r AS (SELECT p.h3_category,COUNT(*) returns FROM returns r JOIN product_master p USING(product_id) GROUP BY 1) SELECT s.h3_category,s.revenue,COALESCE(r.returns,0)*1.0/s.lines return_rate FROM s LEFT JOIN r USING(h3_category) ORDER BY revenue DESC LIMIT 10'),
('hard','What is weekly revenue and total marketing spend?','WITH m AS (SELECT date_trunc(\'week\',CAST(date AS DATE)) w,SUM(spend) spend FROM marketing_spend GROUP BY 1) SELECT t.week_start,t.observed_revenue,m.spend FROM m JOIN mmm_weekly_target t ON CAST(t.week_start AS DATE)=m.w ORDER BY t.week_start'),
('hard','Which customers have high historical spend but no order in the last 90 days?','WITH x AS (SELECT customer_id,SUM(net_item_value) revenue,MAX(order_timestamp) last_order FROM transactions GROUP BY 1) SELECT * FROM x WHERE revenue>10000 AND last_order < (SELECT MAX(order_timestamp)-INTERVAL 90 DAY FROM transactions) ORDER BY revenue DESC LIMIT 100'),
('hard','What are the most common search queries?','SELECT search_query,COUNT(*) n FROM clickstream WHERE event_type=\'search\' AND search_query IS NOT NULL GROUP BY 1 ORDER BY n DESC LIMIT 20'),
]
def main():
    con=duckdb.connect()
    for t in TABLES: con.execute(f"CREATE VIEW {t} AS SELECT * FROM read_parquet('{D/t}.parquet')")
    kept=[]
    for i,(difficulty,q,sql) in enumerate(EVAL,1):
        try: res=con.execute(sql); rows=res.fetchall(); kept.append({'id':f'q{i:03d}','difficulty':difficulty,'question':q,'gold_sql':sql,'verified':True,'returns_rows':bool(rows)})
        except Exception as e: print('DROP',q,'->',str(e)[:160])
    (OUT/'text_to_sql_eval.json').write_text(json.dumps({'eval':kept},indent=2)); print(f'verified {len(kept)}/{len(EVAL)} questions')
if __name__=='__main__': main()
