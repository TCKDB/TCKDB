"""The audited rule manifests are read at runtime, so a wheel must carry them.

CI and the Docker image install the backend in editable mode, where every file under ``app/`` is simply there; a wheel
install copies only the Python modules plus whatever ``[tool.setuptools.package-data]`` names. A rule manifest missing
from that list works everywhere the tests run and fails at the first ``default_rules()`` call in a real install. This
asks setuptools itself (the same ``build_py`` data-file discovery a wheel build runs) which data files each package
would ship, for every YAML found under ``app/chemistry``, so a fifth rule family cannot repeat the omission.
"""

from __future__ import annotations

import importlib.resources
from pathlib import Path

import pytest
from setuptools.config.pyprojecttoml import apply_configuration
from setuptools.dist import Distribution

BACKEND = Path(__file__).resolve().parents[1]
CHEMISTRY = BACKEND / "app" / "chemistry"

RULE_MANIFESTS = {
    "app.chemistry.thermo_rules": "e1_g4_over_g3_manifest.yaml",
    "app.chemistry.kinetics_rules": "xyg3_b3lyp_barrier_manifest.yaml",
    "app.chemistry.network_rules": "network_rule_candidates.yaml",
    "app.chemistry.structure_rules": "structure_rule_candidates.yaml",
}


def _shipped_for(pyproject: Path, *, drop: str | None = None) -> dict[str, set[str]]:
    """``{package: file names}`` setuptools' own package-data matching would copy into a wheel.

    ``find_data_files`` is the matcher ``build_py`` runs for every package; only the sdist manifest (which a wheel build
    adds from its own egg-info step and which does not need to know about these files) is left out.
    """
    dist = Distribution({"name": "tckdb-backend-packaging-probe"})
    apply_configuration(dist, str(pyproject))
    build_py = dist.get_command_obj("build_py")
    build_py.ensure_finalized()
    build_py.manifest_files = {}
    if drop is not None:
        build_py.package_data = {k: v for k, v in build_py.package_data.items() if k != drop}
    shipped: dict[str, set[str]] = {}
    for package in build_py.packages:
        files = build_py.find_data_files(package, build_py.get_package_dir(package))
        shipped[package] = {Path(f).name for f in files}
    return shipped


def _shipped_data(monkeypatch) -> dict[str, set[str]]:
    monkeypatch.chdir(BACKEND)
    return _shipped_for(BACKEND / "pyproject.toml")


def test_every_yaml_under_app_chemistry_is_shipped_as_package_data(monkeypatch):
    yamls = sorted(CHEMISTRY.glob("*/*.yaml"))
    assert {f"app.chemistry.{p.parent.name}" for p in yamls} >= set(RULE_MANIFESTS), "the walk found no rule manifests"
    shipped = _shipped_data(monkeypatch)
    missing = [
        f"app.chemistry.{p.parent.name}: {p.name}" for p in yamls if p.name not in shipped.get(f"app.chemistry.{p.parent.name}", set())
    ]
    assert not missing, (
        f"these YAML files are read at runtime but a wheel would not carry them: {missing}. Add the package to "
        "[tool.setuptools.package-data] in backend/pyproject.toml."
    )


@pytest.mark.parametrize(("package", "name"), sorted(RULE_MANIFESTS.items()))
def test_each_rule_manifest_is_a_resource_of_its_package_and_in_the_wheel_list(package, name, monkeypatch):
    assert importlib.resources.files(package).joinpath(name).is_file()
    assert name in _shipped_data(monkeypatch).get(package, set())


def test_the_probe_sees_a_missing_package_data_entry(monkeypatch):
    """The probe is not vacuous: without the structure entry, a wheel would ship no structure manifest."""
    text = (BACKEND / "pyproject.toml").read_text()
    assert '"app.chemistry.structure_rules" = ["*.yaml"]' in text
    monkeypatch.chdir(BACKEND)
    shipped = _shipped_for(BACKEND / "pyproject.toml", drop="app.chemistry.structure_rules")
    assert "structure_rule_candidates.yaml" not in shipped.get("app.chemistry.structure_rules", set())
    assert "network_rule_candidates.yaml" in shipped.get("app.chemistry.network_rules", set())
