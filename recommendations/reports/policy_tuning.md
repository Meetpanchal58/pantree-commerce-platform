# Policy tuning (validation period)

Generated 2026-11-09T15:43:01+00:00 by `python -m recommendations.tune_policy`.

Ranker fitted on 2,538 orders (2025-12-24 to 2026-02-04); settings scored on 2,471 validation orders (2026-02-11 to 2026-03-27). The test period (from 2026-04-03) is not used.

Metric: end-to-end NDCG@10 of the served policy on validation orders. Search: staged: trending window -> quotas x pool size -> ranker blend x first-timer rule.

| Setting | Default | Chosen |
|---|---|---|
| Quotas (collaborative/content/fbt/trending/recent_activity) | 20/20/20/20/20 | 80/17/17/17/17 |
| Pool size | 100 | 150 |
| Ranker weight in blend | 0.5 | 0.75 |
| First-time buyers get popularity order | True | False |
| Trending window (days) | 30 | 30 |

Validation NDCG@10: default 0.0362, chosen 0.0386 (paired difference +0.0025, 95% CI -0.0002 to +0.0049). Because the chosen setting is the best of many on this same period, this difference is optimistic; the test-period numbers in evaluation_report.md are the honest estimate.

## Every setting tried

| Stage | Quotas | Pool | Blend | First-timer popularity | Trending days | Val pool recall | Val NDCG@10 |
|---|---|---|---|---|---|---|---|
| trending_window | 20/20/20/20/20 | 100 | 0.5 | True | 7 | 0.1163 | 0.0336 |
| trending_window | 20/20/20/20/20 | 100 | 0.5 | True | 14 | 0.1165 | 0.0361 |
| trending_window | 20/20/20/20/20 | 100 | 0.5 | True | 30 | 0.1228 | 0.0362 |
| trending_window | 20/20/20/20/20 | 100 | 0.5 | True | 60 | 0.1261 | 0.0357 |
| quotas_pool | 20/7/7/7/7 | 50 | 0.5 | True | 30 | 0.0969 | 0.0338 |
| quotas_pool | 40/2/2/2/2 | 50 | 0.5 | True | 30 | 0.1043 | 0.0351 |
| quotas_pool | 20/20/20/20/20 | 100 | 0.5 | True | 30 | 0.1228 | 0.0362 |
| quotas_pool | 40/15/15/15/15 | 100 | 0.5 | True | 30 | 0.1312 | 0.0352 |
| quotas_pool | 60/10/10/10/10 | 100 | 0.5 | True | 30 | 0.1352 | 0.0347 |
| quotas_pool | 80/5/5/5/5 | 100 | 0.5 | True | 30 | 0.1350 | 0.0344 |
| quotas_pool | 20/32/32/32/32 | 150 | 0.5 | True | 30 | 0.1466 | 0.0353 |
| quotas_pool | 40/27/27/27/27 | 150 | 0.5 | True | 30 | 0.1534 | 0.0370 |
| quotas_pool | 60/22/22/22/22 | 150 | 0.5 | True | 30 | 0.1554 | 0.0361 |
| quotas_pool | 80/17/17/17/17 | 150 | 0.5 | True | 30 | 0.1579 | 0.0370 |
| blend_first_timer | 80/17/17/17/17 | 150 | 0.0 | True | 30 | same lists | 0.0266 |
| blend_first_timer | 80/17/17/17/17 | 150 | 0.0 | False | 30 | same lists | 0.0234 |
| blend_first_timer | 80/17/17/17/17 | 150 | 0.25 | True | 30 | same lists | 0.0350 |
| blend_first_timer | 80/17/17/17/17 | 150 | 0.25 | False | 30 | same lists | 0.0349 |
| blend_first_timer | 80/17/17/17/17 | 150 | 0.5 | True | 30 | same lists | 0.0370 |
| blend_first_timer | 80/17/17/17/17 | 150 | 0.5 | False | 30 | same lists | 0.0374 |
| blend_first_timer | 80/17/17/17/17 | 150 | 0.75 | True | 30 | same lists | 0.0381 |
| blend_first_timer | 80/17/17/17/17 | 150 | 0.75 | False | 30 | same lists | 0.0386 |
| blend_first_timer | 80/17/17/17/17 | 150 | 1.0 | True | 30 | same lists | 0.0367 |
| blend_first_timer | 80/17/17/17/17 | 150 | 1.0 | False | 30 | same lists | 0.0373 |
