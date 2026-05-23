# Production deployment checklist

The single source of truth for environment settings required before a TCKDB instance is exposed to anything beyond a developer's laptop. This page is meant to be quotable in code review and in deploy PRs: "did we tick every row below?"

The settings here apply to any **hosted / shared / public** deployment — lab-shared, self-hosted single-node, and the future hosted community instance. Local single-developer deployments may relax the defaults; everything else should not.

The reference template that already encodes every safe value is [`.env.selfhosted.example`](../../.env.selfhosted.example). Use that as your starting point for any hosted setup, not [`.env.example`](../../.env.example) (which targets local development).

---

## Required hosted settings

Every row below must be set to the indicated value before the API is reachable from outside the host loopback interface.

| Env var | Required value | Why |
|---|---|---|
| `AUTH_ALLOW_OPEN_REGISTRATION` | `false` | Defaults to `true` for local dev convenience. Leaving it `true` in a hosted deployment turns the API into an uncontrolled self-registration endpoint. Seed accounts via `backend/scripts/bootstrap_admin.py` + admin invites instead. |
| `EXPOSE_API_DOCS` | `false` | Defaults to `true`. When `false`, FastAPI never registers `/docs`, `/redoc`, or `/openapi.json`. Hosted deployments either keep docs off entirely or gate them behind a private network. |
| `LEGACY_READS_REQUIRE_AUTH` | `true` | The `/api/v1/{thermo,kinetics,geometries,...}` legacy routes pre-date the internal-IDs visibility policy and leak integer PKs. They must require auth in hosted deployments. |
| `ALLOW_PUBLIC_INTERNAL_IDS` | `false` | Hides internal integer primary keys from scientific responses. Clients use public refs. Set to `true` only in local/dev/test for compatibility with the legacy id-bearing shape. |
| `SESSION_COOKIE_SECURE` | `true` | Required so browsers only send the session cookie over TLS. Set `false` only when testing a local HTTP-only setup. |
| `SESSION_COOKIE_HTTPONLY` | `true` | Prevents JavaScript access to the session cookie. Default is already `true`; do not flip it. |
| `SESSION_COOKIE_SAMESITE` | `lax` | Default is already `lax`. Stricter (`strict`) is acceptable; never use `none` without `secure=true` and explicit CORS allow-listing. |
| `RATE_LIMIT_ENABLED` | `true` | The app-level rate limiter. The test suite turns it off via env var; nothing else should. |
| `CORS_ALLOW_ORIGINS` | explicit allow-list or unset | Default empty means no CORS middleware is registered, which is the correct posture for an API-key-only API. Set to a comma-separated list of trusted browser origins (e.g. `https://app.tckdb.example.org`) only if a browser app needs to call the API. Never use `*`. |
| `TRUSTED_PROXY_HEADER` | match your ingress, or unset | Tells the rate limiter where to read the real client IP. Only set when terminating TLS behind a trusted reverse proxy that overwrites the header. Examples: Cloudflare Tunnel → `CF-Connecting-IP`; nginx with `proxy_set_header X-Real-IP` → `X-Real-IP`. Leave unset for loopback-only / no-proxy setups. |
| `DB_STATEMENT_TIMEOUT_MS` | `30000` (or your chosen budget) | Caps the time any one query may hold a pool slot. Belt-and-braces; also set `ALTER ROLE tckdb SET statement_timeout = '30s'` at the role level. |
| `DB_PASSWORD` | strong random value | Never leave at `tckdb` outside local dev. The hosted compose file refuses to start without one. |
| `S3_ACCESS_KEY` / `S3_SECRET_KEY` | strong random values | Same: never leave at the local-dev defaults in a hosted setup. |
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER` | production DB coordinates | Point at the production PostgreSQL+RDKit instance. Verify the DB exists and the user has appropriate privileges before starting the API. |
| `TCKDB_API_HOST` | `127.0.0.1` | The API should listen on loopback only; the ingress (Cloudflare Tunnel, nginx, Caddy, Traefik, Tailscale, …) is the only thing that talks to it directly. |

### Note on signing secrets

TCKDB does **not** use a signed-cookie or JWT model — sessions are server-side rows in `user_session` keyed by a SHA-256 hash of the cookie token. There is no `SECRET_KEY` / `SESSION_SECRET` to configure. The relevant secrets are the DB password and the S3 credentials above.

If a future change introduces signed cookies or JWTs, add the corresponding secret to this table at the same time as the code change.

---

## Recommended hosted settings

Not strictly required, but strongly recommended for any internet-exposed deployment.

| Env var | Recommended | Why |
|---|---|---|
| `TRUSTED_PROXY_HEADER` | set to your ingress's real-IP header | Without this, all rate-limit keys collapse to the proxy's loopback IP and the per-IP budgets are useless. |
| `RATE_LIMIT_ANON_READ_PER_MINUTE` | 60 (default) | Tune up only after observing legitimate traffic. |
| `RATE_LIMIT_AUTH_READ_PER_MINUTE` | 300 (default) | |
| `RATE_LIMIT_AUTH_WRITE_PER_MINUTE` | 30 (default) | Tight on purpose: one misbehaving uploader should not exhaust a deployment. |
| `RATE_LIMIT_ANON_OTHER_PER_MINUTE` | 20 (default) | Smaller than `ANON_READ` so anonymous writes do not inherit the read budget. |
| `RATE_LIMIT_AUTH_LOGIN_PER_MINUTE` | 10 (default) | Credential-stuffing cap; IP-keyed. |
| `RATE_LIMIT_REGISTER_PER_HOUR` | 10 (default) | Account-spam cap; IP-keyed. Mitigation against botnet registration is upstream (Cloudflare / WAF). |
| `PUBLIC_MAX_LIMIT` | `200` (default) | Hard cap on page size. Lower it on memory-constrained hosts. |
| `MAX_GEOMETRY_ATOMS_PUBLIC` | `500` (default) | |
| `MAX_FULL_CALCULATIONS_PUBLIC` | `100` (default) | |
| `MIN_SUPPORTED_TCKDB_CLIENT_VERSION` | the version you tested against | Older clients are rejected on write paths with `426 Upgrade Required`. |
| `ENFORCE_TCKDB_CLIENT_VERSION_ON_WRITES` | `true` (default) | |

---

## Operational pre-flight

Settings alone are not enough. Before opening the deployment to traffic:

- [ ] `.env.selfhosted` (or your equivalent) is on the host, with every `change-me-*` placeholder replaced.
- [ ] The DB is reachable: `PGPASSWORD=$DB_PASSWORD psql -h $DB_HOST -U $DB_USER -d $DB_NAME -c '\conninfo'`.
- [ ] Migrations are at head: `alembic current` matches `alembic heads`. See [Deployed-DB migration playbook](../../backend/docs/deployment/migrations.md).
- [ ] At least one admin account has been seeded via `backend/scripts/bootstrap_admin.py`.
- [ ] `AUTH_ALLOW_OPEN_REGISTRATION=false` is verified by sending `POST /api/v1/auth/register` and confirming the response is `403` or equivalent.
- [ ] `EXPOSE_API_DOCS=false` is verified by `curl -fsS https://your-host/openapi.json` returning `404`.
- [ ] `LEGACY_READS_REQUIRE_AUTH=true` is verified by hitting `/api/v1/thermo` without credentials and confirming the response is `401`.
- [ ] `SESSION_COOKIE_SECURE=true` is verified by logging in and confirming the `Set-Cookie` response has `Secure` set.
- [ ] Rate limits trip under intentional flood: hit `/auth/login` 11 times in one minute from one IP, confirm the 11th returns `429` with a `Retry-After` header.
- [ ] PostgreSQL `statement_timeout` is set at the role level: `ALTER ROLE tckdb SET statement_timeout = '30s'`.
- [ ] `/api/v1/health` returns `200`.
- [ ] `/api/v1/readyz` returns `200` and a body of the shape `{"status":"ready","database":"ok","alembic_revision":"<rev>"}`. A non-`ready` response means the API is up but the DB / schema is not — do *not* route traffic in that state.
- [ ] `X-Request-ID` is present on every response (try `curl -i https://your-host/api/v1/health` and confirm the header). Clients may set their own `X-Request-ID` on requests for correlation; the server echoes it when safe.
- [ ] `LOG_FORMAT=json` is set so hosted logs are structured and include `request_id`, `level`, `logger`, `message`. (Local/dev defaults to human-readable text.)
- [ ] Reverse-proxy / tunnel terminates TLS at the edge; the API itself listens only on `127.0.0.1`.
- [ ] Backups are configured and verified by running a restore drill at least once (see [shared-private-deployment.md §Backup and restore basics](shared-private-deployment.md#backup-and-restore-basics)).

A deploy that ticks every row in both tables and the pre-flight checklist is safe to expose. A deploy that does not is not.

---

## What this checklist does not cover

Out of scope here; tracked separately:

- Email verification on registration (no implementation today).
- CAPTCHA on the registration / login surface (relies on upstream WAF / Cloudflare).
- API-key permission scopes (today keys inherit owner role).
- OAuth / SSO (not implemented).
- A formal `APP_ENV` / `HOSTED_PROFILE` setting that would let the app fail-fast on unsafe combinations at startup. Today the operator is the safety net; this checklist is the safety net's documentation. A future change may introduce such a flag and a `validate_hosted_security_settings()` guard — see the [deployment readiness audit](../../backend/docs/specs/backend_deployment_readiness_audit.md) for context.

---

## See also

- [`.env.selfhosted.example`](../../.env.selfhosted.example) — template that encodes every safe value above.
- [README](README.md) — deployment scenarios overview.
- [`shared-private-deployment.md`](shared-private-deployment.md) — lab/group operational guide.
- [`self_hosted_single_node.md`](self_hosted_single_node.md) — single-node operator guide.
- [Deployed-DB migration playbook](../../backend/docs/deployment/migrations.md) — `alembic upgrade head` on a real database.
- [Backend deployment readiness audit](../../backend/docs/specs/backend_deployment_readiness_audit.md) — the audit that produced this checklist.
