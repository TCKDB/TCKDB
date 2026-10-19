"""``VersionRegistry``: one current version per name, unknown versions refused.

The registry is the implementation side of the rule that a versioned thing is
a stable name plus an integer version. Four behaviours matter and each has a
test that fails if it is removed: ``get(name)`` answers the *current* version,
``get(name, n)`` answers exactly ``n`` or raises ``UnknownVersion`` (never
falls back), a name can have only one current version, and a name that already
contains a version is refused.
"""

from __future__ import annotations

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


@pytest.mark.parametrize("name", ["foo_v2", "foo.v1", "tckdb.archive.v1", "rules_0_1_0", "a_v2_b", "Foo", "x"])
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
