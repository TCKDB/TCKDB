"""Thermo upload pieces (tabulated point + NASA polynomial fragments).

Only the upload-facing point and NASA pieces ship in the standalone
package: ``ThermoPointBase``, ``ThermoPointCreate``, ``ThermoNASABase``,
``ThermoNASACreate``. The full backend parent ``Thermo*`` schemas
(``ThermoBase``, ``ThermoCreate``, ``ThermoRead``, ``ThermoUpdate``,
``ThermoSourceCalculation*``) stay backend-side because they carry FK
ids and ORM-read shapes that have no place in the wire contract.
"""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tckdb_schemas.common import SchemaBase
from tckdb_schemas.enums import EnthalpyReferenceKind, PhaseKind, ScientificOriginKind


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

    model_config = ConfigDict(allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_property_content(self) -> Self:
        if all(getattr(self, name) is None for name in ("cp_j_mol_k", "h_kj_mol", "s_j_mol_k", "g_kj_mol")):
            raise ValueError("Thermo point must contain at least one property value.")
        return self


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
        # Bound to locals and compared through explicit `is not None`
        # guards rather than a `nones == 0` count. The count is the same
        # fact, but it is a fact about a list rather than about the three
        # attributes, so no reader -- human or type checker -- can tell
        # from the comparison below that all three are floats there. The
        # ordering rules are unchanged.
        t_low, t_mid, t_high = self.t_low, self.t_mid, self.t_high
        supplied = [t for t in (t_low, t_mid, t_high) if t is not None]
        if len(supplied) not in (0, 3):
            raise ValueError(
                "Temperature bounds must be all provided or all omitted."
            )
        if t_low is not None and t_mid is not None and t_high is not None:
            if t_mid <= t_low:
                raise ValueError("t_mid must be greater than t_low.")
            if t_high <= t_mid:
                raise ValueError("t_high must be greater than t_mid.")
        return self


class ThermoNASACreate(ThermoNASABase, SchemaBase):
    """Complete, finite NASA7 fit; historical reads use ThermoNASABase."""

    model_config = ConfigDict(allow_inf_nan=False)

    t_low: float = Field(gt=0)
    t_mid: float = Field(gt=0)
    t_high: float = Field(gt=0)
    a1: float
    a2: float
    a3: float
    a4: float
    a5: float
    a6: float
    a7: float
    b1: float
    b2: float
    b3: float
    b4: float
    b5: float
    b6: float
    b7: float



# ---------------------------------------------------------------------------
# Thermo NASA-9 (Glenn) polynomial interval (arbitrary interval count)
# ---------------------------------------------------------------------------


class ThermoNASA9IntervalBase(BaseModel):
    """Shared fields for one NASA-9 polynomial temperature interval.

    A NASA-9 fit has an arbitrary number of intervals, each with its own
    nine coefficients. ``a1..a7`` are the Cp°/R polynomial coefficients
    (``a1·T⁻² + a2·T⁻¹ + a3 + a4·T + a5·T² + a6·T³ + a7·T⁴``), ``a8`` is the
    enthalpy integration constant and ``a9`` the entropy integration
    constant.

    :param interval_index: 1-based ordering index of this interval.
    :param t_min_k: Interval lower temperature bound (K).
    :param t_max_k: Interval upper temperature bound (K); must exceed t_min_k.
    :param a1..a9: The nine NASA-9 coefficients.
    """

    interval_index: int = Field(ge=1)
    t_min_k: float = Field(gt=0)
    t_max_k: float = Field(gt=0)

    a1: float
    a2: float
    a3: float
    a4: float
    a5: float
    a6: float
    a7: float
    a8: float
    a9: float

    @model_validator(mode="after")
    def validate_interval_bounds(self) -> Self:
        if self.t_max_k <= self.t_min_k:
            raise ValueError("t_max_k must be greater than t_min_k.")
        return self


class ThermoNASA9IntervalCreate(ThermoNASA9IntervalBase, SchemaBase):
    """Nested create payload for one NASA-9 polynomial interval."""

    model_config = ConfigDict(allow_inf_nan=False)


# ---------------------------------------------------------------------------
# Thermo Wilhoit heat-capacity form
# ---------------------------------------------------------------------------


class ThermoWilhoitBase(BaseModel):
    """Shared fields for the Wilhoit continuous heat-capacity form.

    ``Cp = Cp0 + (CpInf − Cp0)·y²·[1 + (y − 1)(a0 + a1·y + a2·y² + a3·y³)]``
    with ``y = T / (T + B)``.

    :param cp0_j_mol_k: Low-temperature heat-capacity limit (J/(mol*K)).
    :param cp_inf_j_mol_k: High-temperature heat-capacity limit (J/(mol*K)).
    :param b_k: The B scaling parameter (K).
    :param a0..a3: Dimensionless Wilhoit shape parameters.
    :param h0_kj_mol: Optional enthalpy integration constant (kJ/mol).
    :param s0_j_mol_k: Optional entropy integration constant (J/(mol*K)).
    """

    cp0_j_mol_k: float = Field(ge=0)
    cp_inf_j_mol_k: float = Field(ge=0)
    b_k: float = Field(gt=0)

    a0: float
    a1: float
    a2: float
    a3: float

    h0_kj_mol: float | None = None
    s0_j_mol_k: float | None = None


class ThermoWilhoitCreate(ThermoWilhoitBase, SchemaBase):
    """Nested create payload for the Wilhoit heat-capacity form."""

    model_config = ConfigDict(allow_inf_nan=False)


class ThermoStateFields(SchemaBase):
    """State and 0 K content shared by the computed bundle contracts.

    The ``phase`` default applies only to omitted computed state. Explicit
    null is evidence of unknown state and survives conversion to standalone
    uploads. ``reference_pressure_bar`` is NEVER defaulted, for any origin
    (decided 2026-09-24, issue #529): the dominant computed producer (ARC)
    computes entropy at 1 atm via a hardcoded translational partition
    function and records no pressure anywhere in its output, so the former
    ``1.0`` (bar) default stamped every computed deposit with a standard
    state its own numbers were not computed at -- see
    ``app.schemas.workflows.thermo_upload.ThermoUploadRequest`` (backend)
    for the full rationale and why ``phase`` keeps its default. Both
    schemas must stay symmetric; an asymmetry here is a bug.
    """

    model_config = ConfigDict(allow_inf_nan=False)

    scientific_origin: ScientificOriginKind = ScientificOriginKind.computed
    phase: PhaseKind | None = None
    # Other declared quantities are routed by the backend, never coerced.
    enthalpy_reference_kind: EnthalpyReferenceKind | str | None = None
    reference_pressure_bar: float | None = Field(default=None, gt=0)
    enthalpy_formation_0k_kj_mol: float | None = None
    enthalpy_formation_0k_uncertainty_kj_mol: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def apply_computed_state_defaults(self) -> Self:
        if self.scientific_origin == ScientificOriginKind.computed:
            if "phase" not in self.model_fields_set:
                self.phase = PhaseKind.gas
        return self
