"""A backup that restores to different data is not a backup.

``~/tckdb_backup.sh`` on the live deployment was a plain ``pg_dump`` with no
``--create``, so whoever restored it made the target database by hand -- and a
bare ``CREATE DATABASE`` inherits its encoding from ``template1``.  On that
host ``template1`` was ``SQL_ASCII`` while the real database was ``UTF8``
(measured 2026-08-12), so restoring a ``UTF8`` dump into a hand-made target
**exited 0, printed no error**, and silently mis-counted every multi-byte
character.

That is the worst available failure shape: the check that a restore worked --
its exit status -- cannot fail.  ``backend/scripts/ops/tckdb_backup.sh`` is the
versioned replacement, and this file is what keeps its two guarantees true.

The first test is the load-bearing one.  It does not read the script at all: it
builds a ``SQL_ASCII`` database and a ``UTF8`` one and runs the script's own
canary query against both, proving the canary *discriminates*.  A canary that
returns the same answer either way would let the script pass while restoring
into the wrong encoding, which is precisely the bug being guarded.

The second test is a deliberately crude text scan, in the same spirit as
``tests/test_scratch_database_names.py``: it cannot prove the script is
correct, only that the specific properties whose absence caused the incident
are still present.  Both are needed -- the first proves the mechanism works,
the second proves the script still uses it.
"""

from __future__ import annotations

from pathlib import Path

import conftest
from conftest import scratch_database_name
from sqlalchemy import create_engine, text

BACKEND_ROOT = Path(__file__).resolve().parents[2]
BACKUP_SCRIPT = BACKEND_ROOT / "scripts" / "ops" / "tckdb_backup.sh"

#: The UTF-8 bytes of U+2014 EM DASH, decoded by the server rather than written
#: as a literal.  A check for an encoding fault must not itself depend on how
#: its own source file is decoded -- that is the same class of bug it exists to
#: catch -- and the repo lints non-ASCII out of runtime strings besides.
CANARY_SQL = "SELECT length(convert_from('\\xe28094'::bytea, 'UTF8'))"


def _admin_engine():
    return create_engine(
        conftest._database_url("postgres"), future=True, isolation_level="AUTOCOMMIT"
    )


def _scalar_in(db_name: str, sql: str):
    engine = create_engine(conftest._database_url(db_name), future=True)
    try:
        with engine.connect() as connection:
            return connection.execute(text(sql)).scalar()
    finally:
        engine.dispose()


def _create(db_name: str, encoding: str) -> None:
    # TEMPLATE template0 is required to choose an encoding at all: template1
    # may already carry data in another one, and Postgres refuses rather than
    # guess.  LC_COLLATE/LC_CTYPE 'C' is what makes SQL_ASCII expressible on a
    # cluster whose template0 has a UTF-8 locale.
    engine = _admin_engine()
    try:
        with engine.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
            connection.execute(
                text(
                    f'CREATE DATABASE "{db_name}" ENCODING \'{encoding}\' '
                    "TEMPLATE template0 LC_COLLATE 'C' LC_CTYPE 'C'"
                )
            )
    finally:
        engine.dispose()


def _drop(db_name: str) -> None:
    engine = _admin_engine()
    try:
        with engine.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
    except Exception:  # pragma: no cover - cleanup must not mask a real failure
        pass
    finally:
        engine.dispose()


def test_the_encoding_canary_can_actually_fail() -> None:
    """The canary must give different answers in UTF8 and SQL_ASCII.

    This is the assertion the whole guard rests on.  If both encodings agreed,
    the backup script would verify nothing while appearing to verify
    everything.
    """
    utf8_db = scratch_database_name("backup_canary_utf8")
    ascii_db = scratch_database_name("backup_canary_sqlascii")
    try:
        _create(utf8_db, "UTF8")
        _create(ascii_db, "SQL_ASCII")

        in_utf8 = _scalar_in(utf8_db, CANARY_SQL)
        in_sql_ascii = _scalar_in(ascii_db, CANARY_SQL)

        assert in_utf8 == 1, (
            "In a UTF8 database the three bytes of U+2014 must measure one "
            f"character; got {in_utf8!r}. The canary is broken."
        )
        assert in_sql_ascii == 3, (
            "In a SQL_ASCII database the same bytes must measure three "
            f"characters; got {in_sql_ascii!r}. The canary cannot detect the "
            "encoding fault it exists to detect."
        )
        assert in_utf8 != in_sql_ascii
    finally:
        _drop(utf8_db)
        _drop(ascii_db)


def test_the_backup_script_pins_and_verifies_its_restore() -> None:
    """The properties whose absence caused the 2026-08-12 drift must persist."""
    assert BACKUP_SCRIPT.is_file(), f"{BACKUP_SCRIPT} is missing"
    body = BACKUP_SCRIPT.read_text(encoding="utf-8")

    assert "--create" in body, (
        "pg_dump must run with --create. Without it the archive carries no "
        "CREATE DATABASE, the restore target is made by hand, and it inherits "
        "template1's encoding -- which is the original defect."
    )
    assert "ENCODING = 'UTF8'" in body and "TEMPLATE = template0" in body, (
        "The script must assert that its own archive pins both the encoding "
        "and template0. pg_dump --create emits exactly that text, so checking "
        "for it is how the script proves --create survived."
    )
    # Matched on the two pieces that carry the meaning rather than on the whole
    # statement: the script escapes the backslash for its own shell quoting, so
    # an equality check against CANARY_SQL would silently never match and the
    # assertion would pass on its fallback forever.
    assert "convert_from" in body and "e28094" in body, (
        "The script must run the multi-byte canary against the restored "
        "database. Verifying only that pg_dump exited 0 is the check that "
        "cannot fail."
    )
    assert "md5sum" in body, (
        "The script must compare source and restored data, not merely confirm "
        "the restore ran."
    )

    non_ascii = [
        (index, line)
        for index, line in enumerate(body.splitlines(), start=1)
        if any(ord(character) > 127 for character in line)
    ]
    assert not non_ascii, (
        "The backup script must stay ASCII-clean: a script that detects an "
        "encoding fault must not depend on the decoding of its own file. "
        f"Offending lines: {[index for index, _ in non_ascii]}"
    )
