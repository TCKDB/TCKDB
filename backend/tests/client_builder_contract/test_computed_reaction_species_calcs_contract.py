"""Contract test for the Phase-3A ``species_calculations`` extension.

Lives in the backend test tree because it imports both the client
builder layer and the backend schema. Verifies the wire shape the
builder emits for reactant/product calculations and duplicate-role
kinetics source links validates against
``ComputedReactionUploadRequest``.
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
    TransitionState,
)

from app.schemas.workflows.computed_reaction_upload import (  # noqa: E402
    ComputedReactionUploadRequest,
)


def _gaussian() -> SoftwareRelease:
    return SoftwareRelease(software="Gaussian", version="16")


def _b3lyp() -> LevelOfTheory:
    return LevelOfTheory(method="wb97xd", basis="def2tzvp")


def _make_bimolecular_upload() -> ComputedReactionUpload:
    """CH3 + H -> CH4 with per-species opt/sp + TS opt/freq + Arrhenius fit."""
    sr = _gaussian()
    lot = _b3lyp()

    ts_geom = Geometry.from_xyz(
        "3\nts\nC 0 0 0\nH 0 0 0.8\nH 0 0 -1.0"
    )
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
        sr, lot, electronic_energy_hartree=-0.5,
        depends_on=h_opt, label="h sp",
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
            # Duplicate ``reactant_energy`` role — bimolecular reaction.
            "reactant_energy": [ch3_sp, h_sp],
            "product_energy": ch4_sp,
            "ts_energy": ts_opt,
            "freq": ts_freq,
        },
    )

    ch3 = Species(smiles="[CH3]", charge=0, multiplicity=2, label="CH3")
    h = Species(smiles="[H]", charge=0, multiplicity=2, label="H")
    ch4 = Species(smiles="C", charge=0, multiplicity=1, label="CH4")

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


@pytest.fixture
def bimolecular_upload() -> ComputedReactionUpload:
    return _make_bimolecular_upload()


def test_species_calculations_payload_validates(bimolecular_upload):
    payload = bimolecular_upload.to_payload()
    validated = ComputedReactionUploadRequest.model_validate(payload)
    assert validated.reaction_family == "H_Abstraction"
    assert {sp.key for sp in validated.species} == {"ch3", "h", "ch4"}

    # Each species gets exactly one conformer with one opt and one
    # additional sp.
    by_key = {sp.key: sp for sp in validated.species}
    for key, expected_extra_type in [
        ("ch3", "sp"),
        ("h", "sp"),
        ("ch4", "sp"),
    ]:
        sp_block = by_key[key]
        assert len(sp_block.conformers) == 1
        assert sp_block.conformers[0].calculation.type.value == "opt"
        assert len(sp_block.calculations) == 1
        assert sp_block.calculations[0].type.value == expected_extra_type
        # The non-opt's geometry_key resolves to the conformer's geom key.
        assert (
            sp_block.calculations[0].geometry_key
            == sp_block.conformers[0].geometry.key
        )

    # TS side carries its own primary + freq.
    assert validated.transition_state is not None
    assert validated.transition_state.calculation.type.value == "opt"
    assert len(validated.transition_state.calculations) == 1
    assert validated.transition_state.calculations[0].type.value == "freq"


def test_to_payload_twice_is_byte_stable_post_validation(bimolecular_upload):
    p1 = bimolecular_upload.to_payload()
    p2 = bimolecular_upload.to_payload()
    v1 = ComputedReactionUploadRequest.model_validate(p1)
    v2 = ComputedReactionUploadRequest.model_validate(p2)
    assert v1.model_dump(mode="json") == v2.model_dump(mode="json")


def test_duplicate_reactant_energy_role_validates(bimolecular_upload):
    """The backend should accept two ``reactant_energy`` entries pointing
    at two different species-side SP calcs."""
    payload = bimolecular_upload.to_payload()
    validated = ComputedReactionUploadRequest.model_validate(payload)
    kin = validated.kinetics[0]
    reactant_energy_links = [
        sc for sc in kin.source_calculations
        if sc.role.value == "reactant_energy"
    ]
    assert len(reactant_energy_links) == 2
    # Each points at a distinct, species-side SP calc.
    keys = {link.calculation_key for link in reactant_energy_links}
    assert len(keys) == 2


def test_kinetics_source_refs_resolve_into_bundle_calc_namespace(
    bimolecular_upload,
):
    """Sanity-check the cross-bucket lookup the backend enforces."""
    payload = bimolecular_upload.to_payload()
    validated = ComputedReactionUploadRequest.model_validate(payload)
    all_calc_keys: set[str] = set()
    for sp in validated.species:
        for conf in sp.conformers:
            all_calc_keys.add(conf.calculation.key)
        for c in sp.calculations:
            all_calc_keys.add(c.key)
    all_calc_keys.add(validated.transition_state.calculation.key)
    for c in validated.transition_state.calculations:
        all_calc_keys.add(c.key)
    for sc in validated.kinetics[0].source_calculations:
        assert sc.calculation_key in all_calc_keys
