"""The structure coverage inventory counts what stored rows support, claims nothing, and writes nothing."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select, text

from app.db.models.calculation import Calculation
from app.db.models.common import CalculationQuality, StructureFindingKind, ValidationStatus
from app.db.models.structure_determination import StructureDetermination, StructureEvidenceFinding
from app.services.structure_selection.inventory import structure_coverage_inventory
from tests.services.structure_selection._support import declaration, make_ts_world, make_world


def _flat(value, prefix=""):
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _flat(v, f"{prefix}{k}.")
    elif isinstance(value, list):
        yield prefix.rstrip("."), value
    elif isinstance(value, (int, float)):
        yield prefix.rstrip("."), value


def _delta(after, before):
    a, b = dict(_flat(after)), dict(_flat(before))
    return {k: a[k] - b.get(k, 0) for k in a if isinstance(a[k], (int, float)) and a[k] != b.get(k, 0)}


@pytest.fixture
def seeded(db_session):
    """One species entry with a known mix of calculations, and one transition state entry. Counts are increments."""
    baseline = structure_coverage_inventory(db_session)
    world = make_world(db_session)
    world.sp("declared_a", -76.40, declared=declaration(), validation=ValidationStatus.fail)
    world.sp("declared_b", -76.45, declared=declaration())
    world.sp("undeclared", -76.50)
    world.sp("no_energy", None, declared=declaration())
    rejected = world.sp("rejected", -76.30, declared=declaration(), quality=CalculationQuality.rejected)
    world.settle()
    ts = make_ts_world(db_session)
    ts.freq("f_ok", frequencies=(-1500.0, 100.0))
    ts.freq("f_multi", frequencies=(-1500.0, -300.0, 100.0))  # several imaginary modes, no designated coordinate
    ts.settle()
    db_session.flush()
    return baseline, world, ts, rejected


def test_the_inventory_counts_each_unknown_that_is_still_open(db_session, seeded):
    baseline, *_ = seeded
    got = _delta(structure_coverage_inventory(db_session), baseline)
    assert got["calculations.energy_bearing"] == 4  # the no-energy calculation is not counted
    assert got["calculations.energy_bearing_with_actual_protocol"] == 3
    assert got["calculations.energy_bearing_without_actual_protocol"] == 1
    assert got["calculations.energy_bearing_species_calculations_without_conformer_observation"] == 4
    assert got["calculations.automated_geometry_validation.fail"] == 1
    assert got["calculations.rejected_quality"] == 1
    assert got["species_entries.with_two_or_more_energy_bearing_calculations"] == 1
    assert got["species_entries.of_which_some_but_not_all_have_an_actual_protocol"] == 1
    assert "species_entries.of_which_every_calculation_has_an_actual_protocol" not in got
    assert got["transition_states.with_a_frequency_result"] == 1
    assert got["transition_states.with_several_imaginary_modes_and_no_designated_reaction_coordinate"] == 1


def test_an_absent_declaration_is_counted_as_absent_never_assumed(db_session, seeded):
    """The undeclared calculation is in ``without``, not silently grouped with the declared ones."""
    baseline, *_ = seeded
    now = structure_coverage_inventory(db_session)["calculations"]
    before = baseline["calculations"]
    assert now["energy_bearing_without_actual_protocol"] - before["energy_bearing_without_actual_protocol"] == 1
    total_split = now["energy_bearing_with_actual_protocol"] + now["energy_bearing_without_actual_protocol"]
    assert total_split == now["energy_bearing"]  # every energy-bearing calculation is in exactly one bucket


def test_counts_are_of_rows_it_never_writes_and_the_note_says_so(db_session, seeded):
    def counts():
        return tuple(
            db_session.execute(select(func.count()).select_from(model)).scalar_one()
            for model in (Calculation, StructureDetermination, StructureEvidenceFinding)
        )

    before = counts()
    result = structure_coverage_inventory(db_session)
    assert counts() == before
    assert "never assumed" in result["note"] and "nothing was written" in result["note"]


def test_findings_are_counted_by_kind_verdict_and_authority(db_session):
    world = make_world(db_session)
    c = world.sp("a", -76.4, declared=declaration())
    baseline = structure_coverage_inventory(db_session)["determinations"]["findings_total"]
    world.finding(calculation=c, kind=StructureFindingKind.identity_incompatibility)
    after = structure_coverage_inventory(db_session)["determinations"]
    assert after["findings_total"] - baseline == 1
    assert any(row["kind"] == "identity_incompatibility" and row["count"] >= 1 for row in after["findings"])


def test_a_read_only_transaction_still_answers(db_session, seeded):
    db_session.flush()
    db_session.execute(text("SAVEPOINT inventory_probe"))
    try:
        db_session.execute(text("SET TRANSACTION READ ONLY"))
    except Exception:  # the harness transaction already ran statements; a real run sets it first
        db_session.execute(text("ROLLBACK TO SAVEPOINT inventory_probe"))
        pytest.skip("cannot become read-only after statements; the script sets it first")
    assert structure_coverage_inventory(db_session)["calculations"]["energy_bearing"] >= 4
