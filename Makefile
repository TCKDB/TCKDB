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

.PHONY: up down migrate reset reset-login test

# Start local Postgres + MinIO and apply migrations to tckdb_dev.
# If the latest initial migration was edited, this will silently
# leave the DB stale — use `make reset` instead.
up:
	docker compose -f docker-compose.local.yml up -d
	cd backend && DB_NAME=tckdb_dev conda run -n tckdb_env alembic upgrade head

# Stop the infrastructure containers (data volumes preserved).
down:
	docker compose -f docker-compose.local.yml down

# Re-apply migrations against tckdb_dev without touching containers.
migrate:
	cd backend && DB_NAME=tckdb_dev conda run -n tckdb_env alembic upgrade head

# Destructive: wipe DB + MinIO volumes, restart infra, re-migrate.
# See the workflow comment at the top of this file for when to use
# this vs. `make up`. Does NOT reseed admin credentials or API keys —
# run `make reset-login` for that.
reset:
	docker compose -f docker-compose.local.yml down -v
	docker compose -f docker-compose.local.yml up -d
	cd backend && DB_NAME=tckdb_dev conda run -n tckdb_env alembic upgrade head

# `make reset`, then re-bootstrap the dev admin user, log in, and mint
# a fresh API key. Requires the API server to be running in another
# terminal (dev_login.sh hits the live /auth endpoints):
#     conda run -n tckdb_env uvicorn main:app --host 127.0.0.1 --port 8000
reset-login: reset
	bash backend/scripts/dev_login.sh

# Run the full backend test suite.
test:
	conda run -n tckdb_env pytest backend/tests/
