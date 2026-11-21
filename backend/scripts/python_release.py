#!/usr/bin/env python3
"""Resolve and smoke-test one TCKDB Python distribution for release.

Used by ``.github/workflows/publish-python.yml``. The packages it knows are
``check_package_version_bump.PACKAGES``, so the version guard and the
publisher cannot disagree about which distributions exist or where they live.

``resolve`` takes a release tag ``<dist-name>-v<version>`` (or a bare
distribution name for a TestPyPI dry run) and prints ``key=value`` lines for
``$GITHUB_OUTPUT``:

- ``name``, ``version``, ``directory``, ``module``;
- ``deps``: the directories of the first-party distributions it requires,
  dependencies first, space-separated. The workflow builds these from the
  same commit and installs them with the package, so a requirement window
  that excludes the in-repo version fails the install.

A tag whose version differs from the package's ``[project].version`` is
refused.

``smoke`` runs inside the clean environment the wheel was installed into. It
imports the module from site-packages, checks the installed version and
``__version__``, and loads every console-script entry point.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import re
import sys
import sysconfig
import tomllib
from importlib import metadata
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_packages():
    path = Path(__file__).with_name("check_package_version_bump.py")
    name = "_python_release_version_guard"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Registered before execution: ``@dataclass`` looks its module up in ``sys.modules``.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module.PACKAGES


PACKAGES = _load_packages()

TAG_RE = re.compile(r"^(?P<name>tckdb-[a-z0-9]+(?:-[a-z0-9]+)*?)-v(?P<version>[0-9][^\s/]*)$")
_REQUIREMENT_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


class ReleaseError(ValueError):
    """A tag or package name that cannot be released as given."""


def _normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def package_names() -> list[str]:
    return [package.name for package in PACKAGES]


def package_directory(name: str) -> str:
    for package in PACKAGES:
        if package.name == name:
            return str(Path(package.pyproject).parent)
    raise ReleaseError(f"{name!r} is not a released TCKDB distribution; known: {', '.join(package_names())}")


def _project(name: str, root: Path) -> dict:
    return tomllib.loads((root / package_directory(name) / "pyproject.toml").read_text())["project"]


def declared_version(name: str, root: Path = REPO_ROOT) -> str:
    return _project(name, root)["version"]


def parse_tag(tag: str) -> tuple[str, str]:
    match = TAG_RE.match(tag)
    if match is None:
        raise ReleaseError(f"tag {tag!r} is not of the form <dist-name>-v<version>")
    name = match.group("name")
    package_directory(name)
    return name, match.group("version")


def first_party_dependencies(name: str, root: Path = REPO_ROOT) -> list[str]:
    """First-party distributions ``name`` requires, transitively, dependencies first."""
    known = {_normalise(n): n for n in package_names()}
    ordered: list[str] = []

    def visit(current: str, trail: tuple[str, ...]) -> None:
        for requirement in _project(current, root).get("dependencies", []):
            match = _REQUIREMENT_NAME_RE.match(requirement)
            dependency = known.get(_normalise(match.group(1))) if match else None
            if dependency is None or dependency in ordered:
                continue
            if dependency in trail:
                raise ReleaseError(f"dependency cycle: {' -> '.join((*trail, dependency))}")
            visit(dependency, (*trail, dependency))
            ordered.append(dependency)

    visit(name, (name,))
    return ordered


def resolve(*, tag: str | None = None, package: str | None = None, root: Path = REPO_ROOT) -> dict[str, str]:
    if (tag is None) == (package is None):
        raise ReleaseError("give exactly one of a tag or a package name")
    if tag is not None:
        name, tag_version = parse_tag(tag)
    else:
        name, tag_version = package, None
    directory = package_directory(name)
    version = declared_version(name, root)
    if tag_version is not None and tag_version != version:
        raise ReleaseError(
            f"tag {tag!r} names version {tag_version}, but {directory}/pyproject.toml is version {version}; "
            "tag the commit that carries the version you mean to publish"
        )
    return {
        "name": name,
        "version": version,
        "directory": directory,
        "module": name.replace("-", "_"),
        "deps": " ".join(package_directory(dep) for dep in first_party_dependencies(name, root)),
    }


def smoke(name: str, version: str) -> list[str]:
    """Check the installed distribution; return what was checked."""
    checked: list[str] = []
    installed = metadata.version(name)
    if installed != version:
        raise ReleaseError(f"installed {name} is {installed}, expected {version}")
    module_name = name.replace("-", "_")
    module = importlib.import_module(module_name)
    site = Path(sysconfig.get_paths()["purelib"]).resolve()
    location = Path(module.__file__).resolve()
    if site not in location.parents:
        raise ReleaseError(f"{module_name} was imported from {location}, not from the installed wheel in {site}")
    checked.append(f"import {module_name} from {location}")
    module_version = getattr(module, "__version__", None)
    if module_version is not None and module_version != version:
        raise ReleaseError(f"{module_name}.__version__ is {module_version}, but the distribution is {version}")
    if module_version is not None:
        checked.append(f"{module_name}.__version__ == {version}")
    for entry_point in metadata.distribution(name).entry_points:
        if entry_point.group == "console_scripts":
            entry_point.load()
            checked.append(f"console script {entry_point.name} -> {entry_point.value}")
    return checked


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    resolve_parser = sub.add_parser("resolve", help="print name, version, directory, module and deps")
    which = resolve_parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--tag", help="release tag <dist-name>-v<version>")
    which.add_argument("--package", help="distribution name, for a dry run without a tag")
    smoke_parser = sub.add_parser("smoke", help="check the installed distribution")
    smoke_parser.add_argument("--name", required=True)
    smoke_parser.add_argument("--version", required=True)
    args = parser.parse_args(argv)

    try:
        if args.command == "resolve":
            for key, value in resolve(tag=args.tag, package=args.package).items():
                print(f"{key}={value}")
        else:
            for line in smoke(args.name, args.version):
                print(line)
    except (ReleaseError, ImportError, metadata.PackageNotFoundError) as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
