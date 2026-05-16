"""Smoke tests for the experimental builder subpackage layout.

The builder layer must be importable without any backend dependencies
(see ``docs/builder_api_mvp.md`` §3). A failure here usually means
someone added an ``app.*`` / SQLAlchemy / FastAPI / RDKit import to a
module under ``tckdb_client/builders/``.
"""

from __future__ import annotations


def test_public_imports_resolve():
    from tckdb_client.builders import (  # noqa: F401
        Calculation,
        ComputedSpeciesUpload,
        Geometry,
        LevelOfTheory,
        Species,
        SoftwareRelease,
        TCKDBBuilderValidationError,
    )


def test_no_forbidden_backend_imports_in_builder_modules():
    """Static scan of the builders subpackage for forbidden imports."""
    import pathlib

    import tckdb_client.builders as builders_pkg

    forbidden = {
        "app",
        "backend",
        "sqlalchemy",
        "alembic",
        "fastapi",
        "pydantic_settings",
        "rdkit",
    }
    root = pathlib.Path(builders_pkg.__file__).parent
    offenders: list[str] = []
    for path in sorted(root.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        for module in forbidden:
            # Match either ``import <m>`` / ``from <m>`` / ``from <m>.``
            tokens = (
                f"import {module}",
                f"from {module} ",
                f"from {module}.",
            )
            if any(token in source for token in tokens):
                offenders.append(f"{path.name}: imports {module}")
    assert not offenders, "forbidden builder imports: " + "; ".join(offenders)


def test_builders_import_without_backend_modules_loaded():
    """The builder subpackage must import in a process where the
    backend's ``app`` namespace was never importable.

    Some downstream environments install ``tckdb-client`` standalone
    (no monorepo on ``sys.path``). This test reimports the builder
    subpackage in a sandbox where ``app`` resolution is blocked.
    """
    import importlib
    import sys
    import types

    class _Blocker:
        def find_module(self, name, path=None):  # noqa: D401 — finder
            if name.split(".", 1)[0] == "app":
                return self

            return None

        def load_module(self, name):  # pragma: no cover - blocker
            raise ImportError(f"blocked: {name}")

    saved_modules = {
        name: mod
        for name, mod in list(sys.modules.items())
        if name.startswith("tckdb_client.builders") or name == "tckdb_client.builders"
    }
    for name in saved_modules:
        del sys.modules[name]

    blocker = _Blocker()
    sys.meta_path.insert(0, blocker)
    try:
        importlib.import_module("tckdb_client.builders")
        # Touch a couple of representative classes so import
        # side-effects are exercised in the sandbox.
        from tckdb_client.builders import Species  # noqa: F401
    finally:
        sys.meta_path.remove(blocker)
        # Drop the freshly-reimported builder modules and restore the
        # originals captured before the sandbox ran. We must overwrite
        # — not ``setdefault`` — because the in-sandbox import created
        # *new* module objects (with *new* class identities), and
        # leaving those in ``sys.modules`` makes ``isinstance(x, Cls)``
        # break in any test module that captured ``Cls`` before this
        # test ran.
        for name in list(sys.modules):
            if (
                name.startswith("tckdb_client.builders")
                or name == "tckdb_client.builders"
            ):
                del sys.modules[name]
        for name, mod in saved_modules.items():
            sys.modules[name] = mod
