# The `data` folder

Pantree's data is not stored in GitHub. Get it with:

```bash
python prepare_data.py            # full shop (or --size small / medium / large)
```

| Path | What it holds |
|---|---|
| `source/` | The [ecommerce-simulator](https://github.com/Meetpanchal58/ecommerce-simulator) tables as published (sales, customers, clickstream, impressions, inventory history, marketing, support, `derived/`, `_ground_truth/`). The analysis notebook reads these. |
| `*.parquet` | The tables the recommender and the web app read, converted from `source/`: `product_master`, `customer_master`, `transactions`, `orders`, `clickstream` (product interactions), `inventory` (daily stock per product). |

`_ground_truth/` holds each simulated customer's true taste. Only the evaluation's oracle may read
it (a test enforces this); see [docs/data.md](../docs/data.md).
