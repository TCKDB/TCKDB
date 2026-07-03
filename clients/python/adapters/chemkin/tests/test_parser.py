"""M1: parser + transport AST correctness (no DB, no network, no RDKit)."""

from pathlib import Path

import pytest

from tckdb_chemkin.parser import parse_mechanism, parse_thermo_file
from tckdb_chemkin.transport import parse_transport_file

FIXTURES = Path(__file__).parent / "fixtures"


def read(name):
    return (FIXTURES / name).read_text()


@pytest.fixture
def mech():
    return parse_mechanism(read("mini.inp"), thermo_text=read("therm.dat"))


def test_elements_and_species(mech):
    assert mech.elements == ["H", "O", "C", "AR"]
    assert mech.species_names == ["H", "H2", "O", "O2", "OH", "H2O", "CH3", "CH4", "AR"]


def test_reactions_header_units(mech):
    assert mech.ea_units == "CAL/MOLE"
    assert mech.a_conc_basis == "MOLES"


def test_arrhenius_reaction(mech):
    r1 = mech.reactions[0]
    assert r1.reactant_names == ["H", "O2"]
    assert r1.product_names == ["O", "OH"]
    assert r1.reversible is True
    assert r1.a == pytest.approx(2.0e14)
    assert r1.n == pytest.approx(0.0)
    assert r1.ea == pytest.approx(16800.0)
    assert not r1.is_falloff and not r1.is_third_body


def test_troe_falloff_with_colliders(mech):
    r2 = mech.reactions[1]
    assert r2.is_falloff is True
    assert r2.reactant_names == ["CH3", "H"]
    assert r2.product_names == ["CH4"]
    assert r2.low == pytest.approx((2.477e33, -4.76, 2440.0))
    assert r2.troe == pytest.approx([0.7830, 74.0, 2941.0, 6964.0])
    assert r2.efficiencies == {"H2": 2.0, "H2O": 6.0, "AR": 0.7}
    # (+M) is not part of stoichiometry.
    assert "M" not in r2.reactant_names + r2.product_names


def test_plog_reaction(mech):
    r3 = mech.reactions[2]
    assert len(r3.plog) == 3
    assert r3.plog[0].pressure_atm == pytest.approx(0.1)
    assert r3.plog[1].a == pytest.approx(2.16e8)
    assert r3.plog[2].ea == pytest.approx(3700.0)


def test_chebyshev_reaction(mech):
    r4 = mech.reactions[3]
    assert r4.chebyshev is not None
    cheb = r4.chebyshev
    assert (cheb.n_temperature, cheb.n_pressure) == (3, 3)
    assert cheb.tmin == pytest.approx(300.0)
    assert cheb.tmax == pytest.approx(2500.0)
    assert cheb.pmin_atm == pytest.approx(0.01)
    assert cheb.pmax_atm == pytest.approx(100.0)
    assert cheb.coefficients[0] == pytest.approx([10.0, 0.2, 0.03])
    assert cheb.coefficients[2] == pytest.approx([0.07, 0.008, 0.0009])


def test_simple_third_body_irreversible(mech):
    r5 = mech.reactions[4]
    assert r5.is_third_body is True
    assert r5.is_falloff is False
    assert r5.reversible is False
    assert r5.reactant_names == ["O", "O"]
    assert r5.product_names == ["O2"]
    assert r5.molecularity == 2


def test_duplicate_pair(mech):
    r6a, r6b = mech.reactions[5], mech.reactions[6]
    assert r6a.duplicate and r6b.duplicate
    assert r6a.reactant_names == r6b.reactant_names == ["O", "OH"]
    assert r6a.a != r6b.a


def test_unsupported_lt_aux_captured(mech):
    r7 = mech.reactions[7]
    assert any("LT" in aux.upper() for aux in r7.unsupported_aux)


def test_thermo_nasa_parsing(mech):
    ch4 = mech.thermo["CH4"]
    assert ch4.composition == {"C": 1, "H": 4}
    assert ch4.t_low == pytest.approx(300.0)
    assert ch4.t_high == pytest.approx(5000.0)
    assert ch4.t_common == pytest.approx(1000.0)
    assert len(ch4.coeffs_high) == 7
    assert len(ch4.coeffs_low) == 7
    # generator seeded CH4 high=15.xx, low=16.xx
    assert ch4.coeffs_high[0] == pytest.approx(15.0)
    assert ch4.coeffs_high[6] == pytest.approx(15.06)
    assert ch4.coeffs_low[0] == pytest.approx(16.0)
    assert ch4.coeffs_low[6] == pytest.approx(16.06)


def test_standalone_thermo_file():
    thermo = parse_thermo_file(read("therm.dat"))
    assert set(["H", "H2", "O", "O2", "OH", "H2O", "CH3", "CH4", "AR"]).issubset(thermo)


def test_transport_parsing():
    tran = parse_transport_file(read("tran.dat"))
    h2o = tran["H2O"]
    assert h2o.geometry_index == 2
    assert h2o.eps_over_k == pytest.approx(572.4)
    assert h2o.sigma_angstrom == pytest.approx(2.605)
    assert h2o.dipole_debye == pytest.approx(1.844)
    ch4 = tran["CH4"]
    assert ch4.polarizability_angstrom3 == pytest.approx(2.6)
    assert ch4.rot_relaxation == pytest.approx(13.0)


def test_units_header_kcal_molecules():
    mech = parse_mechanism(read("units_kcal.inp"))
    assert mech.ea_units == "KCAL/MOLE"
    assert mech.a_conc_basis == "MOLECULES"
