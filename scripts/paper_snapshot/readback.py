"""Read a sample of records back from an instance and check them against the sources.

    python scripts/paper_snapshot/readback.py --api-url http://127.0.0.1:8103 --sources my_sources.yml

Meant for a third, empty instance that the snapshot archive was restored into
(``instance.py restore``), but it reads any instance. One sample per deposited
source, chosen the same way every time:

* **E2**: n-butane. The species entry's calculation count equals what the
  sources hold for it; one output log is downloaded **by digest**, its SHA-256
  is recomputed, and it must equal the digest asked for and the digest of a
  file in the E2 source directory.
* **Formic acid model**: HOCHO. Its stored NASA-7 coefficients and temperature
  ranges equal the CHEMKIN file's entry for that species.

A source with no entry in the sources file is skipped, and the result says so.
It registers a throw-away reader account through the open-registration route
(downloads need any authenticated caller).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
for _path in reversed(
    [
        HERE,
        REPO_ROOT / "schemas" / "python" / "tckdb-schemas",
        REPO_ROOT / "clients" / "python" / "src",
        REPO_ROOT / "clients" / "python" / "adapters" / "chemkin",
        REPO_ROOT / "backend",
    ]
):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import yaml  # noqa: E402

import source_e2  # noqa: E402
from common import Session  # noqa: E402


def check_e2(client, cfg: dict[str, Any]) -> dict[str, Any]:
    root = Path(cfg["root"]).expanduser()
    levels = list(cfg.get("levels") or ["b3lyp_d3bj", "wb97xd"])
    tasks, _ = source_e2.load_tasks(root, levels)
    mine = [t for t in tasks if t.molecule == "n_butane"]
    deposited = [t for t in mine if source_e2.parse(t).is_minimum(t.level)]
    expected_calcs = 2 * len(deposited)  # an opt and a freq per minimum
    source_digests = {hashlib.sha256((t.directory / "job.log").read_bytes()).hexdigest() for t in deposited}

    species = client.search_species(smiles="CCCC", include=["conformers"])["records"]
    entry = species[0]["entries"][0]
    found_calcs = entry["availability"]["calculation_count"]

    artifacts = client.search_artifacts(species_entry_ref=entry["species_entry_ref"], artifact_kind="output_log", limit=200)
    digests = sorted({r["artifact"]["sha256"] if "artifact" in r else r["sha256"] for r in artifacts["records"]})
    asked = digests[0] if digests else None
    body = client.download_artifact(asked) if asked else b""
    recomputed = hashlib.sha256(body).hexdigest()
    return {
        "sample": "n-butane (CCCC), species entry " + entry["species_entry_ref"],
        "calculations_expected": expected_calcs,
        "calculations_found": found_calcs,
        "output_log_digests_found": len(digests),
        "output_log_digests_expected": len(source_digests),
        "downloaded_digest": asked,
        "downloaded_bytes": len(body),
        "download_digest_matches_request": recomputed == asked,
        "download_digest_is_a_source_file": asked in source_digests,
        "pass": found_calcs == expected_calcs
        and set(digests) == source_digests
        and recomputed == asked
        and asked in source_digests,
    }


def check_chemkin(client, cfg: dict[str, Any]) -> dict[str, Any]:
    from tckdb_chemkin.identity import IdentityResolver, parse_species_dictionary
    from tckdb_chemkin.parser import parse_mechanism

    fixture = Path(cfg.get("fixture_dir", "backend/tests/integration/fixtures/rmg_formic_acid_wu2026"))
    fixture = fixture if fixture.is_absolute() else REPO_ROOT / fixture
    mechanism = parse_mechanism((fixture / cfg.get("mechanism_file", "chem_annotated.inp")).read_text())
    resolver = IdentityResolver(rmg_dict=parse_species_dictionary((fixture / "species_dictionary.txt").read_text()))
    resolved = resolver.resolve_mechanism(mechanism)
    name = next(n for n in sorted(mechanism.thermo) if n.startswith("HOCHO("))
    entry = mechanism.thermo[name]
    smiles = resolved[name].smiles

    records = client.search_thermo(smiles=smiles, multiplicity=resolved[name].multiplicity, limit=5)["records"]
    nasa = records[0]["thermo"]["nasa"]
    # Low block -> a1..a7, high block -> b1..b7 (see tckdb_chemkin.payloads._nasa_payload).
    same = (
        nasa["t_low"] == entry.t_low
        and nasa["t_mid"] == entry.t_common
        and nasa["t_high"] == entry.t_high
        and list(nasa["low_temperature_coefficients"]) == list(entry.coeffs_low)
        and list(nasa["high_temperature_coefficients"]) == list(entry.coeffs_high)
    )
    return {
        "sample": f"{name} ({smiles})",
        "thermo_records_found": len(records),
        "nasa7_equals_chemkin_file": same,
        "pass": bool(records) and same,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--sources", type=Path, required=True)
    args = parser.parse_args(argv)
    sources = (yaml.safe_load(args.sources.read_text()) or {}).get("sources") or {}

    session = Session(args.api_url)
    session.register_depositor("snapshot_reader", "snapshot-reader-pass", "reader@snapshot.invalid", "Snapshot read-back")
    results: dict[str, Any] = {}
    with session.client() as client:
        for name, check in (("e2_conformers", check_e2), ("chemkin_formic_acid", check_chemkin)):
            entry = sources.get(name)
            if not entry or not entry.get("enabled", True):
                results[name] = {"skipped": "no entry in the sources config", "pass": True}
                continue
            try:
                results[name] = check(client, entry)
            except Exception as exc:  # a failed read is a failed check, reported, never swallowed
                results[name] = {"error": f"{type(exc).__name__}: {exc}", "pass": False}
    print(json.dumps(results, indent=2, sort_keys=True))
    return 0 if all(r["pass"] for r in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
