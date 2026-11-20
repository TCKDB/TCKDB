# Troubleshooting

Fixes for problems people have actually hit. Start with the check that fits
your setup:

```bash
backend/scripts/check_selfhosted_deployment.sh   # a self-hosted stack
make doctor                                      # running from source
```

`make doctor` prints a hint next to most of the problems below. Each entry
gives the symptom, the cause, the fix and a way to confirm it.

The commands below shorten the self-hosted compose command to `docker compose
...`. In full it is
`docker compose --env-file .env.selfhosted --env-file .env.db-admin --profile selfhost`.

---

## Self-hosted stack

### `up --wait` fails and `init` exited with an error

**Symptom.** `docker compose ... up -d --wait` reports `init` exited non-zero,
and `tckdb-api` never starts.

**Cause.** `init` stops at the first step that fails: a secret check, the
database roles, the migrations, or creating the administrator. The API starts
only after `init` succeeds.

**Fix.** Read its log:

```bash
docker compose ... logs init
```

- `DB_PASSWORD is empty or still a change-me placeholder` (or another
  secret): the env files were copied by hand. Generate them with
  `backend/scripts/ops/tckdb_make_env.sh`.
- `set TCKDB_BOOTSTRAP_USERNAME in .env.db-admin`: add it.
- `hint: pass --password or set TCKDB_BOOTSTRAP_PASSWORD` from
  `bootstrap_admin.py`: the administrator does not exist yet and
  `TCKDB_BOOTSTRAP_PASSWORD` is empty. Set it in `.env.db-admin` and run `up`
  again.
- `password authentication failed`: see the next entry.

**Verify.** `docker compose ... ps -a` shows `init` as `Exited (0)` and
`tckdb-api` as `healthy`.

---

### The API cannot log in to the database after a password change

**Symptom.** `init` or `tckdb-api` fails with `password authentication failed
for user "tckdb_app"` (or the owner), on a stack that used to work.

**Cause.** A database password is set on its role when the volume is first
created. Editing it in an env file later, or rerunning `tckdb_make_env.sh
--force`, changes the file and not the role.

**Fix.** Put the old password back, or change the role to match the file:

```bash
docker compose ... exec db psql -U tckdb_admin -d tckdb \
    -c "ALTER ROLE tckdb_app PASSWORD '<the new DB_PASSWORD>'"
docker compose ... up -d --wait
```

Use your `DB_ADMIN_USER`, `DB_NAME` and role names if you changed them. The
same applies to `DB_OWNER_PASSWORD` and the owner role. On a stack with no
data yet, `docker compose ... down -v` and a fresh `up` is quicker.

**Verify.** `backend/scripts/check_selfhosted_deployment.sh` passes.

---

### Browser login does nothing on a lab address

**Symptom.** The web UI's login form is accepted, but you stay logged out.
`POST /api/v1/auth/login` returns `200` and `GET /auth/me` returns `401`. It
works on `http://127.0.0.1:8011`.

**Cause.** The session cookie is marked `Secure`. Browsers drop a secure
cookie served over plain HTTP from any host but localhost.

**Fix.** Serve the instance over HTTPS. On a trusted lab network in
`shared_private` mode you can instead set, in `.env.selfhosted`:

```dotenv
SESSION_COOKIE_SECURE=false
SESSION_COOKIE_ALLOW_PLAIN_HTTP=true
```

and run `up -d --wait` again. The cookie then crosses the network
unencrypted. `hosted_public` refuses this.

**Verify.** After logging in, `GET /api/v1/auth/me` from the browser returns
your user.

---

## Networking and ports

### `port is already allocated` / `address already in use`

**Symptom**

```text
Error response from daemon: ... Bind for 127.0.0.1:5432 failed: port is already allocated
```

or `listen tcp 127.0.0.1:5432: bind: address already in use`. The same can
happen on 9000, 8010 or 8011.

**Cause.** Something on the host already uses the port: often a
host-installed Postgres on 5432, or a second TCKDB stack.

**Fix.** Stop the other service, or move TCKDB's host port. In the env file,
set the matching variable to a free port:

| Port | Variable |
|---|---|
| 5432 | `DB_PORT` |
| 9000 | `S3_PORT` |
| 8010 | `TCKDB_API_PORT` |
| 8011 | `TCKDB_WEB_PORT` |

Only the host side moves; inside the containers nothing changes. When running
from source, also set `DB_PORT` (and `S3_ENDPOINT_URL`) for the host-run
Alembic and API: in `backend/.env`, exported in the shell that starts the
API, which reads only its environment.

**Verify**

```bash
ss -lntp | grep -E ':5432|:5434'
docker compose up -d db
```

---

### Host port and container port

**Symptom.** `docker compose ps` shows `127.0.0.1:5434->5432/tcp`, and a
host-run Alembic fails with `could not connect to server`.

**Cause.** A `ports:` entry is `<host_ip>:<host_port>:<container_port>`.
Postgres in the container always listens on 5432. The host sees it on the
left-hand port.

**Fix.** Use the address for where the process runs:

| Process runs | `DB_HOST` / `DB_PORT` | `S3_ENDPOINT_URL` |
|---|---|---|
| On the host (Alembic, a host-run API, `psql`) | `127.0.0.1` / the host port | `http://127.0.0.1:9000` |
| In a container on the compose network | `db` / `5432` | `http://seaweedfs:9000` |

The `selfhost` profile and `tckdb_deploy.sh` set `DB_HOST=db` and
`DB_PORT=5432` for their containers. The `selfhost` profile also sets
`S3_ENDPOINT_URL`; `tckdb_deploy.sh` does not.

**Verify**

```bash
docker compose ps db
psql -h 127.0.0.1 -p ${DB_PORT:-5432} -U tckdb -d tckdb_dev -c '\dx'
```

The `rdkit` extension is listed.

---

### `Connection refused` from the host to a SeaweedFS port other than 9000

**Symptom.** From the host, or a host-network container, SeaweedFS's filer,
master, volume or gRPC ports refuse at once, and so does a port you published
yourself on 127.0.0.1 (such as 9333 for `S3_SEAWEEDFS_MASTER_URL`). The S3 API
on 9000 works. `weed shell` on the host cannot connect.

**Cause.** The [object store firewall](storage-firewall.md) is doing its job.
It resets new TCP connections from the host to the `storage` network, except
to 9000. The gRPC ports take no credentials, and this rule is what keeps
local processes away from them.

**Fix**

- Run `weed shell`, or read a status page, inside the container:
  `docker compose exec seaweedfs weed shell`.
- For a forward you published yourself, allow its container port:
  `sudo /usr/local/sbin/tckdb_storage_hostfw.sh --allow-ports 9000,9333,9340`,
  and set the same value as `TCKDB_STORAGE_ALLOW_PORTS` in the unit
  (`sudo systemctl edit tckdb-storage-hostfw`) so it survives a reboot.
  Never allow the gRPC ports (18888, 19333, 19340, 19000).

**Verify.** Give `--check` the same list:
`sudo /usr/local/sbin/tckdb_storage_hostfw.sh --check --allow-ports 9000,9333,9340`.
The rule as installed is in `sudo iptables -S OUTPUT | grep tckdb-548`.

---

### Uploads with files return 503, but everything else works

**Symptom.** Uploads without files succeed. Any upload carrying an artifact
(an ESS output log, a checkpoint) returns `503` with
`"code": "artifact_storage_unavailable"`. `/api/v1/health` and
`/api/v1/readyz` are fine.

**Cause.** The API cannot reach the object store. Most often, an API in a
container still has the host's address:

```text
S3_ENDPOINT_URL=http://127.0.0.1:9000     # right for an API on the host
S3_ENDPOINT_URL=http://seaweedfs:9000     # right for an API in a container
                                          # (http://minio:9000 on a MinIO stack)
```

Inside a container, `127.0.0.1` is the container itself. The value is a valid
URL, so it survives a move from host to container unnoticed. The `selfhost`
profile sets the container value itself. `tckdb_deploy.sh` passes its env
file to the container unchanged, so that file needs `http://seaweedfs:9000`.

**Verify**

```bash
curl -s http://127.0.0.1:8010/api/v1/status | jq '.components.artifact_storage'
```

`/status` reports the endpoint and bucket it tried. `reachable: false` means
the address is wrong or the store is down. `reachable: true` with an
unhealthy verdict means the store answered but the bucket is missing or the
keys are refused. The API log has the underlying error for each 503
(`docker compose ... logs tckdb-api`, `docker logs tckdb-api` or
`journalctl -u tckdb-api`; search for `ArtifactStorageUnavailable`).

**Fix.** Set `S3_ENDPOINT_URL` to the compose service name and restart the
API. Whenever a process moves into or out of a container, reread every
address in its env file and ask who it is relative to.

---

### Uploads with files return 507 `artifact_storage_full`

**Symptom.** An upload carrying an artifact returns `507` with
`"code": "artifact_storage_full"`. Reads, queries, uploads without files and
downloads all work.

**Cause.** The object store answered and refused the write: it is out of disk
or the bucket is over its quota. Retrying does not help, which is why it is
507 and not 503.

**Which stores report it**

- **MinIO** says so in its own error codes (`XMinioStorageFull`,
  `XMinioAdminBucketQuotaExceeded`).
- **SeaweedFS** does not: a full disk, exhausted volume slots and an
  enforced bucket quota all answer `InternalError` (HTTP 500), like any
  internal fault. With `S3_SEAWEEDFS_MASTER_URL` set (for example
  `http://seaweedfs:9333` for an API container on the `storage` network),
  TCKDB then asks SeaweedFS how much room it has. If it is less than the
  refused write, the upload gets 507, the refusal is recorded and `/status`
  goes degraded. Without `S3_SEAWEEDFS_MASTER_URL`, a full SeaweedFS shows as
  `503 artifact_storage_unavailable` with `/status` healthy. Check its disk
  (`df -h` on the volume) and its container log, which names the cause:
  `no space left on device`, `No writable volumes and no free volumes left`,
  or `read only ... (e.g. bucket over quota)`.

The API log line names the limit that ran out
(`artifact storage refused a <n>-byte write for want of room ...`):

- **Disk** (`limited by free_space`). `weed mini` keeps 1 % of the disk free
  and checks every 60 s. Below that, every volume turns read-only and even a
  1 KiB write is refused, so a 500 GB disk stops taking writes with about
  5 GB still free. The log shows raw `disk free` and the `usable` bytes above
  the 1 %. Free disk on the volume that holds `tckdb_seaweedfs`; the volumes
  become writable at the next check, with no restart.
- **Volume slots** (`limited by volume_slots`): `volume slots 0/<max> free`
  while the disk is above the 1 %. SeaweedFS sizes its slot count from the
  disk when it starts. Give it more (`-volume.max=<n>` on the `seaweedfs`
  command) or more disk, then recreate the container.
- **Bucket quota** (S3 code `SeaweedFSBucketQuotaExceeded` in `/status`).
  When the store reports room, TCKDB reads the bucket's quota with the S3
  keys it already holds (`GET /<bucket>?seaweedfs-quota` and the bucket's
  capacity object). If the quota is on and less is available than the
  refused write, it is recorded as a quota refusal, which free space never
  clears.

Limits of the SeaweedFS check:

- The quota reading is approximate. `Available` is the quota less the gross
  volume size. Deleted objects keep their bytes until the volume is vacuumed
  (`weed mini` vacuums only above 30 % garbage), so a bucket near its quota
  after deletions can read as full while writes still work. It never misses
  a real quota refusal. If it names a quota while uploads of that size work,
  compare `s3.bucket.list` (logical size) with the quota and vacuum first.
- `AccessDenied` is never read as a quota. On SeaweedFS a write
  `AccessDenied` is a bucket policy.
- The room check and the quota reads share a 4 s budget per refusal.

In `weed shell`, `s3.bucket.list` shows each bucket's `quota:` and `usage:`.
Raise a quota with `s3.bucket.quota -name=<bucket> -op=set -sizeMB=<larger>`
or remove it with `-op=remove`. No `lock` or `enforce` step is needed:
SeaweedFS applies a quota change within about a minute, and removal at
once.

**"Full" depends on the size.** MinIO refuses a write that would cross its
free-space threshold, judged against the object being written. A store in
this state refuses a large ESS log and still accepts a small file. So some
uploads still working is no evidence the store is fine; the refused size in
the `/status` reason tells you how close to the edge it is.

**Verify**

```bash
curl -s http://127.0.0.1:8010/api/v1/status | jq '.components.artifact_storage'
```

Look for `"storage_full": true` and `"storage_full_observed_at"`. The
`reason` names the store's error code and the refused size.

A green `/status` does not prove there is room. The probe is a `head_bucket`,
which a full store answers with 200, and S3 has no capacity query.
`storage_full` reports what the real write path was told. It is stored in the
append-only `artifact_storage_capacity_event` table, so it survives a
restart, and `"storage_full": false` means only that no refusal is
outstanding.

**How it clears.** TCKDB records the size it was refused at and clears only
on evidence of at least that size:

| What happened | Clears? |
|---|---|
| A write of at least the refused size succeeds | yes |
| A smaller write succeeds | no |
| An upload deduplicates against an object already stored | no; that is a read |
| MinIO reports at least the refused size free | yes, unless the refusal was a quota |
| SeaweedFS (with `S3_SEAWEEDFS_MASTER_URL`) reports at least the refused size of disk and slot room | yes, unless the refusal was a quota |
| An administrator clears it | yes |
| Time passes | never |

While a refusal is outstanding, each `/status` poll asks the store again, so
recovery shows on the next poll rather than the next large upload. Against
AWS S3 or another store, it asks nothing and changes nothing.

To check capacity directly, ask the store:

```bash
docker exec <minio-container> mc admin info local     # MinIO
df -h /path/to/data                                    # the volume underneath
```

**If the store is fine and the flag is stale**, an administrator can clear
it, for example after raising a quota, which no free-space reading clears.
Use an administrator's API key:

```bash
curl -s -X POST http://127.0.0.1:8010/api/v1/admin/artifact-storage/capacity/clear \
  -H "X-API-Key: $TCKDB_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"reason": "added a 2 TB volume"}'
```

The reason is required and is recorded with your user. Clearing appends; the
refusal stays in the log. If the store is still full, the next refused upload
records a new refusal. To read the state without changing it:

```bash
curl -s http://127.0.0.1:8010/api/v1/admin/artifact-storage/capacity \
  -H "X-API-Key: $TCKDB_API_KEY"
```

**Fix.** Free space or raise the quota, then upload one artifact at least as
large as the refused size to confirm. On SeaweedFS, deleting objects returns
no disk until the volume is vacuumed, so freeing disk on the host or adding
volume slots is faster.

- Delete or archive unreferenced objects with
  `backend/scripts/ops/verify_artifact_integrity.py --reclaim-orphans`, which
  moves month-old unreferenced objects to a hold; a later
  `--purge-hold-days` run deletes from the hold. The sweep copies before it
  deletes, so on a completely full store free a little space by hand first.
- Grow the volume, or raise the bucket quota (`mc quota set local/<bucket>
  --size ...` on MinIO). MinIO enforces quotas from its usage scanner, so the
  refusal and the recovery both lag a change by a minute or two.

---

### Moving an existing MinIO deployment to SeaweedFS

**Symptoms**

- `docker compose up -d seaweedfs` on a MinIO host fails with `port is
  already allocated` on `127.0.0.1:9000`.
- After switching `S3_ENDPOINT_URL` to SeaweedFS, some downloads return `502`
  with `"code": "artifact_object_missing"`, and `/status` is healthy.

**Cause.** Both services publish `127.0.0.1:9000` on purpose, so the API
cannot be switched to an empty store by accident. The second symptom means
the API was switched before the copy finished. Each such download also
records an `object_missing` integrity event.

**Fix.** Follow
[Moving from MinIO to SeaweedFS](../../backend/docs/deployment/migrating_minio_to_seaweedfs.md).
It publishes SeaweedFS on another host port during the copy and switches the
endpoint only after `backend/scripts/ops/migrate_object_store.py --verify-only`
has read every referenced file back and exited 0.

If you already switched early, don't switch back: uploads since the switch
exist only in SeaweedFS. Stop the API, then finish the copy with both stores
named, since the env file now names SeaweedFS:
`migrate_object_store.py --source-endpoint http://minio:9000 --dest-endpoint http://seaweedfs:9000 --commit`
(use the 127.0.0.1 ports for an API on the host). Start the API once
`--verify-only` with the same flags exits 0. A clean re-read then clears the
recorded `object_missing` events (`verify_artifact_integrity.py --sha256 <digest>`,
or `--all`).

---

## Database

### `database "tckdb_dev" does not exist`

**Symptom**

```text
psql: error: connection to server at "127.0.0.1", port 5432 failed:
FATAL: database "tckdb_dev" does not exist
```

**Cause.** The container is up but the database was never created. Either
`POSTGRES_DB` was unset on the volume's first start, or the volume is left
over from a setup that used another name. `POSTGRES_DB` is read only on a
volume's first start.

**Fix.** On a development stack, the quickest fix is a fresh volume:

```bash
make reset
```

Or create the database by hand. Pass `-E UTF8 -T template0`: a bare
`createdb` copies `template1`, which on many clusters is `SQL_ASCII` (next
entry).

```bash
docker compose exec db createdb -U tckdb -E UTF8 -T template0 tckdb_dev
```

**Verify**

```bash
docker compose exec db psql -U tckdb -l
```

---

### Postgres rejects writes / weird text behavior — `SQL_ASCII` instead of `UTF8`

**Symptom.** Broken Unicode, or migrations fail with `invalid byte sequence
for encoding "SQL_ASCII"`. `psql -l` shows the database with
`Encoding | SQL_ASCII`.

**Cause.** The data volume was created before the compose file pinned the
encoding. With no locale set, `initdb` fell back to `SQL_ASCII`, which stores
any bytes and checks none. Docker never reruns `initdb` on an existing
volume, so the encoding is fixed when the volume is created.

`docker-compose.yml` now sets `LANG=C.UTF-8` and
`POSTGRES_INITDB_ARGS=--encoding=UTF8` on `db`, so new volumes are `UTF8`.
That cannot fix an existing volume.

`SQL_ASCII` works until the first non-ASCII byte arrives, then rolls back the
whole transaction carrying it, possibly months after the volume was created.
The API logs the cluster's `server_encoding` at startup and reports it in
`/api/v1/status`, so you can check before that happens.

**Fix.** Changing a cluster's encoding takes a dump and a restore. On a
development database you can just start again:

```bash
docker compose down -v      # deletes the volume
docker compose up -d
```

On a database worth keeping, dump it, check the dump while the volume still
exists, then recreate and restore. Use your own `DB_USER` / `DB_NAME` (on a
self-hosted stack, the `DB_ADMIN_USER` from `.env.db-admin`). `down -v` also
deletes the object store's volume. On a stack with stored files, stop the
stack and remove only the database volume instead:
`docker compose ... down`, then `docker volume rm <project>_tckdb_pgdata`.

```bash
# 1. Dump. Keep -T: without it docker compose allocates a terminal, which
#    rewrites LF to CRLF inside the binary dump and silently corrupts it.
docker compose exec -T db \
    pg_dump -U "${DB_USER:-tckdb}" -d "${DB_NAME:-tckdb_dev}" -Fc > tckdb.dump

# 2. Check it BEFORE deleting anything. Expect a table of contents.
#    "did not find magic string" means the dump is corrupt: stop here.
pg_restore --list tckdb.dump | head

# 3. Only now delete the volume and recreate it as UTF8.
docker compose down -v
docker compose up -d

# 4. Restore.
docker compose exec -T db \
    pg_restore -U "${DB_USER:-tckdb}" -d "${DB_NAME:-tckdb_dev}" < tckdb.dump
```

Without `pg_restore` on the host, run step 2 in the container, still before
step 3:

```bash
docker compose exec -T db pg_restore --list /dev/stdin < tckdb.dump | head
```

`DB_CLIENT_ENCODING=utf8`, `?client_encoding=utf8` in the database URL and
`PGCLIENTENCODING=UTF8` in the API image all act on the client side. None of
them can rescue a `SQL_ASCII` server.

**Verify**

```bash
docker compose exec db psql -U tckdb -l
# expect: Encoding | UTF8   for tckdb AND for template1

curl -s localhost:8010/api/v1/status | jq '.components.database'
# expect: server_encoding "UTF8" AND template_encoding "UTF8"
```

---

### The cluster template disagrees with the production database

**Symptom.** `psql -l` shows the application database as `UTF8` but
`template1` (usually also `template0` and `postgres`) as `SQL_ASCII`.
`/api/v1/status` reports `server_encoding: "UTF8"` and
`template_encoding: "SQL_ASCII"`.

**Why it is a problem.** `CREATE DATABASE` with no `TEMPLATE` copies `template1`.
A cluster in this state gives `SQL_ASCII` to every database created next,
including the one a restore recreates. That is the most likely way a fixed
cluster turns broken again.

Nothing errors when it happens. A `SQL_ASCII` database accepts every byte of
a `UTF8` dump, so the restore exits 0 and the data looks present. But
multi-byte characters stop being characters:

```
UTF8 source db:   'em dash: —'  length() = 10   octet_length() = 12
naive restore:    'em dash: —'  length() = 12   octet_length() = 12
```

`length()`, `substring()`, `LIKE` and every index built on them are then
wrong for every non-ASCII value.

**How a cluster gets here.** Its application database was converted in
place (dumped, dropped, recreated with an explicit encoding, restored) and
the templates were left alone.

**Fix.** Don't rebuild the cluster. The templates matter only at `CREATE
DATABASE`, so make every `CREATE DATABASE` explicit, as the restore steps in
these docs do:

```sql
CREATE DATABASE tckdb ENCODING 'UTF8' TEMPLATE template0;
```

```bash
createdb -E UTF8 -T template0 tckdb
```

`TEMPLATE template0` is needed even when `template0` is itself `SQL_ASCII`:
`template0` exists to be copied into a database with another encoding.
Asking for an encoding without it is refused:

```
ERROR:  new encoding (UTF8) is incompatible with the encoding of the
        template database (SQL_ASCII)
HINT:  Use the same encoding as in the template database, or use
       template0 as template.
```

That refusal is safe. The dangerous form is the bare `CREATE DATABASE`,
which succeeds and inherits `SQL_ASCII` silently.

Leave out `LC_COLLATE` and `LC_CTYPE` unless you need them. Omitted, they
come from `template0`, which is `C` on these clusters and works with any
encoding. A non-`C` locale that disagrees with `UTF8` is how this command
goes wrong.

Rebuilding `template1` from `template0` is possible but not recommended
here. It is a cluster-level change with an outage, and it gains nothing over
explicit `CREATE DATABASE` statements. It also does nothing for databases
that already exist.

**Verify**

```bash
docker compose exec db psql -U tckdb -d postgres \
    -c "SELECT datname, pg_encoding_to_char(encoding) FROM pg_database ORDER BY datname;"

curl -s localhost:8010/api/v1/status | jq '.components.database.template_encoding'
```

---

## Python / FastAPI (running from source)

### `ModuleNotFoundError: No module named 'app'`

**Symptom.** `ModuleNotFoundError: No module named 'app'` when running
`uvicorn main:app` or a backend script.

**Cause.** The backend imports `app.*` relative to `backend/`. Run from
anywhere else, the wrong directory is on `sys.path`.

**Fix.** Run from `backend/`, or use the Make target:

```bash
cd backend
conda run -n tckdb_env uvicorn main:app --host 127.0.0.1 --port 8010
# or, from the repository root:
make api
```

`backend/scripts/bootstrap_admin.py` fixes `sys.path` itself and runs from
either place. Older scripts may need `backend/`.

**Verify**

```bash
cd backend
conda run -n tckdb_env python -c "from app.api.app import create_app; print(create_app())"
```

---

### Uvicorn fails with `Error loading ASGI app` / `--factory required`

**Symptom**

```text
ERROR: Error loading ASGI app. Could not import module "app.api.app".
```

or

```text
TypeError: ASGI callable is not a coroutine. Did you mean to pass --factory?
```

**Cause.** `app.api.app:create_app` is a factory that builds and returns the
app. Uvicorn needs `--factory` to call it, and `app.api.app:app` does not
exist.

**Fix.** Use `backend/main.py`, which calls the factory and exposes `app`:

```bash
# from backend/
conda run -n tckdb_env uvicorn main:app --host 127.0.0.1 --port 8010
```

Or pass `--factory`:

```bash
# from backend/
conda run -n tckdb_env uvicorn app.api.app:create_app --factory \
    --host 127.0.0.1 --port 8010
```

**Verify**

```bash
curl http://127.0.0.1:8010/api/v1/health
# -> {"status":"ok"}
```

---

### `pip install -e .` fails on RDKit / I don't want conda

**Symptom**

```text
ERROR: Could not find a version that satisfies the requirement rdkit (...)
```

or, on a platform without a prebuilt RDKit wheel,
`ERROR: Failed building wheel for rdkit`.

**Cause.** `backend/pyproject.toml` makes RDKit an optional extra. Conda users
get it from conda-forge through `backend/environment.yml`; pip users must ask
for it.

**Fix.** Install `tckdb-schemas` first either way; it is not on PyPI.

- **With conda** (the easiest):

    ```bash
    mamba env create -n tckdb_env -f backend/environment.yml
    conda activate tckdb_env
    pip install -e schemas/python/tckdb-schemas
    cd backend && pip install -e ".[dev]"   # not .[dev,rdkit]: conda has RDKit
    ```

- **With pip or uv**, add the `rdkit` extra:

    ```bash
    cd backend
    uv sync --extra dev --extra rdkit
    # or:
    pip install -e ../schemas/python/tckdb-schemas
    pip install -e ".[dev,rdkit]"
    ```

  RDKit wheels exist for x86_64 Linux and macOS and for aarch64 Linux.
  Elsewhere (Windows on ARM, musl Linux) use conda-forge.

**Verify**

```bash
python -c "from rdkit import Chem; print(Chem.MolFromSmiles('O'))"
# -> <rdkit.Chem.rdchem.Mol object at 0x...>
```

---

## curl pitfalls

### `curl: (3) bad range in URL` for bracketed SMILES

**Symptom**

```bash
curl "http://127.0.0.1:8010/api/v1/scientific/species/search?smiles=C[CH]C"
# curl: (3) bad range in URL position 64: ... ?smiles=C[CH]C
```

**Cause.** curl reads `[...]` in a URL as a range to expand, and the SMILES
brackets collide with it.

**Fix.** Let curl encode the value:

```bash
curl -G "http://127.0.0.1:8010/api/v1/scientific/species/search" \
    --data-urlencode "smiles=C[CH]C"
```

Or `curl --globoff "...?smiles=C%5BCH%5DC"`.

**Verify.** You get JSON, possibly with empty `results`, not a curl error.

---

### `jq: parse error` when piping `curl -i`

**Symptom.** `jq: parse error: Invalid numeric literal at line 1, column 9`.

**Cause.** `curl -i` prints the headers before the body, and jq tries to
parse them.

**Fix.** Drop `-i`, or keep the status apart from the body:

```bash
curl -s "$TCKDB_BASE_URL/scientific/species/search?smiles=O" | jq .

curl -s -o /tmp/body.json -w 'HTTP %{http_code}\n' "$URL"
jq . /tmp/body.json
```

---

## Cloudflare Tunnel and DNS

Only for a public instance behind Cloudflare.

### `curl: could not resolve host` after adding a Cloudflare DNS route

**Symptom.** `curl: (6) Could not resolve host: tckdb.example.org`, though
the record shows in the Cloudflare dashboard.

**Cause.** Your resolver cached the earlier "no such name" answer, for up to
the zone's negative TTL (often a few minutes).

**Fix.** Wait, or flush the cache:

```bash
sudo resolvectl flush-caches                                    # systemd-resolved
sudo systemctl restart nscd                                     # nscd
sudo dscacheutil -flushcache && sudo killall -HUP mDNSResponder # macOS
```

Ask another resolver to confirm: `dig +short tckdb.example.org @1.1.1.1`.

**Verify.** `curl -fsS https://tckdb.example.org/api/v1/health`.

---

### DNS record, tunnel and ingress rule: all three needed

**Symptom.** The public URL shows Cloudflare's "1033: Argo Tunnel error" or
"1016: Origin DNS error", or hangs.

**Cause.** Three separate pieces must line up:

1. **DNS:** `tckdb.example.org` points at Cloudflare (a `CNAME` to the
   tunnel's hostname).
2. **Tunnel:** `cloudflared` runs on the server and is registered with the
   same account.
3. **Ingress rule:** the tunnel forwards `tckdb.example.org` to
   `http://127.0.0.1:8011` (or `:8010` for the API alone).

**Fix.** In the Zero Trust dashboard check that:

- Networks → Tunnels shows your tunnel as "Healthy";
- its public hostnames include `tckdb.example.org`, type `HTTP`, with the
  right local URL;
- DNS has the matching `CNAME` (Cloudflare usually creates it).

**Verify**

```bash
docker compose --env-file .env.selfhosted --env-file .env.db-admin \
    --profile cloudflare logs cloudflared | tail -50
curl -fsS https://tckdb.example.org/api/v1/health
```

---

### A database GUI through `cloudflared access tcp`

To reach the database from DataGrip or DBeaver without publishing Postgres,
see [Public server](self_hosted_single_node.md#optional-a-database-gui-through-the-tunnel):

```bash
cloudflared access tcp --hostname pg-tckdb.example.org --url localhost:15434
psql -h 127.0.0.1 -p 15434 -U tckdb_app -d tckdb -c 'select 1'
```

---

## Still stuck

1. Run the check script or `make doctor`.
2. Find the matching entry above and run its verify step after the fix.
3. If it is new, open an issue with the env variable names (not the
   secrets), the exact command and its output.
