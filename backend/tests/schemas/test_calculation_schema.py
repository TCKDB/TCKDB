import pytest
from pydantic import ValidationError

from app.db.models.common import CalculationQuality, CalculationType
from app.schemas.entities.calculation import (
    CalculationCreateResolved,
    CalculationDependencyCreate,
    CalculationUpdate,
)
from app.schemas.fragments.calculation import CalculationCreateRequest


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
