"""A restore runs under its own statement ceiling, never under the API's 30 s.

With the API's ``statement_timeout`` now really applied to every checkout
(#604), a restore would inherit 30 s. Its final ``COMMIT`` runs every deferred
foreign-key check for the whole load; measured at 16.2 s on a 3.47-million-row
corpus on a fast workstation, so the limit would have cancelled restores on
slower hosts. ``restore_archive`` therefore raises the limit for its own
transaction (``SET LOCAL``) and puts it back when the transaction ends.
"""

from __future__ import annotations

import io

from sqlalchemy import text

from app.services.archive import core as archive_core
from app.services.archive import restore_archive, write_archive
from tests.services.archive.test_archive import _empty_archive_tables

CEILING_MS = archive_core.ARCHIVE_RESTORE_STATEMENT_TIMEOUT_MS


def _show_ms(session) -> int:
    return int(session.execute(text("SELECT setting::bigint FROM pg_settings WHERE name = 'statement_timeout'")).scalar_one())


def _restore_recording_timeout(db_session, monkeypatch, *, before_ms: int | None) -> list[int]:
    """Restore an empty archive, recording the limit seen in the write transaction."""
    _empty_archive_tables(db_session)
    payload = io.BytesIO()
    write_archive(db_session, payload)
    if before_ms is not None:
        db_session.execute(text(f"SET LOCAL statement_timeout = {before_ms}"))
    seen: list[int] = []
    real_lock = archive_core._lock_snapshot_tables

    def _spy(session, tables):
        seen.append(_show_ms(session))
        return real_lock(session, tables)

    monkeypatch.setattr(archive_core, "_lock_snapshot_tables", _spy)
    restore_archive(db_session, io.BytesIO(payload.getvalue()))
    return seen


def test_restore_runs_with_the_long_ceiling_and_the_api_limit_is_not_kept(db_session, monkeypatch) -> None:
    assert _show_ms(db_session) == 30_000, "the harness engine should carry the API's 30 s limit"
    seen = _restore_recording_timeout(db_session, monkeypatch, before_ms=None)
    assert seen == [CEILING_MS]


def test_restore_never_lowers_a_longer_limit(db_session, monkeypatch) -> None:
    longer = CEILING_MS * 2
    assert _restore_recording_timeout(db_session, monkeypatch, before_ms=longer) == [longer]


def test_restore_does_not_impose_a_limit_where_the_operator_disabled_it(db_session, monkeypatch) -> None:
    assert _restore_recording_timeout(db_session, monkeypatch, before_ms=0) == [0]


def test_the_ceiling_is_local_to_the_restore_transaction(db_engine) -> None:
    """``SET LOCAL`` must not leak onto the pooled connection after the commit."""
    from sqlalchemy.orm import Session

    with Session(db_engine) as session:
        with session.begin():
            archive_core._allow_long_restore_statements(session)
            assert _show_ms(session) == CEILING_MS
        assert _show_ms(session) == 30_000
