# Recommendation evaluation report

Generated 2026-11-13T23:21:02+00:00 by `python -m recommendations.evaluate`.

**Question asked:** for each real order in the held-out period, given everything known one hour before it, does the system rank the products the customer actually bought near the top?

Test orders: 13,042. Candidate list: up to 50 products (per-source quotas collaborative 20, content 7, fbt 7, trending 7, recent_activity 7, then filled by score). Products the customer already bought, and products that were out of stock, not yet launched or discontinued at the query time, are excluded. The candidate list and the segment-aware ordering come from `recommendations/policy.py`, the same code the storefront serves.

Policy settings (chosen on the validation period by `python -m recommendations.tune_policy`): pool of 50, quotas collaborative 20, content 7, fbt 7, trending 7, recent_activity 7, ranker weight 0.75 in the blend with the retrieval score, first-time buyers get the same ranker blend, trending window 60 days.

## 1. Retrieval: is the right product in the list at all?

4,463 of 13,042 test orders (34.2%) had a bought product somewhere in the candidate list.

| Source | Orders where its list contained a bought product |
|---|---|
| trending | 5,161 (39.6%) |
| collaborative | 3,696 (28.3%) |
| recent_activity | 3,248 (24.9%) |
| fbt | 1,635 (12.5%) |
| content | 319 (2.4%) |

## 2. Ranking quality: end to end

Every test order. A bought product missing from the candidate list counts as a miss.

### All — 12,456 orders (ADEQUATELY POWERED)

| Ordering | Recall@5 | NDCG@5 | Recall@10 | NDCG@10 | MRR@10 |
|---|---|---|---|---|---|
| Random order | 0.032 | 0.020 | 0.063 | 0.030 | 0.023 |
| Popularity (most viewed) | 0.053 | 0.034 | 0.111 | 0.053 | 0.040 |
| Retrieval score (no ranker) | 0.053 | 0.033 | 0.090 | 0.046 | 0.036 |
| New ranker (11 features) | 0.089 | 0.058 | 0.149 | 0.078 | 0.062 |
| Ranker blend (all customers) | 0.089 | 0.057 | 0.147 | 0.076 | 0.060 |
| Served policy (segment-aware) | 0.089 | 0.057 | 0.147 | 0.076 | 0.060 |
| Oracle (true simulator taste, not deployable) | 0.083 | 0.056 | 0.137 | 0.074 | 0.061 |

### First Order — 3,267 orders (ADEQUATELY POWERED)

| Ordering | Recall@5 | NDCG@5 | Recall@10 | NDCG@10 | MRR@10 |
|---|---|---|---|---|---|
| Random order | 0.027 | 0.016 | 0.052 | 0.025 | 0.017 |
| Popularity (most viewed) | 0.047 | 0.030 | 0.100 | 0.048 | 0.034 |
| Retrieval score (no ranker) | 0.052 | 0.030 | 0.086 | 0.041 | 0.030 |
| New ranker (11 features) | 0.065 | 0.042 | 0.110 | 0.057 | 0.044 |
| Ranker blend (all customers) | 0.066 | 0.041 | 0.110 | 0.056 | 0.042 |
| Served policy (segment-aware) | 0.066 | 0.041 | 0.110 | 0.056 | 0.042 |
| Oracle (true simulator taste, not deployable) | 0.099 | 0.066 | 0.151 | 0.084 | 0.068 |

### Repeat Order — 9,189 orders (ADEQUATELY POWERED)

| Ordering | Recall@5 | NDCG@5 | Recall@10 | NDCG@10 | MRR@10 |
|---|---|---|---|---|---|
| Random order | 0.034 | 0.021 | 0.067 | 0.032 | 0.024 |
| Popularity (most viewed) | 0.055 | 0.035 | 0.115 | 0.055 | 0.042 |
| Retrieval score (no ranker) | 0.053 | 0.034 | 0.092 | 0.048 | 0.038 |
| New ranker (11 features) | 0.098 | 0.064 | 0.163 | 0.086 | 0.068 |
| Ranker blend (all customers) | 0.097 | 0.062 | 0.160 | 0.084 | 0.067 |
| Served policy (segment-aware) | 0.097 | 0.062 | 0.160 | 0.084 | 0.067 |
| Oracle (true simulator taste, not deployable) | 0.077 | 0.052 | 0.133 | 0.071 | 0.058 |

## 3. Ranking quality: ranking only

Only orders whose candidate list contains a bought product: measures ordering alone.

### All — 4,463 orders (ADEQUATELY POWERED)

| Ordering | Recall@5 | NDCG@5 | Recall@10 | NDCG@10 | MRR@10 |
|---|---|---|---|---|---|
| Random order | 0.102 | 0.061 | 0.200 | 0.093 | 0.063 |
| Popularity (most viewed) | 0.168 | 0.104 | 0.349 | 0.163 | 0.110 |
| Retrieval score (no ranker) | 0.167 | 0.102 | 0.286 | 0.141 | 0.101 |
| New ranker (11 features) | 0.279 | 0.176 | 0.465 | 0.237 | 0.172 |
| Ranker blend (all customers) | 0.278 | 0.173 | 0.459 | 0.232 | 0.168 |
| Served policy (segment-aware) | 0.278 | 0.173 | 0.459 | 0.232 | 0.168 |
| Oracle (true simulator taste, not deployable) | 0.261 | 0.171 | 0.433 | 0.227 | 0.169 |

### First Order — 959 orders (ADEQUATELY POWERED)

| Ordering | Recall@5 | NDCG@5 | Recall@10 | NDCG@10 | MRR@10 |
|---|---|---|---|---|---|
| Random order | 0.099 | 0.059 | 0.197 | 0.090 | 0.059 |
| Popularity (most viewed) | 0.176 | 0.109 | 0.374 | 0.174 | 0.115 |
| Retrieval score (no ranker) | 0.193 | 0.110 | 0.325 | 0.153 | 0.103 |
| New ranker (11 features) | 0.242 | 0.153 | 0.409 | 0.207 | 0.150 |
| Ranker blend (all customers) | 0.245 | 0.151 | 0.405 | 0.203 | 0.145 |
| Served policy (segment-aware) | 0.245 | 0.151 | 0.405 | 0.203 | 0.145 |
| Oracle (true simulator taste, not deployable) | 0.376 | 0.246 | 0.570 | 0.309 | 0.232 |

### Repeat Order — 3,504 orders (ADEQUATELY POWERED)

| Ordering | Recall@5 | NDCG@5 | Recall@10 | NDCG@10 | MRR@10 |
|---|---|---|---|---|---|
| Random order | 0.102 | 0.062 | 0.201 | 0.094 | 0.064 |
| Popularity (most viewed) | 0.166 | 0.103 | 0.342 | 0.160 | 0.109 |
| Retrieval score (no ranker) | 0.160 | 0.100 | 0.275 | 0.138 | 0.100 |
| New ranker (11 features) | 0.289 | 0.183 | 0.481 | 0.245 | 0.178 |
| Ranker blend (all customers) | 0.287 | 0.179 | 0.474 | 0.240 | 0.175 |
| Served policy (segment-aware) | 0.287 | 0.179 | 0.474 | 0.240 | 0.175 |
| Oracle (true simulator taste, not deployable) | 0.230 | 0.151 | 0.395 | 0.205 | 0.152 |

## Are the differences real? (paired bootstrap over test orders, end-to-end NDCG@10)

| Comparison | Mean difference | 95% confidence interval |
|---|---|---|
| new ranker vs retrieval | +0.0323 | +0.0292 to +0.0353 |
| new ranker vs popularity | +0.0248 | +0.0219 to +0.0278 |
| new ranker vs random | +0.0479 | +0.0440 to +0.0516 |
| segment aware vs retrieval | +0.0304 | +0.0280 to +0.0328 |
| segment aware vs popularity | +0.0229 | +0.0202 to +0.0254 |
| segment aware vs production blend | +0.0000 | +0.0000 to +0.0000 |

If the interval does not include 0, the difference is unlikely to be chance.

## Share of achievable gain captured (NDCG@10)

Because the data is synthetic, the true taste function is known. The oracle ranks each candidate list by it (ceiling, not deployable). Share captured = (ordering − random) ÷ (oracle − random), with a paired bootstrap 95% CI over orders. Random is 0% and the oracle 100% by construction; values above 100% mean the ordering beat the taste ceiling on realised purchases.

### End To End

| Ordering | All | First order | Repeat order |
|---|---|---|---|
| Random order | 0.0% (0.0% to 0.0%) | 0.0% (0.0% to 0.0%) | 0.0% (0.0% to 0.0%) |
| Popularity (most viewed) | 52.6% (45.9% to 59.5%) | 38.9% (29.3% to 49.1%) | 60.0% (50.7% to 70.0%) |
| Retrieval score (no ranker) | 35.4% (28.6% to 41.8%) | 28.2% (19.2% to 37.1%) | 39.4% (30.2% to 48.5%) |
| New ranker (11 features) | 109.0% (100.3% to 118.7%) | 55.0% (45.7% to 65.5%) | 138.2% (124.8% to 153.6%) |
| Ranker blend (all customers) | 104.5% (95.8% to 114.1%) | 52.7% (43.4% to 63.0%) | 132.7% (119.5% to 148.1%) |
| Served policy (segment-aware) | 104.5% (95.8% to 114.1%) | 52.7% (43.4% to 63.0%) | 132.7% (119.5% to 148.1%) |
| Oracle (true simulator taste, not deployable) | 100.0% (100.0% to 100.0%) | 100.0% (100.0% to 100.0%) | 100.0% (100.0% to 100.0%) |

### Ranking Only

| Ordering | All | First order | Repeat order |
|---|---|---|---|
| Random order | 0.0% (0.0% to 0.0%) | 0.0% (0.0% to 0.0%) | 0.0% (0.0% to 0.0%) |
| Popularity (most viewed) | 52.5% (45.8% to 59.0%) | 38.4% (29.1% to 47.8%) | 60.1% (51.0% to 69.8%) |
| Retrieval score (no ranker) | 36.1% (29.2% to 42.5%) | 28.7% (20.1% to 37.4%) | 40.0% (31.4% to 49.2%) |
| New ranker (11 features) | 107.5% (98.9% to 116.9%) | 53.7% (43.9% to 63.9%) | 136.6% (123.3% to 151.7%) |
| Ranker blend (all customers) | 104.0% (95.2% to 113.3%) | 51.7% (41.8% to 61.4%) | 132.3% (119.4% to 147.4%) |
| Served policy (segment-aware) | 104.0% (95.2% to 113.3%) | 51.7% (41.8% to 61.4%) | 132.3% (119.4% to 147.4%) |
| Oracle (true simulator taste, not deployable) | 100.0% (100.0% to 100.0%) | 100.0% (100.0% to 100.0%) | 100.0% (100.0% to 100.0%) |

## Retrieval ceiling

Ranking **all** eligible products by true taste, the top 50 contains a product the customer bought for **17.8%** of test orders (mean recall@50 16.2%). The actual candidate lists (also up to 50 products) contain one for **34.2%** (mean recall 31.6%). The gap is how much better retrieval could get with perfect knowledge of taste.


## Leave-one-source-out ablation (test period)

Each row rebuilds the candidate lists without one source, retrains the ranker without it on period B and scores the served policy on the same test orders. The interval is the paired-bootstrap 95% CI of the change in NDCG@10 versus the full system.

| Without | Pool recall | Served NDCG@10 | Change vs full (95% CI) |
|---|---|---|---|
| (nothing: full system) | 0.3157 | 0.0763 | – |
| collaborative | 0.2650 | 0.0739 | -0.0024 (-0.0046 to -0.0002) |
| content | 0.3434 | 0.0777 | +0.0014 (-0.0003 to +0.0032) |
| fbt | 0.3098 | 0.0761 | -0.0002 (-0.0019 to +0.0016) |
| trending | 0.2680 | 0.0688 | -0.0074 (-0.0095 to -0.0053) |
| recent_activity | 0.2548 | 0.0687 | -0.0076 (-0.0094 to -0.0057) |

**Recall acceptance check (passed):** combined candidate list recall 0.3157 vs 0.2520 for the best single source (trending, its own top list of the same size).

## Why the order counts differ

- 13,042 paid orders fall in the test period.
- 1,873 came from customers with no earlier views or purchases (cold start); they are scored with popularity candidates and empty-history feature defaults.
- 586 only re-bought products the customer already had; those products are excluded from candidates, so the end-to-end tables use 12,456 orders.
- 4,463 orders had a bought product in the candidate list. Ranking-only tables need at least 10 candidates and at least one product that wasn't bought: 0 dropped for a short list and 0 because every candidate was bought, leaving 4,463.


## What the new ranker relies on (share of total gain)

| Feature | Share |
|---|---|
| log_product_popularity | 33.8% |
| category_affinity | 15.9% |
| log_price_ratio | 9.9% |
| avg_rating | 9.1% |
| log_customer_activity | 8.3% |
| retrieval_score | 8.0% |
| discount_pct | 6.5% |
| source_id | 3.4% |
| log_customer_purchases | 3.0% |
| n_sources | 1.8% |
| same_type_as_anchor | 0.4% |

## Settings

```json
{
  "pool_size": 50,
  "per_source": {
    "collaborative": 20,
    "content": 7,
    "fbt": 7,
    "trending": 7,
    "recent_activity": 7
  },
  "policy": {
    "quotas": {
      "collaborative": 20,
      "content": 7,
      "fbt": 7,
      "trending": 7,
      "recent_activity": 7
    },
    "pool_size": 50,
    "ranker_blend": 0.75,
    "first_timer_popularity": false,
    "trending_window_days": 60
  },
  "min_pool": 10,
  "embargo_days": 7,
  "content_source": true,
  "t_rank": "2026-05-07 21:37:05",
  "t_test": "2026-08-23 23:40:34",
  "t_end": "2026-11-13 23:46:50",
  "ranker_training_rows": 610050,
  "ranker_training_orders": 12201,
  "ranker_training_orders_with_purchase_in_list": 4174,
  "test_orders": 13042
}
```
