# Public client access (HTTPS + shared login)

HistoryIT serves the operator console behind Nginx with cookie sessions and
browser-trusted HTTPS using a **stable Elastic IP** and Let's Encrypt IP
certificates (Certbot 5.4+, `shortlived` profile, webroot validation).

## New environment variables

| Name | Purpose |
|------|---------|
| `APP_LOGIN_USERNAME` | Shared demo username |
| `APP_LOGIN_PASSWORD_HASH` | Argon2 hash from `server/scripts/hash_password.py` |
| `ALLOWED_ORIGINS` | Comma-separated origins for cookie CSRF checks, e.g. `https://203.0.113.10` |
| `SESSION_TTL_SECONDS` | Session lifetime (default `43200`) |
| `COOKIE_SECURE` | `true` / `false` / `auto` (default `true` in Compose) |
| `COOKIE_SAMESITE` | Cookie SameSite (`lax` recommended) |
| `LOGIN_MAX_FAILURES` | Failed attempts before lockout |
| `LOGIN_WINDOW_SECONDS` | Failure window |
| `LOGIN_LOCKOUT_SECONDS` | Lockout duration |
| `API_TOKEN` | Optional **server-only** token for trusted clients (`X-API-Token`). Empty disables token auth; it never opens the API. |
| `PUBLIC_IPV4` | Elastic IP used for certificate identity (host/env for scripts; not hard-coded in repo) |
| `CERTBOT_EMAIL` | Let's Encrypt registration email (scripts) |

Removed from the frontend: `VITE_API_TOKEN`. Production builds must not bake API secrets into the JS bundle.

### Rotate the previously browser-exposed API token

If an older deployment baked `VITE_API_TOKEN` into the static assets:

1. Generate a new `API_TOKEN` (or leave it empty if unused).
2. Remove `VITE_API_TOKEN` from `.env` / `.env.ec2`.
3. Rebuild and redeploy the `web` image so old bundles are gone.
4. Treat the old token as compromised.

## Password hash setup

On a trusted machine (repo `server/` with dependencies installed):

```bash
cd server
python scripts/hash_password.py
```

Paste the printed hash into `APP_LOGIN_PASSWORD_HASH`. Set `APP_LOGIN_USERNAME` to the shared demo username. Missing or invalid login configuration fails closed (login returns 503; APIs stay unauthorized).

## Elastic IP (before certificate issuance)

1. In AWS, allocate an **Elastic IP** and associate it with the EC2 instance.
2. Prefer this address over the temporary public IP that changes on stop/start.
3. Open security group **TCP 80** and **TCP 443** to clients that should reach the app (and to the public internet only when you intend public demo access).
4. Export the address for scripts — do not commit it:

```bash
export PUBLIC_IPV4='YOUR_ELASTIC_IPV4'
export CERTBOT_EMAIL='you@example.com'
```

5. Set `ALLOWED_ORIGINS=https://YOUR_ELASTIC_IPV4` in `.env`.

## Safe migration from the running HTTP stack

1. **Stop serving the app on plain HTTP** while keeping the host up:

```bash
cd /path/to/history-it
docker compose -f docker-compose.prod.yml -f docker-compose.bootstrap.yml up -d --no-deps web
```

   Bootstrap Nginx serves only `/.well-known/acme-challenge/` and `/nginx-health`. It does **not** serve the SPA or proxy `/api`, so login credentials are never posted over HTTP during issuance.

2. **Issue the certificate** (Certbot ≥ 5.4, short-lived IP profile):

```bash
chmod +x deploy/scripts/*.sh
./deploy/scripts/bootstrap-cert.sh
```

3. **Bring up the full HTTPS stack** (single API worker, port 8000 unpublished):

```bash
docker compose -f docker-compose.prod.yml --env-file .env up -d --build
```

   Keep renewals on cron via `deploy/scripts/renew-certs.sh` (preferred for Nginx reload).

4. Confirm:

```bash
curl -fsS "https://${PUBLIC_IPV4}/health"
curl -fsS -o /dev/null -w "%{http_code}\n" "https://${PUBLIC_IPV4}/docs"   # expect 404
```

5. Schedule renewals (short-lived certs renew often). Example crontab:

```cron
0 */12 * * * cd /path/to/history-it && ./deploy/scripts/renew-certs.sh >> /var/log/historyit-cert-renew.log 2>&1
```

`renew-certs.sh` runs `certbot renew` and reloads Nginx on success.

## Auth behavior

- Browser: username/password → opaque HttpOnly session cookie + CSRF cookie; mutations send `X-CSRF-Token` and must come from `ALLOWED_ORIGINS`.
- Refresh keeps a valid session; expiry or logout returns the UI to the login page.
- Logout deletes the server-side session.
- All operational `/api/*` routes require a valid session or a configured matching `API_TOKEN`.
- In-memory sessions are thread-safe for the single Uvicorn worker; process restart requires login again.
