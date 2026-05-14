# Public Read Abuse Controls

Status: implemented (security phase 1).
Scope: hosted, internet-facing deployments of TCKDB.
Audit reference: `docs/audits/security_public_read_abuse_audit.md` — findings F1–F6.

This document describes the application-level guardrails that gate
the public scientific-read API for a hosted MVP. Everything below is
controlled by environment variables read into
`app/api/config.py::Settings`. Defaults are tuned for a hosted
deployment; local development overrides them via the `tckdb_env`
configuration block.

---

## 1. Rate limiting

The middleware `app.api.rate_limit.RateLimitMiddleware` is registered
unconditionally and short-circuits when `RATE_LIMIT_ENABLED=false`. It
keeps an in-process fixed-window counter per `(bucket, identity)`
pair.

### Buckets

Buckets are split by route class so a noisy uploader cannot starve
the public read surface and an anonymous scraper does not inherit the
generous read budget for writes:

| Bucket        | Identity                       | Window | Default budget |
| ------------- | ------------------------------ | ------ | -------------- |
| `anon_read`   | client IP                       | 60 s   | `RATE_LIMIT_ANON_READ_PER_MINUTE` = 60 |
| `auth_read`   | hash of `X-API-Key` or session  | 60 s   | `RATE_LIMIT_AUTH_READ_PER_MINUTE` = 300 |
| `auth_write`  | hash of `X-API-Key` or session  | 60 s   | `RATE_LIMIT_AUTH_WRITE_PER_MINUTE` = 30 |
| `anon_other`  | client IP                       | 60 s   | `RATE_LIMIT_ANON_OTHER_PER_MINUTE` = 20 |
| `login`       | client IP                       | 60 s   | `RATE_LIMIT_AUTH_LOGIN_PER_MINUTE` = 10 |
| `register`    | client IP                       | 3600 s | `RATE_LIMIT_REGISTER_PER_HOUR` = 10 |

Classification rules:

- **`anon_read` / `auth_read`** — public scientific reads (GET
  `/api/v1/scientific/...`, POST `/api/v1/scientific/.../search`, GET
  `/api/v1/workflow-tools`, GET `/api/v1/workflow-tool-releases`).
  Split by whether a credential is present.
- **`auth_write`** — authenticated mutating requests (POST/PUT/PATCH/
  DELETE) on any non-login/register, non-public-read path.
- **`auth_read`** also serves as the authenticated fallback for
  non-mutating requests on non-public-read paths (e.g. authenticated
  GET on `/api/v1/admin/...`). An `auth_other` bucket will be added
  only when a concrete route class warrants its own budget.
- **`anon_other`** — anonymous everything-else, including stray
  mutating POSTs. Anonymous writes deliberately do **not** inherit
  the read budget.

A request enters an authenticated bucket if it bears any credential
(an `X-API-Key` header or a `tckdb_session` cookie) even when that
credential is invalid — the limiter only fingerprints the credential
for bucket selection; the handler still returns 401 for a bad key.

Storage is still in-process and single-worker only. Hosted
multi-worker deployments will need a shared backend (Redis) — left as
follow-up.

### Migration from pre-split buckets

Anonymous scientific reads, authenticated scientific reads,
authenticated writes, and anonymous other requests now use separate
buckets. External deployments whose `.env` predates this split must
rename:

```text
RATE_LIMIT_ANON_PER_MINUTE -> RATE_LIMIT_ANON_READ_PER_MINUTE
RATE_LIMIT_AUTH_PER_MINUTE -> RATE_LIMIT_AUTH_READ_PER_MINUTE
```

and optionally set the two new knobs (otherwise the defaults from
`Settings` apply):

```text
RATE_LIMIT_AUTH_WRITE_PER_MINUTE=30
RATE_LIMIT_ANON_OTHER_PER_MINUTE=20
```

There are no aliases — the old names are no-ops and Pydantic will
silently ignore them.

### Trusted-proxy header

The single setting `TRUSTED_PROXY_HEADER` controls how the limiter
derives a client IP. The default is empty — the limiter uses the
ASGI transport peer (i.e. whatever opened the socket), which cannot
be spoofed by the public client. **That is the correct default for
local uvicorn and any deployment where the public internet talks
directly to the worker.**

Set the header name *only* when the worker is behind a reverse proxy
that:

1. Strips any client-supplied copy of the header before forwarding,
   and
2. Rewrites the header with the original client IP on every request.

Without those two properties, trusting a client-supplied header is
worse than no proxy header at all — attackers can claim arbitrary
client IPs and walk past per-IP rate limits.

#### Supported header shapes

The parser handles:

| Shape                                      | Example                                  | Result          |
| ------------------------------------------ | ---------------------------------------- | --------------- |
| `X-Forwarded-For` comma list               | `1.2.3.4, 10.0.0.1, 10.0.0.2`            | `1.2.3.4`       |
| Single-IP headers (`X-Real-IP`, `CF-Connecting-IP`) | `1.2.3.4`                       | `1.2.3.4`       |
| `CloudFront-Viewer-Address` IPv4 + port    | `203.0.113.10:443`                       | `203.0.113.10`  |
| Raw IPv6                                   | `2001:db8::1`                            | `2001:db8::1`   |
| Hostname + port (not IPv4)                 | `example.com:443`                        | `example.com:443` (unchanged) |

Whitespace around the leftmost entry is trimmed. If the configured
header is absent or empty on a given request, the limiter falls
through to the ASGI transport peer so a misconfigured caller still
gets a per-connection bucket instead of joining a shared "unknown"
group.

Raw IPv6 host:port (e.g. `[2001:db8::1]:443`) is **not** parsed —
CloudFront customers on IPv6 should configure a clean client-IP
header upstream (`X-Real-IP` / `CF-Connecting-IP`) rather than rely
on `CloudFront-Viewer-Address`.

#### Per-deployment recipes

```env
# Local / direct uvicorn — the safest default.
TRUSTED_PROXY_HEADER=

# nginx or Traefik in front of TCKDB. Both prepend the client IP
# to X-Forwarded-For when their config sets ``trusted_proxies`` /
# the equivalent.
TRUSTED_PROXY_HEADER=X-Forwarded-For

# Cloudflare — CF-Connecting-IP is the single-IP header set by
# Cloudflare's edge and overwritten on every request.
TRUSTED_PROXY_HEADER=CF-Connecting-IP

# Single-IP proxy header convention (kubernetes ingress, custom
# upstream).
TRUSTED_PROXY_HEADER=X-Real-IP
```

**Never** set `TRUSTED_PROXY_HEADER=X-Forwarded-For` when the worker
is reachable from the public internet without a stripping proxy in
front of it.

The matching test coverage lives in
`backend/tests/api/test_api_rate_limit_proxy_headers.py`.

### Response shape

A rejected request returns HTTP 429 with the stable code
`rate_limit_exceeded`:

```json
{
  "detail": "Too many requests. Retry after the current rate-limit window expires.",
  "code": "rate_limit_exceeded",
  "bucket": "anon_read",
  "retry_after_seconds": 47
}
```

The `Retry-After` header echoes the same wait. `bucket` lets the
client decide whether to back off the anonymous fleet or just slow a
single credentialed worker.

### Health checks

`/api/v1/health` is exempt — operators ping it on a loop and it must
remain cheap to call.

---

## 2. Anonymous query caps

Even within the rate budget, individual requests cannot fan out
unboundedly. The caps below are enforced server-side in the service
layer; the API rejects exceeding requests with HTTP 422 and a stable
code.

| Setting                          | Default | Error code (in `detail`) |
| -------------------------------- | ------- | ------------------------ |
| `PUBLIC_MAX_LIMIT`               | 200     | `limit_too_large` (Pydantic level may also return generic 422) |
| `PUBLIC_MAX_OFFSET`              | 10 000  | `offset_too_large` |
| `MAX_GEOMETRY_ATOMS_PUBLIC`      | 500     | `geometry_too_large` |
| `MAX_FULL_CALCULATIONS_PUBLIC`   | 100     | `query_too_expensive` |
| `MAX_FULL_GEOMETRIES_PUBLIC`     | 100     | `query_too_expensive` |
| `MAX_FULL_ARTIFACTS_PUBLIC`      | 100     | `query_too_expensive` |

`PUBLIC_MAX_LIMIT` is clamped to the per-endpoint `MAX_LIMIT` (200)
so cutting it lower in config is honored, but raising it above 200
has no effect.

The `/reaction-entries/{id}/full` cap applies regardless of how the
section was requested — there is no way to evade it by enumerating
`include=` tokens vs. `include=all`.

Future curator/admin override: not implemented in this phase.

---

## 3. Reaction-search enumeration guard

`/scientific/reactions/search` requires at least one *meaningful*
scoping filter:

- `reactants` and/or `products`, **or**
- `reaction_ref`, **or**
- `reaction_entry_ref`.

A request with none of these returns 422 with detail
`missing_reaction_search_filter`. This closes the F6 path where an
empty filter would scan every `reaction_entry.id`. Ref-only lookups
push the filter into SQL so the candidate set is small from the
start.

`family`, `min_review_status`, and the include-flag options are
explicitly **not** considered meaningful filters because none of
them bound the candidate set to a small, predictable size.

---

## 4. Login / register throttling

`/api/v1/auth/login` and `/api/v1/auth/api-keys` share the `login`
bucket; `/api/v1/auth/register` uses the `register` bucket. Both are
per-IP. The login error message is intentionally identical for
"unknown user" and "wrong password" so the response does not reveal
account existence.

For hosted production:

- Set `AUTH_ALLOW_OPEN_REGISTRATION=false` and seed accounts via the
  admin bootstrap script. The throttle is a backstop, not the policy.
- Cap API keys per user at the application layer (not implemented in
  this phase — left for follow-up; see F2 in the audit).

---

## 5. CORS

The factory in `app/api/app.py` registers `CORSMiddleware` **only**
when `CORS_ALLOW_ORIGINS` is non-empty. The hosted default is an
empty list, which means the API rejects all cross-origin browser
calls by virtue of no CORS headers ever being returned.

| Setting                  | Default                          |
| ------------------------ | -------------------------------- |
| `CORS_ALLOW_ORIGINS`     | `[]` (no middleware registered)  |
| `CORS_ALLOW_CREDENTIALS` | `false`                          |
| `CORS_ALLOW_METHODS`     | `["GET", "POST", "OPTIONS"]`     |
| `CORS_ALLOW_HEADERS`     | `["Authorization", "Content-Type", "X-API-Key"]` |

Production deployments set the allow-list to the canonical web
frontend origin only. Wildcard origins combined with
`allow_credentials=true` are not configurable — the middleware
inputs are passed through unchanged, and FastAPI enforces the
no-wildcard-with-credentials rule.

The reverse proxy must not rewrite `Access-Control-Allow-Origin`
once it leaves the app; weakening the CORS response at the proxy
turns this guardrail off.

---

## 6. Recommendations for the reverse proxy / CDN

The middleware here is the second line of defense. A hosted
deployment should also:

- Enforce a coarser per-IP rate limit at the reverse proxy (e.g.
  300/min for `/api/v1/`) so abusive bursts are shed before they
  reach the application worker.
- Cache `GET /api/v1/scientific/*` responses for a short TTL keyed by
  (path + query + `Authorization` + `X-API-Key`); coordinate the TTL
  with the curator publish workflow.
- Block requests larger than 4 MB at the proxy. The application does
  not currently enforce a body-size cap.
- Disable Swagger / ReDoc / `/openapi.json` in production until the
  audit's F8 follow-up lands.

---

## 7. Phase 2 hardening — error leakage, legacy routes, docs, input bounds, cookies

The findings below were medium-severity in the audit; they don't
block hosted MVP but improve production posture without redesigning
the read API.

### 7.1 Error response hardening (F11, F18)

- The `IntegrityError` handler still classifies the failure by
  SQLSTATE but emits only `{ "detail", "code", "category" }` in the
  public body. SQLSTATE, constraint names, raw psycopg/SQLAlchemy
  text, and the offending SQL statement are kept in the
  `logger.warning` record and never reach the client.
- The category (`"integrity_error"`) is a stable taxonomy for clients
  that want to branch on classes of failure rather than codes.
- `NotFoundError` messages emitted by the scientific handle resolver
  and the legacy entity-read routes no longer echo integer ids.
  When the caller supplied a public ref, the ref is echoed (refs are
  public-by-design); when the caller supplied an integer id, the
  message is just `"<resource> not found"`. The integer id is logged
  at `INFO` for operators.

### 7.2 Legacy entity-read auth gate (F14)

`/api/v1/{thermo,kinetics,species,reactions,calculations,…}` predate
the visibility policy and return a flatter shape that still carries
integer PKs. They are now gated behind a single setting:

| Setting                       | Default | Effect when true |
| ----------------------------- | ------- | ---------------- |
| `LEGACY_READS_REQUIRE_AUTH`   | `true`  | Anonymous calls return 401; an API key or session is required. |

`/api/v1/scientific/*`, `/api/v1/lookup/*`, `/api/v1/health`,
`/api/v1/auth/*`, `/api/v1/uploads/*`, `/api/v1/bundles/*`,
`/api/v1/submissions/*`, and `/api/v1/record-reviews/*` are **not**
covered by this gate. They keep their own visibility / auth posture.

Local development sets `LEGACY_READS_REQUIRE_AUTH=false` so the old
routes stay open for ad-hoc inspection.

### 7.3 OpenAPI / Swagger / ReDoc exposure (F8)

| Setting          | Default | Effect when false |
| ---------------- | ------- | ----------------- |
| `EXPOSE_API_DOCS`| `true`  | `/docs`, `/redoc`, and `/openapi.json` are not registered. FastAPI returns 404. |

Hosted production sets this to false. Local/dev leaves it on. The
scientific API endpoints are unaffected by either mode.

### 7.4 Free-text input bounds (F9)

`app/schemas/reads/_field_bounds.py` defines a single source of
truth for the per-field maximum lengths applied to every public
scientific search schema:

| Field                | Max length |
| -------------------- | ---------- |
| `smiles`             | 2048 |
| `inchi`              | 4096 |
| `inchi_key`          | 64 |
| `formula`            | 256 |
| `method` / `basis`   | 256 |
| `software`           | 256 |
| `workflow_tool`      | 256 |
| `family`             | 256 |
| Public refs (`*_ref`)| 64 |
| Reactants / products | up to 32 items; each ≤ 2048 chars |

Pydantic raises 422 on oversize values. The bounds are deliberately
generous — chemistry is not validated here, only payload size.

### 7.5 Session cookie posture (F16)

| Setting                      | Default | Notes |
| ---------------------------- | ------- | ----- |
| `SESSION_COOKIE_SECURE`      | `true`  | Set to `false` for local HTTP login. |
| `SESSION_COOKIE_SAMESITE`    | `"lax"` | Set to `"strict"` if you have no cross-site GET flows. |
| `SESSION_COOKIE_HTTPONLY`    | `true`  | Always true in current code; settable for completeness. |

The cookie is set by `/api/v1/auth/login` and `/api/v1/auth/register`.
The reverse proxy must not rewrite `Set-Cookie` to weaken these flags.

### 7.6 Path-handle length bound (F17)

The four scientific handle routes —
`GET /scientific/species-entries/{handle}/thermo`,
`GET /scientific/reaction-entries/{handle}/kinetics`,
`GET /scientific/reaction-entries/{handle}/full`,
`GET /scientific/geometries/{handle}` — declare
`Path(..., min_length=1, max_length=64)` on the handle parameter.
Public refs are ~31 chars (`<prefix>_<26-char body>`), so 64 leaves
generous headroom. Any longer path component is rejected at the
FastAPI validation layer with 422 before any handle parsing or DB
work runs. Integer compatibility handles (PKs) are well under the
cap.

Other route paths use `int` path parameters (`{thermo_id}`,
`{kinetics_id}`, ...). FastAPI rejects non-integer values with 422
during parameter coercion, so an over-long string never reaches the
handler.

### 7.7 `include=` parser semantics (F19)

`include=` accepts a comma-separated token list and that is the
documented public form:

```
?include=provenance,review
?include=all
?include=all,internal_ids
```

Repeated query parameters are accepted for HTTP-client compatibility
(`?include=a&include=b` → `[a, b]`) but they are not the canonical
form. The OpenAPI description, public docs, and the Python client
all use the comma-separated encoding. Whitespace around each token
is trimmed; empty tokens are dropped; duplicates are deduplicated
server-side. Don't rely on positional differences between the two
encodings.

`include=all` expands to every legal token for the endpoint **except
`internal_ids`**. Callers who want the legacy id-bearing shape must
opt in explicitly: `include=internal_ids` or `include=all,internal_ids`.
In hosted production (`ALLOW_PUBLIC_INTERNAL_IDS=false`) the
`internal_ids` token is silently dropped from the resolved include
set; the response's `request.include` echoes the resolved set so
callers can detect the drop.

Unknown tokens — and known-but-illegal-for-the-endpoint tokens —
return 422 with the per-endpoint legal list in the message.

Hosted deployments that layer a WAF in front of the API should
write rules that match either encoding. See F19 in the audit.

### 7.8 Integer path-handle probe oracle (F7)

Scientific detail routes continue to accept both forms of path
handle: a public ref (``spe_…``, ``rxe_…``, ``geom_…``) and an
integer compatibility id (``1``, ``2``, …). Refs are the documented
public form and the only form used in published examples; integers
are accepted so older callers keep working.

The 404 response shape for unknown handles is now indistinguishable
between integers and refs at the body level:

| Lookup form        | Status | Body |
| ------------------ | ------ | ---- |
| Unknown integer    | 404    | `{"detail": "<resource> not found", "code": "handle_not_found"}` |
| Unknown ref        | 404    | `{"detail": "<resource> not found (<resource>_ref='<ref>')", "code": "handle_not_found"}` |
| Wrong-prefix ref   | 422    | `{"detail": "handle_type_mismatch: …"}` |
| Malformed handle   | 422    | `{"detail": "invalid_handle: …"}` |

For integer probes, the probed integer never appears in the public
body — operators can still correlate 404 spikes via the server log
(``logger.info("path_handle_not_found …")``). Two distinct
unknown integer probes return byte-identical bodies, so an attacker
cannot derive any per-id signal from the response alone.

Timing remains a residual oracle: serving a real row is slower than
returning a 404. Rate limiting (Phase 1) caps how fast an attacker
can sample the timing channel; the curator-only legacy routes
(Phase 2) prevent anonymous bulk listing.

Public docs and the Python client examples use ref handles
exclusively. Integer handles remain undocumented in the public
surface — they are a compatibility shim, not a contract.

### 7.9 Database statement timeout (F13)

A PostgreSQL ``statement_timeout`` is applied on every new DBAPI
connection through a SQLAlchemy ``connect`` listener. The setting
``DB_STATEMENT_TIMEOUT_MS`` (default ``30000``) is read at engine
construction time:

| Value      | Effect |
| ---------- | ------ |
| `30000`    | Every connection runs ``SET statement_timeout = 30000`` (30 s). |
| Positive N | Sets ``N`` milliseconds. |
| `0` / null | Listener is not registered; the role-level / cluster default applies. |

A query cancelled by PostgreSQL surfaces as a SQLAlchemy
``OperationalError`` with SQLSTATE ``57014``. The handler in
``app/api/errors.py`` maps this to:

```json
{
  "detail": "The request exceeded the database query timeout. Narrow the query or contact a curator for bulk access.",
  "code": "query_timeout"
}
```

with HTTP 503. The offending SQL statement and full driver
exception stay in the server log; no raw SQL reaches the client.
Other ``OperationalError`` variants (connection drops, admin
shutdowns) collapse to a generic ``database_unavailable`` body.

#### Hosted recommendation

The app-level listener is a belt-and-braces safety net. Production
deployments should also pin the timeout at the database role level
so a forgetful or panicked deployment cannot disable it:

```sql
ALTER ROLE tckdb SET statement_timeout = '30s';
```

If you later split the API into a read role and a write role,
prefer:

```sql
ALTER ROLE tckdb_reader SET statement_timeout = '15s';
ALTER ROLE tckdb_writer SET statement_timeout = '120s';
```

Setting the value at the role level survives application restarts
and removes the timeout from the application's set of mistakes.

---

## 8. Testing matrix

| Concern                                  | Test file |
| ---------------------------------------- | --------- |
| Rate limit + buckets                     | `backend/tests/api/test_api_rate_limiting.py` |
| Trusted-proxy header parsing             | `backend/tests/api/test_api_rate_limit_proxy_headers.py` |
| Login / register throttle                | `backend/tests/api/test_api_auth_throttling.py` |
| Pagination / geometry / `/full` caps + F6| `backend/tests/api/test_api_query_caps.py` |
| CORS posture                             | `backend/tests/api/test_api_cors.py` |
| IntegrityError sanitization              | `backend/tests/api/test_api_integrity_error_handler.py` |
| NotFoundError sanitization (F18)         | `backend/tests/api/test_api_notfound_sanitization.py` |
| Legacy read auth gate (F14)              | `backend/tests/api/test_api_legacy_route_auth.py` |
| OpenAPI exposure (F8)                    | `backend/tests/api/test_api_docs_exposure.py` |
| Free-text bounds (F9)                    | `backend/tests/api/test_api_field_length_bounds.py` |
| Secure cookie (F16)                      | `backend/tests/api/test_api_secure_cookie.py` |
| Path-handle length bound (F17)           | `backend/tests/api/scientific/test_api_path_handle_bounds.py` |
| Integer-handle probe oracle (F7)         | `backend/tests/api/scientific/test_api_integer_handle_oracle.py` |
| DB statement timeout (F13)               | `backend/tests/api/test_api_db_statement_timeout.py` |

Two autouse fixtures in `backend/tests/conftest.py` keep the rest of
the test suite working under the hardened defaults:
`_disable_rate_limit_by_default` and
`_security_phase2_test_defaults` (toggles
`legacy_reads_require_auth` and `session_cookie_secure` to false).

---

## 9. Future work (not in this phase)

- F7 follow-on — fully drop integer-id acceptance from path-handle
  routes once no external client depends on the compatibility shim.
- F13 follow-on — split the API into a dedicated read role and write
  role with distinct `statement_timeout` values
  (`tckdb_reader`/`tckdb_writer`).
- Per-API-key budgets, possibly Redis-backed for multi-worker deployments.
- Curator/admin override for `geometry_too_large` and `/full` caps.
- Tighter `category` taxonomy for the integrity-error envelope.
