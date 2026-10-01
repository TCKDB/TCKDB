"""Worked payloads (b) and (c) of the composite plan, as computed-species bundles (ADR 0021, P5).

Shared by the API, service and migration tests so each exercises the same
recipes. Numbers are the ORCA 6.1 manual's water energies for (b) (so the
extrapolated correlation energy has an independent hand calculation) and round
decimals for (c) (so the expected total is plain arithmetic).
"""

from __future__ import annotations

import copy

SOFTWARE = {"name": "orca", "version": "6.0.1"}
WATER = {"smiles": "O", "charge": 0, "multiplicity": 1}
WATER_XYZ = "3\nwater\nO 0.0 0.0 0.117\nH 0.0 0.757 -0.469\nH 0.0 -0.757 -0.469"
OTHER_WATER_XYZ = "3\nwater\nO 0.0 0.0 0.117\nH 0.0 0.80 -0.469\nH 0.0 -0.80 -0.469"
OPT_LOT = {"method": "wb97xd", "basis": "def2tzvp"}

# --- (b) CCSD(T)/CBS from TZ and QZ ------------------------------------------------------
TZ = {"method": "CCSD(T)", "basis": "cc-pVTZ"}
QZ = {"method": "CCSD(T)", "basis": "cc-pVQZ"}
R_T, C_T = -76.056728252, -0.275383016
R_Q, C_Q = -76.064381269, -0.295324345
#: (4^3 C_Q - 3^3 C_T) / (4^3 - 3^3) = -11.465416648 / 37, by hand.
CORR_CBS_X3 = -0.3098761256216216
TOTAL_B = R_Q + CORR_CBS_X3  # -76.37425739462162
#: Quantities in the recomputation: the QZ reference and two correlation energies, plus the total.
TOL_B = 2e-6

LABEL_B = "CBS[ref:CCSD(T)/cc-pVQZ + corr:CCSD(T)/cc-pV{T,Q}Z; inverse_power x=3; n=3,4]"

SCHEME_B = {
    "kind": "extrapolation",
    "terms": [
        {
            "key": "ref",
            "operation": "value",
            "energy_component": "reference",
            "inputs": [{"slot": "value", "level_of_theory": QZ}],
        },
        {
            "key": "corr",
            "operation": "extrapolation",
            "energy_component": "correlation",
            "formula": "inverse_power",
            "exponent": 3,
            "inputs": [
                {"slot": "cardinal", "cardinal_number": 3, "level_of_theory": TZ},
                {"slot": "cardinal", "cardinal_number": 4, "level_of_theory": QZ},
            ],
        },
    ],
}

INPUTS_B = [
    {"term_key": "ref", "slot": "value", "calculation_key": "spq"},
    {"term_key": "corr", "slot": "cardinal", "cardinal_number": 3, "calculation_key": "spt"},
    {"term_key": "corr", "slot": "cardinal", "cardinal_number": 4, "calculation_key": "spq"},
]


def sp(key: str, lot: dict, energy: float | None, components: list[tuple[str, float]] | None = None, **extra) -> dict:
    calc: dict = {
        "key": key,
        "type": "sp",
        "software_release": SOFTWARE,
        "level_of_theory": lot,
    }
    if energy is not None:
        calc["sp_result"] = {"electronic_energy_hartree": energy}
    if components:
        calc["sp_energy_components"] = [{"component": c, "value_hartree": v} for c, v in components]
    calc.update(extra)
    return calc


def assembled(scheme: dict, inputs: list[dict], total: float | None, key: str = "cbs", **extra) -> dict:
    block: dict = {"assembly": "assembled", "inputs": copy.deepcopy(inputs)}
    if total is not None:
        block["electronic_energy_hartree"] = total
    calc = {
        "key": key,
        "type": "composite",
        "level_of_theory": {"composite_scheme": copy.deepcopy(scheme)},
        "composite_result": block,
    }
    calc.update(extra)
    return calc


def opt() -> dict:
    return {
        "key": "opt0",
        "type": "opt",
        "software_release": SOFTWARE,
        "level_of_theory": OPT_LOT,
        "opt_result": {"converged": True},
    }


def bundle(additional: list[dict], *, xyz: str = WATER_XYZ) -> dict:
    return {
        "species_entry": dict(WATER),
        "conformers": [
            {
                "key": "c0",
                "geometry": {"xyz_text": xyz},
                "primary_calculation": opt(),
                "additional_calculations": additional,
            }
        ],
    }


def sps_b() -> list[dict]:
    return [
        sp("spt", TZ, R_T + C_T, [("reference", R_T), ("correlation", C_T)]),
        sp("spq", QZ, R_Q + C_Q, [("reference", R_Q), ("correlation", C_Q)]),
    ]


def bundle_b(total: float | None = TOTAL_B, *, scheme: dict | None = None, inputs: list[dict] | None = None) -> dict:
    return bundle([*sps_b(), assembled(scheme or SCHEME_B, inputs or INPUTS_B, total)])


# --- (c) focal point -----------------------------------------------------------------------
FC = {"method": "CCSD(T)", "basis": "cc-pCVTZ", "core_treatment": "frozen_core"}
AE = {"method": "CCSD(T)", "basis": "cc-pCVTZ", "core_treatment": "all_electron"}
TQ_HIGH = {"method": "CCSDT(Q)", "basis": "cc-pVDZ"}
TQ_LOW = {"method": "CCSD(T)", "basis": "cc-pVDZ"}
REL_HIGH = {"method": "CCSD(T)", "basis": "cc-pVTZ-DK", "keywords": "DKH2"}
REL_LOW = {"method": "CCSD(T)", "basis": "cc-pVTZ"}
DBOC_LOT = {"method": "HF", "basis": "cc-pVDZ"}

E_BASE, E_AE, E_FC = -76.375, -76.40, -76.39
E_TQ_HIGH, E_TQ_LOW = -76.20, -76.19
E_REL_HIGH, E_REL_LOW = -76.43, -76.42
E_HF, DBOC = -76.02, 0.0027
#: base + (ae - fc) + (tq_high - tq_low) + (rel_high - rel_low) + dboc.
TOTAL_C = E_BASE + (E_AE - E_FC) + (E_TQ_HIGH - E_TQ_LOW) + (E_REL_HIGH - E_REL_LOW) + DBOC
#: Eight consumed numbers plus the total: max(1e-6, 5e-7 * 9).
TOL_C = 4.5e-6

SCHEME_C = {
    "kind": "additive",
    "terms": [
        {
            "key": "base",
            "operation": "base",
            "energy_component": "total",
            "inputs": [{"slot": "value", "level_of_theory": QZ}],
        },
        {
            "key": "dcv",
            "operation": "difference",
            "energy_component": "total",
            "inputs": [{"slot": "high", "level_of_theory": AE}, {"slot": "low", "level_of_theory": FC}],
        },
        {
            "key": "dtq",
            "operation": "difference",
            "energy_component": "total",
            "inputs": [{"slot": "high", "level_of_theory": TQ_HIGH}, {"slot": "low", "level_of_theory": TQ_LOW}],
        },
        {
            "key": "drel",
            "operation": "difference",
            "energy_component": "total",
            "inputs": [{"slot": "high", "level_of_theory": REL_HIGH}, {"slot": "low", "level_of_theory": REL_LOW}],
        },
        {
            "key": "dboc",
            "operation": "value",
            "energy_component": "dboc",
            "inputs": [{"slot": "value", "level_of_theory": DBOC_LOT}],
        },
    ],
}

INPUTS_C = [
    {"term_key": "base", "slot": "value", "calculation_key": "sp_base"},
    {"term_key": "dcv", "slot": "high", "calculation_key": "sp_ae"},
    {"term_key": "dcv", "slot": "low", "calculation_key": "sp_fc"},
    {"term_key": "dtq", "slot": "high", "calculation_key": "sp_tq_high"},
    {"term_key": "dtq", "slot": "low", "calculation_key": "sp_tq_low"},
    {"term_key": "drel", "slot": "high", "calculation_key": "sp_rel_high"},
    {"term_key": "drel", "slot": "low", "calculation_key": "sp_rel_low"},
    {"term_key": "dboc", "slot": "value", "calculation_key": "sp_hf"},
]


def sps_c() -> list[dict]:
    return [
        sp("sp_base", QZ, E_BASE),
        sp("sp_ae", AE, E_AE),
        sp("sp_fc", FC, E_FC),
        sp("sp_tq_high", TQ_HIGH, E_TQ_HIGH),
        sp("sp_tq_low", TQ_LOW, E_TQ_LOW),
        sp("sp_rel_high", REL_HIGH, E_REL_HIGH),
        sp("sp_rel_low", REL_LOW, E_REL_LOW),
        sp("sp_hf", DBOC_LOT, E_HF, [("dboc", DBOC)]),
    ]


def bundle_c(total: float | None = TOTAL_C) -> dict:
    return bundle([*sps_c(), assembled(SCHEME_C, INPUTS_C, total, key="fpa")])
