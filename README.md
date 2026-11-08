# Pantree — E-commerce Data, Storefront & Recommendations

Pantree is a sample online shop and its data. You can generate a practice shopping dataset,
start the shop on your computer, sign in with an email or phone code, and see how shopping
actions are saved and used to suggest products.

This guide is written for someone setting up Pantree for the first time. Follow the steps in
order. The lower sections contain more detailed setup notes and technical reference material.

## Start here: setup and use Pantree

### What you need

- Windows, macOS, or Linux; the commands below are for Windows PowerShell.
- Python 3.11 or newer and an internet connection to install Python packages.
- Docker Desktop running, to start the sign-in service.
- To receive a real sign-in code: an email sending account (SMTP), or an Android phone running
  OpenSMS on the same trusted network. You can set up one or both.

### 1. Install Pantree

Open PowerShell in the folder containing this README, then run:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

The recommendation packages include machine-learning libraries and can take a while to install.
You only need to install them once for this environment.

### 2. Create the practice dataset

From the same project folder, run:

```powershell
.\.venv\Scripts\python.exe generate_data.py --mode initialize
.\.venv\Scripts\python.exe validators\preflight_checks.py
```

The first command creates the shop catalog, customers, shopping activity, orders, stock history,
and other practice data in the `data` folder. The second checks that the generated files agree
with each other. Read [the data folder guide](data/README.md) for what is created.

**Important:** `initialize` replaces the previously generated Parquet files and simulator state
in `data`. Do not run it if you need to keep an existing generated dataset. To advance an existing
dataset without starting over, use:

```powershell
.\.venv\Scripts\python.exe generate_data.py --mode increment
```

### 3. Create the storefront database

Run this once, after generating the dataset:

```powershell
.\.venv\Scripts\python.exe webapp\seed_db.py
```

This loads products, customers, and starting stock into the live shop database at
`webapp\pantree_app.db`. **Seeding replaces that database.** If it already contains shopping
activity you want to keep, stop the app and back it up before running the seeder.

### 4. Set up email or phone-code sign-in

Copy the example settings file and open it:

```powershell
Copy-Item .env.example .env
notepad .env
```

In `.env`, provide values for the SuperTokens Core and its database:

- `SUPERTOKENS_DB_USER` and `SUPERTOKENS_DB_NAME` already have example values.
- Set a private, strong value for `SUPERTOKENS_DB_PASSWORD`.
- Set a private API key for `SUPERTOKENS_API_KEY`.

Use a different random value for each password/key. You can make one with:

```powershell
py -c "import secrets; print(secrets.token_hex(32))"
```

Copy the result into the matching field in `.env`; do not post it in chat or commit it to GitHub.

For a local test, configure at least one way to receive codes:

- **Email:** fill in `SMTP_HOST`, `SMTP_FROM_EMAIL`, `SMTP_USERNAME`, and `SMTP_PASSWORD`.
  Gmail needs an App Password; Resend is another supported SMTP option. Keep credentials private.
- **SMS:** set `OPENSMS_API_URL` to your Android phone's trusted LAN address ending in `/sms/`,
  and set `OPENSMS_API_KEY` to the key from OpenSMS. Do not expose the phone's endpoint to the
  public internet.

The supplied Docker production setup requires both email and SMS settings. On Render, you can
use email OTP alone: configure all four SMTP values above, and the sign-in page will offer email
only when OpenSMS is not configured. A phone's local Wi-Fi address is not reachable from Render;
do not expose the OpenSMS API or its key publicly. For production, also set
`PANTREE_RATE_LIMIT_SECRET` to a private random value of at least 32 characters. Do not put real
passwords or keys in GitHub. On a 512 MB instance such as Render's free tier, set
`PANTREE_HEAVY_RECOMMENDATIONS=0` so the app skips the collaborative-filtering and
sentence-transformer models, which otherwise exceed the memory limit; recommendations fall back to
lighter sources. `render.yaml` installs `requirements-render.txt`, which leaves out those
models' packages (including PyTorch) for faster builds.
See the detailed
[email and phone sign-in setup](#passwordless-authentication) if you need provider-specific help.

Make sure Docker Desktop is running. Start the sign-in service from the project folder:

```powershell
docker compose --env-file .env -f docker-compose.auth.yml up -d
```

Wait until the SuperTokens and PostgreSQL services are healthy. They store and verify codes and
sessions; the shop itself still stores profiles and shopping activity in SQLite. After a
successful code check, Pantree remembers the sign-in in your browser. A shopper can only use
their own cart and orders; there are no separate administrator accounts in this demo.

To check that the services have started, run:

```powershell
docker compose --env-file .env -f docker-compose.auth.yml ps
```

### 5. Start the shop

From the project folder, run:

```powershell
.\.venv\Scripts\python.exe -m uvicorn webapp.app:app --reload --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/login`. You can also launch from inside the `webapp` folder. In
`app:app`, the first `app` means “the file `app.py` here” and the second means “the FastAPI
application inside it”:

```powershell
Set-Location webapp
..\.venv\Scripts\python.exe -m uvicorn app:app --reload
```

Or activate `.venv` first and run `python -m uvicorn app:app --reload`.

### 6. Try the shop

1. New shoppers open **Create account**, fill in their details (name, email, optional mobile, city,
   optional gender, age range and "how did you hear about us", marketing choices, and terms) and
   verify the code sent to their email. The account is created only after the code is verified.
   Signing up again with the same email or mobile shows "An account with this email already exists".
2. Returning shoppers use **Sign in** with their email or mobile and the code they receive. Sign-in
   never creates an account; an unknown email shows "No account found. Create an account first".
3. Browse departments, search, open a product, add products to your cart or wishlist, and try a
   checkout.
4. Visit **Your Orders** to mark the demo order delivered or cancel it. After marking it
   delivered, you can also try a return or replacement.
5. Use **Sign out** when you are finished.

Checkout is a demonstration: it does not charge a real card. It creates a paid demo order in
**Placed** status. In **Your Orders**, mark it delivered to try a return or replacement, or cancel
it to see the payment marked refunded and the items returned to stock.

### Where shopping activity is saved

The shop database, `webapp\pantree_app.db`, is the main live database. The table below describes
what Pantree currently records. It records these meaningful shop actions, not every mouse movement
or time spent on a page.

| What the shopper does | What Pantree saves |
|---|---|
| Creates an account | Non-personal profile in `customers` (signup date, city/state/region, gender, age range, acquisition channel, marketing opt-ins, terms acceptance time); personal details in `customer_pii` (name, verified email, mobile); the sign-in link in `supertokens_customers`. Details wait in `pending_signups` until the code is verified (expires after 30 minutes). |
| Signs in successfully | The sign-in link in `supertokens_customers` (first time on a device identity), a local session in `sessions`, and an `app_open` event. SuperTokens also stores the sign-in identity and session. |
| Places an order | The delivery address is saved to `customer_pii` for the next checkout. |
| Opens the home page | A `page_view` event in `clickstream`. |
| Opens a category or browse page | A `category_view` event in `clickstream`. Filter and sort changes are not currently saved as separate events. |
| Searches | A `search` event and the search text in `clickstream`. |
| Opens a product | A `pdp_view` event with its product ID in `clickstream`. |
| Adds or removes a cart item, or changes its quantity | The current cart in `cart`, plus an `add_to_cart`, `remove_from_cart`, or `cart_quantity_update` event. |
| Adds or removes a wishlist item | The current choice in `wishlist`, plus a `wishlist_add` or `wishlist_remove` event. |
| Opens the cart or starts checkout | A `page_view` or `checkout_start` event in `clickstream`. |
| Completes the demo checkout | An `orders` row, one `transactions` row per product, lower stock in `inventory`, a `SALE` row in `inventory_movements`, and `payment_attempt`/`purchase` events in `clickstream`. The cart is then cleared. |
| Marks an order delivered | The order and its transactions are updated, and an `order_delivered` event is recorded in `clickstream`. |
| Cancels a placed order | The order is marked cancelled/refunded, transaction revenue is removed, stock is restored, a `CANCELLATION` movement is saved, and an `order_cancelled` event is recorded. |
| Requests a return | The transaction/order status changes, a row is added to `returns`, stock is returned to `inventory`, an `inventory_movements` row is added, and a `return_completed` event is recorded. |
| Requests a replacement | The original transaction status changes, stock movements are recorded, and a `replacement_completed` event is added. |

`clickstream` is Pantree's activity diary: one row per recorded event, with a customer, session,
time, action, and (when relevant) product or search text. The current cart and wishlist tables
store the shopper's latest selections; they are not a full history of every change.
To look at the records yourself, open `webapp\pantree_app.db` with a SQLite viewer and browse
the table named in the right-hand column above. Search phrases are saved in `clickstream`, so
avoid entering private information into the shop's search box.

Pantree also copies selected live changes—customers, events, orders, transactions, inventory,
returns, and stock movements—to `webapp\pantree_analytics.duckdb`. That copy is for reports and
analysis. SQLite remains the live shop's source of truth. The generated Parquet files in `data`
are the starting practice dataset; they are not rewritten every time someone uses the shop.

Recommendations are logged separately so they can be measured (see
[Measuring recommendations on the live site](#measuring-recommendations-on-the-live-site)):
every served product goes to `recommendation_impressions`, it is marked seen once at least half
of the card is on screen, and opening it adds a row to `recommendation_clicks`.

### How the three recommendation rows work

- **Recommended for you (home page):** looks at what the signed-in shopper viewed, added to the
  cart, wishlisted, or bought. One part finds available items of related product types and
  audiences; another uses broader patterns from the generated shopping history. If there is not
  enough activity to personalize yet, it shows popular products instead.
- **Frequently bought together (product page):** first looks for products purchased in the same
  orders as the current product. If there is not enough order evidence, it tries products viewed
  together, then known compatible pairings, then other well-rated products from the department.
- **Similar products (product page):** compares the product's name, description, and details with
  other catalog items. It keeps items of the same product type and a compatible audience; if
  there are too few close matches, it fills the row with well-rated items of that type.

All three rows leave out unavailable products, the product already being viewed, and products
the customer has already bought where applicable. Recommendation scores are sorting signals, not
the probability that someone will buy. The recommendation API includes the reason and source
for each result. The current cards mainly show product details; the product-page heading may
also change when a fallback source is used. The cart recommendation API exists, but the current
cart page does not display a recommendation row.

### A few words you may see

| Word | In everyday language |
|---|---|
| Dataset / warehouse | The collection of generated tables about customers, products, orders, and activity. |
| Parquet | A compact file format used for the generated tables in `data`. |
| SQLite | The local database that the live shop reads and updates. |
| DuckDB | A separate local database used to explore and analyze a copy of selected shop data. |
| OTP | A one-time sign-in code sent to your email or phone. |
| Cold start | A shopper or product with too little history to personalize recommendations yet. |
| Candidate | A product considered as a possible recommendation before filtering and sorting. |
| SMTP | The standard way an app connects to an email service to send messages. |
| SuperTokens | The sign-in service that creates/verifies codes and remembers sign-in sessions. |
| FastAPI | The Python web framework used to run the shop. You do not need to use it directly. |
| OLTP | A technical name for the database that handles everyday shop activity. |
| OLAP | A technical name for the separate copy used to study or report on activity. |
| ALS | A way to find products based on patterns in what shoppers view, add, or buy. |
| FAISS | A tool that quickly finds products with descriptions/details similar to another product. |

For a deeper explanation of the generator, simulator settings, and output files, see
[Data generation and refresh](#refreshing-data-over-time-incremental) and
[the data folder guide](data/README.md). For deployment on a public server, use the
[production setup](#production).

### [Live Demo](https://pantree-storefront.onrender.com/login)

Hosted on Render's free tier; the first request after inactivity may take 30-60 seconds.

---

## Optional: how the main pieces fit together

The picture below shows where the practice data and live shopping activity go. You can skip it
if you just want to run the shop.

### Architecture

```
                         ┌─────────────────────────────┐
   product_details.csv → │   Stateful Simulator         │  python generate_data.py
   (real fashion seed)   │   (initialize / increment)   │
                         └──────────────┬──────────────┘
                                        │ writes
                                        ▼
                         ┌─────────────────────────────┐
                         │   Warehouse (Parquet)        │  masters, orders, transactions,
                         │   dimensional + fact tables  │  clickstream, inventory, returns,
                         └──────┬───────────────┬───────┘  reviews, support, marketing, MMM,
                                │               │          monthly features, churn labels
                     seeds ▼    │               │  reads ▼
             ┌──────────────────┐               └────────────────────────────┐
             │  Storefront app  │  writes live                                │
             │  FastAPI + SQLite│ ───────────► SQLite (OLTP, source of truth) │
             │  (login→checkout)│              analytics_sync.py               │
             └──────────────────┘ ───────────► DuckDB (OLAP mirror) ──────────┘ → ML / LLM projects
```

- **SQLite** is the transactional store the website writes to (carts, orders, clickstream, inventory).
- **DuckDB** mirrors it for fast analytics and model training (`ATTACH` the SQLite file, or query the partitioned Parquet facts).

## Short launch command (after setup)

If you have already generated the data, seeded the shop database, configured sign-in, and started
Docker, launch the shop from the project folder with:

```powershell
.\.venv\Scripts\python.exe -m uvicorn webapp.app:app --reload --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/login`. For the complete first-time steps, use
[Start here: setup and use Pantree](#start-here-setup-and-use-pantree) above.

## Detailed sign-in and deployment notes

The steps below add detail for someone configuring email/phone sign-in or deploying Pantree.
You do not need to understand the technical terms to try the local shop.

### Passwordless authentication

The storefront uses the SuperTokens Passwordless recipe and Session recipe for email-or-phone
OTP sign-up, login, refresh, and logout. Sign-up and sign-in are separate flows: sign-up
(`POST /signup/otp/request`) collects profile details, rejects an email or mobile that already
has an account, and creates the customer only after the code is verified; sign-in
(`POST /login/otp/request`) sends a code only to existing accounts and never creates one.
SQLite continues to own Pantree profiles, carts, and orders, while SuperTokens Core stores
authentication/session data in PostgreSQL.

#### Customer data and personal information

Customer data is split into two SQLite tables joined by `customer_id`:

| Table | Holds | Copied to DuckDB analytics? |
|---|---|---|
| `customers` (customer dimension) | signup date, city/state/region, gender, age range, acquisition channel, Prime status, email/SMS opt-in, account status, terms acceptance time | Yes |
| `customer_pii` (personal data) | first and last name, verified email, mobile, saved delivery address | **No** |

Analysis and recommendation code only needs the dimension, so names, emails, phone numbers and
addresses never leave the live database. Older databases are migrated automatically on start-up:
personal columns are moved out of `customers` (and emails out of the legacy `auth` table) into
`customer_pii`. Telling a visitor that an email "already exists" or "has no account" reveals
whether it is registered; the OTP rate limits (applied before that check) throttle bulk probing.

#### Sign-in and access control

Email and phone number are alternative identifiers for the same passwordless flow. The login
page normalizes the identifier, asks SuperTokens Core to create a one-time code, and sends that
code through the configured provider (SMTP for email or OpenSMS for SMS). SuperTokens verifies
the code; OpenSMS is a delivery provider only and does not create or validate authentication
codes. The supplied Compose configurations set a five-minute lifetime and allow five verification
attempts per code. Resend requests create a new code and are throttled along with initial
requests.

After successful verification, Pantree either creates the new customer from the saved sign-up
details or, for sign-in, links the verified SuperTokens identity to the existing customer with
that verified email or phone (a code verified for an unknown contact creates nothing). The
app then creates a SuperTokens session and a local session-to-customer mapping. The browser
receives an HttpOnly `sid` cookie; it is Secure for HTTPS, uses SameSite=Lax, and has a seven-day
maximum age. Invalid or expired sessions are rejected, and an expired access session is refreshed
through SuperTokens when possible. Logout revokes the SuperTokens session and clears the local
cookie.

Protected storefront actions are available to a signed-in, mapped customer; data operations use
that customer's ID rather than an ID supplied by the browser. The recommendation API requires
this session. This is customer authentication and session authorization, not a role/permission
system: the app does not define separate shopper/admin roles. OTP send requests have a 30-second
cooldown; initial sends are limited to five per identifier and 20 per client IP in a 15-minute
window. Resends use the device ID as the per-identifier bucket and are also subject to the IP
limit. Unsafe requests with an Origin or Referer are checked against the configured app origin;
production also rejects unsafe requests without one. SuperTokens' own passwordless HTTP endpoints
for creating, resending, or consuming codes are disabled in favor of Pantree's throttled routes.

#### Local setup

1. Copy `.env.example` to `.env` and edit it locally; `.env` is ignored by Git:

   ```powershell
   Copy-Item .env.example .env
   notepad .env
   ```

2. Set `PUBLIC_APP_URL` to the exact browser origin. For Gmail SMTP, set
   `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=465`, and your full Gmail address in both
   `SMTP_FROM_EMAIL` and `SMTP_USERNAME`. Put the Google App Password in
   `SMTP_PASSWORD` (not your regular Gmail password). Alternatively, use Resend SMTP
   (host `smtp.resend.com`, port `465`, username `resend`, and the Resend API key as
   `SMTP_PASSWORD`). SMTP port `465` (or Resend's `2465`, for hosts such as Render's free tier that block 465) is enforced so OTP mail uses implicit TLS. With Resend, verify
   the sending domain and its DNS
   records in Resend, and use an authorized `SMTP_FROM_EMAIL`. Gmail requires 2-Step
   Verification; do not use your normal account password.
3. Configure OpenSMS on an Android phone connected to the same trusted LAN. Set
   `OPENSMS_API_URL` to the phone's LAN address ending in `/sms/`, and set the generated API key
   in `OPENSMS_API_KEY`. Do not expose the phone API publicly or forward its port through your
   router. Pantree uses a five-second request timeout. SuperTokens generates and verifies the OTP;
   OpenSMS only sends it. Mobile numbers may include `+` and an international calling code;
   unprefixed 10-digit numbers are interpreted as India.
4. Set `SUPERTOKENS_API_KEY`, `SUPERTOKENS_DB_PASSWORD`, and
   `PANTREE_RATE_LIMIT_SECRET` privately. Use at least 32 random characters for the rate-limit
   secret (for example, generate 64 hexadecimal characters with
   `python -c "import secrets; print(secrets.token_hex(32))"`). Keep secrets in `.env` locally
   and a secret manager in production. Never commit or share them.
5. Start the pinned SuperTokens Core and its persistent PostgreSQL database:

   ```powershell
   docker compose --env-file .env -f docker-compose.auth.yml up -d
   ```

6. Set `SUPERTOKENS_CONNECTION_URI=http://127.0.0.1:3567` for an app running on the host,
   then start Uvicorn from `webapp` as usual. If using a non-default port, set `PUBLIC_APP_URL`
   to that origin (for example `http://127.0.0.1:8013`).

#### Production

The full stack is defined in [`docker-compose.production.yml`](./docker-compose.production.yml):
FastAPI, Caddy HTTPS reverse proxy, SuperTokens Core, and PostgreSQL. The production FastAPI
image uses a pinned Python base, runs as a non-root user, trusts forwarded headers only from
Caddy's fixed private-network address, and exposes a readiness check that verifies the mounted
SQLite schema. Caddy obtains and renews TLS certificates automatically; DNS must point to the
server and inbound ports 80/443 must be available.

1. On the deployment host, set the DNS A/AAAA records for `PUBLIC_APP_HOST`, allow inbound
   TCP 80/443 (and UDP 443 for HTTP/3), and ensure no other service occupies those ports.
2. Copy `.env.example` to `.env`. Set the public host, durable host directories
   (`PANTREE_DB_DIR` containing `pantree_app.db`, and `PANTREE_DATA_DIR` containing the
   read-only parquet warehouse), SuperTokens/PostgreSQL credentials, rate-limit secret, SMTP
   credentials, and OpenSMS endpoint/key. Set `APP_ENV=production` and
   `PUBLIC_APP_URL=https://<PUBLIC_APP_HOST>` if running outside the supplied production
   Compose file. Store secrets in a managed secret store where available.

   Required `.env` values for the production Compose file:

   | Variable(s) | Purpose |
   |---|---|
   | `PUBLIC_APP_HOST` | Public DNS hostname for Caddy TLS. |
   | `PANTREE_DB_DIR`, `PANTREE_DATA_DIR` | Durable SQLite directory and read-only warehouse directory on the host. |
   | `SUPERTOKENS_DB_USER`, `SUPERTOKENS_DB_PASSWORD`, `SUPERTOKENS_DB_NAME`, `SUPERTOKENS_API_KEY` | Private SuperTokens PostgreSQL and Core credentials. |
   | `PANTREE_RATE_LIMIT_SECRET` | At least 32 random characters; HMAC key for request-limit buckets. |
   | `SMTP_HOST`, `SMTP_FROM_EMAIL`, `SMTP_USERNAME`, `SMTP_PASSWORD` | Real email provider settings. `SMTP_PORT` must be `465`, or `2465` with Resend; `SMTP_FROM_NAME` defaults to `Pantree`. |
   | `OPENSMS_API_URL`, `OPENSMS_API_KEY` | Private OpenSMS phone endpoint and Bearer API key for SMS delivery. |

   The production Compose file sets `APP_ENV=production`, `PUBLIC_APP_URL` from
   `PUBLIC_APP_HOST`, and `SUPERTOKENS_CONNECTION_URI` to the private Core service address.
3. Before first launch, create a consistent SQLite snapshot on the source machine and securely
   transfer it to `PANTREE_DB_DIR/pantree_app.db`. For example:

   ```sh
   python -c "import sqlite3; s=sqlite3.connect('webapp/pantree_app.db'); d=sqlite3.connect('pantree_app.backup.db'); s.backup(d); d.close(); s.close()"
   scp pantree_app.backup.db deploy@your-host:/srv/pantree/sqlite/pantree_app.db
   ```

   On the server, ensure UID 10001 can read/write the database and directory (for example,
   `sudo chown -R 10001:10001 /srv/pantree/sqlite`) and can read the warehouse directory. Copy
   the required parquet files into `PANTREE_DATA_DIR`. Do not initialize an empty storefront
   database over existing data.
4. Validate and start the stack:

   ```sh
   docker compose --env-file .env -f docker-compose.production.yml config --quiet
   docker compose --env-file .env -f docker-compose.production.yml up --build -d
   docker compose --env-file .env -f docker-compose.production.yml ps
   ```

5. Confirm all services report healthy, open `https://$PUBLIC_APP_HOST/healthz`, and complete
   the real email and SMS checks below. Keep Core and PostgreSQL private; only Caddy publishes
   ports. The storefront uses a single Uvicorn worker because SQLite is the OLTP source of
   truth. Do not scale multiple app replicas against separate SQLite files.

The Core, PostgreSQL, Caddy, and Python base images are pinned by immutable registry digests.
PostgreSQL and Caddy certificate state use named persistent volumes; the storefront SQLite
database and raw warehouse use explicit durable host directories. Compose health checks cover
PostgreSQL, Core, FastAPI readiness, and Caddy configuration. Review and intentionally update
image digests during maintenance rather than using floating tags. Direct Python packages in
`requirements.txt` are pinned to exact versions.

#### Backups and restore

Use an encrypted off-host backup destination and test restores periodically. SQLite's online
backup API safely captures the live database (including WAL state) into the same mounted host
directory:

```sh
docker compose --env-file .env -f docker-compose.production.yml exec -T pantree python -c "import sqlite3; source=sqlite3.connect('/var/lib/pantree/pantree_app.db'); target=sqlite3.connect('/var/lib/pantree/pantree_app.backup.db'); source.backup(target); target.close(); source.close()"
```

Copy `pantree_app.backup.db` off-host and encrypt it. To restore, stop the app, preserve the
current database separately, copy the verified backup to `PANTREE_DB_DIR/pantree_app.db`, set
owner/permissions for UID 10001, then start the app and confirm `/healthz`. Back up SuperTokens
PostgreSQL separately:

```sh
docker compose --env-file .env -f docker-compose.production.yml exec -T supertokens-postgres sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' > supertokens.sql
```

Protect and encrypt this dump as it contains authentication records. To test a restore, use a
separate recovery deployment with an empty database, stop its app and Core, then load the dump:

```sh
docker compose --env-file .env -f docker-compose.production.yml exec -T supertokens-postgres sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" "$POSTGRES_DB"' < supertokens.sql
```

Verify the recovery deployment before switching traffic. Do not load a full dump over a live,
initialized Core database. `docker compose down` preserves named volumes; **do not use
`docker compose down -v`** for routine redeployment.

SuperTokens generates six-digit OTPs that expire after five minutes and permit five
verification attempts. Both OTP endpoints have a 30-second cooldown; new-code requests use the
contact identifier as their request-limit bucket, while resends use the device ID. Each bucket
is limited to five requests per 15 minutes, and each client IP is limited to 20 requests per
15 minutes. Email/SMS channels fail closed when their provider settings are missing; codes are
not logged or returned to the browser. Account matching uses only SuperTokens-verified email or
phone; conflicting matches across Pantree customers are rejected.

#### Before opening the shop to the public

The local setup above is for trying Pantree on your own computer. Before putting it on the
internet, an operator needs to configure a real domain, HTTPS, durable storage, private
credentials, email/SMS delivery, and backups. Then test a real sign-in and the main shopping
actions on that public site. The Compose and backup instructions below describe those steps.
Passing local tests by itself does not mean that a public deployment or real SMS/email delivery
has been tested.

### Refreshing data over time (incremental)

This advances the generated practice data; it does not update the live shop database. The live
shop's seeder replaces its database, so do not run it on a database with activity you want to
keep. There is not currently a safe command that merges newly generated customers and products
into an already-used shop database.

```bash
.\.venv\Scripts\python.exe generate_data.py --mode increment
```

## Change the dataset size (optional)

The generator normally creates about 30,000 starting customers and 10,000 products. If you want
a smaller or larger practice dataset, set these values in PowerShell before running
`initialize`. Starting over replaces the generated files, so only do this when that is okay.

```powershell
$env:PANTREE_CUSTOMERS = "30000"
$env:PANTREE_PRODUCTS = "10000"
.\.venv\Scripts\python.exe generate_data.py --mode initialize
```
Change the two numbers to the starting customer and product counts you want. The default end
date is today, and the random seed is fixed by default so the same settings produce repeatable
practice data.

## What is in the generated practice data?

| Table or files | In everyday language |
|---|---|---|
| `customer_master`, `product_master`, `seller_master`, `agent_master` | Basic details about shoppers, products, sellers, and support staff. |
| `orders`, `transactions` | Orders and the products inside each order. |
| `clickstream` | The generated activity diary: visits, searches, product views, carts, and purchases. |
| `inventory` | How many units were available for each product over time. |
| `returns`, `product_reviews`, `support_contacts` | Example returns, customer reviews, and support requests. |
| `marketing_spend`, `mmm_weekly_target` | Example marketing costs and weekly sales totals. |
| `product_affinity` | Pairs of products often bought or viewed together. |
| `customer_monthly_features`, `customer_churn_labels` | Monthly customer summaries and whether a customer later stopped buying. |

`data/metrics.json` explains how the project calculates its main measurements.

## Product recommendations

The recommendation service reads the existing Parquet files without regenerating them. It is
available on the storefront home page and at `/api/recommendations/homepage`,
`/api/recommendations/product`, and `/api/recommendations/cart` (all require a signed-in session).
The homepage displays “Recommended for you”
and “Trending products”; product detail pages display “Frequently bought together” and “Similar
products”. Candidate retrieval, filtering, and optional ranking are performed by
`recommendations/serve.py`. The API returns product details plus `score`, `source`, and `reason`
so each result can be traced to its retrieval signal; the storefront also uses source metadata
to adjust the related-products heading.

### What each storefront row means

| Storefront row | Selection logic |
|---|---|
| **Recommended for you** (homepage) | Combines seeded implicit ALS candidates with products related to the customer's recent activity. Interaction strength is weighted (product view < cart addition < purchase); order activity is associated with browsing using a same-customer, same-day soft join. Recent signed-in views, cart additions, wishlists, and purchases from SQLite can immediately seed recommendations. The recent-activity source looks for in-stock candidates in the same product type and compatible audience, prioritizing recent category activity (or ratings/reviews when activity is absent), and may add discounted products related to those seeds. Already-purchased products are excluded. If there are no usable personalized candidates, the row falls back to recent popularity, then highly rated in-stock catalog products. The UI labels that fallback “Popular while we learn your taste.” Customers with no purchases yet see trending products first, because the evaluation showed popularity predicts first purchases best. |
| **Frequently bought together** (product page) | Tries evidence in order: products that appeared in the same realized orders as the anchor; warehouse co-view/affinity pairs; curated complementary product-type pairings; then other highly rated products from the same department. It moves to the next source if the earlier source has no eligible results. Candidates must be active, in stock, a different product type from the anchor, and audience-compatible; the anchor and already-purchased products are removed. Duplicate items across the two displayed product-page rows are removed. The heading changes when results come solely from co-view, curated, or department-fallback evidence. |
| **Similar products** (product page) | Searches a FAISS index of Sentence Transformer embeddings built from product name, description, and attributes. Results are filtered to the same product type and a compatible gender/audience (including universal categories), with inactive, out-of-stock, purchased, anchor, and duplicate-row products removed. If there are too few embedding matches, it fills from the same type/audience using rating, review count, and product ID as deterministic tie-breaks. |

These are retrieval rules, not a guarantee of purchase or user-specific preference. Source scores are
normalized retrieval scores and may be blended with the optional LightGBM ranker; they should be
used to compare/rank candidates for this service, not interpreted as purchase probabilities.
Trending is a separate homepage row: it aggregates recent warehouse product interactions (views,
cart additions, and wishlists) and realized order quantities over the latest 30 days in the
dataset, and falls back to highly rated in-stock catalog products when those counts are
unavailable. All rows filter against the current SQLite stock before returning results.

The recommendation API accepts the `homepage`, `product`, and `cart` surfaces. Product-page
requests use `product_id` and the `similar` or `complementary` intent; homepage requests use the
`personalized` or `trending` intent. The cart surface uses the customer's current cart as context.
The cart endpoint is available, but the current cart template does not render a recommendation
row.
For example, the storefront calls
`GET /api/recommendations/homepage?intent=personalized&limit=30` and
`GET /api/recommendations/product?product_id=<id>&intent=similar&limit=100`; requests without a
valid storefront session receive `401`. Each response item includes `product_id`, `name`, `score`,
`source`, and `reason`, along with the product and live cart/stock details needed to render a card.
The async route runs recommendation work in a thread pool. Expensive ALS and content-index setup
is warmed outside the request path; the content index is persisted under the ignored
`recommendations/.cache/` directory and rebuilt when the product catalog changes. Sentence
Transformers/FAISS, implicit ALS, and LightGBM are declared in `requirements.txt`; without
optional model dependencies, the corresponding source is unavailable and eligible fallback
sources keep recommendations usable.

For a clean, isolated install on Windows:

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m recommendations.evaluate
```

The recommendation dependencies in `requirements.txt` support seeded implicit ALS,
Sentence Transformers + FAISS, and a LightGBM ranker. The ranker is optional: until
`recommendations/ranker.txt` exists (written by the evaluation), the service sorts by fused
retrieval scores. Without the optional packages, those sources log a warning and
popularity/affinity/deals still work.

### How recommendations are evaluated

`python -m recommendations.evaluate` answers one question for every real order in the held-out
final period: **given everything known one hour before the order, does the system rank the
products the customer actually bought near the top?** That is the job of the home-page
"Recommended for you" row.

- **Three time periods, no peeking.** Retrieval sources (ALS, co-purchases, trending, recent
  activity) learn only from data before each period's cut-off, with a 7-day gap. The ranker is
  trained on candidate lists for orders in the middle period (B) and everything is scored on the
  final period (C).
- **Same lists, different orderings.** For each order a candidate list of up to 100 products is
  built (20 per source, then filled by score; already-bought products excluded). Random order,
  popularity, retrieval score (production without a ranker), the old 3-feature ranker, the new
  11-feature ranker, the production 50/50 blend and a segment-aware policy all rank the *same*
  lists, so differences come from ranking alone.
- **Two views.** *End to end* uses every test order (a bought product missing from the list counts
  as a miss). *Ranking only* uses orders whose list contains a bought product.
- **Segments and uncertainty.** Results are reported for first-time and repeat buyers, with a
  power label (under 100 orders = underpowered) and paired-bootstrap 95% confidence intervals.
- Reports are written to `recommendations/reports/evaluation_report.md` and `.json`.

> **Why the evaluation was rebuilt.** The earlier version used the first product a shopper viewed in
> a visit as the query and counted a candidate as correct only if bought later that day. The viewed
> product was usually the one bought, but it is excluded from the candidates, so only about 1 in
> 2,800 lists contained a correct answer and the metrics measured almost nothing.

### Results (default dataset: 33,060 customers, 10,000 products, 1 Jan 2025 – 30 Jun 2026, seed 42)

End-to-end NDCG@10 on 4,658 held-out orders (adequately powered):

| Ordering | All orders | First-time buyers (1,454) | Repeat buyers (3,204) |
|---|---|---|---|
| Random | 0.004 | 0.003 | 0.005 |
| Popularity | 0.015 | 0.022 | 0.011 |
| Retrieval score (previous production) | 0.018 | 0.010 | 0.022 |
| Old ranker (3 features) | 0.003 | 0.001 | 0.004 |
| **New ranker (11 features)** | **0.025** | 0.019 | **0.028** |
| **Production blend (50/50)** | **0.026** | 0.021 | **0.028** |
| **Segment-aware (served)** | **0.026** | **0.022** | **0.028** |

- The new ranker beats retrieval scores by +0.0072 NDCG@10 (95% CI +0.0047 to +0.0099), about
  +40% relative, and beats popularity and the old ranker; all intervals exclude zero.
- The old 3-feature ranker was worse than random: its features could not separate candidates.
- Popularity is the best signal for **first-time buyers**, so the home page now shows them trending
  products first (the segment-aware policy). Overall it ties the blend (+0.0002, CI −0.0009 to
  +0.0013) while matching popularity for first-time buyers.
- Within lists that contain a bought product, NDCG@10 rises from 0.171 (retrieval) to 0.239 (blend).
- **Retrieval is the bottleneck:** only 11.6% of test orders had a bought product in the candidate
  list at all. Collaborative filtering found one for 10.8% of orders, trending 7.9%, recent
  activity 4.3%, content similarity 3.4%, co-purchases 1.9%.
- What the ranker relies on most: product popularity, how many sources agreed on a product,
  the retrieval score, the source, and how many products the customer bought before.

ALS settings were tuned with `python -m recommendations.tune_als` (chosen on the validation period,
checked once on the test period): views weighted 0.5, cart adds 3 and purchases 8 (previously
1/3/5), 32 factors (previously 48). Held-out Recall@20 0.0669 → 0.0699, NDCG@20 0.0435 → 0.0453.
Full grid: `recommendations/reports/als_tuning.md`.

To reproduce: generate with `PANTREE_TODAY=2026-06-30`, then run `python -m recommendations.tune_als`
and `python -m recommendations.evaluate`. Absolute numbers are low because the task is predicting
the exact product out of 10,000; compare orderings with each other, not with published benchmarks.

### Measuring recommendations on the live site

The offline evaluation replays history; the live site also records how shoppers react:

| Table | One row per | Main columns |
|---|---|---|
| `recommendation_impressions` | product served in a recommendation row | request id, customer, surface/intent, A/B variant, product, position, source, score, served time, seen time |
| `recommendation_clicks` | recommended product opened | impression, request id, customer, product, position, click time |

- **Seen** means at least half of the card was on screen (`webapp/static/rec_tracking.js` uses an
  IntersectionObserver and batches the reports); served-but-never-visible cards don't count.
- **Clicks** come from recommendation links carrying `?rec=<request id>&pos=<position>`; links that
  don't match the customer and product are ignored, and each impression counts one click at most.
- **A/B test:** set `PANTREE_AB_TEST=ranker` to split customers 50/50 by a hash of their ID
  (stable per customer). `control` sees retrieval order without the ranker; `treatment` sees the ranker.
- **Report:** `python -m recommendations.online_metrics` writes
  `recommendations/reports/online_metrics.md` with served/seen/clicks, click-through rate with a
  95% Wilson interval, add-to-cart within 24 hours and purchase within 7 days of a click, per
  surface, source and variant, plus the A/B difference bootstrapped by customer (impressions from the
  same customer are not independent).

To extend an initialized warehouse and explicitly grow the customer population, set
`PANTREE_TODAY` to the desired end date and `PANTREE_INCREMENT_TARGET_CUSTOMERS` to the desired
final customer count before running `python generate_data.py --mode increment`. Increment
cohorts are distributed across the appended months; the target does not alter existing
customers or products.

### Recommendation code modules (for developers)

- `recommendations/candidates/collaborative.py` combines weighted views, carts, and realized
  purchases into a seeded implicit-ALS user-item model; unknown users return no ALS candidates.
- `recommendations/candidates/content.py` embeds product names, descriptions, and attributes,
  persists the FAISS index, and over-retrieves similar products for post-filtering.
- `recommendations/candidates/fbt.py` prioritizes same-order purchases, then co-view evidence,
  and finally curated compatible-category pairings.
- `recommendations/candidates/popularity.py` aggregates recent product views/orders for
  trending results and ranks markdowns for deal candidates.
- `recommendations/candidates/fusion.py` deduplicates source results while preserving the
  winning source tag and reason.
- `recommendations/ranker.py` builds the 11 point-in-time candidate features (shared by training
  and serving, so they cannot drift apart) and trains/scores the LightGBM LambdaRank model.
- `recommendations/serve.py` routes homepage, product, and cart requests, applies stock,
  category, gender, cart, and already-bought filters, then returns ranked explanations.
- `recommendations/evaluate.py` runs the three-period order-level evaluation described above,
  writes the reports and saves the serving ranker.
- `recommendations/tune_als.py` tunes ALS interaction weights and hyperparameters.

## Validation
- `validators/validate_dataset.py` — cross-table integrity, temporal order, funnel ordering,
  reconciliation, no-sale-before-launch, and churn-leakage checks.
- `validators/preflight_checks.py` — the above **plus** a churn model-readiness smoke test
  (base rate + leakage-safe AUC + top-decile lift), so labels can't silently drift to an
  unmodelable rate.

## Data analysis

`analysis/pantree_insights.ipynb` explores the generated warehouse with charts; rebuild it with
`pip install -r requirements-analysis.txt` then `python analysis/build_notebook.py`. Findings on the default dataset:

- **Conversion:** 2.7% of 815,666 sessions end in a purchase; only 9% of cart adds are bought.
- **Cash on delivery:** 6.6% of COD parcels come back undelivered (RTO) vs 2.7% for prepaid (2.4×).
- **Returns:** 16.7% of delivered items are returned (footwear 19.8%, accessories 12.4%);
  size and fit problems explain 39% of returns.
- **Channels:** a Referral customer is worth ₹3,661 on average vs ₹1,428 for Social Ads (2.6×).
- **Repeat buyers:** 53% of buyers order again and bring 80% of revenue.
- **Price change:** after 1 Feb 2025 customers paid 8.5% more relative to list price.
- **Data gap found:** the festival calendar in `generators/config.py` ends in June 2025, so later
  festive seasons (e.g. Diwali 2025) get no demand boost. Extending it is a next step.

The generated warehouse can also support RFM, customer lifetime value, churn (labels included)
and marketing-mix projects.

## Honest notes
- This is **realistic synthetic data** with deliberately engineered, documented signal — it is
  validated and leakage-checked, but it is simulated, not production data.
- Browsing in the simulator follows each customer's hidden taste with 20% exploration, so views
  and cart adds carry preference signal; the strength of that signal is a modelling choice.
- The headline evaluation is an offline replay on historical orders. The site now logs
  recommendation impressions and clicks and supports an A/B split, but those numbers need real
  traffic, which the demo does not have yet.
- Product images source from a real fashion catalogue (`generators/product_details.csv`);
  for the live site, images are served locally so they always load.

## License
MIT — see [LICENSE](LICENSE).
