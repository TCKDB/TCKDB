from typing import Self

from pydantic import BaseModel, Field, field_validator, model_validator

from app.db.models.common import (
    ArrheniusAUnits,
    KineticsCalculationRole,
    KineticsModelKind,
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
    :param literature_id: Optional linked literature row.
    :param workflow_tool_release_id: Optional workflow provenance.
    :param software_release_id: Optional software provenance.
    :param a: Optional Arrhenius pre-exponential factor.
    :param a_units: Optional units for the pre-exponential factor.
    :param n: Optional temperature exponent.
    :param ea_kj_mol: Optional activation energy in kJ/mol.
    :param tmin_k: Optional minimum valid temperature in K.
    :param tmax_k: Optional maximum valid temperature in K.
    :param degeneracy: Optional reaction-path degeneracy.
    :param tunneling_model: Optional tunneling model label.
    :param note: Optional free-text note.
    """

    reaction_entry_id: int
    scientific_origin: ScientificOriginKind
    model_kind: KineticsModelKind = KineticsModelKind.modified_arrhenius
    is_third_body: bool = False

    literature_id: int | None = None
    workflow_tool_release_id: int | None = None
    software_release_id: int | None = None

    a: float | None = None
    a_units: ArrheniusAUnits | None = None
    n: float | None = None
    ea_kj_mol: float | None = None

    a_uncertainty: float | None = None
    a_uncertainty_kind: KineticsUncertaintyKind | None = None
    n_uncertainty: float | None = None
    ea_uncertainty_kj_mol: float | None = None

    tmin_k: float | None = Field(default=None, gt=0)
    tmax_k: float | None = Field(default=None, gt=0)

    degeneracy: float | None = None
    tunneling_model: TunnelingModel | None = None
    pressure_context: PressureContext | None = None
    pressure_bar: float | None = Field(default=None, gt=0)
    note: str | None = None

    @field_validator("tunneling_model", mode="before")
    @classmethod
    def _normalize_tunneling(cls, v):
        return normalize_tunneling_model(v)

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
    is_third_body: bool | None = None

    literature_id: int | None = None
    workflow_tool_release_id: int | None = None
    software_release_id: int | None = None

    a: float | None = None
    a_units: ArrheniusAUnits | None = None
    n: float | None = None
    ea_kj_mol: float | None = None

    a_uncertainty: float | None = None
    a_uncertainty_kind: KineticsUncertaintyKind | None = None
    n_uncertainty: float | None = None
    ea_uncertainty_kj_mol: float | None = None

    tmin_k: float | None = Field(default=None, gt=0)
    tmax_k: float | None = Field(default=None, gt=0)

    degeneracy: float | None = None
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
