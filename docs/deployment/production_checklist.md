# Production deployment checklist

The single source of truth for environment settings required before a TCKDB instance is exposed to anything beyond a developer's laptop. This page is meant to be quotable in code review and in deploy PRs: "did we tick every row below?"

The settings here apply to any **hosted / shared / public** deployment — lab-shared, self-hosted single-node, and the future hosted community instance. Local single-developer deployments may relax the defaults; everything else should not.

The reference template that already encodes every safe value is [`.env.selfhosted.example`](../../.env.selfhosted.example). Use that as your starting point for any hosted setup, not [`.env.example`](../../.env.example) (which targets local development).

---

## Deployment mode and startup guard

Set `DEPLOYMENT_MODE` to one of:

| Value | Use |
|---|---|
| `local` | Single-developer laptop / CI. Developer-friendly defaults (open registration, exposed docs, plaintext cookies) are allowed. Default if unset. |
| `shared_private` | Lab/internal deployment behind a private network. All Required hosted settings below are enforced at startup. |
| `hosted_public` | Public, internet-facing deployment. Same enforced settings as `shared_private`, plus `CORS_ALLOW_ORIGINS` may not contain `*`. |

In `shared_private` and `hosted_public`, the API refuses to boot if any required setting is at an unsafe value. The error message lists every violation in one pass, so a misconfigured deploy fails fast instead of running with quiet production gaps. Implementation: [`backend/app/api/startup_checks.py`](../../backend/app/api/startup_checks.py).

Examples:

```env
# Local development
DEPLOYMENT_MODE=local

# Shared private (lab/internal) deployment
DEPLOYMENT_MODE=shared_private

# Public deployment
DEPLOYMENT_MODE=hosted_public
```

---

## Required hosted settings

Every row below must be set to the indicated value before the API is reachable from outside the host loopback interface. In `DEPLOYMENT_MODE=shared_private` or `hosted_public`, the startup guard refuses to boot when any of the rows tagged "**enforced**" below is at an unsafe value.

| Env var | Required value | Why |
|---|---|---|
| `DEPLOYMENT_MODE` | `shared_private` or `hosted_public` | **enforced** Selects the hosted posture and turns on the startup safety guard. See [Deployment mode and startup guard](#deployment-mode-and-startup-guard). |
| `AUTH_ALLOW_OPEN_REGISTRATION` | `false` | **enforced** Defaults to `true` for local dev convenience. Leaving it `true` in a hosted deployment turns the API into an uncontrolled self-registration endpoint. Seed accounts via `backend/scripts/bootstrap_admin.py` + admin invites instead. |
| `EXPOSE_API_DOCS` | `false` | **enforced** Defaults to `true`. When `false`, FastAPI never registers `/docs`, `/redoc`, or `/openapi.json`. Hosted deployments either keep docs off entirely or gate them behind a private network. |
| `LEGACY_READS_REQUIRE_AUTH` | `true` | **enforced** The `/api/v1/{thermo,kinetics,geometries,...}` legacy routes pre-date the internal-IDs visibility policy and leak integer PKs. They must require auth in hosted deployments. |
| `ALLOW_PUBLIC_INTERNAL_IDS` | `false` | **enforced** Hides internal integer primary keys from scientific responses. Clients use public refs. Set to `true` only in local/dev/test for compatibility with the legacy id-bearing shape. |
| `SESSION_COOKIE_SECURE` | `true` | **enforced** Required so browsers only send the session cookie over TLS. Set `false` only when testing a local HTTP-only setup. |
| `SESSION_COOKIE_HTTPONLY` | `true` | Prevents JavaScript access to the session cookie. Default is already `true`; do not flip it. |
| `SESSION_COOKIE_SAMESITE` | `lax` | Default is already `lax`. Stricter (`strict`) is acceptable; never use `none` without `secure=true` and explicit CORS allow-listing. |
| `RATE_LIMIT_ENABLED` | `true` | **enforced** The app-level rate limiter. The test suite turns it off via env var; nothing else should. |
| `CORS_ALLOW_ORIGINS` | explicit allow-list or unset | **enforced (no wildcard)** Default empty means no CORS middleware is registered, which is the correct posture for an API-key-only API. Set to a comma-separated list of trusted browser origins (e.g. `https://app.tckdb.example.org`) only if a browser app needs to call the API. `*` is rejected at startup in both hosted modes. |
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

## Public-read policy assumptions

The scientific read endpoints (`/api/v1/scientific/...`) serve anonymous traffic in hosted deployments. Two operational assumptions back the policy choices made in the read layer — both must hold before exposing the API publicly.

### Rejected / deprecated records are opt-in, not authorization-gated

Public scientific search endpoints hide `rejected` and `deprecated` records by default. Callers may explicitly opt in via `include_rejected=true` and/or `include_deprecated=true` on any search request. The flags are **not** auth-gated — anonymous callers can flip them.

This is an intentional transparency policy for scientific reproducibility, not an authorization boundary. The motivating use case is "I want to understand why this record was deprecated and what replaced it"; gating that behind auth would defeat the purpose of a public scientific archive.

Operational consequence:

- **Do not store private or sensitive data in rejected or deprecated records.** The same anonymous traffic that can read `approved` content can opt in to read these by name.
- Curators rejecting a record should treat the rejection note as public.
- If a future deployment policy needs to restrict this (e.g. a behind-the-firewall lab instance where deprecated data has a privacy implication), gate it at the ingress layer or fork the flag's default — do not assume the application enforces auth on these flags.

### Artifact storage buckets must be private

The scientific artifact read surface (`/api/v1/scientific/artifacts/...` and the `include=artifacts` projections on calculation detail / search) exposes artifact **metadata** only:

- `kind`, `filename`, `sha256`, `bytes`, `created_at`
- `uri` — the raw storage URI verbatim, e.g. `s3://bucket/key`

The endpoint never inlines artifact body bytes, never resolves the URI to a presigned download URL, and no public endpoint accepts a caller-supplied URI as a download target.

Operational consequence:

- **Artifact storage buckets must be private** (no anonymous read ACL, no public bucket policy). The `uri` is a name, not an access grant — but it loses that property if the bucket is also publicly listable.
- If a future endpoint generates presigned download URLs, it must enforce its own authorization separately; the public scientific read surface deliberately does not.
- Bucket-listing capabilities should also be private — leaking a bucket-key namespace via the API is acceptable only when those names cannot be turned into reads externally.

A deploy that fails either assumption is not safe to expose anonymously, regardless of how strict the application-level checks are.

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
| `LOG_FORMAT` | `json` for hosted | Structured one-JSON-per-line output with `request_id`, `level`, `logger`, `message`, `timestamp`. Recommended when logs are shipped to journald/ELK/Datadog/etc. Default `text` is fine for ad-hoc inspection only. |
| `PUBLIC_MAX_LIMIT` | `200` (default) | Hard cap on page size. Lower it on memory-constrained hosts. |
| `MAX_GEOMETRY_ATOMS_PUBLIC` | `500` (default) | |
| `MAX_FULL_CALCULATIONS_PUBLIC` | `100` (default) | |
| `MIN_SUPPORTED_TCKDB_CLIENT_VERSION` | the version you tested against | Older clients are rejected on write paths with `426 Upgrade Required`. |
| `ENFORCE_TCKDB_CLIENT_VERSION_ON_WRITES` | `true` (default) | |

---

## Operational pre-flight

Settings alone are not enough. Before opening the deployment to traffic:

- [ ] `DEPLOYMENT_MODE` is set to `shared_private` or `hosted_public`. The API exits at startup if any **enforced** row in the table above is at an unsafe value — the error message lists every violation in one pass.
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

## Rate limiter and Uvicorn workers

The default rate-limit backend is **in-process**. Every Uvicorn worker keeps its own counters in local memory and they are never reconciled. Running `uvicorn ... --workers N` therefore multiplies the *effective* bucket budget by `N` — a 60/min anonymous-read limit with `--workers 4` is actually 240/min from a single source IP.

For the self-hosted / single-node / Pi deployment: keep `--workers 1`. The shipped systemd unit at [`examples/deployment/systemd/tckdb-api.service`](../../examples/deployment/systemd/tckdb-api.service) is configured this way.

If a single worker is not enough throughput, scale out *after* moving the limiter to a shared backend (e.g. Redis). Do not raise `--workers` while the in-process limiter is still in use — the rate-limit smoke test (the `/auth/login` flood from the pre-flight) will still trip 429, masking the bucket inflation.

---

## What this checklist does not cover

Out of scope here; tracked separately:

- Email verification on registration (no implementation today).
- CAPTCHA on the registration / login surface (relies on upstream WAF / Cloudflare).
- API-key permission scopes (today keys inherit owner role).
- OAuth / SSO (not implemented).

---

## See also

- [`.env.selfhosted.example`](../../.env.selfhosted.example) — template that encodes every safe value above.
- [README](README.md) — deployment scenarios overview.
- [`shared-private-deployment.md`](shared-private-deployment.md) — lab/group operational guide.
- [`self_hosted_single_node.md`](self_hosted_single_node.md) — single-node operator guide.
- [Deployed-DB migration playbook](../../backend/docs/deployment/migrations.md) — `alembic upgrade head` on a real database.
- [Backend deployment readiness audit](../../backend/docs/specs/backend_deployment_readiness_audit.md) — the audit that produced this checklist.
