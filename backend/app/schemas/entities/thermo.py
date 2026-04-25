"""Entity schemas for thermochemistry models.

Covers: Thermo (parent), ThermoPoint (tabulated values),
ThermoNASA (polynomial coefficients), and ThermoSourceCalculation
(provenance links to supporting calculations).
"""

from typing import Self

from pydantic import BaseModel, Field, model_validator

from app.db.models.common import ScientificOriginKind, ThermoCalculationRole
from app.schemas.common import (
    ORMBaseSchema,
    SchemaBase,
    TimestampedCreatedByReadSchema,
)


# ---------------------------------------------------------------------------
# Thermo point (tabulated values at a temperature)
# ---------------------------------------------------------------------------


class ThermoPointBase(BaseModel):
    """Shared fields for a tabulated thermo data point.

    :param temperature_k: Temperature in K.
    :param cp_j_mol_k: Heat capacity at constant pressure in J/(mol*K).
    :param h_kj_mol: Enthalpy in kJ/mol.
    :param s_j_mol_k: Entropy in J/(mol*K).
    :param g_kj_mol: Gibbs free energy in kJ/mol.
    """

    temperature_k: float = Field(gt=0)
    cp_j_mol_k: float | None = None
    h_kj_mol: float | None = None
    s_j_mol_k: float | None = None
    g_kj_mol: float | None = None


class ThermoPointCreate(ThermoPointBase, SchemaBase):
    """Nested create payload for a thermo data point."""


class ThermoPointUpdate(SchemaBase):
    """Patch schema for a thermo data point."""

    cp_j_mol_k: float | None = None
    h_kj_mol: float | None = None
    s_j_mol_k: float | None = None
    g_kj_mol: float | None = None


class ThermoPointRead(ThermoPointBase, ORMBaseSchema):
    """Read schema for a thermo data point."""

    thermo_id: int


# ---------------------------------------------------------------------------
# Thermo NASA polynomial coefficients
# ---------------------------------------------------------------------------


class ThermoNASABase(BaseModel):
    """Shared fields for NASA polynomial coefficients.

    Temperature bounds must be all-or-none and ordered: t_low < t_mid < t_high.

    :param t_low: Low temperature bound (K).
    :param t_mid: Mid temperature bound (K).
    :param t_high: High temperature bound (K).
    :param a1..a7: Low-temperature polynomial coefficients.
    :param b1..b7: High-temperature polynomial coefficients.
    """

    t_low: float | None = Field(default=None, gt=0)
    t_mid: float | None = Field(default=None, gt=0)
    t_high: float | None = Field(default=None, gt=0)

    a1: float | None = None
    a2: float | None = None
    a3: float | None = None
    a4: float | None = None
    a5: float | None = None
    a6: float | None = None
    a7: float | None = None

    b1: float | None = None
    b2: float | None = None
    b3: float | None = None
    b4: float | None = None
    b5: float | None = None
    b6: float | None = None
    b7: float | None = None

    @model_validator(mode="after")
    def validate_temperature_bounds(self) -> Self:
        temps = [self.t_low, self.t_mid, self.t_high]
        nones = sum(t is None for t in temps)
        if nones not in (0, 3):
            raise ValueError(
                "Temperature bounds must be all provided or all omitted."
            )
        if nones == 0:
            if self.t_mid <= self.t_low:
                raise ValueError("t_mid must be greater than t_low.")
            if self.t_high <= self.t_mid:
                raise ValueError("t_high must be greater than t_mid.")
        return self


class ThermoNASACreate(ThermoNASABase, SchemaBase):
    """Nested create payload for NASA polynomial coefficients."""


class ThermoNASAUpdate(ThermoNASABase, SchemaBase):
    """Patch schema for NASA polynomial coefficients.

    Reuses the base validator since temperature bounds are tightly coupled.
    """


class ThermoNASARead(ThermoNASABase, ORMBaseSchema):
    """Read schema for NASA polynomial coefficients."""

    thermo_id: int


# ---------------------------------------------------------------------------
# Thermo source calculation link
# ---------------------------------------------------------------------------


class ThermoSourceCalculationBase(BaseModel):
    """Shared fields for a thermo → calculation link.

    :param calculation_id: Referenced calculation row.
    :param role: Scientific role of the calculation.
    """

    calculation_id: int
    role: ThermoCalculationRole


class ThermoSourceCalculationCreate(ThermoSourceCalculationBase, SchemaBase):
    """Nested create payload for a thermo source-calculation link."""


class ThermoSourceCalculationUpdate(SchemaBase):
    """Patch schema for a thermo source-calculation link."""

    role: ThermoCalculationRole | None = None


class ThermoSourceCalculationRead(ThermoSourceCalculationBase, ORMBaseSchema):
    """Read schema for a thermo source-calculation link."""

    thermo_id: int


# ---------------------------------------------------------------------------
# Thermo (parent)
# ---------------------------------------------------------------------------


class ThermoBase(BaseModel):
    """Shared scalar fields for a thermo record.

    :param species_entry_id: Owning species-entry id.
    :param scientific_origin: Scientific origin category.
    :param literature_id: Optional linked literature row.
    :param workflow_tool_release_id: Optional workflow provenance.
    :param software_release_id: Optional software provenance.
    :param h298_kj_mol: Standard enthalpy of formation at 298 K (kJ/mol).
    :param s298_j_mol_k: Standard entropy at 298 K (J/(mol*K)).
    :param h298_uncertainty_kj_mol: Uncertainty in H298 (kJ/mol).
    :param s298_uncertainty_j_mol_k: Uncertainty in S298 (J/(mol*K)).
    :param tmin_k: Optional minimum valid temperature in K.
    :param tmax_k: Optional maximum valid temperature in K.
    :param note: Optional free-text note.
    """

    species_entry_id: int
    scientific_origin: ScientificOriginKind

    literature_id: int | None = None
    workflow_tool_release_id: int | None = None
    software_release_id: int | None = None

    h298_kj_mol: float | None = None
    s298_j_mol_k: float | None = None

    h298_uncertainty_kj_mol: float | None = Field(default=None, ge=0)
    s298_uncertainty_j_mol_k: float | None = Field(default=None, ge=0)

    tmin_k: float | None = Field(default=None, gt=0)
    tmax_k: float | None = Field(default=None, gt=0)

    note: str | None = None

    @model_validator(mode="after")
    def validate_temperature_range(self) -> Self:
        if (
            self.tmin_k is not None
            and self.tmax_k is not None
            and self.tmin_k > self.tmax_k
        ):
            raise ValueError("tmin_k must be less than or equal to tmax_k.")
        return self


class ThermoCreate(ThermoBase, SchemaBase):
    """Create schema for a thermo record.

    Nested creation is supported for tabulated points, NASA polynomials,
    and source-calculation links.
    """

    points: list[ThermoPointCreate] = Field(default_factory=list)
    nasa: ThermoNASACreate | None = None
    source_calculations: list[ThermoSourceCalculationCreate] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def validate_unique_points(self) -> Self:
        temps = [p.temperature_k for p in self.points]
        if len(set(temps)) != len(temps):
            raise ValueError(
                "Thermo points must be unique by temperature_k."
            )
        return self

    @model_validator(mode="after")
    def validate_unique_source_calculations(self) -> Self:
        keys = [
            (sc.calculation_id, sc.role) for sc in self.source_calculations
        ]
        if len(set(keys)) != len(keys):
            raise ValueError(
                "Source calculations must be unique by (calculation_id, role)."
            )
        return self


class ThermoUpdate(SchemaBase):
    """Patch schema for a thermo record."""

    scientific_origin: ScientificOriginKind | None = None

    literature_id: int | None = None
    workflow_tool_release_id: int | None = None
    software_release_id: int | None = None

    h298_kj_mol: float | None = None
    s298_j_mol_k: float | None = None

    h298_uncertainty_kj_mol: float | None = Field(default=None, ge=0)
    s298_uncertainty_j_mol_k: float | None = Field(default=None, ge=0)

    tmin_k: float | None = Field(default=None, gt=0)
    tmax_k: float | None = Field(default=None, gt=0)

    note: str | None = None

    @model_validator(mode="after")
    def validate_temperature_range(self) -> Self:
        if (
            self.tmin_k is not None
            and self.tmax_k is not None
            and self.tmin_k > self.tmax_k
        ):
            raise ValueError("tmin_k must be less than or equal to tmax_k.")
        return self


class ThermoRead(ThermoBase, TimestampedCreatedByReadSchema):
    """Read schema for a thermo record."""

    points: list[ThermoPointRead] = Field(default_factory=list)
    nasa: ThermoNASARead | None = None
    source_calculations: list[ThermoSourceCalculationRead] = Field(
        default_factory=list
    )
