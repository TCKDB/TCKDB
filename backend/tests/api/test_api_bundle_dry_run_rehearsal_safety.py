"""A dry run's rehearsal must not hurt real writers, and must keep nothing.

``/bundles/dry-run`` rehearses ``/bundles/submit`` inside a rolled-back
savepoint (#577). The independent review of that change found four ways the
rehearsal could still reach outside itself, each reproduced here before it
was fixed:

* **F1** -- its inserts take the locks a submit's do. A dry run and a real
  submit inserting the same species in opposite orders deadlocked, and the
  *submit* was the victim (503 ``database_unavailable``).
* **F2** -- ``authenticate_api_key`` leaves ``api_key.last_used_at``
  unflushed in the dry run's session; the first query flushed it, and the
  row lock it took was held for the whole rehearsal, so two dry runs on one
  key ran one after the other.
* **F4** -- the commit guard watched only ``Session.commit``;
  ``session.connection().commit()`` and ``session.execute(text("COMMIT"))``
  both persisted the whole rehearsed import.
* **F3** -- every dry run of a bundle citing a new DOI refetched it from
  Crossref, while holding the rehearsal's locks.

These tests run in the production topology -- a fresh session per request
on its own pooled connection, never committed -- because the shared
single-transaction session every other API test uses cannot show locking
between requests, and cannot show a commit escaping it. Nothing here
commits unless a guard fails.
"""

from __future__ import annotations

import itertools
import random
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.api.app import create_app
from app.api.deps import get_db, get_write_db
from app.db.models.app_user import AppUser
from app.db.models.literature import Literature
from app.db.models.species import Species, SpeciesEntry
from app.db.models.submission import Submission
from app.db.models.thermo import Thermo
from app.schemas.fragments.identity import SpeciesEntryIdentityPayload
from app.schemas.workflows.contribution_bundle import ContributionBundleV0
from app.services import literature_metadata
from app.services.species_resolution import resolve_species_entry
from app.workflows import contribution_bundle_submit as submit_module
from app.workflows.contribution_bundle_submit import (
    rehearse_contribution_bundle_submit,
    submit_contribution_bundle,
)
from app.workflows.rehearsal import RehearsalCommitRefused, RehearsalContended, rehearsal
from tests.api.test_api_bundle_dry_run_submit_parity import _example

DRY_RUN = "/api/v1/bundles/dry-run"
API_KEY = {"X-API-Key": "test-api-key-for-tckdb"}

_chain = itertools.count(random.randint(4, 12))


def _fresh_smiles() -> str:
    """A primary alcohol no other test in this process has used."""
    return "FC(F)" + "C" * next(_chain) + "CO"


def _thermo_record(smiles: str) -> dict:
    record = _example("thermo-bundle-v0.json")["records"]["thermo_uploads"][0]
    record["species_entry"] = {"smiles": smiles, "charge": 0, "multiplicity": 1}
    return record


def _thermo_bundle(*smiles: str, **record_changes: Any) -> dict:
    bundle = _example("thermo-bundle-v0.json")
    bundle["records"]["thermo_uploads"] = [
        {**_thermo_record(s), **record_changes} for s in smiles
    ]
    return bundle


def _committed_counts(engine) -> dict[str, int]:
    """Row counts on a brand-new connection: only what was committed.

    Never a pooled one -- a connection the rehearsal used could come back
    still inside its transaction, and would then see its own writes.
    """
    probe_engine = create_engine(engine.url, poolclass=NullPool)
    try:
        with Session(probe_engine) as session:
            return {
                model.__tablename__: session.scalar(select(func.count()).select_from(model)) or 0
                for model in (Species, SpeciesEntry, Thermo, Submission, Literature)
            }
    finally:
        probe_engine.dispose()


def _backend_pid(session: Session) -> int:
    return session.connection().exec_driver_sql("SELECT pg_backend_pid()").scalar_one()


def _wait_until_blocked(engine, pid: int, *, timeout: float = 10.0) -> None:
    """Return once backend ``pid`` is waiting on a heavyweight lock."""
    deadline = time.monotonic() + timeout
    with engine.connect() as probe:
        while time.monotonic() < deadline:
            waiting = probe.exec_driver_sql(
                "SELECT wait_event_type = 'Lock' FROM pg_stat_activity WHERE pid = %s",
                (pid,),
            ).scalar()
            probe.rollback()
            if waiting:
                return
            time.sleep(0.005)
    raise AssertionError(f"backend {pid} never blocked on a lock")


class _Worker(threading.Thread):
    """Run ``fn`` on a thread and keep its result or exception."""

    def __init__(self, fn: Callable[[], Any]) -> None:
        super().__init__(daemon=True)
        self._fn = fn
        self.result: Any = None
        self.error: BaseException | None = None
        self.elapsed = 0.0

    def run(self) -> None:
        start = time.monotonic()
        try:
            self.result = self._fn()
        except BaseException as exc:
            self.error = exc
        finally:
            self.elapsed = time.monotonic() - start


def _end_the_dry_run_request(dry: _Worker, dry_session: Session) -> None:
    """Wait for the rehearsal, then end its transaction as the request would.

    Rolling the savepoint back is not enough to free a waiter: a
    transaction blocked on a row a savepoint inserted waits on the
    *top-level* transaction once the savepoint is gone, so the rows stay
    locked until the dry-run request's session closes -- which in production
    is the moment the route returns. Here the test holds the session, so it
    ends it.
    """
    dry.join(30)
    assert not dry.is_alive(), "the rehearsal never gave way"
    dry_session.rollback()


@pytest.fixture
def sessions(db_engine) -> Iterator[Callable[[], Session]]:
    """Fresh sessions on their own connections; every one rolled back."""
    opened: list[Session] = []
    factory = sessionmaker(bind=db_engine, expire_on_commit=False)

    def _open() -> Session:
        session = factory()
        opened.append(session)
        return session

    yield _open
    for session in opened:
        session.rollback()
        session.close()


@pytest.fixture
def prod_client(db_engine, _api_test_user) -> Iterator[TestClient]:
    """The real auth dependency and a fresh, never-committed session per
    request -- ``get_db`` as production wires it."""
    factory = sessionmaker(bind=db_engine, expire_on_commit=False)

    def _get_db() -> Iterator[Session]:
        session = factory()
        try:
            yield session
        finally:
            session.close()

    def _no_writes() -> Iterator[Session]:
        raise AssertionError("these tests never call a committing route")
        yield  # pragma: no cover

    app = create_app()
    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_write_db] = _no_writes
    with TestClient(app) as client:
        yield client


# ---------------------------------------------------------------------------
# F1 -- a dry run gives way to a real submit
# ---------------------------------------------------------------------------


def test_a_dry_run_waiting_first_times_out_and_the_submit_completes(
    db_engine, sessions, _api_test_user
) -> None:
    """The rehearsal waits for the submit's row, then the submit waits for
    the rehearsal's. Without ``lock_timeout`` the rehearsal's single deadlock
    check has already passed (nothing to find yet), the submit's finds the
    cycle a second later, and the submit is the one aborted."""
    x, y = _fresh_smiles(), _fresh_smiles()
    submit_session, dry_session = sessions(), sessions()

    # The real submit has already written species X in its transaction.
    resolve_species_entry(
        submit_session,
        SpeciesEntryIdentityPayload(smiles=x, charge=0, multiplicity=1),
        created_by=None,
    )
    submit_session.flush()

    dry_pid = _backend_pid(dry_session)
    dry = _Worker(
        lambda: rehearse_contribution_bundle_submit(
            dry_session,
            ContributionBundleV0.model_validate(_thermo_bundle(y, x)),
            actor=dry_session.get(AppUser, _api_test_user),
        )
    )
    dry.start()
    _wait_until_blocked(db_engine, dry_pid)  # holds Y, waits for X
    # Past the rehearsal's own (10 ms) deadlock check, which found nothing:
    # from here only its lock_timeout can stop the submit being the victim.
    time.sleep(0.1)

    submit = _Worker(
        lambda: submit_contribution_bundle(
            submit_session,
            ContributionBundleV0.model_validate(_thermo_bundle(y)),
            actor=submit_session.get(AppUser, _api_test_user),
        )
    )
    submit.start()  # needs Y
    _end_the_dry_run_request(dry, dry_session)

    assert isinstance(dry.result, RehearsalContended), dry.result
    assert dry.result.reason == "lock_timeout"
    submit.join(30)
    assert not submit.is_alive(), "the real submit is still blocked"
    assert submit.error is None, f"the real submit failed: {submit.error!r}"
    assert submit.result.summary.records_imported == 1


def test_a_dry_run_that_closes_a_deadlock_is_its_victim(
    db_engine, sessions, _api_test_user, monkeypatch
) -> None:
    """The submit waits for the rehearsal's row first, then the rehearsal
    waits for the submit's. The rehearsal's own ``deadlock_timeout`` is far
    shorter than the submit's, so it finds the cycle and aborts itself; the
    0.8 s pause puts the submit's check inside the rehearsal's
    ``lock_timeout``, so without that setting the submit is aborted."""
    x, y = _fresh_smiles(), _fresh_smiles()
    submit_session, dry_session = sessions(), sessions()
    resolve_species_entry(
        submit_session,
        SpeciesEntryIdentityPayload(smiles=x, charge=0, multiplicity=1),
        created_by=None,
    )
    submit_session.flush()

    first_record_done = threading.Event()
    go_on = threading.Event()
    real_persist = submit_module.persist_thermo_upload

    def _pausing_persist(session, upload, **kwargs):
        thermo = real_persist(session, upload, **kwargs)
        if session is dry_session and not first_record_done.is_set():
            first_record_done.set()
            assert go_on.wait(30)
        return thermo

    monkeypatch.setattr(submit_module, "persist_thermo_upload", _pausing_persist)

    dry = _Worker(
        lambda: rehearse_contribution_bundle_submit(
            dry_session,
            ContributionBundleV0.model_validate(_thermo_bundle(y, x)),
            actor=dry_session.get(AppUser, _api_test_user),
        )
    )
    dry.start()
    assert first_record_done.wait(30)  # rehearsal holds Y

    submit_pid = _backend_pid(submit_session)
    submit = _Worker(
        lambda: submit_contribution_bundle(
            submit_session,
            ContributionBundleV0.model_validate(_thermo_bundle(y)),
            actor=submit_session.get(AppUser, _api_test_user),
        )
    )
    submit.start()
    _wait_until_blocked(db_engine, submit_pid)  # submit waits for Y
    time.sleep(0.8)
    go_on.set()  # rehearsal now asks for X: the cycle closes
    _end_the_dry_run_request(dry, dry_session)

    assert isinstance(dry.result, RehearsalContended), dry.result
    assert dry.result.reason == "deadlock"
    submit.join(30)
    assert not submit.is_alive(), "the real submit is still blocked"
    assert submit.error is None, f"the real submit failed: {submit.error!r}"
    assert submit.result.summary.records_imported == 1


def test_a_contended_dry_run_is_a_coded_retryable_503(prod_client, monkeypatch) -> None:
    def _contended(*_args, **_kwargs):
        raise RehearsalContended("lock_timeout")

    monkeypatch.setattr(submit_module, "rehearse_contribution_bundle_submit", _contended)
    from app.api.routes import bundles as bundles_route

    monkeypatch.setattr(bundles_route, "rehearse_contribution_bundle_submit", _contended)
    resp = prod_client.post(DRY_RUN, json=_thermo_bundle(_fresh_smiles()), headers=API_KEY)
    assert resp.status_code == 503, resp.text
    body = resp.json()
    assert body["code"] == "dry_run_contended"
    assert body["context"] == {"reason": "lock_timeout"}
    assert resp.headers["Retry-After"] == "1"


# ---------------------------------------------------------------------------
# F2 -- two dry runs on one API key run side by side
# ---------------------------------------------------------------------------


def test_two_dry_runs_on_one_key_do_not_queue(prod_client, monkeypatch) -> None:
    hold = 1.5
    real_submit = submit_module.submit_contribution_bundle

    def _slow_submit(session, bundle, *, actor):
        result = real_submit(session, bundle, actor=actor)
        time.sleep(hold)  # inside the rehearsal, holding whatever it holds
        return result

    monkeypatch.setattr(submit_module, "submit_contribution_bundle", _slow_submit)

    bundles = [_thermo_bundle(_fresh_smiles()), _thermo_bundle(_fresh_smiles())]
    workers = [
        _Worker(lambda b=b: prod_client.post(DRY_RUN, json=b, headers=API_KEY))
        for b in bundles
    ]
    start = time.monotonic()
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(60)
    wall = time.monotonic() - start

    for worker in workers:
        assert worker.error is None, worker.error
        assert worker.result.status_code == 200, worker.result.text
        assert worker.result.json()["bundle_valid"] is True
    # Serialised, the second waits out the first's whole rehearsal.
    assert wall < 2 * hold, f"two same-key dry runs took {wall:.2f}s: they queued"


# ---------------------------------------------------------------------------
# F4 -- every guarded way of publishing the rehearsal is refused
# ---------------------------------------------------------------------------


def _connection_commit(session: Session) -> None:
    session.connection().commit()


def _sql_commit(session: Session) -> None:
    session.execute(text("COMMIT"))


def _sql_end(session: Session) -> None:
    session.execute(text("/* sneaky */ end"))


def _session_commit(session: Session) -> None:
    session.commit()


def _sql_commit_behind_a_dashed_literal(session: Session) -> None:
    """``'--'`` is a string, not a comment: the COMMIT after it is real."""
    session.execute(text("SELECT '--'; COMMIT"))


def _driver_sql_commit_behind_a_dashed_literal(session: Session) -> None:
    session.connection().exec_driver_sql("SELECT '--'; COMMIT")


def _sql_commit_behind_a_dollar_quote(session: Session) -> None:
    session.execute(text("SELECT $tag$ -- /* $tag$; COMMIT"))


def _raw_dbapi_commit(session: Session) -> None:
    """The driver's own connection, under SQLAlchemy (#592 item 2)."""
    session.connection().connection.dbapi_connection.commit()


@pytest.mark.parametrize(
    "publish",
    [
        _connection_commit,
        _sql_commit,
        _sql_end,
        _session_commit,
        _raw_dbapi_commit,
        _sql_commit_behind_a_dashed_literal,
        _driver_sql_commit_behind_a_dashed_literal,
        _sql_commit_behind_a_dollar_quote,
    ],
    ids=lambda f: f.__name__.lstrip("_"),
)
def test_a_rehearsal_cannot_publish_its_writes(
    prod_client, db_engine, monkeypatch, publish
) -> None:
    real_submit = submit_module.submit_contribution_bundle

    def _submit_then_publish(session, bundle, *, actor):
        result = real_submit(session, bundle, actor=actor)
        publish(session)
        return result

    monkeypatch.setattr(submit_module, "submit_contribution_bundle", _submit_then_publish)
    before = _committed_counts(db_engine)
    with pytest.raises(RehearsalCommitRefused):
        prod_client.post(DRY_RUN, json=_thermo_bundle(_fresh_smiles()), headers=API_KEY)
    assert _committed_counts(db_engine) == before
    # And no pooled connection came back still inside the rehearsal's
    # transaction, where the next request would see -- or commit -- it.
    for _ in range(db_engine.pool.size() + 1):
        with Session(db_engine) as pooled:
            assert pooled.scalar(select(func.count()).select_from(Submission)) == before["submission"]


# ---------------------------------------------------------------------------
# F3 -- literature metadata is fetched once, and before the rehearsal
# ---------------------------------------------------------------------------


def test_a_second_dry_run_of_the_same_doi_makes_no_fetch(prod_client, monkeypatch) -> None:
    literature_metadata.clear_metadata_cache()
    fetches: list[tuple[str, bool]] = []
    rehearsing = threading.Event()

    def _fake_fetch(doi: str) -> dict:
        fetches.append((doi, rehearsing.is_set()))
        return {"title": "A title", "issued": 2024, "URL": f"https://doi.org/{doi}"}

    real_rehearsal = submit_module.rehearsal

    def _marking_rehearsal(session):
        rehearsing.set()
        try:
            with real_rehearsal(session):
                yield
        finally:
            rehearsing.clear()

    monkeypatch.setattr(literature_metadata, "_fetch_doi_metadata_uncached", _fake_fetch)
    monkeypatch.setattr(
        submit_module, "rehearsal", __import__("contextlib").contextmanager(_marking_rehearsal)
    )

    doi = f"10.5555/tckdb-577-{random.randint(0, 10**9)}"
    literature = {"doi": doi, "title": "Depositor title"}
    for _ in range(2):
        resp = prod_client.post(
            DRY_RUN,
            json=_thermo_bundle(_fresh_smiles(), literature=literature),
            headers=API_KEY,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["bundle_valid"] is True

    assert fetches == [(doi, False)], (
        "expected exactly one fetch, made before the rehearsal opened; got "
        f"{fetches}"
    )
    literature_metadata.clear_metadata_cache()


def test_the_metadata_cache_is_bounded_and_ages_out(monkeypatch) -> None:
    literature_metadata.clear_metadata_cache()
    calls: list[str] = []

    def _fake_fetch(doi: str) -> dict:
        calls.append(doi)
        return {"title": [doi]}

    monkeypatch.setattr(literature_metadata, "_fetch_doi_metadata_uncached", _fake_fetch)
    monkeypatch.setattr(literature_metadata, "METADATA_CACHE_MAX_ENTRIES", 2)
    for doi in ("10.1/a", "10.1/b", "10.1/c", "10.1/a"):
        literature_metadata.fetch_doi_metadata(doi)
    assert calls == ["10.1/a", "10.1/b", "10.1/c", "10.1/a"]  # a was evicted

    # A cached answer is a copy: mutating it cannot change the next one.
    literature_metadata.fetch_doi_metadata("10.1/c")["title"] = ["mutated"]
    assert literature_metadata.fetch_doi_metadata("10.1/c")["title"] == ["10.1/c"]

    monkeypatch.setattr(literature_metadata, "METADATA_CACHE_TTL_S", 0.0)
    literature_metadata.fetch_doi_metadata("10.1/c")
    assert calls[-1] == "10.1/c" and len(calls) == 5

    # A failed lookup is not remembered.
    monkeypatch.setattr(literature_metadata, "_fetch_doi_metadata_uncached", lambda d: None)
    assert literature_metadata.fetch_doi_metadata("10.1/none") is None
    monkeypatch.setattr(literature_metadata, "_fetch_doi_metadata_uncached", _fake_fetch)
    literature_metadata.fetch_doi_metadata("10.1/none")
    assert calls[-1] == "10.1/none"
    literature_metadata.clear_metadata_cache()


# ---------------------------------------------------------------------------
# #592 item 1 -- a savepoint name is not an identity
# ---------------------------------------------------------------------------


def _listener_count(connection) -> int:
    return len(connection.dispatch.before_cursor_execute) + len(connection.dispatch.commit)


def test_reusing_the_rehearsals_savepoint_name_cannot_release_it(sessions) -> None:
    """``SAVEPOINT <ours>`` then ``RELEASE <ours>`` twice used to count the
    name as inner, so the second RELEASE dropped the rehearsal's own
    savepoint. PostgreSQL releases the *most recent* savepoint of a name, so
    the guard must too."""
    session = sessions()
    connection = session.connection()
    seen: list[str] = []

    def _record(_conn, _cursor, statement, *_rest):
        if statement.startswith("SAVEPOINT"):
            seen.append(statement.split()[1])

    event.listen(connection, "before_cursor_execute", _record)
    listeners_before = _listener_count(connection) - 1  # not counting _record
    steps: list[str] = []
    try:
        with pytest.raises(RehearsalCommitRefused, match="release a savepoint it did not open"):
            with rehearsal(session):
                ours = seen[0]
                session.execute(text(f"SAVEPOINT {ours}"))
                session.execute(text(f"RELEASE {ours}"))  # the duplicate: fine
                steps.append("the duplicate was released")
                session.execute(text(f"RELEASE {ours}"))  # the rehearsal's own
    finally:
        event.remove(connection, "before_cursor_execute", _record)
    # The refusal came from the second RELEASE, not the first: releasing the
    # newest savepoint of a name is legitimate, and refusing it too would
    # pass this test having proved nothing about which one was tracked.
    assert steps == ["the duplicate was released"]
    assert _listener_count(connection) == listeners_before, "guards were left attached"


def test_releasing_a_savepoint_opened_inside_the_rehearsal_is_allowed(sessions) -> None:
    """The guard refuses only what is not the rehearsal's to release: an
    inner savepoint, opened and released by the code under rehearsal, is
    ordinary (submit does exactly this), including after a rollback to it."""
    session = sessions()
    with rehearsal(session):
        session.execute(text("SAVEPOINT inner_one"))
        session.execute(text("SAVEPOINT inner_two"))
        session.execute(text("ROLLBACK TO SAVEPOINT inner_one"))
        session.execute(text("RELEASE SAVEPOINT inner_one"))
        session.execute(text('SAVEPOINT "Mixed Case"'))
        session.execute(text('RELEASE "Mixed Case"'))


def test_a_release_below_the_rehearsal_is_refused_after_a_rollback_to(sessions) -> None:
    """``ROLLBACK TO`` discards the savepoints made after its target, so a
    name that only existed there must not still count as inner."""
    session = sessions()
    connection = session.connection()
    seen: list[str] = []

    def _record(_conn, _cursor, statement, *_rest):
        if statement.startswith("SAVEPOINT"):
            seen.append(statement.split()[1])

    event.listen(connection, "before_cursor_execute", _record)
    try:
        with pytest.raises(RehearsalCommitRefused):
            with rehearsal(session):
                ours = seen[0]
                session.execute(text("SAVEPOINT a"))
                session.execute(text(f"SAVEPOINT {ours}"))  # duplicate of ours, after a
                session.execute(text("ROLLBACK TO SAVEPOINT a"))  # discards the duplicate
                session.execute(text(f"RELEASE {ours}"))  # now names the rehearsal's own
    finally:
        event.remove(connection, "before_cursor_execute", _record)


# ---------------------------------------------------------------------------
# #592 item 4 -- a failed lookup is not repeated under the rehearsal's locks,
# and is remembered by nobody else
# ---------------------------------------------------------------------------


def _failing_fetch_recording(fetches: list, rehearsing: threading.Event):
    def _fetch(doi: str) -> None:
        fetches.append((doi, rehearsing.is_set()))
        return None

    return _fetch


def _marking_rehearsal(rehearsing: threading.Event):
    real_rehearsal = submit_module.rehearsal

    def _marked(session):
        rehearsing.set()
        try:
            with real_rehearsal(session):
                yield
        finally:
            rehearsing.clear()

    return __import__("contextlib").contextmanager(_marked)


def test_a_failed_prefetch_is_not_repeated_by_the_rehearsal_but_is_by_a_later_lookup(
    prod_client, monkeypatch
) -> None:
    literature_metadata.clear_metadata_cache()
    fetches: list[tuple[str, bool]] = []
    rehearsing = threading.Event()
    monkeypatch.setattr(
        literature_metadata, "_fetch_doi_metadata_uncached", _failing_fetch_recording(fetches, rehearsing)
    )
    monkeypatch.setattr(submit_module, "rehearsal", _marking_rehearsal(rehearsing))

    doi = f"10.5555/tckdb-592-{random.randint(0, 10**9)}"
    resp = prod_client.post(
        DRY_RUN,
        json=_thermo_bundle(_fresh_smiles(), literature={"doi": doi, "title": "Depositor title"}),
        headers=API_KEY,
    )
    assert resp.status_code == 200, resp.text
    assert fetches == [(doi, False)], f"the rehearsal repeated a failed fetch under its locks: {fetches}"

    # The dry run's request is over: its memory of the failure went with it.
    assert literature_metadata.fetch_doi_metadata(doi) is None
    assert fetches == [(doi, False), (doi, False)]
    literature_metadata.clear_metadata_cache()


def test_one_users_failed_dry_run_cannot_make_another_users_submit_skip_the_fetch(
    monkeypatch,
) -> None:
    """The harm a general negative cache does: A's failed lookup, then B's real
    submit of the same DOI makes no fetch and stores a row without metadata."""
    literature_metadata.clear_metadata_cache()
    fetches: list[str] = []
    monkeypatch.setattr(
        literature_metadata, "_fetch_doi_metadata_uncached", lambda d: fetches.append(d)
    )
    with literature_metadata.failure_scope():  # user A's dry run
        assert literature_metadata.fetch_doi_metadata("10.1/shared") is None  # prefetch
        assert literature_metadata.fetch_doi_metadata("10.1/shared") is None  # rehearsal
    assert fetches == ["10.1/shared"]  # inside the scope: once
    # A's request has ended without reaching anything else (say, abandoned as
    # contended). User B's submit -- outside any scope -- must fetch.
    assert literature_metadata.fetch_doi_metadata("10.1/shared") is None
    assert fetches == ["10.1/shared", "10.1/shared"]
    literature_metadata.clear_metadata_cache()


def test_the_scope_is_not_shared_with_a_concurrent_request(monkeypatch) -> None:
    literature_metadata.clear_metadata_cache()
    fetches: list[str] = []
    monkeypatch.setattr(
        literature_metadata, "_fetch_doi_metadata_uncached", lambda d: fetches.append(d)
    )
    inside, proceed = threading.Event(), threading.Event()

    def _dry_run() -> None:
        with literature_metadata.failure_scope():
            literature_metadata.fetch_doi_metadata("10.1/conc")
            inside.set()
            assert proceed.wait(30)

    worker = _Worker(_dry_run)
    worker.start()
    assert inside.wait(30)
    literature_metadata.fetch_doi_metadata("10.1/conc")  # another request, no scope
    proceed.set()
    worker.join(30)
    assert worker.error is None
    assert fetches == ["10.1/conc", "10.1/conc"]
    literature_metadata.clear_metadata_cache()


def test_a_slow_failure_is_still_not_repeated_within_the_scope(monkeypatch) -> None:
    """A Crossref timeout takes 10 s. Nothing here is timed, so a failure
    that took longer than any window still holds for the whole request."""
    literature_metadata.clear_metadata_cache()
    fetches: list[str] = []
    clock = [1000.0]
    monkeypatch.setattr(
        literature_metadata, "time", __import__("types").SimpleNamespace(monotonic=lambda: clock[0])
    )

    def _slow_failure(doi: str) -> None:
        fetches.append(doi)
        clock[0] += 600.0  # the request "took" ten minutes, then failed
        return None

    monkeypatch.setattr(literature_metadata, "_fetch_doi_metadata_uncached", _slow_failure)
    with literature_metadata.failure_scope():
        literature_metadata.fetch_doi_metadata("10.1/slow")
        clock[0] += 600.0
        literature_metadata.fetch_doi_metadata("10.1/slow")
    assert fetches == ["10.1/slow"]
    literature_metadata.clear_metadata_cache()


def test_guards_are_removed_even_when_the_final_rollback_raises(sessions, monkeypatch) -> None:
    """A raise from the rehearsal's own rollback used to skip every
    ``event.remove`` after it (#592 item 1). Force one, and count."""
    from sqlalchemy.orm import SessionTransaction

    session = sessions()
    connection = session.connection()
    raw = connection.connection.dbapi_connection
    before = (_listener_count(connection), len(session.dispatch.before_commit))

    def _boom(self, *args, **kwargs):
        raise RuntimeError("boom")

    with monkeypatch.context() as patched:
        with pytest.raises(RuntimeError, match="boom"):
            with rehearsal(session):
                patched.setattr(SessionTransaction, "rollback", _boom)
    assert (_listener_count(connection), len(session.dispatch.before_commit)) == before
    assert "commit" not in raw.__dict__, "the raw connection's commit was left shadowed"


# ---------------------------------------------------------------------------
# Savepoint statements PostgreSQL accepts without a space before a quote
# ---------------------------------------------------------------------------


@pytest.fixture
def own_savepoint(sessions):
    """A session, and the name the rehearsal will give its savepoint (``seen[0]``)."""
    session = sessions()
    connection = session.connection()
    seen: list[str] = []

    def _record(_conn, _cursor, statement, *_rest):
        if statement.startswith("SAVEPOINT"):
            seen.append(statement[len("SAVEPOINT"):].strip())

    event.listen(connection, "before_cursor_execute", _record)
    yield session, seen
    event.remove(connection, "before_cursor_execute", _record)


def test_a_quoted_release_with_no_space_cannot_release_the_rehearsal(own_savepoint) -> None:
    session, seen = own_savepoint
    with pytest.raises(RehearsalCommitRefused, match="release a savepoint it did not open"):
        with rehearsal(session):
            session.execute(text(f'RELEASE"{seen[0]}"'))


def test_a_quoted_savepoint_with_no_space_is_tracked_and_releasable(sessions) -> None:
    """Untracked, ``RELEASE"a"`` would look like a release of something the
    rehearsal never opened, and be refused."""
    session = sessions()
    with rehearsal(session):
        session.execute(text('SAVEPOINT"a"'))
        session.execute(text('RELEASE "a"'))  # spaced: only matches if the SAVEPOINT was tracked


def test_a_quoted_rollback_to_with_no_space_discards_later_savepoints(own_savepoint) -> None:
    session, seen = own_savepoint
    with pytest.raises(RehearsalCommitRefused):
        with rehearsal(session):
            ours = seen[0]
            session.execute(text('SAVEPOINT"a"'))
            session.execute(text(f"SAVEPOINT {ours}"))  # duplicate of ours, after a
            session.execute(text('ROLLBACK TO"a"'))  # discards the duplicate
            session.execute(text(f"RELEASE {ours}"))  # now the rehearsal's own
