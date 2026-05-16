"""``ComputedReactionUpload`` × ``species_thermo`` integration tests.

Covers attachment, deterministic payload emission, and the
cross-bucket validation rule that thermo source_calculations must
resolve into the same species's calculation bucket.
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
    Thermo,
    TransitionState,
)


def _gaussian() -> SoftwareRelease:
    return SoftwareRelease(software="Gaussian", version="16")


def _b3lyp() -> LevelOfTheory:
    return LevelOfTheory(method="wb97xd", basis="def2tzvp")


@pytest.fixture
def ts_geom():
    return Geometry.from_xyz("3\nts\nC 0 0 0\nH 0 0 0.8\nH 0 0 -1.0")


@pytest.fixture
def ch4():
    return Species(smiles="C", charge=0, multiplicity=1, label="CH4")


@pytest.fixture
def ch3():
    return Species(smiles="[CH3]", charge=0, multiplicity=2, label="CH3")


@pytest.fixture
def basic_upload(ts_geom, ch4, ch3):
    sr = _gaussian()
    lot = _b3lyp()
    ch4_geom = Geometry.from_xyz(
        "5\nch4\nC 0 0 0\nH 0 0 1\nH 0 0 -1\nH 0 1 0\nH 0 -1 0"
    )
    ch4_opt = Calculation.opt(
        sr, lot, output_geometry=ch4_geom, converged=True,
        final_energy_hartree=-40.5, label="ch4 opt",
    )
    ch4_sp = Calculation.sp(
        sr, lot, electronic_energy_hartree=-40.51,
        depends_on=ch4_opt, label="ch4 sp",
    )
    ts_opt = Calculation.opt(sr, lot, output_geometry=ts_geom, converged=True)
    ts_freq = Calculation.freq(
        sr, lot, n_imag=1, imag_freq_cm1=-1200.0, depends_on=ts_opt,
    )
    kin = Kinetics.modified_arrhenius(
        A=1e10, A_units="per_s", n=0, Ea=0,
        source_calculations={"ts_energy": ts_opt},
    )
    thermo = Thermo.nasa(
        coeffs_low=[0.5] + [0.0] * 6,
        coeffs_high=[0.5] + [0.0] * 6,
        t_low=200, t_mid=1000, t_high=5000,
        h298_kj_mol=-74.6, s298_j_mol_k=186.3,
    )
    rxn = ChemReaction(
        reactants=[ch3], products=[ch4],
        family="H_Abstraction",
        transition_state=TransitionState(charge=0, multiplicity=2, geometry=ts_geom),
        kinetics=[kin],
    )
    return ComputedReactionUpload(
        reaction=rxn,
        calculations=[ts_opt, ts_freq],
        species_calculations={ch4: [ch4_opt, ch4_sp]},
        species_thermo={ch4: thermo},
    )


# --- attachment + emission -------------------------------------------


def test_species_thermo_emits_thermo_block(basic_upload):
    payload = basic_upload.to_payload()
    by_key = {sp["key"]: sp for sp in payload["species"]}
    assert "thermo" in by_key["ch4"]
    assert by_key["ch4"]["thermo"]["h298_kj_mol"] == -74.6
    # The other species got no thermo.
    assert "thermo" not in by_key["ch3"]


def test_species_thermo_nasa_emits_expected_keys(basic_upload):
    payload = basic_upload.to_payload()
    ch4_thermo = next(
        sp for sp in payload["species"] if sp["key"] == "ch4"
    )["thermo"]
    # NASA block was emitted under the wire field name "nasa".
    assert "nasa" in ch4_thermo
    assert ch4_thermo["nasa"]["t_low"] == 200.0
    assert ch4_thermo["nasa"]["a1"] == 0.5
    assert ch4_thermo["nasa"]["b7"] == 0.0
    # tmin/tmax were inherited from the NASA range.
    assert ch4_thermo["tmin_k"] == 200.0
    assert ch4_thermo["tmax_k"] == 5000.0


def test_to_payload_is_deterministic(basic_upload):
    p1 = basic_upload.to_payload()
    p2 = basic_upload.to_payload()
    assert p1 == p2
    assert json.dumps(p1, sort_keys=True) == json.dumps(p2, sort_keys=True)


def test_thermo_attaches_to_species_without_calculations(ts_geom, ch4, ch3):
    sr = _gaussian()
    lot = _b3lyp()
    ts_opt = Calculation.opt(sr, lot, output_geometry=ts_geom, converged=True)
    thermo = Thermo.scalar(h298_kj_mol=-74.6)
    rxn = ChemReaction(
        reactants=[ch3], products=[ch4],
        transition_state=TransitionState(charge=0, multiplicity=2, geometry=ts_geom),
    )
    payload = ComputedReactionUpload(
        reaction=rxn,
        calculations=[ts_opt],
        species_thermo={ch4: thermo},
    ).to_payload()
    by_key = {sp["key"]: sp for sp in payload["species"]}
    # ch4 has thermo but no conformer / calculations.
    assert by_key["ch4"]["conformers"] == []
    assert by_key["ch4"]["calculations"] == []
    assert by_key["ch4"]["thermo"]["h298_kj_mol"] == -74.6


# --- validation ------------------------------------------------------


def test_species_not_in_reaction_rejected(ts_geom, ch4, ch3):
    sr = _gaussian()
    lot = _b3lyp()
    extra = Species(smiles="O", charge=0, multiplicity=1, label="H2O")
    ts_opt = Calculation.opt(sr, lot, output_geometry=ts_geom, converged=True)
    thermo = Thermo.scalar(h298_kj_mol=0.0)
    rxn = ChemReaction(
        reactants=[ch3], products=[ch4],
        transition_state=TransitionState(charge=0, multiplicity=2, geometry=ts_geom),
    )
    with pytest.raises(TCKDBBuilderValidationError):
        ComputedReactionUpload(
            reaction=rxn,
            calculations=[ts_opt],
            species_thermo={extra: thermo},
        )


def test_non_thermo_value_rejected(ts_geom, ch4, ch3):
    sr = _gaussian()
    lot = _b3lyp()
    ts_opt = Calculation.opt(sr, lot, output_geometry=ts_geom, converged=True)
    rxn = ChemReaction(
        reactants=[ch3], products=[ch4],
        transition_state=TransitionState(charge=0, multiplicity=2, geometry=ts_geom),
    )
    with pytest.raises(TCKDBBuilderValidationError):
        ComputedReactionUpload(
            reaction=rxn,
            calculations=[ts_opt],
            species_thermo={ch4: "not a thermo"},  # type: ignore[dict-item]
        )


def test_thermo_source_outside_same_species_bucket_rejected(
    ts_geom, ch4, ch3
):
    """A Thermo with source_calculations pointing at a DIFFERENT
    species's calc is rejected up front, even though the field is
    not emitted on the wire for computed-reaction.
    """
    sr = _gaussian()
    lot = _b3lyp()
    g = Geometry.from_xyz("1\nx\nH 0 0 0")
    ch3_opt = Calculation.opt(sr, lot, output_geometry=g, converged=True)
    ch4_opt = Calculation.opt(sr, lot, output_geometry=g, converged=True)
    ts_opt = Calculation.opt(sr, lot, output_geometry=ts_geom, converged=True)
    # Thermo attached to ch4 but sourced from ch3's opt — wrong bucket.
    bad_thermo = Thermo.scalar(
        h298_kj_mol=0.0,
        source_calculations={"opt": ch3_opt},
    )
    rxn = ChemReaction(
        reactants=[ch3], products=[ch4],
        transition_state=TransitionState(charge=0, multiplicity=2, geometry=ts_geom),
    )
    with pytest.raises(TCKDBBuilderValidationError):
        ComputedReactionUpload(
            reaction=rxn,
            calculations=[ts_opt],
            species_calculations={ch4: [ch4_opt], ch3: [ch3_opt]},
            species_thermo={ch4: bad_thermo},
        )


def test_thermo_source_inside_same_species_accepted(ts_geom, ch4, ch3):
    sr = _gaussian()
    lot = _b3lyp()
    g = Geometry.from_xyz("1\nx\nH 0 0 0")
    ch4_opt = Calculation.opt(sr, lot, output_geometry=g, converged=True)
    ch4_sp = Calculation.sp(
        sr, lot, electronic_energy_hartree=-1.0, depends_on=ch4_opt,
    )
    ts_opt = Calculation.opt(sr, lot, output_geometry=ts_geom, converged=True)
    good_thermo = Thermo.scalar(
        h298_kj_mol=0.0,
        source_calculations={"opt": ch4_opt, "sp": ch4_sp},
    )
    rxn = ChemReaction(
        reactants=[ch3], products=[ch4],
        transition_state=TransitionState(charge=0, multiplicity=2, geometry=ts_geom),
    )
    upload = ComputedReactionUpload(
        reaction=rxn,
        calculations=[ts_opt],
        species_calculations={ch4: [ch4_opt, ch4_sp]},
        species_thermo={ch4: good_thermo},
    )
    payload = upload.to_payload()
    # Wire payload still doesn't carry source_calculations — that's
    # the deliberate-omission contract for computed-reaction.
    ch4_thermo = next(
        sp for sp in payload["species"] if sp["key"] == "ch4"
    )["thermo"]
    assert "source_calculations" not in ch4_thermo


def test_thermo_source_outside_upload_rejected(ts_geom, ch4, ch3):
    sr = _gaussian()
    lot = _b3lyp()
    g = Geometry.from_xyz("1\nx\nH 0 0 0")
    floating = Calculation.opt(sr, lot, output_geometry=g, converged=True)
    ts_opt = Calculation.opt(sr, lot, output_geometry=ts_geom, converged=True)
    thermo = Thermo.scalar(
        h298_kj_mol=0.0,
        source_calculations={"opt": floating},
    )
    rxn = ChemReaction(
        reactants=[ch3], products=[ch4],
        transition_state=TransitionState(charge=0, multiplicity=2, geometry=ts_geom),
    )
    # ch4 has no species_calculations at all → floating doesn't
    # belong to ch4's bucket → reject.
    with pytest.raises(TCKDBBuilderValidationError):
        ComputedReactionUpload(
            reaction=rxn,
            calculations=[ts_opt],
            species_thermo={ch4: thermo},
        )


def test_snapshot_structural(basic_upload):
    """Structural snapshot — locks per-species block shape and the
    fields we expect on the thermo block. Avoids a byte-exact
    snapshot so an extra key minted upstream doesn't break the test."""
    payload = basic_upload.to_payload()
    by_key = {sp["key"]: sp for sp in payload["species"]}

    # ch4 carries opt + sp + thermo.
    assert len(by_key["ch4"]["conformers"]) == 1
    assert by_key["ch4"]["conformers"][0]["calculation"]["type"] == "opt"
    assert [c["type"] for c in by_key["ch4"]["calculations"]] == ["sp"]
    assert set(by_key["ch4"]["thermo"]) >= {
        "h298_kj_mol", "s298_j_mol_k", "tmin_k", "tmax_k", "nasa",
    }
    # ch3 is identity-only — neither calcs nor thermo.
    assert by_key["ch3"]["conformers"] == []
    assert by_key["ch3"]["calculations"] == []
    assert "thermo" not in by_key["ch3"]

    # The TS bucket survived unchanged.
    assert payload["transition_state"]["calculation"]["type"] == "opt"
    assert [c["type"] for c in payload["transition_state"]["calculations"]] == [
        "freq"
    ]
