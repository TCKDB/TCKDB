"""Shared fixtures for the dataset-release service tests.

The release layer needs a real curator, a real scientific record, and a real
review state, so these fixtures build a small but complete corpus: one species
entry with two competing thermo candidates, both approved, so a curator has an
actual choice to make and the "candidates are still retrievable" claim has
something to be true about.
"""

from __future__ import annotations

import pytest

from app.db.models.app_user import AppUser
from app.db.models.common import AppUserRole, RecordReviewStatus, SubmissionRecordType
from app.services.record_review import set_record_review_status
from app.services.release.curation import (
    create_release,
    resolve_curation_policy,
)
from app.services.submission import link_records
from tests.services.release._attest import deposit_and_attest
from tests.services.scientific_read._factories import (
    make_species,
    make_species_entry,
    make_thermo_scalar,
)


@pytest.fixture
def curator(db_session) -> AppUser:
    user = AppUser(username="release-curator", role=AppUserRole.curator)
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture
def second_curator(db_session) -> AppUser:
    user = AppUser(username="release-curator-2", role=AppUserRole.curator)
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture
def policy(db_session, curator):
    return resolve_curation_policy(
        db_session,
        name="tckdb-benchmark",
        version="1.0",
        description="Prefer the highest-level composite single point.",
        criteria={"requires_review_status": "approved"},
        created_by=curator.id,
    )


@pytest.fixture
def species_entry(db_session):
    species = make_species(db_session, smiles="CCO")
    return make_species_entry(db_session, species=species)


@pytest.fixture
def attested_submission(db_session, curator):
    """One deposit, by the curator, attested ``CC-BY-4.0`` by its depositor.

    Empty until a fixture links records to it. The curator plays the
    depositor here because a ``depositor_agreement`` must be made by the
    account that created the submission, and the curator is the one user
    every release test already has.
    """
    return deposit_and_attest(
        db_session,
        depositor=curator,
        records=[],
        title="release fixture deposit",
    )


@pytest.fixture
def thermo_candidates(db_session, species_entry, curator, attested_submission):
    """Two approved thermo candidates for one species entry, both licensed.

    Linked to :func:`attested_submission` because a release refuses a record
    nobody agreed to license; a candidate that is approved but unattested is
    its own test case (``test_release_rights.py``), not the default corpus.
    """
    first = make_thermo_scalar(
        db_session, species_entry=species_entry, h298_kj_mol=-234.5, s298_j_mol_k=281.6
    )
    second = make_thermo_scalar(
        db_session, species_entry=species_entry, h298_kj_mol=-235.9, s298_j_mol_k=280.9
    )
    for row in (first, second):
        set_record_review_status(
            db_session,
            record_type=SubmissionRecordType.thermo,
            record_id=row.id,
            status=RecordReviewStatus.approved,
            actor=curator,
            note="approved for release fixture",
        )
    link_records(
        db_session,
        submission=attested_submission,
        records=[
            (SubmissionRecordType.thermo, first.id, None),
            (SubmissionRecordType.thermo, second.id, None),
        ],
    )
    return first, second


@pytest.fixture
def draft_release(db_session, policy, curator):
    return create_release(
        db_session,
        tag="2026.07.0",
        title="TCKDB curated thermochemistry, July 2026",
        curation_policy=policy,
        data_license="CC-BY-4.0",
        code_license="MIT",
        citation_text="TCKDB curated dataset release 2026.07.0.",
        contact="tckdb-maintainers@example.org",
        changelog_entry="First curated release.",
        created_by=curator.id,
    )
