# Recommendation evaluation report

Generated 2026-11-08T19:27:06+00:00 by `python -m recommendations.evaluate`.

**Question asked:** for each real order in the held-out period, given everything known one hour before it, does the system rank the products the customer actually bought near the top?

Test orders: 4,811. Candidate list: up to 100 products (20 per source, then filled by score). Products the customer already bought are excluded.

## 1. Retrieval: is the right product in the list at all?

557 of 4,811 test orders (11.6%) had a bought product somewhere in the candidate list.

| Source | Orders where its list contained a bought product |
|---|---|
| collaborative | 521 (10.8%) |
| trending | 381 (7.9%) |
| recent_activity | 205 (4.3%) |
| content | 165 (3.4%) |
| fbt | 92 (1.9%) |

## 2. Ranking quality: end to end

Every test order. A bought product missing from the candidate list counts as a miss.

### All — 4,658 orders (ADEQUATELY POWERED)

| Ordering | Recall@5 | NDCG@5 | Recall@10 | NDCG@10 | MRR@10 |
|---|---|---|---|---|---|
| Random order | 0.004 | 0.003 | 0.009 | 0.004 | 0.003 |
| Popularity (most viewed) | 0.018 | 0.012 | 0.026 | 0.015 | 0.012 |
| Retrieval score (no ranker) | 0.022 | 0.015 | 0.032 | 0.018 | 0.016 |
| Old ranker (3 features) | 0.003 | 0.002 | 0.006 | 0.003 | 0.003 |
| New ranker (11 features) | 0.031 | 0.020 | 0.046 | 0.025 | 0.021 |
| Production blend (50/50) | 0.032 | 0.021 | 0.044 | 0.026 | 0.022 |
| Segment-aware (popular for first-timers, blend otherwise) | 0.033 | 0.022 | 0.044 | 0.026 | 0.022 |

### First Order — 1,454 orders (ADEQUATELY POWERED)

| Ordering | Recall@5 | NDCG@5 | Recall@10 | NDCG@10 | MRR@10 |
|---|---|---|---|---|---|
| Random order | 0.004 | 0.002 | 0.007 | 0.003 | 0.002 |
| Popularity (most viewed) | 0.026 | 0.017 | 0.040 | 0.022 | 0.018 |
| Retrieval score (no ranker) | 0.014 | 0.008 | 0.021 | 0.010 | 0.008 |
| Old ranker (3 features) | 0.000 | 0.000 | 0.002 | 0.001 | 0.000 |
| New ranker (11 features) | 0.020 | 0.013 | 0.038 | 0.019 | 0.015 |
| Production blend (50/50) | 0.021 | 0.015 | 0.039 | 0.021 | 0.017 |
| Segment-aware (popular for first-timers, blend otherwise) | 0.026 | 0.017 | 0.040 | 0.022 | 0.018 |

### Repeat Order — 3,204 orders (ADEQUATELY POWERED)

| Ordering | Recall@5 | NDCG@5 | Recall@10 | NDCG@10 | MRR@10 |
|---|---|---|---|---|---|
| Random order | 0.005 | 0.003 | 0.009 | 0.005 | 0.004 |
| Popularity (most viewed) | 0.014 | 0.009 | 0.020 | 0.011 | 0.010 |
| Retrieval score (no ranker) | 0.026 | 0.018 | 0.037 | 0.022 | 0.019 |
| Old ranker (3 features) | 0.005 | 0.003 | 0.007 | 0.004 | 0.004 |
| New ranker (11 features) | 0.037 | 0.024 | 0.049 | 0.028 | 0.024 |
| Production blend (50/50) | 0.036 | 0.024 | 0.046 | 0.028 | 0.025 |
| Segment-aware (popular for first-timers, blend otherwise) | 0.036 | 0.024 | 0.046 | 0.028 | 0.025 |

## 3. Ranking quality: ranking only

Only orders whose candidate list contains a bought product: measures ordering alone.

### All — 556 orders (ADEQUATELY POWERED)

| Ordering | Recall@5 | NDCG@5 | Recall@10 | NDCG@10 | MRR@10 |
|---|---|---|---|---|---|
| Random order | 0.045 | 0.025 | 0.089 | 0.040 | 0.026 |
| Popularity (most viewed) | 0.171 | 0.109 | 0.249 | 0.136 | 0.101 |
| Retrieval score (no ranker) | 0.217 | 0.140 | 0.312 | 0.171 | 0.131 |
| Old ranker (3 features) | 0.039 | 0.025 | 0.066 | 0.033 | 0.025 |
| New ranker (11 features) | 0.297 | 0.190 | 0.438 | 0.236 | 0.177 |
| Production blend (50/50) | 0.303 | 0.199 | 0.424 | 0.239 | 0.185 |
| Segment-aware (popular for first-timers, blend otherwise) | 0.313 | 0.204 | 0.424 | 0.242 | 0.188 |

### First Order — 137 orders (PROVISIONAL)

| Ordering | Recall@5 | NDCG@5 | Recall@10 | NDCG@10 | MRR@10 |
|---|---|---|---|---|---|
| Random order | 0.039 | 0.019 | 0.082 | 0.033 | 0.021 |
| Popularity (most viewed) | 0.302 | 0.194 | 0.478 | 0.255 | 0.187 |
| Retrieval score (no ranker) | 0.179 | 0.099 | 0.255 | 0.125 | 0.085 |
| Old ranker (3 features) | 0.000 | 0.000 | 0.022 | 0.007 | 0.003 |
| New ranker (11 features) | 0.240 | 0.152 | 0.473 | 0.231 | 0.159 |
| Production blend (50/50) | 0.262 | 0.173 | 0.477 | 0.244 | 0.175 |
| Segment-aware (popular for first-timers, blend otherwise) | 0.302 | 0.194 | 0.478 | 0.255 | 0.187 |

### Repeat Order — 419 orders (PROVISIONAL)

| Ordering | Recall@5 | NDCG@5 | Recall@10 | NDCG@10 | MRR@10 |
|---|---|---|---|---|---|
| Random order | 0.047 | 0.027 | 0.092 | 0.042 | 0.028 |
| Popularity (most viewed) | 0.128 | 0.081 | 0.174 | 0.097 | 0.074 |
| Retrieval score (no ranker) | 0.230 | 0.153 | 0.331 | 0.186 | 0.145 |
| Old ranker (3 features) | 0.052 | 0.033 | 0.080 | 0.042 | 0.032 |
| New ranker (11 features) | 0.316 | 0.202 | 0.427 | 0.238 | 0.183 |
| Production blend (50/50) | 0.317 | 0.208 | 0.406 | 0.237 | 0.188 |
| Segment-aware (popular for first-timers, blend otherwise) | 0.317 | 0.208 | 0.406 | 0.237 | 0.188 |

## Are the differences real? (paired bootstrap over test orders, end-to-end NDCG@10)

| Comparison | Mean difference | 95% confidence interval |
|---|---|---|
| new ranker vs retrieval | +0.0072 | +0.0047 to +0.0099 |
| new ranker vs popularity | +0.0109 | +0.0079 to +0.0140 |
| new ranker vs legacy ranker | +0.0223 | +0.0189 to +0.0261 |
| new ranker vs random | +0.0213 | +0.0179 to +0.0250 |
| segment aware vs retrieval | +0.0077 | +0.0055 to +0.0102 |
| segment aware vs popularity | +0.0114 | +0.0087 to +0.0141 |
| segment aware vs production blend | +0.0002 | -0.0009 to +0.0013 |

If the interval does not include 0, the difference is unlikely to be chance.

## What the new ranker relies on (share of total gain)

| Feature | Share |
|---|---|
| log_product_popularity | 18.8% |
| n_sources | 16.3% |
| retrieval_score | 13.2% |
| source_id | 12.5% |
| log_customer_purchases | 11.0% |
| log_price_ratio | 9.1% |
| discount_pct | 5.9% |
| category_affinity | 5.1% |
| avg_rating | 4.1% |
| log_customer_activity | 2.2% |
| same_type_as_anchor | 1.8% |

## Settings

```json
{
  "sample_limit": 400000,
  "pool_size": 100,
  "per_source": 20,
  "min_pool": 10,
  "embargo_days": 7,
  "content_source": true,
  "t_rank": "2025-12-11 09:33:23",
  "t_test": "2026-03-24 09:08:19",
  "t_end": "2026-06-30 22:31:54",
  "ranker_training_rows": 443200,
  "ranker_training_orders": 4432,
  "ranker_training_orders_with_purchase_in_list": 500,
  "test_orders": 4811
}
```
