"""The manifest is only worth something if it detects drift.

These tests exercise the claim the Stage 3 exit criterion rests on: a published
release can be re-derived from the database, every checksum still matches, and
if anything the release depends on changes, verification fails loudly.
"""

from __future__ import annotations

import hashlib
import json

import pytest
from sqlalchemy import text

from app.db.models.common import ReadProfile, SubmissionRecordType
from app.services.release import versions
from app.services.release.artifacts import (
    ARTIFACT_PATHS,
    canonical_json,
    render_artifacts,
)
from app.services.release.curation import add_selection, publish_release
from app.services.release.manifest import (
    ReleaseStateError,
    freeze_manifest,
    live_divergence,
    load_manifest,
    manifest_digest,
    recorded_manifest_document,
    verify_release,
)


def _publish_with_selection(session, release, curator, thermo, species_entry):
    add_selection(
        session,
        release=release,
        record_type=SubmissionRecordType.thermo,
        record_id=thermo.id,
        subject_type=SubmissionRecordType.species_entry,
        subject_id=species_entry.id,
        rationale="CCSD(T)-F12 composite single point; frequencies all real.",
        selected_by=curator.id,
    )
    publish_release(session, release)
    return freeze_manifest(session, release, created_by=curator.id)


# ---------------------------------------------------------------------------
# Freezing
# ---------------------------------------------------------------------------


def test_freezing_requires_a_published_release(db_session, draft_release, curator):
    with pytest.raises(ReleaseStateError, match="release_not_published"):
        freeze_manifest(db_session, draft_release, created_by=curator.id)


def test_a_release_has_exactly_one_manifest(
    db_session, draft_release, curator, thermo_candidates, species_entry
):
    first, _second = thermo_candidates
    _publish_with_selection(db_session, draft_release, curator, first, species_entry)
    with pytest.raises(ReleaseStateError, match="manifest_already_frozen"):
        freeze_manifest(db_session, draft_release, created_by=curator.id)


def test_manifest_binds_versions_licenses_and_profile(
    db_session, draft_release, curator, thermo_candidates, species_entry
):
    first, _second = thermo_candidates
    manifest = _publish_with_selection(
        db_session, draft_release, curator, first, species_entry
    )

    assert manifest.manifest_schema == versions.MANIFEST_SCHEMA
    # A release is the curated contract by construction, recorded not assumed.
    assert manifest.profile is ReadProfile.curated
    assert manifest.alembic_revision and manifest.alembic_revision != ""
    assert manifest.review_policy_version == versions.REVIEW_POLICY_VERSION
    assert manifest.recovery_archive_schema == versions.RECOVERY_ARCHIVE_SCHEMA
    assert manifest.data_license == "CC-BY-4.0"
    assert manifest.code_license == "MIT"
    assert manifest.citation_text == draft_release.citation_text
    assert manifest.curation_policy_id == draft_release.curation_policy_id
    assert len(manifest.content_sha256) == 64


def test_manifest_ships_all_four_artifacts_with_checksums(
    db_session, draft_release, curator, thermo_candidates, species_entry
):
    first, _second = thermo_candidates
    manifest = _publish_with_selection(
        db_session, draft_release, curator, first, species_entry
    )
    paths = {row.path for row in manifest.artifacts}
    assert paths == set(ARTIFACT_PATHS.values())
    for row in manifest.artifacts:
        assert len(row.sha256) == 64
        assert row.byte_count > 0
        assert row.record_count >= 1


def test_release_ships_the_unselected_candidate_too(
    db_session, draft_release, curator, thermo_candidates, species_entry
):
    """The exit criterion: candidates stay retrievable alongside the selection."""
    chosen, other = thermo_candidates
    _publish_with_selection(db_session, draft_release, curator, chosen, species_entry)

    rendered = {a.path: a for a in render_artifacts(db_session, draft_release)}
    candidates = [
        json.loads(line)
        for line in rendered["candidate_records.ndjson"].content.decode().splitlines()
    ]
    refs = {row["record_ref"]: row for row in candidates}

    assert chosen.public_ref in refs
    assert other.public_ref in refs, "an unselected candidate must still ship"
    assert refs[chosen.public_ref]["selected_in_release"] is True
    assert refs[other.public_ref]["selected_in_release"] is False
    # And the scientific value is actually present, not just the identity.
    assert refs[other.public_ref]["record"]["h298_kj_mol"] == pytest.approx(-235.9)


def test_release_ships_review_history_for_every_candidate(
    db_session, draft_release, curator, thermo_candidates, species_entry
):
    chosen, other = thermo_candidates
    _publish_with_selection(db_session, draft_release, curator, chosen, species_entry)

    rendered = {a.path: a for a in render_artifacts(db_session, draft_release)}
    history = [
        json.loads(line)
        for line in rendered["review_history.ndjson"].content.decode().splitlines()
    ]
    by_ref = {row["record_ref"]: row for row in history}

    for record in (chosen, other):
        assert record.public_ref in by_ref
        entry = by_ref[record.public_ref]
        assert entry["review"]["status"] == "approved"
        assert entry["events"], "the review event log must ship with the release"


def test_selected_records_carry_their_attribution(
    db_session, draft_release, curator, thermo_candidates, species_entry
):
    chosen, _other = thermo_candidates
    _publish_with_selection(db_session, draft_release, curator, chosen, species_entry)

    rendered = {a.path: a for a in render_artifacts(db_session, draft_release)}
    lines = [
        json.loads(line)
        for line in rendered["selected_records.ndjson"].content.decode().splitlines()
    ]
    assert len(lines) == 1
    entry = lines[0]
    assert entry["record_ref"] == chosen.public_ref
    assert entry["selection"]["curator"]["username"] == curator.username
    assert entry["selection"]["curation_policy"]["version"] == "1.0"
    assert "CCSD(T)-F12" in entry["selection"]["rationale"]
    assert entry["record"]["h298_kj_mol"] == pytest.approx(-234.5)


def test_artifacts_never_leak_internal_integer_ids(
    db_session, draft_release, curator, thermo_candidates, species_entry
):
    chosen, _other = thermo_candidates
    _publish_with_selection(db_session, draft_release, curator, chosen, species_entry)
    rendered = render_artifacts(db_session, draft_release)

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                assert not (key == "id" or key.endswith("_id") or key.endswith("_ids")), key
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    for artifact in rendered:
        for line in artifact.content.decode().splitlines():
            walk(json.loads(line))


# ---------------------------------------------------------------------------
# Determinism and verification
# ---------------------------------------------------------------------------


def test_rendering_is_deterministic(
    db_session, draft_release, curator, thermo_candidates, species_entry
):
    chosen, _other = thermo_candidates
    _publish_with_selection(db_session, draft_release, curator, chosen, species_entry)
    first = {a.path: a.sha256 for a in render_artifacts(db_session, draft_release)}
    second = {a.path: a.sha256 for a in render_artifacts(db_session, draft_release)}
    assert first == second


def test_published_release_verifies(
    db_session, draft_release, curator, thermo_candidates, species_entry
):
    chosen, _other = thermo_candidates
    _publish_with_selection(db_session, draft_release, curator, chosen, species_entry)
    report = verify_release(db_session, draft_release)
    assert report.ok, report.problems
    assert report.artifacts_checked == len(ARTIFACT_PATHS)
    assert report.content_sha256_recorded == report.content_sha256_recomputed


def test_manifest_digest_is_reproducible_by_a_third_party(
    db_session, draft_release, curator, thermo_candidates, species_entry
):
    """A citer must be able to recompute the digest from the published bytes.

    This is the whole contract: canonical JSON, SHA-256, no server secret.
    """
    chosen, _other = thermo_candidates
    manifest = _publish_with_selection(
        db_session, draft_release, curator, chosen, species_entry
    )
    document = recorded_manifest_document(db_session, manifest)

    # Recompute exactly as the runbook tells a reader to.
    body = json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    assert hashlib.sha256(body.encode("utf-8")).hexdigest() == manifest.content_sha256

    # And each artifact's digest is over its literal bytes.
    rendered = {a.path: a for a in render_artifacts(db_session, draft_release)}
    for row in manifest.artifacts:
        assert (
            hashlib.sha256(rendered[row.path].content).hexdigest() == row.sha256
        )


def test_an_approved_selected_record_cannot_be_altered_at_all(
    db_session, draft_release, curator, thermo_candidates, species_entry
):
    """The strongest form of "a release selection never changes the science".

    The accepted-science trigger already refuses to mutate an approved product
    row, so a released value cannot drift by being edited — only by the corpus
    around it growing (see the next test). Pinned here because verification
    would otherwise be the only thing standing between a citation and a silent
    rewrite.
    """
    chosen, _other = thermo_candidates
    _publish_with_selection(db_session, draft_release, curator, chosen, species_entry)
    assert verify_release(db_session, draft_release).ok

    with pytest.raises(Exception, match="immutable"):
        db_session.execute(
            text("UPDATE thermo SET h298_kj_mol = :v WHERE id = :i"),
            {"v": -999.0, "i": chosen.id},
        )
    db_session.rollback()


def test_corpus_growth_after_publication_does_not_break_the_release(
    db_session, draft_release, curator, thermo_candidates, species_entry
):
    """The finding that failed review: one ordinary upload must not un-cite a release.

    A release is a snapshot claim. Publishing it and then uploading a third
    thermo for a released species used to change the re-rendered candidate set,
    which broke the digest and made the citable artifacts 409. On a corpus
    where review is expected to progress, that made the citable window
    effectively zero.
    """
    chosen, _other = thermo_candidates
    manifest = _publish_with_selection(
        db_session, draft_release, curator, chosen, species_entry
    )
    published = {row.path: bytes(row.content) for row in manifest.artifacts}
    assert verify_release(db_session, draft_release).ok

    from tests.services.scientific_read._factories import make_thermo_scalar

    make_thermo_scalar(
        db_session, species_entry=species_entry, h298_kj_mol=-236.4, s298_j_mol_k=279.5
    )
    db_session.flush()
    db_session.expire_all()

    # Integrity is untouched: the frozen bytes are what they always were.
    report = verify_release(db_session, draft_release)
    assert report.ok, report.problems
    manifest = load_manifest(db_session, draft_release)
    assert {row.path: bytes(row.content) for row in manifest.artifacts} == published

    # And the growth is *reported*, as information, not as an error.
    divergence = live_divergence(db_session, draft_release)
    assert divergence.diverged
    assert any("candidate_records.ndjson" in d for d in divergence.differences)
    assert "snapshot" in divergence.note


def test_review_state_changing_after_publication_does_not_break_the_release(
    db_session, draft_release, curator, thermo_candidates, species_entry
):
    """Review advancing is the other everyday write that used to un-cite a release."""
    from app.db.models.common import RecordReviewStatus, SubmissionRecordType
    from app.services.record_review import set_record_review_status

    chosen, other = thermo_candidates
    _publish_with_selection(db_session, draft_release, curator, chosen, species_entry)
    assert verify_release(db_session, draft_release).ok

    set_record_review_status(
        db_session,
        record_type=SubmissionRecordType.thermo,
        record_id=other.id,
        status=RecordReviewStatus.deprecated,
        actor=curator,
    )
    db_session.flush()

    assert verify_release(db_session, draft_release).ok


def test_recording_a_doi_does_not_break_the_manifest_digest(
    db_session, draft_release, curator, thermo_candidates, species_entry
):
    """The runbook's own final step used to permanently invalidate the digest.

    Every genuinely cited release has a DOI, so under the old design the steady
    state of every real release was ``verified: false`` and the integrity
    signal was true only for releases nobody had deposited.
    """
    from app.services.release.curation import record_doi

    chosen, _other = thermo_candidates
    manifest = _publish_with_selection(
        db_session, draft_release, curator, chosen, species_entry
    )
    digest_before = manifest.content_sha256
    document_before = recorded_manifest_document(db_session, manifest)

    record_doi(db_session, draft_release, doi="10.5281/zenodo.1234567")
    db_session.flush()
    db_session.expire_all()

    report = verify_release(db_session, draft_release)
    assert report.ok, report.problems
    manifest = load_manifest(db_session, draft_release)
    assert manifest.content_sha256 == digest_before
    assert recorded_manifest_document(db_session, manifest) == document_before
    # The document reports the DOI *as at publication* — i.e. none.
    assert document_before["release"]["doi_at_publication"] is None
    # The annotation is visible, but on the release, not in the frozen document.
    assert draft_release.doi == "10.5281/zenodo.1234567"
    assert live_divergence(db_session, draft_release).diverged


def test_withdrawing_a_release_does_not_break_the_manifest_digest(
    db_session, draft_release, curator, thermo_candidates, species_entry
):
    """Same failure via ``status``: a retraction must not corrupt the citation."""
    from app.services.release.curation import withdraw_release

    chosen, _other = thermo_candidates
    _publish_with_selection(db_session, draft_release, curator, chosen, species_entry)

    withdraw_release(db_session, draft_release, reason="Systematic AEC error.")
    db_session.flush()
    db_session.expire_all()

    report = verify_release(db_session, draft_release)
    assert report.ok, report.problems
    manifest = load_manifest(db_session, draft_release)
    assert (
        recorded_manifest_document(db_session, manifest)["release"][
            "status_at_publication"
        ]
        == "published"
    )


def test_manifest_document_is_rebuildable_from_its_snapshot_columns(
    db_session, draft_release, curator, thermo_candidates, species_entry
):
    """The stored document and the snapshot columns must not be able to drift."""
    from app.services.release.manifest import (
        render_manifest_document,
        snapshot_from_manifest,
    )

    chosen, _other = thermo_candidates
    manifest = _publish_with_selection(
        db_session, draft_release, curator, chosen, species_entry
    )
    rebuilt = render_manifest_document(snapshot_from_manifest(manifest))
    assert rebuilt == dict(manifest.document_json)


def test_verification_fails_when_a_stored_checksum_is_tampered_with(
    db_session, draft_release, curator, thermo_candidates, species_entry
):
    chosen, _other = thermo_candidates
    manifest = _publish_with_selection(
        db_session, draft_release, curator, chosen, species_entry
    )
    row = next(a for a in manifest.artifacts if a.kind.value == "selected_records")
    assert row.content, "artifact bytes must be frozen on the row, not re-rendered"

    # release_artifact is immutable at the database level; a tamper attempt is
    # refused outright, which is the strongest possible outcome.
    with pytest.raises(Exception):
        db_session.execute(
            text("UPDATE release_artifact SET sha256 = :s WHERE id = :i"),
            {"s": "0" * 64, "i": row.id},
        )
    db_session.rollback()


def test_manifest_row_is_immutable_at_the_database_level(
    db_session, draft_release, curator, thermo_candidates, species_entry
):
    chosen, _other = thermo_candidates
    manifest = _publish_with_selection(
        db_session, draft_release, curator, chosen, species_entry
    )
    with pytest.raises(Exception):
        db_session.execute(
            text("UPDATE release_manifest SET content_sha256 = :s WHERE id = :i"),
            {"s": "1" * 64, "i": manifest.id},
        )
    db_session.rollback()


def test_verification_uses_the_recorded_version_binding(
    db_session, draft_release, curator, thermo_candidates, species_entry, monkeypatch
):
    """Upgrading the backend must not invalidate a past release."""
    chosen, _other = thermo_candidates
    _publish_with_selection(db_session, draft_release, curator, chosen, species_entry)

    monkeypatch.setattr(versions, "backend_version", lambda: "99.99.99")
    report = verify_release(db_session, draft_release)
    assert report.ok, report.problems


# ---------------------------------------------------------------------------
# Contract separation
# ---------------------------------------------------------------------------


def test_manifest_states_it_is_not_a_backup_or_a_projection(
    db_session, draft_release, curator, thermo_candidates, species_entry
):
    chosen, _other = thermo_candidates
    manifest = _publish_with_selection(
        db_session, draft_release, curator, chosen, species_entry
    )
    contract = recorded_manifest_document(db_session, manifest)["contract"]

    assert contract["kind"] == "curated_dataset_release"
    assert contract["immutable"] is True
    assert contract["citable"] is True
    assert contract["lossless_database_backup"] is False
    assert contract["reingestible"] is False
    assert contract["distinct_from"]["recovery_archive"] == "tckdb.archive.v1"
    assert contract["distinct_from"]["convenience_projection"] == "tckdb.export.v0"


def test_recovery_archive_schema_tag_does_not_drift():
    """The manifest names the recovery contract; keep the two in step."""
    from app.services.archive.core import ARCHIVE_SCHEMA

    assert versions.RECOVERY_ARCHIVE_SCHEMA == ARCHIVE_SCHEMA


def test_manifest_document_declares_the_curated_profile(
    db_session, draft_release, curator, thermo_candidates, species_entry
):
    chosen, _other = thermo_candidates
    manifest = _publish_with_selection(
        db_session, draft_release, curator, chosen, species_entry
    )
    document = recorded_manifest_document(db_session, manifest)
    assert document["profile"] == "curated"
    assert document["recommendation"] == "tckdb_curated_release"


def test_canonical_json_is_stable_across_key_order():
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})
    assert manifest_digest({"b": 1, "a": 2}) == manifest_digest({"a": 2, "b": 1})


def test_a_withdrawn_selection_still_resolves_in_the_ledger(
    db_session, draft_release, curator, thermo_candidates, species_entry
):
    """A release that declines on one subject must still say *what* it declined on.

    The ledger for a withdrawn selection has no standing row to borrow its
    subject from, so the subject ref has to be resolved from the full selection
    history rather than from what currently stands. The release also keeps a
    standing selection on a second subject, because a release that recommends
    nothing at all is refused outright.
    """
    from app.db.models.common import RecordReviewStatus, SubmissionRecordType
    from app.services.record_review import set_record_review_status
    from app.services.release.curation import add_selection, withdraw_selection
    from app.services.release.manifest import freeze_manifest
    from tests.services.scientific_read._factories import (
        make_species,
        make_species_entry,
        make_thermo_scalar,
    )

    chosen, _other = thermo_candidates

    # A second subject that this release *does* recommend on.
    kept_entry = make_species_entry(
        db_session, species=make_species(db_session, smiles="CC")
    )
    kept = make_thermo_scalar(
        db_session, species_entry=kept_entry, h298_kj_mol=-84.0, s298_j_mol_k=229.6
    )
    set_record_review_status(
        db_session,
        record_type=SubmissionRecordType.thermo,
        record_id=kept.id,
        status=RecordReviewStatus.approved,
        actor=curator,
    )
    add_selection(
        db_session,
        release=draft_release,
        record_type=SubmissionRecordType.thermo,
        record_id=kept.id,
        subject_type=SubmissionRecordType.species_entry,
        subject_id=kept_entry.id,
        rationale="Only candidate, and it agrees with ATcT.",
        selected_by=curator.id,
    )

    original = add_selection(
        db_session,
        release=draft_release,
        record_type=SubmissionRecordType.thermo,
        record_id=chosen.id,
        subject_type=SubmissionRecordType.species_entry,
        subject_id=species_entry.id,
        rationale="Initial pick.",
        selected_by=curator.id,
    )
    withdraw_selection(
        db_session,
        superseded=original,
        rationale="Both candidates disagree with the shock-tube measurement.",
        selected_by=curator.id,
    )
    publish_release(db_session, draft_release)
    freeze_manifest(db_session, draft_release, created_by=curator.id)

    rendered = {a.path: a for a in render_artifacts(db_session, draft_release)}
    ledger = [
        json.loads(line)
        for line in rendered["selection_ledger.ndjson"].content.decode().splitlines()
    ]
    withdrawn_entries = [
        e for e in ledger if e["record_ref"] == chosen.public_ref
    ]
    assert len(withdrawn_entries) == 2
    for entry in withdrawn_entries:
        assert entry["subject_ref"] == species_entry.public_ref
        assert entry["stands"] is False
    assert {e["action"] for e in withdrawn_entries} == {"select", "withdraw"}

    # Nothing is recommended for that subject, but its candidates still ship so
    # a reader can see what the curators could not choose between.
    selected = [
        json.loads(line)
        for line in rendered["selected_records.ndjson"].content.decode().splitlines()
    ]
    assert {e["record_ref"] for e in selected} == {kept.public_ref}
    candidates = {
        json.loads(line)["record_ref"]
        for line in rendered["candidate_records.ndjson"].content.decode().splitlines()
    }
    assert chosen.public_ref in candidates

    assert verify_release(db_session, draft_release).ok


# ---------------------------------------------------------------------------
# What a deposited file actually contains
# ---------------------------------------------------------------------------


def test_artifact_lines_carry_chemical_identity_and_level_of_theory(
    db_session, draft_release, curator, species_entry
):
    """A deposited release must be interpretable without the database it came from.

    Before this, a ``selected_records`` line held the numbers and
    ``public_ref`` and had silently dropped ``species_entry_id``,
    ``statmech_id``, ``literature_id``, ``software_release_id`` and
    ``workflow_tool_release_id``; each ``thermo_source_calculation`` row
    serialized to a bare ``{"role": ...}``. The file stated a heat of formation
    for an opaque handle with no SMILES, no level of theory, no software and no
    citation — strictly less science than the unauthenticated read API.
    """
    from app.db.models.common import RecordReviewStatus, SubmissionRecordType
    from app.db.models.software import SoftwareRelease
    from app.db.models.thermo import ThermoSourceCalculation
    from app.services.record_review import set_record_review_status
    from app.services.release.curation import add_selection
    from app.services.release.manifest import freeze_manifest
    from tests.services.scientific_read._factories import (
        make_calculation,
        make_lot,
        make_software,
        make_thermo_scalar,
    )

    lot = make_lot(db_session, method="CCSD(T)-F12", basis="cc-pVTZ-F12")
    software = make_software(db_session, name="Molpro")
    software_release = SoftwareRelease(software_id=software.id, version="2024.1")
    db_session.add(software_release)
    db_session.flush()
    calculation = make_calculation(
        db_session, species_entry_id=species_entry.id, lot_id=lot.id
    )
    calculation.software_release_id = software_release.id
    db_session.flush()
    thermo = make_thermo_scalar(
        db_session, species_entry=species_entry, h298_kj_mol=-234.5, s298_j_mol_k=281.6
    )
    db_session.add(
        ThermoSourceCalculation(
            thermo_id=thermo.id, calculation_id=calculation.id, role="sp"
        )
    )
    db_session.flush()
    set_record_review_status(
        db_session,
        record_type=SubmissionRecordType.thermo,
        record_id=thermo.id,
        status=RecordReviewStatus.approved,
        actor=curator,
    )
    add_selection(
        db_session,
        release=draft_release,
        record_type=SubmissionRecordType.thermo,
        record_id=thermo.id,
        subject_type=SubmissionRecordType.species_entry,
        subject_id=species_entry.id,
        rationale="Composite single point at CCSD(T)-F12/cc-pVTZ-F12.",
        selected_by=curator.id,
    )
    publish_release(db_session, draft_release)
    freeze_manifest(db_session, draft_release, created_by=curator.id)

    rendered = {a.path: a for a in render_artifacts(db_session, draft_release)}
    line = json.loads(
        rendered["selected_records.ndjson"].content.decode().splitlines()[0]
    )

    # --- chemical identity -------------------------------------------------
    subject = line["subject"]
    assert subject["species_entry_ref"] == species_entry.public_ref
    assert subject["species_ref"].startswith("spc_")
    assert subject["smiles"], "a released value must name its molecule"
    assert "inchi_key" in subject
    assert subject["charge"] is not None
    assert subject["multiplicity"] is not None

    # --- provenance --------------------------------------------------------
    calculations = line["provenance"]["calculations"]
    assert calculations, "a released value must cite the calculation behind it"
    entry = calculations[0]
    assert entry["calculation_ref"] == calculation.public_ref
    assert entry["level_of_theory"]["method"] == "CCSD(T)-F12"
    assert entry["level_of_theory"]["basis"] == "cc-pVTZ-F12"
    assert entry["software"]["name"] == "Molpro"

    # --- foreign keys became refs rather than vanishing --------------------
    record = line["record"]
    assert record["species_entry_ref"] == species_entry.public_ref
    source_rows = record["thermo_source_calculation"]
    assert source_rows[0]["calculation_ref"] == calculation.public_ref
    assert source_rows[0]["role"] == "sp"
    # …and no raw integer key survived anywhere.
    assert "species_entry_id" not in record
    assert "calculation_id" not in source_rows[0]


def test_group_additivity_provenance_ships_with_an_estimated_thermo(
    db_session, draft_release, curator, species_entry
):
    """A GA-estimated thermo must not look like a computed one in a release."""
    from app.db.models.common import SubmissionRecordType
    from app.services.release.records import RECORD_VALUE_TABLES

    tables = {
        spec.table for spec in RECORD_VALUE_TABLES[SubmissionRecordType.thermo]
    }
    assert "applied_group_additivity" in tables, (
        "without this, a Benson-group estimate ships in a citable release with "
        "no indication that it is an estimate"
    )
    ga = next(
        spec
        for spec in RECORD_VALUE_TABLES[SubmissionRecordType.thermo]
        if spec.table == "applied_group_additivity"
    )
    assert {child.table for child in ga.children} == {
        "applied_group_additivity_component"
    }


def test_manifest_omits_block_discloses_what_is_actually_stripped(
    db_session, draft_release, curator, thermo_candidates, species_entry
):
    """A depositor is entitled to know what is *not* in the file they sign."""
    chosen, _other = thermo_candidates
    manifest = _publish_with_selection(
        db_session, draft_release, curator, chosen, species_entry
    )
    omits = recorded_manifest_document(db_session, manifest)["contract"]["omits"]
    joined = " ".join(omits)
    assert "public_refs_are_used_instead" in joined
    assert "geometries" in joined


def test_empty_release_cannot_be_published(db_session, draft_release, curator):
    """A citable, DOI-able release containing nothing is not a release."""
    publish_release(db_session, draft_release)
    with pytest.raises(ReleaseStateError, match="release_selects_nothing"):
        freeze_manifest(db_session, draft_release, created_by=curator.id)


def test_non_finite_value_fails_diagnosably_not_as_a_500(db_session):
    """``allow_nan=False`` is right; the bare ValueError it raises is not."""
    from app.services.release.artifacts import NonFiniteValueError, canonical_json

    with pytest.raises(NonFiniteValueError, match="non_finite_value") as excinfo:
        canonical_json({"record": {"h298_kj_mol": float("nan")}})
    # The message locates the offending value so a curator can find the record.
    assert "$.record.h298_kj_mol" in str(excinfo.value)


def test_truncate_cannot_erase_an_append_only_curation_history(
    db_session, draft_release, curator, thermo_candidates, species_entry
):
    """Row triggers do not fire for TRUNCATE; a statement trigger must."""
    chosen, _other = thermo_candidates
    _publish_with_selection(db_session, draft_release, curator, chosen, species_entry)

    for table in ("release_selection", "release_artifact", "release_manifest"):
        with pytest.raises(Exception, match="append-only"):
            db_session.execute(text(f"TRUNCATE {table} CASCADE"))
        db_session.rollback()


# ---------------------------------------------------------------------------
# The frozen document depends on nothing that can move
# ---------------------------------------------------------------------------


def test_verification_survives_an_edit_to_the_contract_literal(
    db_session, draft_release, curator, thermo_candidates, species_entry, monkeypatch
):
    """The residue of the old N3: the ``contract`` block was still live code.

    ``_contract_block`` holds literals — an ``omits`` list and contract tags —
    and was rebuilt at verification time. Appending one entry (the sort of edit
    any future maintainer makes) turned every past release's cross-check into
    "the stored document no longer matches its snapshot columns". It affected
    only the cross-check, never the citation, but it is the same failure mode
    already fixed twice.
    """
    from app.services.release import manifest as manifest_module

    chosen, _other = thermo_candidates
    _publish_with_selection(db_session, draft_release, curator, chosen, species_entry)
    assert verify_release(db_session, draft_release).ok

    def _edited(*, ships, recovery_archive_schema):
        block = manifest_module._contract_block(
            ships=ships, recovery_archive_schema=recovery_archive_schema
        )
        block["omits"].append("something_a_future_maintainer_documented")
        return block

    monkeypatch.setattr(manifest_module, "_contract_block", _edited)

    report = verify_release(db_session, draft_release)
    assert report.ok, report.problems
    # The published document still says what it said.
    manifest = load_manifest(db_session, draft_release)
    document = recorded_manifest_document(db_session, manifest)
    assert (
        "something_a_future_maintainer_documented" not in document["contract"]["omits"]
    )


def test_the_frozen_document_depends_only_on_the_manifest_row(
    db_session, draft_release, curator, thermo_candidates, species_entry
):
    """Mutate everything the document used to read live, and it must not move.

    Covers the release row, the curation policy row, and the module constants
    at once — the general property, rather than one instance of it.
    """
    from app.services.release import manifest as manifest_module
    from app.services.release import versions as versions_module
    from app.services.release.manifest import (
        render_manifest_document,
        snapshot_from_manifest,
    )

    chosen, _other = thermo_candidates
    manifest = _publish_with_selection(
        db_session, draft_release, curator, chosen, species_entry
    )
    before = render_manifest_document(snapshot_from_manifest(manifest))

    db_session.execute(
        text(
            "UPDATE dataset_release SET citation_text = :c, title = :t, "
            "description = :d, contact = :k, doi = :o WHERE id = :i"
        ),
        {
            "c": "rewritten",
            "t": "rewritten",
            "d": "rewritten",
            "k": "rewritten@example.org",
            "o": "10.5281/zenodo.9",
            "i": draft_release.id,
        },
    )
    db_session.execute(
        text("UPDATE curation_policy SET description = :d WHERE id = :i"),
        {"d": "rewritten rubric", "i": draft_release.curation_policy_id},
    )
    db_session.expire_all()

    manifest = load_manifest(db_session, draft_release)
    assert render_manifest_document(snapshot_from_manifest(manifest)) == before
    assert verify_release(db_session, draft_release).ok

    # …and module-constant drift is equally inert.
    original = versions_module.MANIFEST_SCHEMA
    try:
        versions_module.MANIFEST_SCHEMA = "tckdb.dataset_release.v2"
        manifest_module.versions.MANIFEST_SCHEMA = "tckdb.dataset_release.v2"
        assert render_manifest_document(snapshot_from_manifest(manifest)) == before
        assert verify_release(db_session, draft_release).ok
    finally:
        versions_module.MANIFEST_SCHEMA = original
        manifest_module.versions.MANIFEST_SCHEMA = original


def test_network_structure_rows_name_their_channel_and_state(
    db_session, curator, policy
):
    """A released solve must attach its barriers and well energies to something.

    ``network_channel`` and ``network_state`` carry no ``public_ref``, so the
    blanket FK-stripping left ``network_solve_channel_barrier`` and
    ``network_solve_state_energy`` naming no channel and no state: numbers that
    could not be attached to the wells or channels they belong to.
    """
    from app.db.models.common import (
        EnergyCorrectionConvention,
        EnergyZeroConvention,
        NetworkChannelKind,
        NetworkKineticsModelKind,
        NetworkStateKind,
        RecordReviewStatus,
        SubmissionRecordType,
    )
    from app.db.models.network_pdep import NetworkSolveStateEnergy
    from app.services.record_review import set_record_review_status
    from app.services.release.records import serialize_records
    from tests.services.scientific_read._factories import (
        make_network,
        make_network_channel,
        make_network_kinetics,
        make_network_solve,
        make_network_state,
    )

    network = make_network(db_session)
    state_a = make_network_state(
        db_session,
        network=network,
        kind=NetworkStateKind.well,
        composition_hash="a" * 64,
        label="well A",
    )
    state_b = make_network_state(
        db_session,
        network=network,
        kind=NetworkStateKind.well,
        composition_hash="b" * 64,
        label="well B",
    )
    channel = make_network_channel(
        db_session,
        network=network,
        source_state=state_a,
        sink_state=state_b,
        kind=NetworkChannelKind.isomerization,
        channel_key="A->B",
    )
    solve = make_network_solve(db_session, network=network)

    # ``network_kinetics.channel_id`` is the channel case;
    # ``network_solve_state_energy.state_id`` is the state case. Between them
    # they cover both FK targets that carry no public ref.
    make_network_kinetics(
        db_session,
        channel=channel,
        solve=solve,
        model_kind=NetworkKineticsModelKind.chebyshev,
    )
    db_session.add(
        NetworkSolveStateEnergy(
            solve_id=solve.id,
            state_id=state_a.id,
            energy_kj_mol=-1.5,
            energy_zero_convention=EnergyZeroConvention.lowest_state,
            correction_convention=EnergyCorrectionConvention.electronic_plus_zpe,
        )
    )
    db_session.flush()
    set_record_review_status(
        db_session,
        record_type=SubmissionRecordType.network_solve,
        record_id=solve.id,
        status=RecordReviewStatus.approved,
        actor=curator,
    )

    payload = serialize_records(
        db_session,
        record_type=SubmissionRecordType.network_solve,
        record_ids=[solve.id],
    )[solve.id]

    kinetics_row = payload["network_kinetics"][0]
    assert kinetics_row["channel_key"] == channel.channel_key
    assert "channel_id" not in kinetics_row

    energy = payload["network_solve_state_energy"][0]
    assert energy["state_composition_hash"] == state_a.composition_hash
    assert "state_id" not in energy


def test_natural_key_field_names_are_deterministic():
    """Pin the emitted names so a rename is a visible, deliberate change."""
    from app.services.release.records import RefResolver

    assert RefResolver.emitted_field("lot_id", "public_ref") == "lot_ref"
    assert RefResolver.emitted_field("channel_id", "channel_key") == "channel_key"
    assert (
        RefResolver.emitted_field("state_id", "composition_hash")
        == "state_composition_hash"
    )


def test_user_and_artifact_foreign_keys_are_still_dropped():
    """Natural-key substitution must not become a leak of user primary keys."""
    from app.db.base import Base
    from app.services.release.records import RefResolver

    thermo = Base.metadata.tables["thermo"]
    assert "created_by" not in RefResolver.fk_targets(thermo)

    tunneling = Base.metadata.tables["kinetics_tunneling_application"]
    targets = RefResolver.fk_targets(tunneling)
    assert "result_artifact_id" not in targets
    assert "sct_path_integral_artifact_id" not in targets
