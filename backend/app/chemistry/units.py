"""Unit conversion utilities for scientific quantities."""

from app.api.error_contract import CodedValueError
from app.db.models.common import ActivationEnergyUnits, ArrheniusAUnits, EnergyUnit
from app.scientific_checks import (
    CheckTier,
    CodeChannel,
    PythonCheck,
    ScientificCheck,
)

#: Raised when an Arrhenius A carries units of the wrong dimensionality for
#: the reaction order it is declared at.
W_ARRHENIUS_A_UNITS_MISMATCH = "arrhenius_a_units_molecularity_mismatch"

#: Raised when a molecularity outside 1..3 is asked for.
W_UNSUPPORTED_MOLECULARITY = "unsupported_reaction_molecularity"

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
# Molar energy ↔ hartree
# ---------------------------------------------------------------------------

#: Hartree → kJ/mol. Same constant as
#: ``app/importers/cccbdb/normalizers/units.py`` and
#: ``app/services/scientific_read/ml_dataset.py``; kept numerically
#: identical to those on purpose, so a value converted on one surface and
#: read back on another does not disagree in the eleventh digit.
HARTREE_TO_KJ_MOL = 2625.4996394798254

#: kJ/mol per unit of each :class:`~app.db.models.common.EnergyUnit`.
#: Written out member by member rather than derived, so adding a member to
#: the enum without deciding its factor produces a ``None`` conversion (see
#: :func:`convert_energy_to_hartree`) instead of a wrong number.
_ENERGY_TO_KJ_MOL: dict[EnergyUnit, float] = {
    EnergyUnit.hartree: HARTREE_TO_KJ_MOL,
    EnergyUnit.kj_mol: 1.0,
    EnergyUnit.kcal_mol: _CAL_TO_J,
}


def convert_energy_to_hartree(
    value: float,
    units: EnergyUnit,
) -> float | None:
    """Convert a molar energy to hartree.

    Used by the read layer to put an applied energy correction into the
    same unit as the ``electronic_energy_hartree`` it is an addend to, so
    a consumer never has to carry a conversion factor of its own to add
    the two. The verbatim stored value and its stored unit travel beside
    the converted one on the wire; nothing is converted in place and
    nothing is converted silently.

    ``EnergyUnit.hartree`` round-trips through the kJ/mol pivot rather
    than short-circuiting, so every unit is converted by exactly one code
    path. The pivot is exact for hartree by construction (``x * k / k``),
    which the unit test pins.

    :param value: The energy in *units*.
    :param units: The unit *value* is expressed in.
    :returns: The energy in hartree, or ``None`` when *units* is a member
        this module has no factor for — an unconvertible unit is reported
        as "not converted", never as zero.
    """
    factor = _ENERGY_TO_KJ_MOL.get(units)
    if factor is None:
        return None
    return value * factor / HARTREE_TO_KJ_MOL


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
        raise CodedValueError(
            W_UNSUPPORTED_MOLECULARITY,
            f"Unsupported reaction molecularity: {molecularity}. "
            "Expected 1 (unimolecular), 2 (bimolecular), or 3 (termolecular).",
            context={"molecularity": molecularity},
            message_prefix=False,
        )
    if a_units not in allowed:
        order_label = {1: "unimolecular", 2: "bimolecular", 3: "termolecular"}
        allowed_names = sorted(u.value for u in allowed)
        raise CodedValueError(
            W_ARRHENIUS_A_UNITS_MISMATCH,
            f"a_units '{a_units.value}' is incompatible with "
            f"{order_label[molecularity]} reaction (molecularity={molecularity}). "
            f"Expected one of: {allowed_names}.",
            context={
                "a_units": a_units.value,
                "molecularity": molecularity,
                "expected": allowed_names,
            },
            message_prefix=False,
        )


CHECK_ARRHENIUS_A_UNITS_MATCH_MOLECULARITY = ScientificCheck(
    group="Rate coefficients",
    sort_key=1,
    code=W_ARRHENIUS_A_UNITS_MISMATCH,
    asserts=(
        "An Arrhenius pre-exponential factor carries units of the "
        "dimensionality its reaction order requires — per-second for "
        "unimolecular, concentration^-1 time^-1 for bimolecular, "
        "concentration^-2 time^-1 for termolecular."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    tier_rationale=(
        "Definitional. The dimensionality of A follows from the rate law, so "
        "an A in cm3/mol/s on a unimolecular reaction is not an unusual result "
        "but a number that cannot mean what it says. A mis-declared unit is "
        "also silently catastrophic downstream, since nothing later in the "
        "stack can recover the intended order from the value alone."
    ),
    adr="0008",
    enforced_by=(
        PythonCheck(
            validate_a_units_for_molecularity,
            note=(
                "Called from the kinetics upload schema, so it refuses at the "
                "wire boundary. The order is not simply ``len(reactants)``: a "
                "simple ``+M`` third-body reaction carries a ``[M]`` term on "
                "the main line and validates one order higher, while a falloff "
                "reaction's main line is the high-pressure limit k-infinity "
                "and keeps ``len(reactants)``, its low-pressure limit k0 being "
                "validated separately one order up."
            ),
        ),
    ),
    escape_hatch=(
        "None, and the refinements are the reason it can block without firing "
        "on correct science: PLOG and Chebyshev are refused the "
        "``is_third_body`` flag outright, because both already encode the full "
        "pressure dependence and the flag would otherwise inflate the expected "
        "order by one — rejecting a PLOG entry carrying the *correct* units "
        "and accepting one carrying the units of the next order up."
    ),
)
