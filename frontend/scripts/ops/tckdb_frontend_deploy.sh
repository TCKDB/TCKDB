#!/usr/bin/env bash
# Deploy a published TCKDB frontend image to a host that already runs the API.
#
# This script is deliberately separate from backend/scripts/ops/tckdb_deploy.sh:
# a static frontend has no migrations, database, or .env.pi dependency. It
# joins the API's Docker network and Nginx forwards /api/ internally to the
# existing tckdb-api container. The public reverse proxy should target the
# loopback port this script publishes (8011 by default), never the container
# directly.
#
# Usage:
#   tckdb_frontend_deploy.sh sha-<40-char-commit>
#   tckdb_frontend_deploy.sh --check
#
# Optional site config: a JSON file such as {"homeExampleRef": "spe_..."}, bind
# mounted read-only as /usr/share/nginx/html/site-config.json. The path is
# TCKDB_FRONTEND_SITE_CONFIG, else <repo checkout>/.site-config.json (gitignored)
# when that file exists, so a routine redeploy keeps the example. With neither,
# the site serves no such file and the home page shows no example record.
# The file must be a regular file readable by others (nginx runs as uid 101) and
# its homeExampleRef must be a spe_ or rxe_ ref. It is validated on the host
# before deploying and again as served after the health check; its contents are
# never printed. Edits to the file need a redeploy or `docker restart`.
#
# Only sha tags are accepted because they identify the exact frontend source
# revision. This avoids a UI deployment that cannot answer which bundle it
# serves. The script never reads or modifies .env.pi.
set -euo pipefail

IMAGE_REPO="${TCKDB_FRONTEND_IMAGE_REPO:-laxzal/tckdb-frontend}"
CONTAINER="${TCKDB_FRONTEND_CONTAINER:-tckdb-frontend}"
API_CONTAINER="${TCKDB_API_CONTAINER:-tckdb-api}"
NETWORK="${TCKDB_FRONTEND_NETWORK:-tckdbv2_default}"
PORT="${TCKDB_FRONTEND_PORT:-8011}"
LOCK_FILE="${TCKDB_FRONTEND_LOCK_FILE:-/tmp/tckdb-frontend-deploy.lock}"
# Health wait: probe about once a second for ~30 s while the container starts.
HEALTH_ATTEMPTS="${TCKDB_FRONTEND_HEALTH_ATTEMPTS:-30}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SITE_CONFIG="${TCKDB_FRONTEND_SITE_CONFIG:-}"
if [[ -z "$SITE_CONFIG" && -f "$REPO_ROOT/.site-config.json" ]]; then
    SITE_CONFIG="$REPO_ROOT/.site-config.json"
fi
HAS_PREVIOUS=false
PREVIOUS_CONTAINER=""
INCUMBENT_ID=""
DEPLOY_PHASE="preflight"

die() { echo "error: $*" >&2; exit 1; }

# Validate before anything mutates: a typo here must not cost a live outage.
[[ "$HEALTH_ATTEMPTS" =~ ^[1-9][0-9]{0,2}$ ]] \
    || die "TCKDB_FRONTEND_HEALTH_ATTEMPTS must be an integer 1-999"

# Reads a site config on stdin; succeeds only for an object whose homeExampleRef is a spe_ or rxe_ ref.
# Prints nothing.
valid_site_config() {
    if command -v jq >/dev/null 2>&1; then
        jq -e '(.homeExampleRef | type == "string") and (.homeExampleRef | test("^(spe|rxe)_[a-z2-7]{26}$"))' >/dev/null 2>&1
    else
        python3 -c 'import json,re,sys
d=json.load(sys.stdin)
sys.exit(0 if isinstance(d,dict) and isinstance(d.get("homeExampleRef"),str) and re.fullmatch(r"(spe|rxe)_[a-z2-7]{26}",d["homeExampleRef"]) else 1)' >/dev/null 2>&1
    fi
}

# A path must be a regular file (not a directory, which Docker would create and mount as a
# directory, and not a symlink), absolute, free of characters that split a mount option,
# readable by others, and valid. Checked before anything mutates.
if [[ -n "$SITE_CONFIG" ]]; then
    [[ "$SITE_CONFIG" = /* ]] || die "the site config path must be absolute"
    [[ "$SITE_CONFIG" != *[,:]* ]] || die "the site config path must not contain ',' or ':'"
    [[ -f "$SITE_CONFIG" && ! -L "$SITE_CONFIG" ]] || die "the site config must be a regular file that exists"
    mode="$(stat -c '%a' "$SITE_CONFIG")"
    (( (8#$mode & 8#004) != 0 )) \
        || die "the site config is not readable by others (mode $mode); nginx runs as uid 101. Run: chmod o+r <file>"
    valid_site_config < "$SITE_CONFIG" \
        || die 'the site config must be JSON with a "homeExampleRef" that is a spe_ or rxe_ ref'
fi

running_image() {
    docker inspect "$CONTAINER" --format '{{.Config.Image}}' 2>/dev/null || echo "(not running as a container)"
}

running_digest() {
    local image
    image="$(running_image)"
    docker image inspect "$image" --format '{{index .RepoDigests 0}}' 2>/dev/null || echo unknown
}

restore_previous() {
    local reason="$1"
    echo "error: $reason; restoring the previous frontend" >&2
    if [[ "$(docker inspect "$PREVIOUS_CONTAINER" --format '{{.Id}}' 2>/dev/null || true)" != "$INCUMBENT_ID" ]]; then
        echo "error: preserved container identity does not match the incumbent" >&2
        echo "manual recovery: inspect docker ps -a before changing $CONTAINER" >&2
        return 1
    fi
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
    docker rename "$PREVIOUS_CONTAINER" "$CONTAINER" || {
        echo "error: could not restore $PREVIOUS_CONTAINER automatically" >&2
        echo "manual recovery: docker rename $PREVIOUS_CONTAINER $CONTAINER && docker start $CONTAINER" >&2
        return 1
    }
    docker start "$CONTAINER" >/dev/null || {
        echo "error: restored container could not start; inspect docker logs $CONTAINER" >&2
        echo "manual recovery: docker start $CONTAINER" >&2
        return 1
    }
    echo "restored: $PREVIOUS_IMAGE ($PREVIOUS_DIGEST)" >&2
}

rollback_after_interruption() {
    local reason="$1"
    local preserved_id=""
    trap - ERR INT TERM
    echo "error: $reason during phase $DEPLOY_PHASE" >&2

    # A signal can arrive inside docker stop or docker rename. Consult Docker
    # rather than trusting the phase: after an atomic rename, exactly one of
    # these names exists; before it, the original name still exists.
    if [[ -n "$PREVIOUS_CONTAINER" ]]; then
        preserved_id="$(docker inspect "$PREVIOUS_CONTAINER" --format '{{.Id}}' 2>/dev/null || true)"
    fi
    if [[ "$preserved_id" == "$INCUMBENT_ID" && -n "$INCUMBENT_ID" ]]; then
        restore_previous "interrupted candidate deployment" || exit 2
    elif docker inspect "$CONTAINER" >/dev/null 2>&1; then
        if "$HAS_PREVIOUS"; then
            docker start "$CONTAINER" >/dev/null || {
                echo "error: incumbent could not be restarted" >&2
                echo "manual recovery: docker start $CONTAINER" >&2
                exit 2
            }
            echo "restarted incumbent container $CONTAINER" >&2
        else
            docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
            echo "first-install candidate removed; NPM remains on the API upstream" >&2
        fi
    elif "$HAS_PREVIOUS"; then
        echo "error: neither preserved nor incumbent frontend container exists" >&2
        echo "manual recovery: inspect docker ps -a for $CONTAINER" >&2
        exit 2
    else
        docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
        echo "first-install candidate removed; NPM remains on the API upstream" >&2
    fi
    exit 1
}

# One health probe. Quiet on purpose: right after `docker run` the container
# is still starting and curl legitimately fails ("Connection reset by peer"),
# which is not an error worth printing. The reason for the latest failure is
# kept in LAST_FAILURE and reported only if the whole wait window is exhausted.
verify_frontend() {
    local body rc path
    local paths=(/ /species/CH3 /api/v1/status)
    [[ -n "$SITE_CONFIG" ]] && paths+=(/site-config.json)
    for path in "${paths[@]}"; do
        # Bounded: a container that accepts the connection but never answers
        # must not hang the loop, or the ~30 s window is not a real bound.
        rc=0
        body="$(curl -fs --connect-timeout 2 --max-time 5 "http://127.0.0.1:${PORT}${path}" 2>/dev/null)" || rc=$?
        if (( rc != 0 )); then
            LAST_FAILURE="GET ${path} failed (curl exit ${rc})"
            return 1
        fi
        case "$path" in
            /api/v1/status)
                jq -e '.status == "ok" and (.degraded | type == "array") and .degraded == []' <<<"$body" >/dev/null \
                    || { LAST_FAILURE="GET ${path} answered but was not status=ok with nothing degraded"; return 1; }
                ;;
            /site-config.json)
                printf '%s' "$body" | valid_site_config \
                    || { LAST_FAILURE="GET ${path} did not serve a valid site config"; return 1; }
                ;;
            *)
                grep -q '<div id="root">' <<<"$body" \
                    || { LAST_FAILURE="GET ${path} answered but did not return the SPA shell"; return 1; }
                ;;
        esac
    done
}

if [[ "${1:-}" == "--check" ]]; then
    echo "container: $(running_image)"
    echo "digest:    $(running_digest)"
    echo "api:       $(docker inspect "$API_CONTAINER" --format '{{.Config.Image}}' 2>/dev/null \
        || echo '(not running)')"
    echo "network:   $NETWORK"
    echo "loopback:  http://127.0.0.1:$PORT"
    exit 0
fi

TAG="${1:-}"
[[ "$TAG" =~ ^sha-[0-9a-f]{40}$ ]] || die "usage: $0 sha-<40-char-commit>|--check"
IMAGE="${IMAGE_REPO}:${TAG}"

# One host may receive a manual retry, timer, or two operator shells. Serialize
# before even pulling so a second invocation cannot observe or reuse a stale
# preservation container while the first transaction is in flight.
exec 9>"$LOCK_FILE"
flock -n 9 \
    || die "another frontend deployment holds $LOCK_FILE; refusing concurrent mutation"

docker inspect "$API_CONTAINER" >/dev/null 2>&1 || die "required API container '$API_CONTAINER' does not exist"
[[ "$(docker inspect "$API_CONTAINER" --format '{{.State.Running}}')" == "true" ]] \
    || die "required API container '$API_CONTAINER' is stopped"
docker network inspect "$NETWORK" >/dev/null 2>&1 || die "required Docker network '$NETWORK' does not exist"
docker inspect "$API_CONTAINER" --format '{{range $name, $_ := .NetworkSettings.Networks}}{{println $name}}{{end}}' \
    | grep -Fx "$NETWORK" >/dev/null \
    || die "API container '$API_CONTAINER' is not attached to '$NETWORK'"

echo "==> deploying $IMAGE"
# Pull before replacing the current frontend, so an unavailable tag leaves the
# existing site untouched.
docker pull --quiet "$IMAGE" >/dev/null || die "could not pull $IMAGE"
echo "    pulled: $(docker inspect "$IMAGE" --format '{{index .RepoDigests 0}}' 2>/dev/null || echo unknown)"

if docker inspect "$CONTAINER" >/dev/null 2>&1; then
    HAS_PREVIOUS=true
    PREVIOUS_IMAGE="$(running_image)"
    PREVIOUS_DIGEST="$(running_digest)"
    INCUMBENT_ID="$(docker inspect "$CONTAINER" --format '{{.Id}}')"
    PREVIOUS_CONTAINER="${CONTAINER}-previous-${INCUMBENT_ID:0:12}"
    docker inspect "$PREVIOUS_CONTAINER" >/dev/null 2>&1 \
        && die "preservation name $PREVIOUS_CONTAINER is already occupied; inspect it before retrying"

    # Keep the complete known-good container configuration. It is stopped only
    # after the candidate image is available, then renamed so a failed candidate
    # can be restored exactly (ports, network, restart policy, and image).
else
    echo "    no existing frontend: performing a first install"
fi

# Install traps before the first mutation. A first install has no incumbent,
# while an upgrade's handler detects whether Docker still calls it the original
# name or the preserved name when a signal races stop/rename.
trap 'rollback_after_interruption "unexpected deployment error"' ERR
trap 'rollback_after_interruption "received interrupt or termination signal"' INT TERM

if "$HAS_PREVIOUS"; then
    DEPLOY_PHASE="incumbent_stopping"
    docker stop "$CONTAINER" >/dev/null
    DEPLOY_PHASE="incumbent_renaming"
    docker rename "$CONTAINER" "$PREVIOUS_CONTAINER"
fi

DEPLOY_PHASE="candidate_starting"

RUN_ARGS=(--network "$NETWORK" -p "127.0.0.1:${PORT}:8080" --restart unless-stopped)
if [[ -n "$SITE_CONFIG" ]]; then
    RUN_ARGS+=(--mount "type=bind,source=${SITE_CONFIG},target=/usr/share/nginx/html/site-config.json,readonly")
    echo "    site config: mounted read-only"
fi

if ! docker run -d --name "$CONTAINER" "${RUN_ARGS[@]}" "$IMAGE" >/dev/null; then
    if "$HAS_PREVIOUS"; then
        restore_previous "could not start candidate $IMAGE" || true
    else
        docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
        echo "error: could not start first-install candidate $IMAGE; NPM remains on the API upstream" >&2
    fi
    exit 1
fi

DEPLOY_PHASE="candidate_verifying"
LAST_FAILURE="no probe ran"
for attempt in $(seq 1 "$HEALTH_ATTEMPTS"); do
    if verify_frontend; then
        if "$HAS_PREVIOUS"; then
            if ! docker rm "$PREVIOUS_CONTAINER" >/dev/null; then
                echo "warning: deployed, but preserved backup remains: $PREVIOUS_CONTAINER" >&2
                echo "remove it after inspection: docker rm $PREVIOUS_CONTAINER" >&2
            fi
        fi
        trap - ERR INT TERM
        echo "==> frontend healthy on 127.0.0.1:${PORT} (attempt ${attempt}/${HEALTH_ATTEMPTS})"
        echo "==> deployed $IMAGE"
        exit 0
    fi
    sleep 1
done

echo "error: frontend not healthy after ${HEALTH_ATTEMPTS} attempts (~${HEALTH_ATTEMPTS}s): $LAST_FAILURE" >&2
docker logs "$CONTAINER" 2>&1 | tail -50 >&2 || true
if "$HAS_PREVIOUS"; then
    restore_previous "candidate $IMAGE failed SPA/API health verification" || true
else
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
    echo "error: first-install candidate $IMAGE failed health verification; NPM remains on the API upstream" >&2
fi
exit 1
