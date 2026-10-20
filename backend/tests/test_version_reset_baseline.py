"""Every TCKDB-owned version is at the release baseline (1), and every rule manifest's pin is its file's bytes.

The versioning programme reset every version TCKDB owns to 1 at the release baseline (owner decision 1, 2026-10-24).
This file fails when a registry is empty (so the parametrization cannot pass vacuously), when a registered current
version is not 1, and when a rule manifest's SHA-256 pin is not the digest of the YAML it pins (the checksum
mechanism: a pin is the owner's approval of reviewed bytes, so moving the bytes without moving the pin must fail).

A future release that bumps a version has to change ``BASELINE`` below in the same change, in plain sight of the review.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable

import pytest

from app.chemistry.kinetics_rules.xyg3_barrier_manifest import MANIFEST_PATH as XYG3_PATH
from app.chemistry.network_rules.manifest import MANIFEST_PATH as NETWORK_PATH
from app.chemistry.structure_rules.manifest import MANIFEST_PATH as STRUCTURE_PATH
from app.chemistry.thermo_rules.e1_manifest import MANIFEST_PATH as E1_PATH
from app.services import document_formats
from app.services.kinetics_selection.manifest import MANIFEST_FORMATS as KINETICS_FORMATS
from app.services.kinetics_selection.models import ASSESSMENT_SEMANTICS as KINETICS_SEMANTICS
from app.services.kinetics_selection.rules import XYG3_MANIFEST_SHA256
from app.services.kinetics_selection.rules import default_rules_registry as kinetics_rules
from app.services.machine_review.audit_adapter import PAYLOAD_FORMATS
from app.services.network_selection.manifest import MANIFEST_FORMATS as NETWORK_FORMATS
from app.services.network_selection.rules import NETWORK_RULE_MANIFEST_SHA256
from app.services.network_selection.rules import default_rules_registry as network_rules
from app.services.parser_registry import PARSERS
from app.services.relative_conformer_energy.manifest import MANIFEST_FORMATS as RELATIVE_FORMATS
from app.services.release.manifest import RENDERERS
from app.services.statmech_selection.manifest import ASSESSMENT_SEMANTICS as STATMECH_SEMANTICS
from app.services.statmech_selection.manifest import MANIFEST_FORMATS as STATMECH_FORMATS
from app.services.structure_selection.manifest import MANIFEST_FORMATS as STRUCTURE_FORMATS
from app.services.structure_selection.rules import STRUCTURE_RULE_MANIFEST_SHA256
from app.services.structure_selection.rules import default_rules_registry as structure_rules
from app.services.thermo_selection.models import ASSESSMENT_SEMANTICS as THERMO_SEMANTICS
from app.services.thermo_selection.models import MANIFEST_FORMATS as THERMO_FORMATS
from app.services.thermo_selection.rules import E1_MANIFEST_SHA256
from app.services.thermo_selection.rules import default_rules_registry as thermo_rules
from app.services.trust.rubrics import RUBRICS
from app.versioning import VersionRegistry

#: The version every TCKDB-owned thing is at today.
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
    "thermo_rules": thermo_rules,
    "kinetics_rules": kinetics_rules,
    "structure_rules": structure_rules,
    "network_rules": network_rules,
}


@pytest.mark.parametrize("name", sorted(REGISTRIES))
def test_every_registered_name_is_at_the_baseline_and_nothing_is_empty(name: str) -> None:
    registry = REGISTRIES[name]()
    refs = registry.refs()
    assert refs, f"{name} holds nothing: the parametrization would pass vacuously"
    for ref in refs:
        assert registry.is_current(ref), (name, ref)
        assert ref.version == BASELINE, (name, ref)
        assert registry.current(ref.name).version == BASELINE


def test_the_expected_registries_are_all_covered() -> None:
    assert len(REGISTRIES) == 18


@pytest.mark.parametrize(
    ("pin", "path"),
    [
        (E1_MANIFEST_SHA256, E1_PATH),
        (XYG3_MANIFEST_SHA256, XYG3_PATH),
        (NETWORK_RULE_MANIFEST_SHA256, NETWORK_PATH),
        (STRUCTURE_RULE_MANIFEST_SHA256, STRUCTURE_PATH),
    ],
    ids=["e1", "xyg3", "network", "structure"],
)
def test_every_rule_manifest_pin_is_the_digest_of_its_yaml(pin: str, path) -> None:
    """Kills: editing a rule manifest's bytes and leaving its old pin (or the reverse)."""
    assert hashlib.sha256(path.read_bytes()).hexdigest() == pin


def test_the_four_pins_are_distinct_values_written_out_for_the_owner_to_approve() -> None:
    """The pull request lists these four moves; a pin that did not move would equal its old value here."""
    assert {
        "e1": E1_MANIFEST_SHA256,
        "xyg3": XYG3_MANIFEST_SHA256,
        "network": NETWORK_RULE_MANIFEST_SHA256,
        "structure": STRUCTURE_RULE_MANIFEST_SHA256,
    } == {
        "e1": "f0e9a2676e5778f377c63bea6b6d3511fdd528d25f2da097b9b63607cb0d0926",
        "xyg3": "0b3b743d1e9a262797cb4a36fe69c339a5dfefbda0c1949d51ec452fe29e4293",
        "network": "7c349eb20b4f82eb64d332d9741f7951ccc4da8baa6f5d3d626de8c8fa28c56c",
        "structure": "0dc5ff853cec30767a2e38fbabb07f3966efca34d8af7d43624a3246a6639c27",
    }
