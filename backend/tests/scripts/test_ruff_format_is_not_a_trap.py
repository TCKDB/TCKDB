"""``ruff format`` must never be a loaded gun pointing at this tree.

The tool was installed, given a ``line-length``, and gated by nothing. That is
the worst of the three possible states: configured looks like adopted, so
somebody eventually runs it -- and 669 of 870 files under ``app tests
scripts`` disagree with it. An agent ran it over five files it was already
editing and turned an ~800-line reviewable diff into ~4,000 lines it then had
to rebuild from ``main`` by hand.

``[tool.ruff.format] exclude = ["**"]`` in ``backend/pyproject.toml`` makes
the formatter a no-op. This test is what keeps that true, and it is
deliberately written to accept *either* end state:

* the formatter is disabled, so ``ruff format --check`` has nothing to say; or
* somebody has done the reformat properly, so ``ruff format --check`` is clean
  because the tree is actually formatted.

It fails only on the half-done middle -- the exclusion removed without the
reformat, which is the state that was in force before this file existed.
"""

from __future__ import annotations

import shutil
import subprocess
import tomllib
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = BACKEND_ROOT / "pyproject.toml"


def test_ruff_format_changes_nothing_in_this_tree() -> None:
    """Running the formatter must be safe, whatever the reason it is safe."""
    ruff = shutil.which("ruff")
    assert ruff is not None, (
        "ruff is not on PATH. It is declared in the [dev] extra alongside "
        "pytest, so an environment that can run this test can run it."
    )

    result = subprocess.run(
        [ruff, "format", "--check", "app", "tests", "scripts"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, (
        "`ruff format --check` reports work to do, which means the formatter "
        "is live against a tree that has not been formatted. Anyone who runs "
        "`ruff format` on the files they are editing will silently rewrite "
        "them and lose their diff in the noise.\n\n"
        "Either restore `[tool.ruff.format] exclude = [\"**\"]` in "
        "backend/pyproject.toml, or land the whole-tree reformat as its own "
        "commit and gate `ruff format --check` in backend-ci.yml. Both are "
        "defensible; the state this test refuses is having neither.\n\n"
        f"{result.stdout}\n{result.stderr}"
    )


def test_the_decision_is_recorded_where_someone_would_look() -> None:
    """A no-op with no explanation is a config line somebody deletes.

    The reasoning lives next to the setting rather than in a document, because
    the person about to run ``ruff format`` is looking at ``pyproject.toml``.
    """
    config = tomllib.loads(PYPROJECT.read_text())
    ruff = config["tool"]["ruff"]

    assert "format" in ruff, (
        "backend/pyproject.toml has no [tool.ruff.format] section. If the "
        "formatter has been adopted, delete this test with the reformat; if "
        "not, the exclusion belongs here."
    )
    assert ruff["format"].get("exclude") == ["**"]

    text = PYPROJECT.read_text()
    section = text.split("[tool.ruff.format]", 1)[0].rsplit(
        "# ---------------------------------------------------------------------------",
        3,
    )[-1]
    assert "ruff format" in section and len(section) > 800, (
        "the [tool.ruff.format] exclusion has lost the explanation that "
        "precedes it. Without it the next reader sees a config line that "
        "appears to do nothing and removes it."
    )
