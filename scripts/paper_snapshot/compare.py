"""Compare two snapshot builds: the determinism check.

    python scripts/paper_snapshot/compare.py <build-one-dir> <build-two-dir>

Each directory is a ``build.py`` output (``tckdb.archive`` and
``expected_outputs/``). Two builds from an empty instance are expected to
agree on content, not on bytes: public refs are random, so they differ between
builds, and so do timestamps. This tool states exactly how much it ignores.

* **expected_outputs/**: per file, byte-identical or not. A file that is not
  byte-identical is compared again with every public ref replaced by a
  placeholder and every list sorted (refs set the order the generators sort
  by, so a different ref set reorders rows). The file is then reported as
  ``identical after masking refs and ordering`` or ``DIFFERS``.
* **archive blobs**: the set of SHA-256 digests and sizes (job files), compared
  exactly.
* **archive rows**: per table, the multiset of rows with timestamps removed,
  every public ref masked and the object-store bucket name in artifact URIs masked, compared exactly. Integer ids are kept, so the check
  also says that the two builds assigned the same ids in the same order.

Exit status 0 only if nothing differs.
"""

from __future__ import annotations

import json
import re
import sys
import tarfile
from collections import Counter
from pathlib import Path
from typing import Any

REF = re.compile(r"\b[a-z]{2,6}_[a-z2-7]{26}\b")
TIMESTAMP = re.compile(r"^\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d")
TIMESTAMP_KEYS = {"created_at", "updated_at", "submitted_at", "approved_at", "rejected_at", "retrieved_at"}


BUCKET = re.compile(r"s3://[^/\"]+/")


def mask_refs(text: str) -> str:
    """Public refs, and the bucket name inside an artifact URI (it names the instance, not the content)."""
    return BUCKET.sub("s3://<bucket>/", REF.sub("<ref>", text))


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sort_lists(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: sort_lists(v) for k, v in node.items()}
    if isinstance(node, list):
        return sorted((sort_lists(v) for v in node), key=canonical)
    return node


def mask_document(document: Any) -> Any:
    return sort_lists(json.loads(mask_refs(canonical(document))))


def compare_expected(one: Path, two: Path) -> dict[str, Any]:
    a, b = one / "expected_outputs", two / "expected_outputs"
    names = sorted({p.name for p in a.glob("*")} | {p.name for p in b.glob("*")})
    result: dict[str, str] = {}
    for name in names:
        pa, pb = a / name, b / name
        if not pa.is_file() or not pb.is_file():
            result[name] = "DIFFERS: present in only one build"
        elif pa.read_bytes() == pb.read_bytes():
            result[name] = "byte-identical"
        elif name.endswith(".json"):
            same = mask_document(json.loads(pa.read_text())) == mask_document(json.loads(pb.read_text()))
            result[name] = "identical after masking refs and ordering" if same else "DIFFERS"
        else:
            # The Markdown rendering is a table view of the JSON next to it; the JSON verdict stands for it.
            result[name] = "not compared (rendering of the .json beside it)"
    return result


def open_rows(archive: Path):
    with tarfile.open(archive) as tar:
        manifest = json.load(tar.extractfile("manifest.json"))
        rows: dict[str, Counter] = {}
        for line in tar.extractfile("rows.ndjson"):
            record = json.loads(line)
            if record.get("record_type") != "row":
                continue
            values = {
                k: v for k, v in record["values"].items()
                if k not in TIMESTAMP_KEYS and not (isinstance(v, str) and TIMESTAMP.match(v))
            }
            rows.setdefault(record["table"], Counter())[mask_refs(canonical(values))] += 1
    return manifest, rows


def compare_archives(one: Path, two: Path) -> dict[str, Any]:
    ma, ra = open_rows(one / "tckdb.archive")
    mb, rb = open_rows(two / "tckdb.archive")
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
        "database_revisions_equal": ma["database_revisions"] == mb["database_revisions"],
        "blobs_one": len(blobs_a),
        "blobs_two": len(blobs_b),
        "blob_sets_equal": blobs_a == blobs_b,
        "tables_compared": len(tables),
        "rows_one": sum(sum(c.values()) for c in ra.values()),
        "rows_two": sum(sum(c.values()) for c in rb.values()),
        "tables_that_differ": differing,
    }


def main(argv: list[str] | None = None) -> int:
    args = (argv if argv is not None else sys.argv[1:])
    if len(args) != 2:
        print(__doc__)
        return 2
    one, two = Path(args[0]), Path(args[1])
    expected = compare_expected(one, two)
    archive = compare_archives(one, two)
    print(json.dumps({"expected_outputs": expected, "archive": archive}, indent=2, sort_keys=True))
    bad = [n for n, v in expected.items() if v.startswith("DIFFERS")]
    ok = not bad and archive["blob_sets_equal"] and not archive["tables_that_differ"] and archive["database_revisions_equal"]
    print("\nRESULT:", "no difference" if ok else f"DIFFERENCES ({bad or ''} {list(archive['tables_that_differ'])})")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
