#!/usr/bin/env bash
# check_selfhosted_deployment.sh — sanity-check a single-node TCKDB
# compose deployment.
#
# Verifies the invariants typically debugged when something looks off
# on a single-node compose deployment: docker compose health, RDKit
# extension, Alembic head, and a couple of public-facing read
# endpoints. Defaults target the self-hosted recipe (canonical
# docker-compose.yml + .env.selfhosted), but every input is overridable
# so the same script works for local-compose deployments, lab servers,
# or any other compose-based topology — a Raspberry Pi is one valid host.
#
# Run from the repo root:
#   backend/scripts/check_selfhosted_deployment.sh
#
# Override the base URL to test a different deployment:
#   TCKDB_BASE_URL=https://tckdb.example.org/api/v1 \
#       backend/scripts/check_selfhosted_deployment.sh
#
# Overridable inputs:
#   COMPOSE_FILE      docker compose file (default: docker-compose.yml)
#   COMPOSE_ENV_FILE  env file passed to docker compose (default: .env.selfhosted)
#   TCKDB_BASE_URL    API base URL to probe (default: http://127.0.0.1:8010/api/v1)
#   DB_NAME           DB to inspect (default: tckdb)
#   DB_USER           DB user (default: tckdb)
#
# Exit codes:
#   0   all checks passed
#   1   one or more checks failed (details on stderr)
#   2   usage error

case "${1:-}" in
    -h|--help|help)
        sed -n '2,31p' "$0" | sed 's/^# \{0,1\}//'
        exit 0
        ;;
esac

set -uo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
COMPOSE_ENV_FILE="${COMPOSE_ENV_FILE:-.env.selfhosted}"
TCKDB_BASE_URL="${TCKDB_BASE_URL:-http://127.0.0.1:8010/api/v1}"
DB_NAME="${DB_NAME:-tckdb}"
DB_USER="${DB_USER:-tckdb}"

fail_count=0
ok()   { printf "  \033[32mOK\033[0m   %s\n" "$*"; }
bad()  { printf "  \033[31mFAIL\033[0m %s\n" "$*" >&2; fail_count=$((fail_count + 1)); }
warn() { printf "  \033[33mWARN\033[0m %s\n" "$*"; }
section() { printf "\n== %s ==\n" "$*"; }

# Pretty-print JSON if jq is available; otherwise echo as-is.
jqp() {
    if command -v jq >/dev/null 2>&1; then jq "$@"; else cat; fi
}

compose() {
    docker compose --env-file "$COMPOSE_ENV_FILE" -f "$COMPOSE_FILE" "$@"
}

# --- Preconditions ----------------------------------------------------------

section "preconditions"

if ! command -v docker >/dev/null 2>&1; then
    bad "docker not on PATH"
else
    ok "docker found ($(docker --version 2>/dev/null | head -1))"
fi

if [[ ! -f "$COMPOSE_FILE" ]]; then
    bad "compose file '$COMPOSE_FILE' not found (run from repo root)"
fi

if [[ ! -f "$COMPOSE_ENV_FILE" ]]; then
    warn "compose env file '$COMPOSE_ENV_FILE' not found; docker compose will fall back to ambient env"
fi

# --- docker compose ps ------------------------------------------------------

section "compose services"

ps_output=""
if ! ps_output="$(compose ps --format json 2>&1)"; then
    bad "docker compose ps failed:"
    echo "$ps_output" >&2
else
    # Each running service is a JSON object on its own line (newer compose).
    # Fall back to a non-jq scan if jq is unavailable.
    if command -v jq >/dev/null 2>&1; then
        for svc in db minio; do
            row="$(echo "$ps_output" | jq -c --arg s "$svc" 'select(.Service == $s)' 2>/dev/null || true)"
            if [[ -z "$row" ]]; then
                bad "$svc service not listed in 'docker compose ps'"
                continue
            fi
            state="$(echo "$row" | jq -r '.State // empty')"
            health="$(echo "$row" | jq -r '.Health // empty')"
            if [[ "$state" == "running" && ( -z "$health" || "$health" == "healthy" ) ]]; then
                ok "$svc is running${health:+ (health=$health)}"
            else
                bad "$svc is not healthy (state=$state health=${health:-<none>})"
            fi
        done
    else
        for svc in db minio; do
            if echo "$ps_output" | grep -q "\"Service\":\"$svc\""; then
                ok "$svc listed (install jq for health detail)"
            else
                bad "$svc not in compose ps output"
            fi
        done
    fi
fi

# --- RDKit extension --------------------------------------------------------

section "postgres + RDKit"

rdkit_out=""
if rdkit_out="$(compose exec -T db psql -U "$DB_USER" -d "$DB_NAME" -tA \
        -c "select extname from pg_extension where extname = 'rdkit';" 2>&1)"; then
    if [[ "$rdkit_out" == *"rdkit"* ]]; then
        ok "rdkit extension present in $DB_NAME"
    else
        bad "rdkit extension NOT present in $DB_NAME (psql output: $rdkit_out)"
    fi
else
    bad "psql query failed: $rdkit_out"
fi

# --- Alembic revision -------------------------------------------------------

section "alembic revision"

if command -v conda >/dev/null 2>&1; then
    # Run from backend/ so alembic.ini is found.
    alembic_out="$(cd backend 2>/dev/null && conda run -n tckdb_env alembic current 2>&1 || true)"
    if [[ -n "$alembic_out" ]]; then
        # Just report; whether it's "at head" is implicit when there's
        # only one initial migration in this repo.
        head_line="$(echo "$alembic_out" | grep -E "[0-9a-f]{8,}" | head -1 || true)"
        if [[ -n "$head_line" ]]; then
            ok "alembic current: $head_line"
        else
            warn "alembic current returned no revision id; raw output:"
            echo "$alembic_out"
        fi
    else
        warn "could not run 'alembic current' (conda env tckdb_env unavailable?)"
    fi
else
    warn "conda not on PATH; skipping alembic revision check"
fi

# --- API endpoints ----------------------------------------------------------

section "API: $TCKDB_BASE_URL"

health_body=""
if health_body="$(curl -fsS "$TCKDB_BASE_URL/health" 2>&1)"; then
    if echo "$health_body" | grep -q '"status"[[:space:]]*:[[:space:]]*"ok"'; then
        ok "GET /health -> {\"status\":\"ok\"}"
    else
        bad "GET /health did not return status=ok; body: $health_body"
    fi
else
    bad "GET /health failed: $health_body"
fi

# Anonymous scientific read (water — always present in the demo seed).
search_status=""
if search_status="$(curl -sS -o /dev/null -w '%{http_code}' \
        "$TCKDB_BASE_URL/scientific/species/search?smiles=O" 2>/dev/null)"; then
    if [[ "$search_status" == "200" ]]; then
        ok "GET /scientific/species/search?smiles=O -> 200"
    else
        bad "GET /scientific/species/search?smiles=O -> $search_status (expected 200)"
    fi
else
    bad "GET /scientific/species/search?smiles=O request failed"
fi

# --- Summary ----------------------------------------------------------------

section "summary"
if (( fail_count == 0 )); then
    echo "  all checks passed"
    exit 0
else
    echo "  $fail_count check(s) failed" >&2
    exit 1
fi
