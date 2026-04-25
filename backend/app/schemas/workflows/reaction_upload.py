from typing import Self

from pydantic import Field, field_validator, model_validator

from app.schemas.common import SchemaBase
from app.schemas.fragments.identity import SpeciesEntryIdentityPayload
from app.schemas.reaction_family import find_canonical_reaction_family
from app.schemas.utils import normalize_optional_text


class ReactionParticipantUpload(SchemaBase):
    """Workflow-facing ordered participant slot for a reaction entry.

    :param species_entry_id: Existing species-entry id to reuse directly.
    :param species_entry: Species-entry identity payload to resolve or create.
    :param note: Optional note stored on the structured participant row.
    """

    species_entry_id: int | None = None
    species_entry: SpeciesEntryIdentityPayload | None = None
    note: str | None = None

    @model_validator(mode="after")
    def validate_reference_choice(self) -> Self:
        self.note = normalize_optional_text(self.note)
        if (self.species_entry_id is None) == (self.species_entry is None):
            raise ValueError(
                "Provide exactly one of species_entry_id or species_entry."
            )
        return self


class ReactionUploadRequest(SchemaBase):
    """Workflow-facing reaction upload payload.

    The backend resolves the graph identity into ``ChemReaction`` and
    ``ReactionParticipant`` rows, then creates a new ``ReactionEntry`` with
    ordered structured participants for the resolved species-entry forms.

    :param reversible: Whether the uploaded reaction is reversible.
    :param reaction_family: Optional reaction-family label.
    :param reaction_family_source_note: Required when ``reaction_family`` is not a supported canonical family.
    :param reactants: Ordered structured participants on the reactant side.
    :param products: Ordered structured participants on the product side.
    """

    reversible: bool
    reaction_family: str | None = None
    reaction_family_source_note: str | None = None
    reactants: list[ReactionParticipantUpload] = Field(min_length=1)
    products: list[ReactionParticipantUpload] = Field(min_length=1)

    @field_validator("reaction_family", "reaction_family_source_note")
    @classmethod
    def normalize_reaction_family(cls, value: str | None) -> str | None:
        return normalize_optional_text(value)

    @model_validator(mode="after")
    def validate_reaction_family(self) -> Self:
        if self.reaction_family is None:
            if self.reaction_family_source_note is not None:
                raise ValueError(
                    "reaction_family_source_note requires reaction_family."
                )
            return self

        if find_canonical_reaction_family(self.reaction_family) is None:
            if self.reaction_family_source_note is None:
                raise ValueError(
                    "reaction_family_source_note is required when reaction_family "
                    "is not a supported canonical family."
                )
        return self
