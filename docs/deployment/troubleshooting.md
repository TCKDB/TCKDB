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

Or, less destructive, create the DB by hand:

```bash
docker compose exec db \
    createdb -U tckdb tckdb_dev
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

A pre-existing Postgres data volume created with `SQL_ASCII`
encoding. Docker won't recreate the DB on subsequent starts.

**Fix**

This is a clean-slate operation — back up first if you have anything
worth keeping:

```bash
docker compose down -v
docker compose up -d
```

The new volume initializes with `UTF8`. The `DB_CLIENT_ENCODING=utf8`
in the env templates is belt-and-braces on top of that.

**Verify**

```bash
docker compose exec db psql -U tckdb -l
# expect: Encoding | UTF8
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
