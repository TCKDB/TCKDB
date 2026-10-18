"""Structure rule manifest 0.2.0 and the dual pin: 0.1.0 stays loadable for replay only.

A recorded decision names (rule, version, manifest digest) and replay holds all three. Manifest 0.2.0 changed every
entry, so the live registry carries 0.2.0 and the 0.1.0 entries rest on the preserved 0.1.0 bytes, kept for replay and
labelled historical, exactly as the kinetics XYG3 rule did for its 0.3.0 manifest. ``golden/structure_manifest_rules_0_1_0.json``
is a manifest recorded under rule version 0.1.0 before the move; it is never regenerated.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from app.chemistry.structure_rules.manifest import MANIFEST_0_1_0_PATH, MANIFEST_PATH
from app.services.structure_selection import (
    ReplayError,
    replay_matches,
    replay_provenance,
    replay_structure_decision,
    select_entry_structures,
)
from app.services.structure_selection import rules as rules_module
from app.services.structure_selection.rules import (
    RULES_WITH_IMPLEMENTED_PREDICATES,
    STRUCTURE_RULE_MANIFEST_SHA256,
    STRUCTURE_RULE_VERSION_PINS,
    default_rules,
    replay_rules,
)
from tests.services.structure_selection.test_manifest import reseal
from tests.services.structure_selection.test_rule_audit import RULE_IDS, _protocol_request, _two_protocols

GOLDEN = Path(__file__).parent / "golden" / "structure_manifest_rules_0_1_0.json"
PIN_0_1_0 = "cff519d81754e1cb42ac52d86d6a9988b7702455cc96d699f3d7c3413e535048"
ACONFL = ("S-ACONFL-LNO-LARGE-BASIS", "S-ACONFL-DLPNO-F12-VERYTIGHT")

#: Written out here, not imported, so editing the manifest and its definition together cannot hide a change.
EVIDENCE = {
    "E1": "A numeric uncertainty for the reference values, per member or as a stated set-level bound, with its source.",
    "E2": "Independence: the reference shares no computed component with the candidate, shown from recorded composition, not asserted.",
    "E3": "A resolved margin: for superiority, the paired difference in the error metric between two candidates exceeds its combined uncertainty (reference uncertainty plus paired sampling uncertainty by a stated method with stated n). For a near-tie ranking, every adjacent pair in the ranking is resolved this way; unresolved neighbours are reported as indistinguishable, not ordered.",
    "E4": "Coverage and sameness: the full named member set, the same geometries for both candidates, the same named reference conformer as the source.",
    "E5": "Domain: the claim is scoped to the member set measured; transfer to other molecules needs evidence on them.",
    "E6": "Established protocol facts: every setting that defines the protocol (thresholds, PNO cut-offs, extrapolation, core treatment, corrections) is typed and established, not read from a label.",
    "E7": "Owner acceptance of the rule that would carry the claim, and its predicates implemented and reviewed.",
}


def _golden() -> dict:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def _rule_entries(manifest: dict) -> list[dict]:
    return manifest["decision"]["protocol"]["rules"]


# -- the version <-> pin ledger -----------------------------------------------------------------------------------


def test_each_rule_version_is_tied_to_one_manifest_digest_and_moving_a_pin_needs_a_new_version():
    """The ledger is written out here, so editing a pin together with its test cannot hide a move."""
    ledger = {
        "0.1.0": "cff519d81754e1cb42ac52d86d6a9988b7702455cc96d699f3d7c3413e535048",
        "0.2.0": "e8c25422f4192b2ba505321959b6be6afe943516bcbb224c2445cdc6b6c054f4",
    }
    assert STRUCTURE_RULE_VERSION_PINS == ledger
    assert STRUCTURE_RULE_MANIFEST_SHA256 == ledger["0.2.0"]
    # The file on disk hashes to the live pin: editing the manifest without moving the pin fails here.
    assert hashlib.sha256(MANIFEST_PATH.read_bytes()).hexdigest() == ledger["0.2.0"]
    assert hashlib.sha256(MANIFEST_0_1_0_PATH.read_bytes()).hexdigest() == ledger["0.1.0"]
    for rule in replay_rules():
        assert rule.describe()["manifest_sha256"] == ledger[rule.version], (rule.rule_id, rule.version)
    assert {r.version for r in replay_rules()} == set(ledger)


# -- new decisions are 0.2.0; 0.1.0 is replay only ---------------------------------------------------------------------


def test_the_live_registry_carries_only_0_2_0_and_replay_adds_0_1_0_for_the_same_rules():
    live = {(r.rule_id, r.version) for r in default_rules()}
    assert live == {(rule_id, "0.2.0") for rule_id in RULE_IDS}
    replayable = {(r.rule_id, r.version) for r in replay_rules()}
    assert replayable == live | {(rule_id, "0.1.0") for rule_id in RULE_IDS}
    assert {r.describe()["manifest_version"] for r in default_rules()} == {"0.2.0"}


def test_a_decision_made_now_carries_0_2_0_and_its_digest_and_is_labelled_current(db_session):
    world = _two_protocols(db_session)
    result = select_entry_structures(
        db_session, entry_id=world.entry.id, request=_protocol_request(), require_snapshot=False
    )
    entries = _rule_entries(result.manifest)
    assert {e["rule_id"] for e in entries} == RULE_IDS
    assert {e["version"] for e in entries} == {"0.2.0"}
    assert {e["manifest_sha256"] for e in entries} == {STRUCTURE_RULE_VERSION_PINS["0.2.0"]}
    assert replay_matches(result.manifest)
    label = replay_provenance(result.manifest)
    assert label["kind"] == "current" and label["superseded_rule_versions"] == [] and label["reassessed"] is False


# -- a decision recorded under 0.1.0 still replays ----------------------------------------------------------------------


def test_the_golden_manifest_really_was_recorded_under_0_1_0():
    old = _golden()
    entries = _rule_entries(old)
    assert {e["rule_id"] for e in entries} == RULE_IDS
    assert {e["version"] for e in entries} == {"0.1.0"}
    assert {e["manifest_sha256"] for e in entries} == {PIN_0_1_0}


def test_a_structure_manifest_recorded_under_0_1_0_replays_and_is_labelled_historical():
    old = _golden()
    assert replay_matches(old)
    assert replay_structure_decision(old) == old["decision"]
    label = replay_provenance(old)
    assert label["kind"] == "historical" and label["reassessed"] is False
    assert set(label["superseded_rule_versions"]) == {f"{rule_id} 0.1.0" for rule_id in RULE_IDS}
    assert "kept for replay only" in label["statement"] and "0.1.0" in label["statement"]


def test_the_0_1_0_rules_are_replay_only_inactive_and_unapproved():
    old = [r for r in replay_rules() if r.version == "0.1.0"]
    assert {r.rule_id for r in old} == RULE_IDS
    assert not ({(r.rule_id, r.version) for r in old} & {(r.rule_id, r.version) for r in default_rules()})
    for rule in old:
        described = rule.describe()
        assert rule.status == "inactive" and described["activation_approved"] is False
        assert described["manifest_version"] == "0.1.0"
    # The old blocker id is part of the 0.1.0 record and stays there; the live record has the renamed one.
    old_ids = {b["id"] for r in old for b in r.describe()["activation_blockers"]}
    new_ids = {b["id"] for r in default_rules() for b in r.describe()["activation_blockers"]}
    assert "relative_energy_is_not_a_supported_quantity" in old_ids
    assert "relative_energy_is_not_a_supported_quantity" not in new_ids


def test_a_0_1_0_entry_naming_the_current_digest_is_refused():
    forged = _golden()
    _rule_entries(forged)[0]["manifest_sha256"] = STRUCTURE_RULE_MANIFEST_SHA256  # 0.1.0 does not rest on 0.2.0's bytes
    with pytest.raises(ReplayError) as exc:
        replay_structure_decision(reseal(forged))
    assert exc.value.code == "rule_manifest_changed"


@pytest.mark.parametrize("version", ["0.1.9", "0.3.0", "9.9.9", ""])
def test_a_version_that_never_existed_is_still_refused_naming_it(version):
    renamed = copy.deepcopy(_golden())
    _rule_entries(renamed)[0]["version"] = version
    with pytest.raises(ReplayError, match="rule_unavailable") as exc:
        replay_structure_decision(reseal(renamed))
    assert exc.value.code == "rule_unavailable" and version in str(exc.value)


def test_the_loader_refuses_the_preserved_bytes_under_the_wrong_pin():
    from app.chemistry.structure_rules.manifest import ManifestError, load_structure_rule_manifest

    with pytest.raises(ManifestError, match="pinned digest"):
        load_structure_rule_manifest(expected_sha256=STRUCTURE_RULE_MANIFEST_SHA256, path=MANIFEST_0_1_0_PATH)
    with pytest.raises(ManifestError, match="pinned digest"):
        load_structure_rule_manifest(expected_sha256=PIN_0_1_0)


# -- nothing is activated -------------------------------------------------------------------------------------------


def test_no_rule_of_either_version_is_activated_and_no_predicate_is_implemented():
    assert RULES_WITH_IMPLEMENTED_PREDICATES == frozenset()
    assert rules_module.RULES_WITH_IMPLEMENTED_PREDICATES == frozenset()
    for rule in replay_rules():
        described = rule.describe()
        assert described["activation_approved"] is False and described["owner_acceptance"] is None, rule.rule_id
        assert rule.status == "inactive" and rule.predicates_implemented is False, rule.rule_id
    for path in (MANIFEST_PATH, MANIFEST_0_1_0_PATH):
        raw = yaml.safe_load(path.read_bytes())
        assert {r["activation_approved"] for r in raw["rules"]} == {False}
        assert {r["owner_acceptance"] for r in raw["rules"]} == {None}


# -- what 0.2.0 says -------------------------------------------------------------------------------------------------------


def _blockers(rule_id: str) -> dict[str, dict]:
    rule = next(r for r in default_rules() if r.rule_id == rule_id)
    return {b["id"]: b for b in rule.describe()["activation_blockers"]}


@pytest.mark.parametrize("rule_id", ACONFL)
def test_the_new_blocker_lists_exactly_e1_to_e7_with_their_definitions(rule_id):
    blocker = _blockers(rule_id)["descriptive_mad_is_not_preference_evidence"]
    needed = blocker["evidence_needed"]
    assert [n["id"] for n in needed] == ["E1", "E2", "E3", "E4", "E5", "E6", "E7"]
    assert {n["id"]: n["text"] for n in needed} == EVIDENCE
    assert "2026-10-13" in blocker["text"]


def test_the_fixed_model_rule_has_no_e1_to_e7_blocker_but_records_the_declined_universal_form():
    blockers = _blockers("S-FIXED-MODEL-CONVERGENCE")
    assert "descriptive_mad_is_not_preference_evidence" not in blockers
    assert "not a universal tighter-settings rule" in blockers["owner_declined_universal_form"]["text"]


@pytest.mark.parametrize("rule_id", ACONFL)
def test_the_relative_energy_blocker_now_says_what_still_blocks(rule_id):
    blockers = _blockers(rule_id)
    assert "relative_energy_is_not_a_supported_quantity" not in blockers
    text = blockers["no_preference_stage_consumes_relative_conformer_energy"]["text"]
    assert "structure_energy" in text and "never builds an edge" in text
    assert "relative_conformer_energy" in text


@pytest.mark.parametrize("rule_id", ACONFL)
def test_the_owner_decision_items_are_marked_answered_with_the_decision_and_its_date(rule_id):
    rule = next(r for r in default_rules() if r.rule_id == rule_id)
    owner = [e for e in rule.describe()["evidence_needed"] if e["source"] == "owner decision"]
    assert len(owner) == 1
    assert owner[0]["status"] == "answered" and owner[0]["answered_on"] == "2026-10-13"
    assert "owner decision D" in owner[0]["answered_by"]
    # Every other evidence item is still open: only the owner item is answered.
    assert all("status" not in e for e in rule.describe()["evidence_needed"] if e["source"] != "owner decision")


def test_the_history_records_decision_d_and_every_rule_entry_is_0_2_0():
    raw = yaml.safe_load(MANIFEST_PATH.read_bytes())
    assert raw["manifest_version"] == "0.2.0"
    assert [h["version"] for h in raw["history"]] == ["0.1.0", "0.2.0"]
    change = raw["history"][1]["change"]
    assert "2026-10-13" in change and "named-reference operation" in change
    assert {r["version"] for r in raw["rules"]} == {"0.2.0"}


def test_the_source_is_corrected_and_recorded_as_given_not_fetched():
    raw = yaml.safe_load(MANIFEST_PATH.read_bytes())
    source = next(s for s in raw["sources"] if s["id"] == "SANTRA_MARTIN_2022_OA_REPRINT")
    assert source["read_from"] == "https://webhome.weizmann.ac.il/home/comartin/OAreprints/320.pdf"
    assert source["sha256"] == "ef5ebc990d49a2b2569e4826966b20203206002024d4b9493c88871540bb190e"
    assert "not fetched" in source["accessed"] and "Table 4" in source["used_for"] and "p. 9378" in source["used_for"]
    for rule_id in ACONFL:
        assert "SANTRA_MARTIN_2022_OA_REPRINT" in next(r for r in raw["rules"] if r["rule_id"] == rule_id)["source_ids"]


def test_the_aconf20_footnote_marker_is_a_noted_discrepancy_that_follows_neither_reading():
    manifest = next(iter(default_rules()))._manifest  # type: ignore[attr-defined]
    [item] = manifest.noted_discrepancies
    assert item["id"] == "ACONF20_REFERENCE_FOOTNOTE_MARKER" and item["resolved"] is False
    assert set(item["affects"]) == set(ACONFL)
    assert "b" in item["observation"] and "c" in item["observation"] and "p. 9382" in item["observation"]
    assert "most likely a typesetting slip" in item["reading"] and "does not decide it" in item["reading"]
    assert "noted_discrepancies" in _blockers(ACONFL[0])["reference_is_silver_and_partly_circular"]["text"]
