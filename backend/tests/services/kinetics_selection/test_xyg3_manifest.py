"""The audited XYG3 barrier manifest: complete by listing, pinned by digest, and not approved.

Membership and activation are separate facts. The manifest lists every reaction the sources print and loads; it
also states why the rule is not activatable, and the loader refuses any document whose claims disagree.
"""

from __future__ import annotations

import copy
import hashlib
import re

import pytest
import yaml
from rdkit import Chem
from rdkit.Chem import rdMolDescriptors

from app.chemistry.kinetics_rules.xyg3_barrier_manifest import (
    EXPECTED_SUBSET_REACTIONS,
    MANIFEST_PATH,
    ManifestError,
    load_xyg3_barrier_manifest,
    parse_xyg3_barrier_manifest,
    parse_xyg3_barrier_manifest_bytes,
)
from app.services.kinetics_selection.rules import XYG3_MANIFEST_SHA256, XYG3B3LYPBarrierRule

RAW = yaml.safe_load(MANIFEST_PATH.read_bytes())


def doc(**changes):
    """A deep copy of the shipped document with ``status`` entries replaced."""
    raw = copy.deepcopy(RAW)
    raw["status"].update(changes)
    return raw


def test_the_shipped_bytes_are_the_pinned_ones_and_the_pin_is_enforced():
    data = MANIFEST_PATH.read_bytes()
    assert hashlib.sha256(data).hexdigest() == XYG3_MANIFEST_SHA256
    assert load_xyg3_barrier_manifest(expected_sha256=XYG3_MANIFEST_SHA256).sha256 == XYG3_MANIFEST_SHA256
    with pytest.raises(ManifestError, match="pinned digest"):
        parse_xyg3_barrier_manifest_bytes(data + b"# edited\n", expected_sha256=XYG3_MANIFEST_SHA256)
    with pytest.raises(ManifestError, match="pinned digest"):
        parse_xyg3_barrier_manifest_bytes(data, expected_sha256="0" * 64)


def test_membership_is_complete_by_listing_and_the_counts_follow_from_it():
    manifest = load_xyg3_barrier_manifest(expected_sha256=XYG3_MANIFEST_SHA256)
    assert len(manifest.members) == 38 and manifest.barrier_height_count == 76
    subsets = {s: sum(m.subset == s for m in manifest.members) for s in EXPECTED_SUBSET_REACTIONS}
    assert subsets == {"HT": 19, "HAT": 6, "NS": 8, "UM": 5} == EXPECTED_SUBSET_REACTIONS
    assert {m.dataset for m in manifest.members} == {"HTBH38/04", "NHTBH38/04"}
    assert len({m.member_id for m in manifest.members}) == 38
    assert RAW["status"]["count_stated_by_source"] == {
        "reactions": 38, "barrier_heights": 76, "HT38": 38, "HAT12": 12, "NS16": 16, "UM10": 10
    }


def test_every_identity_is_recomputed_from_its_smiles_not_taken_on_trust():
    for member in load_xyg3_barrier_manifest(expected_sha256=XYG3_MANIFEST_SHA256).members:
        for species in (*member.reactants, *member.products):
            mol = Chem.MolFromSmiles(species.smiles)
            assert mol is not None, species.key
            assert Chem.MolToInchiKey(mol) == species.inchikey, (member.member_id, species.key)
            assert rdMolDescriptors.CalcMolFormula(mol) == species.molecular_formula, (member.member_id, species.key)
            assert re.fullmatch(r"[A-Z]{14}-[A-Z]{10}-[A-Z]", species.inchikey)


def test_the_symmetric_reactions_have_equal_forward_and_reverse_barriers_and_nothing_else_does_unexplained():
    members = load_xyg3_barrier_manifest(expected_sha256=XYG3_MANIFEST_SHA256).members
    for member in members:
        same_sides = member.reactant_signature == member.product_signature
        if same_sides:
            assert member.forward_kcal_mol == member.reverse_kcal_mol, member.member_id
    # The identity exchange reactions and the pentadiene hydrogen shift are the degenerate ones in the sets.
    degenerate = {m.member_id for m in members if m.reactant_signature == m.product_signature}
    assert len(degenerate) >= 3
    assert all(m.forward_kcal_mol == m.reverse_kcal_mol for m in members if m.member_id in degenerate)


def test_the_published_numbers_and_the_correction_are_recorded_next_to_each_other():
    aggregates = RAW["aggregates"]
    printed = aggregates["zhang_2009_table_2_mad_kcal_mol"]
    assert printed["B3LYP"] == {"all_76": 4.28, "HT38": 4.23, "HAT12": 8.49, "NS16": 3.25, "UM10": 2.02}
    assert printed["XYG3"]["all_76"] == 1.02
    original = aggregates["zhao_2005_b3lyp_mg3s_published"]["original_table_4_5"]
    corrected = aggregates["zhao_2005_b3lyp_mg3s_published"]["corrected_2006"]
    # Zhang prints the uncorrected total; the correction moves it, and only the totals.
    assert original["all76_mue_as_printed"] == printed["B3LYP"]["all_76"] == 4.28
    assert corrected["all76_mue"] == 4.40 and corrected["nonHT38_mue"] == 4.58
    assert original["HAT12_mue"] == printed["B3LYP"]["HAT12"]
    assert {c["source"] for c in RAW["corrections_applied"]} == {"ZHAO_2005_CORRECTION"}
    assert any(c["reason"].startswith("CH3 + FCl") for c in RAW["corrections_applied"])


def test_the_five_papers_are_pinned_by_digest():
    ids = {s["id"] for s in RAW["sources"]}
    assert ids == {"ZHANG_2009", "ZHANG_2009_SI", "ZHAO_2005", "ZHAO_2005_SI", "ZHAO_2005_CORRECTION"}
    assert all(re.fullmatch(r"[0-9a-f]{64}", s["sha256"]) for s in RAW["sources"])
    assert len({s["sha256"] for s in RAW["sources"]}) == 5


# -- the manifest is not approved, and says why ---------------------------------------------------------


def test_it_loads_but_is_not_activatable_and_names_every_blocker():
    manifest = load_xyg3_barrier_manifest(expected_sha256=XYG3_MANIFEST_SHA256)
    assert manifest.activation_approved is False and manifest.activatable is False
    assert manifest.curator_acceptance is None
    assert {b["id"] for b in manifest.activation_blockers} == {
        "comparator_protocol_unverified",
        "only_aggregate_evidence",
        "reference_geometry_not_expressible",
        "declared_energy_level_not_verifiable_from_calculations",
        "correction_changes_a_cited_total",
    }
    assert XYG3B3LYPBarrierRule(manifest).status == "inactive"
    reasons = XYG3B3LYPBarrierRule(manifest).inactive_reasons
    assert len(reasons) == 6 and reasons[-1].startswith("not approved")


def test_a_document_that_claims_approval_must_carry_an_acceptance_and_no_blockers(monkeypatch):
    from tests.services.kinetics_selection._support import pinned_rule

    approved = doc(activation_approved=True)
    with pytest.raises(ManifestError, match="no curator acceptance"):
        parse_xyg3_barrier_manifest(approved)
    accepted = doc(activation_approved=True, curator_acceptance={"accepted_by": "owner", "date": "2026-10-05"})
    with pytest.raises(ManifestError, match="blockers are still listed"):
        parse_xyg3_barrier_manifest(accepted)
    cleared = doc(activation_approved=True, curator_acceptance={"accepted_by": "owner", "date": "2026-10-05"},
                  activation_blockers=[])
    manifest = parse_xyg3_barrier_manifest(cleared)
    assert manifest.activatable
    rule = pinned_rule(cleared, monkeypatch)  # the only way an approved manifest reaches the rule: a new pin
    assert rule.status == "active" and rule.inactive_reasons == ()


def test_a_document_that_is_not_approved_must_say_why():
    with pytest.raises(ManifestError, match="lists no reason"):
        parse_xyg3_barrier_manifest(doc(activation_blockers=[]))


@pytest.mark.parametrize(
    "mutate,message",
    [
        (lambda r: r["members"].pop(), "reactions"),
        (lambda r: r["status"]["count_stated_by_source"].update(barrier_heights=75), "barrier-height count"),
        (lambda r: r["status"]["count_found"].update(reactions=37), "reactions"),
        (lambda r: r["status"].update(identity_unresolved=1), "unresolved"),
        (lambda r: r["status"].update(membership_listed_complete=False), "not marked complete"),
        (lambda r: r["members"][0].update(identity_status="unresolved"), "not resolved"),
        (lambda r: r["members"][1].update(member_id=r["members"][0]["member_id"]), "repeat"),
        (lambda r: r["members"][0].update(subset="UM"), "subset"),
        (lambda r: r.update(corrections_applied=[]), "no corrections"),
        (lambda r: r["status"].update(activation_approved="yes"), "true or false"),
        (lambda r: r["status"]["count_stated_by_source"].update(HT38=36), "subset barrier-height counts"),
    ],
    ids=["member_dropped", "heights_count", "found_count", "unresolved_count", "not_complete", "member_unresolved",
         "repeated_id", "wrong_subset", "no_corrections", "approval_not_bool", "subset_count"],
)
def test_a_document_that_disagrees_with_itself_does_not_load(mutate, message):
    raw = copy.deepcopy(RAW)
    mutate(raw)
    with pytest.raises(ManifestError, match=message):
        parse_xyg3_barrier_manifest(raw)


def test_the_degenerate_flag_follows_the_species_and_the_identity_exchanges_say_so():
    manifest = load_xyg3_barrier_manifest(expected_sha256=XYG3_MANIFEST_SHA256)
    flagged = {m["member_id"] for m in RAW["members"] if m["degenerate_identity_reaction"]}
    same_species = {m.member_id for m in manifest.members if m.reactant_signature == m.product_signature}
    assert flagged == same_species
    # The two complex-to-complex exchanges were once recorded as not degenerate while their species were identical.
    assert {"NHT08", "NHT10"} <= flagged
    assert manifest.version == "0.3.0"
    assert [h["version"] for h in RAW["manifest_history"]] == ["0.1.0", "0.2.0", "0.3.0"]


@pytest.mark.parametrize("member_id,flag", [("NHT08", False), ("HT01", True)])
def test_a_flag_that_disagrees_with_the_species_does_not_load(member_id, flag):
    raw = copy.deepcopy(RAW)
    next(m for m in raw["members"] if m["member_id"] == member_id)["degenerate_identity_reaction"] = flag
    with pytest.raises(ManifestError, match="degenerate_identity_reaction"):
        parse_xyg3_barrier_manifest(raw)


def test_the_energy_level_blocker_describes_the_field_that_exists_and_no_text_is_html_escaped():
    manifest = load_xyg3_barrier_manifest(expected_sha256=XYG3_MANIFEST_SHA256)
    texts = {b["id"]: b["text"] for b in manifest.activation_blockers}
    assert "electronic_method_not_a_protocol_field" not in texts
    text = " ".join(texts["declared_energy_level_not_verifiable_from_calculations"].split())
    assert "energy_level_of_theory_id" in text and "has no electronic method" not in text
    # The source and the rule's own output carry the plain characters; an entity can only come from a later display.
    reasons = " ".join(XYG3B3LYPBarrierRule(manifest).inactive_reasons)
    assert "4.34 -> 4.58" in reasons
    assert not re.search(r"&(gt|lt|amp|quot|#\d+);", reasons)
