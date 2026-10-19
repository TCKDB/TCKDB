"""Find a version embedded in a name, in the code TCKDB ships.

The rule (``app/versioning.py``, ``tckdb_schemas.fragments.version``): a
versioned thing has a stable name and a separate integer version. A name never
contains the version. This module is the detector for that rule; the guard
test (``tests/test_no_versioned_names.py``) decides what to do with what it
finds, and ``python -m tests.versioned_names_scan`` prints the current findings
in the form ``tests/versioned_names_debt.py`` holds them.

What is scanned (see ``SCAN_ROOTS``): ``backend/app``, the ``tckdb_schemas``
wire package and the ``tckdb_client`` package, plus the OpenAPI golden. The
QCSchema and Chemkin adapters are deliberately not roots: their ``v1``/``v2``
names are MolSSI's and Cantera's, not TCKDB's.

Five kinds of finding, by what carries the version:

``identifier``
    A name in code: variable, attribute, function, class, argument, keyword,
    import alias. Fails on a ``vN`` segment beside other segments
    (``COMPUTED_THERMO_V2``, ``parse_machine_review_v2_payload``), a camel
    ``...VN`` suffix (``ContributionBundleV0``, ``RuleV0_2_0``) or three numeric
    segments (``MANIFEST_0_1_0_PATH``). A bare ``v1``/``v2`` is a vector, not a
    version, and is not a finding. The segment test is on ``_``-split parts
    because a ``\\b`` after the digits does not fire before ``_payload``.
``string``
    A string constant that is data: ``"computed_thermo_v2"``,
    ``"tckdb.archive.v1"``, ``"thermoml-cp-parser/0.1.0"``,
    ``"computed_thermo@2"``. Docstrings, and any bare string statement, are
    documentation and are skipped.
``fstring``
    An f-string that builds a versioned name: constant text ending ``_v``,
    ``.v``, ``@v`` or ``@`` directly before a formatted value.
``filename``
    A file or directory name under a root: ``rules_0_1_0.yaml``,
    ``ContributionBundleV0.schema.json``.
``openapi``
    A component schema name, or a ``const``/``enum`` string, in the OpenAPI
    golden: a versioned value that exists only on the wire.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from tckdb_schemas.fragments.version import (
    AT_VERSION,
    CAMEL_VERSION,
    VERSION_SEGMENT,
    embeds_version,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

SCAN_ROOTS: tuple[Path, ...] = (
    REPO_ROOT / "backend" / "app",
    REPO_ROOT / "schemas" / "python" / "tckdb-schemas" / "tckdb_schemas",
    REPO_ROOT / "clients" / "python" / "src" / "tckdb_client",
)

OPENAPI_GOLDEN = REPO_ROOT / "backend" / "tests" / "api" / "golden" / "openapi.json"

#: Three numeric segments in an identifier: ``MANIFEST_0_1_0_PATH``.
_IDENT_SEMVER = re.compile(r"(?:^|_)\d+_\d+_\d+(?:_|$)")
_SEGMENT_V = re.compile(r"^[vV]\d+$")
#: ``name/0.1.0`` or ``name-0.1.0`` in a string: a semver stamped onto a name.
_STRING_NAME_SEMVER = re.compile(r"(?<=[A-Za-z0-9])[-/@]\d+\.\d+\.\d+(?![0-9])")
#: ``name_0_3_0`` in a string.
_STRING_SEMVER_SEGMENTS = re.compile(r"(?<=[A-Za-z])_\d+_\d+_\d+(?![0-9])")
_FSTRING_TAIL = re.compile(r"(?:[_.@]v|@)$")

_MAX_TOKEN = 100


@dataclass(frozen=True)
class Finding:
    """One place a version is embedded in a name. ``path`` is repo-relative."""

    path: str
    kind: str
    token: str
    line: int = 0

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.path, self.kind, self.token)


def is_versioned_identifier(name: str) -> bool:
    """True if the identifier carries a version (a bare ``v1`` does not)."""
    segments = [part for part in name.split("_") if part]
    if len(segments) > 1 and any(_SEGMENT_V.match(part) for part in segments):
        return True
    return bool(CAMEL_VERSION.search(name) or _IDENT_SEMVER.search(name))


def is_versioned_string(value: str) -> bool:
    """True if a string constant embeds a version in a name."""
    return bool(
        VERSION_SEGMENT.search(value)
        or CAMEL_VERSION.search(value)
        or AT_VERSION.search(value)
        or _STRING_NAME_SEMVER.search(value)
        or _STRING_SEMVER_SEGMENTS.search(value)
    )


def _clip(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= _MAX_TOKEN else text[: _MAX_TOKEN - 3] + "..."


def _identifier_names(node: ast.AST) -> list[str]:
    """The names *node* introduces or refers to, as written."""
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Attribute):
        return [node.attr]
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return [node.name]
    if isinstance(node, ast.arg):
        return [node.arg]
    if isinstance(node, ast.keyword):
        return [node.arg] if node.arg else []
    if isinstance(node, ast.alias):
        names = node.name.split(".")
        if node.asname:
            names.append(node.asname)
        return names
    if isinstance(node, ast.ExceptHandler):
        return [node.name] if node.name else []
    if isinstance(node, (ast.Global, ast.Nonlocal)):
        return list(node.names)
    return []


def scan_source(source: str, path: str) -> list[Finding]:
    """Identifier, string and f-string findings in one module's source."""
    tree = ast.parse(source)
    findings: list[Finding] = []

    # A bare string statement (a docstring, an attribute note) documents; it is
    # not a value anything stores or serves.
    documentation: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            documentation.add(id(node.value))

    for node in ast.walk(tree):
        for name in _identifier_names(node):
            if is_versioned_identifier(name):
                findings.append(Finding(path, "identifier", name, getattr(node, "lineno", 0)))
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in documentation and is_versioned_string(node.value):
                findings.append(Finding(path, "string", _clip(node.value), node.lineno))
        if isinstance(node, ast.JoinedStr):
            values = node.values
            for index, part in enumerate(values[:-1]):
                if (
                    isinstance(part, ast.Constant)
                    and isinstance(part.value, str)
                    and isinstance(values[index + 1], ast.FormattedValue)
                    and _FSTRING_TAIL.search(part.value)
                ):
                    findings.append(Finding(path, "fstring", _clip(ast.unparse(node)), node.lineno))
                    break
    return findings


def scan_names(roots: tuple[Path, ...] = SCAN_ROOTS, repo_root: Path = REPO_ROOT) -> list[Finding]:
    """File and directory names under *roots* that embed a version."""
    findings: list[Finding] = []
    for root in roots:
        for entry in sorted(root.rglob("*")):
            if "__pycache__" in entry.parts or entry.suffix == ".pyc":
                continue
            if embeds_version(entry.name):
                findings.append(Finding(entry.relative_to(repo_root).as_posix(), "filename", entry.name))
    return findings


def scan_code(roots: tuple[Path, ...] = SCAN_ROOTS, repo_root: Path = REPO_ROOT) -> list[Finding]:
    """Identifier, string and f-string findings in every ``.py`` under *roots*."""
    findings: list[Finding] = []
    for root in roots:
        for file in sorted(root.rglob("*.py")):
            rel = file.relative_to(repo_root).as_posix()
            findings.extend(scan_source(file.read_text(encoding="utf-8"), rel))
    return findings


def scan_openapi(golden: Path = OPENAPI_GOLDEN, repo_root: Path = REPO_ROOT) -> list[Finding]:
    """Component names and ``const``/``enum`` strings in the OpenAPI golden."""
    rel = golden.relative_to(repo_root).as_posix()
    document = json.loads(golden.read_text(encoding="utf-8"))
    components = document.get("components", {})
    findings = [
        Finding(rel, "openapi", f"schema:{name}")
        for name in components.get("schemas", {})
        if embeds_version(name)
    ]

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "const" and isinstance(value, str):
                    if is_versioned_string(value):
                        findings.append(Finding(rel, "openapi", f"value:{_clip(value)}"))
                elif key == "enum" and isinstance(value, list):
                    findings.extend(
                        Finding(rel, "openapi", f"value:{_clip(item)}")
                        for item in value
                        if isinstance(item, str) and is_versioned_string(item)
                    )
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(components)
    return findings


def scan_all() -> list[Finding]:
    return scan_code() + scan_names() + scan_openapi()


def counts(findings: list[Finding]) -> Counter[tuple[str, str, str]]:
    """Occurrences per (path, kind, token)."""
    return Counter(finding.key for finding in findings)


def main() -> int:  # pragma: no cover - maintenance helper
    rows = sorted(counts(scan_all()).items())
    print(f"{len(rows)} entries, {sum(n for _, n in rows)} occurrences", file=sys.stderr)
    for (path, kind, token), n in rows:
        print(f"    ({path!r}, {kind!r}, {token!r}, {n}),")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
