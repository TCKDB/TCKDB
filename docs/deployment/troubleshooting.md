# TCKDB troubleshooting

Concrete fixes for the failure modes that have actually bitten
people. The first stop for any "won't start" problem should be:

```bash
backend/scripts/tckdb_doctor.sh
```

which surfaces most of these with an actionable hint inline. This
page is the longer write-up: one entry per real-world issue, each
with **symptom**, **cause**, **fix**, and **verify** sections.

---

## Networking and ports

### `port is already allocated` / `address already in use` on 5432

**Symptom**

```text
Error response from daemon: ... Bind for 0.0.0.0:5432 failed: port is already allocated
```

or, on Linux:

```text
listen tcp 127.0.0.1:5432: bind: address already in use
```

**Cause**

A host-installed Postgres is already listening on 5432.

**Fix**

Pick one:

- Stop the host Postgres: `sudo systemctl stop postgresql` (Debian/Ubuntu).
- Or remap the TCKDB container to a different host port. In your env
  file set `DB_PORT=5434` (any free port) — the compose file
  publishes `127.0.0.1:${DB_PORT:-5432}:5432`, so the **host port**
  becomes 5434 and the **container port** stays at 5432.

If you choose the remap, **also** set `DB_PORT=5434` for the host-run
Alembic / Uvicorn — see the next entry.

**Verify**

```bash
ss -lntp | grep -E ':5432|:5434'
docker compose up -d db
```

---

### Confusion between host port and container port

**Symptom**

`docker compose ps` shows something like:

```text
127.0.0.1:5434->5432/tcp
```

…and a host-run Alembic fails with `could not connect to server` on
the wrong port.

**Cause**

Docker `ports:` entries are `<host_ip>:<host_port>:<container_port>`.
The container Postgres always listens on `5432`. The **host** sees it
on whichever host port the compose file publishes.

**Fix**

- When the API and Alembic run on **the host** (the local dev
  default), use `DB_HOST=127.0.0.1` and `DB_PORT=<host port>` —
  whatever the left-hand side of the mapping is.
- When the API runs **inside** the compose network (in a future
  containerized API setup), use `DB_HOST=db` and `DB_PORT=5432` —
  service-to-service, no host port involved.

**Verify**

```bash
docker compose ps db
psql -h 127.0.0.1 -p ${DB_PORT:-5432} -U tckdb -d tckdb_dev -c '\dx'
```

You should see the `rdkit` extension listed.

---

### Uploads with files return 503, but everything else works

**Symptom**

Plain uploads succeed. Any upload carrying an artifact (an ESS output log, a
checkpoint) returns `503` with `"code": "artifact_storage_unavailable"`.
`/api/v1/health` and `/api/v1/readyz` are fine.

**Cause**

The API cannot reach the object store. By far the most common reason is a
containerised API still configured with the host's address:

```text
S3_ENDPOINT_URL=http://127.0.0.1:9000     # correct on the host
S3_ENDPOINT_URL=http://minio:9000         # correct inside the compose network
```

Inside a container, `127.0.0.1` is the container's own loopback. The setting
is not malformed, which is why it survives a migration from a host deployment
unnoticed.

**Verify**

```bash
curl -s http://127.0.0.1:8010/api/v1/status | jq '.components.artifact_storage'
```

`/status` reports the endpoint and bucket it actually reached for, and splits
`reachable: false` (wrong address, or the store is down) from `reachable:
true` with an unhealthy verdict (the store answered but the bucket is missing
or the credentials are refused). The server log also carries the underlying
botocore error for each 503 — `journalctl -u tckdb-api` or
`docker logs tckdb-api`, filtered on `ArtifactStorageUnavailable`.

**Fix**

Set `S3_ENDPOINT_URL` to the compose service name and restart the API. See
[self_hosted_single_node.md](self_hosted_single_node.md#if-the-api-runs-in-a-container-address-siblings-by-service-name).

---

### Uploads with files return 507 `artifact_storage_full`

**Symptom**

An upload carrying an artifact returns `507` with
`"code": "artifact_storage_full"`. Reads, queries, and file-less uploads all
work normally. Downloads of existing artifacts also work normally.

**"Full" is not all-or-nothing, and this is the confusing part.** MinIO
refuses a write that would breach its free-space threshold, sized against the
object being written — so on a store in this state a large ESS log is refused
while a small text artifact still succeeds. Measured: a store that refused an
8 MiB artifact accepted a 1-byte one in the same second. So *some uploads
still working is not evidence that the store is fine*, and the refused size in
the `/status` reason is the number that tells you how close to the edge you
are.

**Cause**

The object store answered and refused the *write*: it is out of disk, or the
bucket is over its quota. This is not transient from a depositor's side — no
number of retries clears it, which is why the status is 507 rather than 503.

**Verify**

```bash
curl -s http://127.0.0.1:8010/api/v1/status | jq '.components.artifact_storage'
```

Look for `"storage_full": true` and `"storage_full_observed_at"`. The
`reason` names the store's own error code and the size of the write it
refused.

Read this next part before trusting a green `/status`. The `/status` probe is
a `head_bucket`, and a full store answers a `head_bucket` with **200**:

- On MinIO `RELEASE.2025-09-07T16-13-09Z`, measured on a volume filled to its
  free-space threshold, every read succeeded and even a **1-byte** write
  succeeded on the same store that refused a 4 MiB one — MinIO's threshold
  check is sized against the incoming object.
- So a *capacity* problem is invisible to any read-only probe, and a cheap
  synthetic write probe would miss it too. The S3 API exposes no capacity or
  quota query to ask instead.

`storage_full` therefore reports what the **real write path** was told, not
what a probe found. It is recorded in the append-only
`artifact_storage_capacity_event` table, so:

- **It survives a restart.** It used to be process memory, and a restart
  silently reset it to healthy while every upload still failed.
- **`"storage_full": false` does not mean there is space.** It means no
  refusal is outstanding. A store that fills while TCKDB is idle still reads
  healthy until the next depositor arrives — onset needs one upload attempt.

**How it clears**, which is the part worth reading carefully. Because a full
store still accepts small writes, TCKDB records the *size* it was refused at
and clears only on evidence of at least that size:

| what happened | does it clear? |
|---|---|
| a write of **at least** the refused size succeeds | yes |
| a write **smaller** than the refused size succeeds | **no** — recorded, but the store is still refusing real artifacts |
| deduplicating against an object already stored | no — that is a read |
| MinIO reports **at least** the refused size free | yes, unless the refusal was a bucket quota |
| an operator clears it explicitly | yes, always |
| time passing | **never** — there is deliberately no expiry |

There is no timer because a timer guesses and a size-qualified success
measures. A stale "full" you can clear in one command is safer than a flag
that goes quiet while the disk is still full.

While a refusal is outstanding, `/status` also asks MinIO's admin API how
much room it has, so **recovery is noticed on the next poll rather than on
the next large upload**. It uses the credentials the API already holds, writes
nothing, and is skipped entirely on a healthy store. Against AWS S3 or any
non-MinIO store it returns no opinion and changes nothing.

It cannot see a bucket quota: measured, MinIO refused a 2 MiB write with
`XMinioAdminBucketQuotaExceeded` while reporting **418 MiB free**. A quota
refusal therefore clears only on a real successful write or an operator.

To check capacity directly, ask the store rather than the API:

```bash
docker exec <minio-container> mc admin info local     # MinIO
df -h /path/to/minio/data                             # the underlying volume
```

**If the store is fine and the flag is stale**, an admin can clear it. Use
this when you have fixed the cause and do not want to wait for a large upload
— or when the refusal was a quota, which no free-space reading will clear:

```bash
curl -s -X POST http://127.0.0.1:8010/api/v1/admin/artifact-storage/capacity/clear \
  -H "Authorization: Bearer $TCKDB_ADMIN_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"reason": "added a 2 TB volume and restarted MinIO"}'
```

The reason is required and is recorded with your user id. Clearing **appends**
— it never edits or deletes the refusal, so the incident stays in the log. If
the store is in fact still full, the next refused upload records a new refusal
and `/status` degrades again.

To see the current state without changing it:

```bash
curl -s http://127.0.0.1:8010/api/v1/admin/artifact-storage/capacity \
  -H "Authorization: Bearer $TCKDB_ADMIN_TOKEN"
```

**Fix**

Free space or raise the quota. `/status` clears itself on the next poll once
MinIO reports enough room; to confirm end to end, upload one artifact **at
least as large as the refused size** shown in the `reason` — a small one will
succeed without clearing anything, by design:

- delete or archive unreferenced objects. The reclaim sweep is the supported
  route: `backend/scripts/ops/verify_artifact_integrity.py --reclaim-orphans`
  moves month-old unreferenced objects to a hold, and a separate
  `--purge-hold-days` run deletes from the hold. **Note that the sweep itself
  writes** (it copies before deleting), so on a completely full store the
  copy is refused too and the first pass may fail with the same condition;
  free a little space by hand first.
- grow the volume, or raise the bucket quota
  (`mc quota set local/<bucket> --size ...`). MinIO enforces a hard quota
  asynchronously from its data-usage scanner, so both the refusal and the
  recovery lag the change by a minute or two.

---

## Database

### `database "tckdb_dev" does not exist`

**Symptom**

```text
psql: error: connection to server at "127.0.0.1", port 5432 failed:
FATAL: database "tckdb_dev" does not exist
```

**Cause**

The container is up but the DB itself was never created — usually
because compose was started without `POSTGRES_DB` set, or the data
volume survived from a previous experiment that used a different
name.

**Fix**

The fastest reset: wipe the volume and bring the stack back up. The
`POSTGRES_DB=tckdb_dev` env var (in `docker-compose.yml` /
`docker-compose.yml`) is only consulted on the **first** start of a
volume.

```bash
docker compose down -v
docker compose up -d
cd backend
conda run -n tckdb_env alembic upgrade head
```

Or, less destructive, create the DB by hand. **Pass `-E UTF8 -T
template0`** — a bare `createdb` copies `template1`, which on many clusters
is `SQL_ASCII`, and you would be creating the exact problem the next section
describes:

```bash
docker compose exec db \
    createdb -U tckdb -E UTF8 -T template0 tckdb_dev
```

**Verify**

```bash
docker compose exec db \
    psql -U tckdb -l
```

---

### Postgres rejects writes / weird text behavior — `SQL_ASCII` instead of `UTF8`

**Symptom**

Weird Unicode breakage, or migrations fail with `invalid byte
sequence for encoding "SQL_ASCII"`. `psql -l` shows the database with
`Encoding | SQL_ASCII`.

**Cause**

A Postgres data volume created before the encoding was pinned. The
`db` image sets no locale of its own, so `initdb` fell back to
`SQL_ASCII` — a cluster that stores whatever bytes it is handed and
validates none of them. Docker will not re-run `initdb` on an existing
volume, so the encoding a deployment gets is decided once, at volume
creation, and is permanent.

`docker-compose.yml` now pins `LANG=C.UTF-8` and
`POSTGRES_INITDB_ARGS=--encoding=UTF8` on the `db` service, so volumes
created from this repo are `UTF8`. That fixes new deployments; it
cannot fix an existing volume.

**Why it bites so late.** `SQL_ASCII` works perfectly until the first
non-ASCII byte arrives. On 2026-08-04 that byte was an em dash in a
warning message, months after the volume was created, and it rolled
back the whole upload carrying it. Nothing connected the two. The API
now logs its cluster's `server_encoding` at startup and reports it in
`/api/v1/status`, so the question is answerable before something
strange happens rather than after.

**Fix**

Changing a cluster's encoding needs a dump and restore; there is no
in-place conversion. On a **dev** database, where the data is
disposable, that reduces to a clean slate:

```bash
docker compose down -v      # destroys the volume
docker compose up -d
```

On a database with anything worth keeping, dump first, **verify the dump
while the volume still exists**, then recreate and restore. Substitute
your own `DB_USER` / `DB_NAME` if they differ from the compose defaults.

```bash
# 1. Dump. -T is not optional: without it `docker compose exec` allocates
#    a pseudo-TTY, whose line discipline rewrites LF to CRLF *inside the
#    binary -Fc stream*. The dump looks fine and is silently corrupt.
docker compose exec -T db \
    pg_dump -U "${DB_USER:-tckdb}" -d "${DB_NAME:-tckdb_dev}" -Fc > tckdb.dump

# 2. Verify BEFORE destroying anything. `down -v` is irreversible, so a
#    dump that cannot be read must be discovered while the original is
#    still there. Expect a table of contents; `did not find magic string`
#    means the dump is corrupt -- stop, and do not run step 3.
pg_restore --list tckdb.dump | head

# 3. Only now destroy the volume and recreate it with UTF8.
docker compose down -v
docker compose up -d

# 4. Restore.
docker compose exec -T db \
    pg_restore -U "${DB_USER:-tckdb}" -d "${DB_NAME:-tckdb_dev}" < tckdb.dump
```

If `pg_restore` is not installed on the host, run step 2 inside the
container instead — it must still happen before step 3:

```bash
docker compose exec -T db pg_restore --list /dev/stdin < tckdb.dump | head
```

`DB_CLIENT_ENCODING=utf8` in the env templates, `?client_encoding=utf8`
in the URL both `app/api/config.py` and `alembic/env.py` build, and
`PGCLIENTENCODING=UTF8` in `backend/Dockerfile` are all belt-and-braces
on the *client* side. None of them can rescue a `SQL_ASCII` server.

**Verify**

```bash
docker compose exec db psql -U tckdb -l
# expect: Encoding | UTF8   -- for tckdb AND for template1

curl -s localhost:8010/api/v1/status | jq '.components.database'
# expect: server_encoding "UTF8" AND template_encoding "UTF8"
```

---

### The cluster template disagrees with the production database

**Symptom**

`psql -l` shows the application database as `UTF8` but `template1` (and
usually `template0` and `postgres`) as `SQL_ASCII`. `/api/v1/status` reports
`server_encoding: "UTF8"` and `template_encoding: "SQL_ASCII"`.

**Why it matters**

`CREATE DATABASE` with no `TEMPLATE` clause copies `template1`. So a cluster
in this state hands `SQL_ASCII` to every database created next to a correct
one — including the database a **restore** recreates. Every restore runbook
in this repo drops and recreates before loading the dump, which makes this
the single most likely way a cluster that was fixed reverts to broken.

The reversion is silent. A `SQL_ASCII` database accepts every byte a `UTF8`
dump contains — there is no validation to fail — so `psql -f dump.sql` exits
0 and the data looks present. What changes is that multi-byte characters stop
being characters. Measured on a `SQL_ASCII` cluster (PostgreSQL 17):

```
UTF8 source db:   'em dash: —'  length() = 10   octet_length() = 12
naive restore:    'em dash: —'  length() = 12   octet_length() = 12
```

Nothing errors. `length()`, `substring()`, `LIKE`, and every index built on
them are now wrong for every non-ASCII value, and the damage is only
discovered when something downstream cares.

**How a cluster gets here**

By having its application database converted in place — dumped, dropped,
recreated with an explicit encoding, restored — without anyone touching the
cluster's templates. That is precisely what happened to the live deployment
after the 2026-08-04 incident: `tckdb` was corrected, `template1` was not,
and the two still disagreed when checked on 2026-08-12.

**Fix**

Do **not** rebuild the cluster for this. The templates only matter at
`CREATE DATABASE` time, so the durable fix is to make every `CREATE DATABASE`
explicit — which the runbooks in this repo now are:

```sql
CREATE DATABASE tckdb ENCODING 'UTF8' TEMPLATE template0;
```

```bash
createdb -E UTF8 -T template0 tckdb
```

`TEMPLATE template0` is load-bearing and is **not** made wrong by `template0`
itself being `SQL_ASCII`. `template0` exists precisely to be copied into a
database with a different encoding; specifying an encoding without it is
refused outright:

```
ERROR:  new encoding (UTF8) is incompatible with the encoding of the
        template database (SQL_ASCII)
HINT:  Use the same encoding as in the template database, or use
       template0 as template.
```

That refusal is safe — it fails loudly rather than producing the wrong
thing. The dangerous form is the bare `CREATE DATABASE`, which succeeds and
inherits `SQL_ASCII` without comment.

Do not add `LC_COLLATE`/`LC_CTYPE` unless you specifically want them. Omitted,
they are inherited from `template0`, which is `C` on these clusters, and `C`
is compatible with any encoding. Naming a non-`C` locale that disagrees with
`UTF8` is how this incantation actually goes wrong.

Correcting `template1` itself is possible but is deliberately **not**
recommended here. There is no in-place encoding change for a template any
more than for an ordinary database, so it means dropping `template1` and
recreating it from `template0` with an explicit encoding — a cluster-level
operation, with an outage, that wants a rehearsed runbook and buys nothing
that explicit `CREATE DATABASE` statements do not already buy. It also does
nothing for any database that already exists.

**Verify**

```bash
docker compose exec db psql -U tckdb -d postgres \
    -c "SELECT datname, pg_encoding_to_char(encoding) FROM pg_database ORDER BY datname;"

curl -s localhost:8010/api/v1/status | jq '.components.database.template_encoding'
```

---

## Python / FastAPI

### `ModuleNotFoundError: No module named 'app'` when running Uvicorn or scripts

**Symptom**

```text
ModuleNotFoundError: No module named 'app'
```

…when running `uvicorn main:app` or a backend script.

**Cause**

The backend imports `app.*` relative to the `backend/` directory.
Running Uvicorn from the **repo root** (or anywhere else) puts the
wrong directory on `sys.path`.

**Fix**

`cd backend/` first, then run Uvicorn:

```bash
cd backend
conda run -n tckdb_env uvicorn main:app --host 127.0.0.1 --port 8010
```

The Make target handles this:

```bash
make api
```

`backend/scripts/bootstrap_admin.py` adds a `sys.path` shim so it can
be invoked either from `backend/` or the repo root; older scripts may
require `backend/` as the working directory.

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

**Cause**

`app.api.app:create_app` is a **factory** — it builds an app and
returns it. Pointing Uvicorn at the factory module without
`--factory` (or skipping the factory and asking for `app.api.app:app`
when that name doesn't exist) leaves Uvicorn confused.

**Fix**

Use the bundled `backend/main.py` shim, which imports the factory
and exposes a plain ASGI `app`:

```bash
# from backend/
conda run -n tckdb_env uvicorn main:app --host 127.0.0.1 --port 8010
```

Or, if you must point Uvicorn at the factory directly, pass
`--factory`:

```bash
# from backend/
conda run -n tckdb_env uvicorn app.api.app:create_app --factory \
    --host 127.0.0.1 --port 8010
```

The repo's standard form is `main:app`.

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

or, on a platform without a prebuilt RDKit wheel:

```text
ERROR: Failed building wheel for rdkit
```

**Cause**

`backend/pyproject.toml` keeps RDKit as an **opt-in extra**, not a
hard runtime dependency. Conda users get it from conda-forge via
`backend/environment.yml`; pure-pip users have to ask for it
explicitly.

**Fix**

Pick one:

- **Have conda available?** Use the conda-forge build — it's the
  smoothest:
  ```bash
  mamba env create -n tckdb_env -f backend/environment.yml
  conda activate tckdb_env
  cd backend && pip install -e ".[dev]"   # NOT .[dev,rdkit] — conda has it
  ```
- **Pure pip / uv?** Include the `rdkit` extra:
  ```bash
  cd backend
  uv sync --extra dev --extra rdkit
  # or:
  pip install -e ".[dev,rdkit]"
  ```
  The pip RDKit wheel exists on x86_64 Linux/macOS and aarch64
  Linux. On other platforms (Windows ARM, musl-Linux, exotic
  architectures) you'll need conda-forge.

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

**Cause**

`curl` interprets `[...]` in a URL as a range expansion (it'll happily
expand `host[1-3].example.org` for you). The square brackets in the
SMILES are colliding with that feature.

**Fix**

Use `-G` + `--data-urlencode` and `curl` will encode the value
correctly:

```bash
curl -G "http://127.0.0.1:8010/api/v1/scientific/species/search" \
    --data-urlencode "smiles=C[CH]C"
```

Equivalent: `curl --globoff "...?smiles=C%5BCH%5DC"`.

**Verify**

You should get a JSON response (possibly empty `results`), not a
curl error.

---

### `jq: parse error` when piping `curl -i`

**Symptom**

```text
jq: parse error: Invalid numeric literal at line 1, column 9
```

**Cause**

`curl -i` writes the HTTP headers and a blank line **before** the
body. `jq` tries to parse the whole stream as JSON.

**Fix**

Drop `-i`, or split header from body:

```bash
curl -s "$TCKDB_BASE_URL/scientific/species/search?smiles=O" | jq .
```

For HTTP-status debugging without breaking `jq`:

```bash
curl -s -o /tmp/body.json -w 'HTTP %{http_code}\n' "$URL"
jq . /tmp/body.json
```

---

## Cloudflare Tunnel and DNS

These only apply to self-hosted public deployments. Local dev does
not need Cloudflare.

### `curl: could not resolve host` after adding a Cloudflare DNS route

**Symptom**

```text
curl: (6) Could not resolve host: tckdb.example.org
```

…even though the DNS record is visibly added in the Cloudflare
dashboard.

**Cause**

Your local DNS resolver has cached the previous **NXDOMAIN** answer.
Negative caching is bounded by the zone's negative TTL (often a few
minutes).

**Fix**

Wait it out, or flush:

```bash
# systemd-resolved
sudo resolvectl flush-caches

# nscd
sudo systemctl restart nscd

# macOS
sudo dscacheutil -flushcache && sudo killall -HUP mDNSResponder
```

Try a different resolver to confirm:

```bash
dig +short tckdb.example.org @1.1.1.1
```

**Verify**

```bash
curl -fsS https://tckdb.example.org/api/v1/health
```

---

### DNS record vs tunnel vs ingress rule — all three needed

**Symptom**

Public URL returns Cloudflare's "1033: Argo Tunnel error" or
"1016: Origin DNS error" page, or just hangs.

**Cause**

The three layers are independent and all have to line up:

1. **DNS** — `tckdb.example.org` points at Cloudflare (`CNAME` to
   the tunnel-managed hostname).
2. **Tunnel** — `cloudflared` is running on the host and registered
   with the same Cloudflare account.
3. **Ingress rule** — inside the tunnel config, `tckdb.example.org`
   forwards to `http://127.0.0.1:8010`.

Missing any one of these breaks the path.

**Fix**

Verify in the Cloudflare Zero Trust dashboard:

- Networks → Tunnels → your tunnel is "Healthy".
- The tunnel's Public Hostnames list includes `tckdb.example.org`
  with `Type: HTTP` and `URL: 127.0.0.1:8010`.
- DNS → the corresponding `CNAME` exists (Cloudflare usually
  auto-creates it; double-check).

**Verify**

```bash
docker compose --env-file .env.selfhosted \
    --profile cloudflare logs cloudflared | tail -50
curl -fsS https://tckdb.example.org/api/v1/health
```

---

### DataGrip / DBeaver through a protected `cloudflared access tcp` tunnel

**Symptom**

You want a DB GUI session against a remote TCKDB without opening
Postgres on `0.0.0.0`.

**Fix**

The pattern is documented in
[self_hosted_single_node.md](self_hosted_single_node.md) ("Optional:
protected DB-GUI access via TCP tunnel"). One-liner reminder:

```bash
cloudflared access tcp \
    --hostname pg-tckdb.example.org \
    --url localhost:15434
```

Leave that running, then point DataGrip at:

```text
Host:     127.0.0.1
Port:     15434
Database: tckdb
User:     tckdb
Password: <DB_PASSWORD from .env.selfhosted>
```

Three auth layers stack: Cloudflare Access policy → tunnel access
token → Postgres role/password. A leaked DB password alone cannot
reach the database from the internet.

**Verify**

```bash
psql -h 127.0.0.1 -p 15434 -U tckdb -d tckdb -c 'select 1'
```

---

## When in doubt

1. Run the doctor first:
   ```bash
   backend/scripts/tckdb_doctor.sh
   ```
2. Read the matching section above. Every entry includes a verify
   command — running it after the fix confirms the issue is closed
   out, rather than just silently masked.
3. If the failure is novel, file a small reproducer (env vars, exact
   command, error output) before opening an issue.
