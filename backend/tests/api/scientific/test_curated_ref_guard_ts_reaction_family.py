"""PR-2b: the transition-state and reaction route families withhold hidden refs under ``?profile=curated``.

What this pins, beyond the all-routes sweep (``test_curated_ref_sweep.py``, whose baseline no longer lists any
transition-state, reaction-entry, reaction or kinetics entry):

* **Leaks.** Every route of the family, crawled under ``curated`` with and without ``internal_ids`` and with every
  include token (``trust`` and ``assessments`` are not part of ``all``), names no hidden record by ref, by integer id,
  by URL or in free text, and answers 200.
* **No substitution.** Curated is a withholding of exploratory on those routes.
* **D1.** A visible transition-state entry whose transition state is hidden stays visible with ``transition_state``
  null; there is no cascading hide. Widening the field alone is not enough: the owner block is also a pointer.
* **D4.** A hidden ref the guard cannot place still fails closed.
* **D5.** Counts and flags that would expose a hidden record follow the floor.
* **Scoping parents.** An unreviewed reaction entry or species entry is not a gated record: nothing about it is hidden.
"""

from __future__ import annotations

import pytest

from app.services.scientific_read import ref_guard
from tests.api.scientific._curated_sweep_analysis import (
    id_leaks,
    order_independent,
    parse_body,
    read_baseline,
    ref_leaks,
    withholding_violations,
)
from tests.api.scientific._curated_sweep_crawl import enumerate_routes, variants_for
from tests.api.scientific._curated_sweep_run import install_spy, run_pass
from tests.api.scientific._curated_sweep_world import build_sweep_world

BASE = "/api/v1/scientific"


def _is_selection_document(path: str) -> bool:
    """Hashed, replayable documents (``.../select`` and ``/manifest``). They never pass the boundary hook (they stamp
    the profile and return) and get their own seam in PR-3."""
    return path.endswith(("/select", "/select/manifest"))


def _in_family(path: str) -> bool:
    return ref_guard.route_matches(path, ref_guard.TS_REACTION_PREFIXES) and not _is_selection_document(path)


def _family(specs):
    return [s for s in specs if _in_family(s.path)]


def _request_text(spec, world, label, *, ids):
    for variant in variants_for(spec, world, with_internal_ids=ids):
        if variant.label == label:
            return variant.request_text
    return ""


def test_ts_and_reaction_routes_name_no_hidden_record_under_curated(client, db_session, login_as, monkeypatch):
    """The crawl that finds the family's leaks on ``main`` finds none, on a world that holds every response shape.

    Also: no route of the family is left unplaceable (a 500 from ``RefGuardUnplaceable``), every request answers 200,
    and curated is only ever a withholding of exploratory (no sibling is substituted into a gap).

    Kills: not enabling a prefix; a block whose pointer class is missing (the request fails closed, so this crawl
    sees a 500); a hide that substitutes a visible sibling; a crawl that visits nothing.
    """
    from app.api.config import settings

    world = build_sweep_world(db_session)
    login_as(world.extra["curator"].id)
    specs = _family(enumerate_routes(client.app))
    assert len(specs) >= 12, "the family crawl is empty or truncated"
    spy = install_spy(monkeypatch)
    hidden = world.hidden_refs()

    exploratory = run_pass(client, db_session, specs, world, spy, profile="exploratory")
    curated = run_pass(client, db_session, specs, world, spy, profile="curated")
    monkeypatch.setattr(settings, "allow_public_internal_ids", True)
    curated_ids = run_pass(client, db_session, specs, world, spy, profile="curated", with_ids=True)
    monkeypatch.setattr(settings, "allow_public_internal_ids", False)

    problems: list[str] = []
    for name, results in (("curated", curated), ("curated+ids", curated_ids)):
        for (route, label), r in results.items():
            if r.status != 200:
                problems.append(f"[{name}] {route} {label} answered {r.status} (unplaceable={r.unplaceable})")
            if r.unplaceable is not None:
                problems.append(f"[{name}] {route} {label} unplaceable {r.unplaceable}")
    for (route, label), r in curated.items():
        spec = next(s for s in specs if s.key == route)
        for key, path in ref_leaks(r.text, hidden, _request_text(spec, world, label, ids=False)):
            problems.append(f"hidden ref {key} at {route[1]} {path}")
    for (route, _label), r in curated_ids.items():
        for key, path in id_leaks(r.text, world):
            problems.append(f"hidden id {key} at {route[1]} {path}")
    for (route, label), c in curated.items():
        ekind, eobj = parse_body(exploratory[(route, label)].text)
        ckind, cobj = parse_body(c.text)
        if ekind == "json" and ckind == "json":
            violations = withholding_violations(order_independent(route[1], eobj), order_independent(route[1], cobj))
            problems.extend(f"not a withholding: {route[1]} {p}" for p in violations)
    assert sum(len(r.applied_sites) for r in curated.values()) > 20, "the enabled guard withheld almost nothing"
    exploratory_text = "\n".join(r.text for r in exploratory.values())
    for key in ("calc_h_ts", "calc_h_tssp", "calc_h_irc", "calc_h_ps", "ts_h", "tse_h", "sm_h", "kin_h", "net_h"):
        if key in hidden:
            assert hidden[key] in exploratory_text, f"positive control: {key} never named under exploratory"
    assert not problems, "\n".join(problems)


def test_the_baseline_keeps_no_ts_or_reaction_entry():
    """The ratchet only shrinks: PR-2b removed every entry of the family and none may come back.

    Kills: re-adding a leak, hidden id or unplaceable of this family to the baseline instead of fixing it.
    """
    baseline = read_baseline()
    assert baseline, "baseline file is missing or empty"
    for section, entries in baseline.items():
        for entry in entries:
            path = entry.split(" ", 2)[1]
            assert not _in_family(path), f"{section}: {entry}"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _get(client, path, profile, **params):
    return client.get(f"{BASE}{path}", params={**params, "profile": profile})


def _walk(node):
    """Every (key, value) pair in a JSON document."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield key, value
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


# ---------------------------------------------------------------------------
# D1 and D4: the owner block of a transition-state entry
# ---------------------------------------------------------------------------


def test_a_visible_entry_of_a_hidden_transition_state_stays_visible_with_the_owner_blanked(
    client, db_session, login_as
):
    """D1: no cascading hide. The entry, its reaction context and review are kept; only the owner block goes.

    Kills: hiding the child (404 or dropped from the browse); leaving the owner named; blanking an owner that is
    visible; touching exploratory.
    """
    world = build_sweep_world(db_session)
    login_as(world.extra["curator"].id)
    hidden_ts = world.hidden["ts_h"].public_ref
    under_hidden = world.visible["tse_v"].public_ref
    under_visible = world.visible["tse_v2"].public_ref

    cur = _get(client, f"/transition-state-entries/{under_hidden}", "curated", include="entries")
    assert cur.status_code == 200, cur.text
    record = cur.json()["record"]
    assert record["transition_state"] is None
    assert record["transition_state_entry"]["transition_state_entry_ref"] == under_hidden
    assert record["reaction"]["reaction_entry_ref"] and record["evidence_summary"] is not None
    assert [e["transition_state_entry"]["transition_state_entry_ref"] for e in record["entries"]] == [under_hidden]
    assert all(e["transition_state"] is None for e in record["entries"])
    assert hidden_ts not in cur.text

    exp = _get(client, f"/transition-state-entries/{under_hidden}", "exploratory")
    assert exp.json()["record"]["transition_state"]["transition_state_ref"] == hidden_ts

    ok = _get(client, f"/transition-state-entries/{under_visible}", "curated")
    assert ok.json()["record"]["transition_state"]["transition_state_ref"] == world.visible["ts_v"].public_ref

    # The entry is still in the browse, with its owner blanked, and the /full block keeps the entry.
    browse = _get(client, "/transition-states/browse", "curated", limit=100).json()["records"]
    row = next(r for r in browse if r["transition_state_entry"]["transition_state_entry_ref"] == under_hidden)
    assert row["transition_state"] is None
    full = _get(
        client,
        f"/reaction-entries/{world.extra['reaction_entry'].public_ref}/full",
        "curated",
        include="transition_states",
    ).json()
    in_full = next(t for t in full["transition_states"] if t["transition_state_entry_ref"] == under_hidden)
    assert in_full["transition_state_ref"] is None


def test_widening_the_owner_field_alone_still_fails_closed(client, db_session, login_as, monkeypatch):
    """D4 and the #770-review correction: without the owner block registered as a pointer the guard has nowhere to put
    a hidden transition-state ref, so the request fails instead of leaking.

    Kills: treating the nullable field as enough; dropping the D1 registration.
    """
    world = build_sweep_world(db_session)
    login_as(world.extra["curator"].id)
    from app.schemas.reads.scientific_transition_state import TransitionStateCoreBlock

    monkeypatch.setattr(ref_guard, "POINTER_MODELS", ref_guard.POINTER_MODELS - {TransitionStateCoreBlock})
    with pytest.raises(ref_guard.RefGuardUnplaceable) as caught:
        _get(client, f"/transition-state-entries/{world.visible['tse_v'].public_ref}", "curated")
    assert caught.value.ref_prefix == "ts"


# ---------------------------------------------------------------------------
# Every registered block is reached by the world, and unregistering it changes the answer
# ---------------------------------------------------------------------------


def _shapes(world):
    rxe = world.extra["reaction_entry"].public_ref
    tse2 = world.visible["tse_v2"].public_ref
    tse1 = world.visible["tse_v"].public_ref
    ts = world.visible["ts_v"].public_ref
    full_all = (f"/reaction-entries/{rxe}/full", {"include": ["all", "trust"]})
    kinetics_all = (f"/reaction-entries/{rxe}/kinetics", {"include": ["all", "assessments", "interpretations"]})
    entry_all = (f"/transition-state-entries/{tse2}", {"include": ["all", "entries"]})
    concept_all = (f"/transition-states/{ts}", {"include": ["all", "entries"]})
    from app.schemas.reads import scientific_common as common
    from app.schemas.reads import scientific_kinetics as kin
    from app.schemas.reads import scientific_provenance as prov
    from app.schemas.reads import scientific_transition_state as tsr

    return [
        (tsr.TransitionStateCoreBlock, (f"/transition-state-entries/{tse1}", {})),
        (tsr.TransitionStateCalculationSummary, entry_all),
        (tsr.TransitionStateEntryStatmech, concept_all),
        (tsr.TransitionStateEntryValidationEvidence, concept_all),
        (tsr.ScientificTransitionStateEntryRecord, entry_all),
        (tsr.TransitionStateSaddlePointEvidence, entry_all),
        (tsr.TransitionStateValidationEvidenceSummary, entry_all),
        (tsr.TransitionStateComparedEnergySummary, entry_all),
        (prov.ReactionFullCalculationEvidenceSummary, full_all),
        (prov.ReactionFullIRCItem, full_all),
        (prov.ReactionFullScanItem, full_all),
        (prov.ReactionFullPathSearchItem, full_all),
        (prov.ReactionFullCalculationArtifacts, full_all),
        (prov.ReactionFullNetworkMembership, full_all),
        (prov.ReactionAtomMapBadge, full_all),
        (prov.ReactionAtomMapDetail, full_all),
        (prov.TransitionStateCalculationSlot, full_all),
        (prov.TransitionStateDependency, full_all),
        (kin.KineticsDeterminationBlock, kinetics_all),
        (kin.KineticsInterpretationAssignmentBlock, kinetics_all),
        (kin.KineticsTunnelingApplicationBlock, kinetics_all),
        (common.PathSearchSummary, kinetics_all),
        (common.ValidationSummary, kinetics_all),
        (common.SCFStabilitySummary, kinetics_all),
        (common.SupersessionNotice, kinetics_all),
    ]


def test_every_block_the_family_registers_is_reached_and_matters(client, db_session, login_as, monkeypatch):
    """Each pointer registered for this family is built by the world, answers 200 with it, and changes the answer
    (a different body, or a request that fails closed) when it is unregistered.

    The PR-2a review found seven blocks that failed closed under curated because the world never built them. A block
    unregistered here that leaves the response unchanged is a registration nothing exercises.

    Kills: removing any one registration; a world that stops building a shape.
    """
    import json

    world = build_sweep_world(db_session)
    login_as(world.extra["curator"].id)
    registry = ref_guard.POINTER_MODELS
    for model, (route, params) in _shapes(world):
        assert model in registry, f"{model.__name__} is not registered"
        registered = _get(client, route, "curated", **params)
        assert registered.status_code == 200, (model.__name__, route, registered.text)
        monkeypatch.setattr(ref_guard, "POINTER_MODELS", registry - {model})
        try:
            answer = _get(client, route, "curated", **params).text
        except ref_guard.RefGuardUnplaceable:
            answer = None
        finally:
            monkeypatch.setattr(ref_guard, "POINTER_MODELS", registry)
        assert answer is None or json.loads(answer) != json.loads(registered.text), (
            f"unregistering {model.__name__} changed nothing on {route}: the world does not reach it"
        )


def test_the_untyped_irc_block_of_a_kinetics_provenance_goes_whole(client, db_session, login_as, monkeypatch):
    """A hidden IRC calculation takes the whole ``provenance.irc`` block with it, not just its ref.

    Kills: leaving ``{"type": "irc", "calculation_ref": null}`` behind (it would say a hidden record exists);
    dropping the field from ``POINTER_FIELDS``.
    """
    world = build_sweep_world(db_session)
    login_as(world.extra["curator"].id)
    route = f"/reaction-entries/{world.extra['reaction_entry'].public_ref}/kinetics"
    exploratory = _get(client, route, "exploratory").json()["records"]
    assert any(r["provenance"]["irc"] for r in exploratory), "the world builds no kinetics IRC"
    curated = _get(client, route, "curated").json()["records"]
    assert all(r["provenance"]["irc"] is None for r in curated)
    from app.schemas.reads.scientific_kinetics import KineticsProvenance

    monkeypatch.setattr(ref_guard, "POINTER_FIELDS", ref_guard.POINTER_FIELDS - {(KineticsProvenance, "irc")})
    left = _get(client, route, "curated").json()["records"]
    assert any(r["provenance"]["irc"] for r in left), "without the field registered a skeleton is left behind"


def test_a_determination_aimed_at_a_hidden_record_is_not_served(client, db_session, login_as):
    """A determination whose target entry (or network channel) is hidden is withheld whole: a ``resolved_channel`` with
    no target would say a hidden record exists.

    Kills: nulling only the ref (a shell remains); serving the determination.
    """
    world = build_sweep_world(db_session)
    login_as(world.extra["curator"].id)
    route = f"/reaction-entries/{world.extra['reaction_entry'].public_ref}/kinetics"
    by_ref = {r["kinetics_ref"]: r for r in _get(client, route, "exploratory").json()["records"]}
    assert by_ref[world.visible["kin_v"].public_ref]["determination"] is not None
    assert by_ref[world.visible["kin_vn"].public_ref]["determination"] is not None
    cur = {r["kinetics_ref"]: r for r in _get(client, route, "curated").json()["records"]}
    assert cur[world.visible["kin_v"].public_ref]["determination"] is None
    assert cur[world.visible["kin_vn"].public_ref]["determination"] is None


# ---------------------------------------------------------------------------
# D5: counts and flags follow the floor
# ---------------------------------------------------------------------------


def test_counts_and_flags_of_a_visible_entry_follow_the_floor(client, db_session, login_as):
    """``tse_vw`` is visible and every calculation and evidence row under it is hidden, and its sibling ``tse_hw2`` is
    hidden: curated counts and flags say nothing was found; exploratory is unchanged.

    Kills: counting hidden calculations, entries or evidence in a count or flag; touching exploratory.
    """
    world = build_sweep_world(db_session)
    login_as(world.extra["curator"].id)
    entry = world.visible["tse_vw"].public_ref
    cur = _get(client, f"/transition-state-entries/{entry}", "curated", include="all").json()["record"]
    assert cur["evidence_summary"]["calculation_count"] == 0
    assert not any(cur["evidence_summary"][k] for k in cur["evidence_summary"] if k.startswith("has_"))
    available = cur["available_sections"]
    assert not (
        available["has_entries"]
        or available["has_calculations"]
        or available["has_geometries"]
        or available["has_validation_evidence"]
    )
    assert cur["validation"]["irc"] == "absent"
    exp = _get(client, f"/transition-state-entries/{entry}", "exploratory", include="all").json()["record"]
    assert exp["evidence_summary"]["calculation_count"] == 2 and exp["evidence_summary"]["has_irc"] is True
    assert exp["available_sections"]["has_entries"] is True and exp["validation"]["irc"] == "present"

    ts = world.visible["ts_vw"].public_ref
    cur_ts = _get(client, f"/transition-states/{ts}", "curated").json()["record"]
    assert cur_ts["entries_summary"]["total"] == 1
    assert cur_ts["evidence_summary"]["entry_count"] == 1
    assert cur_ts["evidence_summary"]["calculation_count"] == 0
    assert cur_ts["evidence_summary"]["evidence_coverage"]["irc"] == 0
    assert cur_ts["available_sections"]["has_calculations"] is False
    exp_ts = _get(client, f"/transition-states/{ts}", "exploratory").json()["record"]
    assert exp_ts["entries_summary"]["total"] == 2 and exp_ts["evidence_summary"]["calculation_count"] == 2


def test_reaction_availability_follows_the_floor(client, db_session, login_as):
    """A reaction entry whose only kinetics, transition state and path search are hidden reports none of them.

    Kills: counting hidden records in ``kinetics_count`` or the flags; hiding the reaction entry itself (it is a
    scoping parent, never gated).
    """
    world = build_sweep_world(db_session)
    login_as(world.extra["curator"].id)
    ref = world.extra["reaction_entry_all_hidden"].public_ref

    def availability(profile):
        rows = _get(client, "/reactions/browse", profile, limit=100).json()["records"]
        return next(r for r in rows if r["reaction_entry_ref"] == ref)["availability"]

    exploratory = availability("exploratory")
    assert exploratory["has_kinetics"] and exploratory["has_transition_state"] and exploratory["has_path_search"]
    curated = availability("curated")
    assert curated["kinetics_count"] == 0
    assert not (curated["has_kinetics"] or curated["has_transition_state"] or curated["has_path_search"])


# ---------------------------------------------------------------------------
# Free text, URLs, scoping parents
# ---------------------------------------------------------------------------


def test_free_text_that_names_a_hidden_record_is_blanked(client, db_session, login_as):
    """A transition state's note that cites a hidden calculation is blanked whole; the visible fields stay.

    Kills: leaving the note; blanking the visible label with it.
    """
    world = build_sweep_world(db_session)
    login_as(world.extra["curator"].id)
    hidden = world.hidden["calc_h_ts"].public_ref
    ts = world.visible["ts_v"]
    exp = _get(client, f"/transition-states/{ts.public_ref}", "exploratory").json()["record"]["transition_state"]
    assert hidden in exp["note"]
    cur = _get(client, f"/transition-states/{ts.public_ref}", "curated").json()["record"]["transition_state"]
    assert cur["note"] is None and cur["label"] == exp["label"]


def test_a_reaction_display_string_naming_a_hidden_record_is_blanked(db_session):
    """The reaction equation and a participant label go through the free-text rule like any other string: a required
    string becomes ``""``, a nullable one ``None``. (Both are computed from species identity today and cannot carry a
    ref; this pins that nothing exempts them if that ever changes.)

    Kills: exempting display strings from the free-text rule.
    """
    from pydantic import BaseModel

    from app.db.models.common import ProfileRecommendation, ReadProfile
    from app.services.scientific_read.profile import ResolvedReadProfile

    class Record(BaseModel):
        equation: str
        species_entry_label: str | None = None

    world = build_sweep_world(db_session)
    hidden = world.hidden["tse_h"].public_ref
    visible = world.visible["tse_v"].public_ref
    curated = ResolvedReadProfile(profile=ReadProfile.curated, recommendation=ProfileRecommendation.approved_floor_only)
    record = Record(equation=f"A + B -> C ({hidden})", species_entry_label=f"excited {hidden}")
    ref_guard.withhold_in_model(db_session, record, profile=curated)
    assert record.equation == "" and record.species_entry_label is None
    kept = Record(equation=f"A + B -> C ({visible})", species_entry_label="E")
    ref_guard.withhold_in_model(db_session, kept, profile=curated)
    assert kept.equation.endswith(f"({visible})") and kept.species_entry_label == "E"


def test_no_endpoint_url_of_the_full_read_names_a_hidden_record(client, db_session, login_as):
    """Every ``endpoint`` the ``/full`` read serves (scan, IRC, path-search and conformer items) is read as a URL; the
    ones that named a hidden calculation are withheld with their item.

    Kills: serving the endpoint of a hidden item; a world with no hidden item that carries an endpoint.
    """
    world = build_sweep_world(db_session)
    login_as(world.extra["curator"].id)
    route = f"/reaction-entries/{world.extra['reaction_entry'].public_ref}/full"
    hidden = list(world.hidden_refs().values())
    exploratory = _get(client, route, "exploratory", include="all").text
    assert any(f"/{ref}" in exploratory for ref in hidden if ref.startswith("calc_")), "no hidden endpoint to withhold"
    curated = _get(client, route, "curated", include="all").json()
    endpoints = [value for key, value in _walk(curated) if key == "endpoint"]
    assert endpoints, "the curated /full carries no endpoint at all: the check is vacuous"
    assert not [e for e in endpoints for ref in hidden if ref in e]


def test_unreviewed_scoping_parents_are_not_hidden(client, db_session, login_as):
    """A reaction entry or species entry that has no review is not a gated record: its routes answer 200 under curated
    and nothing about it is blanked. The records under it are still judged one by one.

    Kills: treating a scoping parent as gated (404 or blanked participant).
    """
    world = build_sweep_world(db_session)
    login_as(world.extra["curator"].id)
    unreviewed_entry = world.extra["species_entry_unreviewed"].public_ref
    for key in ("reaction_entry_unreviewed", "reaction_entry_hidden_participant"):
        ref = world.extra[key].public_ref
        for suffix in ("full", "kinetics"):
            cur = _get(client, f"/reaction-entries/{ref}/{suffix}", "curated", include="all")
            assert cur.status_code == 200, (key, suffix, cur.text)
    ref = world.extra["reaction_entry_hidden_participant"].public_ref
    cur = _get(client, f"/reaction-entries/{ref}/full", "curated")
    # The /full participant list applies the floor to the species entries it lists (existing behaviour, not the
    # guard's): the unreviewed entry is neither listed nor named, and the route still answers.
    assert unreviewed_entry not in cur.text
    exp = _get(client, f"/reaction-entries/{ref}/full", "exploratory")
    assert [p["species_entry_ref"] for p in exp.json()["species"]["reactants"]] == [unreviewed_entry]
    unreviewed_rxe = world.extra["reaction_entry_unreviewed"].public_ref
    records = _get(client, f"/reaction-entries/{unreviewed_rxe}/kinetics", "curated").json()["records"]
    assert [r["kinetics_ref"] for r in records] == [world.visible["kin_vu"].public_ref]
