"""Read-only compatibility inventory for the Phase A creation/export profile."""

from collections.abc import Iterator

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.models.common import UploadJobKind, UploadJobStatus
from app.db.models.thermo import Thermo
from app.db.models.upload_job import UploadJob
from app.schemas.workflows.computed_reaction_upload import ComputedReactionUploadRequest
from app.schemas.workflows.thermo_upload import ThermoUploadRequest
from app.services.contribution_bundle_export import (
    ContributionBundleExportError,
    _thermo_to_upload,
)
from app.services.scientific_read.chemkin_serialize import thermo_chemkin_incompatibilities


def iter_thermo_contract_inventory(session: Session) -> Iterator[dict]:
    """Yield stable record references and reasons, without changing any row.

    Run in a read-only repeatable-read transaction for a consistent inventory.
    Queue entries are reported even if compatible: deployment must drain them
    under the old worker, not reinterpret their omitted state after upgrade.
    """
    statement = select(Thermo).options(selectinload(Thermo.nasa)).order_by(Thermo.id)
    for thermo in session.scalars(statement).yield_per(200):
        creation_errors = []
        try:
            _thermo_to_upload(thermo)
        except ContributionBundleExportError as exc:
            creation_errors.append(str(exc))
        export_errors = thermo_chemkin_incompatibilities(thermo, thermo.nasa)
        if creation_errors or export_errors:
            yield {
                "kind": "thermo", "ref": thermo.public_ref,
                "upload_incompatibilities": creation_errors,
                "chemkin_incompatibilities": export_errors,
            }
    validators = {
        UploadJobKind.thermo: ThermoUploadRequest,
        UploadJobKind.computed_reaction: ComputedReactionUploadRequest,
    }
    jobs = select(UploadJob).where(
        UploadJob.status.in_([UploadJobStatus.queued, UploadJobStatus.processing])
    ).order_by(UploadJob.created_at, UploadJob.id)
    for job in session.scalars(jobs).yield_per(200):
        errors = []
        validator = validators.get(job.kind)
        if validator is not None:
            try:
                validator.model_validate(job.payload)
            except ValidationError as exc:
                errors = [
                    {"location": list(error["loc"]), "reason": error["msg"]}
                    for error in exc.errors(include_input=False, include_context=False)
                ]
        yield {
            "kind": "upload_job", "ref": str(job.id), "upload_kind": job.kind.value,
            "status": job.status.value, "thermo_profile_checked": validator is not None,
            "upload_incompatibilities": errors,
        }
