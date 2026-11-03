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
    """A fake registry: DOI -> title (or None for a failed lookup), recording every call."""
    titles: dict[str, str | None] = {}
    calls: list[str] = []

    def fake(doi):
        calls.append(doi)
        title = titles.get(doi)
        return None if title is None else {"title": title}

    monkeypatch.setattr("app.services.literature_metadata.fetch_doi_metadata", fake)
    monkeypatch.setattr(sys.modules["reresolve_literature_titles"], "fetch_doi_metadata", fake)
    monkeypatch.setattr(
        sys.modules["reresolve_literature_titles"], "fetch_isbn_metadata",
        lambda isbn: (_ for _ in ()).throw(AssertionError("no ISBN lookups expected")),
    )
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
