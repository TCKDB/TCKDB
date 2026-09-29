"""TorsionDrive import: the mapping and each of its refusals.

The route cases themselves (exact point counts, energies and coordinate
values) are pinned in ``test_corpus.py``. This module drives the refusals
that need a targeted edit of a real document -- each edit is one field,
made on the parsed ``torsiondrive_v2`` fixture and re-serialised, so the
document still validates as a qcelemental ``TorsionDriveResult`` and only
the named property is wrong.
"""

from __future__ import annotations

import json
import math

import pytest

from tckdb_qcschema.errors import (
    E_SCAN_COORDINATE_GEOMETRY_MISMATCH,
    E_SCAN_EXTRA_CONSTRAINTS_UNSUPPORTED,
    E_SCAN_GRID_INVALID,
    E_SCAN_PARENT_MISMATCH,
    E_SCAN_PARENT_OPT_REQUIRED,
    QCSchemaAdapterError,
)
from tckdb_qcschema.mapping import build_conformer_upload_payload
from tckdb_qcschema.reader import read_document
from tckdb_qcschema.scan import (
    DIHEDRAL_TOLERANCE_DEGREES,
    build_scan_bundle_payload,
    dihedral_degrees,
    parse_grid_key,
)
from tckdb_qcschema.uploader import scan_bundle_idempotency_key, sha256_bytes

from conftest import load_case

TD_V2 = "torsiondrive_v2"
PARENT_V2 = "torsiondrive_parent_opt_v2"


def _bundle(td_raw: bytes, parent_raw: bytes | None, *, smiles: str | None = "OO"):
    return build_scan_bundle_payload(
        read_document(td_raw),
        raw_bytes=td_raw,
        raw_artifact_filename="td.qcschema.json",
        raw_artifact_sha256=sha256_bytes(td_raw),
        parent_record=read_document(parent_raw) if parent_raw is not None else None,
        parent_raw_bytes=parent_raw,
        parent_artifact_filename="opt.qcschema.json" if parent_raw is not None else None,
        parent_artifact_sha256=sha256_bytes(parent_raw) if parent_raw is not None else None,
        declared_smiles=smiles,
    )


def _edited(edit) -> bytes:
    doc = json.loads(load_case(TD_V2)[0])
    edit(doc)
    return json.dumps(doc).encode()


def _refusal(td_raw: bytes, parent_raw: bytes | None = None) -> str:
    parent_raw = load_case(PARENT_V2)[0] if parent_raw is None else parent_raw
    with pytest.raises(QCSchemaAdapterError) as excinfo:
        _bundle(td_raw, parent_raw)
    return excinfo.value.code


def _scan(bundle) -> dict:
    return bundle.payload["conformers"][0]["additional_calculations"][0]


# --- the route ------------------------------------------------------------


def test_v2_drive_maps_to_a_scan_on_the_parent_opt() -> None:
    bundle = _bundle(load_case(TD_V2)[0], load_case(PARENT_V2)[0])
    scan = _scan(bundle)
    assert bundle.dimension == 1 and bundle.point_count == 4
    assert scan["software_release"] == {"name": "Psi4", "version": "1.11"}
    assert scan["workflow_tool_release"] == {"name": "TorsionDrive", "version": "v1.2.0"}
    assert scan["level_of_theory"] == {"method": "hf", "basis": "sto-3g"}
    (coordinate,) = scan["scan_result"]["coordinates"]
    assert (coordinate["atom1_index"], coordinate["atom2_index"], coordinate["atom3_index"], coordinate["atom4_index"]) == (3, 1, 2, 4)
    assert coordinate["step_size"] == 90.0
    assert [p["point_index"] for p in scan["scan_result"]["points"]] == [1, 2, 3, 4]
    assert [p["coordinate_values"][0]["coordinate_value"] for p in scan["scan_result"]["points"]] == [
        -90.0,
        0.0,
        90.0,
        180.0,
    ]
    assert len(scan["input_geometries"]) == 1
    qc = scan["parameters_json"]["tckdb_qcschema"]
    assert qc["record_kind"] == "torsion_drive"
    assert qc["parent_opt_canonical_document_sha256"] == read_document(load_case(PARENT_V2)[0]).canonical_sha256
    assert "username" not in qc["provenance"]
    retained = bundle.report.to_dict()["retained_only"]
    assert any(entry.startswith("scan_results") for entry in retained)
    observed = {(p["section"], p["raw_key"]): p["raw_value"] for p in scan["parameters"]}
    assert observed[("qcschema.optimization_spec", "program")] == "geometric"
    assert observed[("qcschema.torsiondrive", "program")] == "torsiondrive"


def test_dihedral_ranges_and_thresholds_are_carried() -> None:
    def edit(doc):
        kw = doc["input_data"]["specification"]["keywords"]
        kw["dihedral_ranges"] = [[-90, 180]]
        kw["energy_upper_limit"] = 0.05

    scan = _scan(_bundle(_edited(edit), load_case(PARENT_V2)[0]))
    (coordinate,) = scan["scan_result"]["coordinates"]
    assert (coordinate["start_value"], coordinate["end_value"]) == (-90.0, 180.0)
    observed = {(p["section"], p["raw_key"]): p["raw_value"] for p in scan["parameters"]}
    assert float(observed[("qcschema.torsiondrive.keywords", "energy_upper_limit")]) == 0.05


def test_bundle_key_depends_on_both_documents() -> None:
    td = read_document(load_case(TD_V2)[0])
    p1 = read_document(load_case(PARENT_V2)[0])
    p2 = read_document(load_case("torsiondrive_parent_opt_v1")[0])
    k1 = scan_bundle_idempotency_key(td.canonical_sha256, p1.canonical_sha256)
    assert k1 == scan_bundle_idempotency_key(td.canonical_sha256, p1.canonical_sha256)
    assert k1 != scan_bundle_idempotency_key(td.canonical_sha256, p2.canonical_sha256)
    assert k1.startswith("qcschema:") and k1.endswith(":computed-species")


# --- refusals -------------------------------------------------------------


def test_no_parent_is_refused() -> None:
    assert _refusal_no_parent() == E_SCAN_PARENT_OPT_REQUIRED


def _refusal_no_parent() -> str:
    with pytest.raises(QCSchemaAdapterError) as excinfo:
        _bundle(load_case(TD_V2)[0], None)
    return excinfo.value.code


def test_conformer_mapper_refuses_a_drive() -> None:
    raw = load_case(TD_V2)[0]
    with pytest.raises(QCSchemaAdapterError) as excinfo:
        build_conformer_upload_payload(
            read_document(raw),
            raw_bytes=raw,
            raw_artifact_filename="td.qcschema.json",
            raw_artifact_sha256=sha256_bytes(raw),
            declared_smiles="OO",
        )
    assert excinfo.value.code == E_SCAN_PARENT_OPT_REQUIRED


def test_parent_that_is_not_an_optimization_is_refused() -> None:
    assert _refusal(load_case(TD_V2)[0], load_case("energy_v1")[0]) == E_SCAN_PARENT_MISMATCH


def test_parent_of_another_molecule_is_refused() -> None:
    other = load_case("torsiondrive_2d_parent_opt_v2")[0]
    assert _refusal(load_case(TD_V2)[0], other) == E_SCAN_PARENT_MISMATCH


def test_parent_the_drive_did_not_start_from_is_refused() -> None:
    def edit(doc):
        doc["input_data"]["initial_molecule"][0]["geometry"][0] += 1e-3

    assert _refusal(_edited(edit)) == E_SCAN_PARENT_MISMATCH


def test_parent_with_another_multiplicity_is_refused() -> None:
    def edit(doc):
        doc["input_data"]["initial_molecule"][0]["molecular_multiplicity"] = 3

    assert _refusal(_edited(edit)) == E_SCAN_PARENT_MISMATCH


def test_grid_key_its_geometry_contradicts_is_refused() -> None:
    def edit(doc):
        for block in ("final_molecules", "scan_properties", "scan_results"):
            doc[block]["91"] = doc[block].pop("90")

    assert _refusal(_edited(edit)) == E_SCAN_COORDINATE_GEOMETRY_MISMATCH


@pytest.mark.parametrize("bad_key", ["ninety", "90,0", "90.5"])
def test_malformed_grid_key_is_refused(bad_key: str) -> None:
    def edit(doc):
        for block in ("final_molecules", "scan_properties", "scan_results"):
            doc[block][bad_key] = doc[block].pop("90")

    assert _refusal(_edited(edit)) == E_SCAN_GRID_INVALID


def test_grid_point_without_energy_is_refused() -> None:
    def edit(doc):
        doc["scan_properties"]["90"]["return_energy"] = None

    assert _refusal(_edited(edit)) == E_SCAN_GRID_INVALID


def test_energy_without_final_molecule_is_refused() -> None:
    def edit(doc):
        del doc["final_molecules"]["90"]

    assert _refusal(_edited(edit)) == E_SCAN_GRID_INVALID


def test_extra_optimizer_constraints_are_refused() -> None:
    def edit(doc):
        doc["input_data"]["specification"]["specification"]["keywords"]["constraints"] = {
            "freeze": [{"type": "distance", "indices": [0, 1]}]
        }

    assert _refusal(_edited(edit)) == E_SCAN_EXTRA_CONSTRAINTS_UNSUPPORTED


# --- the pieces -----------------------------------------------------------


@pytest.mark.parametrize("key", ["180,-60", "180 -60", "[180, -60]", " 180 , -60 "])
def test_grid_key_spellings(key: str) -> None:
    assert parse_grid_key(key, 2) == (180.0, -60.0)


def test_dihedral_of_a_known_geometry() -> None:
    # H-O-O-H with the second H rotated 90 degrees about the O-O axis.
    coords = [(0, 0, 0), (1.4, 0, 0), (-0.3, 0.9, 0), (1.7, 0, 0.9)]
    assert dihedral_degrees(coords, (2, 0, 1, 3)) == pytest.approx(90.0, abs=1e-9)
    assert math.isclose(DIHEDRAL_TOLERANCE_DEGREES, 1e-3)


def test_near_collinear_dihedral_is_not_checkable() -> None:
    coords = [(-1, 0, 0), (0, 0, 0), (1, 0, 0), (1, 1, 0)]
    with pytest.raises(ValueError):
        dihedral_degrees(coords, (0, 1, 2, 3))
