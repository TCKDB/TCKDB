"""``POST /scientific/reaction-entries/{ref}/kinetics/select``: the public kinetics selection endpoint.

The assessor, engine, rules and manifest are tested under ``tests/services/kinetics_selection``. These tests hold
the HTTP contract: the request is normalised or refused, every outcome is reachable, the response says why,
visibility follows the read profile, the manifest replays, and no database id leaves the building.

The rule registry shipped in this release has no active rule, so the end-to-end preference tests swap in a
test-only active rule at the service boundary (``selection.default_rules``), exactly as the service tests do.
"""

from __future__ import annotations

import copy
import json

import pytest

from app.db.models.common import RecordReviewStatus as S
from app.services.kinetics_selection import public as public_module
from app.services.kinetics_selection import replay_decision, replay_matches
from app.services.kinetics_selection import selection as selection_module
from app.services.kinetics_selection.rules import default_rules
from tests.services.kinetics_selection.test_engine import LabelRule
from tests.services.kinetics_selection.test_selection import ALLOWED_INTEGER_PATHS, _paths
from tests.services.kinetics_selection.test_service import declared, determination
from tests.services.scientific_read._factories import make_species, make_transition_state, next_inchi_key

PREFERRED = {"version": 1, "method_kind": "saddle_point_tst"}
YIELDING = {"version": 1, "method_kind": "variational_tst"}
RULE = LabelRule("TEST-R1", {"saddle_point_tst"}, {"variational_tst"})

BASE = {
    "direction": "forward",
    "target": {"kind": "whole_reaction"},
    "coefficient_basis": "elementary_coefficient",
    "temperature_min_k": 500.0,
    "temperature_max_k": 1500.0,
    "pressure": {"kind": "independent"},
}


def body(**changes):
    return {**copy.deepcopy(BASE), **changes}


def url(entry, *, manifest: bool = False) -> str:
    return f"/api/v1/scientific/reaction-entries/{entry.public_ref}/kinetics/select" + ("/manifest" if manifest else "")


def post(client, entry, request=None, *, profile: str | None = None, manifest: bool = False):
    params = {"profile": profile} if profile else None
    return client.post(url(entry, manifest=manifest), json=body() if request is None else request, params=params)


def with_protocol(session, world, key, protocol, **kw):
    det = determination(session, world, key)

    def attach(k):
        k.protocol_declaration = protocol
        session.flush()

    return declared(session, world, det, children=attach, **kw), det


@pytest.fixture
def two(db_session, world):
    old_good, det_good = with_protocol(db_session, world, "good", PREFERRED, status=S.not_reviewed)
    new_other, det_other = with_protocol(db_session, world, "other", YIELDING, status=S.approved)
    return old_good, det_good, new_other, det_other


@pytest.fixture
def active_rule(monkeypatch):
    monkeypatch.setattr(selection_module, "default_rules", lambda: (RULE,))


def code(response) -> str:
    return response.json()["code"]


# -- the headline: a scoped preference outranks recency, end to end ---------------------------------------------


def test_an_older_preferred_determination_is_selected_over_a_newer_one_while_browse_order_is_unchanged(
    client, db_session, world, two, active_rule
):
    good, det_good, other, det_other = two
    browse = client.get(f"/api/v1/scientific/reaction-entries/{world.entry.public_ref}/kinetics")
    assert browse.status_code == 200
    browse_refs = [r["kinetics_ref"] for r in browse.json()["records"]]
    assert browse_refs[0] == other.public_ref  # browse: review first, so the approved one

    response = post(client, world.entry)
    assert response.status_code == 200, response.text
    out = response.json()
    assert out["outcome"] == "policy_preferred"
    assert out["selection"] == {
        "determination_ref": det_good.public_ref,
        "kinetics_refs": [good.public_ref],
        "basis": "policy_preferred",
        "administrative": False,
        "explanation": out["basis"],
    }
    assert out["fronts"] == [[det_good.public_ref], [det_other.public_ref]]
    assert out["administrative_order"][0] == det_other.public_ref  # administrative order alone says the other
    (edge,) = out["relations"]["edges"]
    assert (edge["preferred"], edge["dispreferred"], edge["rule_id"]) == (det_good.public_ref, det_other.public_ref, "TEST-R1")
    assert edge["label"] == "expected-performance inference"
    assert out["policy"]["name"] == "kinetics_method_preferred" and out["policy"]["rules_applied"] is True
    assert [r.public_ref for r in (good, other)] and browse_refs == [r["kinetics_ref"] for r in client.get(
        f"/api/v1/scientific/reaction-entries/{world.entry.public_ref}/kinetics"
    ).json()["records"]]


def test_with_no_active_rule_the_same_population_is_unranked_and_says_so(client, db_session, world, two):
    out = post(client, world.entry).json()
    assert out["outcome"] == "incomparable_alternatives" and out["selection"] is None
    assert out["relations"]["edges"] == []
    assert {r["status"] for r in out["policy"]["rules"]} == {"active", "inactive"} - {"active"}
    assert all(r["inactive_reasons"] for r in out["policy"]["rules"])
    assert default_rules() and len(out["policy"]["rules"]) == len(default_rules())


def test_the_response_echoes_the_normalised_request_and_the_effective_floor(client, db_session, world, two):
    out = post(client, world.entry, body(phase="gas", quantity="rate_coefficient")).json()
    echo = out["request"]
    assert echo["reaction_entry_ref"] == world.entry.public_ref
    assert echo["quantity"] == "rate_coefficient" and echo["phase"] == "gas" and echo["direction"] == "forward"
    assert echo["target"] == {"kind": "whole_reaction", "transition_state_entry_ref": None, "network_ref": None, "channel_key": None}
    assert echo["pressure"] == {"kind": "independent", "min_bar": None, "max_bar": None}
    assert echo["collider"] is None and echo["policy"] == "method_preferred" and echo["mode"] == "all"
    assert echo["profile"] == "exploratory"
    assert out["review"]["effective_floor"] == "not_reviewed"
    assert out["review"]["effective_statuses"] == ["approved", "under_review", "not_reviewed"]


# -- every outcome is reachable ---------------------------------------------------------------------------------


def test_no_records_is_no_applicable_candidate_and_names_nothing(client, world):
    out = post(client, world.entry).json()
    assert out["outcome"] == "no_applicable_candidate" and out["selection"] is None
    assert out["candidates"] == [] and out["disclosures"]["visible_candidates"] == 0


def test_a_single_qualifying_determination_is_sole_eligible_not_preferred(client, db_session, world):
    k, det = with_protocol(db_session, world, "only", PREFERRED, status=S.approved)
    out = post(client, world.entry).json()
    assert out["outcome"] == "sole_eligible_candidate"
    assert out["selection"]["basis"] == "sole_eligible_candidate" and out["selection"]["administrative"] is False
    assert out["selection"]["determination_ref"] == det.public_ref and out["selection"]["kinetics_refs"] == [k.public_ref]


def test_opposing_rules_are_a_policy_conflict_and_nothing_is_selected_even_in_first_mode(client, db_session, world, two, monkeypatch):
    monkeypatch.setattr(
        selection_module, "default_rules",
        lambda: (RULE, LabelRule("TEST-R2", {"variational_tst"}, {"saddle_point_tst"})),
    )
    for mode in ("all", "first"):
        out = post(client, world.entry, body(mode=mode)).json()
        assert out["outcome"] == "policy_conflict" and out["selection"] is None, mode
        assert out["relations"]["opposing_pairs"] or out["relations"]["cycles"]


def test_first_mode_names_an_administrative_first_among_unranked_alternatives_and_says_it_is_not_a_method_claim(
    client, db_session, world, two
):
    good, det_good, other, det_other = two
    out = post(client, world.entry, body(mode="first")).json()
    assert out["outcome"] == "incomparable_alternatives"
    pick = out["selection"]
    assert pick["basis"] == "administrative_first" and pick["administrative"] is True
    assert pick["determination_ref"] == det_other.public_ref and pick["kinetics_refs"] == [other.public_ref]
    assert "not a" in pick["explanation"] or "administrative" in pick["explanation"]
    assert post(client, world.entry, body(mode="all")).json()["selection"] is None


def test_a_selected_determination_returns_every_eligible_fitted_representation_together(client, db_session, world):
    det = determination(db_session, world, "one-determination")
    a = declared(db_session, world, det, status=S.approved)
    b = declared(db_session, world, det, status=S.approved)
    out = post(client, world.entry).json()
    assert out["outcome"] == "sole_eligible_candidate"
    assert sorted(out["selection"]["kinetics_refs"]) == sorted([a.public_ref, b.public_ref])
    (grouped,) = out["determinations"]
    assert grouped["representation_count"] == 2 and grouped["determination_ref"] == det.public_ref


def test_a_record_that_does_not_state_its_question_is_disclosed_unresolved_and_does_not_compete(client, db_session, world, two):
    from app.db.models.common import SubmissionRecordType
    from tests.services.scientific_read._factories import make_kinetics, set_review

    legacy = make_kinetics(db_session, reaction_entry=world.entry)
    set_review(db_session, record_type=SubmissionRecordType.kinetics, record_id=legacy.id, status=S.approved)
    out = post(client, world.entry).json()
    assert out["disclosures"]["unresolved_refs"] == [legacy.public_ref]
    row = next(c for c in out["candidates"] if c["kinetics_ref"] == legacy.public_ref)
    assert row["applicability"] == "unresolved" and row["eligible"] is False
    assert out["selection"] is None or out["selection"]["determination_ref"] != row["determination_ref"]
    assert "unresolved" in " ".join(out["disclosures"]["notes"])


def test_an_administrative_policy_applies_no_rule(client, db_session, world, two, active_rule):
    for policy in ("default", "most_reviewed", "latest"):
        out = post(client, world.entry, body(policy=policy)).json()
        assert out["outcome"] == "incomparable_alternatives" and out["policy"]["rules_applied"] is False, policy
        assert out["policy"]["rules"] == [] and out["relations"]["edges"] == []
    assert post(client, world.entry, body(policy="latest")).json()["administrative_order"] == post(
        client, world.entry, body(policy="latest", mode="first")
    ).json()["administrative_order"]


# -- the request is normalised or refused -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "changes",
    [
        {"phase": "liquid"},
        {"direction": "net"},
        {"direction": "sideways"},
        {"quantity": "rate_of_progress"},
        {"temperature_min_k": 0},
        {"temperature_min_k": -3.0},
        {"temperature_max_k": float("nan")},
        {"temperature_min_k": 2000.0},  # min above max
        {"pressure": {"kind": "finite", "min_bar": 1.0, "max_bar": 1.0}},  # finite without a collider
        {"pressure": {"kind": "finite", "min_bar": 2.0, "max_bar": 1.0}, "collider": {"components": [{"species_ref": "spc_x"}]}},
        {"pressure": {"kind": "finite", "min_bar": 1.0}, "collider": {"components": [{"species_ref": "spc_x"}]}},
        {"pressure": {"kind": "independent", "min_bar": 1.0}},
        {"pressure": {"kind": "high_pressure_limit", "max_bar": 1.0}},
        {"pressure": {"kind": "finite", "min_bar": 0, "max_bar": 1.0}, "collider": {"components": [{"species_ref": "spc_x"}]}},
        {"coefficient_basis": "composition_effective_coefficient"},  # needs a collider
        {"coefficient_basis": "rate_of_progress"},
        {"target": {"kind": "whole_reaction", "network_ref": "net_x", "channel_key": "c"}},
        {"target": {"kind": "resolved_channel"}},
        {"target": {"kind": "resolved_channel", "transition_state_entry_ref": "tse_x", "network_ref": "net_x", "channel_key": "c"}},
        {"target": {"kind": "resolved_channel", "network_ref": "net_x"}},
        {"collider": {"components": [{"species_ref": "spc_a"}, {"species_ref": "spc_a"}]}},
        {"collider": {"components": [{"species_ref": "spc_a", "mole_fraction": 0.4}, {"species_ref": "spc_b", "mole_fraction": 0.4}]}},
        {"collider": {"components": [{"species_ref": "spc_a", "mole_fraction": 0.5}, {"species_ref": "spc_b"}]}},
        {"collider": {"components": [{"species_ref": "spc_a", "mole_fraction": 0.5}]}},
        {"collider": {"components": []}},
        {"policy": "benchmark_reference"},
        {"mode": "best"},
        {"max_candidates": 5},  # no cap field
        {"reaction_entry_id": 1},
        {"kinetics_id": 1},
    ],
)
def test_a_request_that_is_not_a_well_posed_gas_phase_rate_question_is_refused_with_422(client, world, changes):
    request = body(**changes)
    # json cannot carry NaN; send it as the sentinel the server must refuse
    text = json.dumps(request, allow_nan=True)
    response = client.post(url(world.entry), content=text, headers={"content-type": "application/json"})
    assert response.status_code == 422, (changes, response.text)
    # refused by the request's own rules, not by a later reference check
    assert response.json()["code"] == "request_validation_error", (changes, response.text)


def test_the_path_takes_a_public_reaction_entry_ref_and_refuses_an_integer_or_another_kind(client, world):
    root = "/api/v1/scientific/reaction-entries"
    by_id = client.post(f"{root}/{world.entry.id}/kinetics/select", json=body())
    assert by_id.status_code == 422 and by_id.json()["code"] == "invalid_handle"
    by_id_manifest = client.post(f"{root}/{world.entry.id}/kinetics/select/manifest", json=body())
    assert by_id_manifest.status_code == 422 and by_id_manifest.json()["code"] == "invalid_handle"
    other = client.post(f"{root}/spe_deadbeef/kinetics/select", json=body())
    assert other.status_code == 422 and other.json()["code"] == "handle_type_mismatch"
    assert client.post(f"{root}/rxe_doesnotexist/kinetics/select", json=body()).status_code == 404


def test_post_search_fields_must_be_in_the_body(client, world):
    response = client.post(url(world.entry) + "?direction=forward", json=body())
    assert response.status_code == 422 and "post_search_fields_must_be_in_body" in response.text


def test_every_public_ref_the_request_names_must_exist(client, db_session, world):
    unknown = body(target={"kind": "resolved_channel", "transition_state_entry_ref": "tse_nope"})
    assert post(client, world.entry, unknown).status_code == 404
    net = body(target={"kind": "resolved_channel", "network_ref": "net_nope", "channel_key": "c"})
    assert post(client, world.entry, net).status_code == 404
    collider = body(pressure={"kind": "finite", "min_bar": 1.0, "max_bar": 1.0}, collider={"components": [{"species_ref": "spc_nope"}]})
    assert post(client, world.entry, collider).status_code == 404
    wrong_kind = body(pressure={"kind": "finite", "min_bar": 1.0, "max_bar": 1.0}, collider={"components": [{"species_ref": world.entry.public_ref}]})
    assert post(client, world.entry, wrong_kind).status_code == 422


def test_a_named_collider_and_a_mixture_are_echoed_as_components(client, db_session, world):
    n2 = make_species(db_session, smiles="N#N", multiplicity=1, inchi_key=next_inchi_key("KSN"))
    ar = make_species(db_session, smiles="[Ar]", multiplicity=1, inchi_key=next_inchi_key("KSR"))
    finite = {"kind": "finite", "min_bar": 1.0, "max_bar": 10.0}
    one = post(client, world.entry, body(pressure=finite, collider={"components": [{"species_ref": n2.public_ref}]}))
    assert one.status_code == 200, one.text
    assert one.json()["request"]["collider"] == {"components": [{"species_ref": n2.public_ref, "mole_fraction": None}]}
    mix = {"components": [{"species_ref": n2.public_ref, "mole_fraction": 0.79}, {"species_ref": ar.public_ref, "mole_fraction": 0.21}]}
    two_ = post(client, world.entry, body(pressure=finite, collider=mix))
    assert two_.status_code == 200, two_.text
    assert two_.json()["request"]["collider"] == mix
    assert two_.json()["request"]["pressure"] == finite


def test_a_resolved_channel_names_a_visible_transition_state_entry(client, db_session, world):
    from tests.services.scientific_read._factories import make_transition_state_entry

    ts = make_transition_state(db_session, reaction_entry=world.entry)
    tse = make_transition_state_entry(db_session, transition_state=ts)
    request = body(target={"kind": "resolved_channel", "transition_state_entry_ref": tse.public_ref})
    out = post(client, world.entry, request)
    assert out.status_code == 200, out.text
    assert out.json()["request"]["target"]["transition_state_entry_ref"] == tse.public_ref
    assert out.json()["outcome"] == "no_applicable_candidate"


# -- the candidate cap ------------------------------------------------------------------------------------------


def test_more_visible_candidates_than_the_cap_is_a_coded_422_and_nothing_is_assessed(client, db_session, world, two, monkeypatch):
    from app.services.kinetics_selection import loader

    monkeypatch.setattr(public_module, "MAX_CANDIDATES", 1)
    called = []
    real = loader.load_population
    monkeypatch.setattr(loader, "load_population", lambda *a, **k: called.append(1) or real(*a, **k))
    response = post(client, world.entry)
    assert response.status_code == 422
    assert code(response) == "kinetics_selection_population_too_large"
    assert response.json()["context"] == {"limit": 1, "visible_candidates": 2}
    assert called == []


def test_the_cap_counts_only_what_the_caller_can_see(client, db_session, world, two, monkeypatch):
    monkeypatch.setattr(public_module, "MAX_CANDIDATES", 1)
    # two records exist; under curated only the approved one is visible, so one is within a cap of one
    assert post(client, world.entry, profile="curated").status_code == 200
    assert post(client, world.entry).status_code == 422


# -- visibility -------------------------------------------------------------------------------------------------


def test_under_curated_a_hidden_record_is_in_no_ref_no_count_and_no_manifest_arithmetic(client, db_session, world, two):
    good, det_good, other, det_other = two  # `good` is not reviewed: hidden under the curated floor
    resp = post(client, world.entry, profile="curated")
    assert resp.status_code == 200, resp.text
    text = resp.text
    assert good.public_ref not in text and det_good.public_ref not in text
    out = resp.json()
    assert out["outcome"] == "sole_eligible_candidate" and out["selection"]["determination_ref"] == det_other.public_ref
    disclosures = out["disclosures"]
    assert disclosures["excluded_by_review"] == [] and disclosures["excluded_by_review_withheld"] is True
    assert disclosures["excluded_count"] == 0 and disclosures["visible_candidates"] == 1
    manifest = post(client, world.entry, profile="curated", manifest=True)
    assert good.public_ref not in manifest.text and det_good.public_ref not in manifest.text
    population = manifest.json()["population"]
    assert population["kinetics_rows_for_entry"] == 1 and population["excluded_count"] == 0
    assert population["excluded_by_review"] == [] and population["excluded_by_review_withheld"] is True


def test_curated_answers_are_the_same_whether_or_not_hidden_records_exist(client, db_session, world):
    approved, det = with_protocol(db_session, world, "shown", PREFERRED, status=S.approved)
    before = post(client, world.entry, profile="curated").json()
    with_protocol(db_session, world, "hidden-1", YIELDING, status=S.not_reviewed)
    with_protocol(db_session, world, "hidden-2", PREFERRED, status=S.under_review)
    after = post(client, world.entry, profile="curated").json()
    assert after == before
    assert post(client, world.entry, profile="curated", manifest=True).json() == post(
        client, world.entry, profile="curated", manifest=True
    ).json()
    assert approved.public_ref in json.dumps(before) and det.public_ref in json.dumps(before)


def test_a_transition_state_the_curated_floor_hides_is_the_same_404_as_an_unknown_one(client, db_session, world):
    from tests.services.scientific_read._factories import make_transition_state_entry

    ts = make_transition_state(db_session, reaction_entry=world.entry)
    tse = make_transition_state_entry(db_session, transition_state=ts)
    request = body(target={"kind": "resolved_channel", "transition_state_entry_ref": tse.public_ref})
    assert post(client, world.entry, request, profile="exploratory").status_code == 200
    hidden = post(client, world.entry, request, profile="curated")
    unknown = post(
        client, world.entry, body(target={"kind": "resolved_channel", "transition_state_entry_ref": "tse_nope"}), profile="curated"
    )
    assert hidden.status_code == unknown.status_code == 404
    assert hidden.json()["code"] == unknown.json()["code"]


def test_under_the_exploratory_profile_records_below_the_floor_are_listed_with_their_reason(client, db_session, world):
    shown, _ = with_protocol(db_session, world, "shown", PREFERRED, status=S.approved)
    rejected, _ = with_protocol(db_session, world, "rejected", PREFERRED, status=S.rejected)
    out = post(client, world.entry).json()
    assert out["disclosures"]["excluded_by_review_withheld"] is False and out["disclosures"]["excluded_count"] == 1
    assert [e["kinetics_ref"] for e in out["disclosures"]["excluded_by_review"]] == [rejected.public_ref]
    assert shown.public_ref in json.dumps(out["selection"])
    floor = post(client, world.entry, body(min_review_status="approved")).json()
    assert floor["review"]["effective_floor"] == "approved"


# -- the manifest -----------------------------------------------------------------------------------------------


def test_the_manifest_route_downloads_the_same_decision_and_replays_without_the_database(client, db_session, world, two, active_rule):
    response = post(client, world.entry, manifest=True)
    assert response.status_code == 200, response.text
    assert response.headers["content-disposition"] == f'attachment; filename="kinetics_selection_{world.entry.public_ref}.json"'
    wire = response.json()
    select = post(client, world.entry).json()
    assert wire["outcome"] == select["outcome"] == "policy_preferred"
    assert wire["decision"]["fronts"] == select["fronts"]
    assert wire["policy"] == {"name": "kinetics_method_preferred", "version": "1"}
    assert wire["snapshot_isolation"] in {"read committed", "repeatable read", "serializable"}
    assert "max_candidates" not in wire["request"] and wire["request"]["profile"] == "exploratory"
    assert replay_matches(wire, rules=(RULE,))
    assert replay_decision(wire, rules=(RULE,)) == wire["decision"]
    # a later change of the registry refuses the replay rather than re-answering
    from app.services.kinetics_selection import ReplayError

    with pytest.raises(ReplayError):
        replay_decision(wire, rules=default_rules())


def test_the_response_and_the_manifest_cannot_disagree(client, db_session, world, two, active_rule):
    out = post(client, world.entry).json()
    wire = post(client, world.entry, manifest=True).json()
    assert [c["kinetics_ref"] for c in out["candidates"]] == [c["kinetics_ref"] for c in wire["candidates"]]
    assert [c["eligible"] for c in out["candidates"]] == [c["eligible"] for c in wire["candidates"]]
    assert out["relations"]["edges"] == wire["decision"]["edges"]
    assert out["administrative_order"] == wire["decision"]["administrative_order"]
    assert out["rule_matches"] == wire["decision"]["rule_matches"]
    assert out["disclosures"]["visible_candidates"] == wire["population"]["visible_candidates"]


# -- what neither document may carry ---------------------------------------------------------------------------


def _manifest_paths(wire):
    return _paths(wire)


def _keys(value):
    found = set()
    if isinstance(value, dict):
        for k, v in value.items():
            found.add(k)
            found |= _keys(v)
    elif isinstance(value, list):
        for v in value:
            found |= _keys(v)
    return found


#: Every integer the response may hold, by exact path: counts, orders and ordinals, never a row id.
ALLOWED_RESPONSE_INTEGER_PATHS = {
    "disclosures.visible_candidates", "disclosures.excluded_count", "determinations[].representation_count",
    "candidates[].applicability_declaration.version", "candidates[].applicability_declaration.reaction_order",
    "candidates[].protocol.version",
}


def test_no_database_id_appears_in_either_document_and_integers_sit_at_exact_paths(client, db_session, world, two, active_rule):
    good, det_good, other, det_other = two
    out = post(client, world.entry)
    wire = post(client, world.entry, manifest=True)
    for doc in (out.json(), wire.json()):
        # no key names a row id: the only ``*_id`` key is the rule registry key, which is not a database id
        assert {k for k in _keys(doc) if k.endswith("_id")} <= {"rule_id"}, sorted(_keys(doc))
        assert "id" not in _keys(doc)
        text = json.dumps(doc)
        for row in (world.entry, good, other, det_good, det_other):
            assert row.public_ref in text or row is world.entry
    manifest_paths = _paths(wire.json())
    allowed = ALLOWED_INTEGER_PATHS
    assert manifest_paths <= allowed, sorted(manifest_paths - allowed)
    response_paths = _paths(out.json())
    assert response_paths <= ALLOWED_RESPONSE_INTEGER_PATHS, sorted(response_paths - ALLOWED_RESPONSE_INTEGER_PATHS)
    assert {"disclosures.visible_candidates", "determinations[].representation_count"} <= response_paths


def test_every_ref_in_a_response_is_a_public_ref_of_the_right_kind(client, db_session, world, two, active_rule):
    good, det_good, other, det_other = two
    out = post(client, world.entry).json()
    assert {c["kinetics_ref"][:4] for c in out["candidates"]} == {"kin_"}
    assert {d["determination_ref"][:5] for d in out["determinations"]} == {"kdet_"}
    assert out["request"]["reaction_entry_ref"].startswith("rxe_")
    flat = {r for front in out["fronts"] for r in front}
    assert flat == {det_good.public_ref, det_other.public_ref}


def test_the_endpoint_is_read_only_and_leaves_browse_order_alone(client, db_session, world, two, active_rule):
    from sqlalchemy import func, select

    from app.db.models.common import SubmissionRecordType  # noqa: F401
    from app.db.models.kinetics import Kinetics, KineticsDetermination
    from app.db.models.record_review import RecordReview

    def counts():
        return tuple(db_session.scalar(select(func.count()).select_from(m)) for m in (Kinetics, KineticsDetermination, RecordReview))

    before = counts()
    list_url = f"/api/v1/scientific/reaction-entries/{world.entry.public_ref}/kinetics"
    browse = [r["kinetics_ref"] for r in client.get(list_url).json()["records"]]
    post(client, world.entry)
    post(client, world.entry, manifest=True)
    assert counts() == before
    assert [r["kinetics_ref"] for r in client.get(list_url).json()["records"]] == browse


# -- the snapshot ---------------------------------------------------------------------------------------------


def test_both_routes_get_their_session_from_the_snapshot_dependency_and_not_from_get_db():
    from fastapi.routing import APIRoute

    from app.api.app import create_app
    from app.api.deps import get_db, get_snapshot_db

    app = create_app()
    routes = [
        r for r in app.routes
        if isinstance(r, APIRoute)
        and r.path.startswith("/api/v1/scientific/reaction-entries/")
        and r.path.endswith(("/kinetics/select", "/kinetics/select/manifest"))
    ]
    assert len(routes) == 2

    for route in routes:
        direct = [dep.call for dep in route.dependant.dependencies]
        assert get_snapshot_db in direct, route.path
        # ``get_db`` is reachable only through the router-level profile dependency, never as the route's session
        assert get_db not in direct, route.path
        assert next(p for p in route.dependant.dependencies if p.call is get_snapshot_db).name == "session"


def test_the_manifest_route_checks_every_named_ref_too(client, world):
    unknown = body(target={"kind": "resolved_channel", "transition_state_entry_ref": "tse_nope"})
    assert post(client, world.entry, unknown, manifest=True).status_code == 404
    net = body(target={"kind": "resolved_channel", "network_ref": "net_nope", "channel_key": "c"})
    assert post(client, world.entry, net, manifest=True).status_code == 404
    collider = body(pressure={"kind": "finite", "min_bar": 1.0, "max_bar": 1.0}, collider={"components": [{"species_ref": "spc_nope"}]})
    assert post(client, world.entry, collider, manifest=True).status_code == 404


def test_the_read_profile_the_answer_was_made_under_is_stamped_on_the_response(client, db_session, world, two):
    assert post(client, world.entry, profile="curated").json()["request"]["profile"] == "curated"
    assert post(client, world.entry).json()["request"]["profile"] == "exploratory"
    assert post(client, world.entry, profile="curated", manifest=True).json()["request"]["profile"] == "curated"


def test_edges_under_two_objectives_are_listed_unused_and_nothing_is_selected(client, db_session, world, two, monkeypatch):
    monkeypatch.setattr(
        selection_module, "default_rules",
        lambda: (
            LabelRule("TEST-R1", {"saddle_point_tst"}, {"variational_tst"}, objective_key="barrier"),
            LabelRule("TEST-R2", {"saddle_point_tst"}, {"variational_tst"}, objective_key="tunneling"),
        ),
    )
    out = post(client, world.entry).json()
    assert out["outcome"] == "incomparable_alternatives" and out["selection"] is None
    assert out["relations"]["edges"] == []
    assert {e["objective_key"] for e in out["relations"]["unused_edges"]} == {"barrier", "tunneling"}
    assert "more than one objective" in out["basis"]


# -- the public layer, where the live service already guarantees what the layer also guards -------------------


def _manifest_with_excluded():
    return {
        "request": {"max_candidates": 500, "effective_review_statuses": ["approved"]},
        "population": {
            "kinetics_rows_for_entry": 9, "visible_candidates": 2, "assessed": 2, "excluded_count": 7,
            "excluded_by_review": [{"kinetics_ref": "kin_x", "review_status": "not_reviewed", "reason": "below_review_floor"}],
        },
        "decision": {"outcome": "incomparable_alternatives"},
    }


def test_redaction_withholds_what_the_floor_excluded_and_the_arithmetic_that_would_reveal_it():
    original = _manifest_with_excluded()
    out = public_module.redact_manifest(original, withhold_excluded=True)
    population = out["population"]
    assert population["excluded_by_review"] == [] and population["excluded_count"] == 0
    assert population["kinetics_rows_for_entry"] == population["visible_candidates"] == 2
    assert population["excluded_by_review_withheld"] is True
    assert "max_candidates" not in out["request"] and original["population"]["excluded_count"] == 7  # a copy
    assert out["decision"] == original["decision"]
    shown = public_module.redact_manifest(original, withhold_excluded=False)["population"]
    assert shown["excluded_count"] == 7 and shown["kinetics_rows_for_entry"] == 9 and len(shown["excluded_by_review"]) == 1
    assert shown["excluded_by_review_withheld"] is False


def test_a_first_pick_is_never_made_through_a_conflict_whatever_the_decision_carries():
    from app.schemas.reads.scientific_kinetics_selection import KineticsSelectionMode

    manifest = {
        "determinations": [{"determination_ref": "kdet_a", "representation_refs": ["kin_a"]}],
        "decision": {
            "outcome": "policy_conflict", "basis": "b", "selected_determination_ref": None, "representation_refs": [],
            "administrative_first": {"determination_ref": "kdet_a", "basis": "administrative"},
        },
    }
    assert public_module._pick(manifest, KineticsSelectionMode.first) is None
    manifest["decision"]["outcome"] = "incomparable_alternatives"
    pick = public_module._pick(manifest, KineticsSelectionMode.first)
    assert pick is not None and pick.administrative and pick.kinetics_refs == ["kin_a"]
    assert public_module._pick(manifest, KineticsSelectionMode.all) is None


def test_a_single_collider_is_sent_to_the_service_without_a_fraction_and_a_mixture_with_them():
    from app.schemas.reads.scientific_kinetics_selection import KineticsSelectionRequest

    single = KineticsSelectionRequest.model_validate(
        body(pressure={"kind": "finite", "min_bar": 1.0, "max_bar": 1.0}, collider={"components": [{"species_ref": "spc_n2"}]})
    )
    assert public_module.to_service_request(single).collider.mole_fractions is None
    mixture = KineticsSelectionRequest.model_validate(
        body(
            pressure={"kind": "finite", "min_bar": 1.0, "max_bar": 1.0},
            collider={"components": [{"species_ref": "spc_n2", "mole_fraction": 0.5}, {"species_ref": "spc_ar", "mole_fraction": 0.5}]},
        )
    )
    assert public_module.to_service_request(mixture).collider.mole_fractions == (0.5, 0.5)


# -- the request schema refuses by itself, whatever the layers below would also say -------------------------------


def _refused(changes, match):
    from pydantic import ValidationError

    from app.schemas.reads.scientific_kinetics_selection import KineticsSelectionRequest

    with pytest.raises(ValidationError, match=match):
        KineticsSelectionRequest.model_validate(body(**changes))


@pytest.mark.parametrize(
    "changes,match",
    [
        ({"collider": {"components": [{"species_ref": "spc_a"}, {"species_ref": "spc_a"}]}}, "listed twice"),
        ({"collider": {"components": [{"species_ref": "spc_a", "mole_fraction": 0.5}]}}, "no mole fraction"),
        ({"collider": {"components": [{"species_ref": "spc_a", "mole_fraction": 0.5}, {"species_ref": "spc_b"}]}}, "every component"),
        (
            {"collider": {"components": [{"species_ref": "spc_a", "mole_fraction": 0.4}, {"species_ref": "spc_b", "mole_fraction": 0.4}]}},
            "never renormalised",
        ),
        ({"pressure": {"kind": "finite", "min_bar": 2.0, "max_bar": 1.0}}, "must not exceed"),
        ({"pressure": {"kind": "finite", "min_bar": 1.0}}, "needs min_bar and max_bar"),
        ({"pressure": {"kind": "independent", "max_bar": 1.0}}, "carries no bounds"),
        ({"temperature_min_k": 2000.0}, "must not exceed"),
        ({"phase": "liquid"}, "gas phase only"),
        ({"pressure": {"kind": "finite", "min_bar": 1.0, "max_bar": 1.0}}, "collider is required"),
        ({"coefficient_basis": "composition_effective_coefficient"}, "collider is required"),
        ({"target": {"kind": "whole_reaction", "channel_key": "c"}}, "names no transition state"),
        ({"target": {"kind": "resolved_channel"}}, "either a transition state entry or a network channel"),
        ({"target": {"kind": "resolved_channel", "network_ref": "net_a"}}, "needs both network_ref and channel_key"),
    ],
)
def test_each_rule_of_the_request_is_enforced_by_the_schema_itself(changes, match):
    _refused(changes, match)


def test_a_well_posed_request_validates_and_a_mixture_summing_to_one_within_the_shared_tolerance_does_too():
    from app.schemas.reads.scientific_kinetics_selection import KineticsSelectionRequest

    ok = body(
        pressure={"kind": "finite", "min_bar": 1.0, "max_bar": 1.0},
        collider={"components": [{"species_ref": "spc_a", "mole_fraction": 0.7}, {"species_ref": "spc_b", "mole_fraction": 0.3}]},
    )
    assert KineticsSelectionRequest.model_validate(ok).collider is not None
