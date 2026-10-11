"""The qualification service against a real, committed database: one repeatable-read read-only snapshot, and no leak.

Every other test here runs inside the harness's one outer transaction, which cannot become a snapshot. This one stubs
nothing: committed rows live in a scratch database behind a ONE-connection pool, and what is asserted is observed from
inside the call, on the connection serving it: the snapshot is repeatable read and read only, and the next pooled session
on the same process is back to read committed and writable. Committed rows cannot be cleaned up row by row (the science
tables carry append-only triggers), so the database is dropped whole.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.services.read_snapshot import SnapshotNotConsistentError
from app.services.statmech_selection.service import qualify_target
from tests import conftest
from tests.conftest import scratch_database_name
from tests.services.statmech_selection._support import THERMAL, make, req
from tests.services.statmech_selection.test_service import build

BACKEND_ROOT = Path(__file__).resolve().parents[3]
PROBE = text("SELECT pg_backend_pid(), current_setting('transaction_isolation'), current_setting('transaction_read_only')")


@pytest.fixture(scope="module")
def scratch_engine() -> Iterator:
    db_name = scratch_database_name("statmech_service_real")
    conftest._recreate_test_database(db_name)
    engine = None
    try:
        subprocess.run(
            ["conda", "run", "-n", "tckdb_env", "alembic", "upgrade", "head"],
            cwd=BACKEND_ROOT, env=conftest._db_env(db_name), check=True, capture_output=True, text=True,
        )
        engine = create_engine(conftest._database_url(db_name), pool_size=1, max_overflow=0)
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
        conftest._drop_test_database(db_name)


@pytest.fixture(scope="module")
def committed(scratch_engine):
    with Session(scratch_engine) as session:
        scene = build(session)
        refs = SimpleNamespace(entry=scene.world.entry.public_ref, entry_id=scene.world.entry.id, group=scene.group.public_ref)
        session.commit()
    return refs


def _request(committed):
    return req(make(THERMAL, target__species_entry_ref=committed.entry, target__scope__conformer_group_ref=committed.group))


def test_the_service_reads_one_repeatable_read_read_only_snapshot_and_does_not_leak_it(scratch_engine, committed):
    with Session(scratch_engine) as session:
        result = qualify_target(session, request=_request(committed), owner_id=committed.entry_id)  # require_snapshot defaults on
        pid, isolation, read_only = session.execute(PROBE).one()
        assert isolation == "repeatable read" and read_only == "on"
    assert result.snapshot_isolation == "repeatable read"
    assert result.summary.outcome == "qualified_models"
    with Session(scratch_engine) as later:
        next_pid, isolation, read_only = later.execute(PROBE).one()
    assert next_pid == pid, "the one-connection pool did not hand back the same connection"
    assert isolation == "read committed" and read_only == "off"


def test_a_session_already_inside_a_weaker_transaction_is_refused_not_quietly_answered(scratch_engine, committed):
    with Session(scratch_engine) as session:
        session.execute(text("SELECT 1"))  # an isolation level cannot be changed after a statement has run
        with pytest.raises(SnapshotNotConsistentError):
            qualify_target(session, request=_request(committed), owner_id=committed.entry_id)
    with Session(scratch_engine) as later:
        assert later.execute(PROBE).one()[1:] == ("read committed", "off")
