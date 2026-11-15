"""The snapshot compare script's hashes follow the server's formulas, and a hash that does not match is a difference."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import sys
import tarfile
from pathlib import Path

import pytest

from app.services.deposit_warnings import content_hash
from app.services.release.manifest import manifest_digest as server_manifest_digest

SNAPSHOT_DIR = Path(__file__).resolve().parents[3] / "scripts" / "paper_snapshot"


@pytest.fixture(scope="module")
def compare():
    sys.path.insert(0, str(SNAPSHOT_DIR))
    try:
        import compare as module

        yield module
    finally:
        sys.path.remove(str(SNAPSHOT_DIR))


CASES = [
    ("code_a", "field[0].x", "A message with no ref.", {"n": 1, "xs": ["a", "b"], "flag": True}),
    ("code_b", "f", "Another message.", None),
]


@pytest.mark.parametrize(("code", "field", "message", "context"), CASES)
def test_warning_hash_equals_the_server_hash(compare, code, field, message, context):
    assert compare.warning_hash(code, field, message, context) == content_hash(code, field, message, context)


@pytest.mark.parametrize(("code", "field", "message", "context"), CASES)
def test_masked_hash_of_a_ref_free_warning_equals_the_server_hash(compare, code, field, message, context):
    values = {"code": code, "field": field, "message": message, "context": context, "content_hash": "x"}
    assert compare.masked_warning_hash(values) == content_hash(code, field, message, context)


def test_masked_hash_ignores_the_public_refs_in_a_warning(compare):
    def values(ref: str) -> dict:
        return {
            "code": "c",
            "field": "f",
            "message": f"calc {ref} differs",
            "context": {"refs": [ref]},
            "content_hash": "x",
        }

    one = compare.masked_warning_hash(values("calc_" + "a" * 26))
    two = compare.masked_warning_hash(values("calc_" + "b" * 26))
    assert one == two


# --- release files -----------------------------------------------------------------------------------------------


def _release(lines: list[dict]):
    data = ("\n".join(json.dumps(line, sort_keys=True, separators=(",", ":")) for line in lines) + "\n").encode()
    artifact = {
        "id": 1,
        "release_manifest_id": 7,
        "path": "selected_records.ndjson",
        "content": {"$bytes": base64.b64encode(data).decode()},
        "sha256": hashlib.sha256(data).hexdigest(),
        "byte_count": len(data),
    }
    document = {
        "artifacts": [{"path": artifact["path"], "sha256": artifact["sha256"], "byte_count": artifact["byte_count"]}],
        "release": {"published_at": "2026-01-01T00:00:00.000001"},
    }
    manifest = {"id": 7, "document_json": document, "content_sha256": server_manifest_digest(document)}
    return artifact, manifest


def test_manifest_digest_equals_the_server_digest(compare):
    document = {"b": [1, 2.5, None, "é"], "a": {"z": True, "y": "x"}}
    assert compare.manifest_digest(document) == server_manifest_digest(document)


def test_a_consistent_release_has_no_problem(compare):
    artifact, manifest = _release([{"record_ref": "a", "n": 1}])
    assert compare.release_problems([artifact], [manifest]) == []


def test_an_artifact_hash_that_does_not_match_its_bytes_is_reported(compare):
    artifact, manifest = _release([{"record_ref": "a", "n": 1}])
    artifact["sha256"] = "0" * 64
    problems = compare.release_problems([artifact], [manifest])
    assert any("stored bytes hash to" in p for p in problems)


def test_a_manifest_hash_that_does_not_match_its_document_is_reported(compare):
    artifact, manifest = _release([{"record_ref": "a", "n": 1}])
    manifest["content_sha256"] = "0" * 64
    assert any("document hashes to" in p for p in compare.release_problems([artifact], [manifest]))


def test_a_manifest_entry_that_does_not_match_the_artifact_is_reported(compare):
    artifact, manifest = _release([{"record_ref": "a", "n": 1}])
    manifest["document_json"]["artifacts"][0]["sha256"] = "1" * 64
    manifest["content_sha256"] = server_manifest_digest(manifest["document_json"])
    assert any("does not match the stored bytes" in p for p in compare.release_problems([artifact], [manifest]))


def test_a_release_that_cannot_be_recomputed_is_reported(compare):
    artifact, manifest = _release([{"record_ref": "a", "n": 1}])
    artifact["content"] = {"$bytes": "not base64!"}
    manifest["document_json"] = None
    problems = compare.release_problems([artifact], [manifest])
    assert any("cannot be read" in p for p in problems)
    assert any("cannot be recomputed" in p for p in problems)


def test_masked_release_rows_ignore_refs_and_timestamps_but_not_content_or_order(compare):
    def masked(ref, stamp, numbers):
        artifact, manifest = _release([{"record_ref": ref, "attested_at": stamp, "n": n} for n in numbers])
        return compare.masked_release_rows([artifact], [manifest])

    one = masked("thm_" + "a" * 26, "2026-01-01T00:00:00.000001", [1, 2])
    two = masked("thm_" + "b" * 26, "2026-02-02T01:01:01.000002", [1, 2])
    assert one == two
    assert masked("thm_" + "a" * 26, "2026-01-01T00:00:00.000001", [2, 1]) != one


def _with_bytes(artifact: dict, manifest: dict, data: bytes) -> None:
    artifact["content"] = {"$bytes": base64.b64encode(data).decode()}
    artifact["sha256"] = hashlib.sha256(data).hexdigest()
    artifact["byte_count"] = len(data)
    manifest["document_json"]["artifacts"][0].update(sha256=artifact["sha256"], byte_count=artifact["byte_count"])
    manifest["content_sha256"] = server_manifest_digest(manifest["document_json"])


def test_a_line_that_is_not_in_canonical_form_is_reported(compare):
    artifact, manifest = _release([{"a": 1}])
    _with_bytes(artifact, manifest, b'{"b":2,"a":1}\n')
    assert any("not in canonical form" in p for p in compare.release_problems([artifact], [manifest]))
    _with_bytes(artifact, manifest, b'{"a": 1}\n')
    assert any("not in canonical form" in p for p in compare.release_problems([artifact], [manifest]))
    _with_bytes(artifact, manifest, b'{"a":1}')
    assert any("newline" in p for p in compare.release_problems([artifact], [manifest]))


def test_the_servers_own_line_writer_output_is_canonical(compare):
    from app.services.release.artifacts import _ndjson

    data, _ = _ndjson([{"z": "é", "a": [1, 2.5, None], "m": {"y": True, "b": "x"}}, {"k": 1}])
    assert compare.non_canonical_lines(data) == []


# --- main() end to end -------------------------------------------------------------------------------------------


def _build(tmp_path: Path, name: str, artifact: dict, manifest: dict) -> Path:
    build = tmp_path / name
    (build / "expected_outputs").mkdir(parents=True)
    (build / "expected_outputs" / "one.json").write_text("{}")
    rows = [
        {"record_type": "row", "table": "release_artifact", "values": artifact},
        {"record_type": "row", "table": "release_manifest", "values": manifest},
    ]
    with tarfile.open(build / "tckdb.archive", "w") as tar:
        for member, payload in (
            ("manifest.json", json.dumps({"database_revisions": ["r1"], "blobs": []}).encode()),
            ("rows.ndjson", "\n".join(json.dumps(r) for r in rows).encode()),
        ):
            info = tarfile.TarInfo(member)
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
    return build


def test_main_exits_zero_for_a_clean_pair_and_non_zero_for_a_release_hash_problem(compare, tmp_path, capsys):
    one = _build(tmp_path, "one", *_release([{"record_ref": "a", "n": 1}]))
    two = _build(tmp_path, "two", *_release([{"record_ref": "a", "n": 1}]))
    assert compare.main([str(one), str(two)]) == 0
    assert "RESULT: no difference" in capsys.readouterr().out

    artifact, manifest = _release([{"record_ref": "a", "n": 1}])
    artifact["sha256"] = "0" * 64
    bad = _build(tmp_path, "bad", artifact, manifest)
    assert compare.main([str(one), str(bad)]) == 1
    assert "RESULT: DIFFERENCES" in capsys.readouterr().out
