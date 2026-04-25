# Single-machine private deployment (Local v0)

Run TCKDB privately on a single machine — laptop, dev workstation, or
single-user host — using the **same** backend schema, services,
authentication model, and upload APIs as every other TCKDB deployment.
This is a **deployment scenario**, not a separate edition or mode of
TCKDB. The "Local v0" name comes from the milestone that delivered the
first documented form of this scenario; the page describes the
scenario, not a fork. See [the deployment guide overview](README.md)
for the wider taxonomy.

> **Same backend, not a lite fork.**
> A single-machine private deployment does **not** sync to hosted, and
> it does **not** import or export contribution bundles.
> Local-to-hosted contribution is handled separately through
> contribution bundles. See
> [docs/decisions/0023-local-offline-and-hosted-submission-model.md](../decisions/0023-local-offline-and-hosted-submission-model.md).

> **Need a shared lab/group instance?** See
> [shared-private-deployment.md](shared-private-deployment.md).
> **Submitting from HPC?** See
> [client-access-from-hpc.md](client-access-from-hpc.md) — HPC is a
> client environment, not a separate deployment.
> **No Docker available?** See
> [native-advanced.md](native-advanced.md) — native install is one
> infrastructure strategy among several. All of these reuse the same
> backend, schema, and auth model documented on this page.

The page below uses the **Docker Compose quick-start** as its
infrastructure strategy: containers for PostgreSQL+RDKit and MinIO,
plus the host-side conda env for the backend API and upload worker.
This is a quick-start *recipe* for provisioning the application's
dependencies — not the definition of a single-machine deployment.
Other infrastructure strategies (managed PostgreSQL+RDKit,
[native install](native-advanced.md), and the deferred Apptainer
packaging) serve the same scenario.

---

## Prerequisites

- Docker and Docker Compose v2 (`docker compose version`)
- Miniforge/Conda with the `tckdb_env` environment installed
- `curl` for the smoke-test commands below

The host-side commands all use `conda run -n tckdb_env ...`; if your
shell already has the env activated you can drop the prefix.

### Working directories

The repo is split into `backend/` (Python/FastAPI) and `frontend/`
(Vite/TS). Two cwd contexts in this guide:

- **Project root** — `docker compose ...` commands. The compose files
  live at the root.
- **`backend/`** — every other host-side command (`alembic`,
  `uvicorn`, `python -m app.workers.upload_worker`, anything under
  `scripts/`). Get there once with `cd backend` and stay there.

Each section below states which context it assumes.

---

## 1. Start the local stack

> Run from the **project root**.

```bash
cp backend/.env.local.example backend/.env
docker compose -f docker-compose.local.yml up -d
```

This brings up:

| Service | Image | Purpose |
|---------|-------|---------|
| `db`    | `informaticsmatters/rdkit-cartridge-debian` | PostgreSQL with the RDKit cartridge |
| `minio` | `minio/minio` | S3-compatible artifact storage |

Both services run on `network_mode: host` so the host-side API reaches
them at `127.0.0.1:5432` and `127.0.0.1:9000` without overrides.

The backend API and upload worker are **not** containerized in Local v0
— see [Future work](#future-work) for the rationale.

---

## 2. Initialize the schema

> Run from `backend/`.

```bash
cd backend
set -a; source .env; set +a
conda run -n tckdb_env alembic upgrade head
```

This applies the single initial migration and seeds reference data
(e.g. `reaction_family`).

---

## 3. Bootstrap the first admin

> Run from `backend/`.

`backend/scripts/bootstrap_admin.py` is **idempotent**: it creates a
new admin if no matching account exists, and promotes (and
reactivates) an existing one if it does. Run it once after the schema
is up.

```bash
conda run -n tckdb_env python scripts/bootstrap_admin.py \
  --username admin \
  --email admin@example.local \
  --password "change-me" \
  --full-name "Local Admin"
```

Avoid passing `--password` on a shared shell — use the env var instead:

```bash
TCKDB_BOOTSTRAP_PASSWORD='change-me' \
  conda run -n tckdb_env python scripts/bootstrap_admin.py \
    --username admin \
    --email admin@example.local
```

> **Containerized usage.** Once a backend image exists (see
> [Future work](#future-work)) the same script will run as
> `docker compose -f docker-compose.local.yml exec api python scripts/bootstrap_admin.py ...`.
> For Local v0 the host-side command above is the supported path.

---

## 4. Start the backend API

> Run from `backend/`.

```bash
conda run -n tckdb_env uvicorn main:app \
  --host 127.0.0.1 --port 8000 --reload
```

By default `TCKDB_INLINE_WORKER=true` (in `backend/.env.local.example`)
so the upload worker runs as a thread inside the API process. To run
it separately for isolation, set `TCKDB_INLINE_WORKER=false` and start
a second terminal (also in `backend/`):

```bash
conda run -n tckdb_env python -m app.workers.upload_worker
```

---

## 5. Verify the deployment (smoke test)

### a. Health endpoint

```bash
curl -sf http://127.0.0.1:8000/api/v1/health
# {"status":"ok"}
```

### b. Log in (or register, if open registration is enabled)

If you bootstrapped an admin in step 3, log in:

```bash
curl -sf -c cookies.txt \
  -H "Content-Type: application/json" \
  -X POST http://127.0.0.1:8000/api/v1/auth/login \
  -d '{"username": "admin", "password": "change-me"}'
```

Or, with `AUTH_ALLOW_OPEN_REGISTRATION=true`, register a new user:

```bash
curl -sf -c cookies.txt \
  -H "Content-Type: application/json" \
  -X POST http://127.0.0.1:8000/api/v1/auth/register \
  -d '{"username": "alice", "password": "change-me-too", "email": "alice@example.local"}'
```

See [Single-user local vs shared lab-server](#single-user-local-vs-shared-lab-server)
for when to flip `AUTH_ALLOW_OPEN_REGISTRATION` off.

### c. Create an API key

API keys can only be minted with a session cookie — never with another
API key.

```bash
curl -sf -b cookies.txt \
  -H "Content-Type: application/json" \
  -X POST http://127.0.0.1:8000/api/v1/auth/api-keys \
  -d '{"label": "local-dev"}'
```

The response includes a one-time `key` field (e.g. `tck_...`). **Save
it now** — the plaintext value is never shown again.

```bash
export TCKDB_API_KEY='tck_...'
```

### d. Confirm anonymous uploads are rejected

```bash
curl -i -X POST http://127.0.0.1:8000/api/v1/uploads/...
# HTTP/1.1 401 Unauthorized
```

### e. Confirm API-key auth reaches the route

Any upload route accepts the `X-API-Key` header. The exact path and
payload depend on the workflow; using a generic placeholder:

```bash
curl -sf -X POST http://127.0.0.1:8000/api/v1/uploads/<workflow> \
  -H "X-API-Key: $TCKDB_API_KEY" \
  -H "Content-Type: application/json" \
  --data @payload.json
```

A 200/202 (or schema-validation 4xx) response confirms the request
reached the upload route under your `created_by` attribution.

---

## Single-machine private vs shared private deployment

The page above covers the **single-machine** scenario, where the API
binds to `127.0.0.1` and only the local user can reach it. Open
self-registration is acceptable and keeps the instance self-serve:

```env
AUTH_ALLOW_OPEN_REGISTRATION=true
```

If the API is instead reachable on a lab network (for example
`0.0.0.0:8000` behind a reverse proxy at
`http://tckdb.lab.example.org`), open registration becomes a soft
account-creation backdoor. Disable it:

```env
AUTH_ALLOW_OPEN_REGISTRATION=false
```

…and seed every account explicitly with
`backend/scripts/bootstrap_admin.py` (run from `backend/`):

```bash
conda run -n tckdb_env python scripts/bootstrap_admin.py \
  --username alice --email alice@lab.example.org
```

That posture — closed registration, per-user API keys, reverse proxy,
backups, access control — is the **shared private deployment**
scenario, documented in detail in
[shared-private-deployment.md](shared-private-deployment.md). It is
the same backend you bring up on this page; only operational policy
changes.

---

## Generic client targeting

Any TCKDB API client — script, notebook, lab pipeline, workflow tool,
future CLI, future frontend — targets a deployment by configuring just
two values:

- `base_url`
- `api_key`

```yaml
# Local
tckdb:
  base_url: "http://localhost:8000/api/v1"
  api_key: "tck_..."
```

```yaml
# Shared private deployment (e.g. lab service node)
tckdb:
  base_url: "https://tckdb.lab.example.org/api/v1"
  api_key: "tck_..."
```

```yaml
# Hosted
tckdb:
  base_url: "https://tckdb.example.org/api/v1"
  api_key: "tck_..."
```

The client behavior is identical across deployments — TCKDB is not tied
to a particular workflow tool.

---

## Stop and reset

### Stop (preserve data)

```bash
docker compose -f docker-compose.local.yml down
```

### Full reset (drop volumes — destroys local DB and artifacts)

```bash
docker compose -f docker-compose.local.yml down -v
```

You'll need to re-run the migration (step 2) and bootstrap admin (step
3) afterward.

---

## Troubleshooting

- **`alembic upgrade head` cannot connect.** Confirm the DB container is
  healthy: `docker compose -f docker-compose.local.yml ps`. The default
  port (5432) must be free on the host because the compose file uses
  `network_mode: host`.
- **Port 5432 already in use.** Either stop the existing Postgres
  service or change `DB_PORT` in `.env` *and* in your environment.
  The application reads `DB_PORT` from env.
- **`401 Authentication required` even with a session cookie.** Make
  sure the client is preserving cookies (`-c cookies.txt -b cookies.txt`
  for curl). Sessions expire — re-login if needed.
- **API key works but upload returns 403.** API keys inherit the
  user's role. Curators/admins are required for some endpoints — log in
  as a curator/admin before minting the key, or promote the user via
  the admin endpoints.
- **MinIO bucket missing.** The first artifact upload fails until the
  bucket exists. Either create it via the MinIO console at
  `http://localhost:9001` (login with `S3_ACCESS_KEY` /
  `S3_SECRET_KEY`) or use `mc mb`.

---

## What Local v0 explicitly does *not* include

These are deliberately deferred — they are not bugs:

- Contribution bundle export
- Hosted bundle import
- Local-to-hosted push
- Raw database synchronization
- Lite/reduced schema
- A local-only backend fork
- Apptainer/Singularity images
- Native installer automation
- Frontend bundle UI
- Service accounts
- Production hardening (TLS, secrets management, hardened cookies, etc.)

Local v0 makes TCKDB runnable privately. It does not create a separate
product, separate schema, or separate scientific validation path.

---

## Future work

- **Backend container packaging.** Tracked as its own milestone in
  [docs/roadmaps/backend-container-packaging-spec.md](../roadmaps/backend-container-packaging-spec.md).
  Local v0 deliberately runs the API and worker on the host because
  the repo has not yet standardized a Python dependency manifest.
  Containerizing the backend without that foundation is out of scope.
- **Shared private deployment.** Closed registration, reverse proxy,
  TLS, backups, access control on a lab service node. Documented in
  [shared-private-deployment.md](shared-private-deployment.md). Same
  backend; different operational policy.
- **Client access from HPC.** HPC jobs are TCKDB API clients, not a
  separate deployment. Patterns and Apptainer/Singularity status are
  in [client-access-from-hpc.md](client-access-from-hpc.md).
  Apptainer image build files are deferred packaging.
- **Native install (advanced infrastructure strategy).** Documented in
  [native-advanced.md](native-advanced.md); installer automation is
  still out of scope.

---

## Reference

- [docs/roadmaps/local-v0-deployment-spec.md](../roadmaps/local-v0-deployment-spec.md) — milestone spec
- [docs/decisions/0023-local-offline-and-hosted-submission-model.md](../decisions/0023-local-offline-and-hosted-submission-model.md) — architecture decision
- [docs/decisions/0022-auth-and-roles-v1.md](../decisions/0022-auth-and-roles-v1.md) — auth model used by local and hosted alike
- [Deployment guide overview](README.md) — the full taxonomy
- [Shared private deployment](shared-private-deployment.md) — same backend on a shared lab/group machine
- [Client access from HPC](client-access-from-hpc.md) — HPC jobs as TCKDB clients; Apptainer/Singularity is deferred packaging
- [Native advanced install](native-advanced.md) — infrastructure strategy when Docker/Apptainer aren't available
