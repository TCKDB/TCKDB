"""Every scientific route, under ``profile=curated``, must not name a record the profile hides.

The fix for that lives in :mod:`app.services.scientific_read.ref_guard`; this is the net under it. It calls every
``/api/v1/scientific/*`` route against a database in which every review-gated type has a hidden instance linked from a
visible parent (:mod:`_curated_sweep_world`), and looks for the hidden refs in the raw response bodies.

The sweep is **enforce-only**: it demands zero hidden refs, zero hidden ids and zero substitutions on every route.
There is no baseline and no report mode (both were deleted by guard PR-5, together with the rollout allowlist).

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
"""

from __future__ import annotations

from collections import defaultdict

from app.db.models.common import ReadProfile, RecordReviewStatus
from app.services.scientific_read import ref_guard
from app.services.scientific_read.profile import ResolvedReadProfile
from tests.api.scientific._curated_sweep_analysis import (
    RELAXED_UNLISTED,
    fmt,
    id_leaks,
    order_independent,
    parse_body,
    ref_leaks,
    refs_outside_floored_rows,
    route_subject_identities,
    subject_leaks,
    subject_rows,
    withholding_violations,
)
from tests.api.scientific._curated_sweep_crawl import enumerate_routes, missing_providers
from tests.api.scientific._curated_sweep_inputs import CRAWL_UNSUPPORTED
from tests.api.scientific._curated_sweep_run import install_spy, run_pass
from tests.api.scientific._curated_sweep_world import GATED_KINDS, build_sweep_world

BASE = "/api/v1/scientific"

#: Routes in the sweep that reach neither the response boundary nor a document seam, each with the PR that gives it one.
#: Empty since guard PR-3 (the selection documents) and PR-4 (the export streams). A route that starts reaching the guard
#: while listed here fails the test (delete the entry); one that stops reaching it without being listed fails too.
KNOWN_UNGUARDED: dict[tuple[str, str], str] = {}

#: Routes whose answer is a DECISION over a population: a selection, its manifest, and what is exported from one. The
#: population is the profile's, and evidence that points at a record the reader cannot open reads as unavailable (hidden
#: == unknown), so under curated the decision itself (outcome, selected node, ordinals, checksums, advisories keyed by
#: candidate) legitimately differs from the exploratory one, and "curated is a withholding of exploratory" is not the
#: property. The property for these is hidden == absent: the curated selection over a world equals the exploratory
#: selection over the same world with the hidden records never created, which
#: ``tests/services/scientific_read/test_selection_hidden_equals_absent.py`` proves per engine. What stays checked here,
#: for these routes too: no hidden ref anywhere, and the seam was crossed.
SELECTION_DOCUMENT_ROUTES = frozenset(
    {
        ("POST", f"{BASE}/{root}/select{suffix}")
        for root in (
            "reaction-entries/{reaction_entry_ref}/kinetics",
            "species-entries/{species_entry_ref}/thermo",
            "species-entries/{species_entry_ref}/calculations",
            "species-entries/{species_entry_ref}/conformers",
            "transition-state-entries/{transition_state_entry_ref}/evidence",
            "networks/{network_ref}/kinetics",
            "statmech",
        )
        for suffix in ("", "/manifest")
    }
    | {("POST", f"{BASE}/networks/{{network_ref}}/kinetics/export-selected")}
)

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
    # A hidden freq calculation that is the most recent freq of a visible TS entry, beside a visible freq of the same
    # entry: the reaction whose single-slot picks a fall-through would move to the sibling.
    "calc_h_tsfq": {_FULL, _KIN},
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
    # The selection world (guard PR-3): the hidden solve a visible solve cites as its model-fidelity reference, and the
    # hidden successor a visible statmech's supersession notice names.
    "nsolve_hsel": {
        f"{BASE}/network-kinetics/search",
        f"{BASE}/network-solves/search",
        f"{BASE}/networks/browse",
        f"{BASE}/networks/search",
        f"{BASE}/networks/{{network_ref}}/kinetics/select",
        f"{BASE}/networks/{{network_ref}}/kinetics/select/manifest",
    },
    "sm_hsucc": {
        f"{BASE}/analytics/statmech",
        f"{_SPE}/statmech",
        f"{BASE}/statmech/search",
        f"{BASE}/statmech/select",
        f"{BASE}/statmech/select/manifest",
    },
}


#: Why a route still leaves out an unlisted record that exploratory names (ADR 0023): its service applies the earlier rule.
SERVICE_FLOOR = (
    "a search or detail service applies the record floor to a RECORD it lists or counts (a network solve, a kinetics record, "
    "a transition state or entry: ADR 0023 Q3/Q5), or to a matching filter (step 4)"
)
DERIVED = (
    "a reference row's usage list, usage count and usage-eligibility filter preview the RECORD list of what cites it (a "
    "calculation, a statmech) and keep the record floor; a correction scheme's or scale factor's calculation kind is lost "
    "only when it rides with an application to a transition-state entry below the floor"
)
EXPORTS_AND_LOADERS = "export populations and the relative-energy loader floor their evidence (step 5)"

#: Per route, the KINDS of unlisted record (the prefix of the world's key: ``calc``, ``tse``, ...) that exploratory names as
#: evidence and curated still leaves out, with the reason. Every other route, and every other kind on a listed route, must
#: show everything exploratory shows, so a guard that blanks evidence again fails the sweep wherever it was already moved.
#: What a returned ROW that is itself below the floor holds is not evidence (the floor drops the whole row) and is excluded
#: by the row's actual status. A kind that is now shown is stale and fails: the list can only shrink.
STILL_ON_THE_EARLIER_RULE: dict[tuple[str, str], tuple[set[str], str]] = {
    ("GET", "/api/v1/scientific/energy-correction-schemes/{energy_correction_scheme_ref_or_id}"): ({'calc', 'tse'}, DERIVED),
    ("GET", "/api/v1/scientific/energy-correction-schemes/search"): ({'calc', 'tse'}, DERIVED),
    ("GET", "/api/v1/scientific/export/ml/reactions.ndjson"): ({'calc', 'kin', 'ts', 'tse'}, EXPORTS_AND_LOADERS),
    ("GET", "/api/v1/scientific/export/ml/species.ndjson"): ({'calc', 'thm'}, EXPORTS_AND_LOADERS),
    ("GET", "/api/v1/scientific/export/ndjson"): ({'kin', 'thm', 'trn'}, EXPORTS_AND_LOADERS),
    ("GET", "/api/v1/scientific/frequency-scale-factors/{frequency_scale_factor_ref_or_id}"): ({'sm', 'tse'}, DERIVED),
    ("GET", "/api/v1/scientific/frequency-scale-factors/search"): ({'sm', 'tse'}, DERIVED),
    ("GET", "/api/v1/scientific/level-of-theories/browse"): ({'calc'}, DERIVED),
    ("GET", "/api/v1/scientific/level-of-theories/{level_of_theory_ref_or_id}"): ({'calc'}, DERIVED),
    ("GET", "/api/v1/scientific/level-of-theories/search"): ({'calc'}, DERIVED),
    ("GET", "/api/v1/scientific/networks/browse"): ({'nsolve'}, SERVICE_FLOOR),
    ("GET", "/api/v1/scientific/network-solves/{network_solve_ref_or_id}"): ({'tse'}, SERVICE_FLOOR),
    ("GET", "/api/v1/scientific/network-solves/search"): ({'tse'}, SERVICE_FLOOR),
    ("GET", "/api/v1/scientific/networks/search"): ({'calc', 'nsolve', 'tse'}, SERVICE_FLOOR),
    ("GET", "/api/v1/scientific/reaction-entries/{reaction_entry_id}/full"): ({'calc', 'cg', 'kin', 'ts', 'tse'}, SERVICE_FLOOR),
    ("POST", "/api/v1/scientific/energy-correction-schemes/search"): ({'calc', 'tse'}, DERIVED),
    ("POST", "/api/v1/scientific/frequency-scale-factors/search"): ({'sm', 'tse'}, DERIVED),
    ("POST", "/api/v1/scientific/level-of-theories/search"): ({'calc'}, DERIVED),
    ("POST", "/api/v1/scientific/networks/{network_ref_or_id}/kinetics/evaluate"): ({'nkin', 'nsolve'}, SERVICE_FLOOR),
    ("POST", "/api/v1/scientific/network-solves/search"): ({'tse'}, SERVICE_FLOOR),
    ("POST", "/api/v1/scientific/networks/search"): ({'calc', 'nsolve', 'tse'}, SERVICE_FLOOR),
    ("POST", "/api/v1/scientific/species-entries/{species_entry_ref}/conformers/relative-energies"): ({'cg'}, EXPORTS_AND_LOADERS),
    ("POST", "/api/v1/scientific/species-entries/{species_entry_ref}/conformers/relative-energies/compare"): ({'cg'}, EXPORTS_AND_LOADERS),
    ("POST", "/api/v1/scientific/species-entries/{species_entry_ref}/conformers/relative-energies/compare/manifest"): ({'calc', 'cg'}, EXPORTS_AND_LOADERS),
    ("POST", "/api/v1/scientific/species-entries/{species_entry_ref}/conformers/relative-energies/manifest"): ({'calc', 'cg'}, EXPORTS_AND_LOADERS),
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
    hidden_refs = {r.public_ref for r in world.hidden.values()}
    # Unlisted: every hidden record is below the record floor and none is withheld as evidence.
    assert ref_guard.below_floor_refs(db_session, hidden_refs, profile=curated) == hidden_refs
    assert ref_guard.withheld_evidence_refs(db_session, hidden_refs, profile=curated) == set()
    visible_refs = [r.public_ref for r in world.visible.values()]
    assert ref_guard.below_floor_refs(db_session, visible_refs, profile=curated) == set()
    assert ref_guard.withheld_evidence_refs(db_session, visible_refs, profile=curated) == set()

    # Withheld twins: the same records turned rejected / deprecated are withheld as evidence, and both statuses are used.
    statuses = world.withhold_hidden(db_session)
    assert set(statuses.values()) == {RecordReviewStatus.rejected, RecordReviewStatus.deprecated}
    assert ref_guard.withheld_evidence_refs(db_session, hidden_refs, profile=curated) == hidden_refs
    assert ref_guard.below_floor_refs(db_session, hidden_refs, profile=curated) == hidden_refs
    assert ref_guard.withheld_evidence_refs(db_session, visible_refs, profile=curated) == set()


def _approved_floor_only():
    from app.db.models.common import ProfileRecommendation

    return ProfileRecommendation.approved_floor_only


def test_curated_sweep_enforces_the_guard(client, db_session, login_as, monkeypatch):
    """The sweep proper, in two states of the same world (ADR 0023).

    **Unlisted** (the world as built: not_reviewed / under_review): a record below the approved floor may be named NESTED
    with its badge, but is never the answer: not ``record``, not a row of ``records``, not a candidate. Curated must still
    be a withholding of exploratory (no substitution).

    **Withheld** (the same records turned rejected / deprecated): linked evidence at these statuses is withheld, so under
    curated they appear NOWHERE, by ref or by id, and curated is still a withholding of the exploratory crawl taken
    before the change (exploratory withholds nothing as evidence, so that crawl is the superset).

    Kills (see each block): an empty world or crawl, a skipped route, a substitution, a route that bypasses the guard,
    an unlisted record served as an answer, a withheld record named anywhere.
    """
    from app.api.config import settings

    world = build_sweep_world(db_session)
    login_as(world.extra["curator"].id)
    specs = enumerate_routes(client.app)
    spy = install_spy(monkeypatch)
    hidden = _hidden_world_refs(world)
    identities = route_subject_identities(client.app)

    exploratory = run_pass(client, db_session, specs, world, spy, profile="exploratory")
    curated = run_pass(client, db_session, specs, world, spy, profile="curated")
    monkeypatch.setattr(settings, "allow_public_internal_ids", True)
    exploratory_ids = run_pass(client, db_session, specs, world, spy, profile="exploratory", with_ids=True)
    monkeypatch.setattr(settings, "allow_public_internal_ids", False)

    # Which returned rows are below the floor, by their status now (before the world is flipped to withheld).
    floored_rows: dict[tuple[tuple[str, str], str], set[str]] = {}
    for (route, label), e in exploratory.items():
        refs = {ref for ref, _ in subject_rows(e.text, identities.get(route)) if ref}
        statuses = ref_guard._review_statuses(db_session, refs) if refs else {}
        floored_rows[(route, label)] = {ref for ref, status in statuses.items() if status.value != "approved"}

    # The same world, its hidden records now rejected / deprecated.
    world.withhold_hidden(db_session)
    withheld = run_pass(client, db_session, specs, world, spy, profile="curated")
    monkeypatch.setattr(settings, "allow_public_internal_ids", True)
    withheld_ids = run_pass(client, db_session, specs, world, spy, profile="curated", with_ids=True)
    monkeypatch.setattr(settings, "allow_public_internal_ids", False)

    failures: list[str] = []

    # -- every crawled request answered 200: a crawl full of 422/404 verifies nothing ------------------------------
    for name, results in (
        ("exploratory", exploratory),
        ("curated (unlisted)", curated),
        ("curated (withheld)", withheld),
        ("curated+ids (withheld)", withheld_ids),
    ):
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
    if set(EXPECTED_EXPLORATORY_SIGHTINGS) != set(hidden):
        failures.append("the positive control must list every hidden record")
    for key, routes in EXPECTED_EXPLORATORY_SIGHTINGS.items():
        for route_path in sorted(routes - sightings[key]):
            failures.append(f"positive control: {key} is not named by exploratory {route_path}")

    # -- the curated crawl is not empty: every visible record still shows up somewhere --------------------------------
    for name, results in (("unlisted", curated), ("withheld", {**withheld, **withheld_ids})):
        curated_text = "\n".join(r.text for r in results.values())
        for key, row in world.visible.items():
            if row.public_ref not in curated_text:
                failures.append(f"curated crawl ({name}) never names visible record {key}: the pass may be vacuous")

    # -- unlisted records are linked evidence: PER ROUTE, curated names every nested hidden ref that exploratory names -------
    # Two things exploratory names are the floor's, not evidence: a record it returns as the answer, and anything nested
    # inside a returned ROW that is itself below the floor (the whole row is dropped by curated). Both are judged by the
    # row's ACTUAL review status in the database, not by membership of the tracked hidden records. What a route still leaves
    # out is on the earlier rule until its step lands and is listed in STILL_ON_THE_EARLIER_RULE (by route and ref kind).
    missing: dict[tuple[str, str], set[str]] = defaultdict(set)
    shown_routes = 0
    for (route, label), e in exploratory.items():
        c = curated[(route, label)]
        if route in SELECTION_DOCUMENT_ROUTES or route in EXPECTED_CURATED_STATUS:
            continue
        identity = identities.get(route)
        as_answer = {k for k, _ in subject_leaks(e.text, hidden, identity)}
        floored = floored_rows[(route, label)]
        if parse_body(e.text)[0] == "json":
            outside = refs_outside_floored_rows(e.text, identity, floored)
            named = [k for k, ref in hidden.items() if k not in as_answer and ref in outside]
        else:  # an NDJSON or zip body has no subject rows to exclude: judged by text
            named = [k for k, ref in hidden.items() if ref in e.text]
        if not named:
            continue
        gone = {k for k in named if hidden[k] not in c.text}
        if gone:
            missing[route] |= {k.split("_", 1)[0] for k in gone}
        else:
            shown_routes += 1
    for route, kinds in sorted(missing.items()):
        allowed = STILL_ON_THE_EARLIER_RULE.get(route, (None, ""))[0]
        if allowed is None:
            failures.append(
                fmt(route, f"exploratory names unlisted {sorted(kinds)} records that curated leaves out (evidence must be shown)")
            )
        elif kinds - set(allowed):
            failures.append(fmt(route, f"{sorted(kinds - set(allowed))} are newly left out beside the listed {sorted(allowed)}"))
    for route, (allowed, reason) in sorted(STILL_ON_THE_EARLIER_RULE.items()):
        assert reason and allowed, route
        stale = set(allowed) - missing.get(route, set())
        if stale:
            failures.append(fmt(route, f"now shows {sorted(stale)}: remove them from STILL_ON_THE_EARLIER_RULE"))
    if shown_routes < 15:
        failures.append(f"only {shown_routes} request variants show unlisted evidence in full: the evidence rule is not exercised")

    # -- 6. guard reached: every crawled route passes the boundary, or is listed as bypassing it ---------------------
    reached_by_route = {route: any(r.reached for _, r in rs) for route, rs in _by_route(curated).items()}
    unreached = {route for route, reached in reached_by_route.items() if not reached}
    for route in sorted(unreached - set(KNOWN_UNGUARDED)):
        failures.append(fmt(route, "never reached the ref-guard boundary and is not in KNOWN_UNGUARDED"))
    for route in sorted(set(KNOWN_UNGUARDED) - unreached):
        failures.append(fmt(route, "now reaches the boundary: remove it from KNOWN_UNGUARDED"))
    for route, reason in KNOWN_UNGUARDED.items():
        assert reason, route

    # -- 4a. unlisted: never the answer. Withheld: named nowhere, by ref or by id -------------------------------------
    subject_found: set[str] = set()
    for (route, _label), r in curated.items():
        for key, path in subject_leaks(r.text, hidden, identities.get(route)):
            subject_found.add(fmt(route, f"{key} @ {path}"))
    ref_found: set[str] = set()
    for (route, label), r in withheld.items():
        spec_request = _request_text(specs, route, label, world, ids=False)
        for key, path in ref_leaks(r.text, hidden, spec_request):
            ref_found.add(fmt(route, f"{key} @ {path}"))
    id_found: set[str] = set()
    for (route, _label), r in withheld_ids.items():
        for key, path in id_leaks(r.text, world):
            id_found.add(fmt(route, f"{key} @ {path}"))

    # -- 5. curated is a withholding of exploratory (no substitution), in both states ---------------------------------
    substituted: set[str] = set()
    for state, crawl in (("unlisted", curated), ("withheld", withheld)):
        for (route, label), c in crawl.items():
            if route in EXPECTED_CURATED_STATUS:
                continue  # the curated answer is a different status by design; its leak is in the ref check
            if route in SELECTION_DOCUMENT_ROUTES:
                continue  # see SELECTION_DOCUMENT_ROUTES: the substitution check for these is made at the engine
            e = exploratory[(route, label)]
            ekind, eobj = parse_body(e.text)
            ckind, cobj = parse_body(c.text)
            if ekind != "json" or ckind != "json":
                continue
            mode = "equal" if state == "unlisted" else "le"
            for path in withholding_violations(
                order_independent(route[1], eobj),
                order_independent(route[1], cobj),
                mode=mode,
                relaxed=frozenset(RELAXED_UNLISTED.get(route[1], {})) if state == "unlisted" else frozenset(),
            ):
                if route in RELATIVE_ENERGY_ROUTES and path.split("<", 1)[0] in RELATIVE_ENERGY_POPULATION_PATHS:
                    continue
                if (
                    route in RELATIVE_ENERGY_COMPARE_ROUTES
                    and path.split("<", 1)[0] in RELATIVE_ENERGY_COMPARE_POPULATION_PATHS
                ):
                    continue
                substituted.add(f"[{state}] " + fmt(route, path))

    for entry in sorted(subject_found):
        failures.append(f"unlisted record served as the answer: {entry}")
    for entry in sorted(ref_found):
        failures.append(f"withheld ref leak: {entry}")
    for entry in sorted(id_found):
        failures.append(f"withheld id leak: {entry}")
    for entry in sorted(substituted):
        failures.append(f"not a withholding of exploratory: {entry}")

    assert not failures, "\n".join(failures)


def _request_text(specs, route, label, world, *, ids):
    from tests.api.scientific._curated_sweep_crawl import variants_for

    spec = next(s for s in specs if s.key == route)
    for variant in variants_for(spec, world, with_internal_ids=ids):
        if variant.label == label:
            return variant.request_text
    return ""
