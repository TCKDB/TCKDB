#!/usr/bin/env python
"""Copy every stored object from one S3-compatible store to another, and prove it.

Written to move an existing deployment from MinIO to SeaweedFS (#541), but
nothing here is specific to either: source and destination are any two
S3-compatible stores, and the runbook's rollback runs it the other way. The
operator runbook is ``backend/docs/deployment/migrating_minio_to_seaweedfs.md``.

Why a copy is the whole migration. Every database reference to a stored
object is a bucket and a key, never an endpoint: ``calculation_artifact.uri``
and ``external_source_record.raw_uri`` are written as ``s3://<bucket>/<key>``,
and the read path does not even parse them -- it rebuilds the key from the
row's digest (``content_addressed_key``) and reads the configured
``S3_BUCKET`` at the configured ``S3_ENDPOINT_URL``. So once the bytes are at
the same keys in a bucket of the same name, pointing ``S3_ENDPOINT_URL`` at
the new server is the entire cutover. No database row changes, and this tool
never writes to the database.

Usage (inside the API image, where ``S3_*`` already name the source)::

    # 1. Plan: list what would be copied. Writes nothing anywhere.
    python scripts/ops/migrate_object_store.py --dest-endpoint http://seaweedfs:9000

    # 2. Copy, then verify. Safe to repeat: sound objects are skipped.
    python scripts/ops/migrate_object_store.py --dest-endpoint http://seaweedfs:9000 --commit

    # 3. Verify only: re-read every object from the destination.
    python scripts/ops/migrate_object_store.py --dest-endpoint http://seaweedfs:9000 --verify-only

The destination's credentials and bucket come from ``--dest-*`` flags or
``DEST_S3_ENDPOINT_URL`` / ``DEST_S3_ACCESS_KEY`` / ``DEST_S3_SECRET_KEY`` /
``DEST_S3_BUCKET`` / ``DEST_S3_REGION``; any left unset is taken from the
source, which is right for the compose file, where SeaweedFS and MinIO read
the same ``S3_ACCESS_KEY`` / ``S3_SECRET_KEY``. These are inputs to this tool
only, not application settings. Secrets are never printed.

**Which side is right is decided by the digest, not by the direction.**
Every key TCKDB writes names its own SHA-256 (``<aa>/<sha256>`` and
``reclaimed/<sha256>``), and a database row names the digest of the object
it references. That digest is the authority:

* a destination object whose bytes already hash to it is **never
  overwritten**, whatever the source holds;
* a source object whose bytes do not hash to it is **never written**: the
  copy aborts inside the upload, before the destination is touched, and the
  key is reported as a source defect;
* only a key that names no digest and that no row references is copied on
  the older rule (replace when size or SHA-256 differs), and each such key
  is listed as unverifiable.

This is what makes running the tool backwards (the rollback) safe: a store
whose copy was damaged cannot damage the other one's sound copy.

What each mode does:

* **dry run** (default): lists both buckets and reports, per source key,
  whether it would be copied (absent from the destination, or a different
  size) or is already present at the same size (digests are compared on
  ``--commit``). Also reads the database's references and says how many are
  absent from the *source*. Writes nothing anywhere, so it cannot run the
  same-store probe below; it says so.
* ``--commit``: first proves source and destination are different stores
  (below), then copies every source key under the rules above, streamed
  (bounded memory, multipart for large objects), creating the destination
  bucket if needed, and reads each copy back. Then verifies.
* ``--verify-only``: the same-store proof, then the verification alone.

**The same-store proof.** An endpoint can have many names (``127.0.0.1``,
``localhost``, a service alias), so comparing URLs cannot tell that the
"destination" is the source; copying a store onto itself skips everything
and verifies perfectly. So ``--commit`` and ``--verify-only`` write one
random probe key (``tckdb-migrate-probe/<uuid>``) to the destination, look
for it in the source bucket, and delete it from the destination. If the
source can see it, they refuse. This probe is the only write to the
destination outside the copy itself, and it needs write access to the
destination even in ``--verify-only``.

Verification, and what a pass has to mean. Every database row that
references a stored object is read back **from the destination**, hashed and
measured, and compared with the row's digest and byte count. Every source key
must then be present in the destination: keys that name a digest are hashed
and must match it; any other key must be present at the same size. A run that
compared no references is not a pass: it exits 2 unless ``--allow-empty``
says the deployment really has none. The report states how many rows and
objects were compared, so a reader can see that something was.

A failure is looked up in the source too. ``source_sound: true`` means the
copy lost or damaged it. ``false`` means the source was already wrong: a
pre-existing break. ``--tolerate-source-defects`` forgives a pre-existing
break only where the destination is no different from the source's own state:
byte-identical to it, or absent because the tool refused to carry bytes that
contradict their digest.

Exit status: ``0`` verified; ``1`` something is missing, mismatched, failed
to copy, or was not copied because the source is defective; ``2`` nothing was
verified (no references, a store that did not answer, a missing destination
bucket, source and destination being one store, or a bad invocation).

The source is only ever read: no call here writes, deletes or modifies an
object in it. The database is opened read-only, at the connection level, so
not even an integrity event can be written.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from boto3.s3.transfer import TransferConfig  # noqa: E402
from botocore.config import Config as BotoConfig  # noqa: E402
from botocore.exceptions import BotoCoreError, ClientError  # noqa: E402

from app.services import artifact_storage  # noqa: E402
from app.services.artifact_storage import (  # noqa: E402
    _MISSING_OBJECT_CODES,
    RECLAIM_HOLD_PREFIX,
    _error_code,
    build_s3_client,
    content_addressed_key,
)

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_NOT_VERIFIED = 2

#: Read and write in pieces this size. Artifacts are capped at 50 MB on
#: upload, but nothing stops an operator's bucket holding larger objects, so
#: nothing here may hold a whole object in memory.
CHUNK_BYTES = 8 * 1024 * 1024

#: Multipart above one chunk, and at most four chunks buffered while
#: uploading from a non-seekable stream: ~32 MiB resident, whatever the
#: object's size. ``max_in_memory_upload_chunks`` is what bounds a
#: streamed upload; its default (10) would allow ~80 MiB. boto3's
#: ``TransferConfig`` does not take it as an argument, but it is the
#: s3transfer attribute the upload manager reads, so it is set after.
TRANSFER = TransferConfig(
    multipart_threshold=CHUNK_BYTES,
    multipart_chunksize=CHUNK_BYTES,
    max_concurrency=4,
)
TRANSFER.max_in_memory_upload_chunks = 4

#: How many individual problems a report lists before it only counts them.
LIST_LIMIT = 200

#: Where the same-store probe is written, in the destination only.
PROBE_PREFIX = "tckdb-migrate-probe/"

_BUCKET_MISSING_CODES = frozenset({"404", "NoSuchBucket", "NotFound"})


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StoreConfig:
    """One S3 store: where it is, which bucket, and how to sign.

    Both keys are excluded from ``repr`` and from :meth:`describe`, so a
    traceback or a report can never carry the secret.
    """

    endpoint_url: str
    bucket: str
    region: str
    access_key: str = field(repr=False)
    secret_key: str = field(repr=False)

    def describe(self) -> dict[str, str]:
        return {
            "endpoint": sanitized_endpoint(self.endpoint_url),
            "bucket": self.bucket,
            "region": self.region,
        }

    def client(self):
        return build_s3_client(
            endpoint_url=self.endpoint_url,
            access_key=self.access_key,
            secret_key=self.secret_key,
            region=self.region,
            config=BotoConfig(retries={"max_attempts": 5, "mode": "standard"}),
        )


def sanitized_endpoint(raw: str) -> str:
    """``scheme://host:port`` only: no userinfo, path or query.

    The same reduction ``/api/v1/status`` applies, for the same reason: a
    credential pasted into an endpoint URL must not reach a report.
    """
    try:
        parts = urlsplit(raw)
    except ValueError:
        return "(unparseable)"
    if not parts.scheme or not parts.hostname:
        return "(unset)" if not raw else "(unparseable)"
    netloc = parts.hostname
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    return f"{parts.scheme}://{netloc}"


class UsageError(Exception):
    """The invocation cannot be run as given; exits 2."""


def resolve_stores(args: argparse.Namespace, env: dict[str, str]) -> tuple[StoreConfig, StoreConfig, bool]:
    """Source from ``--source-*`` or the app's ``S3_*``; destination from
    ``--dest-*`` or ``DEST_S3_*``, each unset value falling back to the
    source's.

    Returns ``(source, dest, dest_credentials_inherited)``. The destination
    endpoint has no fallback. Identical spellings are refused here; other
    spellings of one store are caught by :func:`assert_distinct_stores`.
    """
    source = StoreConfig(
        endpoint_url=args.source_endpoint or artifact_storage.S3_ENDPOINT_URL,
        bucket=args.source_bucket or artifact_storage.S3_BUCKET,
        region=args.source_region or artifact_storage.S3_REGION,
        access_key=args.source_access_key or artifact_storage.S3_ACCESS_KEY,
        secret_key=args.source_secret_key or artifact_storage.S3_SECRET_KEY,
    )
    endpoint = args.dest_endpoint or env.get("DEST_S3_ENDPOINT_URL")
    if not endpoint:
        raise UsageError(
            "no destination: pass --dest-endpoint or set DEST_S3_ENDPOINT_URL "
            "(e.g. http://seaweedfs:9000 inside the compose network)"
        )
    access_key = args.dest_access_key or env.get("DEST_S3_ACCESS_KEY")
    secret_key = args.dest_secret_key or env.get("DEST_S3_SECRET_KEY")
    inherited = not access_key and not secret_key
    dest = StoreConfig(
        endpoint_url=endpoint,
        bucket=args.dest_bucket or env.get("DEST_S3_BUCKET") or source.bucket,
        region=args.dest_region or env.get("DEST_S3_REGION") or source.region,
        access_key=access_key or source.access_key,
        secret_key=secret_key or source.secret_key,
    )
    if (
        sanitized_endpoint(dest.endpoint_url) == sanitized_endpoint(source.endpoint_url)
        and dest.bucket == source.bucket
    ):
        raise UsageError(
            "source and destination are the same bucket on the same endpoint; "
            "every object would be 'skipped' and verification would compare "
            "the store with itself"
        )
    return source, dest, inherited


# ---------------------------------------------------------------------------
# Listing, reading, and the same-store probe
# ---------------------------------------------------------------------------


def list_objects(client, bucket: str) -> dict[str, int]:
    """Every key in ``bucket`` with its size, across all pages.

    No prefix: the reclaim hold (``reclaimed/<digest>``) and objects no row
    references are part of the store and are copied like everything else.
    """
    listing: dict[str, int] = {}
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket):
        for obj in page.get("Contents", []):
            listing[str(obj["Key"])] = int(obj["Size"])
    return listing


def bucket_exists(client, bucket: str) -> bool:
    try:
        client.head_bucket(Bucket=bucket)
    except ClientError as exc:
        if _error_code(exc) in _BUCKET_MISSING_CODES:
            return False
        raise
    return True


def ensure_bucket(client, bucket: str) -> bool:
    """Create ``bucket`` if it is missing. Returns whether it was created."""
    if bucket_exists(client, bucket):
        return False
    client.create_bucket(Bucket=bucket)
    return True


class ObjectMissing(Exception):
    """The store answered, and the key is not there."""


def stream_digest(client, bucket: str, key: str) -> tuple[str, int]:
    """SHA-256 and length of one object, read in chunks, never whole."""
    try:
        response = client.get_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        if _error_code(exc) in _MISSING_OBJECT_CODES:
            raise ObjectMissing(key) from exc
        raise
    digest = hashlib.sha256()
    length = 0
    for chunk in response["Body"].iter_chunks(chunk_size=CHUNK_BYTES):
        digest.update(chunk)
        length += len(chunk)
    return digest.hexdigest(), length


def observe(client, bucket: str, key: str) -> tuple[str, int] | None:
    """``(sha256, bytes)`` of an object, or ``None`` if it is not there."""
    try:
        return stream_digest(client, bucket, key)
    except ObjectMissing:
        return None


class SameStore(UsageError):
    """Source and destination turned out to be one store under two names."""


def assert_distinct_stores(src_client, source: StoreConfig, dst_client, dest: StoreConfig) -> None:
    """Refuse when the destination is the source under another name.

    Writes a random probe key to the **destination**, asks the **source**
    whether it can see it, and deletes it from the destination. Nothing is
    ever written to the source through the source's client; if the probe is
    visible there, the only write that reached the source was this probe,
    and it is deleted before the refusal. The destination bucket must exist.
    """
    key = f"{PROBE_PREFIX}{uuid.uuid4().hex}"
    dst_client.put_object(
        Bucket=dest.bucket,
        Key=key,
        Body=b"migrate_object_store.py same-store probe; safe to delete\n",
    )
    try:
        try:
            src_client.head_object(Bucket=source.bucket, Key=key)
            visible = True
        except ClientError as exc:
            if _error_code(exc) not in _MISSING_OBJECT_CODES | _BUCKET_MISSING_CODES:
                raise
            visible = False
    finally:
        dst_client.delete_object(Bucket=dest.bucket, Key=key)
    if visible:
        raise SameStore(
            f"the destination {dest.describe()} is the source {source.describe()} under "
            "another name: a probe written to the destination is visible in the source. "
            "Copying a store onto itself would skip everything and verify nothing."
        )


# ---------------------------------------------------------------------------
# The copy
# ---------------------------------------------------------------------------


def digest_named_by_key(key: str) -> str | None:
    """The SHA-256 a key claims its bytes have, if it is one of ours.

    ``<aa>/<digest>`` (content-addressed) and ``reclaimed/<digest>`` (the
    hold) both name their digest; any other key names nothing.
    """
    tail = key.rsplit("/", 1)[-1]
    if len(tail) != 64 or any(c not in "0123456789abcdef" for c in tail):
        return None
    if key == content_addressed_key(tail) or key == f"{RECLAIM_HOLD_PREFIX}{tail}":
        return tail
    return None


class SourceDefect(Exception):
    """The source's bytes contradict the digest their key or row names."""

    def __init__(self, key: str, expected: str, observed: str, observed_bytes: int) -> None:
        super().__init__(
            f"{key}: source bytes hash to {observed} ({observed_bytes} bytes), "
            f"not the {expected} the key or its database row names"
        )
        self.key = key
        self.expected = expected
        self.observed = observed
        self.observed_bytes = observed_bytes


class SourceVanished(Exception):
    """A source key that was listed and is now gone (a concurrent reclaim)."""


class CopyMismatch(Exception):
    """A copy that did not read back as what was sent."""


class HashingReader:
    """A read-only stream over an S3 body that hashes what passes through.

    Deliberately has no ``seek``/``tell``: that is what makes boto3's
    transfer manager treat it as a non-seekable stream and read it
    sequentially in bounded chunks, rather than trying to size or rewind it.

    Given ``expected_sha256``, it raises :class:`SourceDefect` from the read
    that delivers the last byte, **before** handing that byte to the
    uploader. A single-part upload has then not been sent, and a multipart
    upload has not been completed (the transfer manager aborts it), so bytes
    that contradict their digest never become an object in the destination.
    """

    def __init__(self, body, *, key: str = "", expected_sha256: str | None = None,
                 expected_length: int | None = None) -> None:
        self._body = body
        self._sha = hashlib.sha256()
        self.bytes_read = 0
        self._key = key
        self._expected = expected_sha256
        self._length = expected_length
        self._checked = False

    def read(self, amt: int | None = -1) -> bytes:
        data = self._body.read(amt if amt is not None and amt >= 0 else None)
        self._sha.update(data)
        self.bytes_read += len(data)
        at_end = not data or (self._length is not None and self.bytes_read >= self._length)
        if self._expected is not None and at_end and not self._checked:
            self._checked = True
            if self.hexdigest() != self._expected:
                raise SourceDefect(self._key, self._expected, self.hexdigest(), self.bytes_read)
        return data

    def readable(self) -> bool:
        return True

    def hexdigest(self) -> str:
        return self._sha.hexdigest()


def copy_one(
    src_client,
    source: StoreConfig,
    dst_client,
    dest: StoreConfig,
    key: str,
    *,
    expected_sha256: str | None = None,
    dest_present: bool = False,
) -> tuple[str, int]:
    """Stream one object across and read it back. Returns (digest, bytes).

    Raises :class:`SourceVanished` if the source key is gone,
    :class:`SourceDefect` if ``expected_sha256`` is given and the source
    bytes do not match it (nothing is written), and :class:`CopyMismatch`
    if the destination does not read back as what was sent -- including a
    destination that has no object at all after the upload.
    """
    try:
        response = src_client.get_object(Bucket=source.bucket, Key=key)
    except ClientError as exc:
        if _error_code(exc) in _MISSING_OBJECT_CODES:
            raise SourceVanished(key) from exc
        raise
    length = response.get("ContentLength")
    reader = HashingReader(
        response["Body"],
        key=key,
        expected_sha256=expected_sha256,
        expected_length=int(length) if length is not None else None,
    )
    extra: dict[str, Any] = {
        "ContentType": response.get("ContentType") or "application/octet-stream"
    }
    if response.get("Metadata"):
        extra["Metadata"] = response["Metadata"]
    dst_client.upload_fileobj(reader, dest.bucket, key, ExtraArgs=extra, Config=TRANSFER)
    sent_digest, sent_bytes = reader.hexdigest(), reader.bytes_read
    if expected_sha256 is not None and sent_digest != expected_sha256:
        # The reader should have refused before the upload finished; if a
        # transfer path ever read past it, undo what this call created
        # rather than leave bad bytes at a key that was empty.
        if not dest_present:
            dst_client.delete_object(Bucket=dest.bucket, Key=key)
        raise SourceDefect(key, expected_sha256, sent_digest, sent_bytes)
    if length is not None and int(length) != sent_bytes:
        raise CopyMismatch(f"source announced {length} bytes but {sent_bytes} were read")
    try:
        got_digest, got_bytes = stream_digest(dst_client, dest.bucket, key)
    except ObjectMissing:
        raise CopyMismatch("the destination has no object at this key after the upload") from None
    if (got_digest, got_bytes) != (sent_digest, sent_bytes):
        raise CopyMismatch(
            f"destination reads back sha256={got_digest} bytes={got_bytes}, "
            f"sent sha256={sent_digest} bytes={sent_bytes}"
        )
    return sent_digest, sent_bytes


#: Plan outcomes for one source key, decided from the two listings alone.
COPY = "copy"
COMPARE = "compare"


def plan_key(source_size: int, dest_size: int | None) -> str:
    """What a key needs, from sizes alone (the dry run's plan).

    Absent or a different size: it must be copied. The same size is not
    enough to skip -- two different contents can share a length -- so it is
    ``compare``: the digests decide, at commit time.
    """
    if dest_size is None or dest_size != source_size:
        return COPY
    return COMPARE


def should_skip(
    *, source_digest: str, source_size: int, dest_digest: str, dest_size: int
) -> bool:
    """For a key that names no digest: skip only on the same size **and**
    the same SHA-256.

    ETags are not compared: a multipart upload's ETag is a digest of part
    digests, so the same bytes carry different ETags on two servers (or
    with two part sizes), and a single-part ETag is only an MD5.
    """
    return source_size == dest_size and source_digest == dest_digest


@dataclass
class CopyReport:
    listed: int = 0
    listed_bytes: int = 0
    would_copy: int = 0
    would_copy_bytes: int = 0
    present_same_size: int = 0
    copied: int = 0
    copied_bytes: int = 0
    skipped: int = 0
    skipped_bytes: int = 0
    failed: list[dict[str, str]] = field(default_factory=list)
    vanished_from_source: list[str] = field(default_factory=list)
    dest_bucket_created: bool = False
    #: Keys **not written** because the source bytes contradict the digest
    #: the key or its row names. The destination keeps what it had.
    source_defects: list[dict[str, Any]] = field(default_factory=list)
    #: Keys that name no digest and that no row references, copied on size
    #: and SHA-256 comparison alone: nothing says which side is right.
    unverifiable: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "listed": self.listed,
            "listed_bytes": self.listed_bytes,
            "would_copy": self.would_copy,
            "would_copy_bytes": self.would_copy_bytes,
            "present_same_size": self.present_same_size,
            "copied": self.copied,
            "copied_bytes": self.copied_bytes,
            "skipped": self.skipped,
            "skipped_bytes": self.skipped_bytes,
            "failed": len(self.failed),
            "failures": self.failed[:LIST_LIMIT],
            "vanished_from_source": _capped(self.vanished_from_source),
            "dest_bucket_created": self.dest_bucket_created,
            "source_defects": len(self.source_defects),
            "source_defect_keys": self.source_defects[:LIST_LIMIT],
            "unverifiable": len(self.unverifiable),
            "unverifiable_keys": _capped(self.unverifiable),
        }


def _source_digest(src_client, bucket: str, key: str) -> tuple[str, int]:
    try:
        return stream_digest(src_client, bucket, key)
    except ObjectMissing:
        raise SourceVanished(key) from None


def run_copy(
    source: StoreConfig,
    dest: StoreConfig,
    *,
    commit: bool,
    row_digests: dict[str, str] | None = None,
    src_client=None,
    dst_client=None,
    log: Callable[[str], None] = print,
) -> tuple[CopyReport, dict[str, int]]:
    """Plan (and with ``commit``, perform) the copy. Returns the report and
    the source listing it worked from.

    ``row_digests`` maps a key to the digest the database says it holds; it
    only matters for a key whose name carries no digest.
    """
    src_client = src_client or source.client()
    dst_client = dst_client or dest.client()
    row_digests = row_digests or {}
    report = CopyReport()

    source_listing = list_objects(src_client, source.bucket)
    report.listed = len(source_listing)
    report.listed_bytes = sum(source_listing.values())

    if bucket_exists(dst_client, dest.bucket):
        dest_listing = list_objects(dst_client, dest.bucket)
    else:
        dest_listing = {}
        if commit:
            report.dest_bucket_created = ensure_bucket(dst_client, dest.bucket)
            log(f"created destination bucket {dest.bucket!r}")
        else:
            log(f"destination bucket {dest.bucket!r} does not exist; --commit creates it")

    for key in sorted(source_listing):
        size = source_listing[key]
        if not commit:
            if plan_key(size, dest_listing.get(key)) == COPY:
                report.would_copy += 1
                report.would_copy_bytes += size
                log(f"would copy {key} ({size} bytes)")
            else:
                report.present_same_size += 1
            continue
        expected = digest_named_by_key(key) or row_digests.get(key)
        try:
            dest_present = key in dest_listing
            # Why an existing destination object is being replaced, logged
            # only once the write has actually happened: a refused copy
            # (a source defect) must not read as a replacement.
            replaces: str | None = None
            if expected is not None:
                if dest_present:
                    held = observe(dst_client, dest.bucket, key)
                    if held is not None and held[0] == expected:
                        # Sound already: never overwritten, whatever the
                        # source holds. This is the rollback's guarantee.
                        report.skipped += 1
                        report.skipped_bytes += held[1]
                        continue
                    if held is not None:
                        replaces = "the destination's bytes did not match its digest"
                    dest_present = held is not None
            else:
                report.unverifiable.append(key)
                if dest_present and plan_key(size, dest_listing[key]) == COMPARE:
                    source_digest, source_size = _source_digest(src_client, source.bucket, key)
                    held = observe(dst_client, dest.bucket, key)
                    if held is not None and should_skip(
                        source_digest=source_digest,
                        source_size=source_size,
                        dest_digest=held[0],
                        dest_size=held[1],
                    ):
                        report.skipped += 1
                        report.skipped_bytes += size
                        continue
                if dest_present:
                    replaces = "names no digest; the destination's bytes differed from the source's"
            _, sent = copy_one(
                src_client, source, dst_client, dest, key,
                expected_sha256=expected, dest_present=dest_present,
            )
            report.copied += 1
            report.copied_bytes += sent
            log(f"copied {key} ({sent} bytes)" + (f"; replaced: {replaces}" if replaces else ""))
        except SourceVanished:
            # Listed, then gone: the reclaim sweep can move an object while
            # the API is still serving. Not a copy failure; the final pass,
            # run with writes stopped, lists afresh.
            report.vanished_from_source.append(key)
            log(f"vanished from source after listing: {key}")
        except SourceDefect as exc:
            report.source_defects.append(
                {
                    "key": key,
                    "expected_sha256": exc.expected,
                    "source_sha256": exc.observed,
                    "source_bytes": exc.observed_bytes,
                }
            )
            log(f"NOT COPIED {exc}")
        except (ClientError, BotoCoreError, CopyMismatch) as exc:
            reason = f"{type(exc).__name__}: {exc}"
            report.failed.append({"key": key, "reason": reason})
            log(f"FAILED {key}: {reason}")
    return report, source_listing


# ---------------------------------------------------------------------------
# References: every database row that names a stored object
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Expectation:
    """What the database says one object must be.

    One per distinct ``(table, key, sha256, bytes)``: rows that share a
    content-addressed object share an expectation, and rows that disagree
    about the same object each get one, so the disagreement surfaces.
    """

    table: str
    key: str
    sha256: str
    expected_bytes: int
    rows: int


@dataclass
class ReferenceScan:
    expectations: list[Expectation] = field(default_factory=list)
    rows: dict[str, int] = field(default_factory=dict)
    #: Rows whose reference is not an ``s3://`` URI at all, by table.
    #: ``external_source_record.raw_uri`` legitimately holds an archive
    #: member path when the store was down at import time.
    not_object_references: dict[str, int] = field(default_factory=dict)
    not_object_reference_examples: list[str] = field(default_factory=list)
    #: ``external_source_record`` rows whose ``s3://`` URI names a bucket
    #: other than the one being migrated. They do reference an object, just
    #: not one this run moves, so they are a warning rather than a skip.
    other_bucket_references: list[str] = field(default_factory=list)
    #: ``calculation_artifact`` rows whose URI names a different bucket or
    #: key than the one the read path actually uses.
    uri_disagreements: list[str] = field(default_factory=list)
    uri_buckets: set[str] = field(default_factory=set)

    def row_digests(self) -> dict[str, str]:
        """Key -> the digest rows name for it, where they agree."""
        seen: dict[str, set[str]] = {}
        for e in self.expectations:
            seen.setdefault(e.key, set()).add(e.sha256)
        return {key: next(iter(digests)) for key, digests in seen.items() if len(digests) == 1}


def parse_s3_uri(uri: str) -> tuple[str, str] | None:
    """``s3://bucket/key`` -> ``(bucket, key)``; anything else -> ``None``."""
    if not uri.startswith("s3://"):
        return None
    bucket, _, key = uri[len("s3://"):].partition("/")
    if not bucket or not key:
        return None
    return bucket, key


def build_expectations(
    artifact_rows: Iterable[tuple[str, str, int]],
    custody_rows: Iterable[tuple[str, str, int]],
    *,
    bucket: str,
) -> ReferenceScan:
    """Turn raw rows into what verification must check. Pure.

    ``artifact_rows`` are ``(uri, sha256, bytes)`` from
    ``calculation_artifact``. The object that matters is the one the app
    reads -- ``content_addressed_key(sha256)`` -- whatever the URI says; a
    URI that names another key or bucket is reported, not followed.

    ``custody_rows`` are ``(raw_uri, content_sha256, content_length)`` from
    ``external_source_record``. There the URI *is* the reference (nothing
    rebuilds it), so its key is what is checked.
    """
    scan = ReferenceScan()
    counts: dict[tuple[str, str, str, int], int] = {}

    artifact_total = 0
    for uri, sha256, nbytes in artifact_rows:
        artifact_total += 1
        key = content_addressed_key(sha256)
        parsed = parse_s3_uri(uri)
        if parsed is None:
            scan.uri_disagreements.append(uri)
        else:
            scan.uri_buckets.add(parsed[0])
            if parsed != (bucket, key):
                scan.uri_disagreements.append(uri)
        ident = ("calculation_artifact", key, sha256, int(nbytes))
        counts[ident] = counts.get(ident, 0) + 1
    scan.rows["calculation_artifact"] = artifact_total

    custody_total = 0
    for raw_uri, sha256, nbytes in custody_rows:
        custody_total += 1
        parsed = parse_s3_uri(raw_uri)
        if parsed is None:
            scan.not_object_references["external_source_record"] = (
                scan.not_object_references.get("external_source_record", 0) + 1
            )
            if len(scan.not_object_reference_examples) < 20:
                scan.not_object_reference_examples.append(raw_uri)
            continue
        if parsed[0] != bucket:
            scan.other_bucket_references.append(raw_uri)
            continue
        ident = ("external_source_record", parsed[1], sha256, int(nbytes))
        counts[ident] = counts.get(ident, 0) + 1
    scan.rows["external_source_record"] = custody_total

    scan.expectations = [
        Expectation(table=t, key=k, sha256=s, expected_bytes=b, rows=n)
        for (t, k, s, b), n in sorted(counts.items())
    ]
    return scan


def read_references(session, *, bucket: str) -> ReferenceScan:
    """Every row that names a stored object, found by reading the code.

    Two tables do: ``calculation_artifact`` (written by
    ``artifact_persistence`` and archive restore, both through
    ``store_artifact``) and ``external_source_record.raw_uri`` (ThermoML
    import, also through ``store_artifact``). ``release_artifact`` holds its
    bytes in the database, and ``artifact_integrity_event`` records findings
    about digests rather than references to objects; neither is here.
    """
    from sqlalchemy import select

    from app.db.models.calculation import CalculationArtifact
    from app.db.models.external_source import ExternalSourceRecord

    artifact_rows = session.execute(
        select(CalculationArtifact.uri, CalculationArtifact.sha256, CalculationArtifact.bytes)
    ).tuples()
    custody_rows = session.execute(
        select(
            ExternalSourceRecord.raw_uri,
            ExternalSourceRecord.content_sha256,
            ExternalSourceRecord.content_length,
        )
    ).tuples()
    return build_expectations(artifact_rows, custody_rows, bucket=bucket)


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


MISSING = "missing"
MISMATCHED = "mismatched"


def compare_expectation(expectation: Expectation, observed: tuple[str, int] | None) -> str | None:
    """``None`` when the object is what the row says; otherwise why not. Pure."""
    if observed is None:
        return MISSING
    digest, length = observed
    if digest != expectation.sha256 or length != expectation.expected_bytes:
        return MISMATCHED
    return None


def tolerable(source_sound: bool, dest: tuple[str, int] | None, src: tuple[str, int] | None) -> bool:
    """May ``--tolerate-source-defects`` forgive this failure? Pure.

    Only when the source was already wrong **and** the destination is no
    different from it: byte-identical (or both absent), or absent because
    the copy refused to carry bytes that contradict their digest. A
    destination that differs from a defective source is a copy problem of
    its own and is never forgiven.
    """
    if source_sound:
        return False
    return dest is None or dest == src


class MissingBucket(Exception):
    """The destination bucket does not exist."""


class _Observer:
    """Reads each object at most once per store, and counts what it read."""

    def __init__(self, client, bucket: str) -> None:
        self._client, self._bucket = client, bucket
        self.cache: dict[str, tuple[str, int] | None] = {}

    def __call__(self, key: str) -> tuple[str, int] | None:
        if key not in self.cache:
            self.cache[key] = observe(self._client, self._bucket, key)
        return self.cache[key]


def verify(
    scan: ReferenceScan,
    source: StoreConfig,
    dest: StoreConfig,
    *,
    src_client=None,
    dst_client=None,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Read every referenced object, and every digest-named key, back from
    the destination and compare.

    Two claims, reported apart:

    ``references``
        each database row's object, from the destination, has the row's
        SHA-256 and byte count;
    ``key_parity``
        every source key is in the destination -- hashed against the
        digest its name carries where it carries one, else at the same size.

    Every failure is looked up in the source as well, so the report can tell
    a copy fault (``source_sound: true``) from a break the source already had.
    """
    src_client = src_client or source.client()
    dst_client = dst_client or dest.client()
    source_listing = list_objects(src_client, source.bucket)
    if not bucket_exists(dst_client, dest.bucket):
        raise MissingBucket(dest.bucket)
    dest_listing = list_objects(dst_client, dest.bucket)
    in_dest = _Observer(dst_client, dest.bucket)
    in_source = _Observer(src_client, source.bucket)

    problems: dict[str, list[dict[str, Any]]] = {MISSING: [], MISMATCHED: []}
    verified_rows = 0
    referenced_keys: set[str] = set()
    for expectation in scan.expectations:
        referenced_keys.add(expectation.key)
        held = in_dest(expectation.key)
        outcome = compare_expectation(expectation, held)
        if outcome is None:
            verified_rows += expectation.rows
            continue
        original = in_source(expectation.key)
        source_sound = compare_expectation(expectation, original) is None
        entry = {
            "table": expectation.table,
            "key": expectation.key,
            "rows": expectation.rows,
            "expected_sha256": expectation.sha256,
            "expected_bytes": expectation.expected_bytes,
            "dest_sha256": held[0] if held else None,
            "dest_bytes": held[1] if held else None,
            "source_sound": source_sound,
            "tolerable": tolerable(source_sound, held, original),
        }
        problems[outcome].append(entry)
        log(
            f"{outcome.upper()} {expectation.key} ({expectation.table}, "
            f"{expectation.rows} row(s)); source copy "
            f"{'sound' if source_sound else 'ALSO BAD'}"
        )

    parity, parity_defect_keys = _key_parity(source_listing, dest_listing, in_dest, in_source, log)
    ref_entries = problems[MISSING] + problems[MISMATCHED]
    defect_objects = {p["key"] for p in ref_entries if p["tolerable"]} | parity_defect_keys
    return {
        "rows": dict(scan.rows),
        "rows_compared": sum(e.rows for e in scan.expectations),
        "rows_verified": verified_rows,
        "distinct_objects_read": len(referenced_keys),
        "objects_verified": len(referenced_keys - {p["key"] for p in ref_entries}),
        "dest_objects_hashed": len(in_dest.cache),
        "missing": len(problems[MISSING]),
        "missing_objects": problems[MISSING][:LIST_LIMIT],
        "mismatched": len(problems[MISMATCHED]),
        "mismatched_objects": problems[MISMATCHED][:LIST_LIMIT],
        "untolerable": sum(1 for p in ref_entries if not p["tolerable"]),
        "preexisting_source_defects": sum(1 for p in ref_entries if p["tolerable"]),
        #: Distinct objects the source already had wrong, across both checks.
        "source_defect_objects": len(defect_objects),
        "not_object_references": dict(scan.not_object_references),
        "not_object_reference_examples": scan.not_object_reference_examples,
        "other_bucket_references": len(scan.other_bucket_references),
        "other_bucket_reference_examples": scan.other_bucket_references[:20],
        "uri_disagreements": len(scan.uri_disagreements),
        "uri_disagreement_examples": scan.uri_disagreements[:20],
        "uri_buckets": sorted(scan.uri_buckets),
        "key_parity": parity,
    }


def _key_parity(source_listing, dest_listing, in_dest, in_source, log) -> tuple[dict[str, Any], set[str]]:
    failures: list[dict[str, Any]] = []
    defects: list[dict[str, Any]] = []
    digest_verified = same_size = 0
    for key in sorted(source_listing):
        named = digest_named_by_key(key)
        if named is None:
            if key not in dest_listing:
                failures.append({"key": key, "problem": MISSING, "source_sound": None})
            elif dest_listing[key] != source_listing[key]:
                failures.append({"key": key, "problem": "size_mismatch", "source_sound": None})
            else:
                same_size += 1
            continue
        held = in_dest(key)
        if held is not None and held[0] == named:
            digest_verified += 1
            continue
        original = in_source(key)
        source_sound = original is not None and original[0] == named
        entry = {
            "key": key,
            "problem": MISSING if held is None else MISMATCHED,
            "dest_sha256": held[0] if held else None,
            "source_sha256": original[0] if original else None,
            "source_sound": source_sound,
        }
        if tolerable(source_sound, held, original):
            defects.append(entry)
            log(f"SOURCE DEFECT {key}: the source's bytes do not match the digest its key names")
        else:
            failures.append(entry)
            log(f"{entry['problem'].upper()} {key} (key parity)")
    return {
        "source_keys": len(source_listing),
        "digest_verified": digest_verified,
        "present_same_size": same_size,
        "failures": len(failures),
        "failure_keys": failures[:LIST_LIMIT],
        "source_defects": len(defects),
        "source_defect_keys": defects[:LIST_LIMIT],
        "dest_only": len(set(dest_listing) - set(source_listing)),
    }, {d["key"] for d in defects}


# ---------------------------------------------------------------------------
# The verdict
# ---------------------------------------------------------------------------


def verdict(
    report: dict[str, Any], *, allow_empty: bool, tolerate_source_defects: bool
) -> tuple[int, str]:
    """Exit code and one-line result from a finished report. Pure.

    Order matters: a failure outranks "nothing checked", because a run that
    found a missing object has learnt something whatever else it skipped.
    """
    copy = report.get("copy") or {}
    refs = report.get("references")
    problems: list[str] = []
    if copy.get("failed"):
        problems.append(f"{copy['failed']} object(s) failed to copy")
    if refs is not None:
        bad_refs = refs["untolerable"]
        if not tolerate_source_defects:
            bad_refs += refs["preexisting_source_defects"]
        if bad_refs:
            problems.append(
                f"{refs['missing']} referenced object(s) missing and "
                f"{refs['mismatched']} mismatched in the destination"
            )
        parity = refs["key_parity"]
        bad_keys = parity["failures"]
        if not tolerate_source_defects:
            bad_keys += parity["source_defects"]
        if bad_keys:
            problems.append(
                f"{parity['failures']} source key(s) missing or wrong in the destination and "
                f"{parity['source_defects']} whose source bytes contradict their digest"
            )
    elif copy.get("source_defects") and not tolerate_source_defects:
        problems.append(f"{copy['source_defects']} source object(s) contradict their digest")
    if problems:
        return EXIT_FAILED, "FAILED: " + "; ".join(problems)
    if refs is None:
        return EXIT_OK, "PLANNED (dry run): nothing was written and nothing verified"
    if refs["rows_compared"] == 0 and not allow_empty:
        return EXIT_NOT_VERIFIED, (
            "NOT VERIFIED: no database row references a stored object, so "
            "nothing was compared. Pass --allow-empty if this deployment "
            "really has none."
        )
    parity = refs["key_parity"]
    tolerated = refs["source_defect_objects"]
    note = (
        f"; {tolerated} pre-existing source defect(s) tolerated, see "
        "references.*_objects and key_parity.source_defect_keys"
        if tolerated
        else ""
    )
    return EXIT_OK, (
        f"VERIFIED: {refs['rows_verified']} row(s) over "
        f"{refs['distinct_objects_read']} distinct object(s) read back from the "
        f"destination and matched; {parity['digest_verified']} of "
        f"{parity['source_keys']} source key(s) hashed and matching their digest, "
        f"{parity['present_same_size']} more (naming no digest) present at the same size"
        f"{note}"
    )


def _capped(items: list[str]) -> list[str]:
    return items[:LIST_LIMIT]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def read_only_connection(engine=None):
    """A connection to the app's database on which every transaction is
    ``READ ONLY`` at the server.

    So a write -- even an integrity event -- is refused by PostgreSQL rather
    than merely not attempted. Set as a session characteristic and committed
    before any work, because the connection may already be inside a
    transaction by the time it is handed over, and ``SET TRANSACTION`` (or
    the driver's ``read_only`` flag) cannot change the one in progress.
    Close it with :func:`discard_connection`, never back into the pool.
    """
    if engine is None:
        from app.api.deps import engine
    connection = engine.connect()
    connection.exec_driver_sql("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
    connection.commit()
    return connection


def discard_connection(connection) -> None:
    """Drop a read-only connection instead of pooling it for a writer."""
    connection.invalidate()
    connection.close()


def run(
    args: argparse.Namespace,
    *,
    session=None,
    env: dict[str, str] | None = None,
    log: Callable[[str], None] | None = None,
) -> tuple[int, dict[str, Any]]:
    """Do what the arguments ask and return ``(exit_code, report)``.

    ``session`` is injectable for tests; production opens a read-only one.
    Raises :class:`UsageError` (including :class:`SameStore`) for an
    invocation that must not run.
    """
    log = log or (lambda message: print(message, file=sys.stderr, flush=True))
    source, dest, inherited = resolve_stores(args, dict(os.environ) if env is None else env)
    mode = "verify_only" if args.verify_only else ("commit" if args.commit else "dry_run")
    report: dict[str, Any] = {
        "mode": mode,
        "source": source.describe(),
        "destination": {**dest.describe(), "credentials": "same as source" if inherited else "DEST_S3_* / --dest-*"},
        "warnings": [],
    }
    if dest.bucket != source.bucket:
        report["warnings"].append(
            f"destination bucket {dest.bucket!r} differs from source bucket "
            f"{source.bucket!r}: rows record s3://{source.bucket}/... URIs, so "
            "after the switch they would name a bucket the app no longer reads"
        )
    log(f"source {source.describe()} -> destination {dest.describe()} ({mode})")

    src_client, dst_client = source.client(), dest.client()
    owned = None
    try:
        if session is None:
            from sqlalchemy.orm import Session

            owned = read_only_connection()
            session = Session(bind=owned)
        scan = read_references(session, bucket=source.bucket)
        for bucket in sorted(scan.uri_buckets - {source.bucket}):
            report["warnings"].append(
                f"calculation_artifact URIs name bucket {bucket!r}, not the source "
                f"bucket {source.bucket!r}; the app reads S3_BUCKET regardless"
            )
        if scan.other_bucket_references:
            report["warnings"].append(
                f"{len(scan.other_bucket_references)} external_source_record row(s) name an "
                f"s3:// object outside the source bucket {source.bucket!r}; this run does not "
                "move or verify them (see references.other_bucket_reference_examples)"
            )

        if mode == "dry_run":
            report["warnings"].append(
                "the same-store probe was not run: a dry run writes nothing, and the probe "
                "is a write to the destination. --commit and --verify-only run it first."
            )
        elif mode == "commit":
            report["dest_bucket_created"] = ensure_bucket(dst_client, dest.bucket)
            assert_distinct_stores(src_client, source, dst_client, dest)
        else:
            if not bucket_exists(dst_client, dest.bucket):
                report["result"] = (
                    f"NOT VERIFIED: the destination bucket {dest.bucket!r} does not exist at "
                    f"{sanitized_endpoint(dest.endpoint_url)}; nothing was compared"
                )
                return EXIT_NOT_VERIFIED, report
            assert_distinct_stores(src_client, source, dst_client, dest)

        if mode != "verify_only":
            copy_report, source_listing = run_copy(
                source, dest, commit=mode == "commit", row_digests=scan.row_digests(),
                src_client=src_client, dst_client=dst_client, log=log,
            )
            report["copy"] = copy_report.as_dict()
            if report.get("dest_bucket_created"):
                report["copy"]["dest_bucket_created"] = True
            log(
                f"source: {copy_report.listed} object(s), {copy_report.listed_bytes} bytes; "
                + (
                    f"would copy {copy_report.would_copy} ({copy_report.would_copy_bytes} bytes), "
                    f"{copy_report.present_same_size} already present at the same size"
                    if mode == "dry_run"
                    else f"copied {copy_report.copied}, skipped {copy_report.skipped}, "
                    f"not copied (source defect) {len(copy_report.source_defects)}, "
                    f"failed {len(copy_report.failed)}"
                )
            )

        if mode == "dry_run":
            listing = source_listing
            absent = sorted({e.key for e in scan.expectations if e.key not in listing})
            report["preflight"] = {
                "rows": scan.rows,
                "distinct_referenced_objects": len({e.key for e in scan.expectations}),
                "referenced_but_absent_from_source": len(absent),
                "absent_examples": absent[:20],
                "not_object_references": scan.not_object_references,
                "other_bucket_references": len(scan.other_bucket_references),
            }
            if absent:
                report["warnings"].append(
                    f"{len(absent)} referenced object(s) are absent from the source; "
                    "no copy can put them in the destination, so --verify-only will "
                    "fail on them (see preflight.absent_examples)"
                )
            log(
                f"database: {sum(e.rows for e in scan.expectations)} row(s) reference "
                f"{report['preflight']['distinct_referenced_objects']} distinct object(s), "
                f"{len(absent)} of them absent from the source; "
                f"{sum(scan.not_object_references.values())} row(s) hold no object reference"
            )
        else:
            refs = verify(scan, source, dest, src_client=src_client, dst_client=dst_client, log=log)
            report["references"] = refs
            parity = refs["key_parity"]
            log(
                f"verified {refs['rows_verified']} of {refs['rows_compared']} referencing row(s) "
                f"over {refs['distinct_objects_read']} distinct object(s) read from the destination; "
                f"missing {refs['missing']}, mismatched {refs['mismatched']}; source keys: "
                f"{parity['digest_verified']} hashed and matching, {parity['present_same_size']} "
                f"at the same size, {parity['failures']} failing, {parity['source_defects']} "
                f"source defect(s), of {parity['source_keys']}"
            )
    except MissingBucket as exc:
        report["result"] = (
            f"NOT VERIFIED: the destination bucket {exc} does not exist at "
            f"{sanitized_endpoint(dest.endpoint_url)}; nothing was compared"
        )
        return EXIT_NOT_VERIFIED, report
    except (ClientError, BotoCoreError) as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["result"] = f"NOT VERIFIED: a store did not answer ({type(exc).__name__})"
        return EXIT_NOT_VERIFIED, report
    finally:
        if owned is not None:
            session.close()
            discard_connection(owned)

    code, result = verdict(
        report,
        allow_empty=args.allow_empty,
        tolerate_source_defects=args.tolerate_source_defects,
    )
    report["result"] = result
    return code, report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--commit", action="store_true", help="copy, then verify (default: dry run)")
    mode.add_argument("--verify-only", action="store_true", help="verify the destination; copy nothing")
    for side, default in (("source", "S3_*"), ("dest", "DEST_S3_*, else the source's")):
        parser.add_argument(f"--{side}-endpoint", help=f"S3 endpoint URL (default: {default})")
        parser.add_argument(f"--{side}-bucket", help=f"bucket (default: {default})")
        parser.add_argument(f"--{side}-region", help=f"region (default: {default})")
        parser.add_argument(f"--{side}-access-key", help=f"access key (default: {default})")
        parser.add_argument(
            f"--{side}-secret-key",
            help=f"secret key (default: {default}); prefer the environment variable, "
            "which does not appear in the process list",
        )
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help="accept a database with no object references (exit 0 instead of 2)",
    )
    parser.add_argument(
        "--tolerate-source-defects",
        action="store_true",
        help=(
            "do not fail on an object that is wrong in the SOURCE (its bytes contradict "
            "the digest its key or row names) where the destination is identical to the "
            "source's copy or holds nothing because the tool refused to copy it; each is "
            "still listed. A destination that differs from the source is never tolerated."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        code, report = run(args)
    except UsageError as exc:
        print(f"RESULT: NOT VERIFIED. {exc}", file=sys.stderr)
        return EXIT_NOT_VERIFIED
    except KeyboardInterrupt:
        # Nothing to undo: an object becomes visible in the destination only
        # once its upload completes, and the next run skips what did.
        print("RESULT: INTERRUPTED. Run the same command again to resume.", file=sys.stderr)
        return EXIT_NOT_VERIFIED
    print(json.dumps(report, indent=2, sort_keys=True))
    print(f"RESULT: {report['result']}", file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
