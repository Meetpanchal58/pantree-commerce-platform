# Recommendation evaluation report

Generated 2026-11-09T16:38:26+00:00 by `python -m recommendations.evaluate`.

**Question asked:** for each real order in the held-out period, given everything known one hour before it, does the system rank the products the customer actually bought near the top?

Test orders: 5,831. Candidate list: up to 150 products (per-source quotas collaborative 80, content 17, fbt 17, trending 17, recent_activity 17, then filled by score). Products the customer already bought, and products that were out of stock, not yet launched or discontinued at the query time, are excluded. The candidate list and the segment-aware ordering come from `recommendations/policy.py`, the same code the storefront serves.

Policy settings (chosen on the validation period by `python -m recommendations.tune_policy`): pool of 150, quotas collaborative 80, content 17, fbt 17, trending 17, recent_activity 17, ranker weight 0.75 in the blend with the retrieval score, first-time buyers get the same ranker blend, trending window 30 days.

## 1. Retrieval: is the right product in the list at all?

1,191 of 5,831 test orders (20.4%) had a bought product somewhere in the candidate list.

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
| Random order | 0.006 | 0.004 | 0.014 | 0.006 | 0.005 |
| Popularity (most viewed) | 0.022 | 0.014 | 0.030 | 0.016 | 0.014 |
| Retrieval score (no ranker) | 0.029 | 0.020 | 0.039 | 0.024 | 0.022 |
| New ranker (11 features) | 0.046 | 0.034 | 0.060 | 0.038 | 0.035 |
| Ranker blend (all customers) | 0.046 | 0.034 | 0.058 | 0.039 | 0.036 |
| Served policy (segment-aware) | 0.046 | 0.034 | 0.058 | 0.039 | 0.036 |
| Oracle (true simulator taste, not deployable) | 0.051 | 0.036 | 0.076 | 0.045 | 0.041 |

### First Order — 1,521 orders (ADEQUATELY POWERED)

| Ordering | Recall@5 | NDCG@5 | Recall@10 | NDCG@10 | MRR@10 |
|---|---|---|---|---|---|
| Random order | 0.003 | 0.003 | 0.007 | 0.004 | 0.004 |
| Popularity (most viewed) | 0.028 | 0.019 | 0.038 | 0.023 | 0.020 |
| Retrieval score (no ranker) | 0.013 | 0.008 | 0.018 | 0.010 | 0.009 |
| New ranker (11 features) | 0.031 | 0.021 | 0.041 | 0.025 | 0.023 |
| Ranker blend (all customers) | 0.029 | 0.021 | 0.042 | 0.025 | 0.023 |
| Served policy (segment-aware) | 0.029 | 0.021 | 0.042 | 0.025 | 0.023 |
| Oracle (true simulator taste, not deployable) | 0.043 | 0.031 | 0.061 | 0.038 | 0.035 |

### Repeat Order — 4,126 orders (ADEQUATELY POWERED)

| Ordering | Recall@5 | NDCG@5 | Recall@10 | NDCG@10 | MRR@10 |
|---|---|---|---|---|---|
| Random order | 0.007 | 0.004 | 0.016 | 0.007 | 0.005 |
| Popularity (most viewed) | 0.020 | 0.012 | 0.026 | 0.014 | 0.012 |
| Retrieval score (no ranker) | 0.035 | 0.025 | 0.047 | 0.029 | 0.026 |
| New ranker (11 features) | 0.051 | 0.038 | 0.067 | 0.043 | 0.040 |
| Ranker blend (all customers) | 0.052 | 0.039 | 0.065 | 0.043 | 0.041 |
| Served policy (segment-aware) | 0.052 | 0.039 | 0.065 | 0.043 | 0.041 |
| Oracle (true simulator taste, not deployable) | 0.054 | 0.038 | 0.081 | 0.048 | 0.043 |

## 3. Ranking quality: ranking only

Only orders whose candidate list contains a bought product: measures ordering alone.

### All — 1,191 orders (ADEQUATELY POWERED)

| Ordering | Recall@5 | NDCG@5 | Recall@10 | NDCG@10 | MRR@10 |
|---|---|---|---|---|---|
| Random order | 0.032 | 0.019 | 0.076 | 0.033 | 0.022 |
| Popularity (most viewed) | 0.120 | 0.072 | 0.162 | 0.086 | 0.066 |
| Retrieval score (no ranker) | 0.155 | 0.106 | 0.219 | 0.127 | 0.103 |
| New ranker (11 features) | 0.253 | 0.178 | 0.330 | 0.203 | 0.168 |
| Ranker blend (all customers) | 0.250 | 0.180 | 0.327 | 0.205 | 0.172 |
| Served policy (segment-aware) | 0.250 | 0.180 | 0.327 | 0.205 | 0.172 |
| Oracle (true simulator taste, not deployable) | 0.296 | 0.200 | 0.442 | 0.249 | 0.195 |

### First Order — 225 orders (PROVISIONAL)

| Ordering | Recall@5 | NDCG@5 | Recall@10 | NDCG@10 | MRR@10 |
|---|---|---|---|---|---|
| Random order | 0.024 | 0.020 | 0.058 | 0.030 | 0.024 |
| Popularity (most viewed) | 0.230 | 0.144 | 0.315 | 0.174 | 0.135 |
| Retrieval score (no ranker) | 0.102 | 0.066 | 0.153 | 0.083 | 0.064 |
| New ranker (11 features) | 0.256 | 0.166 | 0.343 | 0.195 | 0.153 |
| Ranker blend (all customers) | 0.236 | 0.159 | 0.361 | 0.201 | 0.158 |
| Served policy (segment-aware) | 0.236 | 0.159 | 0.361 | 0.201 | 0.158 |
| Oracle (true simulator taste, not deployable) | 0.355 | 0.247 | 0.511 | 0.299 | 0.238 |

### Repeat Order — 966 orders (ADEQUATELY POWERED)

| Ordering | Recall@5 | NDCG@5 | Recall@10 | NDCG@10 | MRR@10 |
|---|---|---|---|---|---|
| Random order | 0.033 | 0.019 | 0.081 | 0.034 | 0.021 |
| Popularity (most viewed) | 0.095 | 0.055 | 0.126 | 0.066 | 0.050 |
| Retrieval score (no ranker) | 0.168 | 0.115 | 0.234 | 0.137 | 0.112 |
| New ranker (11 features) | 0.252 | 0.180 | 0.327 | 0.205 | 0.171 |
| Ranker blend (all customers) | 0.253 | 0.184 | 0.320 | 0.206 | 0.175 |
| Served policy (segment-aware) | 0.253 | 0.184 | 0.320 | 0.206 | 0.175 |
| Oracle (true simulator taste, not deployable) | 0.282 | 0.189 | 0.426 | 0.237 | 0.185 |

## Are the differences real? (paired bootstrap over test orders, end-to-end NDCG@10)

| Comparison | Mean difference | 95% confidence interval |
|---|---|---|
| new ranker vs retrieval | +0.0145 | +0.0118 to +0.0171 |
| new ranker vs popularity | +0.0219 | +0.0187 to +0.0251 |
| new ranker vs random | +0.0321 | +0.0281 to +0.0359 |
| segment aware vs retrieval | +0.0147 | +0.0122 to +0.0172 |
| segment aware vs popularity | +0.0221 | +0.0190 to +0.0253 |
| segment aware vs production blend | +0.0000 | +0.0000 to +0.0000 |

If the interval does not include 0, the difference is unlikely to be chance.

## Share of achievable gain captured (NDCG@10)

Because the data is synthetic, the true taste function is known. The oracle ranks each candidate list by it (ceiling, not deployable). Share captured = (ordering − random) ÷ (oracle − random), with a paired bootstrap 95% CI over orders. Random is 0% and the oracle 100% by construction; values above 100% mean the ordering beat the taste ceiling on realised purchases.

### End To End

| Ordering | All | First order | Repeat order |
|---|---|---|---|
| Random order | 0.0% (0.0% to 0.0%) | 0.0% (0.0% to 0.0%) | 0.0% (0.0% to 0.0%) |
| Popularity (most viewed) | 26.2% (19.4% to 33.2%) | 54.9% (39.7% to 72.4%) | 17.3% (10.3% to 24.6%) |
| Retrieval score (no ranker) | 45.3% (37.3% to 53.8%) | 19.2% (7.0% to 31.7%) | 53.5% (43.3% to 64.4%) |
| New ranker (11 features) | 82.5% (72.2% to 93.2%) | 61.2% (46.6% to 77.8%) | 89.1% (76.2% to 103.3%) |
| Ranker blend (all customers) | 83.0% (72.7% to 93.7%) | 62.7% (48.2% to 79.7%) | 89.3% (76.6% to 103.7%) |
| Served policy (segment-aware) | 83.0% (72.7% to 93.7%) | 62.7% (48.2% to 79.7%) | 89.3% (76.6% to 103.7%) |
| Oracle (true simulator taste, not deployable) | 100.0% (100.0% to 100.0%) | 100.0% (100.0% to 100.0%) | 100.0% (100.0% to 100.0%) |

### Ranking Only

| Ordering | All | First order | Repeat order |
|---|---|---|---|
| Random order | 0.0% (0.0% to 0.0%) | 0.0% (0.0% to 0.0%) | 0.0% (0.0% to 0.0%) |
| Popularity (most viewed) | 24.6% (18.2% to 31.1%) | 53.3% (39.0% to 69.5%) | 15.7% (9.4% to 22.3%) |
| Retrieval score (no ranker) | 43.4% (35.6% to 51.6%) | 19.5% (7.5% to 32.4%) | 50.8% (41.6% to 60.8%) |
| New ranker (11 features) | 79.0% (69.0% to 90.4%) | 61.4% (47.1% to 78.7%) | 84.5% (72.8% to 97.0%) |
| Ranker blend (all customers) | 80.0% (69.6% to 91.3%) | 63.6% (49.4% to 80.1%) | 85.0% (73.2% to 98.5%) |
| Served policy (segment-aware) | 80.0% (69.6% to 91.3%) | 63.6% (49.4% to 80.1%) | 85.0% (73.2% to 98.5%) |
| Oracle (true simulator taste, not deployable) | 100.0% (100.0% to 100.0%) | 100.0% (100.0% to 100.0%) | 100.0% (100.0% to 100.0%) |

## Retrieval ceiling

Ranking **all** eligible products by true taste, the top 150 contains a product the customer bought for **46.2%** of test orders (mean recall@150 39.8%). The actual candidate lists (also up to 150 products) contain one for **20.4%** (mean recall 16.7%). The gap is how much better retrieval could get with perfect knowledge of taste.


## Leave-one-source-out ablation (test period)

Each row rebuilds the candidate lists without one source, retrains the ranker without it on period B and scores the served policy on the same test orders. The interval is the paired-bootstrap 95% CI of the change in NDCG@10 versus the full system.

| Without | Pool recall | Served NDCG@10 | Change vs full (95% CI) |
|---|---|---|---|
| (nothing: full system) | 0.1667 | 0.0386 | – |
| collaborative | 0.1153 | 0.0294 | -0.0092 (-0.0119 to -0.0066) |
| content | 0.1609 | 0.0384 | -0.0001 (-0.0015 to +0.0012) |
| fbt | 0.1655 | 0.0376 | -0.0010 (-0.0023 to +0.0003) |
| trending | 0.1609 | 0.0340 | -0.0045 (-0.0062 to -0.0030) |
| recent_activity | 0.1596 | 0.0400 | +0.0014 (+0.0002 to +0.0026) |

**Recall acceptance check (passed):** combined candidate list recall 0.1667 vs 0.1178 for the best single source (collaborative, its own top list of the same size).

## Why the order counts differ

- 5,831 paid orders fall in the test period.
- 25 came from customers with no earlier views or purchases (cold start); they are scored with popularity candidates and empty-history feature defaults.
- 184 only re-bought products the customer already had; those products are excluded from candidates, so the end-to-end tables use 5,647 orders.
- 1,191 orders had a bought product in the candidate list. Ranking-only tables need at least 10 candidates and at least one product that wasn't bought: 0 dropped for a short list and 0 because every candidate was bought, leaving 1,191.


## What the new ranker relies on (share of total gain)

| Feature | Share |
|---|---|
| n_sources | 22.9% |
| retrieval_score | 20.7% |
| log_product_popularity | 11.8% |
| log_price_ratio | 11.6% |
| source_id | 9.5% |
| log_customer_purchases | 7.7% |
| category_affinity | 5.7% |
| discount_pct | 4.6% |
| log_customer_activity | 2.6% |
| avg_rating | 2.4% |
| same_type_as_anchor | 0.5% |

## Settings

```json
{
  "pool_size": 150,
  "per_source": {
    "collaborative": 80,
    "content": 17,
    "fbt": 17,
    "trending": 17,
    "recent_activity": 17
  },
  "policy": {
    "quotas": {
      "collaborative": 80,
      "content": 17,
      "fbt": 17,
      "trending": 17,
      "recent_activity": 17
    },
    "pool_size": 150,
    "ranker_blend": 0.75,
    "first_timer_popularity": false,
    "trending_window_days": 30
  },
  "min_pool": 10,
  "embargo_days": 7,
  "content_source": true,
  "t_rank": "2025-12-24 10:05:18",
  "t_test": "2026-04-03 21:03:57.200000",
  "t_end": "2026-06-30 22:58:53",
  "ranker_training_rows": 803569,
  "ranker_training_orders": 5365,
  "ranker_training_orders_with_purchase_in_list": 1025,
  "test_orders": 5831
}
```
