# Raspberry Pi hosted deployment

> This is the canonical recipe for **Mode 2 — self-hosted online
> TCKDB**. For the broader picture (the three deployment modes,
> domain/tunnel requirements, air-gapped HPC notes, and how data
> moves between instances) see
> [deployment_modes.md](deployment_modes.md) and the
> [export/import roadmap](../roadmaps/export_import_roadmap.md).

Deploy TCKDB on a Raspberry Pi 4 or 5 as a small **public scientific
database**. Anonymous reads under `/api/v1/scientific/*` are allowed;
authenticated writes are not. Public reach happens through a
**Cloudflare Tunnel**, not a router port-forward.

This is the **hosted** sibling of [local-v0.md](local-v0.md) and
[shared-private-deployment.md](shared-private-deployment.md). Same
application, same schema, same auth model — what changes is the
**operational posture**:

- The API is reachable from the public internet.
- Anonymous scientific reads are allowed, but every hosted abuse-control
  knob is on.
- Postgres and MinIO are not reachable from anything but the API
  process on the Pi.
- TLS terminates at Cloudflare; the hop from `cloudflared` to the API
  is loopback only.

> **Not a separate edition.** Use [.env.pi.example](../../.env.pi.example)
> and [docker-compose.pi.yml](../../docker-compose.pi.yml). The backend
> code is the same code the rest of the deployment scenarios run.

---

## Where this scenario fits

| Scenario | Audience | Reachability | Reads | Writes |
|---|---|---|---|---|
| [Single-machine private](local-v0.md) | One user | `127.0.0.1` | Open | Open registration |
| [Shared private](shared-private-deployment.md) | A lab | Lab URL | Auth | Seeded accounts |
| **Pi hosted (this doc)** | Public | Cloudflare hostname | Anon, throttled | Seeded accounts |
| Hosted community | Public | Public URL | Anon, throttled | Operator-managed |

---

## Prerequisites

### Hardware

- Raspberry Pi **4 (4GB+) or 5 (4GB+)**. The 8GB SKU is more comfortable
  if you expect concurrent search traffic.
- USB-3 SSD or NVMe HAT for the Postgres volume. SD cards burn out
  under WAL traffic.
- Wired Ethernet preferred over Wi-Fi.

### OS

- **Raspberry Pi OS Bookworm (64-bit)** or **Debian Bookworm arm64**.
  64-bit is required — the RDKit cartridge image only ships `linux/amd64`
  and `linux/arm64`.

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
`aarch64`:

```bash
curl -L -o /tmp/miniforge.sh \
    https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-aarch64.sh
sudo bash /tmp/miniforge.sh -b -p /opt/conda
sudo /opt/conda/bin/conda env create -n tckdb_env -f /opt/tckdb/backend/environment.yml
```

(Substitute your project layout — the systemd unit assumes the repo is
checked out at `/opt/tckdb`.)

---

## Docker installation

Use the Docker Engine apt repo (the Debian-shipped `docker.io` is
fine but lags). Pi OS is Debian-derived so the Debian repo works:

```bash
curl -fsSL https://download.docker.com/linux/debian/gpg | \
    sudo gpg --dearmor -o /usr/share/keyrings/docker.gpg
echo "deb [arch=arm64 signed-by=/usr/share/keyrings/docker.gpg] \
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

> **Image arch note.** The `informaticsmatters/rdkit-cartridge-debian`
> image publishes both `linux/amd64` and `linux/arm64`, so the Pi runs
> it natively — no qemu/binfmt setup required.

---

## Filesystem layout

```text
/opt/tckdb/                          # git checkout of TCKDB_v2
    backend/                         # python backend
    docker-compose.pi.yml            # symlink or copy from repo
    .env.pi                          # NOT committed; created from .env.pi.example
/var/backups/tckdb/                  # nightly pg_dump output
/var/log/                            # journald + Docker json-file logs
```

Owner everything by a dedicated unprivileged `tckdb` user/group; the
systemd units run as that user.

---

## Compose services

[docker-compose.pi.yml](../../docker-compose.pi.yml) provisions three
services:

| Service | Image | Binding | Public? |
|---|---|---|---|
| `db` | `informaticsmatters/rdkit-cartridge-debian` (arm64) | `127.0.0.1:5432` | **No** |
| `minio` | `minio/minio` | `127.0.0.1:9000` + `127.0.0.1:9001` | **No** |
| `cloudflared` | `cloudflare/cloudflared` | host network (egress only) | tunnel only |

The FastAPI backend and (inline) upload worker run **on the host** as
a systemd unit, not in Docker, so the existing conda-based dev/test
workflow is preserved. The host process talks to Docker services over
loopback.

`ports:` entries are all `127.0.0.1:...:...`. **Never change them** to
bare `5432:5432` — that publishes Postgres on every interface,
including the LAN.

---

## Environment variables (hosted-safe defaults)

Copy [`.env.pi.example`](../../.env.pi.example) to `.env.pi` and fill in
the placeholders. The non-negotiable hosted toggles:

| Variable | Required value | Why |
|---|---|---|
| `EXPOSE_API_DOCS` | `false` | Don't ship Swagger/ReDoc to the public surface. |
| `LEGACY_READS_REQUIRE_AUTH` | `true` | Legacy `/api/v1/{thermo,…}` routes leak integer PKs. |
| `ALLOW_PUBLIC_INTERNAL_IDS` | `false` | Clients use refs as handles. |
| `RATE_LIMIT_ENABLED` | `true` | Hosted abuse control is mandatory. |
| `SESSION_COOKIE_SECURE` | `true` | TLS-only cookies. Cloudflare terminates TLS. |
| `AUTH_ALLOW_OPEN_REGISTRATION` | `false` | Seeded accounts only. |
| `TRUSTED_PROXY_HEADER` | `CF-Connecting-IP` | See next section. |
| `DB_STATEMENT_TIMEOUT_MS` | `30000` (or stricter) | Per-session SQL cap. |
| `TCKDB_API_HOST` | `127.0.0.1` | Only `cloudflared` talks to the API. |

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
  the Pi over the authenticated tunnel. Origin spoofing is not
  reachable.
- `X-Forwarded-For` is forwarded as-is from the client request — an
  abuser can prepend anything to it.

Set it once in `.env.pi`:

```dotenv
TRUSTED_PROXY_HEADER=CF-Connecting-IP
```

If you ever change the ingress (e.g. swap in a different tunnel
product or add a local reverse proxy), revisit this — the **only**
correct value is the header the trusted ingress overwrites
unconditionally.

---

## Cloudflare Tunnel setup

You need a Cloudflare account, a zone (e.g. `tckdb.example.org`), and
the Zero Trust dashboard enabled (the free tier is fine).

1. **Create the tunnel** in the Zero Trust dashboard (Networks →
   Tunnels → Create a tunnel → Cloudflared). Pick a name like
   `tckdb-pi`. Cloudflare prints a tunnel token — this is the value
   for `CLOUDFLARED_TUNNEL_TOKEN`.

2. **Store the token** outside the committed repo. Add a line to
   `/opt/tckdb/.env.pi` (do **not** add it to `.env.pi.example`):

   ```dotenv
   CLOUDFLARED_TUNNEL_TOKEN=eyJhIjoi...   # from the Zero Trust UI
   ```

3. **Configure the public hostname.** In the same UI, add a Public
   Hostname:

   - Subdomain: `api`
   - Domain: `tckdb.example.org`
   - Type: `HTTP`
   - URL: `127.0.0.1:8000`

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

`TCKDB_PUBLIC_BASE_URL` in `.env.pi` should be the public URL:

```dotenv
TCKDB_PUBLIC_BASE_URL=https://api.tckdb.example.org/api/v1
```

---

## Postgres `statement_timeout`

Two layers — both should be on:

1. **App-session level.** `DB_STATEMENT_TIMEOUT_MS=30000` in `.env.pi`.
   This is applied on every new DBAPI connection and protects against
   a runaway query in the pool. Set it as a positive integer in ms.

2. **Role level (recommended).** Persist the same value on the role so
   any client — including ad-hoc `psql` sessions — inherits it:

   ```bash
   docker compose --env-file .env.pi -f docker-compose.pi.yml \
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
sudo -u tckdb cp .env.pi.example .env.pi
sudo -u tckdb $EDITOR .env.pi        # fill in every change-me-* value

# 1. Bring up the data plane
docker compose --env-file .env.pi -f docker-compose.pi.yml up -d db minio

# 2. Run migrations
cd backend
DB_NAME=$(grep ^DB_NAME ../.env.pi | cut -d= -f2) \
DB_USER=$(grep ^DB_USER ../.env.pi | cut -d= -f2) \
DB_PASSWORD=$(grep ^DB_PASSWORD ../.env.pi | cut -d= -f2) \
DB_HOST=127.0.0.1 DB_PORT=5432 \
    /opt/conda/bin/conda run -n tckdb_env alembic upgrade head

# 3. Persist the statement_timeout at role level (see above)

# 4. Seed an admin account
/opt/conda/bin/conda run -n tckdb_env python scripts/bootstrap_admin.py \
    --username admin --email admin@tckdb.example.org

# 5. Start the API as a service
sudo cp ../examples/deployment/systemd/tckdb-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tckdb-api.service

# 6. Bring up the tunnel last - this is what publishes the API
cd ..
docker compose --env-file .env.pi -f docker-compose.pi.yml up -d cloudflared
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

A copy on the Pi is not a backup. Sync `/var/backups/tckdb/` to an
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
docker compose -f docker-compose.pi.yml exec -T minio \
    mc mirror /data/tckdb-artifacts /data/.snapshots/$(date +%F)
```

…and rclone-sync the snapshot directory off-box.

### Restore drill

```bash
# Drop and recreate the DB (destructive)
docker compose -f docker-compose.pi.yml --env-file .env.pi exec -T db \
    psql -U "$DB_USER" -d postgres \
    -c "DROP DATABASE IF EXISTS $DB_NAME; CREATE DATABASE $DB_NAME OWNER $DB_USER;"

# Restore from a dump
gunzip -c /var/backups/tckdb/tckdb-2026-05-12.sql.gz | \
    docker compose -f docker-compose.pi.yml --env-file .env.pi exec -T db \
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

The Pi is on the internet via Cloudflare, but SSH should *not* be.
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
- Disable Avahi/mDNS if the Pi runs on a hostile LAN.
- **Never** run the API as root or as the `pi` user. The systemd unit
  uses an unprivileged `tckdb` user.

---

## Health checks

### Internal (Docker)

`docker compose ps` should show every service `healthy`:

```bash
docker compose --env-file .env.pi -f docker-compose.pi.yml ps
```

### External (tunnel)

```bash
curl -fsS https://api.tckdb.example.org/api/v1/health
# -> {"status":"ok"}
```

A non-200 here means either the tunnel is misrouted (Cloudflare side)
or `tckdb-api.service` is down (`systemctl status tckdb-api`).

### Monitoring

For a single-Pi deployment, a recurring uptime check from an external
service (UptimeRobot, healthchecks.io) against `/api/v1/health` is
sufficient. Page on three consecutive failures.

---

## Smoke tests

Run these from a workstation, **not** on the Pi (you want to exercise
the public path).

### 1. Health

```bash
curl -fsS https://api.tckdb.example.org/api/v1/health
# {"status":"ok"}
```

### 2. Anonymous scientific read via `query_cookbook.py`

The cookbook lives at
[clients/python/tckdb-client/examples/query_cookbook.py](../../clients/python/tckdb-client/examples/query_cookbook.py).
It uses refs as handles by default, which is what
`ALLOW_PUBLIC_INTERNAL_IDS=false` requires:

```bash
cd clients/python/tckdb-client
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

`RATE_LIMIT_ANON_PER_MINUTE=60` (default) means the 61st request from
one IP inside a fixed window must be rejected with `429`:

```bash
for i in $(seq 1 70); do
    code=$(curl -s -o /dev/null -w "%{http_code}\n" \
        https://api.tckdb.example.org/api/v1/health)
    echo "$i $code"
done | tail -15
```

Expected: a run of `200`s followed by `429`s. If you never see `429`,
either `RATE_LIMIT_ENABLED=false` slipped in, or
`TRUSTED_PROXY_HEADER` is misconfigured and every request looks like
it's coming from `127.0.0.1` (which is whitelisted differently — check
the audit log).

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
`.env.pi` and `systemctl restart tckdb-api`.

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
#    leak into a downstream client.
docker compose --env-file .env.pi -f docker-compose.pi.yml stop cloudflared
sudo systemctl stop tckdb-api

# 2. Pin the previous git revision and restart.
cd /opt/tckdb
sudo -u tckdb git checkout <prev-good-sha>
sudo systemctl start tckdb-api
docker compose --env-file .env.pi -f docker-compose.pi.yml start cloudflared
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

`.env.pi` is the single source of hosted-posture truth. Keep the
previous version in git (in a *private* repo, not this one — it has
secrets) or copy it before edits:

```bash
sudo cp /opt/tckdb/.env.pi /opt/tckdb/.env.pi.$(date +%F)
$EDITOR /opt/tckdb/.env.pi
sudo systemctl restart tckdb-api
# verify smoke tests; if anything regressed:
sudo cp /opt/tckdb/.env.pi.$(date +%F) /opt/tckdb/.env.pi
sudo systemctl restart tckdb-api
```

### Emergency kill switch

```bash
docker compose --env-file .env.pi -f docker-compose.pi.yml stop cloudflared
```

This drops the tunnel and the public URL returns Cloudflare's "tunnel
offline" page. Everything else — Postgres, MinIO, the API — stays up
on loopback so you can debug at leisure.

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

## See also

- [deployment_modes.md](deployment_modes.md) — the three deployment
  modes and how the Pi recipe fits into Mode 2.
- [export_import_roadmap.md](../roadmaps/export_import_roadmap.md) —
  cross-instance data movement (offline → hosted, etc.).
- [`.env.pi.example`](../../.env.pi.example) — full annotated env file.
- [`docker-compose.pi.yml`](../../docker-compose.pi.yml) — the data-plane stack.
- [`examples/deployment/systemd/`](../../examples/deployment/systemd/) — host service units.
- [`docs/audits/security_public_read_abuse_audit.md`](../audits/security_public_read_abuse_audit.md) — the threat model that motivates the hosted toggles above.
- [`docs/specs/public_read_abuse_controls.md`](../specs/public_read_abuse_controls.md) — the implemented abuse-control spec these env vars wire.
