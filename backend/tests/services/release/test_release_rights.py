"""A release may only ship records somebody agreed to license.

The reproduction this file opens with was green *before* the gate existed:
an approved, submission-linked thermo record whose submission carried no
rights attestation was selected and published under ``CC-BY-4.0`` with
nothing refusing it. ``LICENSE-DATA`` said "a default is not consent" and the
tests enforced that sentence as prose. The first test is that reproduction,
inverted: it is the regression that proves the gate is live.

Everything else here pins the shape of the gate: which deposits count, what
"compatible" means (an exact identifier match and nothing cleverer), that
publishing re-checks a wider set than the selections, and that the artifacts
say who licensed what without ever printing a user's row id.
"""

from __future__ import annotations

import json

import pytest

from app.db.models.app_user import AppUser
from app.db.models.common import (
    AppUserRole,
    RecordReviewStatus,
    RightsBasisKind,
    SubmissionKind,
    SubmissionRecordType,
    SubmissionSourceKind,
)
from app.services.record_review import set_record_review_status
from app.services.release.curation import (
    ReleaseCurationError,
    add_selection,
    current_selection,
    publish_release,
    supersede_selection,
)
from app.services.release.manifest import freeze_manifest, verify_release
from app.services.rights import (
    RightsAttestationError,
    RightsAttestationForbidden,
    record_attestation,
    standing_attestation,
)
from app.services.submission import create_submission, link_records
from tests.services.release._attest import attest_thermo, deposit_and_attest
from tests.services.scientific_read._factories import make_thermo_scalar


@pytest.fixture
def depositor(db_session) -> AppUser:
    user = AppUser(username="release-depositor", role=AppUserRole.user)
    db_session.add(user)
    db_session.flush()
    return user


def _approved_thermo(db_session, species_entry, curator, *, h298=-201.4):
    row = make_thermo_scalar(
        db_session, species_entry=species_entry, h298_kj_mol=h298, s298_j_mol_k=260.2
    )
    set_record_review_status(
        db_session,
        record_type=SubmissionRecordType.thermo,
        record_id=row.id,
        status=RecordReviewStatus.approved,
        actor=curator,
        note="approved for the rights tests",
    )
    return row


def _linked_submission(db_session, depositor, record):
    """A real deposit of ``record`` with no rights attestation at all."""
    submission = create_submission(
        db_session,
        created_by=depositor.id,
        submission_kind=SubmissionKind.thermo,
        title="deposit without any rights attestation",
    )
    link_records(
        db_session,
        submission=submission,
        records=[(SubmissionRecordType.thermo, record.id, None)],
    )
    return submission


def _select(db_session, release, curator, thermo, species_entry):
    return add_selection(
        db_session,
        release=release,
        record_type=SubmissionRecordType.thermo,
        record_id=thermo.id,
        subject_type=SubmissionRecordType.species_entry,
        subject_id=species_entry.id,
        rationale="Composite single point; frequencies all real.",
        selected_by=curator.id,
    )


# ---------------------------------------------------------------------------
# The reproduction, inverted
# ---------------------------------------------------------------------------


def test_an_unattested_deposit_cannot_be_selected_or_published(
    db_session, draft_release, curator, species_entry, depositor
):
    """Before the gate: selected and published, no refusal. After: refused.

    The record is approved and linked to a real submission; the only thing
    missing is anyone saying it may be licensed. That absence must refuse
    the selection. (The publish-time half of the gate -- a record linked or
    a candidate added *after* selection -- has its own tests below.)
    """
    thermo = _approved_thermo(db_session, species_entry, curator)
    _linked_submission(db_session, depositor, thermo)

    with pytest.raises(ReleaseCurationError, match="^rights_basis_missing:"):
        _select(db_session, draft_release, curator, thermo, species_entry)

    # Nothing was appended: the draft has no standing selection to publish.
    assert (
        current_selection(
            db_session,
            release=draft_release,
            subject_type=SubmissionRecordType.species_entry,
            subject_id=species_entry.id,
            record_type=SubmissionRecordType.thermo,
        )
        is None
    )


# ---------------------------------------------------------------------------
# What counts as a basis
# ---------------------------------------------------------------------------


def test_an_attestation_under_a_different_license_is_incompatible(
    db_session, draft_release, curator, species_entry, depositor
):
    """``CC0-1.0`` is more permissive than ``CC-BY-4.0`` and still refused.

    Compatibility is an exact identifier match. Whether a CC0 deposit may be
    republished with an attribution requirement is a legal judgement, and the
    code declines to make it; a curator who knows records a new attestation.
    """
    thermo = _approved_thermo(db_session, species_entry, curator)
    attest_thermo(db_session, depositor=depositor, rows=[thermo], license_id="CC0-1.0")

    with pytest.raises(ReleaseCurationError, match="^rights_basis_incompatible:") as info:
        _select(db_session, draft_release, curator, thermo, species_entry)
    assert "'CC0-1.0'" in str(info.value) and "'CC-BY-4.0'" in str(info.value)


@pytest.mark.parametrize(
    ("attested", "released", "compatible"),
    [
        ("CC-BY-4.0", "CC-BY-4.0", True),
        ("cc-by-4.0", "CC-BY-4.0", True),
        (" CC-BY-4.0 ", "CC-BY-4.0", True),
        # A prefix match would accept every one of these; exact equality
        # refuses them. Share-alike and an older version are different terms.
        ("CC-BY-SA-4.0", "CC-BY-4.0", False),
        ("CC-BY-4.0", "CC-BY-SA-4.0", False),
        ("CC-BY-3.0", "CC-BY-4.0", False),
        ("CC-BY-4.0", "CC-BY-3.0", False),
        ("CC0-1.0", "CC-BY-4.0", False),
    ],
)
def test_licenses_match_is_exact_equality_not_a_prefix(attested, released, compatible):
    from app.services.rights import licenses_match

    assert licenses_match(attested, released) is compatible


@pytest.mark.parametrize("attested", ["CC-BY-SA-4.0", "CC-BY-3.0"])
def test_a_near_miss_license_is_incompatible_at_selection(
    db_session, draft_release, curator, species_entry, depositor, attested
):
    """``CC-BY-SA-4.0`` and ``CC-BY-3.0`` share a prefix with the release's license and are still refused."""
    thermo = _approved_thermo(db_session, species_entry, curator)
    attest_thermo(db_session, depositor=depositor, rows=[thermo], license_id=attested)

    with pytest.raises(ReleaseCurationError, match="^rights_basis_incompatible:") as info:
        _select(db_session, draft_release, curator, thermo, species_entry)
    assert f"{attested!r}" in str(info.value)


def test_a_compatible_attestation_is_matched_case_insensitively(
    db_session, draft_release, curator, species_entry, depositor
):
    thermo = _approved_thermo(db_session, species_entry, curator)
    attest_thermo(db_session, depositor=depositor, rows=[thermo], license_id="cc-by-4.0")

    _select(db_session, draft_release, curator, thermo, species_entry)
    publish_release(db_session, draft_release)
    manifest = freeze_manifest(db_session, draft_release, created_by=curator.id)
    assert verify_release(db_session, draft_release).ok
    assert manifest.rights_summary_json["attestation_count"] == 1


def test_a_record_linked_to_no_submission_is_refused_as_missing(
    db_session, draft_release, curator, species_entry
):
    """No link means nothing to attest against: refuse, do not assume."""
    thermo = _approved_thermo(db_session, species_entry, curator)

    with pytest.raises(ReleaseCurationError, match="^rights_basis_missing:") as info:
        _select(db_session, draft_release, curator, thermo, species_entry)
    assert "linked to no submission" in str(info.value)


def test_every_linked_deposit_must_be_attested(
    db_session, draft_release, curator, species_entry, depositor
):
    """One attested deposit and one unattested deposit of the same record: refused.

    The release cannot say which deposit it is republishing, so the answer
    has to hold for both.
    """
    thermo = _approved_thermo(db_session, species_entry, curator)
    attest_thermo(db_session, depositor=depositor, rows=[thermo])
    unattested = _linked_submission(db_session, depositor, thermo)

    with pytest.raises(ReleaseCurationError, match="^rights_basis_missing:") as info:
        _select(db_session, draft_release, curator, thermo, species_entry)
    assert unattested.public_ref in str(info.value)


def test_a_curator_can_cover_a_historical_record_with_an_actor(
    db_session, draft_release, curator, species_entry
):
    """The path for records that predate rights capture.

    A ``migration`` submission is created, the record is linked, and a curator
    records ``historical_review`` under their own name. The refusal lifts;
    the attestation names who decided, which is what a default could not.
    """
    thermo = _approved_thermo(db_session, species_entry, curator)
    with pytest.raises(ReleaseCurationError, match="^rights_basis_missing:"):
        _select(db_session, draft_release, curator, thermo, species_entry)

    submission = create_submission(
        db_session,
        created_by=curator.id,
        submission_kind=SubmissionKind.thermo,
        source_kind=SubmissionSourceKind.migration,
        title="historical coverage of a pre-B1 deposit",
    )
    link_records(
        db_session,
        submission=submission,
        records=[(SubmissionRecordType.thermo, thermo.id, None)],
    )
    row = record_attestation(
        db_session,
        submission=submission,
        license_id="CC-BY-4.0",
        basis=RightsBasisKind.historical_review,
        actor=curator,
        note="Reviewed the 2026-03 ARC deposits; all the operator's own runs.",
    )
    assert row.basis is RightsBasisKind.historical_review
    assert row.attested_by == curator.id

    selection = _select(db_session, draft_release, curator, thermo, species_entry)
    assert selection.record_id == thermo.id


# ---------------------------------------------------------------------------
# Publish re-checks, over everything that ships
# ---------------------------------------------------------------------------


def test_superseding_the_attestation_to_another_license_after_selection_refuses_publish(
    db_session, draft_release, curator, species_entry, depositor
):
    thermo = _approved_thermo(db_session, species_entry, curator)
    submission = attest_thermo(db_session, depositor=depositor, rows=[thermo])
    _select(db_session, draft_release, curator, thermo, species_entry)

    # A correction is an insert that supersedes; the standing row moves.
    corrected = record_attestation(
        db_session,
        submission=submission,
        license_id="CC0-1.0",
        basis=RightsBasisKind.depositor_agreement,
        actor=depositor,
    )
    assert standing_attestation(db_session, submission_id=submission.id).id == corrected.id

    with pytest.raises(ReleaseCurationError, match="^rights_basis_incompatible:"):
        publish_release(db_session, draft_release)


def test_an_unattested_candidate_for_a_covered_subject_refuses_publish(
    db_session, draft_release, curator, species_entry, depositor
):
    """A gate on selections alone would still ship unlicensed bytes.

    ``candidate_records.ndjson`` carries every candidate for a covered
    subject. A second, unattested thermo for the same species entry arriving
    between selection and publication is refused -- never silently dropped
    from the file.
    """
    chosen = _approved_thermo(db_session, species_entry, curator)
    attest_thermo(db_session, depositor=depositor, rows=[chosen])
    _select(db_session, draft_release, curator, chosen, species_entry)

    latecomer = _approved_thermo(db_session, species_entry, curator, h298=-199.0)
    _linked_submission(db_session, depositor, latecomer)

    with pytest.raises(
        ReleaseCurationError, match="^candidate_rights_basis_missing:"
    ) as info:
        publish_release(db_session, draft_release)
    assert latecomer.public_ref in str(info.value)


def test_an_incompatibly_attested_candidate_refuses_publish(
    db_session, draft_release, curator, species_entry, depositor
):
    chosen = _approved_thermo(db_session, species_entry, curator)
    attest_thermo(db_session, depositor=depositor, rows=[chosen])
    _select(db_session, draft_release, curator, chosen, species_entry)

    latecomer = _approved_thermo(db_session, species_entry, curator, h298=-199.0)
    attest_thermo(db_session, depositor=depositor, rows=[latecomer], license_id="CC0-1.0")

    with pytest.raises(
        ReleaseCurationError, match="^candidate_rights_basis_incompatible:"
    ):
        publish_release(db_session, draft_release)


def test_supersede_selection_checks_the_replacement_too(
    db_session, draft_release, curator, species_entry, depositor
):
    chosen = _approved_thermo(db_session, species_entry, curator)
    attest_thermo(db_session, depositor=depositor, rows=[chosen])
    selection = _select(db_session, draft_release, curator, chosen, species_entry)

    replacement = _approved_thermo(db_session, species_entry, curator, h298=-199.0)
    _linked_submission(db_session, depositor, replacement)

    with pytest.raises(ReleaseCurationError, match="^rights_basis_missing:"):
        supersede_selection(
            db_session,
            superseded=selection,
            record_id=replacement.id,
            rationale="Higher level of theory.",
            selected_by=curator.id,
        )


# ---------------------------------------------------------------------------
# What the artifacts say
# ---------------------------------------------------------------------------


def _every_line(manifest) -> dict[str, list[dict]]:
    return {
        artifact.path: [
            json.loads(line) for line in artifact.content.decode("utf-8").splitlines()
        ]
        for artifact in manifest.artifacts
    }


def _walk(node, key):
    """Every value stored under ``key`` anywhere in a nested structure."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key:
                yield v
            yield from _walk(v, key)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item, key)


def test_every_shipped_record_line_says_who_licensed_it(
    db_session, draft_release, curator, species_entry, depositor, thermo_candidates
):
    """Both files, every line, a ``rights`` block; never a user primary key."""
    chosen, _other = thermo_candidates
    # A third candidate from a *different* depositor with a different basis,
    # so the block is not trivially uniform.
    extra = _approved_thermo(db_session, species_entry, curator, h298=-199.0)
    extra_deposit = deposit_and_attest(
        db_session,
        depositor=depositor,
        records=[(SubmissionRecordType.thermo, extra.id)],
    )
    _select(db_session, draft_release, curator, chosen, species_entry)
    publish_release(db_session, draft_release)
    manifest = freeze_manifest(db_session, draft_release, created_by=curator.id)
    lines = _every_line(manifest)

    selected = lines["selected_records.ndjson"]
    candidates = lines["candidate_records.ndjson"]
    assert len(selected) == 1 and len(candidates) == 3

    for line in selected + candidates:
        block = line["rights"]
        assert set(block) >= {"license", "basis", "attestation_ref", "attested_at", "attested_by"}
        assert block["license"] == "CC-BY-4.0"
        assert block["basis"] == "depositor_agreement"
        assert block["attestation_ref"].startswith("sra_")
        assert block["attested_at"]
        assert set(block["attested_by"]) == {"username", "full_name", "orcid", "affiliation"}

    by_ref = {line["record_ref"]: line for line in candidates}
    assert by_ref[extra.public_ref]["rights"]["attested_by"]["username"] == depositor.username
    assert by_ref[chosen.public_ref]["rights"]["attested_by"]["username"] == curator.username
    assert by_ref[extra.public_ref]["rights"]["attestation_ref"] == (
        standing_attestation(db_session, submission_id=extra_deposit.id).public_ref
    )

    # No integer ``attested_by`` anywhere, in any of the four files.
    for path, rows in lines.items():
        for value in _walk(rows, "attested_by"):
            assert isinstance(value, dict), f"{path}: attested_by is {value!r}"

    # And the manifest counts distinct agreements, not lines.
    assert manifest.document_json["rights"] == {
        "data_license": "CC-BY-4.0",
        "attestation_count": 2,
        "basis_kinds": {"depositor_agreement": 2},
    }


# ---------------------------------------------------------------------------
# The attestation service's own refusals
# ---------------------------------------------------------------------------


def test_a_depositor_agreement_is_only_the_depositors_to_make(
    db_session, curator, depositor
):
    submission = create_submission(
        db_session, created_by=depositor.id, submission_kind=SubmissionKind.thermo
    )
    with pytest.raises(RightsAttestationForbidden, match="^rights_attestation_not_depositor:"):
        record_attestation(
            db_session,
            submission=submission,
            license_id="CC-BY-4.0",
            basis=RightsBasisKind.depositor_agreement,
            actor=curator,
        )


def test_the_other_bases_need_a_curator(db_session, depositor):
    submission = create_submission(
        db_session, created_by=depositor.id, submission_kind=SubmissionKind.thermo
    )
    with pytest.raises(
        RightsAttestationForbidden, match="^rights_attestation_requires_curator:"
    ):
        record_attestation(
            db_session,
            submission=submission,
            license_id="CC-BY-4.0",
            basis=RightsBasisKind.historical_review,
            actor=depositor,
        )


def test_source_terms_basis_must_quote_its_terms(db_session, curator, depositor):
    submission = create_submission(
        db_session, created_by=depositor.id, submission_kind=SubmissionKind.thermo
    )
    with pytest.raises(RightsAttestationError, match="^rights_source_terms_required:"):
        record_attestation(
            db_session,
            submission=submission,
            license_id="CC-BY-4.0",
            basis=RightsBasisKind.source_terms,
            actor=curator,
            source_terms="   ",
        )
    row = record_attestation(
        db_session,
        submission=submission,
        license_id="CC-BY-4.0",
        basis=RightsBasisKind.source_terms,
        actor=curator,
        source_terms="NIST CCCBDB terms of use, section 3.",
    )
    assert row.source_terms == "NIST CCCBDB terms of use, section 3."
