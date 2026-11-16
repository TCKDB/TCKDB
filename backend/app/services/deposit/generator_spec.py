"""Resolve an operator-supplied ``module:attr`` into a generator registry."""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from pathlib import Path

from app.services.deposit.build import DepositError
from app.services.deposit.expected_outputs import Generator


def load_generators(spec: str | None) -> tuple[Mapping[str, Generator], Path]:
    """Import ``module:attr`` and return ``(registry, directory of the module's source)``.

    ``attr`` is a mapping of name to generator.
    Refuses a missing spec, a malformed one, an unimportable module, a missing attribute
    and an empty registry.
    """
    if not spec:
        raise DepositError("generators_required: pass --generators module:attr naming the generator registry")
    module_name, separator, attr = spec.partition(":")
    if not separator or not module_name or not attr:
        raise DepositError(f"generators_spec_invalid: {spec!r} is not of the form module:attr")
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise DepositError(f"generators_spec_invalid: cannot import {module_name!r}: {exc}") from exc
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
    return registry, Path(source).resolve().parent
