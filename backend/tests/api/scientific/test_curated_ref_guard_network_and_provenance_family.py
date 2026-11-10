"""PR-2c: the network, literature, analytics and provenance-vocabulary route families withhold hidden refs under
``?profile=curated``.

The families are the networks, network solves and fits, literature, analytics, levels of theory, frequency scale
factors, energy-correction schemes and composite schemes. What this pins, beyond the all-routes sweep
(``test_curated_ref_sweep.py``, which is enforce-only since PR-5 and covers every route):

* **Leaks.** Every route of the families, crawled under ``curated`` with and without ``internal_ids`` and with every
  include token, names no hidden record by ref, integer id, URL or free text, and answers 200.
* **No substitution.** Curated is a withholding of exploratory on those routes.
* **D1.** A visible solve (or fit) of a hidden network stays visible with ``network`` null; no cascading hide. Widening
  the field alone is not enough: the owner block is also a pointer.
* **D4.** A hidden ref the guard cannot place still fails closed.
* **D5.** Every count, flag and filter of the families agrees with what the same profile serves. The sweep comparator
  ignores population keys, so each is pinned here, against the list or block the count describes.
* **Population.** The batch evaluate of a network evaluates the fits of the solves the profile shows and counts nothing
  else against its grid cap: a fit of a hidden solve was an existence oracle (404 naming the fit, and a cap message that
  counted it).
* **Backstops.** The service filters the lists it counts; the registered pointers are what the guard falls back on, and
  each is exercised against an unfiltered payload.
"""

from __future__ import annotations

import pytest

from app.db.models.common import ProfileRecommendation, ReadProfile, RecordReviewStatus
from app.services.scientific_read import ref_guard
from app.services.scientific_read.profile import ResolvedReadProfile
from tests.api.scientific._curated_families import NETWORK_PROVENANCE_PREFIXES
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

APPROVED = RecordReviewStatus.approved
BASE = "/api/v1/scientific"
CURATED = ResolvedReadProfile(profile=ReadProfile.curated, recommendation=ProfileRecommendation.approved_floor_only)


def _is_selection_document(path: str) -> bool:
    """Hashed, replayable documents (``.../select``, ``/manifest``, ``/export-selected``): their own seam in PR-3."""
    return path.endswith(("/select", "/select/manifest", "/export-selected"))


def _in_family(path: str) -> bool:
    return ref_guard.route_matches(path, NETWORK_PROVENANCE_PREFIXES) and not _is_selection_document(path)


def _family(specs):
    return [s for s in specs if _in_family(s.path)]


def _request_text(spec, world, label, *, ids):
    for variant in variants_for(spec, world, with_internal_ids=ids):
        if variant.label == label:
            return variant.request_text
    return ""


def _get(client, path, profile, **params):
    return client.get(f"{BASE}{path}", params={**params, "profile": profile})


def _post(client, path, profile, body):
    return client.post(f"{BASE}{path}", params={"profile": profile}, json=body)


def _text(model) -> str:
    return model.model_dump_json()


# ---------------------------------------------------------------------------
# The crawl and the ratchet
# ---------------------------------------------------------------------------


def test_network_and_provenance_routes_name_no_hidden_record_under_curated(client, db_session, login_as, monkeypatch):
    """The crawl that finds the families' leaks on ``main`` finds none, on a world that holds every response shape.

    Also: no route is left unplaceable (a 500 from ``RefGuardUnplaceable``), every request answers 200, and curated is
    only ever a withholding of exploratory (no sibling is substituted into a gap).

    Kills: not enabling a prefix; a block whose pointer class is missing (the request fails closed, so this crawl sees
    a 500); a hide that substitutes a visible sibling; a crawl that visits nothing.
    """
    from app.api.config import settings

    world = build_sweep_world(db_session)
    login_as(world.extra["curator"].id)
    specs = _family(enumerate_routes(client.app))
    assert len(specs) >= 25, "the family crawl is empty or truncated"
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
    assert sum(len(r.applied_sites) for r in curated.values()) > 30, "the enabled guard withheld almost nothing"
    exploratory_text = "\n".join(r.text for r in exploratory.values())
    for key in ("calc_h", "calc_h_lot", "calc_h_comp", "nsolve_h", "nkin_h", "nkin_h_plog", "tse_h", "sm_h", "net_h"):
        assert hidden[key] in exploratory_text, f"positive control: {key} never named under exploratory"
    assert not problems, "\n".join(problems)


def test_both_evaluate_routes_reach_the_boundary(client, db_session, login_as, monkeypatch):
    """The two evaluate routes return their model directly, so they call the hook themselves.

    Kills: removing the hook call from either route (the sweep's KNOWN_UNGUARDED check says the same).
    """
    world = build_sweep_world(db_session, withheld=True)
    login_as(world.extra["curator"].id)
    spy = install_spy(monkeypatch)
    nkin = world.visible["nkin_v"].public_ref
    net = world.visible["net_v"].public_ref
    spy.reset()
    one = _get(client, f"/network-kinetics/{nkin}/evaluate", "curated", temperature_k=1000.0, pressure_bar=1.0)
    assert one.status_code == 200 and spy.reached
    spy.reset()
    batch = _post(
        client, f"/networks/{net}/kinetics/evaluate", "curated", {"temperature_k": [1000.0], "pressure_bar": [1.0]}
    )
    assert batch.status_code == 200 and spy.reached


# ---------------------------------------------------------------------------
# D1 and D4: the owner blocks of a solve and of a fit
# ---------------------------------------------------------------------------


def test_a_visible_solve_or_fit_of_a_hidden_network_stays_visible_with_the_owner_blanked(client, db_session, login_as):
    """D1: no cascading hide. The solve, its evidence and review are kept, and so is the fit; only the owner goes.

    Kills: hiding the child (404 or dropped from search); leaving the owner named; blanking an owner that is visible;
    touching exploratory.
    """
    world = build_sweep_world(db_session, withheld=True)
    login_as(world.extra["curator"].id)
    hidden_net = world.hidden["net_h"].public_ref
    solve = world.visible["nsolve_v_under_hidden_net"].public_ref
    fit = world.visible["nkin_v_under_hidden_net"].public_ref

    cur = _get(client, f"/network-solves/{solve}", "curated", include="all")
    assert cur.status_code == 200, cur.text
    record = cur.json()["record"]
    assert record["network"] is None and record["network_solve"]["network_solve_ref"] == solve
    assert record["evidence_summary"] is not None and hidden_net not in cur.text
    assert (
        _get(client, f"/network-solves/{solve}", "exploratory").json()["record"]["network"]["network_ref"] == hidden_net
    )

    cur_fit = _get(client, f"/network-kinetics/{fit}", "curated", include="all")
    assert cur_fit.status_code == 200, cur_fit.text
    fit_record = cur_fit.json()["record"]
    assert fit_record["network"] is None and fit_record["network_kinetics"]["network_kinetics_ref"] == fit
    assert hidden_net not in cur_fit.text
    assert (
        _get(client, f"/network-kinetics/{fit}", "exploratory").json()["record"]["network"]["network_ref"] == hidden_net
    )

    # The search lists them too, owner blanked; a solve of a visible network keeps its owner.
    rows = _get(client, "/network-solves/search", "curated", has_chebyshev="true", limit=100).json()["records"]
    by_ref = {r["network_solve"]["network_solve_ref"]: r for r in rows}
    assert by_ref[solve]["network"] is None
    assert by_ref[world.visible["nsolve_v"].public_ref]["network"]["network_ref"] == world.visible["net_v"].public_ref
    fits = _get(client, "/network-kinetics/search", "curated", has_chebyshev="true", limit=100).json()["records"]
    by_fit = {r["network_kinetics"]["network_kinetics_ref"]: r for r in fits}
    assert by_fit[fit]["network"] is None and by_fit[world.visible["nkin_v"].public_ref]["network"] is not None


@pytest.mark.parametrize(
    ("owner_model", "route", "key"),
    [
        ("NetworkContextSummary", "/network-solves/{ref}", "nsolve_v_under_hidden_net"),
        ("NetworkKineticsNetworkContext", "/network-kinetics/{ref}", "nkin_v_under_hidden_net"),
    ],
)
def test_widening_the_owner_field_alone_still_fails_closed(
    client, db_session, login_as, monkeypatch, owner_model, route, key
):
    """D4 and the #770-review correction: without the owner block registered as a pointer the guard has nowhere to put
    a hidden network ref, so the request fails instead of leaking.

    Kills: treating the nullable field as enough; dropping a D1 registration.
    """
    world = build_sweep_world(db_session, withheld=True)
    login_as(world.extra["curator"].id)
    model = next(m for m in ref_guard.POINTER_MODELS if m.__name__ == owner_model)
    monkeypatch.setattr(ref_guard, "POINTER_MODELS", ref_guard.POINTER_MODELS - {model})
    with pytest.raises(ref_guard.RefGuardUnplaceable) as caught:
        _get(client, route.format(ref=world.visible[key].public_ref), "curated")
    assert caught.value.ref_prefix == "net"


# ---------------------------------------------------------------------------
# Backstops: every pointer registered for these families, against an unfiltered payload
# ---------------------------------------------------------------------------


def _blocks(db_session, world):
    """``(model name, factory)`` for each pointer whose list the service already filters. A factory returns a fresh,
    UNFILTERED payload (built outside a curated request) that names a hidden record in that block."""
    from app.services.scientific_read.frequency_scale_factors import get_frequency_scale_factor
    from app.services.scientific_read.network_kinetics_batch_evaluate import evaluate_network_kinetics_batch
    from app.services.scientific_read.networks import get_network, get_network_solve

    net = world.visible["net_v"].public_ref
    solve = world.visible["nsolve_v"].public_ref
    fsf = world.extra["fsf"].public_ref

    def network(*include):
        return lambda: get_network(db_session, network_handle=net, include=list(include)).record

    def solve_record(*include):
        return lambda: get_network_solve(db_session, network_solve_handle=solve, include=list(include)).record

    return [
        ("NetworkSolveSummary", network("solves")),
        ("NetworkKineticsSummary", network("kinetics")),
        ("NetworkSourceCalculationSummary", solve_record("source_calculations")),
        ("NetworkChannelMicroreactionSummary", network("channels")),
        ("NetworkSolveStateEnergySourceSummary", solve_record("state_energies")),
        (
            "NetworkKineticsBatchFitEvaluation",
            lambda: evaluate_network_kinetics_batch(
                db_session, network_handle=net, temperatures_k=[1000.0], pressures_bar=[1.0]
            ),
        ),
        (
            "FrequencyScaleFactorUsageSummary",
            lambda: (
                get_frequency_scale_factor(db_session, frequency_scale_factor_handle=fsf, include=["used_by"]).record
            ),
        ),
    ]


def test_every_block_the_families_register_is_placed_by_the_guard(db_session, monkeypatch):
    """The service filters these lists before the guard sees them (the counts need the same population), so the
    registrations are the backstop. Each is exercised on an unfiltered payload: with the block registered the hidden
    element is deleted whole; without it the guard either fails closed or places the ref differently.

    Kills: removing any one of these registrations; a block the world no longer builds.
    """
    world = build_sweep_world(db_session, withheld=True)
    hidden = set(world.hidden_refs().values())
    registry = ref_guard.POINTER_MODELS

    def named(payload):
        text = _text(payload)
        return {ref for ref in hidden if ref in text}

    for name, factory in _blocks(db_session, world):
        model = next((m for m in registry if m.__name__ == name), None)
        assert model is not None, f"{name} is not registered in POINTER_MODELS"
        before = factory()
        assert named(before), f"{name}: the unfiltered payload names no hidden record: the world does not reach it"
        ref_guard.withhold_in_model(db_session, before, profile=CURATED)
        assert not named(before), f"{name}: a hidden ref survived the guard"
        reference = _text(before)

        monkeypatch.setattr(ref_guard, "POINTER_MODELS", registry - {model})
        try:
            other = factory()
            ref_guard.withhold_in_model(db_session, other, profile=CURATED)
            same = _text(other) == reference
        except ref_guard.RefGuardUnplaceable:
            same = False
        finally:
            monkeypatch.setattr(ref_guard, "POINTER_MODELS", registry)
        assert not same, f"unregistering {name} changed nothing: the registration is not exercised"


# ---------------------------------------------------------------------------
# The batch evaluate is not an existence oracle
# ---------------------------------------------------------------------------


def test_the_batch_evaluate_evaluates_only_the_fits_of_visible_solves(client, db_session, login_as, monkeypatch):
    """A fit is reviewed through its solve. The batch evaluate used to list every fit of the network, evaluate each
    through the handle gate (404 naming a hidden fit) and count them against the grid cap (so the cap message gave the
    number of fits, hidden ones included). Under curated the population is the fits of the solves the profile shows.

    The cap is lowered so that all four fits break it and the two visible ones do not.

    Kills: removing the population filter (curated answers 404, or 422 with the hidden count).
    """
    from app.services.scientific_read import network_kinetics_batch_evaluate as module

    world = build_sweep_world(db_session, withheld=True)
    login_as(world.extra["curator"].id)
    net = world.visible["net_v"].public_ref
    visible_fits = {world.visible["nkin_v"].public_ref, world.visible["nkin_v_hidden_sources"].public_ref}
    hidden_fits = {world.hidden["nkin_h"].public_ref, world.hidden["nkin_h_plog"].public_ref}
    body = {"temperature_k": [1000.0, 1100.0], "pressure_bar": [1.0]}

    exploratory = _post(client, f"/networks/{net}/kinetics/evaluate", "exploratory", body)
    assert {f["network_kinetics_ref"] for f in exploratory.json()["fits"]} == visible_fits | hidden_fits

    monkeypatch.setattr(module, "BATCH_EVALUATE_POINT_CAP", 4)
    refused = _post(client, f"/networks/{net}/kinetics/evaluate", "exploratory", body)
    assert refused.status_code == 422 and "4 stored" in refused.text

    curated = _post(client, f"/networks/{net}/kinetics/evaluate", "curated", body)
    assert curated.status_code == 200, curated.text
    assert {f["network_kinetics_ref"] for f in curated.json()["fits"]} == visible_fits
    assert not any(ref in curated.text for ref in hidden_fits)
    ordered = sorted((world.visible[k] for k in ("nkin_v", "nkin_v_hidden_sources")), key=lambda row: row.id)
    assert [f["network_kinetics_ref"] for f in curated.json()["fits"]] == [row.public_ref for row in ordered], (
        "the batch lists fits in ascending row id"
    )


# ---------------------------------------------------------------------------
# D5: networks, solves and fits
# ---------------------------------------------------------------------------


def test_network_counts_flags_and_blocks_agree_with_what_is_served(client, db_session, login_as):
    """Every count and flag of a network agrees with the block it describes under the same profile, and none counts a
    hidden solve, fit, source calculation or microreaction.

    Kills: counting hidden solves, fits or source calculations; an envelope that reaches past the visible solves;
    ``has_plog`` or a channel's ``has_kinetics`` resting on a hidden fit; listing a microreaction of a hidden entry.
    """
    world = build_sweep_world(db_session, withheld=True)
    login_as(world.extra["curator"].id)
    net = world.visible["net_v"].public_ref
    hidden_entry = world.hidden["tse_h"].public_ref

    cur = _get(client, f"/networks/{net}", "curated", include="all").json()["record"]
    exp = _get(client, f"/networks/{net}", "exploratory", include="all").json()["record"]
    ev, xv = cur["evidence_summary"], exp["evidence_summary"]

    assert ev["solve_count"] == len(cur["solves"]) == 2 and xv["solve_count"] == len(exp["solves"]) == 3
    assert ev["kinetics_count"] == len(cur["kinetics"]) == 2 and xv["kinetics_count"] == len(exp["kinetics"]) == 4
    assert ev["source_calculation_count"] == len(cur["source_calculations"])
    assert xv["source_calculation_count"] == len(exp["source_calculations"]) > ev["source_calculation_count"]
    assert (ev["has_chebyshev"], ev["has_plog"]) == (True, False) and xv["has_plog"] is True
    assert cur["network"]["solve_temperature_max_k"] == 2000.0 and exp["network"]["solve_temperature_max_k"] == 5000.0
    assert cur["network"]["solve_pressure_max_bar"] == 100.0 and exp["network"]["solve_pressure_max_bar"] == 1000.0
    available = cur["available_sections"]
    assert available["has_solves"] and available["has_kinetics"] and available["has_source_calculations"]
    # A solve summary's own source count is the count its detail page serves, and the shared solve's is the smaller one.
    exp_by_solve = {s["network_solve_ref"]: s for s in exp["solves"]}
    for summary in cur["solves"]:
        ref = summary["network_solve_ref"]
        detail = _get(client, f"/network-solves/{ref}", "curated").json()["record"]
        assert summary["source_calculation_count"] == detail["evidence_summary"]["source_calculation_count"], ref
    shared = world.visible["nsolve_v"].public_ref
    assert (
        next(s for s in cur["solves"] if s["network_solve_ref"] == shared)["source_calculation_count"]
        < exp_by_solve[shared]["source_calculation_count"]
    )

    by_key = {c["channel_key"]: c for c in cur["channels"]}
    exp_by_key = {c["channel_key"]: c for c in exp["channels"]}
    only_hidden_fit = "ch-visible-net-hidden-solve"
    assert exp_by_key[only_hidden_fit]["has_kinetics"] is True and by_key[only_hidden_fit]["has_kinetics"] is False
    visible_channel = "ch-visible-net-visible-solve"
    assert [m["path_kind"] for m in exp_by_key[visible_channel]["microreactions"]].count("saddle_point") == 2
    assert [m["path_kind"] for m in by_key[visible_channel]["microreactions"]].count("saddle_point") == 1
    assert sum(1 for m in by_key[visible_channel]["microreactions"] if m["path_kind"] == "barrierless") == 1
    assert hidden_entry not in _get(client, f"/networks/{net}", "curated", include="all").text


def test_solve_counts_flags_and_blocks_agree_with_what_is_served(client, db_session, login_as):
    """A solve's evidence counts and section flags agree with its blocks; a barrier on a hidden entry is not listed
    and a hidden source calculation is not counted, named or used to fill a state energy's source.

    Kills: counting the hidden barrier or source calculation; serving a state-energy source whose calculation (or
    zero-point calculation) is hidden; the older single slot naming a hidden calculation; a stale ``partial_sources``.
    """
    world = build_sweep_world(db_session, withheld=True)
    login_as(world.extra["curator"].id)
    solve = world.visible["nsolve_v"].public_ref
    hidden_calc = world.hidden["calc_h"].public_ref

    cur = _get(client, f"/network-solves/{solve}", "curated", include="all").json()["record"]
    exp = _get(client, f"/network-solves/{solve}", "exploratory", include="all").json()["record"]
    assert cur["evidence_summary"]["source_calculation_count"] == len(cur["source_calculations"])
    assert exp["evidence_summary"]["source_calculation_count"] == len(exp["source_calculations"])
    assert cur["evidence_summary"]["source_calculation_count"] < exp["evidence_summary"]["source_calculation_count"]
    assert cur["available_sections"]["has_channel_barriers"] is True

    assert len(exp["channel_barriers"]) == 2 and len(cur["channel_barriers"]) == 1
    assert cur["channel_barriers"][0]["source_calculation_ref"] is None, "the visible barrier's hidden source is masked"
    assert any(b["source_calculation_ref"] == hidden_calc for b in exp["channel_barriers"])
    assert cur["channel_barriers"][0]["transition_state_entry_ref"] == world.visible["tse_v2"].public_ref

    assert sum(len(e["sources"]) for e in exp["state_energies"]) == 10
    assert sum(len(e["sources"]) for e in cur["state_energies"]) == 3
    single = [e for e in exp["state_energies"] if e["source_calculation_ref"] == hidden_calc]
    assert single, "the world builds no older single-slot state energy"
    assert all(e["source_calculation_ref"] is None for e in cur["state_energies"])
    assert hidden_calc not in _get(client, f"/network-solves/{solve}", "curated", include="all").text

    # A solve whose only barrier is on the hidden entry has barriers, and shows none.
    only_hidden_barrier = world.visible["nsolve_v_hidden_sources"].public_ref
    cur_b = _get(client, f"/network-solves/{only_hidden_barrier}", "curated", include="all").json()["record"]
    exp_b = _get(client, f"/network-solves/{only_hidden_barrier}", "exploratory", include="all").json()["record"]
    assert exp_b["available_sections"]["has_channel_barriers"] is True and len(exp_b["channel_barriers"]) == 1
    assert cur_b["available_sections"]["has_channel_barriers"] is False and cur_b["channel_barriers"] == []

    # Two participants, the second one's source (E: its zero-point calculation, F: its calculation) hidden: the source
    # goes and the state says its sources are incomplete, in the same breath.
    import hashlib

    for tag in ("e-state", "f-state"):
        digest = hashlib.sha256(tag.encode()).hexdigest()
        exp_e = next(e for e in exp["state_energies"] if e["state_composition_hash"] == digest)
        cur_e = next(e for e in cur["state_energies"] if e["state_composition_hash"] == digest)
        assert len(exp_e["sources"]) == 2 and exp_e["partial_sources"] is False, tag
        assert len(cur_e["sources"]) == 1 and cur_e["partial_sources"] is True, tag

    # The legacy single slot, hidden: G (two participants) and H (a slot owned by no participant). The default profile
    # serves one source and ``partial_sources: true``; curated serves neither, and never ``partial_sources: true`` beside
    # an empty ``sources`` (a shape the default profile cannot produce).
    for tag in ("g-state", "h-state"):
        digest = hashlib.sha256(tag.encode()).hexdigest()
        exp_g = next(e for e in exp["state_energies"] if e["state_composition_hash"] == digest)
        cur_g = next(e for e in cur["state_energies"] if e["state_composition_hash"] == digest)
        assert len(exp_g["sources"]) == 1 and exp_g["partial_sources"] is True, tag
        assert exp_g["source_calculation_ref"] is not None, tag
        assert cur_g["sources"] == [] and cur_g["partial_sources"] is False, tag
        assert cur_g["source_calculation_ref"] is None, tag
    assert cur["state_energies"], "no state energy served: the invariant below is vacuous"
    assert all(e["sources"] for e in cur["state_energies"] if e["partial_sources"])


def test_a_channel_whose_only_microreaction_is_hidden_keeps_its_mechanism_and_serves_none(client, db_session, login_as):
    """Owner decision D1 again: a visible record stays visible and its links to hidden records are dropped, even when
    the result is a shape no upload produces. An ``elementary`` channel whose only microreaction cites a hidden
    transition-state entry is served as ``mechanism: elementary, microreactions: []``; nothing else is invented
    (no mechanism change, no placeholder path, no dropped channel).

    Kills: dropping the channel; rewriting its mechanism; leaving the hidden path named.
    """
    world = build_sweep_world(db_session, withheld=True)
    login_as(world.extra["curator"].id)
    net = world.visible["net_v"].public_ref
    key = "ch-only-hidden-entry"

    def channel(profile):
        record = _get(client, f"/networks/{net}", profile, include="channels").json()["record"]
        return next(c for c in record["channels"] if c["channel_key"] == key)

    exp, cur = channel("exploratory"), channel("curated")
    assert exp["mechanism"] == "elementary" and [m["path_kind"] for m in exp["microreactions"]] == ["saddle_point"]
    assert cur["mechanism"] == "elementary" and cur["microreactions"] == []
    assert {k: v for k, v in cur.items() if k != "microreactions"} == {
        k: v for k, v in exp.items() if k != "microreactions"
    }


def test_a_reference_to_a_hidden_solve_is_nulled_and_never_flagged(client, db_session, login_as):
    """A validation entry that pins a hidden solve serves ``reference_solve_ref: null`` under curated like any ref to a
    hidden record. ``reference_withheld`` stays false on every read: upload refuses a reference to a solve the depositor
    cannot see, so the default profile never serves ``true`` and a curated ``true`` would be the one signal that a hidden
    solve exists.

    Kills: setting the flag when a reference is hidden; leaving the reference.
    """
    world = build_sweep_world(db_session, withheld=True)
    login_as(world.extra["curator"].id)
    solve = world.visible["nsolve_v"].public_ref
    hidden = world.hidden["nsolve_h"].public_ref

    def entries(profile):
        return _get(client, f"/network-solves/{solve}", profile).json()["record"]["network_solve"]["validation"][
            "entries"
        ]

    exp, cur = entries("exploratory"), entries("curated")
    assert exp and cur
    assert [e["reference_solve_ref"] for e in exp] == [hidden]
    assert [e["reference_solve_ref"] for e in cur] == [None]
    assert all(e["reference_withheld"] is False for e in [*exp, *cur])


def test_the_analytics_watermark_does_not_encode_the_id_of_a_hidden_record(client, db_session, login_as):
    """The watermark travels in ``next_cursor``, sealed under the server's key. Under curated its ``max_id`` is the largest
    id the profile shows, not the largest id in the table.

    Kills: taking the maximum over the unfloored table (a hidden newest record shows up as a larger ``max_id``).
    """
    import base64
    import json

    world = build_sweep_world(db_session)
    login_as(world.extra["curator"].id)

    def decoded(profile):
        body = _get(client, "/analytics/kinetics", profile, limit=1).json()
        cursor = body["next_cursor"]
        assert cursor, "the world has fewer than two kinetics rows: no cursor to decode"
        from app.services.scientific_read.keyset import unseal

        # The token is sealed, so a client cannot read the bound; the server's key opens it here.
        raw = unseal(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        return json.loads(raw)

    def find_max_id(node):
        if isinstance(node, dict):
            if "max_id" in node:
                return node["max_id"]
            for value in node.values():
                found = find_max_id(value)
                if found is not None:
                    return found
        return None

    exp_max, cur_max = find_max_id(decoded("exploratory")), find_max_id(decoded("curated"))
    visible_ids = [row.id for key, row in world.visible.items() if key.split("_", 1)[0] == "kin"]
    hidden_ids = [row.id for key, row in world.hidden.items() if key.split("_", 1)[0] == "kin"]
    assert max(hidden_ids) > max(visible_ids), "the world's newest kinetics is not hidden: the check is vacuous"
    assert exp_max == max(hidden_ids)
    assert cur_max == max(visible_ids)


def test_fit_detail_counts_agree_with_its_source_calculations(client, db_session, login_as):
    """A fit's ``source_calculation_count`` is the length of the list ``include=source_calculations`` serves.

    Kills: counting the solve's hidden source calculations in the fit's evidence.
    """
    world = build_sweep_world(db_session, withheld=True)
    login_as(world.extra["curator"].id)
    for key in ("nkin_v", "nkin_v_hidden_sources"):
        ref = world.visible[key].public_ref
        record = _get(client, f"/network-kinetics/{ref}", "curated", include="all").json()["record"]
        assert record["evidence_summary"]["source_calculation_count"] == len(record["source_calculations"]), key
        assert record["available_sections"]["has_source_calculations"] is bool(record["source_calculations"]), key
        exp = _get(client, f"/network-kinetics/{ref}", "exploratory", include="all").json()["record"]
        assert (
            exp["evidence_summary"]["source_calculation_count"]
            >= record["evidence_summary"]["source_calculation_count"]
        )
    only_hidden = _get(
        client, f"/network-kinetics/{world.visible['nkin_v_hidden_sources'].public_ref}", "curated", include="all"
    ).json()["record"]
    assert only_hidden["source_calculations"] == []


@pytest.mark.parametrize(
    ("path", "filters"),
    [
        (
            "/networks/search",
            [
                ("has_plog", "evidence_summary", "has_plog"),
                ("has_chebyshev", "evidence_summary", "has_chebyshev"),
                ("has_solves", "evidence_summary", "solve_count"),
                ("has_kinetics", "evidence_summary", "kinetics_count"),
            ],
        ),
        (
            "/network-solves/search",
            [
                ("has_source_calculations", "evidence_summary", "source_calculation_count"),
                ("has_plog", "evidence_summary", "has_plog"),
                ("has_kinetics", "evidence_summary", "kinetics_count"),
            ],
        ),
        ("/network-kinetics/search", [("has_source_calculations", "evidence_summary", "source_calculation_count")]),
    ],
)
def test_every_network_search_filter_agrees_with_the_flag_each_served_record_carries(
    client, db_session, login_as, path, filters
):
    """Under curated each evidence filter value returns exactly the records whose served flag (or count) says so.

    A filter that matched on a hidden solve, fit or source calculation would be an existence oracle: it would return a
    record whose served block says there is nothing there.

    Kills: any filter that looks at records the profile hides.
    """
    world = build_sweep_world(db_session, withheld=True)
    login_as(world.extra["curator"].id)
    checked = 0
    for name, block, field in filters:
        for value in ("true", "false"):
            records = _get(client, path, "curated", limit=100, **{name: value}).json()["records"]
            for record in records:
                served = record[block][field]
                assert bool(served) is (value == "true"), (path, name, value, served)
                checked += 1
    assert checked, "no record was served under any filter value: the check is vacuous"


def test_network_searches_do_not_find_a_network_by_what_only_a_hidden_record_has(client, db_session, login_as):
    """The envelope, kind and provenance filters ask about the solves, fits and calculations the profile shows.

    Kills: ``temperature_min`` matching the hidden solve's wider envelope; ``method`` matching a level that only a hidden
    calculation carries; ``has_plog`` matching the hidden PLOG fit.
    """
    world = build_sweep_world(db_session, withheld=True)
    login_as(world.extra["curator"].id)
    net = world.visible["net_v"].public_ref
    solves = {world.visible["nsolve_v"].public_ref, world.visible["nsolve_v_hidden_sources"].public_ref}

    def networks(profile, **params):
        rows = _get(client, "/networks/search", profile, limit=100, **params).json()["records"]
        return {r["network"]["network_ref"] for r in rows}

    for params in (
        {"temperature_min": 4000},
        {"pressure_min": 500},
        {"has_plog": "true"},
        {"method": "hiddenonly"},
        {"method": "solveonly"},  # a shown calculation, cited only by the hidden solve
    ):
        assert net in networks("exploratory", **params), params
        assert net not in networks("curated", **params), params

    # A visible network whose only solve (and fit) is hidden has none.
    only_hidden = world.visible["net_v_hidden_solves_only"].public_ref
    for params in ({"has_solves": "true"}, {"has_kinetics": "true"}, {"has_chebyshev": "true"}):
        assert only_hidden in networks("exploratory", **params), params
        assert only_hidden not in networks("curated", **params), params
    assert only_hidden in networks("curated", has_solves="false")

    found = lambda profile, **p: {  # noqa: E731
        r["network_solve"]["network_solve_ref"]
        for r in _get(client, "/network-solves/search", profile, limit=100, **p).json()["records"]
    }
    assert solves <= found("exploratory", method="hiddenonly")
    assert not found("curated", method="hiddenonly")
    assert world.visible["nsolve_v_hidden_sources"].public_ref in found("curated", has_source_calculations="false")
    assert world.visible["nsolve_v_hidden_sources"].public_ref not in found(
        "exploratory", has_source_calculations="false"
    )

    fits = lambda profile, **p: {  # noqa: E731
        r["network_kinetics"]["network_kinetics_ref"]
        for r in _get(client, "/network-kinetics/search", profile, limit=100, **p).json()["records"]
    }
    assert world.visible["nkin_v_hidden_sources"].public_ref in fits("exploratory", method="hiddenonly")
    assert not fits("curated", method="hiddenonly")
    assert not fits("curated", has_plog="true")


# ---------------------------------------------------------------------------
# D5: levels of theory, corrections, scale factors, literature
# ---------------------------------------------------------------------------


def test_level_of_theory_usage_follows_the_floor(client, db_session, login_as):
    """A level used only by hidden calculations is not listed, has no usage and no software breakdown; the shared level
    counts and lists only the calculations the profile shows, and a visible calculation whose owner entry is hidden keeps
    its row with the owner blanked.

    Kills: counting or listing a hidden calculation; listing a hidden-only level in browse or search; a ``used_by`` limit
    applied before the floor; leaving the hidden owner of a visible calculation named.
    """
    world = build_sweep_world(db_session, withheld=True)
    login_as(world.extra["curator"].id)
    only = world.extra["lot_hidden_only"].public_ref
    shared = world.extra["lot"].public_ref

    def levels(profile, path, **params):
        return {
            r["level_of_theory"]["level_of_theory_ref"]
            for r in _get(client, path, profile, limit=100, **params).json()["records"]
        }

    assert only in levels("exploratory", "/level-of-theories/search", method="hiddenonly")
    assert only not in levels("curated", "/level-of-theories/search", method="hiddenonly")
    assert only in levels("exploratory", "/level-of-theories/browse")
    assert only not in levels("curated", "/level-of-theories/browse")
    assert world.extra["lot"].public_ref in levels("curated", "/level-of-theories/browse")

    detail = _get(client, f"/level-of-theories/{only}", "curated", include="all").json()["record"]
    assert (
        detail["evidence_summary"]["calculation_usage_count"] == 0
        and detail["available_sections"]["has_used_by"] is False
    )
    assert detail["used_by"] == [] and detail["software"] == {"software": [], "workflow_tools": []}
    assert (
        detail["evidence_summary"]["distinct_software_count"] == 0
        and detail["available_sections"]["has_software"] is False
    )
    exp_only = _get(client, f"/level-of-theories/{only}", "exploratory", include="all").json()["record"]
    assert exp_only["evidence_summary"]["calculation_usage_count"] == 2
    assert (
        exp_only["evidence_summary"]["distinct_software_count"] == 1 and exp_only["available_sections"]["has_software"]
    )
    assert [s["software"] for s in exp_only["software"]["software"]] == ["hiddensoft"]
    assert [w["workflow_tool"] for w in exp_only["software"]["workflow_tools"]] == ["hiddenflow"]

    cur = _get(client, f"/level-of-theories/{shared}", "curated", include="all").json()["record"]
    exp = _get(client, f"/level-of-theories/{shared}", "exploratory", include="all").json()["record"]
    assert cur["evidence_summary"]["calculation_usage_count"] == len(cur["used_by"]) < len(exp["used_by"])
    assert (
        sum(row["calculation_count"] for row in cur["software"]["software"])
        <= cur["evidence_summary"]["calculation_usage_count"]
    )
    owned = world.visible["calc_ts_on_hidden_entry"].public_ref
    row = next(r for r in cur["used_by"] if r["calculation_ref"] == owned)
    assert (row["record_type"], row["record_ref"], row["record_endpoint"]) == ("transition_state_entry", None, None)
    assert any(r["calculation_ref"] == owned and r["record_ref"] for r in exp["used_by"])


def test_correction_scheme_usage_follows_the_floor(client, db_session, login_as):
    """An application to a hidden transition-state entry is not listed or counted; one to a visible entry whose source
    calculation is hidden is, with the source blanked. A scheme applied only to hidden records has no usage.

    Kills: counting or listing an application to a hidden entry; naming a hidden source calculation; filtering
    ``used_by_calculation`` on a hidden calculation or target.
    """
    world = build_sweep_world(db_session, withheld=True)
    login_as(world.extra["curator"].id)
    ecs, only = world.extra["ecs"].public_ref, world.extra["ecs_hidden_only"].public_ref
    hidden_calc = world.hidden["calc_h"].public_ref

    cur = _get(client, f"/energy-correction-schemes/{ecs}", "curated", include="all").json()["record"]
    exp = _get(client, f"/energy-correction-schemes/{ecs}", "exploratory", include="all").json()["record"]
    assert cur["evidence_summary"]["applied_usage_count"] == len(cur["used_by"]) == 1
    assert exp["evidence_summary"]["applied_usage_count"] == len(exp["used_by"]) == 2
    (kept,) = cur["used_by"]
    assert kept["record_type"] == "species_entry"
    assert kept["source_calculation_ref"] is None and kept["source_calculation_endpoint"] is None
    assert any(r["source_calculation_ref"] == hidden_calc for r in exp["used_by"])
    assert cur["energy_correction_scheme"]["note"] is None and exp["energy_correction_scheme"]["note"]

    none = _get(client, f"/energy-correction-schemes/{only}", "curated", include="all").json()["record"]
    assert none["used_by"] == [] and none["evidence_summary"]["applied_usage_count"] == 0
    assert none["evidence_summary"]["has_applied_usage"] is False and none["available_sections"]["has_used_by"] is False
    assert (
        _get(client, f"/energy-correction-schemes/{only}", "exploratory").json()["record"]["evidence_summary"][
            "applied_usage_count"
        ]
        == 1
    )

    def schemes(profile, **params):
        rows = _get(client, "/energy-correction-schemes/search", profile, limit=100, **params).json()["records"]
        return {r["energy_correction_scheme"]["energy_correction_scheme_ref"] for r in rows}

    assert {ecs, only} <= schemes("exploratory", used_by_calculation="true")
    assert not schemes("curated", used_by_calculation="true") & {ecs, only}


def test_frequency_scale_factor_usage_follows_the_floor(client, db_session, login_as):
    """Usage by a hidden statmech, or an application to a hidden entry, is not counted or listed.

    Kills: counting hidden statmech in ``statmech_usage_count``; listing the hidden user; ``used_by_statmech`` matching
    a hidden statmech.
    """
    world = build_sweep_world(db_session, withheld=True)
    login_as(world.extra["curator"].id)
    fsf, only = world.extra["fsf"].public_ref, world.extra["fsf_hidden_only"].public_ref

    cur = _get(client, f"/frequency-scale-factors/{fsf}", "curated", include="all").json()["record"]
    exp = _get(client, f"/frequency-scale-factors/{fsf}", "exploratory", include="all").json()["record"]
    assert cur["evidence_summary"]["statmech_usage_count"] == 1 and exp["evidence_summary"]["statmech_usage_count"] == 2
    assert [r["record_ref"] for r in cur["used_by"]] == [world.visible["sm_v2"].public_ref]
    assert cur["frequency_scale_factor"]["note"] is None and exp["frequency_scale_factor"]["note"]

    none = _get(client, f"/frequency-scale-factors/{only}", "curated", include="all").json()["record"]
    assert none["used_by"] == [] and none["evidence_summary"]["has_statmech_usage"] is False
    assert none["available_sections"]["has_used_by"] is False
    assert _get(client, f"/frequency-scale-factors/{only}", "exploratory", include="all").json()["record"]["used_by"]

    def factors(profile):
        rows = _get(client, "/frequency-scale-factors/search", profile, limit=100, used_by_statmech="true").json()[
            "records"
        ]
        return {r["frequency_scale_factor"]["frequency_scale_factor_ref"] for r in rows}

    assert {fsf, only} <= factors("exploratory")
    assert factors("curated") & {fsf, only} == {fsf}


def test_a_hidden_user_of_a_scale_factor_does_not_displace_a_shown_one_under_the_limit(
    client, db_session, login_as, monkeypatch
):
    """``used_by`` is bounded. The floor is applied before the limit, so a hidden statmech or a hidden application
    standing first in row order leaves its place to the shown ones behind it.

    The scale factor's users, in row order, are a hidden statmech, a shown statmech, an application to a hidden entry and
    one to a shown entry; with a limit of two the shown statmech and the shown application are what is served.

    Kills: a limit applied to hidden rows (the statmech floor or the application floor removed).
    """
    from app.services.scientific_read import frequency_scale_factors

    world = build_sweep_world(db_session, withheld=True)
    login_as(world.extra["curator"].id)
    ref = world.extra["fsf_limit"].public_ref
    monkeypatch.setattr(frequency_scale_factors, "_USAGE_LIMIT", 2)
    served = _get(client, f"/frequency-scale-factors/{ref}", "curated", include="used_by").json()["record"]["used_by"]
    assert {u["record_ref"] for u in served} == {
        world.visible["sm_v3"].public_ref,
        world.extra["species_entry"].public_ref,
    }


def test_literature_counts_agree_with_the_records_it_lists(client, db_session, login_as):
    """A literature record's linked-record counts are the records ``/records`` lists under the same profile.

    Kills: counting hidden calculations, thermo, kinetics, statmech, transport or networks; a ``has_linked_records``
    flag resting on a hidden record.
    """
    world = build_sweep_world(db_session)
    login_as(world.extra["curator"].id)
    for key in ("literature", "literature_hidden_only"):
        ref = world.extra[key].public_ref
        for profile in ("curated", "exploratory"):
            record = _get(client, f"/literature/{ref}", profile, include="all").json()["record"]
            listed = _get(client, f"/literature/{ref}/records", profile, limit=100).json()
            counts = record["record_counts"]
            assert counts["total_records"] == listed["pagination"]["total"], (key, profile)
            by_type: dict[str, int] = {}
            for r in listed["records"]:
                by_type[r["record_type"]] = by_type.get(r["record_type"], 0) + 1
            assert counts["calculations"] == by_type.get("calculation", 0), (key, profile)
            assert counts["statmech"] == by_type.get("statmech", 0) and counts["transport"] == by_type.get(
                "transport", 0
            )
            assert counts["network_solves"] == by_type.get("network_solve", 0)
            assert record["available_sections"]["has_linked_records"] is (counts["total_records"] > 0)
    only = world.extra["literature_hidden_only"].public_ref
    cur = _get(client, f"/literature/{only}", "curated", include="all").json()["record"]
    assert cur["record_counts"]["total_records"] == 0 and cur["available_sections"]["has_linked_records"] is False
    assert (
        _get(client, f"/literature/{only}", "exploratory", include="all").json()["record"]["record_counts"][
            "total_records"
        ]
        >= 3
    )
    shared = _get(client, f"/literature/{world.extra['literature'].public_ref}", "curated", include="all").json()[
        "record"
    ]
    assert shared["record_counts"]["total_records"] > 0


def test_a_solve_listed_under_literature_keeps_its_row_with_the_hidden_network_blanked(client, db_session, login_as):
    """The literature records list a visible solve of a hidden network: the row stays, its ``network_ref`` is null.

    Kills: dropping the row; leaving the hidden network named (a nullable sibling field, not a pointer).
    """
    world = build_sweep_world(db_session, withheld=True)
    login_as(world.extra["curator"].id)
    ref = world.extra["literature"].public_ref
    solve = world.visible["nsolve_v_under_hidden_net"].public_ref
    exp = {
        r["record_ref"]: r
        for r in _get(client, f"/literature/{ref}/records", "exploratory", limit=100).json()["records"]
    }
    cur = {
        r["record_ref"]: r for r in _get(client, f"/literature/{ref}/records", "curated", limit=100).json()["records"]
    }
    assert exp[solve]["network_ref"] == world.hidden["net_h"].public_ref
    assert solve in cur and cur[solve]["network_ref"] is None


# ---------------------------------------------------------------------------
# D5: analytics
# ---------------------------------------------------------------------------


def test_analytics_provenance_flags_and_filters_agree(client, db_session, login_as):
    """A kinetics or thermo row says it rests on statmech (or on a transition-state entry) only when the profile shows
    that record, and the matching filter returns exactly the rows whose flag says so.

    Kills: a flag resting on a hidden statmech or entry; a filter that matches a hidden one; a statmech row naming a hidden
    transition-state entry.
    """
    world = build_sweep_world(db_session, withheld=True)
    login_as(world.extra["curator"].id)

    def rows(profile, endpoint, ref_key, **params):
        out = _get(client, f"/analytics/{endpoint}", profile, limit=100, **params).json()["records"]
        return {r[ref_key]: r for r in out}

    kin_only_hidden = world.visible["kin_vh"].public_ref
    exp = rows("exploratory", "kinetics", "kinetics_ref")
    cur = rows("curated", "kinetics", "kinetics_ref")
    assert exp[kin_only_hidden]["has_statmech_provenance"] and exp[kin_only_hidden]["has_transition_state_provenance"]
    assert not cur[kin_only_hidden]["has_statmech_provenance"]
    assert not cur[kin_only_hidden]["has_transition_state_provenance"]
    mixed = cur[world.visible["kin_v"].public_ref]
    assert mixed["has_statmech_provenance"] is True and mixed["has_transition_state_provenance"] is False
    for flag, param in (
        ("has_statmech_provenance", "has_statmech_provenance"),
        ("has_transition_state_provenance", "has_transition_state_provenance"),
    ):
        for value in (True, False):
            served = set(rows("curated", "kinetics", "kinetics_ref", **{param: str(value).lower()}))
            assert served == {ref for ref, r in cur.items() if r[flag] is value}, (flag, value)
            assert served, f"no kinetics row under {flag}={value}: the check is vacuous"

    hidden_statmech = world.visible["thm_v2"].public_ref
    exp_t = rows("exploratory", "thermo", "thermo_ref")
    cur_t = rows("curated", "thermo", "thermo_ref")
    assert (
        exp_t[hidden_statmech]["has_statmech_provenance"] is True
        and cur_t[hidden_statmech]["has_statmech_provenance"] is False
    )
    for value in (True, False):
        served = set(rows("curated", "thermo", "thermo_ref", has_statmech_provenance=str(value).lower()))
        assert served == {ref for ref, r in cur_t.items() if r["has_statmech_provenance"] is value}, value

    hidden_entry = world.hidden["tse_h"].public_ref
    exp_s = rows("exploratory", "statmech", "statmech_ref")
    cur_s = rows("curated", "statmech", "statmech_ref")
    on_hidden = {ref for ref, r in exp_s.items() if r["transition_state_entry_ref"] == hidden_entry}
    shown_on_hidden = on_hidden & set(cur_s)
    assert shown_on_hidden, "the world has no shown statmech on the hidden entry: the check is vacuous"
    assert all(cur_s[ref]["transition_state_entry_ref"] is None for ref in shown_on_hidden)
    assert all(r["transition_state_entry_ref"] != hidden_entry for r in cur_s.values())


# ---------------------------------------------------------------------------
# Free text and the composite scheme
# ---------------------------------------------------------------------------


def test_free_text_that_names_a_hidden_record_is_blanked(client, db_session, login_as):
    """A solve's note and a network's description that cite a hidden record are blanked whole; their visible fields stay.

    Kills: leaving the text; blanking the neighbours with it.
    """
    world = build_sweep_world(db_session, withheld=True)
    login_as(world.extra["curator"].id)
    solve = world.visible["nsolve_v"].public_ref
    exp = _get(client, f"/network-solves/{solve}", "exploratory").json()["record"]["network_solve"]
    cur = _get(client, f"/network-solves/{solve}", "curated").json()["record"]["network_solve"]
    assert world.hidden["calc_h"].public_ref in exp["note"] and cur["note"] is None
    assert cur["me_method"] == exp["me_method"] and cur["tmax_k"] == exp["tmax_k"]
    net = world.visible["net_v"].public_ref
    exp_net = _get(client, f"/networks/{net}", "exploratory").json()["record"]["network"]
    cur_net = _get(client, f"/networks/{net}", "curated").json()["record"]["network"]
    assert world.hidden["nsolve_h"].public_ref in exp_net["description"] and cur_net["description"] is None
    assert cur_net["name"] == exp_net["name"]


def test_a_composite_scheme_is_served_whole_under_curated(client, db_session, login_as):
    """The scheme read names levels of theory, a literature record and the terms of a recipe: none is a gated record, so
    nothing is withheld and the answer equals the exploratory one.

    Kills: a guard that blanks vocabulary; a scheme route left out of the crawl.
    """
    world = build_sweep_world(db_session, withheld=True)
    login_as(world.extra["curator"].id)
    ref = world.extra["composite_scheme"].public_ref
    cur = _get(client, f"/composite-schemes/{ref}", "curated")
    exp = _get(client, f"/composite-schemes/{ref}", "exploratory")
    assert cur.status_code == 200 and cur.json()["record"] == exp.json()["record"]
    assert len(cur.json()["record"]["terms"]) == 2 and cur.json()["record"]["bound_levels_of_theory"]
