"""Gaussian and ORCA keywords that change a result land on a canonical key with a closed-set value.

Each case is a deck line as a depositor writes it. A spelling the parser cannot place stays a raw row with no
canonical key; it is never forced onto the nearest value. The reconciliation against what the depositor typed
(``test_parameter_reconciliation``) compares canonical values only, so a spelling left raw here is a comparison
that cannot be made, not one that passes.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.services.gaussian_parameter_parser import _parse_route_tokens
from app.services.orca_parameter_parser import parse_orca_log
from app.services.parameter_closed_values import CLOSED_VALUES


def _canonical(params: list[dict]) -> dict[str, list[str | None]]:
    out: dict[str, list[str | None]] = {}
    for p in params:
        if p["canonical_key"] is not None:
            out.setdefault(p["canonical_key"], []).append(p["canonical_value"])
    return out


def _orca(deck: str) -> list[dict]:
    return parse_orca_log(text=deck + "\n* xyz 0 1\nH 0 0 0\n*\n")["parameters"]


# ---------------------------------------------------------------------------
# Gaussian
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "route, expected",
    [
        ("#p b3lyp/6-31g(d) opt int=ultrafine", "ultrafine"),
        ("#p b3lyp/6-31g(d) opt Int=UltraFine", "ultrafine"),
        ("#p b3lyp/6-31g(d) opt int=(grid=ultrafine)", "ultrafine"),
        ("#p b3lyp/6-31g(d) opt int(grid=ultrafine)", "ultrafine"),
        ("#p b3lyp/6-31g(d) opt integral=(grid=ultrafine,acc2e=12)", "ultrafine"),
        ("#p b3lyp/6-31g(d) opt integral=ultrafinegrid", "ultrafine"),
        ("#p b3lyp/6-31g(d) opt int=grid=ultrafine", "ultrafine"),
        # A named grid and its size are one value.
        ("#p b3lyp/6-31g(d) opt int=(grid=99590)", "ultrafine"),
        ("#p b3lyp/6-31g(d) opt int=(grid=75302)", "fine"),
        ("#p b3lyp/6-31g(d) opt int=finegrid", "fine"),
        ("#p b3lyp/6-31g(d) opt int=superfine", "superfine"),
        ("#p b3lyp/6-31g(d) opt int=(grid=-96032)", "-96032"),
    ],
)
def test_gaussian_grid_spellings_give_one_canonical_value(route, expected):
    assert _canonical(_parse_route_tokens(route))["grid.quality"] == [expected]


def test_gaussian_grid_without_a_stated_grid_has_no_grid_row():
    assert "grid.quality" not in _canonical(_parse_route_tokens("#p b3lyp/6-31g(d) opt freq"))


@pytest.mark.parametrize(
    "route, expected",
    [
        ("#p b3lyp/6-31g(d) scf=tight", "tight"),
        ("#p b3lyp/6-31g(d) scf=verytight", "very_tight"),
        ("#p b3lyp/6-31g(d) scf = tight", "tight"),
        ("#p b3lyp/6-31g(d) scf(tight)", "tight"),
        ("#p b3lyp/6-31g(d) scf=(tight,maxcycle=512)", "tight"),
    ],
)
def test_gaussian_scf_convergence_spellings(route, expected):
    assert _canonical(_parse_route_tokens(route))["scf.convergence"] == [expected]


def test_gaussian_opt_options_without_equals_are_read():
    canonical = _canonical(_parse_route_tokens("#p b3lyp/6-31g(d) opt(ts,calcfc,noeigentest)"))
    assert canonical["opt.saddle_order"] == ["1"]
    assert canonical["opt.initial_hessian"] == ["calculate_at_first_point"]
    assert canonical["opt.eigen_test"] == ["disabled"]


def test_gaussian_free_form_values_are_mirrored_into_the_canonical_value():
    canonical = _canonical(_parse_route_tokens("#p b3lyp/6-31g(d) scf=(maxcycle=512) int=(acc2e=12) opt=(maxstep=10)"))
    assert canonical["scf.max_cycles"] == ["512"]
    assert canonical["integral.accuracy"] == ["12"]
    assert canonical["opt.max_step"] == ["10"]


@pytest.mark.parametrize(
    "route, expected",
    [
        ("#p b3lyp/6-31g(d) EmpiricalDispersion=GD3BJ", "d3_bj"),
        ("#p b3lyp/6-31g(d) EmpiricalDispersion=GD3", "d3_zero"),
        ("#p b3lyp/6-31g(d) EmpiricalDispersion(GD2)", "d2"),
        ("#p b3lyp/6-31g(d) EmpiricalDispersion=None", "none"),
    ],
)
def test_gaussian_empirical_dispersion(route, expected):
    assert _canonical(_parse_route_tokens(route))["dispersion.correction"] == [expected]


def test_gaussian_dispersion_the_vocabulary_does_not_know_stays_raw():
    params = _parse_route_tokens("#p b3lyp/6-31g(d) EmpiricalDispersion=PFD")
    assert "dispersion.correction" not in _canonical(params)
    assert any(p["raw_key"] == "EmpiricalDispersion" and p["raw_value"] == "PFD" for p in params)


def test_gaussian_scrf_model_and_solvent():
    canonical = _canonical(_parse_route_tokens("#p b3lyp/6-31g(d) opt SCRF=(SMD,Solvent=Water)"))
    assert canonical["solvation.model"] == ["smd"]
    assert canonical["solvation.solvent"] == ["water"]


def test_gaussian_scrf_with_a_solvent_and_no_model_states_no_model():
    canonical = _canonical(_parse_route_tokens("#p b3lyp/6-31g(d) opt scrf=(solvent=toluene)"))
    assert canonical["solvation.solvent"] == ["toluene"]
    assert "solvation.model" not in canonical  # the program default is not assumed


def test_gaussian_scrf_solvent_name_with_a_comma_is_kept_whole():
    canonical = _canonical(_parse_route_tokens("#p b3lyp/6-31g(d) scrf=(pcm,solvent=n,n-dimethylformamide)"))
    assert canonical["solvation.solvent"] == ["n,n-dimethylformamide"]
    assert canonical["solvation.model"] == ["iefpcm"]


@pytest.mark.parametrize(
    "route, expected",
    [
        ("#p ub3lyp/6-31g(d) stable=opt", "optimize"),
        ("#p ub3lyp/6-31g(d) stable(opt)", "optimize"),
        ("#p ub3lyp/6-31g(d) stable", "check_only"),
    ],
)
def test_gaussian_stable(route, expected):
    assert _canonical(_parse_route_tokens(route))["scf.stability_analysis"] == [expected]


@pytest.mark.parametrize(
    "route, expected",
    [
        ("#p mp2(full)/6-31g(d)", "all_electron"),
        ("#p ccsd(t,full)/cc-pvtz", "all_electron"),
        ("#p ccsd(t,fc)/cc-pvtz", "frozen_core"),
        ("#p mp2(fc) opt", "frozen_core"),
    ],
)
def test_gaussian_frozen_core_marker(route, expected):
    assert _canonical(_parse_route_tokens(route))["core.frozen"] == [expected]


def test_gaussian_plain_correlated_method_states_no_core_choice():
    assert "core.frozen" not in _canonical(_parse_route_tokens("#p ccsd(t)/cc-pvtz"))
    assert "core.frozen" not in _canonical(_parse_route_tokens("#p b3lyp/6-31g(d) pop=(full)"))


def test_gaussian_basis_function_type_tokens():
    canonical = _canonical(_parse_route_tokens("#p b3lyp/gen 6d 10f"))
    assert canonical["basis.d_function_type"] == ["cartesian"]
    assert canonical["basis.f_function_type"] == ["cartesian"]
    canonical = _canonical(_parse_route_tokens("#p b3lyp/gen 5D 7F"))
    assert canonical["basis.d_function_type"] == ["spherical"]
    assert canonical["basis.f_function_type"] == ["spherical"]


def test_gaussian_relativistic_hamiltonian():
    assert _canonical(_parse_route_tokens("#p b3lyp/gen int=dkh"))["relativistic.hamiltonian"] == ["dkh2"]
    assert _canonical(_parse_route_tokens("#p b3lyp/gen int=(ultrafine,dkh2)"))["relativistic.hamiltonian"] == ["dkh2"]


# ---------------------------------------------------------------------------
# ORCA
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "keyword, expected",
    [("RIJCOSX", "rijcosx"), ("RIJK", "rijk"), ("NoRI", "none"), ("RIJONX", "rijonx"), ("NoCOSX", "rij")],
)
def test_orca_ri_approximation(keyword, expected):
    assert _canonical(_orca(f"! B3LYP def2-SVP {keyword}"))["ri.approximation"] == [expected]


def test_orca_without_an_ri_keyword_states_no_ri_approximation():
    assert "ri.approximation" not in _canonical(_orca("! B3LYP def2-SVP TightSCF"))


@pytest.mark.parametrize("keyword, expected", [("D3BJ", "d3_bj"), ("D3ZERO", "d3_zero"), ("D3", "d3_zero"), ("D4", "d4"), ("NoD3", "none")])
def test_orca_dispersion_keyword(keyword, expected):
    assert _canonical(_orca(f"! B3LYP def2-SVP {keyword}"))["dispersion.correction"] == [expected]


def test_orca_cpcm_keyword_names_model_and_solvent():
    params = _orca("! B3LYP def2-SVP CPCM(Water)")
    assert _canonical(params)["solvation.model"] == ["cpcm"]
    assert _canonical(params)["solvation.solvent"] == ["water"]


def test_orca_smd_block_turns_a_cpcm_keyword_into_one_smd_model():
    deck = '! B3LYP def2-SVP CPCM(Water)\n%cpcm smd true SMDsolvent "water" end'
    canonical = _canonical(_orca(deck))
    assert canonical["solvation.model"] == ["smd"]
    assert canonical["solvation.solvent"] == ["water", "water"]


@pytest.mark.parametrize("keyword, expected", [("FrozenCore", "frozen_core"), ("NoFrozenCore", "all_electron")])
def test_orca_frozen_core_keyword(keyword, expected):
    assert _canonical(_orca(f"! DLPNO-CCSD(T) cc-pVTZ {keyword}"))["core.frozen"] == [expected]


def test_orca_frozen_core_block_window():
    assert _canonical(_orca("! DLPNO-CCSD(T) cc-pVTZ\n%method FrozenCore FC_EWIN end"))["core.frozen"] == ["window"]
    assert _canonical(_orca("! DLPNO-CCSD(T) cc-pVTZ\n%method FrozenCore FC_NONE end"))["core.frozen"] == ["all_electron"]


@pytest.mark.parametrize("keyword, expected", [("DKH2", "dkh2"), ("DKH", "dkh2"), ("ZORA", "zora"), ("X2C", "x2c")])
def test_orca_relativistic_keyword(keyword, expected):
    assert _canonical(_orca(f"! B3LYP SARC-DKH-TZVP {keyword}"))["relativistic.hamiltonian"] == [expected]


def test_orca_auxiliary_basis_tokens_are_raw_rows_with_no_canonical_key():
    params = _orca("! B3LYP def2-TZVP def2/J def2-TZVP/C def2/JK")
    aux = [p for p in params if p["section"] == "basis_aux"]
    assert {p["raw_key"] for p in aux} == {"def2/J", "def2-TZVP/C", "def2/JK"}
    assert all(p["canonical_key"] is None for p in aux)
    # No leftover "general" row for a basis token.
    assert not [p for p in params if p["section"] == "general" and "/" in p["raw_key"]]


# ---------------------------------------------------------------------------
# The vocabulary
# ---------------------------------------------------------------------------


def test_every_closed_set_key_is_in_the_seeded_vocabulary(db_conn):
    rows = db_conn.execute(
        text("SELECT canonical_key FROM calculation_parameter_vocab WHERE canonical_key = ANY(:keys)"),
        {"keys": sorted(CLOSED_VALUES)},
    ).scalars().all()
    assert sorted(rows) == sorted(CLOSED_VALUES)


def test_a_canonical_value_outside_its_closed_set_is_dropped_to_a_raw_row():
    from app.services.parameter_closed_values import canonical_or_none

    assert canonical_or_none("dispersion.correction", "d3_bj") == ("dispersion.correction", "d3_bj")
    assert canonical_or_none("dispersion.correction", "pfd") == (None, None)
    assert canonical_or_none("dispersion.correction", None) == (None, None)
    assert canonical_or_none("scf.max_cycles", "512") == ("scf.max_cycles", "512")
