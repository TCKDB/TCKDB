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


def test_the_nine_sources_are_pinned_by_digest():
    ids = {s["id"] for s in RAW["sources"]}
    assert ids == {
        "ZHANG_2009", "ZHANG_2009_SI", "ZHAO_2005", "ZHAO_2005_SI", "ZHAO_2005_CORRECTION",
        "ZHANG_2010_JCP", "ZHANG_2010_JCP_SI", "ZHANG_2010_CHEMCOMM", "ZHANG_2011_PNAS",
    }
    assert all(re.fullmatch(r"[0-9a-f]{64}", s["sha256"]) for s in RAW["sources"])
    assert len({s["sha256"] for s in RAW["sources"]}) == 9
    by_id = {s["id"]: s for s in RAW["sources"]}
    assert by_id["ZHANG_2010_JCP"]["sha256"] == "f0df839629686b6ce88fdbb87f158e3eb5458131c682061ce5cf41b956340e73"
    assert by_id["ZHANG_2010_JCP_SI"]["sha256"] == "8fdb70a5de8857ea97deaed77156bcb984b1471e2caa892ab430ac19dc9d2eb2"
    assert re.fullmatch(r"[0-9a-f]{64}", by_id["ZHANG_2010_JCP_SI"]["contained_file_sha256"])


def test_the_same_basis_comparison_and_its_table_typo_are_recorded_and_internally_consistent():
    same_basis = RAW["aggregates"]["zhang_2010_jcp_table_3_b9_mad_kcal_mol"]
    assert same_basis["XYG3"]["all_76"] == 1.02 and same_basis["B3LYP"]["all_76"] == 4.54
    assert round(same_basis["B3LYP"]["all_76"] - same_basis["XYG3"]["all_76"], 2) == same_basis["same_basis_gap_all_76"]
    assert same_basis["xyg3_closer_barriers"] + same_basis["b3lyp_closer_barriers"] == 76
    # The subset MADs weight 12 + 16 + 10 + 38 = 76 barriers.
    for method in ("XYG3", "B3LYP"):
        m = same_basis[method]
        total = (12 * m["HAT12"] + 16 * m["NS16"] + 10 * m["UM10"] + 38 * m["HT38"]) / 76
        assert abs(total - m["all_76"]) < 0.01, method
    typo = same_basis["printed_typo"]
    assert (typo["printed"], typo["correct"]) == (1.66, 1.38) and same_basis["XYG3"]["HAT12"] == typo["correct"]
    # The 2009 B3LYP row is not the same-basis one.
    assert RAW["aggregates"]["zhang_2009_table_2_mad_kcal_mol"]["B3LYP"]["all_76"] != same_basis["B3LYP"]["all_76"]


def test_the_scoped_claim_is_narrow_and_lists_what_it_does_not_license():
    claim = RAW["scoped_claim"]
    assert "69 of the 76" in claim["statement"] and "1.02" in claim["statement"] and "4.54" in claim["statement"]
    assert {x["id"] for x in claim["does_not_license"]} == {
        "single_barriers", "other_geometries", "other_bases", "zpe_tunnelling_rate_accuracy",
        "reactions_outside_the_38", "mixed_basis_figures", "corrected_mg3s_total",
    }


# -- the manifest is not approved, and says why ---------------------------------------------------------


def test_it_loads_but_is_not_activatable_and_names_every_blocker():
    manifest = load_xyg3_barrier_manifest(expected_sha256=XYG3_MANIFEST_SHA256)
    assert manifest.activation_approved is False and manifest.activatable is False
    assert manifest.curator_acceptance is None
    assert {b["id"] for b in manifest.activation_blockers} == {
        "reference_geometry_not_expressible",
        "owner_sign_off_not_given",
        "member_level_treatment_undecided",
        "declared_energy_level_not_verifiable_from_calculations",
    }
    # Resolved blockers are history, not blockers: the comparator-basis one is resolved by the JCP 2010 same-basis data.
    resolved = {b["id"]: b for b in RAW["status"]["resolved_blockers"]}
    assert set(resolved) == {
        "comparator_protocol_unverified",
        "electronic_method_not_a_protocol_field",
        "only_aggregate_evidence",
        "correction_changes_a_cited_total",
    }
    assert "comparator_protocol_unverified" not in {b["id"] for b in manifest.activation_blockers}
    assert XYG3B3LYPBarrierRule(manifest).status == "inactive"
    reasons = XYG3B3LYPBarrierRule(manifest).inactive_reasons
    assert len(reasons) == 5 and reasons[-1].startswith("not approved")


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
    assert manifest.version == 1 and RAW["version"] == 1 and RAW["name"] == "xyg3_b3lyp_barrier"
    assert RAW["manifest_date"] == "2026-10-13"
    assert "manifest_version" not in RAW and "manifest_history" not in RAW


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
    assert "QCISD/MG3" in reasons
    resolved_text = " ".join(b["text"] for b in RAW["status"]["resolved_blockers"])
    assert "4.34 -> 4.58" in resolved_text
    assert not re.search(r"&(gt|lt|amp|quot|#\d+);", reasons + resolved_text)


def test_the_geometry_blocker_does_not_claim_the_owner_chose_a_route():
    text = " ".join(
        next(b for b in RAW["status"]["activation_blockers"] if b["id"] == "reference_geometry_not_expressible")["text"].split()
    )
    assert "(a)" in text and "(b)" in text and "has not been made" in text
    assert "would not widen the evidence beyond QCISD/MG3" in text
    assert "until TCKDB can record" not in " ".join(str(RAW).split())
