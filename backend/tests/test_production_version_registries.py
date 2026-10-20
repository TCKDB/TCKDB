"""Every production ``VersionRegistry`` dispatches the way the versioning design says it does.

The registries are the single place a versioned thing is looked up, so each one is held to the same
contract: ``get(name, None)`` is the current version, an unknown version raises
:class:`~app.versioning.UnknownVersion` (never the current one), exactly one version per name is
current, and the registry is ``startup_checked`` so ``create_app`` validates it.

The expected set of families is written out. A registry that is dropped, renamed or left out of
the startup check changes this list and fails the test, so the parametrization cannot quietly
shrink to nothing.
"""

from __future__ import annotations

import pytest

from app.services.kinetics_selection.manifest import MANIFEST_FORMATS as KINETICS_FORMATS
from app.services.machine_review.admin_trigger import SUPPORTED_RECORD_TYPES
from app.services.manifest_formats import ManifestFormat, read_manifest_format
from app.services.network_selection.manifest import MANIFEST_FORMATS as NETWORK_FORMATS
from app.services.relative_conformer_energy.manifest import MANIFEST_FORMATS as RELATIVE_ENERGY_FORMATS
from app.services.statmech_selection.manifest import MANIFEST_FORMATS as STATMECH_FORMATS
from app.services.structure_selection.manifest import MANIFEST_FORMATS as STRUCTURE_FORMATS
from app.services.thermo_selection.models import MANIFEST_FORMATS as THERMO_FORMATS
from app.services.trust.models import EvidenceRubric
from app.services.trust.rubrics import (
    OWNER_SCOPED_RUBRIC_NAMES,
    RUBRIC_NAME_FOR_RECORD_TYPE,
    RUBRICS,
)
from app.versioning import _STARTUP_REGISTRIES, UnknownVersion, VersionRegistry, validate_startup_registries

REGISTRIES: tuple[VersionRegistry, ...] = (
    RUBRICS,
    THERMO_FORMATS,
    KINETICS_FORMATS,
    STATMECH_FORMATS,
    STRUCTURE_FORMATS,
    NETWORK_FORMATS,
    RELATIVE_ENERGY_FORMATS,
)

EXPECTED_FAMILIES = {
    "trust_rubrics",
    "thermo_selection_manifest_formats",
    "kinetics_selection_manifest_formats",
    "statmech_selection_manifest_formats",
    "structure_selection_manifest_formats",
    "network_selection_manifest_formats",
    "relative_conformer_energy_manifest_formats",
}


def _names(registry: VersionRegistry) -> list[str]:
    return sorted({ref.name for ref in registry.refs()})


def test_the_production_registries_are_the_expected_ones() -> None:
    assert {r.family for r in REGISTRIES} == EXPECTED_FAMILIES
    assert len(REGISTRIES) == len(EXPECTED_FAMILIES)
    for registry in REGISTRIES:
        assert registry.refs(), f"{registry.family} holds nothing"


@pytest.mark.parametrize("registry", REGISTRIES, ids=lambda r: r.family)
def test_every_production_registry_is_startup_checked(registry: VersionRegistry) -> None:
    """A registry that is not startup-checked is never validated by ``create_app``."""
    assert registry.startup_checked is True
    assert registry in _STARTUP_REGISTRIES
    validate_startup_registries()


@pytest.mark.parametrize("registry", REGISTRIES, ids=lambda r: r.family)
def test_none_is_the_current_version_and_an_unknown_version_is_refused(registry: VersionRegistry) -> None:
    names = _names(registry)
    assert names, f"{registry.family} holds no names"
    for name in names:
        current = registry.current(name)
        assert registry.get(name, None) is registry.get(name, current.version)
        assert registry.get(name) is registry.get(name, None)
        assert registry.is_current(current)
        unknown = max(ref.version for ref in registry.refs() if ref.name == name) + 1000
        with pytest.raises(UnknownVersion) as raised:
            registry.get(name, unknown)
        assert raised.value.name == name and raised.value.version == unknown
        assert current.version in raised.value.known
    with pytest.raises(UnknownVersion):
        registry.get("not_a_registered_name", None)


@pytest.mark.parametrize("registry", REGISTRIES, ids=lambda r: r.family)
def test_each_name_has_exactly_one_current_version(registry: VersionRegistry) -> None:
    for name in _names(registry):
        current = [ref for ref in registry.refs() if ref.name == name and registry.is_current(ref)]
        assert len(current) == 1, name


def test_the_rubric_registry_holds_every_rubric_by_its_own_name_and_version() -> None:
    for ref in RUBRICS.refs():
        rubric = RUBRICS.get(ref.name, ref.version)
        assert isinstance(rubric, EvidenceRubric)
        assert (rubric.name, rubric.version) == (ref.name, ref.version)
        assert rubric.ref == ref


def test_the_record_type_routing_table_covers_every_record_type_that_has_an_evidence_block() -> None:
    """A record type dropped from the table silently loses its trust evidence."""
    assert set(RUBRIC_NAME_FOR_RECORD_TYPE) == set(SUPPORTED_RECORD_TYPES)
    for record_type, name in RUBRIC_NAME_FOR_RECORD_TYPE.items():
        assert RUBRICS.get(name).record_type == record_type, record_type
    for record_type, name in OWNER_SCOPED_RUBRIC_NAMES:
        assert record_type in RUBRIC_NAME_FOR_RECORD_TYPE
        assert RUBRICS.get(name).record_type == record_type


def test_the_api_vocabulary_lists_the_owner_scoped_rubrics_checks() -> None:
    """The vocabulary must read the owner-scoped rubric too, not only the record-type defaults."""
    from app.glossary import rubric_checks

    listed = {(check.rubric, check.name) for check in rubric_checks()}
    ts_rubric = RUBRICS.get("computed_ts_statmech")
    assert ts_rubric.checks
    assert {("computed_ts_statmech", spec.name) for spec in ts_rubric.checks} <= listed
    assert ("computed_statmech", "species_entry_present") in listed
    assert ("computed_ts_statmech", "transition_state_entry_present") in listed


@pytest.mark.parametrize(
    "recorded",
    [None, True, False, 2.0, "2", 0, -1, 1000],
    ids=repr,
)
def test_a_manifest_format_the_registry_does_not_hold_is_refused_never_read_as_current(recorded: object) -> None:
    """``registry.get(name, None)`` means "current"; a manifest that records no version must not get it."""
    from app.services.thermo_selection.models import MANIFEST_FORMAT_NAME

    class Refused(Exception):
        pass

    with pytest.raises(Refused, match="unknown manifest format"):
        read_manifest_format(
            THERMO_FORMATS, MANIFEST_FORMAT_NAME, {"manifest_format_version": recorded}, Refused
        )
    with pytest.raises(Refused, match="unknown manifest format"):
        read_manifest_format(THERMO_FORMATS, MANIFEST_FORMAT_NAME, {}, Refused)


def test_the_current_manifest_format_is_read_back_as_registered() -> None:
    from app.services.thermo_selection.models import MANIFEST_FORMAT_NAME

    current = THERMO_FORMATS.current(MANIFEST_FORMAT_NAME)
    got = read_manifest_format(THERMO_FORMATS, MANIFEST_FORMAT_NAME, {"manifest_format_version": current.version}, ValueError)
    assert got == ManifestFormat(MANIFEST_FORMAT_NAME, current.version)


def test_the_exported_format_constants_are_what_each_registry_holds_as_current() -> None:
    """The constants are only the registration argument. That each *writer* stamps the registry's version, and
    each reader refuses a float, bool, text or missing one, is tested per engine beside its manifest tests."""
    from app.services.kinetics_selection.manifest import MANIFEST_FORMAT_NAME as k_name
    from app.services.kinetics_selection.manifest import MANIFEST_FORMAT_VERSION as k_ver
    from app.services.network_selection.manifest import MANIFEST_FORMAT_NAME as n_name
    from app.services.network_selection.manifest import MANIFEST_FORMAT_VERSION as n_ver
    from app.services.statmech_selection.manifest import MANIFEST_FORMAT_NAME as s_name
    from app.services.statmech_selection.manifest import MANIFEST_FORMAT_VERSION as s_ver
    from app.services.structure_selection.manifest import MANIFEST_FORMAT_NAME as t_name
    from app.services.structure_selection.manifest import MANIFEST_FORMAT_VERSION as t_ver
    from app.services.thermo_selection.models import MANIFEST_FORMAT_NAME as h_name
    from app.services.thermo_selection.models import MANIFEST_FORMAT_VERSION as h_ver

    for registry, name, version in (
        (KINETICS_FORMATS, k_name, k_ver),
        (NETWORK_FORMATS, n_name, n_ver),
        (STATMECH_FORMATS, s_name, s_ver),
        (STRUCTURE_FORMATS, t_name, t_ver),
        (THERMO_FORMATS, h_name, h_ver),
    ):
        assert registry.current(name).version == version
