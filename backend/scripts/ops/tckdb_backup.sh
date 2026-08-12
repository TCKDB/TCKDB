#!/usr/bin/env bash
# Nightly TCKDB Postgres backup: dump from the db container, gzip, keep 14 days.
#
# ---------------------------------------------------------------------------
# WHY --create IS NOT OPTIONAL
# ---------------------------------------------------------------------------
# Without it the dump carries no CREATE DATABASE, so whoever restores it makes
# the target database by hand -- and a bare CREATE DATABASE inherits its
# encoding from template1. On this cluster template1 was SQL_ASCII until
# 2026-08-12 while the real database was UTF8, so restoring a UTF8 dump into a
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
# measurement the 2026-08-12 investigation used. It tests the restored
# database's encoding rather than its contents, so it fires even on an empty
# database and cannot be satisfied by accident.
set -euo pipefail

DEST=/home/calvin/tckdb_backups
CONTAINER=tckdbv2-db-1
SOURCE_DB=tckdb
# Scratch databases on any TCKDB host must match tckdb_test* by convention.
SCRATCH="tckdb_test_restore_check_$$"

STAMP=$(date +%Y%m%d_%H%M%S)
FILE="$DEST/tckdb_${STAMP}.sql.gz"

psql_admin() { docker exec -i "$CONTAINER" psql -U tckdb -d postgres -v ON_ERROR_STOP=1 -tAq "$@"; }
psql_scratch() { docker exec -i "$CONTAINER" psql -U tckdb -d "$SCRATCH" -v ON_ERROR_STOP=1 -tAq "$@"; }

drop_scratch() { psql_admin -c "DROP DATABASE IF EXISTS \"$SCRATCH\"" >/dev/null 2>&1 || true; }
# The scratch database must not outlive this script even on failure -- a
# leaked one would be restored-into again next run and quietly diverge.
trap drop_scratch EXIT

mkdir -p "$DEST"

# --- write the archive ------------------------------------------------------
docker exec "$CONTAINER" pg_dump -U tckdb -d "$SOURCE_DB" --create | gzip >"$FILE"
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
  | docker exec -i "$CONTAINER" psql -U tckdb -d postgres -v ON_ERROR_STOP=1 -q >/dev/null

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
sum_source="$(docker exec "$CONTAINER" pg_dump -U tckdb -d "$SOURCE_DB" | md5sum | cut -d' ' -f1)"
sum_restored="$(docker exec "$CONTAINER" pg_dump -U tckdb -d "$SCRATCH" | md5sum | cut -d' ' -f1)"
if [[ "$sum_source" != "$sum_restored" ]]; then
    echo "FAIL: restored data differs from source (${sum_source} != ${sum_restored})" >&2
    exit 1
fi

drop_scratch

# --- prune ------------------------------------------------------------------
find "$DEST" -name 'tckdb_*.sql.gz' -mtime +14 -delete

echo "wrote $FILE ($(du -h "$FILE" | cut -f1)) -- verified: restores to UTF8, data identical"
