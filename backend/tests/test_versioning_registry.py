"""``VersionRegistry``: one current version per name, unknown versions refused.

The registry is the implementation side of the rule that a versioned thing is
a stable name plus an integer version. Four behaviours matter and each has a
test that fails if it is removed: ``get(name)`` answers the *current* version,
``get(name, n)`` answers exactly ``n`` or raises ``UnknownVersion`` (never
falls back), a name can have only one current version, and a name that already
contains a version is refused.
"""

from __future__ import annotations

from collections.abc import Mapping

import pytest
from pydantic import ValidationError
from tckdb_schemas.fragments.version import VersionRef

from app.services.trust.models import EvidenceRubric
from app.versioning import UnknownVersion, VersionRegistry


def _registry() -> VersionRegistry[str]:
    registry: VersionRegistry[str] = VersionRegistry("test_family")
    # The current one is neither the first registered, nor the last, nor the
    # highest, so each of those is a distinguishable wrong answer.
    registry.register("thing", 1, "thing-one", current=False)
    registry.register("thing", 2, "thing-two", current=True)
    registry.register("thing", 3, "thing-three", current=False)
    registry.register("other", 1, "other-one", current=True)
    registry.validate()
    return registry


def test_get_without_a_version_returns_the_current_one() -> None:
    registry = _registry()
    assert registry.get("thing") == "thing-two"
    assert registry.get("other") == "other-one"
    assert registry.current("thing") == VersionRef(name="thing", version=2)


def test_get_with_a_version_returns_exactly_that_version() -> None:
    registry = _registry()
    assert registry.get("thing", 1) == "thing-one"
    assert registry.get("thing", 3) == "thing-three"
    assert registry.get("thing", 2) == registry.get("thing")


def test_get_of_an_unregistered_version_raises_unknown_version_and_does_not_fall_back() -> None:
    registry = _registry()
    with pytest.raises(UnknownVersion) as raised:
        registry.get("thing", 7)
    error = raised.value
    assert (error.family, error.name, error.version) == ("test_family", "thing", 7)
    assert error.known == (1, 2, 3)
    assert isinstance(error, LookupError)
    assert "7" in str(error) and "thing" in str(error)


def test_get_of_an_unregistered_name_raises_unknown_version() -> None:
    registry = _registry()
    with pytest.raises(UnknownVersion) as raised:
        registry.get("missing")
    assert raised.value.known == ()
    with pytest.raises(UnknownVersion):
        registry.get("missing", 1)
    with pytest.raises(UnknownVersion):
        registry.current("missing")


def test_a_name_cannot_have_two_current_versions() -> None:
    registry: VersionRegistry[str] = VersionRegistry("f")
    registry.register("thing", 1, "a", current=True)
    with pytest.raises(ValueError, match="already has current version 1"):
        registry.register("thing", 2, "b", current=True)
    # The refused registration left nothing behind.
    with pytest.raises(UnknownVersion):
        registry.get("thing", 2)
    assert registry.get("thing") == "a"


def test_the_same_name_and_version_cannot_be_registered_twice() -> None:
    registry: VersionRegistry[str] = VersionRegistry("f")
    registry.register("thing", 1, "a", current=True)
    with pytest.raises(ValueError, match="already registered"):
        registry.register("thing", 1, "b", current=False)
    assert registry.get("thing", 1) == "a"


def test_a_name_without_a_current_version_fails_validation() -> None:
    registry: VersionRegistry[str] = VersionRegistry("f")
    registry.register("thing", 1, "a", current=False)
    with pytest.raises(ValueError, match="no current version for thing"):
        registry.validate()
    with pytest.raises(UnknownVersion, match="no current version"):
        registry.get("thing")
    assert registry.get("thing", 1) == "a"


@pytest.mark.parametrize("name", ["foo_v2", "foo.v1", "tckdb.archive.v1", "rules_0_1_0", "a_v2_b", "1abc", "x"])
def test_a_name_that_embeds_a_version_or_is_malformed_is_refused(name: str) -> None:
    registry: VersionRegistry[str] = VersionRegistry("f")
    with pytest.raises(ValueError):
        registry.register(name, 1, "impl", current=True)
    assert registry.refs() == ()


@pytest.mark.parametrize("version", [0, -1, True, 1.0, "1", None])
def test_a_version_must_be_an_integer_of_at_least_one(version: object) -> None:
    registry: VersionRegistry[str] = VersionRegistry("f")
    with pytest.raises(ValueError):
        registry.register("thing", version, "impl", current=True)  # type: ignore[arg-type]
    assert registry.refs() == ()


def test_is_current_is_true_only_for_the_current_registered_version() -> None:
    registry = _registry()
    assert registry.is_current(VersionRef(name="thing", version=2))
    assert not registry.is_current(VersionRef(name="thing", version=1))  # known, but older
    assert not registry.is_current(VersionRef(name="thing", version=9))  # never registered
    assert not registry.is_current(VersionRef(name="missing", version=1))


def test_refs_lists_every_registered_pair_in_name_then_version_order() -> None:
    registry = _registry()
    assert registry.refs() == (
        VersionRef(name="other", version=1),
        VersionRef(name="thing", version=1),
        VersionRef(name="thing", version=2),
        VersionRef(name="thing", version=3),
    )


def test_a_version_dependent_function_follows_the_pattern() -> None:
    """``version: int | None = None``, dispatch through the registry."""
    registry: VersionRegistry = VersionRegistry("formats")
    registry.register("doc", 1, lambda value: f"one:{value}", current=False)
    registry.register("doc", 2, lambda value: f"two:{value}", current=True)
    registry.validate()

    def render(value: str, *, version: int | None = None) -> str:
        return registry.get("doc", version)(value)

    assert render("x") == "two:x"
    assert render("x", version=1) == "one:x"
    with pytest.raises(UnknownVersion):
        render("x", version=3)


# -- VersionRef ---------------------------------------------------------------


def test_version_ref_is_frozen_hashable_and_compares_by_value() -> None:
    ref = VersionRef(name="computed_thermo", version=1)
    assert ref == VersionRef(name="computed_thermo", version=1)
    assert len({ref, VersionRef(name="computed_thermo", version=1)}) == 1
    with pytest.raises(ValidationError):
        ref.version = 2  # type: ignore[misc]


@pytest.mark.parametrize("version", [0, "2", 2.0, True])
def test_version_ref_version_is_a_real_integer_of_at_least_one(version: object) -> None:
    with pytest.raises(ValidationError):
        VersionRef(name="thing", version=version)  # type: ignore[arg-type]


def test_version_ref_forbids_extra_fields() -> None:
    with pytest.raises(ValidationError):
        VersionRef(name="thing", version=1, extra=1)  # type: ignore[call-arg]


def test_version_ref_refuses_a_name_with_a_version_in_it() -> None:
    with pytest.raises(ValidationError, match="embeds a version"):
        VersionRef(name="computed_thermo_v2", version=2)


def test_an_evidence_rubric_exposes_its_ref() -> None:
    rubric = EvidenceRubric(name="computed_thermo", version=3, record_type="thermo", checks=())
    assert rubric.ref == VersionRef(name="computed_thermo", version=3)
    assert not hasattr(rubric, "qualified_name")


# -- strict versions and the startup check -------------------------------------


@pytest.mark.parametrize("version", [True, False, 2.0, 1.0, "2", 2 + 0j])
def test_get_refuses_a_version_that_is_not_exactly_an_int(version: object) -> None:
    registry = _registry()
    with pytest.raises(TypeError, match="must be an int"):
        registry.get("thing", version)  # type: ignore[arg-type]


def test_get_still_accepts_none_and_a_real_int() -> None:
    registry = _registry()
    assert registry.get("thing", None) == "thing-two"
    assert registry.get("thing", 1) == "thing-one"


def test_a_startup_checked_registry_with_a_currentless_name_fails_the_sweep() -> None:
    from app.versioning import validate_startup_registries

    broken: VersionRegistry[str] = VersionRegistry("broken_family", startup_checked=True)
    broken.register("thing", 1, "a", current=False)
    with pytest.raises(ValueError, match="no current version for thing"):
        validate_startup_registries()


def test_a_registry_that_did_not_opt_in_is_not_swept_up() -> None:
    from app.versioning import validate_startup_registries

    loose: VersionRegistry[str] = VersionRegistry("loose_family")
    loose.register("thing", 1, "a", current=False)
    validate_startup_registries()  # does not raise: it is not startup-checked


def test_a_well_formed_startup_checked_registry_passes() -> None:
    from app.versioning import validate_startup_registries

    fine: VersionRegistry[str] = VersionRegistry("fine_family", startup_checked=True)
    fine.register("thing", 1, "a", current=True)
    validate_startup_registries()
    assert fine.get("thing") == "a"


def test_create_app_runs_the_registry_check(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.api.app as app_module

    calls: list[str] = []
    monkeypatch.setattr(app_module, "validate_version_registries", lambda: calls.append("ran"))
    app_module.create_app()
    assert calls == ["ran"]


def test_create_app_fails_when_a_startup_registry_is_malformed() -> None:
    import app.api.app as app_module

    broken: VersionRegistry[str] = VersionRegistry("broken_at_boot", startup_checked=True)
    broken.register("thing", 1, "a", current=False)
    with pytest.raises(ValueError, match="no current version for thing"):
        app_module.create_app()


# -- every registry in app is seen by the startup check ------------------------


def _unchecked_registries(modules: Mapping[str, object]) -> list[str]:
    """``module.attr`` of each module-level ``VersionRegistry`` not built with ``startup_checked=True``.

    Looks at module attributes and one level of dict/list/tuple values, which is where a registry
    kept in a table of families would sit.
    """
    found = []
    for module_name, module in sorted(modules.items()):
        for attr, value in vars(module).items():
            candidates = [value]
            if isinstance(value, dict):
                candidates = list(value.values())
            elif isinstance(value, (list, tuple)):
                candidates = list(value)
            for candidate in candidates:
                if isinstance(candidate, VersionRegistry) and not candidate.startup_checked:
                    found.append(f"{module_name}.{attr} ({candidate.family})")
    return found


def test_every_registry_in_app_is_startup_checked() -> None:
    """Import all of ``app`` and require every module-level registry to opt in to the startup check.

    TODAY THIS HOLDS BECAUSE THERE ARE NO REGISTRIES IN ``app`` YET: ``app/versioning.py`` is the only
    thing this change adds, and the families that will use it arrive in later PRs. The assertion that
    the sweep reached the code is on the module count, and the planted-registry test below proves the
    detector bites; neither needs a registry invented here. When a later PR adds one with
    ``startup_checked=False`` (the default), this fails and says which.
    """
    import importlib
    import pkgutil
    import sys

    import app

    for info in pkgutil.walk_packages(app.__path__, "app."):
        importlib.import_module(info.name)
    modules = {name: mod for name, mod in sys.modules.items() if name.startswith("app.") and mod is not None}
    assert len(modules) > 300, f"only {len(modules)} app modules were imported"
    assert "app.versioning" in modules
    assert _unchecked_registries(modules) == []


def test_the_sweep_finds_a_planted_unchecked_registry_and_ignores_checked_ones() -> None:
    import types

    planted = types.ModuleType("app.planted")
    planted.LOOSE = VersionRegistry("loose")  # type: ignore[attr-defined]
    planted.CHECKED = VersionRegistry("checked", startup_checked=True)  # type: ignore[attr-defined]
    planted.TABLE = {"a": VersionRegistry("in_a_table")}  # type: ignore[attr-defined]
    planted.NOT_A_REGISTRY = {"a": 1}  # type: ignore[attr-defined]
    found = _unchecked_registries({"app.planted": planted})
    assert sorted(found) == ["app.planted.LOOSE (loose)", "app.planted.TABLE (in_a_table)"]
    clean = types.ModuleType("app.clean")
    clean.OK = VersionRegistry("ok", startup_checked=True)  # type: ignore[attr-defined]
    assert _unchecked_registries({"app.clean": clean}) == []
