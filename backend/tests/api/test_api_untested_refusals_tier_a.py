"""Eleven catalogued refusals, provoked through the wire for the first time.

Why this file exists
--------------------
``app/api/code_catalogue.py`` enumerates every code the API can put in
the ``code`` field of an error body, and a client's ``RejectionCode``
enum is generated from it. ``backend/docs/reviews/error_code_coverage_triage.md``
measured which of those codes the test suite actually produces: 91 of
144, leaving 53 that no test had ever provoked. A catalogued code no
test reaches is a claim nobody has checked — the catalogue cannot tell
a verified refusal apart from one whose guard has quietly drifted, and
the runtime observer in ``tests/error_code_observer.py`` can only hold
the catalogue to codes some test actually emits.

This file covers the triage's **Tier A**: the eleven codes reachable
without an upload fixture. Each test issues the request a depositor or
curator would issue, and asserts the ``(status, code)`` pair a client
receives — not the service function's exception. A code only a service
call can produce is not a code a client can branch on, which is the
distinction that made ``invalid_pagination`` wrong for four tests before
#170.

What the assertions are, and are not
------------------------------------
Assertions are on ``status`` and ``code``, and on structured ``context``
where the refusal carries any. Never on substrings of ``detail``:
Pydantic echoes rejected input back into its error string, so a
substring check can pass on a field that was wrongly *accepted*. The
one place ``detail`` is read is to prove a **negative** — that no
database primary key leaked into it (DR-0028 Req. 2).

Every refusal that has a valid neighbour is paired with one, and the
pairing is the point. A test asserting only "a 422 arrived" passes
against a guard that refuses everything; asserting that the same request
minus the one bad field is *accepted* is what makes the refusal mean
something. Where no cheap accepted neighbour exists the docstring says
so.

Corrections to the triage document, recorded here because the tests are
the evidence:

* ``unknown_record`` and ``record_has_no_subject`` are listed under
  "one request, no fixtures". They are not: both live behind
  ``POST /releases/{tag}/selections``, which 404s unless a curation
  policy and a draft release were created first. Three requests, not
  one. Everything else about their entries held.
* ``invalid_pagination`` is reachable only through the **POST** search
  body. The GET route pins ``offset`` at ``ge=0`` and ``limit`` at
  ``ge=1``, so FastAPI answers ``request_validation_error`` first; the
  body schema's ``offset: int = 0`` carries no bound, and that is the
  only door left open to it. The triage said "any composed-search POST",
  which is right, but the GET/POST asymmetry is the load-bearing part
  and is asserted below.
* A nonexistent ref must still be *well formed*. ``_REF_RE`` is
  ``^([a-z]+)_([A-Za-z0-9]+)$``, so a placeholder like
  ``spc_no_such_species`` is refused as ``invalid_handle`` before any
  conflict is detected. The triage's ``spc_nope`` happens to satisfy
  the grammar; a longer, more readable placeholder does not. This is
  the same class of probe error the triage recorded for the ``sp_`` /
  ``spc_`` prefix, one layer further in.

Measured while writing these: every one of the eleven arrives with an
empty ``context``. The envelope's advice to clients is "read
``context``, never ``detail``", and on this surface there is nothing to
read — the structured facts exist only for codes raised as declared
validation errors. Not asserted here, because pinning ``context == {}``
would make an improvement cost a red test; recorded so it is not
mistaken for a property.
"""

from __future__ import annotations

import pytest

from app.db.models.app_user import AppUser
from app.db.models.common import (
    RecordReviewStatus,
    ScientificOriginKind,
    StatmechTreatmentKind,
    SubmissionRecordType,
)
from app.db.models.statmech import Statmech
from app.services.record_review import set_record_review_status
from tests.services.scientific_read._factories import (
    make_chem_reaction,
    make_lot,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_statmech,
    make_thermo_scalar,
    make_transition_state,
    make_transition_state_entry,
)

_EXPORT_NDJSON = "/api/v1/scientific/export/ndjson"
_EXPORT_ML_SPECIES = "/api/v1/scientific/export/ml/species.ndjson"
_SPECIES_CALC_SEARCH = "/api/v1/scientific/species-calculations/search"
_CURATOR_TASKS = "/api/v1/admin/machine-review/curator-tasks"
_RELEASES = "/api/v1/releases"


def _assert_refusal(response, *, status: int, code: str) -> dict:
    """The response is *status* and its envelope ``code`` is *code*.

    Both halves matter. ``status`` is the retry advice a client branches
    on and is generated into the client's ``REJECTION_STATUSES``; ``code``
    is what tells the caller which of the many refusals at that status
    happened. The triage found two catalogue entries whose status was
    simply wrong, undetected because nothing compared the pair.
    """
    assert response.status_code == status, response.text
    body = response.json()
    assert body.get("code") == code, (
        f"expected ({status}, {code!r}); got "
        f"({response.status_code}, {body.get('code')!r}). "
        f"detail={body.get('detail')!r}"
    )
    return body


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def curator(client, login_as, _api_curator_user):
    """Act as a curator; the export and release surfaces are curator-gated."""
    login_as(_api_curator_user)
    return client


@pytest.fixture
def admin(client, login_as, _api_admin_user):
    """Act as an admin; the curator-task queue is admin-only."""
    login_as(_api_admin_user)
    return client


@pytest.fixture
def species_entry(db_session):
    """One species entry, so a seed has something real to resolve to."""
    return make_species_entry(
        db_session, species=make_species(db_session, smiles="CCO")
    )


_POLICY = {
    "name": "tier-a-policy",
    "version": "1.0",
    "description": "Prefer the composite single point with converged frequencies.",
    "criteria": {"requires_review_status": "approved"},
}

_RELEASE_TAG = "2026.08.0-tier-a"

_RELEASE = {
    "tag": _RELEASE_TAG,
    "title": "Tier A refusal fixture release",
    "curation_policy_name": _POLICY["name"],
    "curation_policy_version": _POLICY["version"],
    "data_license": "CC-BY-4.0",
    "code_license": "MIT",
    "citation_text": "TCKDB tier-A refusal fixture.",
    "contact": "tckdb-maintainers@example.org",
    "changelog_entry": "Fixture release.",
}


@pytest.fixture
def draft_release(curator):
    """A registered policy version and a draft release citing it.

    ``POST /releases/{tag}/selections`` 404s on an unknown release before
    it ever looks at ``record_ref``, so the two selection refusals below
    are unreachable without this. That is the respect in which the triage
    filed them in the wrong tier.
    """
    assert curator.post(f"{_RELEASES}/policies", json=_POLICY).status_code == 201
    assert curator.post(_RELEASES, json=_RELEASE).status_code == 201
    return _RELEASE_TAG


def _approve(db_session, curator_id, *, record_type, record_id) -> None:
    set_record_review_status(
        db_session,
        record_type=record_type,
        record_id=record_id,
        status=RecordReviewStatus.approved,
        actor=db_session.get(AppUser, curator_id),
    )
    db_session.flush()


# ---------------------------------------------------------------------------
# 1. export_seed_unresolved
# ---------------------------------------------------------------------------


def test_export_ndjson_refuses_a_seed_ref_that_names_nothing(
    curator, db_session, species_entry
) -> None:
    """A bulk export never silently drops a seed the caller asked for.

    To a curator building a mechanism, the difference between "that
    reaction has no data" and "you misspelled the ref" is the difference
    between a scientific finding and a typo. ``resolve_seed`` refuses the
    whole request rather than exporting the resolvable part, and it does
    so *before* the streaming response starts — a ``ValueError`` raised
    from inside the generator would arrive after the 200 headers were
    already on the wire, where no client could act on it.

    Both raise sites are provoked: the reaction seed and the species
    seed report the same code, because the remedy is the same.
    """
    reaction_seed = curator.get(
        _EXPORT_NDJSON, params={"reaction_ref": "rxe_nosuchreaction"}
    )
    _assert_refusal(reaction_seed, status=422, code="export_seed_unresolved")

    species_seed = curator.get(
        _EXPORT_NDJSON, params={"species_ref": "spe_nosuchentry"}
    )
    _assert_refusal(species_seed, status=422, code="export_seed_unresolved")

    # The accepted neighbour: the same request with a ref that resolves.
    # Without it, both assertions above would still pass on a build where
    # every export 422s.
    accepted = curator.get(
        _EXPORT_NDJSON,
        params={
            "species_ref": species_entry.public_ref,
            "min_review_status": "under_review",
        },
    )
    assert accepted.status_code == 200, accepted.text


# ---------------------------------------------------------------------------
# 2. ml_export_seed_unresolved
# ---------------------------------------------------------------------------


def test_ml_species_export_refuses_a_species_ref_that_names_nothing(
    curator, species_entry
) -> None:
    """The ML export refuses an unresolvable seed rather than short-changing it.

    An ML dataset silently missing one of the species a caller listed is
    worse than one that fails: the file looks complete, and the training
    run that consumes it has no way to notice. The seed is resolved
    eagerly for the same reason as the NDJSON export above.
    """
    refused = curator.get(
        _EXPORT_ML_SPECIES, params={"species_ref": "spe_nosuchentry"}
    )
    _assert_refusal(refused, status=422, code="ml_export_seed_unresolved")

    accepted = curator.get(
        _EXPORT_ML_SPECIES,
        params={
            "species_ref": species_entry.public_ref,
            "min_review_status": "under_review",
        },
    )
    assert accepted.status_code == 200, accepted.text


# ---------------------------------------------------------------------------
# 3. ml_export_lot_unresolved
# ---------------------------------------------------------------------------


def test_ml_species_export_refuses_an_unknown_level_of_theory_filter(
    curator, db_session, species_entry
) -> None:
    """An unknown ``lot_ref`` is refused, not treated as "no such energies".

    This is the one filter on the ML export whose failure mode is
    invisible: an unmatched LOT would emit every record with an empty
    energy block, which reads exactly like a corpus that has no data at
    that level. The refusal carries its own code, distinct from the
    seed's, because the field to fix is a different one.

    The seed itself resolves in both halves, so the LOT is the only
    thing left that can be refused.
    """
    lot = make_lot(db_session, method="wb97xd", basis="def2tzvp")

    refused = curator.get(
        _EXPORT_ML_SPECIES,
        params={
            "species_ref": species_entry.public_ref,
            "lot_ref": "lot_nosuchlevel",
            "min_review_status": "under_review",
        },
    )
    _assert_refusal(refused, status=422, code="ml_export_lot_unresolved")

    accepted = curator.get(
        _EXPORT_ML_SPECIES,
        params={
            "species_ref": species_entry.public_ref,
            "lot_ref": lot.public_ref,
            "min_review_status": "under_review",
        },
    )
    assert accepted.status_code == 200, accepted.text


# ---------------------------------------------------------------------------
# 4. unsafe_lowest_energy_comparison
# ---------------------------------------------------------------------------


def test_lowest_energy_ranking_requires_an_exact_species_and_level(
    client, db_session, species_entry
) -> None:
    """Ranking by energy across levels of theory would be scientifically false.

    Electronic energies are only comparable within one level of theory
    and one species entry; ordering a mixed set by ``electronic_energy``
    produces a "lowest" that means nothing. Rather than return a ranked
    list a caller would reasonably trust, the search refuses until both
    filters pin the comparison down.

    The sibling refusal ``unsupported_ranking_for_calculation_type``
    guards the other precondition (the calculation type must carry an
    energy at all) and is checked first, which is why the body below
    names ``sp``.
    """
    lot = make_lot(db_session, method="ccsdt", basis="ccpvtz")

    refused = client.post(
        _SPECIES_CALC_SEARCH,
        json={"calculation_type": "sp", "ranking": "lowest_energy"},
    )
    _assert_refusal(refused, status=422, code="unsafe_lowest_energy_comparison")

    # Naming only one of the two is still unsafe: half a comparison is
    # not half-refused.
    half = client.post(
        _SPECIES_CALC_SEARCH,
        json={
            "calculation_type": "sp",
            "ranking": "lowest_energy",
            "species_entry_ref": species_entry.public_ref,
        },
    )
    _assert_refusal(half, status=422, code="unsafe_lowest_energy_comparison")

    accepted = client.post(
        _SPECIES_CALC_SEARCH,
        json={
            "calculation_type": "sp",
            "ranking": "lowest_energy",
            "species_entry_ref": species_entry.public_ref,
            "level_of_theory_ref": lot.public_ref,
        },
    )
    assert accepted.status_code == 200, accepted.text


# ---------------------------------------------------------------------------
# 5. invalid_pagination
# ---------------------------------------------------------------------------


def test_composed_search_refuses_a_malformed_page_window(
    client, species_entry
) -> None:
    """A negative offset or a sub-one limit is a caller bug, and says so.

    ``invalid_pagination`` is deliberately narrower than it once was:
    since #170 the two *cap* violations carry their own codes
    (``limit_too_large``, ``offset_too_large``) because those are hosting
    policy and are recoverable by resending a smaller page. These two are
    not recoverable — there is no page number below zero — so they keep
    the malformed-request code.

    Only the POST body can reach it. The GET route declares
    ``offset: int = Query(0, ge=0)`` and ``limit: … ge=1``, so FastAPI
    refuses those first with ``request_validation_error``; the body
    schema's ``offset: int = 0`` carries no bound. Both are asserted, so
    the day someone adds a bound to the body schema this test says which
    contract changed instead of going quietly green.
    """
    negative_offset = client.post(_SPECIES_CALC_SEARCH, json={"offset": -1})
    _assert_refusal(negative_offset, status=422, code="invalid_pagination")

    zero_limit = client.post(_SPECIES_CALC_SEARCH, json={"limit": 0})
    _assert_refusal(zero_limit, status=422, code="invalid_pagination")

    # The framework, not the service, on the GET route — a different code
    # for the same mistake, and the reason four tests once believed they
    # were provoking this one.
    framework = client.get(_SPECIES_CALC_SEARCH, params={"offset": -1})
    _assert_refusal(framework, status=422, code="request_validation_error")

    # The accepted neighbour needs a species identifier, because the
    # pagination check runs *before* ``missing_identifier`` — which is
    # itself worth pinning: a caller who sends both mistakes is told
    # about the page window first.
    accepted = client.post(
        _SPECIES_CALC_SEARCH,
        json={
            "species_entry_ref": species_entry.public_ref,
            "offset": 0,
            "limit": 1,
        },
    )
    assert accepted.status_code == 200, accepted.text


# ---------------------------------------------------------------------------
# 6. species_handle_conflict
# ---------------------------------------------------------------------------


def test_species_id_and_species_ref_that_disagree_are_refused(
    client, db_session, species_entry
) -> None:
    """Two identifiers for one species must agree, or the query is a guess.

    A filter pair that disagrees has two readings — the caller meant one
    of them — and silently AND-ing them to an empty result set would
    report "no data" for a request the server could not interpret. The
    refusal is a 422 rather than an empty page for that reason.

    The body deliberately does not echo the id the ref resolves to.
    Echoing it turned this endpoint into an oracle for the whole
    ref-to-id mapping, which ``internal_ids`` exists to withhold: supply
    a public ref plus any wrong id and the 422 hands back the real one.
    """
    species = species_entry.species

    refused = client.post(
        _SPECIES_CALC_SEARCH,
        json={"species_id": species.id, "species_ref": "spc_nosuchspecies"},
    )
    body = _assert_refusal(refused, status=422, code="species_handle_conflict")
    assert str(species.id) not in str(body.get("detail")), body

    accepted = client.post(
        _SPECIES_CALC_SEARCH,
        json={"species_id": species.id, "species_ref": species.public_ref},
    )
    assert accepted.status_code == 200, accepted.text


# ---------------------------------------------------------------------------
# 7. species_entry_handle_conflict
# ---------------------------------------------------------------------------


def test_species_entry_id_and_ref_that_disagree_are_refused(
    client, species_entry
) -> None:
    """The same rule one level down, with its own code.

    It is a separate code from ``species_handle_conflict`` because the
    field to fix is a different one, and a caller that sends both pairs
    needs to know which of the two disagreed.
    """
    refused = client.post(
        _SPECIES_CALC_SEARCH,
        json={
            "species_entry_id": species_entry.id,
            "species_entry_ref": "spe_nosuchentry",
        },
    )
    body = _assert_refusal(
        refused, status=422, code="species_entry_handle_conflict"
    )
    assert str(species_entry.id) not in str(body.get("detail")), body

    accepted = client.post(
        _SPECIES_CALC_SEARCH,
        json={
            "species_entry_id": species_entry.id,
            "species_entry_ref": species_entry.public_ref,
        },
    )
    assert accepted.status_code == 200, accepted.text


# ---------------------------------------------------------------------------
# 8. curator_task_not_found
# ---------------------------------------------------------------------------


def test_assigning_an_unknown_curator_task_is_a_coded_404(admin) -> None:
    """A missing curator task is named, not left as a bare 404.

    An admin UI assigning a task needs to distinguish "that task is
    gone" (drop it from the queue) from "you are not allowed here" and
    from "the route moved". The coded 404 is what carries that.

    No accepted neighbour is asserted: building a real curator task
    requires a submission, a machine-review run and its findings, which
    is a Tier D fixture. What is asserted instead is that the request
    reached the service — a 403 or a 405 would fail this test, and those
    are the two ways a 404 could arrive without the guard running.

    Measured while writing this, not fixed here: ``GET`` on the same task
    id answers ``{"code": "http_404", "detail": "Curator task not
    found."}`` — a private ``_get_curator_task_or_404`` in
    ``api/routes/admin.py`` raises its own ``HTTPException`` instead of
    calling the service helper. One condition, two contracts, and only
    one of them is a code a client can branch on.
    """
    refused = admin.post(
        f"{_CURATOR_TASKS}/99999999/assign", json={"assignee_id": None}
    )
    body = _assert_refusal(refused, status=404, code="curator_task_not_found")
    # The looked-up row id is logged for the operator, never returned
    # (DR-0028 Req. 2). 99999999 is the caller's own path parameter, so
    # what must be absent is any *other* id.
    assert body.get("context") == {}, body


# ---------------------------------------------------------------------------
# 9. unknown_curation_policy
# ---------------------------------------------------------------------------


def test_opening_a_release_against_an_unregistered_policy_is_refused(
    curator,
) -> None:
    """A release must cite a policy version that exists, before it exists.

    The curation policy is the sentence a release makes about *why* its
    records were chosen, and a published release cites it permanently. A
    release opened against a policy version nobody registered would be a
    citation to nothing, so the policy has to be registered first — and
    the 404 says which half of the request was wrong.

    The accepted neighbour is the same request after registering the
    policy, which is the whole remedy the code is advising.
    """
    refused = curator.post(_RELEASES, json=_RELEASE)
    _assert_refusal(refused, status=404, code="unknown_curation_policy")

    assert curator.post(f"{_RELEASES}/policies", json=_POLICY).status_code == 201
    accepted = curator.post(_RELEASES, json=_RELEASE)
    assert accepted.status_code == 201, accepted.text


# ---------------------------------------------------------------------------
# 10. unknown_record
# ---------------------------------------------------------------------------


def test_selecting_a_record_ref_that_names_nothing_is_refused(
    curator, db_session, draft_release, _api_curator_user
) -> None:
    """A release may not recommend a record that does not exist.

    Curators address candidates by public ref, never by database id, so
    the one thing that can go wrong is a ref with a selectable prefix
    that matches no row. It is refused rather than recorded, because a
    release's whole value is that every ref in it resolves for a reader
    who has only the tag.

    The sibling ``record_ref_not_selectable`` covers a *wrong prefix*;
    this is the right prefix and no row.

    404 since #211, and asserted here at the new status deliberately. The
    body is well formed and the prefix is selectable — the only thing
    wrong is that nothing answers to the ref, which is the definition of
    a 404. It was the fourth ``unknown_*`` on this router and the only one
    that had not reached one. The sibling's 422 is untouched, because a
    wrong prefix *is* a payload a curator corrects and resends.
    """
    entry = make_species_entry(
        db_session, species=make_species(db_session, smiles="CCCO")
    )
    thermo = make_thermo_scalar(
        db_session, species_entry=entry, h298_kj_mol=-255.1, s298_j_mol_k=322.6
    )
    _approve(
        db_session,
        _api_curator_user,
        record_type=SubmissionRecordType.thermo,
        record_id=thermo.id,
    )

    refused = curator.post(
        f"{_RELEASES}/{draft_release}/selections",
        json={
            "record_ref": "thm_nosuchrecord",
            "rationale": "Composite single point, frequencies all real.",
        },
    )
    _assert_refusal(refused, status=404, code="unknown_record")

    accepted = curator.post(
        f"{_RELEASES}/{draft_release}/selections",
        json={
            "record_ref": thermo.public_ref,
            "rationale": "Composite single point, frequencies all real.",
        },
    )
    assert accepted.status_code == 201, accepted.text


# ---------------------------------------------------------------------------
# 11. record_has_no_subject
# ---------------------------------------------------------------------------


def test_selecting_a_statmech_owned_by_a_transition_state_is_refused(
    curator, db_session, draft_release, _api_curator_user
) -> None:
    """A release recommends a record *for* a subject, and a TS statmech has none.

    ``statmech`` has exactly one subject and it may be either a species
    entry or a transition-state entry (``statmech_exactly_one_subject``).
    The release layer derives a selection's subject through
    ``CANDIDATE_SOURCES``, which maps statmech to ``species_entry_id``
    alone — so a transition-state statmech resolves to a real row with no
    subject, and "the TCKDB statmech for ..." has nothing to complete the
    sentence with.

    This is a genuine refusal, not a backend bug: the record exists, the
    ref is well-formed and selectable, and the caller could reasonably
    have expected it to work. What it also is, is a boundary worth
    revisiting — transition-state statmech is first-class everywhere else
    in the schema, and only the release layer cannot address it. If that
    changes, this test should be rewritten to assert the new acceptance
    deliberately, not deleted.

    The transition-state statmech is *approved* before it is offered, so
    the refusal cannot be the approval guard wearing a different name;
    and the accepted neighbour is a species-owned statmech, approved and
    selected, which rules out statmech being unselectable as a type.
    """
    reactant = make_species_entry(
        db_session, species=make_species(db_session, smiles="CC")
    )
    product = make_species_entry(
        db_session, species=make_species(db_session, smiles="C=C")
    )
    reaction = make_chem_reaction(
        db_session,
        reactants=[reactant.species],
        products=[product.species],
    )
    reaction_entry = make_reaction_entry(
        db_session,
        reaction=reaction,
        reactant_entries=[reactant],
        product_entries=[product],
    )
    ts_entry = make_transition_state_entry(
        db_session,
        transition_state=make_transition_state(
            db_session, reaction_entry=reaction_entry
        ),
    )
    ts_statmech = Statmech(
        species_entry_id=None,
        transition_state_entry_id=ts_entry.id,
        scientific_origin=ScientificOriginKind.computed,
        statmech_treatment=StatmechTreatmentKind.rrho,
        external_symmetry=1,
        is_linear=False,
    )
    db_session.add(ts_statmech)
    db_session.flush()
    _approve(
        db_session,
        _api_curator_user,
        record_type=SubmissionRecordType.statmech,
        record_id=ts_statmech.id,
    )

    refused = curator.post(
        f"{_RELEASES}/{draft_release}/selections",
        json={
            "record_ref": ts_statmech.public_ref,
            "rationale": "RRHO treatment at the reported level.",
        },
    )
    _assert_refusal(refused, status=422, code="record_has_no_subject")

    species_statmech = make_statmech(db_session, species_entry=reactant)
    _approve(
        db_session,
        _api_curator_user,
        record_type=SubmissionRecordType.statmech,
        record_id=species_statmech.id,
    )
    accepted = curator.post(
        f"{_RELEASES}/{draft_release}/selections",
        json={
            "record_ref": species_statmech.public_ref,
            "rationale": "RRHO treatment at the reported level.",
        },
    )
    assert accepted.status_code == 201, accepted.text
