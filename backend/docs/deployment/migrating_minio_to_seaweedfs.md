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
| `--commit` | Proves the two stores are different (below), then copies each object across in chunks, never whole in memory. Reads each copy back and checks it. Then verifies. | everything was copied and verified |
| `--verify-only` | Proves the two stores are different, then reads every referenced object back from the **destination** and checks its SHA-256 and byte count against the database row. Also checks every source key: a key that names a digest is hashed in the destination and must match it; any other key must be there at the same size. | every reference and every key was checked and matched |

**Which side is right is decided by the digest, never by the direction.**
Every key TCKDB writes names its own SHA-256 (`<aa>/<sha256>`,
`reclaimed/<sha256>`), and each database row names the digest of its object.
So the tool:

- never overwrites a destination object whose bytes already match that
  digest, whatever the source holds;
- never writes source bytes that do not match it. The upload is abandoned
  before the object exists, and the key is reported under
  `copy.source_defect_keys`;
- copies a key that names no digest and that no row references (such as an
  operator's stray file) by the older rule: replace it when the size or the
  SHA-256 differs. Each such key is listed under `copy.unverifiable_keys`.

That is what makes the rollback, which runs the tool backwards, safe: a
damaged SeaweedFS copy cannot overwrite MinIO's sound one.

**The same-store proof.** One store can have several addresses
(`127.0.0.1`, `localhost`, a service alias), and a store copied onto itself
skips everything and verifies perfectly. So `--commit` and `--verify-only`
first write a random probe key (`tckdb-migrate-probe/<uuid>`) to the
destination, look for it in the source bucket, and delete it. If the source
can see it, they refuse with exit 2. This probe is the only write the tool
makes outside the copy, so `--verify-only` needs write access to the
destination. A dry run writes nothing, so it cannot run the probe, and it
says so in `warnings`.

Exit `1` means something is missing, mismatched, failed to copy, or was
not copied because the source's bytes contradict their digest. Exit `2`
means nothing was verified: no database row references an object, a store
did not answer, the destination bucket does not exist, the two stores are
one, or the command was wrong. **A verify that compared nothing does not
pass**: the report always states how many rows and objects it compared
(`rows_compared`, `distinct_objects_read`). Pass `--allow-empty` only if the
deployment really has no artifacts.

The report is JSON on stdout. Progress lines and the one-line `RESULT` go
to stderr. Endpoints are printed without credentials, and secrets are never
printed.

## Before you start

- **Your MinIO volume's real name.** The compose file declares the volume
  as `tckdb_minio` with no `name:`, so Docker prefixes the compose project
  name: the volume is `<project>_tckdb_minio` (for example
  `tckdb_tckdb_minio` for a checkout in a directory called `tckdb`).
  Find yours with `docker volume ls | grep tckdb_minio`. It is written
  `<minio-volume>` below.
- **Database backups do not include the files.** `pg_dump` and
  `tckdb_backup.sh` save the database only. The stored files live only in
  the object store, so **until the soak in step 10 ends, `<minio-volume>` is
  the only other copy of every file**. If you can spare the disk, take a
  file-level copy of it before you start, while MinIO is stopped or idle:

  ```bash
  docker run --rm -v <minio-volume>:/data:ro -v "$PWD":/backup alpine \
      tar czf /backup/minio-volume-pre-seaweedfs.tgz -C /data .
  ```
- **Disk.** SeaweedFS needs room for a full second copy of the bucket while
  MinIO still holds the first. Check `<minio-volume>`'s size
  (`docker system df -v`) against the free space where Docker keeps its
  volumes.
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
with its data in `<minio-volume>` (declared as `tckdb_minio`), and runs
SeaweedFS as the `seaweedfs` service on `<project>_tckdb_seaweedfs`. **Both publish
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
change. It does **not** cover the stored files (see
[Before you start](#before-you-start)). Use your usual backup (`backend/scripts/ops/tckdb_backup.sh`
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

Objects already copied are hashed in the destination and skipped when they
match their digest. Only new or changed keys are transferred. Expect
`copied` to be small.

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
so the next `up` publishes SeaweedFS on 9000. Deleting the file does not
move a running container. With an API in a container, SeaweedFS keeps
`127.0.0.1:9100` until it is next recreated, which is harmless: that API
reaches it as `seaweedfs:9000`. A host API needs 9000, which step 7 already
gave it.

### 10. Keep MinIO for a soak period

Leave the MinIO **container and `<minio-volume>` untouched for at least two
weeks, and through at least one successful backup cycle.** Database backups
do not contain the files, so during the soak this volume is the only other
copy of every file that existed at the cutover (plus your tarball, if you
took one). Stop MinIO if you like (a host API needs its port freed anyway),
but do not remove the container, do not run `docker compose down -v`, and
do not `docker volume rm <minio-volume>`. After the soak, remove them
deliberately, but first:

**Save the source defects.** Objects whose bytes contradicted their digest
were deliberately *not* copied to SeaweedFS (they are listed in each
`--commit` report under `copy.source_defect_keys`, and in the verify report
under `references.key_parity.source_defect_keys`). Once `<minio-volume>` is
gone, those corrupt bytes, which are evidence of what went wrong, are gone
too. With MinIO running (as in [Rolling back](#rolling-back), step 2),
collect the keys from the reports you saved in steps 3, 5 and 6 and copy
each object out:

```bash
jq -r '.copy.source_defect_keys[]?.key, .references.key_parity.source_defect_keys[]?.key' \
    copy-1.json copy-2.json verify.json | sort -u > source-defects.txt
mkdir -p source-defects
while read -r key; do
    docker compose --env-file <env-file> --profile minio exec -T minio sh -c \
        'mc alias set local http://127.0.0.1:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null && mc cat "local/<bucket>/$1"' \
        sh "$key" < /dev/null > "source-defects/${key//\//_}"
done < source-defects.txt
sha256sum source-defects/*      # each hashes to the source_sha256 the report printed
```

`<bucket>` is your `S3_BUCKET`. An empty `source-defects.txt` means there
is nothing to save. Keep the tarball from
[Before you start](#before-you-start), if you took one, for as long as you
keep these.

## Rolling back

**Anything written after the cutover exists only in SeaweedFS.** So a
rollback copies those files back *first*, and switches the API back only
after that copy verifies. Doing it the other way round leaves every row
created since the cutover pointing at a file MinIO has never seen.

The copy back is the same tool run in the other direction. It is safe even
if SeaweedFS holds a damaged copy of something: it never overwrites a MinIO
object that matches its digest, and never writes bytes that contradict
theirs.

1. **Stop writes**, as in step 4.
2. **Start MinIO again** if you stopped it during the soak. It comes back
   on `<minio-volume>` with everything it held at the cutover. How depends
   on the layout, and the two must not be mixed: SeaweedFS may still hold
   `127.0.0.1:9100` from the migration override (step 9 deletes the file
   but does not move the running container), or `127.0.0.1:9000`.

   **API in a container: publish no host port at all.** The tool reaches
   MinIO as `minio:9000` on the compose network, so it needs none, and
   nothing can collide. Create `docker-compose.minio-rollback.yml`:

   ```yaml
   # Temporary, for copying post-cutover files back to MinIO (API in a container).
   services:
     minio:
       ports: !reset []
   ```

   ```bash
   docker compose --env-file <env-file> --profile minio \
       -f docker-compose.yml -f docker-compose.minio-rollback.yml up -d minio
   ```

   **API on the host: publish MinIO on 9200.** The tool runs on the host and
   needs a host port. 9200/9201 are used by nothing else in this runbook.
   Create `docker-compose.minio-rollback-host.yml`:

   ```yaml
   # Temporary, for copying post-cutover files back to MinIO (API on the host).
   services:
     minio:
       ports: !override
         - "127.0.0.1:9200:9000"
         - "127.0.0.1:9201:9001"
   ```

   ```bash
   docker compose --env-file <env-file> --profile minio \
       -f docker-compose.yml -f docker-compose.minio-rollback-host.yml up -d minio
   ```

3. **Copy back and verify.** The env file still names SeaweedFS as `S3_*`,
   so SeaweedFS is the source.

   API in a container (MinIO is `minio:9000` on the compose network):

   ```bash
   back() {
       docker run --rm --network <network> --env-file <env-file> -e DB_HOST=db -e DB_PORT=5432 \
           "$IMAGE" python scripts/ops/migrate_object_store.py --dest-endpoint http://minio:9000 "$@"
   }
   back --commit && back --verify-only; echo "exit=$?"
   ```

   API on the host (MinIO is `127.0.0.1:9200`), from `backend/`:

   ```bash
   back() {
       DEST_S3_ENDPOINT_URL=http://127.0.0.1:9200 \
           conda run -n tckdb_env python scripts/ops/migrate_object_store.py "$@"
   }
   back --commit && back --verify-only; echo "exit=$?"
   ```

   Continue only on `exit=0`.
4. **Switch the API back.** API in a container: set
   `S3_ENDPOINT_URL=http://minio:9000` and recreate the API as in step 8.
   MinIO can stay unpublished; it only needs a host port again if you want
   `mc` or the console from the host. API on the host: stop SeaweedFS, then
   recreate MinIO *without* the rollback override so it publishes
   `127.0.0.1:9000` again. The env file keeps `http://127.0.0.1:9000`:

   ```bash
   docker compose --env-file <env-file> stop seaweedfs
   docker compose --env-file <env-file> --profile minio up -d minio
   sudo systemctl start tckdb-api
   ```

   Then check `/status` and one download, as in step 9, and delete the
   rollback override file you created.

## When verification fails

Each entry in `references.missing_objects` and
`references.mismatched_objects` names the key and the table, and says
whether the **source** copy is sound:

- `"source_sound": true`: the copy lost or damaged it. Run `--commit`
  again, which recopies any key whose destination copy does not match its
  digest, then `--verify-only`.
- `"source_sound": false`: MinIO already had it missing or wrong. That is
  an existing integrity break, not a copy fault. Investigate it on the
  source with `verify_artifact_integrity.py --sha256 <digest>`. If you
  accept it, `--tolerate-source-defects` stops such entries (and only such
  entries) from failing the run. They are still listed, and the `RESULT`
  line says how many were tolerated.

`key_parity.failure_keys` lists source keys (referenced or not) that are
missing or wrong in the destination. `key_parity.source_defect_keys` and
`copy.source_defect_keys` list keys the tool refused to copy because the
source's bytes contradict their digest. `copy.failures` lists keys that
could not be copied, with the reason.

`--tolerate-source-defects` forgives a break only when the source was
already wrong **and** the destination is no different: byte-identical to
the source's copy, or absent because the tool refused to copy it. A
destination that differs from a defective source still fails.

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
- **Verification re-reads every file whose key names a digest** from the
  destination (in practice, all of them), and each copy is read back once
  as it is written. Expect a full pass to take roughly as long as reading
  the bucket twice. A rerun hashes each destination object again to decide
  whether it can be skipped.
- **A killed run can leave a probe key.** If the tool is killed between
  writing its same-store probe and deleting it, a
  `tckdb-migrate-probe/<uuid>` object stays in the destination. It is
  harmless and safe to delete.
