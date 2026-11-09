# Pantree shop guide

This folder contains the part of Pantree that shoppers use: sign-in, product browsing, the
cart, checkout, and orders. For the complete beginner's guide—including setup, email or phone
sign-in, saved shopping activity, and recommendations—start with the
[main project guide](../docs/running.md#how-to-run).

## Run the shop

Follow the main guide first to install the packages, create the practice dataset and shop
database, configure sign-in, and start Docker. Then, from the project folder, run:

```powershell
.\.venv\Scripts\python.exe -m uvicorn webapp.app:app --reload --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/login`.

If you are already in this `webapp` folder, run:

```powershell
..\.venv\Scripts\python.exe -m uvicorn app:app --reload
```

## What the shop saves

The shop database, `pantree_app.db`, records sign-in profiles and shopping activity. For
example, opening a product is recorded in `clickstream`; adding a product updates the current
`cart`; and checkout records an order and product lines, reduces stock, and saves the related
activity. Orders start as **Placed**. From **Your Orders**, use **Mark Delivered** or **Cancel
Order**. Delivery allows a return or replacement; cancellation restores the units to stock.

The shop copies selected records to `pantree_analytics.duckdb` for reports. SQLite remains the
live shop database. See the main guide's
[shopping activity table](../docs/data.md#where-shopping-activity-is-saved) for details.

## Recommendations

The home page has **Recommended for you** and **Trending products**. Product pages have
**Frequently bought together** and **Similar products**. The main guide explains these rows and
their fallbacks in plain language:
[How the three recommendation rows work](../docs/recommender.md#how-the-three-recommendation-rows-work).

## Demo checkout and deployment

Checkout does not charge a real payment method. It creates a demo order so you can try delivery,
cancellation, returns, and replacements. For the hosted demo and public deployment notes, see
the main guide's [production setup](../docs/deployment.md#production).
