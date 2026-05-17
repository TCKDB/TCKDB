"""``ComputedReactionUpload`` × ``species_transport`` integration tests.

Same forward-compat contract as the computed-species side: builder
accepts and validates locally, the bundle schema lacks a transport
field so nothing is emitted on the wire. These tests pin the
validation surface and the no-emit guarantee.
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
    Transport,
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


# --- API acceptance --------------------------------------------------


def test_accepts_species_transport(ts_geom, ch4, ch3):
    sr = _sr()
    lot = _lot()
    g = Geometry.from_xyz("1\nx\nH 0 0 0")
    ch4_opt = Calculation.opt(sr, lot, output_geometry=g, converged=True)
    ts_opt = Calculation.opt(sr, lot, output_geometry=ts_geom, converged=True)
    tr = Transport(
        sigma_angstrom=3.8, epsilon_over_k_k=141.4,
        dipole_debye=0.0, polarizability_angstrom3=2.6,
        rotational_relaxation=13.0,
        source_calculations={"supporting_geometry": ch4_opt},
    )
    rxn = ChemReaction(
        reactants=[ch3], products=[ch4],
        transition_state=TransitionState(charge=0, multiplicity=2, geometry=ts_geom),
    )
    upload = ComputedReactionUpload(
        reaction=rxn,
        calculations=[ts_opt],
        species_calculations={ch4: [ch4_opt]},
        species_transport={ch4: tr},
    )
    # Stored internally.
    assert upload.species_transport == {ch4: tr}


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
            species_transport={extra: Transport(dipole_debye=0.1)},
        )


def test_non_transport_value_rejected(ts_geom, ch4, ch3):
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
            species_transport={ch4: "not a transport"},  # type: ignore[dict-item]
        )


def test_source_calc_outside_same_species_bucket_rejected(ts_geom, ch4, ch3):
    sr = _sr()
    lot = _lot()
    g = Geometry.from_xyz("1\nx\nH 0 0 0")
    ch3_opt = Calculation.opt(sr, lot, output_geometry=g, converged=True)
    ch4_opt = Calculation.opt(sr, lot, output_geometry=g, converged=True)
    ts_opt = Calculation.opt(sr, lot, output_geometry=ts_geom, converged=True)
    bad_transport = Transport(
        dipole_debye=0.1,
        source_calculations={"supporting_geometry": ch3_opt},  # wrong bucket
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
            species_transport={ch4: bad_transport},
        )


# --- forward-compat payload contract ---------------------------------


def test_transport_is_not_emitted_on_the_wire(ts_geom, ch4, ch3):
    sr = _sr()
    lot = _lot()
    g = Geometry.from_xyz("1\nx\nH 0 0 0")
    ch4_opt = Calculation.opt(sr, lot, output_geometry=g, converged=True)
    ts_opt = Calculation.opt(sr, lot, output_geometry=ts_geom, converged=True)
    tr = Transport(
        sigma_angstrom=3.8, epsilon_over_k_k=141.4,
        source_calculations={"supporting_geometry": ch4_opt},
    )
    rxn = ChemReaction(
        reactants=[ch3], products=[ch4],
        transition_state=TransitionState(charge=0, multiplicity=2, geometry=ts_geom),
    )
    payload = ComputedReactionUpload(
        reaction=rxn,
        calculations=[ts_opt],
        species_calculations={ch4: [ch4_opt]},
        species_transport={ch4: tr},
    ).to_payload()
    for sp_block in payload["species"]:
        assert "transport" not in sp_block


def test_species_transport_optional_no_block_emitted(ts_geom, ch4, ch3):
    sr = _sr()
    lot = _lot()
    ts_opt = Calculation.opt(sr, lot, output_geometry=ts_geom, converged=True)
    rxn = ChemReaction(
        reactants=[ch3], products=[ch4],
        transition_state=TransitionState(charge=0, multiplicity=2, geometry=ts_geom),
    )
    payload_no_transport = ComputedReactionUpload(
        reaction=rxn, calculations=[ts_opt],
    ).to_payload()
    for sp_block in payload_no_transport["species"]:
        assert "transport" not in sp_block


def test_transport_alongside_thermo_and_statmech(ts_geom, ch4, ch3):
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
        species_transport={ch4: Transport(sigma_angstrom=3.8, epsilon_over_k_k=141.4)},
    ).to_payload()
    ch4_block = next(sp for sp in payload["species"] if sp["key"] == "ch4")
    # thermo + statmech land on the wire, transport does not.
    assert "thermo" in ch4_block
    assert "statmech" in ch4_block
    assert "transport" not in ch4_block


def test_to_payload_deterministic_with_species_transport(ts_geom, ch4, ch3):
    sr = _sr()
    lot = _lot()
    g = Geometry.from_xyz("1\nx\nH 0 0 0")
    ch4_opt = Calculation.opt(sr, lot, output_geometry=g, converged=True)
    ts_opt = Calculation.opt(sr, lot, output_geometry=ts_geom, converged=True)
    rxn = ChemReaction(
        reactants=[ch3], products=[ch4],
        transition_state=TransitionState(charge=0, multiplicity=2, geometry=ts_geom),
    )
    upload = ComputedReactionUpload(
        reaction=rxn,
        calculations=[ts_opt],
        species_calculations={ch4: [ch4_opt]},
        species_transport={ch4: Transport(sigma_angstrom=3.8, epsilon_over_k_k=141.4)},
    )
    p1 = upload.to_payload()
    p2 = upload.to_payload()
    assert p1 == p2
    assert json.dumps(p1, sort_keys=True) == json.dumps(p2, sort_keys=True)
