"""Backend hybrid module — upload-facing calculation fragments live in
``tckdb_schemas.fragments.calculation``; the FK-owner mixin and the
internal-resolved request shape stay backend-side because they carry
backend FK ids.
"""

from typing import Self

from pydantic import Field, model_validator

from app.db.models.common import (
    CalculationQuality,
    CalculationType,
)
from app.schemas.common import SchemaBase
from app.schemas.fragments.refs import (
    LevelOfTheoryRef,
    SoftwareReleaseRef,
    WorkflowToolReleaseRef,
)
from tckdb_schemas.fragments.calculation import (  # noqa: F401  (re-exported)
    CalculationConstraintCreate,
    CalculationConstraintPayload,
    CalculationParameterObservation,
    CalculationPayload,
    CalculationWithResultsPayload,
    FreqResultPayload,
    FrequencyModePayload,
    IRCPointPayload,
    IRCResultPayload,
    OptResultPayload,
    OutputGeometryEntry,
    PathSearchPointPayload,
    PathSearchResultPayload,
    SCFStabilityPayload,
    SPResultPayload,
    WavefunctionDiagnosticPayload,
)


class CalculationOwnerRequiredMixin:
    """Shared validator for calculation schemas that require exactly one owner."""

    @model_validator(mode="after")
    def validate_exactly_one_owner(self) -> Self:
        owner_count = sum(
            value is not None
            for value in (self.species_entry_id, self.transition_state_entry_id)
        )
        if owner_count != 1:
            raise ValueError(
                "Exactly one of species_entry_id or transition_state_entry_id must be set"
            )
        return self


class CalculationCreateRequest(CalculationOwnerRequiredMixin, SchemaBase):
    """Reusable upload-oriented request for calculation creation.

    :param type: Calculation type.
    :param quality: Curation quality flag.
    :param species_entry_id: Species-entry owner id when the calculation belongs to a species entry.
    :param transition_state_entry_id: Transition-state-entry owner id when applicable.
    :param software_release: Required software release reference.
    :param workflow_tool_release: Optional workflow tool provenance reference.
    :param level_of_theory: Required level-of-theory reference.
    :param literature_id: Optional literature provenance id.
    """

    type: CalculationType
    quality: CalculationQuality = CalculationQuality.raw

    species_entry_id: int | None = None
    transition_state_entry_id: int | None = None

    software_release: SoftwareReleaseRef
    workflow_tool_release: WorkflowToolReleaseRef | None = None
    level_of_theory: LevelOfTheoryRef

    literature_id: int | None = None
