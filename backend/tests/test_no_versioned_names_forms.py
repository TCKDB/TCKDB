"""The guard's reach: real rule ids, chemistry, and the other ways to build a versioned name.

``tests/test_no_versioned_names.py`` holds the guard and its self-tests. This
file holds the three things a review found it had wrong or thin:

* a rule id such as ``K-XYG3-B3LYP-BARRIER`` is a stable scientific identifier
  and a perfectly good ``VersionRef`` name; the name grammar must take it;
* chemistry must never be read as a version -- ``cc-pV5Z`` is a Dunning basis,
  not version 5 of ``cc-p``;
* a version can be fused into a name by an f-string, ``str.format``, ``%`` or
  ``+`` as well as by writing it out, and each form is a planted violation.

Forms that are deliberately NOT caught, and why: a name and a version joined
through variables that are not named like a version (``a + sep + b``), because
nothing in the syntax says ``b`` is a version and the alternative flags every
string join; and a version fused by a mapping or a ``join`` over a tuple. Both
are left to review. ``f"/releases/{version}"`` is not flagged either: a path
parameter is not a name with a version in it.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]
from tckdb_schemas.fragments.version import CAMEL_VERSION, VersionRef, embeds_version

from app.chemistry.dispersion_names import DISPERSION_ALIASES
from app.chemistry.method_names import NAME_ALIASES, SUFFIX_ALIASES
from app.versioning import VersionRegistry
from tests.versioned_names_scan import (
    REPO_ROOT,
    is_versioned_identifier,
    is_versioned_string,
    scan_frontend,
    scan_source,
    scan_yaml,
)

CHEMISTRY_ROOT = REPO_ROOT / "backend" / "app" / "chemistry"


def _rule_ids_in_manifests() -> set[str]:
    """Every ``rule_id`` in the rule-manifest YAMLs, read from the files."""
    found: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "rule_id" and isinstance(value, str):
                    found.add(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for file in sorted(CHEMISTRY_ROOT.rglob("*.yaml")):
        for document in yaml.safe_load_all(file.read_text(encoding="utf-8")):
            walk(document)
    return found


def test_every_real_rule_id_is_a_valid_version_name_and_registers() -> None:
    ids = _rule_ids_in_manifests()
    assert {"E1", "K-XYG3-B3LYP-BARRIER", "N-JM-CH4-RATE", "S-ACONFL-DLPNO-F12-VERYTIGHT"} <= ids
    assert len(ids) >= 10, f"only {len(ids)} rule ids read; the manifests were not reached"
    registry: VersionRegistry[str] = VersionRegistry("rules")
    for rule_id in sorted(ids):
        assert not embeds_version(rule_id), rule_id
        assert not is_versioned_string(rule_id) and not is_versioned_identifier(rule_id), rule_id
        assert VersionRef(name=rule_id, version=1).name == rule_id
        registry.register(rule_id, 1, rule_id, current=True)
    registry.validate()
    assert {ref.name for ref in registry.refs()} == ids


#: Chemistry spellings the detector must never read as a version.
CHEMISTRY_TOKENS = (
    # Dunning zeta >= 5 (what the camel pattern got wrong), alone and inside a method string.
    "cc-pV5Z", "aug-cc-pV5Z", "cc-pV6Z", "jun-cc-pV5Z", "jul-cc-pV5Z", "aug-cc-pV6Z", "MP2/cc-pV5Z",
    "CCSD(T)/aug-cc-pV5Z", "cc-pwCV5Z", "aug-cc-pwCV5Z", "cc-pV5Z-DK", "cc-pV5Z-PP", "cc-pv5z", "ccpv5z",
    "aug-ccpv5z", "ANO-RCC-V5Z", "cc-pV(5+d)Z", "x2c-TZVPPall",
    # The reviewer's clean list.
    "B3LYP-D3", "6-31G(d,p)", "def2-TZVP", "aug-cc-pVTZ", "G4", "M06-2X", "ωB97X-V", "CCSD(T)",
    # Other families with a V or a digit run next to a letter.
    "cc-pVQZ", "cc-pVDZ-F12", "cc-pVTZ-F12", "def2-QZVPP", "def2-SVP", "def2-TZVPD", "ma-def2-TZVP",
    "6-311++G(2df,2pd)", "6-311+G(d,p)", "3-21G", "STO-3G", "LANL2DZ", "TZV2P", "DZVP2", "NEVPT2",
    "CASPT2", "DLPNO-CCSD(T)", "DLPNO-CCSD(T)-F12", "PNO-LCCSD(T)-F12", "B97M-V", "B97M-rV", "ωB97M-V",
    "LC-VV10", "rVV10", "SCAN-rVV10", "wB97X-D3BJ", "B2PLYP-D3(BJ)", "r2SCAN-3c", "HF-3c", "PW6B95",
    "revDSD-PBEP86-D4", "PBE0-D3(BJ)", "TPSS-D3", "CBS-QB3", "G3B3", "W1BD", "CBS-APNO", "MRCI+Q",
    "XYG3", "B3LYP", "CH4", "F12", "S-ACONFL-DLPNO-F12-VERYTIGHT", "K-XYG3-B3LYP-BARRIER",
    "N-JM-CH4-RATE", "E1", "pc-2", "Sapporo-DKH3-QZP", "aug-cc-pCVQZ-DK", "cc-pVn+dZ",
)

#: Parameter-parser stamps that fuse a version in; real debt, not chemistry.
_PARSER_STAMPS = {"gaussian_v1", "orca_v2", "molpro_v1", "psi4-0.1.0"}


def _chemistry_corpus() -> set[str]:
    """Method, basis and dispersion strings from the vocabularies and the chemistry code."""
    corpus: set[str] = set(CHEMISTRY_TOKENS)
    aliases: list[Any] = [*NAME_ALIASES, *SUFFIX_ALIASES, *DISPERSION_ALIASES]
    for alias in aliases:
        corpus.update((alias.alias, alias.canonical))
    sources = [*CHEMISTRY_ROOT.rglob("*.py"), *(REPO_ROOT / "backend/app/services").glob("*_parameter_parser.py")]
    for file in sources:
        tree = ast.parse(file.read_text(encoding="utf-8"))
        statements = {
            id(node.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in statements:
                if node.value.endswith(".yaml") or len(node.value) > 80:
                    continue
                corpus.add(node.value)
                corpus.update(node.value.replace(",", " ").split())
    return corpus - _PARSER_STAMPS


def test_no_chemistry_spelling_is_flagged_as_a_version() -> None:
    corpus = _chemistry_corpus()
    assert len(corpus) > 500, f"the chemistry corpus is only {len(corpus)} strings"
    assert {"cc-pV5Z", "NEVPT2", "def2-TZVP", "B3LYP-D3"} <= corpus
    flagged = sorted(
        token
        for token in corpus
        if is_versioned_string(token) or is_versioned_identifier(token) or embeds_version(token)
    )
    assert not flagged, f"chemistry flagged as a version: {flagged}"


def test_the_dunning_exclusion_is_what_keeps_pv5z_clean() -> None:
    """Without the ``Z`` exclusion ``cc-pV5`` would match; the pattern does fire on a real camel version."""
    assert CAMEL_VERSION.search("cc-pV5Z") is None
    assert CAMEL_VERSION.search("cc-pV5") is not None
    assert CAMEL_VERSION.search("RuleV5") is not None


def test_the_lowercase_glued_pattern_is_not_greedy_about_vv10() -> None:
    from tckdb_schemas.fragments.version import LOWER_GLUED_VERSION

    assert LOWER_GLUED_VERSION.search("thingv10") is not None
    assert LOWER_GLUED_VERSION.search("scanrvv10") is None  # the nonlocal-correlation VV10 is not a version
    assert LOWER_GLUED_VERSION.search("rev2") is None


@pytest.mark.parametrize("scanner", ["scan_code", "scan_names", "scan_openapi", "scan_yaml", "scan_frontend"])
def test_scan_all_includes_every_scanner(scanner: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """A scanner with no findings today must still be wired in: it is the one that catches tomorrow's."""
    from tests import versioned_names_scan as module

    sentinel = module.Finding("sentinel", "test", scanner)
    monkeypatch.setattr(module, scanner, lambda: [sentinel])
    assert sentinel in module.scan_all()


def _kinds(source: str) -> list[tuple[str, str]]:
    return [(f.kind, f.token) for f in scan_source(source, "planted.py")]


@pytest.mark.parametrize(
    ("source", "kind"),
    [
        ('n = f"{name}-v{version}"', "fstring"),
        ('n = f"{name}/{v}"', "fstring"),
        ('n = f"{name}/{version}"', "fstring"),
        ('n = f"{name}V{version}"', "fstring"),
        ('n = f"{name}-{version}"', "fstring"),
        ('n = f"{name}_{rubric.version}"', "fstring"),
        ('n = "{}_v{}".format(name, version)', "build"),
        ('n = "{name}.v{version}".format(name=a, version=b)', "build"),
        ('n = name + "_v" + str(version)', "build"),
        ('n = name + ".v" + str(version)', "build"),
        ('n = "%s_v%d" % (name, version)', "build"),
        ('n = "%(name)s_v%(version)d" % values', "build"),
        ('X = "foov2"', "string"),
        ("foov2 = 1", "identifier"),
        ("def thingv10(): ...", "identifier"),
    ],
)
def test_the_detector_catches_the_other_building_forms(source: str, kind: str) -> None:
    found = _kinds(source)
    assert len(found) == 1, found
    assert found[0][0] == kind


@pytest.mark.parametrize(
    "source",
    [
        'n = f"{name}: {version}"',
        'n = f"version {version}"',
        'n = f"Gaussian v{ver}"',
        'n = f"/releases/{version}"',
        'n = f"{a}/{b}"',
        'n = f"{a}-{b}"',
        'n = "{}: {}".format(a, b)',
        'n = a + "_" + b',
        'n = name + "_value"',
        'n = "%s/%s" % (a, b)',
        'X = "VV10"',
        'X = "cc-pV5Z"',
        'X = "rev2"',
        "ccpv5z = 1",
    ],
)
def test_the_new_forms_leave_ordinary_code_alone(source: str) -> None:
    assert _kinds(source) == []


def test_the_detector_catches_a_versioned_yaml_value(tmp_path: Path) -> None:
    (tmp_path / "rules.yaml").write_text(
        "rule_id: thing_v2\n"
        "nested:\n"
        "  - manifest: tool/0.1.0\n"
        "  - basis: cc-pV5Z\n"
        "  - id: E1\n"
        "  - label: K-XYG3-B3LYP-BARRIER\n"
    )
    found = sorted(f.token for f in scan_yaml(roots=(tmp_path,), repo_root=tmp_path))
    assert found == ["thing_v2", "tool/0.1.0"]


def test_the_detector_catches_a_versioned_frontend_literal_and_identifier(tmp_path: Path) -> None:
    (tmp_path / "api.ts").write_text(
        "// a comment about computed_thermo_v2 is skipped\n"
        "const RUBRIC_V2 = 'computed_thermo_v2';\n"
        "const url = `/api/v1/species`;\n"
        "const ok = 'cc-pV5Z';\n"
    )
    (tmp_path / "api.test.ts").write_text("const X_V2 = 'a_v2';\n")
    (tmp_path / "__tests__").mkdir()
    (tmp_path / "__tests__" / "b.ts").write_text("const Y_V2 = 1;\n")
    found = sorted(f.token for f in scan_frontend(root=tmp_path, repo_root=tmp_path))
    assert found == ["RUBRIC_V2", "computed_thermo_v2"]
