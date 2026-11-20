"""The PyPI publish workflow covers exactly the packages the version guard covers.

``.github/workflows/publish-python.yml`` maps a release tag to a package
directory through ``backend/scripts/python_release.py``, which reads
``check_package_version_bump.PACKAGES``. These tests pin that the workflow's
manual-run choices name the same packages, that every package's tag matches
the workflow's tag filter and resolves to its own directory and version, and
that the publish jobs keep the shape trusted publishing needs: OIDC
permission, the named environments, TestPyPI before PyPI, PyPI only for a
pushed tag, and no stored secret.

Run by the repo gate with ``--noconftest`` (stdlib and PyYAML only), so a
change to the workflow alone is checked.
"""

from __future__ import annotations

import fnmatch
import importlib.util
import sys
import tomllib
from pathlib import Path

import pytest
import yaml

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_ROOT.parent
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "publish-python.yml"
CLIENT_CI_PATH = REPO_ROOT / ".github" / "workflows" / "python-client-ci.yml"
PUBLISH_ACTION = "pypa/gh-action-pypi-publish@release/v1"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


release = _load_module("python_release_under_test", BACKEND_ROOT / "scripts" / "python_release.py")
guard = _load_module("version_guard_for_publish_test", BACKEND_ROOT / "scripts" / "check_package_version_bump.py")

NAMES = [package.name for package in guard.PACKAGES]


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def _triggers(workflow: dict) -> dict:
    # PyYAML reads the bare key ``on`` as the boolean True.
    return workflow.get("on", workflow.get(True))


def _version(package) -> str:
    return tomllib.loads((REPO_ROOT / package.pyproject).read_text())["project"]["version"]


def test_the_resolver_reads_the_version_guards_packages():
    assert release.package_names() == NAMES
    assert len(NAMES) == 5, NAMES


def test_manual_run_choices_are_the_guarded_packages():
    options = _triggers(_workflow())["workflow_dispatch"]["inputs"]["package"]["options"]
    assert options == NAMES, (
        "publish-python.yml's package choices must equal check_package_version_bump.PACKAGES; "
        f"workflow {options}, guard {NAMES}"
    )


@pytest.mark.parametrize("package", guard.PACKAGES, ids=lambda p: p.name)
def test_each_package_tag_matches_the_filter_and_resolves_to_its_directory(package):
    version = _version(package)
    tag = f"{package.name}-v{version}"
    patterns = _triggers(_workflow())["push"]["tags"]
    assert any(fnmatch.fnmatchcase(tag, pattern) for pattern in patterns), (tag, patterns)

    resolved = release.resolve(tag=tag)
    assert resolved["name"] == package.name
    assert resolved["version"] == version
    assert resolved["directory"] == str(Path(package.pyproject).parent)
    assert (REPO_ROOT / resolved["directory"] / "pyproject.toml").is_file()
    assert resolved["module"] == package.name.replace("-", "_")


@pytest.mark.parametrize("package", guard.PACKAGES, ids=lambda p: p.name)
def test_a_tag_naming_another_version_is_refused(package):
    with pytest.raises(release.ReleaseError, match="tag the commit"):
        release.resolve(tag=f"{package.name}-v{_version(package)}.999")


@pytest.mark.parametrize("tag", ["v0.2.0", "tckdb-backend-v0.2.0", "tckdb-client-0.1.0", "tckdb-client-vx"])
def test_a_tag_outside_the_scheme_or_the_packages_is_refused(tag):
    with pytest.raises(release.ReleaseError):
        release.resolve(tag=tag)


def test_first_party_dependencies_come_before_their_dependents():
    schemas, client = "schemas/python/tckdb-schemas", "clients/python"
    assert release.resolve(package="tckdb-schemas")["deps"] == ""
    assert release.resolve(package="tckdb-mcp")["deps"] == ""
    assert release.resolve(package="tckdb-client")["deps"] == schemas
    for name in ("tckdb-chemkin", "tckdb-qcschema"):
        assert release.resolve(package=name)["deps"] == f"{schemas} {client}", name


def test_the_build_job_resolves_builds_checks_and_smoke_tests():
    steps = _workflow()["jobs"]["build"]["steps"]
    script = "\n".join(str(step.get("run", "")) for step in steps)
    assert "python_release.py resolve --tag" in script
    assert "python_release.py resolve --package" in script
    assert "python -m build --outdir dist" in script
    assert "twine check --strict dist/*" in script
    assert "python_release.py smoke" in script
    assert "merge-base --is-ancestor" in script


def test_publish_jobs_use_trusted_publishing_in_the_named_environments():
    workflow = _workflow()
    assert workflow["permissions"] == {"contents": "read"}
    jobs = workflow["jobs"]

    testpypi = jobs["publish-testpypi"]
    assert testpypi["needs"] == "build"
    assert testpypi["environment"]["name"] == "testpypi"
    assert testpypi["permissions"] == {"id-token": "write"}
    (publish,) = [step for step in testpypi["steps"] if step.get("uses") == PUBLISH_ACTION]
    assert publish["with"]["repository-url"] == "https://test.pypi.org/legacy/"

    pypi = jobs["publish-pypi"]
    assert set(pypi["needs"]) == {"build", "publish-testpypi"}
    assert pypi["environment"]["name"] == "pypi"
    assert pypi["permissions"] == {"id-token": "write"}
    assert "github.event_name == 'push'" in pypi["if"]
    assert "startsWith(github.ref, 'refs/tags/')" in pypi["if"]
    (publish,) = [step for step in pypi["steps"] if step.get("uses") == PUBLISH_ACTION]
    assert "repository-url" not in (publish.get("with") or {})


def test_the_workflow_stores_no_secret():
    assert "secrets." not in WORKFLOW_PATH.read_text(encoding="utf-8")


def test_client_ci_builds_and_checks_every_package():
    """Metadata rot fails a pull request, not the release."""
    workflow = yaml.safe_load(CLIENT_CI_PATH.read_text(encoding="utf-8"))
    job = workflow["jobs"]["package-metadata"]
    script = "\n".join(str(step.get("run", "")) for step in job["steps"])
    assert "twine check --strict" in script
    for package in guard.PACKAGES:
        directory = str(Path(package.pyproject).parent)
        assert directory in script, f"{package.name} ({directory}) is not built by python-client-ci.yml"
    paths = _triggers(workflow)["pull_request"]["paths"]
    for package in guard.PACKAGES:
        directory = str(Path(package.pyproject).parent)
        assert any(fnmatch.fnmatchcase(f"{directory}/pyproject.toml", p) for p in paths), (
            f"a change to {directory} does not start python-client-ci.yml"
        )
