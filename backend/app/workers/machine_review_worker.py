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
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.api.config import settings
from app.api.deps import SessionLocal
from app.db.models.app_user import AppUser
from app.db.models.common import (
    MachineReviewJobEventKind,
    MachineReviewJobFailure,
    MachineReviewJobStatus,
)
from app.db.models.machine_review_job import MachineReviewJob
from app.db.models.submission import Submission
from app.services.machine_review import jobs
from app.services.machine_review import lessons as lesson_store
from app.services.machine_review.agent.guidance import GuidanceEntry, GuidanceIndex
from app.services.machine_review.agent.loop import LoopResult, RunBudget
from app.services.machine_review.agent.tools import ToolRegistry
from app.services.machine_review.guidance_corpus import default_guidance_index, guidance_seed_codes
from app.services.machine_review.offer import offer_violation, offered_refs_from_context
from app.services.machine_review.providers.fake import ScriptedFakeMachineReviewAgent
from app.services.machine_review.providers.interface import (
    MachineReviewContext,
    MachineReviewProvider,
    parse_machine_review_payload,
)
from app.services.machine_review.providers.local import LocalMachineReviewProvider
from app.services.machine_review.providers.openai_transport import ModelContextExceededError
from app.services.machine_review.run import (
    _MAX_FAILURE_TEXT_CHARS,
    _MAX_MODEL_CHARS,
    MachineReviewCancelled,
    ReviewBudgetExhausted,
    _clip,
    _failure_code_for,
    _failure_text,
    build_review_context_with_records,
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
        submission_id, provider_key, job_model = job.submission_id, job.provider_key, job.model
        switches = read_machine_review_switches(session, settings_obj)
        requester = session.get(AppUser, job.requested_by)
        if not switches.review_allowed or not jobs.requester_may_run(switches, requester):
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
            job_model=job_model,
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
    job_model: str | None,
    provider: MachineReviewProvider | None,
) -> None:
    result: MachineReviewProviderResult | None = None
    reason: str | None = None
    code = MachineReviewJobFailure.internal_error
    parse_failed = False
    provider_name: str | None = None
    summaries: dict[tuple[str, str], Any] = {}
    lessons: list[GuidanceEntry] = []
    tokens: tuple[int, int] | None = None

    try:
        with session_factory() as session:
            context, summaries = build_review_context_with_records(session, submission_id, settings_obj)
            lessons = lesson_store.verified_lesson_entries(
                session, record_types={record_type for record_type, _ in offered_refs_from_context(context)}
            )
            job = session.get_one(MachineReviewJob, job_id)
            job.context_hash = _context_hash(context)
            jobs.append_event(
                session, job, MachineReviewJobEventKind.context_built, outcome="ok", settings_obj=settings_obj
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
                elif isinstance(selected, LocalMachineReviewProvider):
                    agent = _run_agent(session_factory, settings_obj, selected, context, summaries, job_id, lessons)
                    result, reason, parse_failed, tokens = agent.result, agent.reason, agent.parse_failed, agent.tokens
                    if agent.budget_exhausted:
                        with session_factory() as session:
                            _store_tokens(session.get_one(MachineReviewJob, job_id), tokens)
                            jobs.finish_job(
                                session,
                                job_id,
                                attempt,
                                status=MachineReviewJobStatus.failed,
                                failure_code=MachineReviewJobFailure.budget_exhausted,
                                failure_detail="The review ran out of time or room before it could answer.",
                                settings_obj=settings_obj,
                            )
                            session.commit()
                        return
                    if agent.code is not None:
                        code = agent.code
                else:
                    if _cancel_requested(session_factory, job_id):
                        raise MachineReviewCancelled
                    result, reason, call_code = invoke_provider(selected, context, settings_obj)
                    if result is not None:
                        violation = _offer_violation(result, context)
                        if violation is not None:
                            result, reason, call_code = None, violation, MachineReviewJobFailure.invalid_output
                    if result is None and call_code is not None:
                        code = call_code
                        parse_failed = call_code is MachineReviewJobFailure.invalid_output
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
        if jobs.is_cancel_requested(session, job_id):
            jobs.finish_job(session, job_id, attempt, status=MachineReviewJobStatus.cancelled, settings_obj=settings_obj)
            session.commit()
            return
        submission = session.get_one(Submission, submission_id)
        if result is not None:
            stamped_model = job_model or settings_obj.llm_precheck_model
            result = result.model_copy(
                update={
                    "model": _clip(stamped_model, _MAX_MODEL_CHARS) if stamped_model else None,
                    "provider": provider_name,
                }
            )
        outcome, event = record_review(
            session,
            submission=submission,
            result=result,
            failure_reason=reason,
            provider_name=provider_name,
            settings_obj=settings_obj,
            model=job_model,
        )
        job = session.get_one(MachineReviewJob, job_id)
        _store_tokens(job, tokens)
        jobs.append_event(
            session,
            job,
            MachineReviewJobEventKind.parse_failed if parse_failed else MachineReviewJobEventKind.final_output,
            outcome="invalid" if parse_failed else ("ok" if reason is None else "failed"),
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


def _store_tokens(job: MachineReviewJob, tokens: tuple[int, int] | None) -> None:
    if tokens is not None:
        job.tokens_in, job.tokens_out = tokens


def _cancel_requested(session_factory: Callable[[], Session], job_id: int) -> bool:
    with session_factory() as session:
        flag = jobs.is_cancel_requested(session, job_id)
        session.rollback()
        return flag


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
                session, job, MachineReviewJobEventKind.model_turn, outcome="ok", settings_obj=settings_obj
            )
            session.commit()

    def cancelled() -> bool:
        return _cancel_requested(session_factory, job_id)

    try:
        text = agent.run(context, on_step=on_step, cancelled=cancelled)
    except MachineReviewCancelled:
        raise
    except Exception as exc:
        return None, _failure_text("Machine review failed to review", exc, settings_obj), False
    try:
        result = parse_machine_review_payload(text)
    except Exception as exc:
        return None, _failure_text("Machine review output was invalid", exc, settings_obj), True
    violation = _offer_violation(result, context)
    if violation is not None:
        return None, violation, True
    return result, None, False


@dataclass
class _AgentRun:
    result: MachineReviewProviderResult | None = None
    reason: str | None = None
    parse_failed: bool = False
    code: MachineReviewJobFailure | None = None
    budget_exhausted: bool = False
    tokens: tuple[int, int] | None = None


def _run_agent(
    session_factory: Callable[[], Session],
    settings_obj: Any,
    provider: LocalMachineReviewProvider,
    context: MachineReviewContext,
    summaries: dict[tuple[str, str], Any],
    job_id: int,
    lessons: Sequence[GuidanceEntry] = (),
) -> _AgentRun:
    """Run the tool loop with one short transaction per event; no session is open during a model call."""
    registry = ToolRegistry(
        offered=offered_refs_from_context(context),
        summaries=summaries,
        guidance=default_guidance_index(),
        guidance_seed=guidance_seed_codes(context, summaries),
        lessons=GuidanceIndex(lessons) if lessons else None,
    )
    budget = RunBudget.from_settings(settings_obj)
    seen = {"turns": 0}

    def emit(
        kind: MachineReviewJobEventKind,
        *,
        tool_name: str | None = None,
        outcome: str,
        started_at: datetime,
        duration_ms: int,
        detail: dict[str, Any] | None,
    ) -> None:
        with session_factory() as session:
            job = session.get_one(MachineReviewJob, job_id)
            if kind is MachineReviewJobEventKind.model_turn:
                seen["turns"] += 1
                job.step_count = seen["turns"]
            jobs.append_event(
                session,
                job,
                kind,
                tool_name=tool_name,
                outcome=outcome,
                started_at=started_at,
                duration_ms=duration_ms,
                detail=detail,
                settings_obj=settings_obj,
            )
            session.commit()

    def cancelled() -> bool:
        return _cancel_requested(session_factory, job_id)

    try:
        loop: LoopResult = provider.run_agent(
            context, registry=registry, budget=budget, emit=emit, cancelled=cancelled
        )
    except MachineReviewCancelled:
        raise
    except (ReviewBudgetExhausted, ModelContextExceededError) as exc:
        return _AgentRun(budget_exhausted=True, tokens=getattr(exc, "tokens", None))
    except Exception as exc:
        return _AgentRun(
            reason=_failure_text("Machine review failed to review", exc, settings_obj), code=_failure_code_for(exc)
        )
    tokens = (loop.tokens_in, loop.tokens_out)
    try:
        result = parse_machine_review_payload(loop.text)
    except Exception as exc:
        return _AgentRun(
            reason=_failure_text("Machine review output was invalid", exc, settings_obj),
            parse_failed=True,
            tokens=tokens,
        )
    violation = _offer_violation(result, context, registry.returned_sources)
    if violation is not None:
        return _AgentRun(reason=violation, parse_failed=True, tokens=tokens)
    return _AgentRun(result=result, tokens=tokens)


def _offer_violation(
    result: MachineReviewProviderResult,
    context: MachineReviewContext,
    returned_sources: frozenset[str] = frozenset(),
) -> str | None:
    """Why ``result`` cites something the run did not offer or return, as failure text; ``None`` if it does not."""
    violation = offer_violation(
        result, offered_refs=offered_refs_from_context(context), returned_sources=returned_sources
    )
    if violation is None:
        return None
    return _clip(f"Machine review output was invalid: {violation}", _MAX_FAILURE_TEXT_CHARS)


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
