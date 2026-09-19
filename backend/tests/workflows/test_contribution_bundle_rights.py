"""The ``rights`` fragment survives a bundle round trip, hosted-actor attested.

Submit a bundle carrying ``submission.rights`` and the hosted importer records
a ``depositor_agreement`` by the authenticated submitter -- never by the
local exporter label. Export the imported records again and the bundle
carries the same fragment, read from the standing attestation.
"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select

from app.db.models.app_user import AppUser
from app.db.models.common import AppUserRole, RightsBasisKind, SubmissionRecordType
from app.db.models.submission_rights import SubmissionRightsAttestation
from app.schemas.workflows.contribution_bundle import ContributionBundleV0
from app.services.contribution_bundle_export import (
    deposit_rights_for_records,
    export_thermo_bundle,
)
from app.services.rights import standing_attestation
from app.workflows.contribution_bundle_submit import submit_contribution_bundle

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLES_DIR = REPO_ROOT / "examples" / "bundles"

RIGHTS = {
    "license": "CC-BY-4.0",
    "depositor_attests_right_to_license": True,
    "source_terms": "Our own Gaussian 16 runs, 2026.",
}


def _bundle_with_rights() -> ContributionBundleV0:
    raw = json.loads((EXAMPLES_DIR / "thermo-bundle-v0.json").read_text())
    raw["submission"]["rights"] = dict(RIGHTS)
    return ContributionBundleV0.model_validate(raw)


def _submitter(session) -> AppUser:
    user = AppUser(username="bundle-rights-submitter", role=AppUserRole.user)
    session.add(user)
    session.flush()
    return user


def test_bundle_submit_records_the_depositor_agreement(db_session):
    bundle = _bundle_with_rights()
    actor = _submitter(db_session)

    result = submit_contribution_bundle(db_session, bundle, actor=actor)

    row = standing_attestation(db_session, submission_id=result.submission_id)
    assert row is not None
    assert row.basis is RightsBasisKind.depositor_agreement
    assert row.license_id == "CC-BY-4.0"
    assert row.source_terms == RIGHTS["source_terms"]
    # The hosted actor, not the bundle's exporter label.
    assert row.attested_by == actor.id
    assert bundle.exporter.local_user_label != actor.username
    assert (
        db_session.scalar(
            select(SubmissionRightsAttestation).where(
                SubmissionRightsAttestation.submission_id == result.submission_id
            )
        )
        is row
    )


def test_a_bundle_without_rights_records_nothing(db_session):
    raw = json.loads((EXAMPLES_DIR / "thermo-bundle-v0.json").read_text())
    assert "rights" not in raw["submission"]
    bundle = ContributionBundleV0.model_validate(raw)
    result = submit_contribution_bundle(db_session, bundle, actor=_submitter(db_session))
    assert standing_attestation(db_session, submission_id=result.submission_id) is None


def test_export_carries_the_same_fragment_and_round_trips(db_session):
    bundle = _bundle_with_rights()
    actor = _submitter(db_session)
    result = submit_contribution_bundle(db_session, bundle, actor=actor)
    thermo_ids = [
        record.record_id
        for record in result.records
        if record.record_type.value == SubmissionRecordType.thermo.value
    ]
    assert thermo_ids, "the example bundle imports at least one thermo record"

    fragment = deposit_rights_for_records(
        db_session, record_type=SubmissionRecordType.thermo, record_ids=thermo_ids
    )
    assert fragment is not None
    assert fragment.model_dump() == bundle.submission.rights.model_dump()

    exported = export_thermo_bundle(
        db_session,
        thermo_ids=thermo_ids,
        title="round trip",
        summary="Re-export of the imported records.",
        exporter_label="round-tripper",
        rights=fragment,
    )
    assert exported.submission.rights == bundle.submission.rights
    # …and through JSON, the way the CLI writes it.
    again = ContributionBundleV0.model_validate(exported.model_dump(mode="json"))
    assert again.submission.rights == bundle.submission.rights


def test_the_exporter_never_invents_consent(db_session):
    raw = json.loads((EXAMPLES_DIR / "thermo-bundle-v0.json").read_text())
    bundle = ContributionBundleV0.model_validate(raw)
    result = submit_contribution_bundle(db_session, bundle, actor=_submitter(db_session))
    thermo_ids = [
        r.record_id for r in result.records if r.record_type.value == "thermo"
    ]
    assert (
        deposit_rights_for_records(
            db_session, record_type=SubmissionRecordType.thermo, record_ids=thermo_ids
        )
        is None
    )
