"""No test may call a pinned revision id "head".

A migration test has to name the revision it is about. What it must not do
is name it ``CURRENT_HEAD``, because the two words disagree: the value is
fixed the moment it is typed, and head moves every time a revision lands.
The name wins. Three separate tests have downgraded the shared per-run
database and restored it to a constant called ``_CURRENT_HEAD`` on the
reasonable belief that this put it back at head -- and each time the damage
landed on unrelated files hundreds of tests later, because the constant had
gone stale in between.

The third of those (#197) happened *after* the first two had been fixed in
place, each leaving a comment warning the next author about it. That is the
finding this file encodes: a comment explaining a misleading name is weaker
than not having the misleading name, so the name is now asserted against
rather than annotated.

``tests/db/_migration_chain.py`` is the sanctioned way to name a subject --
``revision_under_test("b1c2d3e4f5a6")`` -- and ``tests/db/conftest.py``
holds the two runtime halves: ``alembic_head``, which reads head from disk,
and ``_must_leave_the_database_at_head``, which fails the test that leaves
the shared database somewhere else.
"""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parent

#: An alembic revision id as this repo generates them: twelve hex digits.
_REVISION_ID = re.compile(r"\A[0-9a-f]{12}\Z")


def _binds_a_revision_to_a_head_name(node: ast.AST) -> list[str]:
    """Names in one statement that say "head" but hold a fixed revision."""
    if isinstance(node, ast.AnnAssign):
        targets, value = [node.target], node.value
    elif isinstance(node, ast.Assign):
        targets, value = node.targets, node.value
    else:
        return []
    if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
        return []
    if not _REVISION_ID.match(value.value):
        return []
    return [target.id for target in targets if isinstance(target, ast.Name) and "HEAD" in target.id.upper()]


def _python_files() -> list[Path]:
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(TESTS_ROOT):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        found.extend(Path(dirpath) / name for name in filenames if name.endswith(".py"))
    return sorted(found)


def test_no_test_binds_a_revision_id_to_a_name_saying_head() -> None:
    offenders: dict[str, list[str]] = {}
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        names = [name for node in ast.walk(tree) for name in _binds_a_revision_to_a_head_name(node)]
        if names:
            offenders[path.relative_to(TESTS_ROOT).as_posix()] = sorted(names)

    assert not offenders, (
        f"these constants hold a fixed revision under a name that says head: "
        f"{offenders}. A revision id is head only until the next one lands, so "
        "the name will be wrong and nobody will be present when it becomes "
        "wrong. If it means 'the revision this file is about', say that: "
        'tests/db/_migration_chain.revision_under_test("<id>") gives it and '
        "its parent. If it means head, do not write an id at all -- use the "
        "literal string 'head', or the alembic_head fixture."
    )


def test_the_scan_recognises_the_shape_it_is_looking_for() -> None:
    """The assertion above passes trivially if the matcher matches nothing.

    Its whole job is to find something that is currently absent from the
    tree, so a typo in the regex or in the ``ast`` walk would make it green
    forever and silently. These are the exact spellings the four files
    carried before they were converted, plus the ones they must not be
    replaced with.
    """
    caught = 'CURRENT_HEAD = "b1c2d3e4f5a6"\n_PREVIOUS_HEAD: str = "a8b9c0d1e2f3"\n'
    names = [name for node in ast.walk(ast.parse(caught)) for name in _binds_a_revision_to_a_head_name(node)]
    assert names == ["CURRENT_HEAD", "_PREVIOUS_HEAD"], names

    # Not the target: an honestly named subject, and head read from disk.
    allowed = (
        'STAGE2_REVISION = "c1d2e3f4a5b6"\n'
        '_MIGRATION = revision_under_test("b7e4d1a9c026")\n'
        "head = script.get_current_head()\n"
        'RESTORE_TO_HEAD = "head"\n'
    )
    assert not [name for node in ast.walk(ast.parse(allowed)) for name in _binds_a_revision_to_a_head_name(node)]
