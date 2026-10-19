"""Per-route inputs for the curated sweep.

Path parameters are provided by **parameter name**. A parameter with no provider fails the sweep with "add a
provider", which is how a new route is forced into the sweep instead of being skipped. Every provider returns
records that are *visible* under ``profile=curated`` (the subject of a response must pass the handle gate).

Query strings and bodies come from ``ROUTE_INPUTS``, keyed by ``(method, route template)``. A route that answers 422 in
the crawl fails the sweep ("register inputs"): a crawl full of 422s verifies nothing.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from tests.api.scientific._curated_sweep_world import SweepWorld

BASE = "/api/v1/scientific"


def _refs(world: SweepWorld, *keys: str) -> list[str]:
    return [world.visible[k].public_ref for k in keys]


PATH_PARAM_PROVIDERS: dict[str, Callable[[SweepWorld], list[str]]] = {
    "calculation_ref_or_id": lambda w: _refs(
        w, "calc_vopt", "calc_vsp", "calc_ts_on_hidden_entry", "calc_ts_v", "calc_comp"
    ),
    "conformer_group_ref_or_id": lambda w: _refs(w, "cg_v"),
    "conformer_observation_ref_or_id": lambda w: _refs(w, "co_v", "co_v2"),
    "composite_scheme_ref": lambda w: [w.extra["composite_scheme"].public_ref],
    "energy_correction_scheme_ref_or_id": lambda w: [w.extra["ecs"].public_ref, w.extra["ecs_hidden_only"].public_ref],
    "frequency_scale_factor_ref_or_id": lambda w: [
        w.extra["fsf"].public_ref,
        w.extra["fsf_hidden_only"].public_ref,
        w.extra["fsf_limit"].public_ref,
    ],
    "geometry_handle": lambda w: [
        w.extra["geometry_hidden_calc"].public_ref,
        w.extra["geometry_visible_calc"].public_ref,
        w.extra["geometry_hidden_ts_entry"].public_ref,
        w.extra["geometry_ambiguous_owners"].public_ref,
    ],
    "level_of_theory_ref_or_id": lambda w: [w.extra["lot"].public_ref, w.extra["lot_hidden_only"].public_ref],
    "literature_ref_or_id": lambda w: [w.extra["literature"].public_ref, w.extra["literature_hidden_only"].public_ref],
    "network_kinetics_ref_or_id": lambda w: _refs(w, "nkin_v", "nkin_v_under_hidden_net"),
    # The selection routes (select, manifest, export-selected) take the dedicated network of the selection world.
    "network_ref": lambda w: _refs(w, "net_sel"),
    "network_ref_or_id": lambda w: _refs(w, "net_v"),
    "network_solve_ref_or_id": lambda w: _refs(w, "nsolve_v", "nsolve_v_under_hidden_net"),
    "reaction_entry_id": lambda w: [w.extra["reaction_entry"].public_ref],
    "reaction_entry_ref": lambda w: [w.extra["reaction_entry"].public_ref],
    "species_entry_id": lambda w: [w.extra["species_entry"].public_ref],
    "species_entry_ref": lambda w: [w.extra["species_entry"].public_ref],
    "statmech_ref_or_id": lambda w: _refs(w, "sm_v", "sm_v2", "sm_ts_v"),
    "transition_state_entry_ref": lambda w: _refs(w, "tse_v", "tse_v2"),
    "transition_state_entry_ref_or_id": lambda w: _refs(w, "tse_v", "tse_v2", "tse_vu", "tse_vw", "tse_vp"),
    "transition_state_ref_or_id": lambda w: _refs(w, "ts_v", "ts_vu", "ts_vw", "ts_vp"),
    "transport_ref_or_id": lambda w: _refs(w, "trn_v"),
    "sha256": lambda w: [w.extra["artifact_sha_visible"]],
}

#: Providers that apply to one route only, for a parameter whose name is shared with routes that need other records
#: (the scan, IRC and path-search reads need a calculation that has that kind of result).
ROUTE_PATH_PARAM_PROVIDERS: dict[tuple[str, str], dict[str, Callable[[SweepWorld], list[str]]]] = {
    ("GET", f"{BASE}/calculations/{{calculation_ref_or_id}}/scan"): {
        "calculation_ref_or_id": lambda w: _refs(w, "calc_scan")
    },
    ("GET", f"{BASE}/calculations/{{calculation_ref_or_id}}/irc"): {
        "calculation_ref_or_id": lambda w: _refs(w, "calc_irc", "calc_irc_v")
    },
    ("GET", f"{BASE}/calculations/{{calculation_ref_or_id}}/path-search"): {
        "calculation_ref_or_id": lambda w: _refs(w, "calc_ps", "calc_ps_v")
    },
    # PR-2b: reaction entries whose scoping parents are unreviewed (never hidden, never 404), crawled beside the
    # reviewed one on the two reads that list a reaction entry's participants and records.
    **{
        ("GET", f"{BASE}/reaction-entries/{{reaction_entry_id}}/{suffix}"): {
            "reaction_entry_id": lambda w: [
                w.extra["reaction_entry"].public_ref,
                w.extra["reaction_entry_unreviewed"].public_ref,
                w.extra["reaction_entry_hidden_participant"].public_ref,
                w.extra["reaction_entry_all_hidden"].public_ref,
                w.extra["reaction_entry_siblings"].public_ref,
            ]
        }
        for suffix in ("full", "kinetics")
    },
}

#: Path parameters of routes that are exempt (never crawled), so they need no provider.
EXEMPT_ONLY_PARAMS = frozenset({"release_handle", "artifact_path"})


def _search(*filter_makers: Callable[[SweepWorld], dict[str, Any]]):
    """A search route: the same filter as a query string (GET) and as a JSON body (POST)."""

    def make(w: SweepWorld) -> list[dict[str, Any]]:
        return [{"query": f(w), "json": f(w)} for f in filter_makers]

    return make


def _post(*bodies: Callable[[SweepWorld], dict[str, Any]]):
    return lambda w: [{"json": b(w)} for b in bodies]


_sa = lambda w: w.extra["species_entry"].public_ref  # noqa: E731
_rxe = lambda w: w.extra["reaction_entry"].public_ref  # noqa: E731

_SEARCHES: dict[str, list[Callable[[SweepWorld], dict[str, Any]]]] = {
    "artifacts": [
        lambda w: {"calculation_ref": w.visible["calc_vopt"].public_ref},
        lambda w: {"artifact_kind": "output_log"},
    ],
    "calculations": [
        lambda w: {"calculation_type": "freq"},
        lambda w: {"calculation_type": "opt"},
        lambda w: {"calculation_type": "sp"},
    ],
    "conformers": [
        lambda w: {"conformer_group_ref": w.visible["cg_v"].public_ref},
        lambda w: {"conformer_observation_ref": w.visible["co_v2"].public_ref},
    ],
    "energy-correction-schemes": [
        lambda w: {"energy_correction_scheme_ref": w.extra["ecs"].public_ref, "include": ["all"]},
        lambda w: {"energy_correction_scheme_ref": w.extra["ecs_hidden_only"].public_ref, "include": ["all"]},
    ],
    "frequency-scale-factors": [
        lambda w: {"frequency_scale_factor_ref": w.extra["fsf"].public_ref, "include": ["all"]},
        lambda w: {"frequency_scale_factor_ref": w.extra["fsf_hidden_only"].public_ref, "include": ["all"]},
    ],
    "kinetics": [
        lambda w: {"reaction_entry_ref": _rxe(w)},
        # PR-2b: every include token (``trust`` and ``assessments`` are not part of ``all``) and the superseded records.
        lambda w: {
            "reaction_entry_ref": _rxe(w),
            "include": ["all", "trust", "assessments"],
            "include_deprecated": True,
            "include_rejected": True,
        },
    ],
    "level-of-theories": [
        lambda w: {"method": "wb97xd", "include": ["all"]},
        lambda w: {"method": "hiddenonly", "include": ["all"]},
    ],
    # PR-2c: the PLOG filters match only the hidden solve's fit; the Chebyshev ones match visible and hidden fits.
    "network-kinetics": [
        lambda w: {"has_chebyshev": True, "include": ["all"]},
        lambda w: {"has_plog": True, "include": ["all"]},
    ],
    "network-solves": [
        lambda w: {"has_chebyshev": True, "include": ["all"]},
        lambda w: {"has_plog": True, "include": ["all"]},
    ],
    "networks": [
        lambda w: {"has_chebyshev": True, "include": ["all"]},
        lambda w: {"has_plog": True, "include": ["all"]},
    ],
    "reactions": [
        lambda w: {"reaction_entry_ref": _rxe(w)},
        lambda w: {"reaction_entry_ref": _rxe(w), "include": ["all"]},
        lambda w: {"reaction_entry_ref": w.extra["reaction_entry_hidden_participant"].public_ref, "include": ["all"]},
    ],
    "species-calculations": [lambda w: {"species_entry_ref": _sa(w)}],
    "species": [lambda w: {"species_entry_ref": _sa(w)}],
    "statmech": [
        lambda w: {"species_entry_ref": _sa(w)},
        lambda w: {"transition_state_entry_ref": w.visible["tse_v2"].public_ref},
    ],
    "thermo": [lambda w: {"species_entry_ref": _sa(w)}],
    "transition-states": [
        lambda w: {"has_calculations": True},
        # PR-2b: ``trust`` and ``entries`` are not part of ``all`` on the search surface.
        lambda w: {"reaction_entry_ref": _rxe(w), "include": ["all", "trust", "entries"]},
    ],
    "transport": [lambda w: {"has_lj_parameters": True}],
}


def _variants(*queries: dict[str, Any]):
    return lambda w: [{"query": q} for q in queries]


_ALL_TS_TOKENS = ["all", "entries", "validation_evidence", "statmech", "review"]

_SELECT_STRUCTURE = _post(lambda w: {})
_KINETICS_SELECT = _post(
    lambda w: {
        "direction": "forward",
        "target": {"kind": "whole_reaction"},
        "coefficient_basis": "elementary_coefficient",
        "temperature_min_k": 500.0,
        "temperature_max_k": 1500.0,
        "pressure": {"kind": "independent"},
    }
)
_THERMO_SELECT = _post(lambda w: {"target": {"kind": "equilibrium_ensemble"}})
# Relative conformer energies: the visible group is the reference; the world's hidden group is the same entry's, so a
# leak of it into the answer or the manifest is what this crawl would see.
_RELATIVE_ENERGIES = _post(
    lambda w: {
        "reference_conformer_ref": w.visible["cg_v"].public_ref,
        "energy_kind": "electronic_energy",
        "level": {"level_of_theory_ref": w.extra["lot"].public_ref},
        "certificate": "local_minimum_any_surface",
    }
)
# The comparison of two protocols. The world has one level, so the protocols differ by a recipe (the same level named
# twice would be refused as identical); the visible group is the reference, the hidden group the same entry's.
_RELATIVE_ENERGIES_COMPARE = _post(
    lambda w: {
        "reference_conformer_ref": w.visible["cg_v"].public_ref,
        "energy_kind": "electronic_energy",
        "certificate": "local_minimum_any_surface",
        "candidate": {
            "level": {"level_of_theory_ref": w.extra["lot"].public_ref},
            "recipe": {
                "version": 1,
                "source": {"origin": "producer_declared", "producer": "arc"},
                "spin_treatment": {"state": "known", "value": "restricted"},
            },
        },
        "reference": {"level": {"level_of_theory_ref": w.extra["lot"].public_ref}},
        "pairing": "same_basin",
    }
)

def _network_question(w: SweepWorld) -> dict[str, Any]:
    """A single-channel question the dedicated network's visible solve answers (its hidden sibling answers it too)."""
    ns = w.extra["network_select_world"]
    return {
        "scope": "single_channel",
        "channel_key": "assoc",
        "observable": "product_resolved_coefficient",
        "coefficient_basis": "kernel",
        "temperature_min_k": 500.0,
        "temperature_max_k": 1500.0,
        "pressure_min_bar": 0.5,
        "pressure_max_bar": 5.0,
        "bath": {"components": [{"species_ref": ns.ar.public_ref}]},
        "partition": {"retained": sorted(ns.hashes.values())},
    }


_NETWORK_SELECT = _post(_network_question)

#: A thermal qualification of the species entry's statmech models in the visible conformer group.
_STATMECH_SELECT = _post(
    lambda w: {
        "intent": "qualify",
        "task": "within_basin_thermal",
        "observable": "entropy",
        "target": {
            "kind": "species_entry",
            "species_entry_ref": w.extra["species_entry"].public_ref,
            "scope": {"kind": "basin", "conformer_group_ref": w.visible["cg_v"].public_ref},
            "isotope_convention": "entry_documented_masses",
            "electronic_scope": "entry_state",
            "stereochemical_scope": "entry_identity",
        },
        "conditions": {"temperature_domain_k": {"min": 298.15, "max": 500.0}, "normalization": "internal_only"},
    }
)


def _export_selected(w: SweepWorld) -> list[dict[str, Any]]:
    """The export request each profile would make: the manifest ITS download gives, and the node it selects.

    An export is only accepted for a manifest the server would write itself now, so the body depends on the profile the
    request is made under; ``json_by_profile`` carries one per profile.
    """
    from app.db.models.common import ProfileRecommendation, ReadProfile
    from app.schemas.reads.scientific_network_selection import NetworkSelectionRequest
    from app.services.network_selection.public import profile_has_floor, redact_manifest, run_selection
    from app.services.scientific_read.profile import (
        EXPLORATORY,
        ResolvedReadProfile,
        reset_current_read_profile,
        set_current_read_profile,
    )

    session = w.extra["session"]
    session.info["tckdb_read_snapshot_opt_out"] = True  # the harness holds one outer transaction, as the dependency override says
    network_ref = w.visible["net_sel"].public_ref
    out: dict[str, dict[str, Any]] = {}
    for name, resolved in (
        ("exploratory", EXPLORATORY),
        (
            "curated",
            ResolvedReadProfile(profile=ReadProfile.curated, recommendation=ProfileRecommendation.approved_floor_only),
        ),
    ):
        token = set_current_read_profile(resolved)
        try:
            selection = run_selection(
                session, network_ref=network_ref, body=NetworkSelectionRequest.model_validate(_network_question(w))
            )
            manifest = redact_manifest(selection.manifest, withhold_excluded=profile_has_floor())
        finally:
            reset_current_read_profile(token)
        decision = manifest["decision"]
        node = decision["selected_ref"] or decision["fronts"][0][0]
        row = next(a for a in manifest["assessments"]["determinations"] if a["determination_ref"] == node)
        out[name] = {
            "manifest": manifest,
            "node_ref": node,
            "representation_refs": list(row["eligible_fit_refs"]),
            "allow_administrative_choice": True,
        }
    return [{"json_by_profile": out}]


#: ``(method, route template)`` -> list of input variants, each ``{"query": {...}, "json": {...}}`` (GET uses the
#: query, POST the JSON). A route absent from this map is crawled once with no extra input (empty JSON for POSTs).
def _export_species_refs(w: SweepWorld) -> list[str]:
    """The species entries the exports are seeded with beside ``all``: the ones holding hidden thermo, statmech,
    transport witnesses, calculations and conformers (none of them is a participant of a reaction)."""
    return [
        w.extra[key].public_ref
        for key in (
            "species_entry",
            "species_entry_export",
            "species_entry_conflict",
            "species_entry_hidden_link",
            "species_entry_hidden_thermo_only",
        )
    ]


ROUTE_INPUTS: dict[tuple[str, str], Callable[[SweepWorld], list[dict[str, Any]]]] = {
    **{(m, f"{BASE}/{name}/search"): _search(*makers) for name, makers in _SEARCHES.items() for m in ("GET", "POST")},
    **{
        (m, f"{BASE}/species/structure-search"): _search(
            lambda w: {"query_smiles": w.extra["species_entry"].species.smiles}
        )
        for m in ("GET", "POST")
    },
    # PR-2b: the family's detail reads with every include token, the opt-in ones (``trust``, ``assessments``) included.
    ("GET", f"{BASE}/reaction-entries/{{reaction_entry_id}}/full"): _variants(
        {},
        {"include": ["all", "trust"]},
        {"include": ["all"], "include_review": "full", "include_deprecated": "true", "include_rejected": "true"},
    ),
    ("GET", f"{BASE}/reaction-entries/{{reaction_entry_id}}/kinetics"): _variants(
        {},
        {"include": ["all", "trust", "assessments", "interpretations"], "include_deprecated": "true"},
    ),
    ("GET", f"{BASE}/transition-state-entries/{{transition_state_entry_ref_or_id}}"): _variants(
        {}, {"include": [*_ALL_TS_TOKENS, "trust"]}
    ),
    ("GET", f"{BASE}/transition-states/{{transition_state_ref_or_id}}"): _variants({}, {"include": _ALL_TS_TOKENS}),
    ("GET", f"{BASE}/transition-states/browse"): _variants({}, {"include": _ALL_TS_TOKENS}),
    # PR-2c: ``points`` is not part of ``all`` on a fit's detail.
    ("GET", f"{BASE}/network-kinetics/{{network_kinetics_ref_or_id}}"): _variants(
        {}, {"include": ["all", "points"]}
    ),
    ("GET", f"{BASE}/network-kinetics/{{network_kinetics_ref_or_id}}/evaluate"): lambda w: [
        {"query": {"temperature_k": 1000.0, "pressure_bar": 1.0}}
    ],
    ("POST", f"{BASE}/networks/{{network_ref_or_id}}/kinetics/evaluate"): _post(
        lambda w: {"temperature_k": [1000.0], "pressure_bar": [1.0]}
    ),
    ("GET", f"{BASE}/export/ndjson"): lambda w: [
        {"query": {"all": True, "species_ref": _export_species_refs(w), "min_review_status": "not_reviewed"}},
        # Every candidate, not the first: the hidden thermo and kinetics records are candidates too.
        {
            "query": {
                "all": True,
                "species_ref": _export_species_refs(w),
                "min_review_status": "not_reviewed",
                "collapse": "all",
            }
        },
    ],
    ("GET", f"{BASE}/export/ml/species.ndjson"): lambda w: [
        {"query": {"all": True, "min_review_status": "not_reviewed", "include_hessian": True}}
    ],
    ("GET", f"{BASE}/export/ml/reactions.ndjson"): lambda w: [
        {"query": {"all": True, "min_review_status": "not_reviewed"}}
    ],
    ("POST", f"{BASE}/export/chemkin"): _post(
        lambda w: {
            "seed": {"all_reactions": True, "species_refs": _export_species_refs(w)},
            "min_review_status": "not_reviewed",
        }
    ),
    ("POST", f"{BASE}/reaction-entries/{{reaction_entry_ref}}/kinetics/select"): _KINETICS_SELECT,
    ("POST", f"{BASE}/reaction-entries/{{reaction_entry_ref}}/kinetics/select/manifest"): _KINETICS_SELECT,
    ("POST", f"{BASE}/networks/{{network_ref}}/kinetics/select"): _NETWORK_SELECT,
    ("POST", f"{BASE}/networks/{{network_ref}}/kinetics/select/manifest"): _NETWORK_SELECT,
    ("POST", f"{BASE}/networks/{{network_ref}}/kinetics/export-selected"): _export_selected,
    ("POST", f"{BASE}/statmech/select"): _STATMECH_SELECT,
    ("POST", f"{BASE}/statmech/select/manifest"): _STATMECH_SELECT,
    ("POST", f"{BASE}/species-entries/{{species_entry_ref}}/thermo/select"): _THERMO_SELECT,
    ("POST", f"{BASE}/species-entries/{{species_entry_ref}}/thermo/select/manifest"): _THERMO_SELECT,
    **{
        (
            "POST",
            f"{BASE}/species-entries/{{species_entry_ref}}/conformers/relative-energies{suffix}",
        ): _RELATIVE_ENERGIES
        for suffix in ("", "/manifest")
    },
    **{
        (
            "POST",
            f"{BASE}/species-entries/{{species_entry_ref}}/conformers/relative-energies/compare{suffix}",
        ): _RELATIVE_ENERGIES_COMPARE
        for suffix in ("", "/manifest")
    },
    **{
        ("POST", f"{BASE}/{root}/select{suffix}"): _SELECT_STRUCTURE
        for root in (
            "species-entries/{species_entry_ref}/calculations",
            "species-entries/{species_entry_ref}/conformers",
            "transition-state-entries/{transition_state_entry_ref}/evidence",
        )
        for suffix in ("", "/manifest")
    },
}

#: ``(method, route template)`` -> reason this route cannot be crawled yet. Empty since guard PR-3: the selection routes
#: (network select, statmech select, export-selected) are crawled over the selection world.
CRAWL_UNSUPPORTED: dict[tuple[str, str], str] = {}
