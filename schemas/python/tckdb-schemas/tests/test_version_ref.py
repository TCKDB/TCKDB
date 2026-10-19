"""``VersionRef``: a stable name plus a separate integer version."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tckdb_schemas.fragments.version import VERSIONED_NAME_PATTERNS, VersionRef, embeds_version


def test_a_ref_holds_the_name_and_the_integer_apart() -> None:
    ref = VersionRef(name="tckdb.dataset_release", version=1)
    assert (ref.name, ref.version) == ("tckdb.dataset_release", 1)
    assert ref.model_dump() == {"name": "tckdb.dataset_release", "version": 1}


def test_a_ref_is_frozen_and_hashable() -> None:
    ref = VersionRef(name="computed_thermo", version=1)
    assert {ref: 1}[VersionRef(name="computed_thermo", version=1)] == 1
    with pytest.raises(ValidationError):
        ref.version = 2  # type: ignore[misc]


@pytest.mark.parametrize("version", [0, -3, "2", 2.0, True, None])
def test_the_version_is_a_strict_integer_of_at_least_one(version: object) -> None:
    with pytest.raises(ValidationError):
        VersionRef(name="thing", version=version)  # type: ignore[arg-type]


@pytest.mark.parametrize("name", ["", "x", "Thing", "1thing", "thing_", "has space", "a" * 129])
def test_a_malformed_name_is_refused(name: str) -> None:
    with pytest.raises(ValidationError):
        VersionRef(name=name, version=1)


@pytest.mark.parametrize(
    "name",
    ["computed_thermo_v2", "tckdb.archive.v1", "machine_review_v2_payload", "rule-v10", "manifest_0_3_0", "rule-0.1.0"],
)
def test_a_name_with_a_version_in_it_is_refused(name: str) -> None:
    assert embeds_version(name)
    with pytest.raises(ValidationError, match="embeds a version"):
        VersionRef(name=name, version=1)


@pytest.mark.parametrize(
    "name", ["computed_thermo", "tckdb.dataset_release", "statmech-selection", "gaussian_parameters", "psi4"]
)
def test_an_ordinary_name_is_accepted(name: str) -> None:
    assert not embeds_version(name)
    assert VersionRef(name=name, version=1).name == name


def test_extra_fields_are_forbidden() -> None:
    with pytest.raises(ValidationError):
        VersionRef(name="thing", version=1, label="x")  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "text",
    ["ContributionBundleV0", "XRuleV0_2_0", "ContributionBundleV0.schema.json", "parse_v2_payload", "name@2"],
)
def test_the_patterns_see_the_forms_a_trailing_word_boundary_misses(text: str) -> None:
    assert any(pattern.search(text) for pattern in VERSIONED_NAME_PATTERNS)


@pytest.mark.parametrize("text", ["v1", "v2_", "HFV2", "vault_2", "a_v2x", "/api/v1/species", "utf_8"])
def test_the_patterns_leave_alone_what_is_not_a_version(text: str) -> None:
    assert not embeds_version(text)
