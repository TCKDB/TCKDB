"""Moving a deployment's stored objects to another S3 store (#541).

The tool's claim is "the destination now holds everything the database
points at, byte for byte", and the way that claim fails in this repository
is not a wrong answer but a confident one over nothing: a verify that
compared no rows, or a skip that trusted a size. So most of what follows is
about the negative space -- a corrupted or missing object must go red, a
run with no references must not be a pass, and the source must never be
written to.

The second theme is direction. The runbook's rollback runs the tool
backwards, so "the source" may be the store whose copy is damaged. The
digest a key (or its row) names decides which side is right: a sound
destination object is never overwritten, and bytes that contradict their
digest are never written anywhere.

The pure logic runs against an in-memory fake store. One test runs the whole
copy, both directions, against a real S3 server (the one the suite is
configured for, as source and as destination in two scratch buckets, or a
second server named by ``TCKDB_MIGRATE_TEST_DEST_*``) and then downloads
through the API with the app pointed at the destination. It skips locally
when no store answers and fails on CI, where one is always started.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError
from sqlalchemy.exc import DBAPIError

from app.db.models.common import ExternalSourceRecordKind
from app.db.models.external_source import ExternalSource, ExternalSourceRecord
from app.services import artifact_storage
from app.services.artifact_storage import content_addressed_key
from tests.services._live_object_store import store_description, store_server_header, unavailable
from tests.services.scientific_read._factories import (
    attach_artifact,
    make_calculation,
    make_species,
    make_species_entry,
)

_TOOL = Path(__file__).parents[2] / "scripts" / "ops" / "migrate_object_store.py"


def _load_tool():
    """Import the script by path, registered first so its dataclasses resolve."""
    spec = importlib.util.spec_from_file_location("migrate_object_store", _TOOL)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


tool = _load_tool()

BUCKET = "tckdb-artifacts"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _ca(data: bytes) -> str:
    return content_addressed_key(_sha(data))


def _flip(data: bytes, at: int = 0) -> bytes:
    damaged = bytearray(data)
    damaged[at] ^= 0xFF
    return bytes(damaged)


# ---------------------------------------------------------------------------
# A fake S3 store: enough of boto3's surface, and a log of every write
# ---------------------------------------------------------------------------


class _FakeBody:
    def __init__(self, data: bytes) -> None:
        self._buf = io.BytesIO(data)
        self.largest_read = 0

    def read(self, amt=None) -> bytes:
        data = self._buf.read() if amt is None else self._buf.read(amt)
        self.largest_read = max(self.largest_read, len(data))
        return data

    def iter_chunks(self, chunk_size):
        while True:
            chunk = self.read(chunk_size)
            if not chunk:
                return
            yield chunk


def _client_error(code: str, op: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, op)


class FakeS3:
    """In-memory buckets. ``writes`` records every mutating call.

    Like a real transfer manager, ``upload_fileobj`` reads the whole stream
    before anything becomes an object, so an exception raised by the reader
    leaves the destination untouched.
    """

    def __init__(self, buckets: dict[str, dict[str, bytes]] | None = None, *, page_size: int = 2):
        self.buckets = {name: dict(objs) for name, objs in (buckets or {}).items()}
        self.page_size = page_size
        self.writes: list[tuple[str, str]] = []
        self.bodies: list[_FakeBody] = []
        #: Applied to bytes on upload, to model a store that damages them.
        self.on_upload = lambda data: data
        #: Keys that list, but answer 404 to a GET.
        self.unreadable: set[str] = set()

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return self

    def paginate(self, Bucket):
        if Bucket not in self.buckets:
            raise _client_error("NoSuchBucket", "ListObjectsV2")
        keys = sorted(self.buckets[Bucket])
        if not keys:
            yield {}
        for i in range(0, len(keys), self.page_size):
            yield {
                "Contents": [
                    {"Key": k, "Size": len(self.buckets[Bucket][k])}
                    for k in keys[i:i + self.page_size]
                ]
            }

    def head_bucket(self, Bucket):
        if Bucket not in self.buckets:
            raise _client_error("404", "HeadBucket")

    def create_bucket(self, Bucket):
        self.writes.append(("create_bucket", Bucket))
        self.buckets[Bucket] = {}

    def head_object(self, Bucket, Key):
        if Bucket not in self.buckets or Key not in self.buckets[Bucket]:
            raise _client_error("404", "HeadObject")
        return {"ContentLength": len(self.buckets[Bucket][Key])}

    def put_object(self, Bucket, Key, Body):
        self.writes.append(("put_object", Key))
        self.buckets[Bucket][Key] = Body

    def delete_object(self, Bucket, Key):
        self.writes.append(("delete_object", Key))
        self.buckets[Bucket].pop(Key, None)

    def get_object(self, Bucket, Key):
        if Key in self.unreadable:
            raise _client_error("NoSuchKey", "GetObject")
        try:
            data = self.buckets[Bucket][Key]
        except KeyError:
            raise _client_error("NoSuchKey", "GetObject") from None
        body = _FakeBody(data)
        self.bodies.append(body)
        return {
            "Body": body,
            "ContentLength": len(data),
            "ContentType": "application/octet-stream",
            "Metadata": {},
        }

    def upload_fileobj(self, fileobj, Bucket, Key, ExtraArgs=None, Config=None):
        self.writes.append(("upload_fileobj", Key))
        chunks = []
        while True:
            chunk = fileobj.read(Config.multipart_chunksize)
            if not chunk:
                break
            chunks.append(chunk)
        self.buckets[Bucket][Key] = self.on_upload(b"".join(chunks))


def _source_objects() -> dict[str, bytes]:
    """The shapes a real bucket holds: artifacts, the reclaim hold, orphans."""
    a, b, orphan, held = b"gaussian log A\n" * 50, b"orca log B\n" * 70, b"orphan\n", b"held\n"
    return {
        _ca(a): a,
        _ca(b): b,
        _ca(orphan): orphan,
        f"reclaimed/{_sha(held)}": held,
        "stray/notes.txt": b"operator notes\n",
    }


def _referenced(objects):
    return {k: v for k, v in objects.items() if tool.digest_named_by_key(k) and not k.startswith("reclaimed/")}


def _stores(source_objects=None, dest_objects=None):
    source = FakeS3({BUCKET: source_objects if source_objects is not None else _source_objects()})
    dest = FakeS3({BUCKET: dest_objects} if dest_objects is not None else {})
    return source, dest


def _configs(dest_bucket: str = BUCKET):
    src = tool.StoreConfig("http://minio:9000", BUCKET, "us-east-1", "src-key", "SRC-SECRET-541")
    dst = tool.StoreConfig("http://seaweedfs:9000", dest_bucket, "us-east-1", "dst-key", "DST-SECRET-541")
    return src, dst


def _copy(source, dest, *, commit=True, row_digests=None, log=None):
    src_cfg, dst_cfg = _configs()
    return tool.run_copy(
        src_cfg, dst_cfg, commit=commit, row_digests=row_digests,
        src_client=source, dst_client=dest, log=log or (lambda _m: None),
    )


# ---------------------------------------------------------------------------
# Listing, planning, skipping
# ---------------------------------------------------------------------------


def test_listing_walks_every_page_and_every_prefix() -> None:
    source, _ = _stores()
    listing = tool.list_objects(source, BUCKET)
    assert set(listing) == set(_source_objects())
    assert any(k.startswith("reclaimed/") for k in listing)
    assert "stray/notes.txt" in listing
    # page_size=2 over five keys: three pages had to be read to get here.
    assert len(listing) == 5


@pytest.mark.parametrize(
    ("source_size", "dest_size", "expected"),
    [(10, None, "copy"), (10, 9, "copy"), (10, 10, "compare")],
)
def test_the_plan_never_skips_on_size_alone(source_size, dest_size, expected) -> None:
    assert tool.plan_key(source_size, dest_size) == expected


def test_skip_needs_the_same_size_and_the_same_digest() -> None:
    same = {"source_digest": "a" * 64, "source_size": 5, "dest_digest": "a" * 64, "dest_size": 5}
    assert tool.should_skip(**same) is True
    assert tool.should_skip(**{**same, "dest_digest": "b" * 64}) is False
    assert tool.should_skip(**{**same, "dest_size": 6}) is False


def test_a_dry_run_writes_nothing_and_counts_what_it_would_copy() -> None:
    source, dest = _stores()
    report, _ = _copy(source, dest, commit=False)
    assert source.writes == [] and dest.writes == []
    assert dest.buckets == {}
    assert report.would_copy == 5
    assert report.would_copy_bytes == sum(len(v) for v in _source_objects().values())


def test_commit_copies_every_key_then_a_rerun_skips_them_all() -> None:
    source, dest = _stores()
    first, _ = _copy(source, dest)
    assert (first.copied, first.skipped, first.failed) == (5, 0, [])
    assert first.dest_bucket_created is True
    assert dest.buckets[BUCKET] == _source_objects()
    assert first.unverifiable == ["stray/notes.txt"]

    second, _ = _copy(source, dest)
    assert (second.copied, second.skipped, second.failed) == (0, 5, [])
    assert source.writes == []


def test_a_same_size_destination_object_with_wrong_bytes_is_replaced() -> None:
    objects = _source_objects()
    key = next(iter(_referenced(objects)))
    source, dest = _stores(dest_objects={**objects, key: _flip(objects[key])})

    logged: list[str] = []
    report, _ = _copy(source, dest, log=logged.append)
    assert report.copied == 1 and report.skipped == 4
    assert dest.buckets[BUCKET][key] == objects[key]
    assert source.writes == []
    assert [m for m in logged if "replaced" in m] == [
        f"copied {key} ({len(objects[key])} bytes); replaced: "
        "the destination's bytes did not match its digest"
    ]


def test_a_copy_that_does_not_read_back_is_a_failure() -> None:
    source, dest = _stores()
    dest.on_upload = lambda data: data[:-1] + b"X"
    report, _ = _copy(source, dest)
    assert report.copied == 0
    assert len(report.failed) == 5
    assert all("CopyMismatch" in f["reason"] for f in report.failed)


def test_a_destination_404_on_read_back_is_a_copy_failure_not_a_vanished_source() -> None:
    """Finding 3: only the source GET may classify a key as vanished."""
    objects = _source_objects()
    key = next(iter(_referenced(objects)))
    source, dest = _stores()
    dest.unreadable.add(key)
    report, _ = _copy(source, dest)
    assert report.vanished_from_source == []
    assert [f["key"] for f in report.failed] == [key]
    assert "no object at this key after the upload" in report.failed[0]["reason"]
    code, _ = tool.verdict({"copy": report.as_dict(), "references": None},
                           allow_empty=True, tolerate_source_defects=True)
    assert code == tool.EXIT_FAILED


def test_objects_are_streamed_in_chunks_never_read_whole(monkeypatch) -> None:
    """Shrink both chunk sizes far below the object and watch every read."""
    monkeypatch.setattr(tool, "CHUNK_BYTES", 64)
    monkeypatch.setattr(tool, "TRANSFER", SimpleNamespace(multipart_chunksize=128))
    big = os.urandom(10_000)
    source, dest = _stores(source_objects={_ca(big): big})
    report, _ = _copy(source, dest)
    assert report.copied == 1
    assert dest.buckets[BUCKET][_ca(big)] == big
    # The copy read the source in upload-sized pieces, and the read-back
    # hashed the destination in CHUNK_BYTES pieces; a whole-object read
    # anywhere would show up here as 10000.
    assert [b.largest_read for b in source.bodies] == [128]
    assert [b.largest_read for b in dest.bodies] == [64]
    # And the reader handed to the uploader cannot be rewound, which is
    # what makes boto3 stream it rather than size or buffer it.
    assert not hasattr(tool.HashingReader(io.BytesIO()), "seek")


# ---------------------------------------------------------------------------
# Direction: the digest decides which side is right (finding 1)
# ---------------------------------------------------------------------------


def test_a_rollback_never_overwrites_sound_bytes_with_a_damaged_source() -> None:
    """The reviewer's reproduction: the store being copied FROM holds a
    same-size corruption and a truncation; the store copied TO is sound."""
    good = _source_objects()
    same_size_key, truncated_key = list(_referenced(good))[:2]
    damaged = {
        **good,
        same_size_key: _flip(good[same_size_key]),
        truncated_key: good[truncated_key][: len(good[truncated_key]) // 2],
    }
    source, dest = _stores(source_objects=damaged, dest_objects=dict(good))

    report, _ = _copy(source, dest)
    assert dest.buckets[BUCKET] == good, "a sound destination object was overwritten"
    assert report.copied == 0 and report.skipped == 5
    assert source.writes == []
    assert [w for w in dest.writes if w[0] == "upload_fileobj"] == []

    # And the destination verifies, because it is what the rows say.
    refs = _verify(source, dest, _scan_for(_referenced(good)))
    assert refs["missing"] == refs["mismatched"] == 0
    assert refs["key_parity"]["digest_verified"] == 4


def test_forward_a_damaged_source_object_is_never_written_to_an_empty_key() -> None:
    good = _source_objects()
    key = next(iter(_referenced(good)))
    source, dest = _stores(source_objects={**good, key: _flip(good[key])})

    report, _ = _copy(source, dest)
    assert key not in dest.buckets[BUCKET]
    assert [d["key"] for d in report.source_defects] == [key]
    assert report.source_defects[0]["expected_sha256"] == _sha(good[key])
    assert report.copied == 4

    refs = _verify(source, dest, _scan_for(_referenced(good)))
    assert refs["missing"] == 1 and refs["preexisting_source_defects"] == 1
    strict, _ = tool.verdict({"references": refs}, allow_empty=False, tolerate_source_defects=False)
    lenient, result = tool.verdict({"references": refs}, allow_empty=False, tolerate_source_defects=True)
    assert strict == tool.EXIT_FAILED
    assert lenient == tool.EXIT_OK and "pre-existing source defect" in result


def test_bad_source_bytes_do_not_replace_bad_destination_bytes_either() -> None:
    good = _source_objects()
    key = next(iter(_referenced(good)))
    src_bad, dst_bad = _flip(good[key], 0), _flip(good[key], 1)
    source, dest = _stores(source_objects={**good, key: src_bad}, dest_objects={**good, key: dst_bad})
    logged: list[str] = []
    report, _ = _copy(source, dest, log=logged.append)
    assert dest.buckets[BUCKET][key] == dst_bad
    assert [d["key"] for d in report.source_defects] == [key]
    # Nothing was replaced, so nothing may say it was.
    assert not any("replac" in m for m in logged), logged
    assert any(m.startswith(f"NOT COPIED {key}") for m in logged)


def test_a_key_that_names_no_digest_is_checked_against_its_row() -> None:
    row_bytes = b"what the row says\n"
    source, dest = _stores(source_objects={"custom/snapshot.xml": b"something else\n"})
    report, _ = _copy(source, dest, row_digests={"custom/snapshot.xml": _sha(row_bytes)})
    assert "custom/snapshot.xml" not in dest.buckets[BUCKET]
    assert [d["key"] for d in report.source_defects] == ["custom/snapshot.xml"]
    assert report.unverifiable == []


# ---------------------------------------------------------------------------
# References and verification
# ---------------------------------------------------------------------------


def test_expectations_group_shared_digests_and_set_aside_non_references() -> None:
    a, b = "a" * 64, "b" * 64
    scan = tool.build_expectations(
        [
            (f"s3://{BUCKET}/{content_addressed_key(a)}", a, 10),
            (f"s3://{BUCKET}/{content_addressed_key(a)}", a, 10),
            (f"s3://{BUCKET}/{content_addressed_key(b)}", b, 7),
            # a row that disagrees with its siblings about the same object
            (f"s3://{BUCKET}/{content_addressed_key(b)}", b, 8),
            ("s3://elsewhere/zz/nothing", a, 10),
        ],
        [
            (f"s3://{BUCKET}/{content_addressed_key('c' * 64)}", "c" * 64, 3),
            ("thermoml/member.xml", "d" * 64, 3),
            (f"s3://other-bucket/{content_addressed_key('e' * 64)}", "e" * 64, 3),
        ],
        bucket=BUCKET,
    )
    assert scan.rows == {"calculation_artifact": 5, "external_source_record": 3}
    by_key = {(e.table, e.key, e.expected_bytes): e.rows for e in scan.expectations}
    assert by_key[("calculation_artifact", content_addressed_key(a), 10)] == 3
    assert ("calculation_artifact", content_addressed_key(b), 7) in by_key
    assert ("calculation_artifact", content_addressed_key(b), 8) in by_key
    assert ("external_source_record", content_addressed_key("c" * 64), 3) in by_key
    assert scan.not_object_references == {"external_source_record": 1}
    # Finding 7: an s3:// link into another bucket is a reference, not a
    # "not a reference", and is surfaced rather than folded in.
    assert scan.other_bucket_references == [f"s3://other-bucket/{content_addressed_key('e' * 64)}"]
    assert scan.uri_disagreements == ["s3://elsewhere/zz/nothing"]
    assert scan.uri_buckets == {BUCKET, "elsewhere"}


def _scan_for(objects: dict[str, bytes]):
    return tool.build_expectations(
        [(f"s3://{BUCKET}/{key}", _sha(data), len(data)) for key, data in objects.items()],
        [],
        bucket=BUCKET,
    )


def _verify(source, dest, scan):
    src_cfg, dst_cfg = _configs()
    return tool.verify(scan, src_cfg, dst_cfg, src_client=source, dst_client=dest, log=lambda _m: None)


def test_verify_passes_only_having_read_every_referenced_object() -> None:
    objects = _source_objects()
    source, dest = _stores(dest_objects=dict(objects))
    refs = _verify(source, dest, _scan_for(_referenced(objects)))
    assert refs["rows_compared"] == 3 and refs["rows_verified"] == 3
    assert refs["distinct_objects_read"] == 3
    assert refs["missing"] == 0 and refs["mismatched"] == 0
    assert refs["key_parity"]["digest_verified"] == 4
    assert refs["key_parity"]["present_same_size"] == 1
    code, _ = tool.verdict({"references": refs}, allow_empty=False, tolerate_source_defects=False)
    assert code == tool.EXIT_OK


def test_a_corrupted_destination_object_fails_and_is_named() -> None:
    objects = _source_objects()
    key = next(iter(_referenced(objects)))
    source, dest = _stores(dest_objects={**objects, key: _flip(objects[key], -1)})
    refs = _verify(source, dest, _scan_for(_referenced(objects)))
    assert refs["mismatched"] == 1
    assert refs["mismatched_objects"][0]["key"] == key
    assert refs["mismatched_objects"][0]["source_sound"] is True
    code, result = tool.verdict({"references": refs}, allow_empty=False, tolerate_source_defects=True)
    assert code == tool.EXIT_FAILED, "a copy fault is never tolerated"
    assert "1 mismatched" in result


def test_a_missing_destination_object_fails_and_is_named() -> None:
    objects = _source_objects()
    key = next(iter(_referenced(objects)))
    source, dest = _stores(dest_objects={k: v for k, v in objects.items() if k != key})
    refs = _verify(source, dest, _scan_for(_referenced(objects)))
    assert refs["missing"] == 1 and refs["missing_objects"][0]["key"] == key
    assert [f["key"] for f in refs["key_parity"]["failure_keys"]] == [key]
    code, _ = tool.verdict({"references": refs}, allow_empty=False, tolerate_source_defects=False)
    assert code == tool.EXIT_FAILED


def test_an_unreferenced_key_missing_from_the_destination_still_fails() -> None:
    objects = _source_objects()
    held = next(k for k in objects if k.startswith("reclaimed/"))
    source, dest = _stores(dest_objects={k: v for k, v in objects.items() if k != held})
    refs = _verify(source, dest, _scan_for(_referenced(objects)))
    assert refs["missing"] == 0
    assert [f["key"] for f in refs["key_parity"]["failure_keys"]] == [held]
    code, _ = tool.verdict({"references": refs}, allow_empty=False, tolerate_source_defects=True)
    assert code == tool.EXIT_FAILED


def test_an_unreferenced_held_object_with_wrong_bytes_counts() -> None:
    """Finding 7: a ``reclaimed/<digest>`` whose bytes contradict its name."""
    objects = _source_objects()
    held = next(k for k in objects if k.startswith("reclaimed/"))
    broken = {**objects, held: _flip(objects[held])}
    source, dest = _stores(source_objects=broken)
    report, _ = _copy(source, dest)
    assert held not in dest.buckets[BUCKET]
    refs = _verify(source, dest, _scan_for(_referenced(objects)))
    assert refs["key_parity"]["source_defects"] == 1
    strict, _ = tool.verdict({"copy": report.as_dict(), "references": refs},
                             allow_empty=False, tolerate_source_defects=False)
    lenient, _ = tool.verdict({"copy": report.as_dict(), "references": refs},
                              allow_empty=False, tolerate_source_defects=True)
    assert strict == tool.EXIT_FAILED
    assert lenient == tool.EXIT_OK


def test_a_break_the_source_already_had_is_reported_as_such_and_tolerated_only_on_request() -> None:
    real = b"what the row says\n"
    broken = {_ca(real): b"something else\n"}
    source, dest = _stores(source_objects=broken, dest_objects=dict(broken))
    scan = tool.build_expectations([(f"s3://{BUCKET}/{_ca(real)}", _sha(real), len(real))], [], bucket=BUCKET)
    refs = _verify(source, dest, scan)
    assert refs["mismatched"] == 1 and refs["preexisting_source_defects"] == 1
    assert refs["mismatched_objects"][0]["source_sound"] is False
    strict, _ = tool.verdict({"references": refs}, allow_empty=False, tolerate_source_defects=False)
    lenient, result = tool.verdict({"references": refs}, allow_empty=False, tolerate_source_defects=True)
    assert strict == tool.EXIT_FAILED
    assert lenient == tool.EXIT_OK and "1 pre-existing source defect" in result


def test_a_destination_that_differs_from_a_defective_source_is_never_tolerated() -> None:
    """Finding 4: both sides broken, but broken differently."""
    real = b"what the row says\n"
    source, dest = _stores(
        source_objects={_ca(real): _flip(real, 0)}, dest_objects={_ca(real): _flip(real, 1)}
    )
    scan = tool.build_expectations([(f"s3://{BUCKET}/{_ca(real)}", _sha(real), len(real))], [], bucket=BUCKET)
    refs = _verify(source, dest, scan)
    assert refs["mismatched_objects"][0]["source_sound"] is False
    assert refs["mismatched_objects"][0]["tolerable"] is False
    code, _ = tool.verdict({"references": refs}, allow_empty=False, tolerate_source_defects=True)
    assert code == tool.EXIT_FAILED


def test_no_references_is_not_a_pass() -> None:
    source, dest = _stores(dest_objects=_source_objects())
    refs = _verify(source, dest, tool.build_expectations([], [], bucket=BUCKET))
    assert refs["rows_compared"] == 0
    code, result = tool.verdict({"references": refs}, allow_empty=False, tolerate_source_defects=False)
    assert code == tool.EXIT_NOT_VERIFIED and "nothing was compared" in result
    code, _ = tool.verdict({"references": refs}, allow_empty=True, tolerate_source_defects=False)
    assert code == tool.EXIT_OK


def test_a_failed_copy_fails_the_run_even_when_verification_would_pass() -> None:
    code, _ = tool.verdict(
        {"copy": {"failed": 1}, "references": None}, allow_empty=True, tolerate_source_defects=True
    )
    assert code == tool.EXIT_FAILED


# ---------------------------------------------------------------------------
# Configuration, the same-store probe, and secrets
# ---------------------------------------------------------------------------


def _args(*argv: str):
    return tool.build_parser().parse_args(list(argv))


def test_the_destination_endpoint_is_required() -> None:
    with pytest.raises(tool.UsageError, match="no destination"):
        tool.resolve_stores(_args(), {})


def test_destination_credentials_fall_back_to_the_source() -> None:
    _, dest, inherited = tool.resolve_stores(
        _args("--source-access-key", "k", "--source-secret-key", "s"),
        {"DEST_S3_ENDPOINT_URL": "http://seaweedfs:9000"},
    )
    assert inherited is True
    assert (dest.access_key, dest.secret_key, dest.bucket) == ("k", "s", artifact_storage.S3_BUCKET)


def test_copying_a_bucket_onto_itself_is_refused() -> None:
    with pytest.raises(tool.UsageError, match="same bucket"):
        tool.resolve_stores(
            _args("--source-endpoint", "http://minio:9000", "--dest-endpoint", "http://minio:9000/"), {}
        )


def _wire(monkeypatch, clients: dict[str, FakeS3]) -> None:
    monkeypatch.setattr(tool.StoreConfig, "client", lambda self: clients[self.endpoint_url])


@pytest.mark.parametrize("mode", ["--commit", "--verify-only"])
def test_one_store_under_two_names_is_refused(db_session, monkeypatch, mode) -> None:
    """Finding 2: ``127.0.0.1`` and ``localhost`` are the same MinIO."""
    objects = _source_objects()
    store = FakeS3({BUCKET: dict(objects)})
    _wire(monkeypatch, {"http://127.0.0.1:19546": store, "http://localhost:19546": store})
    _attach_rows(db_session, _referenced(objects), bucket=BUCKET)
    with pytest.raises(tool.SameStore, match="under another name"):
        tool.run(
            _args(mode, "--source-endpoint", "http://127.0.0.1:19546", "--source-bucket", BUCKET),
            session=db_session, env={"DEST_S3_ENDPOINT_URL": "http://localhost:19546"},
            log=lambda _m: None,
        )
    assert store.buckets[BUCKET] == objects, "the probe was left behind"
    assert [w[0] for w in store.writes] == ["put_object", "delete_object"]


def test_distinct_stores_pass_the_probe_and_it_is_removed(monkeypatch) -> None:
    source, dest = _stores(dest_objects={})
    tool.assert_distinct_stores(source, _configs()[0], dest, _configs()[1])
    assert dest.buckets[BUCKET] == {}
    assert source.writes == []
    assert [w[0] for w in dest.writes] == ["put_object", "delete_object"]


def test_verify_only_names_a_missing_destination_bucket(db_session, monkeypatch) -> None:
    """Finding 7: not "a store did not answer (NoSuchBucket)"."""
    source, dest = _stores()
    _wire(monkeypatch, {"http://minio:9000": source, "http://seaweedfs:9000": dest})
    code, report = tool.run(
        _args("--verify-only", "--source-endpoint", "http://minio:9000", "--source-bucket", BUCKET,
              "--dest-bucket", "no-such-bucket"),
        session=db_session, env={"DEST_S3_ENDPOINT_URL": "http://seaweedfs:9000"}, log=lambda _m: None,
    )
    assert code == tool.EXIT_NOT_VERIFIED
    assert "'no-such-bucket' does not exist at http://seaweedfs:9000" in report["result"]
    assert dest.writes == []


def test_endpoints_are_reported_without_userinfo() -> None:
    assert tool.sanitized_endpoint("http://user:pw@seaweedfs:9000/path?x=1") == "http://seaweedfs:9000"


def test_a_whole_run_never_prints_a_secret_or_writes_to_the_source(db_session, monkeypatch) -> None:
    objects = _source_objects()
    source, dest = _stores()
    _wire(monkeypatch, {"http://minio:9000": source, "http://seaweedfs:9000": dest})
    _attach_rows(db_session, _referenced(objects), bucket=BUCKET)
    logged: list[str] = []

    code, report = tool.run(
        _args("--commit", "--source-endpoint", "http://minio:9000", "--source-bucket", BUCKET),
        session=db_session,
        env={
            "DEST_S3_ENDPOINT_URL": "http://seaweedfs:9000",
            "DEST_S3_ACCESS_KEY": "dst-key",
            "DEST_S3_SECRET_KEY": "DST-SECRET-541",
        },
        log=logged.append,
    )
    assert code == tool.EXIT_OK, report["result"]
    assert report["references"]["rows_compared"] == 3
    assert report["copy"]["dest_bucket_created"] is True
    everything = json.dumps(report) + "\n".join(logged)
    assert "DST-SECRET-541" not in everything
    assert artifact_storage.S3_SECRET_KEY not in everything
    assert source.writes == []
    assert not any(k.startswith(tool.PROBE_PREFIX) for k in dest.buckets[BUCKET])


def test_the_database_connection_refuses_writes(db_engine) -> None:
    connection = tool.read_only_connection(db_engine)
    try:
        with pytest.raises(DBAPIError, match="read-only transaction"):
            connection.exec_driver_sql(
                "INSERT INTO external_source (source_name, source_release) VALUES ('x', 'y')"
            )
    finally:
        tool.discard_connection(connection)


def _attach_rows(session, objects: dict[str, bytes], *, bucket: str) -> None:
    species = make_species(session, smiles="CCO")
    entry = make_species_entry(session, species)
    for key, data in objects.items():
        calculation = make_calculation(session, species_entry_id=entry.id)
        attach_artifact(
            session,
            calculation=calculation,
            uri=f"s3://{bucket}/{key}",
            sha256=_sha(data),
            bytes_=len(data),
        )


def test_custody_rows_are_references_too(db_session) -> None:
    """``external_source_record.raw_uri`` names an object when it is ``s3://``."""
    source_row = ExternalSource(source_name="ThermoML", source_release="test-541")
    db_session.add(source_row)
    db_session.flush()
    common = {
        "external_source_id": source_row.id,
        "record_kind": ExternalSourceRecordKind.thermoml_article,
        "source_uri": "https://example.org/thermoml",
        "retrieved_at": datetime(2026, 9, 25),
        "parser_name": "thermoml",
        "parser_version": "1",
        "mapping_version": "1",
    }
    xml = b"<DataReport/>\n"
    db_session.add(
        ExternalSourceRecord(
            **common, source_record_key="10.0/a", content_sha256=_sha(xml),
            content_length=len(xml), raw_uri=f"s3://{BUCKET}/{_ca(xml)}",
        )
    )
    db_session.add(
        ExternalSourceRecord(
            **common, source_record_key="10.0/b", content_sha256="a" * 64,
            content_length=1, raw_uri="thermoml/member.xml",
        )
    )
    db_session.flush()
    scan = tool.read_references(db_session, bucket=BUCKET)
    custody = [e for e in scan.expectations if e.table == "external_source_record"]
    assert [(e.key, e.sha256, e.expected_bytes) for e in custody] == [(_ca(xml), _sha(xml), len(xml))]
    assert scan.not_object_references == {"external_source_record": 1}


def test_a_dry_run_flags_references_the_source_cannot_supply(db_session, monkeypatch) -> None:
    """A row whose file is already gone from the source is found before any copy."""
    objects = _source_objects()
    source, dest = _stores()
    _wire(monkeypatch, {"http://minio:9000": source, "http://seaweedfs:9000": dest})
    lost = b"a log nobody kept\n"
    _attach_rows(db_session, {**_referenced(objects), _ca(lost): lost}, bucket=BUCKET)

    code, report = tool.run(
        _args("--source-endpoint", "http://minio:9000", "--source-bucket", BUCKET),
        session=db_session,
        env={"DEST_S3_ENDPOINT_URL": "http://seaweedfs:9000"},
        log=lambda _m: None,
    )
    assert code == tool.EXIT_OK
    assert report["preflight"]["referenced_but_absent_from_source"] == 1
    assert report["preflight"]["absent_examples"] == [_ca(lost)]
    assert any("absent from the source" in w for w in report["warnings"])
    assert any("same-store probe was not run" in w for w in report["warnings"])
    assert source.writes == [] and dest.writes == []


# ---------------------------------------------------------------------------
# Against real stores
# ---------------------------------------------------------------------------


def _real_store(endpoint, access_key, secret_key, bucket):
    return tool.StoreConfig(endpoint, bucket, artifact_storage.S3_REGION, access_key, secret_key)


def _empty_and_drop(config) -> None:
    client = config.client()
    try:
        for key in tool.list_objects(client, config.bucket):
            client.delete_object(Bucket=config.bucket, Key=key)
        client.delete_bucket(Bucket=config.bucket)
    except ClientError:
        pass


def test_a_real_migration_copies_verifies_rolls_back_safely_and_serves_downloads(
    client, db_session, monkeypatch
) -> None:
    """Copy between two real buckets, break it twice, repair it, run it
    backwards over damaged copies, and download.

    The destination is a second server when ``TCKDB_MIGRATE_TEST_DEST_ENDPOINT_URL``
    (with ``..._ACCESS_KEY`` / ``..._SECRET_KEY``) names one -- that is how
    MinIO -> SeaweedFS was measured -- and otherwise a second bucket on the
    suite's own store, which is what CI runs.
    """
    if store_server_header() is None:
        unavailable(f"{store_description()} did not answer ListBuckets")
    run_id = uuid.uuid4().hex[:12]
    source = _real_store(
        artifact_storage.S3_ENDPOINT_URL,
        artifact_storage.S3_ACCESS_KEY,
        artifact_storage.S3_SECRET_KEY,
        f"mig-src-{run_id}",
    )
    dest = _real_store(
        os.environ.get("TCKDB_MIGRATE_TEST_DEST_ENDPOINT_URL", artifact_storage.S3_ENDPOINT_URL),
        os.environ.get("TCKDB_MIGRATE_TEST_DEST_ACCESS_KEY", artifact_storage.S3_ACCESS_KEY),
        os.environ.get("TCKDB_MIGRATE_TEST_DEST_SECRET_KEY", artifact_storage.S3_SECRET_KEY),
        f"mig-dst-{run_id}",
    )
    # Larger than one multipart chunk, so the streamed multipart path runs.
    large = os.urandom(tool.CHUNK_BYTES + 1_234_567)
    small = b" Entering Gaussian System\n SCF Done: E(RB3LYP) = -40.5\n" * 40
    held, orphan = b"held bytes\n", b"stray\n"
    objects = {
        _ca(large): large,
        _ca(small): small,
        f"reclaimed/{_sha(held)}": held,
        "stray/orphan.txt": orphan,
    }
    src_client = source.client()
    src_client.create_bucket(Bucket=source.bucket)
    try:
        for key, data in objects.items():
            src_client.put_object(Bucket=source.bucket, Key=key, Body=data)
        _attach_rows(db_session, {_ca(large): large, _ca(small): small}, bucket=source.bucket)

        def pointing(a, b):
            """Flags naming ``a`` as the source and ``b`` as the destination."""
            return [
                "--source-endpoint", a.endpoint_url, "--source-bucket", a.bucket,
                "--source-access-key", a.access_key, "--source-secret-key", a.secret_key,
                "--dest-endpoint", b.endpoint_url, "--dest-bucket", b.bucket,
                "--dest-access-key", b.access_key, "--dest-secret-key", b.secret_key,
            ]

        forward = pointing(source, dest)

        def run(*flags, direction=forward):
            return tool.run(_args(*flags, *direction), session=db_session, env={}, log=lambda _m: None)

        code, report = run("--commit")
        assert code == tool.EXIT_OK, report
        assert report["copy"]["copied"] == 4 and report["copy"]["dest_bucket_created"]
        refs = report["references"]
        assert refs["rows_compared"] == 2 and refs["rows_verified"] == 2
        assert refs["distinct_objects_read"] == 2
        assert refs["key_parity"]["digest_verified"] == 3
        assert refs["key_parity"]["present_same_size"] == 1

        code, report = run("--commit")
        assert code == tool.EXIT_OK
        assert (report["copy"]["copied"], report["copy"]["skipped"]) == (0, 4)

        dst_client = dest.client()
        dst_client.put_object(Bucket=dest.bucket, Key=_ca(small), Body=small[:-1] + b"#")
        code, report = run("--verify-only")
        assert code == tool.EXIT_FAILED
        assert [p["key"] for p in report["references"]["mismatched_objects"]] == [_ca(small)]

        dst_client.delete_object(Bucket=dest.bucket, Key=_ca(large))
        code, report = run("--verify-only")
        assert code == tool.EXIT_FAILED
        assert [p["key"] for p in report["references"]["missing_objects"]] == [_ca(large)]

        code, report = run("--commit")
        assert code == tool.EXIT_OK, report
        assert report["copy"]["copied"] == 2

        # The rollback direction, over damaged copies: the destination
        # bucket now plays the source, and holds a same-size corruption and
        # a truncation. The original bucket's sound bytes must survive.
        dst_client.put_object(Bucket=dest.bucket, Key=_ca(small), Body=small[:-1] + b"#")
        dst_client.put_object(Bucket=dest.bucket, Key=_ca(large), Body=large[: len(large) // 2])
        code, report = run("--commit", direction=pointing(dest, source))
        assert report["copy"]["copied"] == 0, report["copy"]
        assert code == tool.EXIT_OK, report["result"]
        for data in (large, small):
            got = src_client.get_object(Bucket=source.bucket, Key=_ca(data))["Body"].read()
            assert _sha(got) == _sha(data), "the rollback overwrote a sound object"

        # Repair the destination, then the switch: the app reads it, and
        # serves the bytes.
        code, report = run("--commit")
        assert code == tool.EXIT_OK, report
        monkeypatch.setattr(artifact_storage, "S3_ENDPOINT_URL", dest.endpoint_url)
        monkeypatch.setattr(artifact_storage, "S3_ACCESS_KEY", dest.access_key)
        monkeypatch.setattr(artifact_storage, "S3_SECRET_KEY", dest.secret_key)
        monkeypatch.setattr(artifact_storage, "S3_BUCKET", dest.bucket)
        for data in (large, small):
            response = client.get(f"/api/v1/scientific/artifacts/{_sha(data)}/download")
            assert response.status_code == 200, response.text
            assert _sha(response.content) == _sha(data)
    finally:
        _empty_and_drop(source)
        _empty_and_drop(dest)
