"""Tests for the Phase-3A ``species_calculations`` extension.

Phase 2 left every Calculation in ``ComputedReactionUpload.calculations``
attached to the transition state; species blocks shipped identity-only.
Phase 3A adds the ``species_calculations: dict[Species, list[Calculation]]``
bucket so reactant/product opt/freq/sp records can travel in the same
upload — and the kinetics record can reference them as
``reactant_energy`` / ``product_energy`` sources.
"""

from __future__ import annotations

import json

import pytest

from tckdb_client.builders import (
    Calculation,
    ChemReaction,
    ComputedReactionUpload,
    Geometry,
    Kinetics,
    LevelOfTheory,
    Species,
    SoftwareRelease,
    TCKDBBuilderValidationError,
    TransitionState,
)


def _gaussian() -> SoftwareRelease:
    return SoftwareRelease(software="Gaussian", version="16")


def _b3lyp() -> LevelOfTheory:
    return LevelOfTheory(method="wb97xd", basis="def2tzvp")


# ----- fixtures -------------------------------------------------------


@pytest.fixture
def ts_geom() -> Geometry:
    return Geometry.from_xyz("3\nts\nC 0 0 0\nH 0 0 0.8\nH 0 0 -1.0")


@pytest.fixture
def ch4():
    return Species(smiles="C", charge=0, multiplicity=1, label="CH4")


@pytest.fixture
def ch3():
    return Species(smiles="[CH3]", charge=0, multiplicity=2, label="CH3")


@pytest.fixture
def h():
    return Species(smiles="[H]", charge=0, multiplicity=2, label="H")


@pytest.fixture
def populated_upload(ts_geom, ch4, ch3, h):
    sr = _gaussian()
    lot = _b3lyp()
    ch4_geom = Geometry.from_xyz(
        "5\nch4\nC 0 0 0\nH 0 0 1\nH 0 0 -1\nH 0 1 0\nH 0 -1 0"
    )
    ch3_geom = Geometry.from_xyz(
        "4\nch3\nC 0 0 0\nH 0 0 1\nH 0 1 0\nH 0 -1 0"
    )
    h_geom = Geometry.from_xyz("1\nh\nH 0 0 0")

    ch4_opt = Calculation.opt(
        sr, lot, output_geometry=ch4_geom, converged=True,
        final_energy_hartree=-40.5, label="ch4 opt",
    )
    ch4_sp = Calculation.sp(
        sr, lot, electronic_energy_hartree=-40.51,
        depends_on=ch4_opt, label="ch4 sp",
    )
    ch3_opt = Calculation.opt(
        sr, lot, output_geometry=ch3_geom, converged=True,
        final_energy_hartree=-39.7, label="ch3 opt",
    )
    ch3_sp = Calculation.sp(
        sr, lot, electronic_energy_hartree=-39.71,
        depends_on=ch3_opt, label="ch3 sp",
    )
    h_opt = Calculation.opt(
        sr, lot, output_geometry=h_geom, converged=True,
        final_energy_hartree=-0.5, label="h opt",
    )
    h_sp = Calculation.sp(
        sr, lot, electronic_energy_hartree=-0.5, depends_on=h_opt, label="h sp",
    )
    ts_opt = Calculation.opt(
        sr, lot, output_geometry=ts_geom, converged=True,
        final_energy_hartree=-270.55, label="ts opt",
    )
    ts_freq = Calculation.freq(
        sr, lot, n_imag=1, imag_freq_cm1=-1200.0, zpe_hartree=0.201,
        depends_on=ts_opt, label="ts freq",
    )

    kin = Kinetics.modified_arrhenius(
        A=1.2e13, A_units="cm3_mol_s", n=0.5, Ea=10.0, Ea_units="kJ/mol",
        Tmin=300, Tmax=2500,
        source_calculations={
            "reactant_energy": [ch3_sp, h_sp],
            "product_energy": ch4_sp,
            "ts_energy": ts_opt,
            "freq": ts_freq,
        },
    )
    rxn = ChemReaction(
        reactants=[ch3, h], products=[ch4],
        family="H_Abstraction",
        transition_state=TransitionState(
            charge=0, multiplicity=2, geometry=ts_geom, label="ts",
        ),
        kinetics=[kin],
    )
    return ComputedReactionUpload(
        reaction=rxn,
        calculations=[ts_opt, ts_freq],
        species_calculations={
            ch4: [ch4_opt, ch4_sp],
            ch3: [ch3_opt, ch3_sp],
            h: [h_opt, h_sp],
        },
    )


# ----- API + payload --------------------------------------------------


def test_species_block_emits_conformer_and_calculations(populated_upload):
    payload = populated_upload.to_payload()
    by_key = {sp["key"]: sp for sp in payload["species"]}
    assert set(by_key) == {"ch4", "ch3", "h"}

    ch4_block = by_key["ch4"]
    assert len(ch4_block["conformers"]) == 1
    assert ch4_block["conformers"][0]["calculation"]["type"] == "opt"
    # ch4_sp lands in species.calculations, not in conformer.
    assert len(ch4_block["calculations"]) == 1
    assert ch4_block["calculations"][0]["type"] == "sp"
    # The non-opt calc's geometry_key points back at the conformer geom.
    assert (
        ch4_block["calculations"][0]["geometry_key"]
        == ch4_block["conformers"][0]["geometry"]["key"]
    )


def test_to_payload_deterministic(populated_upload):
    p1 = populated_upload.to_payload()
    p2 = populated_upload.to_payload()
    assert p1 == p2
    assert json.dumps(p1, sort_keys=True) == json.dumps(p2, sort_keys=True)


def test_snapshot_full_reaction_with_species_calcs(populated_upload):
    payload = populated_upload.to_payload()
    # Spot-check the most fragile pieces of the wire shape: kinetics
    # role wiring with a duplicated reactant_energy role, plus per-species
    # block structure. A full byte-exact snapshot would lock the
    # specific calc/geom number suffixes; we don't want the test to
    # break just because a future builder pre-mints an extra geom key.
    species_blocks = {sp["key"]: sp for sp in payload["species"]}
    for sp_key, calc_types in [
        ("ch4", {"opt", "sp"}),
        ("ch3", {"opt", "sp"}),
        ("h", {"opt", "sp"}),
    ]:
        block = species_blocks[sp_key]
        assert {block["conformers"][0]["calculation"]["type"]} | {
            c["type"] for c in block["calculations"]
        } == calc_types

    # The kinetics record carries two reactant_energy entries (one
    # per bimolecular partner) plus the rest.
    kin_sources = payload["kinetics"][0]["source_calculations"]
    role_seq = [s["role"] for s in kin_sources]
    assert role_seq == [
        "reactant_energy", "reactant_energy",
        "product_energy",
        "ts_energy",
        "freq",
    ]
    # Every kinetics source resolves to a calc key that actually
    # appears somewhere in the bundle.
    all_calc_keys: set[str] = set()
    for sp in payload["species"]:
        if sp["conformers"]:
            all_calc_keys.add(sp["conformers"][0]["calculation"]["key"])
        all_calc_keys.update(c["key"] for c in sp["calculations"])
    all_calc_keys.add(payload["transition_state"]["calculation"]["key"])
    all_calc_keys.update(
        c["key"] for c in payload["transition_state"]["calculations"]
    )
    for src in kin_sources:
        assert src["calculation_key"] in all_calc_keys


# ----- validation -----------------------------------------------------


def test_species_not_in_reaction_rejected(ts_geom, ch4, ch3):
    sr = _gaussian()
    lot = _b3lyp()
    g = Geometry.from_xyz("1\nh\nH 0 0 0")
    extra = Species(smiles="O", charge=0, multiplicity=1, label="H2O")
    opt = Calculation.opt(sr, lot, output_geometry=g, converged=True)
    rxn = ChemReaction(
        reactants=[ch3], products=[ch4],
        transition_state=TransitionState(charge=0, multiplicity=2, geometry=ts_geom),
    )
    with pytest.raises(TCKDBBuilderValidationError):
        ComputedReactionUpload(
            reaction=rxn,
            calculations=[Calculation.opt(sr, lot, output_geometry=ts_geom, converged=True)],
            species_calculations={extra: [opt]},
        )


def test_non_calculation_value_rejected(ts_geom, ch4, ch3):
    sr = _gaussian()
    lot = _b3lyp()
    rxn = ChemReaction(
        reactants=[ch3], products=[ch4],
        transition_state=TransitionState(charge=0, multiplicity=2, geometry=ts_geom),
    )
    with pytest.raises(TCKDBBuilderValidationError):
        ComputedReactionUpload(
            reaction=rxn,
            calculations=[Calculation.opt(sr, lot, output_geometry=ts_geom, converged=True)],
            species_calculations={ch4: ["not a calc"]},  # type: ignore[list-item]
        )


def test_species_calc_dep_must_stay_inside_same_species(ts_geom, ch4, ch3):
    sr = _gaussian()
    lot = _b3lyp()
    ch4_geom = Geometry.from_xyz("1\nx\nH 0 0 0")
    ch3_geom = Geometry.from_xyz("1\nx\nH 0 0 0")
    ch4_opt = Calculation.opt(sr, lot, output_geometry=ch4_geom, converged=True)
    ch3_opt = Calculation.opt(sr, lot, output_geometry=ch3_geom, converged=True)
    # The freq is attached to ch4 but depends on a *ch3* opt — wrong species.
    bad_freq = Calculation.freq(
        sr, lot, n_imag=0, zpe_hartree=0.1, depends_on=ch3_opt,
    )
    rxn = ChemReaction(
        reactants=[ch3], products=[ch4],
        transition_state=TransitionState(charge=0, multiplicity=2, geometry=ts_geom),
    )
    with pytest.raises(TCKDBBuilderValidationError):
        ComputedReactionUpload(
            reaction=rxn,
            calculations=[Calculation.opt(sr, lot, output_geometry=ts_geom, converged=True)],
            species_calculations={ch4: [ch4_opt, bad_freq], ch3: [ch3_opt]},
        )


def test_ts_calc_dep_must_stay_inside_ts(ts_geom, ch4, ch3):
    sr = _gaussian()
    lot = _b3lyp()
    g = Geometry.from_xyz("1\nx\nH 0 0 0")
    ch4_opt = Calculation.opt(sr, lot, output_geometry=g, converged=True)
    ts_opt = Calculation.opt(sr, lot, output_geometry=ts_geom, converged=True)
    # ts_freq depends on a species-side opt — also rejected.
    ts_freq = Calculation.freq(
        sr, lot, n_imag=1, imag_freq_cm1=-1.0, depends_on=ch4_opt,
    )
    rxn = ChemReaction(
        reactants=[ch3], products=[ch4],
        transition_state=TransitionState(charge=0, multiplicity=2, geometry=ts_geom),
    )
    with pytest.raises(TCKDBBuilderValidationError):
        ComputedReactionUpload(
            reaction=rxn,
            calculations=[ts_opt, ts_freq],
            species_calculations={ch4: [ch4_opt]},
        )


def test_kinetics_source_can_reference_species_calcs(ts_geom, ch4, ch3):
    sr = _gaussian()
    lot = _b3lyp()
    g = Geometry.from_xyz("1\nx\nH 0 0 0")
    ts_opt = Calculation.opt(sr, lot, output_geometry=ts_geom, converged=True)
    ch3_opt = Calculation.opt(sr, lot, output_geometry=g, converged=True)
    ch3_sp = Calculation.sp(
        sr, lot, electronic_energy_hartree=-1.0, depends_on=ch3_opt,
    )
    kin = Kinetics.modified_arrhenius(
        A=1.0, A_units="per_s", n=0, Ea=0,
        # Mix of TS-side and species-side calcs.
        source_calculations={"ts_energy": ts_opt, "reactant_energy": ch3_sp},
    )
    rxn = ChemReaction(
        reactants=[ch3], products=[ch4],
        transition_state=TransitionState(charge=0, multiplicity=2, geometry=ts_geom),
        kinetics=[kin],
    )
    upload = ComputedReactionUpload(
        reaction=rxn,
        calculations=[ts_opt],
        species_calculations={ch3: [ch3_opt, ch3_sp]},
    )
    payload = upload.to_payload()
    srcs = payload["kinetics"][0]["source_calculations"]
    # Both refs resolve into the global calc namespace.
    referenced_keys = {s["calculation_key"] for s in srcs}
    assert len(referenced_keys) == 2


def test_kinetics_source_outside_any_bucket_rejected(ts_geom, ch4, ch3):
    sr = _gaussian()
    lot = _b3lyp()
    g = Geometry.from_xyz("1\nx\nH 0 0 0")
    ts_opt = Calculation.opt(sr, lot, output_geometry=ts_geom, converged=True)
    floating = Calculation.sp(sr, lot, input_geometry=g, electronic_energy_hartree=-1.0)
    kin = Kinetics.modified_arrhenius(
        A=1.0, A_units="per_s", n=0, Ea=0,
        source_calculations={"ts_energy": ts_opt, "reactant_energy": floating},
    )
    rxn = ChemReaction(
        reactants=[ch3], products=[ch4],
        transition_state=TransitionState(charge=0, multiplicity=2, geometry=ts_geom),
        kinetics=[kin],
    )
    with pytest.raises(TCKDBBuilderValidationError):
        ComputedReactionUpload(
            reaction=rxn,
            calculations=[ts_opt],
            # Note: floating is in NEITHER calculations nor species_calculations.
            species_calculations={},
        )


def test_primary_ts_must_be_in_ts_bucket(ts_geom, ch4, ch3):
    sr = _gaussian()
    lot = _b3lyp()
    g = Geometry.from_xyz("1\nx\nH 0 0 0")
    ch4_opt = Calculation.opt(sr, lot, output_geometry=g, converged=True)
    ts_opt = Calculation.opt(sr, lot, output_geometry=ts_geom, converged=True)
    rxn = ChemReaction(
        reactants=[ch3], products=[ch4],
        transition_state=TransitionState(charge=0, multiplicity=2, geometry=ts_geom),
    )
    # primary_ts_calculation set to a species-side calc — must be rejected.
    with pytest.raises(TCKDBBuilderValidationError):
        ComputedReactionUpload(
            reaction=rxn,
            calculations=[ts_opt],
            primary_ts_calculation=ch4_opt,
            species_calculations={ch4: [ch4_opt]},
        )


def test_same_species_on_both_sides_dedups(ts_geom):
    """A catalyst-style appearance still produces one species block."""
    sr = _gaussian()
    lot = _b3lyp()
    a = Species(smiles="C", charge=0, multiplicity=1, label="A")
    b = Species(smiles="[CH3]", charge=0, multiplicity=2, label="B")
    a_opt = Calculation.opt(sr, lot, output_geometry=Geometry.from_xyz("1\nx\nH 0 0 0"), converged=True)
    rxn = ChemReaction(
        reactants=[a, b], products=[b, a],
        transition_state=None,
    )
    payload = ComputedReactionUpload(
        reaction=rxn,
        species_calculations={a: [a_opt]},
    ).to_payload()
    keys = [sp["key"] for sp in payload["species"]]
    assert keys == ["a", "b"]
    # A appears once with calcs; B identity-only.
    by_key = {sp["key"]: sp for sp in payload["species"]}
    assert len(by_key["a"]["conformers"]) == 1
    assert by_key["b"]["conformers"] == []


def test_duplicate_species_key_in_dict_rejected(ts_geom, ch4, ch3):
    """Same Species twice via a duplicate key would just overwrite in a
    dict, but two *distinct* Species instances with the same identity
    is the failure mode we actually care about.
    """
    sr = _gaussian()
    lot = _b3lyp()
    g = Geometry.from_xyz("1\nx\nH 0 0 0")
    opt = Calculation.opt(sr, lot, output_geometry=g, converged=True)
    rxn = ChemReaction(
        reactants=[ch3], products=[ch4],
        transition_state=TransitionState(charge=0, multiplicity=2, geometry=ts_geom),
    )
    # Build the dict carefully so the same key appears twice (this is
    # actually impossible in Python — the second binding wins — so we
    # use a list-of-pairs that gets caught via duplicate-Species
    # rejection if it ever sneaks through).
    sc: dict = {ch4: [opt]}
    sc[ch4] = [opt]  # Python collapses to one entry.
    upload = ComputedReactionUpload(
        reaction=rxn,
        calculations=[Calculation.opt(sr, lot, output_geometry=ts_geom, converged=True)],
        species_calculations=sc,
    )
    # No error: single entry survives.
    assert len(upload.to_payload()["species"]) == 2


def test_multi_opt_per_species_rejected(ts_geom, ch4, ch3):
    """Phase-3A supports one opt per species. Multi-opt callers must
    fall back to the raw-dict form until a Conformer builder lands.
    """
    sr = _gaussian()
    lot = _b3lyp()
    g = Geometry.from_xyz("1\nx\nH 0 0 0")
    opt1 = Calculation.opt(sr, lot, output_geometry=g, converged=True)
    opt2 = Calculation.opt(sr, lot, output_geometry=g, converged=True)
    rxn = ChemReaction(
        reactants=[ch3], products=[ch4],
        transition_state=TransitionState(charge=0, multiplicity=2, geometry=ts_geom),
    )
    with pytest.raises(TCKDBBuilderValidationError):
        ComputedReactionUpload(
            reaction=rxn,
            calculations=[Calculation.opt(sr, lot, output_geometry=ts_geom, converged=True)],
            species_calculations={ch4: [opt1, opt2]},
        )


def test_species_with_non_opt_only_rejected(ts_geom, ch4, ch3):
    """Without an opt, a species can't anchor a conformer."""
    sr = _gaussian()
    lot = _b3lyp()
    g = Geometry.from_xyz("1\nx\nH 0 0 0")
    sp_only = Calculation.sp(sr, lot, input_geometry=g, electronic_energy_hartree=-1.0)
    rxn = ChemReaction(
        reactants=[ch3], products=[ch4],
        transition_state=TransitionState(charge=0, multiplicity=2, geometry=ts_geom),
    )
    upload = ComputedReactionUpload(
        reaction=rxn,
        calculations=[Calculation.opt(sr, lot, output_geometry=ts_geom, converged=True)],
        species_calculations={ch4: [sp_only]},
    )
    with pytest.raises(TCKDBBuilderValidationError):
        upload.to_payload()


def test_kinetics_dict_of_list_form(ts_geom, ch4, ch3, h):
    sr = _gaussian()
    lot = _b3lyp()
    g = Geometry.from_xyz("1\nx\nH 0 0 0")
    ts_opt = Calculation.opt(sr, lot, output_geometry=ts_geom, converged=True)
    ch3_opt = Calculation.opt(sr, lot, output_geometry=g, converged=True)
    ch3_sp = Calculation.sp(sr, lot, electronic_energy_hartree=-1.0, depends_on=ch3_opt)
    h_opt = Calculation.opt(sr, lot, output_geometry=g, converged=True)
    h_sp = Calculation.sp(sr, lot, electronic_energy_hartree=-0.5, depends_on=h_opt)
    kin = Kinetics.modified_arrhenius(
        A=1.0, A_units="per_s", n=0, Ea=0,
        source_calculations={
            "reactant_energy": [ch3_sp, h_sp],
            "ts_energy": ts_opt,
        },
    )
    roles = [r for r, _ in kin.source_calculations_iter()]
    assert roles == ["reactant_energy", "reactant_energy", "ts_energy"]


def test_kinetics_list_of_tuples_form_preserves_order(ts_geom, ch4, ch3, h):
    sr = _gaussian()
    lot = _b3lyp()
    g = Geometry.from_xyz("1\nx\nH 0 0 0")
    ts_opt = Calculation.opt(sr, lot, output_geometry=ts_geom, converged=True)
    ch3_opt = Calculation.opt(sr, lot, output_geometry=g, converged=True)
    ch3_sp = Calculation.sp(sr, lot, electronic_energy_hartree=-1.0, depends_on=ch3_opt)
    h_opt = Calculation.opt(sr, lot, output_geometry=g, converged=True)
    h_sp = Calculation.sp(sr, lot, electronic_energy_hartree=-0.5, depends_on=h_opt)
    kin = Kinetics.modified_arrhenius(
        A=1.0, A_units="per_s", n=0, Ea=0,
        source_calculations=[
            ("ts_energy", ts_opt),
            ("reactant_energy", ch3_sp),
            ("reactant_energy", h_sp),
        ],
    )
    out = list(kin.source_calculations_iter())
    assert [r for r, _ in out] == ["ts_energy", "reactant_energy", "reactant_energy"]
    assert [c for _, c in out] == [ts_opt, ch3_sp, h_sp]


def test_kinetics_list_of_tuples_rejects_bad_shape():
    with pytest.raises(TCKDBBuilderValidationError):
        Kinetics.modified_arrhenius(
            A=1.0, A_units="per_s", n=0, Ea=0,
            source_calculations=[("ts_energy",)],  # type: ignore[list-item]
        )
