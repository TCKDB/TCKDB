#!/usr/bin/env bash
# Build the self-host install bundle for a release.
#
# Writes dist/tckdb-selfhost-<version>.tar.gz and a .sha256 file beside it.
# The tarball holds one directory, tckdb-selfhost-<version>/, with the compose
# file, the env templates, the Caddyfile and the operator scripts at the paths
# they have in the repository, so every script runs unchanged inside it, plus
# a README.md and a VERSION file. The application itself is the published
# images; nothing is compiled here.
#
# The same tree gives the same bytes: entries sorted by name, owned by 0:0,
# modes fixed (755 for directories and scripts, 644 otherwise), every mtime
# set to SOURCE_DATE_EPOCH or else the time of the HEAD commit, and gzip
# writing no name or timestamp.
#
# USAGE
#   backend/scripts/ops/make_selfhost_bundle.sh [options] VERSION
#
#   VERSION is the release, such as 0.4.1 (a leading v is dropped). It must
#   equal the default image tag of every TCKDB image in docker-compose.yml
#   and the version in backend/pyproject.toml.
#
# OPTIONS
#   --out DIR     where to write (default: dist/ under the repository root)
#   -h, --help    show this help
#
# Needs GNU tar, gzip and sha256sum.
#
# Exit codes: 0 written, 1 refused or failed, 2 usage error.
set -euo pipefail

usage() { sed -n '2,/^set -euo/p' "$0" | sed '$d; s/^# \{0,1\}//'; }
die() { echo "error: $*" >&2; exit 1; }
usage_error() { echo "error: $*" >&2; echo "run with --help for usage" >&2; exit 2; }

for arg in "$@"; do
    case "$arg" in -h|--help) usage; exit 0 ;; esac
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
OUT_DIR="$REPO_ROOT/dist"
VERSION=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --out) [[ $# -ge 2 ]] || usage_error "--out needs a value"; OUT_DIR="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        -*) usage_error "unknown option '$1'" ;;
        *) [[ -z "$VERSION" ]] || usage_error "one VERSION only, got '$VERSION' and '$1'"; VERSION="$1"; shift ;;
    esac
done
[[ -n "$VERSION" ]] || usage_error "VERSION is required, such as 0.4.1"
VERSION="${VERSION#v}"
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+([-+][0-9A-Za-z.-]+)?$ ]] \
    || usage_error "VERSION must be a release such as 0.4.1, got '$VERSION'"

# Every file the bundle carries, relative to the repository root. Keep in step
# with ALLOWED in backend/tests/ops/test_selfhost_bundle.py.
FILES=(
    .env.db-admin.example
    .env.selfhosted.example
    LICENSE
    backend/scripts/check_selfhosted_deployment.sh
    backend/scripts/lib/auth_key.sh
    backend/scripts/ops/tckdb-alert-failed.service
    backend/scripts/ops/tckdb-alert.service
    backend/scripts/ops/tckdb-alert.timer
    backend/scripts/ops/tckdb-storage-hostfw.service
    backend/scripts/ops/tckdb_alert_check.sh
    backend/scripts/ops/tckdb_backup.sh
    backend/scripts/ops/tckdb_deploy.sh
    backend/scripts/ops/tckdb_make_env.sh
    backend/scripts/ops/tckdb_storage_hostfw.sh
    backend/scripts/tckdb_auth.sh
    deploy/caddy/Caddyfile
    docker-compose.yml
    examples/deployment/systemd/tckdb-backup.service
    examples/deployment/systemd/tckdb-backup.timer
)

tar --version 2>/dev/null | grep -q 'GNU tar' || die "GNU tar is required (for --sort=name)"
command -v gzip >/dev/null 2>&1 || die "gzip is required"
command -v sha256sum >/dev/null 2>&1 || die "sha256sum is required"

for f in "${FILES[@]}"; do
    [[ -f "$REPO_ROOT/$f" ]] || die "missing from the repository: $f"
done

# The version must be the one the compose file starts and the backend carries.
compose_tags="$(grep -oE 'laxzal/tckdb[-_][A-Za-z0-9_-]+:\$\{TCKDB_IMAGE_TAG:-[^}]*\}' "$REPO_ROOT/docker-compose.yml" \
    | sed 's/.*:-//; s/}$//' | sort -u)"
[[ -n "$compose_tags" ]] || die "docker-compose.yml names no TCKDB image with a default tag"
if [[ "$compose_tags" != "$VERSION" ]]; then
    die "docker-compose.yml defaults its TCKDB images to $(echo "$compose_tags" | paste -sd, -), not $VERSION"
fi
pyproject_version="$(awk '
    /^\[/ { in_project = ($0 == "[project]") }
    in_project && /^version[[:space:]]*=/ { gsub(/^version[[:space:]]*=[[:space:]]*"|"[[:space:]]*$/, ""); print; exit }
' "$REPO_ROOT/backend/pyproject.toml")"
[[ "$pyproject_version" == "$VERSION" ]] \
    || die "backend/pyproject.toml is version '${pyproject_version}', not $VERSION"

if [[ -n "${SOURCE_DATE_EPOCH:-}" ]]; then
    epoch="$SOURCE_DATE_EPOCH"
else
    epoch="$(git -C "$REPO_ROOT" log -1 --format=%ct HEAD 2>/dev/null)" \
        || die "not a git checkout; set SOURCE_DATE_EPOCH to fix the file times"
fi
[[ "$epoch" =~ ^[0-9]+$ ]] || die "SOURCE_DATE_EPOCH must be seconds since the epoch, got '$epoch'"

name="tckdb-selfhost-$VERSION"
mkdir -p "$OUT_DIR"
OUT_DIR="$(cd "$OUT_DIR" && pwd)"
staging="$(mktemp -d)" tmp_out=""
trap 'rm -rf "$staging" ${tmp_out:+"$tmp_out"}' EXIT
top="$staging/$name"

for f in "${FILES[@]}"; do
    mkdir -p "$top/$(dirname "$f")"
    cp "$REPO_ROOT/$f" "$top/$f"
done

printf '%s\n' "$VERSION" >"$top/VERSION"
cat >"$top/README.md" <<EOF
# TCKDB self-host bundle $VERSION

Everything needed to run TCKDB $VERSION with Docker Compose: the compose file,
the env templates and the operator scripts. The application comes as the
published images \`laxzal/tckdb-api:$VERSION\` and \`laxzal/tckdb-frontend:$VERSION\`.

## Install

You need \`bash\`, \`openssl\` and Docker with Compose v2. From this directory:

\`\`\`bash
backend/scripts/ops/tckdb_make_env.sh
docker compose --env-file .env.selfhosted --env-file .env.db-admin \\
    --profile selfhost up -d --wait
\`\`\`

The first command asks who should reach TCKDB, writes \`.env.selfhosted\` and
\`.env.db-admin\` with generated secrets, prints the administrator's password
once, and prints the start command for your answer. Then open
<http://127.0.0.1:8011> and log in.

## Upgrade

1. Back up the database and both env files.
2. Unpack the new release's bundle beside this directory.
3. Copy \`.env.selfhosted\` and \`.env.db-admin\` from this directory into the
   new one. They hold every password and \`COMPOSE_PROJECT_NAME\`, which keeps
   the same database and file volumes.
4. If \`.env.selfhosted\` sets \`TCKDB_IMAGE_TAG\`, change it to the new
   release or delete the line.
5. Run the same \`up -d --wait\` command from the new directory.

## Documentation

The full guide, including access from a lab network or the internet,
backups and troubleshooting: <https://tckdb.github.io/TCKDB/deployment/>.
Source and releases: <https://github.com/TCKDB/TCKDB>.
EOF

find "$top" -type d -exec chmod 0755 {} +
find "$top" -type f -exec chmod 0644 {} +
find "$top" -type f -name '*.sh' -exec chmod 0755 {} +

tarball="$OUT_DIR/$name.tar.gz"
tmp_out="$(mktemp "$OUT_DIR/.$name.XXXXXX")"
(
    cd "$staging"
    LC_ALL=C tar --sort=name --format=gnu --numeric-owner --owner=0 --group=0 \
        --mtime="@$epoch" --no-acls --no-xattrs --no-selinux \
        -cf - "$name"
) | gzip -9 -n >"$tmp_out"
chmod 0644 "$tmp_out"
mv -f "$tmp_out" "$tarball"
(cd "$OUT_DIR" && sha256sum "$name.tar.gz" >"$name.tar.gz.sha256")

echo "wrote $tarball"
echo "wrote $tarball.sha256"
cat "$tarball.sha256"
