"""Every TCKDB-owned version is at its expected current value, and no registry is empty.

The versioning programme reset every version TCKDB owns to 1 at the release baseline (owner decision 1, 2026-10-24).
This file fails when a registry is empty (so the parametrization cannot pass vacuously) and when a registered current
version is not the one written out below.

A release that bumps a version has to change ``EXPECTED`` below in the same change, in plain sight of the review. The
five selection manifest formats are at 2: removing the method-preference rules changed the shape of their decision
documents, and a replay of the old shape is refused as an unknown version.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from app.parser_registry import PARSERS
from app.services import document_formats
from app.services.kinetics_selection.manifest import MANIFEST_FORMATS as KINETICS_FORMATS
from app.services.kinetics_selection.models import ASSESSMENT_SEMANTICS as KINETICS_SEMANTICS
from app.services.machine_review.audit_adapter import PAYLOAD_FORMATS
from app.services.network_selection.manifest import MANIFEST_FORMATS as NETWORK_FORMATS
from app.services.relative_conformer_energy.manifest import MANIFEST_FORMATS as RELATIVE_FORMATS
from app.services.release.manifest import RENDERERS
from app.services.statmech_selection.manifest import ASSESSMENT_SEMANTICS as STATMECH_SEMANTICS
from app.services.statmech_selection.manifest import MANIFEST_FORMATS as STATMECH_FORMATS
from app.services.structure_selection.manifest import MANIFEST_FORMATS as STRUCTURE_FORMATS
from app.services.thermo_selection.models import ASSESSMENT_SEMANTICS as THERMO_SEMANTICS
from app.services.thermo_selection.models import MANIFEST_FORMATS as THERMO_FORMATS
from app.services.trust.rubrics import RUBRICS
from app.versioning import VersionRegistry

#: The version every TCKDB-owned thing is at today, unless ``EXPECTED`` says otherwise.
BASELINE = 1

REGISTRIES: dict[str, Callable[[], VersionRegistry]] = {
    "rubrics": lambda: RUBRICS,
    "document_formats": lambda: document_formats.FORMATS,
    "parsers": lambda: PARSERS,
    "machine_review_payload_formats": lambda: PAYLOAD_FORMATS,
    "release_manifest_renderers": lambda: RENDERERS,
    "thermo_formats": lambda: THERMO_FORMATS,
    "kinetics_formats": lambda: KINETICS_FORMATS,
    "statmech_formats": lambda: STATMECH_FORMATS,
    "structure_formats": lambda: STRUCTURE_FORMATS,
    "network_formats": lambda: NETWORK_FORMATS,
    "relative_energy_formats": lambda: RELATIVE_FORMATS,
    "thermo_semantics": lambda: THERMO_SEMANTICS,
    "kinetics_semantics": lambda: KINETICS_SEMANTICS,
    "statmech_semantics": lambda: STATMECH_SEMANTICS,
}

#: Registries whose current version is not the baseline, and why: the method-preference rules were removed, which
#: changed the decision documents these formats describe.
EXPECTED: dict[str, int] = {
    "thermo_formats": 2,
    "kinetics_formats": 2,
    "statmech_formats": 2,
    "structure_formats": 2,
    "network_formats": 2,
    "relative_energy_formats": 2,
}

#: One rubric is above the baseline: the transition-state badge lists its qualifying refs in the structure
#: administrative key, whose version 2 changed that order.
EXPECTED_REF: dict[tuple[str, str], int] = {("rubrics", "computed_transition_state"): 2}


@pytest.mark.parametrize("name", sorted(REGISTRIES))
def test_every_registered_name_is_at_its_expected_version_and_nothing_is_empty(name: str) -> None:
    registry = REGISTRIES[name]()
    refs = registry.refs()
    assert refs, f"{name} holds nothing: the parametrization would pass vacuously"
    for ref in refs:
        expected = EXPECTED_REF.get((name, ref.name), EXPECTED.get(name, BASELINE))
        assert registry.is_current(ref), (name, ref)
        assert ref.version == expected, (name, ref)
        assert registry.current(ref.name).version == expected


def test_the_expected_registries_are_all_covered() -> None:
    assert len(REGISTRIES) == 14
