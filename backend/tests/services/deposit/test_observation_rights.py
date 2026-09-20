"""A release's deposit may not bundle an unlicensed observation as evidence.

``molecular_property_observation`` has no seat at ``SELECTABLE_RECORD_TYPES``
or ``CANDIDATE_SOURCES`` (``app/db/models/dataset_release.py`` /
``app/services/release/records.py``), so
:func:`app.services.release.curation.publish_release`'s rights gate -- which
walks exactly that candidate set -- structurally cannot see one. The frozen
release artifacts (``selected_records.ndjson`` / ``candidate_records.ndjson``)
never carry an observation row, so gating *there* would be a check that can
never fire.

The reproduction this file opens with was green before this gate existed: an
observation about a species a release covers, licensed under nothing at all,
travelled out anyway the moment anyone built that release's ``tckdb.deposit.v1``
-- ``write_deposit`` bundles the *entire* ``tckdb.archive.v1`` evidence
archive unconditionally, unattested rows included. The first test is that
reproduction, inverted: it is the regression that proves
``_assert_observations_have_rights_basis`` (wired into ``write_deposit`` right
before the archive is written) is live.

Everything else pins the shape of the gate: what counts as "in scope" (an
observation attached to a species entry the release's own
``release_record_universe`` names as a subject -- an observation about an
unrelated species is out of scope and never blocks this release), what
"compatible" means (an exact license-identifier match, same rule as the
selectable-record gate), and that a compatible attestation lets the build
proceed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from app.db.models.app_user import AppUser
from app.db.models.common import (
    AppUserRole,
    RightsBasisKind,
    SubmissionKind,
    SubmissionRecordType,
)
from app.services.deposit.build import (
    ObservationRightsBasisIncompatibleError,
    ObservationRightsBasisMissingError,
    _assert_observations_have_rights_basis,
    write_deposit,
)
from app.services.release.curation import add_selection, publish_release
from app.services.release.manifest import freeze_manifest
from app.services.rights import record_attestation
from app.services.submission import create_submission, link_records
from scripts.paper.registry import GENERATORS
from tests.services.release._attest import deposit_and_attest
from tests.services.scientific_read._factories import (
    make_observation,
    make_species,
    make_species_entry,
    next_inchi_key,
)

_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_REPO_ROOT = _BACKEND_ROOT.parent
_GENERATOR_DIR = _BACKEND_ROOT / "scripts" / "paper"


def _allowlist(session) -> list[str]:
    return sorted(session.scalars(select(AppUser.username)))


@pytest.fixture
def depositor(db_session) -> AppUser:
    user = AppUser(username="observation-depositor", role=AppUserRole.user)
    db_session.add(user)
    db_session.flush()
    return user


def _cover_species_entry_as_a_release_subject(
    db_session, release, curator, thermo_candidates, species_entry
):
    """Make ``species_entry`` a real subject of ``release`` and publish it.

    Cheapest honest way to put a species entry into
    ``release_record_universe(...).subject_pairs``: select one of its
    already-approved, already-attested thermo candidates.
    """
    first, _second = thermo_candidates
    add_selection(
        db_session,
        release=release,
        record_type=SubmissionRecordType.thermo,
        record_id=first.id,
        subject_type=SubmissionRecordType.species_entry,
        subject_id=species_entry.id,
        rationale="Covers the species entry for the observation-rights gate.",
        selected_by=curator.id,
    )
    publish_release(db_session, release)


# ---------------------------------------------------------------------------
# The reproduction, inverted
# ---------------------------------------------------------------------------


def test_an_unattested_in_scope_observation_refuses_the_deposit(
    db_session, draft_release, curator, thermo_candidates, species_entry
):
    """Before the gate: bundled into the archive, no refusal. After: refused."""
    _cover_species_entry_as_a_release_subject(
        db_session, draft_release, curator, thermo_candidates, species_entry
    )
    make_observation(db_session, species_entry=species_entry)

    with pytest.raises(
        ObservationRightsBasisMissingError, match="^observation_rights_basis_missing:"
    ):
        _assert_observations_have_rights_basis(db_session, draft_release)


# ---------------------------------------------------------------------------
# What counts as a basis
# ---------------------------------------------------------------------------


def test_linked_but_unattested_observation_still_refuses(
    db_session, draft_release, curator, thermo_candidates, species_entry, depositor
):
    _cover_species_entry_as_a_release_subject(
        db_session, draft_release, curator, thermo_candidates, species_entry
    )
    obs = make_observation(db_session, species_entry=species_entry)
    submission = create_submission(
        db_session,
        created_by=depositor.id,
        submission_kind=SubmissionKind.other,
        title="deposit without any rights attestation",
    )
    link_records(
        db_session,
        submission=submission,
        records=[
            (SubmissionRecordType.molecular_property_observation, obs.id, None)
        ],
    )

    with pytest.raises(
        ObservationRightsBasisMissingError, match="^observation_rights_basis_missing:"
    ):
        _assert_observations_have_rights_basis(db_session, draft_release)


def test_attested_under_a_different_license_refuses(
    db_session, draft_release, curator, thermo_candidates, species_entry
):
    """``draft_release.data_license`` is ``CC-BY-4.0`` (see the release conftest)."""
    _cover_species_entry_as_a_release_subject(
        db_session, draft_release, curator, thermo_candidates, species_entry
    )
    obs = make_observation(db_session, species_entry=species_entry)
    deposit_and_attest(
        db_session,
        depositor=curator,
        records=[(SubmissionRecordType.molecular_property_observation, obs.id)],
        license_id="CC0-1.0",
    )

    with pytest.raises(
        ObservationRightsBasisIncompatibleError,
        match="^observation_rights_basis_incompatible:",
    ):
        _assert_observations_have_rights_basis(db_session, draft_release)


def test_attested_under_a_compatible_license_passes(
    db_session, draft_release, curator, thermo_candidates, species_entry
):
    _cover_species_entry_as_a_release_subject(
        db_session, draft_release, curator, thermo_candidates, species_entry
    )
    obs = make_observation(db_session, species_entry=species_entry)
    deposit_and_attest(
        db_session,
        depositor=curator,
        records=[(SubmissionRecordType.molecular_property_observation, obs.id)],
        license_id="CC-BY-4.0",
    )

    # No raise.
    _assert_observations_have_rights_basis(db_session, draft_release)


# ---------------------------------------------------------------------------
# Scope: only observations tied to a subject this release actually covers
# ---------------------------------------------------------------------------


def test_an_observation_about_an_unrelated_species_never_blocks_this_release(
    db_session, draft_release, curator, thermo_candidates, species_entry
):
    """An observation about a species this release never selected or
    candidated evidence for is out of scope, unattested or not."""
    _cover_species_entry_as_a_release_subject(
        db_session, draft_release, curator, thermo_candidates, species_entry
    )
    other_species = make_species(
        db_session, smiles="CCN", inchi_key=next_inchi_key("UNRELATED")
    )
    other_entry = make_species_entry(db_session, species=other_species)
    make_observation(db_session, species_entry=other_entry)

    # No raise: the unrelated observation is not in this release's universe.
    _assert_observations_have_rights_basis(db_session, draft_release)


def test_a_release_with_no_covered_subjects_is_a_cheap_no_op(
    db_session, draft_release, species_entry
):
    """An unpublished draft with no standing selection covers no subject."""
    make_observation(db_session, species_entry=species_entry)
    # No raise: draft_release has no selection yet, so subject_pairs is empty.
    _assert_observations_have_rights_basis(db_session, draft_release)


def test_an_identity_unresolved_observation_is_unreachable_by_scope(
    db_session, draft_release, curator, thermo_candidates, species_entry
):
    """A ``species_entry_id IS NULL`` row can never match any subject id."""
    _cover_species_entry_as_a_release_subject(
        db_session, draft_release, curator, thermo_candidates, species_entry
    )
    make_observation(db_session, species_entry=None)

    # No raise: nothing to look up, by construction.
    _assert_observations_have_rights_basis(db_session, draft_release)


# ---------------------------------------------------------------------------
# The gate is actually wired into write_deposit (review round 2, F1)
#
# Every test above calls ``_assert_observations_have_rights_basis`` directly.
# That alone cannot catch the wiring itself going missing: deleting the call
# site in ``write_deposit`` (app/services/deposit/build.py, right before
# ``write_archive(...)``) left every one of those tests green, because none
# of them ever runs ``write_deposit``. These two do.
# ---------------------------------------------------------------------------


def test_write_deposit_refuses_an_unattested_in_scope_observation(
    db_session, draft_release, curator, thermo_candidates, species_entry, tmp_path
):
    _cover_species_entry_as_a_release_subject(
        db_session, draft_release, curator, thermo_candidates, species_entry
    )
    make_observation(db_session, species_entry=species_entry)
    freeze_manifest(db_session, draft_release, created_by=curator.id)

    with pytest.raises(
        ObservationRightsBasisMissingError, match="^observation_rights_basis_missing:"
    ):
        write_deposit(
            db_session,
            release_tag=draft_release.tag,
            output_dir=tmp_path / "out",
            author_accounts=_allowlist(db_session),
            repo_root=_REPO_ROOT,
            generators=GENERATORS,
            generator_dir=_GENERATOR_DIR,
            require_clean_tree=False,
            require_exact_tag=False,
        )
    assert not (tmp_path / "out").exists(), "a refused build leaves nothing behind"


def test_write_deposit_builds_over_an_attested_in_scope_observation(
    db_session, draft_release, curator, thermo_candidates, species_entry, tmp_path
):
    """The positive case: a compatibly-attested in-scope observation does not block."""
    _cover_species_entry_as_a_release_subject(
        db_session, draft_release, curator, thermo_candidates, species_entry
    )
    obs = make_observation(db_session, species_entry=species_entry)
    deposit_and_attest(
        db_session,
        depositor=curator,
        records=[(SubmissionRecordType.molecular_property_observation, obs.id)],
        license_id="CC-BY-4.0",
    )
    freeze_manifest(db_session, draft_release, created_by=curator.id)

    result = write_deposit(
        db_session,
        release_tag=draft_release.tag,
        output_dir=tmp_path / "out",
        author_accounts=_allowlist(db_session),
        repo_root=_REPO_ROOT,
        generators=GENERATORS,
        generator_dir=_GENERATOR_DIR,
        require_clean_tree=False,
        require_exact_tag=False,
    )
    assert result.path.exists()


# ---------------------------------------------------------------------------
# licenses_match is basis-agnostic (review round 2, F3): a source_terms
# attestation must pass the gate exactly like a depositor_agreement one.
# Forcing ``basis is RightsBasisKind.depositor_agreement`` in the gate would
# leave this test the only thing catching it.
# ---------------------------------------------------------------------------


def test_a_source_terms_attestation_under_a_matching_license_passes(
    db_session, draft_release, curator, thermo_candidates, species_entry
):
    _cover_species_entry_as_a_release_subject(
        db_session, draft_release, curator, thermo_candidates, species_entry
    )
    obs = make_observation(db_session, species_entry=species_entry)
    submission = create_submission(
        db_session,
        created_by=curator.id,
        submission_kind=SubmissionKind.other,
        title="source-terms attested deposit",
    )
    link_records(
        db_session,
        submission=submission,
        records=[
            (SubmissionRecordType.molecular_property_observation, obs.id, None)
        ],
    )
    record_attestation(
        db_session,
        submission=submission,
        license_id="CC-BY-4.0",
        basis=RightsBasisKind.source_terms,
        actor=curator,
        source_terms="NIST ThermoML Archive terms of use permit redistribution.",
    )

    # No raise: a source_terms attestation under a matching license passes
    # the gate exactly as a depositor_agreement one does.
    _assert_observations_have_rights_basis(db_session, draft_release)
