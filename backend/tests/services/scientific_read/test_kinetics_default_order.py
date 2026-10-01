"""Default kinetics order is review status, then newest (#648 rule for kinetics).

``evidence_completeness`` and ``temperature_coverage`` stay on each record as
displayed fields; neither takes part in the ordering. The read
(``get_reaction_kinetics`` / ``search_kinetics``) must pick the same top record
as the export (``build_export_record_set``).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.db.models.common import (
    CalculationType,
    KineticsCalculationRole,
    RecordReviewStatus,
    ScientificOriginKind,
    SubmissionRecordType,
)
from app.db.models.kinetics import KineticsSourceCalculation
from app.schemas.reads.scientific_common import CollapseMode, SelectionPolicy
from app.schemas.reads.scientific_kinetics import KineticsReadRequest
from app.schemas.reads.scientific_kinetics_search import KineticsSearchRequest
from app.services.scientific_read.export import SeedSelection, build_export_record_set
from app.services.scientific_read.kinetics import get_reaction_kinetics
from app.services.scientific_read.kinetics_search import search_kinetics
from tests.services.scientific_read._factories import (
    make_calculation,
    make_chem_reaction,
    make_kinetics,
    make_reaction_entry,
    make_species,
    make_species_entry,
    next_inchi_key,
    set_review,
)

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _reaction(db_session):
    rs = make_species(db_session, smiles="CC", inchi_key=next_inchi_key("KO"))
    ps = make_species(db_session, smiles="CCC", inchi_key=next_inchi_key("KP"))
    chem = make_chem_reaction(db_session, reactants=[rs], products=[ps])
    reactant_entry = make_species_entry(db_session, rs)
    entry = make_reaction_entry(
        db_session,
        reaction=chem,
        reactant_entries=[reactant_entry],
        product_entries=[make_species_entry(db_session, ps)],
    )
    entry.test_reactant_entry_id = reactant_entry.id  # owner for source calcs
    return entry


def _kinetics(
    db_session, entry, *, origin, age_days, status=None, with_calc=False, **kw
):
    """Kinetics ``age_days`` old (larger = older). ``created_at`` is set
    explicitly: rows in one test transaction would otherwise share ``now()``."""
    k = make_kinetics(db_session, reaction_entry=entry, scientific_origin=origin, **kw)
    k.created_at = _T0 - timedelta(days=age_days)
    if with_calc:
        calc = make_calculation(
            db_session,
            type=CalculationType.sp,
            species_entry_id=entry.test_reactant_entry_id,
        )
        db_session.add(
            KineticsSourceCalculation(
                kinetics_id=k.id, calculation_id=calc.id, role=KineticsCalculationRole.reactant_energy
            )
        )
    db_session.flush()
    if status is not None:
        set_review(
            db_session,
            record_type=SubmissionRecordType.kinetics,
            record_id=k.id,
            status=status,
        )
    return k


def _read_all(db_session, entry, **kw):
    return get_reaction_kinetics(
        db_session, reaction_entry_id=entry.id, request=KineticsReadRequest(**kw)
    )


def _read_first(db_session, entry, **kw):
    resp = _read_all(db_session, entry, collapse=CollapseMode.first, **kw)
    return resp.records[0].kinetics_id


def _export_first(db_session, entry):
    rs = build_export_record_set(
        db_session,
        seed=SeedSelection(reaction_refs=[entry.public_ref]),
        min_review_status=None,
        collapse=CollapseMode.first,
        selection_policy=SelectionPolicy.default,
    )
    (rr,) = rs.reaction_records
    (sel,) = rr.kinetics
    return sel.kinetics.id


def test_newer_experimental_beats_older_computed_with_same_review(db_session):
    entry = _reaction(db_session)
    computed = _kinetics(
        db_session, entry, origin=ScientificOriginKind.computed, age_days=10, with_calc=True
    )
    experimental = _kinetics(
        db_session, entry, origin=ScientificOriginKind.experimental, age_days=1
    )

    resp = _read_all(db_session, entry)
    by_id = {r.kinetics_id: r for r in resp.records}
    # The checklist still favours the computed record; the test only passes
    # because the score no longer orders the list.
    assert (
        by_id[computed.id].evidence_completeness.score
        > by_id[experimental.id].evidence_completeness.score
    )
    assert [r.kinetics_id for r in resp.records] == [experimental.id, computed.id]
    assert _read_first(db_session, entry) == experimental.id


def test_better_review_status_beats_newer_record(db_session):
    entry = _reaction(db_session)
    approved_old = _kinetics(
        db_session,
        entry,
        origin=ScientificOriginKind.computed,
        age_days=30,
        status=RecordReviewStatus.approved,
    )
    _kinetics(db_session, entry, origin=ScientificOriginKind.experimental, age_days=1)

    assert _read_first(db_session, entry) == approved_old.id


def test_requested_temperature_range_does_not_reorder(db_session):
    """Coverage is displayed, not ranked: a newer record with no range still
    beats an older record that covers the requested range."""
    entry = _reaction(db_session)
    _kinetics(db_session, entry, origin=ScientificOriginKind.computed, age_days=10)
    newer = _kinetics(
        db_session,
        entry,
        origin=ScientificOriginKind.experimental,
        age_days=1,
        tmin_k=None,
        tmax_k=None,
    )

    resp = _read_all(
        db_session,
        entry,
        collapse=CollapseMode.first,
        temperature_min=400.0,
        temperature_max=1000.0,
    )
    assert resp.records[0].kinetics_id == newer.id


def test_created_at_decides_not_id(db_session):
    """The record with the HIGHER id has the OLDER ``created_at``: the newest
    by ``created_at`` wins, in the read and in the export."""
    entry = _reaction(db_session)
    newest_low_id = _kinetics(
        db_session, entry, origin=ScientificOriginKind.computed, age_days=1
    )
    older_high_id = _kinetics(
        db_session, entry, origin=ScientificOriginKind.experimental, age_days=20
    )
    assert older_high_id.id > newest_low_id.id

    assert _read_first(db_session, entry) == newest_low_id.id
    assert _export_first(db_session, entry) == newest_low_id.id


@pytest.mark.parametrize("approve_old", [False, True])
def test_read_and_export_pick_the_same_record(db_session, approve_old):
    entry = _reaction(db_session)
    old = _kinetics(
        db_session,
        entry,
        origin=ScientificOriginKind.computed,
        age_days=10,
        with_calc=True,
        status=RecordReviewStatus.approved if approve_old else None,
    )
    new = _kinetics(db_session, entry, origin=ScientificOriginKind.experimental, age_days=1)

    expected = old.id if approve_old else new.id
    assert _read_first(db_session, entry) == expected
    assert _export_first(db_session, entry) == expected


def test_search_orders_entry_kinetics_by_review_then_newest(db_session):
    entry = _reaction(db_session)
    computed = _kinetics(
        db_session, entry, origin=ScientificOriginKind.computed, age_days=10, with_calc=True
    )
    experimental = _kinetics(
        db_session, entry, origin=ScientificOriginKind.experimental, age_days=1
    )
    resp = search_kinetics(
        db_session, KineticsSearchRequest(reactants=["CC"], products=["CCC"])
    )
    assert [r.kinetics.kinetics_id for r in resp.records] == [
        experimental.id,
        computed.id,
    ]
