"""Keep in-process Alembic runs from silently disabling log capture.

``tests/db/test_dataset_release_migration.py`` and
``tests/db/test_upload_job_lease_migration.py`` call ``alembic.command``
directly, which execs ``alembic/env.py`` in *this* process. That file does::

    if config.config_file_name is not None:
        fileConfig(config.config_file_name)

and ``logging.config.fileConfig`` defaults to ``disable_existing_loggers=True``
and replaces the root logger's handlers with the one declared in
``alembic.ini``. The handler it throws away is the one pytest's ``caplog``
fixture reads from, and the loggers it disables include every
``app.*`` logger already created.

Nothing announces this. It reads as five unrelated tests in three other trees
quietly asserting against an empty ``caplog`` — ``assert 'Refusing to fetch
unverified' in ''`` — for the rest of the pytest process, hundreds of tests
after the migration test that caused it. Reproduce it in three seconds with::

    pytest -p no:randomly tests/db/test_upload_job_lease_migration.py \\
        tests/services/test_best_effort_isolation.py \\
        tests/importers/cccbdb/test_url_guard.py

Repairing the damage afterwards is the wrong shape — pytest's logging plugin
adds and removes its own root handlers around each test phase, so restoring a
snapshot taken at setup would put a stale handler list back at teardown.
Preventing it is exact: during a test, the harness owns logging configuration,
so Alembic's copy of it is a no-op. Alembic's own output is unaffected on the
command line and in deployment, where ``env.py`` is untouched.
"""

from __future__ import annotations

import logging.config
from typing import Iterator

import pytest
from sqlalchemy import text

from tests.db._migration_chain import current_head

#: Node id of the first test observed to finish with the shared database
#: below alembic head, so later failures can point at it instead of each
#: opening its own investigation. See ``_must_leave_the_database_at_head``.
_FIRST_OFFENDER: str | None = None


@pytest.fixture(autouse=True)
def _alembic_must_not_reconfigure_logging(monkeypatch) -> Iterator[None]:
    """Neutralise ``fileConfig`` for the duration of each test in this tree.

    ``env.py`` re-executes ``from logging.config import fileConfig`` every time
    Alembic runs it, so patching the attribute on the module is enough — there
    is no early-bound reference to work around.

    Any future test elsewhere that calls ``alembic.command`` in-process needs
    this too; move the fixture up to ``tests/conftest.py`` rather than
    rediscovering the symptom. The same applies to
    ``_must_leave_the_database_at_head`` below, for the same reason.
    """
    monkeypatch.setattr(logging.config, "fileConfig", lambda *args, **kwargs: None)
    yield


@pytest.fixture(scope="session")
def alembic_head() -> str:
    """The revision ``alembic upgrade head`` would stop at, read from disk.

    See ``_migration_chain.current_head``, which this exposes as a fixture
    and which explains why it is read rather than written down. A test that
    needs its own *subject* wants ``_migration_chain.revision_under_test``
    instead: that is a different concept, and conflating the two is the
    mistake ``_must_leave_the_database_at_head`` below exists to catch.
    """
    return current_head()


@pytest.fixture(autouse=True)
def _must_leave_the_database_at_head(request, db_engine, alembic_head) -> Iterator[None]:
    """Fail the test that downgraded the shared database, not its victims.

    Four files in this tree run ``alembic.command`` in-process against the
    *per-run database every other test in the session is sharing*. A test
    that downgrades it and does not put it back does not fail: it succeeds,
    and the damage lands on whatever runs next, in files with no connection
    to migrations at all. The symptom is a red test asserting something that
    is true, against a schema that is a few revisions old.

    That has now happened three times, each time with the same shape and
    each time diagnosed from scratch:

    * ``test_upload_job_lease_migration.py`` -- ``species_entry.isotope_key
      does not exist``, hundreds of tests later.
    * ``test_dataset_release_migration.py`` -- the same, in files that had
      nothing to do with releases.
    * ``test_imaginary_mode_tau_basis_constraint.py`` -- three failures in
      ``test_statmech_torsion_index_uniqueness.py`` reporting that a
      duplicate torsion index had been refused by the *wrong* unique
      constraint. It had not; ``b7e4d1a9c026`` renames that index, the
      database was one revision below it, and the rule the test cares about
      was enforced correctly the whole time.

    The first two were each fixed in place, by the author who happened to
    tread on them, and the comment they left did not stop the third. So the
    check lives here instead: it costs one query per test in this directory
    and it names the test that did it.

    Teardown-only. A test is entitled to move the version around while it
    runs -- that is what these files are for. What it may not do is finish
    with the version somewhere other than where it found it.

    Every test after the offender fails too, and deliberately: each of them
    really did run against the wrong schema, so a green result from one
    would be a result about a database nobody meant to test. Only the first
    is *blamed*, though -- ``_FIRST_OFFENDER`` carries that name into every
    later message, so a hundred red tests still point at one line of one
    file rather than a hundred investigations.
    """
    yield
    with db_engine.connect() as connection:
        version = connection.scalar(text("SELECT version_num FROM alembic_version"))
    if version == alembic_head:
        return

    global _FIRST_OFFENDER
    if _FIRST_OFFENDER is None:
        _FIRST_OFFENDER = request.node.nodeid
        blame = "This test left it there."
    else:
        blame = (
            f"{_FIRST_OFFENDER} left it there; this test is downstream of "
            "that, and its result means nothing until that one is fixed."
        )
    raise AssertionError(
        f"the shared test database is at alembic revision {version!r}, not "
        f"head ({alembic_head!r}). {blame} Every test that follows runs "
        "against a schema missing every revision above that point, and will "
        "fail somewhere unrelated with no sign of what moved. Restore it "
        'with ``command.upgrade(config, "head")`` -- the literal string, not '
        "a revision id: a revision id is head only until the next one lands."
    )
