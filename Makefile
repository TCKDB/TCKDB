# TCKDB local-dev convenience targets.
#
# These wrap the bare commands so contributors don't have to remember
# the right env-var combination on every fresh-start. They are
# deliberately thin: each target is one or two commands you could run
# yourself if you wanted to.

.PHONY: up down migrate test

# Start local Postgres + MinIO and apply migrations to tckdb_dev.
#
# Note: if you've recently edited the latest initial migration
# (currently d861dfd60891), the existing dev DB will be stale —
# alembic will not replay an already-applied edited migration. See
# CLAUDE.md "Migration Rules" for the dropdb/createdb habit in that
# case.
up:
	docker compose -f docker-compose.local.yml up -d
	DB_NAME=tckdb_dev conda run -n tckdb_env alembic upgrade head

# Stop the infrastructure containers (data volumes preserved).
down:
	docker compose -f docker-compose.local.yml down

# Re-apply migrations against tckdb_dev without touching containers.
migrate:
	DB_NAME=tckdb_dev conda run -n tckdb_env alembic upgrade head

# Run the full backend test suite.
test:
	conda run -n tckdb_env pytest backend/tests/
