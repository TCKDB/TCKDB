"""CHEMKIN <-> TCKDB form and unit mapping constants (single source of truth).

Everything an *exporter* would also need to reason about the CHEMKIN<->TCKDB
correspondence lives here so both directions share one table. Keep this module
free of RDKit and of TCKDB imports — it is pure data + tiny helpers.

TCKDB target enum *values* (mirrored as plain strings so this stays
dependency-light):

* ``KineticsModelKind``:      arrhenius, modified_arrhenius, lindemann, troe,
                              sri, plog, chebyshev
* ``ArrheniusAUnits``:        per_s, cm3_mol_s, cm3_molecule_s, cm6_mol2_s,
                              cm6_molecule2_s, m3_mol_s, m6_mol2_s
* ``ActivationEnergyUnits``:  j_mol, kj_mol, cal_mol, kcal_mol
* ``ScientificOriginKind``:   computed, experimental, estimated
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Kinetics model-kind tokens (TCKDB KineticsModelKind values)
# ---------------------------------------------------------------------------

MODEL_ARRHENIUS = "arrhenius"
MODEL_MODIFIED_ARRHENIUS = "modified_arrhenius"
MODEL_LINDEMANN = "lindemann"
MODEL_TROE = "troe"
MODEL_SRI = "sri"
MODEL_PLOG = "plog"
MODEL_CHEBYSHEV = "chebyshev"


# ---------------------------------------------------------------------------
# Activation-energy units
# ---------------------------------------------------------------------------

# CHEMKIN REACTIONS-header Ea token -> conversion factor to kJ/mol (§7).
# R used for KELVIN is 8.31446e-3 kJ/mol/K; EVOLTS uses 96.485 kJ/mol per eV.
EA_TOKEN_TO_KJ_MOL: dict[str, float] = {
    "CAL/MOLE": 4.184e-3,
    "CAL/MOL": 4.184e-3,
    "KCAL/MOLE": 4.184,
    "KCAL/MOL": 4.184,
    "JOULES/MOLE": 1e-3,
    "JOULES/MOL": 1e-3,
    "JOUL/MOLE": 1e-3,
    "KJOULES/MOLE": 1.0,
    "KJOULES/MOL": 1.0,
    "KJOU/MOLE": 1.0,
    "KELVIN": 8.31446e-3,
    "KELVINS": 8.31446e-3,
    "EVOLTS": 96.485,
    "EVOLT": 96.485,
}

# CHEMKIN Ea token -> TCKDB ActivationEnergyUnits value, when a lossless
# pass-through exists (so ``reported_ea`` keeps its native magnitude/units).
# KELVIN / EVOLTS have no enum home -> caller converts to kj_mol instead.
EA_TOKEN_TO_TCKDB_UNIT: dict[str, str] = {
    "CAL/MOLE": "cal_mol",
    "CAL/MOL": "cal_mol",
    "KCAL/MOLE": "kcal_mol",
    "KCAL/MOL": "kcal_mol",
    "JOULES/MOLE": "j_mol",
    "JOULES/MOL": "j_mol",
    "JOUL/MOLE": "j_mol",
    "KJOULES/MOLE": "kj_mol",
    "KJOULES/MOL": "kj_mol",
    "KJOU/MOLE": "kj_mol",
}

DEFAULT_EA_TOKEN = "CAL/MOLE"


# ---------------------------------------------------------------------------
# Pre-exponential (A-factor) units -- molecularity aware (§7)
# ---------------------------------------------------------------------------

DEFAULT_A_CONC_BASIS = "MOLES"

# (concentration basis, reaction order) -> ArrheniusAUnits value.
# Order 1 => per_s (no concentration dependence, basis irrelevant).
# Order 2 => cm3 per (mol|molecule) per s.
# Order 3 => cm6 per (mol|molecule)^2 per s  (also the falloff-k0 basis).
_A_UNITS: dict[tuple[str, int], str] = {
    ("MOLES", 1): "per_s",
    ("MOLECULES", 1): "per_s",
    ("MOLES", 2): "cm3_mol_s",
    ("MOLECULES", 2): "cm3_molecule_s",
    ("MOLES", 3): "cm6_mol2_s",
    ("MOLECULES", 3): "cm6_molecule2_s",
}


def a_units_for(conc_basis: str, order: int) -> str:
    """Return the TCKDB ArrheniusAUnits value for a rate of the given order.

    :param conc_basis: ``MOLES`` or ``MOLECULES`` (from the REACTIONS header).
    :param order: Concentration order of the rate constant (1, 2, or 3).
        For a falloff k0 this is ``molecularity + 1``.
    :raises ValueError: if the order is outside 1..3 (v1 scope).
    """
    basis = conc_basis.upper()
    key = (basis, order)
    if key not in _A_UNITS:
        raise ValueError(
            f"Unsupported rate order {order} for A-units (basis={basis}); "
            "CHEMKIN v1 import supports uni/bi/termolecular (order 1-3)."
        )
    return _A_UNITS[key]


# ---------------------------------------------------------------------------
# Pressure conversion
# ---------------------------------------------------------------------------

ATM_TO_BAR = 1.01325


def atm_to_bar(pressure_atm: float) -> float:
    """Convert a pressure in atm to bar (PLOG / Chebyshev domains, §7)."""
    return pressure_atm * ATM_TO_BAR


# ---------------------------------------------------------------------------
# Aux-keyword classification (reaction block)
# ---------------------------------------------------------------------------

# Auxiliary keywords the parser recognises. Anything else that looks like an
# aux line (contains ``/``) but is not a keyword is treated as a collider
# efficiency list.
KNOWN_AUX_KEYWORDS = frozenset(
    {
        "LOW",
        "HIGH",
        "TROE",
        "SRI",
        "PLOG",
        "CHEB",
        "TCHEB",
        "PCHEB",
        "REV",
        "DUP",
        "DUPLICATE",
    }
)

# Recognised-but-unsupported (v1) aux keywords: parse + report, do not fail.
UNSUPPORTED_AUX_KEYWORDS = frozenset(
    {"LT", "RLT", "FORD", "RORD", "JAN", "FIT1", "XSMI", "MOME", "TDEP", "EXCI"}
)
