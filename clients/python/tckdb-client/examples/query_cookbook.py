"""TCKDB scientific query cookbook — runnable recipe gallery.

Each recipe in this script answers one scientific question against a
live TCKDB deployment using ``tckdb-client``. Recipes are small, named,
and runnable individually via ``--recipe NAME`` so callers can lift a
single block into their own code without dragging in the rest of the
demo.

Usage::

    python examples/query_cookbook.py --recipe list

    python examples/query_cookbook.py --recipe species_search \\
        --smiles "O" --base-url http://127.0.0.1:8000/api/v1

    python examples/query_cookbook.py --recipe all \\
        --smiles "O" --reactant "[CH3]" --product "CH4" \\
        --base-url http://127.0.0.1:8000/api/v1 --json

Conventions:

- All recipes treat **refs as the normal handles**. Integer ids are
  only retrieved when ``--include-internal-ids`` is passed AND the
  deployment sets ``ALLOW_PUBLIC_INTERNAL_IDS=true``.
- Recipes are intentionally short — they print only the few fields a
  real caller would inspect. Pass ``--json`` to get the raw response
  envelope for each recipe.
- Empty results print a friendly message; client errors print the
  ``status_code`` / ``code`` / ``detail`` triple. The script keeps
  going so multi-recipe runs survive partial-data deployments.

See also: ``docs/guides/scientific_query_cookbook.md`` for the
companion prose guide.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Callable

from tckdb_client import (
    TCKDBAuthenticationError,
    TCKDBClient,
    TCKDBConnectionError,
    TCKDBForbiddenError,
    TCKDBHTTPError,
    TCKDBValidationError,
)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000/api/v1",
        help=(
            "TCKDB API base URL including /api/v1 "
            "(default: http://127.0.0.1:8000/api/v1)."
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
        default="O",
        help="Species SMILES for species-side recipes (default: 'O').",
    )
    parser.add_argument(
        "--reactant",
        action="append",
        default=[],
        help=(
            "Reactant SMILES for the kinetics recipe; pass multiple times "
            "for multiple reactants."
        ),
    )
    parser.add_argument(
        "--product",
        action="append",
        default=[],
        help="Product SMILES for the kinetics recipe; pass multiple times.",
    )
    parser.add_argument(
        "--temperature-min",
        type=float,
        default=300.0,
        help="Lower temperature bound (K) for thermo/kinetics recipes.",
    )
    parser.add_argument(
        "--temperature-max",
        type=float,
        default=2000.0,
        help="Upper temperature bound (K) for thermo/kinetics recipes.",
    )
    parser.add_argument(
        "--calculation-type",
        default="sp",
        help="calculation_type filter for the species-calculation recipes.",
    )
    parser.add_argument(
        "--recipe",
        default="all",
        help=(
            "Recipe to run. Use 'list' to print available recipes. Default "
            "'all' runs every recipe (reaction recipes skip silently if no "
            "--reactant/--product is supplied)."
        ),
    )
    parser.add_argument(
        "--include-internal-ids",
        action="store_true",
        help=(
            "Request integer-ID restoration via include=internal_ids. Only "
            "effective when the deployment sets ALLOW_PUBLIC_INTERNAL_IDS=true."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the raw JSON envelope for each recipe.",
    )
    return parser.parse_args(argv)


def _includes(args: argparse.Namespace, *base: str) -> list[str]:
    """Build an include= list; append 'internal_ids' on opt-in."""
    out = list(base)
    if args.include_internal_ids and "internal_ids" not in out:
        out.append("internal_ids")
    return out


def _dump_json(args: argparse.Namespace, label: str, payload: Any) -> None:
    if args.json:
        print(f"\n--- {label} (raw JSON) ---")
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def _print_http_error(label: str, exc: TCKDBHTTPError) -> None:
    print(
        f"\n[{label}] HTTP error:",
        f"status_code={exc.status_code},",
        f"code={getattr(exc, 'code', None)!r},",
        f"detail={getattr(exc, 'detail', None)!r}",
        file=sys.stderr,
    )


def _safely_run(label: str, fn: Callable[[], Any]) -> Any:
    try:
        return fn()
    except (
        TCKDBValidationError,
        TCKDBAuthenticationError,
        TCKDBForbiddenError,
        TCKDBHTTPError,
    ) as exc:
        _print_http_error(label, exc)
    except TCKDBConnectionError as exc:
        print(f"\n[{label}] connection/timeout error: {exc}", file=sys.stderr)
    return None


# ---------------------------------------------------------------------------
# Recipes
#
# Each recipe is a self-contained function that takes (client, args). The
# bodies are deliberately short — copy-paste them into your own script
# and they should run.
# ---------------------------------------------------------------------------


def recipe_species_search(
    client: TCKDBClient, args: argparse.Namespace
) -> dict | None:
    """Q: What species does TCKDB have that match this SMILES?

    Returns the per-species + per-entry shape. Each
    ``species_entry_ref`` is a stable handle suitable for follow-up
    reads.
    """
    print(f"\n=== Recipe 1: species search ===\n  smiles={args.smiles!r}")
    response = client.search_species(
        smiles=args.smiles,
        include=_includes(args, "review"),
        collapse="all",
    )
    _dump_json(args, "species_search", response)
    if not args.json:
        records = response.get("records") or []
        if not records:
            print("  No species found for this SMILES.")
            return response
        for rec in records:
            print(
                f"  species_ref={rec.get('species_ref')}"
                f" smiles={rec.get('canonical_smiles')!r}"
                f" charge={rec.get('charge')}"
                f" multiplicity={rec.get('multiplicity')}"
            )
            for entry in rec.get("entries") or []:
                avail = entry.get("availability") or {}
                review = (entry.get("review") or {}).get("status")
                print(
                    f"    species_entry_ref={entry.get('species_entry_ref')}"
                    f" kind={entry.get('species_entry_kind')}"
                    f" review={review}"
                )
                print(
                    "      availability:",
                    f"thermo={avail.get('has_thermo')},",
                    f"statmech={avail.get('has_statmech')},",
                    f"calculations={avail.get('calculation_count')}",
                )
    return response


def recipe_thermo_search(
    client: TCKDBClient, args: argparse.Namespace
) -> dict | None:
    """Q: Give me the best thermo record for this species across the
    requested temperature range.

    ``collapse="first"`` asks the backend to return the top record
    under its locked sort order (temperature coverage → review rank →
    evidence completeness → recency). Use the returned
    ``species_entry_ref`` for follow-up reads.
    """
    print(
        f"\n=== Recipe 2: thermo search ===\n"
        f"  smiles={args.smiles!r}"
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
    _dump_json(args, "thermo_search", response)
    if not args.json:
        records = response.get("records") or []
        if not records:
            print("  No thermo records for this species/temperature window.")
            return response
        rec = records[0]
        species = rec.get("species") or {}
        thermo = rec.get("thermo") or {}
        cov = thermo.get("temperature_coverage") or {}
        ev = thermo.get("evidence_completeness") or {}
        print(
            f"  species_entry_ref={species.get('species_entry_ref')}"
            f" thermo_ref={thermo.get('thermo_ref')}"
            f" model_kind={thermo.get('model_kind')}"
        )
        print(
            f"  H298={thermo.get('h298_kj_mol')} kJ/mol"
            f" S298={thermo.get('s298_j_mol_k')} J/mol/K"
        )
        print(
            f"  temperature_coverage: covers={cov.get('covers_requested_range')}"
            f" extrapolation_K={cov.get('extrapolation_distance_k')}"
        )
        print(f"  evidence: {ev.get('score')}/{ev.get('max')}")
    return response


def recipe_thermo_provenance(
    client: TCKDBClient, args: argparse.Namespace
) -> dict | None:
    """Q: What freq / SP calculations stand behind this thermo record?

    The thermo response carries a ``provenance`` block. For
    statmech-derived thermo the read service falls back to the
    statmech's own source calculations when the thermo row didn't
    declare its own (see ``docs/audits/thermo_provenance_geometry_audit.md``).
    """
    print(f"\n=== Recipe 3: thermo provenance inspection ===\n  smiles={args.smiles!r}")
    response = client.search_thermo(
        smiles=args.smiles,
        temperature_min=args.temperature_min,
        temperature_max=args.temperature_max,
        collapse="first",
        include=_includes(args, "provenance", "review"),
    )
    _dump_json(args, "thermo_provenance", response)
    if not args.json:
        records = response.get("records") or []
        if not records:
            print("  No thermo records to inspect.")
            return response
        prov = (records[0].get("thermo") or {}).get("provenance") or {}
        lot = prov.get("level_of_theory") or {}
        sw = prov.get("software") or {}
        print(f"  statmech_ref         = {prov.get('statmech_ref')}")
        print(f"  freq_calculation_ref = {prov.get('freq_calculation_ref')}")
        print(f"  sp_calculation_ref   = {prov.get('sp_calculation_ref')}")
        print(
            f"  level_of_theory_ref  = {lot.get('level_of_theory_ref')}"
            f" ({lot.get('method')}/{lot.get('basis')})"
        )
        print(
            f"  software_release_ref = {sw.get('software_release_ref')}"
            f" ({sw.get('software')} {sw.get('version')})"
        )
    return response


def recipe_kinetics_search(
    client: TCKDBClient, args: argparse.Namespace
) -> dict | None:
    """Q: Find kinetics for this elementary reaction.

    Skips silently when no reactants/products are supplied.
    """
    if not (args.reactant and args.product):
        print(
            "\n=== Recipe 4: kinetics search ===\n"
            "  Skipped: no --reactant / --product supplied."
        )
        return None
    print(
        f"\n=== Recipe 4: kinetics search ===\n"
        f"  reactants={args.reactant} products={args.product}"
        f" direction=either"
        f" T={args.temperature_min:g}..{args.temperature_max:g} K"
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
    _dump_json(args, "kinetics_search", response)
    if not args.json:
        records = response.get("records") or []
        if not records:
            print("  No kinetics records for this reaction/temperature window.")
            return response
        rec = records[0]
        reaction = rec.get("reaction") or {}
        kinetics = rec.get("kinetics") or {}
        params = kinetics.get("parameters") or {}
        print(
            f"  reaction_entry_ref={reaction.get('reaction_entry_ref')}"
            f" matched_direction={reaction.get('matched_direction')}"
        )
        print(
            f"  kinetics_ref={kinetics.get('kinetics_ref')}"
            f" model={kinetics.get('model_kind')}"
            f" origin={kinetics.get('scientific_origin')}"
        )
        print(
            f"  A={params.get('A')} ({params.get('A_units')})"
            f" n={params.get('n')}"
            f" Ea={params.get('Ea_kj_mol')} kJ/mol"
        )
    return response


def recipe_species_calculation_search(
    client: TCKDBClient, args: argparse.Namespace
) -> dict | None:
    """Q: What ``--calculation-type`` calculations exist for this species
    at any level of theory?

    The response includes the resolved ``level_of_theory_ref`` per
    record, which feeds into the LoT-scoped recipe below.
    """
    print(
        f"\n=== Recipe 5: species-calculation search ===\n"
        f"  smiles={args.smiles!r} calculation_type={args.calculation_type}"
    )
    response = client.search_species_calculations(
        smiles=args.smiles,
        calculation_type=args.calculation_type,
        collapse="all",
        include=_includes(args, "provenance", "review"),
    )
    _dump_json(args, "species_calculation_search", response)
    if not args.json:
        records = response.get("records") or []
        if not records:
            print(
                f"  No {args.calculation_type} calculations found for this species."
            )
            return response
        for rec in records[:5]:
            calc = rec.get("calculation") or {}
            lot = rec.get("level_of_theory") or {}
            print(
                f"  calculation_ref={calc.get('calculation_ref')}"
                f" type={calc.get('calculation_type')}"
                f" quality={calc.get('calculation_quality')}"
                f" LoT={lot.get('method')}/{lot.get('basis')}"
                f" lot_ref={lot.get('level_of_theory_ref')}"
            )
        if len(records) > 5:
            print(f"  … ({len(records) - 5} more)")
    return response


def recipe_lowest_sp_energy(
    client: TCKDBClient, args: argparse.Namespace
) -> dict | None:
    """Q: For this species, what's the lowest single-point energy at any
    level of theory?

    ``ranking="lowest_energy"`` + ``collapse="first"`` is the
    documented combination. Requires ``calculation_type=sp`` (the
    backend rejects ``lowest_energy`` for non-energy types).
    """
    print(
        f"\n=== Recipe 6: lowest SP energy ===\n  smiles={args.smiles!r}"
    )
    response = client.search_species_calculations(
        smiles=args.smiles,
        calculation_type="sp",
        ranking="lowest_energy",
        collapse="first",
        include=_includes(args, "provenance", "review"),
    )
    _dump_json(args, "lowest_sp_energy", response)
    if not args.json:
        records = response.get("records") or []
        if not records:
            print("  No SP calculations with a populated electronic_energy_hartree.")
            return response
        rec = records[0]
        calc = rec.get("calculation") or {}
        energy = rec.get("energy") or {}
        lot = rec.get("level_of_theory") or {}
        print(
            f"  calculation_ref={calc.get('calculation_ref')}"
            f" energy_hartree={energy.get('energy_hartree')}"
            f" ({energy.get('energy_kind')})"
        )
        print(
            f"  LoT: {lot.get('method')}/{lot.get('basis')}"
            f" lot_ref={lot.get('level_of_theory_ref')}"
        )
    return response


def recipe_optimized_geometry(
    client: TCKDBClient, args: argparse.Namespace
) -> dict | None:
    """Q: For this species, give me an opt calculation and the ref of its
    optimized geometry.

    The species-calculation record for an opt carries
    ``primary_output_geometry_ref`` (set to the ``final`` output
    geometry by upload convention). Use that ref for the geometry
    download recipe.
    """
    print(f"\n=== Recipe 7: optimized geometry retrieval ===\n  smiles={args.smiles!r}")
    response = client.search_species_calculations(
        smiles=args.smiles,
        calculation_type="opt",
        collapse="first",
        include=_includes(args, "provenance", "review"),
    )
    _dump_json(args, "optimized_geometry", response)
    if not args.json:
        records = response.get("records") or []
        if not records:
            print("  No opt calculations for this species.")
            return response
        rec = records[0]
        calc = rec.get("calculation") or {}
        geom = rec.get("geometry") or {}
        print(
            f"  calculation_ref={calc.get('calculation_ref')}"
            f" primary_output_geometry_ref={geom.get('primary_output_geometry_ref')}"
            f" role={geom.get('primary_output_geometry_role')}"
        )
        inputs = [g.get("geometry_ref") for g in geom.get("input_geometries") or []]
        outputs = [g.get("geometry_ref") for g in geom.get("output_geometries") or []]
        print(f"  input_geometries  = {inputs}")
        print(f"  output_geometries = {outputs}")
    return response


def recipe_geometry_download(
    client: TCKDBClient, args: argparse.Namespace
) -> dict | None:
    """Q: Download the actual coordinates for a geometry_ref.

    Resolves the handle in two steps: first find an opt's
    ``primary_output_geometry_ref`` (else fall back to an SP's input
    geometry ref), then call ``get_geometry``. The response gives
    ``symbols`` + ``coords`` plus a small produced-by / used-as-input-by
    provenance summary.
    """
    print(f"\n=== Recipe 8: geometry coordinate download ===\n  smiles={args.smiles!r}")

    # Step 1: find a geometry handle.
    opt_response = client.search_species_calculations(
        smiles=args.smiles,
        calculation_type="opt",
        collapse="first",
    )
    geometry_handle = None
    for rec in opt_response.get("records") or []:
        ref = (rec.get("geometry") or {}).get("primary_output_geometry_ref")
        if ref:
            geometry_handle = ref
            break

    if geometry_handle is None:
        # Fall back to an SP's input geometry.
        sp_response = client.search_species_calculations(
            smiles=args.smiles,
            calculation_type="sp",
            collapse="first",
        )
        for rec in sp_response.get("records") or []:
            inputs = (rec.get("geometry") or {}).get("input_geometries") or []
            if inputs:
                geometry_handle = inputs[0].get("geometry_ref")
                break

    if geometry_handle is None:
        print("  No geometry ref found to download.")
        return None

    # Step 2: fetch the coordinates.
    response = client.get_geometry(
        geometry_handle, include=_includes(args, "provenance")
    )
    _dump_json(args, "geometry_download", response)
    if not args.json:
        natoms = response.get("natoms") or len(response.get("symbols") or [])
        units = response.get("coordinate_units")
        symbols = response.get("symbols") or []
        coords = response.get("coords") or []
        print(
            f"  geometry_ref={response.get('geometry_ref')}"
            f" natoms={natoms} units={units}"
            f" format={response.get('format')}"
        )
        for sym, xyz in list(zip(symbols, coords))[:5]:
            x, y, z = (list(xyz) + [0.0, 0.0, 0.0])[:3]
            print(f"    {sym:<2} {x:>10.3f} {y:>10.3f} {z:>10.3f}")
        if natoms and natoms > 5:
            print(f"    … ({natoms - 5} more)")
        prov = response.get("provenance") or {}
        produced = prov.get("produced_by") or []
        consumed = prov.get("used_as_input_by") or []
        print(
            f"  provenance: produced_by={len(produced)},"
            f" used_as_input_by={len(consumed)}"
        )
    return response


def recipe_chained_followup(
    client: TCKDBClient, args: argparse.Namespace
) -> None:
    """Q: Demonstrate end-to-end ref-based chaining for one species.

    The shape of every hosted workflow:

        chemistry → search → ref → detail read

    Here: SMILES → search_thermo → species_entry_ref →
    get_species_thermo (full detail at that entry).
    """
    print(f"\n=== Recipe 9: chained ref-based follow-up ===\n  smiles={args.smiles!r}")
    summary = client.search_thermo(
        smiles=args.smiles,
        temperature_min=args.temperature_min,
        temperature_max=args.temperature_max,
        collapse="first",
        include=_includes(args, "provenance", "review"),
    )
    _dump_json(args, "chained_followup_search", summary)
    records = summary.get("records") or []
    if not records:
        if not args.json:
            print("  No thermo to follow.")
        return
    species_entry_ref = (
        records[0].get("species") or {}
    ).get("species_entry_ref")
    if not species_entry_ref:
        if not args.json:
            print("  No species_entry_ref in thermo search response.")
        return
    detail = client.get_species_thermo(
        species_entry_id=species_entry_ref,
        temperature_min=args.temperature_min,
        temperature_max=args.temperature_max,
        collapse="first",
        include=_includes(args, "provenance", "review"),
    )
    _dump_json(args, "chained_followup_detail", detail)
    if not args.json:
        returned = (detail.get("pagination") or {}).get("returned", 0)
        total = (detail.get("pagination") or {}).get("total", 0)
        print(
            f"  search → species_entry_ref={species_entry_ref}"
            f" → get_species_thermo: returned={returned}, total={total}"
        )


def recipe_internal_ids(
    client: TCKDBClient, args: argparse.Namespace
) -> dict | None:
    """Q: Show me the integer IDs too (compatibility / local debug).

    By default scientific reads hide integer IDs. This recipe sends
    ``include=internal_ids`` explicitly. The token only takes effect
    when the deployment sets ``ALLOW_PUBLIC_INTERNAL_IDS=true``;
    otherwise it is silently dropped and the response stays
    refs-only — visible in the ``request.include`` echo.
    """
    print(f"\n=== Recipe 10: optional internal_ids usage ===\n  smiles={args.smiles!r}")
    response = client.search_species(
        smiles=args.smiles,
        include=["review", "internal_ids"],  # explicit, ignores --include-internal-ids
        collapse="all",
    )
    _dump_json(args, "internal_ids", response)
    if not args.json:
        echo = response.get("request") or {}
        effective = echo.get("include") or []
        records = response.get("records") or []
        if "internal_ids" in effective:
            note = "ALLOWED — integer IDs visible in this response"
        else:
            note = (
                "silently dropped (ALLOW_PUBLIC_INTERNAL_IDS=false) — "
                "response stays refs-only"
            )
        print(f"  include echo : {effective}")
        print(f"  internal_ids : {note}")
        if records:
            rec = records[0]
            sid = rec.get("species_id")
            sref = rec.get("species_ref")
            print(f"  species_ref  : {sref}")
            print(f"  species_id   : {sid if sid is not None else '(hidden)'}")
    return response


# ---------------------------------------------------------------------------
# Recipe registry + main
# ---------------------------------------------------------------------------


RECIPES: dict[str, Callable[[TCKDBClient, argparse.Namespace], Any]] = {
    "species_search": recipe_species_search,
    "thermo_search": recipe_thermo_search,
    "thermo_provenance": recipe_thermo_provenance,
    "kinetics_search": recipe_kinetics_search,
    "species_calculation_search": recipe_species_calculation_search,
    "lowest_sp_energy": recipe_lowest_sp_energy,
    "optimized_geometry": recipe_optimized_geometry,
    "geometry_download": recipe_geometry_download,
    "chained_followup": recipe_chained_followup,
    "internal_ids": recipe_internal_ids,
}


def _print_recipe_list() -> None:
    print("Available recipes:")
    for name, fn in RECIPES.items():
        doc = (fn.__doc__ or "").strip().splitlines()
        summary = doc[0] if doc else ""
        print(f"  {name:<28} {summary}")
    print("\n  all                          Run every recipe in order.")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.recipe == "list":
        _print_recipe_list()
        return 0

    if args.recipe != "all" and args.recipe not in RECIPES:
        print(
            f"Unknown recipe: {args.recipe!r}. "
            "Use --recipe list to see available recipes.",
            file=sys.stderr,
        )
        return 2

    print("TCKDB scientific query cookbook")
    print(f"  base_url    : {args.base_url}")
    print(f"  api_key     : {'(set)' if args.api_key else '(none)'}")
    print(f"  recipe      : {args.recipe}")
    print(f"  smiles      : {args.smiles!r}")
    if args.reactant or args.product:
        print(f"  reactants   : {args.reactant}")
        print(f"  products    : {args.product}")
    print(
        "  hint        : Refs are public handles; integer IDs are "
        "compatibility/debug fields."
    )

    client = TCKDBClient(
        base_url=args.base_url,
        api_key=args.api_key,
        timeout=args.timeout,
    )

    try:
        if args.recipe == "all":
            for name, fn in RECIPES.items():
                _safely_run(name, lambda fn=fn: fn(client, args))
        else:
            _safely_run(args.recipe, lambda: RECIPES[args.recipe](client, args))
    finally:
        client.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
