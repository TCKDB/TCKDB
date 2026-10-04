from typing import Self

from pydantic import BaseModel, Field, field_validator, model_validator
from tckdb_schemas.kinetics_declarations import (
    StoredKineticsApplicabilityDeclaration,
    StoredKineticsProtocolDeclaration,
)

from app.db.models.common import (
    ArrheniusAUnits,
    KineticsCalculationRole,
    KineticsDegeneracyConvention,
    KineticsDirection,
    KineticsModelKind,
    KineticsRepresentationRole,
    KineticsUncertaintyKind,
    PressureContext,
    ScientificOriginKind,
    TunnelingModel,
)
from app.schemas.common import (
    ORMBaseSchema,
    SchemaBase,
    TimestampedCreatedByReadSchema,
)
from app.schemas.utils import normalize_tunneling_model


class KineticsSourceCalculationBase(BaseModel):
    """Shared fields for a kinetics source-calculation link.

    :param calculation_id: Referenced calculation row.
    :param role: Semantic role of the supporting calculation.
    """

    calculation_id: int
    role: KineticsCalculationRole


class KineticsSourceCalculationCreate(KineticsSourceCalculationBase, SchemaBase):
    """Nested create payload for a kinetics source-calculation link."""


class KineticsSourceCalculationUpdate(SchemaBase):
    """Patch schema for a kinetics source-calculation link.

    This schema assumes the parent kinetics id and calculation id come from the route.

    :param role: Optional replacement role.
    """

    role: KineticsCalculationRole | None = None


class KineticsSourceCalculationRead(
    KineticsSourceCalculationBase,
    ORMBaseSchema,
):
    """Read schema for a kinetics source-calculation link."""

    kinetics_id: int


class KineticsBase(BaseModel):
    """Shared scalar fields for a kinetics record.

    :param reaction_entry_id: Owning reaction-entry id.
    :param scientific_origin: Scientific origin category for this kinetics record.
    :param model_kind: Kinetics functional form.
    :param is_third_body: True for a simple ``+M`` third-body reaction (no falloff).
    :param determination_id: The determination this record is a representation of; set
        together with ``representation_role`` or not at all.
    :param representation_role: This record's role in that determination.
    :param applicability_declaration: The depositor's versioned applicability declaration,
        in its stored form (public refs only); None when none was declared.
    :param protocol_declaration: The depositor's versioned protocol declaration, in its
        stored form; None when none was declared.
    :param energy_level_of_theory_id: Level of theory the depositor declared for the
        record's energies, stored as declared; None when none was declared.
    :param literature_id: Optional linked literature row.
    :param workflow_tool_release_id: Optional workflow provenance.
    :param software_release_id: Optional software provenance.
    :param a: Optional Arrhenius pre-exponential factor.
    :param a_units: Optional units for the pre-exponential factor.
    :param n: Optional temperature exponent.
    :param t0_k: Reference temperature T0 in K (``k = A (T/T0)^n exp(-Ea/RT)``).
    :param ea_kj_mol: Optional activation energy in kJ/mol.
    :param tmin_k: Optional minimum valid temperature in K.
    :param tmax_k: Optional maximum valid temperature in K.
    :param degeneracy: Optional finite, strictly positive reaction-path degeneracy.
    :param degeneracy_convention: Whether degeneracy is already included in the rate.
    :param tunneling_model: Optional tunneling model label.
    :param note: Optional free-text note.
    """

    reaction_entry_id: int
    scientific_origin: ScientificOriginKind
    model_kind: KineticsModelKind = KineticsModelKind.modified_arrhenius
    direction: KineticsDirection | None = None
    is_third_body: bool = False

    determination_id: int | None = None
    representation_role: KineticsRepresentationRole | None = None
    applicability_declaration: StoredKineticsApplicabilityDeclaration | None = None
    protocol_declaration: StoredKineticsProtocolDeclaration | None = None

    energy_level_of_theory_id: int | None = None

    literature_id: int | None = None
    workflow_tool_release_id: int | None = None
    software_release_id: int | None = None
    network_kinetics_id: int | None = None

    a: float | None = None
    a_units: ArrheniusAUnits | None = None
    n: float | None = None
    t0_k: float = Field(default=1.0, gt=0, le=10000.0, allow_inf_nan=False)
    ea_kj_mol: float | None = None

    a_uncertainty: float | None = None
    a_uncertainty_kind: KineticsUncertaintyKind | None = None
    n_uncertainty: float | None = None
    ea_uncertainty_kj_mol: float | None = None

    tmin_k: float | None = Field(default=None, gt=0)
    tmax_k: float | None = Field(default=None, gt=0)

    degeneracy: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    degeneracy_convention: KineticsDegeneracyConvention = (
        KineticsDegeneracyConvention.unknown
    )
    tunneling_model: TunnelingModel | None = None
    pressure_context: PressureContext | None = None
    pressure_bar: float | None = Field(default=None, gt=0)
    note: str | None = None

    @field_validator("tunneling_model", mode="before")
    @classmethod
    def _normalize_tunneling(cls, v):
        return normalize_tunneling_model(v)

    @model_validator(mode="after")
    def validate_determination_iff_role(self) -> Self:
        """A determination and the role in it are stated together or not at all.

        The same rule as ``ck_kinetics_determination_iff_role``, stated on the resolved
        payload so a caller that builds one directly is refused before the database is.
        Whether the determination is of this record's reaction entry and direction is not
        decidable here; see ``assert_kinetics_declaration_columns``.
        """
        if (self.determination_id is None) != (self.representation_role is None):
            raise ValueError(
                "determination_id and representation_role are both present or both absent."
            )
        return self

    @model_validator(mode="after")
    def validate_pressure_context(self) -> Self:
        if (
            self.pressure_context == PressureContext.apparent_at_pressure
            and self.pressure_bar is None
        ):
            raise ValueError(
                "pressure_context='apparent_at_pressure' requires pressure_bar."
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


class KineticsCreate(KineticsBase, SchemaBase):
    """Create schema for a kinetics record.

    Nested creation is supported for source-calculation links.
    Parent foreign keys for those child rows are taken from the created kinetics
    resource rather than from the payload.
    """

    source_calculations: list[KineticsSourceCalculationCreate] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def validate_unique_source_calculations(self) -> Self:
        keys = [
            (source.calculation_id, source.role) for source in self.source_calculations
        ]
        if len(set(keys)) != len(keys):
            raise ValueError(
                "Kinetics source calculations must be unique by (calculation_id, role)."
            )
        return self


class KineticsUpdate(SchemaBase):
    """Patch schema for a kinetics record."""

    reaction_entry_id: int | None = None
    scientific_origin: ScientificOriginKind | None = None
    model_kind: KineticsModelKind | None = None
    direction: KineticsDirection | None = None
    is_third_body: bool | None = None

    literature_id: int | None = None
    workflow_tool_release_id: int | None = None
    software_release_id: int | None = None
    network_kinetics_id: int | None = None

    a: float | None = None
    a_units: ArrheniusAUnits | None = None
    n: float | None = None
    t0_k: float | None = Field(default=None, gt=0, le=10000.0, allow_inf_nan=False)
    ea_kj_mol: float | None = None

    a_uncertainty: float | None = None
    a_uncertainty_kind: KineticsUncertaintyKind | None = None
    n_uncertainty: float | None = None
    ea_uncertainty_kj_mol: float | None = None

    tmin_k: float | None = Field(default=None, gt=0)
    tmax_k: float | None = Field(default=None, gt=0)

    degeneracy: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    degeneracy_convention: KineticsDegeneracyConvention | None = None
    tunneling_model: TunnelingModel | None = None
    pressure_context: PressureContext | None = None
    pressure_bar: float | None = Field(default=None, gt=0)
    note: str | None = None

    @field_validator("tunneling_model", mode="before")
    @classmethod
    def _normalize_tunneling(cls, v):
        return normalize_tunneling_model(v)

    @model_validator(mode="after")
    def validate_temperature_range_when_complete(self) -> Self:
        if (
            self.tmin_k is not None
            and self.tmax_k is not None
            and self.tmin_k > self.tmax_k
        ):
            raise ValueError("tmin_k must be less than or equal to tmax_k.")
        return self


class KineticsRead(KineticsBase, TimestampedCreatedByReadSchema):
    """Read schema for a kinetics record."""

    source_calculations: list[KineticsSourceCalculationRead] = Field(
        default_factory=list
    )
