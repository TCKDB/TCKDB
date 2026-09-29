"""The mapping report names every field of the document, once (issue #573).

Before 0.5.0 a ``driver="hessian"`` import dropped ``properties.return_energy``,
``extras.qcvars`` and nine other ``properties.*`` fields, and named none of
them. These tests hold the report to the document with their own walk of the
raw JSON, not the adapter's (:mod:`tckdb_qcschema.report_coverage`), so a
defect in the adapter's walk cannot pass by agreeing with itself.

Corpus: every ``outcome: route`` case of the adapter's own fixtures, every
document of the backend corpus (``backend/tests/fixtures/qcschema/``), and
both families of the Psi4 B3LYP/def2-TZVP water Hessian from the C-Q4
demonstration.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import qcelemental.models.v1 as qcel_v1
import qcelemental.models.v2 as qcel_v2
from conftest import FIXTURES, discover_corpus_cases, load_case
from tckdb_qcschema.errors import E_MAPPING_REPORT_INCOMPLETE, QCSchemaAdapterError
from tckdb_qcschema.mapping import build_conformer_upload_payload
from tckdb_qcschema.reader import read_document
from tckdb_qcschema.report_coverage import check_mapping_report, rule_paths
from tckdb_qcschema.scan import build_scan_bundle_payload
from tckdb_qcschema.uploader import sha256_bytes

REPO_ROOT = Path(__file__).resolve().parents[5]
BACKEND_CORPUS = REPO_ROOT / "backend" / "tests" / "fixtures" / "qcschema"
WATER = BACKEND_CORPUS / "water_b3lyp_def2tzvp_psi4"
WATER_V1 = WATER / "water_b3lyp_def2tzvp_hessian_v1.json"
WATER_V2 = WATER / "water_b3lyp_def2tzvp_hessian_v2.json"


def _corpus() -> list[Path]:
    adapter = [
        FIXTURES / case / "document.json"
        for case in discover_corpus_cases()
        if load_case(case)[1]["outcome"] == "route"
    ]
    backend = sorted(BACKEND_CORPUS.glob("*/document.json"))
    return [*adapter, *backend, WATER_V1, WATER_V2]


CORPUS = _corpus()


def _id(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT))


def _import(raw: bytes) -> tuple[dict, dict]:
    record = read_document(raw)
    payload, report = build_conformer_upload_payload(
        record,
        raw_bytes=raw,
        raw_artifact_filename="document.qcschema.json",
        raw_artifact_sha256=sha256_bytes(raw),
        declared_smiles="O",
    )
    return payload, report.to_dict()


# ---------------------------------------------------------------------------
# the test's own oracle
# ---------------------------------------------------------------------------


def _present(value) -> bool:
    return value is not None and value not in ("", [], {})


def _leaves(node, here: str = "") -> list[str]:
    if isinstance(node, dict):
        out: list[str] = []
        for key, value in node.items():
            out += _leaves(value, f"{here}.{key}" if here else key)
        return out
    return [here] if _present(node) else []


def _buckets_naming(report: dict, leaf: str) -> list[str]:
    return [
        bucket
        for bucket, entries in report.items()
        if any(leaf == e or leaf.startswith(e + ".") for e in entries)
    ]


# ---------------------------------------------------------------------------
# completeness over every corpus document
# ---------------------------------------------------------------------------


def _scan_parent(path: Path) -> str | None:
    """The parent optimization case of an adapter torsion-drive fixture."""
    if path.parent.parent != FIXTURES:
        return None
    return load_case(path.parent.name)[1].get("parent_opt_case")


def _import_any(path: Path) -> tuple[dict, dict]:
    """``(the report stored in the payload, the report returned)``.

    A torsion drive imports together with its parent optimization; its
    report is the scan calculation's.
    """
    raw = path.read_bytes()
    parent_case = _scan_parent(path)
    if parent_case is None:
        payload, report = _import(raw)
        return payload["calculation"]["parameters_json"]["tckdb_qcschema"]["mapping_report"], report
    parent_raw = load_case(parent_case)[0]
    bundle = build_scan_bundle_payload(
        read_document(raw),
        raw_bytes=raw,
        raw_artifact_filename="document.qcschema.json",
        raw_artifact_sha256=sha256_bytes(raw),
        parent_record=read_document(parent_raw),
        parent_raw_bytes=parent_raw,
        parent_artifact_filename="parent.qcschema.json",
        parent_artifact_sha256=sha256_bytes(parent_raw),
        declared_smiles=load_case(path.parent.name)[1]["smiles_arg"],
    )
    scan = bundle.payload["conformers"][0]["additional_calculations"][0]
    return scan["parameters_json"]["tckdb_qcschema"]["mapping_report"], bundle.report.to_dict()


def test_corpus_is_not_empty() -> None:
    """Red, never vacuously green, if a fixture tree goes missing."""
    adapter = [p for p in CORPUS if p.parent.parent == FIXTURES]
    # 8 energy/gradient/hessian/optimization cases plus the 3 optimizations
    # the torsion drives start from; and the 3 torsion drives themselves.
    assert len([p for p in adapter if _scan_parent(p) is None]) == 11
    assert len([p for p in adapter if _scan_parent(p) is not None]) == 3
    assert len([p for p in CORPUS if p.parent.parent == BACKEND_CORPUS and p.name == "document.json"]) == 8
    for path in CORPUS:
        assert path.is_file(), path


@pytest.mark.parametrize("path", CORPUS, ids=_id)
def test_every_field_is_in_exactly_one_bucket(path: Path) -> None:
    raw = path.read_bytes()
    stored, report = _import_any(path)
    leaves = _leaves(json.loads(raw))
    assert len(leaves) > 20, "the walk found almost nothing; the oracle is broken"
    problems = {
        leaf: buckets
        for leaf in leaves
        if len(buckets := _buckets_naming(report, leaf)) != 1
    }
    assert problems == {}, problems
    for bucket, entries in report.items():
        assert len(entries) == len(set(entries)), f"{bucket} lists a path twice"
    # The stored copy is the returned report, not an earlier snapshot.
    assert stored == report


# ---------------------------------------------------------------------------
# the water Hessian, bucket by bucket
# ---------------------------------------------------------------------------

#: Buckets shared by both families of the water document, in v1 spelling.
_WATER_SHARED = {
    "transformed": [
        "--smiles",
        "molecule.fragments",
        "molecule.geometry",
        "molecule.mass_numbers",
        "molecule.masses",
        "molecule.molecular_charge",
        "molecule.molecular_multiplicity",
        "molecule.real",
        "molecule.symbols",
        "provenance.creator",
        "provenance.version",
        "return_result",
        "success",
    ],
    "retained_only": [
        "extras.qcvars",
        "molecule.atom_labels",
        "molecule.atomic_numbers",
        "molecule.fix_com",
        "molecule.fix_orientation",
        "molecule.fragment_charges",
        "molecule.fragment_multiplicities",
        "molecule.name",
        "molecule.provenance",
        "molecule.schema_name",
        "molecule.schema_version",
        "molecule.validated",
        "properties.calcinfo_nalpha",
        "properties.calcinfo_natom",
        "properties.calcinfo_nbasis",
        "properties.calcinfo_nbeta",
        "properties.calcinfo_nmo",
        "properties.nuclear_repulsion_energy",
        "properties.return_energy",
        "properties.return_gradient",
        "properties.return_hessian",
        "provenance.hostname",
        "provenance.memory",
        "provenance.nthreads",
        "provenance.routine",
        "provenance.wall_time",
        "schema_name",
        "schema_version",
    ],
    "unsupported": [
        "provenance.cpu",
        "provenance.module",
        "provenance.qcengine_version",
        "provenance.username",
        "stdout",
    ],
    "rejected": [],
}

WATER_V1_BUCKETS = {
    "transformed": sorted(
        _WATER_SHARED["transformed"] + ["driver", "keywords", "model.basis", "model.method"]
    ),
    "retained_only": sorted(_WATER_SHARED["retained_only"] + ["protocols"]),
    "unsupported": _WATER_SHARED["unsupported"],
    "rejected": [],
}

WATER_V2_BUCKETS = {
    "transformed": sorted(
        _WATER_SHARED["transformed"]
        + [
            "input_data.specification.driver",
            "input_data.specification.keywords",
            "input_data.specification.model.basis",
            "input_data.specification.model.method",
        ]
    ),
    "retained_only": sorted(
        _WATER_SHARED["retained_only"]
        + [
            "input_data.molecule",
            "input_data.provenance",
            "input_data.schema_name",
            "input_data.schema_version",
            "input_data.specification.protocols",
            "input_data.specification.schema_name",
            "properties.schema_name",
        ]
    ),
    "unsupported": _WATER_SHARED["unsupported"],
    "rejected": [],
}


@pytest.mark.parametrize(
    ("path", "expected"),
    [(WATER_V1, WATER_V1_BUCKETS), (WATER_V2, WATER_V2_BUCKETS)],
    ids=["v1", "v2"],
)
def test_water_hessian_buckets_exactly(path: Path, expected: dict) -> None:
    _payload, report = _import(path.read_bytes())
    assert {bucket: sorted(entries) for bucket, entries in report.items()} == expected


# ---------------------------------------------------------------------------
# the energy of a Hessian document (the #573 decision)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", [WATER_V1, WATER_V2], ids=["v1", "v2"])
def test_hessian_energy_is_named_retained_only_and_not_stored(path: Path) -> None:
    """A freq record has no energy field, and a sibling sp would be wired to
    it by a single_point_on edge, whose parent must be an opt. So the energy
    is not stored anywhere in the payload, and the report says so."""
    raw = path.read_bytes()
    payload, report = _import(raw)
    energy = json.loads(raw)["properties"]["return_energy"]
    assert energy == pytest.approx(-76.4629955819491, abs=0)

    assert payload["calculation"]["type"] == "freq"
    assert "sp_result" not in payload["calculation"]
    assert payload.get("additional_calculations", []) == []
    assert str(energy) not in json.dumps(payload["calculation"]["hessian"])
    assert "properties.return_energy" in report["retained_only"]
    assert _buckets_naming(report, "properties.return_energy") == ["retained_only"]


@pytest.mark.parametrize(
    ("case", "bucket"),
    [
        ("energy_v1", "transformed"),
        ("energy_v2", "transformed"),
        ("gradient_v1", "transformed"),
        ("gradient_v2", "transformed"),
        ("hessian_v1", "retained_only"),
        ("hessian_v2", "retained_only"),
    ],
)
def test_return_energy_bucket_per_driver(case: str, bucket: str) -> None:
    """energy and gradient store it as the sp energy; hessian cannot."""
    raw, _meta = load_case(case)
    _payload, report = _import(raw)
    assert _buckets_naming(report, "properties.return_energy") == [bucket]


# ---------------------------------------------------------------------------
# unknown keys: listed, not refused
# ---------------------------------------------------------------------------


def test_unknown_keys_are_listed_unsupported() -> None:
    """qcelemental accepts arbitrary keys in extras, model, provenance and
    molecule.extras. The raw document is kept, so each is listed, not refused."""
    document = json.loads(WATER_V2.read_bytes())
    document["extras"]["producer_note"] = {"nested": {"deep": 1}}
    document["input_data"]["specification"]["model"]["grid"] = "ultrafine"
    document["provenance"]["cluster"] = "zeus"
    document["molecule"]["extras"] = {"label": "w1"}
    document["input_data"]["specification"]["extras"] = {"queue": "alon_q"}
    raw = json.dumps(document).encode()

    payload, report = _import(raw)

    for path in (
        "extras.producer_note",
        "input_data.specification.model.grid",
        "provenance.cluster",
        "molecule.extras.label",
        "input_data.specification.extras.queue",
    ):
        assert path in report["unsupported"], path
    # Nothing else moved.
    baseline = {k: sorted(v) for k, v in WATER_V2_BUCKETS.items()}
    assert sorted(report["transformed"]) == baseline["transformed"]
    assert sorted(report["retained_only"]) == baseline["retained_only"]
    assert payload["calculation"]["type"] == "freq"


# ---------------------------------------------------------------------------
# the check itself refuses
# ---------------------------------------------------------------------------


def test_check_refuses_an_unclassified_field() -> None:
    document = json.loads(WATER_V2.read_bytes())
    _payload, report = _import(WATER_V2.read_bytes())
    report = copy.deepcopy(report)
    report["retained_only"].remove("properties.return_energy")
    with pytest.raises(QCSchemaAdapterError) as excinfo:
        check_mapping_report(document, report)
    assert excinfo.value.code == E_MAPPING_REPORT_INCOMPLETE
    assert excinfo.value.context["unclassified"] == ["properties.return_energy"]


def test_check_refuses_a_field_in_two_buckets() -> None:
    document = json.loads(WATER_V2.read_bytes())
    _payload, report = _import(WATER_V2.read_bytes())
    report = copy.deepcopy(report)
    report["unsupported"].append("extras")
    with pytest.raises(QCSchemaAdapterError) as excinfo:
        check_mapping_report(document, report)
    assert excinfo.value.code == E_MAPPING_REPORT_INCOMPLETE
    assert set(excinfo.value.context["ambiguous"]) == {
        leaf for leaf in _leaves(document) if leaf.startswith("extras.")
    }


def test_import_refuses_when_a_branch_leaves_its_energy_unclassified(monkeypatch) -> None:
    """A branch-owned field is never filled in by a static rule: if the
    hessian branch stops naming the energy, the import refuses instead of
    posting a report that omits it (the #573 defect, re-created)."""
    import tckdb_qcschema.mapping as mapping

    real = mapping.complete_mapping_report

    def forgetting_the_energy(record, report):
        report.retained_only.remove("properties.return_energy")
        real(record, report)

    monkeypatch.setattr(mapping, "complete_mapping_report", forgetting_the_energy)
    with pytest.raises(QCSchemaAdapterError) as excinfo:
        _import(WATER_V2.read_bytes())
    assert excinfo.value.code == E_MAPPING_REPORT_INCOMPLETE
    assert excinfo.value.context["unclassified"] == ["properties.return_energy"]


# ---------------------------------------------------------------------------
# the rules against the pinned qcelemental models
# ---------------------------------------------------------------------------


def _fields(model_cls) -> list[str]:
    fields = getattr(model_cls, "model_fields", None)
    if fields is None:
        fields = model_cls.__fields__
    return [getattr(f, "alias", None) or name for name, f in fields.items()]


#: Containers that accept arbitrary keys (qcelemental ``extra="allow"``, or a
#: free dict), or whose keys the mapping branches handle as a whole: a key
#: in one of these is either mapped by a branch or listed ``unsupported``.
_BRANCH_OR_OPEN = {
    "extras",
    "provenance",
    "keywords",
    "native_files",
    "stdout",
    "stderr",
    "wavefunction",
    "return_result",
}

#: (family, record kind) -> (prefix, pinned qcelemental class) for every
#: closed model whose fields the static rules must name.
_CLOSED_MODELS = {
    ("v1", "atomic"): [
        ("", qcel_v1.AtomicResult),
        ("molecule", qcel_v1.Molecule),
        ("properties", qcel_v1.AtomicResultProperties),
    ],
    ("v2", "atomic"): [
        ("", qcel_v2.AtomicResult),
        ("molecule", qcel_v2.Molecule),
        ("properties", qcel_v2.AtomicProperties),
        ("input_data", qcel_v2.AtomicInput),
        ("input_data.specification", qcel_v2.AtomicSpecification),
    ],
    ("v1", "optimization"): [
        ("", qcel_v1.OptimizationResult),
        ("initial_molecule", qcel_v1.Molecule),
        ("final_molecule", qcel_v1.Molecule),
        ("input_specification", qcel_v1.procedures.QCInputSpecification),
    ],
    ("v2", "optimization"): [
        ("", qcel_v2.OptimizationResult),
        ("final_molecule", qcel_v2.Molecule),
        ("properties", qcel_v2.OptimizationProperties),
        ("input_data", qcel_v2.OptimizationInput),
        ("input_data.initial_molecule", qcel_v2.Molecule),
        ("input_data.specification", qcel_v2.OptimizationSpecification),
        ("input_data.specification.specification", qcel_v2.AtomicSpecification),
    ],
    # The grid-keyed containers (final_molecules, final_energies /
    # scan_properties, optimization_history / scan_results) and the starting
    # molecules are branch-owned: the scan branch classifies every field
    # under them, per grid point (tckdb_qcschema.scan._classify_grid).
    ("v1", "torsion_drive"): [
        ("", qcel_v1.TorsionDriveResult),
        ("keywords", qcel_v1.procedures.TDKeywords),
        ("input_specification", qcel_v1.procedures.QCInputSpecification),
        ("optimization_spec", qcel_v1.procedures.OptimizationSpecification),
    ],
    ("v2", "torsion_drive"): [
        ("", qcel_v2.TorsionDriveResult),
        ("properties", qcel_v2.TorsionDriveProperties),
        ("input_data", qcel_v2.TorsionDriveInput),
        ("input_data.specification", qcel_v2.TorsionDriveSpecification),
        ("input_data.specification.keywords", qcel_v2.TorsionDriveKeywords),
        ("input_data.specification.specification", qcel_v2.OptimizationSpecification),
        ("input_data.specification.specification.specification", qcel_v2.AtomicSpecification),
    ],
}


@pytest.mark.parametrize(
    ("family", "kind"), sorted(_CLOSED_MODELS), ids=lambda v: str(v)
)
def test_rules_name_every_field_the_pinned_qcelemental_declares(family: str, kind: str) -> None:
    """Raising the qcelemental pin must not let a new field drift into
    ``unsupported`` unreviewed: a declared field with no rule turns this red.
    (At runtime such a field would be listed ``unsupported``, never dropped.)
    """
    rules = set(rule_paths(family, kind))
    known = rules | {a for r in rules for a in _ancestors(r)}
    missing = []
    for prefix, model_cls in _CLOSED_MODELS[(family, kind)]:
        for name in _fields(model_cls):
            path = f"{prefix}.{name}" if prefix else name
            if path in known or name in _BRANCH_OR_OPEN or path.endswith(".extras"):
                continue
            missing.append(path)
    assert missing == [], missing


def _ancestors(path: str) -> list[str]:
    parts = path.split(".")
    return [".".join(parts[:i]) for i in range(1, len(parts))]
