"""A database error that is neither a conflict nor an outage is a coded 500 (#628).

``IntegrityError`` is a 409 and ``OperationalError`` a 503. Every other driver
error -- a deferred trigger raising ``P0001``, a ``DataError`` such as
``22P02`` at commit -- used to have no handler, so it reached the client as a
``text/plain`` "Internal Server Error" with a traceback in the ASGI log.

The failures here are real ones, raised by PostgreSQL through psycopg and
wrapped by SQLAlchemy, and they happen at *commit*, in ``get_write_db``'s
teardown, which is where #628 saw them.
"""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.api import deps as api_deps
from app.api.deps import get_write_db
from app.api.errors import register_exception_handlers

#: Statements PostgreSQL refuses with a class other than integrity (23) or
#: operator intervention / connection (53, 57, 08).
_REFUSED_AT_COMMIT = {
    "trigger_raise_p0001": (
        "DO $$ BEGIN RAISE EXCEPTION 'secret trigger text: acc_secret_table' "
        "USING ERRCODE = 'P0001'; END $$"
    ),
    "data_error_22p02": "SELECT 'secret-literal'::int",
}
_LEAKS = ("secret", "acc_secret_table", "DO $$", "SELECT", "P0001", "22P02", "sqlstate")


@pytest.fixture
def commit_refused_client(db_engine, monkeypatch):
    """The real ``get_write_db`` over a session whose COMMIT is refused by the database."""
    statement: dict[str, str] = {}

    class _RefusedAtCommit(Session):
        def commit(self) -> None:
            if statement:
                self.execute(text(statement["sql"]))
            super().commit()

    monkeypatch.setattr(
        api_deps, "SessionLocal", sessionmaker(bind=db_engine, class_=_RefusedAtCommit)
    )

    app = FastAPI()
    register_exception_handlers(app)

    @app.post("/write", status_code=201)
    def _write(session: Session = Depends(get_write_db, scope="function")):
        return {"ok": True}

    client = TestClient(app, raise_server_exceptions=False)
    client.refuse_with = statement  # type: ignore[attr-defined]
    return client


@pytest.mark.parametrize("name", sorted(_REFUSED_AT_COMMIT))
def test_a_commit_refused_by_the_database_is_a_coded_500(commit_refused_client, name) -> None:
    commit_refused_client.refuse_with["sql"] = _REFUSED_AT_COMMIT[name]
    resp = commit_refused_client.post("/write")

    assert resp.status_code == 500, resp.text
    assert resp.headers["content-type"].startswith("application/json"), resp.text
    body = resp.json()
    assert body["code"] == "database_request_failed", body
    assert body["context"] == {}
    rendered = repr(body)
    for leak in _LEAKS:
        assert leak not in rendered, f"{leak!r} reached the client: {body}"


def test_the_refusal_is_a_dbapi_error_that_no_narrower_handler_takes(db_engine) -> None:
    """Pins the premise: the two failures above are neither of the classes that
    already had a handler, so a 500 here is the new handler and not an old one."""
    with Session(db_engine) as session:
        for sql in _REFUSED_AT_COMMIT.values():
            with pytest.raises(DBAPIError) as caught:
                session.execute(text(sql))
            assert not isinstance(caught.value, (IntegrityError, OperationalError)), type(caught.value)
            session.rollback()


def test_a_write_that_commits_is_untouched(commit_refused_client) -> None:
    resp = commit_refused_client.post("/write")
    assert resp.status_code == 201, resp.text
    assert resp.json() == {"ok": True}
