"""``backfill_observation_submission_links.py``: CLI wiring for #514.

Service-level behavior (idempotency, dry-run, grouping, the
``historical_review`` attestation) is covered in
``tests/services/test_observation_submission_link_backfill.py``. This file
covers what only exists at the CLI boundary: the database-name safety
guard, actor creation, and argument parsing -- using the same dynamic
import / session-proxy device ``tests/scripts/test_backfill_assumed_tau.py``
uses, so nothing here writes to a real database and every write rolls back
with the enclosing test.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest
from sqlalchemy import select

from app.db.models.app_user import AppUser, AppUserRole
from app.db.models.common import SubmissionRecordType, SubmissionSourceKind
from app.db.models.submission import Submission, SubmissionRecordLink
from tests.services.scientific_read._factories import make_observation

_SCRIPT = (
    pathlib.Path(__file__).parents[2]
    / "scripts"
    / "ops"
    / "backfill_observation_submission_links.py"
)


@pytest.fixture(scope="module")
def backfill():
    spec = importlib.util.spec_from_file_location(
        "backfill_observation_submission_links", _SCRIPT
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _SessionProxy:
    """The test's own session, wearing the shape the script expects.

    The script does ``with Session(engine) as session: ...`` (not
    ``SessionLocal()``), so the proxy stands in for ``Session`` itself --
    called with the (unused, since ``create_engine`` is also patched to a
    no-op) engine argument and returned as its own context manager.
    """

    def __init__(self, session) -> None:
        self._session = session

    def __getattr__(self, name):
        return getattr(self._session, name)

    def __call__(self, *_args, **_kwargs):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _run_main(
    backfill, monkeypatch, db_session, argv, *, db_name: str = "tckdb_test_backfill"
):
    proxy = _SessionProxy(db_session)
    monkeypatch.setattr("sys.argv", ["backfill_observation_submission_links.py", *argv], raising=False)
    monkeypatch.setattr(backfill, "Session", proxy)
    monkeypatch.setattr(backfill, "create_engine", lambda *a, **k: None)
    monkeypatch.setenv("DB_NAME", db_name)
    return backfill.main()


# ---------------------------------------------------------------------------
# Database-target safety guard
# ---------------------------------------------------------------------------


def test_refuses_a_non_test_database_without_target_db(backfill, monkeypatch, db_session, capsys):
    make_observation(db_session, external_source_name="CCCBDB")

    rc = _run_main(
        backfill,
        monkeypatch,
        db_session,
        ["--license", "CC-BY-4.0", "--actor", "op1", "--commit"],
        db_name="tckdb_prod",
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert "--target-db" in err

    # Nothing was written -- the guard refuses before the pipeline runs.
    assert db_session.scalar(select(Submission)) is None


def test_target_db_matching_the_real_name_is_accepted(backfill, monkeypatch, db_session):
    make_observation(db_session, external_source_name="CCCBDB")

    rc = _run_main(
        backfill,
        monkeypatch,
        db_session,
        ["--license", "CC-BY-4.0", "--actor", "op1", "--commit", "--target-db", "tckdb_prod"],
        db_name="tckdb_prod",
    )
    assert rc == 0
    submission = db_session.scalar(
        select(Submission).where(Submission.source_kind == SubmissionSourceKind.migration)
    )
    assert submission is not None


def test_target_db_that_does_not_match_is_refused(backfill, monkeypatch, db_session, capsys):
    make_observation(db_session, external_source_name="CCCBDB")

    rc = _run_main(
        backfill,
        monkeypatch,
        db_session,
        ["--license", "CC-BY-4.0", "--actor", "op1", "--commit", "--target-db", "some_other_db"],
        db_name="tckdb_prod",
    )
    assert rc == 2
    assert db_session.scalar(select(Submission)) is None


def test_a_tckdb_test_star_database_needs_no_override(backfill, monkeypatch, db_session):
    make_observation(db_session, external_source_name="CCCBDB")

    rc = _run_main(
        backfill,
        monkeypatch,
        db_session,
        ["--license", "CC-BY-4.0", "--actor", "op1", "--commit"],
        db_name="tckdb_test_ci",
    )
    assert rc == 0
    assert db_session.scalar(select(Submission)) is not None


# ---------------------------------------------------------------------------
# Actor creation
# ---------------------------------------------------------------------------


def test_actor_is_created_as_a_curator_if_new(backfill, monkeypatch, db_session):
    make_observation(db_session, external_source_name="CCCBDB")
    assert (
        db_session.query(AppUser).filter_by(username="fresh_operator").first() is None
    )

    rc = _run_main(
        backfill,
        monkeypatch,
        db_session,
        ["--license", "CC-BY-4.0", "--actor", "fresh_operator", "--commit"],
    )
    assert rc == 0
    user = db_session.query(AppUser).filter_by(username="fresh_operator").first()
    assert user is not None
    assert user.role is AppUserRole.curator


def test_actor_is_reused_if_it_already_exists(backfill, monkeypatch, db_session):
    existing = AppUser(username="existing_operator", role=AppUserRole.admin)
    db_session.add(existing)
    db_session.flush()
    make_observation(db_session, external_source_name="CCCBDB")

    rc = _run_main(
        backfill,
        monkeypatch,
        db_session,
        ["--license", "CC-BY-4.0", "--actor", "existing_operator", "--commit"],
    )
    assert rc == 0
    submission = db_session.scalar(
        select(Submission).where(Submission.source_kind == SubmissionSourceKind.migration)
    )
    assert submission.created_by == existing.id
    # Role was not overwritten by _ensure_actor.
    db_session.refresh(existing)
    assert existing.role is AppUserRole.admin


# ---------------------------------------------------------------------------
# Dry run is the default
# ---------------------------------------------------------------------------


def test_dry_run_is_the_default_and_writes_nothing(backfill, monkeypatch, db_session, capsys):
    obs = make_observation(db_session, external_source_name="CCCBDB")

    rc = _run_main(backfill, monkeypatch, db_session, ["--license", "CC-BY-4.0", "--actor", "op1"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Dry run" in out
    assert db_session.scalar(select(Submission)) is None
    assert (
        db_session.scalar(
            select(SubmissionRecordLink).where(
                SubmissionRecordLink.record_type
                == SubmissionRecordType.molecular_property_observation,
                SubmissionRecordLink.record_id == obs.id,
            )
        )
        is None
    )


def test_commit_flag_persists(backfill, monkeypatch, db_session, capsys):
    obs = make_observation(db_session, external_source_name="CCCBDB")

    rc = _run_main(
        backfill, monkeypatch, db_session, ["--license", "CC-BY-4.0", "--actor", "op1", "--commit"]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "Linked 1 row" in out
    link = db_session.scalar(
        select(SubmissionRecordLink).where(
            SubmissionRecordLink.record_type
            == SubmissionRecordType.molecular_property_observation,
            SubmissionRecordLink.record_id == obs.id,
        )
    )
    assert link is not None


def test_no_unlinked_rows_reports_nothing_to_do(backfill, monkeypatch, db_session, capsys):
    rc = _run_main(
        backfill, monkeypatch, db_session, ["--license", "CC-BY-4.0", "--actor", "op1", "--commit"]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "Nothing to do" in out


# ---------------------------------------------------------------------------
# Pure guard function -- no DB needed
# ---------------------------------------------------------------------------


def test_refuse_target_pure_function(backfill):
    assert backfill._refuse_target("tckdb_test", None) is None
    assert backfill._refuse_target("tckdb_test_ci", None) is None
    assert backfill._refuse_target("tckdb_prod", None) is not None
    assert backfill._refuse_target("tckdb_prod", "tckdb_prod") is None
    assert backfill._refuse_target("tckdb_prod", "wrong_name") is not None
    # tckdb_testing_real must NOT pass as a test database by accident.
    assert backfill._refuse_target("tckdb_testing_real", None) is not None
