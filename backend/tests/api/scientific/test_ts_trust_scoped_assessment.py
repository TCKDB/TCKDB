"""The scoped structure assessment inside the transition-state trust fragment (#723, rubric v5).

What this pins, and the mutation each group kills (the mutation table is in the PR):

* **Detail versus list (owner decision O3).** A single transition-state-entry detail read runs the assessment; every
  surface that lists entries carries the coded status ``not_assessed_on_lists`` and issues no statement for it, so the
  same entry renders the same on every list page.
* **Agreement (O1).** The block equals what ``POST .../evidence/select`` answers to the echoed question.
* **The representative frequency result (O2).** The frequency-presence checks read the result a declaration pins; an
  entry with no declaration keeps reading the newest attached result.
* **Owner conditions.** Label, completeness, counts and ``is_certified`` never move because of the assessment; the new
  warning has zero weight; an unresolved target is not a pass.
* **Curated (D6).** The assessment runs under the request's profile; a target resting on a hidden calculation is
  unresolved and warns; nothing is cached.
"""

from __future__ import annotations

import ast
import inspect
import json
import pathlib
import re

import pytest
from sqlalchemy import event

from app.api.error_contract import CodedValueError
from app.db.models.common import StructureDeterminationTargetKind, StructureFindingKind, StructureSourceRole
from app.services.structure_selection import DECISION_VERSION
from app.services.structure_selection.models import (
    ASSESSMENT_VERSION,
    NORMALIZER_VERSION,
    StructureOutcome,
)
from app.services.trust import (
    COMPUTED_TRANSITION_STATE_V5,
    EvidenceCheckKind,
    TransitionStateTrustFragment,
    TrustFragment,
    evaluate_computed_transition_state_entry,
)
from app.services.trust.models import (
    TransitionStateScopedAssessmentOutcome,
    TransitionStateScopedAssessmentStatus,
)
from app.services.trust.rubrics import TS_SCOPED_STRUCTURE_VERSIONS
from tests.services.scientific_read._factories import make_geometry
from tests.services.structure_selection._support import make_ts_world
from tests.services.structure_selection.test_service import build_basin

API = "/api/v1/scientific"
SADDLE = StructureDeterminationTargetKind.saddle_point
WARNING = "declared_saddle_target_qualifies"
UNDESIGNATED = "no_frequency_result_has_undesignated_imaginary_modes"
GOOD = (-1500.0, 100.0, 200.0)  # one imaginary mode
FLAT = (100.0, 200.0)  # none
THREE = (-1500.0, -800.0, -300.0, 100.0)  # several, no designation
BANNED = ("validated", "certified", "disproved", "not a saddle point")


def detail(client, entry, *, profile: str | None = None) -> dict:
    qs = "include=trust" + (f"&profile={profile}" if profile else "")
    response = client.get(f"{API}/transition-state-entries/{entry.public_ref}?{qs}")
    assert response.status_code == 200, response.text
    return response.json()["record"]["trust"]


def search(client, **params) -> dict:
    qs = "&".join(f"{k}={v}" for k, v in {"include": "trust", "charge": 0, **params}.items())
    response = client.get(f"{API}/transition-states/search?{qs}")
    assert response.status_code == 200, response.text
    return response.json()


def saddle(world, *, n_freq=GOOD, geometry=None, **kw):
    return build_basin(world, target=SADDLE, geometry=geometry or make_geometry(world.session), n_freq=n_freq, **kw)


def declared_world(db_session, **kw):
    world = make_ts_world(db_session)
    d, opt, sp, f = saddle(world, **kw)
    world.settle()
    return world, d, f


def select(client, entry, task: dict, *, profile: str | None = None) -> dict:
    qs = f"?profile={profile}" if profile else ""
    response = client.post(f"{API}/transition-state-entries/{entry.public_ref}/evidence/select{qs}", json=task)
    assert response.status_code == 200, response.text
    return response.json()


def count_statements(db_session, call):
    seen: list[str] = []

    def hook(conn, cursor, statement, *args):
        seen.append(statement)

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", hook)
    try:
        out = call()
    finally:
        event.remove(bind, "before_cursor_execute", hook)
    return out, len(seen)


# ---------------------------------------------------------------------------
# Detail versus list
# ---------------------------------------------------------------------------


def test_a_detail_read_runs_the_assessment_and_a_qualifying_target_adds_no_warning(client, db_session):
    world, d, f = declared_world(db_session)
    trust = detail(client, world.entry)
    block = trust["structure_assessment"]
    assert block["status"] == "assessed" and block["outcome"] == "qualified_evidence"
    assert block["qualifying_refs"] == [d.public_ref] and block["declared_targets"] == 1
    assert block["task"] == {
        "intent": "qualify_evidence",
        "quantity": None,
        "validation_claim": "first_order_saddle",
        "require_connectivity": False,
        "coverage_requirement": "known_values",
    }
    assert WARNING not in trust["evidence"]["checks"]  # a passed warning is absent from the map, as for every warning
    assert block["versions"] == {"assessment": ASSESSMENT_VERSION, "normalizer": NORMALIZER_VERSION, "decision": DECISION_VERSION}
    assert trust["is_certified"] is False


def test_no_declaration_is_not_a_missing_check_and_costs_exactly_one_statement(client, db_session, monkeypatch):
    from app.services.scientific_read import transition_states as service
    from app.services.trust.models import TransitionStateScopedAssessment, TransitionStateTrustContext
    from app.services.trust.ts_scoped import BADGE_TASK

    world = make_ts_world(db_session)
    url = f"{API}/transition-state-entries/{world.entry.public_ref}?include=trust"
    real = service.scoped_ts_context
    baseline = TransitionStateTrustContext(
        assessment=TransitionStateScopedAssessment(
            status=TransitionStateScopedAssessmentStatus.no_declared_target, task=BADGE_TASK, declared_targets=0
        )
    )
    monkeypatch.setattr(service, "scoped_ts_context", lambda session, entry: baseline)
    _, without = count_statements(db_session, lambda: client.get(url))
    monkeypatch.setattr(service, "scoped_ts_context", real)
    response, with_gate = count_statements(db_session, lambda: client.get(url))
    trust = response.json()["record"]["trust"]
    assert trust["structure_assessment"]["status"] == "no_declared_target"
    assert trust["structure_assessment"]["outcome"] is None and trust["structure_assessment"]["units"] == []
    assert trust["evidence"]["checks"][WARNING] == "not_applicable"
    assert with_gate - without == 1  # kills: the pipeline run unconditionally, or the gate missing


def test_a_list_never_runs_the_assessment_and_says_so_in_a_coded_status(client, db_session, monkeypatch):
    from app.services.scientific_read import transition_states as service

    world, d, f = declared_world(db_session)

    def forbidden(*args, **kwargs):
        raise AssertionError("a list surface ran the structure assessment")

    monkeypatch.setattr(service, "scoped_ts_context", forbidden)
    body = search(client)
    [record] = [r for r in body["records"] if r["transition_state_entry"]["transition_state_entry_ref"] == world.entry.public_ref]
    block = record["trust"]["structure_assessment"]
    assert block["status"] == "not_assessed_on_lists"
    assert block["outcome"] is None and block["units"] == [] and block["declared_targets"] is None
    assert record["trust"]["evidence"]["checks"][WARNING] == "not_applicable"


def _same_graph(world, *, declare: bool):
    """The same calculation graph on an entry; only ``declare`` adds the saddle determination row."""
    from app.db.models.common import CalculationDependencyRole
    from tests.services.scientific_read._factories import attach_dependency

    geometry = make_geometry(world.session)
    opt = world.opt("o", -40.2, geometry=geometry)
    sp = world.sp("s", -40.3, geometry=geometry)
    f = world.freq("f", frequencies=GOOD, geometry=geometry)
    attach_dependency(world.session, parent=opt, child=sp, role=CalculationDependencyRole.single_point_on)
    attach_dependency(world.session, parent=opt, child=f, role=CalculationDependencyRole.freq_on)
    if declare:
        world.determination([("geometry_optimization", opt), ("energy", sp), ("curvature", f)], evaluated=geometry, target=SADDLE)
    world.settle()


def test_a_list_costs_the_same_statements_whether_or_not_the_entry_declares_a_target(client, db_session):
    """Kills: the gate or the pipeline reachable from a list (statements would grow by the pipeline's ~30)."""
    declared, plain = make_ts_world(db_session), make_ts_world(db_session)
    _same_graph(declared, declare=True)
    _same_graph(plain, declare=False)

    def page(world):
        return count_statements(db_session, lambda: search(client, transition_state_entry_ref=world.entry.public_ref))

    page(plain)  # warm per-process caches before measuring
    body_plain, n_plain = page(plain)
    body_declared, n_declared = page(declared)
    assert n_declared == n_plain, (n_declared, n_plain)
    assert len(body_plain["records"]) == len(body_declared["records"]) == 1


def test_the_assessment_costs_a_bounded_number_of_statements_on_a_detail_read(client, db_session):
    """One declared target costs a fixed number of statements on its own page (measured: 46 over the same graph), and nothing on a list."""
    declared, plain = make_ts_world(db_session), make_ts_world(db_session)
    _same_graph(declared, declare=True)
    _same_graph(plain, declare=False)

    def page(world):
        return count_statements(db_session, lambda: detail(client, world.entry))[1]

    page(plain)
    extra = page(declared) - page(plain)
    assert 1 < extra <= 70, extra  # a runaway per-unit or per-calculation loop would break the upper bound


def test_the_same_entry_renders_the_same_on_two_different_list_pages(client, db_session):
    world, d, f = declared_world(db_session)
    other = make_ts_world(db_session)
    other.settle()
    wide = search(client, limit=50)

    def block_of(body, ref):
        for r in body["records"]:
            if r["transition_state_entry"]["transition_state_entry_ref"] == ref:
                return r["trust"]
        return None

    wide_trust = block_of(wide, world.entry.public_ref)
    assert wide_trust is not None
    rxe = world.entry.transition_state.reaction_entry
    second = search(client, reaction_entry_ref=rxe.public_ref)
    assert [r["transition_state_entry"]["transition_state_entry_ref"] for r in second["records"]] == [world.entry.public_ref]
    assert block_of(second, world.entry.public_ref) == wide_trust
    assert wide_trust["structure_assessment"]["status"] == "not_assessed_on_lists"


def test_the_siblings_of_a_detail_read_are_a_list_and_are_not_assessed(client, db_session):
    world, d, f = declared_world(db_session)
    response = client.get(f"{API}/transition-state-entries/{world.entry.public_ref}?include=trust,entries")
    assert response.status_code == 200, response.text
    record = response.json()["record"]
    assert record["trust"]["structure_assessment"]["status"] == "assessed"
    [own] = [e for e in record["entries"] if e["transition_state_entry"]["transition_state_entry_ref"] == world.entry.public_ref]
    assert own["trust"]["structure_assessment"]["status"] == "not_assessed_on_lists"


def test_the_reaction_document_embeds_entries_and_is_not_assessed(client, db_session):
    world, d, f = declared_world(db_session)
    rxe = world.entry.transition_state.reaction_entry
    body = client.get(f"{API}/reaction-entries/{rxe.public_ref}/full?include=trust").json()
    [ts] = [t for t in body["transition_states"] if t["transition_state_entry_ref"] == world.entry.public_ref]
    assert ts["trust"]["structure_assessment"]["status"] == "not_assessed_on_lists"
    standalone = detail(client, world.entry)
    for key in ("rubric", "rubric_version", "label", "evidence_completeness", "passed_count", "possible_count"):
        assert ts["trust"]["evidence"][key] == standalone["evidence"][key]


def test_a_refusal_on_the_selection_bounds_does_not_fail_the_read(client, db_session, monkeypatch):
    from app.services.structure_selection import selection

    world, d, f = declared_world(db_session)

    def refuse(*args, **kwargs):
        raise CodedValueError("structure_selection_evidence_too_large", "over the bound", context={"bound": "nested_rows"})

    monkeypatch.setattr(selection, "select_entry_structures", refuse)
    trust = detail(client, world.entry)
    block = trust["structure_assessment"]
    assert block["status"] == "not_assessed_over_bound" and block["refusal_code"] == "structure_selection_evidence_too_large"
    assert block["outcome"] is None and block["qualifying_refs"] == []  # nothing inferred from a subset
    assert trust["evidence"]["checks"][WARNING] == "not_applicable"


# ---------------------------------------------------------------------------
# Agreement with the evidence-selection endpoint
# ---------------------------------------------------------------------------


def _scenario_qualifies(db_session):
    world, d, f = declared_world(db_session)
    return world


def _scenario_zero_modes(db_session):
    world, d, f = declared_world(db_session, n_freq=FLAT)
    return world


def _scenario_undesignated_modes(db_session):
    world, d, f = declared_world(db_session, n_freq=THREE)
    return world


def _scenario_contested_pair(db_session):
    world = make_ts_world(db_session)
    geometry = make_geometry(db_session)
    saddle(world, geometry=geometry)
    saddle(world, geometry=geometry, n_freq=FLAT)
    world.settle()
    return world


def _scenario_qualifies_beside_a_contested_target(db_session):
    """One target qualifies, another is contested: the outcome is ``qualified_evidence`` and the warning still fires."""
    world = make_ts_world(db_session)
    contested_geometry = make_geometry(db_session)
    saddle(world, geometry=contested_geometry)
    saddle(world, geometry=contested_geometry, n_freq=FLAT)
    saddle(world)
    world.settle()
    return world


def _scenario_finding_invalidates_curvature(db_session):
    world = make_ts_world(db_session)
    d, opt, sp, f = saddle(world)
    world.settle()
    world.finding(calculation=f, kind=StructureFindingKind.role_invalidation, role=StructureSourceRole.curvature)
    db_session.flush()
    return world


def _scenario_witness_on_another_geometry(db_session):
    world = make_ts_world(db_session)
    here, there = make_geometry(db_session), make_geometry(db_session)
    opt = world.opt("o", -40.2, geometry=here)
    sp = world.sp("s", -40.3, geometry=here)
    elsewhere = world.freq("f", frequencies=GOOD, geometry=there)
    world.determination([("geometry_optimization", opt), ("energy", sp), ("curvature", elsewhere)], evaluated=here, target=SADDLE)
    world.settle()
    return world


SCENARIOS = {
    "qualifies": _scenario_qualifies,
    "witness_has_no_imaginary_mode": _scenario_zero_modes,
    "undesignated_extra_modes": _scenario_undesignated_modes,
    "same_target_contested_pair": _scenario_contested_pair,
    "qualifies_beside_a_contested_target": _scenario_qualifies_beside_a_contested_target,
    "finding_invalidates_curvature": _scenario_finding_invalidates_curvature,
    "witness_on_another_geometry": _scenario_witness_on_another_geometry,
}


@pytest.mark.parametrize("name", sorted(SCENARIOS))
@pytest.mark.parametrize("profile", [None, "curated"])
def test_the_badge_equals_the_evidence_endpoint_for_the_echoed_question(client, db_session, name, profile):
    """Kills: the badge asking a different request, profile or rule set than the one it echoes."""
    world = SCENARIOS[name](db_session)
    trust = detail(client, world.entry, profile=profile)
    block = trust["structure_assessment"]
    answer = select(client, world.entry, block["task"], profile=profile)
    assert block["status"] == "assessed", block
    assert block["outcome"] == answer["outcome"]
    assert block["qualifying_refs"] == answer["selected_refs"]
    assert block["contested_target_count"] == len(answer["contested_targets"])
    assert block["declared_targets"] == len(answer["units"])
    assert [
        (u["unit_ref"], u["applicability"], u["blocking"], [r["code"] for r in u["reasons"]]) for u in answer["units"]
    ] == [(u["unit_ref"], u["applicability"], u["blocking"], u["reasons"]) for u in block["units"]]
    warns = trust["evidence"]["checks"].get(WARNING) == "warning"
    assert warns == (answer["outcome"] != "qualified_evidence" or bool(answer["contested_targets"]))


def test_the_scenarios_cover_the_verdicts_the_matrix_is_for(client, db_session):
    seen = {}
    for name, build in SCENARIOS.items():
        world = build(db_session)
        block = detail(client, world.entry)["structure_assessment"]
        seen[name] = (block["outcome"], sorted({c for u in block["units"] for c in u["blocking"] + u["reasons"]}))
    assert seen["qualifies"][0] == "qualified_evidence"
    assert "curvature_contradicts_claim" in seen["witness_has_no_imaginary_mode"][1]
    assert "reaction_coordinate_not_established" in seen["undesignated_extra_modes"][1]
    assert seen["same_target_contested_pair"][0] == "evidence_conflict"
    assert seen["qualifies_beside_a_contested_target"][0] == "qualified_evidence"
    assert detail(client, SCENARIOS["qualifies_beside_a_contested_target"](db_session).entry)["structure_assessment"][
        "contested_target_count"
    ] == 1
    assert seen["finding_invalidates_curvature"][0] != "qualified_evidence"
    assert seen["witness_on_another_geometry"][0] != "qualified_evidence"
    assert len({v[0] for v in seen.values()}) >= 3  # the matrix is not one answer repeated


# ---------------------------------------------------------------------------
# Owner conditions: the assessment never moves the badge
# ---------------------------------------------------------------------------

_BADGE_KEYS = ("rubric", "rubric_version", "label", "evidence_completeness", "passed_count", "possible_count", "hard_fail_reason")


def test_a_refuted_declared_witness_changes_only_the_warning_and_the_block(client, db_session):
    """Kills: the scoped verdict leaking into the label, the weights or the certification."""
    twin_ok, *_ = declared_world(db_session)
    twin_refuted = _scenario_finding_invalidates_curvature(db_session)
    ok, refuted = detail(client, twin_ok.entry), detail(client, twin_refuted.entry)
    assert ok["trust_status"] == refuted["trust_status"]
    assert ok["is_certified"] is False and refuted["is_certified"] is False
    for key in _BADGE_KEYS:
        assert ok["evidence"].get(key) == refuted["evidence"].get(key), key
    assert WARNING not in ok["evidence"]["checks"] and refuted["evidence"]["checks"][WARNING] == "warning"
    drop = lambda checks: {k: v for k, v in checks.items() if k != WARNING}  # noqa: E731
    assert drop(ok["evidence"]["checks"]) == drop(refuted["evidence"]["checks"])
    assert ok["structure_assessment"]["outcome"] != refuted["structure_assessment"]["outcome"]


def test_the_warning_is_zero_weight_and_an_unresolved_target_is_not_a_pass(client, db_session):
    spec = next(c for c in COMPUTED_TRANSITION_STATE_V5.checks if c.name == WARNING)
    assert spec.kind is EvidenceCheckKind.warning
    unresolved = detail(client, _scenario_undesignated_modes(db_session).entry)
    assert unresolved["structure_assessment"]["outcome"] != "qualified_evidence"
    assert unresolved["evidence"]["checks"][WARNING] == "warning"  # missing evidence stays unresolved, never rounded to a pass
    # The warning is never counted: possible_count excludes warning-kind checks, so it equals the unwarned twin's.
    clean = detail(client, declared_world(db_session)[0].entry)
    assert unresolved["evidence"]["possible_count"] == clean["evidence"]["possible_count"]


def test_no_wording_of_the_check_or_the_block_says_validated_certified_or_disproved():
    spec = next(c for c in COMPUTED_TRANSITION_STATE_V5.checks if c.name == WARNING)
    text = spec.explain.lower()
    assert "needs investigation" in text and "not that the transition state is disproved" in text
    for word in ("validated", "certified"):
        assert word not in text.replace("no warning certifies nothing", "")


def test_another_entrys_declaration_is_never_borrowed(client, db_session, monkeypatch):
    """Kills: borrowing across sibling entries of one transition state."""
    from tests.services.scientific_read._factories import make_transition_state_entry

    world = make_ts_world(db_session)
    world.settle()
    sibling = make_transition_state_entry(db_session, transition_state=world.entry.transition_state)
    sibling_world = type(world)(
        session=db_session, entry=sibling, geometry=make_geometry(db_session), lot=world.lot, ts=True
    )
    saddle(sibling_world)
    sibling_world.settle()

    from app.services.structure_selection import selection

    ran_for: list[int] = []
    real = selection.select_entry_structures

    def spy(session, *, entry_id, **kwargs):
        ran_for.append(entry_id)
        return real(session, entry_id=entry_id, **kwargs)

    monkeypatch.setattr(selection, "select_entry_structures", spy)
    assert detail(client, world.entry)["structure_assessment"]["status"] == "no_declared_target"
    assert ran_for == []  # the sibling's declaration did not open this entry's pipeline
    assert detail(client, sibling)["structure_assessment"]["status"] == "assessed"
    assert ran_for == [sibling.id]


# ---------------------------------------------------------------------------
# O2: the representative frequency result
# ---------------------------------------------------------------------------


def _with_unrelated_rerun(world, frequencies):
    """A frequency result attached to the entry but pinned by no declaration, created after everything else."""
    world.freq("rerun", frequencies=frequencies)
    world.settle()


def test_a_declaration_pins_the_result_the_frequency_checks_read(client, db_session):
    """An older pinned one-mode witness plus a newer unrelated three-mode rerun: the required check reads the pin."""
    world, d, f = declared_world(db_session)
    _with_unrelated_rerun(world, THREE)
    trust = detail(client, world.entry)
    checks = trust["evidence"]["checks"]
    assert checks["reaction_coordinate_designated_for_ts"] == "passed"  # kills: the rule missing, or an unpinned pick
    assert checks[UNDESIGNATED] == "warning"  # the rerun stays visible: unscoped warnings read every attached result
    assert trust["structure_assessment"]["outcome"] == "qualified_evidence"


def test_only_a_result_pinned_in_the_curvature_role_is_the_representative(client, db_session):
    """A frequency result the declaration pins for another purpose is not the declared curvature witness.

    Kills: pins read from every source of the declaration, whatever its role.
    """
    world = make_ts_world(db_session)
    geometry = make_geometry(db_session)
    opt = world.opt("o", -40.2, geometry=geometry)
    sp = world.sp("s", -40.3, geometry=geometry)
    witness = world.freq("witness", frequencies=GOOD, geometry=geometry)
    other = world.freq("other", frequencies=THREE, geometry=geometry)  # newer, and pinned, but not as the curvature witness
    world.determination(
        [
            ("geometry_optimization", opt),
            ("energy", sp),
            ("curvature", witness),
            ("alternative_characterization", other),
        ],
        evaluated=geometry,
        target=SADDLE,
    )
    world.settle()
    checks = detail(client, world.entry)["evidence"]["checks"]
    assert checks["reaction_coordinate_designated_for_ts"] == "passed"  # the curvature witness, one imaginary mode


def test_the_mirror_case_a_pinned_undesignated_witness_is_missing_though_a_newer_result_has_one_mode(client, db_session):
    world, d, f = declared_world(db_session, n_freq=THREE)
    _with_unrelated_rerun(world, GOOD)
    checks = detail(client, world.entry)["evidence"]["checks"]
    assert checks["reaction_coordinate_designated_for_ts"] == "missing"


def test_an_entry_with_no_declaration_still_reads_the_newest_attached_result(client, db_session):
    """Kills: the rule applied without a declaration."""
    world = make_ts_world(db_session)
    world.freq("old", frequencies=GOOD)
    world.freq("new", frequencies=THREE)
    world.settle()
    checks = detail(client, world.entry)["evidence"]["checks"]
    assert checks["reaction_coordinate_designated_for_ts"] == "missing"


def test_a_list_reads_the_newest_attached_result_because_it_does_not_look_at_declarations(client, db_session):
    world, d, f = declared_world(db_session)
    _with_unrelated_rerun(world, THREE)
    [record] = [
        r for r in search(client)["records"]
        if r["transition_state_entry"]["transition_state_entry_ref"] == world.entry.public_ref
    ]
    assert record["trust"]["evidence"]["checks"]["reaction_coordinate_designated_for_ts"] == "missing"


# ---------------------------------------------------------------------------
# Curated (D6)
# ---------------------------------------------------------------------------


def test_a_target_resting_on_a_hidden_calculation_is_unresolved_under_curated_and_warns(client, db_session):
    """Kills: the assessment run under the wrong profile, or cached across profiles."""
    world = make_ts_world(db_session)
    d, opt, sp, f = saddle(world, statuses={"freq": None})  # the curvature witness is below the curated floor
    world.settle()
    exploratory = detail(client, world.entry)
    curated = detail(client, world.entry, profile="curated")
    assert exploratory["structure_assessment"]["outcome"] == "qualified_evidence"
    assert WARNING not in exploratory["evidence"]["checks"]
    block = curated["structure_assessment"]
    assert block["outcome"] == "no_applicable_candidate"
    assert [u["reasons"] for u in block["units"]] == [["required_evidence_unavailable"]]
    assert curated["evidence"]["checks"][WARNING] == "warning"
    text = json.dumps(curated)
    assert f.public_ref not in text  # nothing names the hidden calculation
    # And back again: nothing was kept from the curated read.
    assert detail(client, world.entry)["structure_assessment"]["outcome"] == "qualified_evidence"


def test_the_hidden_pin_falls_back_to_the_newest_attached_visible_result_under_curated(client, db_session):
    world = make_ts_world(db_session)
    saddle(world, statuses={"freq": None})
    world.freq("visible", frequencies=THREE)
    world.settle()
    # The hidden pinned witness is out of the shown graph, so the rule falls back; the visible undesignated result is read.
    # (exploratory: the pin is visible and chosen.)
    assert detail(client, world.entry, profile="curated")["evidence"]["checks"]["reaction_coordinate_designated_for_ts"] == "missing"
    assert detail(client, world.entry)["evidence"]["checks"]["reaction_coordinate_designated_for_ts"] == "passed"


# ---------------------------------------------------------------------------
# Contracts: no default, version pin, machine review, fragment shape
# ---------------------------------------------------------------------------

_APP = pathlib.Path(__file__).resolve().parents[3] / "app"


def test_context_and_assess_structures_are_required_keyword_only_arguments():
    """Kills: a default ``None`` reintroduced, which would let a caller silently fall back to the unscoped answer."""
    from app.services.scientific_read import transition_states, trust_floor
    from app.services.trust import evaluate_loaded_transition_state_entry

    for func, name in (
        (evaluate_loaded_transition_state_entry, "context"),
        (trust_floor.evaluate_shown_transition_state_entry, "context"),
        (transition_states.build_transition_state_entry_trust_fragment, "context"),
        (transition_states.build_entry_record, "assess_structures"),
        (transition_states._build_entry_record, "assess_structures"),
    ):
        parameter = inspect.signature(func).parameters[name]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY, (func.__name__, name)
        assert parameter.default is inspect.Parameter.empty, (func.__name__, name)


def test_every_production_call_site_passes_the_context():
    guarded = {
        "evaluate_loaded_transition_state_entry": "context",
        "evaluate_shown_transition_state_entry": "context",
        "build_transition_state_entry_trust_fragment": "context",
        "build_entry_record": "assess_structures",
        "_build_entry_record": "assess_structures",
    }
    calls = 0
    for path in _APP.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
            if name not in guarded:
                continue
            if path.name == "species.py":  # its own, unrelated ``_build_entry_record``
                continue
            calls += 1
            assert guarded[name] in {k.arg for k in node.keywords}, f"{path.name}:{node.lineno} {name}"
    assert calls >= 8  # the scan found the call sites: a rename cannot make this vacuous


def test_the_pinned_structure_versions_equal_the_live_ones_and_drift_refuses(db_session, monkeypatch):
    """Kills: silent structure-semantics drift under the badge."""
    assert TS_SCOPED_STRUCTURE_VERSIONS == {
        "assessment": ASSESSMENT_VERSION,
        "normalizer": NORMALIZER_VERSION,
        "decision": DECISION_VERSION,
    }
    world = make_ts_world(db_session)
    from app.services.trust import rubrics, ts_scoped

    monkeypatch.setitem(rubrics.TS_SCOPED_STRUCTURE_VERSIONS, "decision", "999")
    with pytest.raises(RuntimeError, match="deliberate rubric bump"):
        ts_scoped.scoped_ts_context(db_session, world.entry)


def test_the_outcome_vocabulary_is_the_structure_selection_vocabulary_narrowed_to_the_evidence_question():
    ours = {o.value for o in TransitionStateScopedAssessmentOutcome}
    assert ours <= {o.value for o in StructureOutcome}
    assert ours == {"qualified_evidence", "no_applicable_candidate", "evidence_conflict"}


def test_the_ts_fragment_extends_the_shared_one_and_the_shared_one_is_unchanged():
    assert issubclass(TransitionStateTrustFragment, TrustFragment)
    assert "structure_assessment" in TransitionStateTrustFragment.model_fields
    assert "structure_assessment" not in TrustFragment.model_fields


def test_machine_review_evaluation_of_a_declared_entry_equals_the_exploratory_public_evidence(client, db_session):
    """Kills: the machine-review path built without the context."""
    world = make_ts_world(db_session)
    d, opt, sp, f = saddle(world, n_freq=THREE)
    world.settle()
    _with_unrelated_rerun(world, GOOD)
    public = detail(client, world.entry)["evidence"]
    internal = evaluate_computed_transition_state_entry(db_session, world.entry.id)
    assert {k: v.value for k, v in internal.checks.items()} == public["checks"]
    assert (internal.passed_count, internal.possible_count) == (public["passed_count"], public["possible_count"])
    assert public["checks"]["reaction_coordinate_designated_for_ts"] == "missing"  # the pin, not the newer one-mode rerun


def test_the_response_names_no_database_id_and_no_witness_calculation(client, db_session):
    world, d, f = declared_world(db_session)
    trust = detail(client, world.entry)
    block = json.dumps(trust["structure_assessment"])
    assert f.public_ref not in block  # a unit names the determination and codes, not the calculations it rests on
    assert not re.search(r'"[a-z_]*_id": \d', block)
