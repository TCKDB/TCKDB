"""Selection reproducibility: the frozen ``selected_records`` digest survives a
restore, and re-rendering from the restored ledger under the same policy
reproduces it byte for byte.

Selection in TCKDB is curator-driven (an append-only ledger under a registered
policy, ADR 0007), so "re-running selection" means replaying that ledger: the
archive carries it, the restore puts it back, and the artifact renderer is
deterministic over it. The test publishes and freezes a release, writes the
recovery archive, empties the database, restores, re-renders, and compares
SHA-256 digests with the ones recorded at publication.

Two mutations on the restored state (a different standing candidate, a
different policy version) must turn the digest check red, and a release with
no eligible candidate must be refused explicitly.

What this proves, exactly: that the selection ledger and every field the
renderer reads round-trip through ``tckdb.archive.v1`` intact, that the
frozen ``release_artifact`` bytes come back with their recorded digests, and
that rendering over the restored rows is deterministic. What it does not
prove: that the database being rendered is the restored one rather than the
source one, except through a restore that loses or alters something; the
restore assertion in ``_restore`` (four artifact rows back, digests equal to
the ones read before the emptying) is what makes a skipped or no-op restore
fail instead of passing silently.
"""

from __future__ import annotations

import hashlib
import io

import pytest
from sqlalchemy import select, text

from app.db.models.common import RecordReviewStatus, SubmissionRecordType
from app.db.models.dataset_release import (
    CurationPolicy,
    DatasetRelease,
    ReleaseArtifact,
    ReleaseManifest,
    ReleaseSelection,
)
from app.services.archive import restore_archive, write_archive
from app.services.release.artifacts import render_artifacts
from app.services.release.curation import (
    ReleaseCurationError,
    add_selection,
    create_release,
    publish_release,
    resolve_curation_policy,
)
from app.services.release.manifest import (
    ReleaseStateError,
    freeze_manifest,
    live_divergence,
    verify_release,
)
from tests.services.archive.conftest import _restore_sequences, _snapshot_sequences
from tests.services.archive.test_archive import _empty_archive_tables
from tests.services.scientific_read._factories import make_thermo_scalar, set_review

SELECTED = "selected_records.ndjson"


@pytest.fixture(autouse=True)
def _preserve_sequence_state(request, db_engine):
    """``restore_archive`` resets sequences non-transactionally; put them back
    (the archive suite's containment, applied here because this file restores)."""
    request.getfixturevalue("_api_test_user")
    snapshot = _snapshot_sequences(db_engine)
    try:
        yield
    finally:
        _restore_sequences(db_engine, snapshot)


def _select(session, release, curator, thermo, species_entry, rationale="CCSD(T)-F12 composite."):
    return add_selection(
        session,
        release=release,
        record_type=SubmissionRecordType.thermo,
        record_id=thermo.id,
        subject_type=SubmissionRecordType.species_entry,
        subject_id=species_entry.id,
        rationale=rationale,
        selected_by=curator.id,
    )


def _rendered_digests(session, release) -> dict[str, str]:
    return {artifact.path: artifact.sha256 for artifact in render_artifacts(session, release)}


@pytest.fixture
def frozen(db_session, draft_release, curator, thermo_candidates, species_entry):
    """Publish, freeze, archive: returns what a reproducer would be handed."""
    chosen, other = thermo_candidates
    _select(db_session, draft_release, curator, chosen, species_entry)
    publish_release(db_session, draft_release)
    manifest = freeze_manifest(db_session, draft_release, created_by=curator.id)
    digests = {artifact.path: artifact.sha256 for artifact in manifest.artifacts}
    assert SELECTED in digests and len(digests) == 4
    # The digest recorded at publication is the digest of the frozen bytes.
    for artifact in manifest.artifacts:
        assert hashlib.sha256(artifact.content).hexdigest() == artifact.sha256
    archive = io.BytesIO()
    write_archive(db_session, archive)
    return {
        "archive": archive.getvalue(),
        "digests": digests,
        "release_ref": draft_release.public_ref,
        "policy_ref": draft_release.curation_policy.public_ref,
        "policy_version": draft_release.curation_policy.version,
        "chosen_ref": chosen.public_ref,
        "other_id": other.id,
        "species_entry_id": species_entry.id,
    }


def _restore(db_session, frozen) -> DatasetRelease:
    _empty_archive_tables(db_session)
    assert db_session.scalar(select(DatasetRelease)) is None
    restore_archive(db_session, io.BytesIO(frozen["archive"]))
    release = db_session.scalar(select(DatasetRelease).where(DatasetRelease.public_ref == frozen["release_ref"]))
    assert release is not None
    # The restore is load-bearing: the frozen artifact rows must be back, and
    # their bytes must hash to the digests read before the tables were
    # emptied. A no-op restore leaves zero rows here.
    restored_rows = db_session.scalars(
        select(ReleaseArtifact)
        .join(ReleaseManifest, ReleaseArtifact.release_manifest_id == ReleaseManifest.id)
        .where(ReleaseManifest.dataset_release_id == release.id)
    ).all()
    assert len(restored_rows) == 4
    assert {row.path: hashlib.sha256(row.content).hexdigest() for row in restored_rows} == frozen["digests"]
    return release


def test_replay_on_the_restored_database_reproduces_the_frozen_digest(db_session, frozen):
    release = _restore(db_session, frozen)
    # Same registered policy, same version, ledger intact.
    assert release.curation_policy.public_ref == frozen["policy_ref"]
    assert release.curation_policy.version == frozen["policy_version"]
    assert verify_release(db_session, release).problems == []

    # Re-derive the selection under the frozen policy: replay the ledger and
    # re-render. The rendering reads only the restored rows.
    rendered = _rendered_digests(db_session, release)
    assert rendered[SELECTED] == frozen["digests"][SELECTED]
    assert rendered == frozen["digests"]
    assert not live_divergence(db_session, release).diverged

    # And the selected line names the candidate the curator chose.
    selected = next(a for a in render_artifacts(db_session, release) if a.path == SELECTED)
    assert selected.record_count == 1
    assert frozen["chosen_ref"] in selected.content.decode()


def _with_triggers_disabled(session, statement: str, params: dict) -> None:
    """Simulate a restored database whose state differs from the archived one.

    The ledger and policy rows are append-only by trigger, so the mutation is
    applied in replica mode, as a hostile or corrupted restore would land it.
    """
    session.execute(text("SET LOCAL session_replication_role = replica"))
    session.execute(text(statement), params)
    session.execute(text("SET LOCAL session_replication_role = origin"))
    session.expire_all()


def test_a_different_standing_candidate_turns_the_digest_red(db_session, frozen):
    release = _restore(db_session, frozen)
    selection = db_session.scalar(
        select(ReleaseSelection).where(ReleaseSelection.dataset_release_id == release.id)
    )
    assert selection.record_id != frozen["other_id"]
    _with_triggers_disabled(
        db_session,
        "UPDATE release_selection SET record_id = :other WHERE id = :id",
        {"other": frozen["other_id"], "id": selection.id},
    )
    rendered = _rendered_digests(db_session, release)
    assert rendered[SELECTED] != frozen["digests"][SELECTED]
    assert live_divergence(db_session, release).diverged
    # The frozen bytes themselves are untouched: only the replay disagrees.
    assert verify_release(db_session, release).problems == []


def test_a_different_policy_version_turns_the_digest_red(db_session, frozen):
    release = _restore(db_session, frozen)
    policy = db_session.scalar(select(CurationPolicy).where(CurationPolicy.public_ref == frozen["policy_ref"]))
    _with_triggers_disabled(
        db_session,
        "UPDATE curation_policy SET version = :version WHERE id = :id",
        {"version": frozen["policy_version"] + "-mutated", "id": policy.id},
    )
    rendered = _rendered_digests(db_session, release)
    assert rendered[SELECTED] != frozen["digests"][SELECTED]
    assert live_divergence(db_session, release).diverged


def test_no_eligible_candidate_is_refused_explicitly(db_session, frozen, curator):
    release = _restore(db_session, frozen)
    policy = release.curation_policy
    same_policy = resolve_curation_policy(
        db_session,
        name=policy.name,
        version=policy.version,
        description=policy.description,
        criteria=dict(policy.criteria_json or {}),
        created_by=curator.id,
    )
    assert same_policy.id == policy.id
    draft = create_release(
        db_session,
        tag="2026.07.1",
        title="Replay under the same policy",
        curation_policy=same_policy,
        data_license=release.data_license,
        code_license=release.code_license,
        citation_text="Replay.",
        contact=release.contact,
        changelog_entry="Replay with no eligible candidate.",
        created_by=curator.id,
    )
    species_entry_id = frozen["species_entry_id"]
    from app.db.models.species import SpeciesEntry

    species_entry = db_session.get(SpeciesEntry, species_entry_id)
    unreviewed = make_thermo_scalar(db_session, species_entry=species_entry, h298_kj_mol=-1.0, s298_j_mol_k=1.0)
    with pytest.raises(ReleaseCurationError, match="record_not_approved"):
        _select(db_session, draft, curator, unreviewed, species_entry)
    set_review(
        db_session,
        record_type=SubmissionRecordType.thermo,
        record_id=unreviewed.id,
        status=RecordReviewStatus.rejected,
    )
    with pytest.raises(ReleaseCurationError, match="record_not_approved"):
        _select(db_session, draft, curator, unreviewed, species_entry)
    # Nothing eligible was selected, so the release recommends nothing and
    # cannot be frozen either.
    publish_release(db_session, draft)
    with pytest.raises(ReleaseStateError, match="release_selects_nothing"):
        freeze_manifest(db_session, draft, created_by=curator.id)
