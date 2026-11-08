# ALS tuning report

Generated 2026-11-08T19:16:02+00:00 by `python -m recommendations.tune_als`.

Metric: how well each customer's top 20 ALS products predict what they buy in the next period.

## Validation grid

| Stage | Weights (view, cart, buy) | Factors | Reg | Recall@20 | NDCG@20 |
|---|---|---|---|---|---|
| weights | (1, 3, 5) | 48 | 0.1 | 0.0633 | 0.0415 |
| weights | (1, 1, 1) | 48 | 0.1 | 0.0601 | 0.0391 |
| weights | (1, 5, 10) | 48 | 0.1 | 0.0639 | 0.0415 |
| weights | (0.5, 3, 8) | 48 | 0.1 | 0.0651 | 0.0417 |
| weights | (0.2, 1, 10) | 48 | 0.1 | 0.0658 | 0.0416 |
| weights | (0.1, 0.5, 20) | 48 | 0.1 | 0.0657 | 0.0407 |
| weights | (0.0, 0.0, 1.0) | 48 | 0.1 | 0.0664 | 0.0410 |
| hyperparameters | (0.5, 3, 8) | 16 | 0.01 | 0.0651 | 0.0408 |
| hyperparameters | (0.5, 3, 8) | 16 | 0.1 | 0.0647 | 0.0408 |
| hyperparameters | (0.5, 3, 8) | 16 | 1.0 | 0.0648 | 0.0410 |
| hyperparameters | (0.5, 3, 8) | 32 | 0.01 | 0.0664 | 0.0419 |
| hyperparameters | (0.5, 3, 8) | 32 | 0.1 | 0.0661 | 0.0419 |
| hyperparameters | (0.5, 3, 8) | 32 | 1.0 | 0.0659 | 0.0418 |
| hyperparameters | (0.5, 3, 8) | 48 | 0.01 | 0.0649 | 0.0418 |
| hyperparameters | (0.5, 3, 8) | 48 | 0.1 | 0.0651 | 0.0417 |
| hyperparameters | (0.5, 3, 8) | 48 | 1.0 | 0.0647 | 0.0417 |
| hyperparameters | (0.5, 3, 8) | 64 | 0.01 | 0.0668 | 0.0416 |
| hyperparameters | (0.5, 3, 8) | 64 | 0.1 | 0.0666 | 0.0415 |
| hyperparameters | (0.5, 3, 8) | 64 | 1.0 | 0.0669 | 0.0416 |
| hyperparameters | (0.5, 3, 8) | 128 | 0.01 | 0.0625 | 0.0382 |
| hyperparameters | (0.5, 3, 8) | 128 | 0.1 | 0.0611 | 0.0377 |
| hyperparameters | (0.5, 3, 8) | 128 | 1.0 | 0.0621 | 0.0382 |

## Held-out test (checked once)

| Setting | Customers | Recall@20 | NDCG@20 |
|---|---|---|---|
| Default 48 factors, reg 0.1, weights (1, 3, 5) | 3,558 | 0.0669 | 0.0435 |
| Tuned 32 factors, reg 0.01, weights (0.5, 3, 8) | 3,558 | 0.0699 | 0.0453 |
