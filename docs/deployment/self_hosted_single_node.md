# Public server

This page turns a [self-hosted](README.md) stack into a public instance on
one small Linux server. Anyone can read `/api/v1/scientific/*`. Only accounts
you create can write. Every abuse control is on.

The example uses Cloudflare Tunnel, so the server needs no open inbound port.
A reverse proxy with a certificate works the same way; see
[Ingress options](#ingress-options).

Before anyone else can reach it, tick every row of the
[Production checklist](production_checklist.md).

## The host

- 64-bit Linux. Debian, Ubuntu and Raspberry Pi OS (64-bit) are the easiest,
  because Docker publishes packages for them. A Raspberry Pi 4 or 5 works;
  32-bit Raspberry Pi OS does not.
- 4 GB of RAM, 8 GB more comfortable under search traffic.
- An SSD for the Docker volumes. An SD card wears out under database writes
  within months.
- Wired Ethernet if you can.

Install the basics and Docker Engine from Docker's apt repository (`docker.io`
from the distribution also works but lags):

```bash
sudo apt update
sudo apt install -y ca-certificates curl gnupg git openssl ufw fail2ban unattended-upgrades jq
curl -fsSL https://download.docker.com/linux/debian/gpg | \
    sudo gpg --dearmor -o /usr/share/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker.gpg] \
    https://download.docker.com/linux/debian bookworm stable" | \
    sudo tee /etc/apt/sources.list.d/docker.list
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

On Ubuntu, replace `debian` and `bookworm` with `ubuntu` and your release
name.

Run TCKDB as a dedicated, unprivileged user, never as root or a default
account such as `pi` or `ubuntu`:

```bash
sudo adduser --system --group --home /var/lib/tckdb tckdb
sudo usermod -aG docker tckdb
sudo install -d -o tckdb -g tckdb /opt/tckdb
```

## Install

```bash
sudo -u tckdb git clone https://github.com/TCKDB/TCKDB.git /opt/tckdb
cd /opt/tckdb
sudo -u tckdb backend/scripts/ops/tckdb_make_env.sh --mode hosted_public \
    --public-url https://tckdb.example.org --proxy-header CF-Connecting-IP
sudo -u tckdb docker compose --env-file .env.selfhosted --env-file .env.db-admin --profile selfhost up -d --wait
sudo -u tckdb backend/scripts/check_selfhosted_deployment.sh
```

The script still asks for the administrator's username and email. Run the
later `docker compose` commands on this page as `tckdb` too.

`--public-url` is the address people will use, without `/api/v1`. The web UI
answers at its root and the API under `/api/v1`. `CF-Connecting-IP` is the
header Cloudflare sets; see [Trusted proxy header](#trusted-proxy-header).

## The settings that matter

`tckdb_make_env.sh` writes safe values for all of these.
`DEPLOYMENT_MODE=hosted_public` makes the API refuse to start if one is
wrong.

| Variable | Value | Why |
|---|---|---|
| `DEPLOYMENT_MODE` | `hosted_public` | Turns on the startup safety check. |
| `AUTH_ALLOW_OPEN_REGISTRATION` | `false` | Only accounts you create. |
| `EXPOSE_API_DOCS` | `false` | No live request console on the public site. |
| `EXPOSE_API_REFERENCE` | your choice, default `false` | `true` publishes the read-only `/redoc` and `/openapi.json`. `/docs` stays off. |
| `LEGACY_READS_REQUIRE_AUTH` | `true` | The legacy `/api/v1/{thermo,…}` routes expose database row IDs. |
| `ALLOW_PUBLIC_INTERNAL_IDS` | `false` | Clients use public refs. |
| `RATE_LIMIT_ENABLED` | `true` | Abuse control. |
| `PAGING_CURSOR_KEY` | random, 32+ characters | Seals paging cursors. Changing it only breaks cursors in flight. |
| `SESSION_COOKIE_SECURE` | `true` | Cookies only over HTTPS. Cloudflare terminates TLS. |
| `TRUSTED_PROXY_HEADER` | `CF-Connecting-IP` | See below. |
| `DB_STATEMENT_TIMEOUT_MS` | `30000` or lower | Caps any one query. |

The rate-limit budgets live in [`.env.selfhosted.example`](../../.env.selfhosted.example)
and the [Production checklist](production_checklist.md#recommended-settings).

## Trusted proxy header

The rate limiter keys on the client's IP address. Behind a tunnel or proxy,
every request arrives from the proxy, so the API reads the real address from
`TRUSTED_PROXY_HEADER`.

Behind Cloudflare, use `CF-Connecting-IP`. Cloudflare's edge sets it and it
reaches the server over the authenticated tunnel. Don't use
`X-Forwarded-For`: Cloudflare passes on whatever the client sent in it.

If you change the ingress, change this too. The right value is the header
that your ingress always overwrites.

## Ingress options

Pick one. Two ingresses in front of one API fight each other.

| Option | Gives you | Set up |
|---|---|---|
| Cloudflare Tunnel, as the compose `cloudflared` service | A public HTTPS hostname, no open inbound port | Below |
| Cloudflare Tunnel, as a `cloudflared` system service | The same | Cloudflare's instructions; don't also start the compose service |
| nginx, Caddy or Traefik | TLS in front of `127.0.0.1:8011` | Your proxy config; see [Lab server](shared-private-deployment.md#reverse-proxy) |
| Tailscale, WireGuard or an SSH tunnel | Private access only, no public surface | Your overlay network |

## Cloudflare Tunnel

Three things have to line up:

- a **DNS record** that sends the name to Cloudflare;
- a **tunnel**, an outbound connection from `cloudflared` on your server to
  Cloudflare;
- an **ingress rule** in the tunnel that forwards the hostname to a local
  address.

Creating one of them routes nothing on its own.

You need a Cloudflare account, a zone such as `example.org`, and Zero Trust
enabled (the free tier is enough).

1. **Create the tunnel** in the Zero Trust dashboard: Networks → Tunnels →
   Create a tunnel → Cloudflared. Copy the token it prints.
2. **Store the token** in `.env.selfhosted`, never in the template:

    ```dotenv
    CLOUDFLARED_TUNNEL_TOKEN=eyJhIjoi...
    ```

3. **Add a public hostname** to the tunnel: subdomain `tckdb`, domain
   `example.org`, type `HTTP`, URL `127.0.0.1:8011`. That serves the web UI
   and the API. Use `127.0.0.1:8010` instead for the API alone. Add no other
   rule: never 5432 (Postgres), 9000 (the object store) or 9001.
4. **Start it:**

    ```bash
    docker compose --env-file .env.selfhosted --env-file .env.db-admin \
        --profile cloudflare up -d cloudflared
    ```

Cloudflare manages the certificate. Set the zone's SSL/TLS mode to
**Full (strict)**.

A new hostname can fail with "could not resolve host" for a few minutes while
your resolver caches the earlier "no such name" answer. Wait, or flush the
resolver; see [Troubleshooting](troubleshooting.md#cloudflare-tunnel-and-dns).

### Optional: a database GUI through the tunnel

To use DataGrip, DBeaver or `psql` against the server without publishing
Postgres, add a second public hostname of type `TCP` with URL
`tcp://127.0.0.1:5432`, for example `pg-tckdb.example.org`. Protect it with a
Cloudflare Access application whose policy admits only you. Then, on your
workstation:

```bash
cloudflared access tcp --hostname pg-tckdb.example.org --url localhost:15434
```

Connect the GUI to `127.0.0.1:15434` with the `DB_NAME`, `DB_USER` and
`DB_PASSWORD` from `.env.selfhosted`. Three checks stand in the way of an
attacker: the Access policy, the tunnel token Access issues, and the database
password. A leaked password alone does not reach the database.

## Statement timeout

Two limits, both on by default:

- `DB_STATEMENT_TIMEOUT_MS=30000` in `.env.selfhosted` applies to every
  connection the API opens.
- `init` sets the same limit on the runtime database role when it provisions
  the roles, so every client of that role inherits it. Check it:

    ```bash
    docker compose --env-file .env.selfhosted --env-file .env.db-admin exec db \
        psql -U tckdb_app -d tckdb -c 'SHOW statement_timeout;'
    ```

    Use your `DB_USER` and `DB_NAME` if you changed them.

Tighten it, to 10 s for example, once you have seen a few days of real
queries.

## Backups and restore

### Nightly database dump

The example [backup unit and timer](../../examples/deployment/systemd/) run
`pg_dump` in the `db` container every night at 03:15. They write
`/var/backups/tckdb/tckdb-YYYY-MM-DD.sql.gz` and delete dumps older than 30
days. They expect the repository at `/opt/tckdb` and run as `tckdb`.

```bash
sudo cp examples/deployment/systemd/tckdb-backup.service /etc/systemd/system/
sudo cp examples/deployment/systemd/tckdb-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tckdb-backup.timer
sudo systemctl list-timers | grep tckdb-backup
```

### Copy it off the machine

A backup on the same disk is not a backup. Sync `/var/backups/tckdb/` to
another machine, for example with `rclone sync /var/backups/tckdb remote:tckdb-backups`
or `rsync` to a NAS. Encrypt it where it is stored.

### Files

Copy the artifact bucket out through the S3 API with any S3 client. With an
`rclone` remote named `tckdb` that points at `http://127.0.0.1:9000` with the
keys from `.env.selfhosted`:

```bash
rclone sync tckdb:tckdb-artifacts /srv/backups/tckdb-artifacts/$(date +%F)
```

Keep copies of `.env.selfhosted` and `.env.db-admin` somewhere private as
well. Never commit them.

### Restore

Run this on a scratch copy at least once a quarter. A backup you have never
restored is a guess.

```bash
C="docker compose --env-file .env.selfhosted --env-file .env.db-admin --profile selfhost"

$C stop frontend tckdb-api
$C exec -T db psql -U tckdb_admin -d postgres \
    -c "DROP DATABASE IF EXISTS tckdb;" \
    -c "CREATE DATABASE tckdb OWNER tckdb_owner ENCODING 'UTF8' TEMPLATE template0;"

# A nightly .sql.gz dump:
gunzip -c /var/backups/tckdb/tckdb-YYYY-MM-DD.sql.gz | $C exec -T db psql -U tckdb_admin -d tckdb
# or a .dump file from the Self-host page:
$C exec -T db pg_restore -U tckdb_admin -d tckdb < tckdb-YYYY-MM-DD.dump

$C up -d --wait
```

The names are the template defaults: `DB_ADMIN_USER`, `DB_OWNER_USER` and
`DB_NAME`. Use yours if you changed them.

Keep `ENCODING 'UTF8' TEMPLATE template0`. Without it the new database copies
`template1`, which on many clusters is `SQL_ASCII`. The restore then succeeds
and every non-ASCII string is silently wrong. See
[Troubleshooting](troubleshooting.md#the-cluster-template-disagrees-with-the-production-database).

## Upgrade and roll back

Upgrade as on the [Self-host page](README.md#upgrade): back up, set
`TCKDB_IMAGE_TAG` in `.env.selfhosted`, run `up -d --wait`. `init` migrates
before the new API starts. On a database with data, read the
[migration runbook](../../backend/docs/deployment/migrations.md) first.

To roll back:

1. Stop the ingress so nobody reads a half-upgraded instance:
   `docker compose --env-file .env.selfhosted --env-file .env.db-admin stop cloudflared`
   (or stop your proxy).
2. Set `TCKDB_IMAGE_TAG` back to the previous tag.
3. If the upgrade ran a migration, [restore](#restore) the last backup.
4. Run `up -d --wait`, check it, then start the ingress again.

Copy the env file before you edit it, so a bad setting is one `cp` away from
undone:

```bash
cp .env.selfhosted .env.selfhosted.$(date +%F)
```

**Emergency stop.** Stopping the ingress takes the instance off the internet.
For Cloudflare Tunnel the URL then shows the "tunnel offline" page. The
database, object store and API keep running on 127.0.0.1, so you can debug
from the server.

## Logs

- Docker logs: the compose file caps each service at five 10 MB files.
- journald (the backup unit): cap it in `/etc/systemd/journald.conf` with
  `SystemMaxUse=500M` and `SystemKeepFree=1G`, then
  `sudo systemctl restart systemd-journald`.
- Set `LOG_FORMAT=json` (the template default) when you ship logs to a
  collector. Each line then carries `request_id`, `level`, `logger` and
  `message`.

## SSH and host hardening

The instance is public through Cloudflare. SSH should not be.

```text
# /etc/ssh/sshd_config.d/tckdb.conf
PasswordAuthentication no
PermitRootLogin no
KbdInteractiveAuthentication no
```

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow from 192.168.0.0/16 to any port 22 proto tcp
sudo ufw enable
```

Better still, add an `SSH` public hostname to the tunnel for `127.0.0.1:22`
behind a Cloudflare Access policy, then close port 22 to the LAN too.

Also enable `fail2ban` for SSH and `unattended-upgrades` for security
updates, and turn off Avahi/mDNS on an untrusted network.

## Health and monitoring

```bash
curl -fsS https://tckdb.example.org/api/v1/health   # {"status":"ok"}: the process is up and reaches the database
curl -fsS https://tckdb.example.org/api/v1/readyz   # {"status":"ready","database":"ok","alembic_revision":"<rev>"}
```

- `/health` failing means the tunnel is misrouted or the API container is
  down (`docker compose ... ps`).
- `/readyz` failing while `/health` passes means the database is unreachable
  or not migrated. Fix it before sending traffic.

Point an external uptime check (UptimeRobot, healthchecks.io) at `/readyz`
and alert after three failures in a row. Every response carries an
`X-Request-ID` header. Clients may send their own, and the server echoes it
when it is safe. More in the
[monitoring runbook](../../backend/docs/deployment/monitoring.md).

## Smoke tests

Run these from another machine, so they go through the tunnel.

```bash
base=https://tckdb.example.org/api/v1

# Anonymous read works.
curl -fsS -G "$base/scientific/species/search" --data-urlencode "smiles=O"

# Legacy routes need a login: expect 401.
curl -s -o /dev/null -w "%{http_code}\n" "$base/thermo/"

# The web UI answers at the root: expect 200.
curl -s -o /dev/null -w "%{http_code}\n" https://tckdb.example.org/

# Postgres is not reachable: expect a refusal or a timeout.
nc -vz tckdb.example.org 5432
```

If `nc` connects, stop the stack. Something forwards a port you did not mean
to open.

On the server, check the API's own address. The web UI answers every path
outside `/api/`, so through the tunnel these return 200 whatever the setting.

```bash
# The /docs console is off: expect 404.
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8010/docs
# Expect 404, or 200 if you set EXPOSE_API_REFERENCE=true.
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8010/openapi.json
```

**Rate limit.** The 61st anonymous search in one minute should get `429`
(`RATE_LIMIT_ANON_READ_PER_MINUTE=60`). `/health` is exempt, so use a search:

```bash
for i in $(seq 1 70); do
    curl -s -o /dev/null -w "%{http_code}\n" -X POST "$base/scientific/reactions/search" \
        -H "Content-Type: application/json" \
        --data '{"reactants":["CC"],"products":["C[CH2]"],"limit":1}'
done | sort | uniq -c
```

Expect a run of `200`s, then `429`s whose body names `"bucket":"anon_read"`.
Don't send an API key in this loop: that moves the requests to the
authenticated bucket (300 per minute). No `429` at all means
`RATE_LIMIT_ENABLED` is off or `TRUSTED_PROXY_HEADER` is wrong.

## Closing the object store to the host

On Linux, also install the [object store firewall](storage-firewall.md). It
keeps local processes and host-network containers such as `cloudflared` off
SeaweedFS's unauthenticated internal ports.

## Running the API outside compose

Two other layouts start only `db` and `seaweedfs` from compose:

- `backend/scripts/ops/tckdb_deploy.sh` runs a published API image as a
  container it manages. It backs up, migrates, swaps the container and
  checks `/status`. Use it for an external S3 store, which the `selfhost`
  profile does not support. It passes its env file to the container
  unchanged, so `S3_ENDPOINT_URL` there must be an address the container can
  reach: `http://seaweedfs:9000` for the bundled store.
- The API on the host under systemd, from the conda environment: see
  [Without Docker](native-advanced.md).

Keep a single API process either way. The rate limiter counts in process
memory, so each extra Uvicorn worker multiplies every budget; see
[Rate limiter and Uvicorn workers](production_checklist.md#rate-limiter-and-uvicorn-workers).

## What this setup does not do

- **Sync with other instances.** It is independent. Records move between
  instances as [contribution bundles](../contribution-bundles/format.md).
- **Cache API responses at Cloudflare.** Cloudflare does not cache
  `/api/v1/*` by default. Leave it that way.
- **Run a managed database.** Postgres runs in Docker on the same machine.
  For heavy write traffic, move to a managed Postgres with RDKit and keep this
  machine for the API.
