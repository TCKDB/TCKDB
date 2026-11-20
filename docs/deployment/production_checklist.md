# Production checklist

The settings and checks that must hold before a TCKDB instance is reachable
from anywhere but its own machine: a lab server, a public instance, or
anything in between. A laptop that only you use may relax them.

[`.env.selfhosted.example`](../../.env.selfhosted.example) already holds a safe
value for every row, and `backend/scripts/ops/tckdb_make_env.sh` fills in the
secrets. Start from those, not from [`.env.example`](../../.env.example),
which is for development.

---

## Deployment mode and startup guard

Set `DEPLOYMENT_MODE` to one of:

| Value | Use |
|---|---|
| `local` | A developer's laptop or CI. Open registration, the `/docs` console and plain-HTTP cookies are allowed. The default when unset. |
| `shared_private` | A lab or internal instance on a private network. Every **enforced** row below is checked at startup. The default that `tckdb_make_env.sh` writes. |
| `hosted_public` | A public, internet-facing instance. The same checks, and `SESSION_COOKIE_SECURE=false` is never accepted. |

In `shared_private` and `hosted_public` the API refuses to start while any
enforced setting is unsafe, and lists every problem in one message. The
checks are in [`backend/app/api/startup_checks.py`](../../backend/app/api/startup_checks.py).

On a [self-hosted](README.md) stack, the one-off `init` container also
refuses to run while any of the seven secrets is empty or a `change-me`
placeholder, so the API never starts with one.

---

## Required settings

Set every row before the API is reachable from beyond the host's loopback
interface.

| Env var | Required value | Why |
|---|---|---|
| `DEPLOYMENT_MODE` | `shared_private` or `hosted_public` | **enforced** Selects the posture and turns on the startup guard. |
| `AUTH_ALLOW_OPEN_REGISTRATION` | `false` | **enforced** `true` by default for development. Left on, anyone can create an account. Create accounts with `backend/scripts/bootstrap_admin.py`. |
| `EXPOSE_API_DOCS` | `false` | **enforced** `true` by default. When `false`, FastAPI registers none of `/docs`, `/redoc` and `/openapi.json`. `/docs` is Swagger UI, a live request console pointed at the instance, so it is enforced off. To publish the API contract without it, set `EXPOSE_API_REFERENCE=true`. |
| `EXPOSE_API_REFERENCE` | your choice | Not enforced; **`false` by default**. When `true` and `EXPOSE_API_DOCS=false`, FastAPI serves ReDoc at `/redoc` and the schema at `/openapi.json`, still without `/docs`. ReDoc has no "Try it out" button. Its page loads the renderer from `cdn.jsdelivr.net` and fonts from Google Fonts, so it is blank for a reader who blocks those hosts. |
| `LEGACY_READS_REQUIRE_AUTH` | `true` | **enforced** The legacy `/api/v1/{thermo,kinetics,geometries,...}` routes expose integer row IDs. |
| `ALLOW_PUBLIC_INTERNAL_IDS` | `false` | **enforced** Hides integer primary keys from scientific responses. Clients use public refs. `true` only in local development and tests, for the old ID-bearing shape. |
| `SESSION_COOKIE_SECURE` | `true` | **enforced** Browsers then send the session cookie only over TLS. A `shared_private` instance served over plain HTTP on a trusted network may set `false` together with `SESSION_COOKIE_ALLOW_PLAIN_HTTP=true`; the API logs a warning and the cookie crosses the network unencrypted. `hosted_public` refuses `false` regardless. |
| `SESSION_COOKIE_HTTPONLY` | `true` | Keeps the cookie away from JavaScript. Already the default. |
| `SESSION_COOKIE_SAMESITE` | `lax` | Already the default. `strict` is fine. Never `none` without `secure=true` and an explicit CORS allow-list. |
| `PAGING_CURSOR_KEY` | a random secret, 32+ characters | **enforced** Seals paging cursors so they hold no readable record ID. Every API process must share it. Changing it breaks cursors in flight (clients get 422 `invalid_cursor` and start their traversal again); nothing stored depends on it. |
| `RATE_LIMIT_ENABLED` | `true` | **enforced** The test suite turns it off; nothing else should. |
| `CORS_ALLOW_ORIGINS` | an explicit list, or unset | **enforced: no `*`** Unset means no CORS middleware, which is right for an API used with keys. List trusted browser origins (for example `https://app.tckdb.example.org`) only if a browser app on another origin calls the API. |
| `TRUSTED_PROXY_HEADER` | the header your ingress sets, or unset | Unset by default. Tells the rate limiter where to read the client's IP. Set it only behind a proxy that always overwrites the header: Cloudflare → `CF-Connecting-IP`; nginx with `proxy_set_header X-Real-IP` → `X-Real-IP`. |
| `DB_STATEMENT_TIMEOUT_MS` | `30000`, or your budget | Caps how long one query may hold a pool slot. The role setup also sets it on the runtime role. |
| `DB_CONNECT_TIMEOUT_SECONDS` | `10` (the default) | Caps how long opening one connection may take. `DB_STATEMENT_TIMEOUT_MS` cannot help until a server answers, so this is what bounds a database host that is unreachable rather than down (a firewall drop, a removed container, a stale DNS answer), which otherwise waits out the kernel's SYN retries, about 130 s. Raise it only for a distant database; `0` restores the OS default. |
| `DB_PASSWORD` | strong and random | **enforced: not empty, not `change-me`** Compose refuses to start without one. |
| `DB_ADMIN_*` / `DB_OWNER_*` | strong, distinct, operator-only | In a separate mode-600 file (`.env.db-admin`) that the API and worker never load. Alembic uses the owner account. |
| `S3_ACCESS_KEY` / `S3_SECRET_KEY` | strong and random | **enforced: not empty, not `change-me`** Never the development defaults. |
| `SEAWEEDFS_JWT_KEY` | `openssl rand -hex 32` | Used by the `seaweedfs` service only. It signs the tokens its filer and volume server require, which keeps other containers out of the store's internal HTTP ports. The container exits 78 without one of 16+ characters. |
| `S3_SEAWEEDFS_MASTER_URL` | `http://seaweedfs:9333` with the bundled store; unset otherwise | Optional. Lets the API tell a full SeaweedFS from a fault (507 and a degraded `/status` instead of 503) and warn before it fills. |
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER` | the production database | `DB_HOST` and `DB_PORT` are the host-side address. The `selfhost` compose profile sets its own container values. |
| `TCKDB_API_HOST` | `127.0.0.1` | For an API run on the host. The `selfhost` profile publishes the API container on 127.0.0.1 itself. Only the ingress should talk to the API. |
| `TCKDB_EXTRA_NETWORKS` | your `<project>_storage`, e.g. `tckdb_storage` | Only for an API container started by `tckdb_deploy.sh` that uses the bundled SeaweedFS; the `selfhost` profile joins the network itself. The object store sits on its own `storage` network, and anything on it can reach SeaweedFS's unauthenticated gRPC ports, so attach nothing else. |
| `TCKDB_MIGRATION_ENV_FILE` | an owner-only env file (`DB_OWNER_USER` / `DB_OWNER_PASSWORD`), mode 600 | Only for `tckdb_deploy.sh`, once owner and runtime roles are split. Passed to the `alembic upgrade` run only, never to the API. Plain `KEY=value` lines; never point it at `.env.db-admin`. See [database roles](../../backend/docs/deployment/database_roles.md). |

### Signing secrets

Sessions are server-side rows in `user_session`, keyed by a SHA-256 hash of
the cookie token. There is no signed cookie or JWT for users, so there is no
`SECRET_KEY` or `SESSION_SECRET` to set. The secrets are the database
passwords, the S3 keys, the paging cursor key and SeaweedFS's JWT key, which
signs tokens inside the object store only.

---

## Public-read policy assumptions

The scientific read routes (`/api/v1/scientific/...`) answer anonymous
callers. Two assumptions behind them must hold before the API is public.

### Rejected / deprecated records are opt-in, not authorization-gated

Search hides `rejected` and `deprecated` records by default. Any caller,
anonymous included, can ask for them with `include_rejected=true` or
`include_deprecated=true`. This is a transparency policy, not an
authorization boundary: a reader should be able to see why a record was
deprecated and what replaced it.

- **Store nothing private in a rejected or deprecated record.** Anyone who
  can read approved records can read these by asking.
- Treat a rejection note as public.
- If an instance needs to hide them, do it at the ingress. The application
  does not.

### Artifact storage buckets must be private

Artifact search and `include=artifacts` on calculation detail return
metadata only:

- `kind`, `filename`, `sha256`, `bytes`, `created_at`;
- `uri`, the raw storage URI, such as `s3://bucket/key`.

Search never includes file contents or a presigned URL. The download route
`GET /api/v1/scientific/artifacts/{sha256}/download` takes only a canonical
digest, requires an approved owning calculation, and checks the SHA-256 and
byte length before returning anything. It never accepts a storage URI from
the caller.

- **Keep the bucket private:** no anonymous read, no public bucket policy.
  The `uri` names an object; it stops being harmless if the bucket is
  publicly listable.
- The download route is public only for approved calculations. Unknown and
  unapproved digests both return 404, and integrity or storage failures fail
  closed.
- Keep bucket listing private too.

An instance that breaks either assumption is not safe to open to anonymous
readers, however strict the application's own checks are.

---

## Recommended settings

Not enforced, but set them for anything on the internet.

| Env var | Recommended | Why |
|---|---|---|
| `TRUSTED_PROXY_HEADER` | your ingress's client-IP header | Without it every rate-limit key is the proxy's address and the per-IP budgets mean nothing. |
| `RATE_LIMIT_ANON_READ_PER_MINUTE` | 60 (default) | Raise only after watching real traffic. |
| `RATE_LIMIT_AUTH_READ_PER_MINUTE` | 300 (default) | |
| `RATE_LIMIT_AUTH_WRITE_PER_MINUTE` | 30 (default) | Tight on purpose: one misbehaving uploader should not exhaust the instance. |
| `RATE_LIMIT_BUNDLE_DRY_RUN_PER_MINUTE` | 10 (default) | `POST /bundles/dry-run` rehearses a full submit, so it has its own tighter bucket. It neither spends nor is limited by `AUTH_WRITE`. |
| `ANONYMOUS_MAX_BODY_BYTES` | 1048576 (default, 1 MiB) | Largest request body from a caller with no valid key or session, on any POST/PUT/PATCH under `/api/`. Refused with `413 anonymous_body_too_large` before it is read. Callers with credentials are unaffected; the selected-network export keeps its own cap. `0` turns it off. |
| `BUNDLE_MAX_BODY_BYTES` | 5242880 (default, 5 MiB) | Body cap for `/bundles/dry-run` and `/bundles/submit`, refused with `413 bundle_too_large` before parsing. About ten times the largest ARC run file measured. `0` turns it off; a negative value is refused at startup. (`RATE_LIMIT_*=0`, by contrast, refuses everything.) |
| `BUNDLE_MAX_RECORDS` | 500 (default) | Thermo plus kinetics records per bundle, refused with `422 bundle_too_many_records`. Real bundles carry one to a handful. |
| `RATE_LIMIT_ANON_OTHER_PER_MINUTE` | 20 (default) | Lower than `ANON_READ`, so anonymous writes don't get the read budget. |
| `RATE_LIMIT_AUTH_LOGIN_PER_MINUTE` | 10 (default) | Limits password guessing; keyed by IP. |
| `RATE_LIMIT_NAME_SEARCH_ANON_PER_MINUTE` / `_AUTH_PER_MINUTE` / `_IP_PER_MINUTE` | 30 / 120 / 120 (defaults) | `GET /scientific/species/name-search` has its own bucket. Every request also counts against the IP ceiling, so rotating credentials cannot exceed it. Keep the ceiling at or above the authenticated limit. |
| `RATE_LIMIT_REGISTER_PER_HOUR` | 10 (default) | Limits account spam; keyed by IP. Botnet registration needs an upstream WAF such as Cloudflare. |
| `LOG_FORMAT` | `json` | One JSON object per line with `request_id`, `level`, `logger`, `message`, `timestamp`, for journald, ELK, Datadog and the like. `text` suits reading by hand. |
| `PUBLIC_MAX_LIMIT` | `200` (default) | Largest page size. Lower it on a small host. |
| `MAX_GEOMETRY_ATOMS_PUBLIC` | `500` (default) | |
| `MAX_FULL_CALCULATIONS_PUBLIC` | `100` (default) | |
| `MIN_SUPPORTED_TCKDB_CLIENT_VERSION` | the client version you tested | Older clients get `426 Upgrade Required` on writes. |
| `ENFORCE_TCKDB_CLIENT_VERSION_ON_WRITES` | `true` (default) | |
| `TCKDB_CONTACT_EMAIL` | unset (default) | Adds `mailto:<address>` to the User-Agent of outbound requests. Crossref then puts lookups in its polite pool; without it they use the public pool. |

---

## Before you open it

Settings alone are not enough. Before any traffic:

- [ ] `DEPLOYMENT_MODE` is `shared_private` or `hosted_public`, and the API
  started. It exits at startup if any enforced row is unsafe.
- [ ] `.env.selfhosted` (or your equivalent) has no `change-me` value left.
  `tckdb_make_env.sh` generates them all.
- [ ] `backend/scripts/check_selfhosted_deployment.sh` passes. On a
  self-hosted stack it covers the next four rows: `init` provisions and
  checks the database roles, migrates to head and creates the first
  administrator on every start, and the check script confirms the result.
- [ ] The runtime role is a non-owner, non-superuser with no DDL,
  temporary-table or `TRUNCATE` rights: `configure_database_roles.py check`
  passes.
- [ ] The API and worker see no `DB_ADMIN_*` or `DB_OWNER_*` credentials.
- [ ] The database is at the latest migration: `alembic current` matches
  `alembic heads`. See the [migration runbook](../../backend/docs/deployment/migrations.md).
- [ ] At least one administrator exists.
- [ ] Registration is closed: `POST /api/v1/auth/register` returns `403` or
  similar.
- [ ] `/docs` is off: `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8010/docs`
  on the server returns `404`. Ask the API's own address; through the web UI
  every path outside `/api/` returns the app. (With
  `EXPOSE_API_REFERENCE=true`, `/openapi.json` and `/redoc` return `200` by
  design; `/docs` must still be `404`.)
- [ ] The API itself serves no page at `/`:
  `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8010/` returns `404`.
- [ ] If the ingress points at the web UI, `GET /` at the public address
  returns `200 text/html`, so someone following a link from a paper lands on
  a page, not a JSON 404.
- [ ] Legacy routes need a login: `/api/v1/thermo` without credentials
  returns `401`.
- [ ] Session cookies are secure: after a login over HTTPS, `Set-Cookie`
  carries `Secure` (unless you chose the plain-HTTP opt-in on a lab network).
- [ ] Rate limits trip: the 11th `/auth/login` from one IP within a minute
  returns `429` with a `Retry-After` header.
- [ ] The runtime role has a `statement_timeout`; the role setup sets it.
- [ ] `/api/v1/health` returns `200`.
- [ ] `/api/v1/readyz` returns `200` with
  `{"status":"ready","database":"ok","alembic_revision":"<rev>"}`. Anything
  else means the API is up but the database or schema is not; don't send
  traffic.
- [ ] Every response has an `X-Request-ID` header (`curl -i .../api/v1/health`).
  Clients may send their own and the server echoes it when it is safe.
- [ ] `LOG_FORMAT=json`, so logs carry `request_id`, `level`, `logger` and
  `message`.
- [ ] TLS terminates at the proxy or tunnel, and the API listens only on
  `127.0.0.1`.
- [ ] On a Linux host running the bundled `seaweedfs` service, the
  [object store firewall](storage-firewall.md) is installed and
  `sudo /usr/local/sbin/tckdb_storage_hostfw.sh --check` exits 0.
- [ ] Backups run, and one [restore](self_hosted_single_node.md#restore) has
  been tried.

An instance that passes both tables and this list is safe to open. One that
does not, is not.

---

## Rate limiter and Uvicorn workers

The rate limiter counts in process memory. Each Uvicorn worker keeps its own
counters, and they are never combined. With `--workers N` every budget is
multiplied by `N`: a 60-per-minute anonymous limit with `--workers 4` lets
240 through from one IP.

Keep one worker. The API image and the example systemd unit
([`tckdb-api.service`](../../examples/deployment/systemd/tckdb-api.service))
both run one. Scale out only after moving the limiter to a shared backend
such as Redis. The `/auth/login` flood test above still trips `429` with
several workers, so it will not reveal the inflated budgets.

---

## Not covered

- Email verification at registration (not implemented).
- CAPTCHA on registration and login (use an upstream WAF such as Cloudflare).
- Permission scopes on API keys (a key has its owner's role).
- OAuth and single sign-on (not implemented).

---

## Loading species names from PubChem

Name search has no names until some are loaded. The fetch runs on an
operator's machine, never at request time. The load runs inside the API
container with the API's own `DB_*` settings, so it needs no API key. Run the
fetch from `backend/` (no `PYTHONPATH` needed).

1. **Fetch.** Builds the species list from the public species browse (GET
   only), asks PubChem for each InChIKey with its charge and covalent-unit
   count (nothing else is sent; at most 4 requests a second, 10 s timeout, up
   to 2 retries on 503, responses over 1 MB refused) and writes a review
   file. The file is rewritten every 25 InChIKeys, so an interrupted run
   keeps what it fetched:

    ```bash
    python scripts/names/fetch_pubchem_names.py --from-api https://<host> --save-species species.json --out names_review.json
    # or, from a species list you already have (species_ref, inchi_key and an integer charge per item; smiles is optional):
    python scripts/names/fetch_pubchem_names.py --species species.json --out names_review.json
    ```

    Each row of `rows` is one name: species ref, InChIKey, name, kind
    (`common` from PubChem's Title, `systematic` from its IUPACName), CID,
    PubChem URL and retrieval time. Only a PubChem compound with one covalent
    unit and the species' charge is a candidate, and exactly one must remain.
    `skipped` lists what was left out and why: `no_pubchem_match`,
    `no_matching_cid` (PubChem has compounds, none with one unit and that
    charge), `several_cids` (for example tautomers),
    `inchikey_shared_by_species` (PubChem does not know spin state),
    `smiles_multi_component`, `lookup_failed` (timeout, network error, 503
    after retries, oversized or malformed response), `name_missing`,
    `name_length`, `name_characters`, `name_no_letter_or_digit`,
    `bad_input_row`, `bad_browse_record`.
2. **Review.** Read `names_review.json` and delete rows you do not want.
   Only the `rows` section is loaded.
3. **Dry run.** Reports every row as `would_insert` or `skipped` with a
   reason, and writes nothing. Run it in the API container, reading the file
   from standard input. On a self-hosted stack:

    ```bash
    docker compose --env-file .env.selfhosted --env-file .env.db-admin --profile selfhost \
        exec -T tckdb-api /opt/conda/bin/python scripts/names/import_species_names.py /dev/stdin < names_review.json
    ```

    For a container started another way, `<api-container>` being its name
    (the image's entrypoint activates the environment):

    ```bash
    docker exec -i <api-container> /usr/local/bin/_entrypoint.sh python scripts/names/import_species_names.py /dev/stdin < names_review.json
    ```

4. **Import.** The same command with `--commit`. A name is written only when
   the species ref exists, is not merged, still has the row's InChIKey, and
   that InChIKey belongs to no other live species. Names are stored with
   source kind `offline_import` and the one shared `external_source` row
   `PubChem Compound` / `rolling`, the PubChem CID (`external_record_key`)
   and the row's `retrieved_at`; a row without a usable `retrieved_at` is
   skipped as `bad_row`. A name the species already has (live or retired,
   curated or imported) is skipped, so a rerun writes nothing new and a
   retired name is never restored. Skip reasons: `bad_row`,
   `species_not_found`, `inchikey_mismatch`, `inchikey_shared_by_species`,
   `species_merged`, `already_present`, `retired_name_not_restored`,
   `insert_refused` (the database refused the insert for a reason other than
   a merged species), plus the name reasons above.

## See also

- [`.env.selfhosted.example`](../../.env.selfhosted.example): a safe value for every row above.
- [Self-host TCKDB](README.md)
- [Lab server](shared-private-deployment.md)
- [Public server](self_hosted_single_node.md)
- [Migration runbook](../../backend/docs/deployment/migrations.md): `alembic upgrade head` on a database that holds data.
