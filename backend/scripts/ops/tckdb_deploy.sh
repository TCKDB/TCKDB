#!/usr/bin/env bash
# Deploy a published TCKDB API image to this host.
#
# WHAT IT DOES, IN ORDER, AND WHY THE ORDER MATTERS
#   1. Resolve the image and refuse to deploy a moving tag.
#   2. Back up the database.
#   3. Pull the image (before stopping anything, so a failed pull is a no-op).
#   4. Run migrations from the NEW image, against the running database.
#   5. Restart the API onto the new image.
#   6. Verify /api/v1/status, and say plainly how to roll back if it is not ok.
#
#   Migrations run BEFORE the new API starts. The reverse order would leave new
#   code talking to an old schema, which is the one arrangement that can
#   produce wrong answers rather than an obvious failure.
#
# WHAT CANNOT BE LOST
#   The database is a separate container with its own named volume; this image
#   is stateless. Replacing it cannot touch the data. The step that touches
#   data is the migration, which is why a backup is taken immediately before,
#   every time, regardless of whether a migration turns out to be pending.
#
# PINNING
#   Deploy an immutable tag -- `sha-<commit>` or a release such as `0.3.0`.
#   `latest` moves, so a host running it cannot answer "which code is this?",
#   and two hosts running it may differ. Passing `latest` requires
#   --allow-mutable and says so.
#
# USAGE
#   tckdb_deploy.sh sha-<full-commit>       # deploy a pinned build
#   tckdb_deploy.sh 0.3.0                   # deploy a release
#   tckdb_deploy.sh --check                 # report what is running vs available
#
#   This script is for an API container it manages itself, beside a compose
#   `db`. A stack run with docker-compose.yml's `selfhost` profile upgrades
#   with `TCKDB_IMAGE_TAG=<tag> docker compose ... --profile selfhost up -d`
#   instead; running both collides on the `tckdb-api` name and port 8010.
#
# WHICH DATABASE
#   TCKDB_DB_CONTAINER / TCKDB_DB_NETWORK name the compose project's `db`
#   container and the network the API joins. Unset, they are found in order:
#   COMPOSE_PROJECT_NAME (its `db` service and `<project>_default`); the
#   container `tckdbv2-db-1` if it exists; `docker compose ps -q db` run here;
#   else `tckdbv2-db-1` / `tckdbv2_default`.
#   The backup runs pg_dump as DB_ADMIN_USER (TCKDB_MIGRATION_ENV_FILE, else
#   TCKDB_ENV_FILE, else the db container's POSTGRES_USER) on DB_NAME
#   (TCKDB_ENV_FILE, else the container's POSTGRES_DB), each else `tckdb`.
#
# EXTRA NETWORKS
#   TCKDB_EXTRA_NETWORKS (optional, space- or comma-separated) names Docker
#   networks the API container joins besides TCKDB_DB_NETWORK. The compose
#   file keeps the object store on its own `storage` network, so an
#   API that uses the bundled SeaweedFS needs e.g.
#       TCKDB_EXTRA_NETWORKS=tckdbv2_storage
#   Unset (the default) changes nothing. Set, each value is checked before
#   anything changes (it exists; it is not the DB network, `host`, `none`
#   or a repeat), and the new container is created, connected and only then
#   started, replacing the running one at the last moment. A connect that
#   still fails leaves the running API as it was.
#
# MIGRATION CREDENTIALS
#   TCKDB_MIGRATION_ENV_FILE (optional) is a second env file passed ONLY to the
#   `alembic upgrade` run, after TCKDB_ENV_FILE, so its values win there. It is
#   where DB_OWNER_USER / DB_OWNER_PASSWORD belong once the database has
#   separate owner and runtime roles (backend/docs/deployment/database_roles.md):
#   Alembic needs the owner, and the API must never hold it. Unset (the
#   default) changes nothing. Set, it must be a readable file, checked before
#   anything changes. Owner or admin credentials found in TCKDB_ENV_FILE are
#   reported, because that file is also the API's.
set -uo pipefail

IMAGE_REPO="${TCKDB_IMAGE_REPO:-laxzal/tckdb-api}"
CONTAINER="${TCKDB_CONTAINER:-tckdb-api}"
API_PORT="${TCKDB_API_PORT:-8010}"
DEFAULT_DB_CONTAINER="tckdbv2-db-1"
DEFAULT_DB_NETWORK="tckdbv2_default"
DB_CONTAINER="${TCKDB_DB_CONTAINER:-}"
DB_NETWORK="${TCKDB_DB_NETWORK:-}"
# No default for either path: this script is public, and a
# path under one operator's home directory would mean every copy of it
# defaults to that operator's environment file and backup location.
ENV_FILE="${TCKDB_ENV_FILE:-}"
MIGRATION_ENV_FILE="${TCKDB_MIGRATION_ENV_FILE:-}"
BACKUP_DIR="${TCKDB_BACKUP_DIR:-}"
STATUS_URL="${TCKDB_LOCAL_STATUS_URL:-http://127.0.0.1:${API_PORT}/api/v1/status}"
# Word-split on spaces and commas; empty means none.
EXTRA_NETWORKS_RAW="${TCKDB_EXTRA_NETWORKS:-}"
read -r -a EXTRA_NETWORKS <<<"${EXTRA_NETWORKS_RAW//,/ }"

die() { echo "error: $*" >&2; exit 1; }

running_image() {
    # `docker inspect` prints an empty line for a missing container, so test
    # the output, not only the exit code.
    local image
    image="$(docker inspect --type container "$CONTAINER" --format '{{.Config.Image}}' 2>/dev/null)"
    echo "${image:-(not running as a container)}"
}

if [[ "${1:-}" == "--check" ]]; then
    # Read-only, and touches neither the env file nor the backup dir, so it
    # does not require either to be set.
    check_body="$(curl -s "$STATUS_URL" 2>/dev/null)"
    echo "container:      $(running_image)"
    networks="$(docker inspect --type container "$CONTAINER" --format '{{range $k, $_ := .NetworkSettings.Networks}}{{$k}} {{end}}' 2>/dev/null)"
    echo "networks:       ${networks:-n/a}"
    # is-active prints a state even when it exits non-zero; n/a only when it prints nothing.
    systemd_state="$(systemctl is-active tckdb-api.service 2>/dev/null)"
    echo "systemd uvicorn: ${systemd_state:-n/a}"
    echo "live revision:  $(grep -o '"alembic_revision":"[^"]*"' <<<"$check_body" || echo unknown)"
    if command -v jq >/dev/null 2>&1; then
        # The object store is a hard dependency that fails independently of
        # the database, and its address is the thing most likely to be wrong
        # after a host-to-container move. Show it here so "what is running?"
        # answers that too.
        echo "status:         $(jq -r '"\(.status) degraded=[\((.degraded // []) | join(","))]"' <<<"$check_body" 2>/dev/null || echo unknown)"
        echo "artifact store: $(jq -r '.components.artifact_storage | "\(.endpoint) bucket=\(.bucket) healthy=\(.healthy) \(.reason // "")"' <<<"$check_body" 2>/dev/null || echo unknown)"
    fi
    exit 0
fi

[[ -z "$ENV_FILE" ]] && die "TCKDB_ENV_FILE is not set (path to this host's env file, e.g. .env.pi)"
[[ -z "$BACKUP_DIR" ]] && die "TCKDB_BACKUP_DIR is not set (directory to write pre-deploy backups to)"

TAG="${1:-}"
[[ -z "$TAG" ]] && die "usage: $0 <tag>|--check   (tag e.g. sha-<commit> or 0.3.0)"

if [[ "$TAG" == "latest" && "${2:-}" != "--allow-mutable" ]]; then
    die "'latest' moves, so the deployed version would be unanswerable. Pass an immutable tag (sha-<commit> or 0.3.0), or re-run with: $0 latest --allow-mutable"
fi

# The compose project's `db` container and default network (see WHICH DATABASE).
compose_project_of() {
    docker inspect --type container "$1" --format '{{index .Config.Labels "com.docker.compose.project"}}' 2>/dev/null
}
if [[ -z "$DB_CONTAINER" ]]; then
    if [[ -n "${COMPOSE_PROJECT_NAME:-}" ]]; then
        DB_CONTAINER="$(docker ps -q --filter "label=com.docker.compose.project=${COMPOSE_PROJECT_NAME}" \
            --filter "label=com.docker.compose.service=db" 2>/dev/null | head -1)"
        [[ -n "$DB_CONTAINER" ]] \
            || die "no running db container in compose project '${COMPOSE_PROJECT_NAME}'; nothing has been changed"
    elif docker inspect --type container "$DEFAULT_DB_CONTAINER" >/dev/null 2>&1; then
        DB_CONTAINER="$DEFAULT_DB_CONTAINER"
    else
        DB_CONTAINER="$(docker compose ps -q db 2>/dev/null | head -1)"
        DB_CONTAINER="${DB_CONTAINER:-$DEFAULT_DB_CONTAINER}"
    fi
fi
if [[ -z "$DB_NETWORK" ]]; then
    if [[ -n "${COMPOSE_PROJECT_NAME:-}" ]]; then
        DB_NETWORK="${COMPOSE_PROJECT_NAME}_default"
    elif [[ "$DB_CONTAINER" == "$DEFAULT_DB_CONTAINER" ]]; then
        DB_NETWORK="$DEFAULT_DB_NETWORK"
    else
        db_project="$(compose_project_of "$DB_CONTAINER")"
        DB_NETWORK="${db_project:+${db_project}_default}"
        DB_NETWORK="${DB_NETWORK:-$DEFAULT_DB_NETWORK}"
    fi
fi

# env_value FILE KEY: the last `KEY=value` in FILE without surrounding quotes.
env_value() {
    [[ -n "$1" && -f "$1" ]] || return 0
    sed -n "s/^[[:space:]]*\(export[[:space:]]\{1,\}\)\{0,1\}$2=//p" "$1" | tail -1 \
        | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/"
}
# container_env CONTAINER KEY: KEY from the container's configured environment.
# The admin is usually in neither env file (the migration file is owner-only),
# but the postgres image was created with it as POSTGRES_USER.
container_env() {
    docker inspect --type container "$1" --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null \
        | sed -n "s/^$2=//p" | head -1
}
BACKUP_DB_USER="$(env_value "$MIGRATION_ENV_FILE" DB_ADMIN_USER)"
BACKUP_DB_USER="${BACKUP_DB_USER:-$(env_value "$ENV_FILE" DB_ADMIN_USER)}"
BACKUP_DB_USER="${BACKUP_DB_USER:-$(container_env "$DB_CONTAINER" POSTGRES_USER)}"
BACKUP_DB_USER="${BACKUP_DB_USER:-tckdb}"
BACKUP_DB_NAME="$(env_value "$ENV_FILE" DB_NAME)"
BACKUP_DB_NAME="${BACKUP_DB_NAME:-$(container_env "$DB_CONTAINER" POSTGRES_DB)}"
BACKUP_DB_NAME="${BACKUP_DB_NAME:-tckdb}"

IMAGE="${IMAGE_REPO}:${TAG}"
echo "==> deploying ${IMAGE}"

MIGRATION_ENV_ARGS=()
if [[ -n "$MIGRATION_ENV_FILE" ]]; then
    [[ -f "$MIGRATION_ENV_FILE" && -r "$MIGRATION_ENV_FILE" ]] \
        || die "TCKDB_MIGRATION_ENV_FILE '${MIGRATION_ENV_FILE}' is not a readable file; nothing has been changed"
    MIGRATION_ENV_ARGS=(--env-file "$MIGRATION_ENV_FILE")
fi
# The API container reads TCKDB_ENV_FILE, so a role that can alter the schema
# or disable triggers must not be in it. Reported rather than refused: a
# deployment without separated roles has nowhere else to put them yet.
# A bare `DB_OWNER_PASSWORD` line (no `=`) counts too: Docker then copies the
# value from the deploying shell, which may have sourced the operator file.
if grep -Eq '^[[:space:]]*(export[[:space:]]+)?DB_(OWNER|ADMIN)_PASSWORD([[:space:]]*=|[[:space:]]*$)' "$ENV_FILE" 2>/dev/null; then
    echo "warning: ${ENV_FILE} holds DB_OWNER_PASSWORD or DB_ADMIN_PASSWORD, and the API container reads that file; move them to TCKDB_MIGRATION_ENV_FILE (owner) or an operator-only file (admin)" >&2
fi

# Before anything changes: every network the new container must join is one
# it can join. Each value refused here would otherwise fail `docker network
# connect` after the backup and the migration (Docker refuses a network the
# container is already on, and `host` / `none`).
seen_networks=" $DB_NETWORK "
for net in "$DB_NETWORK" "${EXTRA_NETWORKS[@]}"; do
    docker network inspect "$net" >/dev/null 2>&1 \
        || die "Docker network '${net}' does not exist; nothing has been changed (check TCKDB_DB_NETWORK / TCKDB_EXTRA_NETWORKS)"
done
for net in "${EXTRA_NETWORKS[@]}"; do
    case "$net" in
        host|none)
            die "TCKDB_EXTRA_NETWORKS names '${net}', which cannot be joined as an extra network; nothing has been changed" ;;
    esac
    if [[ "$net" == "$DB_NETWORK" ]]; then
        die "TCKDB_EXTRA_NETWORKS names '${net}', which is already the API's network (TCKDB_DB_NETWORK), so remove it; nothing has been changed"
    fi
    if [[ "$seen_networks" == *" $net "* ]]; then
        die "TCKDB_EXTRA_NETWORKS names '${net}' more than once; nothing has been changed"
    fi
    seen_networks+="$net "
done

# 1. Back up first. Cheap, and the only step that makes the rest reversible.
mkdir -p "$BACKUP_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="${BACKUP_DIR}/tckdb-predeploy-${STAMP}.dump"
docker exec "$DB_CONTAINER" pg_dump -U "$BACKUP_DB_USER" -d "$BACKUP_DB_NAME" -Fc > "$BACKUP" \
    || die "backup of database '${BACKUP_DB_NAME}' as '${BACKUP_DB_USER}' in ${DB_CONTAINER} failed; refusing to deploy"
echo "    backup: $BACKUP ($(du -h "$BACKUP" | cut -f1))"

# 2. Pull before stopping anything: a failed pull should change nothing.
docker pull --quiet "$IMAGE" >/dev/null || die "could not pull ${IMAGE}"
DIGEST="$(docker inspect "$IMAGE" --format '{{index .RepoDigests 0}}' 2>/dev/null || echo unknown)"
echo "    pulled: $DIGEST"

# 3. Migrate with the NEW image while the OLD API is still serving. Read
#    traffic keeps working throughout; only writes touching changed tables are
#    at risk, and this deployment's migrations refuse rather than guess when
#    they meet data they cannot classify.
echo "==> migrating"
docker run --rm --network "$DB_NETWORK" --env-file "$ENV_FILE" "${MIGRATION_ENV_ARGS[@]}" \
    -e DB_HOST=db -e DB_PORT=5432 \
    "$IMAGE" alembic upgrade head || die "migration failed; the old API is still running and the backup is at ${BACKUP}"

# 4. Swap the API.
echo "==> restarting API"
if systemctl is-active --quiet tckdb-api.service 2>/dev/null; then
    echo "    stopping the systemd uvicorn unit (pre-container deployment)"
    sudo systemctl stop tckdb-api.service
fi
CONTAINER_ARGS=(
    --network "$DB_NETWORK"
    --env-file "$ENV_FILE"
    -e DB_HOST=db -e DB_PORT=5432
    -e TCKDB_INLINE_WORKER=true
    -p "127.0.0.1:${API_PORT}:8010"
    --restart unless-stopped
)
if [[ ${#EXTRA_NETWORKS[@]} -eq 0 ]]; then
    # Unchanged from before TCKDB_EXTRA_NETWORKS existed.
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
    docker run -d --name "$CONTAINER" "${CONTAINER_ARGS[@]}" \
        "$IMAGE" >/dev/null || die "could not start the new container; roll back with: $0 <previous-tag>"
else
    # Created beside the running API, wired to every network, and only then
    # swapped in and started: it never runs cut off from the object store,
    # and a network that will not connect costs nothing but this container.
    NEXT="${CONTAINER}-next"
    docker rm -f "$NEXT" >/dev/null 2>&1 || true
    docker create --name "$NEXT" "${CONTAINER_ARGS[@]}" "$IMAGE" >/dev/null \
        || die "could not create the new container; the old API is still running"
    for net in "${EXTRA_NETWORKS[@]}"; do
        if ! docker network connect "$net" "$NEXT"; then
            docker rm -f "$NEXT" >/dev/null 2>&1 || true
            die "could not connect the new container to network '${net}'. The old API is still running and unchanged. Fix TCKDB_EXTRA_NETWORKS (or the network) and re-run; or deploy without it and join by hand: docker network connect ${net} ${CONTAINER}"
        fi
        echo "    joined network: ${net}"
    done
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
    docker rename "$NEXT" "$CONTAINER" \
        || die "could not rename ${NEXT} to ${CONTAINER}; start it by hand: docker rename ${NEXT} ${CONTAINER} && docker start ${CONTAINER}"
    docker start "$CONTAINER" >/dev/null || die "could not start the new container; roll back with: $0 <previous-tag>"
fi

# 5. Verify, and be specific about what to do if it is wrong.
#
#    WHAT "VERIFIED" HAS TO MEAN
#      Both `status` and `degraded` are checked, not just `status`. They are
#      derived from the same set today, so a divergence would be a bug rather
#      than a state -- which is the point: if that derivation ever changes, a
#      deploy that ships a degraded component must fail here rather than pass
#      on a field that happened to stay "ok". The old check was a substring
#      match for `"status":"ok"` anywhere in the body, which does not even
#      distinguish the top-level field from one nested inside a component.
#
#      This only catches what /status covers. /status probes the database,
#      the worker and artifact storage, so a deployment whose object-store
#      endpoint is unreachable is not reported ok here.
live_status() {
    if command -v jq >/dev/null 2>&1; then
        jq -r '.status // ""' <<<"$1" 2>/dev/null
    else
        # `status` is the first top-level key FastAPI emits, so the first
        # match is the top-level one and not a component's field.
        grep -o '"status"[[:space:]]*:[[:space:]]*"[^"]*"' <<<"$1" | head -1 | sed 's/.*"\([^"]*\)"$/\1/'
    fi
}

live_degraded() {
    # Comma-separated component names, empty when nothing is degraded.
    if command -v jq >/dev/null 2>&1; then
        jq -r '(.degraded // []) | join(", ")' <<<"$1" 2>/dev/null
    else
        # `degraded` is an ARRAY, so a string-shaped grep would silently
        # return empty for every value including a non-empty one -- i.e. it
        # would report a degraded deployment as clean. Match the array.
        sed -n 's/.*"degraded"[[:space:]]*:[[:space:]]*\[\([^]]*\)\].*/\1/p' <<<"$1" | head -1 | tr -d '"'
    fi
}

echo "==> verifying"
for _ in $(seq 1 45); do
    body="$(curl -s "$STATUS_URL" 2>/dev/null)"
    if [[ -n "$body" ]]; then
        reported_status="$(live_status "$body")"
        reported_degraded="$(live_degraded "$body")"
        if [[ "$reported_status" == "ok" && -z "$reported_degraded" ]]; then
            echo "    $body"
            echo "==> deployed ${IMAGE}"
            exit 0
        fi
    fi
    sleep 2
done

echo "    last response: ${body:-<none>}" >&2
if [[ -n "${reported_degraded:-}" ]]; then
    echo "    degraded components: ${reported_degraded}" >&2
    if command -v jq >/dev/null 2>&1; then
        jq -r '[.components // {} | to_entries[] | select(.value.healthy == false) | "    \(.key): \(.value.reason // "unhealthy")"] | .[]' <<<"$body" >&2
    fi
fi
cat >&2 <<EOF
error: the new container did not report status=ok with nothing degraded.

  The container may well be running -- "degraded" means it answered and
  reported a broken component, which is a different problem from a container
  that never came up. Read the component reasons above first; an
  artifact_storage failure is usually the object-store endpoint in the env
  file, not the image.

  logs:      docker logs ${CONTAINER} | tail -50
  status:    curl -s ${STATUS_URL}
  roll back: $0 <previous-tag>
  restore:   the pre-deploy backup is at ${BACKUP}
             (only needed if a migration is the problem -- the image itself
              holds no data)
EOF
exit 1
