#!/usr/bin/env bash
# Nightly TCKDB Postgres backup: dump from the db container, gzip, keep 14 days.
#
# Dumps the database from the compose project's `db` container with
# `pg_dump --create`, writes $TCKDB_BACKUP_DEST/tckdb_<YYYYmmdd_HHMMSS>.sql.gz,
# restores it into a scratch database to verify it (see below), then deletes
# tckdb_*.sql.gz files there older than 14 days. Takes no arguments.
#
# USAGE
#   tckdb_backup.sh
#   tckdb_backup.sh -h | --help
#
# ENVIRONMENT
#   TCKDB_BACKUP_DEST     required: directory to write backups to (created if
#                         missing)
#   TCKDB_ENV_FILE        optional: the stack's env file, such as
#                         .env.selfhosted; read for COMPOSE_PROJECT_NAME,
#                         DB_ADMIN_USER and DB_NAME
#   COMPOSE_PROJECT_NAME  optional: the compose project; wins over the env file
#   TCKDB_DB_CONTAINER    optional: the db container; wins over the project
#   TCKDB_DB_USER         optional: the role pg_dump and psql run as
#   TCKDB_DB_NAME         optional: the database to back up
#
# WHICH DATABASE
#   The container is TCKDB_DB_CONTAINER; else the running `db` service of
#   COMPOSE_PROJECT_NAME (the environment's, else TCKDB_ENV_FILE's); else
#   the container `tckdbv2-db-1` if it exists; else `docker compose ps -q db`
#   run here, given TCKDB_ENV_FILE; else `tckdbv2-db-1`.
#   The role is TCKDB_DB_USER, else DB_ADMIN_USER in TCKDB_ENV_FILE; the
#   database is TCKDB_DB_NAME, else DB_NAME in TCKDB_ENV_FILE. For a container
#   found through a compose project each then falls back to the container's
#   POSTGRES_USER / POSTGRES_DB. Both finally fall back to `tckdb`.
#   With only TCKDB_BACKUP_DEST set and `tckdbv2-db-1` present, this dumps
#   database `tckdb` as role `tckdb` in `tckdbv2-db-1`.
#
# EXAMPLES
#   TCKDB_BACKUP_DEST=/srv/tckdb/backups tckdb_backup.sh
#   TCKDB_BACKUP_DEST=/var/backups/tckdb TCKDB_ENV_FILE=/opt/tckdb/.env.selfhosted \
#       tckdb_backup.sh
#
# EXIT CODES
#   0  written and verified; non-zero otherwise (1 for a failed check).
#
# ---------------------------------------------------------------------------
# WHY --create IS NOT OPTIONAL
# ---------------------------------------------------------------------------
# Without it the dump carries no CREATE DATABASE, so whoever restores it makes
# the target database by hand -- and a bare CREATE DATABASE inherits its
# encoding from template1. If template1 is SQL_ASCII while the real
# database is UTF8, restoring a UTF8 dump into a
# hand-made target exited 0, printed no error, and silently mis-counted every
# multi-byte character: length('em dash: <U+2014>') came back 12 instead of 10.
#
# `pg_dump --create` emits
#     CREATE DATABASE tckdb WITH TEMPLATE = template0 ENCODING = 'UTF8' ...
# which pins both the template and the encoding, so the restore cannot inherit
# the wrong one no matter what template1 happens to be at the time.
#
# ---------------------------------------------------------------------------
# WHY THE DUMP IS VERIFIED AND NOT MERELY WRITTEN
# ---------------------------------------------------------------------------
# A backup nobody has restored is a hypothesis. `exit 0` from pg_dump means
# "the bytes were written", not "the bytes restore to the same data" -- and the
# encoding bug above is invisible in exactly that gap. So every run restores
# the archive it just wrote into a scratch database and compares.
#
# The two checks answer different questions and neither subsumes the other:
#   (1) does the archive pin its own encoding and template?   [text check]
#   (2) does the archive actually restore to identical data?  [round trip]
#
# The canary in (2) is length('em dash: <U+2014>') == 10, which is the exact
# measurement that exposes a SQL_ASCII restore. It tests the restored
# database's encoding rather than its contents, so it fires even on an empty
# database and cannot be satisfied by accident.
set -euo pipefail

usage() { sed -n '2,/^set -euo/p' "$0" | sed '$d; s/^# \{0,1\}//'; }
for arg in "$@"; do
    case "$arg" in -h | --help) usage; exit 0 ;; esac
done

# No default: this script is public, and a path under one
# operator's home directory would mean every copy of it defaults to that
# operator's backup location.
if [[ -z "${TCKDB_BACKUP_DEST:-}" ]]; then
    echo "error: TCKDB_BACKUP_DEST is not set (directory to write backups to)" >&2
    exit 1
fi
DEST="$TCKDB_BACKUP_DEST"
ENV_FILE="${TCKDB_ENV_FILE:-}"
LEGACY_CONTAINER=tckdbv2-db-1
if [[ -n "$ENV_FILE" ]] && ! [[ -f "$ENV_FILE" && -r "$ENV_FILE" ]]; then
    echo "error: TCKDB_ENV_FILE '${ENV_FILE}' is not a readable file" >&2
    exit 1
fi

# env_value FILE KEY: the last `KEY=value` in FILE without surrounding quotes.
env_value() {
    [[ -n "$1" && -f "$1" ]] || return 0
    sed -n "s/^[[:space:]]*\(export[[:space:]]\{1,\}\)\{0,1\}$2=//p" "$1" | tail -1 \
        | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/"
}
# container_env CONTAINER KEY: KEY from the container's configured environment.
container_env() {
    docker inspect --type container "$1" --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null \
        | sed -n "s/^$2=//p" | head -1
}

# The container (see WHICH DATABASE). from_project: found through compose.
CONTAINER="${TCKDB_DB_CONTAINER:-}"
from_project=0
project="${COMPOSE_PROJECT_NAME:-$(env_value "$ENV_FILE" COMPOSE_PROJECT_NAME)}"
if [[ -z "$CONTAINER" && -n "$project" ]]; then
    CONTAINER="$(docker ps -q --filter "label=com.docker.compose.project=${project}" \
        --filter "label=com.docker.compose.service=db" 2>/dev/null | head -1)"
    if [[ -z "$CONTAINER" ]]; then
        echo "error: no running db container in compose project '${project}'" >&2
        exit 1
    fi
    from_project=1
elif [[ -z "$CONTAINER" ]]; then
    if docker inspect --type container "$LEGACY_CONTAINER" --format '{{.Id}}' >/dev/null 2>&1; then
        CONTAINER="$LEGACY_CONTAINER"
    else
        compose_args=()
        [[ -n "$ENV_FILE" ]] && compose_args=(--env-file "$ENV_FILE")
        CONTAINER="$(docker compose "${compose_args[@]}" ps -q db 2>/dev/null | head -1)"
        if [[ -n "$CONTAINER" ]]; then from_project=1; else CONTAINER="$LEGACY_CONTAINER"; fi
    fi
fi

DB_USER="${TCKDB_DB_USER:-$(env_value "$ENV_FILE" DB_ADMIN_USER)}"
SOURCE_DB="${TCKDB_DB_NAME:-$(env_value "$ENV_FILE" DB_NAME)}"
if [[ $from_project -eq 1 ]]; then
    DB_USER="${DB_USER:-$(container_env "$CONTAINER" POSTGRES_USER)}"
    SOURCE_DB="${SOURCE_DB:-$(container_env "$CONTAINER" POSTGRES_DB)}"
fi
DB_USER="${DB_USER:-tckdb}"
SOURCE_DB="${SOURCE_DB:-tckdb}"
# Scratch databases on any TCKDB host must match tckdb_test* by convention.
SCRATCH="tckdb_test_restore_check_$$"

STAMP=$(date +%Y%m%d_%H%M%S)
FILE="$DEST/tckdb_${STAMP}.sql.gz"

psql_admin() { docker exec -i "$CONTAINER" psql -U "$DB_USER" -d postgres -v ON_ERROR_STOP=1 -tAq "$@"; }
psql_scratch() { docker exec -i "$CONTAINER" psql -U "$DB_USER" -d "$SCRATCH" -v ON_ERROR_STOP=1 -tAq "$@"; }

drop_scratch() { psql_admin -c "DROP DATABASE IF EXISTS \"$SCRATCH\"" >/dev/null 2>&1 || true; }
# The scratch database must not outlive this script even on failure -- a
# leaked one would be restored-into again next run and quietly diverge.
trap drop_scratch EXIT

mkdir -p "$DEST"

# --- write the archive ------------------------------------------------------
docker exec "$CONTAINER" pg_dump -U "$DB_USER" -d "$SOURCE_DB" --create | gzip >"$FILE"
test -s "$FILE" || { echo "FAIL: dump is empty" >&2; exit 1; }

# --- check (1): the archive pins encoding and template ----------------------
header="$(zcat "$FILE" | grep -m1 '^CREATE DATABASE' || true)"
if [[ -z "$header" ]]; then
    echo "FAIL: archive contains no CREATE DATABASE -- --create was lost" >&2
    exit 1
fi
if [[ "$header" != *"ENCODING = 'UTF8'"* || "$header" != *"TEMPLATE = template0"* ]]; then
    echo "FAIL: CREATE DATABASE does not pin UTF8 from template0:" >&2
    echo "      $header" >&2
    exit 1
fi

# --- check (2): the archive restores to identical data ----------------------
# Rewrite only the three lines that name the database, so the archive's own
# CREATE DATABASE clause -- encoding and template included -- is what runs.
# Restoring under a different name is the only way to test the real archive
# against a live cluster that already holds the original.
drop_scratch
zcat "$FILE" \
  | sed -e "s/^CREATE DATABASE ${SOURCE_DB} /CREATE DATABASE ${SCRATCH} /" \
        -e "s/^ALTER DATABASE ${SOURCE_DB} /ALTER DATABASE ${SCRATCH} /" \
        -e "s/^\\\\connect ${SOURCE_DB}\$/\\\\connect ${SCRATCH}/" \
  | docker exec -i "$CONTAINER" psql -U "$DB_USER" -d postgres -v ON_ERROR_STOP=1 -q >/dev/null

restored_encoding="$(psql_admin -c "SELECT pg_encoding_to_char(encoding) FROM pg_database WHERE datname='${SCRATCH}'")"
if [[ "$restored_encoding" != "UTF8" ]]; then
    echo "FAIL: restored database is ${restored_encoding}, not UTF8" >&2
    exit 1
fi

# The canary: a multi-byte character must measure 1, not 3.
#
# Written as the UTF-8 byte sequence for U+2014 decoded by the server rather
# than as a literal em dash, for two reasons. A script whose job is to detect
# an encoding fault must not itself depend on how its own file is decoded --
# that is the same class of bug it exists to catch. And the repo lints
# non-ASCII out of runtime strings, so the literal form could not be shared
# with the versioned copy of this script.
#
# In UTF8 this is one character. In SQL_ASCII the same three bytes stay three
# characters, so a restore that landed in the wrong encoding reports 3.
canary="$(psql_scratch -c "SELECT length(convert_from('\\xe28094'::bytea, 'UTF8'))")"
if [[ "$canary" != "1" ]]; then
    echo "FAIL: multi-byte character measures ${canary}, expected 1 -- restored database is not UTF8" >&2
    exit 1
fi

# Structural equality: re-dump both without --create and compare. If the
# restore was faithful these are identical, and any text mangling changes them.
sum_source="$(docker exec "$CONTAINER" pg_dump -U "$DB_USER" -d "$SOURCE_DB" | md5sum | cut -d' ' -f1)"
sum_restored="$(docker exec "$CONTAINER" pg_dump -U "$DB_USER" -d "$SCRATCH" | md5sum | cut -d' ' -f1)"
if [[ "$sum_source" != "$sum_restored" ]]; then
    echo "FAIL: restored data differs from source (${sum_source} != ${sum_restored})" >&2
    exit 1
fi

drop_scratch

# --- prune ------------------------------------------------------------------
find "$DEST" -name 'tckdb_*.sql.gz' -mtime +14 -delete

echo "wrote $FILE ($(du -h "$FILE" | cut -f1)) -- verified: restores to UTF8, data identical"
