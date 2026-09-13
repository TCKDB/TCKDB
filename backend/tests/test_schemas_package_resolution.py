"""The wire-schemas package must come from the checkout under test.

``tckdb_schemas`` is installed editable, and an editable install points at
exactly one directory: the checkout ``pip install -e`` was run from. Inside a
``git worktree`` that is the MAIN checkout, so a branch's own changes to the
wire schemas are invisible to its own tests. The suite reports green against
code that is not the code under review.

What makes this worth a guard rather than a note in a README is that every
part of it is silent:

- A wrong ``PYTHONPATH`` entry raises nothing. Python skips a path that does
  not exist, so ``.../tckdb-schemas/src`` -- a directory that has never
  existed; the package lives at ``.../tckdb-schemas/tckdb_schemas`` -- simply
  degrades to "use the editable install", with no warning.
- Generators inherit the same resolution. Regenerating the OpenAPI golden
  under a bad path reads the *other* checkout's schema and writes back the
  bytes already on disk; the gate then compares stale against stale and
  passes. Artifact and check agree with each other and both disagree with
  the branch.
- The resulting test failures are indistinguishable from a real defect. They
  point at the changed code and say the change is not there.

All of that happened while building #458: two "regenerations" of a golden
that changed nothing, three CI cycles, and six tests failing against a schema
change that was correct.

CI cannot reproduce it -- one checkout, so the editable install necessarily
resolves inside it -- which is exactly why the check lives in ``conftest``
and why these tests drive it directly rather than trying to arrange the
condition.
"""

from __future__ import annotations

from pathlib import Path

import conftest
import pytest


def _repo_root() -> Path:
    return Path(conftest.__file__).resolve().parents[2]


def test_guard_accepts_a_package_inside_this_checkout(monkeypatch) -> None:
    """The normal case, and the one CI is always in."""
    import tckdb_schemas

    inside = _repo_root() / "schemas" / "python" / "tckdb-schemas"
    inside = inside / "tckdb_schemas" / "__init__.py"
    monkeypatch.setattr(tckdb_schemas, "__file__", str(inside))

    conftest._assert_schemas_package_is_this_checkout()


def test_guard_refuses_a_package_from_another_checkout(monkeypatch) -> None:
    """The failure this exists for: an editable install resolving to the main
    checkout while the tests run from a worktree.

    The path used here is the shape the real one takes -- a sibling checkout
    that is emphatically not a parent of this one.
    """
    import tckdb_schemas

    foreign = Path("/home/somebody/another-checkout/TCKDB_v2")
    foreign = foreign / "schemas/python/tckdb-schemas/tckdb_schemas/__init__.py"
    monkeypatch.setattr(tckdb_schemas, "__file__", str(foreign))

    with pytest.raises(conftest.ForeignSchemasPackageError) as excinfo:
        conftest._assert_schemas_package_is_this_checkout()

    message = str(excinfo.value)
    # The message has to carry both halves of the comparison. A refusal that
    # says only "wrong package" sends the reader looking for a defect in the
    # branch, which is the trap this replaces.
    assert str(foreign) in message
    assert str(_repo_root()) in message
    # And the remedy, spelled for THIS checkout -- not a generic instruction
    # the reader has to translate.
    expected_export = str(_repo_root() / "schemas" / "python" / "tckdb-schemas")
    assert expected_export in message
    assert "PYTHONPATH" in message


def test_guard_is_wired_into_session_start() -> None:
    """A guard nothing calls is decoration.

    ``pytest_sessionstart`` is where it has to run: before the first test, and
    only in the controller, so an xdist worker does not re-refuse a session the
    controller already approved.
    """
    import inspect

    source = inspect.getsource(conftest.pytest_sessionstart)
    assert "_assert_schemas_package_is_this_checkout()" in source


def test_refusal_derives_from_usage_error() -> None:
    """Rendering, which is the whole point of the message.

    An exception out of ``pytest_sessionstart`` is printed as
    ``INTERNALERROR>`` followed by a pluggy traceback, which buries the
    message under frames that read as "pytest is broken". ``UsageError`` is
    caught by pytest's entry point and printed plainly. Same reasoning as
    ``ConcurrentTestRunError``; see its docstring.
    """
    assert issubclass(conftest.ForeignSchemasPackageError, pytest.UsageError)


def test_gate_scripts_put_this_checkout_first_on_pythonpath() -> None:
    """The other half of the fix.

    ``conftest`` refuses a bad resolution; the shared gate library prevents
    one, so the common path needs no ceremony. Both are needed: a bare
    ``pytest`` does not source the library, and a script cannot vouch for an
    invocation that bypasses it.
    """
    lib = _repo_root() / "backend" / "scripts" / "lib" / "pytest_run_args.sh"
    text = lib.read_text()

    assert 'export PYTHONPATH="${_tckdb_schemas_parent}' in text
    # Prepended, not appended: an editable install already on the path would
    # otherwise keep winning and the export would change nothing.
    assert '${_tckdb_schemas_parent}${PYTHONPATH:+:$PYTHONPATH}' in text
    # Guarded on the package actually being there, so the export cannot
    # manufacture a path that does not exist -- the original failure.
    assert '-d "${_tckdb_schemas_parent}/tckdb_schemas"' in text
