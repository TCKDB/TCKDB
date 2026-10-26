"""PR-2b: the transition-state and reaction route families withhold hidden refs under ``?profile=curated``.

What this pins, beyond the all-routes sweep (``test_curated_ref_sweep.py``, which is enforce-only since PR-5 and covers
every route):

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

from app.db.models.common import CalculationType, RecordReviewStatus
from app.db.models.common import SubmissionRecordType as R
from app.services.scientific_read import ref_guard
from tests.api.scientific._curated_families import TS_REACTION_PREFIXES
from tests.api.scientific._curated_sweep_analysis import (
    id_leaks,
    order_independent,
    parse_body,
    ref_leaks,
    withholding_violations,
)
from tests.api.scientific._curated_sweep_crawl import enumerate_routes, variants_for
from tests.api.scientific._curated_sweep_run import install_spy, run_pass
from tests.api.scientific._curated_sweep_world import build_sweep_world
from tests.services.scientific_read._factories import (
    make_calculation,
    make_lot,
    make_species,
    make_species_entry,
    make_transition_state_entry,
    next_inchi_key,
    set_review,
)

APPROVED = RecordReviewStatus.approved
BASE = "/api/v1/scientific"


def _is_selection_document(path: str) -> bool:
    """Hashed, replayable documents (``.../select`` and ``/manifest``). They never pass the boundary hook (they stamp
    the profile and return) and get their own seam in PR-3."""
    return path.endswith(("/select", "/select/manifest"))


def _in_family(path: str) -> bool:
    return ref_guard.route_matches(path, TS_REACTION_PREFIXES) and not _is_selection_document(path)


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
    world.withhold_hidden(db_session)  # the crawl above saw the records unlisted; from here they are withheld (ADR 0023)
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
    world = build_sweep_world(db_session, withheld=True)
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
    world = build_sweep_world(db_session, withheld=True)
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
    from tckdb_schemas.kinetics_declarations import StoredKineticsProtocolCalculationRef

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
        # TransitionStateSaddlePointEvidence is deliberately not here (#723): the block is now built from the calculations
        # the profile shows (hidden == absent at the source), so unregistering it changes no answer. It stays registered as
        # the second line of defence; ``test_the_saddle_point_block_stays_registered_behind_the_source_filter`` pins that.
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
        (StoredKineticsProtocolCalculationRef, kinetics_all),
    ]


def test_every_block_the_family_registers_is_reached_and_matters(client, db_session, login_as, monkeypatch):
    """Each pointer registered for this family is built by the world, answers 200 with it, and changes the answer
    (a different body, or a request that fails closed) when it is unregistered.

    The PR-2a review found seven blocks that failed closed under curated because the world never built them. A block
    unregistered here that leaves the response unchanged is a registration nothing exercises.

    Kills: removing any one registration; a world that stops building a shape.
    """
    import json

    world = build_sweep_world(db_session, withheld=True)
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


def test_the_saddle_point_block_stays_registered_behind_the_source_filter():
    from app.schemas.reads import scientific_transition_state as tsr

    assert tsr.TransitionStateSaddlePointEvidence in ref_guard.POINTER_MODELS


def test_the_untyped_irc_block_of_a_kinetics_provenance_goes_whole(client, db_session, login_as, monkeypatch):
    """A hidden IRC calculation takes the whole ``provenance.irc`` block with it, not just its ref.

    Kills: leaving ``{"type": "irc", "calculation_ref": null}`` behind (it would say a hidden record exists);
    dropping the field from ``POINTER_FIELDS``.
    """
    world = build_sweep_world(db_session, withheld=True)
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
    world = build_sweep_world(db_session, withheld=True)
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
    world = build_sweep_world(db_session, withheld=True)
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
    world = build_sweep_world(db_session, withheld=True)
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
    world = build_sweep_world(db_session, withheld=True)
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

    world = build_sweep_world(db_session, withheld=True)
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
    world = build_sweep_world(db_session, withheld=True)
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
    world = build_sweep_world(db_session, withheld=True)
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


# ---------------------------------------------------------------------------
# Review rows, levels of theory and search filters are not existence oracles
# ---------------------------------------------------------------------------


def test_the_review_rows_of_a_full_read_do_not_list_hidden_records(client, db_session, login_as, monkeypatch):
    """``include_review=full`` lists the review rows of the records under a reaction entry. Under curated a record
    below the floor has no row there (its type, status and, with internal ids, its primary key would say it exists),
    and the rows come back in one deterministic order.

    Kills: listing the rows of hidden kinetics or transition-state entries; an unordered query.
    """
    from app.api.config import settings

    world = build_sweep_world(db_session, withheld=True)
    login_as(world.extra["curator"].id)
    route = f"/reaction-entries/{world.extra['reaction_entry'].public_ref}/full"
    params = {"include_review": "full", "include": ["internal_ids"]}
    monkeypatch.setattr(settings, "allow_public_internal_ids", True)

    exploratory = _get(client, route, "exploratory", **params).json()["review_records"]
    hidden_ids = {
        ("kinetics", world.hidden["kin_h"].id),
        ("transition_state_entry", world.hidden["tse_h"].id),
    }
    assert hidden_ids <= {(r["record_type"], r["record_id"]) for r in exploratory}, "the world lists no hidden row"

    curated = _get(client, route, "curated", **params).json()["review_records"]
    assert curated, "the curated read lists no review row at all: the check is vacuous"
    assert not hidden_ids & {(r["record_type"], r["record_id"]) for r in curated}
    assert {r["status"] for r in curated} <= {"approved"}
    again = _get(client, route, "curated", **params).json()["review_records"]
    assert again == curated
    keys = [(r["record_type"], r["record_id"]) for r in curated]
    assert keys == sorted(
        keys, key=lambda k: (["reaction_entry", "kinetics", "transition_state_entry"].index(k[0]), k[1])
    )


def test_levels_of_theory_name_only_the_calculation_types_the_profile_shows(client, db_session, login_as):
    """The keys of a ``levels_of_theory`` map are calculation types, so a hidden IRC would print ``"irc": []`` beside
    ``has_irc: false``. Entry, concept and kinetics levels come only from calculations the profile shows.

    Kills: building the map from hidden calculations; leaving kinetics ``levels.frequency`` to a hidden freq.
    """
    world = build_sweep_world(db_session, withheld=True)
    login_as(world.extra["curator"].id)
    entry = world.visible["tse_vw"].public_ref
    exp = _get(client, f"/transition-state-entries/{entry}", "exploratory").json()["record"]
    assert set(exp["evidence_summary"]["levels_of_theory"]) == {"opt", "irc"}
    cur = _get(client, f"/transition-state-entries/{entry}", "curated").json()["record"]
    assert cur["evidence_summary"]["levels_of_theory"] == {}

    ts = world.visible["ts_vw"].public_ref
    assert _get(client, f"/transition-states/{ts}", "exploratory").json()["record"]["evidence_summary"][
        "levels_of_theory"
    ]
    assert (
        _get(client, f"/transition-states/{ts}", "curated").json()["record"]["evidence_summary"]["levels_of_theory"]
        == {}
    )

    kinetics = f"/reaction-entries/{world.extra['reaction_entry'].public_ref}/kinetics"
    ref = world.visible["kin_v"].public_ref
    exp_k = next(r for r in _get(client, kinetics, "exploratory").json()["records"] if r["kinetics_ref"] == ref)
    cur_k = next(r for r in _get(client, kinetics, "curated").json()["records"] if r["kinetics_ref"] == ref)
    assert exp_k["levels"]["frequency"] is not None and exp_k["levels"]["frequency_source"] == "freq"
    assert cur_k["levels"]["frequency"] is None and cur_k["levels"]["frequency_source"] is None
    # The checklist counts the calculation types under the transition-state ENTRIES that clear the record floor (entries are
    # records, ADR 0023 Q3), minus the calculations the evidence rule withholds: ``kin_vn`` cites nothing and reaches a freq
    # only through the reaction's hidden entry, so it counts none. (A freq under a SHOWN entry counts even unreviewed: see
    # test_curated_evidence_counts_and_levels.py.)
    ref = world.visible["kin_vn"].public_ref
    exp_n = next(r for r in _get(client, kinetics, "exploratory").json()["records"] if r["kinetics_ref"] == ref)
    cur_n = next(r for r in _get(client, kinetics, "curated").json()["records"] if r["kinetics_ref"] == ref)
    assert exp_n["evidence_completeness"]["checklist"]["has_ts_freq_evidence"] is True
    assert cur_n["evidence_completeness"]["checklist"]["has_ts_freq_evidence"] is False


def _extra_hidden_calculation_world(db_session, world, status=RecordReviewStatus.rejected):
    """A level of theory used ONLY by calculations in ``status``: one on a visible TS entry, one on a visible species
    entry. ``None`` leaves them never reviewed. Under the evidence rule (ADR 0023) a rejected calculation is withheld
    from a match and a not-reviewed one is not."""
    lot = make_lot(db_session, method="zzhidden", basis="zzbasis")
    entry = make_transition_state_entry(db_session, transition_state=world.visible["ts_v"], multiplicity=8)
    set_review(db_session, record_type=R.transition_state_entry, record_id=entry.id, status=APPROVED)
    calc = make_calculation(db_session, type=CalculationType.freq, transition_state_entry_id=entry.id, lot_id=lot.id)
    species_entry = make_species_entry(db_session, make_species(db_session, inchi_key=next_inchi_key("SWF")))
    set_review(db_session, record_type=R.species_entry, record_id=species_entry.id, status=APPROVED)
    other = make_calculation(db_session, type=CalculationType.freq, species_entry_id=species_entry.id, lot_id=lot.id)
    if status is not None:
        for c in (calc, other):
            set_review(db_session, record_type=R.calculation, record_id=c.id, status=status)
    db_session.flush()
    return entry, species_entry


def test_search_filters_agree_with_the_flags_the_records_serve(client, db_session, login_as):
    """Under curated a filter on evidence ignores a rejected calculation, so ``has_irc=true`` never returns a record
    that serves ``has_irc: false``; the method/basis filter cannot find a level used only by rejected calculations, on
    the transition-state routes or on the species browse.

    Kills: leaving any one filter on the unfiltered calculation set. (A not-reviewed calculation DOES match, see the
    twin below; the served flags follow in step 3 of ADR 0023.)
    """
    world = build_sweep_world(db_session, withheld=True)
    login_as(world.extra["curator"].id)
    tse_x, species_entry = _extra_hidden_calculation_world(db_session, world)
    only_hidden = world.visible["tse_vw"].public_ref

    flags = {
        "has_calculations": lambda r: r["available_sections"]["has_calculations"],
        "has_opt": lambda r: r["evidence_summary"]["has_opt"],
        "has_freq": lambda r: r["evidence_summary"]["has_freq"],
        "has_sp": lambda r: r["evidence_summary"]["has_sp"],
        "has_irc": lambda r: r["evidence_summary"]["has_irc"],
        "has_path_search": lambda r: r["evidence_summary"]["has_path_search"],
        "has_geometry_validation": lambda r: r["evidence_summary"]["has_geometry_validation"],
        "has_scf_stability": lambda r: r["evidence_summary"]["has_scf_stability"],
    }
    for route in ("/transition-states/browse", "/transition-states/search"):
        extra = {"reaction_entry_ref": world.extra["reaction_entry"].public_ref} if route.endswith("search") else {}
        for name, served in flags.items():
            for want in (True, False):
                rows = _get(client, route, "curated", **{name: str(want).lower()}, limit=100, **extra)
                assert rows.status_code == 200, (route, name, rows.text)
                for record in rows.json()["records"]:
                    assert served(record) is want, f"{route} {name}={want} returned a record serving {not want}"
        # The entry whose every calculation is hidden answers the "absent" side of each filter, and only that side.
        for name in ("has_irc", "has_opt", "has_calculations", "has_geometry_validation", "has_scf_stability"):
            for want in (True, False):
                refs = {
                    r["transition_state_entry"]["transition_state_entry_ref"]
                    for r in _get(client, route, "curated", **{name: str(want).lower()}, limit=100, **extra).json()[
                        "records"
                    ]
                }
                assert (only_hidden in refs) is (not want), (route, name, want)

        found = _get(client, route, "exploratory", method="zzhidden", limit=100, **extra).json()["records"]
        assert [r["transition_state_entry"]["transition_state_entry_ref"] for r in found] == [tse_x.public_ref]
        assert _get(client, route, "curated", method="zzhidden", limit=100, **extra).json()["records"] == []
        assert _get(client, route, "curated", basis="zzbasis", limit=100, **extra).json()["records"] == []

    exploratory = _get(client, "/species/browse", "exploratory", method="zzhidden", limit=100).json()["records"]
    assert exploratory, "the species browse finds nothing under exploratory: the check is vacuous"
    assert _get(client, "/species/browse", "curated", method="zzhidden", limit=100).json()["records"] == []
    assert _get(client, "/species/browse", "curated", basis="zzbasis", limit=100).json()["records"] == []


def test_a_not_reviewed_calculation_matches_a_curated_method_filter(client, db_session, login_as):
    """The twin of the test above: a calculation nobody has reviewed is evidence, so a curated method/basis filter finds
    its level on the transition-state routes and on the species browse (ADR 0023, step 4).

    Kills: matching only through approved calculations (the earlier rule).
    """
    world = build_sweep_world(db_session, withheld=True)
    login_as(world.extra["curator"].id)
    tse_x, species_entry = _extra_hidden_calculation_world(db_session, world, status=None)
    for route in ("/transition-states/browse", "/transition-states/search"):
        extra = {"reaction_entry_ref": world.extra["reaction_entry"].public_ref} if route.endswith("search") else {}
        for profile in ("exploratory", "curated"):
            found = _get(client, route, profile, method="zzhidden", limit=100, **extra).json()["records"]
            assert [r["transition_state_entry"]["transition_state_entry_ref"] for r in found] == [tse_x.public_ref], (
                route,
                profile,
            )
    for profile in ("exploratory", "curated"):
        assert _get(client, "/species/browse", profile, method="zzhidden", limit=100).json()["records"], profile


def test_coverage_and_atom_map_counts_follow_the_floor(client, db_session, login_as):
    """The concept's geometry-validation and SCF-stability coverage, and a reaction's ``has_atom_map``, count only what
    the profile shows. The sweep comparator ignores these keys, so they are asserted here.

    Kills: removing the floor from the coverage join; removing it from ``has_atom_map``.
    """
    world = build_sweep_world(db_session, withheld=True)
    login_as(world.extra["curator"].id)
    ts = world.visible["ts_vw"].public_ref
    exp = _get(client, f"/transition-states/{ts}", "exploratory").json()["record"]["evidence_summary"][
        "evidence_coverage"
    ]
    cur = _get(client, f"/transition-states/{ts}", "curated").json()["record"]["evidence_summary"]["evidence_coverage"]
    assert (exp["geometry_validation"], exp["scf_stability"]) == (1, 1)
    assert (cur["geometry_validation"], cur["scf_stability"]) == (0, 0)

    ref = world.extra["reaction_entry_all_hidden"].public_ref

    def has_atom_map(profile):
        rows = _get(client, "/reactions/browse", profile, limit=100).json()["records"]
        return next(r for r in rows if r["reaction_entry_ref"] == ref)["availability"]["has_atom_map"]

    assert has_atom_map("exploratory") is True and has_atom_map("curated") is False


# ---------------------------------------------------------------------------
# The registries and the subject rule
# ---------------------------------------------------------------------------


def test_the_subject_of_every_enabled_route_sits_at_record_or_records():
    """``_is_subject`` keys off the names ``record`` and ``records``: only a value at one of those keys is protected from
    being deleted as a pointer. An enabled route whose envelope has neither key must not hold a pointer model as a
    single block at its top level (the guard would null it); lists of pointer elements, which the guard may thin, are
    fine, and so is a route named below with the reason it has no subject block.

    Kills: a new enabled route whose subject sits under another key and is itself a pointer model.
    """
    from typing import get_args

    from pydantic import BaseModel

    from app.api.app import create_app

    def classes(annotation):
        yield annotation
        for arg in get_args(annotation):
            yield from classes(arg)

    app = create_app()
    checked = 0
    offenders = []
    for route in app.routes:
        path = getattr(route, "path", "")
        model = getattr(route, "response_model", None)
        if not (
            path.startswith(BASE)
            and not ref_guard.route_matches(path, ref_guard.EXEMPT_ROUTE_PREFIXES)
            and isinstance(model, type)
            and issubclass(model, BaseModel)
        ):
            continue
        checked += 1
        if {"record", "records"} & set(model.model_fields):
            continue
        for name, info in model.model_fields.items():
            annotation = info.annotation
            is_list = getattr(annotation, "__origin__", None) in (list, dict) or any(
                getattr(a, "__origin__", None) in (list, dict) for a in classes(annotation)
            )
            if not is_list and any(c in ref_guard.POINTER_MODELS for c in classes(annotation)):
                offenders.append(f"{path}:{name}")
    assert checked > 40, "the scan found almost no enabled route: it is broken"
    assert not offenders, offenders


def test_trust_verdicts_rest_only_on_the_evidence_the_profile_shows(client, db_session, login_as):
    """A trust fragment is a checklist over the evidence graph. Under curated a hidden optimisation, IRC or geometry
    validation counts for nothing: ``tse_vw`` has only hidden calculations, and ``kin_v`` cites only hidden ones. The
    collections swapped for the evaluation are put back, so a read after a curated one is unchanged.

    Kills: evaluating over the whole graph (a verdict that says a hidden record exists); leaving the graph swapped.
    """
    world = build_sweep_world(db_session, withheld=True)
    login_as(world.extra["curator"].id)
    entry = f"/transition-state-entries/{world.visible['tse_vw'].public_ref}"
    exp = _get(client, entry, "exploratory", include="trust").json()["record"]["trust"]["evidence"]["checks"]
    cur = _get(client, entry, "curated", include="trust").json()["record"]["trust"]["evidence"]["checks"]
    shown = ("supporting_calculations_present", "ts_optimization_present", "irc_evidence_present")
    assert all(exp[name] == "passed" for name in shown), exp
    assert not any(cur[name] == "passed" for name in shown), cur
    assert _get(client, entry, "exploratory", include="trust").json()["record"]["trust"]["evidence"]["checks"] == exp

    kinetics = f"/reaction-entries/{world.extra['reaction_entry'].public_ref}/kinetics"
    ref = world.visible["kin_v"].public_ref

    def checks(profile):
        records = _get(client, kinetics, profile, include="trust").json()["records"]
        return next(r for r in records if r["kinetics_ref"] == ref)["trust"]["evidence"]["checks"]

    assert checks("exploratory")["source_calculations_present"] == "passed"
    assert checks("curated")["source_calculations_present"] == "missing"
    assert checks("exploratory")["source_calculations_present"] == "passed"


def test_an_id_whose_type_sits_in_a_sibling_key_is_seen_by_the_leak_check(db_session):
    """``{"record_type": "kinetics", "record_id": 7}`` names its type in a sibling key; the sweep's id check reads it.

    Kills: dropping the sibling-key rule from ``id_leaks`` (the review-rows leak went unseen for a PR because the stem
    of ``record_id`` names no type).
    """
    import json

    world = build_sweep_world(db_session, withheld=True)
    hidden = world.hidden["kin_h"]
    body = json.dumps(
        {"review_records": [{"record_type": "kinetics", "record_id": hidden.id, "status": "under_review"}]}
    )
    assert id_leaks(body, world) == {("id:kin_h", "review_records[*].record_id")}
    visible = json.dumps({"review_records": [{"record_type": "kinetics", "record_id": world.visible["kin_v"].id}]})
    assert id_leaks(visible, world) == set()


def test_the_legacy_composite_annotation_follows_the_masked_levels():
    """``legacy_composite_shape`` says a named-method level (a catalogued recipe) sits on a non-composite calculation.
    When the calculation that supplied that level is withheld the annotation goes with the level; one supplied by a
    shown calculation stays.

    Kills: computing the annotation before the mask and keeping it.
    """
    from app.schemas.reads.scientific_common import (
        CompositeSchemeSummary,
        LevelOfTheorySummary,
        ScientificLevelsSummary,
    )
    from app.services.scientific_read.kinetics import _without_levels_from_withheld_calculations

    named = LevelOfTheorySummary(
        level_of_theory_id=1,
        level_of_theory_ref="lot_named",
        method="CBS-QB3",
        composite_scheme=CompositeSchemeSummary(composite_scheme_ref="cs_x", kind="named_method", name="CBS-QB3"),
    )
    plain = LevelOfTheorySummary(level_of_theory_id=2, level_of_theory_ref="lot_plain", method="B3LYP", basis="6-31G(d)")
    levels = ScientificLevelsSummary(
        geometry=plain,
        geometry_source="opt",
        frequency=named,
        frequency_source="freq",
        legacy_composite_shape="named_method_level_on_non_composite_calculation",
    )
    kept = _without_levels_from_withheld_calculations(levels, withheld=set(), opt_calc_id=10, freq_calc_id=11, sp_calc_id=None)
    assert kept.legacy_composite_shape is not None
    # The freq is withheld: its level goes, and so does the annotation that rested on it.
    masked = _without_levels_from_withheld_calculations(levels, withheld={11}, opt_calc_id=10, freq_calc_id=11, sp_calc_id=None)
    assert masked.frequency is None and masked.legacy_composite_shape is None
    assert masked.geometry == plain
    # A withheld opt does not touch an annotation that rests on the shown freq.
    other = _without_levels_from_withheld_calculations(levels, withheld={10}, opt_calc_id=10, freq_calc_id=11, sp_calc_id=None)
    assert other.geometry is None and other.legacy_composite_shape is not None


def test_a_hidden_parent_of_a_visible_optimisation_is_neither_shown_nor_counted(client, db_session, login_as):
    """The only path search of ``tse_vp`` is the hidden parent (``optimized_from``) of its visible optimisation. Under
    curated the dependency edge and the parent's ref are absent from ``/full``, the visible optimisation is still
    shown, and the trust verdict does not find the path search through the edge. Exploratory shows all of it.

    Kills: letting the trust evaluation follow a dependency edge to a hidden calculation; the edge surviving in /full.
    """
    world = build_sweep_world(db_session, withheld=True)
    login_as(world.extra["curator"].id)
    hidden = world.hidden["calc_h_psp"].public_ref
    opt = world.visible["calc_vp_opt"].public_ref
    entry = world.visible["tse_vp"].public_ref
    full = f"/reaction-entries/{world.extra['reaction_entry'].public_ref}/full"

    def edges(profile):
        body = _get(client, full, profile, include="transition_states").json()
        item = next(t for t in body["transition_states"] if t["transition_state_entry_ref"] == entry)
        return item, body

    exp, _ = edges("exploratory")
    assert any(
        d["parent_calculation_ref"] == hidden and d["child_calculation_ref"] == opt for d in exp["dependencies"]
    ), "the world builds no hidden-parent edge"
    cur, body = edges("curated")
    assert hidden not in str(body)
    assert not [d for d in cur["dependencies"] if hidden in (d["parent_calculation_ref"], d["child_calculation_ref"])]
    assert opt in str(body), "the visible optimisation is no longer shown"

    detail = f"/transition-state-entries/{entry}"
    exp_checks = _get(client, detail, "exploratory", include="trust").json()["record"]["trust"]["evidence"]["checks"]
    cur_checks = _get(client, detail, "curated", include="trust").json()["record"]["trust"]["evidence"]["checks"]
    assert exp_checks["path_search_evidence_present"] == "passed"
    assert cur_checks["path_search_evidence_present"] != "passed"
    assert cur_checks["ts_optimization_present"] == "passed"
