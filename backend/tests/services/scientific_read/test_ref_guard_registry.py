"""The ref guard's registry, its one visibility predicate, and its single-statement resolver, against a real database."""

from __future__ import annotations

import pytest
from pydantic import BaseModel
from sqlalchemy import event

from app.db.models.common import (
    CalculationType,
    ProfileRecommendation,
    ReadProfile,
    RecordReviewStatus,
    SubmissionRecordType,
)
from app.services.scientific_read import ref_guard
from app.services.scientific_read.common import visible_statuses
from app.services.scientific_read.handles import status_hidden_under_profile
from app.services.scientific_read.profile import (
    EXPLORATORY,
    ResolvedReadProfile,
    reset_current_read_profile,
    set_current_read_profile,
)
from tests.api.scientific._curated_sweep_world import build_sweep_world
from tests.services.scientific_read._factories import (
    make_calculation,
    make_species,
    make_species_entry,
    next_inchi_key,
    set_review,
)

CURATED = ResolvedReadProfile(profile=ReadProfile.curated, recommendation=ProfileRecommendation.approved_floor_only)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_every_review_vocabulary_term_is_gated_or_explicitly_not():
    """A new reviewable type fails here until someone classifies it.

    Kills: dropping a type from the registry without a reason (m3); adding a ``SubmissionRecordType`` unclassified;
    a type listed as both.
    """
    gated = {g.record_type for g in ref_guard.GATED_TYPES.values()}
    ungated = set(ref_guard.UNGATED_REVIEWABLE)
    assert not gated & ungated
    assert gated | ungated == set(SubmissionRecordType)
    assert all(reason for reason in ref_guard.UNGATED_REVIEWABLE.values())


def test_gated_prefixes_come_from_the_public_ref_registry_and_the_handle_gate():
    """Kills: a hand-typed prefix that drifts from ``public_refs.PREFIXES`` or from the handle gate's set."""
    from app.services import public_refs
    from app.services.scientific_read import handles

    for prefix, gated in ref_guard.GATED_TYPES.items():
        assert public_refs.PREFIXES[gated.model.__name__] == prefix
    handle_gated = set(handles._REVIEWABLE_HANDLE_TYPES) | set(handles._PARENT_DERIVED_HANDLE_TYPES)
    assert handle_gated <= {g.model for g in ref_guard.GATED_TYPES.values()}
    # Owner decision D3: records with no detail route are gated too.
    assert {"thm", "kin", "mpo"} <= set(ref_guard.GATED_TYPES)


def test_the_ref_pattern_matches_exactly_a_gated_prefix_and_a_26_character_body():
    """Kills: a pattern that matches an ungated ref (``lot_`` is a fact), prose, or a wrong-length body."""
    body = "a2" * 13
    assert all(ref_guard.GATED_REF_RE.match(f"{p}_{body}") for p in ref_guard.GATED_TYPES)
    for text in (
        f"lot_{body}",
        f"geom_{body}",
        f"calc_{body}x",
        f"calc_{body[:-1]}",
        f"CALC_{body}",
        f" calc_{body}",
        f"see calc_{body}",
    ):
        assert not ref_guard.GATED_REF_RE.match(text), text
    # `nkin_` must not be read as `n` + ... and `nsolve_` must not lose to `net_`: longest prefix first.
    assert ref_guard.GATED_REF_RE.match(f"nsolve_{body}") and ref_guard.GATED_REF_RE.match(f"net_{body}")


def test_pointer_models_are_live_and_never_a_response_subject():
    """Kills: a dead pointer entry (no ref-bearing field); a pointer used as ``record`` / ``records[*]``, which the
    guard would then refuse to delete."""
    for model in ref_guard.POINTER_MODELS:
        assert issubclass(model, BaseModel)
        # ``SupersessionNotice`` holds its refs in ``superseded_by`` / ``current`` and ``GeometryIdentityOwnerRef`` in
        # ``ref``: none is ``*_ref`` named.
        # A wrapper (``ConformerObservationSiblingSummary``) holds its ref one block down.
        assert any(
            name.endswith(("_ref", "_refs")) or name in {"superseded_by", "current", "ref"}
            for holder in (model, *_nested_models(model))
            for name in holder.model_fields
        ), model.__name__

    def all_models(cls):
        for sub in cls.__subclasses__():
            yield sub
            yield from all_models(sub)

    subjects = set()
    for model in set(all_models(BaseModel)):
        if not model.__module__.startswith("app.schemas.reads"):
            continue
        for field_name in ("record", "records"):
            info = model.model_fields.get(field_name)
            if info is not None:
                subjects |= set(_model_classes(info.annotation))
    assert subjects, "found no response envelopes: the scan itself is broken"
    # One model is both a subject and a nested block: a TS entry record is the subject of the entry detail, the browse
    # and the search, and also an element of another record's ``entries``. The planner never deletes a subject
    # position (``_is_subject``), so registering it only ever acts on the nested one; it is named here so a second
    # model cannot join it unnoticed.
    from app.schemas.reads.scientific_transition_state import ScientificTransitionStateEntryRecord

    both = {ScientificTransitionStateEntryRecord}
    assert both <= subjects & ref_guard.POINTER_MODELS, "the allowance names a model that is no longer both"
    assert not (subjects & ref_guard.POINTER_MODELS) - both, sorted(
        m.__name__ for m in (subjects & ref_guard.POINTER_MODELS) - both
    )


def _nested_models(model):
    for info in model.model_fields.values():
        yield from _model_classes(info.annotation)


def _model_classes(annotation):
    from typing import get_args

    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        yield annotation
    for arg in get_args(annotation):
        yield from _model_classes(arg)


# ---------------------------------------------------------------------------
# The one predicate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("profile", [EXPLORATORY, CURATED], ids=["exploratory", "curated"])
def test_the_rank_form_and_the_set_form_of_hidden_agree_for_every_status(profile):
    """The predicate is written once; the SQL-set form that other services still use must agree with it.

    Kills: a comparison off by one (``>=``), a swapped rank, a profile that is ignored.
    """
    token = set_current_read_profile(profile)
    try:
        visible = visible_statuses(min_review_status=None, include_rejected=True, include_deprecated=True)
        for status in RecordReviewStatus:
            assert status_hidden_under_profile(status) == (status not in visible), status
            assert status_hidden_under_profile(status, profile=profile) == (status not in visible), status
    finally:
        reset_current_read_profile(token)
    expected_hidden = set(RecordReviewStatus) - {RecordReviewStatus.approved} if profile is CURATED else set()
    assert {s for s in RecordReviewStatus if status_hidden_under_profile(s, profile=profile)} == expected_hidden


# ---------------------------------------------------------------------------
# The resolver
# ---------------------------------------------------------------------------


def _count(db_session, call):
    seen: list[str] = []

    def hook(conn, cursor, statement, *a):
        seen.append(statement)

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", hook)
    try:
        result = call()
    finally:
        event.remove(bind, "before_cursor_execute", hook)
    return result, len(seen)


@pytest.mark.parametrize("status", list(RecordReviewStatus), ids=lambda s: s.value)
def test_a_calculation_is_hidden_unless_approved_and_a_missing_row_is_not_reviewed(client, db_session, status):
    """Kills: reading a missing review row as visible; treating under_review/rejected/deprecated as visible."""
    entry = make_species_entry(db_session, make_species(db_session, inchi_key=next_inchi_key("RGR")))
    with_row = make_calculation(db_session, type=CalculationType.opt, species_entry_id=entry.id)
    no_row = make_calculation(db_session, type=CalculationType.opt, species_entry_id=entry.id)
    set_review(db_session, record_type=SubmissionRecordType.calculation, record_id=with_row.id, status=status)
    refs = [with_row.public_ref, no_row.public_ref]
    hidden = ref_guard.hidden_refs(db_session, refs, profile=CURATED)
    assert (with_row.public_ref in hidden) == (status is not RecordReviewStatus.approved)
    assert no_row.public_ref in hidden
    assert ref_guard.hidden_refs(db_session, refs, profile=EXPLORATORY) == set()


def test_every_gated_type_is_resolved_in_one_statement_and_exploratory_costs_none(client, db_session):
    """Kills: one statement per prefix; resolving under exploratory; a gated type the resolver cannot answer for
    (including ``nkin``, reviewed through its solve)."""
    world = build_sweep_world(db_session)
    hidden_refs = {row.public_ref for row in world.hidden.values()}
    visible_refs = {row.public_ref for row in world.visible.values()}
    assert len({world.kind_of(k) for k in world.hidden}) == len(ref_guard.GATED_TYPES)

    hidden, statements = _count(
        db_session, lambda: ref_guard.hidden_refs(db_session, hidden_refs | visible_refs, profile=CURATED)
    )
    assert hidden == hidden_refs
    assert statements == 1

    none, statements = _count(
        db_session, lambda: ref_guard.hidden_refs(db_session, hidden_refs | visible_refs, profile=EXPLORATORY)
    )
    assert none == set() and statements == 0

    nothing, statements = _count(db_session, lambda: ref_guard.hidden_refs(db_session, [], profile=CURATED))
    assert nothing == set() and statements == 0


def test_a_network_kinetics_fit_follows_its_solve(client, db_session):
    """Kills: reading nkin's own (absent) review row, which would call every fit hidden, or every fit visible."""
    world = build_sweep_world(db_session)
    nkin_h, nkin_v = world.hidden["nkin_h"], world.visible["nkin_v"]
    assert ref_guard.hidden_refs(db_session, [nkin_h.public_ref, nkin_v.public_ref], profile=CURATED) == {
        nkin_h.public_ref
    }


# ---------------------------------------------------------------------------
# What the profile dependency publishes
# ---------------------------------------------------------------------------


def test_the_hook_sees_the_route_s_own_session_and_route_template(client, db_session, monkeypatch):
    """Pins the two runtime premises: ``scope["route"]`` is populated while dependencies resolve, and the session the
    guard is handed is the route's own (FastAPI caches ``get_db`` per request), not a new one.

    Kills: publishing a different session; publishing the concrete URL instead of the template; never resetting.
    """
    from sqlalchemy.orm import Session

    from app.api.deps import get_db
    from app.services.scientific_read import internal_ids

    world = build_sweep_world(db_session)
    # The harness's ``get_db`` hands every caller one shared session, which would make the identity check below
    # unfailable. Give each call of the dependency a fresh session instead (FastAPI calls it once per request).
    created: list[Session] = []

    def fresh_session():
        session = Session(bind=db_session.get_bind(), expire_on_commit=False, join_transaction_mode="create_savepoint")
        created.append(session)
        yield session

    previous = client.app.dependency_overrides[get_db]
    client.app.dependency_overrides[get_db] = fresh_session
    seen = []
    real = internal_ids.withhold_for_current_request

    def spy(payload):
        ctx = ref_guard.current_ref_guard_context()
        seen.append((ctx.session, ctx.route_path))
        return real(payload)

    monkeypatch.setattr(internal_ids, "withhold_for_current_request", spy)
    ref = world.visible["calc_vopt"].public_ref
    try:
        assert client.get(f"/api/v1/scientific/calculations/{ref}", params={"profile": "curated"}).status_code == 200
    finally:
        client.app.dependency_overrides[get_db] = previous
    assert len(seen) == 1
    session, route_path = seen[0]
    assert len(created) == 1  # one session for the whole request: the route and the profile dependency share it
    assert session is created[0]
    assert session is not db_session
    assert route_path == "/api/v1/scientific/calculations/{calculation_ref_or_id}"
    assert ref_guard.current_ref_guard_context() is None  # reset when the request ended


def test_every_pointer_field_names_a_real_nullable_untyped_field():
    """A typo in ``POINTER_FIELDS`` would silently register nothing.

    Kills: a misspelt field name; a registered field that is typed (it would have a pointer model instead) or cannot
    be nulled.
    """
    from app.services.scientific_read.ref_guard import _field_traits

    assert ref_guard.POINTER_FIELDS
    for model, name in ref_guard.POINTER_FIELDS:
        assert name in model.model_fields, f"{model.__name__}.{name} does not exist"
        nullable, untyped = _field_traits(model, name)
        assert nullable, f"{model.__name__}.{name} is not nullable"
        info = model.model_fields[name]
        assert "dict" in str(info.annotation).lower(), f"{model.__name__}.{name} is not an untyped dict block"
