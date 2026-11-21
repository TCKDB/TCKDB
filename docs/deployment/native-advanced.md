# Without Docker

Run TCKDB with no containers: Postgres with RDKit, the Python backend and an
object store installed on the host. It serves the same setups as the Docker
route, a personal instance or a [lab server](shared-private-deployment.md),
with the same schema, API and accounts.

Use it only when Docker is not an option: institution policy, an
unsupported OS, or a lab that already runs Postgres with RDKit and wants
TCKDB to use it. Otherwise [Self-host](README.md) is far less work.

## What you install

| Component | Why | Where from |
|---|---|---|
| Postgres with the RDKit cartridge | Identity tables use RDKit `mol` columns and chemistry-aware indexes. | [See below](#postgres-with-rdkit) |
| The `tckdb_env` conda environment | Runs the API, the worker and Alembic. | `backend/environment.yml` |
| An S3-compatible object store | Holds uploaded files such as ESS logs. | SeaweedFS, AWS S3, Google Cloud Storage, a lab S3 service, MinIO |
| A service manager | Keeps the API and worker running across crashes and reboots. | systemd, supervisord, runit |
| A reverse proxy, if anyone else connects | TLS, cookies and `X-API-Key` passed through. | nginx, Caddy, Traefik; see [Lab server](shared-private-deployment.md#reverse-proxy) |

The hard parts are Postgres with RDKit and a durable object store. Everything
above them works as in the Docker route.

## Postgres with RDKit

TCKDB needs the RDKit cartridge inside the database, not only the RDKit
Python library. Options, from least to most work:

1. **Run only the database in a container**, with podman or plain
   `docker run`, from `informaticsmatters/rdkit-cartridge-debian`.
2. **Install a distribution package** that bundles RDKit for Postgres, if
   yours has one, and run `CREATE EXTENSION rdkit;`.
3. **Build the cartridge from source** against your Postgres headers. This
   depends closely on the Postgres and RDKit versions; follow RDKit's own
   build documentation.

Then create the database and a login:

```sql
CREATE DATABASE tckdb_dev ENCODING 'UTF8' TEMPLATE template0;
\c tckdb_dev
CREATE EXTENSION IF NOT EXISTS rdkit;
CREATE USER tckdb WITH PASSWORD '...';
GRANT ALL PRIVILEGES ON DATABASE tckdb_dev TO tckdb;
```

Keep `ENCODING 'UTF8' TEMPLATE template0`. A bare `CREATE DATABASE` copies
`template1`, whose encoding may be `SQL_ASCII`, which stores invalid bytes
without complaint. See
[Troubleshooting](troubleshooting.md#the-cluster-template-disagrees-with-the-production-database).
The first migration expects the `rdkit` extension to exist already.

For a shared instance, split the database owner from the API's login as
described in [database roles](../../backend/docs/deployment/database_roles.md);
`scripts/configure_database_roles.py` does it.

## Python environment

```bash
mamba env create -n tckdb_env -f backend/environment.yml
conda run -n tckdb_env pip install -e schemas/python/tckdb-schemas
conda run -n tckdb_env pip install -e ./backend
```

Install nothing else into `tckdb_env` by hand; `environment.yml` is the
source of truth.

Set the settings in the environment or an env file. Start from
[`examples/deployment/lab-server.env.example`](../../examples/deployment/lab-server.env.example):

```bash
export DB_HOST=127.0.0.1 DB_PORT=5432 DB_NAME=tckdb_dev DB_USER=tckdb DB_PASSWORD='...'
export S3_ENDPOINT_URL=https://s3.lab.example.org S3_BUCKET=tckdb-artifacts S3_REGION=us-east-1
export S3_ACCESS_KEY=... S3_SECRET_KEY=...
export AUTH_ALLOW_OPEN_REGISTRATION=false
```

## Object store

Pick one:

- **SeaweedFS as a host service.** One binary:
  `weed mini -dir=<dir> -s3.port=9000`, run under systemd, with its data
  directory in your backups.
    - Export `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` (the same
      values as `S3_ACCESS_KEY` and `S3_SECRET_KEY`). Without them it accepts
      unauthenticated requests.
    - Set `S3_ENDPOINT_URL=http://127.0.0.1:9000` and create the
      `$S3_BUCKET` bucket.
    - Its filer (8888), master (9333), volume server (9340) and their gRPC
      ports (18888, 19333, 19340, 19000) take no credentials, and over gRPC
      anyone who connects can delete objects. Bind everything to loopback
      with `-ip=127.0.0.1 -ip.bind=127.0.0.1`. On 4.47, as measured, that
      closes every port but the admin gRPC port 33646, which listens on all
      interfaces even with `-admin.ui=false`; block it in the host firewall.
    - Add the `WEED_JWT_*` keys and `WEED_GUARD_WHITE_LIST=127.0.0.1,@` from
      the `seaweedfs` service in `docker-compose.yml` to lock the HTTP ports
      as well.
    - Set `S3_SEAWEEDFS_MASTER_URL=http://127.0.0.1:9333` so the API can tell
      a full store from a fault.
- **A lab S3 service.** Point `S3_ENDPOINT_URL` at it and get a bucket and
  keys.
- **AWS S3.** Set `S3_ENDPOINT_URL=https://s3.<region>.amazonaws.com` and a
  matching `S3_REGION`. Don't leave `S3_ENDPOINT_URL` unset: its default is
  `http://localhost:9000`.
- **Google Cloud Storage.** Use `S3_ENDPOINT_URL=https://storage.googleapis.com`
  with HMAC keys as `S3_ACCESS_KEY` and `S3_SECRET_KEY`.
- **An existing MinIO.** Works the same as SeaweedFS.

Back up the object store as well as the database.

## Migrate and create an administrator

From `backend/`:

```bash
conda run -n tckdb_env alembic upgrade head
TCKDB_BOOTSTRAP_PASSWORD='...' \
  conda run -n tckdb_env python scripts/bootstrap_admin.py \
    --username admin --email admin@lab.example.org
```

Before upgrading a database that holds data, read the
[migration runbook](../../backend/docs/deployment/migrations.md). Other
accounts are created the same way; see
[Admin accounts and API keys](admin_auth_quickstart.md).

## Run the API and the worker

From `backend/`, or with `WorkingDirectory=.../backend` in the unit:

```bash
conda run -n tckdb_env uvicorn main:app --host 127.0.0.1 --port 8010
```

Keep one Uvicorn worker: the rate limiter counts in process memory. The
upload worker runs inside the API while `TCKDB_INLINE_WORKER=true`. To run it
on its own, set `TCKDB_INLINE_WORKER=false` and start:

```bash
conda run -n tckdb_env python -m app.workers.upload_worker
```

[`examples/deployment/systemd/tckdb-api.service`](../../examples/deployment/systemd/tckdb-api.service)
is a unit for the API on the host. It starts with or without Docker; add
your Postgres service to its `After=` line. Adapt the paths, the user and the
env file. A worker unit is the
same with the worker module in `ExecStart`.

## Things that go wrong

- **RDKit cartridge drift.** Upgrading Postgres or the cartridge in place is
  manual, and mismatched versions can silently corrupt the chemistry
  indexes. Pin both.
- **Forgotten files.** Without Docker volumes to point at, it is easy to back
  up the database and not the object store.
- **Open registration.** Set `AUTH_ALLOW_OPEN_REGISTRATION=false` as soon as
  anyone else can reach the API, and set `DEPLOYMENT_MODE` to match; see the
  [Production checklist](production_checklist.md).
- **Postgres upkeep.** Upgrades, WAL retention, vacuuming and disk space are
  now yours.
- **No restart after a reboot.** A bare `uvicorn` stays down. Use a service
  manager.
