# Self-hosting TCKDB

This guide describes how to run a TCKDB instance on a machine you control — a
cloud VM, a lab server, or a single-board computer (e.g. a Raspberry Pi). It
covers the two supported deployment shapes, when to pick each, and the
cross-cutting good practices (exposure, secrets, backups, migrations).

For applying schema migrations to an already-running database, see the
companion [`backend/docs/deployment/migrations.md`](../../backend/docs/deployment/migrations.md)
runbook — everything below assumes that flow for schema changes.

---

## The pieces

A TCKDB deployment has four moving parts:

| Piece | What it is | How it's usually run |
|---|---|---|
| **PostgreSQL (RDKit)** | the database | a container (stateful, pinned image) |
| **MinIO / S3** | artifact object store | a container |
| **API** | the FastAPI app (`uvicorn app.api.app:create_app --factory`) | container **or** native process |
| **Ingress** | public TLS + hostname | a Cloudflare tunnel or reverse proxy |

The database and object store are almost always containers — that's the easy,
uncontroversial part. The real choice is **how you run the API**.

---

## Choosing a shape

### Pattern A — Fully containerized (recommended default)

Everything (DB, MinIO, API, worker) is a container; `docker compose up` brings
the whole stack up. The API runs from a published image
(`ghcr.io/<org>/tckdbv2/tckdb-api`, built by
[`.github/workflows/build-api-image.yml`](../../.github/workflows/build-api-image.yml)).

- **Pros:** maximum reproducibility (the image pins Python + every dependency),
  one mental model, trivial to move to another host.
- **Cons:** you need to build/pull the API image. On **arm64** (a Raspberry Pi),
  building that image is slow — see the arm64 note below.
- **Use when:** you're on an amd64 cloud VM or lab server, or you're willing to
  build/pull an arm64 image on ARM hardware.

### Pattern B — Native API + containerized infra

DB and MinIO are containers; the API runs as a **native process** under
**systemd**, from a conda/micromamba environment. Ingress via a Cloudflare
tunnel.

- **Pros:** no API image to build — the scientific stack (RDKit, cantera)
  installs directly from conda-forge as prebuilt binaries, which is *much*
  smoother on arm64.
- **Cons:** the environment is a "snowflake" (built per-host), so it's less
  perfectly reproducible than an image.
- **Use when:** you're on constrained/ARM hardware (Raspberry Pi) where
  building a container image for the scientific stack is painful.

### The arm64 note (why Pattern B exists)

An **arm64** CPU (Raspberry Pi, Apple Silicon, AWS Graviton) cannot run an
**amd64** image natively — different instruction sets. It only works through an
**emulation layer** (QEMU on Linux, Rosetta on macOS), which is significantly
slower and more memory-hungry — fine for a quick test, wrong for a long-running
server. So on arm64 you want a **native arm64 image**, and *building* one on an
amd64 CI runner requires emulating arm64 (slow). Pattern B sidesteps the build
entirely by installing conda-forge's prebuilt arm64 binaries directly on the
host. If you do want Pattern A on arm64, build the image on a **native arm64
runner** (self-hosted or GitHub-hosted arm) rather than via QEMU.

---

## Pattern B walkthrough (native API under systemd)

This is the reference setup for a Raspberry Pi.

### 1. Infrastructure (containers)

```bash
cd <repo>
docker compose --env-file .env.pi up -d db minio
```

`.env.pi` holds the DB credentials, S3 keys, rate-limit config, cookie/security
settings, etc. **Keep it out of git** (it's ignored) and `chmod 600` it.

### 2. Environment (native)

Create the conda/micromamba env in a **normal env location — not inside the
repo**:

```bash
# Good: central env location.
micromamba create -n tckdb_env -f backend/environment.yml
micromamba run -n tckdb_env pip install -e backend
micromamba run -n tckdb_env pip install -e schemas/python/tckdb-schemas
```

> Do **not** use `micromamba create -p ./backend/y/envs/tckdb_env` — the `-p`
> (path prefix) flag puts the whole multi-gigabyte environment *inside the
> source tree*. Use `-n <name>` so it lands in `~/micromamba/envs/`. (A conda
> env cannot be relocated with `mv` — its files bake in absolute paths — so
> fixing a misplaced env means recreating it at the right location and
> updating the systemd `ExecStart` path.)

### 3. Migrations

```bash
cd backend
set -a; source ../.env.pi; set +a
micromamba run -n tckdb_env alembic upgrade head
```

(See the migrations runbook for the deployed-DB flow: back up first, check
`alembic current`, apply, verify.)

### 4. Run the API under systemd

`/etc/systemd/system/tckdb-api.service`:

```ini
[Unit]
Description=TCKDB API (uvicorn)
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=simple
User=<you>
WorkingDirectory=<repo>/backend
EnvironmentFile=<repo>/.env.pi
ExecStart=<env-path>/bin/uvicorn app.api.app:create_app --factory --host 127.0.0.1 --port 8010
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now tckdb-api
systemctl status tckdb-api
journalctl -u tckdb-api -f      # logs
```

Bind the API to **`127.0.0.1`** (loopback), not `0.0.0.0` — the ingress layer
reaches it locally; nothing else should.

---

## Cross-cutting good practices (both patterns)

### Exposure — tunnel, never a raw port

Put the public hostname behind a **Cloudflare tunnel** (or a reverse proxy you
control). Cloudflare's `cloudflared` connects *outbound* from your host to
Cloudflare and maps `tckdb.example.com` → `localhost:8010`. Benefits: **no
inbound ports opened** on your router, TLS terminated by Cloudflare, your home
IP hidden, and a DDoS/WAF layer for free. Never port-forward the API port raw
with a self-signed cert.

Tunnel ingress can be configured in the Cloudflare dashboard (remote-managed)
or in `~/.cloudflared/config.yml` (local). Point one hostname at
`http://localhost:8010`.

### Secrets

- The `.env` file is the only place credentials live; keep it out of git and
  `chmod 600`.
- Rotate any secret that has ever appeared in a chat, ticket, or shell history.

### Backups (do this)

Schedule a nightly `pg_dump`. A systemd timer is clean:

`/etc/systemd/system/tckdb-backup.service`:
```ini
[Unit]
Description=TCKDB Postgres backup (pg_dump)
After=docker.service
[Service]
Type=oneshot
User=<you>
ExecStart=<repo-or-home>/tckdb_backup.sh
```
`/etc/systemd/system/tckdb-backup.timer`:
```ini
[Unit]
Description=Run TCKDB backup daily
[Timer]
OnCalendar=*-*-* 03:30:00
Persistent=true
[Install]
WantedBy=timers.target
```
`tckdb_backup.sh`:
```bash
#!/usr/bin/env bash
set -euo pipefail
DEST=<backup-dir>; mkdir -p "$DEST"
FILE="$DEST/tckdb_$(date +%Y%m%d_%H%M%S).sql.gz"
docker exec <db-container> pg_dump -U <user> -d <db> | gzip > "$FILE"
find "$DEST" -name 'tckdb_*.sql.gz' -mtime +14 -delete
```
```bash
sudo systemctl enable --now tckdb-backup.timer
systemctl list-timers tckdb-backup.timer
```

> **Offsite copy.** Backups that live only on the same disk as the database die
> with it. Add an `rsync`/`rclone` step to copy each dump to another
> machine or a cloud bucket.

Artifacts (MinIO) are a separate backup concern; mirror the object store too if
you rely on it.

### Migrations on a deployed DB

Follow [`backend/docs/deployment/migrations.md`](../../backend/docs/deployment/migrations.md):
back up → `alembic current` → read revision docstrings → apply step-by-step →
verify. Never drop-and-recreate a deployed DB.

---

## CI/CD (Pattern A)

[`build-api-image.yml`](../../.github/workflows/build-api-image.yml) builds and
publishes a multi-arch image to `ghcr.io` on every push to `main` that touches
the backend. That is the **build half**. The **deploy half** is deliberately
left to the operator, because it involves a choice and a migration step:

1. **Pull + restart** on the host — options: a small webhook that runs
   `docker compose pull tckdb-api && docker compose up -d tckdb-api`; a tool
   like Watchtower; or a scheduled `docker compose pull`.
2. **Run migrations** as part of the deploy — e.g. a one-off
   `docker compose run --rm tckdb-api alembic upgrade head` **before** the new
   API starts serving. Do not let a new image serve against an un-migrated DB.

Pin image tags by digest in production so a deploy is an explicit, reviewable
change rather than a moving `:latest`.

---

## Quick reference

| Task | Command |
|---|---|
| Bring up infra | `docker compose --env-file .env.pi up -d db minio` |
| API status / logs | `systemctl status tckdb-api` · `journalctl -u tckdb-api -f` |
| Restart API | `sudo systemctl restart tckdb-api` |
| Apply migrations | `alembic upgrade head` (env sourced; back up first) |
| Manual backup | `sudo systemctl start tckdb-backup.service` |
| Backup schedule | `systemctl list-timers tckdb-backup.timer` |
