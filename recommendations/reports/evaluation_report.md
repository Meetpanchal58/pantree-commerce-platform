# Recommendation evaluation report

Generated 2026-11-08T20:57:54+00:00 by `python -m recommendations.evaluate`.

**Question asked:** for each real order in the held-out period, given everything known one hour before it, does the system rank the products the customer actually bought near the top?

Test orders: 5,633. Candidate list: up to 100 products (20 per source, then filled by score). Products the customer already bought are excluded.

## 1. Retrieval: is the right product in the list at all?

764 of 5,633 test orders (13.6%) had a bought product somewhere in the candidate list.

| Source | Orders where its list contained a bought product |
|---|---|
| collaborative | 855 (15.2%) |
| recent_activity | 311 (5.5%) |
| trending | 300 (5.3%) |
| content | 252 (4.5%) |
| fbt | 151 (2.7%) |

## 2. Ranking quality: end to end

Every test order. A bought product missing from the candidate list counts as a miss.

### All — 5,433 orders (ADEQUATELY POWERED)

| Ordering | Recall@5 | NDCG@5 | Recall@10 | NDCG@10 | MRR@10 |
|---|---|---|---|---|---|
| Random order | 0.007 | 0.005 | 0.014 | 0.007 | 0.006 |
| Popularity (most viewed) | 0.019 | 0.011 | 0.028 | 0.014 | 0.010 |
| Retrieval score (no ranker) | 0.025 | 0.018 | 0.038 | 0.022 | 0.020 |
| Old ranker (3 features) | 0.004 | 0.003 | 0.008 | 0.004 | 0.004 |
| New ranker (11 features) | 0.039 | 0.027 | 0.049 | 0.031 | 0.028 |
| Production blend (50/50) | 0.041 | 0.028 | 0.050 | 0.031 | 0.028 |
| Segment-aware (popular for first-timers, blend otherwise) | 0.039 | 0.027 | 0.051 | 0.031 | 0.027 |

### First Order — 1,495 orders (ADEQUATELY POWERED)

| Ordering | Recall@5 | NDCG@5 | Recall@10 | NDCG@10 | MRR@10 |
|---|---|---|---|---|---|
| Random order | 0.003 | 0.002 | 0.006 | 0.003 | 0.004 |
| Popularity (most viewed) | 0.024 | 0.014 | 0.040 | 0.020 | 0.015 |
| Retrieval score (no ranker) | 0.012 | 0.007 | 0.024 | 0.011 | 0.009 |
| Old ranker (3 features) | 0.001 | 0.001 | 0.003 | 0.001 | 0.001 |
| New ranker (11 features) | 0.026 | 0.018 | 0.031 | 0.020 | 0.019 |
| Production blend (50/50) | 0.029 | 0.020 | 0.035 | 0.022 | 0.020 |
| Segment-aware (popular for first-timers, blend otherwise) | 0.024 | 0.014 | 0.040 | 0.020 | 0.015 |

### Repeat Order — 3,938 orders (ADEQUATELY POWERED)

| Ordering | Recall@5 | NDCG@5 | Recall@10 | NDCG@10 | MRR@10 |
|---|---|---|---|---|---|
| Random order | 0.008 | 0.006 | 0.016 | 0.008 | 0.007 |
| Popularity (most viewed) | 0.017 | 0.009 | 0.023 | 0.012 | 0.009 |
| Retrieval score (no ranker) | 0.030 | 0.022 | 0.044 | 0.027 | 0.024 |
| Old ranker (3 features) | 0.005 | 0.004 | 0.010 | 0.005 | 0.005 |
| New ranker (11 features) | 0.044 | 0.031 | 0.055 | 0.035 | 0.032 |
| Production blend (50/50) | 0.045 | 0.031 | 0.056 | 0.035 | 0.032 |
| Segment-aware (popular for first-timers, blend otherwise) | 0.045 | 0.031 | 0.056 | 0.035 | 0.032 |

## 3. Ranking quality: ranking only

Only orders whose candidate list contains a bought product: measures ordering alone.

### All — 757 orders (ADEQUATELY POWERED)

| Ordering | Recall@5 | NDCG@5 | Recall@10 | NDCG@10 | MRR@10 |
|---|---|---|---|---|---|
| Random order | 0.061 | 0.040 | 0.118 | 0.059 | 0.043 |
| Popularity (most viewed) | 0.153 | 0.084 | 0.232 | 0.111 | 0.075 |
| Retrieval score (no ranker) | 0.208 | 0.142 | 0.321 | 0.180 | 0.144 |
| Old ranker (3 features) | 0.036 | 0.024 | 0.079 | 0.038 | 0.027 |
| New ranker (11 features) | 0.322 | 0.218 | 0.412 | 0.248 | 0.202 |
| Production blend (50/50) | 0.340 | 0.225 | 0.425 | 0.253 | 0.204 |
| Segment-aware (popular for first-timers, blend otherwise) | 0.326 | 0.211 | 0.435 | 0.248 | 0.194 |

### First Order — 150 orders (PROVISIONAL)

| Ordering | Recall@5 | NDCG@5 | Recall@10 | NDCG@10 | MRR@10 |
|---|---|---|---|---|---|
| Random order | 0.047 | 0.034 | 0.083 | 0.046 | 0.035 |
| Popularity (most viewed) | 0.277 | 0.154 | 0.466 | 0.219 | 0.147 |
| Retrieval score (no ranker) | 0.136 | 0.082 | 0.264 | 0.124 | 0.086 |
| Old ranker (3 features) | 0.013 | 0.008 | 0.033 | 0.015 | 0.010 |
| New ranker (11 features) | 0.312 | 0.209 | 0.372 | 0.229 | 0.188 |
| Production blend (50/50) | 0.349 | 0.222 | 0.418 | 0.245 | 0.195 |
| Segment-aware (popular for first-timers, blend otherwise) | 0.277 | 0.154 | 0.466 | 0.219 | 0.147 |

### Repeat Order — 607 orders (ADEQUATELY POWERED)

| Ordering | Recall@5 | NDCG@5 | Recall@10 | NDCG@10 | MRR@10 |
|---|---|---|---|---|---|
| Random order | 0.065 | 0.042 | 0.127 | 0.062 | 0.045 |
| Popularity (most viewed) | 0.123 | 0.066 | 0.174 | 0.084 | 0.058 |
| Retrieval score (no ranker) | 0.226 | 0.157 | 0.335 | 0.194 | 0.158 |
| Old ranker (3 features) | 0.042 | 0.028 | 0.090 | 0.044 | 0.032 |
| New ranker (11 features) | 0.324 | 0.221 | 0.422 | 0.253 | 0.205 |
| Production blend (50/50) | 0.338 | 0.226 | 0.427 | 0.255 | 0.206 |
| Segment-aware (popular for first-timers, blend otherwise) | 0.338 | 0.226 | 0.427 | 0.255 | 0.206 |

## Are the differences real? (paired bootstrap over test orders, end-to-end NDCG@10)

| Comparison | Mean difference | 95% confidence interval |
|---|---|---|
| new ranker vs retrieval | +0.0083 | +0.0053 to +0.0111 |
| new ranker vs popularity | +0.0168 | +0.0140 to +0.0197 |
| new ranker vs legacy ranker | +0.0264 | +0.0227 to +0.0303 |
| new ranker vs random | +0.0236 | +0.0198 to +0.0275 |
| segment aware vs retrieval | +0.0083 | +0.0059 to +0.0107 |
| segment aware vs popularity | +0.0169 | +0.0143 to +0.0196 |
| segment aware vs production blend | -0.0006 | -0.0016 to +0.0003 |

If the interval does not include 0, the difference is unlikely to be chance.

## What the new ranker relies on (share of total gain)

| Feature | Share |
|---|---|
| log_product_popularity | 20.7% |
| n_sources | 17.3% |
| retrieval_score | 13.5% |
| source_id | 12.9% |
| log_price_ratio | 11.3% |
| log_customer_purchases | 7.1% |
| discount_pct | 6.8% |
| category_affinity | 6.0% |
| avg_rating | 2.6% |
| log_customer_activity | 1.6% |
| same_type_as_anchor | 0.2% |

## Settings

```json
{
  "sample_limit": 400000,
  "pool_size": 100,
  "per_source": 20,
  "min_pool": 10,
  "embargo_days": 7,
  "content_source": true,
  "t_rank": "2025-12-24 08:12:15",
  "t_test": "2026-04-03 19:20:18",
  "t_end": "2026-06-30 22:58:53",
  "ranker_training_rows": 515400,
  "ranker_training_orders": 5154,
  "ranker_training_orders_with_purchase_in_list": 702,
  "test_orders": 5633
}
```
