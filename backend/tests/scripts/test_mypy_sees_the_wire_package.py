"""The ``mypy`` gate must actually type-check the wire-contract package.

``tckdb-schemas`` is a first-party package that lives in this repository
and is installed *editable*. ``mypy`` does not read an editable install's
import hook, so for as long as nothing pointed it at the source tree it
could not find the package by name: all 38 ``import tckdb_schemas...``
statements in ``app/schemas`` reported ``import-not-found``, and
``ignore_missing_imports = true`` absorbed every one of them. The gate
printed ``Success: no issues found in 149 source files`` while every type
error inside the wire package -- and every type error in backend code
arising from how it uses the wire package -- was invisible to it.
Measured when it was fixed: 12 real findings in code that had merged
green, one of them a live defect (a required ``str`` field left holding
``None``).

Three settings make the gate bite, and this file pins all three because
each covers a different half and none substitutes for another:

* ``mypy_path`` makes the package *resolvable*, so backend code is
  checked against its real types instead of ``Any``.
* the ``files`` entry makes it a *check target*. ``follow_imports =
  "silent"`` analyses a merely-imported module but suppresses its own
  errors, so ``mypy_path`` alone would still say nothing about a broken
  annotation inside the package.
* ``ignore_missing_imports = false`` is what stops the whole thing
  recurring. That setting cannot tell "third-party package with no
  stubs" from "first-party package we failed to point mypy at", so with
  it on the second failure is silent by construction.

A configuration that silently ignores the package looks *identical* to
one that works -- same exit code, same reassuring summary line. So the
last test here does not read configuration at all: it runs the gate's
own ``mypy`` over a module that misuses a real ``tckdb_schemas`` symbol
and asserts the misuse is reported. That is the only assertion in this
file that could not pass vacuously.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parent
PYPROJECT = BACKEND_DIR / "pyproject.toml"


def _config_only_env() -> dict[str, str]:
    """The subprocess environment with every *ambient* import path removed.

    Measured, and it cost a false pass: mypy's module search path includes
    the target interpreter's ``sys.path``, so a ``PYTHONPATH`` naming the
    wire package makes ``tckdb_schemas`` resolve **regardless** of the
    configuration -- which is exactly how a developer running the suite
    with the package on ``PYTHONPATH`` (the documented way to test a
    worktree copy) would watch this file pass against a configuration
    that fixes nothing. ``MYPYPATH`` does the same thing more directly.

    Stripping both makes ``backend/pyproject.toml`` the only thing that
    can make the package resolvable, which is the property under test.
    """
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("MYPYPATH", None)
    return env

#: Where the wire package's import root and its top-level package sit,
#: as absolute paths. Derived from the repository layout rather than from
#: the configuration, so a configuration that names the wrong place fails
#: instead of agreeing with itself.
WIRE_IMPORT_ROOT = REPO_ROOT / "schemas" / "python" / "tckdb-schemas"
WIRE_PACKAGE_DIR = WIRE_IMPORT_ROOT / "tckdb_schemas"

#: Floor on the module count, so "the target expands to nothing" cannot
#: read as "the target is covered". 34 modules at the time of writing.
MIN_WIRE_MODULES = 25


def _mypy_config() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"]["mypy"]


def _mypy_overrides() -> list[dict]:
    config = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"]["mypy"]
    return list(config.get("overrides", []))


def _wire_modules() -> list[Path]:
    return sorted(WIRE_PACKAGE_DIR.rglob("*.py"))


def test_the_wire_package_is_actually_on_disk_where_configured() -> None:
    """Guard the guard: every other test here is vacuous if this is wrong."""
    assert WIRE_PACKAGE_DIR.is_dir(), WIRE_PACKAGE_DIR
    assert (WIRE_PACKAGE_DIR / "__init__.py").is_file()
    modules = _wire_modules()
    assert len(modules) >= MIN_WIRE_MODULES, (
        f"only {len(modules)} module(s) under {WIRE_PACKAGE_DIR}; either the "
        f"package moved or MIN_WIRE_MODULES is now wrong"
    )


def test_every_configured_check_target_exists() -> None:
    """A moved package must fail the gate, not silently empty it.

    ``files`` entries are resolved against the working directory, which
    for both CI (``working-directory: backend``) and the documented local
    command is ``backend/``. An entry that no longer names anything is
    the failure mode that turns a gate into a no-op.
    """
    targets = _mypy_config()["files"]
    assert targets, "[tool.mypy] declares no files to check"
    for target in targets:
        resolved = (BACKEND_DIR / target).resolve()
        assert resolved.exists(), f"mypy check target does not exist: {target}"


def test_the_wire_package_is_a_check_target_not_merely_an_import() -> None:
    """``follow_imports = "silent"`` is why being importable is not enough."""
    config = _mypy_config()
    targets = [(BACKEND_DIR / entry).resolve() for entry in config["files"]]
    assert WIRE_PACKAGE_DIR.resolve() in targets, (
        f"{WIRE_PACKAGE_DIR} is not in [tool.mypy] files={config['files']}; "
        f"with follow_imports='silent' its own errors would be suppressed"
    )
    # The setting that makes the distinction matter. If someone turns
    # follow_imports up to "normal" this assertion is the thing that
    # should be revisited -- not deleted.
    assert config.get("follow_imports") == "silent", config.get("follow_imports")


def test_the_wire_package_import_root_is_on_mypy_path() -> None:
    """Resolvable by *name*, so backend code sees real types, not ``Any``."""
    raw = _mypy_config().get("mypy_path")
    assert raw, "[tool.mypy] sets no mypy_path; tckdb_schemas cannot resolve"
    entries = [raw] if isinstance(raw, str) else list(raw)
    resolved = [(BACKEND_DIR / entry).resolve() for entry in entries]
    assert WIRE_IMPORT_ROOT.resolve() in resolved, resolved
    # An import root is the directory *containing* the package, not the
    # package. Pointing one level too deep resolves nothing and looks fine.
    assert (WIRE_IMPORT_ROOT / "tckdb_schemas" / "__init__.py").is_file()


def test_missing_imports_are_errors_not_shrugs() -> None:
    """The recurrence guard.

    With ``ignore_missing_imports = true`` a first-party package that
    stops resolving is indistinguishable from a third-party package with
    no stubs, and the gate reports success. Off, it is an error.
    """
    assert _mypy_config().get("ignore_missing_imports") is not True, (
        "ignore_missing_imports must stay off: it is what made the "
        "unresolvable wire package a silent success. Add a narrow "
        "[[tool.mypy.overrides]] for a specific stubless third-party "
        "module instead, the way the rdkit block does."
    )


def test_no_override_re_enables_the_shrug_for_first_party_code() -> None:
    """A per-module escape hatch must not be aimed at our own package."""
    for override in _mypy_overrides():
        modules = override.get("module")
        modules = [modules] if isinstance(modules, str) else list(modules or [])
        for module in modules:
            first_party = module.split(".")[0] in {"tckdb_schemas", "app"}
            if first_party:
                assert override.get("ignore_missing_imports") is not True, (
                    f"override for {module!r} re-enables "
                    f"ignore_missing_imports on first-party code"
                )
                assert override.get("follow_imports") != "skip", (
                    f"override for {module!r} skips first-party code, which "
                    f"makes it Any everywhere it is used"
                )


def test_every_wire_module_falls_under_a_check_target() -> None:
    """A new subpackage joins the gate the day it is added, not later.

    Asserted against the modules on disk rather than a written-down list,
    for the same reason ``test_gate_coverage.py`` enumerates real test
    files: a list is a thing somebody forgets to extend.
    """
    targets = [(BACKEND_DIR / entry).resolve() for entry in _mypy_config()["files"]]
    uncovered = [
        module
        for module in _wire_modules()
        if not any(module.resolve().is_relative_to(target) for target in targets)
    ]
    assert uncovered == [], uncovered


def test_mypy_reports_a_misuse_of_a_wire_package_symbol(tmp_path: Path) -> None:
    """End-to-end: run the gate's own mypy and watch it catch a real misuse.

    Everything above reads configuration, and configuration can agree
    with itself while checking nothing. This runs
    ``mypy --config-file backend/pyproject.toml`` -- the settings CI
    uses, from the directory CI uses -- over a module that calls
    ``GeometryIn.to_payload`` with an argument it does not take and
    assigns its result to an ``int``. Both are errors only if the wire
    package resolved; if it did not, ``GeometryIn`` is ``Any`` and mypy
    finds nothing.
    """
    mypy = shutil.which("mypy")
    if mypy is None:  # pragma: no cover - depends on the local environment
        pytest.skip("mypy is not installed in this environment")

    probe = tmp_path / "wire_misuse_probe.py"
    probe.write_text(
        "from tckdb_schemas.shared.calculation_in import GeometryIn\n"
        "\n"
        "\n"
        "def probe() -> int:\n"
        '    return GeometryIn(key="k", xyz_text="x").to_payload(99)\n',
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            mypy,
            "--config-file",
            str(PYPROJECT),
            "--no-incremental",
            str(probe),
        ],
        cwd=BACKEND_DIR,
        env=_config_only_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    combined = result.stdout + result.stderr

    assert result.returncode != 0, (
        f"mypy accepted a misuse of a tckdb_schemas symbol, which means the "
        f"package resolved to Any:\n{combined}"
    )
    # Name the codes, so "it failed" cannot be satisfied by an unrelated
    # failure such as a bad config path or a missing interpreter.
    assert "call-arg" in combined, combined
    assert "GeometryIn" in combined, combined
    # And prove the reason is not that the import was unresolvable.
    assert "import-not-found" not in combined, combined


def test_the_probe_would_pass_if_the_package_were_unresolvable(
    tmp_path: Path,
) -> None:
    """The control for the test above -- otherwise it proves nothing.

    Same probe, same mypy, but ``ignore_missing_imports`` on and no
    ``mypy_path``: the pre-fix configuration. It must report the import
    as missing rather than the misuse, which is exactly how a blatant
    error merged green.
    """
    mypy = shutil.which("mypy")
    if mypy is None:  # pragma: no cover - depends on the local environment
        pytest.skip("mypy is not installed in this environment")

    probe = tmp_path / "wire_misuse_probe.py"
    probe.write_text(
        "from tckdb_schemas.shared.calculation_in import GeometryIn\n"
        "\n"
        "\n"
        "def probe() -> int:\n"
        '    return GeometryIn(key="k", xyz_text="x").to_payload(99)\n',
        encoding="utf-8",
    )
    # An empty config file: no mypy_path, and mypy's own default for
    # ignore_missing_imports, which the flag below then forces on.
    empty_config = tmp_path / "no_mypy_path.toml"
    empty_config.write_text("[tool.mypy]\npython_version = \"3.13\"\n", encoding="utf-8")

    result = subprocess.run(
        [
            mypy,
            "--config-file",
            str(empty_config),
            "--ignore-missing-imports",
            "--no-site-packages",
            "--no-incremental",
            str(probe),
        ],
        cwd=BACKEND_DIR,
        env=_config_only_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    combined = result.stdout + result.stderr
    assert result.returncode == 0, (
        "the control is supposed to pass -- if it fails, this test is no "
        f"longer measuring what it claims:\n{combined}"
    )
    assert "call-arg" not in combined, combined


def test_python_version_pin_matches_the_interpreter_family() -> None:
    """A stale ``python_version`` silently changes what the gate checks."""
    pinned = _mypy_config()["python_version"]
    major, minor = (int(part) for part in pinned.split("."))
    assert (major, minor) >= (3, 11), pinned
    assert major == sys.version_info.major, (pinned, sys.version)
