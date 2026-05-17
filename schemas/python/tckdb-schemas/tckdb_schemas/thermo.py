"""Thermo upload pieces (tabulated point + NASA polynomial fragments).

Only the upload-facing point and NASA pieces ship in the standalone
package: ``ThermoPointBase``, ``ThermoPointCreate``, ``ThermoNASABase``,
``ThermoNASACreate``. The full backend parent ``Thermo*`` schemas
(``ThermoBase``, ``ThermoCreate``, ``ThermoRead``, ``ThermoUpdate``,
``ThermoSourceCalculation*``) stay backend-side because they carry FK
ids and ORM-read shapes that have no place in the wire contract.
"""

from typing import Self

from pydantic import BaseModel, Field, model_validator

from tckdb_schemas.common import SchemaBase


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
