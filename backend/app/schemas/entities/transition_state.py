from typing import Self

from pydantic import BaseModel, Field, model_validator

from app.db.models.common import (
    TransitionStateEntryStatus,
    TransitionStateSelectionKind,
)
from app.schemas.common import (
    ORMBaseSchema,
    SchemaBase,
    TimestampedCreatedByReadSchema,
)
from app.schemas.utils import normalize_optional_text


# ---------------------------------------------------------------------------
# TransitionState (reaction-channel-level TS concept)
# ---------------------------------------------------------------------------


class TransitionStateBase(BaseModel):
    """Shared fields for a transition-state concept.

    :param reaction_entry_id: The reaction entry this TS belongs to.
    :param label: Optional human-readable label.
    :param note: Optional free-text note.
    """

    reaction_entry_id: int
    label: str | None = None
    note: str | None = None


class TransitionStateCreate(TransitionStateBase, SchemaBase):
    """Create schema for a transition-state concept."""

    entries: list["TransitionStateEntryCreate"] = Field(default_factory=list)

    @model_validator(mode="after")
    def normalize_text(self) -> Self:
        self.label = normalize_optional_text(self.label)
        self.note = normalize_optional_text(self.note)
        return self


class TransitionStateUpdate(SchemaBase):
    """Patch schema for a transition-state concept."""

    label: str | None = None
    note: str | None = None

    @model_validator(mode="after")
    def normalize_text(self) -> Self:
        self.label = normalize_optional_text(self.label)
        self.note = normalize_optional_text(self.note)
        return self


class TransitionStateRead(TransitionStateBase, TimestampedCreatedByReadSchema):
    """Read schema for a transition-state concept."""

    entries: list["TransitionStateEntryRead"] = Field(default_factory=list)
    selections: list["TransitionStateSelectionRead"] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# TransitionStateEntry (one candidate saddle-point geometry)
# ---------------------------------------------------------------------------


class TransitionStateEntryBase(BaseModel):
    """Shared fields for a transition-state entry.

    :param charge: Net charge of the TS structure.
    :param multiplicity: Spin multiplicity.
    :param unmapped_smiles: Optional SMILES for the TS (no atom maps).
    :param status: Curation status of this TS candidate.
    """

    charge: int
    multiplicity: int = Field(ge=1)
    unmapped_smiles: str | None = None
    status: TransitionStateEntryStatus = TransitionStateEntryStatus.optimized


class TransitionStateEntryCreate(TransitionStateEntryBase, SchemaBase):
    """Create schema for a transition-state entry."""

    transition_state_id: int | None = None

    @model_validator(mode="after")
    def normalize_text(self) -> Self:
        self.unmapped_smiles = normalize_optional_text(self.unmapped_smiles)
        return self


class TransitionStateEntryUpdate(SchemaBase):
    """Patch schema for a transition-state entry."""

    charge: int | None = None
    multiplicity: int | None = Field(default=None, ge=1)
    unmapped_smiles: str | None = None
    status: TransitionStateEntryStatus | None = None

    @model_validator(mode="after")
    def normalize_text(self) -> Self:
        self.unmapped_smiles = normalize_optional_text(self.unmapped_smiles)
        return self


class TransitionStateEntryRead(TransitionStateEntryBase, TimestampedCreatedByReadSchema):
    """Read schema for a transition-state entry."""

    transition_state_id: int


# ---------------------------------------------------------------------------
# TransitionStateSelection (curation layer)
# ---------------------------------------------------------------------------


class TransitionStateSelectionBase(BaseModel):
    """Shared fields for a transition-state selection.

    :param transition_state_id: The TS concept this selection belongs to.
    :param transition_state_entry_id: The selected TS entry.
    :param selection_kind: The purpose of this selection.
    :param note: Optional note.
    """

    transition_state_id: int
    transition_state_entry_id: int
    selection_kind: TransitionStateSelectionKind
    note: str | None = None


class TransitionStateSelectionCreate(TransitionStateSelectionBase, SchemaBase):
    """Create schema for a transition-state selection."""

    @model_validator(mode="after")
    def normalize_text(self) -> Self:
        self.note = normalize_optional_text(self.note)
        return self


class TransitionStateSelectionUpdate(SchemaBase):
    """Patch schema for a transition-state selection."""

    selection_kind: TransitionStateSelectionKind | None = None
    note: str | None = None

    @model_validator(mode="after")
    def normalize_text(self) -> Self:
        self.note = normalize_optional_text(self.note)
        return self


class TransitionStateSelectionRead(TransitionStateSelectionBase, TimestampedCreatedByReadSchema):
    """Read schema for a transition-state selection."""
