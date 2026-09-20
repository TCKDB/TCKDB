"""Unit tests pinning which raw QCSchema field feeds each mapped value.

These exist because several mutation-table entries are not numerically
observable through the real fixture corpus: Psi4 populates both
``return_result`` and ``properties.return_energy`` identically for a
driver-``energy`` job, so swapping which one ``mapping.py`` reads from
would not change the corpus test's output for any *real* fixture. Each
test here constructs a minimal, deliberately partial document that makes
the two sources diverge (one present, the other absent), so using the
wrong one is observable.
"""

from __future__ import annotations

import json

import pytest

from tckdb_qcschema.errors import E_ESS_PROVENANCE_UNAVAILABLE, QCSchemaAdapterError
from tckdb_qcschema.mapping import build_conformer_upload_payload
from tckdb_qcschema.reader import read_document
from tckdb_qcschema.uploader import sha256_bytes

from conftest import load_case


def _minimal_v1_energy_document(*, return_result: float, return_energy: float | None) -> bytes:
    properties = {}
    if return_energy is not None:
        properties["return_energy"] = return_energy
    document = {
        "molecule": {
            "symbols": ["He"],
            "geometry": [0.0, 0.0, 0.0],
            "molecular_charge": 0,
            "molecular_multiplicity": 1,
            "identifiers": {"smiles": "[He]"},
        },
        "driver": "energy",
        "model": {"method": "hf", "basis": "sto-3g"},
        "keywords": {},
        "provenance": {"creator": "TestProgram", "version": "0.0", "routine": "test"},
        "return_result": return_result,
        "success": True,
        "properties": properties,
    }
    return json.dumps(document).encode()


def test_energy_driver_maps_return_result_not_return_energy():
    """driver=energy with return_energy ABSENT must still map cleanly.

    If the mapper used ``properties.return_energy`` instead of
    ``return_result`` for a driver-energy record, this would fail (no
    energy to map) even though ``return_result`` -- the field the plan's
    mapping table names for driver energy -- is right there.
    """
    raw = _minimal_v1_energy_document(return_result=-2.5, return_energy=None)
    record = read_document(raw)
    payload, _report = build_conformer_upload_payload(
        record,
        raw_bytes=raw,
        raw_artifact_filename="synthetic.qcschema.json",
        raw_artifact_sha256=sha256_bytes(raw),
        declared_smiles=None,
    )
    assert payload["calculation"]["sp_result"]["electronic_energy_hartree"] == pytest.approx(-2.5)


def test_energy_driver_value_is_exactly_return_result_when_both_present():
    raw = _minimal_v1_energy_document(return_result=-2.5, return_energy=-2.5)
    record = read_document(raw)
    payload, _report = build_conformer_upload_payload(
        record,
        raw_bytes=raw,
        raw_artifact_filename="synthetic.qcschema.json",
        raw_artifact_sha256=sha256_bytes(raw),
        declared_smiles=None,
    )
    assert payload["calculation"]["sp_result"]["electronic_energy_hartree"] == pytest.approx(-2.5)


# ---------------------------------------------------------------------------
# OptimizationResult: ESS (software_release) vs optimizer (workflow_tool_release)
# ---------------------------------------------------------------------------
#
# provenance.creator on an OptimizationResult names the OPTIMIZER (geomeTRIC),
# not the ESS that actually computed the energies/gradients it consumed.
# Mapping that top-level provenance straight into software_release (the
# pre-fix behaviour) would silently record "Psi4 optimised this molecule at
# geomeTRIC" -- the software and workflow tool swapped. The real fixtures
# (built by a real Psi4-via-geomeTRIC run through qcengine) are the primary
# evidence; the two synthetic cases below cover the trajectory-dropped
# fallback and the "neither available" refusal, which no real fixture
# exercises.


@pytest.mark.parametrize("case", ["optimization_v1", "optimization_v2"])
def test_optimization_software_is_ess_workflow_tool_is_optimizer(case: str) -> None:
    """Mutation: swap software_release/workflow_tool_release -> this goes red."""
    raw, _meta = load_case(case)
    record = read_document(raw)
    payload, _report = build_conformer_upload_payload(
        record,
        raw_bytes=raw,
        raw_artifact_filename=f"{case}.qcschema.json",
        raw_artifact_sha256=sha256_bytes(raw),
        declared_smiles="O",
    )
    calc = payload["calculation"]
    assert calc["software_release"]["name"] == "Psi4"
    assert calc["software_release"]["version"] == "1.11"
    assert calc["workflow_tool_release"]["name"] == "geomeTRIC"
    assert calc["workflow_tool_release"]["version"] == "1.1.1"
    assert calc["parameters_json"]["tckdb_qcschema"]["software_source"] == (
        "trajectory[-1].provenance"
    )


def _drop_trajectory(document: dict, *, family: str) -> dict:
    document = json.loads(json.dumps(document))  # deep copy
    if family == "v1":
        document["trajectory"] = []
    else:
        document["trajectory_results"] = []
    return document


@pytest.mark.parametrize(
    "case,family", [("optimization_v1", "v1"), ("optimization_v2", "v2")]
)
def test_optimization_falls_back_to_program_keyword_when_trajectory_dropped(
    case: str, family: str
) -> None:
    """Protocols dropping the trajectory still leaves the program name."""
    raw, _meta = load_case(case)
    document = _drop_trajectory(json.loads(raw), family=family)
    mutated_raw = json.dumps(document).encode()

    record = read_document(mutated_raw)
    payload, _report = build_conformer_upload_payload(
        record,
        raw_bytes=mutated_raw,
        raw_artifact_filename=f"{case}.qcschema.json",
        raw_artifact_sha256=sha256_bytes(mutated_raw),
        declared_smiles="O",
    )
    calc = payload["calculation"]
    assert calc["software_release"]["name"] == "psi4"
    assert calc["software_release"].get("version") is None
    assert calc["workflow_tool_release"]["name"] == "geomeTRIC"
    assert calc["parameters_json"]["tckdb_qcschema"]["software_source"] == "keywords.program"


@pytest.mark.parametrize(
    "case,family", [("optimization_v1", "v1"), ("optimization_v2", "v2")]
)
def test_optimization_refuses_ess_provenance_unavailable(case: str, family: str) -> None:
    """Trajectory dropped AND no program keyword -> refuse, never guess."""
    raw, _meta = load_case(case)
    document = _drop_trajectory(json.loads(raw), family=family)
    if family == "v1":
        document["keywords"] = {}
    else:
        document["input_data"]["specification"]["specification"]["program"] = ""
    mutated_raw = json.dumps(document).encode()

    record = read_document(mutated_raw)
    with pytest.raises(QCSchemaAdapterError) as excinfo:
        build_conformer_upload_payload(
            record,
            raw_bytes=mutated_raw,
            raw_artifact_filename=f"{case}.qcschema.json",
            raw_artifact_sha256=sha256_bytes(mutated_raw),
            declared_smiles="O",
        )
    assert excinfo.value.code == E_ESS_PROVENANCE_UNAVAILABLE


# ---------------------------------------------------------------------------
# Provenance retention allowlist (drop personal fields like username)
# ---------------------------------------------------------------------------


def test_provenance_username_never_reaches_the_payload():
    """Mutation: retain the whole provenance dict verbatim -> this goes red."""
    raw, _meta = load_case("energy_v1")
    record = read_document(raw)
    payload, _report = build_conformer_upload_payload(
        record,
        raw_bytes=raw,
        raw_artifact_filename="energy_v1.qcschema.json",
        raw_artifact_sha256=sha256_bytes(raw),
        declared_smiles="O",
    )
    retained = payload["calculation"]["parameters_json"]["tckdb_qcschema"]["provenance"]
    assert "username" not in retained
    # The allowlisted fields the real fixture actually carries must survive.
    assert retained["creator"] == "Psi4"
    assert retained["version"] == "1.11"
    assert retained["routine"] == "psi4.schema_runner.run_qcschema"
    assert retained["hostname"] == "archlinux"
    assert retained["nthreads"] == 12
    assert retained["memory"] == pytest.approx(25.564)
    assert retained["wall_time"] == pytest.approx(2.1200902462005615)
    # And nothing outside the allowlist -- not just username -- survives.
    assert set(retained) <= {
        "routine", "hostname", "nthreads", "memory", "wall_time", "creator", "version",
    }
    dropped = payload["calculation"]["parameters_json"]["tckdb_qcschema"]["mapping_report"][
        "unsupported"
    ]
    assert "provenance.username" in dropped
    assert "provenance.cpu" in dropped
    assert "provenance.qcengine_version" in dropped
