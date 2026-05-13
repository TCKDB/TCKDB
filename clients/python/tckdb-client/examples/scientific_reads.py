"""Runnable example: query TCKDB scientifically without knowing entry ids first.

This script exercises the chemistry-first scientific read methods on
``TCKDBClient``. It is intentionally educational — no ranking,
reuse-policy, or "best" decisions are applied client-side; everything
the script prints comes straight from the backend's documented
deterministic ordering.

Phase B/C: each response carries public ``*_ref`` handles alongside the
historical integer ``*_id`` fields. Human-readable output prints refs
first; raw JSON (``--json``) is untouched. Follow-up reads
(``get_reaction_full``, ``get_species_thermo``, repeated
``search_species_calculations``) chain on the returned refs.

Usage::

    python examples/scientific_reads.py \\
        --base-url http://127.0.0.1:8010/api/v1 \\
        --smiles 'C[CH2]'

    python examples/scientific_reads.py \\
        --base-url http://127.0.0.1:8010/api/v1 \\
        --reactant '[CH3]' --reactant 'c1ccccc1' \\
        --product 'CH4' --product '[c]1ccccc1'

    python examples/scientific_reads.py \\
        --base-url http://127.0.0.1:8010/api/v1 \\
        --smiles 'CCO' \\
        --level-of-theory-ref 'lot_...' \\
        --json

If ``--reactant`` and ``--product`` are both supplied (one or more
each), the reaction discovery + chemistry-first kinetics search runs;
otherwise only the species-side queries run. Empty results are
reported with a friendly message.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from tckdb_client import (
    TCKDBAuthenticationError,
    TCKDBClient,
    TCKDBConnectionError,
    TCKDBForbiddenError,
    TCKDBHTTPError,
    TCKDBValidationError,
)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8010/api/v1",
        help=(
            "TCKDB API base URL including /api/v1 "
            "(default: http://127.0.0.1:8010/api/v1)."
        ),
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="API key for authenticated deployments (optional).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="Per-request timeout in seconds (default: 30).",
    )
    parser.add_argument(
        "--smiles",
        default="C[CH2]",
        help=(
            "Species SMILES to query for thermo / species-calculations "
            "(default: 'C[CH2]')."
        ),
    )
    parser.add_argument(
        "--reactant",
        action="append",
        default=[],
        help=(
            "Reaction reactant SMILES; pass multiple times for multiple "
            "reactants. Reaction queries only run when at least one "
            "reactant AND one product are supplied."
        ),
    )
    parser.add_argument(
        "--product",
        action="append",
        default=[],
        help=(
            "Reaction product SMILES; pass multiple times for multiple "
            "products. See --reactant."
        ),
    )
    parser.add_argument(
        "--temperature-min",
        type=float,
        default=300.0,
        help="Lower bound for temperature_coverage filter (Kelvin).",
    )
    parser.add_argument(
        "--temperature-max",
        type=float,
        default=2000.0,
        help="Upper bound for temperature_coverage filter (Kelvin).",
    )
    parser.add_argument(
        "--level-of-theory-id",
        type=int,
        default=None,
        help=(
            "Filter species-calculations search by Calculation.lot_id "
            "(direct LoT match by integer id; compatibility-window form)."
        ),
    )
    parser.add_argument(
        "--level-of-theory-ref",
        default=None,
        help=(
            "Filter species-calculations search by level_of_theory_ref "
            "(public LoT handle, e.g. 'lot_...'). Preferred over --level-of-theory-id."
        ),
    )
    parser.add_argument(
        "--include-internal-ids",
        action="store_true",
        help=(
            "Append 'internal_ids' to every include= list so the backend "
            "may restore the legacy integer ID shape. Only effective when "
            "the deployment sets ALLOW_PUBLIC_INTERNAL_IDS=true; otherwise "
            "the token is silently dropped and refs remain the only handles."
        ),
    )
    parser.add_argument(
        "--only",
        default=None,
        help=(
            "Comma-separated list of sections to run. Allowed values: "
            f"{', '.join(sorted(ALL_SECTIONS))}, all. Default: all."
        ),
    )
    parser.add_argument(
        "--skip",
        default=None,
        help=(
            "Comma-separated list of sections to skip (applied after --only). "
            "Same allowed values as --only."
        ),
    )
    parser.add_argument(
        "--no-followups",
        action="store_true",
        help=(
            "Disable every follow-up section (thermo-detail, lot-followup, "
            "geometry, full). Equivalent to subtracting FOLLOWUP_SECTIONS "
            "from the resolved section set."
        ),
    )
    parser.add_argument(
        "--calculation-type",
        default="sp",
        help=(
            "Backend calculation_type filter for search_species_calculations. "
            "Default: sp. Common values: sp, opt, freq, scan, irc, neb, "
            "path_search, conf. The backend validates; this CLI does not."
        ),
    )
    parser.add_argument(
        "--ranking",
        default="lowest_energy",
        help=(
            "Ranking knob for search_species_calculations. Default: "
            "lowest_energy. If unsupported for the chosen --calculation-type "
            "the backend will return its normal validation error."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print raw JSON responses instead of text summaries.",
    )
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Section selection
# ---------------------------------------------------------------------------


PRIMARY_SECTIONS: frozenset[str] = frozenset(
    {
        "species",
        "thermo",
        "calculations",
        "reactions",
        "kinetics",
    }
)

FOLLOWUP_SECTIONS: frozenset[str] = frozenset(
    {
        "thermo-detail",
        "lot-followup",
        "geometry",
        "full",
    }
)

ALL_SECTIONS: frozenset[str] = PRIMARY_SECTIONS | FOLLOWUP_SECTIONS


def parse_section_set(value: str | None) -> set[str]:
    """Parse a comma-separated section list into a set.

    ``None`` / empty input returns the empty set. The literal ``"all"``
    expands to :data:`ALL_SECTIONS`. Unknown section names raise
    ``argparse.ArgumentTypeError`` with the list of legal names so the
    error surfaces cleanly in CLI usage.
    """
    if value is None or not value.strip():
        return set()
    tokens = {tok.strip() for tok in value.split(",") if tok.strip()}
    if "all" in tokens:
        tokens.discard("all")
        tokens |= ALL_SECTIONS
    bad = tokens - ALL_SECTIONS
    if bad:
        raise argparse.ArgumentTypeError(
            f"unknown section(s): {sorted(bad)!r}; "
            f"legal sections: {sorted(ALL_SECTIONS) + ['all']!r}"
        )
    return tokens


def resolve_sections(args: argparse.Namespace) -> set[str]:
    """Resolve the effective section set from CLI flags.

    Rule order:

    1. ``--only`` selects an initial set (default: every section).
    2. ``--no-followups`` removes the follow-up subset.
    3. ``--skip`` removes any further sections explicitly listed.

    The result is a subset of :data:`ALL_SECTIONS`. Reaction-side
    sections still skip at runtime when no ``--reactant``/``--product``
    is supplied — that's enforced by the per-section guards in main,
    not by this helper.
    """
    selected = parse_section_set(getattr(args, "only", None)) or set(ALL_SECTIONS)
    if getattr(args, "no_followups", False):
        selected -= FOLLOWUP_SECTIONS
    selected -= parse_section_set(getattr(args, "skip", None))
    return selected


def should_run(section: str, selected: set[str]) -> bool:
    """Return True iff *section* is in the resolved selection."""
    return section in selected


def _includes(args: argparse.Namespace, *base: str) -> list[str]:
    """Build an ``include=`` list for a request.

    Appends ``"internal_ids"`` when the caller passed
    ``--include-internal-ids`` so the backend can return the legacy
    integer-ID shape (subject to its own ``ALLOW_PUBLIC_INTERNAL_IDS``
    setting). Idempotent on duplicate tokens.
    """
    out = list(base)
    if args.include_internal_ids and "internal_ids" not in out:
        out.append("internal_ids")
    return out


# ---------------------------------------------------------------------------
# Pretty printers
# ---------------------------------------------------------------------------


def _ref_id(
    record: dict[str, Any] | None,
    ref_key: str,
    id_key: str,
) -> str:
    """Format ``ref_key=... id_key=...``, leading with the ref when present.

    Returns the empty string if both fields are missing. Used everywhere
    the script renders an identifier so refs always come first in
    human-readable output, with the integer id printed second as a
    compatibility/debug aid.
    """
    record = record or {}
    ref = record.get(ref_key)
    rid = record.get(id_key)
    parts: list[str] = []
    if ref:
        parts.append(f"{ref_key}={ref}")
    if rid is not None:
        parts.append(f"{id_key}={rid}")
    return " ".join(parts)


def _print_envelope_summary(label: str, response: dict[str, Any]) -> None:
    pagination = response.get("pagination") or {}
    review = response.get("review_summary") or {}
    total = pagination.get("total", 0)
    returned = pagination.get("returned", len(response.get("records", []) or []))
    print(f"  {label}: returned={returned}, pre-collapse total={total}")
    print(
        "    review counts:",
        f"approved={review.get('approved', 0)},",
        f"under_review={review.get('under_review', 0)},",
        f"not_reviewed={review.get('not_reviewed', 0)},",
        f"deprecated={review.get('deprecated', 0)},",
        f"rejected={review.get('rejected', 0)}",
    )


def _print_species_record(rec: dict[str, Any]) -> None:
    print(
        f"  {_ref_id(rec, 'species_ref', 'species_id')}",
        f"smiles={rec.get('canonical_smiles')!r}",
        f"inchi_key={rec.get('inchi_key')}",
        f"charge={rec.get('charge')}",
        f"multiplicity={rec.get('multiplicity')}",
    )
    for entry in rec.get("entries") or []:
        review = entry.get("review") or {}
        avail = entry.get("availability") or {}
        print(
            f"    {_ref_id(entry, 'species_entry_ref', 'species_entry_id')}",
            f"kind={entry.get('species_entry_kind')}",
            f"electronic_state={entry.get('electronic_state_kind')}",
            f"review={review.get('status')}",
        )
        print(
            "      availability:",
            f"thermo={avail.get('has_thermo')},",
            f"statmech={avail.get('has_statmech')},",
            f"transport={avail.get('has_transport')},",
            f"conformers={avail.get('has_conformers')},",
            f"calculations={avail.get('calculation_count')}",
        )


def _print_thermo_record(rec: dict[str, Any]) -> None:
    species = rec.get("species") or {}
    thermo = rec.get("thermo") or {}
    review = thermo.get("review") or {}
    coverage = thermo.get("temperature_coverage") or {}
    evidence = thermo.get("evidence_completeness") or {}
    print(
        f"    {_ref_id(species, 'species_entry_ref', 'species_entry_id')}",
        f"{_ref_id(thermo, 'thermo_ref', 'thermo_id')}",
        f"model_kind={thermo.get('model_kind')}",
        f"review={review.get('status')}",
    )
    print(
        f"      H298(kJ/mol)={thermo.get('h298_kj_mol')}",
        f"S298(J/mol/K)={thermo.get('s298_j_mol_k')}",
    )
    print(
        f"      temperature_coverage:",
        f"covers={coverage.get('covers_requested_range')},",
        f"extrapolation_K={coverage.get('extrapolation_distance_k')}",
    )
    print(
        f"      evidence: score={evidence.get('score')}/{evidence.get('max')}"
    )


def _print_kinetics_record(rec: dict[str, Any]) -> None:
    reaction = rec.get("reaction") or {}
    kinetics = rec.get("kinetics") or {}
    review = kinetics.get("review") or {}
    coverage = kinetics.get("temperature_coverage") or {}
    provenance = kinetics.get("provenance") or {}
    parameters = kinetics.get("parameters") or {}
    print(
        f"    {_ref_id(reaction, 'reaction_entry_ref', 'reaction_entry_id')}",
        f"matched_direction={reaction.get('matched_direction')}",
        f"{_ref_id(kinetics, 'kinetics_ref', 'kinetics_id')}",
        f"origin={kinetics.get('scientific_origin')}",
    )
    print(
        f"      model_kind={kinetics.get('model_kind')}",
        f"A={parameters.get('A')} ({parameters.get('A_units')})",
        f"n={parameters.get('n')}",
        f"Ea(kJ/mol)={parameters.get('Ea_kj_mol')}",
        f"review={review.get('status')}",
    )
    print(
        f"      temperature_coverage:",
        f"covers={coverage.get('covers_requested_range')},",
        f"extrapolation_K={coverage.get('extrapolation_distance_k')}",
    )
    ts_entry_id = provenance.get("transition_state_entry_id")
    if ts_entry_id is None and not provenance.get("transition_state_entry_ref"):
        print(
            "      provenance: non-TS-backed (TS-chain provenance fields are null)"
        )
    else:
        print(
            f"      provenance:",
            f"{_ref_id(provenance, 'transition_state_entry_ref', 'transition_state_entry_id')},",
            f"ts_opt[{_ref_id(provenance, 'ts_opt_calculation_ref', 'ts_opt_calculation_id')}],",
            f"ts_freq[{_ref_id(provenance, 'ts_freq_calculation_ref', 'ts_freq_calculation_id')}],",
            f"ts_sp[{_ref_id(provenance, 'ts_sp_calculation_ref', 'ts_sp_calculation_id')}]",
        )


def _print_species_calc_record(rec: dict[str, Any]) -> None:
    species = rec.get("species") or {}
    calc = rec.get("calculation") or {}
    energy = rec.get("energy") or {}
    lot = rec.get("level_of_theory") or {}
    sw = rec.get("software_release") or {}
    conformer = rec.get("conformer")
    geom = rec.get("geometry") or {}
    val = rec.get("validation") or {}
    print(
        f"    {_ref_id(species, 'species_entry_ref', 'species_entry_id')}",
        f"{_ref_id(calc, 'calculation_ref', 'calculation_id')}",
        f"type={calc.get('calculation_type')}",
        f"quality={calc.get('calculation_quality')}",
        f"review={(calc.get('review') or {}).get('status')}",
    )
    if energy:
        print(
            f"      energy_hartree={energy.get('energy_hartree')}",
            f"({energy.get('energy_kind')})",
        )
    if lot:
        print(
            f"      LoT: method={lot.get('method')}",
            f"basis={lot.get('basis')}",
            f"{_ref_id(lot, 'level_of_theory_ref', 'level_of_theory_id')}",
        )
    if sw:
        print(
            f"      software:",
            f"{sw.get('software')} {sw.get('version')}",
        )
    if conformer:
        print(
            f"      conformer:",
            f"{_ref_id(conformer, 'conformer_observation_ref', 'conformer_observation_id')},",
            f"{_ref_id(conformer, 'conformer_group_ref', 'conformer_group_id')},",
            f"label={conformer.get('conformer_group_label')!r}",
        )
    else:
        print("      conformer: (none — calculation has no conformer observation)")
    primary_geom_ref = geom.get("primary_output_geometry_ref")
    primary_geom_id = geom.get("primary_output_geometry_id")
    geom_parts = []
    if primary_geom_ref:
        geom_parts.append(f"primary_ref={primary_geom_ref}")
    if primary_geom_id is not None:
        geom_parts.append(f"primary_id={primary_geom_id}")
    print(
        f"      geometry:",
        ", ".join(geom_parts) or "none,",
        f"role={geom.get('primary_output_geometry_role')}",
    )
    geom_val = (val.get("geometry_validation") or {}).get("status", "not_present")
    scf_val = (val.get("scf_stability") or {}).get("status", "not_present")
    print(f"      validation: geometry={geom_val} scf={scf_val}")


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------


def _maybe_dump_json(args: argparse.Namespace, label: str, payload: Any) -> None:
    if args.json:
        print(f"\n--- {label} (raw JSON) ---")
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def run_species_search(client: TCKDBClient, args: argparse.Namespace) -> None:
    print(f"\n[search_species] smiles={args.smiles!r}")
    response = client.search_species(
        smiles=args.smiles, include=_includes(args, "review"), collapse="all"
    )
    _maybe_dump_json(args, "search_species", response)
    if not args.json:
        _print_envelope_summary("search_species", response)
        records = response.get("records") or []
        if not records:
            print("  No species records found for this SMILES.")
            return
        for rec in records:
            _print_species_record(rec)


def run_thermo_search(
    client: TCKDBClient, args: argparse.Namespace
) -> dict | None:
    print(
        f"\n[search_thermo] smiles={args.smiles!r}"
        f" T={args.temperature_min:g}..{args.temperature_max:g} K"
        f" collapse=first"
    )
    response = client.search_thermo(
        smiles=args.smiles,
        temperature_min=args.temperature_min,
        temperature_max=args.temperature_max,
        collapse="first",
        include=_includes(args, "provenance", "review"),
    )
    _maybe_dump_json(args, "search_thermo", response)
    if not args.json:
        _print_envelope_summary("search_thermo (collapse=first)", response)
        records = response.get("records") or []
        if not records:
            print("  No thermo records found for this species query.")
            return response
        for rec in records:
            _print_thermo_record(rec)
    return response


def run_thermo_detail_followup(
    client: TCKDBClient,
    args: argparse.Namespace,
    thermo_response: dict | None,
) -> None:
    """Re-fetch thermo for the same species_entry using its public ref.

    Demonstrates that the entry-id detail endpoint accepts a public ref
    as the path handle. Falls back to the integer id when the response
    pre-dates Phase B and has no ``species_entry_ref`` field.
    """
    if not thermo_response:
        return
    records = thermo_response.get("records") or []
    if not records:
        return
    species = (records[0].get("species") or {})
    species_entry_handle = (
        species.get("species_entry_ref")
        or species.get("species_entry_id")
    )
    if species_entry_handle is None:
        return

    response = client.get_species_thermo(
        species_entry_id=species_entry_handle,
        temperature_min=args.temperature_min,
        temperature_max=args.temperature_max,
        collapse="first",
        include=_includes(args, "provenance", "review"),
    )
    _maybe_dump_json(args, "get_species_thermo_by_ref", response)
    if not args.json:
        returned = (response.get("pagination") or {}).get("returned", 0)
        total = (response.get("pagination") or {}).get("total", 0)
        print(
            f"\n[get_species_thermo] follow-up using species_entry_id={species_entry_handle}"
        )
        print(f"  returned={returned}, total={total}")


def run_species_calculations_search(
    client: TCKDBClient, args: argparse.Namespace
) -> dict | None:
    label_extras = []
    if args.level_of_theory_id is not None:
        label_extras.append(f"level_of_theory_id={args.level_of_theory_id}")
    if args.level_of_theory_ref is not None:
        label_extras.append(f"level_of_theory_ref={args.level_of_theory_ref}")
    print(
        f"\n[search_species_calculations] smiles={args.smiles!r}"
        f" calculation_type={args.calculation_type} ranking={args.ranking}"
        f" collapse=first "
        + " ".join(label_extras)
    )
    response = client.search_species_calculations(
        smiles=args.smiles,
        calculation_type=args.calculation_type,
        level_of_theory_id=args.level_of_theory_id,
        level_of_theory_ref=args.level_of_theory_ref,
        ranking=args.ranking,
        collapse="first",
        include=_includes(args, "provenance", "conformers", "review"),
    )
    _maybe_dump_json(args, "search_species_calculations", response)
    if not args.json:
        _print_envelope_summary(
            "search_species_calculations (collapse=first)", response
        )
        records = response.get("records") or []
        if not records:
            print(
                f"  No {args.calculation_type} calculations found for this "
                "species (or no record satisfies the ranking criterion)."
            )
            return response
        for rec in records:
            _print_species_calc_record(rec)
    return response


def run_lot_ref_followup(
    client: TCKDBClient,
    args: argparse.Namespace,
    calcs_response: dict | None,
) -> None:
    """Re-run species-calculations search filtering by the discovered LoT ref.

    Demonstrates that ``level_of_theory_ref`` works wherever
    ``level_of_theory_id`` works. Skips silently when the previous call
    returned nothing or pre-dates Phase B (no ``level_of_theory_ref``).
    """
    if not calcs_response:
        return
    records = calcs_response.get("records") or []
    if not records:
        return
    lot = records[0].get("level_of_theory") or {}
    lot_ref = lot.get("level_of_theory_ref")
    if not lot_ref:
        return
    # Avoid trivially re-running the same query if the user already
    # supplied --level-of-theory-ref pointing at this exact LoT.
    if args.level_of_theory_ref == lot_ref:
        return

    response = client.search_species_calculations(
        smiles=args.smiles,
        calculation_type=args.calculation_type,
        level_of_theory_ref=lot_ref,
        ranking=args.ranking,
        collapse="first",
        include=_includes(args, "provenance", "conformers", "review"),
    )
    _maybe_dump_json(
        args, "search_species_calculations_by_level_of_theory_ref", response
    )
    if not args.json:
        returned = (response.get("pagination") or {}).get("returned", 0)
        total = (response.get("pagination") or {}).get("total", 0)
        print(
            f"\n[search_species_calculations] follow-up using level_of_theory_ref={lot_ref}"
        )
        print(f"  returned={returned}, total={total}")


def run_geometry_followup(
    client: TCKDBClient,
    args: argparse.Namespace,
    calcs_response: dict | None,
) -> None:
    """Follow a geometry ref from species-calculations into the
    detail endpoint and surface the coordinate payload.

    Prefers ``primary_output_geometry_ref`` (set for opt) and falls
    back to the first ``input_geometries[*].geometry_ref`` (set for
    sp/freq/etc). Skips silently when no geometry ref is available.
    """
    if not calcs_response:
        return
    records = calcs_response.get("records") or []
    if not records:
        return
    geom_block = records[0].get("geometry") or {}
    geometry_handle = geom_block.get("primary_output_geometry_ref")
    if not geometry_handle:
        inputs = geom_block.get("input_geometries") or []
        if inputs:
            geometry_handle = (inputs[0] or {}).get("geometry_ref")
    if not geometry_handle:
        return

    response = client.get_geometry(
        geometry_handle, include=_includes(args, "provenance")
    )
    _maybe_dump_json(args, "get_geometry_by_ref", response)
    if not args.json:
        natoms = response.get("natoms") or len(response.get("symbols") or [])
        units = response.get("coordinate_units")
        symbols = response.get("symbols") or []
        coords = response.get("coords") or []
        print(
            f"\n[get_geometry] geometry_ref={geometry_handle}"
        )
        print(f"  atoms={natoms} units={units}")
        for sym, xyz in list(zip(symbols, coords))[:3]:
            x, y, z = (xyz + [0.0, 0.0, 0.0])[:3]
            print(f"    {sym:<2} {x:>10.3f} {y:>10.3f} {z:>10.3f}")
        if natoms and natoms > 3:
            print(f"    … ({natoms - 3} more)")


def run_reaction_search(
    client: TCKDBClient, args: argparse.Namespace
) -> dict | None:
    if not (args.reactant and args.product):
        return None
    print(
        f"\n[search_reactions] reactants={args.reactant} products={args.product}"
        f" direction=either"
    )
    response = client.search_reactions(
        reactants=args.reactant,
        products=args.product,
        direction="either",
        include=_includes(args, "review"),
        collapse="all",
    )
    _maybe_dump_json(args, "search_reactions", response)
    if not args.json:
        _print_envelope_summary("search_reactions", response)
        records = response.get("records") or []
        if not records:
            print("  No reaction records found for the supplied reactants/products.")
            return response
        for rec in records:
            review = (rec.get("review") or {}).get("status")
            avail = rec.get("availability") or {}
            print(
                f"  {_ref_id(rec, 'reaction_entry_ref', 'reaction_entry_id')}",
                f"matched_direction={rec.get('matched_direction')}",
                f"family={rec.get('family')}",
                f"review={review}",
            )
            print(
                f"    availability: kinetics={avail.get('has_kinetics')},",
                f"transition_state={avail.get('has_transition_state')},",
                f"path_search={avail.get('has_path_search')},",
                f"kinetics_count={avail.get('kinetics_count')}",
            )
    return response


def run_kinetics_search(
    client: TCKDBClient, args: argparse.Namespace
) -> dict | None:
    if not (args.reactant and args.product):
        return None
    print(
        f"\n[search_kinetics] reactants={args.reactant} products={args.product}"
        f" direction=either T={args.temperature_min:g}..{args.temperature_max:g} K"
        f" collapse=first"
    )
    response = client.search_kinetics(
        reactants=args.reactant,
        products=args.product,
        direction="either",
        temperature_min=args.temperature_min,
        temperature_max=args.temperature_max,
        collapse="first",
        include=_includes(args, "provenance", "review"),
    )
    _maybe_dump_json(args, "search_kinetics", response)
    if not args.json:
        _print_envelope_summary("search_kinetics (collapse=first)", response)
        records = response.get("records") or []
        if not records:
            print("  No kinetics records found for this reaction query.")
            return response
        for rec in records:
            _print_kinetics_record(rec)
    return response


def run_full_provenance_followup(
    client: TCKDBClient, args: argparse.Namespace, kinetics_response: dict | None
) -> None:
    """Composite ``/full`` lookup chained from a search_kinetics result.

    Prefers the public ``reaction_entry_ref`` returned by Phase B
    responses; falls back to the integer ``reaction_entry_id`` so the
    script remains useful against older deployments.
    """
    if not kinetics_response:
        return
    records = kinetics_response.get("records") or []
    if not records:
        return
    reaction = records[0].get("reaction") or {}
    reaction_entry_handle = (
        reaction.get("reaction_entry_ref")
        or reaction.get("reaction_entry_id")
    )
    if reaction_entry_handle is None:
        return

    print(
        f"\n[get_reaction_full] reaction_entry_id={reaction_entry_handle}"
        " (follow-up using ref returned by search_kinetics)"
    )
    response = client.get_reaction_full(
        reaction_entry_id=reaction_entry_handle,
        include=_includes(args, "species", "kinetics", "transition_states", "calculations", "review"),
        include_review="full",
    )
    _maybe_dump_json(args, "get_reaction_full", response)
    if not args.json:
        entry = response.get("reaction_entry") or {}
        review = response.get("review_summary") or {}
        # ``reaction_entry`` uses ``id`` rather than ``reaction_entry_id``;
        # the ref sibling is ``reaction_entry_ref`` per Phase B.
        ref = entry.get("reaction_entry_ref")
        rid = entry.get("id")
        label_parts = []
        if ref:
            label_parts.append(f"reaction_entry_ref={ref}")
        if rid is not None:
            label_parts.append(f"reaction_entry_id={rid}")
        print(
            "  " + " ".join(label_parts),
            f"equation={entry.get('equation')!r}",
            f"reversible={entry.get('reversible')}",
        )
        print(
            "  joined review_summary:",
            f"approved={review.get('approved', 0)},",
            f"under_review={review.get('under_review', 0)},",
            f"not_reviewed={review.get('not_reviewed', 0)},",
            f"total={review.get('total', 0)}",
        )
        ts_records = response.get("transition_states") or []
        print(f"  transition_states populated: {len(ts_records)}")


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def _print_http_error(label: str, exc: TCKDBHTTPError) -> None:
    print(
        f"\n[{label}] HTTP error:",
        f"status_code={exc.status_code},",
        f"code={getattr(exc, 'code', None)!r},",
        f"detail={getattr(exc, 'detail', None)!r}",
        file=sys.stderr,
    )


def _print_connection_error(label: str, exc: TCKDBConnectionError) -> None:
    print(f"\n[{label}] connection/timeout error: {exc}", file=sys.stderr)


def _safely_run(label: str, fn) -> Any:
    """Run ``fn``; print structured client errors and return None on failure.

    Returning None lets the script continue with the next operation rather
    than aborting on the first failure, which is more useful when a
    deployment has partial data.
    """
    try:
        return fn()
    except TCKDBValidationError as exc:
        _print_http_error(label, exc)
    except TCKDBAuthenticationError as exc:
        _print_http_error(label, exc)
    except TCKDBForbiddenError as exc:
        _print_http_error(label, exc)
    except TCKDBHTTPError as exc:
        _print_http_error(label, exc)
    except TCKDBConnectionError as exc:
        _print_connection_error(label, exc)
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    selected = resolve_sections(args)

    print("TCKDB scientific read example")
    print(f"  base_url    : {args.base_url}")
    print(f"  api_key     : {'(set)' if args.api_key else '(none)'}")
    print(f"  smiles      : {args.smiles!r}")
    if args.reactant or args.product:
        print(f"  reactants   : {args.reactant}")
        print(f"  products    : {args.product}")
    print(f"  sections    : {', '.join(sorted(selected)) or '(none)'}")
    if args.no_followups:
        print("  followups   : disabled (--no-followups)")
    print(
        "  hint        : Use --json to print raw machine-readable responses."
    )
    print(
        "                Refs are public handles; integer IDs are "
        "compatibility/debug fields."
    )
    print(
        "                Pass --include-internal-ids to request the legacy "
        "id-bearing shape (effective only when the deployment allows it)."
    )
    print(
        "                Narrow output with --only / --skip / --no-followups."
    )

    client = TCKDBClient(
        base_url=args.base_url,
        api_key=args.api_key,
        timeout=args.timeout,
    )

    try:
        if should_run("species", selected):
            _safely_run(
                "search_species",
                lambda: run_species_search(client, args),
            )

        thermo_response: dict | None = None
        if should_run("thermo", selected):
            thermo_response = _safely_run(
                "search_thermo",
                lambda: run_thermo_search(client, args),
            )

        if should_run("thermo-detail", selected):
            if thermo_response is None:
                print(
                    "\nSkipping thermo-detail: search_thermo did not run "
                    "(enable 'thermo' too, or drop '--no-followups')."
                )
            else:
                _safely_run(
                    "get_species_thermo_by_ref",
                    lambda: run_thermo_detail_followup(
                        client, args, thermo_response
                    ),
                )

        calcs_response: dict | None = None
        if should_run("calculations", selected):
            calcs_response = _safely_run(
                "search_species_calculations",
                lambda: run_species_calculations_search(client, args),
            )

        if should_run("lot-followup", selected):
            if calcs_response is None:
                print(
                    "\nSkipping lot-followup: search_species_calculations "
                    "did not run."
                )
            else:
                _safely_run(
                    "search_species_calculations_by_level_of_theory_ref",
                    lambda: run_lot_ref_followup(client, args, calcs_response),
                )

        if should_run("geometry", selected):
            if calcs_response is None:
                print(
                    "\nSkipping geometry: search_species_calculations did "
                    "not run (no geometry ref to follow)."
                )
            else:
                _safely_run(
                    "get_geometry_by_ref",
                    lambda: run_geometry_followup(
                        client, args, calcs_response
                    ),
                )

        if should_run("reactions", selected):
            if not (args.reactant and args.product):
                print(
                    "\nSkipping reactions: no --reactant / --product supplied."
                )
            else:
                _safely_run(
                    "search_reactions",
                    lambda: run_reaction_search(client, args),
                )

        kinetics_response: dict | None = None
        if should_run("kinetics", selected):
            if not (args.reactant and args.product):
                print(
                    "\nSkipping kinetics: no --reactant / --product supplied."
                )
            else:
                kinetics_response = _safely_run(
                    "search_kinetics",
                    lambda: run_kinetics_search(client, args),
                )

        if should_run("full", selected):
            if kinetics_response is None:
                print(
                    "\nSkipping full: search_kinetics did not run (no "
                    "reaction_entry_ref to follow)."
                )
            else:
                _safely_run(
                    "get_reaction_full",
                    lambda: run_full_provenance_followup(
                        client, args, kinetics_response
                    ),
                )
    finally:
        client.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
