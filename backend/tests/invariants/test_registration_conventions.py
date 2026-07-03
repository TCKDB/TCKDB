"""Registration-convention smoke checks.

These tests lock in the two backend conventions that govern how new model
modules and upload-fragment schemas present themselves. They exist to stop
convention drift (the kind that led to the 2026-04-22 audit's "orphan schema"
and "empty ``app/db/__init__.py``" findings) from silently returning.
"""

from __future__ import annotations

import importlib
import pkgutil

import app.db.models as models_pkg
import app.schemas.workflows as workflows_pkg

# Non-mapper model modules (shared enums / helpers). They do not register
# tables, so the registration-parity check ignores them.
_NON_MAPPER_MODEL_MODULES = frozenset({"common"})


def test_every_model_module_is_registered_in_db_models_init() -> None:
    """Every table-carrying ``app/db/models/<name>.py`` must be imported by the
    package init.

    ``alembic/env.py`` loads model metadata via ``import app.db.models``, so a
    module that exists on disk but is not imported in
    ``app/db/models/__init__.py`` will not contribute to ``Base.metadata`` and
    will silently be invisible to autogenerate and to ``create_all``.

    ``common`` is excluded because it holds shared enums, not mapped classes.
    """
    on_disk = {
        name
        for _, name, is_pkg in pkgutil.iter_modules(models_pkg.__path__)
        if not is_pkg and not name.startswith("_")
    }
    expected = on_disk - _NON_MAPPER_MODEL_MODULES
    registered = set(getattr(models_pkg, "__all__", ()))
    missing = expected - registered
    assert not missing, (
        "Model modules present on disk but not imported in "
        "app/db/models/__init__.py — these will be invisible to Alembic "
        f"autogenerate and to Base.metadata: {sorted(missing)}"
    )


def test_orphan_upload_fragments_have_no_standalone_route() -> None:
    """Upload-fragment schemas must remain fragments.

    ``literature_upload`` and ``energy_correction_upload`` are nested-only
    fragments despite living in ``app/schemas/workflows/``. If a future change
    silently wires one as a top-level upload route without also updating the
    module docstring (which currently declares them fragment-only), this test
    flags the inconsistency so the classification drift is resolved
    deliberately rather than by accident.
    """
    import inspect

    from app.api.routes import uploads as uploads_module

    fragment_only_modules = {
        "literature_upload",
        "energy_correction_upload",
    }

    for fragment_name in fragment_only_modules:
        fragment_module = importlib.import_module(
            f"app.schemas.workflows.{fragment_name}"
        )
        docstring = fragment_module.__doc__ or ""
        assert "no standalone" in docstring.lower(), (
            f"{fragment_name} is declared fragment-only but its module "
            f"docstring does not mark it as 'no standalone … route'. Either "
            f"wire the fragment to a route (and update the docstring) or "
            f"restore the fragment-only marker."
        )

    # Lightweight structural check: no upload route imports these fragment
    # schema modules directly. They should only appear as nested fields on
    # other upload request classes.
    uploads_source = inspect.getsource(uploads_module)
    for banned_fragment in fragment_only_modules:
        wire = f"from app.schemas.workflows.{banned_fragment} import"
        if wire in uploads_source:
            raise AssertionError(
                f"{banned_fragment} is imported in app/api/routes/uploads.py; "
                f"if it is now a standalone upload, remove its fragment-only "
                f"docstring and update this regression test."
            )


def test_workflows_pkg_init_is_docstring_only() -> None:
    """``app/schemas/workflows/__init__.py`` must stay a thin module.

    Contributors rely on per-file docstrings to tell fragment-only schemas
    apart from routed upload requests. If the package init ever grows eager
    ``from .X import Y`` re-exports, the orphan-fragment classification can
    silently get lost again. This check is deliberately source-level so it
    does not depend on which submodules happen to be loaded at test time.
    """
    import inspect

    source = inspect.getsource(workflows_pkg)
    # Strip comments and whitespace; any surviving statements would be
    # re-exports or attribute assignments we don't want.
    non_docstring_lines = [
        line.strip()
        for line in source.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    # A bare docstring module has exactly one stripped non-comment line
    # (the opening + closing triple-quoted string on one line) or a
    # multi-line docstring with the quotes on separate lines.
    joined = " ".join(non_docstring_lines)
    assert joined.startswith('"""') and joined.rstrip().endswith('"""'), (
        "app/schemas/workflows/__init__.py must contain only a module "
        f"docstring. Found: {source!r}"
    )
