#!/usr/bin/env python
"""Copy every stored object from one S3-compatible store to another, and prove it.

Written to move an existing deployment from MinIO to SeaweedFS (#541), but
nothing here is specific to either: source and destination are any two
S3-compatible stores. The operator runbook is
``backend/docs/deployment/migrating_minio_to_seaweedfs.md``.

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

    # 2. Copy, then verify. Safe to repeat: identical objects are skipped.
    python scripts/ops/migrate_object_store.py --dest-endpoint http://seaweedfs:9000 --commit

    # 3. Verify only: re-read every referenced object from the destination.
    python scripts/ops/migrate_object_store.py --dest-endpoint http://seaweedfs:9000 --verify-only

The destination's credentials and bucket come from ``--dest-*`` flags or
``DEST_S3_ENDPOINT_URL`` / ``DEST_S3_ACCESS_KEY`` / ``DEST_S3_SECRET_KEY`` /
``DEST_S3_BUCKET`` / ``DEST_S3_REGION``; any left unset is taken from the
source, which is right for the compose file, where SeaweedFS and MinIO read
the same ``S3_ACCESS_KEY`` / ``S3_SECRET_KEY``. These are inputs to this tool
only, not application settings. Secrets are never printed.

What each mode does:

* **dry run** (default): lists both buckets and reports, per source key,
  whether it would be copied (absent from the destination, or a different
  size) or is already present at the same size (its digest is compared on
  ``--commit``). Also reads the database's references and says how many are
  absent from the *source* -- a copy cannot fix those. Writes nothing.
* ``--commit``: copies every source key to the same key in the destination,
  streamed (bounded memory, multipart for large objects), creating the
  destination bucket if needed. A key is skipped only when the destination
  already holds the same size **and** the same SHA-256 -- not the same ETag,
  which differs across servers for multipart uploads. Each copy is re-read
  from the destination and its digest compared with the bytes streamed from
  the source; a copy that does not read back identically is a failure. Then
  runs the verification below.
* ``--verify-only``: the verification alone.

Verification, and what a pass has to mean. Every database row that
references a stored object is read back **from the destination**, hashed and
measured, and compared with the row's digest and byte count; and every
source key must exist in the destination at the same size. A run that
compared no references is not a pass: it exits 2 unless ``--allow-empty``
says the deployment really has none. The report states how many rows and
distinct objects were compared, so a reader can see that something was.

Exit status: ``0`` verified; ``1`` something is missing, mismatched or
failed to copy; ``2`` nothing was verified (no references, a store that did
not answer, or a bad invocation).

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


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StoreConfig:
    """One S3 store: where it is, which bucket, and how to sign.

    ``secret_key`` is excluded from ``repr`` and from :meth:`describe`, so a
    traceback or a report can never carry it.
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


def resolve_stores(args: argparse.Namespace, env: dict[str, str]) -> tuple[StoreConfig, StoreConfig, bool]:
    """Source from ``--source-*`` or the app's ``S3_*``; destination from
    ``--dest-*`` or ``DEST_S3_*``, each unset value falling back to the
    source's.

    Returns ``(source, dest, dest_credentials_inherited)``. The destination
    endpoint has no fallback: copying a store onto itself is the one
    default that could never be right.
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


class UsageError(Exception):
    """The invocation cannot be run as given; exits 2."""


# ---------------------------------------------------------------------------
# Listing and reading
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
        if _error_code(exc) in {"404", "NoSuchBucket", "NotFound"}:
            return False
        raise
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


class HashingReader:
    """A read-only stream over an S3 body that hashes what passes through.

    Deliberately has no ``seek``/``tell``: that is what makes boto3's
    transfer manager treat it as a non-seekable stream and read it
    sequentially in bounded chunks, rather than trying to size or rewind it.
    """

    def __init__(self, body) -> None:
        self._body = body
        self._sha = hashlib.sha256()
        self.bytes_read = 0

    def read(self, amt: int | None = -1) -> bytes:
        data = self._body.read(amt if amt is not None and amt >= 0 else None)
        self._sha.update(data)
        self.bytes_read += len(data)
        return data

    def readable(self) -> bool:
        return True

    def hexdigest(self) -> str:
        return self._sha.hexdigest()


# ---------------------------------------------------------------------------
# The copy
# ---------------------------------------------------------------------------


#: Plan outcomes for one source key, decided from the two listings alone.
COPY = "copy"
COMPARE = "compare"


def plan_key(source_size: int, dest_size: int | None) -> str:
    """What a key needs, from sizes alone.

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
    """Skip only on the same size **and** the same SHA-256.

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
    #: Source objects whose bytes do not hash to the digest their key
    #: names. Copied faithfully -- the copy must not repair or hide
    #: evidence -- and reported, because verification will fail on them.
    source_digest_mismatch: list[str] = field(default_factory=list)

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
            "source_digest_mismatch": _capped(self.source_digest_mismatch),
        }


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


def copy_one(src_client, source: StoreConfig, dst_client, dest: StoreConfig, key: str) -> tuple[str, int]:
    """Stream one object across and read it back. Returns (digest, bytes).

    Raises :class:`CopyMismatch` when the destination does not read back
    as the bytes that were sent, and :class:`ObjectMissing` when the source
    key disappeared after it was listed.
    """
    try:
        response = src_client.get_object(Bucket=source.bucket, Key=key)
    except ClientError as exc:
        if _error_code(exc) in _MISSING_OBJECT_CODES:
            raise ObjectMissing(key) from exc
        raise
    reader = HashingReader(response["Body"])
    extra: dict[str, Any] = {
        "ContentType": response.get("ContentType") or "application/octet-stream"
    }
    if response.get("Metadata"):
        extra["Metadata"] = response["Metadata"]
    dst_client.upload_fileobj(reader, dest.bucket, key, ExtraArgs=extra, Config=TRANSFER)
    sent_digest, sent_bytes = reader.hexdigest(), reader.bytes_read
    expected_length = response.get("ContentLength")
    if expected_length is not None and int(expected_length) != sent_bytes:
        raise CopyMismatch(
            f"source announced {expected_length} bytes but {sent_bytes} were read"
        )
    got_digest, got_bytes = stream_digest(dst_client, dest.bucket, key)
    if (got_digest, got_bytes) != (sent_digest, sent_bytes):
        raise CopyMismatch(
            f"destination reads back sha256={got_digest} bytes={got_bytes}, "
            f"sent sha256={sent_digest} bytes={sent_bytes}"
        )
    return sent_digest, sent_bytes


class CopyMismatch(Exception):
    """A copy that did not read back as what was sent."""


def run_copy(
    source: StoreConfig,
    dest: StoreConfig,
    *,
    commit: bool,
    src_client=None,
    dst_client=None,
    log: Callable[[str], None] = print,
) -> tuple[CopyReport, dict[str, int]]:
    """Plan (and with ``commit``, perform) the copy. Returns the report and
    the source listing it worked from."""
    src_client = src_client or source.client()
    dst_client = dst_client or dest.client()
    report = CopyReport()

    source_listing = list_objects(src_client, source.bucket)
    report.listed = len(source_listing)
    report.listed_bytes = sum(source_listing.values())

    if bucket_exists(dst_client, dest.bucket):
        dest_listing = list_objects(dst_client, dest.bucket)
    else:
        dest_listing = {}
        if commit:
            dst_client.create_bucket(Bucket=dest.bucket)
            report.dest_bucket_created = True
            log(f"created destination bucket {dest.bucket!r}")
        else:
            log(f"destination bucket {dest.bucket!r} does not exist; --commit creates it")

    for key in sorted(source_listing):
        size = source_listing[key]
        plan = plan_key(size, dest_listing.get(key))
        if not commit:
            if plan == COPY:
                report.would_copy += 1
                report.would_copy_bytes += size
                log(f"would copy {key} ({size} bytes)")
            else:
                report.present_same_size += 1
            continue
        try:
            if plan == COMPARE:
                source_digest, source_size = stream_digest(src_client, source.bucket, key)
                dest_digest, dest_size = stream_digest(dst_client, dest.bucket, key)
                if should_skip(
                    source_digest=source_digest,
                    source_size=source_size,
                    dest_digest=dest_digest,
                    dest_size=dest_size,
                ):
                    report.skipped += 1
                    report.skipped_bytes += size
                    _note_source_digest(report, key, source_digest)
                    continue
                log(f"replacing {key}: same size, different sha256 in the destination")
            digest, sent = copy_one(src_client, source, dst_client, dest, key)
            report.copied += 1
            report.copied_bytes += sent
            _note_source_digest(report, key, digest)
            log(f"copied {key} ({sent} bytes)")
        except ObjectMissing:
            # Listed, then gone: the reclaim sweep can move an object while
            # the API is still serving. Not a copy failure; the final pass,
            # run with writes stopped, lists afresh.
            report.vanished_from_source.append(key)
            log(f"vanished from source after listing: {key}")
        except (ClientError, BotoCoreError, CopyMismatch) as exc:
            reason = f"{type(exc).__name__}: {exc}"
            report.failed.append({"key": key, "reason": reason})
            log(f"FAILED {key}: {reason}")
    return report, source_listing


def _note_source_digest(report: CopyReport, key: str, digest: str) -> None:
    named = digest_named_by_key(key)
    if named is not None and named != digest:
        report.source_digest_mismatch.append(key)


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
    #: Rows whose reference is not an ``s3://`` URI into the bucket being
    #: migrated, by table. ``external_source_record.raw_uri`` legitimately
    #: holds an archive member path when the store was down at import time.
    not_object_references: dict[str, int] = field(default_factory=dict)
    not_object_reference_examples: list[str] = field(default_factory=list)
    #: ``calculation_artifact`` rows whose URI names a different bucket or
    #: key than the one the read path actually uses.
    uri_disagreements: list[str] = field(default_factory=list)
    uri_buckets: set[str] = field(default_factory=set)


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
        if parsed is None or parsed[0] != bucket:
            scan.not_object_references["external_source_record"] = (
                scan.not_object_references.get("external_source_record", 0) + 1
            )
            if len(scan.not_object_reference_examples) < 20:
                scan.not_object_reference_examples.append(raw_uri)
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


def key_parity(source_listing: dict[str, int], dest_listing: dict[str, int]) -> dict[str, Any]:
    """Every source key present in the destination at the same size. Pure."""
    missing = sorted(k for k in source_listing if k not in dest_listing)
    size_mismatch = sorted(
        k for k in source_listing if k in dest_listing and dest_listing[k] != source_listing[k]
    )
    return {
        "source_keys": len(source_listing),
        "present_same_size": len(source_listing) - len(missing) - len(size_mismatch),
        "missing": len(missing),
        "missing_keys": _capped(missing),
        "size_mismatch": len(size_mismatch),
        "size_mismatch_keys": _capped(size_mismatch),
        "dest_only": len(set(dest_listing) - set(source_listing)),
    }


def _observe(client, bucket: str, key: str) -> tuple[str, int] | None:
    try:
        return stream_digest(client, bucket, key)
    except ObjectMissing:
        return None


def verify(
    scan: ReferenceScan,
    source: StoreConfig,
    dest: StoreConfig,
    *,
    src_client=None,
    dst_client=None,
    source_listing: dict[str, int] | None = None,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Read every referenced object from the destination and compare.

    A failure is then looked up in the **source** too, so the report can
    say whether the copy lost or damaged it (``source_sound: true``) or the
    source was already that way (``false``) -- a pre-existing break the
    copy faithfully reproduced, which is an integrity finding to chase with
    ``verify_artifact_integrity.py`` rather than a migration failure.
    """
    src_client = src_client or source.client()
    dst_client = dst_client or dest.client()
    if source_listing is None:
        source_listing = list_objects(src_client, source.bucket)
    dest_listing = (
        list_objects(dst_client, dest.bucket) if bucket_exists(dst_client, dest.bucket) else {}
    )

    observed_cache: dict[str, tuple[str, int] | None] = {}
    problems: dict[str, list[dict[str, Any]]] = {MISSING: [], MISMATCHED: []}
    verified_rows = 0
    for expectation in scan.expectations:
        if expectation.key not in observed_cache:
            observed_cache[expectation.key] = _observe(dst_client, dest.bucket, expectation.key)
        outcome = compare_expectation(expectation, observed_cache[expectation.key])
        if outcome is None:
            verified_rows += expectation.rows
            continue
        observed = observed_cache[expectation.key]
        in_source = _observe(src_client, source.bucket, expectation.key)
        entry = {
            "table": expectation.table,
            "key": expectation.key,
            "rows": expectation.rows,
            "expected_sha256": expectation.sha256,
            "expected_bytes": expectation.expected_bytes,
            "dest_sha256": observed[0] if observed else None,
            "dest_bytes": observed[1] if observed else None,
            "source_sound": compare_expectation(expectation, in_source) is None,
        }
        problems[outcome].append(entry)
        log(
            f"{outcome.upper()} {expectation.key} ({expectation.table}, "
            f"{expectation.rows} row(s)); source copy "
            f"{'sound' if entry['source_sound'] else 'ALSO BAD'}"
        )

    total_rows = sum(e.rows for e in scan.expectations)
    failing_keys = {p["key"] for kind in problems.values() for p in kind}
    preexisting = sum(1 for kind in problems.values() for p in kind if not p["source_sound"])
    return {
        "rows": dict(scan.rows),
        "rows_compared": total_rows,
        "rows_verified": verified_rows,
        "distinct_objects_read": len(observed_cache),
        "objects_verified": len(set(observed_cache) - failing_keys),
        "missing": len(problems[MISSING]),
        "missing_objects": problems[MISSING][:LIST_LIMIT],
        "mismatched": len(problems[MISMATCHED]),
        "mismatched_objects": problems[MISMATCHED][:LIST_LIMIT],
        "preexisting_source_defects": preexisting,
        "not_object_references": dict(scan.not_object_references),
        "not_object_reference_examples": scan.not_object_reference_examples,
        "uri_disagreements": len(scan.uri_disagreements),
        "uri_disagreement_examples": scan.uri_disagreements[:20],
        "uri_buckets": sorted(scan.uri_buckets),
        "key_parity": key_parity(source_listing, dest_listing),
    }


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
        counted = refs["missing"] + refs["mismatched"]
        if tolerate_source_defects:
            # Forgiven only where the destination reproduces a break the
            # source already had; a copy that lost or damaged a sound
            # object still fails.
            counted -= refs["preexisting_source_defects"]
        if counted:
            problems.append(
                f"{refs['missing']} referenced object(s) missing and "
                f"{refs['mismatched']} mismatched in the destination"
            )
        parity = refs["key_parity"]
        if parity["missing"] or parity["size_mismatch"]:
            problems.append(
                f"{parity['missing']} source key(s) absent from the destination and "
                f"{parity['size_mismatch']} at a different size"
            )
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
    tolerated = (
        f"; {refs['preexisting_source_defects']} pre-existing source defect(s) "
        "tolerated, see missing_objects/mismatched_objects"
        if refs["preexisting_source_defects"]
        else ""
    )
    return EXIT_OK, (
        f"VERIFIED: {refs['rows_verified']} row(s) over "
        f"{refs['distinct_objects_read']} distinct object(s) read back from the "
        f"destination and matched; {parity['present_same_size']} of "
        f"{parity['source_keys']} source key(s) present at the same size"
        f"{tolerated}"
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
        source_listing = None
        if mode != "verify_only":
            copy_report, source_listing = run_copy(
                source, dest, commit=mode == "commit",
                src_client=src_client, dst_client=dst_client, log=log,
            )
            report["copy"] = copy_report.as_dict()
            log(
                f"source: {copy_report.listed} object(s), {copy_report.listed_bytes} bytes; "
                + (
                    f"would copy {copy_report.would_copy} ({copy_report.would_copy_bytes} bytes), "
                    f"{copy_report.present_same_size} already present at the same size"
                    if mode == "dry_run"
                    else f"copied {copy_report.copied}, skipped {copy_report.skipped}, "
                    f"failed {len(copy_report.failed)}"
                )
            )

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

        if mode == "dry_run":
            listing = source_listing or {}
            absent = sorted({e.key for e in scan.expectations if e.key not in listing})
            report["preflight"] = {
                "rows": scan.rows,
                "distinct_referenced_objects": len({e.key for e in scan.expectations}),
                "referenced_but_absent_from_source": len(absent),
                "absent_examples": absent[:20],
                "not_object_references": scan.not_object_references,
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
            refs = verify(
                scan, source, dest,
                src_client=src_client, dst_client=dst_client,
                source_listing=None,
                log=log,
            )
            report["references"] = refs
            log(
                f"verified {refs['rows_verified']} of {refs['rows_compared']} referencing row(s) "
                f"over {refs['distinct_objects_read']} distinct object(s) read from the destination; "
                f"missing {refs['missing']}, mismatched {refs['mismatched']}; "
                f"source keys present in destination at the same size: "
                f"{refs['key_parity']['present_same_size']} of {refs['key_parity']['source_keys']}"
            )
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
            "do not fail on a referenced object that is missing or wrong in the "
            "destination exactly because it is missing or wrong in the source; "
            "each is still listed with source_sound=false"
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
