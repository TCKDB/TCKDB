"""What ``b7e4d1a9c026`` does when the names are not what it expects.

The migration renames six CHECK constraints and one unique index. On the
database it was written against, every one of the seven is found under
the old name and renamed -- which is a branch the round trip already
covers, and which says nothing about the three cases where the rename is
not simply available.

Those cases are not hypothetical in the way a defensive branch usually
is. This is a *rename*, and hosts reach a given name by different routes:
a database built from scratch after the conventions changed, a re-run of
the revision, a restore from a dump taken mid-flight, a constraint
dropped by hand during an incident. Each of the three is measured here
against a catalog deliberately put into that state, because none of them
runs on a clean upgrade and so none of them would otherwise ever execute.

The choice under test, stated once: **already correctly named is a
no-op**, and the two genuine ambiguities refuse. The goal of the
revision is "the rule is present under the name the model predicts". If
that already holds, the goal is met and failing the deploy would be
punishing a database for being correct. If the rule is *absent*
entirely, or present twice, the migration cannot reach that goal by
renaming and will not pretend otherwise -- recreating a dropped
constraint would mean installing a rule against rows it has not
measured, and merging two is a question only whoever made the second one
can answer.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text

from alembic import command

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_PREVIOUS_HEAD = "e2a7c9d4b615"
_CURRENT_HEAD = "b7e4d1a9c026"

#: The pair exercised here. ``calculation_artifact.bytes > 0`` is chosen
#: for having a one-clause definition that can be spelled out below
#: without ambiguity; the migration treats all seven identically, and
#: ``_OTHER_TABLE`` below checks that it really does by watching a
#: different one roll back.
_TABLE = "calculation_artifact"
_OLD = "ck_calculation_artifact_ck_calculation_artifact_bytes_gt_0"
_NEW = "ck_calculation_artifact_bytes_gt_0"
_DEFINITION = "bytes > 0"

#: Renamed *before* ``_TABLE`` in the migration's list, so if a refusal
#: on ``_TABLE`` left this one renamed, the migration would be applying
#: changes it then declined to finish.
_OTHER_OLD = "ck_calc_freq_mode_ck_calc_freq_mode_imaginary_dispositi_ddc2"


@pytest.fixture
def alembic_at_previous_head(db_engine, monkeypatch):
    """Hold the database one revision below head for the duration.

    Restores head in teardown whatever the test did to the catalog, so a
    failure here fails this test rather than every test that follows it.

    The teardown runs ``downgrade`` before ``upgrade``, and that ordering
    is load-bearing rather than defensive. Two of the tests below finish
    by repairing the catalog and letting the migration through, which
    leaves ``alembic_version`` at head -- so a teardown that only put the
    *names* back and then called ``upgrade`` would find nothing to run,
    and would hand the next test on this worker a database carrying the
    doubled name under a version stamp claiming it had been renamed.
    That is not hypothetical: it is what the first version of this file
    did, and ``test_constraint_names_match_the_model.py`` caught it in CI
    as a schema-wide sweep failure with no obvious author. Forcing the
    version back down first makes the final ``upgrade`` real work again.
    """
    url = db_engine.url.render_as_string(hide_password=False)
    for key, value in (
        ("DB_NAME", db_engine.url.database),
        ("DB_USER", "tckdb"),
        ("DB_PASSWORD", "tckdb"),
        ("DB_HOST", "127.0.0.1"),
        ("DB_PORT", "5432"),
    ):
        monkeypatch.setenv(key, value)
    db_engine.dispose()

    config = Config(str(_BACKEND_ROOT / "alembic.ini"))
    engine = create_engine(url)
    command.downgrade(config, _PREVIOUS_HEAD)
    try:
        yield config, engine
    finally:
        # Order matters; see the docstring. Names first so the downgrade
        # meets a catalog it can act on, then the version stamp back down
        # so the final upgrade actually runs the rename.
        _restore_pre_upgrade_names(engine)
        command.downgrade(config, _PREVIOUS_HEAD)
        command.upgrade(config, _CURRENT_HEAD)
        _assert_left_at_head(engine)
        engine.dispose()


def _constraint_names(engine) -> set[str]:
    with engine.connect() as connection:
        return set(
            connection.execute(
                text(
                    "SELECT conname FROM pg_constraint"
                    " WHERE conrelid = CAST(:table AS regclass) AND contype = 'c'"
                ),
                {"table": _TABLE},
            )
            .scalars()
            .all()
        )


def _execute(engine, statement: str) -> None:
    with engine.begin() as connection:
        connection.execute(text(statement))


def _restore_pre_upgrade_names(engine) -> None:
    """Put the catalog back into the shape the downgrade left it in."""
    names = _constraint_names(engine)
    if _NEW in names and _OLD in names:
        _execute(engine, f'ALTER TABLE {_TABLE} DROP CONSTRAINT "{_NEW}"')
    elif _NEW in names:
        _execute(
            engine, f'ALTER TABLE {_TABLE} RENAME CONSTRAINT "{_NEW}" TO "{_OLD}"'
        )
    elif _OLD not in names:
        _execute(
            engine,
            f'ALTER TABLE {_TABLE} ADD CONSTRAINT "{_OLD}" CHECK ({_DEFINITION})',
        )


def _assert_left_at_head(engine) -> None:
    """Fail loudly here rather than quietly somewhere downstream.

    This file is the only thing in the suite that alters the schema of
    the worker's database, so a teardown that half-worked does not show
    up as a failure in this file -- it shows up as an unrelated test
    somewhere else reading a catalog nobody expected. Checking the exit
    state keeps the blame local.
    """
    names = _constraint_names(engine)
    assert _NEW in names and _OLD not in names, (
        "teardown left calculation_artifact carrying "
        f"{sorted(n for n in names if n.endswith('bytes_gt_0'))}; the next "
        "test on this worker would see a schema this file broke"
    )
    with engine.connect() as connection:
        version = connection.scalar(text("SELECT version_num FROM alembic_version"))
    assert version == _CURRENT_HEAD, (
        f"teardown left alembic_version at {version!r}, not {_CURRENT_HEAD!r}"
    )


def test_a_constraint_already_correctly_named_is_left_alone(alembic_at_previous_head):
    """The re-run and the built-from-scratch host: no-op, deploy proceeds.

    Refusing here would block an upgrade on a database that is already in
    the state this revision exists to produce.
    """
    config, engine = alembic_at_previous_head
    _execute(engine, f'ALTER TABLE {_TABLE} RENAME CONSTRAINT "{_OLD}" TO "{_NEW}"')

    command.upgrade(config, _CURRENT_HEAD)

    names = _constraint_names(engine)
    assert _NEW in names, "the correctly named constraint was disturbed"
    assert _OLD not in names, "the old name came back"
    assert sum(1 for name in names if name.endswith("bytes_gt_0")) == 1, (
        f"the rule ended up on the table more than once: {sorted(names)}"
    )


def test_a_missing_constraint_is_refused_rather_than_recreated(
    alembic_at_previous_head,
):
    """Neither name present means a rule was dropped, not renamed.

    Recreating it here would install an unmeasured rule and, worse, would
    report success -- leaving an operator believing the schema is intact
    when something in their environment is dropping constraints.
    """
    config, engine = alembic_at_previous_head
    _execute(engine, f'ALTER TABLE {_TABLE} DROP CONSTRAINT "{_OLD}"')

    with pytest.raises(RuntimeError) as excinfo:
        command.upgrade(config, _CURRENT_HEAD)

    message = str(excinfo.value)
    assert _OLD in message and _NEW in message, (
        f"the refusal must name both spellings it looked for: {message}"
    )
    assert _TABLE in message

    # Nothing was installed, and nothing that had already been renamed in
    # the same run survived: the migration is all-or-nothing.
    assert _NEW not in _constraint_names(engine)
    with engine.connect() as connection:
        still_old = connection.scalar(
            text("SELECT count(*) FROM pg_constraint WHERE conname = :name"),
            {"name": _OTHER_OLD},
        )
    assert still_old == 1, (
        "a constraint renamed earlier in the same upgrade was not rolled "
        "back when a later one refused"
    )

    # Repaired the way an operator would -- by restoring the rule -- the
    # same migration goes through.
    _execute(
        engine,
        f'ALTER TABLE {_TABLE} ADD CONSTRAINT "{_OLD}" CHECK ({_DEFINITION})',
    )
    command.upgrade(config, _CURRENT_HEAD)
    assert _NEW in _constraint_names(engine)


def test_both_names_present_is_refused_rather_than_merged(alembic_at_previous_head):
    """A rename cannot merge two constraints, so it declines to try.

    Which of the two to keep depends on how the second one arrived, and
    the migration has no way to find that out. Dropping either would
    discard a rule somebody added on purpose.
    """
    config, engine = alembic_at_previous_head
    _execute(
        engine,
        f'ALTER TABLE {_TABLE} ADD CONSTRAINT "{_NEW}" CHECK ({_DEFINITION})',
    )

    with pytest.raises(RuntimeError) as excinfo:
        command.upgrade(config, _CURRENT_HEAD)

    message = str(excinfo.value)
    assert _OLD in message and _NEW in message, (
        f"the refusal must name both constraints it found: {message}"
    )

    # Both are still there: the migration changed nothing on its way out.
    names = _constraint_names(engine)
    assert _OLD in names and _NEW in names

    _execute(engine, f'ALTER TABLE {_TABLE} DROP CONSTRAINT "{_NEW}"')
    command.upgrade(config, _CURRENT_HEAD)
    assert _NEW in _constraint_names(engine)
