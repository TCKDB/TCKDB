"""Contract test for the Phase-3B ``species_thermo`` extension.

Lives in the backend test tree because it imports both the client
builder layer and the backend schema. Verifies the wire shape the
builder emits for per-species thermo (scalar, NASA, points) validates
against ``ComputedReactionUploadRequest``.

Note: the computed-reaction ``BundleThermoIn`` does NOT carry
``source_calculations``. The builder validates source-calc refs
locally for forward compatibility but does not emit them on the wire
for this endpoint — these contract tests therefore do not assert any
``source_calculations`` field on the resulting block.
"""

from __future__ import annotations

import pytest

pytest.importorskip("tckdb_client.builders")

from tckdb_client.builders import (  # noqa: E402
    Calculation,
    ChemReaction,
    ComputedReactionUpload,
    Geometry,
    Kinetics,
    LevelOfTheory,
    Species,
    SoftwareRelease,
    Thermo,
    TransitionState,
)

from app.schemas.workflows.computed_reaction_upload import (  # noqa: E402
    ComputedReactionUploadRequest,
)


def _gaussian() -> SoftwareRelease:
    return SoftwareRelease(software="Gaussian", version="16")


def _lot() -> LevelOfTheory:
    return LevelOfTheory(method="wb97xd", basis="def2tzvp")


def _ts_geom() -> Geometry:
    return Geometry.from_xyz("3\nts\nC 0 0 0\nH 0 0 0.8\nH 0 0 -1.0")


def _ch4_geom() -> Geometry:
    return Geometry.from_xyz(
        "5\nch4\nC 0 0 0\nH 0 0 1\nH 0 0 -1\nH 0 1 0\nH 0 -1 0"
    )


def _build_upload(thermo: Thermo) -> ComputedReactionUpload:
    sr = _gaussian()
    lot = _lot()
    ts_geom = _ts_geom()
    ch4_opt = Calculation.opt(
        sr, lot, output_geometry=_ch4_geom(), converged=True,
        final_energy_hartree=-40.5, label="ch4 opt",
    )
    ch4_sp = Calculation.sp(
        sr, lot, electronic_energy_hartree=-40.51,
        depends_on=ch4_opt, label="ch4 sp",
    )
    ts_opt = Calculation.opt(
        sr, lot, output_geometry=ts_geom, converged=True, label="ts opt",
    )
    ts_freq = Calculation.freq(
        sr, lot, n_imag=1, imag_freq_cm1=-1200.0, depends_on=ts_opt,
        label="ts freq",
    )
    kin = Kinetics.modified_arrhenius(
        A=1.2e13, A_units="cm3_mol_s", n=0.5, Ea=10.0, Ea_units="kJ/mol",
        source_calculations={"ts_energy": ts_opt},
    )
    ch4 = Species(smiles="C", charge=0, multiplicity=1, label="CH4")
    ch3 = Species(smiles="[CH3]", charge=0, multiplicity=2, label="CH3")
    rxn = ChemReaction(
        reactants=[ch3], products=[ch4],
        family="H_Abstraction",
        transition_state=TransitionState(
            charge=0, multiplicity=2, geometry=ts_geom, label="ts",
        ),
        kinetics=[kin],
    )
    return ComputedReactionUpload(
        reaction=rxn,
        calculations=[ts_opt, ts_freq],
        species_calculations={ch4: [ch4_opt, ch4_sp]},
        species_thermo={ch4: thermo},
    )


def _make_thermo_with_sources_pointing_to_species_calcs() -> ComputedReactionUpload:
    """Variant: thermo source_calculations reference species-side calcs.

    These resolve through the builder's same-species-bucket check; the
    field is NOT emitted on the wire today, so the resulting payload
    must still validate against the unchanged ``BundleThermoIn`` shape.
    """
    sr = _gaussian()
    lot = _lot()
    ts_geom = _ts_geom()
    ch4_opt = Calculation.opt(
        sr, lot, output_geometry=_ch4_geom(), converged=True,
        final_energy_hartree=-40.5, label="ch4 opt",
    )
    ch4_sp = Calculation.sp(
        sr, lot, electronic_energy_hartree=-40.51,
        depends_on=ch4_opt, label="ch4 sp",
    )
    ts_opt = Calculation.opt(
        sr, lot, output_geometry=ts_geom, converged=True, label="ts opt",
    )
    thermo = Thermo.scalar(
        h298_kj_mol=-74.6, s298_j_mol_k=186.3,
        source_calculations={"opt": ch4_opt, "sp": ch4_sp},
    )
    kin = Kinetics.modified_arrhenius(A=1e10, A_units="per_s", n=0, Ea=0)
    ch4 = Species(smiles="C", charge=0, multiplicity=1, label="CH4")
    ch3 = Species(smiles="[CH3]", charge=0, multiplicity=2, label="CH3")
    rxn = ChemReaction(
        reactants=[ch3], products=[ch4],
        transition_state=TransitionState(
            charge=0, multiplicity=2, geometry=ts_geom,
        ),
        family="H_Abstraction",
        kinetics=[kin],
    )
    return ComputedReactionUpload(
        reaction=rxn,
        calculations=[ts_opt],
        species_calculations={ch4: [ch4_opt, ch4_sp]},
        species_thermo={ch4: thermo},
    )


# ---------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------


@pytest.fixture
def nasa_thermo() -> Thermo:
    return Thermo.nasa(
        coeffs_low=[0.5] + [0.0] * 6,
        coeffs_high=[0.5] + [0.0] * 6,
        t_low=200, t_mid=1000, t_high=5000,
        h298_kj_mol=-74.6, s298_j_mol_k=186.3,
        label="ch4 nasa",
    )


@pytest.fixture
def scalar_thermo() -> Thermo:
    return Thermo.scalar(
        h298_kj_mol=-74.6, s298_j_mol_k=186.3, tmin_k=200, tmax_k=2000,
    )


@pytest.fixture
def points_thermo() -> Thermo:
    return Thermo.points(
        [
            {"temperature_k": 298.15, "cp_j_mol_k": 35.3, "h_kj_mol": 0.0,
             "s_j_mol_k": 186.3},
            {"temperature_k": 500.0, "cp_j_mol_k": 46.0, "h_kj_mol": 10.0},
        ],
        tmin_k=200, tmax_k=1000,
    )


# ---------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------


def test_nasa_thermo_payload_validates(nasa_thermo):
    upload = _build_upload(nasa_thermo)
    payload = upload.to_payload()
    validated = ComputedReactionUploadRequest.model_validate(payload)
    ch4_block = next(sp for sp in validated.species if sp.key == "ch4")
    assert ch4_block.thermo is not None
    assert ch4_block.thermo.h298_kj_mol == -74.6
    assert ch4_block.thermo.nasa is not None
    assert ch4_block.thermo.nasa.t_low == 200
    assert ch4_block.thermo.nasa.t_mid == 1000
    assert ch4_block.thermo.nasa.t_high == 5000
    assert ch4_block.thermo.nasa.a1 == 0.5
    assert ch4_block.thermo.nasa.b1 == 0.5


def test_scalar_thermo_payload_validates(scalar_thermo):
    upload = _build_upload(scalar_thermo)
    payload = upload.to_payload()
    validated = ComputedReactionUploadRequest.model_validate(payload)
    ch4_block = next(sp for sp in validated.species if sp.key == "ch4")
    assert ch4_block.thermo is not None
    assert ch4_block.thermo.h298_kj_mol == -74.6
    assert ch4_block.thermo.s298_j_mol_k == 186.3
    assert ch4_block.thermo.tmin_k == 200
    assert ch4_block.thermo.tmax_k == 2000
    assert ch4_block.thermo.nasa is None
    assert ch4_block.thermo.points == []


def test_points_thermo_payload_validates(points_thermo):
    upload = _build_upload(points_thermo)
    payload = upload.to_payload()
    validated = ComputedReactionUploadRequest.model_validate(payload)
    ch4_block = next(sp for sp in validated.species if sp.key == "ch4")
    assert ch4_block.thermo is not None
    assert len(ch4_block.thermo.points) == 2
    assert ch4_block.thermo.points[0].temperature_k == 298.15
    assert ch4_block.thermo.points[0].cp_j_mol_k == 35.3


def test_thermo_to_payload_twice_is_byte_stable(nasa_thermo):
    upload = _build_upload(nasa_thermo)
    p1 = upload.to_payload()
    p2 = upload.to_payload()
    v1 = ComputedReactionUploadRequest.model_validate(p1)
    v2 = ComputedReactionUploadRequest.model_validate(p2)
    assert v1.model_dump(mode="json") == v2.model_dump(mode="json")


def test_thermo_source_calculations_resolve_into_species_bucket():
    """Even when source_calculations refer to species-side calcs, the
    resulting payload validates because the field is intentionally not
    emitted by the computed-reaction emitter today.
    """
    upload = _make_thermo_with_sources_pointing_to_species_calcs()
    payload = upload.to_payload()
    validated = ComputedReactionUploadRequest.model_validate(payload)
    ch4_block = next(sp for sp in validated.species if sp.key == "ch4")
    assert ch4_block.thermo is not None
    # The backend schema accepts the block as-is; source_calculations is
    # absent on the wire for computed-reaction.
    assert ch4_block.thermo.h298_kj_mol == -74.6
