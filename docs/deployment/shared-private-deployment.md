# Lab server

A lab or group shares one TCKDB on a machine its members, notebooks and
cluster jobs can reach. The install is the [Self-host](README.md) one. What
changes is who can connect and how accounts are handed out.

Use a lab server when several people need the same records, when cluster jobs
need a fixed upload target, or when the data should stay private until it is
ready to submit to a public instance. One person on one machine does not need
one.

Before anyone else can reach it, tick every row of the
[Production checklist](production_checklist.md).

## Install

Follow [Self-host](README.md) and pick how the lab reaches it from
[Make it reachable](README.md#make-it-reachable):

- **Lab network, plain HTTP** (`--access lan`): the web UI is published on
  the server's lab address. Quickest, but session cookies cross the network
  unencrypted, so use it only on a network you trust.
- **HTTPS with a domain name** (`--access https --mode shared_private`):
  needs a public DNS name and ports 80 and 443 open to the internet, for the
  certificate.
- **Your own proxy** with a lab or institutional certificate, below. Keep
  the default access and answer `shared_private`.
- **Tailscale**, for a lab that already shares a tailnet.

Keep the deployment mode at `shared_private`. It makes the API refuse to start while a safety setting is
unsafe: open registration, `/docs`, unauthenticated legacy routes, internal
IDs, insecure cookies or rate limiting off. The error lists every problem at
once. Plain HTTP passes only because the script also sets
`SESSION_COOKIE_ALLOW_PLAIN_HTTP=true`.

## Accounts

Registration is closed. `init` creates the first administrator. Create
everyone else with `bootstrap_admin.py` in the API container:

```bash
read -rsp "New user's password: " PW; echo
docker compose --env-file .env.selfhosted --env-file .env.db-admin --profile selfhost \
    exec -e TCKDB_BOOTSTRAP_PASSWORD="$PW" tckdb-api \
    /opt/conda/bin/python scripts/bootstrap_admin.py \
    --username alice --email alice@lab.example.org --role user
unset PW
```

`--role` is `user`, `curator` or `admin`, and it defaults to `admin`, so pass
it. Running the command again for an existing user reactivates the account.
It changes the role only with `--force-role-change`. An administrator can also
change roles through the admin API.

## API keys

Each person logs in once and mints keys for their scripts, notebooks and
jobs:

```bash
TCKDB_BASE_URL=https://tckdb.lab.example.org/api/v1 backend/scripts/tckdb_auth.sh login-create-key --name alice-hpc
```

- Give each person their own keys. A key shared by the whole lab loses who
  uploaded what, and revoking it stops everyone.
- Use one key per place (`alice-laptop`, `alice-hpc`), so one can be revoked
  alone.
- A key has its owner's role. Mint keys from the account with the least
  privilege that does the job.
- To rotate a key, mint the new one, switch to it, then revoke the old one. A
  user can hold several keys at once, so nothing stops.

More in [Admin accounts and API keys](admin_auth_quickstart.md) and
[Configuring a client](../clients/generic-client-targeting.md#api-key-safety).

## Reverse proxy

Put a proxy in front for TLS and a lab hostname. It must:

1. terminate TLS with a lab or institutional certificate;
2. pass the `X-API-Key` header through unchanged;
3. pass `Set-Cookie` through unchanged, so browser login works;
4. set the client's address in a header the API can trust (`X-Real-IP`).

A starting point for nginx, in front of the web UI, which passes `/api/` on
to the API:

```nginx
server {
    listen 443 ssl;
    server_name tckdb.lab.example.org;

    ssl_certificate     /etc/ssl/lab/tckdb.crt;
    ssl_certificate_key /etc/ssl/lab/tckdb.key;

    # Uploads can carry ESS logs. The web UI accepts up to 100 MB on upload
    # routes and 10 MB elsewhere; nginx refuses anything over 1 MB by default.
    client_max_body_size 100m;

    location / {
        proxy_pass http://127.0.0.1:8011;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Set `TRUSTED_PROXY_HEADER=X-Real-IP` to match. To serve the API alone, proxy
to `127.0.0.1:8010` instead.

The proxy must overwrite `X-Real-IP` with the client's address, as the
`$remote_addr` line does. The web UI keeps an `X-Real-IP` that arrives from
this machine, so behind a proxy that passes the client's value on, a client
could pick its own rate-limit key. Caddy and Traefik pass cookies and
`X-API-Key` through by default, but don't set `X-Real-IP`: add a header rule
that sets it (Caddy: `header_up X-Real-IP {remote_host}`, as in
`deploy/caddy/Caddyfile`), or leave `TRUSTED_PROXY_HEADER` unset.

Route only the web UI and the API through the proxy. The database, the
object store and the MinIO console stay private.

## Network and firewall

- Keep the server on the lab network or behind the institution's VPN, under
  an internal DNS name. A lab server rarely needs to be on the public
  internet.
- If the proxy must be public, allow only the source ranges that need it,
  such as cluster login nodes and lab subnets.
- Postgres (5432), the object store (9000) and the API (8010) bind to
  127.0.0.1 in every access option. Only the web UI, or Caddy in front of
  it, is opened.
- With `--access lan`, Docker opens the web UI port ahead of ufw and
  firewalld rules. Limit it at the network's firewall or in Docker's
  `DOCKER-USER` chain.
- Revoke a person's keys when they leave.

## Health checks

```bash
curl -i https://tckdb.lab.example.org/api/v1/health   # the process is up and reaches the database
curl -i https://tckdb.lab.example.org/api/v1/readyz   # also checks the schema is migrated
```

A healthy `/readyz` returns `200` with
`{"status":"ready","database":"ok","alembic_revision":"<rev>"}`. A failing
one returns `503` with a `code` (`database_unavailable`,
`schema_not_initialized`) and never the database URL or a stack trace.

Every response carries an `X-Request-ID` header. Scripts that log it can be
matched to the API's log. Set `LOG_FORMAT=json` to get one JSON object per log
line.

## Backup and restore basics

A lab server holds records the lab cares about. Back up three things:

1. **The database:** identity, results, provenance, submissions.
2. **The object store:** logs and other files kept outside the database. A
   database backup alone loses them.
3. **The configuration:** both env files and the proxy config. Keep them in
   a private place, never in a public repository.

The commands are on [Self-host](README.md#back-up), and the scheduled
version and the restore steps are on
[Public server](self_hosted_single_node.md#backups-and-restore). On a managed
S3 store, use its versioning, and still check it.

Restore in this order: configuration, an empty database, the dump, the
files, then start the API and check `GET /auth/me` with a key. Try a restore
once before you need one.

Upgrading a database that holds data: read the
[migration runbook](../../backend/docs/deployment/migrations.md) first.

## Clients

Clients need two values:

```bash
export TCKDB_BASE_URL="https://tckdb.lab.example.org/api/v1"
export TCKDB_API_KEY="tck_..."
```

Cluster jobs are clients too; see [Clients on HPC](client-access-from-hpc.md).
Starter env files:
[`lab-server.env.example`](../../examples/deployment/lab-server.env.example)
(server) and [`hpc-client.env.example`](../../examples/deployment/hpc-client.env.example)
(jobs).

## Lab server and public instance

A lab server is separate from any public instance: its own users, keys and
data. Pointing a client at another base URL copies nothing. To publish
selected records, export them as a bundle and submit it with a key from the
public instance; see
[Local to hosted, step by step](../contribution-bundles/manual-local-to-hosted.md).
