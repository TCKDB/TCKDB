"""``ComputedReactionUpload`` × ``species_statmech`` integration tests.

Unlike ``species_thermo`` (whose backend schema lacks
``source_calculations``), ``species_statmech`` *does* emit
``source_calculations`` on the wire — the backend's
``BundleStatmechIn`` carries the field. These tests cover acceptance,
key resolution, same-species bucket enforcement, and determinism.
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
    Statmech,
    TCKDBBuilderValidationError,
    Thermo,
    TransitionState,
)


def _sr() -> SoftwareRelease:
    return SoftwareRelease(software="Gaussian", version="16")


def _lot() -> LevelOfTheory:
    return LevelOfTheory(method="wb97xd", basis="def2tzvp")


@pytest.fixture
def ts_geom() -> Geometry:
    return Geometry.from_xyz("3\nts\nC 0 0 0\nH 0 0 0.8\nH 0 0 -1.0")


@pytest.fixture
def ch4() -> Species:
    return Species(smiles="C", charge=0, multiplicity=1, label="CH4")


@pytest.fixture
def ch3() -> Species:
    return Species(smiles="[CH3]", charge=0, multiplicity=2, label="CH3")


@pytest.fixture
def populated_upload(ts_geom, ch4, ch3):
    sr = _sr()
    lot = _lot()
    ch4_geom = Geometry.from_xyz(
        "5\nch4\nC 0 0 0\nH 0 0 1\nH 0 0 -1\nH 0 1 0\nH 0 -1 0"
    )
    ch4_opt = Calculation.opt(
        sr, lot, output_geometry=ch4_geom, converged=True,
        final_energy_hartree=-40.5, label="ch4 opt",
    )
    ch4_freq = Calculation.freq(
        sr, lot, n_imag=0, zpe_hartree=0.044, depends_on=ch4_opt,
        label="ch4 freq",
    )
    ch4_sp = Calculation.sp(
        sr, lot, electronic_energy_hartree=-40.51, depends_on=ch4_opt,
        label="ch4 sp",
    )
    ts_opt = Calculation.opt(
        sr, lot, output_geometry=ts_geom, converged=True, label="ts opt",
    )
    ts_freq = Calculation.freq(
        sr, lot, n_imag=1, imag_freq_cm1=-1200.0, depends_on=ts_opt,
        label="ts freq",
    )
    sm_ch4 = Statmech(
        external_symmetry=12, point_group="Td", is_linear=False,
        rigid_rotor_kind="spherical_top", statmech_treatment="rrho",
        source_calculations=[
            ("opt", ch4_opt), ("freq", ch4_freq), ("sp", ch4_sp),
        ],
    )
    kin = Kinetics.modified_arrhenius(
        A=1e10, A_units="per_s", n=0, Ea=0,
        source_calculations={"ts_energy": ts_opt},
    )
    rxn = ChemReaction(
        reactants=[ch3], products=[ch4], family="H_Abstraction",
        transition_state=TransitionState(
            charge=0, multiplicity=2, geometry=ts_geom,
        ),
        kinetics=[kin],
    )
    return ComputedReactionUpload(
        reaction=rxn,
        calculations=[ts_opt, ts_freq],
        species_calculations={ch4: [ch4_opt, ch4_freq, ch4_sp]},
        species_statmech={ch4: sm_ch4},
    )


# ----- API acceptance ------------------------------------------------


def test_species_statmech_emits_block(populated_upload):
    payload = populated_upload.to_payload()
    ch4_block = next(sp for sp in payload["species"] if sp["key"] == "ch4")
    assert "statmech" in ch4_block
    assert ch4_block["statmech"]["external_symmetry"] == 12
    assert ch4_block["statmech"]["point_group"] == "Td"


def test_species_statmech_emits_source_calculations(populated_upload):
    payload = populated_upload.to_payload()
    ch4_block = next(sp for sp in payload["species"] if sp["key"] == "ch4")
    sm_sources = ch4_block["statmech"]["source_calculations"]
    assert [s["role"] for s in sm_sources] == ["opt", "freq", "sp"]
    # Keys point at the same bundle-local calc keys that appear in the
    # conformer + species.calculations sections.
    primary_key = ch4_block["conformers"][0]["calculation"]["key"]
    add_keys = [c["key"] for c in ch4_block["calculations"]]
    bundle_keys = {primary_key, *add_keys}
    for ref in sm_sources:
        assert ref["calculation_key"] in bundle_keys


def test_to_payload_deterministic(populated_upload):
    p1 = populated_upload.to_payload()
    p2 = populated_upload.to_payload()
    assert p1 == p2
    assert json.dumps(p1, sort_keys=True) == json.dumps(p2, sort_keys=True)


# ----- validation ----------------------------------------------------


def test_species_not_in_reaction_rejected(ts_geom, ch4, ch3):
    sr = _sr()
    lot = _lot()
    extra = Species(smiles="O", charge=0, multiplicity=1, label="H2O")
    ts_opt = Calculation.opt(sr, lot, output_geometry=ts_geom, converged=True)
    rxn = ChemReaction(
        reactants=[ch3], products=[ch4],
        transition_state=TransitionState(charge=0, multiplicity=2, geometry=ts_geom),
    )
    with pytest.raises(TCKDBBuilderValidationError):
        ComputedReactionUpload(
            reaction=rxn,
            calculations=[ts_opt],
            species_statmech={extra: Statmech(external_symmetry=1)},
        )


def test_non_statmech_value_rejected(ts_geom, ch4, ch3):
    sr = _sr()
    lot = _lot()
    ts_opt = Calculation.opt(sr, lot, output_geometry=ts_geom, converged=True)
    rxn = ChemReaction(
        reactants=[ch3], products=[ch4],
        transition_state=TransitionState(charge=0, multiplicity=2, geometry=ts_geom),
    )
    with pytest.raises(TCKDBBuilderValidationError):
        ComputedReactionUpload(
            reaction=rxn,
            calculations=[ts_opt],
            species_statmech={ch4: "not a statmech"},  # type: ignore[dict-item]
        )


def test_source_calc_outside_same_species_bucket_rejected(ts_geom, ch4, ch3):
    sr = _sr()
    lot = _lot()
    g = Geometry.from_xyz("1\nx\nH 0 0 0")
    ch3_opt = Calculation.opt(sr, lot, output_geometry=g, converged=True)
    ch4_opt = Calculation.opt(sr, lot, output_geometry=g, converged=True)
    ts_opt = Calculation.opt(sr, lot, output_geometry=ts_geom, converged=True)
    # Statmech attached to ch4 but sourced from a ch3 opt — wrong bucket.
    bad_sm = Statmech(
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
            species_statmech={ch4: bad_sm},
        )


def test_species_statmech_optional_no_block_emitted(ts_geom, ch4, ch3):
    sr = _sr()
    lot = _lot()
    ts_opt = Calculation.opt(sr, lot, output_geometry=ts_geom, converged=True)
    rxn = ChemReaction(
        reactants=[ch3], products=[ch4],
        transition_state=TransitionState(charge=0, multiplicity=2, geometry=ts_geom),
    )
    payload = ComputedReactionUpload(
        reaction=rxn, calculations=[ts_opt],
    ).to_payload()
    for sp_block in payload["species"]:
        assert "statmech" not in sp_block


def test_species_statmech_alongside_thermo(ts_geom, ch4, ch3):
    sr = _sr()
    lot = _lot()
    g = Geometry.from_xyz("1\nx\nH 0 0 0")
    ch4_opt = Calculation.opt(sr, lot, output_geometry=g, converged=True)
    ts_opt = Calculation.opt(sr, lot, output_geometry=ts_geom, converged=True)
    rxn = ChemReaction(
        reactants=[ch3], products=[ch4],
        transition_state=TransitionState(charge=0, multiplicity=2, geometry=ts_geom),
    )
    payload = ComputedReactionUpload(
        reaction=rxn,
        calculations=[ts_opt],
        species_calculations={ch4: [ch4_opt]},
        species_thermo={ch4: Thermo.scalar(h298_kj_mol=-74.6)},
        species_statmech={ch4: Statmech(external_symmetry=12, point_group="Td")},
    ).to_payload()
    ch4_block = next(sp for sp in payload["species"] if sp["key"] == "ch4")
    assert "thermo" in ch4_block
    assert "statmech" in ch4_block
