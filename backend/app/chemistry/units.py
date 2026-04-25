"""Unit conversion utilities for scientific quantities."""

from app.db.models.common import ActivationEnergyUnits, ArrheniusAUnits

# 1 cal = 4.184 J (thermochemical calorie)
_CAL_TO_J = 4.184

_EA_TO_KJ_MOL: dict[ActivationEnergyUnits, float] = {
    ActivationEnergyUnits.kj_mol: 1.0,
    ActivationEnergyUnits.j_mol: 1e-3,
    ActivationEnergyUnits.cal_mol: _CAL_TO_J * 1e-3,
    ActivationEnergyUnits.kcal_mol: _CAL_TO_J,
}


def convert_ea_to_kj_mol(
    value: float,
    units: ActivationEnergyUnits,
) -> float:
    """Convert an activation energy value to kJ/mol.

    :param value: Activation energy in the reported units.
    :param units: The reported units.
    :returns: Activation energy in kJ/mol.
    """
    return value * _EA_TO_KJ_MOL[units]


# ---------------------------------------------------------------------------
# Arrhenius A-units ↔ reaction molecularity
# ---------------------------------------------------------------------------

_A_UNITS_BY_ORDER: dict[int, frozenset[ArrheniusAUnits]] = {
    1: frozenset({ArrheniusAUnits.per_s}),
    2: frozenset({
        ArrheniusAUnits.cm3_mol_s,
        ArrheniusAUnits.cm3_molecule_s,
        ArrheniusAUnits.m3_mol_s,
    }),
    3: frozenset({
        ArrheniusAUnits.cm6_mol2_s,
        ArrheniusAUnits.cm6_molecule2_s,
        ArrheniusAUnits.m6_mol2_s,
    }),
}


def validate_a_units_for_molecularity(
    a_units: ArrheniusAUnits,
    molecularity: int,
) -> None:
    """Raise ValueError if a_units is incompatible with the reaction molecularity.

    :param a_units: The reported Arrhenius A units.
    :param molecularity: Number of reactant molecules (1, 2, or 3).
    :raises ValueError: If the units do not match the expected order.
    """
    allowed = _A_UNITS_BY_ORDER.get(molecularity)
    if allowed is None:
        raise ValueError(
            f"Unsupported reaction molecularity: {molecularity}. "
            "Expected 1 (unimolecular), 2 (bimolecular), or 3 (termolecular)."
        )
    if a_units not in allowed:
        order_label = {1: "unimolecular", 2: "bimolecular", 3: "termolecular"}
        allowed_names = sorted(u.value for u in allowed)
        raise ValueError(
            f"a_units '{a_units.value}' is incompatible with "
            f"{order_label[molecularity]} reaction (molecularity={molecularity}). "
            f"Expected one of: {allowed_names}."
        )
