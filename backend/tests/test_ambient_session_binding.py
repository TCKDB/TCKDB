"""The ambient session factory must name the database the fixtures own.

``app.api.deps`` builds ``engine``/``SessionLocal`` at import time from
``settings.database_url`` — the ambient ``DB_NAME``, locally ``tckdb_dev``.
Nothing in the test harness creates, migrates, inspects or rolls back that
database, so any code path that reaches the factory during a test is talking
to somewhere no assertion is looking.

This is not a hypothetical class of bug. It was root cause 2 of the
seed-independence work: five ``/status`` tests probed through
``health.SessionLocal``, passed in a dev shell and on the PR gate — which
runs ``alembic upgrade head`` against ``DB_NAME`` in an earlier step — and
failed only on the nightly, which does not. They were green for a reason
unrelated to what they asserted.

The call sites cannot simply be handed a request-scoped session: the
commit-time upload audit and the artifact-integrity event writer need a
transaction *independent* of the request's, which has already rolled back by
the time they run; the upload worker and the idempotency decorator run
outside any request at all; the startup encoding check runs at boot; and the
health probes deliberately exercise the process's own engine, which is the
question they exist to answer. So the fix is to make the binding truthful,
and these are the assertions that keep it that way.
"""

from __future__ import annotations

import inspect

import pytest
from sqlalchemy import text

from app.api import deps as api_deps
from app.api.routes import health
from app.services import artifact_integrity, upload_submission
from app.workers import upload_worker


def test_ambient_engine_points_at_the_pytest_database(db_engine) -> None:
    assert api_deps.engine is db_engine
    assert api_deps.engine.url.database == db_engine.url.database
    assert db_engine.url.database.startswith("tckdb_test")


def test_ambient_session_reads_the_pytest_database(db_engine) -> None:
    """Not just the same URL — the same, migrated, database.

    ``alembic_version`` is the table whose absence made the nightly's
    ``/status`` failures look like an application fault.
    """
    with api_deps.SessionLocal() as session:
        revision = session.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one_or_none()
        current = session.execute(text("SELECT current_database()")).scalar_one()

    assert revision is not None
    assert current == db_engine.url.database


@pytest.mark.parametrize(
    "module, attribute",
    [
        (health, "SessionLocal"),
        (upload_worker, "SessionLocal"),
        (api_deps, "SessionLocal"),
    ],
    ids=["health", "upload_worker", "deps"],
)
def test_modules_holding_an_import_time_reference_follow_the_rebind(
    db_engine, module, attribute
) -> None:
    """``from app.api.deps import SessionLocal`` copies the object.

    Rebinding only ``app.api.deps.SessionLocal`` would leave every one of
    these modules pointed at the ambient database, which is precisely how the
    ``/status`` probes stayed broken. ``sessionmaker.configure`` mutates the
    factory in place instead, so the copies follow.
    """
    factory = getattr(module, attribute)

    with factory() as session:
        current = session.execute(text("SELECT current_database()")).scalar_one()

    assert current == db_engine.url.database


@pytest.mark.parametrize(
    "module", [upload_submission, artifact_integrity], ids=["upload", "integrity"]
)
def test_out_of_request_writers_still_fall_back_to_the_ambient_factory(
    module,
) -> None:
    """Pins *which* factory these two committing services default to.

    Both take an injectable ``session_factory`` and fall back to
    ``app.api.deps.SessionLocal``; both *commit*, in a transaction
    deliberately independent of the request's. That independence is why they
    cannot be handed a request session — and it is also why a commit through
    an ambient binding is the worst case: it lands in a database the
    committed-row tripwire does not watch and no assertion can see. The
    write is not merely unasserted, it is invisible.

    If a future change routes them somewhere else, this fails and the
    guarantee above has to be re-argued rather than quietly lost.
    """
    source = inspect.getsource(module)

    assert "from app.api.deps import SessionLocal as session_factory" in source


def test_ambient_factory_refuses_when_no_test_database_is_bound() -> None:
    """Without the rebind, using the factory must fail loudly.

    A pure-unit test that reaches an out-of-request writer never requests
    ``db_engine``, so nothing would rebind — and before this refusal it would
    have silently opened a connection to ``tckdb_dev``. The refusal turns
    that into a message naming the mistake.
    """
    import conftest

    previous = api_deps.engine
    api_deps.bind_ambient_session_factory(conftest._AMBIENT_REFUSING_ENGINE)
    try:
        with pytest.raises(Exception) as excinfo:
            with api_deps.SessionLocal() as session:
                session.execute(text("SELECT 1"))
    finally:
        api_deps.bind_ambient_session_factory(previous)

    assert "not bound to the pytest database" in str(excinfo.value)


def test_refusing_engine_is_installed_before_any_fixture_runs() -> None:
    """The import-time binding, checked without disturbing it.

    ``conftest`` binds the refusing engine at module import, i.e. before the
    first fixture and before any test module is imported. This asserts the
    engine object exists and is not the ambient one, which is what makes the
    default posture "refuse" rather than "write to tckdb_dev".
    """
    import conftest

    from app.api.config import settings

    assert conftest._AMBIENT_REFUSING_ENGINE is not None
    assert conftest._AMBIENT_REFUSING_ENGINE.url.database != settings.db_name
    assert conftest._AMBIENT_REFUSING_ENGINE.url.database in (None, "")
