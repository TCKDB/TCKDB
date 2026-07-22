# Native install (advanced infrastructure strategy)

Native install is one of several
[infrastructure strategies](README.md#infrastructure-strategies) for
bringing up the TCKDB application — alongside the Docker Compose
quick-start and (deferred) Apptainer packaging. It is an **advanced**
strategy: operators provision PostgreSQL+RDKit, Python, and object
storage themselves and run the standard TCKDB commands against them.

> **This is a strategy, not a separate deployment scenario.** A
> natively-installed TCKDB serves the same deployment scenarios as a
> Docker-based one: a [single-machine private deployment](local-v0.md)
> or a [shared private deployment](shared-private-deployment.md). What
> changes is the install method, not the application.
>
> Read this only if Docker (and the deferred Apptainer path) are off
> the table. If Docker works, use the Docker Compose quick-start in
> [local-v0.md](local-v0.md) — re-used unchanged for shared
> deployments per [shared-private-deployment.md](shared-private-deployment.md).
> For HPC, see [client-access-from-hpc.md](client-access-from-hpc.md);
> HPC is a client environment, not a deployment.

This is a **documentation-only** page. There is no installer script,
no system packages, and no native automation here.

---

## Overview

A native install brings TCKDB up by:

1. Installing **PostgreSQL with the RDKit cartridge** as a host service.
2. Installing the **`tckdb_env` conda environment** for the Python
   backend.
3. Providing **object/artifact storage** (MinIO, AWS S3, or another
   S3-compatible service).
4. Running the standard Alembic migrations against the host DB.
5. Bootstrapping the first admin via `backend/scripts/bootstrap_admin.py`.
6. Starting the API (`uvicorn main:app`) and, optionally, the upload
   worker (`python -m app.workers.upload_worker`) under a service
   manager you choose. Both are launched from `backend/`.

Everything above the database and storage layer is identical to a
Docker-based deployment ([single-machine](local-v0.md) or
[shared private](shared-private-deployment.md)). The hard part of a
native install is steps 1 and 3 — provisioning PostgreSQL+RDKit and
durable object storage on a host — which the Docker quick-start
otherwise hides.

---

## When native install might be appropriate

- Docker is unavailable (institution policy, OS support, security
  posture).
- Apptainer/Singularity is unavailable (and image support is deferred —
  see [client-access-from-hpc.md §Apptainer/Singularity (deferred packaging)](client-access-from-hpc.md#apptainersingularity-deferred-packaging)).
- The lab already runs and manages a PostgreSQL+RDKit service for
  other purposes and wants TCKDB to attach to it.
- The host is a single workstation under one user's full admin
  control.

If none of those apply, choose Docker.

---

## Required components

| Component | Why | Source |
|-----------|-----|--------|
| **PostgreSQL** (recent stable) with the **RDKit cartridge** loaded | Identity tables use RDKit `mol` columns and chemistry-aware indexes. | [Build PostgreSQL+RDKit yourself](#postgresql--rdkit-cartridge), or use the upstream Docker image (`informaticsmatters/rdkit-cartridge-debian`) if Docker is acceptable for this *one* component. |
| **Conda / Miniforge** with the project's `tckdb_env` environment | The backend, worker, and Alembic CLI all run from this env. | Project repo (`environment.yml` / equivalent). |
| **Object / artifact storage** (S3-compatible) | Artifact uploads (logs, geometries) write through the object store. | MinIO, AWS S3, lab-managed S3, or any S3 API-compatible service. |
| **Service manager** (systemd, supervisord, runit, …) | To keep `uvicorn` and the upload worker running. | OS-native. |
| **Reverse proxy** (only if exposing the API beyond `127.0.0.1`) | TLS termination, cookie + `X-API-Key` passthrough. | nginx / Caddy / Traefik, etc. See [shared-private-deployment.md §Reverse proxy basics](shared-private-deployment.md#reverse-proxy-basics). |

---

## PostgreSQL + RDKit cartridge

This is the part that makes native install hard. TCKDB depends on the
RDKit cartridge being available *inside* the database, not just as a
Python library.

Options, roughly in increasing order of operational pain:

1. **Use the upstream RDKit-cartridge container.** Pull
   `informaticsmatters/rdkit-cartridge-debian` and run it under
   whatever container runtime you do have (podman, plain `docker run`
   on a workstation). This is the lowest-effort native-ish path.
2. **Use a distro package that bundles RDKit.** A few distributions
   ship `postgresql-rdkit` (or similar). If yours does, install it
   alongside Postgres and `CREATE EXTENSION rdkit;` in the database.
3. **Build the cartridge from source.** Install Postgres dev headers,
   build RDKit with the cartridge target, install the resulting
   shared object into the Postgres extension directory. This is
   deeply version-sensitive (Postgres major version × RDKit version)
   and is **not** documented in detail here. If you must, follow the
   upstream RDKit build documentation.

After the cartridge is installed:

```sql
CREATE DATABASE tckdb_dev;
\c tckdb_dev
CREATE EXTENSION IF NOT EXISTS rdkit;
CREATE USER tckdb WITH PASSWORD '…';
GRANT ALL PRIVILEGES ON DATABASE tckdb_dev TO tckdb;
```

The TCKDB initial migration assumes the extension is already
available; do not skip the `CREATE EXTENSION` step.

---

## Python backend environment

The repo's `tckdb_env` conda environment is the supported Python
runtime. The Docker Compose quick-start runs the API and worker from
this env on the host already; native install does the same.

```bash
# Install Miniforge or your preferred conda distribution.
conda env create -f environment.yml          # or your project's manifest
conda run -n tckdb_env python -V
```

Once a standardized Python dependency manifest is in place,
backend container packaging will land too — at which point native
install becomes even more clearly the *advanced* path.

Set the standard environment variables before running migrations or
starting the API:

```bash
export DB_USER=tckdb
export DB_PASSWORD='…'
export DB_NAME=tckdb_dev
export DB_HOST=127.0.0.1
export DB_PORT=5432
export S3_ENDPOINT_URL=https://s3.lab.example.org
export S3_ACCESS_KEY=…
export S3_SECRET_KEY=…
export S3_BUCKET=tckdb-artifacts
export S3_REGION=us-east-1
export AUTH_ALLOW_OPEN_REGISTRATION=false
```

Use the shared-deployment env example as a starting point:
[`examples/deployment/lab-server.env.example`](../../examples/deployment/lab-server.env.example).

---

## Object / artifact storage

TCKDB writes artifacts (input/output logs, geometries, parsed files)
through an S3-compatible interface. A native install needs *some*
object store.

Choose one of:

- **MinIO** as a host-managed binary. Single Go binary, runs under
  systemd, persists to a directory you back up. Configure
  `S3_ENDPOINT_URL=http://127.0.0.1:9000` and create a bucket named
  `$S3_BUCKET`.
- **Lab-managed S3.** Point `S3_ENDPOINT_URL` at the institutional
  endpoint; provision a bucket and credentials.
- **AWS S3.** Omit `S3_ENDPOINT_URL`; use real AWS credentials.

Database backups alone are **not** sufficient if artifacts live
outside the database. Plan artifact backups too — see
[shared-private-deployment.md §Backup and restore basics](shared-private-deployment.md#backup-and-restore-basics).

---

## Migrations

Alembic migrations are run identically to every other infrastructure
strategy. From `backend/`:

```bash
cd backend
conda run -n tckdb_env alembic upgrade head
```

The repo maintains an ordered Alembic revision chain. The command applies every
pending revision through the current head; migrations require the database to
exist and the RDKit cartridge to be loaded. Review the
[migration runbook](../../backend/docs/deployment/migrations.md) before
upgrading a database that already contains data.

---

## Bootstrap admin

`backend/scripts/bootstrap_admin.py` is the supported account-seeding
tool on any deployment, including native. Run from `backend/`:

```bash
TCKDB_BOOTSTRAP_PASSWORD='change-me' \
  conda run -n tckdb_env python scripts/bootstrap_admin.py \
    --username admin \
    --email admin@lab.example.org \
    --full-name "Lab Admin"
```

The script is idempotent — re-run it with the same username to
promote/reactivate. Subsequent users can be seeded the same way and
promoted via admin endpoints.

---

## API and worker startup

Same commands as the reference path; the only thing that changes is
that you wire them under a service manager so they restart on
failure.

### API

> Run from `backend/` (or set `WorkingDirectory=…/backend` in your
> service unit) so `main:app` resolves.

```bash
conda run -n tckdb_env uvicorn main:app \
  --host 127.0.0.1 --port 8010
```

Bind to `127.0.0.1` and front it with a reverse proxy that
terminates TLS and forwards `X-API-Key` and session cookies — see
[shared-private-deployment.md §Reverse proxy basics](shared-private-deployment.md#reverse-proxy-basics).

### Upload worker

If you want the worker to run separately from the API process (for
isolation), set `TCKDB_INLINE_WORKER=false` in the environment and
start it from `backend/`:

```bash
conda run -n tckdb_env python -m app.workers.upload_worker
```

### Example systemd unit (sketch)

Sketch only — adapt paths, user, and env file location to your host.

```ini
# /etc/systemd/system/tckdb-api.service
[Unit]
Description=TCKDB API
After=network.target postgresql.service

[Service]
Type=simple
User=tckdb
WorkingDirectory=/opt/tckdb/backend
EnvironmentFile=/etc/tckdb/lab-server.env
ExecStart=/opt/conda/bin/conda run -n tckdb_env \
  uvicorn main:app --host 127.0.0.1 --port 8010
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

A matching `tckdb-worker.service` follows the same pattern with
`ExecStart` pointing at the worker module.

---

## Caveats

Things that bite people on native installs:

- **RDKit cartridge version drift.** Upgrading PostgreSQL or the
  cartridge in place is a manual operation; mismatched versions can
  silently corrupt the schema's chemistry-aware indexes. Pin both.
- **Conda env drift.** The `tckdb_env` environment is the source of
  truth for Python dependencies. Don't `pip install` into it ad-hoc.
- **Backup blindness.** Without the Docker volume layout to point at,
  it's easy to forget that artifacts live outside the DB. Cover both
  PostgreSQL and the object store. See
  [shared-private-deployment.md §Backup and restore basics](shared-private-deployment.md#backup-and-restore-basics).
- **TLS, cookies, and `X-API-Key` forwarding.** Same considerations
  as a Docker-based shared deployment. See
  [shared-private-deployment.md §Reverse proxy basics](shared-private-deployment.md#reverse-proxy-basics).
- **Open registration on a reachable native install.** Same hazard as
  any reachable shared deployment: set
  `AUTH_ALLOW_OPEN_REGISTRATION=false` if more than one user can reach
  the API.
- **Self-managed PostgreSQL.** You are now responsible for upgrades,
  WAL retention, vacuuming, and disk monitoring. None of that is
  TCKDB-specific, but TCKDB will not paper over it for you.
- **Service-manager restarts.** A bare `uvicorn` will not come back
  up after a reboot. Use systemd / supervisord / equivalent.

---

## Why the Docker quick-start is preferred

- The RDKit cartridge image is the painful step on native installs;
  the Docker quick-start skips it.
- The reference Docker Compose file (`docker-compose.yml`) is
  tested as part of the documented single-machine /
  [shared private deployment](shared-private-deployment.md) flow.
  Native install is documented but not part of that tested path.
- Backups, networking, and service supervision are simpler when the
  DB and object store are isolated container processes with named
  volumes.
- A standardized backend container image is on the roadmap.
  When it ships, a Docker-based shared deployment becomes a single
  `docker compose up` deployment — the gap between native and
  Docker will widen, not narrow.

Native install remains valid for advanced operators, but it is not
the path the project's tooling is optimized for.

---

## Non-goals

- No native installer automation (no `.deb`, `.rpm`, `brew`,
  installer script, or Ansible role).
- No prescribed RDKit cartridge build recipe — the upstream project
  documents that better than this repo can.
- No production SRE runbook (HA, replication, observability).
- No Kubernetes manifests.
- No Apptainer image definitions.
- No native frontend deployment.
- No service accounts.
- No raw database synchronization.

---

## See also

- [Deployment guide overview](README.md)
- [Single-machine private deployment](local-v0.md)
- [Shared private deployment](shared-private-deployment.md)
- [Client access from HPC](client-access-from-hpc.md)
- [Generic client targeting](../clients/generic-client-targeting.md)
- DR-0022 — Auth and Roles v1
- DR-0023 — Local/Offline and Hosted Submission Model
