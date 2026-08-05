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
#   Deploy an immutable tag -- `sha-<commit>` or `v1.2.3`. `latest` moves, so a
#   host running it cannot answer "which code is this?", and two hosts running
#   it may differ. Passing `latest` requires --allow-mutable and says so.
#
# USAGE
#   tckdb_deploy.sh sha-<full-commit>       # deploy a pinned build
#   tckdb_deploy.sh v1.2.3                  # deploy a release
#   tckdb_deploy.sh --check                 # report what is running vs available
set -uo pipefail

IMAGE_REPO="${TCKDB_IMAGE_REPO:-laxzal/tckdb-api}"
CONTAINER="${TCKDB_CONTAINER:-tckdb-api}"
API_PORT="${TCKDB_API_PORT:-8010}"
DB_CONTAINER="${TCKDB_DB_CONTAINER:-tckdbv2-db-1}"
DB_NETWORK="${TCKDB_DB_NETWORK:-tckdbv2_default}"
ENV_FILE="${TCKDB_ENV_FILE:-/home/calvin/repos/tckdbv2/.env.pi}"
BACKUP_DIR="${TCKDB_BACKUP_DIR:-/home/calvin/backups}"
STATUS_URL="${TCKDB_LOCAL_STATUS_URL:-http://127.0.0.1:${API_PORT}/api/v1/status}"

die() { echo "error: $*" >&2; exit 1; }

running_image() {
    docker inspect "$CONTAINER" --format '{{.Config.Image}}' 2>/dev/null || echo "(not running as a container)"
}

if [[ "${1:-}" == "--check" ]]; then
    echo "container:      $(running_image)"
    echo "systemd uvicorn: $(systemctl is-active tckdb-api.service 2>/dev/null || echo n/a)"
    echo "live revision:  $(curl -s "$STATUS_URL" | grep -o '"alembic_revision":"[^"]*"' || echo unknown)"
    exit 0
fi

TAG="${1:-}"
[[ -z "$TAG" ]] && die "usage: $0 <tag>|--check   (tag e.g. sha-<commit> or v1.2.3)"

if [[ "$TAG" == "latest" && "${2:-}" != "--allow-mutable" ]]; then
    die "'latest' moves, so the deployed version would be unanswerable. Pass an immutable tag (sha-<commit> or v1.2.3), or re-run with: $0 latest --allow-mutable"
fi

IMAGE="${IMAGE_REPO}:${TAG}"
echo "==> deploying ${IMAGE}"

# 1. Back up first. Cheap, and the only step that makes the rest reversible.
mkdir -p "$BACKUP_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="${BACKUP_DIR}/tckdb-predeploy-${STAMP}.dump"
docker exec "$DB_CONTAINER" pg_dump -U tckdb -d tckdb -Fc > "$BACKUP" \
    || die "backup failed; refusing to deploy"
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
docker run --rm --network "$DB_NETWORK" --env-file "$ENV_FILE" \
    -e DB_HOST=db -e DB_PORT=5432 \
    "$IMAGE" alembic upgrade head || die "migration failed; the old API is still running and the backup is at ${BACKUP}"

# 4. Swap the API.
echo "==> restarting API"
if systemctl is-active --quiet tckdb-api.service 2>/dev/null; then
    echo "    stopping the systemd uvicorn unit (pre-container deployment)"
    sudo systemctl stop tckdb-api.service
fi
docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
docker run -d --name "$CONTAINER" \
    --network "$DB_NETWORK" \
    --env-file "$ENV_FILE" \
    -e DB_HOST=db -e DB_PORT=5432 \
    -e TCKDB_INLINE_WORKER=true \
    -p "127.0.0.1:${API_PORT}:8010" \
    --restart unless-stopped \
    "$IMAGE" >/dev/null || die "could not start the new container; roll back with: $0 <previous-tag>"

# 5. Verify, and be specific about what to do if it is wrong.
echo "==> verifying"
for _ in $(seq 1 45); do
    body="$(curl -s "$STATUS_URL" 2>/dev/null)"
    [[ "$body" == *'"status":"ok"'* ]] && { echo "    $body"; echo "==> deployed ${IMAGE}"; exit 0; }
    sleep 2
done

echo "    last response: ${body:-<none>}" >&2
cat >&2 <<EOF
error: the new container did not report status=ok.

  logs:      docker logs ${CONTAINER} | tail -50
  roll back: $0 <previous-tag>
  restore:   the pre-deploy backup is at ${BACKUP}
             (only needed if a migration is the problem -- the image itself
              holds no data)
EOF
exit 1
