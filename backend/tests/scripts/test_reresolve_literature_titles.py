"""``reresolve_literature_titles.py``: dry run, upgrade, failed lookup, classification of NULL rows.

Runs the script's ``main()`` against the pytest transaction with the network mocked, the
same way ``test_backfill_assumed_tau.py`` does.
"""

from __future__ import annotations

import contextlib
import importlib.util
import pathlib
import sys

import pytest

from app.db.models.common import LiteratureTitleSource
from app.db.models.literature import Literature

_SCRIPT = pathlib.Path(__file__).parents[2] / "scripts" / "ops" / "reresolve_literature_titles.py"


@pytest.fixture(scope="module")
def script():
    spec = importlib.util.spec_from_file_location("reresolve_literature_titles", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _SessionProxy:
    def __init__(self, session) -> None:
        self._session = session

    def __getattr__(self, name):
        return getattr(self._session, name)

    def __call__(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def begin(self):
        return contextlib.nullcontext()


def _run(script, monkeypatch, db_session, argv, *, db_name="tckdb_test_reresolve"):
    import app.api.deps as deps
    from app.api.config import settings

    monkeypatch.setattr("sys.argv", ["reresolve_literature_titles.py", "--delay", "0", *argv], raising=False)
    monkeypatch.setattr(deps, "SessionLocal", _SessionProxy(db_session))
    monkeypatch.setattr(settings, "db_name", db_name)
    return script.main()


def _lit(session, doi, title, source) -> Literature:
    row = Literature(kind="article", title=title, doi=doi, title_source=source)
    session.add(row)
    session.flush()
    return row


@pytest.fixture
def registry(monkeypatch):
    """A fake registry: DOI or ISBN -> title (or absent for a failed lookup), recording every call."""
    titles: dict[str, str | None] = {}
    calls: list[str] = []
    module = sys.modules["reresolve_literature_titles"]

    def fake_doi(doi):
        calls.append(doi)
        title = titles.get(doi)
        return None if title is None else {"title": title}

    def fake_isbn(isbn):
        calls.append(isbn)
        title = titles.get(isbn)
        return None if title is None else {"Title": title}

    monkeypatch.setattr(module, "fetch_doi_metadata", fake_doi)
    monkeypatch.setattr(module, "fetch_isbn_metadata", fake_isbn)
    return titles, calls


def test_dry_run_writes_nothing(script, registry, monkeypatch, db_session, capsys) -> None:
    titles, calls = registry
    row = _lit(db_session, "10.1000/dry", "Typed Title", LiteratureTitleSource.depositor_supplied)
    titles["10.1000/dry"] = "Registry Title"

    assert _run(script, monkeypatch, db_session, ["--ref", row.public_ref]) == 0

    db_session.refresh(row)
    assert (row.title, row.title_source) == ("Typed Title", LiteratureTitleSource.depositor_supplied)
    assert calls == ["10.1000/dry"]  # it did look
    out = capsys.readouterr().out
    assert f"{row.public_ref}  depositor_supplied -> resolved_metadata" in out
    assert "Dry run" in out
    assert "Registry Title" not in out and "Typed Title" not in out  # refs only, never titles


def test_apply_upgrades_a_depositor_supplied_row_and_touches_nothing_else(script, registry, monkeypatch, db_session) -> None:
    titles, _ = registry
    row = _lit(db_session, "10.1000/up", "Typed Title", LiteratureTitleSource.depositor_supplied)
    ref, doi, isbn = row.public_ref, row.doi, row.isbn
    titles["10.1000/up"] = "Registry Title"

    assert _run(script, monkeypatch, db_session, ["--apply", "--ref", ref]) == 0

    db_session.refresh(row)
    assert row.title == "Registry Title"
    assert row.title_source == LiteratureTitleSource.resolved_metadata
    assert (row.public_ref, row.doi, row.isbn) == (ref, doi, isbn)


def test_a_failed_lookup_leaves_the_row_untouched_and_is_counted(script, registry, monkeypatch, db_session, capsys) -> None:
    row = _lit(db_session, "10.1000/fail", "Typed Title", LiteratureTitleSource.depositor_supplied)  # not in the registry

    assert _run(script, monkeypatch, db_session, ["--apply", "--ref", row.public_ref]) == 0

    db_session.refresh(row)
    assert (row.title, row.title_source) == ("Typed Title", LiteratureTitleSource.depositor_supplied)
    out = capsys.readouterr().out
    assert "unchanged (no_title)" in out and "no_title=1" in out


def test_a_lookup_that_raises_is_counted_not_fatal(script, registry, monkeypatch, db_session, capsys) -> None:
    row = _lit(db_session, "10.1000/boom", "Typed Title", None)
    monkeypatch.setattr(
        sys.modules["reresolve_literature_titles"], "fetch_doi_metadata",
        lambda doi: (_ for _ in ()).throw(RuntimeError("registry down")),
    )

    assert _run(script, monkeypatch, db_session, ["--apply", "--ref", row.public_ref]) == 0

    db_session.refresh(row)
    assert row.title_source is None and row.title == "Typed Title"
    assert "lookup_error=1" in capsys.readouterr().out


def test_a_null_row_with_a_registry_title_is_classified(script, registry, monkeypatch, db_session, capsys) -> None:
    titles, _ = registry
    row = _lit(db_session, "10.1000/null", "Old Title", None)
    titles["10.1000/null"] = "Registry Title"

    assert _run(script, monkeypatch, db_session, ["--apply", "--ref", row.public_ref]) == 0

    db_session.refresh(row)
    assert (row.title, row.title_source) == ("Registry Title", LiteratureTitleSource.resolved_metadata)
    assert f"{row.public_ref}  not_recorded -> resolved_metadata" in capsys.readouterr().out


def test_resolved_rows_and_rows_without_identifiers_are_never_looked_up(script, registry, monkeypatch, db_session) -> None:
    titles, calls = registry
    resolved = _lit(db_session, "10.1000/done", "Registry Title", LiteratureTitleSource.resolved_metadata)
    no_id = Literature(kind="report", title="Manual", title_source=LiteratureTitleSource.depositor_supplied)
    db_session.add(no_id)
    db_session.flush()
    titles["10.1000/done"] = "Something Else"

    _run(script, monkeypatch, db_session, ["--apply", "--limit", "100000"])

    assert "10.1000/done" not in calls
    db_session.refresh(resolved)
    db_session.refresh(no_id)
    assert resolved.title == "Registry Title"
    assert (no_id.title, no_id.title_source) == ("Manual", LiteratureTitleSource.depositor_supplied)


def test_apply_on_a_deployed_looking_database_is_refused_without_the_flag(script, registry, monkeypatch, db_session) -> None:
    titles, calls = registry
    row = _lit(db_session, "10.1000/guard", "Typed Title", None)
    titles["10.1000/guard"] = "Registry Title"

    assert _run(script, monkeypatch, db_session, ["--apply", "--ref", row.public_ref], db_name="tckdb_prod") == 2
    assert calls == []
    db_session.refresh(row)
    assert row.title_source is None


def test_the_isbn_branch_upgrades_a_row_that_has_only_an_isbn(script, registry, monkeypatch, db_session) -> None:
    titles, calls = registry
    row = Literature(kind="book", title="Typed Book", isbn="9780387954523", title_source=LiteratureTitleSource.depositor_supplied)
    db_session.add(row)
    db_session.flush()
    titles["9780387954523"] = "Registry Book"

    assert _run(script, monkeypatch, db_session, ["--apply", "--ref", row.public_ref]) == 0

    db_session.refresh(row)
    assert (row.title, row.title_source) == ("Registry Book", LiteratureTitleSource.resolved_metadata)
    assert calls == ["9780387954523"]


def test_a_mixed_run_tallies_each_outcome_and_flags_titles_that_differ(script, registry, monkeypatch, db_session, capsys) -> None:
    titles, _ = registry
    # Same title up to case and a trailing period: an upgrade, not a mismatch.
    same = _lit(db_session, "10.1000/mix1", "a study of x.", LiteratureTitleSource.depositor_supplied)
    titles["10.1000/mix1"] = "A Study of X"
    # A different paper under the DOI: an upgrade, flagged.
    other = _lit(db_session, "10.1000/mix2", "Typed Title About Y", None)
    titles["10.1000/mix2"] = "A Completely Different Paper"
    # No registry answer.
    missing = _lit(db_session, "10.1000/mix3", "Typed Title", LiteratureTitleSource.depositor_supplied)
    # Out of scope: already verified.
    done = _lit(db_session, "10.1000/mix4", "Verified", LiteratureTitleSource.resolved_metadata)
    titles["10.1000/mix4"] = "Changed Upstream"
    monkeypatch.setattr(script, "_BATCH", 1)  # commit after every row, exercising the batch path

    assert _run(script, monkeypatch, db_session, ["--apply", "--since", "2000-01-01"]) == 0

    for row in (same, other, missing, done):
        db_session.refresh(row)
    assert (same.title, same.title_source) == ("A Study of X", LiteratureTitleSource.resolved_metadata)
    assert (other.title, other.title_source) == ("A Completely Different Paper", LiteratureTitleSource.resolved_metadata)
    assert (missing.title, missing.title_source) == ("Typed Title", LiteratureTitleSource.depositor_supplied)
    assert (done.title, done.title_source) == ("Verified", LiteratureTitleSource.resolved_metadata)
    out = capsys.readouterr().out
    assert "no_title=1" in out and "upgraded=2" in out
    assert f"{other.public_ref}  not_recorded -> resolved_metadata  [registry title differs from stored]" in out
    assert f"{same.public_ref}  depositor_supplied -> resolved_metadata\n" in out  # not flagged
    assert "1 upgrade(s) replaced a stored title the registry title does not match" in out
    assert "A Completely Different Paper" not in out and "Typed Title About Y" not in out


def test_since_leaves_older_rows_alone(script, registry, monkeypatch, db_session) -> None:
    from datetime import datetime, timezone

    titles, calls = registry
    old = _lit(db_session, "10.1000/old", "Typed Old", None)
    new = _lit(db_session, "10.1000/new", "Typed New", None)
    old.created_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
    db_session.flush()
    titles["10.1000/old"] = "Registry Old"
    titles["10.1000/new"] = "Registry New"

    assert _run(script, monkeypatch, db_session, ["--apply", "--since", "2025-01-01"]) == 0

    db_session.refresh(old)
    db_session.refresh(new)
    assert (old.title, old.title_source) == ("Typed Old", None)
    assert (new.title, new.title_source) == ("Registry New", LiteratureTitleSource.resolved_metadata)
    assert "10.1000/old" not in calls
