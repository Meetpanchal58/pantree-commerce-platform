# Running Pantree

Install, get the data, set up sign-in and start the shop. Hosting: [deployment.md](deployment.md).

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
[email and phone sign-in setup](deployment.md#passwordless-authentication) if you need provider-specific help.

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

Sign-in internals (OTP flow, rate limits, sessions), production Docker Compose, backups and the
pre-launch checklist are in [deployment.md](deployment.md).
