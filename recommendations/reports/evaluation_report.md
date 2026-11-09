# Recommendation evaluation report

Generated 2026-11-09T14:58:19+00:00 by `python -m recommendations.evaluate`.

**Question asked:** for each real order in the held-out period, given everything known one hour before it, does the system rank the products the customer actually bought near the top?

Test orders: 5,831. Candidate list: up to 100 products (20 per source, then filled by score). Products the customer already bought, and products that were out of stock, not yet launched or discontinued at the query time, are excluded. The candidate list and the segment-aware ordering come from `recommendations/policy.py`, the same code the storefront serves.

## 1. Retrieval: is the right product in the list at all?

900 of 5,831 test orders (15.4%) had a bought product somewhere in the candidate list.

| Source | Orders where its list contained a bought product |
|---|---|
| collaborative | 841 (14.4%) |
| trending | 366 (6.3%) |
| recent_activity | 363 (6.2%) |
| content | 260 (4.5%) |
| fbt | 151 (2.6%) |

## 2. Ranking quality: end to end

Every test order. A bought product missing from the candidate list counts as a miss.

### All — 5,647 orders (ADEQUATELY POWERED)

| Ordering | Recall@5 | NDCG@5 | Recall@10 | NDCG@10 | MRR@10 |
|---|---|---|---|---|---|
| Random order | 0.004 | 0.003 | 0.012 | 0.005 | 0.004 |
| Popularity (most viewed) | 0.022 | 0.014 | 0.030 | 0.017 | 0.014 |
| Retrieval score (no ranker) | 0.029 | 0.020 | 0.039 | 0.024 | 0.022 |
| New ranker (11 features) | 0.047 | 0.035 | 0.060 | 0.039 | 0.036 |
| Production blend (50/50) | 0.044 | 0.033 | 0.055 | 0.037 | 0.035 |
| Segment-aware (popular for first-timers, blend otherwise) | 0.045 | 0.033 | 0.055 | 0.037 | 0.035 |
| Oracle (true simulator taste, not deployable) | 0.052 | 0.037 | 0.072 | 0.044 | 0.041 |

### First Order — 1,521 orders (ADEQUATELY POWERED)

| Ordering | Recall@5 | NDCG@5 | Recall@10 | NDCG@10 | MRR@10 |
|---|---|---|---|---|---|
| Random order | 0.003 | 0.002 | 0.010 | 0.004 | 0.003 |
| Popularity (most viewed) | 0.029 | 0.019 | 0.039 | 0.023 | 0.020 |
| Retrieval score (no ranker) | 0.013 | 0.008 | 0.018 | 0.010 | 0.009 |
| New ranker (11 features) | 0.031 | 0.022 | 0.041 | 0.026 | 0.024 |
| Production blend (50/50) | 0.024 | 0.017 | 0.039 | 0.022 | 0.020 |
| Segment-aware (popular for first-timers, blend otherwise) | 0.029 | 0.019 | 0.039 | 0.023 | 0.020 |
| Oracle (true simulator taste, not deployable) | 0.045 | 0.033 | 0.060 | 0.038 | 0.035 |

### Repeat Order — 4,126 orders (ADEQUATELY POWERED)

| Ordering | Recall@5 | NDCG@5 | Recall@10 | NDCG@10 | MRR@10 |
|---|---|---|---|---|---|
| Random order | 0.005 | 0.003 | 0.013 | 0.006 | 0.004 |
| Popularity (most viewed) | 0.020 | 0.012 | 0.027 | 0.014 | 0.012 |
| Retrieval score (no ranker) | 0.035 | 0.025 | 0.047 | 0.029 | 0.026 |
| New ranker (11 features) | 0.053 | 0.039 | 0.066 | 0.044 | 0.041 |
| Production blend (50/50) | 0.051 | 0.039 | 0.062 | 0.042 | 0.041 |
| Segment-aware (popular for first-timers, blend otherwise) | 0.051 | 0.039 | 0.062 | 0.042 | 0.041 |
| Oracle (true simulator taste, not deployable) | 0.055 | 0.039 | 0.076 | 0.046 | 0.043 |

## 3. Ranking quality: ranking only

Only orders whose candidate list contains a bought product: measures ordering alone.

### All — 900 orders (ADEQUATELY POWERED)

| Ordering | Recall@5 | NDCG@5 | Recall@10 | NDCG@10 | MRR@10 |
|---|---|---|---|---|---|
| Random order | 0.036 | 0.020 | 0.093 | 0.039 | 0.023 |
| Popularity (most viewed) | 0.162 | 0.096 | 0.220 | 0.116 | 0.087 |
| Retrieval score (no ranker) | 0.207 | 0.141 | 0.294 | 0.169 | 0.136 |
| New ranker (11 features) | 0.350 | 0.246 | 0.449 | 0.278 | 0.229 |
| Production blend (50/50) | 0.324 | 0.233 | 0.418 | 0.263 | 0.220 |
| Segment-aware (popular for first-timers, blend otherwise) | 0.332 | 0.235 | 0.417 | 0.263 | 0.220 |
| Oracle (true simulator taste, not deployable) | 0.408 | 0.274 | 0.563 | 0.325 | 0.256 |

### First Order — 167 orders (PROVISIONAL)

| Ordering | Recall@5 | NDCG@5 | Recall@10 | NDCG@10 | MRR@10 |
|---|---|---|---|---|---|
| Random order | 0.036 | 0.023 | 0.114 | 0.048 | 0.030 |
| Popularity (most viewed) | 0.322 | 0.199 | 0.436 | 0.238 | 0.183 |
| Retrieval score (no ranker) | 0.141 | 0.091 | 0.210 | 0.113 | 0.086 |
| New ranker (11 features) | 0.363 | 0.244 | 0.474 | 0.281 | 0.222 |
| Production blend (50/50) | 0.280 | 0.186 | 0.442 | 0.240 | 0.181 |
| Segment-aware (popular for first-timers, blend otherwise) | 0.322 | 0.199 | 0.436 | 0.238 | 0.183 |
| Oracle (true simulator taste, not deployable) | 0.498 | 0.346 | 0.665 | 0.401 | 0.320 |

### Repeat Order — 733 orders (ADEQUATELY POWERED)

| Ordering | Recall@5 | NDCG@5 | Recall@10 | NDCG@10 | MRR@10 |
|---|---|---|---|---|---|
| Random order | 0.036 | 0.019 | 0.089 | 0.037 | 0.022 |
| Popularity (most viewed) | 0.126 | 0.073 | 0.171 | 0.088 | 0.065 |
| Retrieval score (no ranker) | 0.222 | 0.152 | 0.313 | 0.182 | 0.147 |
| New ranker (11 features) | 0.347 | 0.246 | 0.443 | 0.277 | 0.230 |
| Production blend (50/50) | 0.334 | 0.243 | 0.412 | 0.269 | 0.228 |
| Segment-aware (popular for first-timers, blend otherwise) | 0.334 | 0.243 | 0.412 | 0.269 | 0.228 |
| Oracle (true simulator taste, not deployable) | 0.388 | 0.257 | 0.540 | 0.308 | 0.241 |

## Are the differences real? (paired bootstrap over test orders, end-to-end NDCG@10)

| Comparison | Mean difference | 95% confidence interval |
|---|---|---|
| new ranker vs retrieval | +0.0151 | +0.0125 to +0.0178 |
| new ranker vs popularity | +0.0224 | +0.0193 to +0.0257 |
| new ranker vs random | +0.0337 | +0.0298 to +0.0376 |
| segment aware vs retrieval | +0.0132 | +0.0108 to +0.0156 |
| segment aware vs popularity | +0.0206 | +0.0177 to +0.0236 |
| segment aware vs production blend | +0.0002 | -0.0011 to +0.0016 |

If the interval does not include 0, the difference is unlikely to be chance.

## Share of achievable gain captured (NDCG@10)

Because the data is synthetic, the true taste function is known. The oracle ranks each candidate list by it (ceiling, not deployable). Share captured = (ordering − random) ÷ (oracle − random), with a paired bootstrap 95% CI over orders. Random is 0% and the oracle 100% by construction; values above 100% mean the ordering beat the taste ceiling on realised purchases.

### End To End

| Ordering | All | First order | Repeat order |
|---|---|---|---|
| Random order | 0.0% (0.0% to 0.0%) | 0.0% (0.0% to 0.0%) | 0.0% (0.0% to 0.0%) |
| Popularity (most viewed) | 29.2% (22.9% to 35.6%) | 55.1% (40.9% to 70.9%) | 21.3% (14.9% to 28.5%) |
| Retrieval score (no ranker) | 48.1% (40.3% to 56.7%) | 18.3% (7.2% to 29.5%) | 57.3% (47.3% to 68.1%) |
| New ranker (11 features) | 87.0% (77.1% to 97.4%) | 64.2% (50.1% to 80.6%) | 94.0% (81.2% to 108.5%) |
| Production blend (50/50) | 81.8% (72.2% to 91.8%) | 53.1% (39.3% to 68.7%) | 90.5% (77.7% to 104.3%) |
| Segment-aware (popular for first-timers, blend otherwise) | 82.2% (72.5% to 92.4%) | 55.1% (40.9% to 70.9%) | 90.5% (77.7% to 104.3%) |
| Oracle (true simulator taste, not deployable) | 100.0% (100.0% to 100.0%) | 100.0% (100.0% to 100.0%) | 100.0% (100.0% to 100.0%) |

### Ranking Only

| Ordering | All | First order | Repeat order |
|---|---|---|---|
| Random order | 0.0% (0.0% to 0.0%) | 0.0% (0.0% to 0.0%) | 0.0% (0.0% to 0.0%) |
| Popularity (most viewed) | 26.9% (20.9% to 33.3%) | 53.8% (40.8% to 69.0%) | 18.9% (12.7% to 25.1%) |
| Retrieval score (no ranker) | 45.6% (38.3% to 53.8%) | 18.3% (6.8% to 30.2%) | 53.6% (44.7% to 62.9%) |
| New ranker (11 features) | 83.6% (73.7% to 94.4%) | 66.1% (51.1% to 82.6%) | 88.8% (77.6% to 101.2%) |
| Production blend (50/50) | 78.4% (68.5% to 88.8%) | 54.4% (39.9% to 69.6%) | 85.5% (74.5% to 98.3%) |
| Segment-aware (popular for first-timers, blend otherwise) | 78.3% (68.6% to 88.3%) | 53.8% (40.8% to 69.0%) | 85.5% (74.5% to 98.3%) |
| Oracle (true simulator taste, not deployable) | 100.0% (100.0% to 100.0%) | 100.0% (100.0% to 100.0%) | 100.0% (100.0% to 100.0%) |

## Retrieval ceiling

Ranking **all** eligible products by true taste, the top 100 contains a product the customer bought for **39.6%** of test orders (mean recall@100 33.7%). The actual candidate lists (also up to 100 products) contain one for **15.4%** (mean recall 12.6%). The gap is how much better retrieval could get with perfect knowledge of taste.

## Why the order counts differ

- 5,831 paid orders fall in the test period.
- 25 came from customers with no earlier views or purchases (cold start); they are scored with popularity candidates and empty-history feature defaults.
- 184 only re-bought products the customer already had; those products are excluded from candidates, so the end-to-end tables use 5,647 orders.
- 900 orders had a bought product in the candidate list. Ranking-only tables need at least 10 candidates and at least one product that wasn't bought: 0 dropped for a short list and 0 because every candidate was bought, leaving 900.


## What the new ranker relies on (share of total gain)

| Feature | Share |
|---|---|
| n_sources | 22.0% |
| retrieval_score | 15.4% |
| log_price_ratio | 13.4% |
| source_id | 12.6% |
| log_product_popularity | 11.6% |
| log_customer_purchases | 7.2% |
| category_affinity | 6.4% |
| discount_pct | 5.4% |
| log_customer_activity | 2.9% |
| avg_rating | 2.3% |
| same_type_as_anchor | 0.7% |

## Settings

```json
{
  "pool_size": 100,
  "per_source": 20,
  "min_pool": 10,
  "embargo_days": 7,
  "content_source": true,
  "t_rank": "2025-12-24 10:05:18",
  "t_test": "2026-04-03 21:03:57.200000",
  "t_end": "2026-06-30 22:58:53",
  "ranker_training_rows": 536473,
  "ranker_training_orders": 5365,
  "ranker_training_orders_with_purchase_in_list": 792,
  "test_orders": 5831
}
```
