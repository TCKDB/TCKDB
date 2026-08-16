"""Eleven curation refusals a curator can provoke, asserted on the wire.

Why this file exists
--------------------
``backend/docs/reviews/error_code_coverage_triage.md`` measured which of the
144 codes in :mod:`app.api.code_catalogue` any test actually produces, and
found 53 that none does. A catalogued code no test reaches is a claim nobody
has checked: the catalogue states a ``(status, code)`` pair, the client
generates retry advice from it, and until a request produces the pair nothing
holds either to what a caller receives.

This file covers the triage document's Tier B ("needs a release/selection
fixture") and Tier C ("needs a second record or a state change") — every
refusal on the ``/api/v1/releases/*`` write path and the two that guard a
release *after* it is cut. Eleven codes, one or two tests each, every one
provoked through the HTTP route rather than by calling
:mod:`app.services.release.curation` directly. Several of these already had
service-level tests; a service-level test says the function raises, and says
nothing about the status the depositor is handed or the code they branch on.
Both were wrong for two sibling codes on this same router until #170, which
is the concrete reason the assertion has to be made where the client stands.

Two Tier B rows are not here, on purpose
----------------------------------------
``release_tag_taken`` and ``curation_policy_version_conflict`` are the two
codes the triage document lists as catalogued at 422 while arriving at 409.
That was true when it was written and was fixed in #170: the catalogue records
both at 409, and ``test_api_dataset_releases.py`` provokes both on the wire.
Re-verified before writing this file; nothing here duplicates them.

What each test asserts, and why in that shape
---------------------------------------------
* the **status and the code**, never a substring of ``detail`` — a refusal
  message quotes the caller's own input back, so a substring check can pass
  against a request that was wrongly *accepted*;
* that a **neighbouring valid request is still accepted**. A test that only
  asserts "a 4xx arrived" passes equally against a guard that refuses
  everything, which is the failure mode this repository keeps finding. Where
  the accepted neighbour has to be exercised *first* (``non_finite_value``,
  where the failing publish has already flipped the release status before the
  artifact writer raises — production rolls that back in ``get_write_db``,
  the dependency-overridden test session does not), it is, and the comment
  says so.

Six expected statuses changed in #211, and none of them was relaxed
----------------------------------------------------------------------
``release_not_draft`` (twice), ``release_not_published`` (twice),
``selection_already_stands`` and ``selection_already_superseded`` were
asserted here at **422** and are now asserted at **409**. That is not a
test bending to fit the code: it is this file recording a decision made
about the contract — 409 for a state conflict, 404 for a missing thing,
422 only for a malformed payload. Every one of the six refuses a request
whose body is correct, and no corrected body makes it succeed, so 422 was
telling a client to resend something that could never be accepted.

The assertion is *stronger* after the change, not weaker: the pair is
still exact, and the same tests still require the neighbouring valid
request to be accepted. ``release_tag_taken`` and
``curation_policy_version_conflict`` had already been settled at 409 in
#170, so this removed an inconsistency inside one router rather than
creating one.

Fixtures live in this file rather than in a conftest deliberately: they are
one round old and shared by nothing else yet.
"""

from __future__ import annotations

import math

import pytest
from sqlalchemy import text

from app.db.models.app_user import AppUser
from app.db.models.common import RecordReviewStatus, SubmissionRecordType
from app.services.record_review import set_record_review_status
from tests.services.scientific_read._factories import (
    make_species,
    make_species_entry,
    make_thermo_scalar,
)

POLICY = {
    "name": "tckdb-tier-bc",
    "version": "1.0",
    "description": "Prefer the highest-level composite single point available.",
    "criteria": {"requires_review_status": "approved"},
}

RELEASE_BODY = {
    "tag": "2026.08.0",
    "title": "TCKDB curated thermochemistry, tier B/C probe",
    "curation_policy_name": POLICY["name"],
    "curation_policy_version": POLICY["version"],
    "data_license": "CC-BY-4.0",
    "code_license": "MIT",
    "citation_text": "TCKDB curated dataset release 2026.08.0.",
    "contact": "tckdb-maintainers@example.org",
    "changelog_entry": "Probe release.",
}

TAG = RELEASE_BODY["tag"]
RATIONALE = "CCSD(T)-F12 composite; frequencies all real."


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _approve(session, curator, row) -> None:
    set_record_review_status(
        session,
        record_type=SubmissionRecordType.thermo,
        record_id=row.id,
        status=RecordReviewStatus.approved,
        actor=curator,
    )


@pytest.fixture
def curator(db_session, _api_curator_user):
    return db_session.get(AppUser, _api_curator_user)


@pytest.fixture
def candidates(db_session, curator):
    """One species entry, two approved thermo records — a real choice.

    Two, because superseding needs a second candidate for the *same* subject:
    a release that only ever had one candidate cannot exercise a change of
    mind, which is the whole of the supersession contract.
    """
    entry = make_species_entry(
        db_session, species=make_species(db_session, smiles="CCO")
    )
    chosen = make_thermo_scalar(
        db_session, species_entry=entry, h298_kj_mol=-234.5, s298_j_mol_k=281.6
    )
    other = make_thermo_scalar(
        db_session, species_entry=entry, h298_kj_mol=-228.1, s298_j_mol_k=277.4
    )
    for row in (chosen, other):
        _approve(db_session, curator, row)
    db_session.flush()
    return entry, chosen, other


@pytest.fixture
def foreign_candidate(db_session, curator):
    """An approved thermo belonging to a *different* species entry."""
    entry = make_species_entry(
        db_session, species=make_species(db_session, smiles="CCC")
    )
    row = make_thermo_scalar(
        db_session, species_entry=entry, h298_kj_mol=-104.7, s298_j_mol_k=270.3
    )
    _approve(db_session, curator, row)
    db_session.flush()
    return row


@pytest.fixture
def as_curator(client, login_as, _api_curator_user):
    """Every route under test is curator-gated; log in once."""
    login_as(_api_curator_user)
    return client


def _open_draft(client, *, tag: str = TAG, **overrides) -> None:
    """Register the policy (idempotent) and open a draft release."""
    assert client.post("/api/v1/releases/policies", json=POLICY).status_code == 201
    body = dict(RELEASE_BODY, tag=tag, **overrides)
    created = client.post("/api/v1/releases", json=body)
    assert created.status_code == 201, created.text


def _select(client, record_ref: str, *, tag: str = TAG, rationale: str = RATIONALE):
    return client.post(
        f"/api/v1/releases/{tag}/selections",
        json={"record_ref": record_ref, "rationale": rationale},
    )


def _supersede(
    client, selection_ref: str, record_ref: str, *, tag: str = TAG, rationale=RATIONALE
):
    return client.post(
        f"/api/v1/releases/{tag}/selections/{selection_ref}/supersede",
        json={"record_ref": record_ref, "rationale": rationale},
    )


def _refusal(response, *, status: int, code: str) -> dict:
    """Assert the pair a client actually branches on, and return the body.

    The assertion is on ``code``, never on a substring of ``detail``: a
    refusal message quotes the caller's own input back, so a substring check
    can pass against a request that was wrongly accepted.

    ``context`` is checked for database primary keys (DR-0028 Req 2). Every
    code in this file is a ``message_prefix`` code and carries no structured
    context today, so the check is forward-looking: a row id is an
    implementation detail of one database instance — it does not survive a
    restore and does not agree between the hosted deployment and a lab
    self-host — and the moment one appears here a client can start reading it.
    """
    assert response.status_code == status, response.text
    body = response.json()
    assert body["code"] == code, body
    context = body["context"]
    assert isinstance(context, dict), body
    leaked = [key for key in context if key == "id" or key.endswith("_id")]
    assert not leaked, f"{code} leaked database ids into context: {leaked}"
    return body


# ---------------------------------------------------------------------------
# Tier B — the draft/publish lifecycle
# ---------------------------------------------------------------------------


def test_publishing_a_published_release_is_release_not_draft(
    as_curator, candidates
):
    """Publishing is not idempotent, and saying so is the point.

    A second publish would re-freeze the manifest — a new digest for a tag
    someone may already have cited. The depositor is told the release is no
    longer a draft rather than being handed a silently different citation.
    """
    _entry, chosen, _other = candidates
    _open_draft(as_curator)
    assert _select(as_curator, chosen.public_ref).status_code == 201

    first = as_curator.post(f"/api/v1/releases/{TAG}/publish")
    assert first.status_code == 200, first.text

    again = as_curator.post(f"/api/v1/releases/{TAG}/publish")
    _refusal(again, status=409, code="release_not_draft")


def test_selecting_into_a_published_release_is_release_not_draft(
    as_curator, candidates
):
    """A citation resolves to frozen bytes; appending to it after the fact
    would make the manifest describe a release that no longer exists.

    The refusal is what makes "cut the next release" the only way forward.
    """
    _entry, chosen, other = candidates
    _open_draft(as_curator)

    # The neighbouring request that must stay accepted: the same POST, one
    # state earlier.
    assert _select(as_curator, chosen.public_ref).status_code == 201
    assert as_curator.post(f"/api/v1/releases/{TAG}/publish").status_code == 200

    refused = _select(as_curator, other.public_ref)
    _refusal(refused, status=409, code="release_not_draft")


def test_withdrawing_a_draft_release_is_release_not_published(
    as_curator, candidates
):
    """Withdrawal retracts a *claim*, and a draft has not made one yet.

    ``withdrawn`` is a public status a reader is meant to trust; letting a
    draft enter it would mean a release could be retracted before anyone
    could have cited it, which says nothing.
    """
    _entry, chosen, _other = candidates
    _open_draft(as_curator)
    assert _select(as_curator, chosen.public_ref).status_code == 201

    refused = as_curator.post(
        f"/api/v1/releases/{TAG}/withdraw", json={"reason": "Systematic AEC error."}
    )
    _refusal(refused, status=409, code="release_not_published")

    # The same request, after publication, is accepted.
    assert as_curator.post(f"/api/v1/releases/{TAG}/publish").status_code == 200
    accepted = as_curator.post(
        f"/api/v1/releases/{TAG}/withdraw", json={"reason": "Systematic AEC error."}
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "withdrawn"


def test_attaching_a_doi_to_a_draft_release_is_release_not_published(
    as_curator, candidates
):
    """A DOI is recorded after a deposit, and only a published release is
    what gets deposited.

    Accepting one on a draft would let a citation point at bytes that do not
    exist yet and may still change.
    """
    _entry, chosen, _other = candidates
    _open_draft(as_curator)
    assert _select(as_curator, chosen.public_ref).status_code == 201

    doi = {"doi": "10.5281/zenodo.1234567"}
    refused = as_curator.post(f"/api/v1/releases/{TAG}/doi", json=doi)
    _refusal(refused, status=409, code="release_not_published")

    assert as_curator.post(f"/api/v1/releases/{TAG}/publish").status_code == 200
    accepted = as_curator.post(f"/api/v1/releases/{TAG}/doi", json=doi)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["doi"] == doi["doi"]


def test_withdrawing_a_release_with_a_blank_reason_is_refused(
    as_curator, candidates
):
    """Retracting a published dataset without saying why is not a retraction.

    ``""`` never reaches the service — ``ReasonRequest.reason`` is
    ``min_length=1``, so Pydantic answers first with
    ``request_validation_error``. Whitespace passes that check and is caught
    here, which is why this code exists at all rather than being folded into
    the field constraint.
    """
    _entry, chosen, _other = candidates
    _open_draft(as_curator)
    assert _select(as_curator, chosen.public_ref).status_code == 201
    assert as_curator.post(f"/api/v1/releases/{TAG}/publish").status_code == 200

    refused = as_curator.post(
        f"/api/v1/releases/{TAG}/withdraw", json={"reason": "   "}
    )
    _refusal(refused, status=422, code="withdraw_reason_required")

    # A real reason on the same route is accepted.
    accepted = as_curator.post(
        f"/api/v1/releases/{TAG}/withdraw",
        json={"reason": "Systematic atom-energy-correction error."},
    )
    assert accepted.status_code == 200, accepted.text


def test_a_blank_rationale_is_refused_on_every_selection_route(
    as_curator, candidates
):
    """A curated selection without a stated reason is an unattributed edit.

    The reason a curator wrote is the part of a release that outlives the
    curator, so all three routes that record a decision — select, supersede,
    withdraw-a-selection — refuse whitespace. Each is asserted explicitly
    rather than parametrized: a loop that silently covered two of the three
    would look identical to one covering all three.
    """
    _entry, chosen, other = candidates
    _open_draft(as_curator)

    _refusal(
        _select(as_curator, chosen.public_ref, rationale="   "),
        status=422,
        code="rationale_required",
    )
    created = _select(as_curator, chosen.public_ref)
    assert created.status_code == 201, created.text
    selection_ref = created.json()["selection_ref"]

    _refusal(
        _supersede(as_curator, selection_ref, other.public_ref, rationale="  "),
        status=422,
        code="rationale_required",
    )
    _refusal(
        as_curator.post(
            f"/api/v1/releases/{TAG}/selections/{selection_ref}/withdraw",
            json={"reason": "\t "},
        ),
        status=422,
        code="rationale_required",
    )

    # And the same three routes accept a real reason.
    replacement = _supersede(
        as_curator, selection_ref, other.public_ref, rationale="Better basis set."
    )
    assert replacement.status_code == 201, replacement.text
    withdrawn = as_curator.post(
        f"/api/v1/releases/{TAG}/selections/"
        f"{replacement.json()['selection_ref']}/withdraw",
        json={"reason": "Recommending nothing pending re-review."},
    )
    assert withdrawn.status_code == 201, withdrawn.text


def test_a_second_selection_for_the_same_subject_is_refused(
    as_curator, candidates
):
    """A release recommends *one* value per subject; two would recommend none.

    The depositor is pointed at supersession, which appends and keeps the
    earlier curator's reasoning readable, rather than being allowed to stack
    a second standing selection the read side would have to choose between.
    """
    _entry, chosen, other = candidates
    _open_draft(as_curator)
    first = _select(as_curator, chosen.public_ref)
    assert first.status_code == 201, first.text

    _refusal(
        _select(as_curator, other.public_ref),
        status=409,
        code="selection_already_stands",
    )

    # The route the refusal names is open.
    replacement = _supersede(
        as_curator, first.json()["selection_ref"], other.public_ref
    )
    assert replacement.status_code == 201, replacement.text


def test_a_selectable_ref_that_names_no_record_is_a_404(as_curator, candidates):
    """The fourth ``unknown_*`` on this router, and the last to reach 404.

    ``unknown_release``, ``unknown_selection`` and ``unknown_curation_policy``
    all answered 404 for the same condition; a candidate record answered 422,
    which told a client to correct a body that was already correct. The ref
    below is well formed and its prefix *is* selectable — the only thing
    wrong with it is that nothing answers to it, which is what separates it
    from ``record_ref_not_selectable`` (still 422, and still right: there the
    prefix names no selectable kind at all).
    """
    _entry, chosen, _other = candidates
    _open_draft(as_curator)

    refused = _select(as_curator, "thm_0123456789abcdef")
    _refusal(refused, status=404, code="unknown_record")

    # A ref that does resolve, on the same route, is accepted.
    accepted = _select(as_curator, chosen.public_ref)
    assert accepted.status_code == 201, accepted.text


def test_superseding_a_selection_with_itself_is_refused(as_curator, candidates):
    """Replacing a choice with the same record records no decision.

    It would append a row that reads as a change of mind and changes nothing —
    noise in the one ledger a reader consults to find out why a value was
    recommended.
    """
    _entry, chosen, other = candidates
    _open_draft(as_curator)
    created = _select(as_curator, chosen.public_ref)
    assert created.status_code == 201, created.text
    selection_ref = created.json()["selection_ref"]

    _refusal(
        _supersede(as_curator, selection_ref, chosen.public_ref),
        status=422,
        code="supersedes_same_record",
    )

    accepted = _supersede(as_curator, selection_ref, other.public_ref)
    assert accepted.status_code == 201, accepted.text


def test_superseding_an_already_superseded_selection_is_refused(
    as_curator, candidates
):
    """Supersession chains stay linear, so the ledger has one head.

    Branching would leave two rows each claiming to replace the same
    decision, and no rule for which one a reader should follow. The depositor
    is told to supersede the row that currently stands.
    """
    _entry, chosen, other = candidates
    _open_draft(as_curator)
    created = _select(as_curator, chosen.public_ref)
    assert created.status_code == 201, created.text
    first_ref = created.json()["selection_ref"]

    replacement = _supersede(as_curator, first_ref, other.public_ref)
    assert replacement.status_code == 201, replacement.text

    _refusal(
        _supersede(as_curator, first_ref, chosen.public_ref),
        status=409,
        code="selection_already_superseded",
    )

    # Superseding the row that *does* stand is accepted.
    accepted = _supersede(
        as_curator, replacement.json()["selection_ref"], chosen.public_ref
    )
    assert accepted.status_code == 201, accepted.text


# ---------------------------------------------------------------------------
# Tier C — a second record, or a state change under the release
# ---------------------------------------------------------------------------


def test_superseding_with_another_subjects_record_is_refused(
    as_curator, candidates, foreign_candidate
):
    """A release must never claim "the TCKDB thermo for ethanol" while
    pointing at a record attached to propane.

    ``add`` cannot reach this guard — it derives the record and its subject
    from one lookup — so supersession is the only route a curator can get it
    wrong from: the subject comes from the row being replaced and the record
    from the new ref, and nothing but this check ties them together.
    """
    _entry, chosen, other = candidates
    _open_draft(as_curator)
    created = _select(as_curator, chosen.public_ref)
    assert created.status_code == 201, created.text
    selection_ref = created.json()["selection_ref"]

    _refusal(
        _supersede(as_curator, selection_ref, foreign_candidate.public_ref),
        status=422,
        code="record_subject_mismatch",
    )

    # The same route, with a candidate that does belong to the subject.
    accepted = _supersede(as_curator, selection_ref, other.public_ref)
    assert accepted.status_code == 201, accepted.text


def test_publishing_a_selection_demoted_after_it_was_made_is_refused(
    as_curator, candidates, db_session, curator
):
    """Review is a moving target; a release re-checks at the moment it ships.

    Selecting an approved record and publishing weeks later would otherwise
    put a value under a citable recommendation that a reviewer has since
    deprecated — and ``profile=curated`` would simultaneously refuse to show
    it.

    ``deprecated`` rather than ``rejected`` because ``approved → rejected`` is
    not a permitted review transition (it must route through ``under_review``
    so the re-review is recorded); ``deprecated`` is below the same curated
    floor and is reachable in one step, which is exactly the sequence a real
    curator would produce.
    """
    _entry, chosen, _other = candidates
    _open_draft(as_curator)
    created = _select(as_curator, chosen.public_ref)
    assert created.status_code == 201, created.text

    set_record_review_status(
        db_session,
        record_type=SubmissionRecordType.thermo,
        record_id=chosen.id,
        status=RecordReviewStatus.deprecated,
        actor=curator,
    )
    db_session.flush()

    refused = as_curator.post(f"/api/v1/releases/{TAG}/publish")
    _refusal(refused, status=422, code="selection_no_longer_approved")

    # Restore the approval and the identical request is accepted — the guard
    # reads current review state, it does not refuse this release forever.
    set_record_review_status(
        db_session,
        record_type=SubmissionRecordType.thermo,
        record_id=chosen.id,
        status=RecordReviewStatus.approved,
        actor=curator,
    )
    db_session.flush()
    accepted = as_curator.post(f"/api/v1/releases/{TAG}/publish")
    assert accepted.status_code == 200, accepted.text


def test_publishing_a_record_holding_nan_is_refused_not_a_500(
    as_curator, db_session, curator
):
    """JSON has no NaN, so a stored NaN would produce an uncitable file.

    ``json.dumps(allow_nan=False)`` raises a bare ``ValueError`` reading "Out
    of range float values are not JSON compliant", which is a 500 with nothing
    a curator can act on. The refusal names the JSON path instead, so the
    offending record can be found and superseded.

    The accepted neighbour is published **first**, deliberately: the failing
    publish raises inside the artifact writer, after ``publish_release`` has
    already flipped the status. Production undoes that — ``get_write_db``
    rolls the request back and the release stays a draft — but this session is
    dependency-overridden and has no request-scoped rollback.
    """
    finite_entry = make_species_entry(
        db_session, species=make_species(db_session, smiles="CC")
    )
    finite = make_thermo_scalar(
        db_session, species_entry=finite_entry, h298_kj_mol=-84.0, s298_j_mol_k=229.6
    )
    _approve(db_session, curator, finite)

    nan_entry = make_species_entry(
        db_session, species=make_species(db_session, smiles="CCCC")
    )
    poisoned = make_thermo_scalar(
        db_session, species_entry=nan_entry, h298_kj_mol=float("nan"), s298_j_mol_k=310.0
    )
    # Approval freezes the row, so the value has to be non-finite before it.
    assert math.isnan(poisoned.h298_kj_mol)
    _approve(db_session, curator, poisoned)
    db_session.flush()

    # Neighbour first: an ordinary release of the finite record publishes.
    _open_draft(as_curator, tag="2026.08.1")
    assert _select(as_curator, finite.public_ref, tag="2026.08.1").status_code == 201
    good = as_curator.post("/api/v1/releases/2026.08.1/publish")
    assert good.status_code == 200, good.text

    _open_draft(as_curator, tag="2026.08.2")
    assert _select(as_curator, poisoned.public_ref, tag="2026.08.2").status_code == 201
    refused = as_curator.post("/api/v1/releases/2026.08.2/publish")
    body = _refusal(refused, status=422, code="non_finite_value")
    # The path is the diagnosable part; without it a curator has a rejected
    # release and no way to find which number caused it.
    assert "h298_kj_mol" in body["detail"]


def test_a_tampered_artifact_is_refused_rather_than_served(
    as_curator, candidates, db_session
):
    """A citation that resolves to altered bytes is worse than one that fails.

    The stored bytes are re-hashed on every download. This can only fire if
    the frozen row itself was changed — the trigger on ``release_artifact``
    forbids it, so the test has to suspend the trigger to produce the
    condition — and the answer is a 500 telling the operator to restore from
    backup, not a quiet 200 with a different file.
    """
    _entry, chosen, _other = candidates
    _open_draft(as_curator)
    assert _select(as_curator, chosen.public_ref).status_code == 201
    assert as_curator.post(f"/api/v1/releases/{TAG}/publish").status_code == 200

    manifest = as_curator.get(
        f"/api/v1/scientific/releases/{TAG}/manifest"
    ).json()["manifest"]
    urls = {a["path"]: a["download_url"] for a in manifest["artifacts"]}
    target, untouched = "selected_records.ndjson", "selection_ledger.ndjson"

    # Every artifact downloads cleanly before the tampering.
    for url in urls.values():
        assert as_curator.get(url).status_code == 200

    db_session.execute(text("SET LOCAL session_replication_role = replica"))
    db_session.execute(
        text(
            "UPDATE release_artifact SET content = :bytes "
            "WHERE path = :path AND release_manifest_id = ("
            "  SELECT rm.id FROM release_manifest rm"
            "  JOIN dataset_release dr ON dr.id = rm.dataset_release_id"
            "  WHERE dr.tag = :tag)"
        ),
        {"bytes": b'{"tampered":true}\n', "path": target, "tag": TAG},
    )
    db_session.execute(text("SET LOCAL session_replication_role = origin"))
    db_session.flush()
    db_session.expire_all()

    _refusal(as_curator.get(urls[target]), status=500, code="release_artifact_corrupt")

    # Only the tampered file is refused; the rest of the release still serves.
    assert as_curator.get(urls[untouched]).status_code == 200
