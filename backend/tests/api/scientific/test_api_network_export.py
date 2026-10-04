"""``POST /scientific/networks/{ref}/kinetics/export-selected``: serialise a verified selection, refuse the rest.

The caller saves the manifest of a selection, then submits it with the node it chose and one representation per
member. These tests hold that the server re-checks the manifest against what it holds (stale, forged and incomplete
documents are refused), that the choice must be one the decision allows (administrative choice off by default), that
alternatives are never added and ``DUPLICATE`` never written, that CHEMKIN is forward-only, and that forms with no
serialisation are a structured refusal.
"""

from __future__ import annotations

import copy

import pytest

from app.db.models.common import RecordReviewStatus as S
from app.services.network_selection import manifest as manifest_module
from app.services.network_selection import selection as selection_module
from app.services.network_selection.manifest import manifest_digest
from tests.services.network_selection._rules import ProtocolRule, protocol
from tests.services.network_selection._world import add_solve, build_world, fit_spec, set_review, target

A_, B_ = "chemically_significant_eigenvalues", "modified_strong_collision"
PRODUCT = "product_resolved_coefficient"


@pytest.fixture
def world(db_session):
    return build_world(db_session)


def question(world, **changes):
    base = {
        "scope": "single_channel",
        "channel_key": "assoc",
        "observable": PRODUCT,
        "coefficient_basis": "kernel",
        "temperature_min_k": 500.0,
        "temperature_max_k": 1500.0,
        "pressure_min_bar": 0.5,
        "pressure_max_bar": 5.0,
        "bath": {"components": [{"species_ref": world.ar.public_ref}]},
        "partition": {"retained": sorted(world.hashes.values())},
    }
    base.update(copy.deepcopy(changes))
    return {k: v for k, v in base.items() if v is not None}


def bundle_question(world, *channels, **changes):
    outputs = [{"channel_key": c, "observable": PRODUCT} for c in channels]
    return question(world, scope="projected_bundle", channel_key=None, observable=None, outputs=outputs, **changes)


def manifest_of(client, world, request=None, *, profile=None):
    response = client.post(
        f"/api/v1/scientific/networks/{world.ref}/kinetics/select/manifest",
        json=question(world) if request is None else request,
        params={"profile": profile} if profile else None,
    )
    assert response.status_code == 200, response.text
    return response.json()


def export(client, world, manifest, node, fits, *, ref=None, profile=None, **options):
    body = {"manifest": manifest, "node_ref": node, "representation_refs": list(fits), **options}
    return client.post(
        f"/api/v1/scientific/networks/{ref or world.ref}/kinetics/export-selected",
        json=body,
        params={"profile": profile} if profile else None,
    )


def code(response) -> str:
    return response.json()["code"]


def context(response) -> dict:
    return response.json().get("context", {})


def reseal(manifest: dict) -> dict:
    manifest["digest"] = {"algorithm": "sha256", "value": manifest_digest(manifest)}
    return manifest


@pytest.fixture
def one(db_session, world):
    """One solve, one determination, one PLOG fit: a sole eligible candidate."""
    solve = add_solve(db_session, world, fits=[fit_spec("assoc")], protocol=protocol(A_))
    return solve, solve._dets["d_assoc"].public_ref, solve._fits[0].public_ref


@pytest.fixture
def two(db_session, world):
    a = add_solve(db_session, world, fits=[fit_spec("assoc")], protocol=protocol(A_), review=S.not_reviewed)
    b = add_solve(db_session, world, fits=[fit_spec("assoc")], protocol=protocol(B_), review=S.approved)
    return a, b


def use_rules(monkeypatch, *rules):
    """Swap in test-only active rules at the service boundary and in the replay registry."""
    monkeypatch.setattr(selection_module, "default_rules", lambda: tuple(rules))
    monkeypatch.setattr(manifest_module, "default_rules", lambda: tuple(rules))


# -- the export itself -------------------------------------------------------------------------------------------


def test_a_sole_eligible_node_exports_natively_with_provenance_and_endpoints(client, world, one):
    solve, det, fit = one
    manifest = manifest_of(client, world)
    response = export(client, world, manifest, det, [fit])
    assert response.status_code == 200, response.text
    out = response.json()
    assert out["format"] == "native" and out["files"] is None and out["administrative"] is False
    assert out["selection_basis"] == "sole_eligible_candidate" and out["solve_ref"] == solve.public_ref
    assert out["provenance"]["submitted_manifest_digest"] == manifest["digest"]
    assert out["provenance"]["verified"]["snapshot_isolation"] and out["provenance"]["origin"]["solve_kind"] == "computed"
    assert out["request"]["profile"] == "exploratory" and out["request"]["allow_administrative_choice"] is False
    (member,) = out["members"]
    assert member["determination_ref"] == det and member["channel_key"] == "assoc"
    assert member["channel"]["source"]["kind"] == "bimolecular" and member["channel"]["sink"]["kind"] == "well"
    assert [p["stoichiometry"] for p in member["channel"]["source"]["participants"]] == [1, 1]
    rep = member["representation"]
    assert rep["kinetics_ref"] == fit and rep["model_kind"] == "plog" and len(rep["plog"]) == 3
    assert not any(key.startswith("_") for key in rep)
    assert any("Forward direction only" in a for a in out["assumptions"])


def test_chemkin_output_is_forward_only_with_no_thermo_and_no_duplicate(client, world, one):
    _, det, fit = one
    out = export(client, world, manifest_of(client, world), det, [fit], format="chemkin").json()
    text = out["files"]["chem.inp"]
    assert set(out["files"]) == {"chem.inp"}
    assert "=>" in text and "<=>" not in text and "DUPLICATE" not in text
    assert text.count("PLOG /") == 3 and "THERMO" not in text
    assert f"TCKDB {fit}" in text
    first_pressure = float(text.split("PLOG /")[1].split()[0])
    assert first_pressure == pytest.approx(0.1 / 1.01325, rel=1e-4)  # bar to atm


def test_chemkin_chebyshev_is_written_and_a_molecule_unit_shifts_log10_k(client, db_session, world):
    spec = fit_spec("assoc", model="chebyshev", rep="cheb", pmin=0.1, pmax=10.0, units="cm3_molecule_s")
    solve = add_solve(db_session, world, fits=[spec], protocol=protocol(A_))
    manifest = manifest_of(client, world)
    det, fit = solve._dets["d_assoc"].public_ref, solve._fits[0].public_ref
    text = export(client, world, manifest, det, [fit], format="chemkin").json()["files"]["chem.inp"]
    assert "TCHEB /" in text and "PCHEB /" in text and "CHEB / 2 2" in text
    first = float(text.split("CHEB / 2 2 ")[1].split()[0])
    assert first == pytest.approx(1.0 + 23.7797, abs=1e-3)  # first coefficient plus log10(Avogadro)


def test_a_bundle_exports_every_member_with_its_chosen_representation(client, db_session, world):
    outputs = [{"channel_key": c, "availability": "supplied", "required": True} for c in ("assoc", "elim")]
    solve = add_solve(
        db_session, world, fits=[fit_spec("assoc"), fit_spec("elim")], solve_target=target(world, outputs=outputs),
        product_sets=[{"key": "both", "members": [("d_assoc", []), ("d_elim", [])]}],
    )
    request = bundle_question(world, "assoc", "elim")
    manifest = manifest_of(client, world, request)
    node = f"{solve.public_ref}/both"
    fits = [f.public_ref for f in solve._fits]
    out = export(client, world, manifest, node, fits, format="chemkin").json()
    assert {m["channel_key"] for m in out["members"]} == {"assoc", "elim"}
    assert out["files"]["chem.inp"].count("=>") == 2


# -- the manifest is checked against the server ------------------------------------------------------------------


def test_a_manifest_made_before_a_new_solve_arrived_is_stale_and_is_not_refreshed(client, db_session, world, one):
    _, det, fit = one
    manifest = manifest_of(client, world)
    add_solve(db_session, world, fits=[fit_spec("assoc")], protocol=protocol(B_))  # the population changed
    response = export(client, world, manifest, det, [fit])
    assert response.status_code == 422 and code(response) == "network_export_manifest_stale"
    assert {"solves", "population"} <= set(context(response)["differs"])


def test_a_review_change_makes_the_manifest_stale(client, db_session, world, one):
    solve, det, fit = one
    manifest = manifest_of(client, world)
    set_review(db_session, world, solve, S.under_review)
    response = export(client, world, manifest, det, [fit])
    assert code(response) == "network_export_manifest_stale"
    assert "review_states" in context(response)["differs"] or "solves" in context(response)["differs"]


def test_a_manifest_made_under_another_read_profile_is_stale(client, world, one):
    _, det, fit = one
    manifest = manifest_of(client, world)  # exploratory
    response = export(client, world, manifest, det, [fit], profile="curated")
    assert response.status_code in (404, 422)  # the network is below the curated floor: it is not even visible
    if response.status_code == 422:
        assert code(response) == "network_export_manifest_stale"


def test_a_rule_change_makes_the_manifest_stale(client, world, two, monkeypatch):
    a, _ = two
    manifest = manifest_of(client, world)
    use_rules(monkeypatch, ProtocolRule("T-PREFER-A", prefer=A_, yield_=B_))
    # The recorded registry has no such rule, so replay itself refuses a document claiming otherwise; the live
    # registry has one the document never saw, so the server's content differs.
    response = export(client, world, manifest, a._dets["d_assoc"].public_ref, [a._fits[0].public_ref], allow_administrative_choice=True)
    assert response.status_code == 422 and code(response) in ("network_export_manifest_stale", "network_export_manifest_invalid")


def test_an_edited_manifest_with_a_stale_digest_does_not_replay(client, world, one):
    _, det, fit = one
    manifest = manifest_of(client, world)
    manifest["decision"]["basis"] = "edited"
    response = export(client, world, manifest, det, [fit])
    assert code(response) == "network_export_manifest_invalid" and context(response)["reason"] == "replay_failed"


def test_a_resealed_forgery_does_not_survive_re_assessment(client, world, one):
    _, det, fit = one
    manifest = manifest_of(client, world)
    forged = copy.deepcopy(manifest)
    forged["assessments"]["determinations"][0]["eligible_fit_refs"] = [fit, "nkin_forgedforgedforgedforged"]
    reseal(forged)
    response = export(client, world, forged, det, [fit])
    assert code(response) == "network_export_manifest_invalid"


def test_a_self_consistent_document_that_the_server_does_not_hold_is_stale(client, world, one):
    """Edit the captured fit's summary and recompute assessments, decision and digest the way a forger would."""
    _, det, fit = one
    manifest = manifest_of(client, world)
    forged = copy.deepcopy(manifest)
    forged["solves"][0]["fits"][0]["tmax_k"] = 3000.0  # claims a wider support than the server's stored fit has
    reseal(forged)
    response = export(client, world, forged, det, [fit])
    # Replay may already refuse it (the assessment recorded no longer matches); if the forger also rewrote that,
    # the comparison with the server's own content does.
    assert code(response) in ("network_export_manifest_invalid", "network_export_manifest_stale")


@pytest.mark.parametrize("missing", ["solves", "assessments", "decision", "digest", "network", "request"])
def test_an_incomplete_manifest_is_refused(client, world, one, missing):
    _, det, fit = one
    manifest = manifest_of(client, world)
    del manifest[missing]
    response = export(client, world, manifest, det, [fit])
    assert code(response) == "network_export_manifest_invalid" and context(response)["reason"] == "incomplete"
    assert missing in context(response)["missing"]


def test_another_networks_manifest_is_refused(client, db_session, world, one):
    _, det, fit = one
    manifest = manifest_of(client, world)
    from types import SimpleNamespace

    from tests.services.scientific_read._factories import make_network

    other = SimpleNamespace(ref=make_network(db_session).public_ref)
    response = export(client, other, manifest, det, [fit])
    assert code(response) == "network_export_manifest_invalid" and context(response)["reason"] == "network_mismatch"


# -- the choice ----------------------------------------------------------------------------------------------------


def test_administrative_choice_is_off_by_default_and_never_bypasses_a_conflict(client, world, two, monkeypatch):
    a, b = two
    manifest = manifest_of(client, world)
    assert manifest["outcome"] == "incomparable_alternatives"
    det_a, fit_a = a._dets["d_assoc"].public_ref, a._fits[0].public_ref
    refused = export(client, world, manifest, det_a, [fit_a])
    assert code(refused) == "network_export_choice_not_allowed"
    assert context(refused)["reason"] == "administrative_choice_not_accepted"
    accepted = export(client, world, manifest, det_a, [fit_a], allow_administrative_choice=True).json()
    assert accepted["administrative"] is True and accepted["selection_basis"] == "administrative_choice"

    use_rules(monkeypatch, ProtocolRule("T-A", prefer=A_, yield_=B_), ProtocolRule("T-B", prefer=B_, yield_=A_))
    conflict = manifest_of(client, world)
    assert conflict["outcome"] == "policy_conflict"
    response = export(client, world, conflict, det_a, [fit_a], allow_administrative_choice=True)
    assert code(response) == "network_export_choice_not_allowed" and context(response)["reason"] == "outcome_selects_nothing"


def test_only_the_selected_node_exports_when_a_rule_ranked_them(client, world, two, monkeypatch):
    a, b = two
    use_rules(monkeypatch, ProtocolRule("T-PREFER-A", prefer=A_, yield_=B_))
    manifest = manifest_of(client, world)
    assert manifest["outcome"] == "policy_preferred"
    ok = export(client, world, manifest, a._dets["d_assoc"].public_ref, [a._fits[0].public_ref])
    assert ok.status_code == 200 and ok.json()["selection_basis"] == "policy_preferred"
    other = export(
        client, world, manifest, b._dets["d_assoc"].public_ref, [b._fits[0].public_ref], allow_administrative_choice=True
    )
    assert code(other) == "network_export_choice_not_allowed" and context(other)["reason"] == "not_the_selected_node"


def test_an_unknown_node_is_not_an_eligible_node(client, world, one):
    _, _, fit = one
    response = export(client, world, manifest_of(client, world), "nkdet_doesnotexistdoesnotexist", [fit])
    assert code(response) == "network_export_choice_not_allowed"


def test_nothing_is_exportable_when_no_candidate_applies(client, world, one):
    solve, det, fit = one
    request = question(world, temperature_max_k=1200000.0)
    manifest = manifest_of(client, world, request)
    assert manifest["outcome"] == "no_applicable_candidate"
    response = export(client, world, manifest, det, [fit], allow_administrative_choice=True)
    assert code(response) == "network_export_choice_not_allowed" and context(response)["reason"] == "outcome_selects_nothing"


# -- the representation choice is exact; alternatives are never added -----------------------------------------------


def test_alternates_of_one_determination_are_never_exported_together(client, db_session, world):
    solve = add_solve(
        db_session, world,
        fits=[fit_spec("assoc", rep="plog1"), fit_spec("assoc", rep="cheb1", model="chebyshev", pmin=0.1, pmax=10.0)],
        protocol=protocol(A_),
    )
    det = solve._dets["d_assoc"].public_ref
    plog, cheb = (f.public_ref for f in solve._fits)
    manifest = manifest_of(client, world)
    both = export(client, world, manifest, det, [plog, cheb], format="chemkin")
    assert code(both) == "network_export_representation_choice_invalid"
    assert context(both)["members_with_several"] == [det]
    one_only = export(client, world, manifest, det, [cheb], format="chemkin").json()
    text = one_only["files"]["chem.inp"]
    assert "CHEB /" in text and "PLOG /" not in text and "DUPLICATE" not in text


def test_a_representation_choice_must_cover_every_member_and_name_only_eligible_fits(client, db_session, world):
    outputs = [{"channel_key": c, "availability": "supplied", "required": True} for c in ("assoc", "elim")]
    solve = add_solve(
        db_session, world, fits=[fit_spec("assoc"), fit_spec("elim")], solve_target=target(world, outputs=outputs),
        product_sets=[{"key": "both", "members": [("d_assoc", []), ("d_elim", [])]}],
    )
    manifest = manifest_of(client, world, bundle_question(world, "assoc", "elim"))
    node = f"{solve.public_ref}/both"
    assoc, elim = (f.public_ref for f in solve._fits)
    short = export(client, world, manifest, node, [assoc])
    assert code(short) == "network_export_representation_choice_invalid"
    assert context(short)["members_without_a_choice"] == [solve._dets["d_elim"].public_ref]
    foreign = export(client, world, manifest, node, [assoc, elim, "nkin_notafitofthisnodeatall"])
    assert context(foreign)["not_eligible_for_this_node"] == ["nkin_notafitofthisnodeatall"]
    duplicated = export(client, world, manifest, node, [assoc, elim, elim])
    assert context(duplicated)["duplicated"] == [elim]


def test_a_fit_of_another_solve_is_not_eligible_for_the_node(client, world, two):
    a, b = two
    manifest = manifest_of(client, world)
    response = export(
        client, world, manifest, a._dets["d_assoc"].public_ref, [b._fits[0].public_ref], allow_administrative_choice=True
    )
    assert code(response) == "network_export_representation_choice_invalid"
    assert context(response)["not_eligible_for_this_node"] == [b._fits[0].public_ref]


# -- unsupported forms -------------------------------------------------------------------------------------------


def test_a_chebyshev_that_does_not_store_log10_k_is_refused_for_chemkin_and_kept_natively(client, db_session, world):
    solve = add_solve(
        db_session, world,
        fits=[fit_spec("assoc", model="chebyshev", rep="cheb", pmin=0.1, pmax=10.0, stores_log10=False)],
        protocol=protocol(A_),
    )
    det, fit = solve._dets["d_assoc"].public_ref, solve._fits[0].public_ref
    manifest = manifest_of(client, world)
    refused = export(client, world, manifest, det, [fit], format="chemkin")
    assert refused.status_code == 422 and code(refused) == "network_export_unsupported_form"
    assert {"kinetics_ref": fit, "reason": "chebyshev_does_not_store_log10_k"} in context(refused)["forms"]
    native = export(client, world, manifest, det, [fit], format="native")
    assert native.status_code == 200 and native.json()["members"][0]["representation"]["model_kind"] == "chebyshev"


def test_two_channels_with_one_equation_are_reported_never_added_as_duplicate(client, db_session, world):
    outputs = [{"channel_key": c, "availability": "supplied", "required": True} for c in ("elim", "elim_alt")]
    solve = add_solve(
        db_session, world, fits=[fit_spec("elim"), fit_spec("elim_alt")], solve_target=target(world, outputs=outputs),
        product_sets=[{"key": "pair", "members": [("d_elim", []), ("d_elim_alt", [])]}],
    )
    manifest = manifest_of(client, world, bundle_question(world, "elim", "elim_alt"))
    node = f"{solve.public_ref}/pair"
    fits = [f.public_ref for f in solve._fits]
    refused = export(client, world, manifest, node, fits, format="chemkin")
    assert code(refused) == "network_export_unsupported_form"
    reasons = {f["reason"] for f in context(refused)["forms"]}
    assert reasons == {"equation_collision_is_not_additive"}
    assert {f["kinetics_ref"] for f in context(refused)["forms"]} == set(fits)
    native = export(client, world, manifest, node, fits).json()
    assert native["equation_collisions"] == [{"kinetics_refs": sorted(fits)}] or native["equation_collisions"][0]["kinetics_refs"] == fits
    assert len(native["members"]) == 2


# -- the request ---------------------------------------------------------------------------------------------------


def test_integer_handles_unknown_fields_and_query_keys_are_refused(client, world, one):
    solve, det, fit = one
    manifest = manifest_of(client, world)
    assert export(client, world, manifest, det, ["12"]).status_code == 422
    assert export(client, world, manifest, det, [fit], ref=str(world.network.id)).status_code == 422
    assert export(client, world, manifest, det, [fit], ref="net_doesnotexistdoesnotexist").status_code == 404
    assert export(client, world, manifest, det, [fit], rules=[{"rule_id": "mine"}]).status_code == 422
    assert export(client, world, manifest, det, [fit], network_id=1).status_code == 422
    query = client.post(
        f"/api/v1/scientific/networks/{world.ref}/kinetics/export-selected?format=chemkin",
        json={"manifest": manifest, "node_ref": det, "representation_refs": [fit]},
    )
    assert query.status_code == 422 and "post_search_fields_must_be_in_body" in query.text


def test_no_database_id_appears_in_an_export(client, world, one):
    _, det, fit = one
    out = export(client, world, manifest_of(client, world), det, [fit], format="chemkin").json()

    def walk(value, path="$"):
        if isinstance(value, dict):
            for key, item in value.items():
                yield path, key, item
                yield from walk(item, f"{path}.{key}")
        elif isinstance(value, list):
            for item in value:
                yield from walk(item, path)

    offenders = [
        (path, key) for path, key, value in walk(out)
        if key in ("id", "solve_id", "fit_id", "channel_id", "state_id") or (key.endswith("_id") and value is not None)
    ]
    assert offenders == [], offenders


def test_a_node_behind_the_leading_front_is_refused_even_when_administrative_choice_is_accepted(
    client, db_session, world, monkeypatch
):
    a = add_solve(db_session, world, fits=[fit_spec("assoc")], protocol=protocol(A_))
    b = add_solve(db_session, world, fits=[fit_spec("assoc")], protocol=protocol(B_))
    c = add_solve(db_session, world, fits=[fit_spec("assoc")], protocol=protocol("reservoir_state"))
    use_rules(monkeypatch, ProtocolRule("T-PREFER-A", prefer=A_, yield_=B_))
    manifest = manifest_of(client, world)
    assert manifest["outcome"] == "incomparable_alternatives"  # A and C are unranked; B yields to A
    assert set(manifest["decision"]["fronts"][0]) == {a._dets["d_assoc"].public_ref, c._dets["d_assoc"].public_ref}
    behind = export(
        client, world, manifest, b._dets["d_assoc"].public_ref, [b._fits[0].public_ref], allow_administrative_choice=True
    )
    assert code(behind) == "network_export_choice_not_allowed" and context(behind)["reason"] == "outside_leading_front"
    inside = export(
        client, world, manifest, c._dets["d_assoc"].public_ref, [c._fits[0].public_ref], allow_administrative_choice=True
    )
    assert inside.status_code == 200 and inside.json()["administrative"] is True


def test_a_fit_of_another_solve_or_network_is_refused_when_its_content_is_loaded(db_session, world):
    from app.api.error_contract import CodedValueError
    from app.services.network_selection import export as export_module

    a = add_solve(db_session, world, fits=[fit_spec("assoc")], protocol=protocol(A_))
    b = add_solve(db_session, world, fits=[fit_spec("assoc")], protocol=protocol(B_))
    with pytest.raises(CodedValueError) as caught:
        export_module._load_fits(db_session, world.ref, a.public_ref, [b._fits[0].public_ref])
    assert caught.value.context["of_another_solve"] == [b._fits[0].public_ref]
    with pytest.raises(CodedValueError) as caught:
        export_module._load_fits(db_session, "net_doesnotexistdoesnotexist", a.public_ref, [a._fits[0].public_ref])
    assert caught.value.context["not_found_or_hidden"] == [a._fits[0].public_ref]


def test_a_relabelled_review_basis_is_refused_even_when_the_digest_is_resealed(client, db_session, world, two):
    """Relabel an exploratory manifest as curated while an unreviewed solve stays in it, then re-seal."""
    a, b = two  # a is not_reviewed, b approved
    manifest = manifest_of(client, world)
    forged = copy.deepcopy(manifest)
    forged["visibility"]["read_profile"] = "curated"
    forged["request"]["profile"] = "curated"
    forged["request"]["effective_review_statuses"] = ["approved"]
    reseal(forged)
    response = export(
        client, world, forged, b._dets["d_assoc"].public_ref, [b._fits[0].public_ref], allow_administrative_choice=True
    )
    assert code(response) == "network_export_manifest_invalid" and context(response)["reason"] == "replay_failed"
    assert "review status" in context(response)["detail"]


def _approve_network(session, world) -> None:
    from app.db.models.common import SubmissionRecordType
    from app.services.record_review import ensure_record_review, set_record_review_status

    ensure_record_review(session, record_type=SubmissionRecordType.network, record_id=world.network.id)
    set_record_review_status(
        session, record_type=SubmissionRecordType.network, record_id=world.network.id, status=S.approved, actor=world.actor
    )


# -- review round: bounds, reported rates, direction, units, pinned numbers, provenance ----------------------------


@pytest.mark.parametrize("limits", [{"solves": 0}, {"solves": 10**12, "snapshot_bytes": 10**12}])
def test_the_documents_own_bounds_never_size_the_scan(client, world, one, limits):
    """Lifted or lowered, re-sealed: the bounds are the server's, so the document is refused before any scan."""
    _, det, fit = one
    manifest = manifest_of(client, world)
    forged = copy.deepcopy(manifest)
    forged["request"]["bounds"].update(limits)
    reseal(forged)
    response = export(client, world, forged, det, [fit])
    assert response.status_code == 422 and code(response) == "network_export_manifest_invalid"
    assert context(response)["reason"] == "bounds_not_server_bounds"


def test_a_computed_solve_is_labelled_computed_and_a_reported_one_is_disclosed(client, db_session, world):

    computed = add_solve(db_session, world, fits=[fit_spec("assoc")], protocol=protocol(A_))
    det, fit = computed._dets["d_assoc"].public_ref, computed._fits[0].public_ref
    out = export(client, world, manifest_of(client, world), det, [fit]).json()
    assert out["provenance"]["origin"]["solve_kind"] == "computed" and out["members"][0]["solve_kind"] == "computed"
    assert out["members"][0]["literature_ref"] is None


def _reported_world(db_session, world):
    from app.db.models.common import NetworkSolveKind

    solve = add_solve(db_session, world, fits=[fit_spec("assoc")], protocol=protocol(A_), kind=NetworkSolveKind.reported)
    return solve, solve._dets["d_assoc"].public_ref, solve._fits[0].public_ref


def test_a_reported_solve_reaches_chemkin_only_when_asked_for_and_then_with_its_literature(client, db_session, world):
    """ADR 0010: a transcribed rate must not enter a mechanism file undisclosed."""
    solve, det, fit = _reported_world(db_session, world)
    manifest = manifest_of(client, world)
    refused = export(client, world, manifest, det, [fit], format="chemkin")
    assert code(refused) == "network_export_unsupported_form"
    (form,) = context(refused)["forms"]
    assert form["reason"] == "reported_solve_requires_include_reported" and form["literature_ref"].startswith("lit_")
    out = export(client, world, manifest, det, [fit], format="chemkin", include_reported=True).json()
    text = out["files"]["chem.inp"]
    literature = out["provenance"]["origin"]["literature_ref"]
    assert literature and f"[reported; literature {literature}]" in text and "kind=reported" in text.splitlines()[0]
    assert "ADR 0010" in text and any("transcribed" in a for a in out["assumptions"])
    native = export(client, world, manifest, det, [fit]).json()  # native carries the kind, so it is not gated
    assert native["members"][0]["solve_kind"] == "reported" and native["members"][0]["literature_ref"] == literature


def test_the_read_profile_check_is_what_refuses_a_manifest_made_under_another_profile(client, db_session, world):
    _approve_network(db_session, world)
    solve = add_solve(db_session, world, fits=[fit_spec("assoc")], protocol=protocol(A_), review=S.approved)
    manifest = manifest_of(client, world)  # exploratory
    det, fit = solve._dets["d_assoc"].public_ref, solve._fits[0].public_ref
    response = export(client, world, manifest, det, [fit], profile="curated")
    assert response.status_code == 422 and code(response) == "network_export_manifest_stale"
    assert "read_profile" in context(response)["differs"]
    same = export(client, world, manifest_of(client, world, profile="curated"), det, [fit], profile="curated")
    assert same.status_code == 200, same.text


@pytest.mark.parametrize("units, factor", [("cm3_molecule_s", 6.02214076e23), ("m3_mol_s", 1.0e6)])
def test_each_plog_rows_own_a_units_are_converted(client, db_session, world, units, factor):
    solve = add_solve(db_session, world, fits=[fit_spec("assoc", units=units)], protocol=protocol(A_), review=None)
    solve._fits[0].rate_units = None  # the rows alone state the unit
    db_session.flush()
    det, fit = solve._dets["d_assoc"].public_ref, solve._fits[0].public_ref
    text = export(client, world, manifest_of(client, world), det, [fit], format="chemkin").json()["files"]["chem.inp"]
    first_a = float(text.split("PLOG /")[1].split()[1])
    assert first_a == pytest.approx(1.0e13 * factor, rel=1e-4)


def _equation(text: str) -> tuple[set[str], set[str]]:
    line = next(line for line in text.splitlines() if "=>" in line and not line.startswith("!"))
    left, right = line.split("   ")[0].split("=>")
    return {t.strip() for t in left.split("+")}, {t.strip() for t in right.split("+")}


def test_the_equation_runs_from_the_channels_source_to_its_sink(client, db_session, world):
    outputs = [{"channel_key": c, "availability": "supplied", "required": True} for c in ("assoc", "diss")]
    solve = add_solve(
        db_session, world, fits=[fit_spec("assoc"), fit_spec("diss")], solve_target=target(world, outputs=outputs),
        product_sets=[{"key": "both", "members": [("d_assoc", []), ("d_diss", [])]}],
    )
    manifest = manifest_of(client, world, bundle_question(world, "assoc", "diss"))
    node, (assoc, diss) = f"{solve.public_ref}/both", [f.public_ref for f in solve._fits]
    single = {}
    for ref, channel in ((assoc, "assoc"), (diss, "diss")):
        single[channel] = _equation(
            "\n".join(
                line
                for block in export(client, world, manifest, node, [assoc, diss], format="chemkin")
                .json()["files"]["chem.inp"].split("\n")
                for line in [block]
                if f"TCKDB {ref}" in line
            )
        )
    assert single["assoc"] == ({"H1", "C1H4"}, {"C1H3"})  # association: two reactants into the well
    assert single["diss"] == ({"C1H3"}, {"H1", "C1H4"})  # dissociation: the same species, reversed


def test_a_forged_snapshot_isolation_or_digest_is_never_echoed_as_verified(client, world, one):
    _, det, fit = one
    manifest = manifest_of(client, world)
    forged = copy.deepcopy(manifest)
    forged["snapshot_isolation"] = "serializable (forged)"
    reseal(forged)
    out = export(client, world, forged, det, [fit]).json()
    assert out["provenance"]["submitted_manifest_digest"] == forged["digest"]  # labelled as what the caller sent
    verified = out["provenance"]["verified"]
    assert verified["snapshot_isolation"] != "serializable (forged)" and verified["snapshot_isolation"]
    assert "serializable (forged)" not in str(verified)


def test_editing_a_stored_coefficient_in_place_makes_the_manifest_stale(client, db_session, world):
    from sqlalchemy import select

    from app.db.models.network_pdep import NetworkKineticsPlog

    solve = add_solve(db_session, world, fits=[fit_spec("assoc")], protocol=protocol(A_), review=None)
    det, fit = solve._dets["d_assoc"].public_ref, solve._fits[0].public_ref
    manifest = manifest_of(client, world)
    row = db_session.scalars(select(NetworkKineticsPlog).where(NetworkKineticsPlog.network_kinetics_id == solve._fits[0].id)).first()
    row.a = row.a * 2.0  # same solve, same fit, a different number
    db_session.flush()
    response = export(client, world, manifest, det, [fit])
    assert code(response) == "network_export_manifest_stale" and "solves" in context(response)["differs"]


def test_an_unbounded_plog_is_not_refused_for_a_temperature_unit_it_never_stated(client, db_session, world):
    solve = add_solve(
        db_session, world, fits=[fit_spec("assoc", tmin=None, tmax=None, temperature_units=None)], protocol=protocol(A_)
    )
    det, fit = solve._dets["d_assoc"].public_ref, solve._fits[0].public_ref
    out = export(client, world, manifest_of(client, world), det, [fit], format="chemkin")
    assert out.status_code == 200, out.text


def test_a_plog_with_unusable_pressure_units_gets_an_accurate_reason(client, db_session, world):
    solve = add_solve(db_session, world, fits=[fit_spec("assoc", pressure_units="atm")], protocol=protocol(A_))
    det, fit = solve._dets["d_assoc"].public_ref, solve._fits[0].public_ref
    manifest = manifest_of(client, world)
    if manifest["outcome"] == "no_applicable_candidate":
        pytest.skip("the assessor already excludes non-bar pressure units")  # pragma: no cover
    refused = export(client, world, manifest, det, [fit], format="chemkin")
    assert {"kinetics_ref": fit, "reason": "plog_pressure_units_not_bar"} in context(refused)["forms"]
    assert "axis_units_not_bar_kelvin" not in str(context(refused))


def test_an_unknown_energy_unit_is_refused_not_replaced(client, world, one):
    _, det, fit = one
    manifest = manifest_of(client, world)
    assert export(client, world, manifest, det, [fit], format="chemkin", energy_units="btu/mol").status_code == 422
    ok = export(client, world, manifest, det, [fit], format="chemkin", energy_units="kj/mol").json()
    assert "KJOULES/MOLE" in ok["files"]["chem.inp"]


def test_an_oversized_export_body_is_refused_before_it_is_parsed(client, world, one):
    from app.api.export_limits import MAX_BODY_BYTES

    _, det, fit = one
    junk = {"manifest": {"x": "a" * (MAX_BODY_BYTES + 10)}, "node_ref": det, "representation_refs": [fit]}
    response = client.post(f"/api/v1/scientific/networks/{world.ref}/kinetics/export-selected", json=junk)
    assert response.status_code == 413 and response.json()["code"] == "network_export_body_too_large"
    assert response.json()["context"]["max_bytes"] == MAX_BODY_BYTES


def test_an_export_is_logged_with_who_network_digest_format_and_member_count(client, world, one, caplog):
    import logging

    _, det, fit = one
    manifest = manifest_of(client, world)
    with caplog.at_level(logging.INFO, logger="app.services.network_selection.export"):
        export(client, world, manifest, det, [fit], format="chemkin")
    (record,) = [r for r in caplog.records if "network selected export" in r.getMessage()]
    message = record.getMessage()
    assert "actor=" in message and f"network={world.ref}" in message and "format=chemkin" in message
    assert "members=1" in message and "digest=" in message


def test_the_live_selection_always_runs_under_the_servers_bounds(db_session, world, one, monkeypatch):
    """Even a document that somehow carried other limits would not size the scan: the request is rebuilt with ours."""
    from app.services.network_selection import export as export_module
    from app.services.network_selection.models import BOUNDS_V1, SelectionBounds

    seen = {}

    def spy(session, *, request, require_snapshot):
        seen["bounds"] = request.bounds
        raise RuntimeError("stop")

    monkeypatch.setattr(export_module, "select_network", spy)
    request = {
        "network_ref": world.ref, "scope": "single_channel", "channel_key": "assoc", "observable": PRODUCT,
        "coefficient_basis": "kernel", "degeneracy_applied": None, "temperature_min_k": 500.0, "temperature_max_k": 1500.0,
        "pressure_min_bar": 0.5, "pressure_max_bar": 5.0, "outputs": [], "quantity": "rate_coefficient", "phase": "gas",
        "bath": {"species_refs": [world.ar.public_ref], "mole_fractions": None},
        "partition": {"retained": sorted(world.hashes.values()), "eliminated": [], "lumps": []},
        "boundaries": [], "regime_kind": "time_independent", "initial_state_hashes": [],
        "source_composition_hash": None, "sink_composition_hash": None, "objective": "physical_accuracy",
        "reference_model_ref": None, "reference_outputs": None, "min_review_status": None,
        "administrative_policy": "default", "result_mode": "all", "apply_rules": True,
        "bounds": SelectionBounds(solves=1).to_dict(),
    }
    with pytest.raises(RuntimeError):
        export_module._live_selection(db_session, {"request": request}, require_snapshot=False)
    assert seen["bounds"] == BOUNDS_V1
