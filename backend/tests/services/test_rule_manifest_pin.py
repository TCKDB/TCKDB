"""A rule's status comes only from the manifest parsed from its pinned file, in every registry.

Each audited rule manifest carries a ``sha256`` field naming the bytes it was parsed from, and each rule checks that
field against its pin. A frozen dataclass is copied by ``dataclasses.replace``, which keeps ``sha256`` and changes
whatever the caller asked it to, so a check that reads only the field accepts an edited copy under the pinned digest
(#703): the XYG3 rule reported ``active`` over a manifest with ``activation_approved=True`` and no blockers, and the E1
rule did not check the pin at all. These tests build the edited copy in each of the four registries (E1 thermo, XYG3
kinetics, network, structure) and require the rule to refuse it, and require the unedited manifest, also when loaded
a second time, to be accepted, so a refusal cannot be a rule that refuses everything.
"""

from __future__ import annotations

import copy
import dataclasses
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest

from app.chemistry import manifest_attestation
from app.chemistry.kinetics_rules import xyg3_barrier_manifest as xyg3_module
from app.chemistry.network_rules import manifest as network_module
from app.chemistry.structure_rules import manifest as structure_module
from app.chemistry.thermo_rules import e1_manifest as e1_module
from app.services.kinetics_selection import rules as kinetics_rules
from app.services.network_selection import rules as network_rules
from app.services.structure_selection import rules as structure_rules
from app.services.thermo_selection import rules as thermo_rules


@dataclass(frozen=True)
class Registry:
    name: str
    load: Callable[[], Any]
    #: An edited copy of the loaded manifest that keeps its ``sha256`` and flips what the rule reads.
    edit: Callable[[Any], Any]
    #: Builds the rule over a manifest; for the candidate registries, over its first candidate.
    build: Callable[[Any], Any]
    #: What the rule built over the *edited* manifest would have reported, to show the edit matters.
    reads: Callable[[Any], Any]


def _approve(candidate):
    return dataclasses.replace(
        candidate,
        activation_approved=True,
        activation_blockers=(),
        owner_acceptance={"accepted_by": "owner", "date": "2026-10-10"},
    )


def _edit_candidates(manifest):
    return dataclasses.replace(manifest, candidates=tuple(_approve(c) for c in manifest.candidates))


REGISTRIES = [
    Registry(
        "E1",
        lambda: e1_module.load_e1_manifest(expected_sha256=thermo_rules.E1_MANIFEST_SHA256),
        lambda m: dataclasses.replace(m, members=m.members[:-1]),
        lambda m: thermo_rules.E1Rule(m),
        lambda m: len(m.members),
    ),
    Registry(
        "XYG3",
        lambda: xyg3_module.load_xyg3_barrier_manifest(expected_sha256=kinetics_rules.XYG3_MANIFEST_SHA256),
        lambda m: dataclasses.replace(m, activation_approved=True, activation_blockers=()),
        lambda m: kinetics_rules.XYG3B3LYPBarrierRule(m),
        lambda m: m.activatable,
    ),
    Registry(
        "network",
        lambda: network_module.load_network_rule_manifest(expected_sha256=network_rules.NETWORK_RULE_MANIFEST_SHA256),
        _edit_candidates,
        lambda m: network_rules.AuditedNetworkRule(
            next(c for c in m.candidates if c.level == network_module.LEVEL_CANDIDATE), m
        ),
        lambda m: m.candidates[0].activatable,
    ),
    Registry(
        "structure",
        lambda: structure_module.load_structure_rule_manifest(expected_sha256=structure_rules.STRUCTURE_RULE_MANIFEST_SHA256),
        _edit_candidates,
        lambda m: structure_rules.AuditedStructureRule(m.candidates[0], m),
        lambda m: m.candidates[0].activatable,
    ),
]
IDS = [r.name for r in REGISTRIES]


def test_the_registry_list_covers_the_four_pinned_manifests():
    assert IDS == ["E1", "XYG3", "network", "structure"]


@pytest.mark.parametrize("registry", REGISTRIES, ids=IDS)
def test_the_manifest_loaded_from_the_pinned_file_is_accepted_each_time_it_is_loaded(registry):
    manifest = registry.load()
    assert registry.build(manifest) is not None
    assert registry.build(registry.load()) is not None


@pytest.mark.parametrize("registry", REGISTRIES, ids=IDS)
def test_an_edited_copy_that_keeps_the_pinned_digest_is_refused(registry):
    shipped = registry.load()
    edited = registry.edit(shipped)
    assert edited.sha256 == shipped.sha256, "the copy must still carry the pinned digest, or this proves nothing"
    assert registry.reads(edited) != registry.reads(shipped), "the edit must change what the rule reads"
    with pytest.raises(ValueError, match="pinned"):
        registry.build(edited)


@pytest.mark.parametrize("registry", REGISTRIES, ids=IDS)
def test_a_manifest_with_nested_content_changed_in_place_is_refused(registry):
    """The frozen dataclass does not freeze the dicts inside it; the check reads content, not identity."""
    shipped = registry.load()
    mutated = copy.deepcopy(shipped)
    assert mutated == shipped
    holder = mutated.sources[0]
    holder["tampered"] = True
    with pytest.raises(ValueError, match="pinned"):
        registry.build(mutated)


def test_the_xyg3_edit_that_flipped_the_status_now_cannot_reach_a_rule():
    """The reviewer's probe on #700: approval written into a copy of the shipped manifest."""
    shipped = REGISTRIES[1].load()
    edited = dataclasses.replace(shipped, activation_approved=True, activation_blockers=())
    assert edited.activatable is True  # what the old rule would have turned into status "active"
    with pytest.raises(xyg3_module.ManifestError, match="pinned"):
        kinetics_rules.XYG3B3LYPBarrierRule(edited)


@pytest.mark.parametrize(
    ("registry", "rules_module", "build"),
    [
        (REGISTRIES[2], network_rules, lambda c, m: network_rules.AuditedNetworkRule(c, m)),
        (REGISTRIES[3], structure_rules, lambda c, m: structure_rules.AuditedStructureRule(c, m)),
    ],
    ids=["network", "structure"],
)
def test_an_approved_candidate_cannot_make_a_network_or_structure_rule_active(monkeypatch, registry, rules_module, build):
    """Even with the predicates marked implemented, the edited entry is refused rather than registered active."""
    edited = registry.edit(registry.load())
    candidate = next(c for c in edited.candidates if getattr(c, "level", "candidate") == "candidate")
    assert candidate.activatable is True
    monkeypatch.setattr(rules_module, "RULES_WITH_IMPLEMENTED_PREDICATES", frozenset({candidate.rule_id}))
    with pytest.raises(ValueError, match="pinned"):
        build(candidate, edited)


def test_a_manifest_built_from_a_parsed_document_alone_is_never_attested():
    raw = xyg3_module.yaml.safe_load(xyg3_module.MANIFEST_PATH.read_bytes())
    unsealed = xyg3_module.parse_xyg3_barrier_manifest(raw)
    assert unsealed.sha256 is None
    assert manifest_attestation.is_attested(unsealed) is False


def test_moving_the_pin_to_edited_bytes_is_still_how_an_approval_arrives(monkeypatch):
    """The sanctioned path stays open: new bytes, parsed, with the pin moved to their digest, are accepted."""
    import hashlib

    import yaml

    raw = yaml.safe_load(xyg3_module.MANIFEST_PATH.read_bytes())
    raw["status"].update(
        activation_approved=True, activation_blockers=[], curator_acceptance={"accepted_by": "owner", "date": "2026-10-05"}
    )
    data = yaml.safe_dump(raw, sort_keys=False).encode()
    digest = hashlib.sha256(data).hexdigest()
    monkeypatch.setattr(kinetics_rules, "XYG3_MANIFEST_SHA256", digest)
    manifest = xyg3_module.parse_xyg3_barrier_manifest_bytes(data, expected_sha256=digest)
    assert kinetics_rules.XYG3B3LYPBarrierRule(manifest).status == kinetics_rules.RULE_ACTIVE
