"""Every scientific route, under ``profile=curated``, must not name a record the profile hides.

The fix for that lives in :mod:`app.services.scientific_read.ref_guard`; this is the net under it. It calls every
``/api/v1/scientific/*`` route against a database in which every review-gated type has a hidden instance linked from a
visible parent (:mod:`_curated_sweep_world`), and looks for the hidden refs in the raw response bodies.

This is **report mode** (the guard is wired in but enables no route family yet), so the test does not demand zero
leaks. It demands that the leaks it finds are exactly the ones in ``curated_ref_sweep_baseline.json``: a new leak is a
regression, and a baseline entry that no longer occurs must be deleted, so the file only ever shrinks. Each family PR
enables its routes and removes its entries; the last PR deletes the file.

Why the test cannot pass by looking at nothing (each guard is mutation-tested; see the PR body)
-----------------------------------------------------------------------------------------------
* a **positive control**: under the exploratory profile every hidden record must be named where the inventory says it
  is, otherwise the world is not exercising that path and every curated green means nothing;
* the **world** must really be hidden/visible as labelled, judged by the guard's own predicate, with one hidden
  instance of every gated type;
* every route is **enumerated** from the app and cross-checked against OpenAPI; none is skipped silently;
* a **spy** proves each route reached the guard (or is listed as bypassing it, with the PR that fixes it);
* curated must be a **withholding of** exploratory, which is what catches a hide that quietly substitutes a visible
  sibling (the #767 bug class);
* a curated crawl that returned nothing would pass "no hidden ref anywhere", so every visible record must still show up.

Regenerate the baseline (after a deliberate change) with ``CURATED_SWEEP_REGENERATE=1``.
"""

from __future__ import annotations

import json
from collections import defaultdict

import pytest

from app.db.models.common import ReadProfile
from app.services.scientific_read import ref_guard
from app.services.scientific_read.profile import ResolvedReadProfile
from tests.api.scientific._curated_sweep_analysis import (
    REGENERATE,
    fmt,
    id_leaks,
    order_independent,
    parse_body,
    read_baseline,
    ref_leaks,
    withholding_violations,
    write_baseline,
)
from tests.api.scientific._curated_sweep_crawl import enumerate_routes, missing_providers
from tests.api.scientific._curated_sweep_inputs import CRAWL_UNSUPPORTED
from tests.api.scientific._curated_sweep_run import install_spy, run_pass
from tests.api.scientific._curated_sweep_world import GATED_KINDS, build_sweep_world

BASE = "/api/v1/scientific"

#: Routes in the sweep that do not return through ``apply_internal_ids_visibility``, so the boundary hook never sees
#: them today. Each names the PR that gives it a seam. A route that starts reaching the hook while listed here fails
#: the test (delete the entry); one that stops reaching it without being listed fails too.
KNOWN_UNGUARDED: dict[tuple[str, str], str] = {
    **{
        ("POST", f"{BASE}/{root}/select{suffix}"): "stamps the profile and returns; PR-3 adds the selection seam"
        for root in (
            "reaction-entries/{reaction_entry_ref}/kinetics",
            "species-entries/{species_entry_ref}/thermo",
            "species-entries/{species_entry_ref}/calculations",
            "species-entries/{species_entry_ref}/conformers",
            "transition-state-entries/{transition_state_entry_ref}/evidence",
        )
        for suffix in ("", "/manifest")
    },
    **{
        (
            "POST",
            f"{BASE}/species-entries/{{species_entry_ref}}/conformers/relative-energies{suffix}",
        ): "stamps the profile and returns; guard PR-3 adds run_relative_energies to the selection seam"
        for suffix in ("", "/manifest")
    },
    **{
        (
            "POST",
            f"{BASE}/species-entries/{{species_entry_ref}}/conformers/relative-energies/compare{suffix}",
        ): "stamps the profile and returns; guard PR-3 withholds at the engine seam (the comparison runs the same engine)"
        for suffix in ("", "/manifest")
    },
}

#: The relative-energy routes, and the only two coverage counts that differ between profiles there: a hidden conformer
#: group is not in the compared population under curated, so it is not counted. Scoped to these routes and these keys
#: so no other route's ``coverage`` is muted; the API test asserts the whole coverage block against the visible members.
RELATIVE_ENERGY_ROUTES = {
    ("POST", f"{BASE}/species-entries/{{species_entry_ref}}/conformers/relative-energies{suffix}")
    for suffix in ("", "/manifest")
}
RELATIVE_ENERGY_POPULATION_PATHS = frozenset(
    f"{prefix}coverage.{name}" for prefix in ("", "result.") for name in ("absent", "requested_members")
)
#: The comparison runs the same engine twice, so the same two counts appear under each protocol's block (and, in its
#: manifest, under each run's recorded answer). The same two keys, scoped to the two ``/compare`` routes only.
RELATIVE_ENERGY_COMPARE_ROUTES = {
    ("POST", f"{BASE}/species-entries/{{species_entry_ref}}/conformers/relative-energies/compare{suffix}")
    for suffix in ("", "/manifest")
}
RELATIVE_ENERGY_COMPARE_POPULATION_PATHS = frozenset(
    f"{prefix}coverage.{name}"
    for prefix in (
        "candidate.",
        "reference.",
        "result.candidate.",
        "result.reference.",
        "runs.candidate.result.",
        "runs.reference.result.",
    )
    for name in ("absent", "requested_members")
)

#: Routes that answer something other than 200 to a visible subject under the curated profile, and why. Empty since
#: PR-2c: the network batch evaluate used to evaluate every fit of the network, including those of a hidden solve, and
#: then 404 naming the hidden fit (design 2.8, an existence oracle); it now evaluates only the fits of the solves the
#: profile shows.
EXPECTED_CURATED_STATUS: dict[tuple[str, str], tuple[int, str]] = {}

_LIT = f"{BASE}/literature/{{literature_ref_or_id}}/records"
_FULL = f"{BASE}/reaction-entries/{{reaction_entry_id}}/full"
_SPE = f"{BASE}/species-entries/{{species_entry_id}}"
_CALC = f"{BASE}/calculations/{{calculation_ref_or_id}}"
_TSE = f"{BASE}/transition-state-entries/{{transition_state_entry_ref_or_id}}"
_TS = f"{BASE}/transition-states/{{transition_state_ref_or_id}}"
_NET = f"{BASE}/networks/{{network_ref_or_id}}"
_KIN = f"{BASE}/reaction-entries/{{reaction_entry_id}}/kinetics"
_KSEARCH = f"{BASE}/kinetics/search"
_TSBROWSE = f"{BASE}/transition-states/browse"
_X_ND = f"{BASE}/export/ndjson"
_X_CK = f"{BASE}/export/chemkin"
_X_MLS = f"{BASE}/export/ml/species.ndjson"
_X_MLR = f"{BASE}/export/ml/reactions.ndjson"

#: Hidden record -> route templates where the exploratory profile names it today (the inventory's paths). The positive
#: control. Every pair must be seen by the exploratory crawl.
EXPECTED_EXPLORATORY_SIGHTINGS: dict[str, set[str]] = {
    "calc_h": {
        _CALC,
        f"{BASE}/statmech/{{statmech_ref_or_id}}",
        f"{_SPE}/thermo",
        f"{_SPE}/transport",
        f"{_SPE}/statmech",
        f"{BASE}/geometries/{{geometry_handle}}",
        _NET,
        f"{BASE}/level-of-theories/{{level_of_theory_ref_or_id}}",
        f"{BASE}/transport/{{transport_ref_or_id}}",
    },
    # A hidden calculation with an output geometry on a visible observation (PR-2a review).
    "calc_hgeo": {
        f"{BASE}/conformer-groups/{{conformer_group_ref_or_id}}",
        f"{BASE}/conformer-observations/{{conformer_observation_ref_or_id}}",
        f"{BASE}/level-of-theories/{{level_of_theory_ref_or_id}}",
    },
    "calc_h_ts": {f"{BASE}/statmech/{{statmech_ref_or_id}}", _TSE, _TS, _FULL, _KIN, _KSEARCH},
    # PR-2b: hidden calculations of every kind a visible TS entry holds, named by the entry's reads and by the visible
    # kinetics that cite them.
    "calc_h_tssp": {_TSE, _TS, _TSBROWSE, _FULL, _KIN, _KSEARCH},
    "calc_h_irc": {_TSE, _TS, _TSBROWSE, _FULL, _KIN, _KSEARCH},
    "calc_h_ps": {_TSE, _TS, _TSBROWSE, _FULL, _KIN, _KSEARCH},
    "calc_h_tsopt": {_TSE, _TS, _TSBROWSE, _FULL, _KIN, _KSEARCH},
    "calc_h_scan": {_TSE, _TS, _TSBROWSE, _FULL},
    "cg_h_rxn": {_FULL},
    # D5 world: children of a visible entry and a reaction entry with only hidden records.
    "tse_hw2": {_TS, _TSBROWSE},
    "calc_h_w1": {_TSE, _TS, _TSBROWSE},
    "calc_h_w2": {_TSE, _TS, _TSBROWSE},
    "ts_hw": {_TSBROWSE, _X_MLR},
    "tse_hw": {_TSBROWSE, _X_MLR},
    "calc_h_psw": {_TSBROWSE},
    "kin_hw": {_KIN, _X_ND, _X_MLR},
    "calc_h_psp": {_TSE, _TS, _TSBROWSE, _FULL},
    # PR-4: hidden records in every position the four export streams can name one.
    "calc_h_xlin": {_X_ND, _X_CK, _X_MLS, f"{BASE}/transport/search"},
    "calc_h_xsp": {_X_MLS},
    "sm_h_y": {_X_ND, _X_CK},
    "calc_h_zh": {f"{BASE}/transport/search"},
    "calc_h_xts": {_X_MLR},
    "ts_h_x": {_X_MLR},
    "cg_h": {
        _X_MLS,
        f"{BASE}/species-entries/{{species_entry_ref}}/conformers/relative-energies",
        f"{BASE}/species-entries/{{species_entry_ref}}/conformers/relative-energies/manifest",
        f"{BASE}/species-entries/{{species_entry_ref}}/conformers/relative-energies/compare",
        f"{BASE}/species-entries/{{species_entry_ref}}/conformers/relative-energies/compare/manifest",
        _CALC,
        f"{BASE}/conformer-observations/{{conformer_observation_ref_or_id}}",
        f"{BASE}/statmech/{{statmech_ref_or_id}}",
        f"{_SPE}/statmech",
    },
    "co_h": {_CALC, f"{BASE}/conformer-groups/{{conformer_group_ref_or_id}}", f"{_SPE}/thermo"},
    "ts_h": {_TSE, _FULL},
    "tse_h": {_CALC, _TS, _FULL, _KIN, _KSEARCH},
    "sm_h": {f"{_SPE}/thermo", f"{_SPE}/statmech"},
    "sm_ts_h": {_TSE, _TS},
    "trn_h": {f"{_SPE}/transport"},
    "net_h": {
        f"{BASE}/network-kinetics/{{network_kinetics_ref_or_id}}",
        f"{BASE}/network-solves/{{network_solve_ref_or_id}}",
        _LIT,
        _FULL,
    },
    "nsolve_h": {_NET, _LIT},
    "nkin_h": {f"{_NET}/kinetics/evaluate", f"{BASE}/network-kinetics/search"},
    "nsolve_h3": {f"{BASE}/networks/browse", f"{BASE}/network-solves/search", _LIT},
    "nkin_h3": {f"{BASE}/network-kinetics/search"},
    "kin_h_new": {f"{BASE}/analytics/kinetics", f"{BASE}/kinetics/search"},
    "calc_h_p2": {f"{BASE}/network-solves/{{network_solve_ref_or_id}}"},
    "sm_h2": {f"{BASE}/frequency-scale-factors/{{frequency_scale_factor_ref_or_id}}"},
    # PR-2c: a hidden solve's PLOG fit (the only one of its kind), and the hidden calculations of a level of theory,
    # a composite-scheme binding and a literature record that nothing visible uses.
    "nkin_h_plog": {f"{_NET}/kinetics/evaluate", f"{BASE}/network-kinetics/search"},
    "calc_h_lot": {
        f"{BASE}/level-of-theories/{{level_of_theory_ref_or_id}}",
        f"{BASE}/energy-correction-schemes/{{energy_correction_scheme_ref_or_id}}",
        _LIT,
    },
    "calc_h_lot_ts": {f"{BASE}/level-of-theories/{{level_of_theory_ref_or_id}}", _TSE},
    "calc_h_comp": {f"{BASE}/level-of-theories/browse", f"{BASE}/analytics/calculations"},
    "thm_h": {f"{_SPE}/thermo", _LIT, _X_ND},
    "thm_h_g": {_X_ND},
    "kin_h": {f"{BASE}/reaction-entries/{{reaction_entry_id}}/kinetics", _FULL, _LIT, _X_MLR, _X_ND},
    "mpo_h": {f"{_SPE}/observations"},
}


def _hidden_world_refs(world):
    return world.hidden_refs()


def _by_route(results):
    out = defaultdict(list)
    for (route, label), result in results.items():
        out[route].append((label, result))
    return out


# ---------------------------------------------------------------------------
# Route enumeration needs no database
# ---------------------------------------------------------------------------


def test_every_scientific_route_is_enumerated_and_classified():
    """No route is skipped silently.

    Kills: filtering a route out of the enumeration (m5); a route in OpenAPI but not crawled or vice versa; a path
    parameter with no provider (the way a new route is forced into the sweep); a stale ``CRAWL_UNSUPPORTED`` or
    exemption entry that matches nothing.
    """
    from app.api.app import create_app

    app = create_app()
    specs = enumerate_routes(app)
    crawled_ops = {spec.key for spec in specs}
    openapi_ops = {
        (method.upper(), path)
        for path, ops in app.openapi()["paths"].items()
        if path.startswith("/api/v1/scientific")
        for method in ops
        if method.lower() in {"get", "post", "put", "patch", "delete"}
    }
    assert crawled_ops == openapi_ops
    assert len(specs) > 100  # the enumeration is not empty or truncated
    assert not missing_providers(specs), "add a path-parameter provider in _curated_sweep_inputs.py"
    assert set(CRAWL_UNSUPPORTED) <= crawled_ops, "CRAWL_UNSUPPORTED names a route that does not exist"
    for prefix in ref_guard.EXEMPT_ROUTE_PREFIXES:
        assert any(ref_guard.route_matches(spec.path, [prefix]) for spec in specs), f"exempt prefix {prefix} matches no route"
    exempt = {spec.key for spec in specs if spec.exempt}
    assert not exempt & set(CRAWL_UNSUPPORTED)
    # Every route is crawled, exempt, or unsupported with a reason; nothing else.
    crawled = crawled_ops - exempt - set(CRAWL_UNSUPPORTED)
    assert crawled and all(reason for reason in CRAWL_UNSUPPORTED.values())


# ---------------------------------------------------------------------------
# The sweep
# ---------------------------------------------------------------------------


def test_the_world_has_a_hidden_instance_of_every_gated_type(client, db_session):
    """The fixture is what it claims to be, judged by the guard's own resolver.

    Kills: removing a hidden record from the world (m4); a world whose "hidden" records are in fact visible; a
    "visible" record the profile hides (which would make every curated assertion vacuous in the other direction).
    """
    world = build_sweep_world(db_session)
    curated = ResolvedReadProfile(profile=ReadProfile.curated, recommendation=_approved_floor_only())
    assert {world.kind_of(k) for k in world.hidden} == set(ref_guard.GATED_TYPES)
    assert set(GATED_KINDS) == set(ref_guard.GATED_TYPES)
    hidden = ref_guard.hidden_refs(db_session, [r.public_ref for r in world.hidden.values()], profile=curated)
    assert hidden == {r.public_ref for r in world.hidden.values()}
    visible = ref_guard.hidden_refs(db_session, [r.public_ref for r in world.visible.values()], profile=curated)
    assert visible == set()


def _approved_floor_only():
    from app.db.models.common import ProfileRecommendation

    return ProfileRecommendation.approved_floor_only


def test_curated_sweep_report_and_ratchet(client, db_session, login_as, monkeypatch):
    """The sweep proper: positive control, coverage, leak ratchet, withholding-only, guard-reached.

    Kills (see each block): an empty world or crawl, a skipped route, a substitution, a route that bypasses the guard,
    a new leak, a fixed leak left in the baseline.
    """
    from app.api.config import settings

    world = build_sweep_world(db_session)
    login_as(world.extra["curator"].id)
    specs = enumerate_routes(client.app)
    spy = install_spy(monkeypatch)
    hidden = _hidden_world_refs(world)

    exploratory = run_pass(client, db_session, specs, world, spy, profile="exploratory")
    curated = run_pass(client, db_session, specs, world, spy, profile="curated")
    monkeypatch.setattr(settings, "allow_public_internal_ids", True)
    curated_ids = run_pass(client, db_session, specs, world, spy, profile="curated", with_ids=True)
    exploratory_ids = run_pass(client, db_session, specs, world, spy, profile="exploratory", with_ids=True)
    monkeypatch.setattr(settings, "allow_public_internal_ids", False)

    failures: list[str] = []

    # -- every crawled request answered 200: a crawl full of 422/404 verifies nothing ------------------------------
    for name, results in (("exploratory", exploratory), ("curated", curated), ("curated+ids", curated_ids)):
        assert results, f"{name} pass crawled nothing"
        for (route, label), r in results.items():
            expected = 200
            if name != "exploratory" and route in EXPECTED_CURATED_STATUS:
                expected = EXPECTED_CURATED_STATUS[route][0]
            if r.status != expected:
                failures.append(
                    f"[{name}] {fmt(route, label)} answered {r.status}, expected {expected}; register inputs"
                )

    # -- 1. positive control: exploratory names every hidden record where the inventory says -----------------------
    sightings: dict[str, set[str]] = defaultdict(set)
    for (route, _label), r in {**exploratory, **exploratory_ids}.items():
        for key in ref_leaks(r.text, hidden, ""):
            sightings[key[0]].add(route[1])
    if REGENERATE:
        print("SIGHTINGS", json.dumps({k: sorted(v) for k, v in sorted(sightings.items())}, indent=1))
    if set(EXPECTED_EXPLORATORY_SIGHTINGS) != set(hidden):
        failures.append("the positive control must list every hidden record")
    for key, routes in EXPECTED_EXPLORATORY_SIGHTINGS.items():
        for route_path in sorted(routes - sightings[key]):
            failures.append(f"positive control: {key} is not named by exploratory {route_path}")

    # -- the curated crawl is not empty: every visible record still shows up somewhere --------------------------------
    curated_text = "\n".join(r.text for r in {**curated, **curated_ids}.values())
    for key, row in world.visible.items():
        if row.public_ref not in curated_text:
            failures.append(f"curated crawl never names visible record {key}: the pass may be vacuous")

    # -- 6. guard reached: every crawled route passes the boundary, or is listed as bypassing it ---------------------
    reached_by_route = {route: any(r.reached for _, r in rs) for route, rs in _by_route(curated).items()}
    unreached = {route for route, reached in reached_by_route.items() if not reached}
    for route in sorted(unreached - set(KNOWN_UNGUARDED)):
        failures.append(fmt(route, "never reached the ref-guard boundary and is not in KNOWN_UNGUARDED"))
    for route in sorted(set(KNOWN_UNGUARDED) - unreached):
        failures.append(fmt(route, "now reaches the boundary: remove it from KNOWN_UNGUARDED"))
    for route, reason in KNOWN_UNGUARDED.items():
        assert reason, route

    # -- 4. no hidden ref anywhere (ratchet) ---------------------------------------------------------------------
    ref_found: set[str] = set()
    for (route, label), r in curated.items():
        spec_request = _request_text(specs, route, label, world, ids=False)
        for key, path in ref_leaks(r.text, hidden, spec_request):
            ref_found.add(fmt(route, f"{key} @ {path}"))
    id_found: set[str] = set()
    for (route, _label), r in curated_ids.items():
        for key, path in id_leaks(r.text, world):
            id_found.add(fmt(route, f"{key} @ {path}"))

    # -- 5. curated is a withholding of exploratory (no substitution) -----------------------------------------------
    substituted: set[str] = set()
    for (route, label), c in curated.items():
        if route in EXPECTED_CURATED_STATUS:
            continue  # the curated answer is a different status by design; its leak is in the ref ratchet
        e = exploratory[(route, label)]
        ekind, eobj = parse_body(e.text)
        ckind, cobj = parse_body(c.text)
        if ekind != "json" or ckind != "json":
            continue
        for path in withholding_violations(order_independent(route[1], eobj), order_independent(route[1], cobj)):
            if route in RELATIVE_ENERGY_ROUTES and path.split("<", 1)[0] in RELATIVE_ENERGY_POPULATION_PATHS:
                continue
            if (
                route in RELATIVE_ENERGY_COMPARE_ROUTES
                and path.split("<", 1)[0] in RELATIVE_ENERGY_COMPARE_POPULATION_PATHS
            ):
                continue
            substituted.add(fmt(route, path))

    found = {
        "hidden_ref_leaks": sorted(ref_found),
        "hidden_id_leaks": sorted(id_found),
        "not_a_withholding": sorted(substituted),
    }
    if REGENERATE:
        print("FAILURES-BEFORE-BASELINE", "\n".join(failures))
        write_baseline(found)
        pytest.skip("baseline regenerated")

    baseline = read_baseline()
    for section, now in found.items():
        was = set(baseline.get(section, []))
        for entry in sorted(set(now) - was):
            failures.append(f"NEW {section}: {entry}")
        for entry in sorted(was - set(now)):
            failures.append(f"FIXED {section} (remove it from the baseline): {entry}")

    assert not failures, "\n".join(failures)


def _request_text(specs, route, label, world, *, ids):
    from tests.api.scientific._curated_sweep_crawl import variants_for

    spec = next(s for s in specs if s.key == route)
    for variant in variants_for(spec, world, with_internal_ids=ids):
        if variant.label == label:
            return variant.request_text
    return ""
