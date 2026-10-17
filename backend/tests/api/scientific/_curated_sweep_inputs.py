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
    "calculation_ref_or_id": lambda w: _refs(w, "calc_vopt", "calc_vsp", "calc_ts_on_hidden_entry", "calc_ts_v"),
    "conformer_group_ref_or_id": lambda w: _refs(w, "cg_v"),
    "conformer_observation_ref_or_id": lambda w: _refs(w, "co_v", "co_v2"),
    "energy_correction_scheme_ref_or_id": lambda w: [w.extra["ecs"].public_ref],
    "frequency_scale_factor_ref_or_id": lambda w: [w.extra["fsf"].public_ref],
    "geometry_handle": lambda w: [
        w.extra["geometry_hidden_calc"].public_ref,
        w.extra["geometry_visible_calc"].public_ref,
    ],
    "level_of_theory_ref_or_id": lambda w: [w.extra["lot"].public_ref],
    "literature_ref_or_id": lambda w: [w.extra["literature"].public_ref],
    "network_kinetics_ref_or_id": lambda w: _refs(w, "nkin_v", "nkin_v_under_hidden_net"),
    "network_ref": lambda w: _refs(w, "net_v"),
    "network_ref_or_id": lambda w: _refs(w, "net_v"),
    "network_solve_ref_or_id": lambda w: _refs(w, "nsolve_v", "nsolve_v_under_hidden_net"),
    "reaction_entry_id": lambda w: [w.extra["reaction_entry"].public_ref],
    "reaction_entry_ref": lambda w: [w.extra["reaction_entry"].public_ref],
    "species_entry_id": lambda w: [w.extra["species_entry"].public_ref],
    "species_entry_ref": lambda w: [w.extra["species_entry"].public_ref],
    "statmech_ref_or_id": lambda w: _refs(w, "sm_v", "sm_v2", "sm_ts_v"),
    "transition_state_entry_ref": lambda w: _refs(w, "tse_v", "tse_v2"),
    "transition_state_entry_ref_or_id": lambda w: _refs(w, "tse_v", "tse_v2"),
    "transition_state_ref_or_id": lambda w: _refs(w, "ts_v"),
    "transport_ref_or_id": lambda w: _refs(w, "trn_v"),
    "sha256": lambda w: [w.extra["artifact_sha_visible"]],
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
    "energy-correction-schemes": [lambda w: {"energy_correction_scheme_ref": w.extra["ecs"].public_ref}],
    "frequency-scale-factors": [lambda w: {"frequency_scale_factor_ref": w.extra["fsf"].public_ref}],
    "kinetics": [lambda w: {"reaction_entry_ref": _rxe(w)}],
    "level-of-theories": [lambda w: {"method": "wb97xd"}],
    "network-kinetics": [lambda w: {"has_chebyshev": True}],
    "network-solves": [lambda w: {"has_chebyshev": True}],
    "networks": [lambda w: {"has_chebyshev": True}],
    "reactions": [lambda w: {"reaction_entry_ref": _rxe(w)}],
    "species-calculations": [lambda w: {"species_entry_ref": _sa(w)}],
    "species": [lambda w: {"species_entry_ref": _sa(w)}],
    "statmech": [
        lambda w: {"species_entry_ref": _sa(w)},
        lambda w: {"transition_state_entry_ref": w.visible["tse_v2"].public_ref},
    ],
    "thermo": [lambda w: {"species_entry_ref": _sa(w)}],
    "transition-states": [lambda w: {"has_calculations": True}],
    "transport": [lambda w: {"has_lj_parameters": True}],
}

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

#: ``(method, route template)`` -> list of input variants, each ``{"query": {...}, "json": {...}}`` (GET uses the
#: query, POST the JSON). A route absent from this map is crawled once with no extra input (empty JSON for POSTs).
ROUTE_INPUTS: dict[tuple[str, str], Callable[[SweepWorld], list[dict[str, Any]]]] = {
    **{(m, f"{BASE}/{name}/search"): _search(*makers) for name, makers in _SEARCHES.items() for m in ("GET", "POST")},
    **{
        (m, f"{BASE}/species/structure-search"): _search(
            lambda w: {"query_smiles": w.extra["species_entry"].species.smiles}
        )
        for m in ("GET", "POST")
    },
    ("GET", f"{BASE}/network-kinetics/{{network_kinetics_ref_or_id}}/evaluate"): lambda w: [
        {"query": {"temperature_k": 1000.0, "pressure_bar": 1.0}}
    ],
    ("POST", f"{BASE}/networks/{{network_ref_or_id}}/kinetics/evaluate"): _post(
        lambda w: {"temperature_k": [1000.0], "pressure_bar": [1.0]}
    ),
    ("GET", f"{BASE}/export/ndjson"): lambda w: [{"query": {"all": True, "min_review_status": "not_reviewed"}}],
    ("GET", f"{BASE}/export/ml/species.ndjson"): lambda w: [
        {"query": {"all": True, "min_review_status": "not_reviewed"}}
    ],
    ("GET", f"{BASE}/export/ml/reactions.ndjson"): lambda w: [
        {"query": {"all": True, "min_review_status": "not_reviewed"}}
    ],
    ("POST", f"{BASE}/export/chemkin"): _post(
        lambda w: {"seed": {"all_reactions": True}, "min_review_status": "not_reviewed"}
    ),
    ("POST", f"{BASE}/reaction-entries/{{reaction_entry_ref}}/kinetics/select"): _KINETICS_SELECT,
    ("POST", f"{BASE}/reaction-entries/{{reaction_entry_ref}}/kinetics/select/manifest"): _KINETICS_SELECT,
    ("POST", f"{BASE}/species-entries/{{species_entry_ref}}/thermo/select"): _THERMO_SELECT,
    ("POST", f"{BASE}/species-entries/{{species_entry_ref}}/thermo/select/manifest"): _THERMO_SELECT,
    **{
        ("POST", f"{BASE}/species-entries/{{species_entry_ref}}/conformers/relative-energies{suffix}"): _RELATIVE_ENERGIES
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

#: ``(method, route template)`` -> reason this route cannot be crawled yet. Must be empty once every family is
#: enforced; each entry names the PR that closes it.
CRAWL_UNSUPPORTED: dict[tuple[str, str], str] = {
    ("GET", f"{BASE}/calculations/{{calculation_ref_or_id}}/scan"): "needs a scan-result fixture (PR-2a)",
    ("GET", f"{BASE}/calculations/{{calculation_ref_or_id}}/irc"): "needs an IRC-result fixture (PR-2a)",
    ("GET", f"{BASE}/calculations/{{calculation_ref_or_id}}/path-search"): "needs a path-search fixture (PR-2a)",
    ("GET", f"{BASE}/composite-schemes/{{composite_scheme_ref}}"): "needs a composite-scheme fixture (PR-2c)",
    ("POST", f"{BASE}/networks/{{network_ref}}/kinetics/select"): "needs a determination world (PR-3)",
    ("POST", f"{BASE}/networks/{{network_ref}}/kinetics/select/manifest"): "needs a determination world (PR-3)",
    ("POST", f"{BASE}/networks/{{network_ref}}/kinetics/export-selected"): "needs a verified manifest (PR-3)",
    ("POST", f"{BASE}/statmech/select"): "needs a statmech-model declaration world (PR-3)",
    ("POST", f"{BASE}/statmech/select/manifest"): "needs a statmech-model declaration world (PR-3)",
}
