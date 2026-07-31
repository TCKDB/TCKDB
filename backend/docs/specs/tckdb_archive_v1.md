# TCKDB scientific archive v1

`tckdb.archive.v1` is a portable archive of TCKDB scientific, provenance,
submission, and curation state. It is not an operational PostgreSQL backup.
Use `pg_dump` plus an object-store snapshot when authentication state, request
replay state, worker queues, or deployment-exact storage configuration must be
restored.

## Declared scope

The fail-closed registry in `app/services/archive/registry.py` classifies every
SQLAlchemy table. New tables break the registry test until explicitly included
or excluded.

V1 excludes `api_key`, `user_session`, `idempotency_record`, and `upload_job`.
It also excludes `app_user.password_hash` and the corresponding deployment-local
`submission.upload_job_id`. These exclusions and their reasons are embedded in
every manifest. All other classified rows are preserved, including primary
keys, foreign keys, public references, timestamps, submissions, review history,
machine/reproducibility assessments, and supersession links.

Calculation artifacts are included once per content digest. Their bytes,
SHA-256 digest, length, filename, and database metadata are preserved. On
restore, `calculation_artifact.uri` is rewritten to the destination's
content-addressed object store; the source URI is a deployment locator, not
artifact identity.

## Container and determinism

The uncompressed deterministic tar contains:

- `manifest.json`: schema tag, Alembic revision, exact table/column registry,
  exclusions, row counts/checksum, and blob inventory.
- `rows.ndjson`: one complete SQL row per line, ordered by FK-safe table order
  and primary key.
- `blobs/<sha256>`: byte-exact calculation artifacts.

JSON keys, tar member order, metadata, ownership, permissions, and timestamps
are canonicalized. Floating-point columns use hexadecimal IEEE-754 strings;
dates and datetimes use ISO-8601; enums and UUIDs use canonical strings; arrays
recurse through their element codec; JSONB retains its JSON value; RDKit `mol`
columns use the cartridge's reversible text representation. Identical database
and artifact content at one Alembic revision therefore produces identical bytes.

## Snapshot and restore behavior

`write_archive(session, destination)` acquires PostgreSQL `SHARE` locks on the
Alembic version table and every included table, in lexical order, before its
first revision, row, or artifact query. Concurrent reads continue. Concurrent
`INSERT`, `UPDATE`, `DELETE`, and conflicting DDL wait until the caller commits
or rolls back the surrounding transaction. This prevents a cross-statement
archive under the default READ COMMITTED isolation, at the cost of pausing
writes for the duration of artifact collection and tar creation. Callers should
finish the transaction promptly.

`restore_archive(session, source)` requires the archive and target to have the
same Alembic revision. Every ORM-managed target table, including excluded
credential, session, request-replay, and worker-queue tables, must otherwise be
empty, and the target must contain exactly
the canonical identities seeded by migrations in `reaction_family`,
`calculation_parameter_vocab`, and `conformer_assignment_scheme`. Restore
locks all ORM-managed tables, rechecks this state, replaces those seeds with the
archived rows, and then inserts the remaining rows. Its trigger-safe order
places scientific roots and children before reviews, review events after their
reviews, and supersessions last.

Member names, registry, row counts, ordering, checksums, and every blob are
validated before writing. Destination artifact URIs are substituted and
PostgreSQL sequences are repaired. Artifact-store writes occur before the
database commit; if the database transaction later fails, harmless unreferenced
content-addressed blobs may remain. There is no cross-store atomicity claim.
V1 has no merge or upsert mode and exposes no bypass for the target guard.

The module is admin/CLI infrastructure only. It is not exposed by the public
scientific API.


## Known scaling characteristic: frozen release bytes are inlined

`release_artifact.content` holds the frozen bytes of a published dataset
release (see
[`dataset_release_and_profiles.md`](dataset_release_and_profiles.md)). It is the
archive's only `LargeBinary` column, and it is encoded inline in `rows.ndjson`
via the tagged `$bytes` codec — base64, alongside every other column value —
rather than through the `blobs/` sidecar used for calculation-artifact bytes.

This is correct and lossless: an export → wipe → restore cycle returns
byte-exact artifact content, and the restored database still passes
`verify_release`. The `$bytes` tag mirrors `$float`, so a byte string can never
be confused with a text column that happens to hold base64, and no existing
column's encoding changed.

The trade-off is size and shape: base64 inflates the payload by roughly 4/3,
and one release occupies a **single NDJSON line**. That is fine while releases
are small and keeps the archive's row-and-hash model uniform — one code path,
one integrity story.

**Revisit when:** the first release large enough that a single line in
`rows.ndjson` is uncomfortable to stream, diff, or hold in memory — in practice
artifacts in the tens of MB, or the point at which archive write/restore memory
becomes a concern. The fix is to move release bytes to the existing `blobs/`
sidecar, which already has the streaming and per-blob integrity machinery.
Doing it before there is a release that needs it buys nothing and adds a second
storage path to keep correct.
