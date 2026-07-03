"""Contract test for the Phase-4 ``ComputedReactionUpload.species_statmech``.

Unlike ``species_thermo``, the computed-reaction backend's
``BundleStatmechIn`` DOES carry ``source_calculations`` — these tests
exercise the on-wire emission of those links.
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
    Statmech,
    TransitionState,
)

from app.schemas.workflows.computed_reaction_upload import (
    ComputedReactionUploadRequest,
)


def _sr() -> SoftwareRelease:
    return SoftwareRelease(software="Gaussian", version="16")


def _lot() -> LevelOfTheory:
    return LevelOfTheory(method="wb97xd", basis="def2tzvp")


def _build_upload(statmech: Statmech) -> ComputedReactionUpload:
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
    kin = Kinetics.modified_arrhenius(
        A=1e10, A_units="per_s", n=0, Ea=0,
        source_calculations={"ts_energy": ts_opt},
    )
    ch4 = Species(smiles="C", charge=0, multiplicity=1, label="CH4")
    ch3 = Species(smiles="[CH3]", charge=0, multiplicity=2, label="CH3")

    # The statmech fixture pins source_calculations to species-side
    # calcs, so use the same labels as those builders to make assertion
    # easier. The caller (test) supplied the statmech instance —
    # rebuild it here with the live calc references.
    sm_with_live_refs = Statmech(
        external_symmetry=statmech.external_symmetry,
        point_group=statmech.point_group,
        is_linear=statmech.is_linear,
        rigid_rotor_kind=statmech.rigid_rotor_kind,
        statmech_treatment=statmech.statmech_treatment,
        uses_projected_frequencies=statmech.uses_projected_frequencies,
        source_calculations=[
            ("opt", ch4_opt), ("freq", ch4_freq), ("sp", ch4_sp),
        ],
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
        species_calculations={ch4: [ch4_opt, ch4_freq, ch4_sp]},
        species_statmech={ch4: sm_with_live_refs},
    )


def test_species_statmech_payload_validates_against_backend_schema():
    sm = Statmech(
        external_symmetry=12, point_group="Td", is_linear=False,
        rigid_rotor_kind="spherical_top", statmech_treatment="rrho",
    )
    upload = _build_upload(sm)
    validated = ComputedReactionUploadRequest.model_validate(upload.to_payload())
    ch4_block = next(sp for sp in validated.species if sp.key == "ch4")
    assert ch4_block.statmech is not None
    assert ch4_block.statmech.external_symmetry == 12
    assert ch4_block.statmech.point_group == "Td"
    assert ch4_block.statmech.rigid_rotor_kind.value == "spherical_top"
    assert ch4_block.statmech.statmech_treatment.value == "rrho"


def test_species_statmech_source_calculations_resolve_into_bundle():
    sm = Statmech(external_symmetry=12, point_group="Td")
    upload = _build_upload(sm)
    payload = upload.to_payload()
    validated = ComputedReactionUploadRequest.model_validate(payload)
    ch4_block = next(sp for sp in validated.species if sp.key == "ch4")
    sm_sources = ch4_block.statmech.source_calculations
    assert [sc.role.value for sc in sm_sources] == ["opt", "freq", "sp"]

    # All keys resolve into the bundle's global calc namespace.
    all_calc_keys: set[str] = set()
    for sp_v in validated.species:
        for conf in sp_v.conformers:
            all_calc_keys.add(conf.calculation.key)
        for c in sp_v.calculations:
            all_calc_keys.add(c.key)
    all_calc_keys.add(validated.transition_state.calculation.key)
    for c in validated.transition_state.calculations:
        all_calc_keys.add(c.key)
    for sc in sm_sources:
        assert sc.calculation_key in all_calc_keys


def test_to_payload_twice_is_byte_stable_post_validation():
    sm = Statmech(external_symmetry=12, point_group="Td")
    upload = _build_upload(sm)
    p1 = upload.to_payload()
    p2 = upload.to_payload()
    v1 = ComputedReactionUploadRequest.model_validate(p1)
    v2 = ComputedReactionUploadRequest.model_validate(p2)
    assert v1.model_dump(mode="json") == v2.model_dump(mode="json")
