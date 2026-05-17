"""Roundtrip tests for ``ComputedSpeciesUploadRequest``."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tckdb_schemas.workflows.computed_species_upload import (
    ComputedSpeciesUploadRequest,
)


_H_GEOM = {"xyz_text": "1\nH atom\nH 0.0 0.0 0.0"}
_SOFTWARE = {"name": "Gaussian", "version": "16"}
_LOT = {"method": "B3LYP", "basis": "6-31G(d)"}


def _hydrogen_bundle_data() -> dict:
    return {
        "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
        "conformers": [
            {
                "key": "c0",
                "geometry": dict(_H_GEOM),
                "primary_calculation": {
                    "key": "opt0",
                    "type": "opt",
                    "level_of_theory": dict(_LOT),
                    "software_release": dict(_SOFTWARE),
                    "opt_result": {"converged": True},
                },
            }
        ],
    }


def test_minimal_bundle_validates_and_roundtrips() -> None:
    payload = ComputedSpeciesUploadRequest.model_validate(
        _hydrogen_bundle_data()
    )
    assert payload.species_entry.smiles == "[H]"
    assert payload.conformers[0].primary_calculation.key == "opt0"

    dumped = payload.model_dump(mode="json", exclude_none=True)
    revalidated = ComputedSpeciesUploadRequest.model_validate(dumped)

    assert revalidated.conformers[0].primary_calculation.type.value == "opt"
    assert (
        revalidated.conformers[0].primary_calculation.level_of_theory.method
        == "B3LYP"
    )


def test_bundle_with_thermo_validates_and_roundtrips() -> None:
    data = _hydrogen_bundle_data()
    data["thermo"] = {
        "h298_kj_mol": 218.0,
        "s298_j_mol_k": 114.7,
        "source_calculations": [
            {"calculation_key": "opt0", "role": "opt"},
        ],
    }
    payload = ComputedSpeciesUploadRequest.model_validate(data)
    assert payload.thermo is not None
    revalidated = ComputedSpeciesUploadRequest.model_validate(
        payload.model_dump(mode="json", exclude_none=True)
    )
    assert revalidated.thermo is not None
    assert revalidated.thermo.h298_kj_mol == pytest.approx(218.0)


def test_undefined_thermo_source_calculation_key_rejected() -> None:
    """Thermo source_calculations key must resolve into the bundle calc namespace."""
    data = _hydrogen_bundle_data()
    data["thermo"] = {
        "h298_kj_mol": 218.0,
        "source_calculations": [
            {"calculation_key": "missing_calc", "role": "opt"},
        ],
    }
    with pytest.raises(ValidationError) as exc:
        ComputedSpeciesUploadRequest.model_validate(data)
    assert "undefined" in str(exc.value)


def test_forbidden_db_id_in_parameters_json_rejected() -> None:
    """DR-0029 Requirement 1: bundle payload must not embed DB FK ids."""
    data = _hydrogen_bundle_data()
    data["conformers"][0]["primary_calculation"]["parameters_json"] = {
        "tckdb_extra": {"existing_calculation_id": 42},
    }
    with pytest.raises(ValidationError) as exc:
        ComputedSpeciesUploadRequest.model_validate(data)
    assert "must not include database" in str(exc.value)
