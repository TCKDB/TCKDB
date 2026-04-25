from pydantic import BaseModel, Field, field_validator

from app.db.models.common import ReactionRole
from app.schemas.common import (
    ORMBaseSchema,
    SchemaBase,
    TimestampedCreatedByReadSchema,
    TimestampedReadSchema,
)
from app.schemas.reaction_family import normalize_reaction_family

# -----------------------------
# ReactionFamily (reaction_family)
# -----------------------------


class ReactionFamilyBase(BaseModel):
    name: str = Field(min_length=1)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = normalize_reaction_family(value)
        if normalized is None:
            raise ValueError("reaction_family name must not be blank.")
        return normalized


class ReactionFamilyCreate(ReactionFamilyBase, SchemaBase):
    pass


class ReactionFamilyUpdate(SchemaBase):
    name: str | None = Field(default=None, min_length=1)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        return normalize_reaction_family(value)


class ReactionFamilyRead(ReactionFamilyBase, TimestampedReadSchema):
    pass


# -----------------------------
# ChemReaction (chem_reaction)
# -----------------------------


class ChemReactionBase(BaseModel):
    reversible: bool
    stoichiometry_hash: str | None = Field(default=None, min_length=64, max_length=64)
    reaction_family_id: int | None = None
    reaction_family_raw: str | None = None
    reaction_family_source_note: str | None = None


class ChemReactionCreate(ChemReactionBase, SchemaBase):
    pass


class ChemReactionUpdate(SchemaBase):
    reversible: bool | None = None
    stoichiometry_hash: str | None = Field(default=None, min_length=64, max_length=64)
    reaction_family_id: int | None = None


class ChemReactionRead(ChemReactionBase, TimestampedReadSchema):
    id: int


# ---------------------------------------
# ReactionParticipant (reaction_participant)
# ---------------------------------------


class ReactionParticipantBase(BaseModel):
    reaction_id: int
    species_id: int
    role: ReactionRole
    stoichiometry: int = Field(ge=1)


class ReactionParticipantCreate(ReactionParticipantBase, SchemaBase):
    pass


class ReactionParticipantUpdate(SchemaBase):
    stoichiometry: int | None = Field(default=None, ge=1)


class ReactionParticipantRead(ReactionParticipantBase, ORMBaseSchema):
    pass


# -----------------------------
# ReactionEntry (reaction_entry)
# -----------------------------


class ReactionEntryBase(BaseModel):
    reaction_id: int


class ReactionEntryCreate(ReactionEntryBase, SchemaBase):
    pass


class ReactionEntryUpdate(SchemaBase):
    reaction_id: int | None = None


class ReactionEntryRead(ReactionEntryBase, TimestampedCreatedByReadSchema):
    pass


# ------------------------------------------------------------------
# ReactionEntryStructureParticipant (reaction_entry_structure_participant)
# ------------------------------------------------------------------


class ReactionEntryStructureParticipantBase(BaseModel):
    reaction_entry_id: int
    species_entry_id: int
    role: ReactionRole
    participant_index: int = Field(ge=1)
    note: str | None = None


class ReactionEntryStructureParticipantCreate(
    ReactionEntryStructureParticipantBase, SchemaBase
):
    pass


class ReactionEntryStructureParticipantUpdate(SchemaBase):
    species_entry_id: int | None = None
    role: ReactionRole | None = None
    participant_index: int | None = Field(default=None, ge=1)
    note: str | None = None


class ReactionEntryStructureParticipantRead(
    ReactionEntryStructureParticipantBase, TimestampedCreatedByReadSchema
):
    id: int
