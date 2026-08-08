"""Workflow-facing upload schema for standalone transition-state uploads.

Supports uploading a transition state with an embedded reaction description
(reactants/products by scientific content), a required primary optimisation
calculation, and optional additional calculations (freq, sp, irc).

The backend resolves the reaction identity, creates the TS concept and entry,
resolves the geometry, and persists calculations.
"""

from typing import Self

from pydantic import Field, field_validator, model_validator
from tckdb_schemas.fragments.ts_validation_evidence import (
    TransitionStateValidationEvidenceIn,
    validate_ts_evidence_set,
)
from tckdb_schemas.stationary_point import (
    StationaryPointFinding,
    raise_for_blocking_findings,
)

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
    validation_evidence: list[TransitionStateValidationEvidenceIn] = Field(
        default_factory=list,
        description=(
            "Structured IRC evidence that this saddle point connects the "
            "declared reactants and products. Optional but strongly "
            "recommended: a deposit without it succeeds and returns a "
            "'transition_state_missing_irc_evidence' upload warning. "
            "This payload has no calculation-key namespace, so evidence binds "
            "to the upload's single 'irc' additional calculation; "
            "source_calculation_key must be omitted."
        ),
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
    def validate_validation_evidence(self) -> Self:
        """Bind IRC evidence to this upload's single ``irc`` calculation.

        There are no bundle-local calculation keys here, so the locator is the
        calculation itself: evidence is only depositable alongside exactly one
        ``irc`` additional calculation. That is not a limitation invented
        here — ``transition_state_validation_evidence`` stores a single
        ``reconstruction_calculation_id``, so one evidence record can only ever
        name one calculation.
        """
        if not self.validation_evidence:
            return self

        for record in self.validation_evidence:
            if record.source_calculation_key is not None:
                raise ValueError(
                    "source_calculation_key is not accepted on a standalone "
                    "transition-state upload: it has no calculation-key "
                    "namespace, and evidence binds to the upload's single "
                    "'irc' additional calculation."
                )

        irc_calculations = [
            calc
            for calc in self.additional_calculations
            if calc.type == CalculationType.irc
        ]
        if len(irc_calculations) != 1:
            raise ValueError(
                "validation_evidence requires exactly one additional "
                f"calculation of type 'irc' to bind to; found "
                f"{len(irc_calculations)}."
            )

        validate_ts_evidence_set(
            self.validation_evidence,
            subject_label=self.label or "transition state",
            xyz_text=self.geometry.xyz_text,
            reactant_count=len(self.reaction.reactants),
            product_count=len(self.reaction.products),
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

    def stationary_point_findings(self) -> list[StationaryPointFinding]:
        """Judge this saddle point against its own frequency evidence.

        The whole payload is one transition state, so every frequency
        result in it describes that saddle point — there is no species
        entry here whose zero-imaginary-mode expectation could be
        confused with the TS's one.
        """
        findings: list[StationaryPointFinding] = []
        for label, calc in [
            ("primary_opt", self.primary_opt),
            *(
                (f"additional_calculations[{i}]", c)
                for i, c in enumerate(self.additional_calculations)
            ),
        ]:
            findings.extend(
                calc.transition_state_frequency_findings(
                    location=f"{label}.freq_result"
                )
            )
        return findings

    @model_validator(mode="after")
    def validate_reaction_coordinate_contract(self) -> Self:
        """Refuse frequency evidence with no usable reaction coordinate.

        Definitional, therefore blocking (ADR 0008, narrowed by ADR
        0012: at least one imaginary mode, exactly one designated the
        reaction coordinate, and no undeclared mode stiff enough to make
        that designation meaningless).
        """
        raise_for_blocking_findings(self.stationary_point_findings())
        return self
