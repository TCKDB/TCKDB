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

from tckdb_qcschema.mapping import build_conformer_upload_payload
from tckdb_qcschema.reader import read_document
from tckdb_qcschema.uploader import sha256_bytes


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
