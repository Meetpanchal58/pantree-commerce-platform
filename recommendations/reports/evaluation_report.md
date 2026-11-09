# Recommendation evaluation report

Generated 2026-11-08T22:16:08+00:00 by `python -m recommendations.evaluate`.

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
| Oracle (true simulator taste, not deployable) | 0.046 | 0.034 | 0.064 | 0.040 | 0.038 |

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
| Oracle (true simulator taste, not deployable) | 0.045 | 0.034 | 0.053 | 0.037 | 0.036 |

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
| Oracle (true simulator taste, not deployable) | 0.046 | 0.034 | 0.068 | 0.041 | 0.038 |

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
| Oracle (true simulator taste, not deployable) | 0.404 | 0.283 | 0.568 | 0.337 | 0.270 |

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
| Oracle (true simulator taste, not deployable) | 0.528 | 0.388 | 0.630 | 0.421 | 0.359 |

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
| Oracle (true simulator taste, not deployable) | 0.373 | 0.257 | 0.552 | 0.316 | 0.247 |

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

## Share of achievable gain captured (NDCG@10)

Because the data is synthetic, the true taste function is known. The oracle ranks each candidate list by it (ceiling, not deployable). Share captured = (ordering − random) ÷ (oracle − random), with a paired bootstrap 95% CI over orders. Random is 0% and the oracle 100% by construction; values above 100% mean the ordering beat the taste ceiling on realised purchases.

### End To End

| Ordering | All | First order | Repeat order |
|---|---|---|---|
| Random order | 0.0% (0.0% to 0.0%) | 0.0% (0.0% to 0.0%) | 0.0% (0.0% to 0.0%) |
| Popularity (most viewed) | 20.6% (13.3% to 27.4%) | 47.7% (35.8% to 59.5%) | 9.9% (0.6% to 18.4%) |
| Retrieval score (no ranker) | 46.4% (37.4% to 56.2%) | 22.5% (11.0% to 33.8%) | 55.8% (44.5% to 68.5%) |
| Old ranker (3 features) | -8.3% (-14.9% to -2.7%) | -6.7% (-15.9% to -0.0%) | -9.0% (-17.8% to -1.0%) |
| New ranker (11 features) | 71.4% (60.4% to 83.8%) | 48.8% (33.6% to 63.7%) | 80.2% (66.3% to 95.6%) |
| Production blend (50/50) | 73.4% (62.8% to 85.4%) | 54.4% (39.3% to 69.8%) | 80.9% (67.6% to 95.6%) |
| Segment-aware (popular for first-timers, blend otherwise) | 71.6% (61.0% to 83.1%) | 47.7% (35.8% to 59.5%) | 80.9% (67.6% to 95.6%) |
| Oracle (true simulator taste, not deployable) | 100.0% (100.0% to 100.0%) | 100.0% (100.0% to 100.0%) | 100.0% (100.0% to 100.0%) |

### Ranking Only

| Ordering | All | First order | Repeat order |
|---|---|---|---|
| Random order | 0.0% (0.0% to 0.0%) | 0.0% (0.0% to 0.0%) | 0.0% (0.0% to 0.0%) |
| Popularity (most viewed) | 18.7% (11.8% to 25.3%) | 46.1% (33.9% to 58.6%) | 8.7% (0.5% to 16.5%) |
| Retrieval score (no ranker) | 43.7% (35.0% to 52.9%) | 20.8% (9.3% to 33.6%) | 52.1% (41.5% to 63.9%) |
| Old ranker (3 features) | -7.4% (-14.0% to -1.5%) | -8.3% (-19.2% to -0.1%) | -7.1% (-15.3% to 0.5%) |
| New ranker (11 features) | 68.2% (58.2% to 79.1%) | 48.7% (33.4% to 63.9%) | 75.3% (62.8% to 89.0%) |
| Production blend (50/50) | 69.8% (59.8% to 80.5%) | 52.9% (38.1% to 69.3%) | 76.0% (63.7% to 89.9%) |
| Segment-aware (popular for first-timers, blend otherwise) | 68.0% (58.0% to 78.8%) | 46.1% (33.9% to 58.6%) | 76.0% (63.7% to 89.9%) |
| Oracle (true simulator taste, not deployable) | 100.0% (100.0% to 100.0%) | 100.0% (100.0% to 100.0%) | 100.0% (100.0% to 100.0%) |

## Retrieval ceiling

Ranking **all** eligible products by true taste, the top 100 contains a product the customer bought for **38.8%** of test orders (mean recall@100 33.3%). The actual candidate lists (also up to 100 products) contain one for **13.6%** (mean recall 11.1%). The gap is how much better retrieval could get with perfect knowledge of taste.

## Why the order counts differ

- 5,633 paid orders fall in the test period.
- 26 came from customers with no earlier views or purchases; a known issue (feature building groups by the history anchor and drops groups without one) leaves them out of the scored tables. Kept as-is so results stay comparable; fixing it is a follow-up.
- 174 only re-bought products the customer already had; those products are excluded from candidates, so the end-to-end tables use 5,433 orders.
- 764 orders had a bought product in the candidate list (7 of them among the no-history orders above). Ranking-only tables need at least 10 candidates and at least one product that wasn't bought: 0 dropped for a short list and 0 because every candidate was bought, leaving 757.


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
