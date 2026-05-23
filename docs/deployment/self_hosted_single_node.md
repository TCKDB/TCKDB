# Self-hosted single-node deployment

> This guide describes a self-hosted single-node TCKDB deployment
> using Docker Compose. It was tested on a Raspberry Pi, but the
> architecture applies to any small Linux server — a home/lab server,
> a small VPS, an old workstation. Substitute your host wherever the
> doc mentions a Pi.
>
> This is the canonical recipe for **Mode 2 — self-hosted online
> TCKDB**. For the broader picture (the three deployment modes,
> domain/tunnel requirements, air-gapped HPC notes, and how data
> moves between instances) see
> [deployment_modes.md](deployment_modes.md) and the
> [export/import roadmap](../roadmaps/export_import_roadmap.md).

The deployment exposes anonymous reads under `/api/v1/scientific/*`
publicly through a reverse-proxy/tunnel (Cloudflare Tunnel in this
worked example) and restricts authenticated writes to seeded
accounts. Anything with Docker, ~4 GB RAM, and SSD-backed storage
will do as the host.

Public reach happens through a **reverse-proxy or tunnel**
(Cloudflare Tunnel is what this example walks through), **not** a
router port-forward.

This is the **hosted** sibling of [local-v0.md](local-v0.md) and
[shared-private-deployment.md](shared-private-deployment.md). Same
application, same schema, same auth model — what changes is the
**operational posture**:

- The API is reachable from the public internet.
- Anonymous scientific reads are allowed, but every hosted abuse-control
  knob is on.
- Postgres and MinIO are not reachable from anything but the API
  process on the host.
- TLS terminates at Cloudflare; the hop from `cloudflared` to the API
  is loopback only.

> **Not a separate edition.** Use
> [.env.selfhosted.example](../../.env.selfhosted.example) and
> [docker-compose.yml](../../docker-compose.yml).
> The backend code is the same code the rest of the deployment
> scenarios run.

> **Before you expose the deployment:** confirm every row of the
> [Production checklist](production_checklist.md) is satisfied. The
> `.env.selfhosted.example` referenced above already encodes every
> required value; the checklist is the page you use to verify it on
> the actual deploy.

---

## Where this scenario fits

| Scenario | Audience | Reachability | Reads | Writes |
|---|---|---|---|---|
| [Single-machine private](local-v0.md) | One user | `127.0.0.1` | Open | Open registration |
| [Shared private](shared-private-deployment.md) | A lab | Lab URL | Auth | Seeded accounts |
| **Self-hosted single-node (this doc)** | Public | Tunnel hostname | Anon, throttled | Seeded accounts |
| Hosted community | Public | Public URL | Anon, throttled | Operator-managed |

---

## Prerequisites

### Hardware

Any small Linux server with:

- 4 GB RAM minimum (8 GB more comfortable under concurrent search traffic).
- SSD-backed storage for the Postgres volume — **do not use an SD card**.
  WAL traffic will wear it out within months.
- Wired Ethernet preferred over Wi-Fi for a server role.

Pi-specific hardware notes are in [Raspberry Pi notes](#raspberry-pi-notes)
at the end of this doc.

### OS

- 64-bit Linux. Debian-derivative (Debian, Ubuntu, Raspberry Pi OS
  Bookworm) is the smoothest path because Docker has first-class
  packages.
- The RDKit cartridge image (`informaticsmatters/rdkit-cartridge-debian`)
  publishes `linux/amd64` and `linux/arm64`, so x86_64 servers and
  64-bit Raspberry Pis both run it natively.

### Host packages

```bash
sudo apt update
sudo apt install -y \
    ca-certificates curl gnupg lsb-release \
    git ufw fail2ban unattended-upgrades \
    postgresql-client jq
```

### Conda environment

The backend runs on the host inside `tckdb_env`. Install Miniforge for
your host architecture (`x86_64` or `aarch64`):

```bash
# Pick the matching installer for `uname -m`:
#   x86_64 -> Miniforge3-Linux-x86_64.sh
#   aarch64 -> Miniforge3-Linux-aarch64.sh
curl -L -o /tmp/miniforge.sh \
    "https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-$(uname -m).sh"
sudo bash /tmp/miniforge.sh -b -p /opt/conda
sudo /opt/conda/bin/conda env create -n tckdb_env -f /opt/tckdb/backend/environment.yml
```

(Substitute your project layout — the systemd unit assumes the repo is
checked out at `/opt/tckdb`.)

---

## Docker installation

Use the Docker Engine apt repo (the distro-shipped `docker.io` is
fine but lags). On Debian/Ubuntu/Raspberry Pi OS:

```bash
curl -fsSL https://download.docker.com/linux/debian/gpg | \
    sudo gpg --dearmor -o /usr/share/keyrings/docker.gpg
# `arch=` should match your host: amd64 or arm64.
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker.gpg] \
    https://download.docker.com/linux/debian bookworm stable" | \
    sudo tee /etc/apt/sources.list.d/docker.list
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io \
    docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker tckdb   # create the `tckdb` system user first
```

Verify Compose v2:

```bash
docker compose version
```

---

## Filesystem layout

```text
/opt/tckdb/                          # git checkout of TCKDB_v2
    backend/                         # python backend
    docker-compose.yml            # symlink or copy from repo
    .env.selfhosted                          # NOT committed; created from .env.selfhosted.example
/var/backups/tckdb/                  # nightly pg_dump output
/var/log/                            # journald + Docker json-file logs
```

Owner everything by a dedicated unprivileged `tckdb` user/group; the
systemd units run as that user.

---

## Host ports vs container ports

The host and the container have separate port namespaces. This catches
new operators out, so the convention used by this deployment is spelled
out explicitly here.

A compose `ports:` entry like:

```yaml
ports:
  - "127.0.0.1:5434:5432"
```

means:

```text
host 127.0.0.1, port 5434   forwards to   container port 5432
```

Postgres inside the container still listens on `5432`. The host
publishes that as `5434` only to avoid clashing with a host-installed
Postgres that already binds `5432`.

The mapping splits how the rest of the stack talks to the DB:

| Where the consumer runs | `DB_HOST` | `DB_PORT` |
|---|---|---|
| On the host (Alembic, Uvicorn under systemd, `psql` from the host) | `127.0.0.1` | host-published port (e.g. `5432` or `5434`) |
| Inside the same compose network as `db` (a future `api:` service) | `db` (the service name) | `5432` (the container port) |

The default `docker-compose.yml` publishes
`127.0.0.1:${DB_PORT:-5432}:5432`, so if you set `DB_PORT=5434` in
`.env.selfhosted`, the host sees Postgres at `127.0.0.1:5434` and you
must use that same value in any host-side `DB_PORT`. The container
itself does not move.

The same logic applies to MinIO: container ports `9000`/`9001`
published to `127.0.0.1:9000`/`127.0.0.1:9001`. Never republish them
on `0.0.0.0`.

---

## Compose services

[docker-compose.yml](../../docker-compose.yml)
defines three services, split across **core** services that run by
default and an **opt-in ingress** service behind a Compose profile:

| Service | Image | Binding | Public? | Profile |
|---|---|---|---|---|
| `db` | `informaticsmatters/rdkit-cartridge-debian` | `127.0.0.1:5432` | **No** | (always) |
| `minio` | `minio/minio` | `127.0.0.1:9000` + `127.0.0.1:9001` | **No** | (always) |
| `cloudflared` | `cloudflare/cloudflared` | host network (egress only) | tunnel only | `cloudflare` (opt-in) |

The FastAPI backend and (inline) upload worker run **on the host** as
a systemd unit, not in Docker, so the existing conda-based dev/test
workflow is preserved. The host process talks to Docker services over
loopback.

`ports:` entries are all `127.0.0.1:...:...`. **Never change them** to
bare `5432:5432` — that publishes Postgres on every interface,
including the LAN.

---

## Ingress options

The core stack (`db`, `minio`, plus the host-side API) is independent
of how you publish the API. Ingress is a separate layer; pick **one**
of these, do not stack them:

| Option | What it gives you | Where it's configured |
|---|---|---|
| **Cloudflare Tunnel via Compose (this repo's worked example)** | Public HTTPS hostname through Cloudflare; no inbound firewall hole. | The `cloudflared` service in `docker-compose.yml`, behind the `cloudflare` Compose profile. Token in `.env.selfhosted`. |
| **Cloudflare Tunnel via host systemd `cloudflared`** | Same as above, run as a regular OS service. | Operator-managed; do **not** also start the Compose `cloudflared` service. |
| **nginx / Caddy / Traefik reverse proxy** | TLS-terminating proxy in front of `127.0.0.1:8010`. | Operator-managed, outside this compose file. Set `TRUSTED_PROXY_HEADER` to whatever the proxy injects (e.g. `X-Real-IP` for nginx). |
| **Tailscale / WireGuard / SSH tunnel** | Reach the API over a private overlay only — no public surface. | Operator-managed. Useful for lab-only deployments. |
| **No ingress (loopback only)** | The API is reachable on `127.0.0.1:8010` from the host. Useful for local dev and validation runs. | Nothing to do. |

The Compose default brings up only the core services:

```bash
docker compose --env-file .env.selfhosted \
    up -d db minio
```

Cloudflare Tunnel ingress is opt-in via the `cloudflare` profile:

```bash
docker compose --env-file .env.selfhosted \
    --profile cloudflare up -d cloudflared
```

If you choose nginx/Caddy/Traefik/Tailscale/etc., just don't pass
`--profile cloudflare` and configure your proxy/tunnel separately.

> **Don't run two ingresses pointing at the same API.** Cloudflare
> Tunnel via Compose AND a host-side `cloudflared` systemd unit will
> both try to register the tunnel and one of them will lose. Pick one
> path.

---

## Environment variables (hosted-safe defaults)

Copy [`.env.selfhosted.example`](../../.env.selfhosted.example) to `.env.selfhosted` and fill in
the placeholders. The non-negotiable hosted toggles:

| Variable | Required value | Why |
|---|---|---|
| `DEPLOYMENT_MODE` | `hosted_public` | Activates the startup safety guard so unsafe values for the rows below cause the API to exit at boot instead of silently misconfiguring production. Use `shared_private` for lab-internal deployments. |
| `EXPOSE_API_DOCS` | `false` | Don't ship Swagger/ReDoc to the public surface. |
| `LEGACY_READS_REQUIRE_AUTH` | `true` | Legacy `/api/v1/{thermo,…}` routes leak integer PKs. |
| `ALLOW_PUBLIC_INTERNAL_IDS` | `false` | Clients use refs as handles. |
| `RATE_LIMIT_ENABLED` | `true` | Hosted abuse control is mandatory. |
| `SESSION_COOKIE_SECURE` | `true` | TLS-only cookies. Cloudflare terminates TLS. |
| `AUTH_ALLOW_OPEN_REGISTRATION` | `false` | Seeded accounts only. |
| `TRUSTED_PROXY_HEADER` | `CF-Connecting-IP` | See next section. |
| `DB_STATEMENT_TIMEOUT_MS` | `30000` (or stricter) | Per-session SQL cap. |
| `TCKDB_API_HOST` | `127.0.0.1` | Only `cloudflared` talks to the API. |

For the current default bucket budgets, see
[`.env.selfhosted.example`](../../.env.selfhosted.example). This
deployment doc only documents controls that are required or
policy-relevant; numeric rate-limit budgets
(`RATE_LIMIT_*_PER_MINUTE`) are tunable deployment settings and live
in the env example to avoid two drifting sources of truth.

---

## Trusted proxy header

The rate limiter and audit logging key off the client IP returned by
[`_client_ip()`](../../backend/app/api/rate_limit.py). When
`TRUSTED_PROXY_HEADER` is set, the middleware reads that header; when
unset, it falls back to the ASGI peer (which would be `127.0.0.1`
because of `cloudflared`).

**Use `CF-Connecting-IP`, not `X-Forwarded-For`.** On a Cloudflare
Tunnel egress:

- `CF-Connecting-IP` is injected by Cloudflare's edge and arrives at
  the host over the authenticated tunnel. Origin spoofing is not
  reachable.
- `X-Forwarded-For` is forwarded as-is from the client request — an
  abuser can prepend anything to it.

Set it once in `.env.selfhosted`:

```dotenv
TRUSTED_PROXY_HEADER=CF-Connecting-IP
```

If you ever change the ingress (e.g. swap in a different tunnel
product or add a local reverse proxy), revisit this — the **only**
correct value is the header the trusted ingress overwrites
unconditionally.

---

## Cloudflare Tunnel setup

> **DNS, tunnel, ingress: three separate things.** They're easy to
> confuse and it pays to keep them straight from the start.
>
> - A **DNS record** decides where a name points. Adding
>   `tckdb.example.org` to your DNS zone tells the internet "if you
>   want to reach this name, ask Cloudflare." Nothing about the DNS
>   record knows or cares what TCKDB is.
> - A **Cloudflare Tunnel** is a long-lived outbound connection from
>   `cloudflared` on your host to Cloudflare. Once it's running,
>   Cloudflare can route requests inbound through that connection
>   instead of needing an open port on your firewall.
> - **Ingress rules** inside the tunnel config say "when a request
>   arrives for hostname X, forward it to local URL Y." This is the
>   step that actually wires `https://tckdb.example.org` to
>   `127.0.0.1:8010`.
>
> Creating a Cloudflare DNS record or a tunnel does not by itself
> route traffic anywhere — you also have to add the ingress rule.
> Conversely, an ingress rule for a hostname that has no DNS record
> pointing at Cloudflare is dead-on-arrival. Both have to line up.
>
> If you just added a new hostname and `curl` says
> `NXDOMAIN`/"could not resolve host", that may be DNS negative-cache
> on your local resolver. The cache duration is bounded by the
> zone's negative TTL (often a few minutes); waiting it out, or
> flushing your resolver, fixes it.

You need a Cloudflare account, a zone (e.g. `tckdb.example.org`), and
the Zero Trust dashboard enabled (the free tier is fine).

1. **Create the tunnel** in the Zero Trust dashboard (Networks →
   Tunnels → Create a tunnel → Cloudflared). Pick a name like
   `tckdb-selfhosted`. Cloudflare prints a tunnel token — this is
   the value for `CLOUDFLARED_TUNNEL_TOKEN`.

2. **Store the token** outside the committed repo. Add a line to
   `/opt/tckdb/.env.selfhosted` (do **not** add it to `.env.selfhosted.example`):

   ```dotenv
   CLOUDFLARED_TUNNEL_TOKEN=eyJhIjoi...   # from the Zero Trust UI
   ```

3. **Configure the public hostname.** In the same UI, add a Public
   Hostname:

   - Subdomain: `api`
   - Domain: `tckdb.example.org`
   - Type: `HTTP`
   - URL: `127.0.0.1:8010`

   That is the **only** ingress rule. Do not add rules for `9001`
   (MinIO console), `5432` (Postgres), or any other internal port.

4. **Launch.** Once the rest of the stack is up (next section), the
   `cloudflared` service authenticates with the token and the public
   URL goes live. Cloudflare manages the cert.

> No router port-forward, no inbound firewall hole. UFW should still
> block all inbound traffic except SSH (and SSH should be restricted —
> see [Admin access](#sshadmin-access-hardening)).

---

## HTTPS / public hostname

Cloudflare terminates TLS at the edge with a managed cert. Confirm:

- **SSL/TLS mode** for the zone is set to **Full (strict)** or
  **Strict** — `Flexible` mode would expose the tunnel-side hop as
  plain HTTP from the edge perspective, which it is, but more
  importantly it weakens the public-facing posture.
- For TCKDB the origin is reached via the tunnel (not via an A record),
  so `Strict` is the right setting — the origin certificate is
  Cloudflare's own.

`TCKDB_PUBLIC_BASE_URL` in `.env.selfhosted` should be the public URL:

```dotenv
TCKDB_PUBLIC_BASE_URL=https://api.tckdb.example.org/api/v1
```

---

## Optional: protected DB-GUI access via TCP tunnel

Day-to-day operators may want a DataGrip / DBeaver / `psql` session
against the production database without exposing Postgres publicly.
The right pattern is a **second** Cloudflare Tunnel ingress rule of
type `TCP`, gated behind a Cloudflare Access policy. Postgres still
binds only to `127.0.0.1` on the host — the tunnel forwards from
Cloudflare to the loopback port, and Access authenticates *who* is
allowed to use the forward.

> **Do not expose Postgres on `0.0.0.0`.** The compose file binds
> `127.0.0.1:5432` exactly so this can never happen by accident. The
> tunnel adds a *separately authenticated* path on top of that
> loopback binding.

Setup (one-time, on Cloudflare side):

1. In the same tunnel, add a second public hostname:
   - Subdomain: `pg-tckdb`
   - Domain: `tckdb.example.org` → `pg-tckdb.example.org`
   - Type: `TCP`
   - URL: `tcp://127.0.0.1:5432`
2. In Cloudflare Access, create an Application of type
   "Self-hosted" for `pg-tckdb.example.org`, with a policy that only
   permits your operator email/identity provider.

Then, from any operator workstation:

```bash
# Forwards localhost:15434 on your machine to pg-tckdb.example.org
# over the authenticated tunnel. cloudflared opens an Access prompt
# in the browser on the first call.
cloudflared access tcp \
    --hostname pg-tckdb.example.org \
    --url localhost:15434
```

Leave that running, then configure your DB GUI:

```text
Host:     127.0.0.1
Port:     15434
Database: tckdb
User:     tckdb
Password: <DB_PASSWORD from .env.selfhosted>
```

Three independent authentication layers stack here, and that's the
point:

| Layer | What it checks |
|---|---|
| Cloudflare Access policy | Are you a permitted operator identity? |
| `cloudflared access tcp` tunnel | Did Access issue you a valid token? |
| Postgres `pg_hba` + role/password | Is this `username`/`password` valid for this database? |

A leaked DB password alone cannot reach the database from the
internet — the tunnel never opens without an Access token first.

---

## Postgres `statement_timeout`

Two layers — both should be on:

1. **App-session level.** `DB_STATEMENT_TIMEOUT_MS=30000` in `.env.selfhosted`.
   This is applied on every new DBAPI connection and protects against
   a runaway query in the pool. Set it as a positive integer in ms.

2. **Role level (recommended).** Persist the same value on the role so
   any client — including ad-hoc `psql` sessions — inherits it:

   ```bash
   docker compose --env-file .env.selfhosted \
       exec -T db psql -U "$DB_USER" -d "$DB_NAME" \
       -c "ALTER ROLE tckdb SET statement_timeout = '30s';"
   ```

   Tighten further (e.g. `10s`) once you've observed legitimate query
   shapes for a few days.

---

## Bring-up sequence

```bash
# 0. Clone and prep
sudo mkdir -p /opt/tckdb && sudo chown -R tckdb:tckdb /opt/tckdb
sudo -u tckdb git clone <repo-url> /opt/tckdb
cd /opt/tckdb
sudo -u tckdb cp .env.selfhosted.example .env.selfhosted
sudo -u tckdb $EDITOR .env.selfhosted        # fill in every change-me-* value

# 1. Bring up the data plane
docker compose --env-file .env.selfhosted up -d db minio

# 2. Run migrations.
#    For first bootstrap of an empty DB this is straightforward.
#    For upgrades against a DB that already holds data, follow the
#    operator runbook in
#    backend/docs/deployment/migrations.md (pg_dump first, read
#    revision docstrings, upgrade, smoke-test).
cd backend
DB_NAME=$(grep ^DB_NAME ../.env.selfhosted | cut -d= -f2) \
DB_USER=$(grep ^DB_USER ../.env.selfhosted | cut -d= -f2) \
DB_PASSWORD=$(grep ^DB_PASSWORD ../.env.selfhosted | cut -d= -f2) \
DB_HOST=127.0.0.1 DB_PORT=5432 \
    /opt/conda/bin/conda run -n tckdb_env alembic upgrade head

# 3. Persist the statement_timeout at role level (see above)

# 4. Seed an admin account (and later, log in / mint API keys).
#    See docs/deployment/admin_auth_quickstart.md for the full recipe,
#    including curator seeding and how to wire ARC against the deployment.
/opt/conda/bin/conda run -n tckdb_env python scripts/bootstrap_admin.py \
    --username admin --email admin@tckdb.example.org --role admin

# 5. Start the API as a service
sudo cp ../examples/deployment/systemd/tckdb-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tckdb-api.service

# 6. (Optional, if using Cloudflare Tunnel via Compose) Bring up the
#    tunnel last - this is what publishes the API.
#    For nginx/Caddy/Traefik/Tailscale/etc., skip this step and
#    configure that ingress separately.
cd ..
docker compose --env-file .env.selfhosted \
    --profile cloudflare up -d cloudflared
```

---

## Backups and restore

### Daily logical backup

The [backup unit + timer](../../examples/deployment/systemd/) run
`pg_dump` inside the `db` container nightly at 03:15 and write
`/var/backups/tckdb/tckdb-YYYY-MM-DD.sql.gz`. They retain 30 days and
prune older files.

Install:

```bash
sudo cp examples/deployment/systemd/tckdb-backup.service /etc/systemd/system/
sudo cp examples/deployment/systemd/tckdb-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tckdb-backup.timer
sudo systemctl list-timers | grep tckdb-backup    # verify next run
```

### Off-box copy

A copy on the same host is not a backup. Sync `/var/backups/tckdb/` to an
off-box target — examples:

- `rclone sync /var/backups/tckdb remote:tckdb-backups` (any rclone
  remote — Backblaze B2 is cheap and arm64-friendly).
- `rsync` to a NAS over the LAN.

Schedule via a second timer or piggyback on the existing one. Encrypt
at the storage layer (rclone crypt or B2 server-side encryption).

### MinIO artifact backups

If `S3_BUCKET=tckdb-artifacts` accumulates real data (parsed-output
attachments etc.), mirror it the same way:

```bash
docker compose exec -T minio \
    mc mirror /data/tckdb-artifacts /data/.snapshots/$(date +%F)
```

…and rclone-sync the snapshot directory off-box.

### Restore drill

```bash
# Drop and recreate the DB (destructive)
docker compose --env-file .env.selfhosted exec -T db \
    psql -U "$DB_USER" -d postgres \
    -c "DROP DATABASE IF EXISTS $DB_NAME; CREATE DATABASE $DB_NAME OWNER $DB_USER;"

# Restore from a dump
gunzip -c /var/backups/tckdb/tckdb-2026-05-12.sql.gz | \
    docker compose --env-file .env.selfhosted exec -T db \
    psql -U "$DB_USER" -d "$DB_NAME"
```

Run this drill on a scratch DB at least quarterly — an untested backup
is not a backup.

---

## Log rotation

Three log sources, three rotation strategies — all bounded:

1. **systemd / journald (API + backup units).** Cap the journal size
   in `/etc/systemd/journald.conf`:

   ```ini
   SystemMaxUse=500M
   SystemKeepFree=1G
   ```

   Apply with `sudo systemctl restart systemd-journald`.

2. **Docker container logs (db, minio, cloudflared).** The compose
   file pins `json-file` driver with `max-size: 10m`, `max-file: 5`
   per service.

3. **Backup files.** The `tckdb-backup.service` prunes files older
   than 30 days.

---

## SSH / admin access hardening

The host is on the internet via Cloudflare, but SSH should *not* be.
Lock it to LAN-only or, better, to Cloudflare Access:

### Minimum (LAN-only SSH)

```bash
# /etc/ssh/sshd_config.d/tckdb.conf
PasswordAuthentication no
PermitRootLogin no
KbdInteractiveAuthentication no
AllowUsers tckdb
```

Plus firewall:

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow from 192.168.0.0/16 to any port 22 proto tcp
sudo ufw enable
```

### Better (SSH through Cloudflare Tunnel)

Add a second tunnel ingress rule of type `SSH` pointing at
`127.0.0.1:22`, gated behind a Cloudflare Access policy (email-based
identity). Then close port 22 to the LAN as well — the only path to
SSH becomes `cloudflared access ssh ...` with an authenticated user.

### Other hardening

- `fail2ban` for the SSH jail (already installed in prerequisites).
- `unattended-upgrades` enabled for security updates.
- Disable Avahi/mDNS if the host runs on a hostile LAN.
- **Never** run the API as root or as a default OS account (e.g. the
  `pi` user on Raspberry Pi OS, the `ubuntu` user on Ubuntu cloud
  images). The systemd unit
  uses an unprivileged `tckdb` user.

---

## Health checks

### Internal (Docker)

`docker compose ps` should show every service `healthy`:

```bash
docker compose --env-file .env.selfhosted ps
```

### External (tunnel)

```bash
# Liveness — process up, DB reachable.
curl -fsS https://api.tckdb.example.org/api/v1/health
# -> {"status":"ok"}

# Readiness — DB reachable AND schema migrated. Body includes the
# Alembic revision currently installed.
curl -fsS https://api.tckdb.example.org/api/v1/readyz
# -> {"status":"ready","database":"ok","alembic_revision":"<rev>"}
```

A non-200 on `/health` means either the tunnel is misrouted (Cloudflare
side) or `tckdb-api.service` is down (`systemctl status tckdb-api`).
A non-200 on `/readyz` while `/health` is `200` means the process is
up but the database is unreachable or the schema has not been
migrated — fix before re-routing traffic.

Every response carries an `X-Request-ID` header. Clients may set their
own value (matching `^[A-Za-z0-9._\-]+$`, max 128 chars) for
end-to-end correlation; the server echoes it when safe and otherwise
generates a UUID-style id. Hosted log shipping should enable
`LOG_FORMAT=json` so each log record carries `request_id`, `level`,
`logger`, and `message` fields.

### Monitoring

For a single-node deployment, a recurring uptime check from an external
service (UptimeRobot, healthchecks.io) against `/api/v1/readyz` is
the right probe for load-balancer decisions (it reports both
connectivity and schema state); `/api/v1/health` is a strictly
lighter liveness probe useful for process-up alerts. Page on three
consecutive failures.

---

## Smoke tests

Run these from a workstation, **not** on the host (you want to exercise
the public path).

### 1. Health

```bash
curl -fsS https://api.tckdb.example.org/api/v1/health
# {"status":"ok"}
```

Health verifies connectivity only. `/api/v1/health` is exempt from
rate limiting (see [`rate_limit.py`](../../backend/app/api/rate_limit.py)),
so it does **not** exercise any bucket — use the smoke test in §3
below for that.

### 2. Anonymous scientific read via `query_cookbook.py`

The cookbook lives at
[clients/python/examples/query_cookbook.py](../../clients/python/examples/query_cookbook.py).
It uses refs as handles by default, which is what
`ALLOW_PUBLIC_INTERNAL_IDS=false` requires:

```bash
cd clients/python
pip install -e .

python examples/query_cookbook.py --recipe species_search \
    --smiles "O" \
    --base-url https://api.tckdb.example.org/api/v1
```

A populated deployment returns a non-empty `results` list; an empty DB
returns the "no results" friendly message. Either is success — what
you're checking is that the anonymous path works through the tunnel
and that ref-based handles round-trip.

### 3. Rate-limit smoke test

This exercises the **`anon_read`** bucket — anonymous scientific
reads keyed by client IP. `RATE_LIMIT_ANON_READ_PER_MINUTE=60`
(default) means the 61st anonymous scientific-read request from one
IP inside a fixed window must be rejected with `429`.

`/api/v1/health` is exempt, so the loop targets a real public
scientific search endpoint (`POST /scientific/reactions/search`)
with a minimal valid body that does not depend on seeded data — an
empty `results` list is fine, the point is to land in the
`anon_read` bucket and get rate-limited:

```bash
base=https://api.tckdb.example.org/api/v1
for i in $(seq 1 70); do
    code=$(curl -sS -o /dev/null -w "%{http_code}" \
        -X POST "$base/scientific/reactions/search" \
        -H "Content-Type: application/json" \
        --data '{"reactants":["CC"],"products":["C[CH2]"],"limit":1}')
    printf "%s %s\n" "$i" "$code"
done | tail -15
```

Expected: a run of `200`s (the search may legitimately return zero
results on an empty DB) followed by `429`s once the per-minute
budget is exhausted. If you raised `RATE_LIMIT_ANON_READ_PER_MINUTE`
above 60, raise the loop count accordingly.

The `429` response body identifies the bucket:

```json
{"detail":"...","code":"rate_limit_exceeded","bucket":"anon_read","retry_after_seconds":...}
```

Do **not** add `-H "X-API-Key: ..."` to this loop — that would
switch the request into the `auth_read` bucket (300/min, keyed by
credential fingerprint) and the anonymous limiter would not be
exercised.

If you never see `429`, the usual causes are:

- `RATE_LIMIT_ENABLED=false` slipped into `.env.selfhosted`.
- `TRUSTED_PROXY_HEADER` is misconfigured: behind Cloudflare Tunnel
  every request arrives over loopback, so without
  `TRUSTED_PROXY_HEADER=CF-Connecting-IP` the limiter sees every
  caller as `127.0.0.1` and lumps unrelated callers into one bucket
  (or fails to distinguish them from the origin). Check the audit
  log for the IP the limiter actually recorded.

### 4. Legacy-route auth check

Legacy entity routes must require auth:

```bash
curl -s -o /dev/null -w "%{http_code}\n" \
    https://api.tckdb.example.org/api/v1/thermo/
# expect 401 (LEGACY_READS_REQUIRE_AUTH=true), not 200
```

### 5. OpenAPI hidden check

```bash
curl -s -o /dev/null -w "%{http_code}\n" \
    https://api.tckdb.example.org/docs
# expect 404
curl -s -o /dev/null -w "%{http_code}\n" \
    https://api.tckdb.example.org/openapi.json
# expect 404
```

If either returns 200, `EXPOSE_API_DOCS=true` slipped through — fix
`.env.selfhosted` and `systemctl restart tckdb-api`.

### 6. Postgres not publicly reachable

From *off* the Pi:

```bash
nc -vz api.tckdb.example.org 5432
# expect: connection refused / no route to host
```

There is no ingress rule for 5432, so Cloudflare returns `530` for any
non-matching hostname and the LAN/WAN have no route. If `nc` succeeds,
stop the deployment — you have a port-forward you didn't mean to
create.

---

## Rollback plan

The two reversible failure modes are **migration / image upgrade
regressions** and **config regressions**. Both have a clean undo:

### Image / migration rollback

```bash
# 1. Stop the public surface IMMEDIATELY so partial state doesn't
#    leak into a downstream client. The exact "stop ingress" step
#    depends on which ingress you chose (see "Ingress options").
#    For the Compose cloudflared service:
docker compose --env-file .env.selfhosted stop cloudflared
#    For a host-side cloudflared systemd unit:
#      sudo systemctl stop cloudflared
#    For nginx/Caddy/Traefik: stop or reload-with-503 the proxy.
sudo systemctl stop tckdb-api

# 2. Pin the previous git revision and restart.
cd /opt/tckdb
sudo -u tckdb git checkout <prev-good-sha>
sudo systemctl start tckdb-api
# Restart the ingress that matches your setup:
docker compose --env-file .env.selfhosted \
    --profile cloudflare start cloudflared
```

If a schema migration is the culprit and the DB is now ahead of the
checked-out code, restore the previous nightly:

```bash
# from /var/backups/tckdb/
sudo systemctl stop tckdb-api
docker compose ... exec db psql -U $DB_USER -d postgres \
    -c "DROP DATABASE $DB_NAME; CREATE DATABASE $DB_NAME OWNER $DB_USER;"
gunzip -c tckdb-YYYY-MM-DD.sql.gz | docker compose ... exec -T db \
    psql -U $DB_USER -d $DB_NAME
sudo systemctl start tckdb-api
```

### Config rollback

`.env.selfhosted` is the single source of hosted-posture truth. Keep the
previous version in git (in a *private* repo, not this one — it has
secrets) or copy it before edits:

```bash
sudo cp /opt/tckdb/.env.selfhosted /opt/tckdb/.env.selfhosted.$(date +%F)
$EDITOR /opt/tckdb/.env.selfhosted
sudo systemctl restart tckdb-api
# verify smoke tests; if anything regressed:
sudo cp /opt/tckdb/.env.selfhosted.$(date +%F) /opt/tckdb/.env.selfhosted
sudo systemctl restart tckdb-api
```

### Emergency kill switch

Stop whichever ingress is in front of the API. For the Compose
cloudflared service:

```bash
docker compose --env-file .env.selfhosted \
    stop cloudflared
```

For a host-side cloudflared systemd unit: `sudo systemctl stop
cloudflared`. For nginx/Caddy/Traefik: stop or reload the proxy with a
503-everything config.

This drops the public surface — for Cloudflare Tunnel the URL returns
the "tunnel offline" page. Everything else — Postgres, MinIO, the API
— stays up on loopback so you can debug at leisure.

---

## What this deployment does *not* do

- **No federation, no sync to hosted community instance.** This *is*
  a hosted instance, but it's an independent one. Contribution
  bundles (see `docs/contribution-bundles/`) remain the cross-instance
  mechanism.
- **No managed Postgres.** Postgres runs on the Pi in Docker. For a
  hosted instance with significant write traffic, graduate to a
  managed Postgres+RDKit service and treat the Pi as the API/worker
  tier.
- **No CDN cache of API responses.** Cloudflare's HTTP cache is
  bypassed for `/api/v1/*` by default (no `Cache-Control` set). That
  is correct — scientific responses are id-bearing and not safe to
  cache anonymously. Don't enable Cloudflare caching for these paths.
- **No public submission UI.** Writes are seeded-account only. If you
  later expose a write UI, revisit CORS and rate-limit budgets first.

---

## Raspberry Pi notes

The first working deployment of this recipe ran on a Raspberry Pi 4
under Raspberry Pi OS Bookworm (64-bit). Everything in this guide
applies, with these specifics worth knowing:

- **Hardware.** Use the 4 GB or 8 GB SKU. SD-card storage is the
  failure mode — put the Postgres volume on a USB-3 SSD or NVMe HAT.
  Wired Ethernet beats Wi-Fi for a server role.
- **OS.** Raspberry Pi OS Bookworm (64-bit) or Debian Bookworm arm64.
  32-bit Pi OS will not work — the RDKit cartridge image is
  `linux/arm64`, not `linux/arm`.
- **Image arch.** The `informaticsmatters/rdkit-cartridge-debian`
  image publishes `linux/arm64`, so the Pi runs it natively — no
  qemu/binfmt setup required.
- **Default user.** Raspberry Pi OS ships with a `pi` user. Do
  **not** run the API or DB as `pi`. Create the unprivileged `tckdb`
  user/group exactly as documented above.
- **Cloudflared tunnel name.** A name like `tckdb-pi` is a reasonable
  hint about where the tunnel runs, but it has no operational
  meaning — pick whatever helps you recognize the tunnel in the Zero
  Trust dashboard.

Everything else — compose layout, env vars, abuse-control posture,
backups, smoke tests — is identical to a non-Pi host.

---

## See also

- [admin_auth_quickstart.md](admin_auth_quickstart.md) — seeding
  admin/curator accounts, logging in, minting API keys, and the
  `check_selfhosted_deployment.sh` sanity-check script.
- [deployment_modes.md](deployment_modes.md) — the three deployment
  modes and how this recipe fits into Mode 2.
- [export_import_roadmap.md](../roadmaps/export_import_roadmap.md) —
  cross-instance data movement (offline → hosted, etc.).
- [`.env.selfhosted.example`](../../.env.selfhosted.example) — full annotated env file.
- [`docker-compose.yml`](../../docker-compose.yml) — the data-plane stack.
- [`examples/deployment/systemd/`](../../examples/deployment/systemd/) — host service units.
- [`docs/audits/security_public_read_abuse_audit.md`](../audits/security_public_read_abuse_audit.md) — the threat model that motivates the hosted toggles above.
- [`docs/specs/public_read_abuse_controls.md`](../specs/public_read_abuse_controls.md) — the implemented abuse-control spec these env vars wire.
