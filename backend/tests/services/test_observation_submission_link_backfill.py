"""Tests for ``app.services.observation_submission_link_backfill`` (#514).

Uses the per-test transactional ``db_session`` fixture, so every test rolls
back at teardown regardless of what the service itself commits/rolls back
internally.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.models.app_user import AppUser, AppUserRole
from app.db.models.common import (
    RightsBasisKind,
    SubmissionKind,
    SubmissionRecordType,
    SubmissionSourceKind,
)
from app.db.models.submission import Submission, SubmissionRecordLink
from app.db.models.submission_rights import SubmissionRightsAttestation
from app.services.observation_submission_link_backfill import (
    UNKNOWN_SOURCE_LABEL,
    backfill_observation_submission_links,
)
from app.services.submission import create_submission, link_records
from tests.services.scientific_read._factories import make_observation

_LICENSE_ID = "CC-BY-4.0"


@pytest.fixture
def operator(db_session) -> AppUser:
    user = AppUser(username="backfill_operator_test", role=AppUserRole.curator)
    db_session.add(user)
    db_session.flush()
    return user


def _run(db_session, operator, **kwargs):
    kwargs.setdefault("actor", operator)
    kwargs.setdefault("license_id", _LICENSE_ID)
    return backfill_observation_submission_links(db_session, **kwargs)


def _all_links(db_session, obs_id: int) -> list[SubmissionRecordLink]:
    return list(
        db_session.scalars(
            select(SubmissionRecordLink).where(
                SubmissionRecordLink.record_type
                == SubmissionRecordType.molecular_property_observation,
                SubmissionRecordLink.record_id == obs_id,
            )
        ).all()
    )


def _link_to_a_fresh_submission(db_session, obs, *, created_by: int) -> Submission:
    """Simulate a normal (already-linked) deposit, the way a real one does."""
    submission = create_submission(
        db_session,
        created_by=created_by,
        submission_kind=SubmissionKind.other,
        title="pre-existing deposit (already linked)",
    )
    link_records(
        db_session,
        submission=submission,
        records=[
            (SubmissionRecordType.molecular_property_observation, obs.id, None)
        ],
    )
    return submission


# ---------------------------------------------------------------------------
# Core behavior
# ---------------------------------------------------------------------------


def test_unlinked_rows_get_exactly_one_submission_per_source(db_session, operator):
    # reference_label distinguishes the two CCCBDB rows: mpo_dedupe_key
    # covers (species_entry_id, property_kind, scientific_origin,
    # external_source_name, external_source_release, external_source_url,
    # external_source_record_key, reference_label, scalar_value,
    # temperature_k), and make_observation's other defaults are otherwise
    # identical between the two calls.
    cccbdb_a = make_observation(
        db_session, external_source_name="CCCBDB", reference_label="row-a"
    )
    cccbdb_b = make_observation(
        db_session, external_source_name="CCCBDB", reference_label="row-b"
    )
    other = make_observation(db_session, external_source_name="NIST WebBook")

    result = _run(db_session, operator, commit=True)

    assert result.total_unlinked_found == 3
    assert result.total_linked == 3
    assert {o.source_name for o in result.outcomes} == {"CCCBDB", "NIST WebBook"}

    cccbdb_links_a = _all_links(db_session, cccbdb_a.id)
    cccbdb_links_b = _all_links(db_session, cccbdb_b.id)
    assert len(cccbdb_links_a) == 1
    assert len(cccbdb_links_b) == 1
    # Exactly one submission opened for the CCCBDB group, shared by both rows.
    assert cccbdb_links_a[0].submission_id == cccbdb_links_b[0].submission_id

    other_links = _all_links(db_session, other.id)
    assert len(other_links) == 1
    assert other_links[0].submission_id != cccbdb_links_a[0].submission_id

    cccbdb_submission = db_session.get(Submission, cccbdb_links_a[0].submission_id)
    assert cccbdb_submission.source_kind is SubmissionSourceKind.migration
    assert cccbdb_submission.created_by == operator.id


def test_rows_with_no_recorded_source_group_under_unknown(db_session, operator):
    row = make_observation(db_session, external_source_name=None)

    result = _run(db_session, operator, commit=True)

    assert result.total_linked == 1
    assert result.outcomes[0].source_name == UNKNOWN_SOURCE_LABEL
    assert len(_all_links(db_session, row.id)) == 1


def test_attestation_recorded_is_historical_review(db_session, operator):
    make_observation(db_session, external_source_name="CCCBDB")

    result = _run(db_session, operator, commit=True)
    submission_id = result.outcomes[0].submission_id

    attestations = list(
        db_session.scalars(
            select(SubmissionRightsAttestation).where(
                SubmissionRightsAttestation.submission_id == submission_id
            )
        ).all()
    )
    assert len(attestations) == 1
    assert attestations[0].basis is RightsBasisKind.historical_review
    assert attestations[0].license_id == _LICENSE_ID
    assert attestations[0].attested_by == operator.id


def test_already_linked_rows_are_untouched(db_session, operator):
    linked = make_observation(
        db_session, external_source_name="CCCBDB", reference_label="linked-row"
    )
    original_submission = _link_to_a_fresh_submission(
        db_session, linked, created_by=operator.id
    )
    unlinked = make_observation(
        db_session, external_source_name="CCCBDB", reference_label="unlinked-row"
    )

    result = _run(db_session, operator, commit=True)

    # Only the unlinked row was in scope.
    assert result.total_unlinked_found == 1
    assert result.total_linked == 1

    # The already-linked row still has exactly one link, pointing at its
    # original submission -- never repointed or given a second link.
    links = _all_links(db_session, linked.id)
    assert len(links) == 1
    assert links[0].submission_id == original_submission.id

    unlinked_links = _all_links(db_session, unlinked.id)
    assert len(unlinked_links) == 1
    assert unlinked_links[0].submission_id != original_submission.id


def test_no_scientific_field_is_ever_written(db_session, operator):
    row = make_observation(
        db_session,
        species_entry=None,
        scalar_value=917.8,
        external_source_name="CCCBDB",
    )
    before = (row.species_entry_id, row.scalar_value, row.scalar_unit)

    _run(db_session, operator, commit=True)

    db_session.refresh(row)
    after = (row.species_entry_id, row.scalar_value, row.scalar_unit)
    assert before == after
    assert row.species_entry_id is None


# ---------------------------------------------------------------------------
# Idempotency -- red without the "query unlinked rows fresh every call" scoping
# ---------------------------------------------------------------------------


def test_a_second_run_creates_no_second_submission_or_link(db_session, operator):
    obs = make_observation(db_session, external_source_name="CCCBDB")

    first = _run(db_session, operator, commit=True)
    assert first.total_linked == 1
    submissions_after_first = db_session.scalar(
        select(Submission).where(Submission.source_kind == SubmissionSourceKind.migration)
    )
    assert submissions_after_first is not None
    first_submission_count = len(
        list(
            db_session.scalars(
                select(Submission).where(
                    Submission.source_kind == SubmissionSourceKind.migration
                )
            ).all()
        )
    )

    second = _run(db_session, operator, commit=True)

    assert second.total_unlinked_found == 0
    assert second.total_linked == 0
    assert second.outcomes == []

    second_submission_count = len(
        list(
            db_session.scalars(
                select(Submission).where(
                    Submission.source_kind == SubmissionSourceKind.migration
                )
            ).all()
        )
    )
    assert second_submission_count == first_submission_count

    links = _all_links(db_session, obs.id)
    assert len(links) == 1


# ---------------------------------------------------------------------------
# Dry run -- red without the "commit only when commit=True" guard
# ---------------------------------------------------------------------------


def test_dry_run_writes_nothing(db_session, operator):
    obs = make_observation(db_session, external_source_name="CCCBDB")
    # Checkpoint the setup: db_session uses join_transaction_mode=
    # "create_savepoint" (tests/conftest.py's `client` fixture), so this
    # commit only releases this session's own SAVEPOINT and stays inside
    # the per-test transaction -- it does not leak past this test. Without
    # it, the dry run's own internal session.rollback() below would wipe
    # `obs` itself (uncommitted on the same session), not just what the
    # dry run would have written, and "the dry run left the scope exactly
    # as it found it" could not be checked at all.
    db_session.commit()

    result = _run(db_session, operator, commit=False)

    assert result.total_linked == 1  # reported, but not persisted

    assert _all_links(db_session, obs.id) == []
    assert (
        db_session.scalar(
            select(Submission).where(Submission.source_kind == SubmissionSourceKind.migration)
        )
        is None
    )
    assert db_session.scalar(select(SubmissionRightsAttestation)) is None

    # A real (committing) run afterwards still finds the row unlinked --
    # the dry run left the scope exactly as it found it.
    second = _run(db_session, operator, commit=True)
    assert second.total_linked == 1


def test_dry_run_never_reaches_the_object_store(db_session, operator, monkeypatch):
    """Nothing in this service's path may call the artifact store.

    Patches ``store_artifact`` to blow up if invoked at all, so a
    regression that started writing custody/artifact rows here would be
    caught even on the dry-run path, not only on commit.
    """
    import app.services.artifact_storage as artifact_storage

    def _forbidden(*args, **kwargs):
        raise AssertionError("observation_submission_link_backfill must never touch the object store")

    monkeypatch.setattr(artifact_storage, "store_artifact", _forbidden)

    make_observation(db_session, external_source_name="CCCBDB")
    db_session.commit()  # survive the dry run's internal rollback below
    dry = _run(db_session, operator, commit=False)
    assert dry.total_linked == 1

    committed = _run(db_session, operator, commit=True)
    assert committed.total_linked == 1
