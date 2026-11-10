"""Chunk 6.2: statmech search filters by transition-state entry and by declared basin.

Design: ``statmech_chunk6_design_2026-10-12.md`` (PR 6.2), owner decision D3.
Each test names the mutation it kills. Before this PR ``transition_state_entry_ref``
was silently dropped (a plain ``BaseModel`` ignores unknown fields; an unknown GET
query parameter is ignored), so a caller got unfiltered results.
"""

from __future__ import annotations

import json

from app.db.models.common import (
    RecordReviewStatus,
    ScientificOriginKind,
    StatmechModelScopeKind,
    StatmechTreatmentKind,
    SubmissionRecordType,
)
from app.db.models.statmech import Statmech
from app.db.models.statmech_declaration import StatmechModelDeclaration, StatmechModelMember
from tests.services.scientific_read._factories import (
    make_chem_reaction,
    make_conformer_group,
    make_literature,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_statmech,
    make_transition_state,
    make_transition_state_entry,
    next_inchi_key,
    set_review,
)

SEARCH = "/api/v1/scientific/statmech/search"


def _refs(body) -> set[str]:
    return {r["statmech"]["statmech_ref"] for r in body["records"]}


def _entry(db_session, tag="TSBF"):
    species = make_species(db_session, inchi_key=next_inchi_key(tag))
    return make_species_entry(db_session, species)


def _ts_entry(db_session):
    r1, r2, p = _entry(db_session, "TSR1"), _entry(db_session, "TSR2"), _entry(db_session, "TSP")
    reaction = make_chem_reaction(
        db_session, reactants=[r1.species, r2.species], products=[p.species], reversible=False
    )
    rxn_entry = make_reaction_entry(
        db_session, reaction=reaction, reactant_entries=[r1, r2], product_entries=[p]
    )
    return make_transition_state_entry(
        db_session,
        transition_state=make_transition_state(db_session, reaction_entry=rxn_entry),
        multiplicity=2,
    )


def _ts_statmech(db_session, ts_entry, kind=StatmechTreatmentKind.rrho):
    sm = Statmech(
        transition_state_entry_id=ts_entry.id,
        scientific_origin=ScientificOriginKind.computed,
        statmech_treatment=kind,
    )
    db_session.add(sm)
    db_session.flush()
    return sm


def _declare(db_session, sm, *, scope, group=None):
    decl = StatmechModelDeclaration(
        statmech_id=sm.id,
        schema_version=1,
        scope_kind=scope,
        conformer_group_id=group.id if group is not None else None,
        literature_id=make_literature(db_session).id,
        recipe={},
        content_hash="a" * 64,
    )
    db_session.add(decl)
    db_session.flush()
    return decl


def _world(db_session):
    ts = _ts_entry(db_session)
    ts_sm = _ts_statmech(db_session, ts)
    entry = _entry(db_session)
    sp_sm = make_statmech(db_session, species_entry=entry)
    other_ts_sm = _ts_statmech(db_session, _ts_entry(db_session))
    return ts, ts_sm, entry, sp_sm, other_ts_sm


def test_ts_ref_returns_only_the_ts_owned_record(client, db_session):
    """Kills: the filter not being applied (today's behaviour)."""
    ts, ts_sm, _, sp_sm, other_ts_sm = _world(db_session)
    for body in (
        client.get(SEARCH, params={"transition_state_entry_ref": ts.public_ref, "model_kind": "rrho"}).json(),
        client.post(SEARCH, json={"transition_state_entry_ref": ts.public_ref, "model_kind": "rrho"}).json(),
    ):
        assert _refs(body) == {ts_sm.public_ref}
        assert body["request"]["filter"]["transition_state_entry_ref"] == ts.public_ref


def test_ts_ref_alone_is_a_meaningful_filter(client, db_session):
    """Kills: the field missing from ``_MEANINGFUL_FILTER_FIELDS``."""
    ts, ts_sm, *_ = _world(db_session)
    r = client.post(SEARCH, json={"transition_state_entry_ref": ts.public_ref})
    assert r.status_code == 200, r.text
    assert _refs(r.json()) == {ts_sm.public_ref}


def test_species_entry_ref_in_the_ts_slot_is_a_type_mismatch(client, db_session):
    """Kills: resolving the ref against the wrong model."""
    _, _, entry, *_ = _world(db_session)
    r = client.post(SEARCH, json={"transition_state_entry_ref": entry.public_ref})
    assert r.status_code == 422
    assert "handle_type_mismatch" in r.text


def test_owner_filters_and_combine_into_an_empty_page(client, db_session):
    """Kills: OR-combining the owner filters."""
    ts, ts_sm, entry, sp_sm, _ = _world(db_session)
    body = client.post(
        SEARCH,
        json={"transition_state_entry_ref": ts.public_ref, "species_entry_ref": entry.public_ref},
    ).json()
    assert body["records"] == []
    assert body["request"]["filter"]["transition_state_entry_ref"] == ts.public_ref
    assert body["request"]["filter"]["species_entry_ref"] == entry.public_ref


def test_unknown_ts_ref_is_an_empty_page_like_every_sibling_filter(client, db_session):
    """Kills: a well-formed unknown ref falling through unfiltered."""
    _world(db_session)
    body = client.post(SEARCH, json={"transition_state_entry_ref": "tse_" + "0" * 12}).json()
    assert body["records"] == []


def _basins(db_session):
    entry = _entry(db_session)
    a = make_conformer_group(db_session, entry, label="basin_a")
    b = make_conformer_group(db_session, entry, label="basin_b")
    sm_a = make_statmech(db_session, species_entry=entry)
    sm_b = make_statmech(db_session, species_entry=entry)
    undeclared = make_statmech(db_session, species_entry=entry)
    _declare(db_session, sm_a, scope=StatmechModelScopeKind.basin, group=a)
    _declare(db_session, sm_b, scope=StatmechModelScopeKind.basin, group=b)
    return entry, a, b, sm_a, sm_b, undeclared


def test_declared_filter_is_exact_and_legacy_filter_is_unchanged(client, db_session):
    """Kills: renaming context into membership; matching by owning entry; matching undeclared rows."""
    _, a, b, sm_a, sm_b, undeclared = _basins(db_session)
    everything = {sm_a.public_ref, sm_b.public_ref, undeclared.public_ref}

    legacy = client.get(SEARCH, params={"conformer_group_ref": a.public_ref}).json()
    assert _refs(legacy) == everything

    declared = client.get(SEARCH, params={"declared_conformer_group_ref": a.public_ref}).json()
    assert _refs(declared) == {sm_a.public_ref}
    assert declared["request"]["filter"]["declared_conformer_group_ref"] == a.public_ref
    posted = client.post(SEARCH, json={"declared_conformer_group_ref": b.public_ref}).json()
    assert _refs(posted) == {sm_b.public_ref}


def test_ensemble_member_in_a_basin_does_not_match_the_declared_filter(client, db_session):
    """Kills: widening to member assignment without an owner decision (D3)."""
    entry, a, b, sm_a, sm_b, undeclared = _basins(db_session)
    ensemble = make_statmech(db_session, species_entry=entry)
    decl = _declare(db_session, ensemble, scope=StatmechModelScopeKind.explicit_equilibrium_ensemble)
    db_session.add(
        StatmechModelMember(
            declaration_id=decl.id, member_key="m1", member_statmech_id=undeclared.id, conformer_group_id=a.id
        )
    )
    db_session.flush()
    body = client.post(SEARCH, json={"declared_conformer_group_ref": a.public_ref}).json()
    assert _refs(body) == {sm_a.public_ref}
    assert ensemble.public_ref not in _refs(body)


def test_get_and_post_agree_for_the_new_filters(client, db_session):
    """Kills: a filter wired on one verb only."""
    ts, ts_sm, *_ = _world(db_session)
    _, a, *_ = _basins(db_session)
    for field, ref in (("transition_state_entry_ref", ts.public_ref), ("declared_conformer_group_ref", a.public_ref)):
        got = client.get(SEARCH, params={field: ref}).json()
        posted = client.post(SEARCH, json={field: ref}).json()
        assert _refs(got) == _refs(posted)
        assert _refs(got), field


# --- curated disclosure: a filter is not a subject ---------------------------


def _masked(response, ref: str) -> str:
    """The whole body with the caller-supplied ref masked (it is echoed by design)."""
    assert response.status_code == 200, response.text
    return response.text.replace(ref, "<REF>")


def _both_verbs(client, field: str, ref: str):
    params = {field: ref, "profile": "curated"}
    return (
        client.get(SEARCH, params=params),
        client.post(SEARCH, params={"profile": "curated"}, json={field: ref}),
    )


def test_curated_hidden_ts_entry_is_indistinguishable_from_an_unknown_ref(client, db_session):
    """Kills: a filter that discloses a hidden TS entry (404/422/differing body) or 404-gates it."""
    ts = _ts_entry(db_session)
    _ts_statmech(db_session, ts)  # not reviewed: hidden under curated
    unknown = "tse_" + "0" * 24
    for hidden, missing in zip(
        _both_verbs(client, "transition_state_entry_ref", ts.public_ref),
        _both_verbs(client, "transition_state_entry_ref", unknown),
        strict=True,
    ):
        assert _masked(hidden, ts.public_ref) == _masked(missing, unknown)
        assert hidden.json()["records"] == []


def test_curated_hidden_basin_is_indistinguishable_from_an_unknown_ref(client, db_session):
    """Kills: the declared-basin filter disclosing a hidden basin."""
    _, a, *_ = _basins(db_session)  # declared records exist but are not approved
    unknown = "cg_" + "0" * 24
    for hidden, missing in zip(
        _both_verbs(client, "declared_conformer_group_ref", a.public_ref),
        _both_verbs(client, "declared_conformer_group_ref", unknown),
        strict=True,
    ):
        assert _masked(hidden, a.public_ref) == _masked(missing, unknown)
        assert hidden.json()["records"] == []


def test_curated_approved_statmech_under_a_not_approved_ts_entry_is_returned(client, db_session):
    """Kills: gating the filter subject (the TS entry's review) instead of the returned record's own floor.

    ADR 0023: the statmech is the record; the transition-state entry it hangs on is context, shown with its badge while it
    is merely not reviewed."""
    ts = _ts_entry(db_session)
    sm = _ts_statmech(db_session, ts)
    set_review(
        db_session, record_type=SubmissionRecordType.statmech, record_id=sm.id, status=RecordReviewStatus.approved
    )
    for resp in _both_verbs(client, "transition_state_entry_ref", ts.public_ref):
        assert resp.status_code == 200, resp.text
        assert _refs(resp.json()) == {sm.public_ref}
        (context,) = [r["transition_state"] for r in resp.json()["records"]]
        # ``StatmechTransitionStateContext`` has no review badge field: the entry is named and nothing more.
        assert context is not None and context["transition_state_entry_ref"] == ts.public_ref


def test_curated_approved_statmech_under_a_withheld_ts_entry_is_returned_without_it(client, db_session):
    """The twin: once the entry is rejected the statmech still comes back and the context naming the entry is withheld by
    the ref guard (the caller's own filter ref is echoed under ``request`` only)."""
    ts = _ts_entry(db_session)
    sm = _ts_statmech(db_session, ts)
    set_review(
        db_session, record_type=SubmissionRecordType.statmech, record_id=sm.id, status=RecordReviewStatus.approved
    )
    set_review(
        db_session, record_type=SubmissionRecordType.transition_state_entry, record_id=ts.id,
        status=RecordReviewStatus.rejected,
    )
    for resp in _both_verbs(client, "transition_state_entry_ref", ts.public_ref):
        assert resp.status_code == 200, resp.text
        assert _refs(resp.json()) == {sm.public_ref}
        assert [r["transition_state"] for r in resp.json()["records"]] == [None]
        assert ts.public_ref not in json.dumps(resp.json()["records"])
