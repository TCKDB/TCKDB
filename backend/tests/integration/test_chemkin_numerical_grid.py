"""Numerical grid on the real RMG mechanism: import -> persist -> export, then
both files evaluated by pinned Cantera and compared under predeclared bounds.

The structural round trip lives in ``test_chemkin_round_trip_real.py``; this
file asks the numerical question it does not: does the exported mechanism
give the same thermochemistry and the same rate constants as the file it was
made from, to within what the export's printed digits allow?

The grid, the composition and every bound are stated in
``tests/integration/chemkin_grid.py`` (module docstring: the derivation). The
comparison emits a machine-readable summary (every reaction compared, every
unsupported form or export gap, the maximum deviation per species and per
reaction) to the path in ``TCKDB_CHEMKIN_GRID_SUMMARY_PATH`` when that is set,
and ``test_summary_shape`` pins its shape for a later paper generator.

Mutation checks rewrite the *exported* text and re-run the comparison against
the unmutated original, so each row of the table below is a landed mutation:

==================================================  =======
swap one species' NASA high/low blocks              red
NASA coefficient + 1 unit in the 9th printed digit  red   (the bound is half a unit)
NASA coefficient + 1 unit in a 10th digit           green (below print precision)
Arrhenius A + 1 unit in the original's 7th digit    green (0.01 export units)
Arrhenius A + 1 unit in the export's 5th digit      red   (the bound is half a unit)
Arrhenius A + 10 units in the export's 5th digit    red
drop one Troe collider efficiency                   red
==================================================  =======
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import cantera as ct
import pytest

# Same adapter wiring as test_chemkin_round_trip_real.py (see the note there).
_CHEMKIN_ADAPTER = Path(__file__).resolve().parents[3] / "clients" / "python" / "adapters" / "chemkin"
if _CHEMKIN_ADAPTER.is_dir() and str(_CHEMKIN_ADAPTER) not in sys.path:
    sys.path.append(str(_CHEMKIN_ADAPTER))

from tckdb_chemkin.identity import IdentityResolver
from tckdb_chemkin.parser import parse_mechanism

from tests.integration.chemkin_grid import (
    GRID_PRESSURES_ATM,
    GRID_TEMPERATURES_K,
    PINNED_CANTERA,
    SUMMARY_PATH_ENV,
    SUMMARY_SCHEMA,
    SUPPORTED_FORMS,
    GridComparison,
    canonical,
    compare_mechanisms,
    drop_efficiency,
    group_reactions,
    load_solution,
    perturb_nasa_coefficient,
    replace_unique,
    supported_keys,
    swap_nasa_blocks,
)
from tests.integration.test_chemkin_round_trip_real import (
    _export_chemkin,
    _import_payloads,
    _persist_and_approve,
    _read,
)

FIXTURE_NAME = "rmg_ammonia_methane"


@pytest.fixture
def pipeline(db_session):
    assert ct.__version__ == PINNED_CANTERA, "revalidate the grid before changing the engine pin"
    _mech_in, resolved_in, _normalized_in, built = _import_payloads()
    species_refs, reaction_refs = _persist_and_approve(db_session, built)
    _record_set, export = _export_chemkin(db_session, species_refs, reaction_refs)

    files_in = {"chem.inp": _read("chem.inp")}  # thermo is embedded in the fixture file
    files_out = dict(export.files)

    mech_out = parse_mechanism(files_out["chem.inp"], thermo_text=files_out["therm.dat"])
    resolved_out = IdentityResolver().resolve_mechanism(mech_out)
    smiles_in = {name: canonical(r.smiles) for name, r in resolved_in.items()}
    smiles_out = {name: canonical(r.smiles) for name, r in resolved_out.items()}

    gas_in = load_solution(files_in)
    return {
        "files_in": files_in,
        "files_out": files_out,
        "gas_in": gas_in,
        "smiles_in": smiles_in,
        "smiles_out": smiles_out,
        "export_gaps": list(export.gaps),
        "name_out": {smiles: name for name, smiles in smiles_out.items()},
    }


def _compare(p, files_out: dict[str, str]) -> GridComparison:
    return compare_mechanisms(
        p["gas_in"],
        load_solution(files_out),
        p["smiles_in"],
        p["smiles_out"],
        fixture=FIXTURE_NAME,
        export_gaps=p["export_gaps"],
    )


# ---------------------------------------------------------------------------
# The grid
# ---------------------------------------------------------------------------


def test_exported_mechanism_matches_original_on_the_grid(pipeline):
    result = _compare(pipeline, pipeline["files_out"])
    summary = result.summary()
    if os.environ.get(SUMMARY_PATH_ENV):
        result.write_summary(os.environ[SUMMARY_PATH_ENV])

    assert result.violations == [], "\n".join(result.violations)

    # The compared set is exactly the fixture's supported set, and not empty.
    expected = supported_keys(pipeline["gas_in"], pipeline["smiles_in"])
    assert result.compared_keys == expected
    assert len(expected) == 61
    assert summary["counts"]["rate_terms_compared"] == 64
    assert summary["counts"]["species_compared"] == 21
    assert summary["forms"] == {
        "Arrhenius": 38,  # 41 rate rows: three DUPLICATE pairs collapse to one group each
        "Chebyshev": 9,
        "falloff-Troe": 5,
        "three-body-Arrhenius": 9,
    }
    assert summary["counts"]["unsupported"] == 0
    assert summary["unsupported"] == []
    # Gaps are listed, never dropped; this fixture has none at all, so a future
    # transport or thermo gap fails here rather than passing through an empty list.
    assert summary["export_gaps"] == []
    assert summary["counts"]["export_gaps"] == 0
    assert all(entry["within_bound"] for entry in summary["species"])
    assert all(entry["within_bound"] for entry in summary["reactions"])
    assert sum(1 for entry in summary["reactions"] if entry["duplicate"]) == 3


def test_summary_shape(pipeline):
    """A paper generator reads this shape; keep it stable."""
    summary = _compare(pipeline, pipeline["files_out"]).summary()
    assert summary["schema"] == SUMMARY_SCHEMA
    assert summary["engine"] == {"cantera": PINNED_CANTERA}
    assert summary["grid"] == {
        "temperatures_k": list(GRID_TEMPERATURES_K),
        "pressures_atm": list(GRID_PRESSURES_ATM),
        "composition": "equimolar over every species of the mechanism",
    }
    assert set(summary) == {
        "schema", "fixture", "engine", "grid", "bounds", "counts", "forms", "species",
        "reactions", "unsupported", "export_gaps", "unmatched", "parameter_violations",
        "bound_violations",
    }
    assert set(summary["counts"]) == {
        "species_compared", "reactions_compared", "rate_terms_compared", "unsupported",
        "export_gaps", "unmatched", "parameter_violations", "bound_violations",
    }
    species_keys = {
        "smiles", "name_in", "name_out", "max_abs_deviation", "max_bound",
        "worst_deviation_over_bound", "coefficient_max_abs_diff", "within_bound",
    }
    reaction_keys = {
        "key", "form", "equation_in", "equation_out", "n_terms", "duplicate",
        "max_rel_deviation_kf", "max_rel_deviation_rop", "max_rel_bound",
        "worst_deviation_over_bound", "worst_point", "within_bound",
    }
    assert all(set(entry) == species_keys for entry in summary["species"])
    assert all(set(entry) == reaction_keys for entry in summary["reactions"])
    assert all(set(entry["max_abs_deviation"]) == {"cp_over_r", "h_over_rt", "s_over_r"} for entry in summary["species"])
    assert [entry["smiles"] for entry in summary["species"]] == sorted(entry["smiles"] for entry in summary["species"])
    assert [entry["key"] for entry in summary["reactions"]] == sorted(entry["key"] for entry in summary["reactions"])
    json.dumps(summary, sort_keys=True)  # serialisable


# ---------------------------------------------------------------------------
# Unsupported forms are listed, not dropped (without touching the fixture)
# ---------------------------------------------------------------------------

_PLOG_MECHANISM = """
ELEMENTS
H
END
SPECIES
H2   ! SMILES=[H][H]
H    ! SMILES=[H]
END
THERMO ALL
   300.000  1000.000  5000.000
H2                      H   2               G   200.000  6000.000 1000.00      1
 2.93286575E+00 8.26608026E-04-1.46402364E-07 1.54100414E-11-6.88804800E-16    2
-8.13065581E+02-1.02432865E+00 2.34433112E+00 7.98052075E-03-1.94781510E-05    3
 2.01572094E-08-7.37611761E-12-9.17935173E+02 6.83010238E-01                   4
H                       H   1               G   200.000  6000.000 1000.00      1
 2.50000000E+00 0.00000000E+00 0.00000000E+00 0.00000000E+00 0.00000000E+00    2
 2.54736599E+04-4.46682853E-01 2.50000000E+00 7.05332819E-13-1.99591964E-15    3
 2.30081632E-18-9.27732332E-22 2.54736599E+04-4.46682853E-01                   4
END
REACTIONS CAL/MOLE MOLES
H2 <=> H + H   1.0E+12 0.0 100000.0
    PLOG / 0.1 1.0E+11 0.0 100000.0 /
    PLOG / 10.0 1.0E+13 0.0 100000.0 /
END
"""


def test_an_unsupported_form_is_listed_not_dropped():
    gas = load_solution({"chem.inp": _PLOG_MECHANISM})
    smiles = {"H2": "[H][H]", "H": "[H]"}
    forms = {key[3] for key in group_reactions(gas, smiles)}
    assert forms == {"pressure-dependent-Arrhenius"}
    assert not forms & SUPPORTED_FORMS
    result = compare_mechanisms(gas, gas, smiles, smiles, fixture="plog-probe")
    assert result.reactions == []
    assert supported_keys(gas, smiles) == set()
    assert len(result.unsupported) == 1
    assert result.unsupported[0]["form"] == "pressure-dependent-Arrhenius"
    assert result.unsupported[0]["kind"] == "form"
    assert result.summary()["counts"]["unsupported"] == 1
    # Listing an unsupported form is not itself a failure of the export.
    assert result.violations == []


# ---------------------------------------------------------------------------
# Mutations of the exported text
# ---------------------------------------------------------------------------


def test_mutation_swapped_nasa_blocks_is_red(pipeline):
    files = dict(pipeline["files_out"])
    water = pipeline["name_out"][canonical("O")]
    files["therm.dat"] = swap_nasa_blocks(files["therm.dat"], water)
    result = _compare(pipeline, files)
    assert any(v.startswith(f"{canonical('O')}: reduced thermo deviation") for v in result.bound_violations), result.violations
    # Only water moved.
    assert {v.split(":")[0] for v in result.bound_violations} == {canonical("O")}
    assert result.parameter_violations == []


def _water_thermo_mutation(pipeline, printed_digit: int) -> GridComparison:
    files = dict(pipeline["files_out"])
    water = pipeline["name_out"][canonical("O")]
    # Coefficient 0: the constant Cp/R term of the high-temperature block, so
    # the grid points above T_mid (1500 K, 2000 K) see the change directly.
    files["therm.dat"] = perturb_nasa_coefficient(files["therm.dat"], water, 0, printed_digit)
    assert files["therm.dat"] != pipeline["files_out"]["therm.dat"]
    return _compare(pipeline, files)


def test_mutation_nasa_coefficient_plus_one_unit_of_the_ninth_printed_digit_is_red(pipeline):
    # The export prints nine significant digits; the bound is half a unit of
    # the ninth, so a full unit there is twice the bound.
    result = _water_thermo_mutation(pipeline, 9)
    assert result.bound_violations, "the thermo bound admitted a full unit of the last printed digit"
    assert {v.split(":")[0] for v in result.bound_violations} == {canonical("O")}
    water = next(e for e in result.species if e["smiles"] == canonical("O"))
    assert water["within_bound"] is False
    assert water["max_abs_deviation"]["cp_over_r"] == pytest.approx(1e-8, rel=1e-3)


def test_mutation_nasa_coefficient_plus_one_unit_below_print_precision_is_green(pipeline):
    # One unit in a tenth digit is a tenth of the printed unit: inside the bound.
    result = _water_thermo_mutation(pipeline, 10)
    assert result.violations == [], result.violations
    water = next(e for e in result.species if e["smiles"] == canonical("O"))
    assert 0 < water["max_abs_deviation"]["cp_over_r"] < water["max_bound"]["cp_over_r"]
    assert water["max_abs_deviation"]["cp_over_r"] == pytest.approx(1e-9, rel=1e-3)


#: HO2 + NH2 <=> O2 + NH3, A = 2.179000e+06 in the fixture, printed 2.1790E+06 by the export.
_A_PRINTED = "2.1790E+06"
_A_REACTION = "[NH2] + [O]O <=> N + [O][O] | Arrhenius"


def _a_mutation(pipeline, new_text: str) -> GridComparison:
    files = dict(pipeline["files_out"])
    files["chem.inp"] = replace_unique(files["chem.inp"], f" {_A_PRINTED} ", f" {new_text} ")
    return _compare(pipeline, files)


def test_mutation_a_plus_one_unit_of_the_original_print_is_green(pipeline):
    # 2.179000e+06 -> 2.179001e+06: one unit in the original's seventh digit,
    # a hundredth of a unit of the export's grid, inside the half-unit bound.
    result = _a_mutation(pipeline, "2.179001E+06")
    assert result.violations == [], result.violations
    entry = next(e for e in result.reactions if e["key"] == _A_REACTION)
    assert 0 < entry["max_rel_deviation_kf"] < entry["max_rel_bound"]


def test_mutation_a_plus_one_unit_of_the_export_print_is_red(pipeline):
    # 2.1790E+06 -> 2.1791E+06: one full unit of the export's own grid. The
    # export reproduced the stored value exactly at that digit, so the bound,
    # half a unit, is exceeded by construction. (The brief expected this row
    # green; a half-unit rounding bound cannot admit a full-unit change.)
    result = _a_mutation(pipeline, "2.1791E+06")
    assert result.bound_violations and all(v.startswith(_A_REACTION) for v in result.bound_violations)
    entry = next(e for e in result.reactions if e["key"] == _A_REACTION)
    assert entry["within_bound"] is False
    assert entry["max_rel_deviation_kf"] == pytest.approx(1e2 / 2.179e6, rel=1e-6)
    assert result.parameter_violations == []


def test_mutation_a_plus_ten_units_of_the_export_print_is_red(pipeline):
    result = _a_mutation(pipeline, "2.1800E+06")
    assert result.bound_violations and all(v.startswith(_A_REACTION) for v in result.bound_violations)
    entry = next(e for e in result.reactions if e["key"] == _A_REACTION)
    assert entry["max_rel_deviation_kf"] == pytest.approx(1e3 / 2.179e6, rel=1e-6)
    assert entry["max_rel_deviation_kf"] > 10 * entry["max_rel_bound"] / 2


def test_mutation_dropped_troe_efficiency_is_red(pipeline):
    # H + CH3 (+M) <=> CH4 (+M): Troe with five colliders; drop water (6.0).
    name = pipeline["name_out"]
    files = dict(pipeline["files_out"])
    files["chem.inp"] = drop_efficiency(
        files["chem.inp"],
        [name[canonical("[H]")], name[canonical("[CH3]")]],
        [name[canonical("C")]],
        name[canonical("O")],
    )
    result = _compare(pipeline, files)
    label = "[CH3] + [H] <=> C [M] | falloff-Troe"
    assert any(v.startswith(label) and "efficiencies" in v for v in result.parameter_violations), result.violations
    # The dropped collider also moves k(T,P) outside the bound: it is not only a
    # bookkeeping difference.
    assert any(v.startswith(label) for v in result.bound_violations), result.bound_violations
    assert {v.split(":")[0] for v in result.violations} == {label}
