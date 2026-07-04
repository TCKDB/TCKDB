# syntax=docker/dockerfile:1
#
# TCKDB API image.
#
# Base: micromamba (conda-forge). The scientific stack (RDKit, cantera, numpy)
# ships as prebuilt binaries on conda-forge for BOTH amd64 and arm64, so the
# same Dockerfile builds a native image on either architecture — which is what
# makes multi-arch builds practical (a Raspberry Pi is arm64; most CI/cloud is
# amd64). Building the same stack from pip wheels is far less reliable on arm64.
#
# STATUS: scaffold. It must be validated by an actual multi-arch CI build (see
# .github/workflows/build-api-image.yml). If a runtime import fails for a
# missing dependency, add it to backend/environment.yml (the single source of
# runtime deps) rather than to a pip line here.
#
# Build context is the REPO ROOT (so the shared schemas package is available):
#   docker build -f backend/Dockerfile -t tckdb-api .

# Pin the base by tag; consider pinning by digest for full reproducibility.
FROM mambaorg/micromamba:2-debian12-slim

# ---------------------------------------------------------------------------
# 1. Runtime dependencies from the conda recipe (RDKit, cantera, FastAPI,
#    SQLAlchemy, Alembic, psycopg, pydantic, boto3, pyyaml, isbnlib2, ...).
# ---------------------------------------------------------------------------
COPY --chown=$MAMBA_USER:$MAMBA_USER backend/environment.yml /tmp/environment.yml
RUN micromamba install -y -n base -f /tmp/environment.yml \
    && micromamba clean --all --yes

# Activate the base env for the remaining RUN/CMD instructions.
ARG MAMBA_DOCKERFILE_ACTIVATE=1

# ---------------------------------------------------------------------------
# 2. The shared schemas package and the backend package. Installed with
#    --no-deps because conda already provides every runtime dependency; this
#    is the recommended conda-then-pip pattern (avoids pip clobbering conda
#    binaries with wheels).
# ---------------------------------------------------------------------------
COPY --chown=$MAMBA_USER:$MAMBA_USER schemas/python/tckdb-schemas /app/schemas/tckdb-schemas
COPY --chown=$MAMBA_USER:$MAMBA_USER backend /app/backend
RUN pip install --no-deps -e /app/schemas/tckdb-schemas \
    && pip install --no-deps -e /app/backend

WORKDIR /app/backend
EXPOSE 8010

# DB_* / S3_* / RATE_LIMIT_* etc. are supplied at runtime via the environment
# (docker-compose `env_file:` or a systemd EnvironmentFile). Bind 0.0.0.0
# INSIDE the container; publish it to host loopback (127.0.0.1) so a tunnel /
# reverse proxy is the only thing that can reach it — never expose the port
# directly on a public interface.
CMD ["uvicorn", "app.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8010"]
