# TCKDB

<p align="center">
  <img src="docs/assets/tckdb-wordmark-croppped.png" alt="TCKDB wordmark" width="760">
</p>

**Thermochemical & Kinetics Database** — a provenance-rich,
workflow-tool-agnostic database and HTTP API for computational and
experimental chemistry data: species, reactions, transition states,
geometries, calculations, statmech, thermo, kinetics, transport,
networks, artifacts, and review/moderation state.

In practical terms, TCKDB is a database you can run locally or host
for a group. It stores quantum-chemistry and experimental records
with enough context to answer questions like: *Which calculation
produced this thermo value? Which geometry was used? What level of
theory, software version, and workflow generated it? Has the record
been reviewed?*

TCKDB defines a general scientific storage, provenance, and read/query
contract. Workflow tools such as ARC, RMG, or any other computational
chemistry pipeline can adapt to TCKDB; nothing in the schema or API is
shaped around a single tool.

Documentation website: <https://calvinp0.github.io/tckdbv2/>

---

## Start here

Choose the path that matches what you are trying to do:

- To read the full documentation website, open
  <https://calvinp0.github.io/tckdbv2/> or preview it locally with
  `conda run -n tckdb_env mkdocs serve`.
- To understand what TCKDB is, read [What is TCKDB?](#what-is-tckdb)
  and [What TCKDB stores](#what-tckdb-stores).
- To run it on your machine, use
  [Quick start: local development](#quick-start-local-development).
- To confirm it works with real responses, use
  [Load demo data](#load-demo-data), then
  [Scientific read/query examples](#scientific-readquery-examples).
- To query an existing hosted instance, start with
  [Scientific read/query examples](#scientific-readquery-examples).
- To upload computed or experimental records, see
  [Uploads and workflow-tool integration](#uploads-and-workflow-tool-integration).
- To deploy it for a lab or group, see
  [Quick start: self-hosted single-node deployment](#quick-start-self-hosted-single-node-deployment).

If you are primarily a chemistry user, the shortest useful path is:
install the prerequisites, run the local quick start, load the demo
data, then run the query examples for methane and the demo reactions.

---

## What is TCKDB?

TCKDB stores chemistry data together with the calculation context
needed to judge how each number was produced. It exposes that data
through an HTTP API designed for both human-scale queries and
programmatic ingestion.

A short summary:

> TCKDB is a provenance-rich thermochemistry and kinetics database for
> computational and experimental chemical data.

The point is not just storage — it is **queryable scientific records
with provenance and trust state**, addressed by stable public refs and
fed by an authenticated upload contract.

---

## Why TCKDB exists

The computational thermochemistry and kinetics community does not have
one agreed relational schema for thermo, kinetics, conformers,
transition states, calculations, and provenance. Many datasets contain
useful values but lose the calculation context — the geometry, the
level of theory, the software release, the underlying statmech — that
makes those values trustworthy and reproducible.

TCKDB is an attempt to make scientific records queryable together
with their full provenance and review state, so that:

- Kinetics and thermo data are reproducible from inside the database.
- Methods, software versions, and basis sets can be compared at scale.
- Workflow tools can ingest into a single, structured destination
  instead of bespoke output files.
- Anonymous scientific reads are useful out of the box.
- Future community review and curation have a place to live.

---

## What TCKDB stores

| Category | Entities |
|---|---|
| **Identity** | `species`, `species_entry`, `chem_reaction`, `reaction_entry`, `transition_state`, `transition_state_entry` |
| **Structure** | `geometry`, `conformer_group`, `conformer_observation` |
| **Calculations** | `calculation` (hub), single points, optimizations, frequencies, scans, IRCs, NEBs, composite calculations, input/output geometries, calculation parameters, dependencies, artifacts |
| **Scientific products** | `statmech`, `thermo` (Cp/H/S, NASA polynomials), `kinetics` (Arrhenius, modified Arrhenius, Chebyshev/PLOG pressure dependence), `transport`, `network` (master-equation networks) |
| **Trust / provenance** | `level_of_theory`, `software` + `software_release`, `workflow_tool` + `workflow_tool_release`, `literature` + `author`, `submission`, `record_review` |

Raw output files (Gaussian/Orca logs, NEB traces, …) live in
S3-compatible object storage as **artifacts** and are addressable
through the API by handle.

For example, a Gaussian or ORCA frequency workflow may produce a
geometry, an optimization calculation, a frequency calculation,
software/version provenance, a level of theory, vibrational/statmech
data, and derived thermo. TCKDB stores those as linked records instead
of flattening them into one disconnected table or file.

---

## Core scientific concepts

A short glossary of the most-used nouns:

- **`species`** is graph-level molecular identity (the connectivity).
  **`species_entry`** is a specific scientific form of that species —
  stereochemistry, electronic state, isotopologue, stationary-point
  kind.
- **`chem_reaction`** is reactants → products as multisets of species.
  **`reaction_entry`** is a scientific instance of that reaction with
  resolved `species_entry` participants and attached kinetics.
- **`transition_state`** / **`transition_state_entry`** apply the same
  split to saddle points: identity vs scientific instance with
  geometry, frequencies, and IRC linkage.
- **`geometry`** is a coordinate set, addressed by public handle
  (`geom_…`). Coordinates are fetched explicitly.
- **`calculation`** is the hub for any computed result (`sp`, `opt`,
  `freq`, `scan`, `irc`, `neb`, composite). Specific result rows
  attach to the hub. Calculations form a small **DAG** so downstream
  results cite the upstream calculations they consumed.
- **`conformer_group`** clusters observations of one conformer
  identity. **`conformer_observation`** is a single observed conformer
  from a specific calculation.
- **`statmech`**, **`thermo`**, **`kinetics`**, **`transport`**, and
  **`network`** are the scientific-product tables. Each row carries
  provenance back to the calculations and levels of theory that
  produced it.
- **Public refs vs internal IDs.** Every read response addresses
  records by stable public refs (`species_…`, `reaction_entry_…`,
  `geom_…`, `lot_…`, …). Integer primary keys are hidden by default
  and only surface under explicit `include=internal_ids`, per the
  visibility policy.
- **Review/moderation state.** `record_review` rows track per-record
  curation status (draft → pending → approved → rejected/deprecated).
  Public reads default to approved records.

For the longer-form treatment with examples and design rationale, see
[docs/guides/core_concepts.md](docs/guides/core_concepts.md).

---

## Architecture

In normal use, clients do **not** connect directly to Postgres or
MinIO. You talk to the TCKDB API, either with curl, the Python client,
or a workflow tool. The API then reads/writes the database and artifact
storage for you.

```text
              ┌──────────────────────┐
              │   FastAPI backend    │  127.0.0.1:8010 (loopback)
              │   /api/v1/*          │
              └─────────┬────────────┘
                        │
       ┌────────────────┼──────────────────┐
       ▼                ▼                  ▼
 ┌──────────┐    ┌────────────┐     ┌────────────────┐
 │ Postgres │    │ MinIO / S3 │     │ optional       │
 │ + RDKit  │    │ artifacts  │     │ frontend or    │
 │ (private)│    │ (private)  │     │ workflow tools │
 └──────────┘    └────────────┘     └────────────────┘

       ▲                                     ▲
       │                                     │
       └─ tckdb-client (Python) ─────────────┘
       └─ any HTTP client ───────────────────┘

  Optional ingress in front of the API for public deployments:
  Cloudflare Tunnel, nginx, Caddy, Traefik, Tailscale, WireGuard, …
```

Key invariants:

- **PostgreSQL with the RDKit cartridge** is the chemistry-aware
  storage layer; **MinIO** (or any S3-compatible store) holds
  artifacts.
- **The API is the public surface.** All client traffic — read,
  upload, admin — goes through `/api/v1/*`.
- **The database and object storage are private services.** Shipped
  Compose files publish them on `127.0.0.1` only; they should never
  face the LAN or internet directly.
- **Ingress is a deployment choice**, not part of the scientific
  model. The same backend runs behind Cloudflare Tunnel, an nginx
  reverse proxy, a Tailscale subnet, or nothing at all.

---

## Current capabilities

Based on what is actually wired up in this repository:

- Scientific read/query endpoints under `/api/v1/scientific/*`
  (species, reactions, kinetics, thermo, geometries,
  species-calculations, provenance).
- Public stable refs as the default handle in read responses, with
  internal IDs hidden by deployment policy.
- Dedicated geometry detail endpoint for fetching coordinates by
  handle.
- Anonymous scientific reads on default deployments.
- Authenticated uploads using `X-API-Key` (admin, upload, and
  curator/reviewer roles).
- Calculation hub + result/dependency/parameter/artifact tables.
- Statmech, thermo (incl. NASA), Arrhenius and pressure-dependent
  kinetics, transport, and master-equation network records.
- Submissions and per-record review state.
- A thin Python client (`tckdb-client`) for programmatic
  read/upload.
- Local-development and self-hosted single-node deployment recipes
  driven by a single `docker-compose.yml` with profiles.
- Helper scripts for admin bootstrap, API-key minting (`tckdb_auth.sh`),
  setup diagnostics (`tckdb_doctor.sh`), and self-hosted deployment
  checks (`check_selfhosted_deployment.sh`).

The schema, upload payloads, and read API are still evolving — see
**Project status** below.

---

## Quick start: local development

This path is for running TCKDB on your own machine. It starts the
database and object storage in Docker, runs migrations, then starts
the API on `http://127.0.0.1:8010`.

Prerequisites:

- Git.
- Docker Desktop or Docker Engine with Compose.
- Conda or Mamba. The recommended environment uses conda-forge RDKit.
- A Unix-like shell: Linux, macOS, or WSL on Windows.

The `make` commands below are thin wrappers around Docker, Alembic,
and Uvicorn. They are the preferred local-development commands because
they set the expected working directories and database name for you.

```bash
git clone <repo-url> tckdb
cd tckdb

# 1. Copy the env templates (root Compose env + backend app env)
cp .env.example .env
cp backend/.env.example backend/.env

# 2. Set up the Python env (one-time). Pick ONE of the two paths.
#
#    Path A — conda + pip (recommended; conda-forge RDKit):
mamba env create -n tckdb_env -f backend/environment.yml
conda activate tckdb_env
cd backend && pip install -e ".[dev]" && cd ..
#
#    Path B — pure pip / uv (lockfile-driven, no conda):
# cd backend && uv sync --extra dev --extra rdkit && cd ..

# 3. Start Postgres+RDKit + MinIO and run migrations
make up

# 4. Start the API on 127.0.0.1:8010 (foreground; Ctrl-C to stop)
make api

# 5. From another shell — verify the stack
make doctor

# 6. Smoke-test
curl http://127.0.0.1:8010/api/v1/health
# -> {"status":"ok"}
```

At this point the service is running, but the database may not contain
scientific records yet. Query endpoints can legitimately return empty
`records` arrays until you load data or upload records.

`make help` lists every available target. The first-run diagnostic
`make doctor` checks Docker, the env files, db/minio health, RDKit,
Alembic, and the API — with actionable hints on each failure.

The two Python-env paths in step 2 are complementary:
[backend/environment.yml](backend/environment.yml) installs the
system toolchain (Python + conda-forge RDKit), and
[backend/pyproject.toml](backend/pyproject.toml) defines the
`tckdb-backend` package, its dependency list, dev/test extras, and
the optional pip-RDKit extra. The lockfile next to it
([backend/uv.lock](backend/uv.lock)) pins exact versions for
reproducible installs. See
[docs/deployment/api_containerization_notes.md](docs/deployment/api_containerization_notes.md)
for the full story.

If anything fails, run `make doctor` and consult
[docs/deployment/troubleshooting.md](docs/deployment/troubleshooting.md).
For a longer walkthrough see
[docs/deployment/local-v0.md](docs/deployment/local-v0.md).

---

## Load demo data

For a first successful read/query test, load the small illustrative
demo dataset. It includes methane, ethane, radicals, geometries,
thermo, calculations, and two reactions with kinetics.

> Demo values are not publication-grade. They exist only to exercise
> the API and show response shapes.

With the database already started and migrated:

```bash
cd backend

# Dry run first; writes nothing.
conda run -n tckdb_env python -m scripts.seed_scientific_demo_data

# Actually write the demo rows.
conda run -n tckdb_env python -m scripts.seed_scientific_demo_data --yes
```

Then keep the API running with `make api` from the repository root and
try the examples in [Scientific read/query examples](#scientific-readquery-examples).
You should see non-empty `records` for methane (`smiles=C`) and the
demo reactions.

The full demo-data guide, including cleanup notes, is
[docs/guides/scientific_read_demo_data.md](docs/guides/scientific_read_demo_data.md).

---

## Quick start: self-hosted single-node deployment

For a small server (home/lab box, small VPS, single-board computer,
etc.) that exposes TCKDB to a wider audience:

```bash
# 1. Copy the env template and fill in change-me-* values
cp .env.selfhosted.example .env.selfhosted
$EDITOR .env.selfhosted

# 2. Bring up the core data plane (Postgres + MinIO)
docker compose --env-file .env.selfhosted up -d db minio

# 3. Run migrations and seed an admin (from backend/)
cd backend
conda run -n tckdb_env alembic upgrade head
conda run -n tckdb_env python scripts/bootstrap_admin.py \
    --username alice --email alice@example.org --role admin
```

The API itself currently runs from the host Python environment (no
shipped API container yet):

```bash
conda run -n tckdb_env uvicorn main:app --host 127.0.0.1 --port 8010
```

For production use, run it under a process manager — see
[examples/deployment/systemd/tckdb-api.service](examples/deployment/systemd/tckdb-api.service)
for a reference systemd unit.

**Ingress is optional and pluggable.** Cloudflare Tunnel is one
supported option, behind the `cloudflare` Compose profile:

```bash
docker compose --env-file .env.selfhosted \
    --profile cloudflare up -d cloudflared
```

nginx, Caddy, Traefik, Tailscale, WireGuard, or a host-side
`cloudflared` systemd unit are equally valid — see the "Ingress
options" section in
[docs/deployment/self_hosted_single_node.md](docs/deployment/self_hosted_single_node.md).

Run `backend/scripts/check_selfhosted_deployment.sh` after standing
the stack up to verify db/minio/API health, public read access, and
the API-key path end-to-end.

---

## Authentication and API keys

Anonymous scientific reads under `/api/v1/scientific/*` are allowed by
default. Uploads and admin actions require an API key sent as
`X-API-Key`.

Seed an admin or curator account (idempotent):

```bash
cd backend
conda run -n tckdb_env python scripts/bootstrap_admin.py \
    --username alice \
    --email   alice@example.org \
    --role    admin
```

Log in and mint an API key via the helper script:

```bash
export TCKDB_BASE_URL="http://127.0.0.1:8010/api/v1"
backend/scripts/tckdb_auth.sh login-create-key --name dev-upload
source .tckdb_auth.env

# Verify
backend/scripts/tckdb_auth.sh me
```

The full recipe (curators, key rotation, wiring against a workflow
tool) lives in
[docs/deployment/admin_auth_quickstart.md](docs/deployment/admin_auth_quickstart.md).

---

## Scientific read/query examples

All scientific reads live under `/api/v1/scientific/*` and are
anonymous-readable on default deployments.

If you loaded the demo data, `smiles=C` queries methane and should
return non-empty results. If you have not loaded or uploaded data yet,
the same endpoints may return an empty `records` array; that means the
API is working but has nothing scientific to return.

```bash
# Species search by SMILES (simple atom):
curl -G "http://127.0.0.1:8010/api/v1/scientific/species/search" \
    --data-urlencode "smiles=C"

# Bracketed SMILES — always url-encode with --data-urlencode,
# otherwise brackets get interpreted as a curl URL range:
curl -G "http://127.0.0.1:8010/api/v1/scientific/species/search" \
    --data-urlencode "smiles=C[CH]C"

# Thermo search by SMILES:
curl -G "http://127.0.0.1:8010/api/v1/scientific/thermo/search" \
    --data-urlencode "smiles=CCO"

# Kinetics search by reactants/products (repeat the param for each
# participant; prefer the POST form for complex/bracketed SMILES):
curl -G "http://127.0.0.1:8010/api/v1/scientific/kinetics/search" \
    --data-urlencode "reactants=[OH]" \
    --data-urlencode "reactants=C" \
    --data-urlencode "products=O" \
    --data-urlencode "products=[CH3]"

# Geometry detail by public handle (geom_… ref):
curl "http://127.0.0.1:8010/api/v1/scientific/geometries/geom_abc123"
```

For a more guided first run, use the Python example after loading demo
data:

```bash
# One-time if you did not already install the Python client:
pip install -e ./clients/python

python clients/python/examples/scientific_reads.py \
    --base-url http://127.0.0.1:8010/api/v1 \
    --smiles "C" \
    --reactant "[CH3]" --reactant "[H][H]" \
    --product "C" --product "[H]"
```

A longer cookbook with response shapes lives in
[docs/guides/scientific_query_cookbook.md](docs/guides/scientific_query_cookbook.md).
For querying a public hosted instance (rather than localhost), see
[docs/guides/public_hosted_querying.md](docs/guides/public_hosted_querying.md).

---

## Uploads and workflow-tool integration

Uploads require an API key:

```bash
curl -X POST "$TCKDB_BASE_URL/uploads/calculations" \
    -H "X-API-Key: $TCKDB_API_KEY" \
    -H "Content-Type: application/json" \
    --data-binary @my_calculation_payload.json
```

Workflow tools integrate by submitting structured TCKDB upload
payloads to the appropriate `/api/v1/uploads/*` or `/api/v1/bundles/*`
endpoint. The repo ships a thin Python HTTP client at
[clients/python/](clients/python/).

The important distinction: TCKDB uploads are structured scientific
JSON payloads. Raw Gaussian/ORCA/RMG/ARC files can be stored as
artifacts, but the database records still need the parsed scientific
content: species, calculations, levels of theory, geometries, thermo,
kinetics, provenance, and links between them. A workflow adapter is
usually responsible for turning tool output into that JSON shape.

Install it directly from the monorepo without cloning:

```bash
pip install "git+https://github.com/calvinp0/tckdbv2.git#subdirectory=clients/python"
```

For local editable development:

```bash
pip install -e ./clients/python
```

`tckdb-client` is intentionally chemistry-free — it knows how to
authenticate, encode payloads, retry, and handle idempotency, but it
does not parse SMILES, enumerate conformers, or call RDKit. Any
higher-level domain builders (a future `tckdb-builder`/`tckdb-sdk`)
belong in a separate layer above the client.

ARC is one workflow tool that has been used to exercise TCKDB
ingestion end-to-end, but it has no special status in the schema or
API. Any HTTP client that can POST JSON can upload — see
[docs/specs/arc-tckdb-adapter-v0-spec.md](docs/specs/arc-tckdb-adapter-v0-spec.md)
for one worked-through integration.

---

## Security and deployment notes

- **Never commit** `.env` files, populated cookies, API keys,
  Cloudflare tunnel tokens, or SQL backup dumps. The `.gitignore`
  already covers the canonical locations, but treat anything
  containing real values as sensitive.
- **Never bind Postgres directly to `0.0.0.0`.** The shipped Compose
  files publish to `127.0.0.1` for a reason. Operators wanting a DB
  GUI from a workstation should use a protected TCP tunnel (e.g.
  `cloudflared access tcp` behind a Cloudflare Access policy) or SSH
  port forwarding rather than opening the port — see the
  "Protected DB-GUI access" section of
  [docs/deployment/self_hosted_single_node.md](docs/deployment/self_hosted_single_node.md).
- **Keep object storage private** unless you have an explicit reason
  to expose it; artifacts are reachable through the API by handle.
- **Uploads require API keys.** Anonymous reads are allowed under
  `/api/v1/scientific/*`; writes are not.
- **Local auth artifacts are gitignored.** The auth helper scripts
  produce these files on the local disk only — never commit them:
  ```text
  .tckdb_auth.env
  .tckdb_api_key
  .tckdb_cookies.txt
  cookies.txt
  ```

---

## Project status

TCKDB is **under active development** and pre-1.0. The repository
ships a working backend, a Python client, and reference deployment
recipes (local + self-hosted single-node), but:

- The scientific schema and upload payloads may still evolve.
- The read/query API is being stabilized — endpoints exist and are
  tested, but breaking changes are possible before 1.0.
- Deployment tooling (Compose, env templates, helper scripts) is
  evolving toward broader self-host support; Cloudflare Tunnel is
  one tested ingress, others are supported but less documented.
- API containerization is tracked as a separate milestone; for now,
  the API runs from the host Python environment.
- The frontend is early; programmatic clients (HTTP, `tckdb-client`)
  are the primary supported interface.

Treat hosted instances as **evolving research infrastructure**, not a
stable public service yet.

---

## License

License: TBD. Until a `LICENSE` file is added at the repo root, treat
the code as "source-available, all rights reserved" and contact the
maintainers before redistribution.
