"""A version never goes inside a name.

TCKDB identifies a versioned thing it owns -- a rubric, a rule, a document
format, a parser -- by a stable name and a separate integer version
(``tckdb_schemas.fragments.version.VersionRef``; ``app/versioning.py`` holds
the implementations). Fusing the two (``COMPUTED_THERMO_V2``,
``"tckdb.archive.v1"``, ``ContributionBundleV0``, ``manifest_0_3_0.yaml``)
makes a version bump a rename: every stored value, wire document and
identifier that used the old string has to be found and rewritten, and the
version ends up recorded twice when the record also has a version field.

This file fails the build when new code, a new stored string or a new file name
embeds a version. The detector is ``tests/versioned_names_scan.py``; the
patterns are the ones ``VersionRef`` validates names with, so the two cannot
disagree.

Two lists keep the rule honest (both in ``tests/versioned_names_debt.py``):

* ``ALLOWED`` is permanent and short, each entry with the reason it is not a
  TCKDB version (a botocore class name, NIST's archive name).
* ``DEBT`` is everything that already violates the rule when this guard
  landed. It can only shrink: a new violation fails, a debt entry the scan no
  longer finds fails, a debt entry whose count differs fails, and the ceilings
  below fail any growth in the list itself. Later PRs of the versioning
  programme empty it; the last one deletes it and asserts the scan is clean.

``/api/v1`` is not a finding and needs no allowance: the API's own version
lives in the URL, and ``/v1`` has no name before it for a version to be fused
into.
"""

from __future__ import annotations

import fnmatch
import json
from collections import Counter
from pathlib import Path

import pytest
from tckdb_schemas.fragments.version import VersionRef

from tests.versioned_names_debt import ALLOWED, COMPLETED_PRS, DEBT
from tests.versioned_names_scan import (
    REPO_ROOT,
    SCAN_ROOTS,
    Finding,
    counts,
    is_versioned_identifier,
    is_versioned_string,
    scan_all,
    scan_code,
    scan_names,
    scan_openapi,
    scan_source,
)

#: Upper bounds on the debt list. Lower them (never raise them) as entries go.
DEBT_ENTRY_CEILING = 199
DEBT_OCCURRENCE_CEILING = 338

REMOVING_PRS = frozenset({"PR-2", "PR-3", "PR-4"})


def _allowance(key: tuple[str, str, str]) -> tuple[str, str, str, str] | None:
    path, kind, token = key
    for entry in ALLOWED:
        if kind == entry[1] and fnmatch.fnmatch(path, entry[0]) and fnmatch.fnmatch(token, entry[2]):
            return entry
    return None


def _unallowed() -> Counter[tuple[str, str, str]]:
    return Counter({key: n for key, n in counts(scan_all()).items() if _allowance(key) is None})


def _debt() -> dict[tuple[str, str, str], int]:
    return {(path, kind, token): n for path, kind, token, n, _pr in DEBT}


# ---------------------------------------------------------------------------
# The guard
# ---------------------------------------------------------------------------


def test_no_new_name_embeds_a_version() -> None:
    debt = _debt()
    offenders = [
        f"  {path}  [{kind}]  {token!r}  x{n}"
        + ("" if (path, kind, token) not in debt else f"  (debt allows {debt[(path, kind, token)]})")
        for (path, kind, token), n in sorted(_unallowed().items())
        if n > debt.get((path, kind, token), 0)
    ]
    assert not offenders, (
        "A version is embedded in a name. Give the thing a stable name and carry the "
        "version as a separate integer field (VersionRef; app/versioning.py), or, if the "
        "match is another party's version, add a reasoned entry to ALLOWED:\n" + "\n".join(offenders)
    )


def test_every_debt_entry_still_exists_with_the_same_count() -> None:
    found = _unallowed()
    stale = [
        f"  {path}  [{kind}]  {token!r}: listed x{n}, found x{found.get((path, kind, token), 0)}"
        for path, kind, token, n, _pr in DEBT
        if found.get((path, kind, token), 0) != n
    ]
    assert not stale, (
        "DEBT only shrinks. These entries no longer match the scan -- delete the entry if "
        "it is gone, lower the count if some uses are gone:\n" + "\n".join(stale)
    )


def test_the_debt_list_cannot_grow() -> None:
    assert len(DEBT) <= DEBT_ENTRY_CEILING, "DEBT gained an entry; fix the violation instead"
    assert sum(row[3] for row in DEBT) <= DEBT_OCCURRENCE_CEILING, "DEBT gained occurrences"
    keys = [row[:3] for row in DEBT]
    assert len(keys) == len(set(keys)), "DEBT lists the same (path, kind, token) twice"
    assert all(row[3] >= 1 for row in DEBT), "DEBT holds an entry with no occurrences"
    assert {row[4] for row in DEBT} <= REMOVING_PRS, "every DEBT entry names the PR that removes it"
    assert DEBT, "DEBT is empty: delete it and the tests above that read it (PR-4)"
    assert COMPLETED_PRS <= REMOVING_PRS, "COMPLETED_PRS names a PR the programme does not have"
    done = sorted(row[:3] for row in DEBT if row[4] in COMPLETED_PRS)
    assert not done, f"these rows name a PR already listed in COMPLETED_PRS, which said it was done: {done}"


def test_every_allowance_has_a_reason_and_matches_something() -> None:
    keys = set(counts(scan_all()))
    for path, kind, token, reason in ALLOWED:
        assert len(reason.split()) >= 8, f"allowance for {token!r} needs a real reason"
        assert any(
            k_kind == kind and fnmatch.fnmatch(k_path, path) and fnmatch.fnmatch(k_token, token)
            for k_path, k_kind, k_token in keys
        ), f"allowance ({path}, {kind}, {token}) matches nothing; delete it"


def test_the_debt_and_the_allowances_do_not_overlap() -> None:
    assert not [row for row in DEBT if _allowance(row[:3])]


# ---------------------------------------------------------------------------
# The guard can fail
# ---------------------------------------------------------------------------


def test_the_scan_reaches_the_code_it_claims_to() -> None:
    """A wrong root would find nothing and pass every assertion above."""
    files = [file for root in SCAN_ROOTS for file in root.rglob("*.py")]
    assert len(files) >= 700, f"only {len(files)} modules scanned"
    assert REPO_ROOT / "backend/app/services/trust/rubrics.py" in files
    assert REPO_ROOT / "schemas/python/tckdb-schemas/tckdb_schemas/fragments/version.py" in files
    assert REPO_ROOT / "clients/python/src/tckdb_client/replay.py" in files
    assert [row for row in DEBT if row[1] == "openapi"], "the OpenAPI golden was not reached"
    assert scan_openapi(), "the OpenAPI scan found nothing in a golden that has versioned names"
    assert scan_names(), "the file-name scan found nothing under the roots"
    mcp = REPO_ROOT / "integrations/mcp/src"
    assert mcp in SCAN_ROOTS and any(mcp in file.parents for file in files), "the MCP package was not reached"
    assert len(list((REPO_ROOT / "backend/app/chemistry").rglob("*.yaml"))) >= 6, "rule manifests not reached"
    frontend = [f for f in (REPO_ROOT / "frontend/src").rglob("*.ts*") if ".test." not in f.name]
    assert len(frontend) > 100, "frontend sources not reached"


def test_an_empty_root_finds_nothing_which_is_why_the_reach_test_exists(tmp_path: Path) -> None:
    assert scan_code(roots=(tmp_path,), repo_root=tmp_path) == []
    assert scan_names(roots=(tmp_path,), repo_root=tmp_path) == []


def _kinds(source: str) -> list[tuple[str, str]]:
    return [(f.kind, f.token) for f in scan_source(source, "planted.py")]


@pytest.mark.parametrize(
    ("source", "kind"),
    [
        ("FOO_V2 = 1", "identifier"),
        ("class BarV0: ...", "identifier"),
        ("def x_from_v2(): ...", "identifier"),
        ("def parse_machine_review_v2_payload(): ...", "identifier"),
        ("class MachineRuleV0_2_0: ...", "identifier"),
        ("MANIFEST_0_1_0_PATH = 1", "identifier"),
        ("def f(rule_v2): ...", "identifier"),
        ("from m import COMPUTED_THERMO_V2", "identifier"),
        ("f(prompt_v1=1)", "identifier"),
        ('X = "computed_thermo_v2"', "string"),
        ('X = "tckdb.thing.v1"', "string"),
        ('X = "parse_v2_payload"', "string"),
        ('X = "ContributionBundleV0"', "string"),
        ('X = "thermoml-cp-parser/0.1.0"', "string"),
        ('X = "computed_thermo@2"', "string"),
        ('X = "rules_0_1_0.yaml"', "string"),
        ('n = f"{name}_v{version}"', "fstring"),
        ('n = f"{name}.v{version}"', "fstring"),
        ('n = f"{name}@{version}"', "fstring"),
    ],
)
def test_the_detector_catches_each_planted_form(source: str, kind: str) -> None:
    found = _kinds(source)
    assert len(found) == 1, found
    assert found[0][0] == kind


@pytest.mark.parametrize(
    "source",
    [
        "v1 = a - b",
        "def f(v1, v2): ...",
        "x.v2 = 3",
        '"""v1 immutability, computed_thermo_v2 history."""',
        "def f():\n    '''Replays computed_thermo_v2.'''\n    return 1",
        'X = "/api/v1/species"',
        'VersionRef(name="computed_thermo", version=1)',
        'X = "tckdb_schemas"',
        "utf_8 = 1",
        "sha256 = 1",
        'X = "2026-10-19"',
        "n = f'{name}: {version}'",
    ],
)
def test_the_detector_passes_clean_code(source: str) -> None:
    assert _kinds(source) == []


def test_the_detector_catches_a_versioned_file_name(tmp_path: Path) -> None:
    for name in ("rules_0_1_0.yaml", "ContributionBundleV0.schema.json", "thing_v2.py", "tckdb.archive.v1.json"):
        (tmp_path / name).write_text("{}")
    for name in ("rules.yaml", "version.py", "api_clients.py"):
        (tmp_path / name).write_text("")
    (tmp_path / "pkg_v3").mkdir()
    found = {f.token for f in scan_names(roots=(tmp_path,), repo_root=tmp_path)}
    assert found == {
        "rules_0_1_0.yaml",
        "ContributionBundleV0.schema.json",
        "thing_v2.py",
        "tckdb.archive.v1.json",
        "pkg_v3",
    }


def test_the_detector_catches_a_versioned_openapi_name_and_value(tmp_path: Path) -> None:
    golden = tmp_path / "openapi.json"
    golden.write_text(
        json.dumps(
            {
                "paths": {"/api/v1/species": {}},
                "components": {
                    "schemas": {
                        "ContributionBundleV0": {"properties": {"f": {"const": "tckdb.thing.v1"}}},
                        "Clean": {"properties": {"f": {"enum": ["a_v2", "b"]}}},
                    }
                },
            }
        )
    )
    found = sorted(f.token for f in scan_openapi(golden=golden, repo_root=tmp_path))
    assert found == ["schema:ContributionBundleV0", "value:a_v2", "value:tckdb.thing.v1"]


def test_botocore_and_the_gaussian_keyword_are_detected_then_allowed() -> None:
    assert is_versioned_identifier("SigV4Auth")
    assert is_versioned_identifier("iop_2_9_2000")
    for path, token in (
        ("backend/app/services/artifact_storage_seaweedfs.py", "SigV4Auth"),
        ("backend/app/services/artifact_storage_admin.py", "S3SigV4Auth"),
        ("backend/app/services/gaussian_parameter_parser.py", "iop_2_9_2000"),
    ):
        assert _allowance((path, "identifier", token)) is not None
    assert _allowance(("backend/app/services/trust/rubrics.py", "identifier", "SigV4Auth")) is None


def test_the_detector_and_versionref_agree_on_what_a_versioned_name_is() -> None:
    versioned = ["computed_thermo_v2", "tckdb.archive.v1", "manifest_0_3_0", "a_v2_b", "rule-0.1.0", "x_v10"]
    for name in versioned:
        assert is_versioned_string(name), name
        with pytest.raises(ValueError, match="embeds a version"):
            VersionRef(name=name, version=1)
    for name in ("computed_thermo", "tckdb.dataset_release", "statmech-selection"):
        assert not is_versioned_string(name)
        VersionRef(name=name, version=1)


def test_a_finding_carries_its_location() -> None:
    (finding,) = scan_source("\n\nX = 'a_v2'\n", "p.py")
    assert finding == Finding("p.py", "string", "a_v2", 3)
