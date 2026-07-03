"""Contract test for the Phase-5 ``species_transport`` extension.

Same forward-compat contract as the computed-species side: the
``ComputedReactionUploadRequest`` schema has no transport field
today, so the builder validates locally and drops the data on the
wire. These tests pin that the bundle payload still validates with
the kwarg present, and the primitive-shape ``TransportUploadPayload``
schema accepts the builder's standalone ``to_payload()`` output.
"""

from __future__ import annotations

import pytest

pytest.importorskip("tckdb_client.builders")

from tckdb_client.builders import (
    Calculation,
    ChemReaction,
    ComputedReactionUpload,
    Geometry,
    Kinetics,
    LevelOfTheory,
    SoftwareRelease,
    Species,
    TransitionState,
    Transport,
)

from app.schemas.workflows.computed_reaction_upload import (
    ComputedReactionUploadRequest,
)
from app.schemas.workflows.transport_upload import (
    TransportUploadPayload,
)


def _sr() -> SoftwareRelease:
    return SoftwareRelease(software="Gaussian", version="16")


def _lot() -> LevelOfTheory:
    return LevelOfTheory(method="wb97xd", basis="def2tzvp")


def _build_upload(transport: Transport) -> ComputedReactionUpload:
    sr = _sr()
    lot = _lot()
    ts_geom = Geometry.from_xyz("3\nts\nC 0 0 0\nH 0 0 0.8\nH 0 0 -1.0")
    ch4_geom = Geometry.from_xyz(
        "5\nch4\nC 0 0 0\nH 0 0 1\nH 0 0 -1\nH 0 1 0\nH 0 -1 0"
    )
    ch4_opt = Calculation.opt(
        sr, lot, output_geometry=ch4_geom, converged=True,
        final_energy_hartree=-40.5, label="ch4 opt",
    )
    ts_opt = Calculation.opt(
        sr, lot, output_geometry=ts_geom, converged=True, label="ts opt",
    )
    kin = Kinetics.modified_arrhenius(
        A=1e10, A_units="per_s", n=0, Ea=0,
        source_calculations={"ts_energy": ts_opt},
    )
    ch4 = Species(smiles="C", charge=0, multiplicity=1, label="CH4")
    ch3 = Species(smiles="[CH3]", charge=0, multiplicity=2, label="CH3")
    # The transport supplied to this helper carries source_calc
    # references; re-anchor them onto the live ``ch4_opt`` so the
    # builder's same-bucket check holds.
    transport_live = Transport(
        sigma_angstrom=transport.sigma_angstrom,
        epsilon_over_k_k=transport.epsilon_over_k_k,
        dipole_debye=transport.dipole_debye,
        polarizability_angstrom3=transport.polarizability_angstrom3,
        rotational_relaxation=transport.rotational_relaxation,
        source_calculations={"supporting_geometry": ch4_opt},
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
        calculations=[ts_opt],
        species_calculations={ch4: [ch4_opt]},
        species_transport={ch4: transport_live},
    )


@pytest.fixture
def transport_block() -> Transport:
    return Transport(
        sigma_angstrom=3.8, epsilon_over_k_k=141.4,
        dipole_debye=0.0, polarizability_angstrom3=2.6,
        rotational_relaxation=13.0,
    )


# --- bundle-side contract -------------------------------------------


def test_bundle_payload_validates_with_species_transport_kwarg(
    transport_block,
):
    upload = _build_upload(transport_block)
    payload = upload.to_payload()
    # Transport is not emitted on the wire for any species block.
    for sp_block in payload["species"]:
        assert "transport" not in sp_block
    ComputedReactionUploadRequest.model_validate(payload)


def test_bundle_payload_byte_identical_with_or_without_species_transport():
    sr = _sr()
    lot = _lot()
    ts_geom = Geometry.from_xyz("3\nts\nC 0 0 0\nH 0 0 0.8\nH 0 0 -1.0")
    g = Geometry.from_xyz("1\nx\nH 0 0 0")
    ch4_opt = Calculation.opt(sr, lot, output_geometry=g, converged=True)
    ts_opt = Calculation.opt(sr, lot, output_geometry=ts_geom, converged=True)
    ch4 = Species(smiles="C", charge=0, multiplicity=1, label="CH4")
    ch3 = Species(smiles="[CH3]", charge=0, multiplicity=2, label="CH3")
    rxn = ChemReaction(
        reactants=[ch3], products=[ch4],
        transition_state=TransitionState(
            charge=0, multiplicity=2, geometry=ts_geom,
        ),
    )
    payload_with = ComputedReactionUpload(
        reaction=rxn,
        calculations=[ts_opt],
        species_calculations={ch4: [ch4_opt]},
        species_transport={ch4: Transport(dipole_debye=0.1)},
    ).to_payload()
    payload_without = ComputedReactionUpload(
        reaction=rxn,
        calculations=[ts_opt],
        species_calculations={ch4: [ch4_opt]},
    ).to_payload()
    assert payload_with == payload_without


def test_to_payload_twice_is_byte_stable_post_validation(transport_block):
    upload = _build_upload(transport_block)
    p1 = upload.to_payload()
    p2 = upload.to_payload()
    v1 = ComputedReactionUploadRequest.model_validate(p1)
    v2 = ComputedReactionUploadRequest.model_validate(p2)
    assert v1.model_dump(mode="json") == v2.model_dump(mode="json")


# --- primitive transport contract -----------------------------------


def test_transport_to_payload_validates_against_primitive_schema(
    transport_block,
):
    primitive = transport_block.to_payload()
    validated = TransportUploadPayload.model_validate(primitive)
    assert validated.sigma_angstrom == 3.8
    assert validated.epsilon_over_k_k == 141.4
    assert validated.dipole_debye == 0.0
    assert validated.polarizability_angstrom3 == 2.6
    assert validated.rotational_relaxation == 13.0
