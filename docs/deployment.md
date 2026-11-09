# Sign-in and deployment notes

The steps below add detail for someone configuring email/phone sign-in or deploying Pantree.
You do not need to understand the technical terms to try the local shop.

## Passwordless authentication

The storefront uses the SuperTokens Passwordless recipe and Session recipe for email-or-phone
OTP sign-up, login, refresh, and logout. Sign-up and sign-in are separate flows: sign-up
(`POST /signup/otp/request`) collects profile details, rejects an email or mobile that already
has an account, and creates the customer only after the code is verified; sign-in
(`POST /login/otp/request`) sends a code only to existing accounts and never creates one.
SQLite continues to own Pantree profiles, carts, and orders, while SuperTokens Core stores
authentication/session data in PostgreSQL.

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

### Production

The full stack is defined in [`docker-compose.production.yml`](../docker-compose.production.yml):
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

### Backups and restore

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

### Before opening the shop to the public

The local setup above is for trying Pantree on your own computer. Before putting it on the
internet, an operator needs to configure a real domain, HTTPS, durable storage, private
credentials, email/SMS delivery, and backups. Then test a real sign-in and the main shopping
actions on that public site. The Compose and backup instructions below describe those steps.
Passing local tests by itself does not mean that a public deployment or real SMS/email delivery
has been tested.

Back to the [README](../README.md).
