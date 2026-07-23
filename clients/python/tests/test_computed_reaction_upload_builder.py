"""End-to-end tests for ``ComputedReactionUpload``.

Locks the on-wire payload shape against a snapshot, asserts
determinism, and verifies the cross-reference rules that the
backend's ``ComputedReactionUploadRequest`` enforces.
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


# ----- fixtures -------------------------------------------------------


@pytest.fixture
def ts_geom():
    return Geometry.from_xyz(
        "3\nts\nC 0.0 0.0 0.0\nH 0.0 0.0 0.8\nH 0.0 0.0 -1.0"
    )


@pytest.fixture
def gaussian():
    return SoftwareRelease(software="Gaussian", version="16")


@pytest.fixture
def lot():
    return LevelOfTheory(method="wb97xd", basis="def2tzvp")


@pytest.fixture
def species():
    return {
        "ch3": Species(smiles="[CH3]", charge=0, multiplicity=2, label="CH3"),
        "h": Species(smiles="[H]", charge=0, multiplicity=2, label="H"),
        "ch4": Species(smiles="C", charge=0, multiplicity=1, label="CH4"),
    }


@pytest.fixture
def minimal_upload(gaussian, lot, ts_geom, species):
    ts_opt = Calculation.opt(
        gaussian, lot, output_geometry=ts_geom,
        final_energy_hartree=-270.55, converged=True,
        label="ts opt",
    )
    ts_freq = Calculation.freq(
        gaussian, lot,
        n_imag=1, imag_freq_cm1=-1200.0, zpe_hartree=0.201,
        depends_on=ts_opt, label="ts freq",
    )
    kin = Kinetics.modified_arrhenius(
        A=1.2e13, A_units="cm3/mol/s", n=0.5, Ea=10.0, Ea_units="kJ/mol",
        Tmin=300, Tmax=2500,
        source_calculations={"ts_energy": ts_opt, "freq": ts_freq},
    )
    rxn = ChemReaction(
        reactants=[species["ch3"], species["h"]],
        products=[species["ch4"]],
        family="H_Abstraction",
        transition_state=TransitionState(
            charge=0, multiplicity=2, geometry=ts_geom, label="ts",
        ),
        kinetics=[kin],
    )
    return ComputedReactionUpload(
        reaction=rxn, calculations=[ts_opt, ts_freq],
        primary_ts_calculation=ts_opt,
    )


# ----- determinism + snapshot ----------------------------------------


def test_to_payload_is_deterministic(minimal_upload):
    p1 = minimal_upload.to_payload()
    p2 = minimal_upload.to_payload()
    assert p1 == p2
    assert json.dumps(p1, sort_keys=True) == json.dumps(p2, sort_keys=True)


def test_to_payload_snapshot(minimal_upload):
    payload = minimal_upload.to_payload()
    expected = {
        "species": [
            {
                "key": "ch3",
                "species_entry": {"smiles": "[CH3]", "charge": 0, "multiplicity": 2},
                "conformers": [],
                "calculations": [],
            },
            {
                "key": "h",
                "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
                "conformers": [],
                "calculations": [],
            },
            {
                "key": "ch4",
                "species_entry": {"smiles": "C", "charge": 0, "multiplicity": 1},
                "conformers": [],
                "calculations": [],
            },
        ],
        "reversible": True,
        "reactant_keys": ["ch3", "h"],
        "product_keys": ["ch4"],
        "reaction_family": "H_Abstraction",
        "transition_state": {
            "charge": 0,
            "multiplicity": 2,
            "geometry": {
                "key": "geom_1",
                "xyz_text": "3\nts\nC 0.0 0.0 0.0\nH 0.0 0.0 0.8\nH 0.0 0.0 -1.0",
            },
            "calculation": {
                "key": "ts_opt",
                "type": "opt",
                "software_release": {"name": "Gaussian", "version": "16"},
                "level_of_theory": {"method": "wb97xd", "basis": "def2tzvp"},
                "opt_converged": True,
                "opt_final_energy_hartree": -270.55,
            },
            "calculations": [
                {
                    "key": "ts_freq",
                    "type": "freq",
                    "software_release": {"name": "Gaussian", "version": "16"},
                    "level_of_theory": {"method": "wb97xd", "basis": "def2tzvp"},
                    "geometry_key": "geom_1",
                    "freq_n_imag": 1,
                    "freq_imag_freq_cm1": -1200.0,
                    "freq_zpe_hartree": 0.201,
                    "depends_on": [
                        {"parent_calculation_key": "ts_opt", "role": "freq_on"}
                    ],
                },
            ],
            "label": "ts",
        },
        "kinetics": [
            {
                "reactant_keys": ["ch3", "h"],
                "product_keys": ["ch4"],
                "model_kind": "modified_arrhenius",
                "a": 1.2e13,
                "a_units": "cm3_mol_s",
                "degeneracy_convention": "unknown",
                "n": 0.5,
                "reported_ea": 10.0,
                "reported_ea_units": "kj_mol",
                "tmin_k": 300.0,
                "tmax_k": 2500.0,
                "source_calculations": [
                    {"calculation_key": "ts_opt", "role": "ts_energy"},
                    {"calculation_key": "ts_freq", "role": "freq"},
                ],
            },
        ],
    }
    assert payload == expected


# ----- local validation ----------------------------------------------


def test_primary_ts_calculation_inferred_when_omitted(gaussian, lot, ts_geom, species):
    ts_opt = Calculation.opt(gaussian, lot, output_geometry=ts_geom, converged=True)
    ts_freq = Calculation.freq(
        gaussian, lot, n_imag=1, imag_freq_cm1=-1200.0, depends_on=ts_opt,
    )
    rxn = ChemReaction(
        reactants=[species["ch3"], species["h"]],
        products=[species["ch4"]],
        family="H_Abstraction",
        transition_state=TransitionState(charge=0, multiplicity=2, geometry=ts_geom),
    )
    upload = ComputedReactionUpload(reaction=rxn, calculations=[ts_opt, ts_freq])
    assert upload.primary_ts_calculation is ts_opt


def test_primary_ts_calculation_must_be_in_calculations(
    gaussian, lot, ts_geom, species
):
    opt_in = Calculation.opt(gaussian, lot, output_geometry=ts_geom, converged=True)
    opt_outside = Calculation.opt(gaussian, lot, output_geometry=ts_geom, converged=True)
    rxn = ChemReaction(
        reactants=[species["ch3"]], products=[species["ch4"]],
        transition_state=TransitionState(charge=0, multiplicity=2, geometry=ts_geom),
    )
    with pytest.raises(TCKDBBuilderValidationError):
        ComputedReactionUpload(
            reaction=rxn, calculations=[opt_in],
            primary_ts_calculation=opt_outside,
        )


def test_primary_ts_calculation_must_be_opt(gaussian, lot, ts_geom, species):
    sp = Calculation.sp(
        gaussian, lot, input_geometry=ts_geom, electronic_energy_hartree=-1.0,
    )
    rxn = ChemReaction(
        reactants=[species["ch3"]], products=[species["ch4"]],
        transition_state=TransitionState(charge=0, multiplicity=2, geometry=ts_geom),
    )
    with pytest.raises(TCKDBBuilderValidationError):
        ComputedReactionUpload(
            reaction=rxn, calculations=[sp], primary_ts_calculation=sp,
        )


def test_primary_ts_calculation_rejected_without_transition_state(
    gaussian, lot, ts_geom, species
):
    opt = Calculation.opt(gaussian, lot, output_geometry=ts_geom, converged=True)
    rxn = ChemReaction(
        reactants=[species["ch3"]], products=[species["ch4"]],
    )
    with pytest.raises(TCKDBBuilderValidationError):
        ComputedReactionUpload(
            reaction=rxn, calculations=[opt], primary_ts_calculation=opt,
        )


def test_dependency_outside_upload_rejected(gaussian, lot, ts_geom, species):
    ts_opt = Calculation.opt(gaussian, lot, output_geometry=ts_geom, converged=True)
    outside_opt = Calculation.opt(gaussian, lot, output_geometry=ts_geom, converged=True)
    ts_freq = Calculation.freq(
        gaussian, lot, n_imag=1, imag_freq_cm1=-1.0, depends_on=outside_opt,
    )
    rxn = ChemReaction(
        reactants=[species["ch3"]], products=[species["ch4"]],
        transition_state=TransitionState(charge=0, multiplicity=2, geometry=ts_geom),
    )
    with pytest.raises(TCKDBBuilderValidationError):
        ComputedReactionUpload(reaction=rxn, calculations=[ts_opt, ts_freq])


def test_kinetics_source_calculation_outside_upload_rejected(
    gaussian, lot, ts_geom, species
):
    ts_opt = Calculation.opt(gaussian, lot, output_geometry=ts_geom, converged=True)
    outside_opt = Calculation.opt(gaussian, lot, output_geometry=ts_geom, converged=True)
    kin = Kinetics.modified_arrhenius(
        A=1.0, A_units="per_s", n=0, Ea=0,
        source_calculations={"ts_energy": outside_opt},
    )
    rxn = ChemReaction(
        reactants=[species["ch3"]], products=[species["ch4"]],
        transition_state=TransitionState(charge=0, multiplicity=2, geometry=ts_geom),
        kinetics=[kin],
    )
    with pytest.raises(TCKDBBuilderValidationError):
        ComputedReactionUpload(reaction=rxn, calculations=[ts_opt])


def test_label_collisions_get_suffix(gaussian, lot, ts_geom, species):
    opt1 = Calculation.opt(
        gaussian, lot, output_geometry=ts_geom, converged=True, label="foo",
    )
    opt2 = Calculation.opt(
        gaussian, lot, output_geometry=ts_geom, converged=True, label="foo",
    )
    rxn = ChemReaction(
        reactants=[species["ch3"]], products=[species["ch4"]],
        transition_state=TransitionState(charge=0, multiplicity=2, geometry=ts_geom),
    )
    payload = ComputedReactionUpload(
        reaction=rxn, calculations=[opt1, opt2], primary_ts_calculation=opt1,
    ).to_payload()
    assert payload["transition_state"]["calculation"]["key"] == "foo"
    assert payload["transition_state"]["calculations"][0]["key"] == "foo_2"


def test_transition_state_geometry_falls_back_to_primary_opt(
    gaussian, lot, ts_geom, species
):
    opt = Calculation.opt(gaussian, lot, output_geometry=ts_geom, converged=True)
    rxn = ChemReaction(
        reactants=[species["ch3"]], products=[species["ch4"]],
        transition_state=TransitionState(charge=0, multiplicity=2),
    )
    payload = ComputedReactionUpload(
        reaction=rxn, calculations=[opt],
    ).to_payload()
    assert payload["transition_state"]["geometry"]["xyz_text"].startswith("3\n")


def test_reaction_without_transition_state_emits_no_block(
    gaussian, lot, ts_geom, species
):
    rxn = ChemReaction(
        reactants=[species["ch3"]], products=[species["ch4"]],
    )
    payload = ComputedReactionUpload(reaction=rxn, calculations=[]).to_payload()
    assert "transition_state" not in payload
    assert payload["kinetics" if "kinetics" in payload else "reactant_keys"]


def test_same_species_on_both_sides_dedups_in_species_block(
    gaussian, lot, ts_geom
):
    a = Species(smiles="C", charge=0, multiplicity=1, label="A")
    b = Species(smiles="[CH3]", charge=0, multiplicity=2, label="B")
    rxn = ChemReaction(reactants=[a, b], products=[b, a])
    payload = ComputedReactionUpload(reaction=rxn, calculations=[]).to_payload()
    keys = [sp["key"] for sp in payload["species"]]
    assert keys == ["a", "b"]
    assert payload["reactant_keys"] == ["a", "b"]
    assert payload["product_keys"] == ["b", "a"]
