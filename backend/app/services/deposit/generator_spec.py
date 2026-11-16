"""Resolve an operator-supplied ``module:attr`` into a generator registry.

A deposit carries the registry's package tree under ``scripts/``, so a reproducer
who puts ``<deposit>/scripts`` on ``PYTHONPATH`` imports exactly the sources that
were deposited. That only holds when the registry's top-level package cannot
shadow a package of the checkout it runs beside.
"""

from __future__ import annotations

import importlib
import re
import sys
from collections.abc import Mapping
from pathlib import Path

from app.services.deposit.build import DepositError
from app.services.deposit.expected_outputs import Generator

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_EXTRA_RESERVED = frozenset({"tckdb_schemas", "tckdb_client"})


def reserved_top_level_names() -> frozenset[str]:
    """Top-level names a deposited registry must not use: the backend's own, and the sibling packages."""
    names = {
        entry.name if entry.is_dir() else entry.stem
        for entry in _BACKEND_ROOT.iterdir()
        if not entry.name.startswith(".") and (entry.is_dir() or entry.suffix == ".py")
    }
    return frozenset(names | _EXTRA_RESERVED)


def parse_spec(spec: str | None) -> tuple[str, str]:
    """Split ``module:attr``, requiring dotted Python identifiers on both sides."""
    if not spec:
        raise DepositError("generators_required: pass --generators module:attr naming the generator registry")
    module_name, separator, attr = spec.partition(":")
    parts = module_name.split(".")
    if not separator or not all(_IDENTIFIER.fullmatch(part) for part in parts) or not _IDENTIFIER.fullmatch(attr):
        raise DepositError(f"generators_spec_invalid: {spec!r} is not of the form module.path:attr")
    return module_name, attr


def _purge(top_level: str) -> None:
    """Forget an earlier import of ``top_level`` so the one under the root is the one loaded."""
    if top_level in sys.stdlib_module_names:
        return
    for name in [n for n in sys.modules if n == top_level or n.startswith(top_level + ".")]:
        del sys.modules[name]


def _import_registry(spec: str, module_name: str, attr: str, root: Path | None) -> tuple[Mapping[str, Generator], Path]:
    if root is not None:
        _purge(module_name.partition(".")[0])
        sys.path.insert(0, str(root))
    importlib.invalidate_caches()
    try:
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:
            raise DepositError(f"generators_spec_invalid: cannot import {module_name!r}: {exc!r}") from exc
    finally:
        if root is not None:
            sys.path.remove(str(root))
    try:
        registry = getattr(module, attr)
    except AttributeError as exc:
        raise DepositError(f"generators_spec_invalid: module {module_name!r} has no attribute {attr!r}") from exc
    if not isinstance(registry, Mapping) or not registry:
        raise DepositError(f"generators_spec_invalid: {spec!r} is not a non-empty mapping of name to generator")
    if not all(isinstance(name, str) and callable(fn) for name, fn in registry.items()):
        raise DepositError(f"generators_spec_invalid: {spec!r} must map string names to callables")
    source = getattr(module, "__file__", None)
    if source is None:
        raise DepositError(f"generators_spec_invalid: module {module_name!r} has no source file")
    return registry, Path(source).resolve()


def load_generators(spec: str | None) -> Mapping[str, Generator]:
    """Import ``module:attr`` from the current import path and return the registry."""
    module_name, attr = parse_spec(spec)
    registry, _ = _import_registry(spec, module_name, attr, None)
    return registry


def load_deposit_generators(
    spec: str | None, root: Path | None
) -> tuple[Mapping[str, Generator], list[tuple[Path, str]]]:
    """Resolve a registry to deposit: ``(registry, [(source file, path relative to root)])``.

    ``root`` is an import root: the spec's module must lie under it. The files are every ``*.py``
    of the registry's top-level package (or the one module file), layout preserved, none from
    outside it. Refuses a top-level name the checkout itself uses.
    """
    module_name, attr = parse_spec(spec)
    if root is None:
        raise DepositError("generators_root_required: pass --generator-root, the directory the registry imports from")
    root = Path(root).resolve()
    top_level = module_name.partition(".")[0]
    if top_level in reserved_top_level_names():
        raise DepositError(
            f"generators_name_collision: top-level package {top_level!r} would shadow the checkout's own; "
            "rename the registry package"
        )
    registry, module_file = _import_registry(spec, module_name, attr, root)
    if not module_file.is_relative_to(root):
        raise DepositError(f"generators_outside_root: {module_file} does not lie under {root}")
    package_dir = root / top_level
    sources = sorted(package_dir.rglob("*.py")) if package_dir.is_dir() else [root / f"{top_level}.py"]
    files: list[tuple[Path, str]] = []
    for path in sources:
        resolved = path.resolve()
        if not resolved.is_relative_to(root):
            raise DepositError(f"generators_outside_root: {path} resolves outside {root}")
        files.append((resolved, path.relative_to(root).as_posix()))
    return registry, files
