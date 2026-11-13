# Policy tuning (validation period)

Generated 2026-11-13T22:39:13+00:00 by `python -m recommendations.tune_policy`.

Ranker fitted on 5,887 orders (2026-05-07 to 2026-06-30); settings scored on 5,646 validation orders (2026-07-07 to 2026-08-16). The test period (from 2026-08-23) is not used.

Metric: end-to-end NDCG@10 of the served policy on validation orders. Search: staged: trending window -> quotas x pool size -> ranker blend x first-timer rule.

| Setting | Default | Chosen |
|---|---|---|
| Quotas (collaborative/content/fbt/trending/recent_activity) | 20/20/20/20/20 | 20/7/7/7/7 |
| Pool size | 100 | 50 |
| Ranker weight in blend | 0.5 | 0.75 |
| First-time buyers get popularity order | True | False |
| Trending window (days) | 30 | 60 |

Validation NDCG@10: default 0.0703, chosen 0.0778 (paired difference +0.0075, 95% CI +0.0040 to +0.0112). Because the chosen setting is the best of many on this same period, this difference is optimistic; the test-period numbers in evaluation_report.md are the honest estimate.

## Every setting tried

| Stage | Quotas | Pool | Blend | First-timer popularity | Trending days | Val pool recall | Val NDCG@10 |
|---|---|---|---|---|---|---|---|
| trending_window | 20/20/20/20/20 | 100 | 0.5 | True | 7 | 0.4032 | 0.0699 |
| trending_window | 20/20/20/20/20 | 100 | 0.5 | True | 14 | 0.4030 | 0.0690 |
| trending_window | 20/20/20/20/20 | 100 | 0.5 | True | 30 | 0.4054 | 0.0703 |
| trending_window | 20/20/20/20/20 | 100 | 0.5 | True | 60 | 0.4030 | 0.0719 |
| quotas_pool | 20/7/7/7/7 | 50 | 0.5 | True | 60 | 0.3052 | 0.0723 |
| quotas_pool | 40/2/2/2/2 | 50 | 0.5 | True | 60 | 0.2962 | 0.0706 |
| quotas_pool | 20/20/20/20/20 | 100 | 0.5 | True | 60 | 0.4030 | 0.0719 |
| quotas_pool | 40/15/15/15/15 | 100 | 0.5 | True | 60 | 0.4228 | 0.0701 |
| quotas_pool | 60/10/10/10/10 | 100 | 0.5 | True | 60 | 0.4299 | 0.0692 |
| quotas_pool | 80/5/5/5/5 | 100 | 0.5 | True | 60 | 0.4240 | 0.0704 |
| quotas_pool | 20/32/32/32/32 | 150 | 0.5 | True | 60 | 0.4637 | 0.0690 |
| quotas_pool | 40/27/27/27/27 | 150 | 0.5 | True | 60 | 0.4776 | 0.0691 |
| quotas_pool | 60/22/22/22/22 | 150 | 0.5 | True | 60 | 0.4853 | 0.0694 |
| quotas_pool | 80/17/17/17/17 | 150 | 0.5 | True | 60 | 0.4948 | 0.0695 |
| blend_first_timer | 20/7/7/7/7 | 50 | 0.0 | True | 60 | same lists | 0.0484 |
| blend_first_timer | 20/7/7/7/7 | 50 | 0.0 | False | 60 | same lists | 0.0481 |
| blend_first_timer | 20/7/7/7/7 | 50 | 0.25 | True | 60 | same lists | 0.0633 |
| blend_first_timer | 20/7/7/7/7 | 50 | 0.25 | False | 60 | same lists | 0.0653 |
| blend_first_timer | 20/7/7/7/7 | 50 | 0.5 | True | 60 | same lists | 0.0723 |
| blend_first_timer | 20/7/7/7/7 | 50 | 0.5 | False | 60 | same lists | 0.0753 |
| blend_first_timer | 20/7/7/7/7 | 50 | 0.75 | True | 60 | same lists | 0.0760 |
| blend_first_timer | 20/7/7/7/7 | 50 | 0.75 | False | 60 | same lists | 0.0778 |
| blend_first_timer | 20/7/7/7/7 | 50 | 1.0 | True | 60 | same lists | 0.0766 |
| blend_first_timer | 20/7/7/7/7 | 50 | 1.0 | False | 60 | same lists | 0.0777 |
