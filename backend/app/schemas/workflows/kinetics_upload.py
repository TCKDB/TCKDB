from typing import Self

from pydantic import Field, field_validator, model_validator

from app.chemistry.units import validate_a_units_for_molecularity
from app.db.models.common import (
    ActivationEnergyUnits,
    ArrheniusAUnits,
    KineticsModelKind,
    KineticsUncertaintyKind,
    ScientificOriginKind,
)
from app.schemas.common import SchemaBase
from app.schemas.fragments.identity import SpeciesEntryIdentityPayload
from app.schemas.reaction_family import find_canonical_reaction_family
from app.schemas.fragments.refs import LevelOfTheoryRef, SoftwareReleaseRef, WorkflowToolReleaseRef
from app.schemas.utils import normalize_optional_text
from app.schemas.workflows.literature_upload import LiteratureUploadRequest


class KineticsReactionParticipantUpload(SchemaBase):
    """Workflow-facing ordered participant slot for a kinetics upload.

    :param species_entry: Species-entry identity payload to resolve or create.
    :param note: Optional note stored on the structured participant row.
    """

    species_entry: SpeciesEntryIdentityPayload
    note: str | None = None

    @model_validator(mode="after")
    def normalize_note(self) -> Self:
        self.note = normalize_optional_text(self.note)
        return self


class KineticsReactionUpload(SchemaBase):
    """Workflow-facing reaction content embedded in a kinetics upload.

    :param reversible: Whether the uploaded reaction is reversible.
    :param reaction_family: Optional reaction-family label.
    :param reaction_family_source_note: Required when ``reaction_family`` is not a supported canonical family.
    :param reactants: Ordered structured participants on the reactant side.
    :param products: Ordered structured participants on the product side.
    """

    reversible: bool
    reaction_family: str | None = None
    reaction_family_source_note: str | None = None
    reactants: list[KineticsReactionParticipantUpload] = Field(min_length=1)
    products: list[KineticsReactionParticipantUpload] = Field(min_length=1)

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


class KineticsUploadRequest(SchemaBase):
    """Workflow-facing kinetics upload payload.

    The backend resolves reaction identity/entry, optional literature, and
    optional software/workflow provenance, then creates the kinetics row.

    For computed kinetics, ``energy_level_of_theory`` declares the SP level
    of theory used for the electronic energies.  The backend automatically
    finds the matching SP calculations on each reaction participant's
    conformer and links them as source calculations.  If the lookup is
    ambiguous (e.g., multiple conformers), the upload fails with a clear
    error.

    :param reaction: Reaction described by scientific content.
    :param scientific_origin: Scientific origin category.
    :param model_kind: Kinetics functional form.
    :param energy_level_of_theory: SP level of theory for source-calc auto-resolution.
    :param literature: Optional literature submission payload.
    :param software_release: Optional software provenance reference (fitting tool).
    :param workflow_tool_release: Optional workflow-tool provenance reference.
    :param a: Optional Arrhenius pre-exponential factor.
    :param a_units: Optional units for the pre-exponential factor.
    :param n: Optional temperature exponent.
    :param reported_ea: Optional activation energy in reported units.
    :param reported_ea_units: Units for ``reported_ea`` (required when reported).
    :param tmin_k: Optional minimum valid temperature in K.
    :param tmax_k: Optional maximum valid temperature in K.
    :param degeneracy: Optional reaction-path degeneracy.
    :param tunneling_model: Optional tunneling model label.
    :param note: Optional free-text note.
    """

    reaction: KineticsReactionUpload
    scientific_origin: ScientificOriginKind
    model_kind: KineticsModelKind = KineticsModelKind.modified_arrhenius

    energy_level_of_theory: LevelOfTheoryRef | None = None

    literature: LiteratureUploadRequest | None = None
    software_release: SoftwareReleaseRef | None = None
    workflow_tool_release: WorkflowToolReleaseRef | None = None

    a: float | None = None
    a_units: ArrheniusAUnits | None = None
    n: float | None = None
    reported_ea: float | None = None
    reported_ea_units: ActivationEnergyUnits | None = None

    a_uncertainty: float | None = None
    a_uncertainty_kind: KineticsUncertaintyKind | None = None
    n_uncertainty: float | None = None
    d_reported_ea: float | None = None

    tmin_k: float | None = Field(default=None, gt=0)
    tmax_k: float | None = Field(default=None, gt=0)

    degeneracy: float | None = None
    tunneling_model: str | None = None
    note: str | None = None

    @model_validator(mode="after")
    def normalize_optional_text_fields(self) -> Self:
        self.tunneling_model = normalize_optional_text(self.tunneling_model)
        self.note = normalize_optional_text(self.note)
        return self

    @model_validator(mode="after")
    def validate_reported_ea_pair(self) -> Self:
        has_value = self.reported_ea is not None
        has_units = self.reported_ea_units is not None
        if has_value != has_units:
            raise ValueError(
                "reported_ea and reported_ea_units must both be provided or both omitted."
            )
        return self

    @model_validator(mode="after")
    def validate_temperature_range(self) -> Self:
        if (
            self.tmin_k is not None
            and self.tmax_k is not None
            and self.tmin_k > self.tmax_k
        ):
            raise ValueError("tmin_k must be less than or equal to tmax_k.")
        return self

    @model_validator(mode="after")
    def validate_a_uncertainty_kind(self) -> Self:
        has_value = self.a_uncertainty is not None
        has_kind = self.a_uncertainty_kind is not None
        if has_value != has_kind:
            raise ValueError(
                "a_uncertainty and a_uncertainty_kind must both be provided "
                "or both omitted."
            )
        if (
            self.a_uncertainty_kind == KineticsUncertaintyKind.multiplicative
            and self.a_uncertainty is not None
            and self.a_uncertainty < 1.0
        ):
            raise ValueError(
                "Multiplicative a_uncertainty must be >= 1.0 (factor f, "
                "with the true value within [A/f, A*f])."
            )
        return self

    @model_validator(mode="after")
    def validate_a_units_vs_molecularity(self) -> Self:
        if self.a_units is None:
            return self
        molecularity = len(self.reaction.reactants)
        validate_a_units_for_molecularity(self.a_units, molecularity)
        return self
