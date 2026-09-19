"""The emitted payload validates against the wire package (C-Q1)."""

from __future__ import annotations

import pytest
from tckdb_schemas.fragments.calculation import HessianPayload
from tckdb_schemas.workflows.conformer_upload import ConformerUploadRequest

from tckdb_qcschema.mapping import build_conformer_upload_payload
from tckdb_qcschema.reader import read_document
from tckdb_qcschema.uploader import sha256_bytes

from conftest import load_case


@pytest.mark.parametrize(
    "case",
    ["energy_v1", "energy_v2", "gradient_v1", "hessian_v1", "hessian_v2", "optimization_v1", "optimization_v2"],
)
def test_emitted_payload_validates_against_conformer_upload_request(case):
    raw, _meta = load_case(case)
    record = read_document(raw)
    sha = sha256_bytes(raw)
    payload, _report = build_conformer_upload_payload(
        record,
        raw_bytes=raw,
        raw_artifact_filename=f"{case}.qcschema.json",
        raw_artifact_sha256=sha,
        declared_smiles="O",
    )
    validated = ConformerUploadRequest.model_validate(payload)
    assert validated.calculation.software_release is not None
    assert validated.calculation.level_of_theory.method


@pytest.mark.parametrize("case", ["hessian_v1", "hessian_v2"])
def test_hessian_payload_validates_standalone(case):
    raw, _meta = load_case(case)
    record = read_document(raw)
    sha = sha256_bytes(raw)
    payload, _report = build_conformer_upload_payload(
        record,
        raw_bytes=raw,
        raw_artifact_filename=f"{case}.qcschema.json",
        raw_artifact_sha256=sha,
        declared_smiles="O",
    )
    hessian_dict = payload["calculation"]["hessian"]
    validated = HessianPayload.model_validate(hessian_dict)
    assert validated.source.value == "uploaded"
    natoms = int(payload["calculation"]["input_geometries"][0]["xyz_text"].strip().splitlines()[0])
    expected_len = (3 * natoms) * (3 * natoms + 1) // 2
    assert len(validated.lower_triangle_hartree_bohr2) == expected_len

    # No freq_result, no derived modes -- a Hessian document is stored as
    # the matrix only.
    assert payload["calculation"].get("freq_result") is None
