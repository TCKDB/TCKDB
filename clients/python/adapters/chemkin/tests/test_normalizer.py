"""M1: normalizer unit resolution + kinetics-form tagging."""

from pathlib import Path

import pytest

from tckdb_chemkin.normalizer import normalize_mechanism
from tckdb_chemkin.parser import parse_mechanism

FIXTURES = Path(__file__).parent / "fixtures"


def read(name):
    return (FIXTURES / name).read_text()


@pytest.fixture
def norm():
    mech = parse_mechanism(read("mini.inp"), thermo_text=read("therm.dat"))
    return normalize_mechanism(mech)


def by_index(norm, i):
    return norm.reactions[i]


def test_arrhenius_form_and_units(norm):
    r1 = by_index(norm, 0)
    # n != 0 -> modified_arrhenius
    assert r1.model_kind == "modified_arrhenius" if r1.n != 0 else "arrhenius"
    # H + O2 is bimolecular, MOLES basis -> cm3_mol_s
    assert r1.a_units == "cm3_mol_s"
    # Ea passes through natively (cal_mol has an enum home)
    assert r1.reported_ea == pytest.approx(16800.0)
    assert r1.reported_ea_units == "cal_mol"


def test_arrhenius_vs_modified(norm):
    r1 = by_index(norm, 0)  # n = 0.0
    assert r1.model_kind == "arrhenius"


def test_troe_falloff_form_and_low_units(norm):
    r2 = by_index(norm, 1)
    assert r2.model_kind == "troe"
    assert r2.is_falloff
    # k-inf is bimolecular (CH3 + H) -> cm3_mol_s
    assert r2.a_units == "cm3_mol_s"
    # k0 is one order higher -> cm6_mol2_s
    assert r2.falloff.low_a_units == "cm6_mol2_s"
    assert r2.falloff.troe_alpha == pytest.approx(0.783)
    assert r2.falloff.troe_t3 == pytest.approx(74.0)
    assert r2.falloff.troe_t1 == pytest.approx(2941.0)
    assert r2.falloff.troe_t2 == pytest.approx(6964.0)
    # low Ea converted cal -> kJ/mol
    assert r2.falloff.low_ea_kj_mol == pytest.approx(2440.0 * 4.184e-3)


def test_plog_form_and_pressure_conversion(norm):
    r3 = by_index(norm, 2)
    assert r3.model_kind == "plog"
    assert len(r3.plog) == 3
    p0 = r3.plog[0]
    assert p0.entry_index == 1
    assert p0.pressure_bar == pytest.approx(0.1 * 1.01325)
    assert p0.a_units == "cm3_mol_s"
    assert p0.ea_kj_mol == pytest.approx(3200.0 * 4.184e-3)


def test_chebyshev_form_and_pressure_domain(norm):
    r4 = by_index(norm, 3)
    assert r4.model_kind == "chebyshev"
    c = r4.chebyshev
    assert (c.n_temperature, c.n_pressure) == (3, 3)
    assert c.tmin_k == pytest.approx(300.0)
    assert c.pmin_bar == pytest.approx(0.01 * 1.01325)
    assert c.pmax_bar == pytest.approx(100.0 * 1.01325)


def test_simple_third_body_form(norm):
    r5 = by_index(norm, 4)
    # n = -1 -> modified_arrhenius, third body but not falloff
    assert r5.model_kind == "modified_arrhenius"
    assert r5.is_third_body and not r5.is_falloff
    # termolecular (O + O + M): the [M] term raises the order to 3 -> cm6_mol2_s
    assert r5.a_units == "cm6_mol2_s"


def test_duplicate_collapses_to_multi_arrhenius(norm):
    # The R6 DUPLICATE pair (two identical-equation Arrhenius lines) collapses
    # into a single multi_arrhenius reaction carrying both terms (DR-0036).
    r6 = by_index(norm, 5)
    assert r6.model_kind == "multi_arrhenius"
    assert r6.duplicate
    # Scalar main-line rate is unset; the coefficients live in the terms.
    assert r6.a is None and r6.n is None and r6.reported_ea is None
    entries = r6.arrhenius_entries
    assert [e.entry_index for e in entries] == [1, 2]
    # O + OH is bimolecular -> each summed term keeps cm3_mol_s.
    assert all(e.a_units == "cm3_mol_s" for e in entries)
    assert sorted(e.a for e in entries) == pytest.approx([3.0e12, 1.0e13])
    assert all(e.reported_ea_units == "cal_mol" for e in entries)
    assert sorted(e.reported_ea for e in entries) == pytest.approx([500.0, 700.0])


def test_mismatched_efficiencies_do_not_collapse():
    """Two ``+M`` DUPLICATE lines with DIFFERENT per-line efficiency lists must
    NOT be summed into a multi_arrhenius — that would silently discard the
    second line's efficiencies and store a scientifically wrong rate. They stay
    as two separate rates, each keeping its own efficiencies and carrying a
    ``NOT collapsed`` warning."""
    text = """ELEMENTS
H O AR
END
SPECIES
O O2 H2O AR
END
REACTIONS  CAL/MOLE  MOLES
O + O + M <=> O2 + M      1.000E+17   0.000   0.0
    H2O/6.0/
    DUP
O + O + M <=> O2 + M      2.000E+13   0.000   0.0
    H2O/3.0/  AR/2.0/
    DUP
END
"""
    norm = normalize_mechanism(parse_mechanism(text))
    # Not collapsed: still two separate third-body reactions.
    assert len(norm.reactions) == 2
    assert all(r.model_kind != "multi_arrhenius" for r in norm.reactions)
    assert all(r.is_third_body for r in norm.reactions)
    # Each preserves its OWN efficiency mapping (nothing silently dropped).
    effs = sorted(tuple(sorted(r.efficiencies.items())) for r in norm.reactions)
    assert effs == [(("AR", 2.0), ("H2O", 3.0)), (("H2O", 6.0),)]
    # Each carries a "not collapsed" warning explaining the mismatch.
    assert all(
        any("NOT collapsed" in w for w in r.warnings) for r in norm.reactions
    )


def test_agreeing_arrhenius_duplicates_collapse():
    """The positive control: two plain-Arrhenius DUPLICATE lines that AGREE (no
    third body, no efficiencies) still collapse into one multi_arrhenius."""
    text = """ELEMENTS
H O
END
SPECIES
H O2 O OH
END
REACTIONS  CAL/MOLE  MOLES
H + O2 <=> O + OH      1.000E+13   0.000   500.0
    DUP
H + O2 <=> O + OH      3.000E+12   0.000   700.0
    DUP
END
"""
    norm = normalize_mechanism(parse_mechanism(text))
    assert len(norm.reactions) == 1
    r = norm.reactions[0]
    assert r.model_kind == "multi_arrhenius"
    assert [e.entry_index for e in r.arrhenius_entries] == [1, 2]
    assert sorted(e.a for e in r.arrhenius_entries) == pytest.approx(
        [3.0e12, 1.0e13]
    )


def test_rev_and_lt_warnings(norm):
    # R7 (the LT-unsupported reaction) is now at index 6: the R6 DUPLICATE pair
    # collapsed two source lines into one multi_arrhenius reaction.
    r7 = by_index(norm, 6)
    assert any("Unsupported aux" in w for w in r7.warnings)


def test_units_kcal_molecules_conversion():
    mech = parse_mechanism(read("units_kcal.inp"))
    norm = normalize_mechanism(mech)
    r = norm.reactions[0]
    # bimolecular + MOLECULES basis
    assert r.a_units == "cm3_molecule_s"
    # KCAL/MOLE passes through natively as kcal_mol
    assert r.reported_ea == pytest.approx(12.0)
    assert r.reported_ea_units == "kcal_mol"
