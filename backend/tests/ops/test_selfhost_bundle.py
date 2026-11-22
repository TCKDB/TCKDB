"""The self-host install bundle built by ``make_selfhost_bundle.sh``.

Pinned here:

* the tarball holds exactly the allow-listed files under one top directory,
  with no real env file, no VCS data and no application source;
* the bundle works on its own: ``tckdb_make_env.sh`` runs inside an extracted
  copy, and ``docker compose config`` accepts every profile combination the
  install guide documents, with every file the compose file names present;
* the env files name the compose project, so a new bundle directory reuses
  the volumes of the old one;
* two builds of the same tree give the same bytes, and the ``.sha256`` file
  checks;
* a version that disagrees with the compose file's default image tag or with
  ``backend/pyproject.toml`` is refused.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
BUILD = REPO_ROOT / "backend/scripts/ops/make_selfhost_bundle.sh"


def _release_version() -> str:
    match = re.search(r'^version = "([^"]+)"$', (REPO_ROOT / "backend/pyproject.toml").read_text(), re.M)
    assert match, "backend/pyproject.toml has no version line"
    return match.group(1)


VERSION = _release_version()
TOP = f"tckdb-selfhost-{VERSION}"

#: Every file a self-host needs, at the path the scripts and the compose file
#: expect. A file joins the bundle by being added here and to the script.
ALLOWED = {
    "README.md",
    "VERSION",
    "LICENSE",
    "docker-compose.yml",
    ".env.selfhosted.example",
    ".env.db-admin.example",
    "deploy/caddy/Caddyfile",
    "backend/scripts/check_selfhosted_deployment.sh",
    "backend/scripts/tckdb_auth.sh",
    "backend/scripts/lib/auth_key.sh",
    "backend/scripts/ops/tckdb_make_env.sh",
    "backend/scripts/ops/tckdb_deploy.sh",
    "backend/scripts/ops/tckdb_backup.sh",
    "backend/scripts/ops/tckdb_alert_check.sh",
    "backend/scripts/ops/tckdb-alert.service",
    "backend/scripts/ops/tckdb-alert.timer",
    "backend/scripts/ops/tckdb-alert-failed.service",
    "backend/scripts/ops/tckdb_storage_hostfw.sh",
    "backend/scripts/ops/tckdb-storage-hostfw.service",
    "examples/deployment/systemd/tckdb-backup.service",
    "examples/deployment/systemd/tckdb-backup.timer",
}

#: Profile sets the install guide documents, per ``--access`` answer.
ACCESS_PROFILES = {
    "local": ([], {"selfhost"}),
    "lan": (["--lan-address", "192.168.1.20"], {"selfhost"}),
    "https": (["--domain", "tckdb.example.org", "--acme-email", "admin@example.org"], {"selfhost", "caddy"}),
    "cloudflare": (["--domain", "tckdb.example.org"], {"selfhost", "cloudflare"}),
}


def _build(out_dir: Path, version: str = VERSION, *, script: Path = BUILD, env: dict | None = None):
    return subprocess.run(
        ["bash", str(script), "--out", str(out_dir), version],
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, **(env or {})},
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def bundle(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("dist")
    result = _build(out)
    assert result.returncode == 0, result.stderr
    tarball = out / f"{TOP}.tar.gz"
    assert tarball.is_file(), sorted(p.name for p in out.iterdir())
    return tarball


@pytest.fixture
def extracted(bundle: Path, tmp_path: Path) -> Path:
    dest = tmp_path / "install"
    dest.mkdir()
    with tarfile.open(bundle) as tar:
        tar.extractall(dest, filter="data")
    assert [p.name for p in dest.iterdir()] == [TOP]
    return dest / TOP


# ---------------------------------------------------------------------------
# Contents
# ---------------------------------------------------------------------------


def test_bundle_holds_exactly_the_allow_listed_files(bundle: Path) -> None:
    with tarfile.open(bundle) as tar:
        members = tar.getmembers()
    files = set()
    for member in members:
        assert member.name == TOP or member.name.startswith(f"{TOP}/"), member.name
        assert member.isfile() or member.isdir(), f"{member.name} is neither a file nor a directory"
        if member.isfile():
            files.add(member.name.removeprefix(f"{TOP}/"))
    assert files == ALLOWED


def test_bundle_carries_no_secret_or_source(bundle: Path) -> None:
    with tarfile.open(bundle) as tar:
        names = tar.getnames()
    for name in names:
        base = name.rsplit("/", 1)[-1]
        assert not (base.startswith(".env") and not base.endswith(".example")), name
        assert ".git" not in name.split("/"), name
        assert not name.endswith((".py", ".ts", ".tsx")), name
        assert "/app/" not in name and "/frontend/" not in name, name


def test_entries_are_normalised(bundle: Path) -> None:
    with tarfile.open(bundle) as tar:
        members = tar.getmembers()
    assert [m.name for m in members] == sorted(m.name for m in members)
    assert {(m.uid, m.gid) for m in members} == {(0, 0)}
    assert len({m.mtime for m in members}) == 1
    for member in members:
        if member.isdir() or member.name.endswith(".sh"):
            assert member.mode == 0o755, (member.name, oct(member.mode))
        else:
            assert member.mode == 0o644, (member.name, oct(member.mode))


def test_version_file_and_readme(extracted: Path) -> None:
    assert (extracted / "VERSION").read_text() == f"{VERSION}\n"
    readme = (extracted / "README.md").read_text()
    assert VERSION in readme
    assert "backend/scripts/ops/tckdb_make_env.sh" in readme
    assert "--env-file .env.selfhosted --env-file .env.db-admin" in readme
    assert ".env.selfhosted" in readme and ".env.db-admin" in readme
    assert "COMPOSE_PROJECT_NAME" in readme and "/opt/tckdb" in readme
    curls = [line for line in readme.splitlines() if "curl " in line]
    assert curls and all("curl -f" in line for line in curls), curls


def test_sha256_file_checks(bundle: Path) -> None:
    sums = bundle.with_name(bundle.name + ".sha256")
    assert sums.read_text() == f"{_sha256(bundle)}  {bundle.name}\n"
    if shutil.which("sha256sum"):
        check = subprocess.run(
            ["sha256sum", "-c", sums.name], cwd=bundle.parent, capture_output=True, text=True
        )
        assert check.returncode == 0, check.stdout + check.stderr


def test_two_builds_give_the_same_bytes(bundle: Path, tmp_path: Path) -> None:
    again = _build(tmp_path)
    assert again.returncode == 0, again.stderr
    assert _sha256(tmp_path / bundle.name) == _sha256(bundle)


# ---------------------------------------------------------------------------
# The extracted bundle works on its own
# ---------------------------------------------------------------------------


def _make_env(root: Path, *args: str) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["bash", str(root / "backend/scripts/ops/tckdb_make_env.sh"), "--yes", *args],
        capture_output=True,
        text=True,
        timeout=60,
        stdin=subprocess.DEVNULL,
        env={"PATH": os.environ["PATH"], "HOME": str(root)},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result


def _env_value(path: Path, key: str) -> str | None:
    found = None
    for line in path.read_text().splitlines():
        if line.startswith(f"{key}="):
            found = line.split("=", 1)[1]
    return found


def _referenced_files(root: Path, compose: dict, profiles: set[str]) -> list[Path]:
    """Every env file and bind-mount source the active services name."""
    paths = []
    for svc in compose["services"].values():
        if svc.get("profiles") and not set(svc["profiles"]) & profiles:
            continue
        for entry in svc.get("env_file", []):
            paths.append(root / (entry if isinstance(entry, str) else entry["path"]))
        for volume in svc.get("volumes", []):
            source = volume.split(":", 1)[0] if isinstance(volume, str) else volume.get("source", "")
            if source.startswith(("./", "../", "/")):
                paths.append(root / source)
    return paths


def _docker_compose_available() -> bool:
    if shutil.which("docker") is None:
        return False
    probe = subprocess.run(["docker", "compose", "version"], capture_output=True, text=True)
    return probe.returncode == 0


@pytest.mark.parametrize("access", sorted(ACCESS_PROFILES))
def test_extracted_bundle_configures_every_documented_profile(extracted: Path, access: str) -> None:
    flags, profiles = ACCESS_PROFILES[access]
    _make_env(extracted, "--access", access, *flags)

    runtime = extracted / ".env.selfhosted"
    assert set(_env_value(runtime, "COMPOSE_PROFILES").split(",")) == profiles
    assert _env_value(runtime, "COMPOSE_PROJECT_NAME") == "tckdb"

    compose = yaml.safe_load((extracted / "docker-compose.yml").read_text())
    referenced = _referenced_files(extracted, compose, profiles | {"worker"})
    assert extracted / ".env.db-admin" in referenced
    assert (extracted / "./deploy/caddy/Caddyfile" in referenced) == ("caddy" in profiles)
    for path in referenced:
        assert path.exists(), f"compose names {path.relative_to(extracted)}, which the bundle lacks"

    if not _docker_compose_available():
        pytest.skip("docker compose is not installed; the YAML check above is all that ran")

    env_files = ["--env-file", ".env.selfhosted", "--env-file", ".env.db-admin"]
    # As documented: explicit profiles; COMPOSE_PROFILES from the env file
    # (`make selfhost`, or no --profile); and the optional upload worker.
    runs = [
        [f"--profile={p}" for p in sorted(profiles)],
        [],
        [f"--profile={p}" for p in sorted(profiles | {"worker"})],
    ]
    for profile_args in runs:
        result = subprocess.run(
            ["docker", "compose", *env_files, *profile_args, "config", "--format", "json"],
            cwd=extracted,
            capture_output=True,
            text=True,
            timeout=60,
            env={"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", str(extracted))},
        )
        assert result.returncode == 0, (profile_args, result.stderr)
        model = json.loads(result.stdout)
        assert model["name"] == "tckdb"
        expected = {"db", "seaweedfs", "init", "tckdb-api", "frontend"}
        expected |= {"caddy"} if "caddy" in profiles else set()
        expected |= {"cloudflared"} if "cloudflare" in profiles else set()
        expected |= {"worker"} if any("worker" in a for a in profile_args) else set()
        assert set(model["services"]) == expected, profile_args
        image = model["services"]["tckdb-api"]["image"]
        assert image == f"laxzal/tckdb-api:{VERSION}"


# ---------------------------------------------------------------------------
# Version refusal
# ---------------------------------------------------------------------------


def test_refuses_a_version_the_release_does_not_carry(tmp_path: Path) -> None:
    result = _build(tmp_path, "9.9.9")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "9.9.9" in result.stderr
    assert not list(tmp_path.iterdir())


def test_accepts_a_leading_v(tmp_path: Path) -> None:
    result = _build(tmp_path, f"v{VERSION}")
    assert result.returncode == 0, result.stderr
    assert (tmp_path / f"{TOP}.tar.gz").is_file()


def test_rejects_a_malformed_version(tmp_path: Path) -> None:
    result = _build(tmp_path, "latest")
    assert result.returncode == 2, result.stdout + result.stderr


@pytest.fixture
def repo_copy(tmp_path: Path) -> Path:
    """The bundle's sources, the build script and pyproject, outside git."""
    root = tmp_path / "repo"
    for rel in sorted(ALLOWED - {"README.md", "VERSION"}) + [
        "backend/pyproject.toml",
        "backend/scripts/ops/make_selfhost_bundle.sh",
    ]:
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / rel, target)
    return root


def test_refuses_when_pyproject_disagrees_with_the_compose_tag(repo_copy: Path, tmp_path: Path) -> None:
    pyproject = repo_copy / "backend/pyproject.toml"
    pyproject.write_text(pyproject.read_text().replace(f'version = "{VERSION}"', 'version = "9.9.9"', 1))
    out = tmp_path / "out"
    result = _build(
        out,
        "9.9.9",
        script=repo_copy / "backend/scripts/ops/make_selfhost_bundle.sh",
        env={"SOURCE_DATE_EPOCH": "1700000000"},
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "docker-compose.yml" in result.stderr


def test_refuses_when_compose_images_disagree(repo_copy: Path, tmp_path: Path) -> None:
    compose = repo_copy / "docker-compose.yml"
    text = compose.read_text()
    needle = f"laxzal/tckdb-frontend:${{TCKDB_IMAGE_TAG:-{VERSION}}}"
    assert needle in text
    compose.write_text(text.replace(needle, "laxzal/tckdb-frontend:${TCKDB_IMAGE_TAG:-0.0.1}"))
    result = _build(
        tmp_path / "out",
        VERSION,
        script=repo_copy / "backend/scripts/ops/make_selfhost_bundle.sh",
        env={"SOURCE_DATE_EPOCH": "1700000000"},
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "0.0.1" in result.stderr


@pytest.mark.parametrize("image", ["tckdb-api2", "tckdb-upload-worker", "tckdb_web"])
def test_refuses_any_tckdb_image_name_with_another_default(repo_copy: Path, tmp_path: Path, image: str) -> None:
    compose = repo_copy / "docker-compose.yml"
    compose.write_text(
        compose.read_text().replace(
            "  worker:\n",
            f"  extra:\n    image: laxzal/{image}:${{TCKDB_IMAGE_TAG:-0.0.2}}\n  worker:\n",
            1,
        )
    )
    result = _build(
        tmp_path / "out",
        VERSION,
        script=repo_copy / "backend/scripts/ops/make_selfhost_bundle.sh",
        env={"SOURCE_DATE_EPOCH": "1700000000"},
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "0.0.2" in result.stderr


# ---------------------------------------------------------------------------
# release-bundle.yml: attaching to a release
# ---------------------------------------------------------------------------

WORKFLOW = REPO_ROOT / ".github/workflows/release-bundle.yml"

#: A stand-in for `gh release`: a release is a directory of assets under
#: $GH_RELEASES/<tag>; every call is logged.
_FAKE_GH = r"""#!/usr/bin/env bash
echo "$*" >> "$GH_LOG"
[[ "$1" == release ]] || exit 2
sub="$2"; tag="$3"; shift 3
dir="$GH_RELEASES/$tag"
case "$sub" in
  view)
    [[ -d "$dir" ]] || { echo "release not found" >&2; exit 1; }
    if [[ "$*" == *"--json assets"* ]]; then ls "$dir"; fi
    exit 0 ;;
  create) mkdir -p "$dir"; exit 0 ;;
  download)
    pattern=""; out="."
    while [[ $# -gt 0 ]]; do
      case "$1" in --pattern) pattern="$2"; shift 2 ;; --dir) out="$2"; shift 2 ;; *) shift ;; esac
    done
    mkdir -p "$out"; cp "$dir/$pattern" "$out/"; exit 0 ;;
  upload)
    for f in "$@"; do [[ "$f" == --* ]] || cp "$f" "$dir/"; done; exit 0 ;;
esac
exit 2
"""


def _attach_script() -> str:
    workflow = yaml.safe_load(WORKFLOW.read_text())
    [step] = [s for s in workflow["jobs"]["attach"]["steps"] if "run" in s]
    return step["run"]


@pytest.fixture
def attach(bundle: Path, tmp_path: Path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "gh").write_text(_FAKE_GH)
    (bindir / "gh").chmod(0o755)
    work = tmp_path / "dist"
    work.mkdir()
    for f in (bundle, bundle.with_name(bundle.name + ".sha256")):
        shutil.copy(f, work / f.name)
    releases = tmp_path / "releases"
    releases.mkdir()
    log = tmp_path / "gh.log"

    def _run() -> tuple[subprocess.CompletedProcess, list[str]]:
        result = subprocess.run(
            ["bash", "-eo", "pipefail", "-c", _attach_script()],
            cwd=work,
            env={
                "PATH": f"{bindir}:{os.environ['PATH']}",
                "GH_LOG": str(log),
                "GH_RELEASES": str(releases),
                "TAG": f"v{VERSION}",
                "NAME": TOP,
            },
            capture_output=True,
            text=True,
            timeout=60,
        )
        return result, log.read_text().splitlines() if log.exists() else []

    _run.releases = releases / f"v{VERSION}"
    return _run


def test_attach_creates_the_release_and_uploads(attach) -> None:
    result, calls = attach()
    assert result.returncode == 0, result.stdout + result.stderr
    assert any(c.startswith(f"release create v{VERSION}") for c in calls)
    assert sorted(p.name for p in attach.releases.iterdir()) == [f"{TOP}.tar.gz", f"{TOP}.tar.gz.sha256"]


def test_attach_reuploads_identical_bytes(attach, bundle: Path) -> None:
    attach.releases.mkdir()
    shutil.copy(bundle, attach.releases / bundle.name)
    shutil.copy(bundle.with_name(bundle.name + ".sha256"), attach.releases / f"{bundle.name}.sha256")
    result, calls = attach()
    assert result.returncode == 0, result.stdout + result.stderr
    assert any(c.startswith(f"release upload v{VERSION}") for c in calls)


@pytest.mark.parametrize("asset", ["tarball", "sha256"])
def test_attach_refuses_to_replace_different_bytes(attach, bundle: Path, asset: str) -> None:
    attach.releases.mkdir()
    if asset == "tarball":
        (attach.releases / bundle.name).write_bytes(b"other bytes")
    else:
        (attach.releases / f"{bundle.name}.sha256").write_text(f"{'0' * 64}  {bundle.name}\n")
    result, calls = attach()
    assert result.returncode != 0, result.stdout + result.stderr
    assert "differs" in result.stdout + result.stderr
    assert not any(c.startswith("release upload") for c in calls)


def test_builds_outside_git_with_source_date_epoch(repo_copy: Path, tmp_path: Path) -> None:
    script = repo_copy / "backend/scripts/ops/make_selfhost_bundle.sh"
    first = _build(tmp_path / "a", VERSION, script=script, env={"SOURCE_DATE_EPOCH": "1700000000"})
    assert first.returncode == 0, first.stderr
    with tarfile.open(tmp_path / "a" / f"{TOP}.tar.gz") as tar:
        assert {m.mtime for m in tar.getmembers()} == {1700000000}
