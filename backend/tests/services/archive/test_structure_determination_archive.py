"""An archive that holds structure determinations and their sources restores, and the protection survives it.

``restore_archive`` inserts every row with the database's triggers firing. The source rows are pinned to the
transaction that creates their determination (``tckdb_structure_source_insert_guard``), so a restore that did not say
which determinations it re-creates would be refused by the very guard that protects them. A restore says it for
exactly the determinations it carries, and then stops saying it. The accepted-science guards need no such word: a
restore inserts the review rows after the science they accept (the existing mechanism), and these tests pin that
the determination tables ride it too, under an approved transition state entry and an approved conformer
observation as well as under an owner nobody has accepted.
"""

from __future__ import annotations

import io

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db.models.common import SubmissionRecordType
from app.services.archive import restore_archive, write_archive
from tests.db.test_accepted_science_immutability import _actor, _approve
from tests.db.test_structure_determination_migration import (
    _entry,
    _insert_determination,
    _insert_source,
    _ts_entry,
)
from tests.services.archive.test_archive import _empty_archive_tables
from tests.services.scientific_read._factories import (
    make_calculation,
    make_conformer_group,
    make_conformer_observation,
)

SOURCE_COLUMNS = "determination_id, role, calculation_id"


def _round_trip(session) -> None:
    archive = io.BytesIO()
    write_archive(session, archive)
    frozen = archive.getvalue()
    _empty_archive_tables(session)
    assert session.scalar(text("SELECT count(*) FROM structure_determination")) == 0
    assert session.scalar(text("SELECT count(*) FROM structure_determination_source")) == 0
    restore_archive(session, io.BytesIO(frozen))
    session.expunge_all()


def _sources(session, determination_id: int) -> list[tuple[str, int]]:
    return sorted(
        (role, calc)
        for role, calc in session.execute(
            text("SELECT role::text, calculation_id FROM structure_determination_source WHERE determination_id = :d"),
            {"d": determination_id},
        ).all()
    )


def _assert_pin_is_closed(session, determination_id: int, calculation_id: int, *, match: str = "structure_determination_sources_are_pinned_at_creation", **owner) -> None:
    """The restore did not leave the pin open: a source added now is refused (under an accepted owner, by the
    accepted-science guard that fires first)."""
    with pytest.raises(DBAPIError, match=match), session.begin_nested():
        session.execute(
            text(
                "INSERT INTO structure_determination_source (determination_id, role, calculation_id, species_entry_id, "
                "transition_state_entry_id, conformer_observation_id) VALUES (:d, 'curvature', :c, :s, :t, :o)"
            ),
            {"d": determination_id, "c": calculation_id, "s": owner.get("s"), "t": owner.get("t"), "o": owner.get("o")},
        )


def _two_determinations(session, entry):
    """Two determinations of one species entry, each pinning two calculations."""
    made = []
    for key in ("one", "two"):
        det = _insert_determination(session, species=entry.id, key=key, quantity="electronic_energy")
        energy = make_calculation(session, species_entry_id=entry.id)
        opt = make_calculation(session, species_entry_id=entry.id)
        _insert_source(session, det, energy, role="energy")
        _insert_source(session, det, opt, role="geometry_optimization")
        made.append((det, energy, opt))
    return made


def test_determinations_and_sources_under_an_unaccepted_owner_survive_a_round_trip(db_session):
    entry = _entry(db_session)
    made = _two_determinations(db_session, entry)
    expected = {det: _sources(db_session, det) for det, _, _ in made}
    assert all(len(v) == 2 for v in expected.values())
    _round_trip(db_session)
    assert {det: _sources(db_session, det) for det in expected} == expected
    det, energy, _ = made[0]
    _assert_pin_is_closed(db_session, det, energy.id, s=entry.id)


def test_a_restore_opens_only_the_determinations_it_carries(db_session):
    entry = _entry(db_session)
    (det, energy, _), _ = _two_determinations(db_session, entry)
    _round_trip(db_session)
    # A determination created after the restore is pinned like any other: its sources need their own marker.
    later = _insert_determination(db_session, species=entry.id, key="later", quantity="electronic_energy")
    extra = make_calculation(db_session, species_entry_id=entry.id)
    _assert_pin_is_closed(db_session, later, extra.id, s=entry.id)
    _assert_pin_is_closed(db_session, det, energy.id, s=entry.id)


def test_determinations_under_an_approved_transition_state_entry_survive_and_stay_protected(db_session):
    ts = _ts_entry(db_session)
    det = _insert_determination(db_session, ts=ts.id, kind="saddle_point", key="saddle", quantity="electronic_energy")
    energy = make_calculation(db_session, transition_state_entry_id=ts.id)
    opt = make_calculation(db_session, transition_state_entry_id=ts.id)
    _insert_source(db_session, det, energy, role="energy")
    _insert_source(db_session, det, opt, role="geometry_optimization")
    _approve(db_session, SubmissionRecordType.transition_state_entry, ts.id, _actor(db_session))
    expected = _sources(db_session, det)
    _round_trip(db_session)
    assert _sources(db_session, det) == expected
    # The entry came back accepted, so what it accepts is still immutable: the guards work after a restore.
    with pytest.raises(DBAPIError, match="accepted transition_state_entry record .* is immutable"), db_session.begin_nested():
        db_session.execute(text("DELETE FROM structure_determination WHERE id = :i"), {"i": det})
    with pytest.raises(DBAPIError, match="accepted transition_state_entry record .* is immutable"), db_session.begin_nested():
        _insert_determination(db_session, ts=ts.id, kind="saddle_point", key="another")
    _assert_pin_is_closed(db_session, det, energy.id, match="accepted transition_state_entry record .* is immutable", t=ts.id)


def test_determinations_under_an_approved_conformer_observation_survive_and_stay_protected(db_session):
    entry = _entry(db_session)
    obs = make_conformer_observation(db_session, conformer_group=make_conformer_group(db_session, entry))
    det = _insert_determination(
        db_session, species=entry.id, kind="conformer_basin", obs=obs.id, key="basin", quantity="electronic_energy"
    )
    energy = make_calculation(db_session, species_entry_id=entry.id, conformer_observation_id=obs.id)
    opt = make_calculation(db_session, species_entry_id=entry.id, conformer_observation_id=obs.id)
    _insert_source(db_session, det, energy, role="energy", obs=obs.id)
    _insert_source(db_session, det, opt, role="geometry_optimization", obs=obs.id)
    _approve(db_session, SubmissionRecordType.conformer_observation, obs.id, _actor(db_session))
    expected = _sources(db_session, det)
    _round_trip(db_session)
    assert _sources(db_session, det) == expected
    with pytest.raises(DBAPIError, match="accepted conformer_observation record .* is immutable"), db_session.begin_nested():
        db_session.execute(text("DELETE FROM structure_determination WHERE id = :i"), {"i": det})
    _assert_pin_is_closed(
        db_session, det, energy.id, match="accepted conformer_observation record .* is immutable", s=entry.id, o=obs.id
    )
