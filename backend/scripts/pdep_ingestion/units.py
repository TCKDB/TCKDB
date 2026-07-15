"""Unit conversion helpers for Arkane -> TCKDB PDep ingestion.

Three conversions are load-bearing for this ingestion (see the module
docstring in ``builder.py`` for the full gotcha discussion):

1. Grain size: Arkane's ``maximumGrainSize`` is given in kcal/mol, but
   ``NetworkSolveIn.grain_size_cm_inv`` wants cm^-1.
2. Energy: the ``supporting_information.csv`` electronic energies are in
   J/mol, but ``CalculationIn.sp_electronic_energy_hartree`` wants hartree.
3. Pressure: Chebyshev fits are printed in *bar* in ``output.py`` (matching
   ``input.py``) but in *atm* in the Chemkin ``chem.inp``. We read
   ``output.py`` and take bar directly, so no atm->bar conversion is applied.
   The helper is provided for completeness / cross-checking ``chem.inp``.
"""

from __future__ import annotations

# 1 hartree = 2625499.639 J/mol (CODATA-derived, matches Arkane's constant).
HARTREE_TO_J_MOL: float = 2625499.639

# 1 kcal/mol = 349.7551 cm^-1 (thermochemical calorie).
KCAL_MOL_TO_CM_INV: float = 349.7551

# 1 atm = 1.01325 bar (Chemkin uses atm; Arkane output.py uses bar).
BAR_PER_ATM: float = 1.01325


def kcal_mol_to_cm_inv(value_kcal_mol: float) -> float:
    """Convert an energy grain size from kcal/mol to cm^-1."""
    return value_kcal_mol * KCAL_MOL_TO_CM_INV


def j_mol_to_hartree(value_j_mol: float) -> float:
    """Convert an energy from J/mol to hartree."""
    return value_j_mol / HARTREE_TO_J_MOL


def atm_to_bar(value_atm: float) -> float:
    """Convert a pressure from atm to bar."""
    return value_atm * BAR_PER_ATM


# 1 kcal = 4.184 kJ (thermochemical calorie).
KJ_PER_KCAL: float = 4.184


def ea_to_kj_mol(value: float, units: str) -> float:
    """Convert an activation energy to kJ/mol.

    Arkane PLOG (``PDepArrhenius``) ``Ea`` is emitted in kJ/mol in this data,
    so the common path is a pass-through; the other Arkane energy labels are
    converted for completeness. Unknown units fail loud rather than silently
    mis-scaling a barrier.
    """
    if units == "kJ/mol":
        return value
    if units == "J/mol":
        return value / 1000.0
    if units == "kcal/mol":
        return value * KJ_PER_KCAL
    if units == "cal/mol":
        return value * KJ_PER_KCAL / 1000.0
    raise ValueError(f"Unexpected Ea units {units!r} (expected kJ/mol, J/mol, kcal/mol, cal/mol).")
