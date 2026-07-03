"""Workflow-facing upload schema for standalone transition-state uploads.

Supports uploading a transition state with an embedded reaction description
(reactants/products by scientific content), a required primary optimisation
calculation, and optional additional calculations (freq, sp, irc).

The backend resolves the reaction identity, creates the TS concept and entry,
resolves the geometry, and persists calculations.
"""

from typing import Self

from pydantic import Field, field_validator, model_validator

from app.db.models.common import CalculationType
from app.schemas.common import SchemaBase
from app.schemas.fragments.calculation import CalculationWithResultsPayload
from app.schemas.fragments.geometry import GeometryPayload
from app.schemas.fragments.identity import SpeciesEntryIdentityPayload
from app.schemas.reaction_family import find_canonical_reaction_family
from app.schemas.utils import normalize_optional_text

# ---------------------------------------------------------------------------
# Embedded reaction content (no FK IDs — resolved by the workflow)
# ---------------------------------------------------------------------------


class TSReactionParticipantUpload(SchemaBase):
    """One participant slot in the TS reaction description.

    :param species_entry: Species-entry identity payload to resolve or create.
    :param note: Optional note stored on the structured participant row.
    """

    species_entry: SpeciesEntryIdentityPayload
    note: str | None = None

    @model_validator(mode="after")
    def normalize_note(self) -> Self:
        self.note = normalize_optional_text(self.note)
        return self


class TSReactionUpload(SchemaBase):
    """Embedded reaction content for a transition-state upload.

    :param reversible: Whether the reaction is reversible.
    :param reaction_family: Optional reaction-family label.
    :param reaction_family_source_note: Required when the family is non-canonical.
    :param reactants: Ordered reactant participants.
    :param products: Ordered product participants.
    """

    reversible: bool
    reaction_family: str | None = None
    reaction_family_source_note: str | None = None
    reactants: list[TSReactionParticipantUpload] = Field(min_length=1)
    products: list[TSReactionParticipantUpload] = Field(min_length=1)

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


# ---------------------------------------------------------------------------
# Top-level upload request
# ---------------------------------------------------------------------------

_ALLOWED_ADDITIONAL_TYPES = frozenset(
    {
        CalculationType.freq,
        CalculationType.sp,
        CalculationType.irc,
        CalculationType.path_search,
    }
)


class TransitionStateUploadRequest(SchemaBase):
    """Workflow-facing transition-state upload payload.

    The backend resolves the reaction from the embedded content, creates a
    ``TransitionState`` concept and ``TransitionStateEntry``, resolves the
    geometry and calculation provenance, and optionally attaches additional
    calculations.

    :param reaction: Reaction described by scientific content (reactants/products).
    :param charge: Net charge of the TS structure.
    :param multiplicity: Spin multiplicity of the TS structure.
    :param unmapped_smiles: Optional SMILES for the TS (no atom maps).
    :param geometry: Saddle-point geometry payload (XYZ text).
    :param primary_opt: Required primary optimisation calculation.
    :param additional_calculations: Optional freq / sp / irc / path_search
        calculations. A ``path_search`` additional calculation models a
        TS-guess generator (NEB, GSM, ...) and is wired as the parent of
        the primary opt via ``calculation_dependency.role = optimized_from``.
    :param label: Optional human-readable label for the TS concept.
    :param note: Optional free-text note on the TS concept.
    """

    reaction: TSReactionUpload
    charge: int
    multiplicity: int = Field(ge=1)
    unmapped_smiles: str | None = None

    geometry: GeometryPayload
    primary_opt: CalculationWithResultsPayload
    additional_calculations: list[CalculationWithResultsPayload] = Field(
        default_factory=list
    )

    label: str | None = None
    note: str | None = None

    @model_validator(mode="after")
    def normalize_text(self) -> Self:
        self.label = normalize_optional_text(self.label)
        self.note = normalize_optional_text(self.note)
        self.unmapped_smiles = normalize_optional_text(self.unmapped_smiles)
        return self

    @model_validator(mode="after")
    def validate_primary_opt_is_opt(self) -> Self:
        if self.primary_opt.type != CalculationType.opt:
            raise ValueError(
                f"primary_opt must have type 'opt', "
                f"got '{self.primary_opt.type.value}'."
            )
        return self

    @model_validator(mode="after")
    def validate_additional_calculation_types(self) -> Self:
        for calc in self.additional_calculations:
            if calc.type not in _ALLOWED_ADDITIONAL_TYPES:
                raise ValueError(
                    f"Additional calculation type '{calc.type.value}' is not "
                    f"allowed. Expected one of: "
                    f"{', '.join(t.value for t in sorted(_ALLOWED_ADDITIONAL_TYPES, key=lambda t: t.value))}."
                )
        return self
