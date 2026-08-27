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
HAS_PREVIOUS=false
PREVIOUS_CONTAINER=""
INCUMBENT_ID=""
DEPLOY_PHASE="preflight"

die() { echo "error: $*" >&2; exit 1; }

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

verify_frontend() {
    curl -fsS "http://127.0.0.1:${PORT}/" | grep -q '<div id="root">' || return 1
    curl -fsS "http://127.0.0.1:${PORT}/species/CH3" | grep -q '<div id="root">' || return 1
    curl -fsS "http://127.0.0.1:${PORT}/api/v1/status" \
        | jq -e '.status == "ok" and (.degraded | type == "array") and .degraded == []' >/dev/null
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

if ! docker run -d --name "$CONTAINER" \
    --network "$NETWORK" \
    -p "127.0.0.1:${PORT}:8080" \
    --restart unless-stopped \
    "$IMAGE" >/dev/null; then
    if "$HAS_PREVIOUS"; then
        restore_previous "could not start candidate $IMAGE" || true
    else
        docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
        echo "error: could not start first-install candidate $IMAGE; NPM remains on the API upstream" >&2
    fi
    exit 1
fi

DEPLOY_PHASE="candidate_verifying"
for _ in $(seq 1 20); do
    if verify_frontend; then
        if "$HAS_PREVIOUS"; then
            if ! docker rm "$PREVIOUS_CONTAINER" >/dev/null; then
                echo "warning: deployed, but preserved backup remains: $PREVIOUS_CONTAINER" >&2
                echo "remove it after inspection: docker rm $PREVIOUS_CONTAINER" >&2
            fi
        fi
        trap - ERR INT TERM
        echo "==> deployed $IMAGE"
        exit 0
    fi
    sleep 1
done

docker logs "$CONTAINER" 2>&1 | tail -50 >&2 || true
if "$HAS_PREVIOUS"; then
    restore_previous "candidate $IMAGE failed SPA/API health verification" || true
else
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
    echo "error: first-install candidate $IMAGE failed health verification; NPM remains on the API upstream" >&2
fi
exit 1
