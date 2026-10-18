"""Running a pass of the curated sweep: the guard spy and the per-pass crawl."""

from __future__ import annotations

import io
import zipfile

from sqlalchemy import event

from app.services.scientific_read import ref_guard
from tests.api.scientific._curated_sweep_crawl import Result, send, variants_for
from tests.api.scientific._curated_sweep_inputs import CRAWL_UNSUPPORTED


class GuardSpy:
    """Wraps the boundary hook: records that a request reached it and any ``RefGuardUnplaceable`` it raised."""

    def __init__(self) -> None:
        self.reached = False
        self.unplaceable: tuple[str, str, str] | None = None
        self.route_paths: list[str] = []
        self.applied_reports: list[ref_guard.WithholdReport] = []
        #: The request's guard context, so the selection documents that passed the loader to engine seam are counted.
        self.context: ref_guard.RefGuardContext | None = None

    def reset(self) -> None:
        self.reached = False
        self.unplaceable = None
        self.route_paths = []
        self.applied_reports = []
        self.context = None


def install_spy(monkeypatch) -> GuardSpy:
    """Replace the hook names the guard is reached through with recording wrappers around the real ones."""
    from app.services.scientific_read import internal_ids

    spy = GuardSpy()
    real = internal_ids.withhold_for_current_request

    def wrapper(payload):
        ctx = ref_guard.current_ref_guard_context()
        spy.reached = True
        if ctx is not None:
            spy.route_paths.append(ctx.route_path)
        try:
            return real(payload)
        except ref_guard.RefGuardUnplaceable as exc:
            spy.unplaceable = (exc.model_name, exc.field_path, exc.ref_prefix)
            raise
        finally:
            if ctx is not None:
                spy.applied_reports.extend(ctx.reports)

    monkeypatch.setattr(internal_ids, "withhold_for_current_request", wrapper)

    # The export streams do not return through the response boundary. They reach the guard through the document
    # seam (``ref_guard.withhold_in_document``, looked up on the module at call time), which is spied the same way.
    real_document = ref_guard.withhold_in_document

    def document_wrapper(session, documents, *, profile, mode):
        spy.reached = True
        try:
            report = real_document(session, documents, profile=profile, mode=mode)
        except ref_guard.RefGuardUnplaceable as exc:
            spy.unplaceable = (exc.model_name, exc.field_path, exc.ref_prefix)
            raise
        spy.applied_reports.append(report)
        return report

    monkeypatch.setattr(ref_guard, "withhold_in_document", document_wrapper)

    # A selection document never reaches that hook: it passes the loader to engine seam inside the service. The request's
    # own context counts those, so the spy keeps a handle on it.
    from app.api.routes.scientific import _profile

    real_set = _profile.set_ref_guard_context

    def record_context(ctx):
        spy.context = ctx
        return real_set(ctx)

    monkeypatch.setattr(_profile, "set_ref_guard_context", record_context)
    return spy


def body_text(response) -> str:
    """The response body as text. A zip (the CHEMKIN export) is read member by member: its bytes carry file times and
    its compressed members hide refs from a substring search."""
    content = response.content
    if content[:4] == b"PK\x03\x04":
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            return "\n".join(
                f"--- {name}\n{archive.read(name).decode('utf-8', errors='replace')}"
                for name in sorted(archive.namelist())
            )
    return response.text


def run_pass(client, db_session, specs, world, spy: GuardSpy, *, profile: str, with_ids: bool = False):
    """Crawl every non-exempt, supported route under one profile.

    Returns ``{(route key, variant label): Result}``. A request that dies with ``RefGuardUnplaceable`` is recorded
    as a 500 with ``unplaceable`` set, not raised: the sweep's job is to list those, not to stop at the first.
    """
    statements: list[str] = []

    def hook(conn, cursor, statement, *args):
        statements.append(statement)

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", hook)
    results: dict[tuple[tuple[str, str], str], Result] = {}
    try:
        for spec in specs:
            if spec.exempt or spec.key in CRAWL_UNSUPPORTED:
                continue
            for variant in variants_for(spec, world, with_internal_ids=with_ids):
                spy.reset()
                before = len(statements)
                try:
                    response = send(client, spec, variant, profile=profile)
                    result = Result(response.status_code, body_text(response))
                except ref_guard.RefGuardUnplaceable:
                    result = Result(500, "")
                result.statements = len(statements) - before
                documents = spy.context.documents if spy.context is not None else 0
                result.documents = documents
                result.document_tokens = sorted(
                    token for report in (spy.context.document_reports if spy.context is not None else []) for token in report.tokens.values()
                )
                result.reached = spy.reached or documents > 0
                result.unplaceable = spy.unplaceable
                result.spy_paths = list(spy.route_paths)
                result.applied_sites = [site for report in spy.applied_reports for site in report.sites]
                results[(spec.key, variant.label)] = result
    finally:
        event.remove(bind, "before_cursor_execute", hook)
    return results
