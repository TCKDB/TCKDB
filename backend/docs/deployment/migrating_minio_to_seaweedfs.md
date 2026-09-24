# Moving an existing deployment from MinIO to SeaweedFS

SeaweedFS is the object store TCKDB ships by default (#541). A deployment
that already runs MinIO can keep running it: TCKDB works with any
S3-compatible store. This runbook is for an operator who wants to move one.

## What moves, and what does not

TCKDB keeps raw calculation files (ESS logs, input files, checkpoints) and
ThermoML snapshots in the object store. The database refers to each one by
**bucket and key only**, never by server address:

| Where | Column | Shape |
|---|---|---|
| `calculation_artifact` | `uri`, `sha256`, `bytes` | `s3://<bucket>/<sha256[:2]>/<sha256>` |
| `external_source_record` | `raw_uri`, `content_sha256`, `content_length` | `s3://<bucket>/<sha256[:2]>/<sha256>`, or an archive member path when the store was down at import time (that one is not an object reference) |

The app does not even parse `calculation_artifact.uri` when it reads a file:
it rebuilds the key from the digest and reads the bucket named by
`S3_BUCKET` at the address in `S3_ENDPOINT_URL`. So the migration is:

1. copy every object to the same key in a bucket of the **same name** on
   SeaweedFS;
2. prove the copy is complete and byte-identical;
3. point `S3_ENDPOINT_URL` at SeaweedFS.

**No database row changes.** The bucket can also hold objects that no row
points at, and they are copied too: the reclaim hold (`reclaimed/<digest>`,
see `backend/scripts/ops/verify_artifact_integrity.py`), and orphans left
by uploads that failed after their file was written.

The tool is `backend/scripts/ops/migrate_object_store.py`. It never writes,
deletes or modifies anything in the source store, and it opens the
database read-only. Its `DEST_S3_*` inputs are for the tool only; they are
not application settings.

| Mode | What it does | Exit 0 means |
|---|---|---|
| (no flag) dry run | Lists both buckets and reports what it would copy (count and bytes). Checks every database reference against the *source*. Writes nothing. | the plan was made |
| `--commit` | Copies each object across in chunks, never whole in memory. Skips a key only when the destination already has the same size **and** SHA-256. Reads each copy back and checks it. Then verifies. | everything was copied and verified |
| `--verify-only` | Reads every referenced object back from the **destination**, and checks its SHA-256 and byte count against the database row. Also checks that every source key is in the destination at the same size. | every reference was checked and matched |

Exit `1` means something is missing, mismatched or failed to copy. Exit `2`
means nothing was verified: no database row references an object, a store
did not answer, or the command was wrong. **A verify that compared nothing
does not pass**: the report always states how many rows and objects it
compared (`rows_compared`, `distinct_objects_read`). Pass `--allow-empty`
only if the deployment really has no artifacts.

The report is JSON on stdout. Progress lines and the one-line `RESULT` go
to stderr. Endpoints are printed without credentials, and secrets are never
printed.

## Before you start

- **Disk.** SeaweedFS needs room for a full second copy of the bucket while
  MinIO still holds the first. Check the MinIO volume's size
  (`docker system df -v`, the `tckdb_minio` line) against the free space
  where Docker keeps its volumes.
- **Compose version.** The port override below uses `!override`, which
  needs Docker Compose 2.24.4 or newer (`docker compose version`).
- **Which layout you run.** The commands differ in one place:

  | | API in a container on the compose network (`tckdb_deploy.sh`) | API on the host (systemd / uvicorn) |
  |---|---|---|
  | `S3_ENDPOINT_URL` now | `http://minio:9000` | `http://127.0.0.1:9000` |
  | Tool reaches SeaweedFS at | `http://seaweedfs:9000` | `http://127.0.0.1:9100` (during the migration) |
  | Cutover | change the env file to `http://seaweedfs:9000` | stop MinIO and publish SeaweedFS on 9000; the env file keeps `http://127.0.0.1:9000` |

Below, `<env-file>` is the env file your stack runs with (add a second
`--env-file` if you normally pass one), `<api-container>` is the API
container's name, and `<network>` is the compose network the API joins
(`docker inspect <api-container> --format '{{json .NetworkSettings.Networks}}'`).

## Running both stores at once

The compose file keeps MinIO as the `minio` service under `--profile minio`,
with its data in the `tckdb_minio` volume, and runs SeaweedFS as the
`seaweedfs` service on the `tckdb_seaweedfs` volume. **Both publish
`127.0.0.1:9000`**, so starting SeaweedFS next to a running MinIO fails on
the port. The fix is not to rename anything. For the migration only,
publish SeaweedFS on another host port. Create
`docker-compose.seaweedfs-migration.yml` next to `docker-compose.yml`:

```yaml
# Temporary, for the MinIO -> SeaweedFS migration only.
# MinIO keeps 127.0.0.1:9000 (the API is still using it); SeaweedFS is
# published on 127.0.0.1:9100 so the two do not collide. Inside the compose
# network both still listen on 9000, as minio:9000 and seaweedfs:9000.
services:
  seaweedfs:
    ports: !override
      - "127.0.0.1:9100:9000"
```

Start it with the **same project and env file** as the running stack, so it
joins the same network and takes the same `S3_ACCESS_KEY` / `S3_SECRET_KEY`
and `S3_BUCKET` as MinIO:

```bash
docker compose --env-file <env-file> \
    -f docker-compose.yml -f docker-compose.seaweedfs-migration.yml \
    up -d seaweedfs
docker compose ps seaweedfs        # wait for (healthy)
```

Passing `-f` turns off the automatic `docker-compose.override.yml`. If you
use one, add it as another `-f`. Do not run `up` for `minio` or `db` here:
they are already running and nothing about them changes.

`weed mini` creates the `S3_BUCKET` bucket on start, and the tool creates
it if it is missing. Because SeaweedFS reads the same keys from the env
file, the tool can use the source's credentials for the destination. Set
`DEST_S3_ACCESS_KEY` / `DEST_S3_SECRET_KEY` only if yours differ.

## The tool, as a command

**API in a container.** Record the image the API runs *before* you stop it,
then run the tool from that image on the compose network. This is the same
pattern `tckdb_deploy.sh` uses for migrations, and it works whether or not
the API container is up:

```bash
IMAGE="$(docker inspect <api-container> --format '{{.Config.Image}}')"
migrate() {
    docker run --rm --network <network> --env-file <env-file> \
        -e DB_HOST=db -e DB_PORT=5432 \
        "$IMAGE" python scripts/ops/migrate_object_store.py \
        --dest-endpoint http://seaweedfs:9000 "$@"
}
```

While the API is running you can instead run
`docker exec <api-container> /usr/local/bin/_entrypoint.sh python scripts/ops/migrate_object_store.py --dest-endpoint http://seaweedfs:9000 ...`.

**API on the host.** From `backend/`, with the environment the API runs
with:

```bash
migrate() {
    DEST_S3_ENDPOINT_URL=http://127.0.0.1:9100 \
        conda run -n tckdb_env python scripts/ops/migrate_object_store.py "$@"
}
```

## Steps

### 1. Back up the database

Nothing here writes to it, but a backup should come before any storage
change. Use your usual backup (`backend/scripts/ops/tckdb_backup.sh`
restores its dump to check it), or:

```bash
docker compose exec -T db \
    pg_dump -U "${DB_USER:-tckdb}" -d "${DB_NAME:-tckdb_dev}" -Fc > tckdb-pre-seaweedfs.dump
pg_restore --list tckdb-pre-seaweedfs.dump | head     # expect a table of contents
```

`-T` is required. Without it the pseudo-TTY corrupts the binary dump
silently.

### 2. Start SeaweedFS next to MinIO

As in [Running both stores at once](#running-both-stores-at-once). The API
keeps serving from MinIO throughout.

### 3. Dry run, then copy, while the API still serves

```bash
migrate > plan.json           # dry run: nothing written
migrate --commit > copy-1.json
```

Read the dry run's stderr summary and `preflight` block first. Check that
`referenced_but_absent_from_source` is `0`. If it is not, the source is
already missing files the database points at, and copying cannot fix that.
See [When verification fails](#when-verification-fails). The first
`--commit` does the bulk of the copy while users can still read and upload.
Its closing verification may fail on files uploaded during the copy. The
next step catches those.

### 4. Stop writes

```bash
docker stop <api-container>                                  # API in a container
sudo systemctl stop tckdb-api                                # API on the host
docker compose --env-file <env-file> --profile worker stop worker   # only if you run the standalone worker
```

The inline upload worker runs inside the API process, so stopping the API
stops it too.

### 5. Copy again, to catch what was written meanwhile

```bash
migrate --commit > copy-2.json
```

Objects already copied are compared by size and SHA-256 and skipped. Only
new or changed keys are transferred. Expect `copied` to be small.

### 6. Verify, and require exit 0

```bash
migrate --verify-only > verify.json; echo "exit=$?"
```

Do not continue unless it prints `exit=0` and a `RESULT: VERIFIED: ...`
line whose row and object counts match what you expect of this deployment.
If you switch the API to an incomplete store, every download of a missing
file records an `object_missing` integrity event, and the trust layer then
hard-fails that calculation.

### 7. Switch the endpoint

**API in a container.** In `<env-file>`, set

```text
S3_ENDPOINT_URL=http://seaweedfs:9000
```

Change `S3_ACCESS_KEY` / `S3_SECRET_KEY` only if SeaweedFS was given
different keys. The compose file gives it the same ones. Leave `S3_BUCKET`
unchanged: rows record `s3://<bucket>/...`, and the tool warns if the
destination bucket's name differs.

**API on the host.** Stop MinIO, and publish SeaweedFS on 9000 by
recreating it *without* the migration override. `S3_ENDPOINT_URL` stays
`http://127.0.0.1:9000`:

```bash
docker compose --env-file <env-file> --profile minio stop minio
docker compose --env-file <env-file> up -d seaweedfs     # recreated on 127.0.0.1:9000
```

### 8. Start the API

`docker restart` does **not** re-read an env file. The container has to be
recreated. With `tckdb_deploy.sh`, re-run it with the tag already running
(`tckdb_deploy.sh --check` shows it). It backs up, finds no migration
pending, recreates the container and checks `/status`. On the host,
`sudo systemctl start tckdb-api`.

### 9. Prove the switch

```bash
curl -s http://127.0.0.1:8010/api/v1/status | jq '.components.artifact_storage'
```

Expect `"healthy": true`, and an `endpoint` that is SeaweedFS
(`http://seaweedfs:9000` for a containerised API). Then download one known
file and check its digest:

```bash
docker compose exec -T db psql -U "${DB_USER:-tckdb}" -d "${DB_NAME:-tckdb_dev}" -Atc \
    "select sha256, bytes from calculation_artifact order by id desc limit 1"
curl -fsS -H "X-API-Key: $TCKDB_API_KEY" \
    http://127.0.0.1:8010/api/v1/scientific/artifacts/<sha256>/download -o artifact.bin
sha256sum artifact.bin; stat -c %s artifact.bin       # must equal the row
```

Delete `docker-compose.seaweedfs-migration.yml` once MinIO no longer runs,
so the next `up` publishes SeaweedFS on 9000 (a containerised API does not
need the host port; a host API does).

### 10. Keep MinIO for a soak period

Leave the MinIO **container and the `tckdb_minio` volume untouched for at
least two weeks, and through at least one successful backup cycle.** Stop
MinIO if you like (a host API needs its port freed anyway), but do not
remove the container, do not run `docker compose down -v`, and do not
`docker volume rm tckdb_minio`. After the soak, remove them deliberately.

## Rolling back

Point the endpoint back at MinIO the same way you switched it:
`S3_ENDPOINT_URL=http://minio:9000` for a containerised API, or stop
SeaweedFS and start MinIO on 9000 for a host API. Then recreate or restart
the API as in step 8.

**Anything written after the cutover exists only in SeaweedFS.** Before
rolling back, stop writes, then carry it back with the same tool in the
reverse direction. The env file still names SeaweedFS as `S3_*`, so it is
the source:

```bash
docker run --rm --network <network> --env-file <env-file> -e DB_HOST=db -e DB_PORT=5432 \
    "$IMAGE" python scripts/ops/migrate_object_store.py --dest-endpoint http://minio:9000 --commit
```

Only switch back once that exits 0. Skipping it leaves every row created
after the cutover pointing at a file MinIO has never seen.

## When verification fails

Each entry in `references.missing_objects` and
`references.mismatched_objects` names the key and the table, and says
whether the **source** copy is sound:

- `"source_sound": true`: the copy lost or damaged it. Run `--commit`
  again, which recopies any key whose destination copy differs, then
  `--verify-only`.
- `"source_sound": false`: MinIO already had it missing or wrong. That is
  an existing integrity break, not a copy fault. Investigate it on the
  source with `verify_artifact_integrity.py --sha256 <digest>`. If you
  accept it, `--tolerate-source-defects` stops such entries (and only such
  entries) from failing the run. They are still listed, and the `RESULT`
  line says how many were tolerated.

`key_parity.missing_keys` lists source keys (referenced or not) that are
absent from the destination. `copy.failures` lists keys that could not be
copied, with the reason.

## Things to know

- **Object dates restart.** A copied object's `LastModified` is the time
  of the copy. The orphan reclaim (`--orphan-age-days`) and the hold purge
  (`--purge-hold-days`) in `verify_artifact_integrity.py` age objects by
  that date, so after the move they wait a full period again before acting.
  That errs toward keeping bytes.
- **An interrupted run is safe to repeat.** An object appears in the
  destination only once its upload completes, and the next run skips what
  finished. A run killed mid-upload can leave an unfinished multipart
  upload in SeaweedFS. Readers and the tool cannot see it, and it costs
  only disk space.
- **ETags are not compared.** Multipart ETags depend on the part size, so
  the same bytes can carry different ETags on two servers. The tool
  compares SHA-256 instead.
- **Verification re-reads every referenced file** from the destination,
  and each copy is read back once as it is written. Expect a full pass to
  take roughly as long as reading the bucket twice.
