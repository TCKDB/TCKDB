"""The audited rule manifests are read at runtime, so a wheel must carry them.

CI and the Docker image install the backend in editable mode, where every file under ``app/`` is simply there; a wheel
install copies only the Python modules plus whatever ``[tool.setuptools.package-data]`` names. A rule manifest missing
from that list works everywhere the tests run and fails at the first ``default_rules()`` call in a real install. This
reads the package-data table out of ``pyproject.toml`` (``tomllib``, so it needs no build tooling that the test
environment may lack) and matches each YAML found under ``app/chemistry`` against its own package's globs the way
setuptools does (``fnmatch`` on the file name), so a fifth rule family cannot repeat the omission.
"""

from __future__ import annotations

import fnmatch
import importlib.resources
import tomllib
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
CHEMISTRY = BACKEND / "app" / "chemistry"

RULE_MANIFESTS = {
    "app.chemistry.thermo_rules": "e1_g4_over_g3_manifest.yaml",
    "app.chemistry.kinetics_rules": "xyg3_b3lyp_barrier_manifest.yaml",
    "app.chemistry.network_rules": "network_rule_candidates.yaml",
    "app.chemistry.structure_rules": "structure_rule_candidates.yaml",
}


def _package_data() -> dict[str, list[str]]:
    table = tomllib.loads((BACKEND / "pyproject.toml").read_text())["tool"]["setuptools"]["package-data"]
    assert table, "pyproject.toml declares no package-data table"
    return {package: list(globs) for package, globs in table.items()}


def _shipped(package: str, name: str, package_data: dict[str, list[str]]) -> bool:
    """Whether package-data would carry ``name`` for ``package`` (a ``*`` entry applies to every package)."""
    globs = [*package_data.get(package, []), *package_data.get("*", [])]
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in globs)


def test_every_yaml_under_app_chemistry_is_shipped_as_package_data():
    yamls = sorted(CHEMISTRY.glob("*/*.yaml"))
    assert {f"app.chemistry.{p.parent.name}" for p in yamls} >= set(RULE_MANIFESTS), "the walk found no rule manifests"
    package_data = _package_data()
    missing = [
        f"app.chemistry.{p.parent.name}: {p.name}"
        for p in yamls
        if not _shipped(f"app.chemistry.{p.parent.name}", p.name, package_data)
    ]
    assert not missing, (
        f"these YAML files are read at runtime but a wheel would not carry them: {missing}. Add the package to "
        "[tool.setuptools.package-data] in backend/pyproject.toml."
    )


def test_every_yaml_sits_in_a_real_package():
    """package-data only applies to packages, and ``importlib.resources`` needs one to find the file."""
    for path in CHEMISTRY.glob("*/*.yaml"):
        assert (path.parent / "__init__.py").is_file(), f"{path.parent.name} holds a YAML but is not a package"


@pytest.mark.parametrize(("package", "name"), sorted(RULE_MANIFESTS.items()))
def test_each_rule_manifest_is_a_resource_of_its_package_and_in_the_wheel_list(package, name):
    assert importlib.resources.files(package).joinpath(name).is_file()
    assert _shipped(package, name, _package_data())


def test_the_matcher_sees_a_missing_package_data_entry():
    """Not vacuous: without the structure entry, or with a glob that does not match, the manifest is not shipped."""
    package_data = _package_data()
    assert "app.chemistry.structure_rules" in package_data
    name = RULE_MANIFESTS["app.chemistry.structure_rules"]
    dropped = {k: v for k, v in package_data.items() if k != "app.chemistry.structure_rules"}
    assert not _shipped("app.chemistry.structure_rules", name, dropped)
    assert not _shipped("app.chemistry.structure_rules", name, {"app.chemistry.structure_rules": ["*.json"]})
    assert _shipped("app.chemistry.network_rules", RULE_MANIFESTS["app.chemistry.network_rules"], dropped)
