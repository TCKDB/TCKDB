"""Import a package CI installs: skip without it locally, fail on CI.

``pytest.importorskip`` is the wrong tool for a dependency the CI job is
supposed to provide. It skips on CI exactly as it skips on a laptop, so an
install step that stops installing the package turns every test behind it
into a skip and the gate into a green that tested nothing.

That is what happened to ``tests/client_builder_contract/`` (#575): the
backend job never installed ``tckdb_client``, all nine builder contract
modules skipped on every run, and the gate reported success.

Off CI a missing package is still a skip, with the install command in the
reason, so a developer without it can run the rest of the suite. On CI it is
a collection error. ``on_ci`` reads the same variable as
``tests/services/_live_object_store.on_ci``, which applies this rule to the
object store.
"""

from __future__ import annotations

import importlib
import os
from types import ModuleType

import pytest


def on_ci() -> bool:
    """True on a GitHub Actions runner, which sets this for every step."""
    return os.environ.get("GITHUB_ACTIONS") == "true"


def require_module(name: str, *, install: str) -> ModuleType:
    """Import ``name`` for a test module; skip locally, fail on CI, if absent.

    Call at module level, in place of ``pytest.importorskip(name)``.
    ``install`` is the command that provides the package, quoted in both
    the skip reason and the failure.
    """
    # Report the skip or failure at the caller's line, not this one, as
    # ``pytest.importorskip`` does. Without it every skip reads
    # ``_ci_dependency.py:<n>`` and xdist folds them into one.
    __tracebackhide__ = True
    try:
        return importlib.import_module(name)
    except ImportError as exc:
        reason = f"could not import {name!r} ({exc}); install it with: {install}"
        if on_ci():
            pytest.fail(
                f"{reason} -- CI installs it, so this is a broken setup, not a skip",
                pytrace=False,
            )
        pytest.skip(reason, allow_module_level=True)
