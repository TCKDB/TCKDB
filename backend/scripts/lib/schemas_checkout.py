"""Is the imported ``tckdb_schemas`` the one in this checkout?

Shared by ``backend/tests/conftest.py`` (which refuses a test run) and
``backend/scripts/generate_producer_contract.py`` (which refuses to render a
contract). Both read ``tckdb_schemas`` and both are wrong in the same silent way
when it resolves elsewhere: the editable install in a ``git worktree`` points at
the checkout it was installed from, so the worktree's own wire-model edits are
invisible. For the tests that is a green that tested another branch; for the
generator it is a contract rendered from another branch's models and stamped
with this branch's version. The reasoning is recorded at length on
``_assert_schemas_package_is_this_checkout`` in ``tests/conftest.py``.

Loaded by path (``backend/scripts`` is not a package), and it imports nothing
from ``app`` so either caller can load it first.
"""

from __future__ import annotations

from pathlib import Path


def foreign_schemas_message(repo_root: Path) -> str | None:
    """A refusal message if ``tckdb_schemas`` resolves outside ``repo_root``, else ``None``."""
    try:
        import tckdb_schemas
    except ImportError:  # pragma: no cover - environment without the package
        return None

    resolved = Path(tckdb_schemas.__file__).resolve()
    if repo_root in resolved.parents:
        return None

    expected = repo_root / "schemas" / "python" / "tckdb-schemas"
    return (
        "tckdb_schemas resolves outside this checkout, so this run would "
        "use another checkout's wire schemas and any change made here "
        "would be invisible.\n"
        f"  this checkout: {repo_root}\n"
        f"  resolved to:   {resolved}\n"
        "This is what an editable install does inside a git worktree: it "
        "points at the checkout it was installed from.\n"
        "Fix it for this run by putting the package's parent directory "
        "first on PYTHONPATH:\n"
        f'  export PYTHONPATH="{expected}:$PYTHONPATH"\n'
        "Note the path ends at 'tckdb-schemas' (the directory *containing* "
        "the tckdb_schemas package). A path that does not exist is skipped "
        "silently and leaves you exactly here.\n"
        "The test-*.sh gate scripts do this for you; a bare pytest "
        "invocation does not."
    )
