from typing import Any

from pydantic import BaseModel, Field

from app.db.models.common import (
    CalculationQuality,
    ConformerAssignmentScopeKind,
    ConformerSelectionKind,
    ScientificOriginKind,
)
from app.schemas.common import (
    SchemaBase,
    TimestampedCreatedByReadSchema,
)

# Conformer Group


class ConformerGroupBase(BaseModel):
    species_entry_id: int
    label: str | None = Field(default=None, max_length=64)
    note: str | None = None


class ConformerGroupCreate(ConformerGroupBase, SchemaBase):
    pass


class ConformerGroupUpdate(SchemaBase):
    species_entry_id: int | None = None
    label: str | None = Field(default=None, max_length=64)
    note: str | None = None


class ConformerGroupRead(ConformerGroupBase, TimestampedCreatedByReadSchema):
    """Read schema for one conformer group (basin)."""

    representative_fingerprint_json: dict[str, Any] | None = None
    representative_coords_json: list[Any] | None = None
    selections: list["ConformerSelectionRead"] = Field(default_factory=list)


class ConformerGroupSummaryRead(ConformerGroupRead):
    """Conformer-group summary including the count of attached observations.

    Used by the species-entry conformer-group listing so clients can see how many
    observations back each basin without eager-loading the full observations list.
    """

    observation_count: int = 0


class ConformerGroupDetailRead(ConformerGroupSummaryRead):
    """Conformer-group detail read including nested observations."""

    observations: list["ConformerObservationRead"] = Field(default_factory=list)


class SpeciesEntryConformerGroupsRead(BaseModel):
    """Basin-first conformer listing for one species entry.

    Groups are the primary browse unit; observations are reached by drilling into
    an individual conformer group via `/conformer-groups/{id}`.
    """

    species_entry_id: int
    conformer_group_count: int
    conformer_observation_count: int
    groups: list[ConformerGroupSummaryRead] = Field(default_factory=list)


class LowestSPConformerObservationRead(BaseModel):
    """Lowest qualifying SP conformer observation at a specified LoT.

    This is an explicitly contextual result, not a universal best-conformer
    claim. It only asserts that under the supplied `lot_id` (and optional
    `calculation_quality`), this observation had the lowest qualifying SP
    electronic energy among the species entry's conformer observations.
    """

    species_entry_id: int
    lot_id: int

    conformer_group_id: int
    conformer_observation_id: int
    calculation_id: int

    electronic_energy_hartree: float
    calculation_quality: CalculationQuality


class LowestSPConformerObservationResultRead(BaseModel):
    """Result wrapper for the lowest-SP conformer observation query.

    `result` is `None` when no qualifying SP calculation exists under the
    requested comparison context. This is a normal `200` response, not an
    error: the question is well-formed but simply has no answer yet.
    """

    species_entry_id: int
    lot_id: int
    calculation_quality: CalculationQuality | None = None
    result: LowestSPConformerObservationRead | None = None


# Conformer Observation


class ConformerObservationBase(BaseModel):
    conformer_group_id: int
    assignment_scheme_id: int | None = None
    scientific_origin: ScientificOriginKind = ScientificOriginKind.computed
    note: str | None = Field(default=None, description="Custom note provided by user")


class ConformerObservationCreate(ConformerObservationBase, SchemaBase):
    pass


class ConformerObservationUpdate(SchemaBase):
    conformer_group_id: int | None = None
    assignment_scheme_id: int | None = None
    scientific_origin: ScientificOriginKind | None = None
    note: str | None = None


class ConformerObservationRead(
    ConformerObservationBase, TimestampedCreatedByReadSchema
):
    """Read schema for one conformer observation within a basin."""

    torsion_fingerprint_json: dict[str, Any] | None = None


# Conformer Selection


class ConformerSelectionBase(BaseModel):
    conformer_group_id: int
    assignment_scheme_id: int | None = None
    selection_kind: ConformerSelectionKind
    note: str | None = None


class ConformerSelectionCreate(ConformerSelectionBase, SchemaBase):
    pass


class ConformerSelectionUpdate(SchemaBase):
    conformer_group_id: int | None = None
    assignment_scheme_id: int | None = None
    selection_kind: ConformerSelectionKind | None = None
    note: str | None = None


class ConformerSelectionRead(ConformerSelectionBase, TimestampedCreatedByReadSchema):
    pass


class ConformerAssignmentSchemeBase(BaseModel):
    name: str = Field(max_length=128)
    version: str = Field(max_length=64)
    scope: ConformerAssignmentScopeKind = ConformerAssignmentScopeKind.canonical
    description: str | None = None
    parameters_json: dict[str, Any] | None = None
    code_commit: str | None = Field(default=None, max_length=64)
    is_default: bool = False


class ConformerAssignmentSchemeCreate(ConformerAssignmentSchemeBase, SchemaBase):
    pass


class ConformerAssignmentSchemeUpdate(SchemaBase):
    name: str | None = Field(default=None, max_length=128)
    version: str | None = Field(default=None, max_length=64)
    scope: ConformerAssignmentScopeKind | None = None
    description: str | None = None
    parameters_json: dict[str, Any] | None = None
    code_commit: str | None = Field(default=None, max_length=64)
    is_default: bool | None = None


class ConformerAssignmentSchemeRead(
    ConformerAssignmentSchemeBase,
    TimestampedCreatedByReadSchema,
):
    pass
