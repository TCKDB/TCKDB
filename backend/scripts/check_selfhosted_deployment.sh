#!/usr/bin/env bash
# check_selfhosted_deployment.sh — sanity-check a single-node TCKDB
# compose deployment.
#
# Verifies the invariants typically debugged when something looks off
# on a single-node compose deployment: no placeholder secrets, docker
# compose health, RDKit extension, Alembic head, and a couple of
# public-facing read endpoints. Defaults target the self-hosted recipe (canonical
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
# A compose project other than the directory's: `-p NAME`, or
# COMPOSE_PROJECT_NAME in the environment.
#
# Overridable inputs:
#   COMPOSE_FILE      docker compose file (default: docker-compose.yml)
#   COMPOSE_ENV_FILE  env file passed to docker compose (default: .env.selfhosted)
#   COMPOSE_ADMIN_ENV_FILE  operator env file, also passed when it exists
#                     (default: .env.db-admin)
#   STORAGE_SERVICE   compose service holding the S3 store (default:
#                     seaweedfs; set to minio on a MinIO deployment)
#   TCKDB_BASE_URL    API base URL to probe
#                     (default: http://127.0.0.1:<env file's TCKDB_API_PORT or 8010>/api/v1)
#   DB_NAME           DB to inspect (default: the env file's, else tckdb)
#   DB_USER           DB user (default: the env file's, else tckdb)
#
# Exit codes:
#   0   all checks passed
#   1   one or more checks failed (details on stderr)
#   2   usage error

PROJECT_ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help|help)
            sed -n '2,/^#   2 /p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        -p|--project-name)
            [[ $# -ge 2 ]] || { echo "error: $1 needs a value" >&2; exit 2; }
            PROJECT_ARGS=(-p "$2")
            shift 2
            ;;
        *)
            echo "error: unknown argument '$1' (see --help)" >&2
            exit 2
            ;;
    esac
done

set -uo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
COMPOSE_ENV_FILE="${COMPOSE_ENV_FILE:-.env.selfhosted}"
COMPOSE_ADMIN_ENV_FILE="${COMPOSE_ADMIN_ENV_FILE:-.env.db-admin}"
STORAGE_SERVICE="${STORAGE_SERVICE:-seaweedfs}"

# env_value FILE KEY: the last `KEY=value` in FILE without surrounding quotes;
# empty when FILE or KEY is absent.
env_value() {
    [[ -f "$1" ]] || return 0
    sed -n "s/^[[:space:]]*\(export[[:space:]]\{1,\}\)\{0,1\}$2=//p" "$1" | tail -1 \
        | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/"
}

env_api_port="$(env_value "$COMPOSE_ENV_FILE" TCKDB_API_PORT)"
env_db_name="$(env_value "$COMPOSE_ENV_FILE" DB_NAME)"
env_db_user="$(env_value "$COMPOSE_ENV_FILE" DB_USER)"
TCKDB_BASE_URL="${TCKDB_BASE_URL:-http://127.0.0.1:${env_api_port:-8010}/api/v1}"
DB_NAME="${DB_NAME:-${env_db_name:-tckdb}}"
DB_USER="${DB_USER:-${env_db_user:-tckdb}}"

fail_count=0
ok()   { printf "  \033[32mOK\033[0m   %s\n" "$*"; }
bad()  { printf "  \033[31mFAIL\033[0m %s\n" "$*" >&2; fail_count=$((fail_count + 1)); }
warn() { printf "  \033[33mWARN\033[0m %s\n" "$*"; }
section() { printf "\n== %s ==\n" "$*"; }

# Pretty-print JSON if jq is available; otherwise echo as-is.
jqp() {
    if command -v jq >/dev/null 2>&1; then jq "$@"; else cat; fi
}

ENV_ARGS=(--env-file "$COMPOSE_ENV_FILE")
if [[ -f "$COMPOSE_ADMIN_ENV_FILE" ]]; then
    ENV_ARGS+=(--env-file "$COMPOSE_ADMIN_ENV_FILE")
fi

compose() {
    docker compose "${PROJECT_ARGS[@]}" "${ENV_ARGS[@]}" -f "$COMPOSE_FILE" "$@"
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

# --- Secrets ----------------------------------------------------------------

section "secrets"

# check_secret FILE KEY: FAIL when KEY is empty or a change-me placeholder.
check_secret() {
    local value
    value="$(env_value "$1" "$2")"
    if [[ -z "${value//[[:space:]]/}" ]]; then
        bad "$2 is empty or unset in $1"
    elif [[ "$value" == change-me* ]]; then
        bad "$2 is still a change-me placeholder in $1"
    else
        ok "$2 is set"
    fi
}

if [[ -f "$COMPOSE_ENV_FILE" ]]; then
    for key in DB_PASSWORD S3_ACCESS_KEY S3_SECRET_KEY PAGING_CURSOR_KEY; do
        check_secret "$COMPOSE_ENV_FILE" "$key"
    done
    if [[ "$STORAGE_SERVICE" == seaweedfs ]]; then
        check_secret "$COMPOSE_ENV_FILE" SEAWEEDFS_JWT_KEY
    fi
else
    warn "runtime secrets not checked: no $COMPOSE_ENV_FILE"
fi
if [[ -f "$COMPOSE_ADMIN_ENV_FILE" ]]; then
    for key in DB_ADMIN_PASSWORD DB_OWNER_PASSWORD; do
        check_secret "$COMPOSE_ADMIN_ENV_FILE" "$key"
    done
else
    warn "operator secrets not checked: no $COMPOSE_ADMIN_ENV_FILE (set COMPOSE_ADMIN_ENV_FILE)"
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
        # tckdb-api and frontend are checked only when running (selfhost profile).
        for svc in db "$STORAGE_SERVICE" tckdb-api frontend; do
            row="$(echo "$ps_output" | jq -c --arg s "$svc" 'select(.Service == $s)' 2>/dev/null || true)"
            if [[ -z "$row" ]]; then
                case "$svc" in
                    tckdb-api|frontend) ;;
                    *) bad "$svc service not listed in 'docker compose ps'" ;;
                esac
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
        for svc in db "$STORAGE_SERVICE"; do
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

# In the API container when compose runs one (selfhost profile), else on the
# host. The container holds only the runtime login, which can read the
# revision; DEPLOYMENT_MODE=local lets Alembic use it for this read.
alembic_out=""
alembic_where=""
if [[ -n "$(compose ps -q tckdb-api 2>/dev/null)" ]]; then
    alembic_where="tckdb-api container"
    alembic_out="$(compose exec -T -e DEPLOYMENT_MODE=local tckdb-api /opt/conda/bin/alembic current 2>&1)"
elif command -v conda >/dev/null 2>&1; then
    alembic_where="host, conda env tckdb_env"
    # Run from backend/ so alembic.ini is found.
    alembic_out="$(cd backend 2>/dev/null && conda run -n tckdb_env alembic current 2>&1)"
else
    warn "no tckdb-api container and conda not on PATH; skipping alembic revision check"
fi
if [[ -n "$alembic_where" ]]; then
    head_line="$(grep -E '^[0-9a-f]{12} \(head\)$' <<<"$alembic_out" || true)"
    if [[ -n "$head_line" && "$(wc -l <<<"$head_line")" -eq 1 ]]; then
        ok "alembic current ($alembic_where): $head_line"
    else
        bad "alembic current ($alembic_where) is not one revision at head; output:"
        echo "$alembic_out" >&2
    fi
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
