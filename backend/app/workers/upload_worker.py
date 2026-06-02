"""PostgreSQL-backed upload job worker.

Run as a standalone process:
    conda run -n tckdb_env python -m app.workers.upload_worker

Or start as an inline daemon thread from the FastAPI lifespan by setting:
    TCKDB_INLINE_WORKER=true

Multiple worker processes/threads can run safely — they compete for jobs
using ``SELECT … FOR UPDATE SKIP LOCKED``, so each job is processed exactly
once.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import SessionLocal
from app.db.models.common import SubmissionStatus, UploadJobKind, UploadJobStatus
from app.db.models.submission import Submission
from app.db.models.upload_job import UploadJob
from app.services.record_review import ReviewPolicy
from app.services.submission import mark_ingestion_failed, mark_ingestion_succeeded
from app.services.upload_submission import review_policy_for_submission

logger = logging.getLogger(__name__)

_IDLE_SLEEP = 2.0


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Job claim
# ---------------------------------------------------------------------------

def _claim_one_job(session: Session) -> UploadJob | None:
    """Atomically claim the oldest queued job using FOR UPDATE SKIP LOCKED."""
    job = session.scalars(
        select(UploadJob)
        .where(
            UploadJob.status == UploadJobStatus.queued,
            UploadJob.attempts < UploadJob.max_attempts,
        )
        .order_by(UploadJob.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).first()

    if job is None:
        return None

    job.status = UploadJobStatus.processing
    job.started_at = _utcnow()
    job.attempts += 1
    session.flush()
    return job


# ---------------------------------------------------------------------------
# Handlers — one per upload kind, each returns a JSON-serialisable dict
# ---------------------------------------------------------------------------

def _run_computed_reaction(session: Session, job: UploadJob, review_policy: ReviewPolicy) -> dict:
    from app.schemas.workflows.computed_reaction_upload import ComputedReactionUploadRequest
    from app.workflows.computed_reaction import persist_computed_reaction_upload

    request = ComputedReactionUploadRequest.model_validate(job.payload)
    return persist_computed_reaction_upload(session, request, created_by=job.created_by, review_policy=review_policy)


def _run_conformer(session: Session, job: UploadJob, review_policy: ReviewPolicy) -> dict:
    from app.schemas.workflows.conformer_upload import ConformerUploadRequest
    from app.workflows.conformer import persist_conformer_upload

    request = ConformerUploadRequest.model_validate(job.payload)
    outcome = persist_conformer_upload(
        session, request, created_by=job.created_by, review_policy=review_policy
    )
    obs = outcome.observation
    return {
        "type": "conformer_observation",
        "id": obs.id,
        "species_entry_id": obs.conformer_group.species_entry_id,
        "conformer_group_id": obs.conformer_group_id,
        "primary_calculation": {
            "request_index": outcome.primary_calculation.request_index,
            "calculation_id": outcome.primary_calculation.calculation_id,
            "type": outcome.primary_calculation.type.value,
            "role": outcome.primary_calculation.role,
        },
        "additional_calculations": [
            {
                "request_index": ref.request_index,
                "calculation_id": ref.calculation_id,
                "type": ref.type.value,
                "role": ref.role,
            }
            for ref in outcome.additional_calculations
        ],
    }


def _run_reaction(session: Session, job: UploadJob, review_policy: ReviewPolicy) -> dict:
    from app.schemas.workflows.reaction_upload import ReactionUploadRequest
    from app.workflows.reaction import persist_reaction_upload

    request = ReactionUploadRequest.model_validate(job.payload)
    entry = persist_reaction_upload(session, request, created_by=job.created_by, review_policy=review_policy)
    return {
        "type": "reaction_entry",
        "id": entry.id,
        "reaction_id": entry.reaction_id,
    }


def _run_kinetics(session: Session, job: UploadJob, review_policy: ReviewPolicy) -> dict:
    from app.schemas.workflows.kinetics_upload import KineticsUploadRequest
    from app.workflows.kinetics import persist_kinetics_upload

    request = KineticsUploadRequest.model_validate(job.payload)
    kinetics = persist_kinetics_upload(session, request, created_by=job.created_by, review_policy=review_policy)
    return {
        "type": "kinetics",
        "id": kinetics.id,
        "reaction_entry_id": kinetics.reaction_entry_id,
    }


def _run_network(session: Session, job: UploadJob, review_policy: ReviewPolicy) -> dict:
    from app.schemas.workflows.network_upload import NetworkUploadRequest
    from app.workflows.network import persist_network_upload

    request = NetworkUploadRequest.model_validate(job.payload)
    network = persist_network_upload(session, request, created_by=job.created_by, review_policy=review_policy)
    return {"type": "network", "id": network.id}


def _run_network_pdep(session: Session, job: UploadJob, review_policy: ReviewPolicy) -> dict:
    from app.schemas.workflows.network_pdep_upload import NetworkPDepUploadRequest
    from app.workflows.network_pdep import persist_network_pdep_upload

    request = NetworkPDepUploadRequest.model_validate(job.payload)
    network = persist_network_pdep_upload(session, request, created_by=job.created_by, review_policy=review_policy)
    solve_id = network.solves[0].id if network.solves else None
    return {"type": "network_pdep", "id": network.id, "solve_id": solve_id}


def _run_thermo(session: Session, job: UploadJob, review_policy: ReviewPolicy) -> dict:
    from app.schemas.workflows.thermo_upload import ThermoUploadRequest
    from app.workflows.thermo import persist_thermo_upload

    request = ThermoUploadRequest.model_validate(job.payload)
    thermo = persist_thermo_upload(session, request, created_by=job.created_by, review_policy=review_policy)
    return {
        "type": "thermo",
        "id": thermo.id,
        "species_entry_id": thermo.species_entry_id,
    }


def _run_transition_state(session: Session, job: UploadJob, review_policy: ReviewPolicy) -> dict:
    from app.schemas.workflows.transition_state_upload import TransitionStateUploadRequest
    from app.workflows.transition_state import persist_transition_state_upload

    request = TransitionStateUploadRequest.model_validate(job.payload)
    ts_entry = persist_transition_state_upload(session, request, created_by=job.created_by, review_policy=review_policy)
    return {
        "type": "transition_state_entry",
        "id": ts_entry.id,
        "transition_state_id": ts_entry.transition_state_id,
        "reaction_entry_id": ts_entry.transition_state.reaction_entry_id,
    }


def _run_transport(session: Session, job: UploadJob, review_policy: ReviewPolicy) -> dict:
    from app.schemas.workflows.transport_upload import TransportUploadRequest
    from app.workflows.transport import persist_transport_upload

    request = TransportUploadRequest.model_validate(job.payload)
    transport = persist_transport_upload(session, request, created_by=job.created_by, review_policy=review_policy)
    return {
        "type": "transport",
        "id": transport.id,
        "species_entry_id": transport.species_entry_id,
    }


_DISPATCH: dict[UploadJobKind, callable] = {
    UploadJobKind.computed_reaction: _run_computed_reaction,
    UploadJobKind.conformer:         _run_conformer,
    UploadJobKind.reaction:          _run_reaction,
    UploadJobKind.kinetics:          _run_kinetics,
    UploadJobKind.network:           _run_network,
    UploadJobKind.network_pdep:      _run_network_pdep,
    UploadJobKind.thermo:            _run_thermo,
    UploadJobKind.transition_state:  _run_transition_state,
    UploadJobKind.transport:         _run_transport,
}


# ---------------------------------------------------------------------------
# Job execution
# ---------------------------------------------------------------------------

def _submission_for_job(session: Session, job: UploadJob) -> Submission | None:
    """Return the submission wrapper created for this job at enqueue time.

    Returns ``None`` for legacy jobs enqueued before submissions were wired
    (those simply run without submission/review side effects).
    """
    return session.scalar(
        select(Submission).where(Submission.upload_job_id == str(job.id))
    )


def run_one_job(session: Session, job: UploadJob) -> None:
    """Execute a claimed job and write the result/error back to the row.

    Runs the ingestion under the job's submission: records are persisted
    under review and linked to the submission, and an ``ingestion_succeeded``
    audit event is appended. The submission status stays ``pending`` —
    successful ingestion is not scientific approval.
    """
    handler = _DISPATCH.get(job.kind)
    if handler is None:
        raise NotImplementedError(f"No handler registered for job kind {job.kind!r}")

    submission = _submission_for_job(session, job)
    policy = (
        review_policy_for_submission(submission)
        if submission is not None
        else ReviewPolicy()
    )

    result = handler(session, job, policy)

    if submission is not None:
        mark_ingestion_succeeded(
            session,
            submission=submission,
            summary=f"Ingested async {job.kind.value} job.",
        )
        if isinstance(result, dict):
            result = {**result, "submission_id": submission.id}

    job.status = UploadJobStatus.complete
    job.completed_at = _utcnow()
    job.result = result
    job.error = None


# ---------------------------------------------------------------------------
# Worker loop
# ---------------------------------------------------------------------------

def _process_one_cycle() -> bool:
    """Claim and process one job. Returns True if a job was processed."""
    with SessionLocal() as session:
        with session.begin():
            job = _claim_one_job(session)
            if job is None:
                return False
            job_id = str(job.id)
            job_kind = job.kind

        try:
            with session.begin():
                job = session.get(UploadJob, job_id)
                run_one_job(session, job)
                logger.info("job %s (%s) complete", job_id, job_kind)

        except Exception as exc:
            logger.exception("job %s (%s) failed: %s", job_id, job_kind, exc)
            # This block runs in a *fresh* transaction after the persistence
            # transaction above rolled back — so no partial scientific records
            # survive. The submission was committed at enqueue, so on terminal
            # failure we durably record the failed attempt against it.
            with session.begin():
                job = session.get(UploadJob, job_id)
                job.error = f"{type(exc).__name__}: {exc}"
                if job.attempts >= job.max_attempts:
                    job.status = UploadJobStatus.failed
                    job.completed_at = _utcnow()
                    submission = _submission_for_job(session, job)
                    if submission is not None:
                        mark_ingestion_failed(
                            session,
                            submission=submission,
                            reason=job.error,
                            details_json={"upload_job_id": job_id},
                        )
                        submission.status = SubmissionStatus.failed
                    logger.warning(
                        "job %s exhausted %d attempts — marked failed",
                        job_id, job.max_attempts,
                    )
                else:
                    job.status = UploadJobStatus.queued

    return True


def worker_loop(poll_interval: float = _IDLE_SLEEP) -> None:
    """Run the worker loop forever. Blocks the calling thread."""
    logger.info("Upload worker started (poll_interval=%.1fs)", poll_interval)
    while True:
        try:
            did_work = _process_one_cycle()
        except Exception:
            logger.exception("Unexpected error in worker cycle — sleeping before retry")
            did_work = False

        if not did_work:
            time.sleep(poll_interval)


def run_worker_thread(poll_interval: float = _IDLE_SLEEP) -> threading.Thread:
    """Start the worker loop in a daemon thread and return it."""
    thread = threading.Thread(
        target=worker_loop,
        kwargs={"poll_interval": poll_interval},
        name="upload-worker",
        daemon=True,
    )
    thread.start()
    logger.info("Upload worker thread started (daemon)")
    return thread


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os
    import sys

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    worker_loop()
