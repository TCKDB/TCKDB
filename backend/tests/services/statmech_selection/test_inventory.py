"""The statmech coverage inventory reads linked content, predicts the classifier's verdicts, and writes nothing."""

from __future__ import annotations

import itertools

import pytest
from sqlalchemy import func, select

from app.db.models.calculation import CalculationOutputGeometry
from app.db.models.common import (
    CalculationGeometryRole,
    EnergyCorrectionApplicationRole,
    EnergyUnit,
    FrequencyScaleKind,
    RigidRotorKind,
    StatmechCalculationRole,
    StatmechTreatmentKind,
    TorsionTreatmentKind,
)
from app.db.models.energy_correction import AppliedEnergyCorrection, FrequencyScaleFactor
from app.db.models.statmech import Statmech
from app.services.statmech_selection.inventory import load_legacy_facts, statmech_coverage_inventory
from app.services.statmech_selection.legacy import PROFILES, classify
from tests.services.scientific_read._factories import (
    attach_hessian,
    attach_output_geometry,
    attach_statmech_electronic_level,
    attach_statmech_source_calculation,
    attach_statmech_torsion,
    make_geometry,
    make_literature,
    make_statmech,
)
from tests.services.structure_selection._support import make_ts_world, make_world


def _delta(after: dict, before: dict, profile: str) -> dict[str, int]:
    return {k: after["by_profile"][profile][k] - before["by_profile"][profile][k] for k in after["by_profile"][profile]}


_VALUES = itertools.count(1)


def _scale(session, world, kind: FrequencyScaleKind, value: float | None = None) -> FrequencyScaleFactor:
    value = value if value is not None else 0.5 + next(_VALUES) / 1000  # a distinct row per call: identity includes the value
    fsf = FrequencyScaleFactor(level_of_theory_id=world.lot.id, scale_kind=kind, value=value)
    session.add(fsf)
    session.flush()
    return fsf


def _apply(session, world, fsf, calculation, role=EnergyCorrectionApplicationRole.zpe) -> None:
    session.add(
        AppliedEnergyCorrection(
            target_species_entry_id=world.entry.id,
            source_calculation_id=calculation.id,
            frequency_scale_factor_id=fsf.id,
            application_role=role,
            value=0.01,
            value_unit=EnergyUnit.hartree,
        )
    )
    session.flush()


@pytest.fixture
def seeded(db_session):
    baseline = statmech_coverage_inventory(db_session)
    world = make_world(db_session)
    freq = world.freq("f", frequencies=(100.0, 200.0, 300.0))
    entropy_factor = _scale(db_session, world, FrequencyScaleKind.entropy, 0.99)
    fundamental = _scale(db_session, world, FrequencyScaleKind.fundamental)
    # A: every stored fact the internal entropy claim needs. The factor is linked AND recorded as applied, through an
    # applied energy correction on its own frequency job.
    a = make_statmech(db_session, species_entry=world.entry, optical_isomers=1, frequency_scale_factor_id=entropy_factor.id)
    a.rigid_rotor_kind = RigidRotorKind.asymmetric_top
    a.rotational_constant_a_cm1, a.rotational_constant_b_cm1, a.rotational_constant_c_cm1 = 3.0, 2.0, 1.0
    a.uses_projected_frequencies = False
    attach_statmech_source_calculation(db_session, statmech=a, calculation=freq, role=StatmechCalculationRole.freq)
    attach_statmech_electronic_level(db_session, statmech=a, level_index=1, energy_cm1=0.0, degeneracy=1)
    _apply(db_session, world, entropy_factor, freq, EnergyCorrectionApplicationRole.entropy_contribution)
    # B: a row that says nothing of its own and links nothing.
    b = make_statmech(
        db_session, species_entry=world.entry, external_symmetry=None, is_linear=None, statmech_treatment=None, point_group=None
    )
    # C: two frequency jobs on two different geometries, so the geometry is an undeclared alternative.
    other = make_geometry(db_session)
    freq2 = world.freq("f2", frequencies=(110.0, 210.0, 310.0), geometry=other)
    c = make_statmech(db_session, species_entry=world.entry, optical_isomers=1)
    attach_statmech_source_calculation(db_session, statmech=c, calculation=freq, role=StatmechCalculationRole.freq)
    attach_statmech_source_calculation(db_session, statmech=c, calculation=freq2, role=StatmechCalculationRole.freq)
    # D: a torsion-bearing row that never says whether its frequencies were projected; its factor is linked only.
    d = make_statmech(db_session, species_entry=world.entry, frequency_scale_factor_id=fundamental.id)
    d.statmech_treatment = StatmechTreatmentKind.rrho_1d
    attach_statmech_torsion(db_session, statmech=d, treatment_kind=TorsionTreatmentKind.free_rotor)
    # E: a transition-state-owned row.
    ts = make_ts_world(db_session)
    tfreq = ts.freq("tf", frequencies=(-1500.0, 100.0), reaction_coordinate_mode_index=1)
    e = Statmech(
        transition_state_entry_id=ts.entry.id,
        scientific_origin=a.scientific_origin,
        external_symmetry=1,
        statmech_treatment=a.statmech_treatment,
    )
    db_session.add(e)
    db_session.flush()
    attach_statmech_source_calculation(db_session, statmech=e, calculation=tfreq, role=StatmechCalculationRole.freq)
    db_session.flush()
    return baseline, {"a": a, "b": b, "c": c, "d": d, "e": e}, world


def test_facts_are_read_from_the_links_not_only_the_row(db_session, seeded):
    _, rows, _ = seeded
    facts = load_legacy_facts(db_session)
    a, b, c, e = (facts[rows[k].id] for k in "abce")
    assert a.n_curvature_sources == 1 and a.n_source_geometries == 1 and a.n_rotational_constants == 3
    assert b.n_curvature_sources == 0 and b.n_source_geometries == 0
    assert c.n_curvature_sources == 2 and c.n_source_geometries == 2  # two geometries: alternatives, not a pick
    assert e.reaction_coordinate_designated is True and e.owner_kind.value == "transition_state_entry"


def test_a_scale_factor_is_applied_only_where_an_applied_correction_records_it(db_session, seeded):
    _, rows, _ = seeded
    facts = load_legacy_facts(db_session)
    a, c, d = (facts[rows[k].id] for k in "acd")
    assert a.has_scale_factor and a.applied_scale_kinds == frozenset({"entropy"}) and not a.scale_evidence_unmatched
    # D links a fundamental factor but nothing records it as applied; C shares A's frequency job, so it reads the same
    # recorded application of that job (the evidence is the source calculation's).
    assert d.has_scale_factor and d.applied_scale_kinds == frozenset()
    assert c.applied_scale_kinds == frozenset({"entropy"})


@pytest.mark.parametrize(
    ("role", "kind", "applied", "unmatched"),
    [
        (EnergyCorrectionApplicationRole.zpe, FrequencyScaleKind.zpe, {"zpe"}, False),
        (EnergyCorrectionApplicationRole.entropy_contribution, FrequencyScaleKind.entropy, {"entropy"}, False),
        (EnergyCorrectionApplicationRole.thermal_correction_enthalpy, FrequencyScaleKind.enthalpy, {"enthalpy"}, False),
        # The role names an energy, the factor was calibrated for something else: a factor was used, for no known purpose.
        (EnergyCorrectionApplicationRole.zpe, FrequencyScaleKind.fundamental, set(), True),
        (EnergyCorrectionApplicationRole.entropy_contribution, FrequencyScaleKind.heat_capacity, set(), True),
        (EnergyCorrectionApplicationRole.zpe, FrequencyScaleKind.entropy, set(), True),
        # A role that names no factor kind evidences none.
        (EnergyCorrectionApplicationRole.thermal_correction_energy, FrequencyScaleKind.zpe, set(), True),
        (EnergyCorrectionApplicationRole.thermal_correction_gibbs, FrequencyScaleKind.entropy, set(), True),
    ],
)
def test_application_is_read_from_the_corrections_role_and_counts_only_when_the_factors_kind_agrees(
    db_session, seeded, role, kind, applied, unmatched
):
    _, _, world = seeded
    calc = world.freq("role_probe", frequencies=(100.0, 200.0, 300.0))
    row = make_statmech(db_session, species_entry=world.entry)
    attach_statmech_source_calculation(db_session, statmech=row, calculation=calc, role=StatmechCalculationRole.freq)
    _apply(db_session, world, _scale(db_session, world, kind), calc, role)
    facts = load_legacy_facts(db_session)[row.id]
    assert facts.applied_scale_kinds == frozenset(applied) and facts.scale_evidence_unmatched is unmatched


def test_a_fundamental_factor_in_a_zero_point_correction_does_not_evidence_a_fundamental_scaling(db_session, seeded):
    """The wrong way round was the defect: ``vibrational_component`` must not read present from a ZPE correction."""
    _, _, world = seeded
    calc = world.freq("wrong_way", frequencies=(100.0, 200.0, 300.0))
    row = make_statmech(db_session, species_entry=world.entry, optical_isomers=1)
    row.rigid_rotor_kind = RigidRotorKind.asymmetric_top
    row.uses_projected_frequencies = False
    attach_statmech_source_calculation(db_session, statmech=row, calculation=calc, role=StatmechCalculationRole.freq)
    _apply(db_session, world, _scale(db_session, world, FrequencyScaleKind.fundamental), calc)  # role: zpe
    facts = load_legacy_facts(db_session)[row.id]
    assert classify("vibrational_component", facts)[1]["scale_application"] == "ambiguous"
    assert classify("zpe_component", facts)[1]["scale_application"] == "ambiguous"
    assert classify("vibrational_component", facts)[0].value == "represented_but_ambiguous"


def test_application_evidence_belongs_to_the_record_whose_source_calculation_carries_it(db_session, seeded):
    """Two records of one entry, each linked to a different frequency job, a correction on only one job."""
    _, _, world = seeded
    corrected = world.freq("corrected", frequencies=(100.0, 200.0, 300.0))
    plain = world.freq("plain", frequencies=(100.0, 200.0, 300.0))
    with_evidence = make_statmech(db_session, species_entry=world.entry)
    without = make_statmech(db_session, species_entry=world.entry)
    attach_statmech_source_calculation(db_session, statmech=with_evidence, calculation=corrected, role=StatmechCalculationRole.freq)
    attach_statmech_source_calculation(db_session, statmech=without, calculation=plain, role=StatmechCalculationRole.freq)
    _apply(db_session, world, _scale(db_session, world, FrequencyScaleKind.zpe), corrected)
    mismatched_job = world.freq("mismatched", frequencies=(100.0, 200.0, 300.0))
    mismatched = make_statmech(db_session, species_entry=world.entry)
    attach_statmech_source_calculation(db_session, statmech=mismatched, calculation=mismatched_job, role=StatmechCalculationRole.freq)
    _apply(db_session, world, _scale(db_session, world, FrequencyScaleKind.fundamental), mismatched_job)  # role zpe
    facts = load_legacy_facts(db_session)
    assert facts[with_evidence.id].applied_scale_kinds == frozenset({"zpe"})
    assert facts[without.id].applied_scale_kinds == frozenset() and facts[without.id].scale_evidence_unmatched is False
    assert facts[with_evidence.id].scale_evidence_unmatched is False  # its own evidence matched
    assert facts[mismatched.id].applied_scale_kinds == frozenset() and facts[mismatched.id].scale_evidence_unmatched is True


def test_an_opt_or_composite_source_with_a_hessian_is_curvature_evidence(db_session, seeded):
    """Result-bearing curvature is discovered on any source, not only on a ``freq`` job (plan section 2, item 3)."""
    _, _, world = seeded
    geometry = make_geometry(db_session)
    opt = world.opt("opt_with_hessian", -76.4, geometry=geometry)
    attach_hessian(db_session, calculation=opt, geometry=geometry, natoms=3)
    row = make_statmech(db_session, species_entry=world.entry)
    attach_statmech_source_calculation(db_session, statmech=row, calculation=opt, role=StatmechCalculationRole.opt)
    plain = world.opt("opt_plain", -76.4, geometry=geometry)
    row_plain = make_statmech(db_session, species_entry=world.entry)
    attach_statmech_source_calculation(db_session, statmech=row_plain, calculation=plain, role=StatmechCalculationRole.opt)
    facts = load_legacy_facts(db_session)
    assert facts[row.id].n_curvature_sources == 1
    assert facts[row_plain.id].n_curvature_sources == 0


def test_an_initial_geometry_beside_a_declared_final_one_is_not_a_second_geometry(db_session, seeded):
    _, _, world = seeded
    initial, final = make_geometry(db_session), make_geometry(db_session)
    opt = world.opt("opt_roles", -76.4, geometry=final)
    attach_output_geometry(db_session, calculation=opt, geometry=initial, role=CalculationGeometryRole.initial, output_order=2)
    row = make_statmech(db_session, species_entry=world.entry)
    attach_statmech_source_calculation(db_session, statmech=row, calculation=opt, role=StatmechCalculationRole.opt)
    assert db_session.scalar(select(func.count()).select_from(CalculationOutputGeometry).where(
        CalculationOutputGeometry.calculation_id == opt.id)) == 2
    assert load_legacy_facts(db_session)[row.id].n_source_geometries == 1
    # A scan point beside the declared final output is not an alternative to it either.
    undeclared = world.opt("opt_undeclared", -76.4, geometry=make_geometry(db_session))
    attach_output_geometry(
        db_session, calculation=undeclared, geometry=make_geometry(db_session), role=CalculationGeometryRole.scan_point, output_order=2
    )
    row2 = make_statmech(db_session, species_entry=world.entry)
    attach_statmech_source_calculation(db_session, statmech=row2, calculation=undeclared, role=StatmechCalculationRole.opt)
    assert load_legacy_facts(db_session)[row2.id].n_source_geometries == 1


def test_the_inventory_counts_each_claim_by_the_classifier_and_names_the_open_requirement(db_session, seeded):
    baseline, _, _ = seeded
    now = statmech_coverage_inventory(db_session)
    rot = _delta(now, baseline, "rotational_component")
    assert rot["represented_and_usable"] == 1  # A
    assert rot["absent"] == 1  # B
    assert rot["represented_but_ambiguous"] >= 1  # C has two geometries
    assert rot["unsupported_declaration_version"] == 0
    entropy = _delta(now, baseline, "within_basin_thermal.entropy")
    assert entropy["represented_and_usable"] == 1  # A: entropy factor recorded as applied
    assert entropy["not_applicable"] == 1  # the TS-owned row
    heat_capacity = _delta(now, baseline, "within_basin_thermal.heat_capacity")
    assert heat_capacity["represented_and_usable"] == 0  # A's factor was calibrated for entropy, not Cp
    ts = _delta(now, baseline, "conventional_ts_transverse_model")
    assert ts["not_applicable"] == 4  # the four species-owned rows
    full = _delta(now, baseline, "canonical_model.full_partition")
    assert full["represented_and_usable"] == 0  # no legacy row states a translational normalisation
    assert now["ambiguity_breakdown"]["within_basin_thermal.entropy"]["geometry:ambiguous"] >= 1
    assert now["ambiguity_breakdown"]["within_basin_thermal.heat_capacity"]["scale_application:ambiguous"] >= 1
    assert now["ambiguity_breakdown"]["canonical_model.full_partition"]["translational_normalization:missing"] >= 1


def test_every_record_lands_in_exactly_one_bucket_per_claim(db_session, seeded):
    report = statmech_coverage_inventory(db_session)
    for profile in PROFILES:
        buckets = report["by_profile"][profile]
        total = sum(v for k, v in buckets.items() if k != "unsupported_declaration_version")
        assert total == report["legacy_records"], profile  # a declared model is not a legacy record


def test_the_inventory_and_the_classifier_agree_record_by_record(db_session, seeded):
    _, rows, _ = seeded
    facts = load_legacy_facts(db_session)
    report = statmech_coverage_inventory(db_session)
    for profile in PROFILES:
        usable = sum(classify(profile, f)[0].value == "represented_and_usable" for f in facts.values())
        assert report["by_profile"][profile]["represented_and_usable"] == usable
    # The torsion-bearing row with an unknown projection status is disclosed, not assumed projected.
    assert facts[rows["d"].id].uses_projected_frequencies is None
    assert report["metadata"]["torsion_bearing_with_unknown_projection"] >= 1
    assert report["metadata"]["with_recorded_scale_application"] >= 1


def test_the_inventory_writes_nothing_and_says_it_infers_nothing(db_session, seeded):
    before = db_session.execute(select(func.count()).select_from(Statmech)).scalar_one()
    report = statmech_coverage_inventory(db_session)
    assert db_session.execute(select(func.count()).select_from(Statmech)).scalar_one() == before
    assert "Nothing was written, backfilled or inferred" in report["note"]
    assert report["declarations"]["unsupported_declaration_version"] == 0


def test_a_declared_model_is_counted_as_one_and_read_through_its_declaration_not_as_a_legacy_record(db_session, seeded):
    from app.db.models.common import StatmechModelScopeKind
    from app.db.models.statmech_declaration import StatmechModelDeclaration

    baseline, rows, world = seeded
    before = statmech_coverage_inventory(db_session)
    declared = make_statmech(db_session, species_entry=world.entry)
    unreadable = make_statmech(db_session, species_entry=world.entry)
    for host, version in ((declared, 1), (unreadable, 2)):
        db_session.add(
            StatmechModelDeclaration(
                statmech_id=host.id,
                schema_version=version,
                scope_kind=StatmechModelScopeKind.atom,
                workflow_tool_release_id=None,
                literature_id=make_literature(db_session).id,
                recipe={},
                content_hash="c" * 64,
            )
        )
    db_session.flush()
    after = statmech_coverage_inventory(db_session)
    assert after["records"] == before["records"] + 2 and after["legacy_records"] == before["legacy_records"]
    assert after["declared_models"] == before["declared_models"] + 2
    assert after["declarations"]["unsupported_declaration_version"] == before["declarations"]["unsupported_declaration_version"] + 1
    assert after["declarations"]["by_scope_kind"]["atom"] == before["declarations"]["by_scope_kind"].get("atom", 0) + 2
    for profile in PROFILES:
        assert after["by_profile"][profile]["unsupported_declaration_version"] == after["declarations"]["unsupported_declaration_version"]
        legacy_buckets = {k: v for k, v in after["by_profile"][profile].items() if k != "unsupported_declaration_version"}
        assert legacy_buckets == {k: v for k, v in before["by_profile"][profile].items() if k != "unsupported_declaration_version"}
