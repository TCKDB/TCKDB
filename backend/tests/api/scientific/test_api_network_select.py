"""``POST /scientific/networks/{ref}/kinetics/select``: the public network selection endpoint.

The assessor, engine, rules and manifest are tested under ``tests/services/network_selection``. These tests hold the
HTTP contract: the request is normalised or refused, every outcome is reachable, the response says why, visibility
follows the read profile, the manifest replays, and no database id leaves the building.

The registry shipped in this release has no active rule, so the end-to-end preference tests swap in a test-only
active rule at the service boundary (``selection.default_rules``), exactly as the service tests do.
"""

from __future__ import annotations

import copy

import pytest

from app.db.models.common import RecordReviewStatus as S
from app.services.network_selection import ReplayError, replay_network
from app.services.network_selection import selection as selection_module
from tests.services.network_selection._rules import ProtocolRule, protocol
from tests.services.network_selection._world import add_solve, build_world, fit_spec, set_review, target

A_, B_ = "chemically_significant_eigenvalues", "modified_strong_collision"
RULE = ProtocolRule("T-PREFER-A", prefer=A_, yield_=B_)
#: Names a rule or an ordinal, not a row.
NOT_ROW_IDS = {"rule_id", "overridden_by_rule_id", "id_rank"}


@pytest.fixture
def world(db_session):
    return build_world(db_session)


def question(world, **changes):
    base = {
        "scope": "single_channel",
        "channel_key": "assoc",
        "observable": "product_resolved_coefficient",
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


def url(ref, *, manifest: bool = False) -> str:
    return f"/api/v1/scientific/networks/{ref}/kinetics/select" + ("/manifest" if manifest else "")


def post(client, world, request=None, *, profile=None, manifest=False, ref=None):
    params = {"profile": profile} if profile else None
    return client.post(
        url(ref or world.ref, manifest=manifest), json=question(world) if request is None else request, params=params
    )


@pytest.fixture
def two(db_session, world):
    a = add_solve(db_session, world, fits=[fit_spec("assoc")], protocol=protocol(A_), review=S.not_reviewed)
    b = add_solve(db_session, world, fits=[fit_spec("assoc")], protocol=protocol(B_), review=S.approved)
    return a, b


@pytest.fixture
def active_rule(monkeypatch):
    monkeypatch.setattr(selection_module, "default_rules", lambda: (RULE,))


def code(response) -> str:
    return response.json()["code"]


def walk(value, path="$"):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from walk(item, f"{path}.{key}")
            yield path, key, item
    elif isinstance(value, list):
        for i, item in enumerate(value):
            yield from walk(item, f"{path}[{i}]")


# -- the headline ------------------------------------------------------------------------------------------------


def test_a_scoped_preference_selects_over_recency_end_to_end(client, world, two, active_rule):
    a, b = two
    out = post(client, world).json()
    assert out["outcome"] == "policy_preferred"
    assert out["selection"] == {
        "node_ref": a._dets["d_assoc"].public_ref,
        "scope": "single_channel",
        "solve_ref": a.public_ref,
        "members": [
            {
                "determination_ref": a._dets["d_assoc"].public_ref,
                "channel_key": "assoc",
                "kinetics_refs": [a._fits[0].public_ref],
            }
        ],
        "basis": "policy_preferred",
        "administrative": False,
        "explanation": out["basis"],
    }
    assert out["fronts"] == [[a._dets["d_assoc"].public_ref], [b._dets["d_assoc"].public_ref]]
    (edge,) = out["relations"]["edges"]
    assert (edge["preferred"], edge["rule_id"]) == (a._dets["d_assoc"].public_ref, "T-PREFER-A")
    assert out["policy"]["name"] == "network_method_preferred" and out["policy"]["rules_applied"] is True
    assert out["request"]["network_ref"] == world.ref and out["request"]["profile"] == "exploratory"


def test_with_no_active_rule_the_population_is_unranked_and_says_so(client, world, two):
    out = post(client, world).json()
    assert out["outcome"] == "incomparable_alternatives" and out["selection"] is None
    assert out["relations"]["edges"] == []
    assert out["policy"]["rules"] and {r["status"] for r in out["policy"]["rules"]} == {"inactive"}
    assert all(r["inactive_reasons"] for r in out["policy"]["rules"])


def test_mode_first_names_an_administrative_first_and_says_it_is_not_a_method_claim(client, world, two):
    a, b = two
    out = post(client, world, question(world, mode="first")).json()
    assert out["outcome"] == "incomparable_alternatives"
    assert out["selection"]["basis"] == "administrative_first" and out["selection"]["administrative"] is True
    assert out["selection"]["node_ref"] == b._dets["d_assoc"].public_ref  # approved before not_reviewed
    assert post(client, world).json()["selection"] is None
    # The mode reaches the engine, not only the response builder: the request echo and the recorded decision say so.
    assert out["request"]["result_mode"] == "first"
    manifest = post(client, world, question(world, mode="first"), manifest=True).json()
    assert manifest["decision"]["selection_basis"] == "administrative_first"
    assert post(client, world, manifest=True).json()["decision"]["selection_basis"] is None


@pytest.mark.parametrize("policy", ["default", "most_reviewed", "latest"])
def test_an_administrative_policy_applies_no_rule_even_when_one_is_active(client, world, two, active_rule, policy):
    out = post(client, world, question(world, policy=policy)).json()
    assert out["policy"]["rules_applied"] is False and out["relations"]["edges"] == []
    assert out["outcome"] == "incomparable_alternatives" and out["policy"]["rules"] == []


def test_a_bundle_request_selects_a_declared_product_set(client, db_session, world):
    outputs = [{"channel_key": c, "availability": "supplied", "required": True} for c in ("assoc", "elim")]
    solve = add_solve(
        db_session,
        world,
        fits=[fit_spec("assoc"), fit_spec("elim")],
        solve_target=target(world, outputs=outputs),
        product_sets=[{"key": "both", "members": [("d_assoc", []), ("d_elim", [])]}],
    )
    request = question(
        world,
        scope="projected_bundle",
        channel_key=None,
        observable=None,
        outputs=[{"channel_key": c, "observable": "product_resolved_coefficient"} for c in ("elim", "assoc")],
    )
    out = post(client, world, request).json()
    assert out["outcome"] == "sole_eligible_candidate"
    assert out["selection"]["node_ref"] == f"{solve.public_ref}/both" and out["selection"]["scope"] == "projected_bundle"
    assert {m["channel_key"] for m in out["selection"]["members"]} == {"assoc", "elim"}
    assert [o["channel_key"] for o in out["request"]["outputs"]] == ["assoc", "elim"]  # canonical order


# -- references and the request ----------------------------------------------------------------------------------


def test_an_integer_handle_is_refused(client, world):
    response = post(client, world, ref=str(world.network.id))
    assert response.status_code == 422 and code(response) == "invalid_handle"


def test_a_ref_of_another_kind_is_a_handle_type_mismatch_and_an_unknown_ref_is_404(client, world):
    assert code(post(client, world, ref=world.ar.public_ref)) == "handle_type_mismatch"
    assert post(client, world, ref="net_doesnotexistdoesnotexist").status_code == 404


def test_an_unknown_or_integer_bath_species_ref_is_refused(client, world):
    missing = question(world, bath={"components": [{"species_ref": "spe_doesnotexistdoesnotexist"}]})
    assert post(client, world, missing).status_code in (404, 422)
    integer = question(world, bath={"components": [{"species_ref": str(world.ar.id)}]})
    response = post(client, world, integer)
    assert response.status_code == 422 and code(response) == "invalid_handle"


@pytest.mark.parametrize(
    "changes",
    [
        {"network_ref": "net_x"},  # the network is named by the path
        {"network_id": 1},  # no database ids
        {"channel_id": 1},
        {"max_candidates": 5},  # the bounds are the server's
        {"bounds": {"solves": 1}},
        {"phase": "liquid"},
        {"quantity": "net_rate"},
        {"outputs": [{"channel_key": "assoc", "observable": "product_resolved_coefficient"}]},  # single channel + outputs
        {"channel_key": None},
        {"temperature_min_k": 2000.0},
        {"pressure_min_bar": 50.0},
        {"coefficient_basis": "elementary_coefficient"},
        {"bath": {"components": [{"species_ref": "spe_a"}, {"species_ref": "spe_b"}]}},  # a mixture needs fractions
        {"partition": {"retained": []}},
        {"source_composition_hash": "a" * 64},  # stated together
        {"objective": "model_fidelity"},  # needs reference_model_ref
        {"reference_model_ref": "nks_x"},  # only for model_fidelity
        {"regime": {"kind": "initial_population_restricted"}},
    ],
)
def test_an_ill_posed_request_is_an_ordinary_422(client, world, changes):
    response = post(client, world, question(world, **changes))
    assert response.status_code == 422, (changes, response.text)


def test_channel_key_belongs_to_the_body_not_the_path(client, world):
    response = client.post(f"/api/v1/scientific/networks/{world.ref}/assoc/kinetics/select", json=question(world))
    assert response.status_code == 404


def test_unknown_query_keys_are_refused_on_post(client, world):
    response = client.post(url(world.ref) + "?channel_key=assoc", json=question(world))
    assert response.status_code == 422 and "post_search_fields_must_be_in_body" in response.text


# -- bounds are coded 422s ---------------------------------------------------------------------------------------


def test_too_many_required_outputs_is_a_coded_422_and_nothing_is_assessed(client, world):
    outputs = [{"channel_key": f"c{i}", "observable": "product_resolved_coefficient"} for i in range(201)]
    request = question(world, scope="full_network", channel_key=None, observable=None, outputs=outputs)
    response = post(client, world, request)
    assert response.status_code == 422 and code(response) == "network_selection_population_too_large"
    text = str(response.json())
    assert "required outputs" in text and "limit" in text, text


def test_a_population_over_a_bound_is_refused_not_truncated(client, db_session, world, monkeypatch):
    import dataclasses

    from app.services.network_selection import public
    from app.services.network_selection.models import SelectionBounds

    original = public.to_service_request
    monkeypatch.setattr(
        public,
        "to_service_request",
        lambda body, *, network_ref: dataclasses.replace(original(body, network_ref=network_ref), bounds=SelectionBounds(solves=1)),
    )
    add_solve(db_session, world, fits=[fit_spec("assoc")], protocol=protocol(A_))
    add_solve(db_session, world, fits=[fit_spec("assoc")], protocol=protocol(B_))
    response = post(client, world)
    assert response.status_code == 422 and code(response) == "network_selection_population_too_large"


# -- the manifest ------------------------------------------------------------------------------------------------


def test_the_manifest_replays_with_no_database_and_its_digest_verifies(client, db_session, world, two, active_rule):
    response = post(client, world, manifest=True)
    assert response.status_code == 200
    assert response.headers["content-disposition"] == f'attachment; filename="network_selection_{world.ref}.json"'
    manifest = response.json()
    db_session.rollback()
    assert replay_network(manifest, rules=[RULE]) == manifest["decision"]
    assert manifest["snapshot_isolation"] and manifest["outcome"] == "policy_preferred"
    forged = copy.deepcopy(manifest)
    forged["decision"]["basis"] = "edited"
    with pytest.raises(ReplayError, match="recorded digest"):
        replay_network(forged, rules=[RULE])


def test_the_two_routes_agree(client, world, two, active_rule):
    answer = post(client, world).json()
    manifest = post(client, world, manifest=True).json()
    assert answer["outcome"] == manifest["outcome"]
    assert answer["administrative_order"] == manifest["decision"]["administrative_order"]
    assert answer["fronts"] == manifest["decision"]["fronts"]
    assert answer["determinations"] == manifest["assessments"]["determinations"]


def test_no_database_id_appears_in_either_document(client, world, two, active_rule):
    for manifest in (False, True):
        document = post(client, world, manifest=manifest).json()
        offenders = [
            (path, key)
            for path, key, value in walk(document)
            if key not in NOT_ROW_IDS and (key == "id" or key.endswith("_id") or key.endswith("_ids")) and value is not None
        ]
        assert offenders == [], offenders


def _approve_network(session, world) -> None:
    from app.db.models.common import SubmissionRecordType
    from app.services.record_review import ensure_record_review, set_record_review_status

    ensure_record_review(session, record_type=SubmissionRecordType.network, record_id=world.network.id)
    set_record_review_status(
        session, record_type=SubmissionRecordType.network, record_id=world.network.id, status=S.approved, actor=world.actor
    )


# -- visibility --------------------------------------------------------------------------------------------------


def test_under_a_curated_profile_a_solve_below_the_floor_is_neither_listed_nor_counted(client, db_session, world):
    _approve_network(db_session, world)
    add_solve(db_session, world, fits=[fit_spec("assoc")], protocol=protocol(A_), review=S.approved)
    hidden = add_solve(db_session, world, fits=[fit_spec("assoc")], protocol=protocol(B_), review=S.not_reviewed)
    curated = post(client, world, profile="curated").json()
    assert curated["disclosures"]["excluded_by_review_withheld"] is True
    assert curated["disclosures"]["excluded_count"] == 0 and curated["disclosures"]["excluded_by_review"] == []
    assert hidden.public_ref not in str(curated)
    manifest = post(client, world, profile="curated", manifest=True).json()
    assert manifest["population"]["excluded_by_review"] == [] and manifest["population"]["excluded_count"] == 0
    assert manifest["population"]["excluded_by_review_withheld"] is True  # the manifest route redacts too
    assert hidden.public_ref not in str(manifest)
    assert replay_network(manifest) == manifest["decision"]  # a redacted manifest still replays


def test_under_the_exploratory_profile_a_rejected_solve_is_listed(client, db_session, world):
    add_solve(db_session, world, fits=[fit_spec("assoc")], protocol=protocol(A_), review=S.approved)
    rejected = add_solve(db_session, world, fits=[fit_spec("assoc")], protocol=protocol(B_), review=S.rejected)
    set_review(db_session, world, rejected, S.rejected)
    out = post(client, world, profile="exploratory").json()
    assert out["disclosures"]["excluded_by_review_withheld"] is False
    assert [e["solve_ref"] for e in out["disclosures"]["excluded_by_review"]] == [rejected.public_ref]
    assert out["disclosures"]["excluded_count"] == 1


def test_min_review_status_raises_the_floor(client, db_session, world, two):
    a, b = two
    out = post(client, world, question(world, min_review_status="approved")).json()
    assert out["review"]["effective_floor"] == "approved"
    assert out["outcome"] == "sole_eligible_candidate"
    assert out["selection"]["solve_ref"] == b.public_ref


def test_under_a_curated_profile_a_hidden_reference_solve_is_absent_from_both_documents(client, db_session, world):
    """A validation entry may cite a solve the caller cannot see; the response and manifest must not carry its ref."""
    from tests.services.network_selection._world import validity

    _approve_network(db_session, world)
    hidden = add_solve(db_session, world, fits=[fit_spec("assoc")], protocol=protocol(B_), review=S.not_reviewed)
    cites = {"version": 1, "entries": [{"kind": "model_fidelity", "domain": validity(), "reference_solve_ref": hidden.public_ref}]}
    visible = add_solve(
        db_session, world, fits=[fit_spec("assoc")], protocol=protocol(A_), validation=cites, review=S.approved
    )
    manifest = post(client, world, profile="curated", manifest=True).json()
    entry = next(s for s in manifest["solves"] if s["solve_ref"] == visible.public_ref)["validation"]["entries"][0]
    assert entry["reference_solve_ref"] is None and entry["reference_withheld"] is True
    assert hidden.public_ref not in str(manifest)
    assert hidden.public_ref not in str(post(client, world, profile="curated").json())
    # Control: under the exploratory profile the same solve is visible, and its ref is served.
    exploratory = post(client, world, profile="exploratory", manifest=True).json()
    served = next(s for s in exploratory["solves"] if s["solve_ref"] == visible.public_ref)["validation"]["entries"][0]
    assert served["reference_solve_ref"] == hidden.public_ref
