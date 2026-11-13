# Setup

Install, get the data, set up sign-in, start the shop and host it (Render + Neon).

## How to run

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

### 2. Get the shop's data

From the same project folder, run:

```powershell
.\.venv\Scripts\python.exe prepare_data.py
```

This downloads the shop's history (catalogue, customers, browsing, orders, stock and more, from
1 January 2025 to yesterday) from the [ecommerce-simulator](https://github.com/Meetpanchal58/ecommerce-simulator)
package, which refreshes it every night, and converts it for Pantree. It takes about a minute.
Use `--size small` for a lighter shop on a laptop (`small`, `medium`, `full` (default) or
`large`). Run the same command again later to get the latest days. See [data.md](data.md).

### 3. Create the storefront database

```powershell
.\.venv\Scripts\python.exe webapp\seed_db.py
```

This loads products, customers and starting stock into the live shop database. Locally that is
the SQLite file `webapp\pantree_app.db`, which **seeding replaces**. With `DATABASE_URL` set to a
Postgres database (as on the hosted site), it seeds only an empty database and keeps existing
activity; `--reset` starts over.

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

On Render you can use email OTP alone: configure all four SMTP values above, and the sign-in page will offer email
only when OpenSMS is not configured. A phone's local Wi-Fi address is not reachable from Render;
do not expose the OpenSMS API or its key publicly. For production, also set
`PANTREE_RATE_LIMIT_SECRET` to a private random value of at least 32 characters. Do not put real
passwords or keys in GitHub. On a 512 MB instance such as Render's free tier, set
`PANTREE_HEAVY_RECOMMENDATIONS=0` so the app skips the collaborative-filtering and
sentence-transformer models, which otherwise exceed the memory limit; recommendations fall back to
lighter sources. `render.yaml` installs `requirements-render.txt`, which leaves out those
models' packages (including PyTorch) for faster builds.
See the detailed
[email and phone sign-in details](#passwordless-authentication) if you need provider-specific help.

Make sure Docker Desktop is running. Start the sign-in service from the project folder:

```powershell
docker compose --env-file .env -f docker-compose.auth.yml up -d
```

Wait until the SuperTokens and PostgreSQL services are healthy. They store and verify codes and
sessions; the shop itself stores profiles and shopping activity in its own live database. After a
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

## Short launch command (after setup)

If you have already generated the data, seeded the shop database, configured sign-in, and started
Docker, launch the shop from the project folder with:

```powershell
.\.venv\Scripts\python.exe -m uvicorn webapp.app:app --reload --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/login`. For the complete first-time steps, use
[Start here: setup and use Pantree](#how-to-run) above.

### Getting newer data

`python prepare_data.py` always downloads the latest published days. It refreshes the files the
recommender and the notebook read; it does not touch the live shop database.

## Passwordless authentication

The storefront uses the SuperTokens Passwordless recipe and Session recipe for email-or-phone
OTP sign-up, login, refresh, and logout. Sign-up and sign-in are separate flows: sign-up
(`POST /signup/otp/request`) collects profile details, rejects an email or mobile that already
has an account, and creates the customer only after the code is verified; sign-in
(`POST /login/otp/request`) sends a code only to existing accounts and never creates one.
Pantree's own live database (profiles, carts, orders, activity) is Postgres when
`DATABASE_URL` is set and SQLite otherwise (see "Live database" below), while SuperTokens Core
stores authentication/session data in its own PostgreSQL.

### Sign-in and access control

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

### Local setup

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

## Hosting: Render + Neon Postgres

On Render's free plan the disk is wiped on every redeploy, restart and sleep, so a SQLite file
loses all activity. Set `DATABASE_URL` to a hosted Postgres database and everything people do
on the site (accounts, carts, orders, clicks, impressions) persists.

1. Create a free project at neon.tech (region close to the Render service, e.g. Singapore),
   click **Connect**, keep **Connection pooling** on and copy the connection string.
2. In Render → the web service → **Environment**, add `DATABASE_URL` with that string.
3. Deploy. The build downloads the small shop (`python prepare_data.py --size small --recent-months 6`,
   which keeps six months of browsing for serving so the app fits the free plan's 512 MB) and runs
   `python webapp/seed_db.py`. Seeding fills an empty database; on later deploys of the same shop it
   keeps all live activity and only adds products, customers and stock rows that are new in the
   nightly-refreshed data. If the downloaded shop is a different one (another size or seed), it
   starts over, since old orders point at the old products. `python webapp/seed_db.py --reset`
   deletes everything and seeds again. Seeded tables use about 5 MB of Neon's 1 GB.

Without `DATABASE_URL` the app keeps using SQLite (local development and most tests).
`webapp/database.py` lets the same SQL run on both: it converts `?` placeholders, makes LIKE
case-insensitive as in SQLite, and pools connections (Neon's pooler does not support prepared
statements, so they are turned off). The Neon free plan has 1 GB of storage; a demo site uses
a small fraction of it.

### Reset my recommendations

The account page has a **Reset my recommendations** button. It deletes the customer's
clickstream, recommendation impressions and clicks, cart, wishlist, orders and returns, and
their other login sessions; it keeps the account, the sign-in link and the current login.
Units of open (not yet delivered) orders go back into stock with an `ACCOUNT_RESET` inventory
movement. `customers.recommendations_reset_at` is set, so serving ignores anything stored about
the customer before the reset and personalises again from new activity.

### Before opening the shop to the public

Set a real `PUBLIC_APP_URL` (HTTPS), private keys (`SUPERTOKENS_API_KEY`, `PANTREE_RATE_LIMIT_SECRET`),
working email (and optionally SMS) delivery and `DATABASE_URL`, then test a real sign-in and the main
shopping actions on the public site. Neon can restore the database to an earlier point within its
history window (see Neon's plan limits). Passing local tests does not by itself prove that public
email/SMS delivery works.

Back to the [README](../README.md).
