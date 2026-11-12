"""The calculation dependency graph is acyclic, and the database says so.

A row trigger on ``calculation_dependency`` refuses an
edge whose parent is already reachable from its child. These tests cover the
trigger itself (two- and three-edge cycles, diamonds, self-edges, UPDATE, bulk
inserts, the accepted-science ordering), its mapping to the coded 409, and a real
two-connection race.
"""

from __future__ import annotations

import itertools
import time

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from starlette.requests import Request

from app.api.errors import _integrity_error_handler
from app.db.models.calculation import CalculationDependency
from app.db.models.common import CalculationDependencyRole as Role
from app.db.models.common import CalculationType, SubmissionRecordType
from tests.db.test_accepted_science_immutability import _actor, _approve
from tests.services.scientific_read._factories import (
    make_calculation,
    next_inchi_key,
    unique_smiles,
)

_LOCK_TEXT = "calculation_dependency:acyclic"


def _calcs(session, n, label="ACYC"):
    # The species and the entry are inserted by SQL, not through the ORM.
    species_id = session.scalar(
        text(
            "INSERT INTO species (kind, smiles, inchi_key, charge, multiplicity, stereo_kind) VALUES "
            "(CAST('molecule' AS molecule_kind), :s, :k, 0, 1, CAST('achiral' AS stereo_kind)) RETURNING id"
        ),
        {"s": unique_smiles(session), "k": next_inchi_key(label)},
    )
    entry_id = session.scalar(
        text("INSERT INTO species_entry (species_id, public_ref) VALUES (:s, :r) RETURNING id"),
        {"s": species_id, "r": f"spe_acyc_{species_id}_{label.lower()}"[:30]},
    )
    return [make_calculation(session, species_entry_id=entry_id, type=CalculationType.opt) for _ in range(n)]


def _edge(session, parent, child, role=Role.arkane_source):
    session.add(
        CalculationDependency(
            parent_calculation_id=parent.id, child_calculation_id=child.id, dependency_role=role
        )
    )
    session.flush()


def _refused(session, parent, child, role=Role.arkane_source):
    """Add an edge inside a savepoint and return the IntegrityError it raises."""
    with pytest.raises(IntegrityError) as caught, session.begin_nested():
        _edge(session, parent, child, role)
    return caught.value


# -- the trigger --------------------------------------------------------------


def test_two_edge_cycle_is_refused(db_session):
    a, b = _calcs(db_session, 2)
    _edge(db_session, a, b)
    exc = _refused(db_session, b, a)
    assert exc.orig.diag.constraint_name == "calculation_dependency_acyclic"
    assert exc.orig.sqlstate == "23514"


def test_three_edge_cycle_is_refused(db_session):
    a, b, c = _calcs(db_session, 3)
    _edge(db_session, a, b)
    _edge(db_session, b, c)
    exc = _refused(db_session, c, a)
    assert exc.orig.diag.constraint_name == "calculation_dependency_acyclic"


def test_diamond_is_accepted(db_session):
    a, b, c, d = _calcs(db_session, 4)
    for parent, child in ((a, b), (a, c), (b, d), (c, d)):
        _edge(db_session, parent, child)
    assert db_session.scalar(text("SELECT count(*) FROM calculation_dependency WHERE parent_calculation_id IN (:a,:b,:c)"),
                             {"a": a.id, "b": b.id, "c": c.id}) == 4


def test_self_edge_is_still_refused_by_the_check_constraint(db_session):
    (a,) = _calcs(db_session, 1)
    exc = _refused(db_session, a, a)
    assert exc.orig.diag.constraint_name == "ck_calculation_dependency_not_self"


def test_error_names_the_calculations_by_public_ref_and_no_row_ids(db_session):
    a, b = _calcs(db_session, 2)
    _edge(db_session, a, b)
    exc = _refused(db_session, b, a)
    detail = exc.orig.diag.message_detail
    assert detail == f"parent={b.public_ref};child={a.public_ref}"
    assert str(a.id) not in detail.replace(a.public_ref, "").replace(b.public_ref, "")


def test_update_of_both_endpoints_that_closes_a_cycle_is_refused(db_session):
    a, b, c = _calcs(db_session, 3)
    _edge(db_session, a, b)
    _edge(db_session, b, c)
    _edge(db_session, a, c)  # a legal diamond edge, re-pointed below to (c, a)
    with pytest.raises(IntegrityError) as caught, db_session.begin_nested():
        db_session.execute(
            text(
                "UPDATE calculation_dependency SET parent_calculation_id = :c, child_calculation_id = :a "
                "WHERE parent_calculation_id = :a AND child_calculation_id = :c"
            ),
            {"a": a.id, "c": c.id},
        )
    assert caught.value.orig.diag.constraint_name == "calculation_dependency_acyclic"


def test_update_of_one_endpoint_alone_is_covered(db_session):
    a, b, c = _calcs(db_session, 3)
    _edge(db_session, a, b)
    _edge(db_session, b, c)
    (d,) = _calcs(db_session, 1, "ACYCD")
    _edge(db_session, d, a, Role.freq_on)
    # (d, a) -> (c, a): parent changes only; c is reachable from a, so this closes a->b->c->a.
    with pytest.raises(IntegrityError) as caught, db_session.begin_nested():
        db_session.execute(
            text(
                "UPDATE calculation_dependency SET parent_calculation_id = :c "
                "WHERE parent_calculation_id = :d AND child_calculation_id = :a"
            ),
            {"c": c.id, "d": d.id, "a": a.id},
        )
    assert caught.value.orig.diag.constraint_name == "calculation_dependency_acyclic"


def test_role_only_update_is_allowed(db_session):
    a, b = _calcs(db_session, 2)
    _edge(db_session, a, b, Role.arkane_source)
    db_session.execute(
        text("UPDATE calculation_dependency SET dependency_role = 'freq_on' "
             "WHERE parent_calculation_id = :a AND child_calculation_id = :b"),
        {"a": a.id, "b": b.id},
    )
    assert db_session.scalar(
        text("SELECT dependency_role::text FROM calculation_dependency WHERE parent_calculation_id = :a"),
        {"a": a.id},
    ) == "freq_on"


def test_a_realistic_chain_of_fifty_edges_is_fast_and_a_back_edge_is_still_refused(db_session):
    calcs = _calcs(db_session, 51, "ACYCCHAIN")
    started = time.perf_counter()
    for parent, child in itertools.pairwise(calcs):
        db_session.add(
            CalculationDependency(
                parent_calculation_id=parent.id, child_calculation_id=child.id, dependency_role=Role.arkane_source
            )
        )
    db_session.flush()  # one flush: every row-level trigger sees the earlier rows of the batch
    elapsed = time.perf_counter() - started
    print(f"50-edge chain inserted in {elapsed:.3f}s")
    assert elapsed < 5.0
    exc = _refused(db_session, calcs[-1], calcs[0])
    assert exc.orig.diag.constraint_name == "calculation_dependency_acyclic"


def test_the_trigger_takes_the_documented_advisory_lock(db_session):
    a, b = _calcs(db_session, 2)
    key = db_session.scalar(text("SELECT hashtextextended(:k, 0)"), {"k": _LOCK_TEXT})
    held = "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND pid = pg_backend_pid() AND granted"
    assert db_session.scalar(text(held)) == 0
    _edge(db_session, a, b)
    row = db_session.execute(
        text("SELECT classid, objid FROM pg_locks WHERE locktype = 'advisory' AND pid = pg_backend_pid()")
    ).one()
    assert (row.classid << 32) | row.objid == key & 0xFFFFFFFFFFFFFFFF


def test_trigger_does_not_mask_or_hide_the_accepted_science_refusal(db_session):
    """Both guards stay live on the same table: an approved pair refuses a plain edge as immutable, and
    an unapproved pair refuses a cycle as a cycle."""
    actor = _actor(db_session)
    a, b = _calcs(db_session, 2, "ACYCAS")
    for calc in (a, b):
        _approve(db_session, SubmissionRecordType.calculation, calc.id, actor)
    with pytest.raises(DBAPIError) as caught, db_session.begin_nested():
        _edge(db_session, a, b)
    assert "calculation_dependency_cycle" not in str(caught.value)
    assert "is immutable" in str(caught.value)
    message = str(caught.value.orig)
    assert a.public_ref in message or b.public_ref in message, "the guard names the record by public ref"
    assert f"record {a.id} " not in message and f"record {b.id} " not in message, "and never by row id"
    c, d = _calcs(db_session, 2, "ACYCAS2")
    _edge(db_session, c, d)
    assert _refused(db_session, d, c).orig.diag.constraint_name == "calculation_dependency_acyclic"


# -- the coded refusal --------------------------------------------------------


def test_integrity_handler_maps_the_trigger_to_the_coded_409(db_session):
    import json

    a, b = _calcs(db_session, 2)
    _edge(db_session, a, b)
    exc = _refused(db_session, b, a)
    request = Request({"type": "http", "method": "POST", "path": "/x", "headers": [], "query_string": b""})
    response = _integrity_error_handler(request, exc)
    body = json.loads(response.body)
    assert response.status_code == 409
    assert body["code"] == "calculation_dependency_cycle"
    assert body["category"] == "integrity_error"
    assert body["context"] == {"parent_calculation": b.public_ref, "child_calculation": a.public_ref}
    assert b.public_ref in body["detail"] and a.public_ref in body["detail"]
    assert "calculation_dependency_acyclic" not in body["detail"]  # raw constraint names stay out of bodies


# -- a real race --------------------------------------------------------------


# -- the walk terminates, and a legal reversal is not mistaken for a cycle ---


def test_the_walk_terminates_next_to_a_stored_ring(db_session):
    """Only a ring that was stored with the row trigger disabled (below, inside this test's transaction)
    can exercise the walk's termination; the trigger itself never lets one be written.

    With ``UNION ALL`` the recursive walk would never end: ``statement_timeout`` turns that into an error
    instead of a hung test."""
    a, b, c = _calcs(db_session, 3, "ACYCRING")
    db_session.execute(text("ALTER TABLE calculation_dependency DISABLE TRIGGER trg_calculation_dependency_acyclic"))
    try:
        _edge(db_session, a, b)
        _edge(db_session, b, a, Role.freq_on)
    finally:
        db_session.execute(text("ALTER TABLE calculation_dependency ENABLE TRIGGER trg_calculation_dependency_acyclic"))
    db_session.execute(text("SET LOCAL statement_timeout = '5s'"))
    _edge(db_session, c, a, Role.single_point_on)  # reaches the ring, closes nothing: accepted
    exc = _refused(db_session, a, c, Role.scan_parent)  # a -> c with c -> a: a real cycle, refused rather than looped
    assert exc.orig.diag.constraint_name == "calculation_dependency_acyclic"


def test_reversing_a_single_edge_by_update_is_legal(db_session):
    a, b = _calcs(db_session, 2, "ACYCREV")
    _edge(db_session, a, b)
    db_session.execute(
        text(
            "UPDATE calculation_dependency SET parent_calculation_id = :b, child_calculation_id = :a "
            "WHERE parent_calculation_id = :a AND child_calculation_id = :b"
        ),
        {"a": a.id, "b": b.id},
    )
    assert db_session.scalar(
        text("SELECT count(*) FROM calculation_dependency WHERE parent_calculation_id = :b AND child_calculation_id = :a"),
        {"a": a.id, "b": b.id},
    ) == 1


# -- the deadlock schedule ----------------------------------------------------
