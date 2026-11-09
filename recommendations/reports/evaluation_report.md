# Recommendation evaluation report

Generated 2026-11-09T14:35:46+00:00 by `python -m recommendations.evaluate`.

**Question asked:** for each real order in the held-out period, given everything known one hour before it, does the system rank the products the customer actually bought near the top?

Test orders: 5,831. Candidate list: up to 100 products (20 per source, then filled by score). Products the customer already bought are excluded.

## 1. Retrieval: is the right product in the list at all?

789 of 5,831 test orders (13.5%) had a bought product somewhere in the candidate list.

| Source | Orders where its list contained a bought product |
|---|---|
| collaborative | 841 (14.4%) |
| recent_activity | 342 (5.9%) |
| trending | 320 (5.5%) |
| content | 260 (4.5%) |
| fbt | 152 (2.6%) |

## 2. Ranking quality: end to end

Every test order. A bought product missing from the candidate list counts as a miss.

### All — 5,647 orders (ADEQUATELY POWERED)

| Ordering | Recall@5 | NDCG@5 | Recall@10 | NDCG@10 | MRR@10 |
|---|---|---|---|---|---|
| Random order | 0.005 | 0.004 | 0.011 | 0.005 | 0.004 |
| Popularity (most viewed) | 0.019 | 0.011 | 0.028 | 0.014 | 0.011 |
| Retrieval score (no ranker) | 0.025 | 0.019 | 0.035 | 0.022 | 0.021 |
| Old ranker (3 features) | 0.003 | 0.002 | 0.007 | 0.003 | 0.003 |
| New ranker (11 features) | 0.036 | 0.025 | 0.047 | 0.029 | 0.027 |
| Production blend (50/50) | 0.039 | 0.028 | 0.047 | 0.030 | 0.028 |
| Segment-aware (popular for first-timers, blend otherwise) | 0.039 | 0.027 | 0.049 | 0.031 | 0.028 |
| Oracle (true simulator taste, not deployable) | 0.044 | 0.032 | 0.061 | 0.038 | 0.036 |

### First Order — 1,521 orders (ADEQUATELY POWERED)

| Ordering | Recall@5 | NDCG@5 | Recall@10 | NDCG@10 | MRR@10 |
|---|---|---|---|---|---|
| Random order | 0.005 | 0.003 | 0.012 | 0.006 | 0.004 |
| Popularity (most viewed) | 0.025 | 0.015 | 0.040 | 0.020 | 0.016 |
| Retrieval score (no ranker) | 0.011 | 0.007 | 0.019 | 0.009 | 0.007 |
| Old ranker (3 features) | 0.001 | 0.001 | 0.003 | 0.001 | 0.001 |
| New ranker (11 features) | 0.015 | 0.012 | 0.024 | 0.014 | 0.014 |
| Production blend (50/50) | 0.024 | 0.016 | 0.032 | 0.019 | 0.016 |
| Segment-aware (popular for first-timers, blend otherwise) | 0.025 | 0.015 | 0.040 | 0.020 | 0.016 |
| Oracle (true simulator taste, not deployable) | 0.043 | 0.033 | 0.053 | 0.036 | 0.035 |

### Repeat Order — 4,126 orders (ADEQUATELY POWERED)

| Ordering | Recall@5 | NDCG@5 | Recall@10 | NDCG@10 | MRR@10 |
|---|---|---|---|---|---|
| Random order | 0.006 | 0.004 | 0.011 | 0.005 | 0.005 |
| Popularity (most viewed) | 0.017 | 0.010 | 0.024 | 0.012 | 0.009 |
| Retrieval score (no ranker) | 0.030 | 0.023 | 0.041 | 0.027 | 0.026 |
| Old ranker (3 features) | 0.004 | 0.002 | 0.009 | 0.004 | 0.003 |
| New ranker (11 features) | 0.043 | 0.030 | 0.056 | 0.035 | 0.031 |
| Production blend (50/50) | 0.044 | 0.032 | 0.052 | 0.034 | 0.032 |
| Segment-aware (popular for first-timers, blend otherwise) | 0.044 | 0.032 | 0.052 | 0.034 | 0.032 |
| Oracle (true simulator taste, not deployable) | 0.044 | 0.032 | 0.065 | 0.039 | 0.036 |

## 3. Ranking quality: ranking only

Only orders whose candidate list contains a bought product: measures ordering alone.

### All — 789 orders (ADEQUATELY POWERED)

| Ordering | Recall@5 | NDCG@5 | Recall@10 | NDCG@10 | MRR@10 |
|---|---|---|---|---|---|
| Random order | 0.045 | 0.029 | 0.097 | 0.046 | 0.032 |
| Popularity (most viewed) | 0.161 | 0.088 | 0.243 | 0.117 | 0.079 |
| Retrieval score (no ranker) | 0.204 | 0.148 | 0.296 | 0.178 | 0.148 |
| Old ranker (3 features) | 0.035 | 0.020 | 0.074 | 0.032 | 0.020 |
| New ranker (11 features) | 0.298 | 0.203 | 0.401 | 0.236 | 0.190 |
| Production blend (50/50) | 0.328 | 0.220 | 0.402 | 0.244 | 0.200 |
| Segment-aware (popular for first-timers, blend otherwise) | 0.329 | 0.217 | 0.421 | 0.248 | 0.199 |
| Oracle (true simulator taste, not deployable) | 0.393 | 0.270 | 0.550 | 0.322 | 0.255 |

### First Order — 152 orders (PROVISIONAL)

| Ordering | Recall@5 | NDCG@5 | Recall@10 | NDCG@10 | MRR@10 |
|---|---|---|---|---|---|
| Random order | 0.053 | 0.035 | 0.132 | 0.061 | 0.041 |
| Popularity (most viewed) | 0.309 | 0.168 | 0.502 | 0.235 | 0.156 |
| Retrieval score (no ranker) | 0.125 | 0.073 | 0.224 | 0.105 | 0.069 |
| Old ranker (3 features) | 0.020 | 0.009 | 0.053 | 0.020 | 0.010 |
| New ranker (11 features) | 0.190 | 0.137 | 0.298 | 0.172 | 0.136 |
| Production blend (50/50) | 0.302 | 0.186 | 0.404 | 0.218 | 0.164 |
| Segment-aware (popular for first-timers, blend otherwise) | 0.309 | 0.168 | 0.502 | 0.235 | 0.156 |
| Oracle (true simulator taste, not deployable) | 0.520 | 0.376 | 0.641 | 0.415 | 0.346 |

### Repeat Order — 637 orders (ADEQUATELY POWERED)

| Ordering | Recall@5 | NDCG@5 | Recall@10 | NDCG@10 | MRR@10 |
|---|---|---|---|---|---|
| Random order | 0.043 | 0.027 | 0.089 | 0.042 | 0.029 |
| Popularity (most viewed) | 0.125 | 0.069 | 0.181 | 0.088 | 0.061 |
| Retrieval score (no ranker) | 0.223 | 0.166 | 0.313 | 0.196 | 0.167 |
| Old ranker (3 features) | 0.039 | 0.022 | 0.079 | 0.035 | 0.022 |
| New ranker (11 features) | 0.324 | 0.218 | 0.426 | 0.251 | 0.203 |
| Production blend (50/50) | 0.334 | 0.228 | 0.402 | 0.250 | 0.209 |
| Segment-aware (popular for first-timers, blend otherwise) | 0.334 | 0.228 | 0.402 | 0.250 | 0.209 |
| Oracle (true simulator taste, not deployable) | 0.363 | 0.245 | 0.528 | 0.300 | 0.234 |

## Are the differences real? (paired bootstrap over test orders, end-to-end NDCG@10)

| Comparison | Mean difference | 95% confidence interval |
|---|---|---|
| new ranker vs retrieval | +0.0070 | +0.0045 to +0.0094 |
| new ranker vs popularity | +0.0149 | +0.0119 to +0.0176 |
| new ranker vs legacy ranker | +0.0259 | +0.0225 to +0.0295 |
| new ranker vs random | +0.0238 | +0.0202 to +0.0272 |
| segment aware vs retrieval | +0.0083 | +0.0062 to +0.0104 |
| segment aware vs popularity | +0.0162 | +0.0136 to +0.0188 |
| segment aware vs production blend | +0.0004 | -0.0005 to +0.0012 |

If the interval does not include 0, the difference is unlikely to be chance.

## Share of achievable gain captured (NDCG@10)

Because the data is synthetic, the true taste function is known. The oracle ranks each candidate list by it (ceiling, not deployable). Share captured = (ordering − random) ÷ (oracle − random), with a paired bootstrap 95% CI over orders. Random is 0% and the oracle 100% by construction; values above 100% mean the ordering beat the taste ceiling on realised purchases.

### End To End

| Ordering | All | First order | Repeat order |
|---|---|---|---|
| Random order | 0.0% (0.0% to 0.0%) | 0.0% (0.0% to 0.0%) | 0.0% (0.0% to 0.0%) |
| Popularity (most viewed) | 27.2% (20.7% to 33.6%) | 47.4% (34.4% to 62.3%) | 20.3% (12.7% to 27.9%) |
| Retrieval score (no ranker) | 51.5% (42.4% to 61.2%) | 11.9% (-1.9% to 25.0%) | 64.9% (53.1% to 78.1%) |
| Old ranker (3 features) | -6.7% (-12.0% to -2.1%) | -13.9% (-26.5% to -4.7%) | -4.2% (-10.6% to 1.5%) |
| New ranker (11 features) | 72.8% (62.8% to 82.9%) | 28.9% (14.3% to 43.8%) | 87.7% (74.8% to 102.0%) |
| Production blend (50/50) | 75.7% (65.9% to 86.9%) | 43.0% (27.9% to 59.1%) | 86.8% (73.6% to 101.6%) |
| Segment-aware (popular for first-timers, blend otherwise) | 76.8% (67.3% to 87.8%) | 47.4% (34.4% to 62.3%) | 86.8% (73.6% to 101.6%) |
| Oracle (true simulator taste, not deployable) | 100.0% (100.0% to 100.0%) | 100.0% (100.0% to 100.0%) | 100.0% (100.0% to 100.0%) |

### Ranking Only

| Ordering | All | First order | Repeat order |
|---|---|---|---|
| Random order | 0.0% (0.0% to 0.0%) | 0.0% (0.0% to 0.0%) | 0.0% (0.0% to 0.0%) |
| Popularity (most viewed) | 25.7% (19.8% to 32.1%) | 49.2% (36.8% to 62.9%) | 18.0% (10.2% to 25.3%) |
| Retrieval score (no ranker) | 48.0% (39.2% to 57.8%) | 12.4% (-0.7% to 24.1%) | 59.7% (49.0% to 71.1%) |
| Old ranker (3 features) | -4.9% (-9.9% to 0.0%) | -11.7% (-23.6% to -3.1%) | -2.7% (-8.7% to 3.1%) |
| New ranker (11 features) | 68.9% (59.5% to 79.2%) | 31.4% (16.6% to 45.8%) | 81.2% (69.1% to 93.7%) |
| Production blend (50/50) | 71.9% (62.3% to 82.8%) | 44.5% (30.1% to 60.2%) | 80.9% (68.4% to 93.9%) |
| Segment-aware (popular for first-timers, blend otherwise) | 73.1% (63.5% to 84.4%) | 49.2% (36.8% to 62.9%) | 80.9% (68.4% to 93.9%) |
| Oracle (true simulator taste, not deployable) | 100.0% (100.0% to 100.0%) | 100.0% (100.0% to 100.0%) | 100.0% (100.0% to 100.0%) |

## Retrieval ceiling

Ranking **all** eligible products by true taste, the top 100 contains a product the customer bought for **39.6%** of test orders (mean recall@100 33.7%). The actual candidate lists (also up to 100 products) contain one for **13.5%** (mean recall 10.9%). The gap is how much better retrieval could get with perfect knowledge of taste.

## Why the order counts differ

- 5,831 paid orders fall in the test period.
- 25 came from customers with no earlier views or purchases (cold start); they are scored with popularity candidates and empty-history feature defaults.
- 184 only re-bought products the customer already had; those products are excluded from candidates, so the end-to-end tables use 5,647 orders.
- 789 orders had a bought product in the candidate list. Ranking-only tables need at least 10 candidates and at least one product that wasn't bought: 0 dropped for a short list and 0 because every candidate was bought, leaving 789.


## What the new ranker relies on (share of total gain)

| Feature | Share |
|---|---|
| log_product_popularity | 21.5% |
| n_sources | 16.2% |
| log_price_ratio | 12.9% |
| retrieval_score | 12.8% |
| source_id | 11.5% |
| discount_pct | 7.6% |
| log_customer_purchases | 7.3% |
| category_affinity | 5.4% |
| log_customer_activity | 2.2% |
| avg_rating | 1.6% |
| same_type_as_anchor | 1.0% |

## Settings

```json
{
  "sample_limit": 400000,
  "pool_size": 100,
  "per_source": 20,
  "min_pool": 10,
  "embargo_days": 7,
  "content_source": true,
  "t_rank": "2025-12-24 10:05:18",
  "t_test": "2026-04-03 21:03:57.200000",
  "t_end": "2026-06-30 22:58:53",
  "ranker_training_rows": 536500,
  "ranker_training_orders": 5365,
  "ranker_training_orders_with_purchase_in_list": 734,
  "test_orders": 5831
}
```
