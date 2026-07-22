"""Roundtrip tests for ``ComputedReactionUploadRequest``."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tckdb_schemas.workflows.computed_reaction_upload import (
    BundleKineticsIn,
    ComputedReactionUploadRequest,
)

_SOFTWARE_GAUSSIAN = {"name": "Gaussian", "version": "16"}
_LOT_DFT = {"method": "wb97xd", "basis": "def2tzvp"}

_XYZ_H = "1\nH atom\nH 0.0 0.0 0.0"
_XYZ_CH3 = (
    "4\nmethyl\n"
    "C  0.000  0.000  0.000\n"
    "H  1.080  0.000  0.000\n"
    "H -0.540  0.935  0.000\n"
    "H -0.540 -0.935  0.000"
)
_XYZ_CH4 = (
    "5\nmethane\n"
    "C  0.000  0.000  0.000\n"
    "H  0.629  0.629  0.629\n"
    "H -0.629 -0.629  0.629\n"
    "H -0.629  0.629 -0.629\n"
    "H  0.629 -0.629 -0.629"
)


def _species_block(key: str, smiles: str, charge: int, mult: int, xyz: str) -> dict:
    return {
        "key": key,
        "species_entry": {"smiles": smiles, "charge": charge, "multiplicity": mult},
        "conformers": [
            {
                "key": f"{key}-conf",
                "geometry": {"key": f"{key}-geom", "xyz_text": xyz},
                "calculation": {
                    "key": f"{key}-opt",
                    "type": "opt",
                    "software_release": dict(_SOFTWARE_GAUSSIAN),
                    "level_of_theory": dict(_LOT_DFT),
                    "opt_converged": True,
                },
            }
        ],
        "calculations": [],
    }


def _minimal_reaction_data() -> dict:
    return {
        "species": [
            _species_block("ch3", "[CH3]", 0, 2, _XYZ_CH3),
            _species_block("h", "[H]", 0, 2, _XYZ_H),
            _species_block("ch4", "C", 0, 1, _XYZ_CH4),
        ],
        "reversible": True,
        "reactant_keys": ["ch3", "h"],
        "product_keys": ["ch4"],
    }


def test_minimal_reaction_validates_and_roundtrips() -> None:
    payload = ComputedReactionUploadRequest.model_validate(_minimal_reaction_data())
    assert {s.key for s in payload.species} == {"ch3", "h", "ch4"}
    assert payload.reactant_keys == ["ch3", "h"]
    assert payload.product_keys == ["ch4"]

    dumped = payload.model_dump(mode="json", exclude_none=True)
    revalidated = ComputedReactionUploadRequest.model_validate(dumped)
    assert revalidated.reactant_keys == ["ch3", "h"]
    assert revalidated.product_keys == ["ch4"]


def test_reaction_with_ts_and_kinetics_roundtrips() -> None:
    data = _minimal_reaction_data()
    data["transition_state"] = {
        "charge": 0,
        "multiplicity": 2,
        "geometry": {"key": "ts-geom", "xyz_text": _XYZ_CH3},
        "calculation": {
            "key": "ts-opt",
            "type": "opt",
            "software_release": dict(_SOFTWARE_GAUSSIAN),
            "level_of_theory": dict(_LOT_DFT),
            "opt_converged": True,
        },
    }
    data["kinetics"] = [
        {
            "reactant_keys": ["ch3", "h"],
            "product_keys": ["ch4"],
            "a": 1.2e13,
            "a_units": "cm3_mol_s",
            "n": 0.5,
            "reported_ea": 10.0,
            "reported_ea_units": "kj_mol",
            "tmin_k": 300.0,
            "tmax_k": 2500.0,
        }
    ]
    payload = ComputedReactionUploadRequest.model_validate(data)
    assert payload.transition_state is not None
    assert len(payload.kinetics) == 1

    revalidated = ComputedReactionUploadRequest.model_validate(
        payload.model_dump(mode="json", exclude_none=True)
    )
    assert revalidated.kinetics[0].a_units.value == "cm3_mol_s"
    assert revalidated.kinetics[0].degeneracy_convention.value == "unknown"


def test_kinetics_degeneracy_convention_roundtrips() -> None:
    data = _minimal_reaction_data()
    data["kinetics"] = [
        {
            "reactant_keys": ["ch3", "h"],
            "product_keys": ["ch4"],
            "a": 1.2e13,
            "a_units": "cm3_mol_s",
            "n": 0.5,
            "reported_ea": 10.0,
            "reported_ea_units": "kj_mol",
            "degeneracy": 3.0,
            "degeneracy_convention": "not_applied",
        }
    ]
    payload = ComputedReactionUploadRequest.model_validate(data)
    dumped = payload.model_dump(mode="json", exclude_none=True)
    assert dumped["kinetics"][0]["degeneracy_convention"] == "not_applied"
    assert (
        ComputedReactionUploadRequest.model_validate(dumped)
        .kinetics[0]
        .degeneracy_convention.value
        == "not_applied"
    )


@pytest.mark.parametrize("value", [None, 1.0e-12, 1, 2.5])
def test_bundle_kinetics_accepts_optional_finite_positive_degeneracy(value) -> None:
    kinetics = BundleKineticsIn(
        reactant_keys=["ch3", "h"],
        product_keys=["ch4"],
        degeneracy=value,
    )
    assert kinetics.degeneracy == value


@pytest.mark.parametrize(
    ("value", "error_type"),
    [
        (0, "greater_than"),
        (-1.0, "greater_than"),
        (float("nan"), "finite_number"),
        (float("inf"), "finite_number"),
        (float("-inf"), "finite_number"),
    ],
)
def test_bundle_kinetics_rejects_non_positive_or_nonfinite_degeneracy(
    value,
    error_type,
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        BundleKineticsIn(
            reactant_keys=["ch3", "h"],
            product_keys=["ch4"],
            degeneracy=value,
        )

    assert [(error["loc"], error["type"]) for error in exc_info.value.errors()] == [
        (("degeneracy",), error_type)
    ]


def test_non_canonical_family_without_source_note_rejected() -> None:
    """Non-canonical reaction families require a source note."""
    data = _minimal_reaction_data()
    data["reaction_family"] = "Made_Up_Family"
    with pytest.raises(ValidationError) as exc:
        ComputedReactionUploadRequest.model_validate(data)
    assert "reaction_family_source_note is required" in str(exc.value)


def test_self_dependency_rejected() -> None:
    """A calc cannot list itself as a depends_on parent."""
    data = _minimal_reaction_data()
    ch3 = data["species"][0]
    ch3["conformers"][0]["calculation"]["depends_on"] = [
        {"parent_calculation_key": "ch3-opt", "role": "optimized_from"}
    ]
    with pytest.raises(ValidationError) as exc:
        ComputedReactionUploadRequest.model_validate(data)
    assert "cannot" in str(exc.value) and "itself" in str(exc.value)
