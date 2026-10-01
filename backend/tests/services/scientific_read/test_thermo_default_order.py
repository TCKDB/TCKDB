"""Default thermo order is review status, then newest (#648).

The evidence checklist and temperature coverage stay on each record as
displayed fields; neither takes part in the ordering. The read
(``get_species_thermo`` / ``search_thermo``) must pick the same top record as
the export (``build_export_record_set``).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.db.models.common import (
    RecordReviewStatus,
    ScientificOriginKind,
    SubmissionRecordType,
)
from app.schemas.reads.scientific_common import CollapseMode, SelectionPolicy
from app.schemas.reads.scientific_thermo import ThermoReadRequest
from app.schemas.reads.scientific_thermo_search import ThermoSearchRequest
from app.services.scientific_read.export import SeedSelection, build_export_record_set
from app.services.scientific_read.thermo import get_species_thermo
from app.services.scientific_read.thermo_search import search_thermo
from tests.services.scientific_read._factories import (
    attach_thermo_nasa,
    attach_thermo_source_calculation,
    make_calculation,
    make_species,
    make_species_entry,
    make_thermo_scalar,
    next_inchi_key,
    set_review,
)

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _entry(db_session):
    sp = make_species(db_session, smiles="CC", inchi_key=next_inchi_key("DO"))
    return make_species_entry(db_session, sp)


def _thermo(db_session, entry, *, origin, age_days, status=None, with_calc=False, nasa=False):
    """A thermo ``age_days`` old (larger = older) with an optional review status.

    ``created_at`` is set explicitly: every row in one test transaction would
    otherwise share the same ``now()`` and recency could not be exercised.
    """
    t = make_thermo_scalar(db_session, species_entry=entry, scientific_origin=origin)
    t.created_at = _T0 - timedelta(days=age_days)
    if with_calc:
        calc = make_calculation(db_session, species_entry_id=entry.id)
        attach_thermo_source_calculation(db_session, thermo=t, calculation=calc)
    if nasa:
        attach_thermo_nasa(db_session, thermo=t)
    db_session.flush()
    if status is not None:
        set_review(
            db_session,
            record_type=SubmissionRecordType.thermo,
            record_id=t.id,
            status=status,
        )
    return t


def _read_first(db_session, entry, **kw):
    resp = get_species_thermo(
        db_session,
        species_entry_id=entry.id,
        request=ThermoReadRequest(collapse=CollapseMode.first, **kw),
    )
    return resp.records[0].thermo_id


def _export_first(db_session, entry):
    rs = build_export_record_set(
        db_session,
        seed=SeedSelection(species_refs=[entry.public_ref]),
        min_review_status=None,
        collapse=CollapseMode.first,
        selection_policy=SelectionPolicy.default,
    )
    (sr,) = rs.species_records
    (sel,) = sr.thermos
    return sel.thermo.id


def test_newer_experimental_beats_older_computed_with_same_review(db_session):
    entry = _entry(db_session)
    computed = _thermo(
        db_session, entry, origin=ScientificOriginKind.computed, age_days=10, with_calc=True
    )
    experimental = _thermo(
        db_session, entry, origin=ScientificOriginKind.experimental, age_days=1
    )

    resp = get_species_thermo(
        db_session, species_entry_id=entry.id, request=ThermoReadRequest()
    )
    by_id = {r.thermo_id: r for r in resp.records}
    # The checklist is still reported, and still favours the computed record,
    # so this test only passes because the score no longer orders the list.
    assert (
        by_id[computed.id].evidence_completeness.score
        > by_id[experimental.id].evidence_completeness.score
    )
    assert [r.thermo_id for r in resp.records] == [experimental.id, computed.id]
    assert _read_first(db_session, entry) == experimental.id


def test_better_review_status_beats_newer_record(db_session):
    entry = _entry(db_session)
    approved_old = _thermo(
        db_session,
        entry,
        origin=ScientificOriginKind.computed,
        age_days=30,
        status=RecordReviewStatus.approved,
    )
    _thermo(db_session, entry, origin=ScientificOriginKind.experimental, age_days=1)

    assert _read_first(db_session, entry) == approved_old.id


def test_requested_temperature_range_does_not_reorder(db_session):
    """Coverage is displayed, not ranked: a newer scalar record with no range
    still beats an older record that covers the requested range."""
    entry = _entry(db_session)
    _thermo(
        db_session, entry, origin=ScientificOriginKind.computed, age_days=10, nasa=True
    )
    newer = _thermo(db_session, entry, origin=ScientificOriginKind.experimental, age_days=1)

    resp = get_species_thermo(
        db_session,
        species_entry_id=entry.id,
        request=ThermoReadRequest(
            collapse=CollapseMode.first, temperature_min=300.0, temperature_max=1000.0
        ),
    )
    assert resp.records[0].thermo_id == newer.id


@pytest.mark.parametrize("approve_old", [False, True])
def test_read_and_export_pick_the_same_record(db_session, approve_old):
    entry = _entry(db_session)
    old = _thermo(
        db_session,
        entry,
        origin=ScientificOriginKind.computed,
        age_days=10,
        with_calc=True,
        nasa=True,
        status=RecordReviewStatus.approved if approve_old else None,
    )
    new = _thermo(
        db_session, entry, origin=ScientificOriginKind.experimental, age_days=1, nasa=True
    )

    expected = old.id if approve_old else new.id
    assert _read_first(db_session, entry) == expected
    assert _export_first(db_session, entry) == expected


def test_search_orders_entry_thermo_by_review_then_newest(db_session):
    entry = _entry(db_session)
    computed = _thermo(
        db_session, entry, origin=ScientificOriginKind.computed, age_days=10, with_calc=True
    )
    experimental = _thermo(
        db_session, entry, origin=ScientificOriginKind.experimental, age_days=1
    )
    resp = search_thermo(db_session, ThermoSearchRequest(smiles="CC"))
    assert [r.thermo.thermo_id for r in resp.records] == [experimental.id, computed.id]
