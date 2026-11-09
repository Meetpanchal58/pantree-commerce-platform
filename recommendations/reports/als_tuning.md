# ALS tuning report

Generated 2026-11-09T14:38:59+00:00 by `python -m recommendations.tune_als`.

Metric: how well each customer's top 20 ALS products predict what they buy in the next period.

## Validation grid

| Stage | Weights (view, cart, buy) | Factors | Reg | Recall@20 | NDCG@20 |
|---|---|---|---|---|---|
| weights | (1, 3, 5) | 32 | 0.1 | 0.0768 | 0.0473 |
| weights | (1, 1, 1) | 32 | 0.1 | 0.0706 | 0.0435 |
| weights | (1, 5, 10) | 32 | 0.1 | 0.0768 | 0.0466 |
| weights | (0.5, 3, 8) | 32 | 0.1 | 0.0762 | 0.0468 |
| weights | (0.2, 1, 10) | 32 | 0.1 | 0.0763 | 0.0459 |
| weights | (0.1, 0.5, 20) | 32 | 0.1 | 0.0753 | 0.0453 |
| weights | (0.0, 0.0, 1.0) | 32 | 0.1 | 0.0572 | 0.0370 |
| hyperparameters | (1, 3, 5) | 16 | 0.01 | 0.0758 | 0.0461 |
| hyperparameters | (1, 3, 5) | 16 | 0.1 | 0.0756 | 0.0460 |
| hyperparameters | (1, 3, 5) | 16 | 1.0 | 0.0759 | 0.0462 |
| hyperparameters | (1, 3, 5) | 32 | 0.01 | 0.0774 | 0.0472 |
| hyperparameters | (1, 3, 5) | 32 | 0.1 | 0.0768 | 0.0473 |
| hyperparameters | (1, 3, 5) | 32 | 1.0 | 0.0772 | 0.0471 |
| hyperparameters | (1, 3, 5) | 48 | 0.01 | 0.0726 | 0.0454 |
| hyperparameters | (1, 3, 5) | 48 | 0.1 | 0.0727 | 0.0454 |
| hyperparameters | (1, 3, 5) | 48 | 1.0 | 0.0727 | 0.0455 |
| hyperparameters | (1, 3, 5) | 64 | 0.01 | 0.0724 | 0.0453 |
| hyperparameters | (1, 3, 5) | 64 | 0.1 | 0.0725 | 0.0454 |
| hyperparameters | (1, 3, 5) | 64 | 1.0 | 0.0722 | 0.0453 |
| hyperparameters | (1, 3, 5) | 128 | 0.01 | 0.0687 | 0.0439 |
| hyperparameters | (1, 3, 5) | 128 | 0.1 | 0.0689 | 0.0439 |
| hyperparameters | (1, 3, 5) | 128 | 1.0 | 0.0693 | 0.0442 |

## Held-out test (checked once)

| Setting | Customers | Recall@20 | NDCG@20 |
|---|---|---|---|
| Serving before this run: 32 factors, reg 0.1, weights (1.0, 3.0, 5.0) | 4,170 | 0.0900 | 0.0560 |
| Tuned 32 factors, reg 0.1, weights (1, 3, 5) | 4,170 | 0.0900 | 0.0560 |
