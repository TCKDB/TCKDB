# Notes for the upcoming API containerization

This page tracks the packaging foundation for the **next** DX
milestone — shipping the FastAPI backend as a container so a
self-hosted operator's top-level invocation becomes:

```bash
docker compose --env-file .env.selfhosted \
    --env-file .env.db-admin \
    up -d db minio api
```

The container itself is not yet in the repo. What **is** in the repo
now (after this dependency-standardization pass) is the metadata the
container will build off cleanly.

---

## Current state of backend Python packaging

| File | What it is |
|---|---|
| [backend/pyproject.toml](../../backend/pyproject.toml) | **Authoritative package metadata.** Defines `tckdb-backend`, its runtime dependencies, dev/test extras (`[dev]`), and an opt-in pip-RDKit extra (`[rdkit]`). |
| [backend/uv.lock](../../backend/uv.lock) | Resolved lockfile generated with `uv lock`. Pins every transitive dep (including all extras). Regenerate with `cd backend && uv lock`. |
| [backend/environment.yml](../../backend/environment.yml) | Conda/mamba env recipe — still recommended for developer workstations because of conda-forge RDKit. |

There are now **two complementary sources of truth**:

- `environment.yml` is the **system toolchain** recipe (Python +
  RDKit). It is what a new developer runs first.
- `pyproject.toml` is the **package metadata** for `tckdb-backend`
  itself, plus its pip-installable dependency list. It is what a
  containerized build will consume.

`uv.lock` is the reproducibility layer on top of `pyproject.toml`.

---

## Supported developer setups

### Conda + pip (recommended on dev workstations)

The smoothest path when you want the conda-forge RDKit build and a
fully-working chemistry stack out of the box:

```bash
mamba env create -n tckdb_env -f backend/environment.yml
conda activate tckdb_env
cd backend
pip install -e ".[dev]"          # skip the [rdkit] extra — conda has it
```

After this, both `make api` and `cd backend && pytest` work.

### Pure pip / uv (container-friendly, lockfile-driven)

The path the eventual API container will use:

```bash
cd backend
uv sync --extra dev --extra rdkit
```

`uv sync` installs the locked versions from `uv.lock`. With both
extras you get the full dev stack plus a pip-installed RDKit
(rdkit-pypi-style wheel from conda-forge's PyPI mirror or rdkit's
official wheel, depending on platform).

Pure `pip` works too:

```bash
cd backend
pip install -e ".[dev,rdkit]"
```

…but won't pin transitive versions.

### Regenerating the lockfile

After editing `pyproject.toml` (adding a dep, bumping a bound):

```bash
cd backend
uv lock
git add pyproject.toml uv.lock
```

`uv lock` resolves every declared extra, so the lockfile remains
valid whether you `uv sync` with or without `--extra rdkit`.

---

## Audit answers (current state)

1. **What file defines runtime deps?** `backend/pyproject.toml`
   `[project] dependencies`. `backend/environment.yml` mirrors it for
   conda users.
2. **Conda-only?** No. `pyproject.toml` is pip-installable; `uv.lock`
   pins it.
3. **Includes RDKit?** Yes — via `environment.yml` (conda-forge) for
   conda users, and via the opt-in `[rdkit]` extra for pip/uv users.
4. **Runtime only, or also dev/test?** Both, cleanly separated:
   `[dev]` extra in `pyproject.toml` covers pytest, pytest-cov, httpx,
   and ruff.
5. **Suitable as Docker build input?** Yes — see below.
6. **Pins?** Yes, in `uv.lock` (54 packages resolved). Top-level
   bounds in `pyproject.toml` are kept relaxed (lower bounds at
   major-feature breaks: SQLAlchemy 2, Pydantic 2, psycopg 3, FastAPI
   0.110+).

---

## Is this suitable as Docker build input?

**Yes.** A future `backend/Dockerfile` can now:

1. `COPY pyproject.toml uv.lock /app/`
2. `RUN uv sync --frozen --extra rdkit` (no `--extra dev`)
3. `COPY app/ /app/app/` and `COPY main.py /app/`
4. `CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8010"]`

The first two layers are cache-friendly: rebuilds only re-run the
expensive `uv sync` when `pyproject.toml` or `uv.lock` changes, not
on every source edit.

A multi-stage build can split `uv` install from a slim runtime base.

---

## RDKit packaging decision

RDKit is **opt-in via the `[rdkit]` extra** rather than a hard
runtime dependency. Rationale:

- Conda users get the conda-forge build through `environment.yml` —
  including it again in `pyproject.toml` base deps would double-install
  it (or worse, conflict with the conda-managed copy).
- The lockfile must resolve cleanly on every supported platform. The
  pip `rdkit` wheel exists on x86_64 Linux/macOS and aarch64 Linux,
  which covers the deployment targets, but making it a hard dep
  would tie `pip install tckdb-backend` to those platforms.
- Containers explicitly opt in: `uv sync --extra rdkit` is part of
  the future Dockerfile recipe.

When **not** to use the extra:

- You're on a conda env that already installed `rdkit` from
  conda-forge. The pip wheel is the same project but a different
  packaging — let conda own it.

---

## Recommendation: ready to containerize?

**Yes — packaging prerequisites are now in place.**

The next milestone can move directly to:

1. Add `backend/Dockerfile` (multi-stage; `uv sync --frozen --extra
   rdkit` against the lockfile).
2. Add an `api` service to `docker-compose.yml`, behind a
   `profiles: [api]` profile initially so the host-run flow keeps
   working without a container build.
3. Verify the new flow ends in a green `make doctor` against the
   containerized API.

Out of scope for this packaging pass:

- The Dockerfile itself.
- Any compose-level API service.
- Renaming `app/` or restructuring the package layout.

---

## Open questions for whoever picks up containerization

- **Base image.** `python:3.13-slim-bookworm` vs `python:3.13-alpine`
  vs a `uv`-blessed base. The slim Debian image is the lowest-risk
  default; Alpine occasionally trips C-extension wheels.
- **Two-stage build.** Builder stage with `uv` + compiler toolchain
  for any wheels that need building; runtime stage with just the
  resolved site-packages. Reduces final image size considerably.
- **Tagging policy.** `tckdb-backend:dev` rebuilt on every
  `compose build` vs published `:vX.Y.Z` tags. Likely the former
  until a release cadence exists.
- **Worker process.** `TCKDB_INLINE_WORKER=true` runs the upload
  worker inside the API process; if the containerized API offloads
  that to a separate worker service, the same image runs both with a
  different `CMD`.
- **Build context.** The current `backend/Dockerfile` plan assumes
  `context: backend/`. If the image needs anything from the repo root
  (e.g. `clients/python/` for an integrated build), bump
  to `context: .` and adjust paths.
