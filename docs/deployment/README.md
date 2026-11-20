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

**2. Make the env files.** `tckdb_make_env.sh` asks five questions. Press
Enter to take the default:

| Question | Default |
|---|---|
| Deployment mode | `shared_private` (`hosted_public` for the open internet, `local` for a laptop) |
| Public URL | `http://127.0.0.1:8011` |
| Trusted proxy header | none |
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

Open the web UI and log in as the administrator. Every port binds to
127.0.0.1, so nothing is reachable from another machine yet.

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

## Reach it from other machines

Keep the ports on 127.0.0.1. Put a reverse proxy or a tunnel on the same
machine and point it at:

- `127.0.0.1:8011` for the web UI and the API together (the web UI passes
  `/api/` on to the API), or
- `127.0.0.1:8010` for the API alone.

<figure class="tk-figure">
--8<-- "assets/what-runs-where.svg"
</figure>

Any of these works: nginx or Caddy with a TLS certificate, Cloudflare
Tunnel, or Tailscale. Then:

1. Set `TCKDB_PUBLIC_BASE_URL` in `.env.selfhosted` to the public address,
   ending in `/api/v1`.
2. If the proxy sets the client's address in a header it controls, set
   `TRUSTED_PROXY_HEADER`: `CF-Connecting-IP` for Cloudflare, `X-Real-IP`
   for nginx. Otherwise the rate limiter sees every caller as the proxy.
3. Run the `up -d --wait` command again. Compose recreates the containers
   whose settings changed.

Serve it over HTTPS. Browsers drop the secure session cookie on plain HTTP
from any host but localhost, so logging in fails. On a trusted lab network
you can run plain HTTP in `shared_private` mode: give `tckdb_make_env.sh` an
`http://` public URL and it sets `SESSION_COOKIE_SECURE=false` with
`SESSION_COOKIE_ALLOW_PLAIN_HTTP=true`. The cookie then crosses the network
unencrypted. `hosted_public` always requires HTTPS.

[Public server](self_hosted_single_node.md) covers Cloudflare Tunnel, TLS and
hardening for an internet-facing instance. [Lab server](shared-private-deployment.md)
has an nginx example and the policies for a group.

## Upgrade

1. [Back up](#back-up) first.
2. Update the checkout with `git pull`.
3. Set the image tag in `.env.selfhosted`: `TCKDB_IMAGE_TAG=<tag>`, where the
   tag is a release such as `0.2.0` or `sha-<commit>`.
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

`down` keeps the data. `down -v` deletes the volumes, and with them every
record and file.

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
  loopback ports.
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
