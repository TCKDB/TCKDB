# Run from source

Run the API from your checkout, with Postgres and SeaweedFS in Docker. Use
this to change TCKDB's code or run its tests. To run TCKDB without changing
it, [Self-host](README.md) is quicker.

The short version, with a picture, is [Install locally](../start/getting-started.md).
This page has the details.

## What you need

- Docker with Compose v2 (`docker compose version`).
- Miniforge, Conda or Mamba.
- `curl` for the checks below.

Two working directories:

- **The repository root** for `docker compose` and `make`.
- **`backend/`** for `alembic`, `uvicorn`, the worker and anything under
  `scripts/`.

The commands use `conda run -n tckdb_env ...`. Drop the prefix if the env is
active.

## 1. Set up

From the repository root:

```bash
cp .env.example .env
cp backend/.env.local.example backend/.env
mamba env create -n tckdb_env -f backend/environment.yml
conda run -n tckdb_env pip install -e schemas/python/tckdb-schemas
conda run -n tckdb_env pip install -e "./backend[dev]"
```

`.env` holds the values `docker-compose.yml` needs. `backend/.env` holds the
API's settings. `backend/.env.local.example` turns on open registration and
runs the upload worker inside the API.

## 2. Start Postgres and SeaweedFS

```bash
make up
```

This starts the `db` and `seaweedfs` containers and runs
`alembic upgrade head` against `tckdb_dev`. Both containers publish their
ports on 127.0.0.1 only: Postgres on `DB_PORT` (default 5432) and the S3 API
on `S3_PORT` (default 9000). The API on the host reaches them there.

| Service | Image | Purpose |
|---|---|---|
| `db` | `informaticsmatters/rdkit-cartridge-debian` | Postgres with the RDKit cartridge |
| `seaweedfs` | `chrislusf/seaweedfs` | S3-compatible file storage |

The first migration seeds reference data such as reaction families. Before
upgrading a database that holds data you care about, read the
[migration runbook](../../backend/docs/deployment/migrations.md).

## 3. Create an administrator

From `backend/`:

```bash
TCKDB_BOOTSTRAP_PASSWORD='change-me' \
  conda run -n tckdb_env python scripts/bootstrap_admin.py \
    --username admin --email admin@example.local
```

A new account needs a password, from `TCKDB_BOOTSTRAP_PASSWORD` or
`--password`. The environment variable keeps it out of your shell history.
The script is idempotent: run it again and it reactivates the account. It
changes an existing user's role only with `--force-role-change`. `make admin`
runs the same script; set `TCKDB_BOOTSTRAP_PASSWORD` first.

## 4. Start the API

```bash
make api
```

This runs `uvicorn main:app --host 127.0.0.1 --port 8010` from `backend/` in
the foreground. With `TCKDB_INLINE_WORKER=true` (set by
`backend/.env.local.example`), the upload worker runs inside the API. To run
it on its own, set `TCKDB_INLINE_WORKER=false` and start it in a second
terminal, from `backend/`:

```bash
conda run -n tckdb_env python -m app.workers.upload_worker
```

## 5. Check it

```bash
make doctor
curl -sf http://127.0.0.1:8010/api/v1/health    # {"status":"ok"}
```

Log in and mint an API key. Keys can be minted only with a session cookie,
never with another key:

```bash
curl -sf -c cookies.txt -H "Content-Type: application/json" \
  -X POST http://127.0.0.1:8010/api/v1/auth/login \
  -d '{"username": "admin", "password": "change-me"}'

curl -sf -b cookies.txt -H "Content-Type: application/json" \
  -X POST http://127.0.0.1:8010/api/v1/auth/api-keys \
  -d '{"label": "local-dev"}'
```

The response holds the key (`tck_...`) once. Save it:

```bash
export TCKDB_API_KEY='tck_...'
```

`backend/scripts/tckdb_auth.sh login-create-key` does both steps and writes
the key to a file; see [Admin accounts and API keys](admin_auth_quickstart.md).

With open registration on, new users can also register themselves:

```bash
curl -sf -c cookies.txt -H "Content-Type: application/json" \
  -X POST http://127.0.0.1:8010/api/v1/auth/register \
  -d '{"username": "alice", "password": "change-me-too", "email": "alice@example.local"}'
```

Uploads without a key get `401`. With `-H "X-API-Key: $TCKDB_API_KEY"` they
reach the upload route and are recorded under your account.

Open registration is fine while the API listens on 127.0.0.1. Turn it off
(`AUTH_ALLOW_OPEN_REGISTRATION=false`) as soon as anyone else can reach the
API; see [Lab server](shared-private-deployment.md).

## Stop and reset

```bash
make down     # stop the containers, keep the data
make reset    # delete the database and files, start again, migrate
```

After `make reset`, create the administrator again. `make reset-login` does
that too, and mints a dev API key, while the API is running.

## Common problems

- **`alembic upgrade head` cannot connect.** Check the `db` container is
  healthy with `docker compose ps`.
- **Port 5432 is taken.** Stop the other Postgres, or set `DB_PORT` to a free
  port in both `.env` and `backend/.env`. The container still listens on 5432
  inside; only the host port moves.
- **`401` with a session cookie.** Make curl keep cookies
  (`-c cookies.txt -b cookies.txt`). Sessions expire; log in again.
- **`403` with a valid key.** A key has its owner's role, and some routes
  need a curator or administrator.
- **The artifact bucket is missing.** The `seaweedfs` service creates
  `S3_BUCKET` each time it starts. On MinIO or an external store, create it
  with any S3 client, for example
  `aws --endpoint-url http://127.0.0.1:9000 s3 mb s3://tckdb-artifacts`.

More in [Troubleshooting](troubleshooting.md).
