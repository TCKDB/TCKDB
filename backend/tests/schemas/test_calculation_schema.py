import pytest
from pydantic import ValidationError

from app.db.models.common import CalculationQuality, CalculationType, ConstraintKind
from app.schemas.entities.calculation import (
    CalculationCreateResolved,
    CalculationDependencyCreate,
    CalculationUpdate,
)
from app.schemas.fragments.calculation import (
    CalculationConstraintCreate,
    CalculationCreateRequest,
    CalculationWithResultsPayload,
)


def test_calculation_create_request_requires_exactly_one_owner() -> None:
    with pytest.raises(ValidationError):
        CalculationCreateRequest(type=CalculationType.sp)

    with pytest.raises(ValidationError):
        CalculationCreateRequest(
            type=CalculationType.sp,
            species_entry_id=1,
            transition_state_entry_id=2,
        )

    request = CalculationCreateRequest(
        type=CalculationType.freq,
        quality=CalculationQuality.curated,
        species_entry_id=1,
        software_release={"name": "Gaussian"},
        level_of_theory={"method": "wB97X-D"},
    )

    assert request.species_entry_id == 1
    assert request.transition_state_entry_id is None


def test_calculation_create_request_normalizes_nested_refs() -> None:
    request = CalculationCreateRequest(
        type=CalculationType.sp,
        species_entry_id=1,
        software_release={
            "name": "  Gaussian  ",
            "version": " 16 ",
            "revision": "  C.01 ",
        },
        workflow_tool_release={
            "name": "  ARC ",
            "version": " 1.0 ",
            "git_commit": " abc123 ",
        },
        level_of_theory={
            "method": "  wB97X-D  ",
            "basis": " def2-TZVP ",
        },
    )

    assert request.software_release is not None
    assert request.software_release.name == "Gaussian"
    assert request.software_release.version == "16"
    assert request.software_release.revision == "C.01"

    assert request.workflow_tool_release is not None
    assert request.workflow_tool_release.name == "ARC"
    assert request.workflow_tool_release.version == "1.0"
    assert request.workflow_tool_release.git_commit == "abc123"

    assert request.level_of_theory is not None
    assert request.level_of_theory.method == "wB97X-D"
    assert request.level_of_theory.basis == "def2-TZVP"


def test_calculation_create_request_requires_software_and_level_of_theory() -> None:
    with pytest.raises(ValidationError):
        CalculationCreateRequest(
            type=CalculationType.sp,
            species_entry_id=1,
            level_of_theory={"method": "wB97X-D"},
        )

    with pytest.raises(ValidationError):
        CalculationCreateRequest(
            type=CalculationType.sp,
            species_entry_id=1,
            software_release={"name": "Gaussian"},
        )


def test_calculation_create_resolved_requires_exactly_one_owner() -> None:
    resolved = CalculationCreateResolved(
        type=CalculationType.sp,
        transition_state_entry_id=3,
        software_release_id=4,
        lot_id=5,
    )

    assert resolved.transition_state_entry_id == 3

    with pytest.raises(ValidationError):
        CalculationCreateResolved(
            type=CalculationType.sp,
            species_entry_id=1,
            transition_state_entry_id=2,
            software_release_id=4,
            lot_id=5,
        )


def test_calculation_update_does_not_accept_owner_fields() -> None:
    with pytest.raises(ValidationError):
        CalculationUpdate(species_entry_id=1)


def test_calculation_dependency_schema_rejects_self_edge() -> None:
    with pytest.raises(ValidationError):
        CalculationDependencyCreate(
            parent_calculation_id=7,
            child_calculation_id=7,
            dependency_role="freq_on",
        )


# ---------------------------------------------------------------------------
# CalculationConstraintCreate arity validation
# ---------------------------------------------------------------------------


_CARTESIAN_OK = {
    "constraint_index": 1,
    "constraint_kind": ConstraintKind.cartesian_atom,
    "atom1_index": 3,
}
_BOND_OK = {
    "constraint_index": 2,
    "constraint_kind": ConstraintKind.bond,
    "atom1_index": 1,
    "atom2_index": 2,
    "target_value": 1.45,
}
_ANGLE_OK = {
    "constraint_index": 3,
    "constraint_kind": ConstraintKind.angle,
    "atom1_index": 1,
    "atom2_index": 2,
    "atom3_index": 3,
}
_DIHEDRAL_OK = {
    "constraint_index": 4,
    "constraint_kind": ConstraintKind.dihedral,
    "atom1_index": 1,
    "atom2_index": 2,
    "atom3_index": 3,
    "atom4_index": 4,
}
_IMPROPER_OK = {
    "constraint_index": 5,
    "constraint_kind": ConstraintKind.improper,
    "atom1_index": 1,
    "atom2_index": 2,
    "atom3_index": 3,
    "atom4_index": 4,
}


@pytest.mark.parametrize(
    "payload",
    [_CARTESIAN_OK, _BOND_OK, _ANGLE_OK, _DIHEDRAL_OK, _IMPROPER_OK],
)
def test_calculation_constraint_accepts_valid_arity(payload: dict) -> None:
    constraint = CalculationConstraintCreate(**payload)
    assert constraint.constraint_kind == ConstraintKind(payload["constraint_kind"])


def test_calculation_constraint_cartesian_rejects_extra_atoms() -> None:
    with pytest.raises(ValidationError, match="cartesian_atom"):
        CalculationConstraintCreate(
            constraint_index=1,
            constraint_kind=ConstraintKind.cartesian_atom,
            atom1_index=1,
            atom2_index=2,
        )


def test_calculation_constraint_bond_rejects_missing_atom2() -> None:
    with pytest.raises(ValidationError, match="bond"):
        CalculationConstraintCreate(
            constraint_index=1,
            constraint_kind=ConstraintKind.bond,
            atom1_index=1,
        )


def test_calculation_constraint_bond_rejects_extra_atoms() -> None:
    with pytest.raises(ValidationError, match="bond"):
        CalculationConstraintCreate(
            constraint_index=1,
            constraint_kind=ConstraintKind.bond,
            atom1_index=1,
            atom2_index=2,
            atom3_index=3,
        )


def test_calculation_constraint_angle_rejects_missing_atom3() -> None:
    with pytest.raises(ValidationError, match="angle"):
        CalculationConstraintCreate(
            constraint_index=1,
            constraint_kind=ConstraintKind.angle,
            atom1_index=1,
            atom2_index=2,
        )


def test_calculation_constraint_dihedral_rejects_missing_atom4() -> None:
    with pytest.raises(ValidationError, match="dihedral"):
        CalculationConstraintCreate(
            constraint_index=1,
            constraint_kind=ConstraintKind.dihedral,
            atom1_index=1,
            atom2_index=2,
            atom3_index=3,
        )


def test_calculation_constraint_improper_rejects_missing_atom4() -> None:
    with pytest.raises(ValidationError, match="improper"):
        CalculationConstraintCreate(
            constraint_index=1,
            constraint_kind=ConstraintKind.improper,
            atom1_index=1,
            atom2_index=2,
            atom3_index=3,
        )


def test_calculation_constraint_rejects_duplicate_atom_indices() -> None:
    with pytest.raises(ValidationError, match="distinct"):
        CalculationConstraintCreate(
            constraint_index=1,
            constraint_kind=ConstraintKind.bond,
            atom1_index=1,
            atom2_index=1,
        )


def test_calculation_constraint_rejects_constraint_index_below_1() -> None:
    with pytest.raises(ValidationError):
        CalculationConstraintCreate(
            constraint_index=0,
            constraint_kind=ConstraintKind.cartesian_atom,
            atom1_index=1,
        )


def test_calculation_constraint_rejects_atom_index_below_1() -> None:
    with pytest.raises(ValidationError):
        CalculationConstraintCreate(
            constraint_index=1,
            constraint_kind=ConstraintKind.cartesian_atom,
            atom1_index=0,
        )


# ---------------------------------------------------------------------------
# CalculationWithResultsPayload constraints field
# ---------------------------------------------------------------------------


def _minimal_calc_payload(**overrides) -> dict:
    base = {
        "type": CalculationType.opt,
        "software_release": {"name": "Gaussian"},
        "level_of_theory": {"method": "wB97X-D"},
    }
    base.update(overrides)
    return base


def test_calc_with_results_payload_accepts_constraints_for_non_scan_type() -> None:
    payload = CalculationWithResultsPayload(
        **_minimal_calc_payload(
            type=CalculationType.opt,
            constraints=[_BOND_OK, _DIHEDRAL_OK],
        )
    )
    assert len(payload.constraints) == 2
    assert payload.constraints[0].constraint_kind == ConstraintKind.bond
    assert payload.constraints[1].constraint_kind == ConstraintKind.dihedral


def test_calc_with_results_payload_rejects_duplicate_constraint_index() -> None:
    duplicate = dict(_BOND_OK)
    duplicate["constraint_index"] = _BOND_OK["constraint_index"]
    with pytest.raises(ValidationError, match="constraint_index"):
        CalculationWithResultsPayload(
            **_minimal_calc_payload(
                constraints=[_BOND_OK, duplicate],
            )
        )


def test_calc_with_results_payload_constraints_default_empty() -> None:
    payload = CalculationWithResultsPayload(**_minimal_calc_payload())
    assert payload.constraints == []
