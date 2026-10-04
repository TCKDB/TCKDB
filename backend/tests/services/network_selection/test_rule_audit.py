"""The audited council examples: registered, pinned, and INACTIVE, each with what is missing.

Nothing here activates a rule, and the tests are written so that nothing could: the manifest is pinned by digest,
an entry that claims approval must carry the owner's acceptance and no blockers, an approved entry still applies no
edge until its predicates exist, and the shipped entries are all unapproved with specific missing prerequisites.
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import json

import pytest
import yaml

from app.chemistry.network_rules import manifest as manifest_module
from app.chemistry.network_rules.manifest import (
    MANIFEST_PATH,
    ManifestError,
    parse_network_rule_manifest,
    parse_network_rule_manifest_bytes,
)
from app.services.network_selection import ReplayError, replay_network, select_network
from app.services.network_selection import rules as rules_module
from app.services.network_selection.rules import (
    NETWORK_RULE_MANIFEST_SHA256,
    RULE_ACTIVE,
    RULE_INACTIVE,
    AuditedNetworkRule,
    default_rules,
    validate_rules,
)
from app.services.selection_kernel import Outcome
from tests.services.network_selection._requests import channel_request
from tests.services.network_selection._rules import protocol
from tests.services.network_selection._world import add_solve, fit_spec

COUNCIL_EXAMPLES = {
    "N-AMEDRO-HE-FC": ("representation_fidelity", "Amedro"),
    "N-JG-REDUCTION-FIDELITY": ("model_fidelity", "Johnson"),
    "N-JM-CH4-TRANSFER": ("model_fidelity", "Jasper"),
    "N-JM-CH4-RATE": ("physical_accuracy", "Jasper"),
}


def raw_manifest() -> dict:
    return yaml.safe_load(MANIFEST_PATH.read_bytes())


def test_the_shipped_manifest_is_exactly_the_pinned_bytes_and_loads():
    assert hashlib.sha256(MANIFEST_PATH.read_bytes()).hexdigest() == NETWORK_RULE_MANIFEST_SHA256
    manifest = manifest_module.load_network_rule_manifest(expected_sha256=NETWORK_RULE_MANIFEST_SHA256)
    assert manifest.sha256 == NETWORK_RULE_MANIFEST_SHA256 and manifest.version == "0.1.0"
    assert {c.rule_id for c in manifest.candidates} >= set(COUNCIL_EXAMPLES)
    # No shipped entry is approved, and none is activatable: that is the owner's signed act, never ours.
    assert [c.activatable for c in manifest.candidates] == [False] * len(manifest.candidates)
    assert all(c.activation_approved is False and c.owner_acceptance is None for c in manifest.candidates)


def test_a_manifest_with_any_other_bytes_is_refused_by_digest():
    data = MANIFEST_PATH.read_bytes()
    with pytest.raises(ManifestError, match="pinned digest"):
        parse_network_rule_manifest_bytes(data + b"\n# edited\n", expected_sha256=NETWORK_RULE_MANIFEST_SHA256)
    assert parse_network_rule_manifest_bytes(data, expected_sha256=NETWORK_RULE_MANIFEST_SHA256).sha256 == (
        NETWORK_RULE_MANIFEST_SHA256
    )


def test_a_manifest_handed_to_a_rule_must_be_the_pinned_one():
    other = parse_network_rule_manifest_bytes(MANIFEST_PATH.read_bytes() + b"\n# edited\n", expected_sha256=None)
    assert other.sha256 != NETWORK_RULE_MANIFEST_SHA256
    with pytest.raises(ValueError, match="not the pinned one"):
        AuditedNetworkRule(other.candidates[0], other)
    shipped = manifest_module.load_network_rule_manifest(expected_sha256=NETWORK_RULE_MANIFEST_SHA256)
    edited = dataclasses.replace(shipped.candidates[0], activation_approved=True, activation_blockers=())
    with pytest.raises(ValueError, match="not an entry of the manifest"):
        AuditedNetworkRule(edited, shipped)  # an entry edited after loading is not the pinned entry, whatever the pin


def test_the_council_examples_are_registered_inactive_with_specific_missing_prerequisites():
    rules = {r.rule_id: r for r in default_rules()}
    for rule_id, (objective, author) in COUNCIL_EXAMPLES.items():
        rule = rules[rule_id]
        entry = rule.describe()
        assert rule.status == RULE_INACTIVE and entry["status"] == "inactive"
        assert entry["objective"] == objective and author in entry["citation"]
        assert entry["activation_approved"] is False and entry["owner_acceptance"] is None
        assert entry["activation_blockers"], rule_id
        assert entry["evidence_needed"], rule_id  # exactly which papers, tables or data would be needed
        assert all(item["source"] and item["items"] for item in entry["evidence_needed"])
        assert any("not approved for activation" in reason for reason in rule.inactive_reasons)
        assert any("predicates are not implemented" in reason for reason in rule.inactive_reasons)
        assert entry["manifest_sha256"] == NETWORK_RULE_MANIFEST_SHA256 and entry["edge_label"]


def test_every_shipped_rule_is_inactive_unapproved_and_unactivatable():
    rules = default_rules()
    assert len(rules) == 6
    validate_rules(rules)  # distinct ids, no empty objective key, a named objective
    for rule in rules:
        assert rule.status != RULE_ACTIVE
        assert rule.objective_key.strip()
        entry = rule.describe()
        assert entry["activation_approved"] is False and entry["activation_blockers"]
    assert {r.objective.value for r in rules} == {"physical_accuracy", "model_fidelity", "representation_fidelity"}


def test_the_specific_blockers_are_named_not_generic():
    by_id = {r.rule_id: {b["id"] for b in r.describe()["activation_blockers"]} for r in default_rules()}
    assert {"compared_objects_are_falloff_fits", "fit_audit_is_partial"} <= by_id["N-AMEDRO-HE-FC"]
    assert "fit_parameters_not_audited" not in by_id["N-AMEDRO-HE-FC"]  # the abstract never carried Fc
    assert {"article_not_read", "reduction_vocabulary_one_open_name"} <= by_id["N-JG-REDUCTION-FIDELITY"]
    assert {"abstract_level_evidence_only", "transfer_treatment_not_typed"} <= by_id["N-JM-CH4-TRANSFER"]
    assert {"experimental_reference_not_read"} <= by_id["N-JM-CH4-RATE"]
    assert {"no_verification_step"} <= by_id["N-ME-CONVERGENCE"] and {"no_pinned_heldout_set"} <= by_id["N-REP-HELDOUT"]


def test_inactive_rules_appear_in_a_decision_with_their_reasons_and_make_no_edge(db_session, world):
    add_solve(db_session, world, fits=[fit_spec("assoc")], protocol=protocol("chemically_significant_eigenvalues"))
    add_solve(db_session, world, fits=[fit_spec("assoc")], protocol=protocol("modified_strong_collision"))
    result = select_network(db_session, request=channel_request(world), require_snapshot=False)  # the shipped registry
    assert result.outcome is Outcome.incomparable_alternatives and result.decision.edges == ()
    ids = {m["rule_id"] for m in result.decision.rule_matches}
    assert set(COUNCIL_EXAMPLES) <= ids
    for match in result.decision.rule_matches:
        assert match["applied"] is False
        if match["level"] == "representation":  # judged on fits only, and only for a representation request
            assert match["why"] == "request_is_not_representation_fidelity"
        else:
            assert match["why"] == "rule_status_inactive" and match["reasons"], match["rule_id"]
    assert {r["rule_id"] for r in result.decision.rules} == ids
    assert "no registered rule compares" in result.decision.basis


def test_a_decision_made_under_the_shipped_registry_replays_and_a_changed_pin_refuses(db_session, world):
    add_solve(db_session, world, fits=[fit_spec("assoc")], protocol=protocol("chemically_significant_eigenvalues"))
    manifest = json.loads(json.dumps(select_network(db_session, request=channel_request(world), require_snapshot=False).manifest))
    assert replay_network(manifest) == manifest["decision"]
    assert {r["manifest_sha256"] for r in manifest["decision"]["rules"]} == {NETWORK_RULE_MANIFEST_SHA256}
    forged = copy.deepcopy(manifest)
    forged["decision"]["rules"][0]["manifest_sha256"] = "0" * 64
    from app.services.network_selection.manifest import manifest_digest

    forged["digest"] = {"algorithm": "sha256", "value": manifest_digest(forged)}
    with pytest.raises(ReplayError, match="different audited manifest"):
        replay_network(forged)


# -- the manifest fails closed ------------------------------------------------------------------------


def entry(raw: dict, **changes) -> dict:
    doc = copy.deepcopy(raw)
    doc["rules"][0].update(changes)
    return doc


def test_an_entry_that_claims_approval_needs_the_owners_acceptance_and_no_blockers():
    raw = raw_manifest()
    with pytest.raises(ManifestError, match="no owner acceptance"):
        parse_network_rule_manifest(entry(raw, activation_approved=True, activation_blockers=[]))
    with pytest.raises(ManifestError, match="blockers are still listed"):
        parse_network_rule_manifest(
            entry(raw, activation_approved=True, owner_acceptance={"accepted_by": "owner", "date": "2026-10-04"})
        )
    approved = parse_network_rule_manifest(
        entry(
            raw,
            activation_approved=True,
            activation_blockers=[],
            owner_acceptance={"accepted_by": "owner", "date": "2026-10-04"},
        )
    )
    assert approved.candidates[0].activatable is True  # the loader can express an approved entry; no shipped one is


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"activation_blockers": []}, "lists no reason"),
        ({"evidence_needed": []}, "lists nothing that would be needed"),
        ({"objective_key": "  "}, "objective_key is empty"),
        ({"objective": "universal_accuracy"}, "unknown objective"),
        ({"activation_approved": "yes"}, "must be true or false"),
        ({"required_predicates": []}, "no predicates"),
    ],
)
def test_an_unusable_entry_is_refused(changes, message):
    with pytest.raises(ManifestError, match=message):
        parse_network_rule_manifest(entry(raw_manifest(), **changes))


def test_duplicate_rule_ids_are_refused():
    raw = raw_manifest()
    raw["rules"].append(copy.deepcopy(raw["rules"][0]))
    with pytest.raises(ManifestError, match="rule ids repeat"):
        parse_network_rule_manifest(raw)


def test_owner_approval_alone_applies_no_edge_until_the_predicates_exist(db_session, world, monkeypatch):
    """Even a fully approved entry is inactive and edge-less while ``predicates_implemented`` is false."""
    raw = entry(
        raw_manifest(),
        activation_approved=True,
        activation_blockers=[],
        owner_acceptance={"accepted_by": "owner", "date": "2026-10-04"},
    )
    data = yaml.safe_dump(raw).encode()
    pinned = hashlib.sha256(data).hexdigest()
    monkeypatch.setattr(rules_module, "NETWORK_RULE_MANIFEST_SHA256", pinned)
    manifest = parse_network_rule_manifest_bytes(data, expected_sha256=pinned)
    rule = AuditedNetworkRule(manifest.candidates[0], manifest)
    assert manifest.candidates[0].activatable is True
    assert rule.status == RULE_INACTIVE and "the rule's predicates are not implemented" in rule.inactive_reasons
    add_solve(db_session, world, fits=[fit_spec("assoc")], protocol=protocol("chemically_significant_eigenvalues"))
    add_solve(db_session, world, fits=[fit_spec("assoc")], protocol=protocol("modified_strong_collision"))
    request = channel_request(world, objective=rule.objective, **_pins(rule, world))
    result = select_network(db_session, request=request, rules=[rule], require_snapshot=False)
    assert result.outcome is Outcome.incomparable_alternatives and result.decision.edges == ()


def _pins(rule, world) -> dict:
    """Whatever the request needs to state the rule's objective."""
    if rule.objective.value == "model_fidelity":
        return {"reference_model_ref": world.solves[0].public_ref}
    if rule.objective.value == "representation_fidelity":
        return {"reference_outputs": "a named dataset"}
    return {}


# -- the review fixes: dated acceptance, independent activatable, per-rule predicates, representation level ---------


def _signed() -> dict:
    return {"accepted_by": "owner", "date": "2026-10-04"}


def test_an_acceptance_without_a_date_or_name_is_refused():
    raw = raw_manifest()
    for acceptance in ({"accepted_by": "owner"}, {"date": "2026-10-04"}, {"accepted_by": "owner", "date": ""}):
        with pytest.raises(ManifestError, match="no owner acceptance"):
            parse_network_rule_manifest(
                entry(raw, activation_approved=True, activation_blockers=[], owner_acceptance=acceptance)
            )


def test_an_unapproved_entry_may_not_carry_an_acceptance():
    with pytest.raises(ManifestError, match="carries an owner acceptance but is not approved"):
        parse_network_rule_manifest(entry(raw_manifest(), owner_acceptance=_signed()))


def test_activatable_checks_blockers_and_the_dated_acceptance_itself():
    """A hand-built entry cannot be activatable by leaving blockers listed or an acceptance undated."""
    shipped = manifest_module.load_network_rule_manifest(expected_sha256=NETWORK_RULE_MANIFEST_SHA256)
    base = shipped.candidates[0]
    assert base.activation_blockers
    ok = dataclasses.replace(base, activation_approved=True, activation_blockers=(), owner_acceptance=_signed())
    assert ok.activatable is True
    assert dataclasses.replace(ok, activation_blockers=base.activation_blockers).activatable is False
    assert dataclasses.replace(ok, owner_acceptance=None).activatable is False
    assert dataclasses.replace(ok, owner_acceptance={"accepted_by": "owner"}).activatable is False
    assert dataclasses.replace(ok, activation_approved=False).activatable is False


def test_the_predicates_flag_is_per_rule_and_approving_one_rule_activates_no_other(monkeypatch):
    raw = raw_manifest()
    for rule in raw["rules"]:
        rule.update(activation_approved=True, activation_blockers=[], owner_acceptance=_signed())
    data = yaml.safe_dump(raw).encode()
    pinned = hashlib.sha256(data).hexdigest()
    monkeypatch.setattr(rules_module, "NETWORK_RULE_MANIFEST_SHA256", pinned)
    manifest = parse_network_rule_manifest_bytes(data, expected_sha256=pinned)
    assert all(c.activatable for c in manifest.candidates)

    def built():
        out = {}
        for c in manifest.candidates:
            cls = rules_module.AuditedNetworkRepresentationRule if c.level == "representation" else AuditedNetworkRule
            out[c.rule_id] = cls(c, manifest)
        return out

    assert {r.status for r in built().values()} == {RULE_INACTIVE}  # approved everywhere, implemented nowhere
    monkeypatch.setattr(rules_module, "RULES_WITH_IMPLEMENTED_PREDICATES", frozenset({"N-ME-CONVERGENCE"}))
    status = {rule_id: rule.status for rule_id, rule in built().items()}
    assert status.pop("N-ME-CONVERGENCE") == RULE_ACTIVE
    assert set(status.values()) == {RULE_INACTIVE} and len(status) == len(manifest.candidates) - 1


def test_the_heldout_rule_is_registered_at_the_representation_level_and_can_never_rank_solves():
    rules = {r.rule_id: r for r in default_rules()}
    heldout = rules["N-REP-HELDOUT"]
    assert heldout.level == "representation" and isinstance(heldout, rules_module.NetworkRepresentationRule)
    assert heldout.objective.value == "representation_fidelity"
    assert [r.rule_id for r in rules.values() if r.level == "representation"] == ["N-REP-HELDOUT"]
    with pytest.raises(NotImplementedError):
        heldout.preferred_side(None, None)  # type: ignore[arg-type]
    with pytest.raises(NotImplementedError):
        heldout.yielding_side(None, None)  # type: ignore[arg-type]


def test_a_representation_level_entry_must_state_the_representation_objective_and_match_its_class():
    with pytest.raises(ManifestError, match="representation-level rule must have"):
        parse_network_rule_manifest(entry(raw_manifest(), level="representation", objective="model_fidelity"))
    with pytest.raises(ManifestError, match="unknown level"):
        parse_network_rule_manifest(entry(raw_manifest(), level="solve"))
    shipped = manifest_module.load_network_rule_manifest(expected_sha256=NETWORK_RULE_MANIFEST_SHA256)
    with pytest.raises(ValueError, match="cannot be built as a candidate-level rule"):
        AuditedNetworkRule(shipped.candidate("N-REP-HELDOUT"), shipped)
    with pytest.raises(ValueError, match="cannot be built as a representation-level rule"):
        rules_module.AuditedNetworkRepresentationRule(shipped.candidate("N-ME-CONVERGENCE"), shipped)


def test_the_amedro_entry_is_anchored_to_two_pinned_open_sources():
    manifest = manifest_module.load_network_rule_manifest(expected_sha256=NETWORK_RULE_MANIFEST_SHA256)
    sources = {s["id"]: s for s in manifest.sources}
    assert sources["AMEDRO_2020"]["sha256"] == "8c4e305bddfe5631c54d1c3685dc2ee3351b0e68677abd70be5e0c08dd45288f"
    assert sources["AMEDRO_2020_SUPPLEMENT"]["sha256"] == (
        "05a0e9b72254aabab8867e2defca6b1873d17ab38ce47f3c60631e33b18be37b"
    )
    assert all(s["used_for"] and s["open_access"] is True for s in sources.values())
    amedro = manifest.candidate("N-AMEDRO-HE-FC")
    assert set(amedro.source_ids) == set(sources) and amedro.open_access is True
    where = " ".join(a["where"] for a in amedro.anchors)
    assert "p. 3095" in where and "Fig. S2" in where and "Table 1" in where
    assert manifest.candidate("N-JG-REDUCTION-FIDELITY").open_access is True
    assert manifest.candidate("N-JM-CH4-TRANSFER").open_access is False


def test_a_rule_citing_an_unpinned_source_or_a_source_without_a_digest_is_refused():
    raw = raw_manifest()
    raw["rules"][0]["source_ids"] = ["NOT_PINNED"]
    with pytest.raises(ManifestError, match="does not pin"):
        parse_network_rule_manifest(raw)
    raw = raw_manifest()
    del raw["sources"][0]["sha256"]
    with pytest.raises(ManifestError, match="lacks an id, citation, sha256 or used_for"):
        parse_network_rule_manifest(raw)
    raw = raw_manifest()
    raw["sources"].append(copy.deepcopy(raw["sources"][0]))
    with pytest.raises(ManifestError, match="source ids repeat"):
        parse_network_rule_manifest(raw)
