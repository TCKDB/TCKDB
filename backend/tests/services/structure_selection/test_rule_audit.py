"""The audited council examples: registered, pinned, and INACTIVE, each with what is missing.

Nothing here activates a rule, and the tests are written so that nothing could: the manifest is pinned by digest, an
entry that claims approval must carry the owner's acceptance and no blockers, an approved entry still applies no edge
until its predicates exist, and the shipped entries are all unapproved with specific missing prerequisites. The relative
conformer-energy rules rest on Santra and Martin (2022); what was read of it, from which version, is pinned in the
manifest and checked here by the same structural rules as the other audited manifests.
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib

import pytest
import yaml

from app.chemistry.structure_rules import manifest as manifest_module
from app.chemistry.structure_rules.manifest import (
    MANIFEST_PATH,
    ManifestError,
    parse_structure_rule_manifest,
    parse_structure_rule_manifest_bytes,
)
from app.services.structure_selection import (
    Grain,
    Intent,
    ReplayError,
    StructureRequest,
    replay_matches,
    replay_structure_decision,
    select_entry_structures,
)
from app.services.structure_selection import rules as rules_module
from app.services.structure_selection.models import Objective
from app.services.structure_selection.models import StructureOutcome as O
from app.services.structure_selection.rules import (
    RULE_ACTIVE,
    RULE_INACTIVE,
    STRUCTURE_RULE_MANIFEST_SHA256,
    AuditedStructureRule,
    default_rules,
    validate_rules,
)
from tests.services.scientific_read._factories import make_geometry, make_lot
from tests.services.structure_selection._support import make_world
from tests.services.structure_selection.test_manifest import DECL_B, reseal
from tests.services.structure_selection.test_service import build_basin

RULE_IDS = {"S-ACONFL-LNO-LARGE-BASIS", "S-ACONFL-DLPNO-F12-VERYTIGHT", "S-FIXED-MODEL-CONVERGENCE"}
SANTRA_SHA256 = {
    "SANTRA_MARTIN_2022_PUBLISHED_TEXT": "44f452bb502bc81f58a3e7ab27f2dc4577253417d5bf5222721ffd1f7ab85207",
    "SANTRA_MARTIN_2022_ARXIV_V2": "897ccc1a10d44ec963fa1df5eb2c9ebc068495dd25b386f15c92797f9eafb11d",
    "SANTRA_MARTIN_2022_SI_PDF": "3d0329d9c4f512b16aad64adbc9c17e120bced9979e531d5299444fa4626246f",
    "SANTRA_MARTIN_2022_SI_DATA": "dc0a4157b28507c983fcd0ab9243670e3540eae5b0139bbcff1127845e6f99b5",
}


def raw_manifest() -> dict:
    return yaml.safe_load(MANIFEST_PATH.read_bytes())


def shipped():
    return manifest_module.load_structure_rule_manifest(expected_sha256=STRUCTURE_RULE_MANIFEST_SHA256)


def entry(raw: dict, **changes) -> dict:
    doc = copy.deepcopy(raw)
    doc["rules"][0].update(changes)
    return doc


def _signed() -> dict:
    return {"accepted_by": "owner", "date": "2026-10-05"}


# -- the pin ---------------------------------------------------------------------------------------------


def test_the_shipped_manifest_is_exactly_the_pinned_bytes_and_loads():
    assert hashlib.sha256(MANIFEST_PATH.read_bytes()).hexdigest() == STRUCTURE_RULE_MANIFEST_SHA256
    manifest = shipped()
    assert manifest.sha256 == STRUCTURE_RULE_MANIFEST_SHA256 and manifest.version == "0.1.0"
    assert {c.rule_id for c in manifest.candidates} == RULE_IDS
    # No shipped entry is approved, and none is activatable: that is the owner's signed act, never ours.
    assert [c.activatable for c in manifest.candidates] == [False] * len(manifest.candidates)
    assert all(c.activation_approved is False and c.owner_acceptance is None for c in manifest.candidates)


def test_a_manifest_with_any_other_bytes_is_refused_by_digest():
    data = MANIFEST_PATH.read_bytes()
    with pytest.raises(ManifestError, match="pinned digest"):
        parse_structure_rule_manifest_bytes(data + b"\n# edited\n", expected_sha256=STRUCTURE_RULE_MANIFEST_SHA256)
    assert (
        parse_structure_rule_manifest_bytes(data, expected_sha256=STRUCTURE_RULE_MANIFEST_SHA256).sha256
        == STRUCTURE_RULE_MANIFEST_SHA256
    )


def test_a_manifest_handed_to_a_rule_must_be_the_pinned_one():
    other = parse_structure_rule_manifest_bytes(MANIFEST_PATH.read_bytes() + b"\n# edited\n", expected_sha256=None)
    assert other.sha256 != STRUCTURE_RULE_MANIFEST_SHA256
    with pytest.raises(ValueError, match="not the pinned one"):
        AuditedStructureRule(other.candidates[0], other)
    manifest = shipped()
    edited = dataclasses.replace(manifest.candidates[0], activation_approved=True, activation_blockers=())
    with pytest.raises(ValueError, match="not an entry of the manifest"):
        AuditedStructureRule(edited, manifest)  # an entry edited after loading is not the pinned entry, whatever the pin


# -- what is registered ----------------------------------------------------------------------------------


def test_every_shipped_rule_is_inactive_unapproved_and_unactivatable_with_specific_missing_prerequisites():
    rules = default_rules()
    assert {r.rule_id for r in rules} == RULE_IDS
    validate_rules(rules)
    for rule in rules:
        entry_ = rule.describe()
        assert rule.status == RULE_INACTIVE and entry_["status"] == "inactive" and rule.status != RULE_ACTIVE
        assert entry_["activation_approved"] is False and entry_["owner_acceptance"] is None
        assert entry_["activation_blockers"] and entry_["evidence_needed"], rule.rule_id
        assert all(item["source"] and item["items"] for item in entry_["evidence_needed"])
        # Exact entries, not substrings: a blocker's own text also says "not approved" and "not implemented".
        assert "not approved for activation by the owner" in rule.inactive_reasons
        assert "the rule's predicates are not implemented" in rule.inactive_reasons
        assert entry_["manifest_sha256"] == STRUCTURE_RULE_MANIFEST_SHA256 and entry_["edge_label"]
        # The scope is stated: where each rule stops is written down, not implied.
        assert all(entry_["scope"][k] for k in ("observable", "chemistry", "state_and_geometry", "domain"))
        assert entry_["protocols"]["preferred"] and entry_["protocols"]["yielding"]


def test_the_aconfl_rules_are_narrowly_scoped_to_relative_conformer_energies_of_the_three_alkanes():
    by_id = {r.rule_id: r.describe() for r in default_rules()}
    for rule_id in ("S-ACONFL-LNO-LARGE-BASIS", "S-ACONFL-DLPNO-F12-VERYTIGHT"):
        e = by_id[rule_id]
        assert e["objective"] == "expected_accuracy" and e["observable"] == "relative_conformer_energy"
        assert "n-dodecane" in e["scope"]["chemistry"] and "n-hexadecane" in e["scope"]["chemistry"]
        assert "n-icosane" in e["scope"]["chemistry"] and "only" in e["scope"]["chemistry"]
        assert "no other alkane" in e["scope"]["domain"] or "named dataset members only" in e["scope"]["domain"]
        assert "Santra" in e["citation"] and "10.1021/acs.jpca.2c06407" in e["citation"]
        assert any("expected accuracy" in p for p in e["required_predicates"])
        assert any("Any molecule" in x for x in e["exceptions"])
    lno = by_id["S-ACONFL-LNO-LARGE-BASIS"]
    assert "named reference conformer" in lno["scope"]["observable"].replace("NAMED", "named")
    assert "ref 24" in lno["scope"]["state_and_geometry"]


def test_the_audit_findings_are_recorded_as_specific_blockers():
    by_id = {r.rule_id: {b["id"] for b in r.describe()["activation_blockers"]} for r in default_rules()}
    lno = by_id["S-ACONFL-LNO-LARGE-BASIS"]
    # The reference for two of the three sets is the protocol the rule prefers: circular, and said so.
    assert {
        "reference_is_silver_and_partly_circular",
        "no_numeric_reference_uncertainty",
        "mad_is_not_a_per_conformer_error",
        "relative_energy_is_not_a_supported_quantity",
        "core_correlation_and_corrections_not_stated",
        "geometry_level_not_stated",
        "localized_orbital_settings_need_vocabulary",
        "domain_is_three_molecules",
        "owner_acceptance_and_predicates",
    } <= lno
    dlpno = by_id["S-ACONFL-DLPNO-F12-VERYTIGHT"]
    assert {
        "reference_is_silver_and_not_canonical_beyond_dodecane",
        "no_numeric_reference_uncertainty",
        "program_specific_thresholds",
        "relative_energy_is_not_a_supported_quantity",
    } <= dlpno
    assert {"no_source", "no_pinned_reference_in_the_store", "setting_names_do_not_imply_accuracy"} <= by_id[
        "S-FIXED-MODEL-CONVERGENCE"
    ]
    text = {b["id"]: b["text"] for b in next(r for r in default_rules() if r.rule_id == "S-ACONFL-LNO-LARGE-BASIS").describe()["activation_blockers"]}
    assert "HLC14" in text["reference_is_silver_and_partly_circular"] and "0.31" in text["no_numeric_reference_uncertainty"]


def test_the_fixed_model_convergence_form_has_no_source_and_never_a_tighter_keyword_edge():
    rule = next(r for r in default_rules() if r.rule_id == "S-FIXED-MODEL-CONVERGENCE")
    e = rule.describe()
    assert e["objective"] == "model_fidelity" and e["source_ids"] == [] and e["anchors"] == []
    assert any("pinned reference" in p for p in e["required_predicates"])
    assert "tighter keyword" in e["council_observation"] or "tighter keywords" in e["council_observation"]


def test_the_sources_are_pinned_by_sha256_with_what_each_was_used_for_and_the_version_read():
    manifest = shipped()
    sources = {s["id"]: s for s in manifest.sources}
    assert {k: sources[k]["sha256"] for k in SANTRA_SHA256} == SANTRA_SHA256
    for source in sources.values():
        assert source["used_for"] and source["version"] and len(source["sha256"]) == 64
        assert source["open_access"] is True
    # Which version each number came from, and what was not obtained, is stated where the reader will see it.
    published = sources["SANTRA_MARTIN_2022_PUBLISHED_TEXT"]
    assert "published" in published["version"] and "JATS" in published["version"]
    assert "arXiv v2 PDF" in published["used_for"]
    assert "arXiv v2 p." in sources["SANTRA_MARTIN_2022_ARXIV_V2"]["version"] or "arXiv v2 p. N" in sources["SANTRA_MARTIN_2022_ARXIV_V2"]["version"]
    assert "NOT opened" in sources["SANTRA_MARTIN_2022_SI_DATA"]["used_for"]
    assert "NOT extracted" in sources["SANTRA_MARTIN_2022_SI_PDF"]["used_for"]
    header = MANIFEST_PATH.read_text().split("manifest_version")[0]
    assert "published PDF could not be downloaded" in header


def test_every_quoted_number_is_anchored_to_a_page_table_or_figure():
    manifest = shipped()
    for rule in manifest.candidates:
        if rule.source_ids:
            assert rule.anchors, rule.rule_id
            for anchor in rule.anchors:
                assert any(w in anchor["where"] for w in ("p. ", "pp. ", "Table", "Results", "Introduction", "abstract")), anchor
    lno = manifest.candidate("S-ACONFL-LNO-LARGE-BASIS")
    where = " ".join(a["where"] for a in lno.anchors)
    assert "Table 5, arXiv v2 p. 14" in where and "Table 4" in where and "Table 3" in where and "p. 26" in where
    claims = " ".join(a["claim"] for a in lno.anchors)
    assert "0.04 / 0.01 / 0.03 / 0.08" in claims and "0.95 / 0.77 / 0.98 / 1.03" in claims and "+0.60" in claims and "0.31" in claims
    dlpno = manifest.candidate("S-ACONFL-DLPNO-F12-VERYTIGHT")
    assert "Table 6, arXiv v2 p. 19" in " ".join(a["where"] for a in dlpno.anchors)
    assert "0.04 (ACONFL)" in " ".join(a["claim"] for a in dlpno.anchors)


def test_the_record_says_exactly_what_the_article_says_and_no_more():
    """Wording a reviewer corrected: each is a statement the record must not drift back to."""
    manifest = shipped()
    lno = manifest.candidate("S-ACONFL-LNO-LARGE-BASIS")
    text = " ".join([*lno.protocols["yielding"], lno.proposed_statement])
    assert "looser" not in text and "any other threshold" in text and "vvTight" in text  # vvTight is tighter, not looser
    blockers = {b["id"]: b["text"] for b in lno.activation_blockers}
    circular = blockers["reference_is_silver_and_partly_circular"]
    assert "near-zero" not in circular and "HLC14" in circular and "0.06 and 0.09" in circular and "0.05 on" in circular
    assert "same calculation one preferred" in circular  # the correction's localized part, not the full protocol
    dlpno = manifest.candidate("S-ACONFL-DLPNO-F12-VERYTIGHT")
    assert "(T) unscaled" not in " ".join(dlpno.protocols["preferred"] + dlpno.protocols["yielding"])
    scaling = {b["id"]: b["text"] for b in dlpno.activation_blockers}["t_scaling_row_labels"]
    assert "(T1s)" in scaling and "p. 18" in scaling and "1.1413" in scaling
    where = {a["claim"][:40]: a["where"] for a in dlpno.anchors}
    fortuitous = next(a for a in dlpno.anchors if "fortuitous" in a["claim"])
    assert "arXiv v2 p. 18" in fortuitous["where"], where
    sources = {s["id"]: s for s in manifest.sources}
    assert sources["SANTRA_MARTIN_2022_PUBLISHED_TEXT"]["license"].startswith("CC BY 4.0")
    needed = " ".join(e["source"] for c in manifest.candidates for e in c.evidence_needed)
    assert "Ehlert" in needed and "Closed access" in needed and "10.26434/chemrxiv-2022-cfqhh" in needed and "CC BY-NC 4.0" in needed


def test_the_council_examples_that_are_not_preferences_are_recorded_as_such():
    declined = {d["id"] for d in shipped().considered_not_rules}
    assert declined == {"ORCA_THERMOCHEMISTRY_DECOMPOSITION", "ORCA_GOAT_ENSEMBLE", "TS_MODE_AND_IRC_CHARACTERIZATION"}
    assert all(d["why"] for d in shipped().considered_not_rules)


# -- the manifest fails closed ---------------------------------------------------------------------------


def test_an_entry_that_claims_approval_needs_the_owners_acceptance_and_no_blockers():
    raw = raw_manifest()
    with pytest.raises(ManifestError, match="no owner acceptance"):
        parse_structure_rule_manifest(entry(raw, activation_approved=True, activation_blockers=[]))
    with pytest.raises(ManifestError, match="blockers are still listed"):
        parse_structure_rule_manifest(entry(raw, activation_approved=True, owner_acceptance=_signed()))
    approved = parse_structure_rule_manifest(
        entry(raw, activation_approved=True, activation_blockers=[], owner_acceptance=_signed())
    )
    assert approved.candidates[0].activatable is True  # the loader can express an approved entry; no shipped one is


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"activation_blockers": []}, "lists no reason"),
        ({"evidence_needed": []}, "lists nothing that would be needed"),
        ({"objective_key": "  "}, "objective_key is empty"),
        ({"observable": ""}, "observable is empty"),
        ({"objective": "universal_accuracy"}, "unknown objective"),
        ({"activation_approved": "yes"}, "must be true or false"),
        ({"required_predicates": []}, "no predicates"),
        ({"exceptions": []}, "no exceptions"),
        ({"protocols": {"preferred": ["a"], "yielding": []}}, "both its preferred and its yielding side"),
        ({"scope": {"observable": "x", "chemistry": "x", "state_and_geometry": "x"}}, "scope.domain is not stated"),
        ({"anchors": []}, "anchors no claim"),
        ({"anchors": [{"claim": "a number", "where": ""}]}, "no claim or no location"),
        ({"source_ids": ["NOT_PINNED"]}, "does not pin"),
    ],
)
def test_an_unusable_entry_is_refused(changes, message):
    with pytest.raises(ManifestError, match=message):
        parse_structure_rule_manifest(entry(raw_manifest(), **changes))


def test_a_source_must_be_pinned_with_a_digest_a_version_and_a_use():
    for key in ("sha256", "version", "used_for"):
        raw = raw_manifest()
        raw["sources"][0][key] = ""
        with pytest.raises(ManifestError, match="lacks"):
            parse_structure_rule_manifest(raw)
    raw = raw_manifest()
    raw["sources"][0]["sha256"] = "abc"
    with pytest.raises(ManifestError, match="64 hex"):
        parse_structure_rule_manifest(raw)


def test_duplicate_rule_ids_and_an_acceptance_on_an_unapproved_entry_are_refused():
    raw = raw_manifest()
    raw["rules"].append(copy.deepcopy(raw["rules"][0]))
    with pytest.raises(ManifestError, match="rule ids repeat"):
        parse_structure_rule_manifest(raw)
    with pytest.raises(ManifestError, match="carries an owner acceptance but is not approved"):
        parse_structure_rule_manifest(entry(raw_manifest(), owner_acceptance=_signed()))
    for acceptance in ({"accepted_by": "owner"}, {"date": "2026-10-05"}, {"accepted_by": "owner", "date": ""}):
        with pytest.raises(ManifestError, match="no owner acceptance"):
            parse_structure_rule_manifest(
                entry(raw_manifest(), activation_approved=True, activation_blockers=[], owner_acceptance=acceptance)
            )


def test_activatable_checks_blockers_and_the_dated_acceptance_itself():
    """A hand-built entry cannot be activatable by leaving blockers listed or an acceptance undated."""
    base = shipped().candidates[0]
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
    monkeypatch.setattr(rules_module, "STRUCTURE_RULE_MANIFEST_SHA256", pinned)
    manifest = parse_structure_rule_manifest_bytes(data, expected_sha256=pinned)
    assert all(c.activatable for c in manifest.candidates)

    def built():
        return {c.rule_id: AuditedStructureRule(c, manifest) for c in manifest.candidates}

    assert {r.status for r in built().values()} == {RULE_INACTIVE}  # approved everywhere, implemented nowhere
    monkeypatch.setattr(rules_module, "RULES_WITH_IMPLEMENTED_PREDICATES", frozenset({"S-ACONFL-LNO-LARGE-BASIS"}))
    status = {rule_id: rule.status for rule_id, rule in built().items()}
    assert status.pop("S-ACONFL-LNO-LARGE-BASIS") == RULE_ACTIVE
    assert [r.rule_id for r in rules_module.active_rules(tuple(built().values()))] == ["S-ACONFL-LNO-LARGE-BASIS"]
    assert set(status.values()) == {RULE_INACTIVE} and len(status) == len(manifest.candidates) - 1
    # A model-fidelity rule names its pinned reference model to be active; the audited entry names none, so it cannot
    # be activated by approval and an implemented predicate alone.
    monkeypatch.setattr(rules_module, "RULES_WITH_IMPLEMENTED_PREDICATES", frozenset({"S-FIXED-MODEL-CONVERGENCE"}))
    with pytest.raises(ValueError, match="names no reference model"):
        rules_module.active_rules(tuple(built().values()))


# -- in a decision -----------------------------------------------------------------------------------------


def _two_protocols(db_session):
    world = make_world(db_session)
    other_lot = make_lot(db_session)
    first, second = make_geometry(db_session), make_geometry(db_session)
    build_basin(world, energy=-76.40, geometry=first)
    build_basin(world, energy=-76.41, geometry=second)
    build_basin(world, energy=-76.50, geometry=first, lot=other_lot, declared=DECL_B)
    build_basin(world, energy=-76.51, geometry=second, lot=other_lot, declared=DECL_B)
    world.settle()
    return world


def _protocol_request() -> StructureRequest:
    return StructureRequest(Grain.conformer, Intent.protocol_preferred, objective=Objective.expected_accuracy)


def test_the_shipped_registry_appears_in_a_decision_with_its_reasons_and_makes_no_edge(db_session):
    world = _two_protocols(db_session)
    result = select_entry_structures(
        db_session, entry_id=world.entry.id, request=_protocol_request(), require_snapshot=False
    )
    assert result.outcome is O.incomparable_alternatives and result.decision.protocol["edges"] == []
    matches = {m["rule_id"]: m for m in result.decision.protocol["rule_matches"]}
    assert set(matches) == RULE_IDS
    for match in matches.values():
        assert match["applied"] is False and match["why"] == "rule_status_inactive" and match["reasons"]
    assert {r["rule_id"] for r in result.decision.protocol["rules"]} == RULE_IDS
    assert {r["manifest_sha256"] for r in result.decision.protocol["rules"]} == {STRUCTURE_RULE_MANIFEST_SHA256}


def test_a_decision_made_under_the_shipped_registry_replays_and_a_changed_pin_refuses(db_session):
    world = _two_protocols(db_session)
    result = select_entry_structures(
        db_session, entry_id=world.entry.id, request=_protocol_request(), require_snapshot=False
    )
    manifest = result.manifest
    assert replay_matches(manifest)
    forged = copy.deepcopy(manifest)
    forged["decision"]["protocol"]["rules"][0]["manifest_sha256"] = "0" * 64
    with pytest.raises(ReplayError) as exc:
        replay_structure_decision(reseal(forged))
    assert exc.value.code == "rule_manifest_changed"
    renamed = copy.deepcopy(manifest)
    renamed["decision"]["protocol"]["rules"][0]["version"] = "9.9.9"
    with pytest.raises(ReplayError) as exc:
        replay_structure_decision(reseal(renamed))
    assert exc.value.code == "rule_unavailable"
