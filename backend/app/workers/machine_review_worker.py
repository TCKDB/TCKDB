"""In-process machine-review worker.

Claims queued machine-review jobs, runs the reviewer, and records the result.
Runs as a daemon thread (:func:`run_worker_thread`) or as
``python -m app.workers.machine_review_worker``.

Database work happens in short transactions: no session is held open while the
reviewer runs. The job's terminal state and the review's audit event are written
in one final transaction behind the attempt fence, so a worker that lost its
lease records nothing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import threading
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.api.config import settings
from app.api.deps import SessionLocal
from app.db.models.common import (
    MachineReviewJobEventKind,
    MachineReviewJobFailure,
    MachineReviewJobStatus,
)
from app.db.models.machine_review_job import MachineReviewJob
from app.db.models.submission import Submission
from app.services.machine_review import jobs
from app.services.machine_review.providers.fake import ScriptedFakeMachineReviewAgent
from app.services.machine_review.providers.interface import (
    MachineReviewContext,
    MachineReviewProvider,
    parse_machine_review_payload,
)
from app.services.machine_review.run import (
    MachineReviewCancelled,
    _failure_text,
    build_review_context,
    invoke_provider,
    record_review,
    resolve_provider,
)
from app.services.machine_review.schemas import MachineReviewProviderResult, MachineReviewStatus
from app.services.machine_review.switches import read_machine_review_switches

logger = logging.getLogger(__name__)

_IDLE_SLEEP = 2.0
_HEARTBEAT_INTERVAL = 30.0


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class _Heartbeat(threading.Thread):
    """Extends a job's lease on its own short transactions until stopped."""

    def __init__(self, session_factory: Callable[[], Session], job_id: int, attempt: int, interval: float) -> None:
        super().__init__(name=f"machine-review-heartbeat-{job_id}", daemon=True)
        self._factory = session_factory
        self._job_id = job_id
        self._attempt = attempt
        self._interval = interval
        self._stop_event = threading.Event()

    def run(self) -> None:
        while not self._stop_event.wait(self._interval):
            try:
                with self._factory() as session:
                    jobs.heartbeat(session, self._job_id, self._attempt)
                    session.commit()
            except Exception:
                logger.exception("machine-review heartbeat failed for job %s", self._job_id)

    def stop(self) -> None:
        self._stop_event.set()
        self.join(timeout=5)


def _context_hash(context: MachineReviewContext) -> str:
    canonical = json.dumps(context.model_dump(mode="json"), sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def process_one_cycle(
    session_factory: Callable[[], Session] = SessionLocal,
    settings_obj: Any = settings,
    *,
    provider: MachineReviewProvider | None = None,
    heartbeat: bool = True,
    now: Callable[[], datetime] = _utcnow,
) -> bool:
    """Reap expired leases, claim one job and run it; return whether a job was claimed.

    ``provider`` overrides the configured reviewer, as in
    :func:`~app.services.machine_review.run.run_machine_review_for_submission`.
    """
    with session_factory() as session:
        jobs.reap_expired(session, now())
        job = jobs.claim_next_job(session, now=now(), settings_obj=settings_obj)
        if job is None:
            session.commit()
            return False
        job_id, attempt = job.id, job.attempts
        submission_id, provider_key = job.submission_id, job.provider_key
        if not read_machine_review_switches(session, settings_obj).review_allowed:
            jobs.finish_job(
                session,
                job_id,
                attempt,
                status=MachineReviewJobStatus.cancelled,
                failure_code=MachineReviewJobFailure.machine_review_disabled,
                settings_obj=settings_obj,
            )
        session.commit()
        session.refresh(job)
        if job.status is not MachineReviewJobStatus.running:
            return True

    beat = None
    if heartbeat:
        beat = _Heartbeat(session_factory, job_id, attempt, _HEARTBEAT_INTERVAL)
        beat.start()
    try:
        _run_claimed_job(
            session_factory,
            settings_obj,
            job_id=job_id,
            attempt=attempt,
            submission_id=submission_id,
            provider_key=provider_key,
            provider=provider,
        )
    finally:
        if beat is not None:
            beat.stop()
    return True


def _run_claimed_job(
    session_factory: Callable[[], Session],
    settings_obj: Any,
    *,
    job_id: int,
    attempt: int,
    submission_id: int,
    provider_key: Any,
    provider: MachineReviewProvider | None,
) -> None:
    result: MachineReviewProviderResult | None = None
    reason: str | None = None
    code = MachineReviewJobFailure.internal_error
    parse_failed = False
    provider_name: str | None = None

    try:
        with session_factory() as session:
            context = build_review_context(session, submission_id)
            job = session.get_one(MachineReviewJob, job_id)
            job.context_hash = _context_hash(context)
            jobs.append_event(
                session, job, MachineReviewJobEventKind.context_built, status_code="ok", settings_obj=settings_obj
            )
            session.commit()
    except Exception as exc:
        context = None
        reason = _failure_text("Machine review failed to review", exc, settings_obj)

    if context is not None:
        selected, reason = resolve_provider(settings_obj, provider)
        if selected is None:
            code = MachineReviewJobFailure.config_error
        elif provider is None and settings_obj.ai_review_assistant_mode != jobs.MODE_FOR_KEY.get(provider_key):
            selected, reason, code = None, "Machine review provider does not match the job.", MachineReviewJobFailure.config_error
        if selected is not None:
            provider_name = type(selected).__name__
            try:
                if isinstance(selected, ScriptedFakeMachineReviewAgent):
                    result, reason, parse_failed = _run_scripted(
                        session_factory, settings_obj, selected, context, job_id
                    )
                else:
                    result, reason = invoke_provider(selected, context, settings_obj)
            except MachineReviewCancelled:
                with session_factory() as session:
                    jobs.finish_job(
                        session, job_id, attempt, status=MachineReviewJobStatus.cancelled, settings_obj=settings_obj
                    )
                    session.commit()
                return
            if parse_failed:
                code = MachineReviewJobFailure.invalid_output

    with session_factory() as session:
        submission = session.get_one(Submission, submission_id)
        outcome, event = record_review(
            session,
            submission=submission,
            result=result,
            failure_reason=reason,
            provider_name=provider_name,
            settings_obj=settings_obj,
        )
        job = session.get_one(MachineReviewJob, job_id)
        jobs.append_event(
            session,
            job,
            MachineReviewJobEventKind.parse_failed if parse_failed else MachineReviewJobEventKind.final_output,
            status_code="invalid" if parse_failed else ("ok" if reason is None else "failed"),
            settings_obj=settings_obj,
        )
        failed = outcome.result.status is MachineReviewStatus.machine_review_failed
        if not failed and event is None:
            failed, reason, code = True, "The review could not be recorded.", MachineReviewJobFailure.internal_error
        landed = jobs.finish_job(
            session,
            job_id,
            attempt,
            status=MachineReviewJobStatus.failed if failed else MachineReviewJobStatus.succeeded,
            failure_code=code if failed else None,
            failure_detail=(reason or outcome.failure_reason) if failed else None,
            audit_event=event,
            settings_obj=settings_obj,
        )
        if landed:
            session.commit()
        else:
            session.rollback()


def _run_scripted(
    session_factory: Callable[[], Session],
    settings_obj: Any,
    agent: ScriptedFakeMachineReviewAgent,
    context: MachineReviewContext,
    job_id: int,
) -> tuple[MachineReviewProviderResult | None, str | None, bool]:
    """Run the scripted agent with progress events; return ``(result, reason, parse_failed)``."""

    def on_step(step: int) -> None:
        with session_factory() as session:
            job = session.get_one(MachineReviewJob, job_id)
            job.step_count = step
            jobs.append_event(
                session, job, MachineReviewJobEventKind.model_turn, status_code="ok", settings_obj=settings_obj
            )
            session.commit()

    def cancelled() -> bool:
        with session_factory() as session:
            flag = jobs.is_cancel_requested(session, job_id)
            session.rollback()
            return flag

    try:
        text = agent.run(context, on_step=on_step, cancelled=cancelled)
    except MachineReviewCancelled:
        raise
    except Exception as exc:
        return None, _failure_text("Machine review failed to review", exc, settings_obj), False
    try:
        return parse_machine_review_payload(text), None, False
    except Exception as exc:
        return None, _failure_text("Machine review output was invalid", exc, settings_obj), True


def run_worker_thread(
    stop_event: threading.Event | None = None,
    *,
    cycle: Callable[[], bool] = process_one_cycle,
    poll_interval: float = _IDLE_SLEEP,
) -> threading.Thread:
    """Start the worker loop in a daemon thread; set ``stop_event`` to stop it."""
    stop = stop_event or threading.Event()

    def loop() -> None:
        while not stop.is_set():
            try:
                did_work = cycle()
            except Exception:
                logger.exception("Unexpected error in machine-review worker cycle")
                did_work = False
            if not did_work:
                stop.wait(poll_interval)

    thread = threading.Thread(target=loop, name="machine-review-worker", daemon=True)
    thread.start()
    logger.info("Machine-review worker thread started (daemon)")
    return thread


def main(argv: Sequence[str] | None = None) -> int:
    """Run the worker: one cycle with ``--once``, otherwise until interrupted."""
    parser = argparse.ArgumentParser(description="Machine-review worker")
    parser.add_argument("--once", action="store_true", help="process at most one job and exit")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    if args.once:
        process_one_cycle()
        return 0
    stop = threading.Event()
    thread = run_worker_thread(stop)
    try:
        while thread.is_alive():
            thread.join(timeout=1)
    except KeyboardInterrupt:
        stop.set()
        thread.join(timeout=10)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
