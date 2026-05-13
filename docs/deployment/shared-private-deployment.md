# Shared private deployment

A **shared private deployment** is the same TCKDB backend deployed on a
shared machine — typically a lab service node — for use by a research
group. It runs the same schema, the same API, and the same auth model
as a [single-machine private deployment](local-v0.md) and as the hosted
community instance. The differences are **operational policy**: closed
registration, per-user API keys, backups, reverse proxy, access
control.

> **Not a separate mode.** This scenario is sometimes called a
> "lab-server" deployment in older docs and code. It is not a fork, a
> lite schema, or a separate codebase. It is the same TCKDB
> application with different operational policy. See
> [the deployment guide overview](README.md) and
> [DR-0023](../decisions/0023-local-offline-and-hosted-submission-model.md).

This is a deployment-guidance document. It does **not** ship new
runtime code, container images, or installer automation. See
[Non-goals](#non-goals).

---

## Where this scenario fits

| Scenario | Audience | Reachability | Registration |
|---|---|---|---|
| [Single-machine private](local-v0.md) | One user, laptop/dev | `127.0.0.1` | Open registration acceptable |
| **Shared private (this doc)** | A lab/group | Lab/internal URL | **Closed** registration, seeded accounts |
| Hosted community | Public | Public URL | Operator-managed |

It is the same application in all three rows. Only operational policy
changes.

---

## Overview

A shared private deployment lets a research group:

- run a single shared PostgreSQL+RDKit database for the whole lab,
- run a single backend API and (optionally) upload worker,
- expose the API on a lab/internal URL,
- require authenticated writes,
- issue **per-user** API keys for scripts, notebooks, HPC jobs, and
  workflow tools,
- keep data private to the lab while preserving the option to
  contribute selected records upstream via the
  [manual local-to-hosted flow](../contribution-bundles/manual-local-to-hosted-v0.md).

The reference **infrastructure strategy** is Docker Compose on the
service node — the same stack as
[local-v0.md §Start the local stack](local-v0.md#1-start-the-local-stack).
Other strategies (managed PostgreSQL+RDKit, [native install](native-advanced.md))
are valid; the deployment scenario does not change.

---

## Recommended architecture

```text
              ┌────────────────────────────────────────────┐
              │           Lab service node                 │
              │                                            │
  HTTPS  ┌────┼─►  Reverse proxy (nginx / Caddy / Traefik) │
  443    │    │     │  forwards X-API-Key + cookies        │
         │    │     ▼                                      │
         │    │   TCKDB API (uvicorn, host conda env)      │
         │    │     │                                      │
  HTTP   │    │     ├─► PostgreSQL + RDKit (Docker)        │
  client │    │     └─► MinIO / S3 (Docker or external)    │
  jobs   │    │                                            │
         └────┼─►  Upload worker (inline or separate proc) │
              └────────────────────────────────────────────┘

              ▲
              │  HTTP API (X-API-Key)
              │
   Lab users / scripts / notebooks / HPC compute jobs
```

Key properties:

- **One** PostgreSQL+RDKit database; one API; optionally one worker.
- Clients (lab scripts, HPC jobs, notebooks, future CLIs) use the
  generic `base_url` + `api_key` model documented in
  [generic-client-targeting.md](../clients/generic-client-targeting.md).
  HPC jobs are a [client environment](client-access-from-hpc.md), not
  a separate deployment — they call this deployment's API.
- A reverse proxy is recommended for TLS termination, hostname
  routing, and access control. The backend itself binds plain HTTP.

---

## When this scenario applies

Choose a shared private deployment when:

- Multiple users in a group need to share scientific records.
- HPC or batch jobs need a stable upload target reachable from compute
  nodes (see [client-access-from-hpc.md](client-access-from-hpc.md)).
- The lab wants to keep raw records private but still mint contribution
  bundles for hosted/community TCKDB on demand.
- A single-machine private deployment is no longer sufficient.

Stay on a [single-machine private deployment](local-v0.md) if one user
on one host is sufficient. Use the hosted community instance for
records that are ready for public contribution (via the
[manual local-to-hosted flow](../contribution-bundles/manual-local-to-hosted-v0.md)).

---

## Services involved

The same services as a single-machine deployment, deployed on a shared
host:

| Service       | Role                                                  | Where it runs                       |
|---------------|-------------------------------------------------------|-------------------------------------|
| PostgreSQL+RDKit | Identity, results, provenance, moderation          | Docker (`docker-compose.yml`) — or a managed PostgreSQL+RDKit service, or a [native install](native-advanced.md) |
| MinIO / S3    | Artifact / object storage                              | Docker, or lab-managed S3           |
| Backend API   | FastAPI, schema, auth, upload routes                   | Host conda env (`tckdb_env`)        |
| Upload worker | Async ingestion (optional; can run inline in the API) | Host conda env, or separate proc    |
| Reverse proxy | TLS, routing, header forwarding, access control       | Host or separate appliance          |

The backend and worker run on the host because the repo has not yet
standardized a Python dependency manifest; see
[backend-container-packaging-spec.md](../roadmaps/backend-container-packaging-spec.md).

---

## Authentication and registration policy

Auth is the model from
[DR-0022](../decisions/0022-auth-and-roles-v1.md):

- **Sessions** for humans (login, mint API keys).
- **API keys** for clients (`X-API-Key` header), per-user.

For any deployment reachable beyond `127.0.0.1`, set:

```env
AUTH_ALLOW_OPEN_REGISTRATION=false
```

Reasoning: with the API reachable on the lab network, open
registration is a soft account-creation backdoor. Disable it and seed
every account explicitly. Single-machine private deployments may keep
open registration on because the API is bound to `127.0.0.1` and
unreachable from outside.

A starter set of policies for shared deployments:

- Disable public registration (above).
- Use bootstrap admin to create the first account; have that admin
  create the rest, or extend the bootstrap script per user.
- Issue **one API key per user**. Do not share a lab-wide key.
- Treat keys as bearer credentials — see
  [API keys for users and scripts](#api-keys-for-users-and-scripts) and
  [generic-client-targeting.md §API-key safety](../clients/generic-client-targeting.md#api-key-safety).

---

## Bootstrap admin

The first account on a closed instance is created with the same
`backend/scripts/bootstrap_admin.py` used everywhere else. The script
is idempotent — running it again with the same username promotes (and
reactivates) the existing account. Run it from `backend/`:

```bash
cd backend
TCKDB_BOOTSTRAP_PASSWORD='change-me' \
  conda run -n tckdb_env python scripts/bootstrap_admin.py \
    --username admin \
    --email admin@lab.example.org \
    --full-name "Lab Admin"
```

Subsequent users can be seeded the same way (the script accepts any
username; promote them to admin or curator after the fact via the
admin endpoints):

```bash
conda run -n tckdb_env python scripts/bootstrap_admin.py \
  --username alice --email alice@lab.example.org
```

A regular admin "create user" flow is future work; until it exists, the
bootstrap script is the supported seeding path on a closed instance.

---

## API keys for users and scripts

Once an account exists, the user logs in once (browser or curl with
`-c cookies.txt`) and mints API keys for their scripts/notebooks/HPC
jobs:

```bash
curl -sf -c cookies.txt -H "Content-Type: application/json" \
  -X POST "https://tckdb.lab.example.org/api/v1/auth/login" \
  -d '{"username": "alice", "password": "..."}'

curl -sf -b cookies.txt -H "Content-Type: application/json" \
  -X POST "https://tckdb.lab.example.org/api/v1/auth/api-keys" \
  -d '{"label": "alice-hpc-arc"}'
```

Policy guidance for shared deployments:

- **Per user**, not per lab. A shared lab-wide key destroys
  attribution, makes revocation all-or-nothing, and turns one leak into
  a lab-wide credential incident.
- Use **distinct labels** per environment (`alice-laptop`,
  `alice-hpc`, `alice-notebook`) so revoking a single environment is
  straightforward.
- Keys inherit the owning user's role. Mint keys from the
  lowest-privilege account that satisfies the use case.
- Rotate proactively: mint new key, deploy, then revoke the old one.
  The API supports multiple concurrent keys per user precisely so that
  rotation has no downtime.

Full API-key surface and safety notes:
[generic-client-targeting.md §API keys](../clients/generic-client-targeting.md#api-keys).

---

## Reverse proxy basics

The TCKDB API is plain HTTP and does not terminate TLS. For a shared
deployment, sit it behind a reverse proxy that:

1. Terminates TLS using a lab-issued or institutional certificate.
2. Forwards the **`X-API-Key`** header to the backend untouched.
3. Preserves the **session cookie** issued by `/auth/login` so users
   can mint API keys from the browser.
4. Sets `X-Forwarded-For` / `X-Forwarded-Proto` so logs and any future
   rate-limit logic see real client info.

Illustrative nginx snippet (not a production-complete config):

```nginx
server {
    listen 443 ssl;
    server_name tckdb.lab.example.org;

    ssl_certificate     /etc/ssl/lab/tckdb.crt;
    ssl_certificate_key /etc/ssl/lab/tckdb.key;

    location /api/ {
        proxy_pass http://127.0.0.1:8010/api/;
        proxy_set_header Host              $host;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Critical: forward the API-key header and preserve cookies.
        proxy_set_header X-API-Key         $http_x_api_key;
        proxy_pass_request_headers         on;
    }
}
```

Caddy and Traefik handle header/cookie passthrough by default; the
explicit `proxy_set_header X-API-Key …` line above is a safety belt
for nginx setups that strip unknown headers.

Things to avoid:

- Stripping or rewriting `X-API-Key` in the proxy.
- Buffering / rewriting `Set-Cookie` so session login appears to "work"
  but no cookie reaches the browser.
- Exposing internal admin/debug surfaces (e.g. MinIO console, DB
  admin tooling) on the public proxy.

---

## Firewall, VPN, and access control

A shared private deployment is private. Treat it like one:

- Prefer placing the deployment behind the lab/institutional VPN or on
  an internal-only DNS name. A public-internet-reachable shared private
  deployment is almost never necessary.
- If the proxy must be public-facing, restrict source IPs to the
  ranges that actually need access (HPC login nodes, lab subnets).
- Block direct access to PostgreSQL (5432) and MinIO (9000/9001) at
  the host firewall — only the proxy needs to reach the API, and only
  the API needs to reach the DB and object store.
- Do not put the bootstrap admin password in shared shell history;
  use the `TCKDB_BOOTSTRAP_PASSWORD` env var.
- Separate keys per user; revoke on personnel changes.

---

## Backup and restore basics

A shared deployment is the system of record for data the lab cares
about. Back it up. This section covers the minimum viable approach;
full SRE hardening is out of scope.

There are **three** distinct things to back up:

1. **The database** — identity, results, provenance, submission state.
2. **The artifact / object store** — log files, geometries, anything
   stored outside the DB. **Database backup alone is insufficient if
   artifacts are stored outside the DB.**
3. **Configuration** — `.env`, reverse-proxy config, and any
   institution-specific overrides.

### Database

Use `pg_dump`. The output is plain SQL and survives major Postgres
upgrades better than a binary dump:

```bash
# From the service host, with $DATABASE_URL pointing at the lab DB.
pg_dump "$DATABASE_URL" > tckdb-backup-$(date +%F).sql
```

Schedule it via cron / systemd timer; rotate older copies; verify the
output is non-empty.

### Artifacts / object store

If you use the bundled MinIO container, mirror the `/data` volume to a
backup target. With `mc` (MinIO client):

```bash
mc mirror --overwrite local/tckdb-artifacts s3-backup/tckdb-artifacts
```

If your lab uses a managed S3 (institutional or AWS), rely on its
versioning/lifecycle features, but still verify periodically.

### Configuration

Treat `.env`, reverse-proxy config, and any operational scripts as
config-as-code. Keep them in a private git repo; never commit secrets
in plaintext.

### Restore order

When restoring from cold backup:

1. Restore configuration (`.env`, proxy config).
2. Bring up an empty PostgreSQL+RDKit container.
3. `psql … < tckdb-backup-YYYY-MM-DD.sql` to load the DB dump.
4. Restore the artifact/object store contents.
5. Start the API and worker; smoke-test with `GET /auth/me`.

Test a restore at least once before you need it. A backup you have
never restored is a hypothesis.

---

## Client configuration

Clients (lab scripts, notebooks, HPC jobs, workflow tools) target the
deployment using the generic two-value model:

```yaml
tckdb:
  base_url: "https://tckdb.lab.example.org/api/v1"
  api_key: "tck_replace_me"
```

Or via environment variables:

```bash
export TCKDB_BASE_URL="https://tckdb.lab.example.org/api/v1"
export TCKDB_API_KEY="tck_replace_me"
```

A starter env file is provided at
[`examples/deployment/lab-server.env.example`](../../examples/deployment/lab-server.env.example)
(server-side settings) and
[`examples/deployment/hpc-client.env.example`](../../examples/deployment/hpc-client.env.example)
(client-side settings for HPC/batch jobs).

Targeting model: [generic-client-targeting.md](../clients/generic-client-targeting.md).

---

## Relationship to hosted/community TCKDB

A shared private deployment is independent of the hosted community
instance. They run the same backend but they are separate deployments
with separate users, separate API keys, and separate data.

- Pointing a client at a different `base_url` does **not** sync, copy,
  or migrate data between deployments. See
  [generic-client-targeting.md §Targeting is not syncing](../clients/generic-client-targeting.md#targeting-is-not-syncing).
- The supported way to push selected records from a shared private
  deployment to hosted is the
  [manual local-to-hosted contribution flow](../contribution-bundles/manual-local-to-hosted-v0.md).
  A shared deployment qualifies as a "local instance" for that flow;
  bundle export reads from the shared DB, and submit/dry-run uses
  *hosted* API keys.

---

## Non-goals

This document deliberately does **not** include:

- a backend Dockerfile or container image (tracked in
  [backend-container-packaging-spec.md](../roadmaps/backend-container-packaging-spec.md));
- production-grade SRE (HA, failover, observability, secrets
  management, cookie hardening, full TLS playbooks);
- Kubernetes deployment;
- Apptainer/Singularity images (deferred packaging — see
  [client-access-from-hpc.md §Apptainer/Singularity (deferred packaging)](client-access-from-hpc.md#apptainersingularity-deferred-packaging));
- a native installer (advanced manual path documented in
  [native-advanced.md](native-advanced.md));
- service accounts;
- raw database synchronization;
- frontend deployment;
- a curator review UI for shared deployments.

---

## See also

- [Deployment guide overview](README.md)
- [Single-machine private deployment](local-v0.md)
- [Client access from HPC](client-access-from-hpc.md)
- [Native advanced install](native-advanced.md)
- [Generic client targeting](../clients/generic-client-targeting.md)
- [Manual local-to-hosted contribution flow](../contribution-bundles/manual-local-to-hosted-v0.md)
- [DR-0022 — Auth and Roles v1](../decisions/0022-auth-and-roles-v1.md)
- [DR-0023 — Local/Offline and Hosted Submission Model](../decisions/0023-local-offline-and-hosted-submission-model.md)
- [Implementation plan](../roadmaps/local-offline-and-hosted-submission-implementation-plan.md)
