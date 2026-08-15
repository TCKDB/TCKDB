"""What a migration test may know about the revision chain, and how.

Every test in this directory that drives ``alembic.command`` needs two
revision ids: the one whose ``upgrade`` is under test, and the one to
stand on while running it. Four files used to write both down, under the
names ``_CURRENT_HEAD`` and ``_PREVIOUS_HEAD``.

Only one of those two is information, and neither name is true:

* **The parent is not information.** It is the revision's own
  ``down_revision``, declared in the migration file. Copying it into a
  test creates a second place that can disagree with the first, and a
  disagreement does not surface as a failure -- the test downgrades to
  some other point and usually still passes.
* **Neither is "head".** ``_CURRENT_HEAD`` never meant head. It meant
  *the revision under test*, which is head only between the day it is
  written and the day the next revision lands. ``_PREVIOUS_HEAD`` is
  time-relative in the same way: a revision's parent is fixed forever,
  but "what head used to be" is a statement about a calendar.

The name is not a cosmetic complaint. It is the defect. Three tests have
now downgraded the shared per-run database and restored it to
``_CURRENT_HEAD`` believing that meant head; the third
(``test_imaginary_mode_tau_basis_constraint.py``, #197) did it after the
first two had been fixed in place and had left comments warning about it.
A name that says the wrong thing outlasts every comment written to
explain that it does.

So a test names exactly one thing here -- its subject -- and gets the
rest from the script directory::

    _MIGRATION = revision_under_test("b7e4d1a9c026")

    command.downgrade(config, _MIGRATION.parent)
    command.upgrade(config, _MIGRATION.revision)

``revision_under_test`` deliberately refuses to resolve "head", so a
subject cannot be named that way even by accident. Head is a separate
question with a separate answer -- ``current_head`` below, exposed as the
``alembic_head`` fixture -- and *restoring* the shared database is a third
one again: the literal string ``"head"`` passed to ``command.upgrade``,
with ``conftest.py::_must_leave_the_database_at_head`` failing the test
that gets it wrong. Keeping the three apart is the entire point; the bug
this module exists to prevent is one of them being mistaken for another.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

_BACKEND_ROOT = Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def script_directory() -> ScriptDirectory:
    """The migration chain as it exists on disk, read once per process.

    The single place in this tree that opens ``alembic.ini``. Reading the
    chain rather than restating it is the whole point of the module, so
    there is one reader and everything else asks it.
    """
    return ScriptDirectory.from_config(Config(str(_BACKEND_ROOT / "alembic.ini")))


def current_head() -> str:
    """The revision ``alembic upgrade head`` would stop at, read from disk.

    Read rather than written down. Every hard-coded copy of "the current
    head" in this tree has gone stale within days of being typed, because
    the thing that makes it stale is the next revision landing -- which is
    the one event nobody grepping for the old hash is present for.
    """
    return script_directory().get_current_head()


@dataclass(frozen=True)
class RevisionUnderTest:
    """A migration test's subject, and the revision it runs from.

    ``revision`` is the id the test named. ``parent`` is that revision's
    ``down_revision``, read from the migration file rather than copied
    into the test, so the two can never drift apart.
    """

    revision: str
    parent: str


def revision_under_test(revision: str) -> RevisionUnderTest:
    """Resolve the revision a test is about, and the one below it.

    Refuses a symbolic selector -- ``"head"``, ``"heads"``, ``"base"``,
    ``"<id>-1"``, or an abbreviated hash. Alembic resolves all of those
    happily, and accepting them would put back exactly the ambiguity this
    module exists to remove: a test whose subject silently changes when a
    revision lands is a test that measures a different thing each week.
    The check is that the resolved id is the id that was asked for, which
    is true only of a full literal revision id.

    Raises rather than returning a sentinel, and does it at import time
    because that is when the call sites run. A test that cannot identify
    its own subject has nothing to fall back to, and a collection error
    naming the revision beats a downgrade to somewhere unexpected. An
    unknown id surfaces as alembic's own ``CommandError``; everything else
    here raises ``ValueError``.
    """
    resolved = script_directory().get_revision(revision)
    if resolved is None or resolved.revision != revision:
        raise ValueError(
            f"{revision!r} is not a literal revision id -- alembic resolved it "
            f"to {resolved and resolved.revision!r}. A migration test must name "
            "the revision it is about, because a selector like 'head' or a "
            "partial hash points somewhere else as soon as the chain grows, "
            "and the test then measures a revision nobody chose."
        )

    parent = resolved.down_revision
    if isinstance(parent, tuple):
        raise ValueError(
            f"{revision!r} is a merge point with parents {parent!r}; there is "
            "no single revision to stand on, so this helper cannot pick one. "
            "Name the intended parent explicitly in the test."
        )
    if parent is None:
        raise ValueError(
            f"{revision!r} is the base revision and has nothing below it, so "
            "there is no state for a test to downgrade to."
        )
    return RevisionUnderTest(revision=resolved.revision, parent=parent)
