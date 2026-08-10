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
# STATUS: validated. .github/workflows/build-api-image.yml now builds the
# native arch, RUNS it, and fails the workflow unless the image imports (rdkit,
# tckdb_schemas, the app factory), serves /api/v1/health, and starts its inline
# worker thread — plus an arm64 import check under emulation, because arm64 is
# the deployment target and a missing arm64 build is the failure that would
# only ever show up on the Pi. A `docker build` that succeeds proves the layers
# assembled and nothing more, which is why publishing used to be the first time
# anyone found out.
#
# If a runtime import fails for a missing dependency, add it to
# backend/environment.yml (the single source of runtime deps) rather than to a
# pip line here.
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
# Text encoding, pinned in the image rather than per deployment.
#
# On 2026-08-04 an em dash in a warning message rolled back an entire upload
# against a SQL_ASCII database. The database has since been converted, but the
# encoding a deployment ends up with was a property of whoever created the
# volume and whatever the base image happened to set -- i.e. not a property of
# this repository at all, and so not one anybody could verify.
#
# This is the only place that covers every consumer at once: the API and the
# migration container started by scripts/ops/tckdb_deploy.sh, the compose
# `worker` service, and the smoke runs in .github/workflows/build-api-image.yml.
# Setting it per docker-run invocation instead would mean four places to keep
# in step, and the one that drifts is the one nobody notices.
#
#   LANG / LC_ALL     debian-slim ships no locale, so Python's filesystem and
#                     stdio encodings would otherwise be derived from a POSIX
#                     default. C.UTF-8 is built into glibc and needs no
#                     locale-gen.
#   PYTHONIOENCODING  a non-ASCII character reaching a log line must not raise
#                     UnicodeEncodeError. Crashing on the way to *reporting* a
#                     problem is the worst available failure.
#   PGCLIENTENCODING  belt to the braces of ?client_encoding=utf8 in the URL
#                     that app/api/config.py and alembic/env.py both build; it
#                     also covers psql and pg_dump run inside this image.
# ---------------------------------------------------------------------------
ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONIOENCODING=utf-8 \
    PGCLIENTENCODING=UTF8

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
