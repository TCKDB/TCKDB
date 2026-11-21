# Self-host TCKDB

Run your own TCKDB with Docker. Three commands give you the database, the
object store, the API and the web UI, with every password generated for you.

<figure class="tk-figure">
--8<-- "assets/install-flow.svg"
</figure>

## What you need

- Git, `bash` and `openssl`.
- Docker Engine or Docker Desktop with Compose v2. Check with `docker compose version`.
- A 64-bit machine, x86_64 or arm64. The images are built for both, so a
  Raspberry Pi 4 or 5 on a 64-bit OS works.
- About 5 GB of disk for the images, plus room for your data. Use an SSD, not an SD card.
- 4 GB of RAM. 8 GB is more comfortable.

## Install

Run these in order. After `cd TCKDB`, every command on this page runs from
the repository root.

```bash
git clone https://github.com/TCKDB/TCKDB.git
cd TCKDB
backend/scripts/ops/tckdb_make_env.sh
docker compose --env-file .env.selfhosted --env-file .env.db-admin \
    --profile selfhost up -d --wait
```

`make selfhost` does the last two in one go. It runs the env script only if
the env files are missing.

**1. Clone.** Gets the compose file, the env templates and the scripts. The
application itself comes as published images.

**2. Make the env files.** `tckdb_make_env.sh` asks a few questions. Press
Enter to take the default:

| Question | Default |
|---|---|
| Who should reach TCKDB? | this machine only. See [Make it reachable](#make-it-reachable) for the other three answers |
| Deployment mode | `shared_private`, or `hosted_public` for the internet answers (`local` for a laptop) |
| Administrator username | `admin` |
| Administrator email | none |

It writes two files, both readable by you only:

- `.env.selfhosted` holds the API's settings and secrets.
- `.env.db-admin` holds the database admin and owner passwords and the first
  administrator. The database container and the one-off `init` container
  use it. The API never sees it.

It generates every password and prints the administrator's password once.
Save it. `--yes` takes every default without asking, each question has a
flag, and `--set KEY=VALUE` sets anything else, such as
`--set TCKDB_WEB_PORT=9011`. Run `tckdb_make_env.sh --help` for the list.
It will not overwrite existing files unless you pass `--force`.

**3. Start the stack.** Compose starts the services in this order:

1. `db` (Postgres with the RDKit cartridge) and `seaweedfs` (the S3 object store).
2. `init` sets up the database roles, runs the migrations, creates the first
   administrator and exits. It runs on every start. Steps already done are
   skipped, with one exception: if the first administrator was deactivated,
   `init` reactivates it, and if its role was changed, `init` fails and the
   API does not start. To retire that account, change
   `TCKDB_BOOTSTRAP_USERNAME` in `.env.db-admin` to the account that should
   stay an administrator.
3. `tckdb-api` and `frontend` start once `init` has succeeded.

`--wait` returns when everything is healthy. The first start downloads the
images. With the images already downloaded, the stack is healthy about 50
seconds after `git clone`.

The stack refuses to start while a secret is empty or still a `change-me`
placeholder.

## What you get

| | Address |
|---|---|
| Web UI | <http://127.0.0.1:8011> |
| API | `http://127.0.0.1:8010/api/v1` |
| Administrator | the username you chose, with the printed password |
| Data | Compose volumes `tckdb_pgdata` (database) and `tckdb_seaweedfs` (files) |

Open the web UI and log in as the administrator. With the default answer
every port binds to 127.0.0.1, so nothing is reachable from another machine
yet.

## Make an API key

Scripts, `tckdb-client` and ARC send an API key with every upload. Log in
and mint one:

```bash
TCKDB_BASE_URL=http://127.0.0.1:8010/api/v1 \
    backend/scripts/tckdb_auth.sh login-create-key
source .tckdb_auth.env
```

The script asks for your username and password, then writes `TCKDB_BASE_URL`
and `TCKDB_API_KEY` to `.tckdb_auth.env` (mode 600). Point any client at
those two values. More users, roles and key handling are on
[Admin accounts and API keys](admin_auth_quickstart.md).

## Check it

```bash
backend/scripts/check_selfhosted_deployment.sh
```

It checks that no secret is a placeholder, that every container is running
and healthy, that the RDKit extension is installed, that the database is at
the latest migration, and that the API answers. It exits non-zero if any
check fails.

## Make it reachable

TCKDB starts on this machine only. To let others in, answer the env
script's first question, or pass `--access`. The script then sets the
address, the cookies and the rate limiter's client header together, and
prints the start command.

<figure class="tk-figure">
--8<-- "assets/reachable-ports.svg"
</figure>

| Who reaches it | `--access` | Ports opened | Profiles |
|---|---|---|---|
| This machine only | `local` (default) | none | `selfhost` |
| Your lab network | `lan` | the web UI port, on the lab address | `selfhost` |
| The internet, with your domain name | `https` | 80/tcp, 443/tcp, 443/udp | `selfhost`, `caddy` |
| The internet, no open port | `cloudflare` | none | `selfhost`, `cloudflare` |

The database (5432), the object store (9000) and the API port (8010) stay on
127.0.0.1 in every option. The script records the profiles in
`.env.selfhosted` as `COMPOSE_PROFILES`, so `make selfhost`, and any
`docker compose` command given no `--profile`, starts the right services.

### 1. This machine only

The default. Open <http://127.0.0.1:8011>.

### 2. Your lab network

You need this machine's IPv4 address on the lab network. If the machine
runs a firewall, allow TCP 8011 from the lab.

```bash
backend/scripts/ops/tckdb_make_env.sh --access lan --lan-address 192.168.1.20
docker compose --env-file .env.selfhosted --env-file .env.db-admin \
    --profile selfhost up -d --wait
```

Options 2 and 3 need release 0.3.0 or newer, the default. With 0.2.0 the API
refuses the plain-HTTP cookie setting and does not start, and the web UI passes
on any `X-Real-IP` a client sends.

Without `--lan-address` the script offers the address of the default route.
It sets:

- `TCKDB_WEB_BIND=192.168.1.20`. The web UI listens on that address, and no
  longer on 127.0.0.1.
- `TCKDB_PUBLIC_BASE_URL=http://192.168.1.20:8011/api/v1`.
- `TRUSTED_PROXY_HEADER=X-Real-IP`. The web UI sets `X-Real-IP` to the
  address each lab machine connects from, and drops any value the client
  sent.
- `SESSION_COOKIE_SECURE=false` and `SESSION_COOKIE_ALLOW_PLAIN_HTTP=true`.
  This is plain HTTP, so the login cookie crosses the network unencrypted.
  Use it only on a network you trust. Otherwise use option 3, or your own
  proxy with a lab certificate.

Check from another machine on the network:

```bash
curl -i http://192.168.1.20:8011/api/v1/health
```

Then open `http://192.168.1.20:8011` and log in.

Docker opens a published port ahead of ufw and firewalld rules. To limit
which machines can connect, filter at the network's firewall or in Docker's
`DOCKER-USER` chain. The script warns if the address is a public one.

Rootless Docker does not pass on the client's address: every lab machine
then looks like one client to the rate limiter.

### 3. The internet, with your domain name

You need:

- a domain name with a DNS A record (AAAA for IPv6) pointing at the
  server's public address;
- TCP 80 and 443, and UDP 443, reaching the server from the internet. Let's
  Encrypt checks the domain on port 80 or 443 before it issues a
  certificate.

```bash
backend/scripts/ops/tckdb_make_env.sh --access https --domain tckdb.example.org \
    --acme-email admin@example.org
docker compose --env-file .env.selfhosted --env-file .env.db-admin \
    --profile selfhost --profile caddy up -d --wait
```

Options 2 and 3 need release 0.3.0 or newer, the default.

The `caddy` profile adds [Caddy](https://caddyserver.com/) with
`deploy/caddy/Caddyfile`. On first start it gets a certificate for the
domain. It renews it, redirects HTTP to HTTPS, and passes every request to
the web UI over the compose network. The web UI port stays on 127.0.0.1.

The script sets `TCKDB_DOMAIN`, `TCKDB_ACME_EMAIL` (optional),
`TCKDB_PUBLIC_BASE_URL=https://tckdb.example.org/api/v1` and
`TRUSTED_PROXY_HEADER=X-Real-IP`. Caddy replaces `X-Real-IP` with the
address it was reached from. The mode is `hosted_public`; pass
`--mode shared_private` for a private group server.

Check:

```bash
curl -i https://tckdb.example.org/api/v1/health
docker compose --env-file .env.selfhosted --env-file .env.db-admin logs caddy
```

If no certificate arrives, the Caddy log says why. Usually the DNS record
does not point at the server yet, or port 80 or 443 is blocked.

### 4. The internet, with no open port

A tunnel dials out from the server. No port is opened and the server needs
no public address.

**Cloudflare Tunnel.** You need a domain on Cloudflare and a tunnel: in the
Zero Trust dashboard, Networks → Tunnels → Create a tunnel → Cloudflared.
Copy its token. Give the tunnel a public hostname with the service
`http://127.0.0.1:8011`.

```bash
backend/scripts/ops/tckdb_make_env.sh --access cloudflare --domain tckdb.example.org
docker compose --env-file .env.selfhosted --env-file .env.db-admin \
    --profile selfhost --profile cloudflare up -d --wait
```

The script asks for the token. Without a terminal, add
`CLOUDFLARED_TUNNEL_TOKEN=<token>` to `.env.selfhosted`. It sets
`TRUSTED_PROXY_HEADER=CF-Connecting-IP`, which Cloudflare's edge sets.
Check with `curl -i https://tckdb.example.org/api/v1/health`.
[Public server](self_hosted_single_node.md#cloudflare-tunnel) has the
details.

**Tailscale.** For devices on your tailnet. You need Tailscale on the
server, with MagicDNS and HTTPS certificates turned on for the tailnet.

```bash
backend/scripts/ops/tckdb_make_env.sh --public-url https://tckdb.your-tailnet.ts.net
docker compose --env-file .env.selfhosted --env-file .env.db-admin \
    --profile selfhost up -d --wait
tailscale serve --bg 8011
```

`tailscale serve` gives the web UI an HTTPS address on the tailnet,
`https://<machine name>.<tailnet name>.ts.net`. Use that address as the
public URL. Leave `TRUSTED_PROXY_HEADER` unset. Check from another tailnet
device with `curl -i https://<machine name>.<tailnet name>.ts.net/api/v1/health`.
`tailscale funnel --bg 8011` opens the same address to the whole internet.

### Your own proxy

Already run nginx, Traefik or an institutional proxy? Keep option 1 and
point the proxy at `127.0.0.1:8011` for the web UI and the API together, or
`127.0.0.1:8010` for the API alone.

<figure class="tk-figure">
--8<-- "assets/what-runs-where.svg"
</figure>

Set `TCKDB_PUBLIC_BASE_URL` to the public address, ending in `/api/v1`. Set
`TRUSTED_PROXY_HEADER=X-Real-IP` only if the proxy overwrites `X-Real-IP`
with the client's address. The web UI keeps an `X-Real-IP` that arrives
from this machine, so a proxy that passes the client's value on lets the
client choose its own rate-limit key. [Lab server](shared-private-deployment.md#reverse-proxy)
has an nginx example.

The web UI trusts `X-Real-IP` only from its own IPv4 Docker networks, which
is where a connection to `127.0.0.1:8011` arrives from. Point the proxy at
`127.0.0.1`, not at `localhost` (which may resolve to `::1`) or the
machine's own address. From anywhere else the header is replaced and every
request looks like it came from the proxy.

### Change an existing install

Edit `.env.selfhosted` to match the option: `TCKDB_WEB_BIND`,
`TCKDB_PUBLIC_BASE_URL`, `TRUSTED_PROXY_HEADER`, `DEPLOYMENT_MODE`, the two
cookie settings, `TCKDB_DOMAIN` and `COMPOSE_PROFILES`. Then run `up -d --wait`
with the option's profiles. Compose recreates the containers whose settings
changed. Don't rerun the script with `--force`: it makes new database
passwords, which the existing database refuses.

## Upgrade

1. [Back up](#back-up) first.
2. Update the checkout with `git pull`.
3. Set the image tag in `.env.selfhosted`: `TCKDB_IMAGE_TAG=<tag>`, where the
   tag is a release such as `0.3.0` or `sha-<commit>`. One tag names both the
   API and the web UI image, so a `sha-` tag must come from a commit that
   built both. A release tag always does.
4. Run the `up -d --wait` command again.

`init` runs the migrations before the new API starts. Keep the tag in the env
file. Passed only on the command line, it reverts to the compose default on
the next `up`.

To roll back, set the previous tag. If the upgrade ran a migration, restore
the database backup as well.

## Back up

Back up three things:

- **The database.** This writes a dump to the current directory:

    ```bash
    docker compose --env-file .env.selfhosted --env-file .env.db-admin exec -T db \
        sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > tckdb-$(date +%F).dump
    ```

    Keep the `-T`. Without it Docker allocates a terminal, which corrupts the
    binary dump.

- **The files** in SeaweedFS. Copy the bucket with any S3 client, using the
  endpoint `http://127.0.0.1:9000` and the `S3_ACCESS_KEY` / `S3_SECRET_KEY`
  from `.env.selfhosted`.
- **Both env files.** The database roles keep the passwords they were created
  with, so a restore needs the same files.

Copy the backups to another machine. Scheduled backups, restore and rollback
are on [Public server](self_hosted_single_node.md#backups-and-restore).

## Stop and remove

```bash
docker compose --env-file .env.selfhosted --env-file .env.db-admin \
    --profile selfhost down
```

Pass the same profiles you start with, such as `--profile caddy`. `down`
keeps the data. `down -v` deletes the volumes, and with them every record
and file.

The database passwords are set when the volume is first created. To change
one later, change the role first with `ALTER ROLE`, then the env file. The
command is in `.env.db-admin.example`. Use `tckdb_make_env.sh --force` only
together with a fresh volume.

## Good to know

- The `selfhost` profile always uses the bundled SeaweedFS. For an external
  S3 store (AWS S3, Google Cloud Storage, an existing MinIO), run the API
  container with `backend/scripts/ops/tckdb_deploy.sh` instead. Its header
  lists the settings.
- `TCKDB_BOOTSTRAP_PASSWORD` can be removed from `.env.db-admin` after the
  first start.
- `TCKDB_API_PORT`, `TCKDB_WEB_PORT`, `DB_PORT` and `S3_PORT` move the
  loopback ports. `TCKDB_HTTP_PORT` and `TCKDB_HTTPS_PORT` move Caddy's.
- Uploads are processed inside the API by default. To run a separate upload
  worker from the same image, set `TCKDB_INLINE_WORKER=false` and add
  `--profile worker` to the `up` command.

## Next

- [Which setup do I need?](deployment_modes.md)
- [Production checklist](production_checklist.md) before anyone else can
  reach it.
- [Troubleshooting](troubleshooting.md)
- Operator runbooks on GitHub:
  [migrations on a live database](../../backend/docs/deployment/migrations.md),
  [monitoring](../../backend/docs/deployment/monitoring.md),
  [moving from MinIO to SeaweedFS](../../backend/docs/deployment/migrating_minio_to_seaweedfs.md),
  [cutting a citable dataset release](../../backend/docs/deployment/cutting_a_dataset_release.md).
