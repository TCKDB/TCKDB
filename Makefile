# TCKDB local-dev convenience targets.
#
# These wrap the bare commands so contributors don't have to remember
# the right env-var combination on every fresh-start. They are
# deliberately thin: each target is one or two commands you could run
# yourself if you wanted to.
#
# Day-to-day workflow:
#
#   make up          - normal start / after `git pull`. Boots the
#                      infra containers (data preserved) and runs
#                      `alembic upgrade head`. Cheap and idempotent
#                      when no new migrations are pending.
#
#   make reset       - destructive. Wipes the DB + MinIO volumes,
#                      brings infra back up, re-applies migrations
#                      from scratch. Use only when the mutable
#                      initial migration (d861dfd60891) changed —
#                      yours or pulled from main — or when you
#                      intentionally want a clean DB. Does NOT
#                      reseed admin credentials or API keys.
#
#   make reset-login - `make reset` plus a fresh dev admin user,
#                      session cookie, and API key written to
#                      backend/.tckdb_auth.env and
#                      backend/.tckdb_api_key. Use when you want a
#                      clean DB AND new dev auth files. Credential
#                      creation lives here (and in dev_login.sh)
#                      rather than in `reset` so the rotation is an
#                      explicit, visible step.

.PHONY: up down migrate reset reset-login test test-fast test-scientific test-api test-full test-profile api admin doctor check help

# Print available targets.
help:
	@echo "TCKDB local-dev targets:"
	@echo "  make up           Start db/minio + run migrations"
	@echo "  make api          Start the FastAPI backend on 127.0.0.1:8010 (foreground)"
	@echo "  make admin        Bootstrap a dev admin user (idempotent)"
	@echo "  make doctor       Run setup diagnostics (alias: make check)"
	@echo "  make migrate      Re-apply migrations without restarting infra"
	@echo "  make reset        Wipe DB + MinIO volumes, restart, re-migrate"
	@echo "  make reset-login  reset + dev admin + API key (uses dev_login.sh)"
	@echo "  make down         Stop infra (volumes preserved)"
	@echo "  make test         Run the backend test suite"
	@echo ""
	@echo "Test ladder (see backend/docs/testing.md):"
	@echo "  make test-fast        Tier 0/1: ARGS='<path> [-k expr]' for fast inner-loop"
	@echo "  make test-scientific  Tier 2/3: scientific API + scientific_read services"
	@echo "  make test-api         Tier 3:   full tests/api/ regression gate"
	@echo "  make test-full        Tier 4:   full backend suite (pre-push)"
	@echo "  make test-profile     Surface the slowest tests in a target subset"

# Start local Postgres + MinIO and apply migrations to tckdb_dev.
# Uses the canonical docker-compose.yml at the repo root; Compose
# auto-loads .env. If the latest initial migration was edited, this
# will silently leave the DB stale — use `make reset` instead.
up:
	docker compose up -d db minio
	cd backend && DB_NAME=tckdb_dev conda run -n tckdb_env alembic upgrade head

# Stop the infrastructure containers (data volumes preserved).
down:
	docker compose down

# Re-apply migrations against tckdb_dev without touching containers.
migrate:
	cd backend && DB_NAME=tckdb_dev conda run -n tckdb_env alembic upgrade head

# Destructive: wipe DB + MinIO volumes, restart infra, re-migrate.
# See the workflow comment at the top of this file for when to use
# this vs. `make up`. Does NOT reseed admin credentials or API keys —
# run `make reset-login` for that.
reset:
	docker compose down -v
	docker compose up -d db minio
	cd backend && DB_NAME=tckdb_dev conda run -n tckdb_env alembic upgrade head

# `make reset`, then re-bootstrap the dev admin user, log in, and mint
# a fresh API key. Requires the API server to be running in another
# terminal (dev_login.sh hits the live /auth endpoints):
#     conda run -n tckdb_env uvicorn main:app --host 127.0.0.1 --port 8010
reset-login: reset
	bash backend/scripts/dev_login.sh

# Run the full backend test suite.
test:
	conda run -n tckdb_env pytest backend/tests/

# ---------------------------------------------------------------------
# Test-ladder wrappers. Each delegates to the matching shell script
# under ``backend/scripts/`` so the same entry point works whether
# invoked via Make, directly, or wrapped in ``conda run``. Pass extra
# pytest arguments through ``ARGS=...``:
#
#   make test-fast ARGS="tests/api/test_api_health.py"
#   make test-api  ARGS="-x --maxfail=3"
#
# See ``backend/docs/testing.md`` for the full tier policy.
# ---------------------------------------------------------------------

test-fast:
	conda run -n tckdb_env bash backend/scripts/test-fast.sh $(ARGS)

test-scientific:
	conda run -n tckdb_env bash backend/scripts/test-scientific.sh $(ARGS)

test-api:
	conda run -n tckdb_env bash backend/scripts/test-api.sh $(ARGS)

test-full:
	conda run -n tckdb_env bash backend/scripts/test-full.sh $(ARGS)

test-profile:
	conda run -n tckdb_env bash backend/scripts/test-profile.sh $(ARGS)

# Start the FastAPI backend (foreground). Cd into backend/ so the
# `app` package is on sys.path; this is the exact form that the rest
# of the docs assume.
api:
	cd backend && conda run -n tckdb_env --no-capture-output \
	    uvicorn main:app --host 127.0.0.1 --port 8010

# Bootstrap a dev admin user. Idempotent: re-running on an existing
# user leaves the role unchanged (use --force-role-change to override).
# Set TCKDB_BOOTSTRAP_PASSWORD in the environment to avoid passing
# the password via flags.
admin:
	cd backend && conda run -n tckdb_env python scripts/bootstrap_admin.py \
	    --username "$${TCKDB_BOOTSTRAP_USERNAME:-admin}" \
	    --email    "$${TCKDB_BOOTSTRAP_EMAIL:-admin@example.org}" \
	    --role     admin

# Run setup diagnostics (db/minio health, RDKit, alembic, API).
# `check` is an alias.
doctor check:
	bash backend/scripts/tckdb_doctor.sh
