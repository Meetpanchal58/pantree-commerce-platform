# ALS tuning report

Generated 2026-11-13T22:07:01+00:00 by `python -m recommendations.tune_als`.

Metric: how well each customer's top 20 ALS products predict what they buy in the next period.

## Validation grid

| Stage | Weights (view, cart, buy) | Factors | Reg | Recall@20 | NDCG@20 |
|---|---|---|---|---|---|
| weights | (1, 3, 5) | 32 | 0.1 | 0.2468 | 0.1234 |
| weights | (1, 1, 1) | 32 | 0.1 | 0.2446 | 0.1216 |
| weights | (1, 5, 10) | 32 | 0.1 | 0.2477 | 0.1239 |
| weights | (0.5, 3, 8) | 32 | 0.1 | 0.2479 | 0.1244 |
| weights | (0.2, 1, 10) | 32 | 0.1 | 0.2427 | 0.1182 |
| weights | (0.1, 0.5, 20) | 32 | 0.1 | 0.2264 | 0.1108 |
| weights | (0.0, 0.0, 1.0) | 32 | 0.1 | 0.2013 | 0.1043 |
| hyperparameters | (0.5, 3, 8) | 16 | 0.01 | 0.2563 | 0.1260 |
| hyperparameters | (0.5, 3, 8) | 16 | 0.1 | 0.2541 | 0.1258 |
| hyperparameters | (0.5, 3, 8) | 16 | 1.0 | 0.2574 | 0.1268 |
| hyperparameters | (0.5, 3, 8) | 32 | 0.01 | 0.2506 | 0.1244 |
| hyperparameters | (0.5, 3, 8) | 32 | 0.1 | 0.2479 | 0.1244 |
| hyperparameters | (0.5, 3, 8) | 32 | 1.0 | 0.2476 | 0.1239 |
| hyperparameters | (0.5, 3, 8) | 48 | 0.01 | 0.2399 | 0.1182 |
| hyperparameters | (0.5, 3, 8) | 48 | 0.1 | 0.2429 | 0.1190 |
| hyperparameters | (0.5, 3, 8) | 48 | 1.0 | 0.2386 | 0.1182 |
| hyperparameters | (0.5, 3, 8) | 64 | 0.01 | 0.2328 | 0.1161 |
| hyperparameters | (0.5, 3, 8) | 64 | 0.1 | 0.2336 | 0.1168 |
| hyperparameters | (0.5, 3, 8) | 64 | 1.0 | 0.2337 | 0.1169 |
| hyperparameters | (0.5, 3, 8) | 128 | 0.01 | 0.2147 | 0.1065 |
| hyperparameters | (0.5, 3, 8) | 128 | 0.1 | 0.2162 | 0.1069 |
| hyperparameters | (0.5, 3, 8) | 128 | 1.0 | 0.2163 | 0.1068 |

## Held-out test (checked once)

| Setting | Customers | Recall@20 | NDCG@20 |
|---|---|---|---|
| Serving before this run: 32 factors, reg 0.1, weights (1.0, 3.0, 5.0) | 4,710 | 0.2511 | 0.1236 |
| Tuned 16 factors, reg 1.0, weights (0.5, 3, 8) | 4,710 | 0.2593 | 0.1300 |
