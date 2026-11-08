"""M2: identity resolution (RMG adjlist + CSV), fail-loud, formula cross-check."""

from pathlib import Path

import pytest
from rdkit import Chem

from tckdb_chemkin.identity import (
    IdentityResolutionError,
    IdentityResolver,
    parse_species_dictionary,
    parse_species_map_csv,
    species_from_smiles,
)
from tckdb_chemkin.parser import parse_mechanism

FIXTURES = Path(__file__).parent / "fixtures"


def read(name):
    return (FIXTURES / name).read_text()


def canon(smiles):
    return Chem.MolToSmiles(Chem.MolFromSmiles(smiles))


@pytest.fixture
def rmg_dict():
    return parse_species_dictionary(read("species_dictionary.txt"))


def test_adjlist_radical_ch3(rmg_dict):
    ch3 = rmg_dict["CH3"]
    assert ch3.smiles == canon("[CH3]")
    assert ch3.multiplicity == 2
    assert ch3.charge == 0
    assert ch3.formula == {"C": 1, "H": 3}
    assert ch3.source == "rmg_dict"


def test_adjlist_closed_shell_ch4(rmg_dict):
    ch4 = rmg_dict["CH4"]
    assert ch4.smiles == canon("C")
    assert ch4.multiplicity == 1
    assert ch4.formula == {"C": 1, "H": 4}


def test_adjlist_triplet_o2(rmg_dict):
    o2 = rmg_dict["O2"]
    assert o2.smiles == canon("[O][O]")
    assert o2.multiplicity == 3


def test_adjlist_atomic_oxygen_triplet(rmg_dict):
    o = rmg_dict["O"]
    assert o.smiles == canon("[O]")
    assert o.multiplicity == 3  # ground-state triplet O


def test_adjlist_hydroxyl(rmg_dict):
    oh = rmg_dict["OH"]
    assert oh.smiles == canon("[OH]")
    assert oh.multiplicity == 2


def test_csv_map_with_explicit_charge_multiplicity():
    m = parse_species_map_csv(read("species_map.csv"))
    assert m["CH3"].smiles == canon("[CH3]")
    assert m["CH3"].multiplicity == 2
    assert m["CH4"].multiplicity == 1


def test_csv_defaults_multiplicity_from_structure():
    sp = species_from_smiles("radical", "[CH3]")
    assert sp.multiplicity == 2  # derived from radical count


def test_bath_gas_builtin_argon():
    resolver = IdentityResolver()
    from tckdb_chemkin.ast import SpeciesDecl

    ar = resolver.resolve_one(SpeciesDecl(name="AR"))
    assert ar is not None
    assert ar.smiles == canon("[Ar]")
    assert ar.source == "bath_gas"


def test_full_mechanism_resolves(rmg_dict):
    mech = parse_mechanism(read("mini.inp"), thermo_text=read("therm.dat"))
    resolver = IdentityResolver(rmg_dict=rmg_dict)
    resolved = resolver.resolve_mechanism(mech)
    # every SPECIES name resolved (AR via bath gas)
    assert set(mech.species_names).issubset(resolved)


def test_csv_takes_priority_over_rmg_dict(rmg_dict):
    csv_map = {"CH4": species_from_smiles("CH4", "C", source="csv")}
    resolver = IdentityResolver(csv_map=csv_map, rmg_dict=rmg_dict)
    from tckdb_chemkin.ast import SpeciesDecl

    assert resolver.resolve_one(SpeciesDecl(name="CH4")).source == "csv"


def test_inline_comment_smiles():
    text = (
        "SPECIES\n"
        "FOO  ! SMILES=CCO\n"
        "END\n"
    )
    mech = parse_mechanism(text)
    resolver = IdentityResolver()
    resolved = resolver.resolve_one(mech.species[0])
    assert resolved is not None
    assert resolved.smiles == canon("CCO")
    assert resolved.source == "comment"


# --- fail-loud behaviour --------------------------------------------------


def test_unmapped_species_fails_loud(rmg_dict):
    text = (
        "SPECIES\nH2 XYZ ZZZ\nEND\n"
        "REACTIONS\nH2 + XYZ <=> ZZZ  1.0 0.0 0.0\nEND\n"
    )
    mech = parse_mechanism(text)
    resolver = IdentityResolver(rmg_dict=rmg_dict)
    with pytest.raises(IdentityResolutionError) as exc:
        resolver.resolve_mechanism(mech)
    assert "XYZ" in exc.value.unmapped
    assert "ZZZ" in exc.value.unmapped
    assert "XYZ" in str(exc.value)


def test_formula_mismatch_fails_loud(rmg_dict):
    # Map CH4 to water; its resolved formula H2O1 disagrees with the C1H4
    # NASA thermo composition for CH4 -> hard error.
    mech = parse_mechanism(read("mini.inp"), thermo_text=read("therm.dat"))
    bad_csv = {"CH4": species_from_smiles("CH4", "O", source="csv")}
    resolver = IdentityResolver(csv_map=bad_csv, rmg_dict=rmg_dict)
    with pytest.raises(IdentityResolutionError) as exc:
        resolver.resolve_mechanism(mech)
    assert "CH4" in exc.value.mismatches


def test_allow_pseudo_maps_lump(rmg_dict):
    from tckdb_chemkin.ast import SpeciesDecl

    resolver = IdentityResolver(rmg_dict=rmg_dict, allow_pseudo=True)
    res = resolver.resolve_one(SpeciesDecl(name="LUMP123"))
    assert res is not None
    assert res.molecule_kind == "pseudo"


# --- spin from a TCKDB export's MULT= comment ------------------------------


def test_inline_comment_mult_separates_spin_states_of_one_structure():
    mech = parse_mechanism(
        "SPECIES\n"
        "CH2_T  ! SMILES=[CH2]  MULT=3\n"
        "CH2_S  ! SMILES=[CH2]  MULT=1\n"
        "END\n"
    )
    resolver = IdentityResolver()
    triplet, singlet = (resolver.resolve_one(d) for d in mech.species)
    assert (triplet.smiles, triplet.multiplicity) == (canon("[CH2]"), 3)
    assert (singlet.smiles, singlet.multiplicity) == (canon("[CH2]"), 1)


def test_inline_comment_without_mult_keeps_the_radical_count_default():
    mech = parse_mechanism("SPECIES\nFOO  ! SMILES=[CH3]\nEND\n")
    assert IdentityResolver().resolve_one(mech.species[0]).multiplicity == 2


def test_inline_comment_mult_is_read_beside_stereo_tokens():
    mech = parse_mechanism("SPECIES\nFOO  ! SMILES=C/C=C/C  MULT=1  STEREO=E ref=sp_1\nEND\n")
    resolved = IdentityResolver().resolve_one(mech.species[0])
    assert resolved.multiplicity == 1
    assert resolved.smiles == canon("C/C=C/C")


# --- RMG formal charge (c+1 / c-1 / c+2) ------------------------------------

_CHARGED = """\
CO(11)
multiplicity 1
1 C u0 p1 c-1 {2,T}
2 O u0 p1 c+1 {1,T}

"""


def test_species_dictionary_reads_signed_charges():
    resolved = parse_species_dictionary(_CHARGED)["CO(11)"]
    assert resolved.charge == 0
    assert resolved.smiles == canon("[C-]#[O+]")


def test_species_dictionary_reads_a_net_charge():
    text = "OH-(1)\n1 O u0 p3 c-1 {2,S}\n2 H u0 p0 c0 {1,S}\n\n"
    resolved = parse_species_dictionary(text)["OH-(1)"]
    assert resolved.charge == -1


def test_unreadable_atom_line_is_an_error_not_skipped():
    text = "BAD(1)\n1 C u0 p0 c? {2,S}\n2 H u0 p0 c0 {1,S}\n\n"
    with pytest.raises(ValueError, match=r"BAD\(1\).*c\?"):
        parse_species_dictionary(text)
