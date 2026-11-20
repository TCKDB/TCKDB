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
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import yaml

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_ROOT.parent
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "publish-python.yml"
CLIENT_CI_PATH = REPO_ROOT / ".github" / "workflows" / "python-client-ci.yml"
PUBLISH_ACTION = re.compile(r"^pypa/gh-action-pypi-publish@[0-9a-f]{40}$")
DOWNLOAD_ACTION = re.compile(r"^actions/download-artifact@")


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


def _publish_step(job: dict) -> dict:
    """The job's only steps: one artifact download, then one pinned publish."""
    steps = job["steps"]
    assert [step.get("run") for step in steps if "run" in step] == [], "a publish job runs no shell"
    downloads = [step for step in steps if DOWNLOAD_ACTION.match(step.get("uses", ""))]
    publishes = [step for step in steps if PUBLISH_ACTION.match(step.get("uses", ""))]
    assert len(downloads) == 1, downloads
    assert len(publishes) == 1, f"pypa/gh-action-pypi-publish must appear once, pinned by commit sha: {steps}"
    assert len(steps) == 2, f"a publish job only downloads and publishes the build job's dist: {steps}"
    return publishes[0]


def test_publish_jobs_use_trusted_publishing_in_the_named_environments():
    workflow = _workflow()
    assert workflow["permissions"] == {"contents": "read"}
    jobs = workflow["jobs"]

    testpypi = jobs["publish-testpypi"]
    assert testpypi["needs"] == "build"
    assert testpypi["environment"]["name"] == "testpypi"
    assert testpypi["permissions"] == {"id-token": "write"}
    publish = _publish_step(testpypi)
    assert publish["with"]["repository-url"] == "https://test.pypi.org/legacy/"
    assert publish["with"]["skip-existing"] is True

    pypi = jobs["publish-pypi"]
    assert set(pypi["needs"]) == {"build", "publish-testpypi"}
    assert pypi["environment"]["name"] == "pypi"
    assert pypi["permissions"] == {"id-token": "write"}
    assert "github.event_name == 'push'" in pypi["if"]
    assert "startsWith(github.ref, 'refs/tags/')" in pypi["if"]
    publish = _publish_step(pypi)
    options = publish.get("with") or {}
    assert "repository-url" not in options
    assert "skip-existing" not in options, "PyPI must refuse a version that already exists, not skip it"


def test_no_expression_is_spliced_into_a_shell_script():
    """Values reach ``run:`` through ``env:``, never as ``${{ }}`` text."""
    for name, job in _workflow()["jobs"].items():
        for step in job.get("steps", []):
            assert "${{" not in str(step.get("run", "")), f"{name}: {step.get('name')}"


def test_the_build_checkout_does_not_keep_the_token():
    (checkout,) = [
        step for step in _workflow()["jobs"]["build"]["steps"] if str(step.get("uses", "")).startswith("actions/checkout@")
    ]
    assert checkout["with"]["persist-credentials"] is False


def _shell_command(workflow: dict) -> list[str]:
    """The shell GitHub runs a ``run:`` step with, given the workflow's default."""
    shell = (workflow.get("defaults") or {}).get("run", {}).get("shell")
    if shell == "bash":
        return ["bash", "--noprofile", "--norc", "-eo", "pipefail"]
    assert shell is None, f"unexpected default shell {shell!r}"
    return ["bash", "-e"]


def test_steps_run_under_bash_with_pipefail():
    assert _workflow()["defaults"]["run"]["shell"] == "bash"


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
@pytest.mark.parametrize("event, tag, package", [("push", "tckdb-client-v0.0.0", ""), ("push", "v0.2.0", "")])
def test_the_resolve_step_fails_the_job_on_a_refused_tag(tmp_path, event, tag, package):
    """Run the step's own script the way GitHub would; a refused tag must stop the job."""
    workflow = _workflow()
    (step,) = [s for s in workflow["jobs"]["build"]["steps"] if s.get("id") == "resolve"]
    script = tmp_path / "step.sh"
    script.write_text(step["run"], encoding="utf-8")
    output = tmp_path / "github_output"
    env = {
        **os.environ,
        "EVENT_NAME": event,
        "REF_NAME": tag,
        "PACKAGE": package,
        "GITHUB_OUTPUT": str(output),
    }
    result = subprocess.run(
        [*_shell_command(workflow), str(script)], cwd=REPO_ROOT, env=env, capture_output=True, text=True
    )
    assert result.returncode != 0, f"the resolve step passed a refused tag:\n{result.stdout}{result.stderr}"
    assert "::error::" in result.stderr


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_the_resolve_step_writes_its_outputs_for_a_known_package(tmp_path):
    """The same harness passes a good run, so the refusal above is the tag's doing."""
    workflow = _workflow()
    (step,) = [s for s in workflow["jobs"]["build"]["steps"] if s.get("id") == "resolve"]
    script = tmp_path / "step.sh"
    script.write_text(step["run"], encoding="utf-8")
    output = tmp_path / "github_output"
    env = {**os.environ, "EVENT_NAME": "workflow_dispatch", "REF_NAME": "main", "PACKAGE": "tckdb-client"}
    env["GITHUB_OUTPUT"] = str(output)
    result = subprocess.run(
        [*_shell_command(workflow), str(script)], cwd=REPO_ROOT, env=env, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert "name=tckdb-client" in output.read_text(encoding="utf-8").splitlines()


def test_the_workflow_stores_no_secret():
    assert "secrets." not in WORKFLOW_PATH.read_text(encoding="utf-8")


def test_client_ci_builds_and_checks_every_package():
    """Metadata rot fails a pull request, not the release."""
    workflow = yaml.safe_load(CLIENT_CI_PATH.read_text(encoding="utf-8"))
    job = workflow["jobs"]["package-metadata"]
    script = "\n".join(str(step.get("run", "")) for step in job["steps"])
    (checkout,) = [step for step in job["steps"] if str(step.get("uses", "")).startswith("actions/checkout@")]
    assert checkout["with"]["persist-credentials"] is False
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
