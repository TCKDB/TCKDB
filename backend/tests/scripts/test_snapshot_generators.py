"""Generators added for the demonstration snapshot (``scripts/paper_snapshot``).

Each is exercised on rows seeded through the test factories. They were also run
against a full build of the snapshot;
these tests pin the shape and the rules (public refs only, ordered by ref, a
refusal counted rather than dropped).
"""

from __future__ import annotations

import json

from app.db.models.common import (
    ArtifactKind,
    CalculationType,
    PhaseKind,
    SubmissionRecordType,
    ThermoTargetKind,
)
from app.db.models.deposit_warning import DepositWarning
from app.services.deposit.expected_outputs import render_json
from scripts.paper import snapshot_generators
from tests.services.scientific_read._factories import (
    attach_artifact,
    make_calculation,
    make_calculation_with_conformer,
    make_conformer_group,
    make_conformer_observation,
    make_lot,
    make_species,
    make_species_entry,
    make_thermo_scalar,
)


def _no_integer_ids(document) -> list[str]:
    offenders: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if (key == "id" or key.endswith("_id")) and isinstance(value, int):
                    offenders.append(key)
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(document)
    return offenders


def test_hessian_reanalysis_counts_a_frequency_list_without_a_hessian_as_not_stored(db_session):
    from tests.services.scientific_read._factories import attach_freq_result

    species = make_species(db_session, smiles="O")
    entry = make_species_entry(db_session, species=species)
    calc = make_calculation(db_session, type=CalculationType.freq, species_entry_id=entry.id)
    attach_freq_result(db_session, calculation=calc, frequencies_cm1=[1595.0, 3657.0, 3756.0])

    out = snapshot_generators.hessian_reanalysis(db_session)
    assert out["scope"]["by_status"]["hessian_not_stored"] >= 1
    record = next(r for r in out["records"] if r["calculation_ref"] == calc.public_ref)
    assert record["status"] == "hessian_not_stored"  # counted, not dropped
    assert not _no_integer_ids(out)


def test_conformer_basins_lists_the_levels_that_observed_each_group(db_session):
    species = make_species(db_session, smiles="CCCC")
    entry = make_species_entry(db_session, species=species)
    group = make_conformer_group(db_session, entry)
    first = make_conformer_observation(db_session, conformer_group=group)
    second = make_conformer_observation(db_session, conformer_group=group)
    lot_a = make_lot(db_session, method="B3LYP", basis="def2-TZVP")
    lot_b = make_lot(db_session, method="wB97X-D", basis="def2-TZVP")
    make_calculation_with_conformer(db_session, species_entry=entry, conformer_observation=first, type=CalculationType.opt, lot_id=lot_a.id)
    make_calculation_with_conformer(db_session, species_entry=entry, conformer_observation=second, type=CalculationType.opt, lot_id=lot_b.id)
    make_calculation_with_conformer(db_session, species_entry=entry, conformer_observation=second, type=CalculationType.freq, lot_id=lot_b.id)

    out = snapshot_generators.conformer_basins(db_session)
    row = next(r for r in out["entries"] if r["species_entry_ref"] == entry.public_ref)
    assert row["conformer_groups"] == 1 and row["conformer_observations"] == 2
    (group_row,) = row["groups"]
    assert group_row["conformer_group_ref"] == group.public_ref
    # An observation counts once per level, however many of its calculations ran at that level.
    assert group_row["levels_observing"] == {"B3LYP/def2-TZVP": 1, "wB97X-D/def2-TZVP": 1}
    assert group_row["number_of_levels"] == 2
    assert out["groups_by_number_of_levels"]["2"] >= 1
    assert not _no_integer_ids(out)


def test_calculation_evidence_counts_job_files_by_kind_and_distinct_digests(db_session):
    species = make_species(db_session, smiles="CCO")
    entry = make_species_entry(db_session, species=species)
    one = make_calculation(db_session, type=CalculationType.opt, species_entry_id=entry.id)
    two = make_calculation(db_session, type=CalculationType.freq, species_entry_id=entry.id)
    before = snapshot_generators.calculation_evidence(db_session)
    for calc in (one, two):  # one combined job log attached to both calculations: one digest
        attach_artifact(db_session, calculation=calc, kind=ArtifactKind.output_log, sha256="a" * 64, bytes_=10)
    attach_artifact(db_session, calculation=one, kind=ArtifactKind.input, filename="job.gjf", sha256="b" * 64, bytes_=5)

    after = snapshot_generators.calculation_evidence(db_session)
    assert after["calculations_with_job_files"] == before["calculations_with_job_files"] + 2
    assert after["distinct_job_file_digests"] == before["distinct_job_file_digests"] + 2
    assert after["job_files_by_kind"]["output_log"]["files"] == before["job_files_by_kind"].get("output_log", {"files": 0})["files"] + 2
    assert after["job_files_by_kind"]["input"]["bytes"] == before["job_files_by_kind"].get("input", {"bytes": 0})["bytes"] + 5
    assert not _no_integer_ids(after)


def test_deposit_warnings_group_by_code_and_count_checked_markers(db_session):
    species = make_species(db_session, smiles="CC")
    entry = make_species_entry(db_session, species=species)
    thermo = make_thermo_scalar(db_session, species_entry=entry)
    for n in range(2):
        db_session.add(
            DepositWarning(
                record_type=SubmissionRecordType.thermo,
                record_id=thermo.id,
                code="demo_code",
                field=f"field_{n}",
                message="m",
                context={"n": n},
                content_hash=f"{n}" * 64,
                check_version=1,
            )
        )
    db_session.flush()

    out = snapshot_generators.deposit_warnings(db_session)
    assert out["by_code"]["demo_code"]["count"] == 2
    assert out["by_code"]["demo_code"]["record_types"] == {"thermo": 2}
    assert out["checked_markers"] == 0  # a "checked" marker row has no code; none was written here
    assert out["warnings_total"] == sum(v["count"] for v in out["by_code"].values())


def _declared_gas_thermo(db_session, entry):
    """A thermo record that declares what the selection route needs to judge it: gas phase, equilibrium ensemble."""
    thermo = make_thermo_scalar(db_session, species_entry=entry)
    thermo.thermodynamic_target_kind = ThermoTargetKind.equilibrium_ensemble
    thermo.phase = PhaseKind.gas
    db_session.flush()
    return thermo


def test_thermo_selection_outcomes_never_names_a_best_record(db_session):
    """Two eligible records of one entry: the answer is the pair, and neither is picked."""
    species = make_species(db_session, smiles="C")
    entry = make_species_entry(db_session, species=species)
    first = _declared_gas_thermo(db_session, entry)
    second = _declared_gas_thermo(db_session, entry)

    out = snapshot_generators.thermo_selection_outcomes(db_session)
    row = next(r for r in out["entries"] if r["species_entry_ref"] == entry.public_ref)
    assert row["thermo_records"] == 2
    assert row["outcome"] == "incomparable_alternatives"
    assert row["selected_thermo_ref"] is None
    assert sorted(row["eligible_thermo_refs"]) == sorted([first.public_ref, second.public_ref])
    assert out["outcomes"]["incomparable_alternatives"] >= 1
    first_bytes = render_json(out)
    assert first_bytes == render_json(snapshot_generators.thermo_selection_outcomes(db_session))
    assert not _no_integer_ids(json.loads(first_bytes))


def test_thermo_selection_outcomes_names_the_sole_eligible_record_as_the_only_one(db_session):
    species = make_species(db_session, smiles="CC")
    entry = make_species_entry(db_session, species=species)
    only = _declared_gas_thermo(db_session, entry)

    out = snapshot_generators.thermo_selection_outcomes(db_session)
    row = next(r for r in out["entries"] if r["species_entry_ref"] == entry.public_ref)
    assert row["outcome"] == "sole_eligible_candidate"
    assert row["selected_thermo_ref"] == only.public_ref


def test_thermo_selection_outcomes_says_when_a_record_declares_nothing(db_session):
    species = make_species(db_session, smiles="CCC")
    entry = make_species_entry(db_session, species=species)
    make_thermo_scalar(db_session, species_entry=entry)  # no target, no phase

    out = snapshot_generators.thermo_selection_outcomes(db_session)
    row = next(r for r in out["entries"] if r["species_entry_ref"] == entry.public_ref)
    assert row["outcome"] == "no_applicable_candidate"
    assert "thermodynamic_target_not_declared" in row["reason_codes"]


def test_structure_selection_outcomes_report_an_outcome_per_grain_and_name_no_first_record(db_session):
    species = make_species(db_session, smiles="CCCC")
    entry = make_species_entry(db_session, species=species)
    group = make_conformer_group(db_session, entry)
    observation = make_conformer_observation(db_session, conformer_group=group)
    lot = make_lot(db_session, method="B3LYP", basis="def2-TZVP")
    make_calculation_with_conformer(
        db_session, species_entry=entry, conformer_observation=observation, type=CalculationType.opt, lot_id=lot.id
    )

    out = snapshot_generators.structure_selection_outcomes(db_session)
    row = next(r for r in out["entries"] if r["species_entry_ref"] == entry.public_ref)
    for grain in ("calculations", "conformers"):
        assert row[grain]["outcome"]  # some outcome, stated
        assert row[grain]["administrative_first_named"] is False  # result_mode all: no record is named first
    assert set(out["outcomes_by_grain"]) == {"calculations", "conformers"}
    assert not _no_integer_ids(json.loads(render_json(out)))


def test_hessian_reanalysis_and_basins_list_records_by_content_not_by_ref(db_session):
    """Two builds of the same data differ in refs; the order must not."""
    from tests.services.scientific_read._factories import attach_freq_result

    lot = make_lot(db_session, method="B3LYP", basis="def2-TZVP")
    for smiles in ("CCO", "C", "CC"):
        species = make_species(db_session, smiles=smiles)
        entry = make_species_entry(db_session, species=species)
        calc = make_calculation(db_session, type=CalculationType.freq, species_entry_id=entry.id, lot_id=lot.id)
        attach_freq_result(db_session, calculation=calc, frequencies_cm1=[1000.0, 2000.0])

    out = snapshot_generators.hessian_reanalysis(db_session)
    smiles_in_order = [r["species_smiles"] for r in out["records"] if r["species_smiles"] in {"CCO", "C", "CC"}]
    assert smiles_in_order == sorted(smiles_in_order)
    assert all("species_smiles" in r and "level_of_theory" in r for r in out["records"])


def _declared_world(db_session):
    """An entry whose calculations carry the declaration that establishes a cohort, plus a conformer group."""
    from tests.services.structure_selection._support import make_world

    world = make_world(db_session)
    make_conformer_group(db_session, world.entry)
    return world


def test_structure_selection_orders_values_inside_one_established_cohort_and_names_no_first_record(db_session):
    from tests.services.structure_selection._support import declaration

    world = _declared_world(db_session)
    world.sp("a", -76.40, declared=declaration())
    world.sp("b", -76.45, declared=declaration())
    world.settle()

    out = snapshot_generators.structure_selection_outcomes(db_session)
    row = next(r for r in out["entries"] if r["species_entry_ref"] == world.entry.public_ref)
    calculations = row["calculations"]
    assert calculations["outcome"] == "recorded_minimum"  # ordered inside the one cohort the declarations establish
    assert calculations["cohorts"] == 1 and calculations["unresolved"] == {}
    assert calculations["administrative_first_named"] is False


def test_structure_selection_reports_alternatives_when_declarations_establish_two_cohorts(db_session):
    from tests.services.structure_selection._support import declaration

    world = _declared_world(db_session)
    other_level = make_lot(db_session, method="ccsd(t)", basis="cc-pvqz")
    world.sp("a", -76.40, declared=declaration())
    world.sp("b", -76.55, lot=other_level, declared=declaration())
    world.settle()

    out = snapshot_generators.structure_selection_outcomes(db_session)
    row = next(r for r in out["entries"] if r["species_entry_ref"] == world.entry.public_ref)
    calculations = row["calculations"]
    # Two recipes: the lower absolute energy is not a method winner, so no single minimum is reported.
    assert calculations["cohorts"] == 2
    assert calculations["outcome"] != "recorded_minimum"
    assert calculations["administrative_first_named"] is False
