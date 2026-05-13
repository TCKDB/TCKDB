#!/usr/bin/env bash
# tckdb_doctor.sh — first-run / "why won't it start?" diagnostic.
#
# Runs a sequence of checks against a TCKDB stack and prints
# actionable hints when a check fails. Defaults target local
# development (canonical docker-compose.yml, tckdb_dev, port 8010), but
# every input is overridable so the same tool works against a
# self-hosted recipe.
#
# Run from the repo root:
#   backend/scripts/tckdb_doctor.sh
#
# Overridable inputs (all optional):
#   COMPOSE_FILE      docker compose file (default: docker-compose.yml)
#   COMPOSE_ENV_FILE  env file for docker compose, if any (default: unset)
#   TCKDB_ENV_FILE    backend env file to validate (default: backend/.env)
#   TCKDB_BASE_URL    API base URL to probe (default: http://127.0.0.1:8010/api/v1)
#   DB_NAME           DB to inspect (default: tckdb_dev)
#   DB_USER           DB user (default: tckdb)
#   DB_HOST_PORT      host-published port of the db container (default: 5432)
#   CONDA_ENV         conda env for alembic (default: tckdb_env)
#
# Exit codes:
#   0   all checks passed
#   1   one or more checks failed (see stderr for hints)
#   2   usage error

case "${1:-}" in
    -h|--help|help)
        sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'
        exit 0
        ;;
esac

set -uo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
COMPOSE_ENV_FILE="${COMPOSE_ENV_FILE:-}"
TCKDB_ENV_FILE="${TCKDB_ENV_FILE:-backend/.env}"
TCKDB_BASE_URL="${TCKDB_BASE_URL:-http://127.0.0.1:8010/api/v1}"
DB_NAME="${DB_NAME:-tckdb_dev}"
DB_USER="${DB_USER:-tckdb}"
DB_HOST_PORT="${DB_HOST_PORT:-5432}"
CONDA_ENV="${CONDA_ENV:-tckdb_env}"

fail_count=0
warn_count=0
ok()   { printf "  \033[32mOK\033[0m   %s\n" "$*"; }
bad()  { printf "  \033[31mFAIL\033[0m %s\n" "$*" >&2; fail_count=$((fail_count + 1)); }
warn() { printf "  \033[33mWARN\033[0m %s\n" "$*";  warn_count=$((warn_count + 1)); }
hint() { printf "         \033[36m→\033[0m %s\n" "$*" >&2; }
section() { printf "\n== %s ==\n" "$*"; }

# Build the docker-compose invocation once. COMPOSE_ENV_FILE is
# optional: docker compose falls back to ambient env when unset, which
# is what we want for the no-env-file local dev case.
compose() {
    if [[ -n "$COMPOSE_ENV_FILE" ]]; then
        docker compose --env-file "$COMPOSE_ENV_FILE" -f "$COMPOSE_FILE" "$@"
    else
        docker compose -f "$COMPOSE_FILE" "$@"
    fi
}

# --- 1. Tooling -------------------------------------------------------------

section "1. tooling"

if ! command -v docker >/dev/null 2>&1; then
    bad "docker not on PATH"
    hint "install Docker Engine or Docker Desktop and re-run."
else
    ok "docker found ($(docker --version 2>/dev/null | head -1))"
fi

if ! docker compose version >/dev/null 2>&1; then
    bad "'docker compose' subcommand missing"
    hint "you may have the old standalone 'docker-compose'. install the v2 plugin."
else
    ok "docker compose v2 available"
fi

if ! command -v curl >/dev/null 2>&1; then
    bad "curl not on PATH"
    hint "install curl — the doctor and most TCKDB scripts use it for HTTP probing."
else
    ok "curl found"
fi

if ! command -v jq >/dev/null 2>&1; then
    warn "jq not on PATH (optional but recommended for readable JSON in scripts)"
fi

# --- 2. Repo state and env file --------------------------------------------

section "2. repo / env file"

if [[ ! -f "$COMPOSE_FILE" ]]; then
    bad "compose file '$COMPOSE_FILE' not found"
    hint "run the doctor from the repo root, or set COMPOSE_FILE=<path>."
else
    ok "compose file '$COMPOSE_FILE' present"
fi

if [[ ! -f "$TCKDB_ENV_FILE" ]]; then
    warn "env file '$TCKDB_ENV_FILE' not found"
    hint "for local dev: cp backend/.env.example backend/.env"
else
    ok "env file '$TCKDB_ENV_FILE' present"
fi

# Sanity-check the env file for the host/container port confusion that
# bites new users. We only WARN; we cannot know whether the user is
# running the API on the host or inside compose.
env_db_host=""
env_db_port=""
env_api_port=""
if [[ -f "$TCKDB_ENV_FILE" ]]; then
    env_db_host="$(grep -E '^DB_HOST=' "$TCKDB_ENV_FILE" | tail -1 | cut -d= -f2- | tr -d '"')"
    env_db_port="$(grep -E '^DB_PORT=' "$TCKDB_ENV_FILE" | tail -1 | cut -d= -f2- | tr -d '"')"
    env_api_port="$(grep -E '^TCKDB_API_PORT=' "$TCKDB_ENV_FILE" | tail -1 | cut -d= -f2- | tr -d '"')"
    if [[ "$env_db_host" == "db" ]]; then
        ok "DB_HOST=db (API expected to run inside compose network)"
    elif [[ "$env_db_host" == "127.0.0.1" || "$env_db_host" == "localhost" ]]; then
        ok "DB_HOST=$env_db_host (API expected to run on the host, via loopback)"
    elif [[ -n "$env_db_host" ]]; then
        warn "DB_HOST=$env_db_host — unusual; double-check before debugging further"
    fi
fi

# --- 2b. Port mapping coherence -------------------------------------------
#
# Catches the two recurring port-confusion bugs:
#   (a) host-run API/Alembic uses DB_PORT that does not match the
#       host-published port shown by `docker compose ps`.
#   (b) env file or shell still has TCKDB_API_PORT=8000 left over from
#       older docs — the canonical local port is 8010.

section "2b. port mapping"

cat <<'EOF' | sed 's/^/  /'
Reminder — the two correct configurations for talking to Postgres:

  Host-run Alembic/Uvicorn (the default in local dev):
    DB_HOST=127.0.0.1
    DB_PORT=<host-published port>   # e.g. 5432, or 5434 if remapped
                                    # to avoid a host-installed Postgres

  API inside the compose network (future containerized API):
    DB_HOST=db                      # the compose service name
    DB_PORT=5432                    # the container port (does not move)
EOF

# Pull the actually-published host port for the db service. This works
# for the common `127.0.0.1:<host>:5432` style entry. We are happy with
# best-effort parsing — a WARN here is fine.
published_db_port=""
if pub_out="$(compose ps --format json 2>/dev/null)" && command -v jq >/dev/null 2>&1; then
    published_db_port="$(echo "$pub_out" \
        | jq -r 'select(.Service=="db") | .Publishers // [] | .[] | select(.TargetPort==5432) | .PublishedPort' \
        2>/dev/null | head -1)"
fi
if [[ -n "$published_db_port" ]]; then
    ok "db container publishes 127.0.0.1:$published_db_port -> container 5432"
    if [[ -n "$env_db_port" && "$env_db_host" != "db" && "$env_db_port" != "$published_db_port" ]]; then
        bad "env DB_PORT=$env_db_port but db is actually published on host port $published_db_port"
        hint "host-side Alembic/Uvicorn won't reach the DB until you either:"
        hint "  set DB_PORT=$published_db_port in $TCKDB_ENV_FILE, or"
        hint "  remap the container in the compose file to publish on $env_db_port."
    elif [[ "$DB_HOST_PORT" != "$published_db_port" ]]; then
        warn "DB_HOST_PORT=$DB_HOST_PORT passed to the doctor, but db is published on $published_db_port"
        hint "re-run the doctor with: DB_HOST_PORT=$published_db_port $0"
    fi
else
    warn "could not detect the host-published db port (compose ps unavailable, or db not running)"
    hint "expected mapping shape: \"127.0.0.1:<host>:5432\""
fi

# API port: warn if env file (or shell) still uses the old default 8000
# while the canonical local port is 8010.
if [[ -n "$env_api_port" && "$env_api_port" == "8000" ]]; then
    warn "TCKDB_API_PORT=8000 in $TCKDB_ENV_FILE — the canonical local/host-run API port is now 8010"
    hint "update the env file, or override TCKDB_BASE_URL when launching tools."
fi
expected_api_port="$(echo "$TCKDB_BASE_URL" | sed -nE 's|^https?://[^/:]+:([0-9]+)/.*|\1|p')"
if [[ -n "$expected_api_port" && -n "$env_api_port" && "$expected_api_port" != "$env_api_port" ]]; then
    warn "doctor expects API on port $expected_api_port (from TCKDB_BASE_URL) but env TCKDB_API_PORT=$env_api_port"
    hint "the two must match or you'll be probing the wrong port."
fi

# --- 3. Compose service health ---------------------------------------------

section "3. compose services"

ps_output=""
if ! ps_output="$(compose ps --format json 2>&1)"; then
    bad "docker compose ps failed:"
    echo "$ps_output" >&2
    hint "is Docker running? on Linux: sudo systemctl status docker"
else
    if command -v jq >/dev/null 2>&1; then
        for svc in db minio; do
            row="$(echo "$ps_output" | jq -c --arg s "$svc" 'select(.Service == $s)' 2>/dev/null || true)"
            if [[ -z "$row" ]]; then
                bad "$svc service not listed in 'docker compose ps'"
                hint "start it: docker compose -f $COMPOSE_FILE up -d $svc"
                continue
            fi
            state="$(echo "$row" | jq -r '.State // empty')"
            health="$(echo "$row" | jq -r '.Health // empty')"
            if [[ "$state" == "running" && ( -z "$health" || "$health" == "healthy" ) ]]; then
                ok "$svc is running${health:+ (health=$health)}"
            else
                bad "$svc is not healthy (state=$state health=${health:-<none>})"
                hint "logs: docker compose -f $COMPOSE_FILE logs --tail=50 $svc"
            fi
        done
    else
        for svc in db minio; do
            if echo "$ps_output" | grep -q "\"Service\":\"$svc\""; then
                ok "$svc listed (install jq for health detail)"
            else
                bad "$svc not in compose ps output"
                hint "start it: docker compose -f $COMPOSE_FILE up -d $svc"
            fi
        done
    fi
fi

# --- 4. Database reachable + RDKit installed ------------------------------

section "4. postgres + RDKit"

rdkit_out=""
if rdkit_out="$(compose exec -T db psql -U "$DB_USER" -d "$DB_NAME" -tA \
        -c "select extname from pg_extension where extname = 'rdkit';" 2>&1)"; then
    if [[ "$rdkit_out" == *"rdkit"* ]]; then
        ok "rdkit extension present in $DB_NAME"
    elif echo "$rdkit_out" | grep -qi "does not exist"; then
        bad "database $DB_NAME does not exist"
        hint "create + migrate: docker compose -f $COMPOSE_FILE up -d db && make migrate"
        hint "or (one-off):    docker compose -f $COMPOSE_FILE exec db createdb -U $DB_USER $DB_NAME"
    else
        bad "rdkit extension NOT present in $DB_NAME"
        echo "         psql output: $rdkit_out" >&2
        hint "the rdkit-cartridge image installs it automatically on first start —"
        hint "did the volume get mounted from an older Postgres image? try:"
        hint "  docker compose -f $COMPOSE_FILE down -v && docker compose -f $COMPOSE_FILE up -d"
    fi
else
    bad "psql query failed"
    echo "         output: $rdkit_out" >&2
    hint "is the db container running? see section 3 above."
fi

# --- 5. Alembic revision ---------------------------------------------------

section "5. alembic revision"

if command -v conda >/dev/null 2>&1; then
    alembic_out="$(cd backend 2>/dev/null && \
        DB_NAME="$DB_NAME" DB_USER="$DB_USER" DB_HOST="${env_db_host:-127.0.0.1}" \
        DB_PORT="$DB_HOST_PORT" \
        conda run -n "$CONDA_ENV" alembic current 2>&1 || true)"
    if [[ -n "$alembic_out" ]]; then
        head_line="$(echo "$alembic_out" | grep -E "[0-9a-f]{8,}" | head -1 || true)"
        if [[ -n "$head_line" ]]; then
            ok "alembic current: $head_line"
        else
            warn "alembic current returned no revision id"
            echo "$alembic_out" | sed 's/^/         /'
            hint "if the DB is empty, run: make migrate"
        fi
    else
        warn "could not run 'alembic current' (conda env '$CONDA_ENV' missing?)"
        hint "create the env: conda env create -n $CONDA_ENV -f backend/environment.yml"
    fi
else
    warn "conda not on PATH; skipping alembic revision check"
fi

# --- 6. API surface --------------------------------------------------------

section "6. API: $TCKDB_BASE_URL"

health_body=""
if health_body="$(curl -fsS "$TCKDB_BASE_URL/health" 2>&1)"; then
    if echo "$health_body" | grep -q '"status"[[:space:]]*:[[:space:]]*"ok"'; then
        ok "GET /health -> {\"status\":\"ok\"}"
    else
        bad "GET /health did not return status=ok"
        echo "         body: $health_body" >&2
    fi
else
    bad "GET /health failed: $health_body"
    hint "is the API running? start it with:"
    hint "  make api      (foreground, port 8010)"
    hint "or directly:   conda run -n $CONDA_ENV uvicorn main:app --host 127.0.0.1 --port 8010"
    hint "the command must run from backend/ so 'main.py' is importable."

    # If the user is on the canonical 8010 but actually launched on the
    # legacy 8000 default, probe and tell them. Saves a round of
    # confused debugging.
    if [[ "$TCKDB_BASE_URL" == *":8010/"* ]] && \
       legacy_body="$(curl -fsS "${TCKDB_BASE_URL//:8010\//:8000\/}/health" 2>/dev/null)" && \
       echo "$legacy_body" | grep -q '"status"[[:space:]]*:[[:space:]]*"ok"'; then
        warn "however, an API IS responding on port 8000 (the legacy default)."
        hint "stop it and restart on 8010, or override TCKDB_BASE_URL to point at 8000."
    fi
fi

# Anonymous scientific read — uses water, which seeds out of the box
# only after the demo data script has been run. A 200 means the path
# works; an empty result set is still success.
search_status=""
search_body=""
if search_body="$(curl -sS -w '\n%{http_code}' \
        "$TCKDB_BASE_URL/scientific/species/search?smiles=O" 2>/dev/null)"; then
    search_status="$(echo "$search_body" | tail -n1)"
    if [[ "$search_status" == "200" ]]; then
        ok "GET /scientific/species/search?smiles=O -> 200 (anonymous read works)"
    else
        bad "GET /scientific/species/search?smiles=O -> $search_status (expected 200)"
        echo "         body: $(echo "$search_body" | head -n-1)" >&2
    fi
fi

# --- summary ---------------------------------------------------------------

section "summary"
if (( fail_count == 0 && warn_count == 0 )); then
    echo "  all checks passed."
    exit 0
elif (( fail_count == 0 )); then
    echo "  $warn_count warning(s); core stack looks healthy."
    exit 0
else
    echo "  $fail_count failure(s), $warn_count warning(s)." >&2
    echo "  see hints above and re-run after fixing." >&2
    echo "  troubleshooting guide: docs/deployment/troubleshooting.md" >&2
    exit 1
fi
