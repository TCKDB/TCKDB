"""Worked payloads for user-built composite schemes (ADR 0021, P5).

Two complete ``computed-species`` bundles a producer can copy, rendered into the
producer contract by ``backend/scripts/generate_producer_contract.py`` and checked
by ``tests/test_composite_worked_examples.py``: each parses, and the total each
deposits is recomputed from the energies the same payload carries.

(b) **CCSD(T)/CBS from a TZ/QZ pair.** Two single points with their SCF and
    correlation parts, and an ``assembled`` composite whose scheme takes the
    reference energy at QZ and extrapolates the correlation energy over cardinal
    numbers 3 and 4 with ``inverse_power`` and exponent 3. The energies are the
    water SCF and CCSD(T) correlation energies printed in the ORCA 6.1 manual.

(c) **Focal-point additive.** A QZ base plus a core-valence difference (the same
    level, ``all_electron`` against ``frozen_core``), a higher-order difference,
    a relativistic difference and a DBOC value. The energies are round numbers.

What a producer sees in both: the scheme goes inline in the composite's
``level_of_theory.composite_scheme``; no ``software_release`` on the assembled
composite (no program ran it); ``composite_result.inputs`` name the calculations
by the bundle-local ``key`` of each; the deposited
``electronic_energy_hartree`` is the producer's number, and TCKDB recomputes it
only to check it.
"""

from __future__ import annotations

import copy
from typing import Any

__all__ = ["worked_payload_b", "worked_payload_c"]

_SOFTWARE = {"name": "orca", "version": "6.0.1"}
_WATER = {"smiles": "O", "charge": 0, "multiplicity": 1}
_WATER_XYZ = "3\nwater\nO 0.0 0.0 0.117\nH 0.0 0.757 -0.469\nH 0.0 -0.757 -0.469"
_OPT = {
    "key": "opt0",
    "type": "opt",
    "software_release": _SOFTWARE,
    "level_of_theory": {"method": "wB97X-D", "basis": "def2-TZVP"},
    "opt_result": {"converged": True},
}


def _sp(key: str, level: dict[str, Any], energy: float, components: list[tuple[str, float]] | None = None) -> dict:
    calc: dict[str, Any] = {
        "key": key,
        "type": "sp",
        "software_release": _SOFTWARE,
        "level_of_theory": level,
        "sp_result": {"electronic_energy_hartree": energy},
    }
    if components:
        calc["sp_energy_components"] = [{"component": c, "value_hartree": v} for c, v in components]
    return calc


def _bundle(additional: list[dict]) -> dict:
    return {
        "species_entry": dict(_WATER),
        "conformers": [
            {
                "key": "c0",
                "geometry": {"xyz_text": _WATER_XYZ},
                "primary_calculation": copy.deepcopy(_OPT),
                "additional_calculations": additional,
            }
        ],
    }


def worked_payload_b() -> dict:
    """CCSD(T)/CBS from TZ and QZ: reference at QZ, correlation by ``inverse_power``, exponent 3."""
    tz = {"method": "CCSD(T)", "basis": "cc-pVTZ"}
    qz = {"method": "CCSD(T)", "basis": "cc-pVQZ"}
    ref_t, corr_t = -76.056728252, -0.275383016
    ref_q, corr_q = -76.064381269, -0.295324345
    # (4**3 * corr_q - 3**3 * corr_t) / (4**3 - 3**3): the cardinal-3/4 limit of the correlation energy.
    corr_cbs = (64 * corr_q - 27 * corr_t) / 37
    return _bundle(
        [
            _sp("sp_tz", tz, round(ref_t + corr_t, 9), [("reference", ref_t), ("correlation", corr_t)]),
            _sp("sp_qz", qz, round(ref_q + corr_q, 9), [("reference", ref_q), ("correlation", corr_q)]),
            {
                "key": "ccsdt_cbs",
                "type": "composite",
                "level_of_theory": {
                    "composite_scheme": {
                        "kind": "extrapolation",
                        "terms": [
                            {
                                "key": "ref",
                                "operation": "value",
                                "energy_component": "reference",
                                "inputs": [{"slot": "value", "level_of_theory": qz}],
                            },
                            {
                                "key": "corr",
                                "operation": "extrapolation",
                                "energy_component": "correlation",
                                "formula": "inverse_power",
                                "exponent": 3,
                                "inputs": [
                                    {"slot": "cardinal", "cardinal_number": 3, "level_of_theory": tz},
                                    {"slot": "cardinal", "cardinal_number": 4, "level_of_theory": qz},
                                ],
                            },
                        ],
                    }
                },
                "composite_result": {
                    "assembly": "assembled",
                    "electronic_energy_hartree": round(ref_q + corr_cbs, 9),
                    "inputs": [
                        {"term_key": "ref", "slot": "value", "calculation_key": "sp_qz"},
                        {"term_key": "corr", "slot": "cardinal", "cardinal_number": 3, "calculation_key": "sp_tz"},
                        {"term_key": "corr", "slot": "cardinal", "cardinal_number": 4, "calculation_key": "sp_qz"},
                    ],
                },
            },
        ]
    )


def worked_payload_c() -> dict:
    """Focal point: a QZ base plus core-valence, higher-order, relativistic and DBOC terms."""
    base = {"method": "CCSD(T)", "basis": "cc-pVQZ"}
    cv_fc = {"method": "CCSD(T)", "basis": "cc-pCVTZ", "core_treatment": "frozen_core"}
    cv_ae = {"method": "CCSD(T)", "basis": "cc-pCVTZ", "core_treatment": "all_electron"}
    tq_high = {"method": "CCSDT(Q)", "basis": "cc-pVDZ"}
    tq_low = {"method": "CCSD(T)", "basis": "cc-pVDZ"}
    rel_high = {"method": "CCSD(T)", "basis": "cc-pVTZ-DK", "keywords": "DKH2"}
    rel_low = {"method": "CCSD(T)", "basis": "cc-pVTZ"}
    dboc_level = {"method": "HF", "basis": "cc-pVDZ"}
    e_base, e_ae, e_fc = -76.375, -76.40, -76.39
    e_tq_high, e_tq_low = -76.20, -76.19
    e_rel_high, e_rel_low = -76.43, -76.42
    e_hf, dboc = -76.02, 0.0027
    total = round(e_base + (e_ae - e_fc) + (e_tq_high - e_tq_low) + (e_rel_high - e_rel_low) + dboc, 9)

    def term(key: str, operation: str, component: str, inputs: list[dict]) -> dict:
        return {"key": key, "operation": operation, "energy_component": component, "inputs": inputs}

    return _bundle(
        [
            _sp("sp_base", base, e_base),
            _sp("sp_cv_ae", cv_ae, e_ae),
            _sp("sp_cv_fc", cv_fc, e_fc),
            _sp("sp_tq_high", tq_high, e_tq_high),
            _sp("sp_tq_low", tq_low, e_tq_low),
            _sp("sp_rel_high", rel_high, e_rel_high),
            _sp("sp_rel_low", rel_low, e_rel_low),
            _sp("sp_dboc", dboc_level, e_hf, [("dboc", dboc)]),
            {
                "key": "focal_point",
                "type": "composite",
                "level_of_theory": {
                    "composite_scheme": {
                        "kind": "additive",
                        "terms": [
                            term("base", "base", "total", [{"slot": "value", "level_of_theory": base}]),
                            term(
                                "dcv",
                                "difference",
                                "total",
                                [{"slot": "high", "level_of_theory": cv_ae}, {"slot": "low", "level_of_theory": cv_fc}],
                            ),
                            term(
                                "dtq",
                                "difference",
                                "total",
                                [{"slot": "high", "level_of_theory": tq_high}, {"slot": "low", "level_of_theory": tq_low}],
                            ),
                            term(
                                "drel",
                                "difference",
                                "total",
                                [
                                    {"slot": "high", "level_of_theory": rel_high},
                                    {"slot": "low", "level_of_theory": rel_low},
                                ],
                            ),
                            term("dboc", "value", "dboc", [{"slot": "value", "level_of_theory": dboc_level}]),
                        ],
                    }
                },
                "composite_result": {
                    "assembly": "assembled",
                    "electronic_energy_hartree": total,
                    "inputs": [
                        {"term_key": "base", "slot": "value", "calculation_key": "sp_base"},
                        {"term_key": "dcv", "slot": "high", "calculation_key": "sp_cv_ae"},
                        {"term_key": "dcv", "slot": "low", "calculation_key": "sp_cv_fc"},
                        {"term_key": "dtq", "slot": "high", "calculation_key": "sp_tq_high"},
                        {"term_key": "dtq", "slot": "low", "calculation_key": "sp_tq_low"},
                        {"term_key": "drel", "slot": "high", "calculation_key": "sp_rel_high"},
                        {"term_key": "drel", "slot": "low", "calculation_key": "sp_rel_low"},
                        {"term_key": "dboc", "slot": "value", "calculation_key": "sp_dboc"},
                    ],
                },
            },
        ]
    )
