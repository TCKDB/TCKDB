"""Backend hybrid module — upload-facing thermo point + NASA pieces
live in ``tckdb_schemas.thermo``; the parent ``Thermo*`` ORM-read /
CRUD shapes and the source-calc link schemas remain backend-side
because they carry FK ids.
"""

from typing import Self

from pydantic import BaseModel, Field, model_validator
from tckdb_schemas.thermo import (
    ThermoNASABase,
    ThermoNASACreate,
    ThermoPointBase,
    ThermoPointCreate,
)

from app.db.models.common import (
    PhaseKind,
    ScientificOriginKind,
    ThermoCalculationRole,
)
from app.schemas.common import (
    ORMBaseSchema,
    SchemaBase,
    TimestampedCreatedByReadSchema,
)


class ThermoPointUpdate(SchemaBase):
    """Patch schema for a thermo data point."""

    cp_j_mol_k: float | None = None
    h_kj_mol: float | None = None
    s_j_mol_k: float | None = None
    g_kj_mol: float | None = None


class ThermoPointRead(ThermoPointBase, ORMBaseSchema):
    """Read schema for a thermo data point."""

    thermo_id: int


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
    :param enthalpy_formation_0k_kj_mol: 0 K standard formation enthalpy
        ΔfH°(0 K) (kJ/mol).
    :param enthalpy_formation_0k_uncertainty_kj_mol: Uncertainty in
        ΔfH°(0 K) (kJ/mol).
    :param reference_pressure_bar: Standard-state reference pressure (bar);
        None if unspecified. IUPAC standard is 1 bar (1 atm = 1.01325 bar).
    :param phase: Physical phase the record is referenced to; None if
        unspecified.
    :param statmech_id: Statmech record this computed thermo was derived
        from; None for experimental/literature/group-additivity thermo.
    :param tmin_k: Optional minimum valid temperature in K.
    :param tmax_k: Optional maximum valid temperature in K.
    :param note: Optional free-text note.
    """

    species_entry_id: int
    scientific_origin: ScientificOriginKind

    literature_id: int | None = None
    workflow_tool_release_id: int | None = None
    software_release_id: int | None = None
    statmech_id: int | None = None

    h298_kj_mol: float | None = None
    s298_j_mol_k: float | None = None

    h298_uncertainty_kj_mol: float | None = Field(default=None, ge=0)
    s298_uncertainty_j_mol_k: float | None = Field(default=None, ge=0)

    enthalpy_formation_0k_kj_mol: float | None = None
    enthalpy_formation_0k_uncertainty_kj_mol: float | None = Field(
        default=None, ge=0
    )

    reference_pressure_bar: float | None = None
    phase: PhaseKind | None = None

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
    statmech_id: int | None = None

    h298_kj_mol: float | None = None
    s298_j_mol_k: float | None = None

    h298_uncertainty_kj_mol: float | None = Field(default=None, ge=0)
    s298_uncertainty_j_mol_k: float | None = Field(default=None, ge=0)

    enthalpy_formation_0k_kj_mol: float | None = None
    enthalpy_formation_0k_uncertainty_kj_mol: float | None = Field(
        default=None, ge=0
    )

    reference_pressure_bar: float | None = None
    phase: PhaseKind | None = None

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
