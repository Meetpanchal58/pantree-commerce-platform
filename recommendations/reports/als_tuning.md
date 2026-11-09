# ALS tuning report

Generated 2026-11-08T20:49:29+00:00 by `python -m recommendations.tune_als`.

Metric: how well each customer's top 20 ALS products predict what they buy in the next period.

## Validation grid

| Stage | Weights (view, cart, buy) | Factors | Reg | Recall@20 | NDCG@20 |
|---|---|---|---|---|---|
| weights | (1, 3, 5) | 48 | 0.1 | 0.0787 | 0.0488 |
| weights | (1, 1, 1) | 48 | 0.1 | 0.0727 | 0.0457 |
| weights | (1, 5, 10) | 48 | 0.1 | 0.0774 | 0.0476 |
| weights | (0.5, 3, 8) | 48 | 0.1 | 0.0788 | 0.0485 |
| weights | (0.2, 1, 10) | 48 | 0.1 | 0.0796 | 0.0486 |
| weights | (0.1, 0.5, 20) | 48 | 0.1 | 0.0774 | 0.0470 |
| weights | (0.0, 0.0, 1.0) | 48 | 0.1 | 0.0687 | 0.0436 |
| hyperparameters | (1, 3, 5) | 16 | 0.01 | 0.0798 | 0.0484 |
| hyperparameters | (1, 3, 5) | 16 | 0.1 | 0.0799 | 0.0484 |
| hyperparameters | (1, 3, 5) | 16 | 1.0 | 0.0802 | 0.0486 |
| hyperparameters | (1, 3, 5) | 32 | 0.01 | 0.0805 | 0.0497 |
| hyperparameters | (1, 3, 5) | 32 | 0.1 | 0.0806 | 0.0495 |
| hyperparameters | (1, 3, 5) | 32 | 1.0 | 0.0804 | 0.0497 |
| hyperparameters | (1, 3, 5) | 48 | 0.01 | 0.0788 | 0.0486 |
| hyperparameters | (1, 3, 5) | 48 | 0.1 | 0.0787 | 0.0488 |
| hyperparameters | (1, 3, 5) | 48 | 1.0 | 0.0778 | 0.0485 |
| hyperparameters | (1, 3, 5) | 64 | 0.01 | 0.0769 | 0.0480 |
| hyperparameters | (1, 3, 5) | 64 | 0.1 | 0.0769 | 0.0482 |
| hyperparameters | (1, 3, 5) | 64 | 1.0 | 0.0771 | 0.0480 |
| hyperparameters | (1, 3, 5) | 128 | 0.01 | 0.0742 | 0.0459 |
| hyperparameters | (1, 3, 5) | 128 | 0.1 | 0.0748 | 0.0458 |
| hyperparameters | (1, 3, 5) | 128 | 1.0 | 0.0750 | 0.0462 |

## Held-out test (checked once)

| Setting | Customers | Recall@20 | NDCG@20 |
|---|---|---|---|
| Default 48 factors, reg 0.1, weights (1, 3, 5) | 4,084 | 0.0959 | 0.0593 |
| Tuned 32 factors, reg 1.0, weights (1, 3, 5) | 4,084 | 0.0975 | 0.0604 |
