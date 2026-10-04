"""Selected-export of network kinetics: serialise what a verified selection chose, and refuse everything else.

The caller submits a decision manifest it saved from ``.../kinetics/select/manifest``, the node it chose (a
determination, or a declared product set of one solve), and exactly one fitted representation for every member of
that node. The server then, under one read-only snapshot:

1. checks the manifest is complete, belongs to this network, and **replays** at both levels from its own captured
   inputs (a forged or internally inconsistent document is refused);
2. **re-runs the selection** from the manifest's normalised request against server-held content and the caller's
   authorised population, and requires the scientific content (captured solves, assessments, decision, rule
   registry, read profile) to be identical. A material change in content, review, rules or population is a stale
   manifest: it needs a fresh selection and is never silently refreshed here;
3. checks the choice against the decision: a selected node must be the chosen one, an incomparable leading-front
   node only when the caller accepted an administrative choice (default no), and nothing is exportable from a
   conflict or from no applicable candidate;
4. checks the representation choice is exact: one eligible fit per member, none extra, none missing. Alternatives are
   never added alongside the choice, and ``DUPLICATE`` is never written;
5. refuses forms it cannot serialise with a structured refusal naming each fit and the reason.

The manifest's own digest is never trusted on its own: steps 1 and 2 both recompute. Replay reproduces reasoning from
captured inputs and does not authenticate scientific truth, which is why step 2 compares with the server's content.

Native output preserves solve, determination and representation provenance and the directed endpoints of every
channel. CHEMKIN output is forward-only (``=>``): no reverse coefficient is derived from reversibility, and no
thermodynamics are written, so there is no reverse or thermo assumption to make. Both are recorded in the output.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.error_contract import CodedValueError
from app.db.models.network import Network
from app.db.models.network_pdep import (
    NetworkKinetics,
    NetworkKineticsChebyshev,
    NetworkKineticsPlog,
    NetworkKineticsPoint,
    NetworkSolve,
)
from app.db.models.species import Species, SpeciesEntry
from app.services.network_selection.manifest import ReplayError, replay_network
from app.services.network_selection.models import BOUNDS_V1, NetworkRequest
from app.services.network_selection.selection import select_network
from app.services.scientific_read.chemkin_serialize import (
    _EA_UNIT_HEADERS,
    _a_to_mol_cm_s,
    _composition,
    _convert_ea,
    _elements,
    _flatten_cheb,
    _formula,
    _sanitize_name,
)
from app.services.scientific_read.profile import current_read_profile
from app.services.selection_kernel import Outcome

CODE_MANIFEST_INVALID = "network_export_manifest_invalid"
CODE_MANIFEST_STALE = "network_export_manifest_stale"
CODE_CHOICE_NOT_ALLOWED = "network_export_choice_not_allowed"
CODE_REPRESENTATION_CHOICE = "network_export_representation_choice_invalid"
CODE_UNSUPPORTED_FORM = "network_export_unsupported_form"

FORMATS = ("native", "chemkin")
#: Manifest sections that must match server-held content for the export to proceed.
_CONTENT_SECTIONS = ("network", "solves", "assessments", "decision", "outcome", "policy", "declaration_versions")
_REQUIRED_KEYS = (
    "manifest_format_version", "policy", "request", "visibility", "network", "population", "solves", "assessments",
    "decision", "outcome", "digest",
)
_REPLAY_ERRORS = (ReplayError, KeyError, TypeError, ValueError, AttributeError, IndexError)
_PRESSURE_ATM_PER_BAR = 1.0 / 1.01325


def _refuse(code: str, message: str, **context: Any) -> CodedValueError:
    return CodedValueError(code, message, context=context, message_prefix=False)


@dataclass(frozen=True)
class ExportChoice:
    """What the caller chose, as submitted."""

    node_ref: str
    representation_refs: tuple[str, ...]
    format: str = "native"
    allow_administrative_choice: bool = False
    energy_units: str = "cal/mol"
    naming_policy: str = "formula"


# ---------------------------------------------------------------------------
# 1 and 2: the manifest is complete, replays, and is what the server holds
# ---------------------------------------------------------------------------


def _check_complete(manifest: dict[str, Any], network_ref: str) -> None:
    missing = [k for k in _REQUIRED_KEYS if k not in manifest]
    if missing:
        raise _refuse(
            CODE_MANIFEST_INVALID, "the submitted manifest is incomplete; nothing was exported.",
            reason="incomplete", missing=missing,
        )
    size = len(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    if size > BOUNDS_V1.snapshot_bytes:
        raise _refuse(
            CODE_MANIFEST_INVALID, "the submitted manifest is over the snapshot size bound; nothing was exported.",
            reason="too_large", limit_bytes=BOUNDS_V1.snapshot_bytes,
        )
    request = manifest["request"]
    if not isinstance(request, dict) or request.get("network_ref") != network_ref:
        raise _refuse(
            CODE_MANIFEST_INVALID, "the submitted manifest is for another network; nothing was exported.",
            reason="network_mismatch",
        )


def _replay(manifest: dict[str, Any]) -> None:
    try:
        replay_network(manifest)
    except _REPLAY_ERRORS as exc:
        raise _refuse(
            CODE_MANIFEST_INVALID,
            "the submitted manifest does not replay from its own captured inputs; nothing was exported.",
            reason="replay_failed", detail=str(exc)[:300],
        ) from exc


def _live_selection(session: Session, manifest: dict[str, Any], *, require_snapshot: bool) -> dict[str, Any]:
    request = NetworkRequest.from_dict(
        {k: v for k, v in manifest["request"].items() if k != "effective_review_statuses"}
    )
    return select_network(session, request=request, require_snapshot=require_snapshot).manifest


def _verify_against_server(session: Session, manifest: dict[str, Any], *, require_snapshot: bool) -> None:
    """The submitted manifest must say what the server, reading now, says. Raises ``..._stale`` otherwise."""
    try:
        live = _live_selection(session, manifest, require_snapshot=require_snapshot)
    except (KeyError, TypeError) as exc:
        raise _refuse(
            CODE_MANIFEST_INVALID, "the submitted manifest's request cannot be read; nothing was exported.",
            reason="request_unreadable",
        ) from exc
    echo = current_read_profile().echo()
    submitted_request = manifest["request"]
    unknown = sorted(set(submitted_request) - set(live["request"]) - set(echo))
    differs = [name for name in _CONTENT_SECTIONS if manifest.get(name) != live.get(name)]
    if {k: v for k, v in submitted_request.items() if k in live["request"]} != live["request"]:
        differs.append("request")
    if any(submitted_request.get(k) != v for k, v in echo.items()):
        differs.append("read_profile")
    if manifest["population"].get("counts") != live["population"]["counts"]:
        differs.append("population")
    if manifest["visibility"].get("review_statuses_observed") != live["visibility"]["review_statuses_observed"]:
        differs.append("review_states")
    if unknown or differs:
        raise _refuse(
            CODE_MANIFEST_STALE,
            "the submitted manifest is not what the server holds now (content, review states, rules, population or "
            "read profile changed, or the document was edited); run a fresh selection. Nothing was exported.",
            differs=sorted(set(differs)), unknown_request_keys=unknown,
        )


# ---------------------------------------------------------------------------
# 3 and 4: the choice
# ---------------------------------------------------------------------------


def _members(manifest: dict[str, Any], node_ref: str) -> tuple[str, list[dict[str, Any]]]:
    """The solve and the members (determination, channel, eligible fits) of an eligible node, or ``(\"\", [])``."""
    assessments = manifest["assessments"]
    channel = {a["determination_ref"]: a["channel_key"] for a in assessments["determinations"]}
    if manifest["request"]["scope"] == "single_channel":
        for row in assessments["determinations"]:
            if row["determination_ref"] == node_ref and row["physically_eligible"]:
                return row["solve_ref"], [
                    {"determination_ref": node_ref, "channel_key": row["channel_key"], "fits": list(row["eligible_fit_refs"])}
                ]
        return "", []
    for node in assessments["bundles"]:
        if node["node_ref"] == node_ref and node["physically_eligible"]:
            return node["solve_ref"], [
                {"determination_ref": ref, "channel_key": channel.get(ref), "fits": list(fits)}
                for ref, fits in node["member_fit_refs"]
            ]
    return "", []


def _check_choice(manifest: dict[str, Any], choice: ExportChoice) -> bool:
    """Whether the node may be exported at all. Returns ``administrative`` (the choice was accepted as one)."""
    decision = manifest["decision"]
    outcome = decision["outcome"]
    node = choice.node_ref
    if outcome in (Outcome.policy_preferred.value, Outcome.sole_eligible_candidate.value):
        if node != decision["selected_ref"]:
            raise _refuse(
                CODE_CHOICE_NOT_ALLOWED,
                "the chosen node is not the one the selection chose; a node outside the decision is not exported.",
                reason="not_the_selected_node", outcome=outcome,
            )
        return False
    if outcome == Outcome.incomparable_alternatives.value:
        front = decision["fronts"][0] if decision["fronts"] else []
        if node not in front:
            raise _refuse(
                CODE_CHOICE_NOT_ALLOWED, "the chosen node is not in the leading front of unranked alternatives.",
                reason="outside_leading_front", outcome=outcome,
            )
        if not choice.allow_administrative_choice:
            raise _refuse(
                CODE_CHOICE_NOT_ALLOWED,
                "the selection could not rank the leading alternatives; exporting one is an administrative choice, "
                "which this request did not accept (allow_administrative_choice is false).",
                reason="administrative_choice_not_accepted", outcome=outcome,
            )
        return True
    raise _refuse(
        CODE_CHOICE_NOT_ALLOWED,
        "this selection chose nothing (a conflict, or no applicable candidate); an administrative choice never "
        "bypasses that.",
        reason="outcome_selects_nothing", outcome=outcome,
    )


def _check_representations(members: list[dict[str, Any]], choice: ExportChoice) -> dict[str, str]:
    """``{determination ref: chosen fit ref}``: exactly one eligible fit for every member, and nothing else."""
    submitted = list(choice.representation_refs)
    duplicated = sorted(ref for ref, n in Counter(submitted).items() if n > 1)
    eligible = {fit: m["determination_ref"] for m in members for fit in m["fits"]}
    unknown = sorted(set(submitted) - set(eligible))
    chosen: dict[str, list[str]] = {m["determination_ref"]: [] for m in members}
    for ref in submitted:
        if ref in eligible:
            chosen[eligible[ref]].append(ref)
    missing = sorted(det for det, refs in chosen.items() if not refs)
    several = sorted(det for det, refs in chosen.items() if len(refs) > 1)
    if duplicated or unknown or missing or several:
        raise _refuse(
            CODE_REPRESENTATION_CHOICE,
            "the representation choice must name exactly one eligible fit for every member of the node, and "
            "nothing else; alternatives are never added alongside the choice.",
            duplicated=duplicated, not_eligible_for_this_node=unknown, members_without_a_choice=missing,
            members_with_several=several,
        )
    return {det: refs[0] for det, refs in chosen.items()}


# ---------------------------------------------------------------------------
# Content: the numbers, the endpoints, the species
# ---------------------------------------------------------------------------


def _load_fits(session: Session, network_ref: str, solve_ref: str, fit_refs: list[str]) -> dict[str, dict[str, Any]]:
    rows = list(
        session.execute(
            select(NetworkKinetics, NetworkSolve.public_ref)
            .join(NetworkSolve, NetworkSolve.id == NetworkKinetics.solve_id)
            .join(Network, Network.id == NetworkSolve.network_id)
            .where(NetworkKinetics.public_ref.in_(fit_refs), Network.public_ref == network_ref)
        ).tuples()
    )
    found = {fit.public_ref: (fit, solve) for fit, solve in rows}
    foreign = sorted(ref for ref, (_, solve) in found.items() if solve != solve_ref)
    absent = sorted(set(fit_refs) - set(found))
    if foreign or absent:
        raise _refuse(
            CODE_REPRESENTATION_CHOICE, "a chosen fit is not a fit of this network and solve.",
            not_found_or_hidden=absent, of_another_solve=foreign,
        )
    ids = [fit.id for fit, _ in found.values()]
    plog: dict[int, list[NetworkKineticsPlog]] = {}
    for plog_row in session.scalars(select(NetworkKineticsPlog).where(NetworkKineticsPlog.network_kinetics_id.in_(ids))):
        plog.setdefault(plog_row.network_kinetics_id, []).append(plog_row)
    cheb = {
        c.network_kinetics_id: c
        for c in session.scalars(
            select(NetworkKineticsChebyshev).where(NetworkKineticsChebyshev.network_kinetics_id.in_(ids))
        )
    }
    points: dict[int, list[NetworkKineticsPoint]] = {}
    for point_row in session.scalars(select(NetworkKineticsPoint).where(NetworkKineticsPoint.network_kinetics_id.in_(ids))):
        points.setdefault(point_row.network_kinetics_id, []).append(point_row)
    out: dict[str, dict[str, Any]] = {}
    for ref, (fit, _) in found.items():
        data: dict[str, Any] = {
            "kinetics_ref": ref,
            "model_kind": fit.model_kind.value,
            "temperature_min_k": fit.tmin_k,
            "temperature_max_k": fit.tmax_k,
            "pressure_min_bar": fit.pmin_bar,
            "pressure_max_bar": fit.pmax_bar,
            "rate_units": fit.rate_units.value if fit.rate_units is not None else None,
            "pressure_units": fit.pressure_units.value if fit.pressure_units is not None else None,
            "temperature_units": fit.temperature_units.value if fit.temperature_units is not None else None,
            "stores_log10_k": fit.stores_log10_k,
        }
        if fit.model_kind.value == "plog":
            data["plog"] = [
                {
                    "pressure_bar": r.pressure_bar, "entry_index": r.entry_index, "a": r.a,
                    "a_units": r.a_units.value if r.a_units is not None else None, "n": r.n, "ea_kj_mol": r.ea_kj_mol,
                }
                for r in sorted(plog.get(fit.id, []), key=lambda r: (r.pressure_bar, r.entry_index))
            ]
            data["_plog_units"] = [r.a_units for r in sorted(plog.get(fit.id, []), key=lambda r: (r.pressure_bar, r.entry_index))]
        elif fit.model_kind.value == "chebyshev":
            c = cheb.get(fit.id)
            data["chebyshev"] = (
                None if c is None else
                {"n_temperature": c.n_temperature, "n_pressure": c.n_pressure, "coefficients": c.coefficients}
            )
        else:
            data["points"] = [
                {"temperature_k": r.temperature_k, "pressure_bar": r.pressure_bar, "rate_value": r.rate_value}
                for r in sorted(points.get(fit.id, []), key=lambda r: (r.temperature_k, r.pressure_bar))
            ]
        data["_fit"] = fit
        out[ref] = data
    return out


def _species_content(session: Session, refs: set[str]) -> dict[str, dict[str, Any]]:
    rows = session.execute(
        select(SpeciesEntry.public_ref, Species.smiles, Species.public_ref)
        .join(Species, Species.id == SpeciesEntry.species_id)
        .where(SpeciesEntry.public_ref.in_(refs))
    ).tuples()
    return {entry_ref: {"smiles": smiles, "species_ref": species_ref} for entry_ref, smiles, species_ref in rows}


def _endpoints(manifest: dict[str, Any], channel_key: str) -> dict[str, Any]:
    network = manifest["network"]
    channel = next(c for c in network["channels"] if c["channel_key"] == channel_key)
    state = {s["composition_hash"]: s for s in network["states"]}
    return {
        "channel_key": channel_key,
        "kind": channel["kind"],
        "mechanism": channel["mechanism"],
        "source": state[channel["source_hash"]],
        "sink": state[channel["sink_hash"]],
    }


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------


def _equation_names(side: dict[str, Any], names: dict[str, str]) -> str:
    parts = []
    for ref, stoichiometry in side["participants"]:
        parts.append((f"{stoichiometry} " if stoichiometry != 1 else "") + names[ref])
    return " + ".join(parts)


def _plan_names(species: dict[str, dict[str, Any]], naming_policy: str) -> tuple[dict[str, str], dict[str, Any], list[dict[str, str]]]:
    """``(names by species-entry ref, compositions by ref, problems)``."""
    names: dict[str, str] = {}
    compositions: dict[str, Any] = {}
    problems: list[dict[str, str]] = []
    used: set[str] = set()
    for ref in sorted(species):
        content = species[ref]
        comp = _composition(content["smiles"])
        if comp is None:
            problems.append({"species_ref": ref, "reason": "species_composition_unavailable"})
            continue
        compositions[ref] = comp
        base = _sanitize_name(content["species_ref"]) if naming_policy == "public_ref" else _formula(comp)
        name, suffix = base, 1
        while name in used:
            suffix += 1
            name = f"{base}-{suffix}"
        used.add(name)
        names[ref] = name
    return names, compositions, problems


def _log10_offset(units: Any) -> float | None:
    factor = _a_to_mol_cm_s(1.0, units)
    return None if factor is None or factor <= 0 else math.log10(factor)


def _chemkin_problems(fit: dict[str, Any]) -> list[str]:
    kind = fit["model_kind"]
    if kind not in ("plog", "chebyshev"):
        return [f"model_kind_{kind}_has_no_chemkin_form"]
    problems: list[str] = []
    if fit["pressure_units"] != "bar" or fit["temperature_units"] != "kelvin":
        problems.append("axis_units_not_bar_kelvin")
    if kind == "chebyshev":
        if fit["stores_log10_k"] is not True:
            problems.append("chebyshev_does_not_store_log10_k")
        if None in (fit["temperature_min_k"], fit["temperature_max_k"], fit["pressure_min_bar"], fit["pressure_max_bar"]):
            problems.append("chebyshev_mapping_domain_missing")
        if fit.get("chebyshev") is None:
            problems.append("chebyshev_coefficients_missing")
        elif fit["rate_units"] is None:
            problems.append("rate_units_not_stated")
    else:
        if not fit["plog"]:
            problems.append("plog_entries_missing")
        if any(u is None for u in fit["_plog_units"]) and fit["rate_units"] is None:
            problems.append("rate_units_not_stated")
    return problems


def _reaction_block(
    equation: str, fit: dict[str, Any], energy_units: str
) -> list[str]:
    lines = [f"{equation}   1.0000E+00 0.000 0.0000   ! TCKDB {fit['kinetics_ref']} (k(T,P) is in the {fit['model_kind'].upper()} block)"]
    if fit["model_kind"] == "plog":
        for row, units in zip(fit["plog"], fit["_plog_units"], strict=True):
            a = _a_to_mol_cm_s(row["a"], units or _units_of(fit))
            ea = _convert_ea(row["ea_kj_mol"], energy_units)
            lines.append(
                f"    PLOG / {row['pressure_bar'] * _PRESSURE_ATM_PER_BAR:.4E} {a:.4E} {row['n']:.3f} {ea:.4f} /"
            )
    else:
        cheb = fit["chebyshev"]
        flat = _flatten_cheb(cheb["coefficients"])
        offset = _log10_offset(_units_of(fit)) or 0.0
        if flat:
            flat[0] += offset  # a constant added to the first coefficient shifts log10 k by that constant
        lines.append(f"    TCHEB / {fit['temperature_min_k']:.2f} {fit['temperature_max_k']:.2f} /")
        lines.append(
            f"    PCHEB / {fit['pressure_min_bar'] * _PRESSURE_ATM_PER_BAR:.4E} "
            f"{fit['pressure_max_bar'] * _PRESSURE_ATM_PER_BAR:.4E} /"
        )
        lines.append(
            f"    CHEB / {cheb['n_temperature']} {cheb['n_pressure']} " + " ".join(f"{v:.6E}" for v in flat) + " /"
        )
    return lines


def _units_of(fit: dict[str, Any]) -> Any:
    return fit["_fit"].rate_units


def _chemkin_files(
    members: list[dict[str, Any]], fits: dict[str, dict[str, Any]], manifest: dict[str, Any],
    species: dict[str, dict[str, Any]], choice: ExportChoice,
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """``(files, equation collisions)``; raises the structured refusal for anything unsupported."""
    names, compositions, problems = _plan_names(species, choice.naming_policy)
    forms: list[dict[str, Any]] = [
        {"reason": p["reason"], "species_ref": p["species_ref"]} for p in problems
    ]
    equations: dict[str, list[str]] = {}
    blocks: list[tuple[str, dict[str, Any]]] = []
    for member in members:
        fit = fits[member["chosen"]]
        for reason in _chemkin_problems(fit):
            forms.append({"kinetics_ref": fit["kinetics_ref"], "reason": reason})
        ends = member["channel"]
        try:
            equation = f"{_equation_names(ends['source'], names)} => {_equation_names(ends['sink'], names)}"
        except KeyError:
            continue  # a species without a composition was already reported above
        equations.setdefault(equation, []).append(fit["kinetics_ref"])
        blocks.append((equation, fit))
    collisions = [{"equation": eq, "kinetics_refs": refs} for eq, refs in sorted(equations.items()) if len(refs) > 1]
    for collision in collisions:
        for ref in collision["kinetics_refs"]:
            forms.append({"kinetics_ref": ref, "reason": "equation_collision_is_not_additive", "equation": collision["equation"]})
    if forms:
        raise _refuse(
            CODE_UNSUPPORTED_FORM,
            "the chosen representations cannot be serialised to CHEMKIN; nothing was exported. A native export keeps "
            "every form. Equation collisions are reported, never added as DUPLICATE.",
            format="chemkin", forms=forms,
        )
    elements = _elements({i: compositions[ref] for i, ref in enumerate(sorted(compositions))})
    lines = ["ELEMENTS", " ".join(elements) or " ", "END", "", "SPECIES"]
    for ref in sorted(names):
        lines.append(f"{names[ref]}   ! SMILES={species[ref]['smiles']} ref={species[ref]['species_ref']}")
    lines += ["END", "", f"REACTIONS {_EA_UNIT_HEADERS.get(choice.energy_units.lower(), 'CAL/MOLE')} MOLES"]
    for equation, fit in blocks:
        lines.extend(_reaction_block(equation, fit, choice.energy_units))
    lines += ["END", ""]
    return {"chem.inp": "\n".join(lines)}, collisions


def _native_members(
    members: list[dict[str, Any]], fits: dict[str, dict[str, Any]], species: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    out = []
    for member in members:
        fit = {k: v for k, v in fits[member["chosen"]].items() if not k.startswith("_")}
        ends = member["channel"]
        out.append(
            {
                "determination_ref": member["determination_ref"],
                "channel_key": member["channel_key"],
                "channel": {
                    "kind": ends["kind"],
                    "mechanism": ends["mechanism"],
                    "source": _state_with_species(ends["source"], species),
                    "sink": _state_with_species(ends["sink"], species),
                },
                "representation": fit,
            }
        )
    return out


def _state_with_species(state: dict[str, Any], species: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "composition_hash": state["composition_hash"],
        "kind": state["kind"],
        "participants": [
            {"species_entry_ref": ref, "stoichiometry": n, "smiles": species.get(ref, {}).get("smiles")}
            for ref, n in state["participants"]
        ],
    }


def _native_collisions(members: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Channels of the node that share both endpoints. Reported; never summed."""
    seen: dict[tuple[str, str], list[str]] = {}
    for member in members:
        ends = member["channel"]
        seen.setdefault((ends["source"]["composition_hash"], ends["sink"]["composition_hash"]), []).append(member["chosen"])
    return [{"kinetics_refs": refs} for refs in seen.values() if len(refs) > 1]


# ---------------------------------------------------------------------------
# The entry point
# ---------------------------------------------------------------------------


def export_selected(
    session: Session, *, network_ref: str, manifest: dict[str, Any], choice: ExportChoice, require_snapshot: bool = True
) -> dict[str, Any]:
    """Verify the manifest and the choice, then serialise. Read-only; see the module docstring.

    :raises CodedValueError: ``network_export_manifest_invalid`` (incomplete, another network's, or does not
        replay), ``network_export_manifest_stale`` (not what the server holds now),
        ``network_export_choice_not_allowed``, ``network_export_representation_choice_invalid`` or
        ``network_export_unsupported_form``; every one leaves nothing exported.
    """
    if choice.format not in FORMATS:
        raise ValueError(f"format must be one of {list(FORMATS)}")
    if not isinstance(manifest, dict):
        raise _refuse(CODE_MANIFEST_INVALID, "the submitted manifest is not a document.", reason="not_a_document")
    _check_complete(manifest, network_ref)
    _replay(manifest)
    _verify_against_server(session, manifest, require_snapshot=require_snapshot)
    administrative = _check_choice(manifest, choice)
    solve_ref, members = _members(manifest, choice.node_ref)
    if not members:
        raise _refuse(
            CODE_CHOICE_NOT_ALLOWED, "the chosen node is not an eligible node of this selection.",
            reason="not_an_eligible_node",
        )
    chosen = _check_representations(members, choice)
    fits = _load_fits(session, network_ref, solve_ref, list(chosen.values()))
    for member in members:
        member["chosen"] = chosen[member["determination_ref"]]
        member["channel"] = _endpoints(manifest, member["channel_key"])
    refs = {ref for member in members for end in ("source", "sink") for ref, _ in member["channel"][end]["participants"]}
    species = _species_content(session, refs)
    assumptions = [
        "Forward direction only: the channel is directed, and no reverse coefficient is derived from reversibility.",
        "No thermodynamics are written, so no reverse or equilibrium assumption is made.",
        "Exactly one representation per output; alternatives are neither added nor written as DUPLICATE.",
    ]
    result: dict[str, Any] = {
        "format": choice.format,
        "network_ref": network_ref,
        "node_ref": choice.node_ref,
        "solve_ref": solve_ref,
        "selection_basis": "administrative_choice" if administrative else manifest["decision"]["outcome"],
        "administrative": administrative,
        "provenance": {
            "manifest_digest": manifest["digest"],
            "policy": manifest["policy"],
            "request": {k: manifest["request"][k] for k in ("scope", "coefficient_basis", "temperature_min_k",
                                                              "temperature_max_k", "pressure_min_bar", "pressure_max_bar")},
            "snapshot_isolation": manifest["snapshot_isolation"] if "snapshot_isolation" in manifest else None,
        },
        "assumptions": assumptions,
        "members": _native_members(members, fits, species),
        "files": None,
        "equation_collisions": [],
    }
    if choice.format == "chemkin":
        files, collisions = _chemkin_files(members, fits, manifest, species, choice)
        result["files"] = files
        result["equation_collisions"] = collisions
        result["assumptions"].append("Pressure is written in atm (CHEMKIN) from the stored bar; energies in " + choice.energy_units + ".")
    else:
        result["equation_collisions"] = _native_collisions(members)
    return result


__all__ = [
    "CODE_CHOICE_NOT_ALLOWED", "CODE_MANIFEST_INVALID", "CODE_MANIFEST_STALE", "CODE_REPRESENTATION_CHOICE",
    "CODE_UNSUPPORTED_FORM", "ExportChoice", "export_selected",
]
