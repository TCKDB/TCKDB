"""Fetch candidate species names from PubChem into a review file. Run by an operator, never by the API.

Species come from a JSON list (``--species``) or are read from an instance's public species browse
(``--from-api``, GET only). Only InChIKeys are sent to PubChem, at most 4 requests a second.

    python scripts/names/fetch_pubchem_names.py --from-api https://tckdb.example.org --out names_review.json
    python scripts/names/fetch_pubchem_names.py --species species.json --out names_review.json

Run from ``backend/`` with ``PYTHONPATH=.``. Exit codes: 0 done (see the ``skipped`` section), 2 bad arguments or input.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.importers.pubchem_names.fetch import SPECIES_LIST_FORMAT, PubChemClient, fetch_to_file  # noqa: E402
from app.importers.pubchem_names.species_list import fetch_species_list  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fetch_pubchem_names", description=__doc__.split("\n\n")[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--species", type=Path, help="JSON species list (species_ref, inchi_key, ...)")
    source.add_argument("--from-api", metavar="BASE_URL", help="read the species list from this TCKDB instance")
    parser.add_argument("--save-species", type=Path, help="with --from-api: also write the species list here")
    parser.add_argument("--out", type=Path, required=True, help="the review file to write")
    parser.add_argument("--limit", type=int, help="look up only the first N species (a trial run)")
    args = parser.parse_args(argv)

    try:
        if args.from_api:
            document = fetch_species_list(args.from_api)
            if args.save_species:
                args.save_species.write_text(json.dumps(document, indent=2) + "\n")
        else:
            document = json.loads(args.species.read_text())
    except (OSError, ValueError) as exc:
        print(f"cannot read the species list: {exc}", file=sys.stderr)
        return 2
    species = document.get("species") if isinstance(document, dict) else document
    if not isinstance(species, list):
        print(f"the species list must be a JSON list or a {SPECIES_LIST_FORMAT} document", file=sys.stderr)
        return 2
    if args.limit is not None:
        species = species[: args.limit]

    earlier = document.get("skipped", []) if isinstance(document, dict) else []
    client = PubChemClient()
    review = fetch_to_file(species, args.out, client, earlier_skipped=earlier if isinstance(earlier, list) else [])
    print(f"{len(review['rows'])} names, {len(review['skipped'])} skipped, {client.requests_made} requests -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
