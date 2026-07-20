"""M3: payload builder correctness for each kinetics form + units + thermo/transport."""

from pathlib import Path

import pytest
from rdkit import Chem

from tckdb_chemkin.identity import (
    IdentityResolver,
    parse_species_dictionary,
    parse_species_map_csv,
)
from tckdb_chemkin.parser import parse_mechanism
from tckdb_chemkin.payloads import ImportConfig, build_all_payloads
from tckdb_chemkin.transport import parse_transport_file

FIXTURES = Path(__file__).parent / "fixtures"


def read(name):
    return (FIXTURES / name).read_text()


def canon(smiles):
    return Chem.MolToSmiles(Chem.MolFromSmiles(smiles))


@pytest.fixture
def built():
    mech = parse_mechanism(read("mini.inp"), thermo_text=read("therm.dat"))
    mech.transport = parse_transport_file(read("tran.dat"))
    resolver = IdentityResolver(rmg_dict=parse_species_dictionary(read("species_dictionary.txt")))
    config = ImportConfig(scientific_origin="experimental", mechanism_name="MiniMech", mechanism_version="1.0")
    return build_all_payloads(mech, resolver, config)


def _kin_by_reactants(built, reactants):
    want = sorted(canon(s) for s in reactants)
    for p in built.kinetics:
        got = sorted(x["species_entry"]["smiles"] for x in p["reaction"]["reactants"])
        if got == want:
            return p
    raise AssertionError(f"no kinetics payload with reactants {reactants}")


# --- kinetics forms -------------------------------------------------------


def test_arrhenius_payload(built):
    p = _kin_by_reactants(built, ["[H]", "[O][O]"])
    assert p["model_kind"] == "arrhenius"
    assert p["a"] == pytest.approx(2.0e14)
    assert p["a_units"] == "cm3_mol_s"
    assert p["reported_ea"] == pytest.approx(16800.0)
    assert p["reported_ea_units"] == "cal_mol"
    assert p["reaction"]["reversible"] is True
    assert p["scientific_origin"] == "experimental"
    assert p["workflow_tool_release"] == {"name": "MiniMech", "version": "1.0"}


def test_troe_falloff_payload(built):
    p = _kin_by_reactants(built, ["[CH3]", "[H]"])
    assert p["model_kind"] == "troe"
    # Falloff main line is k∞ (order = real reactants); the +M order lives on
    # falloff.low_a_units, so the simple-third-body flag must stay unset.
    assert "is_third_body" not in p
    fo = p["falloff"]
    assert fo["low_a"] == pytest.approx(2.477e33)
    assert fo["low_a_units"] == "cm6_mol2_s"
    assert fo["low_ea_kj_mol"] == pytest.approx(2440.0 * 4.184e-3)
    assert fo["troe_alpha"] == pytest.approx(0.783)
    assert fo["troe_t3"] == pytest.approx(74.0)
    assert fo["troe_t1"] == pytest.approx(2941.0)
    assert fo["troe_t2"] == pytest.approx(6964.0)
    # collider efficiencies resolved to identities
    effs = {e["collider"]["smiles"]: e["efficiency"] for e in p["third_body_efficiencies"]}
    assert effs[canon("[H][H]")] == pytest.approx(2.0)
    assert effs[canon("O")] == pytest.approx(6.0)
    assert effs[canon("[Ar]")] == pytest.approx(0.7)


def test_plog_payload(built):
    p = _kin_by_reactants(built, ["[OH]", "[H][H]"])
    assert p["model_kind"] == "plog"
    entries = p["plog_entries"]
    assert len(entries) == 3
    assert [e["entry_index"] for e in entries] == [1, 2, 3]
    assert entries[0]["pressure_bar"] == pytest.approx(0.1 * 1.01325)
    assert entries[0]["a_units"] == "cm3_mol_s"
    assert entries[2]["ea_kj_mol"] == pytest.approx(3700.0 * 4.184e-3)


def test_chebyshev_payload(built):
    p = _kin_by_reactants(built, ["[H][H]", "[O][O]"])
    assert p["model_kind"] == "chebyshev"
    c = p["chebyshev"]
    assert (c["n_temperature"], c["n_pressure"]) == (3, 3)
    assert c["pmin_bar"] == pytest.approx(0.01 * 1.01325)
    assert len(c["coefficients"]) == 3
    assert c["coefficients"][0] == pytest.approx([10.0, 0.2, 0.03])


def test_simple_third_body_payload(built):
    p = _kin_by_reactants(built, ["[O]", "[O]"])
    assert p["model_kind"] == "modified_arrhenius"
    assert p["a_units"] == "cm6_mol2_s"
    assert p["is_third_body"] is True
    assert p["reaction"]["reversible"] is False
    assert "third-body" in p.get("note", "").lower()


def test_duplicate_collapses_to_multi_arrhenius(built):
    # The R6 DUPLICATE pair collapses into ONE multi_arrhenius payload whose
    # two summed modified-Arrhenius terms live in ``arrhenius_entries`` (the
    # scalar ``a`` must stay unset, per the backend upload contract).
    dups = [
        p for p in built.kinetics
        if sorted(x["species_entry"]["smiles"] for x in p["reaction"]["reactants"])
        == sorted([canon("[O]"), canon("[OH]")])
    ]
    assert len(dups) == 1
    p = dups[0]
    assert p["model_kind"] == "multi_arrhenius"
    assert "a" not in p
    entries = p["arrhenius_entries"]
    assert [e["entry_index"] for e in entries] == [1, 2]
    assert sorted(e["a"] for e in entries) == pytest.approx([3.0e12, 1.0e13])
    # O + OH is bimolecular -> each term shares cm3_mol_s.
    assert all(e["a_units"] == "cm3_mol_s" for e in entries)
    assert sorted(e["reported_ea"] for e in entries) == pytest.approx([500.0, 700.0])
    assert all(e["reported_ea_units"] == "cal_mol" for e in entries)
    assert "DUPLICATE" in p.get("note", "")


# --- thermo / transport ---------------------------------------------------


def test_thermo_nasa_payload_high_low_mapping(built):
    ch4 = next(p for p in built.thermo if p["species_entry"]["smiles"] == canon("C"))
    nasa = ch4["nasa"]
    assert nasa["t_low"] == pytest.approx(300.0)
    assert nasa["t_mid"] == pytest.approx(1000.0)
    assert nasa["t_high"] == pytest.approx(5000.0)
    # TCKDB convention: a1..a7 = LOW-temperature coefficients (seeded 16.xx),
    # b1..b7 = HIGH-temperature (seeded 15.xx). CHEMKIN lists high-T first,
    # so coeffs_low -> a*, coeffs_high -> b*.
    assert nasa["a1"] == pytest.approx(16.0)
    assert nasa["a7"] == pytest.approx(16.06)
    assert nasa["b1"] == pytest.approx(15.0)
    assert nasa["b7"] == pytest.approx(15.06)


def test_transport_payload(built):
    h2o = next(p for p in built.transport if p["species_entry"]["smiles"] == canon("O"))
    assert h2o["sigma_angstrom"] == pytest.approx(2.605)
    assert h2o["epsilon_over_k_k"] == pytest.approx(572.4)
    assert h2o["dipole_debye"] == pytest.approx(1.844)


def test_counts(built):
    counts = built.counts()
    assert counts["thermo"] == 9
    assert counts["transport"] == 9
    # 7 logical reactions: R1..R7, with the R6 DUPLICATE pair collapsed into a
    # single multi_arrhenius record.
    assert counts["kinetics"] == 7


def test_warnings_include_lt_skip(built):
    assert any("Unsupported aux" in w for w in built.warnings)


# --- units fixture --------------------------------------------------------


def test_units_kcal_molecules_payload():
    mech = parse_mechanism(read("units_kcal.inp"))
    resolver = IdentityResolver(csv_map=parse_species_map_csv(read("species_map.csv")))
    # H and H2 need resolving too; add via bath/dict-free CSV.
    from tckdb_chemkin.identity import species_from_smiles

    resolver.csv_map["H"] = species_from_smiles("H", "[H]", source="csv")
    resolver.csv_map["H2"] = species_from_smiles("H2", "[H][H]", source="csv")
    config = ImportConfig(scientific_origin="experimental")
    built = build_all_payloads(mech, resolver, config)
    p = built.kinetics[0]
    assert p["a_units"] == "cm3_molecule_s"
    assert p["reported_ea"] == pytest.approx(12.0)
    assert p["reported_ea_units"] == "kcal_mol"
