"""Compare two snapshot builds.

    python scripts/paper_snapshot/compare.py <build-one-dir> <build-two-dir>

Each directory is a ``build.py`` output. Compared:

* ``expected_outputs/``: per file, byte-identical, or identical after masking public refs (no list is re-sorted).
* ``chemkin_export/``: per file, byte-identical, or identical after masking public refs (and, in ``.json`` files,
  timestamps such as the manifest's ``generated_at``).
* Archive blobs: the set of (SHA-256, size) pairs.
* Archive rows: per table, the multiset of rows after removing timestamps and masking public refs and the bucket name
  in artifact URIs. Integer ids are kept. A ``deposit_warning`` row's ``content_hash`` covers its message and context,
  which name public refs, so it is recomputed over the masked message and context (same formula as the server) before
  the rows are compared. A ``deposit_warning`` hash that does not match its own content is a difference.
* Release files: a ``release_artifact`` row holds the file bytes (base64) and a ``release_manifest`` row holds the
  document that lists them. Per build, before anything is masked, each artifact's ``sha256`` and ``byte_count`` are
  recomputed from its stored bytes, each line is checked to be in the server's canonical JSON form, the manifest document's artifact entries are checked against them, and the
  manifest's ``content_sha256`` is recomputed from its document (the server's formula). A hash that does not match, or
  that cannot be recomputed, is a difference. Then the bytes are decoded and each line is masked by the row rule (the
  timestamp keys, string values that look like timestamps, public refs and the bucket name; line order is kept), and
  the artifact and manifest hashes are recomputed over the masked content.

Exit status 0 only if nothing differs.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import sys
import tarfile
from collections import Counter
from pathlib import Path
from typing import Any

REF = re.compile(r"\b[a-z]{2,6}_[a-z2-7]{26}\b")
TIMESTAMP = re.compile(r"^\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d")
TIMESTAMP_KEYS = {
    "created_at",
    "updated_at",
    "submitted_at",
    "approved_at",
    "rejected_at",
    "retrieved_at",
}


BUCKET = re.compile(r"s3://[^/\"]+/")


def mask_refs(text: str) -> str:
    """Mask public refs and the bucket name in artifact URIs."""
    return BUCKET.sub("s3://<bucket>/", REF.sub("<ref>", text))


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compare_expected(one: Path, two: Path) -> dict[str, Any]:
    """Per expected-output file: byte-identical, identical after masking refs, or differing."""
    a, b = one / "expected_outputs", two / "expected_outputs"
    names = sorted({p.name for p in a.glob("*")} | {p.name for p in b.glob("*")})
    result: dict[str, str] = {}
    for name in names:
        pa, pb = a / name, b / name
        if not pa.is_file() or not pb.is_file():
            result[name] = "DIFFERS: present in only one build"
        elif pa.read_bytes() == pb.read_bytes():
            result[name] = "byte-identical"
        elif mask_refs(pa.read_text()) == mask_refs(pb.read_text()):
            result[name] = "identical after masking public refs"
        else:
            result[name] = "DIFFERS"
    return result


def masked_export_file(path: Path) -> str:
    """An exported CHEMKIN file with public refs masked; a ``.json`` file is parsed and its timestamps masked first."""
    text = path.read_text()
    if path.suffix == ".json":
        return mask_refs(canonical(mask_json(json.loads(text))))
    return mask_refs(text)


def compare_chemkin_export(one: Path, two: Path) -> dict[str, str]:
    """Per file of ``chemkin_export/``: byte-identical, identical after masking refs and timestamps, or differing."""
    a, b = one / "chemkin_export", two / "chemkin_export"
    names = sorted({p.name for p in a.glob("*")} | {p.name for p in b.glob("*")})
    result: dict[str, str] = {}
    for name in names:
        pa, pb = a / name, b / name
        if not pa.is_file() or not pb.is_file():
            result[name] = "DIFFERS: present in only one build"
        elif pa.read_bytes() == pb.read_bytes():
            result[name] = "byte-identical"
        elif masked_export_file(pa) == masked_export_file(pb):
            result[name] = "identical after masking public refs and timestamps"
        else:
            result[name] = "DIFFERS"
    return result


def warning_hash(code: str, field: str, message: str, context: Any) -> str:
    """The server's ``deposit_warnings.content_hash`` formula."""
    payload = json.dumps(
        [code, field, message, context],
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def masked_warning_hash(values: dict[str, Any]) -> str | None:
    """``deposit_warning.content_hash`` recomputed over the masked message and context."""
    if values.get("content_hash") is None:
        return None
    context = json.loads(mask_refs(canonical(values["context"])))
    return warning_hash(
        values["code"], values["field"], mask_refs(values["message"]), context
    )


def manifest_digest(document: Any) -> str:
    """The server's manifest digest: SHA-256 over the canonical JSON of the manifest document."""
    text = json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def mask_json(value: Any) -> Any:
    """``value`` with the timestamp keys and timestamp-looking strings replaced; nothing else is touched."""
    if isinstance(value, dict):
        return {
            k: "<timestamp>" if k in TIMESTAMP_KEYS else mask_json(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [mask_json(v) for v in value]
    if isinstance(value, str) and TIMESTAMP.match(value):
        return "<timestamp>"
    return value


def _stored_bytes(values: dict[str, Any]) -> bytes:
    return base64.b64decode(values["content"]["$bytes"], validate=True)


def non_canonical_lines(data: bytes) -> list[str]:
    """Lines of release bytes that are not the server's canonical JSON, one per line, newline-terminated."""
    if not data:
        return []
    issues = []
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return ["stored bytes are not UTF-8"]
    if not text.endswith("\n"):
        issues.append("stored bytes do not end with a newline")
    for number, line in enumerate(text.removesuffix("\n").split("\n"), start=1):
        try:
            canonical_line = canonical(json.loads(line))
        except ValueError:
            issues.append(f"line {number} is not JSON")
            continue
        if canonical_line != line:
            issues.append(f"line {number} is not in canonical form")
    return issues


def release_problems(
    artifacts: list[dict[str, Any]], manifests: list[dict[str, Any]]
) -> list[str]:
    """Release hashes that do not match their own content or cannot be recomputed, in one build, unmasked."""
    problems: list[str] = []
    recomputed: dict[tuple[Any, Any], tuple[str, int]] = {}
    for row in artifacts:
        name = f"release_artifact {row.get('id')}"
        try:
            data = _stored_bytes(row)
        except Exception as exc:  # noqa: BLE001 - any failure to read the bytes is a reported difference
            problems.append(
                f"{name}: stored bytes cannot be read ({type(exc).__name__})"
            )
            continue
        digest = hashlib.sha256(data).hexdigest()
        recomputed[(row.get("release_manifest_id"), row.get("path"))] = (
            digest,
            len(data),
        )
        if digest != row.get("sha256"):
            problems.append(
                f"{name}: stored bytes hash to {digest}, recorded {row.get('sha256')}"
            )
        if len(data) != row.get("byte_count"):
            problems.append(
                f"{name}: stored bytes are {len(data)}, recorded {row.get('byte_count')}"
            )
        problems.extend(f"{name}: {issue}" for issue in non_canonical_lines(data))
    manifest_ids = {m.get("id") for m in manifests}
    for row in artifacts:
        if row.get("release_manifest_id") not in manifest_ids:
            problems.append(
                f"release_artifact {row.get('id')}: no manifest row to check it against"
            )
    for manifest in manifests:
        name = f"release_manifest {manifest.get('id')}"
        document = manifest.get("document_json")
        try:
            if manifest_digest(document) != manifest.get("content_sha256"):
                problems.append(
                    f"{name}: document hashes to {manifest_digest(document)}, recorded {manifest.get('content_sha256')}"
                )
            listed = {entry["path"]: entry for entry in document["artifacts"]}
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{name}: hash cannot be recomputed ({type(exc).__name__})")
            continue
        held = {path for (mid, path) in recomputed if mid == manifest.get("id")}
        if held != set(listed):
            problems.append(f"{name}: lists {sorted(listed)} but holds {sorted(held)}")
        for path in held & set(listed):
            digest, size = recomputed[(manifest.get("id"), path)]
            if (
                listed[path].get("sha256") != digest
                or listed[path].get("byte_count") != size
            ):
                problems.append(
                    f"{name}: entry for {path} does not match the stored bytes"
                )
    return problems


def masked_release_rows(
    artifacts: list[dict[str, Any]], manifests: list[dict[str, Any]]
) -> list[tuple[str, dict[str, Any]]]:
    """(table, values) for each release row with the bytes decoded and masked and the hashes recomputed over them."""
    out: list[tuple[str, dict[str, Any]]] = []
    masked_by_key: dict[tuple[Any, Any], tuple[str, int]] = {}
    for row in artifacts:
        values = {k: v for k, v in row.items() if k not in TIMESTAMP_KEYS}
        try:
            lines = _stored_bytes(row).decode("utf-8").split("\n")
            masked = "\n".join(
                mask_refs(canonical(mask_json(json.loads(line)))) if line else line
                for line in lines
            )
        except Exception:  # noqa: BLE001 - reported by release_problems; keep the raw bytes so the rows still differ
            masked = json.dumps(row.get("content"), sort_keys=True)
        data = masked.encode("utf-8")
        masked_by_key[(row.get("release_manifest_id"), row.get("path"))] = (
            hashlib.sha256(data).hexdigest(),
            len(data),
        )
        values.update(
            content=masked,
            sha256=hashlib.sha256(data).hexdigest(),
            byte_count=len(data),
        )
        out.append(("release_artifact", values))
    for row in manifests:
        values = {
            k: v
            for k, v in row.items()
            if k not in TIMESTAMP_KEYS
            and not (isinstance(v, str) and TIMESTAMP.match(v))
        }
        try:
            document = json.loads(canonical(row["document_json"]))
            for entry in document["artifacts"]:
                entry["sha256"], entry["byte_count"] = masked_by_key[
                    (row["id"], entry["path"])
                ]
            document = json.loads(mask_refs(canonical(mask_json(document))))
            values.update(
                document_json=document, content_sha256=manifest_digest(document)
            )
        except Exception:  # noqa: BLE001 - reported by release_problems
            pass
        out.append(("release_manifest", values))
    return out


def open_rows(archive: Path):
    with tarfile.open(archive) as tar:
        manifest = json.load(tar.extractfile("manifest.json"))
        rows: dict[str, Counter] = {}
        mismatched_hashes: list[int] = []
        release_artifacts: list[dict[str, Any]] = []
        release_manifests: list[dict[str, Any]] = []
        for line in tar.extractfile("rows.ndjson"):
            record = json.loads(line)
            if record.get("record_type") != "row":
                continue
            if record["table"] == "release_artifact":
                release_artifacts.append(record["values"])
                continue
            if record["table"] == "release_manifest":
                release_manifests.append(record["values"])
                continue
            values = {
                k: v
                for k, v in record["values"].items()
                if k not in TIMESTAMP_KEYS
                and not (isinstance(v, str) and TIMESTAMP.match(v))
            }
            if (
                record["table"] == "deposit_warning"
                and values.get("content_hash") is not None
            ):
                unmasked = warning_hash(
                    values["code"],
                    values["field"],
                    values["message"],
                    values["context"],
                )
                if unmasked != values["content_hash"]:
                    mismatched_hashes.append(values["id"])
                values["content_hash"] = masked_warning_hash(values)
            rows.setdefault(record["table"], Counter())[
                mask_refs(canonical(values))
            ] += 1
    for table, values in masked_release_rows(release_artifacts, release_manifests):
        rows.setdefault(table, Counter())[mask_refs(canonical(values))] += 1
    return (
        manifest,
        rows,
        mismatched_hashes,
        release_problems(release_artifacts, release_manifests),
    )


def compare_archives(one: Path, two: Path) -> dict[str, Any]:
    ma, ra, bad_a, rel_a = open_rows(one / "tckdb.archive")
    mb, rb, bad_b, rel_b = open_rows(two / "tckdb.archive")
    blobs_a = {(b["sha256"], b["bytes"]) for b in ma["blobs"]}
    blobs_b = {(b["sha256"], b["bytes"]) for b in mb["blobs"]}
    tables = sorted(set(ra) | set(rb))
    differing = {}
    for table in tables:
        ca, cb = ra.get(table, Counter()), rb.get(table, Counter())
        if ca != cb:
            only_a, only_b = ca - cb, cb - ca
            differing[table] = {
                "rows_one": sum(ca.values()),
                "rows_two": sum(cb.values()),
                "only_in_one": len(only_a),
                "only_in_two": len(only_b),
                "sample_only_in_one": next(iter(only_a), None),
                "sample_only_in_two": next(iter(only_b), None),
            }
    return {
        "database_revisions_equal": ma["database_revisions"]
        == mb["database_revisions"],
        "blobs_one": len(blobs_a),
        "blobs_two": len(blobs_b),
        "blob_sets_equal": blobs_a == blobs_b,
        "tables_compared": len(tables),
        "rows_one": sum(sum(c.values()) for c in ra.values()),
        "rows_two": sum(sum(c.values()) for c in rb.values()),
        "deposit_warning_hashes_not_matching_their_content": {
            "one": len(bad_a),
            "two": len(bad_b),
        },
        "release_hashes_not_matching_their_content": {"one": rel_a, "two": rel_b},
        "tables_that_differ": differing,
    }


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 2:
        print(__doc__)
        return 2
    one, two = Path(args[0]), Path(args[1])
    expected = compare_expected(one, two)
    archive = compare_archives(one, two)
    chemkin = compare_chemkin_export(one, two)
    print(
        json.dumps(
            {"expected_outputs": expected, "chemkin_export": chemkin, "archive": archive},
            indent=2,
            sort_keys=True,
        )
    )
    bad = [n for n, v in expected.items() if v.startswith("DIFFERS")]
    bad += [f"chemkin_export/{n}" for n, v in chemkin.items() if v.startswith("DIFFERS")]
    ok = (
        not bad
        and archive["blob_sets_equal"]
        and not archive["tables_that_differ"]
        and archive["database_revisions_equal"]
        and not any(
            archive["deposit_warning_hashes_not_matching_their_content"].values()
        )
        and not any(archive["release_hashes_not_matching_their_content"].values())
    )
    print(
        "\nRESULT:",
        "no difference"
        if ok
        else f"DIFFERENCES ({bad or ''} {list(archive['tables_that_differ'])} {archive['deposit_warning_hashes_not_matching_their_content']} {archive['release_hashes_not_matching_their_content']})",
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
