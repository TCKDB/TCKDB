"""Generator for the CHEMKIN export of the CH4 + OH use case (``scripts/paper_snapshot``).

* ``use_case_chemkin_export``: the CHEMKIN export of C + [OH] <=> O + [CH3], made in-process by the route's own
  function from the database alone. The request is found from release ``RELEASE_TAG`` (the rate record, and the [CH3]
  thermo record it selects) and from the rate record's submission (the other species' thermo records). The output has
  what the manifest says could not be written, the result of loading the files with Cantera's strict ``ck2yaml``,
  k(300 K) of the forward reaction as Cantera evaluates it from the exported file next to the stored fit's k(300 K),
  and H298 of [CH3] from the exported NASA polynomial next to the named record's H298.

``python -m scripts.paper.chemkin_export --check DIR`` makes the same export and compares it with the files a build saved
from ``POST /scientific/export/chemkin`` (byte for byte; ``manifest.json`` without its ``generated_at``).

Public refs and the manifest's ``generated_at`` change from build to build, so the file digests in the output are
taken over the file with refs replaced by ``<ref>`` and ``generated_at`` removed (``HASH_RULE``). The digest of the
bytes as written is recorded by the build in ``snapshot_record.json``.

The parsing, hashing and arithmetic helpers do not need Cantera or a database.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import math
import os
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.chemistry.arrhenius import GAS_CONSTANT_J_MOL_K
from app.db.models.common import SubmissionRecordType
from app.db.models.dataset_release import DatasetRelease
from app.db.models.kinetics import Kinetics
from app.db.models.species import Species, SpeciesEntry
from app.db.models.submission import SubmissionRecordLink
from app.db.models.thermo import Thermo
from scripts.paper.generators import _standing_selections
from scripts.paper.snapshot_cases import (
    H298_TEMPERATURE_K,
    USE_CASE_PRODUCTS,
    USE_CASE_REACTANTS,
    USE_CASE_TEMPERATURE_K,
    _rate_at,
    _released_in,
    _submissions_of,
    _thermo_value,
)

RELEASE_TAG = "snapshot-demo-1"
NAMED_SPECIES = ("C", "[OH]", "O", "[CH3]")
SELECTED_SPECIES = "[CH3]"
FILES = ("chem.inp", "therm.dat", "tran.dat", "manifest.json")
REQUEST_FILE = "export_request.json"

REF = re.compile(r"\b[a-z]{2,6}_[a-z2-7]{26}\b")
HASH_RULE = (
    "SHA-256 of the file with every public ref replaced by <ref>; manifest.json is parsed, generated_at is removed "
    "and the rest is written as JSON with sorted keys, compact separators and non-ASCII kept, before refs are replaced"
)
#: m3 kmol-1 s-1 (Cantera) to cm3 mol-1 s-1, for a bimolecular rate coefficient.
BIMOLECULAR_FROM_CANTERA = 1.0e3


def sig(value: float | None, digits: int = 6) -> float | None:
    """``value`` to ``digits`` significant figures."""
    if value is None or value == 0 or not math.isfinite(value):
        return value
    return float(f"{value:.{digits - 1}e}")


def mask_refs(text: str) -> str:
    return REF.sub("<ref>", text)


def masked_sha256(name: str, data: bytes) -> str:
    """The digest ``HASH_RULE`` describes."""
    if name == "manifest.json":
        document = json.loads(data.decode("utf-8"))
        document.pop("generated_at", None)
        text = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    else:
        text = data.decode("utf-8")
    return hashlib.sha256(mask_refs(text).encode("utf-8")).hexdigest()


def ratio(numerator: float | None, denominator: float | None) -> float | None:
    """``numerator / denominator`` to six significant figures; ``None`` when either is missing or the divisor is zero."""
    if numerator is None or denominator is None or denominator == 0:
        return None
    return sig(numerator / denominator)


# ---------------------------------------------------------------------------
# the exported files
# ---------------------------------------------------------------------------

_SPECIES_LINE = re.compile(r"^(?P<name>\S+)\s+!\s*SMILES=(?P<smiles>\S+)\s+MULT=(?P<mult>\d+)\s+ref=(?P<ref>\S+)\s*$")
_REACTION_LINE = re.compile(
    r"^(?P<eq>\S.*?<=>.*?|\S.*?=>.*?)\s+(?P<a>\S+)\s+(?P<n>\S+)\s+(?P<ea>\S+)\s*(?:!.*)?$"
)


def parse_chem_inp(text: str) -> dict[str, Any]:
    """Species (name, SMILES, multiplicity, species ref from the comment) and reactions (equation, A, n, Ea) of ``chem.inp``."""
    species: list[dict[str, Any]] = []
    reactions: list[dict[str, Any]] = []
    units = None
    section = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        keyword = line.split()[0].upper()
        if keyword in ("ELEMENTS", "ELEM", "SPECIES", "SPEC", "REACTIONS", "REAC"):
            section = keyword[:4]
            if section == "REAC":
                units = line.split()[1:]
            continue
        if keyword == "END":
            section = None
            continue
        if section == "SPEC":
            match = _SPECIES_LINE.match(line)
            if match is None:
                raise ValueError(f"chem.inp: unreadable species line: {line!r}")
            species.append(
                {
                    "name": match["name"],
                    "smiles": match["smiles"],
                    "multiplicity": int(match["mult"]),
                    "species_ref": match["ref"],
                }
            )
        elif section == "REAC" and not line.startswith("!"):
            match = _REACTION_LINE.match(line)
            if match is None:
                raise ValueError(f"chem.inp: unreadable reaction line: {line!r}")
            reactions.append(
                {
                    "equation": match["eq"].strip(),
                    "a": float(match["a"]),
                    "n": float(match["n"]),
                    "ea": float(match["ea"]),
                }
            )
    return {"species": species, "reactions": reactions, "reaction_units": units}


_THERMO_HEAD = re.compile(r"^(?P<name>\S+)\s.*G\s+(?P<tlow>[\d.]+)\s+(?P<thigh>[\d.]+)\s+(?P<tmid>[\d.]+)\s+1\s*$")


def _fields(line: str) -> list[float]:
    return [float(line[i : i + 15]) for i in range(0, 75, 15) if line[i : i + 15].strip()]


def parse_therm_dat(text: str) -> dict[str, dict[str, Any]]:
    """NASA-7 records of ``therm.dat`` by species name: temperatures and the high and low coefficient lists."""
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("!")]
    out: dict[str, dict[str, Any]] = {}
    i = 0
    while i < len(lines):
        match = _THERMO_HEAD.match(lines[i])
        if match is None:
            i += 1
            continue
        block = lines[i + 1 : i + 4]
        if len(block) < 3:
            raise ValueError(f"therm.dat: {match['name']}: fewer than three coefficient lines")
        numbers = [x for ln in block for x in _fields(ln)]
        if len(numbers) != 14:
            raise ValueError(f"therm.dat: {match['name']}: {len(numbers)} coefficients, expected 14")
        out[match["name"]] = {
            "t_low_k": float(match["tlow"]),
            "t_mid_k": float(match["tmid"]),
            "t_high_k": float(match["thigh"]),
            "high": numbers[:7],
            "low": numbers[7:],
        }
        i += 4
    return out


def nasa7_h_kj_mol(record: dict[str, Any], temperature_k: float) -> float:
    """H(T) in kJ/mol from a ``parse_therm_dat`` record: H/RT = a1 + a2 T/2 + a3 T^2/3 + a4 T^3/4 + a5 T^4/5 + a6/T."""
    if not record["t_low_k"] <= temperature_k <= record["t_high_k"]:
        raise ValueError(f"{temperature_k} K is outside the polynomial's {record['t_low_k']}-{record['t_high_k']} K")
    a = record["low"] if temperature_k < record["t_mid_k"] else record["high"]
    t = temperature_k
    h_over_rt = a[0] + a[1] * t / 2 + a[2] * t**2 / 3 + a[3] * t**3 / 4 + a[4] * t**4 / 5 + a[5] / t
    return h_over_rt * GAS_CONSTANT_J_MOL_K * t / 1000.0


def manifest_summary(manifest: dict[str, Any]) -> dict[str, Any]:
    """What the manifest says: counts, the profile, every gap (ordered by content) and the transport file's state."""
    gaps = sorted(
        (g for g in manifest.get("gaps") or []), key=lambda g: json.dumps(g, sort_keys=True, ensure_ascii=False)
    )
    return {
        "format": manifest.get("format"),
        "counts": manifest.get("counts"),
        "profile": manifest.get("profile"),
        "min_review_status": manifest.get("min_review_status"),
        "gaps": gaps,
        "transport_file": manifest.get("transport_file"),
    }


# ---------------------------------------------------------------------------
# Cantera
# ---------------------------------------------------------------------------


def cantera_load(chem: Path, therm: Path, tran: Path | None, temperature_k: float) -> dict[str, Any]:
    """Convert with the strict ``ck2yaml`` and load the result; k of every reaction at ``temperature_k`` in cm3 mol-1 s-1.

    Returns a status instead of raising when Cantera is missing or refuses the files, so the output states it.
    """
    try:
        import cantera as ct
        from cantera import ck2yaml
    except ImportError as exc:
        return {"status": "cantera_not_installed", "detail": str(exc)}
    base: dict[str, Any] = {"version": ct.__version__, "converter": "cantera.ck2yaml.convert, permissive=False"}
    with tempfile.TemporaryDirectory(prefix="chemkin_export_") as tmp:
        yaml_path = os.path.join(tmp, "mechanism.yaml")
        try:
            ck2yaml.convert(
                str(chem),
                thermo_file=str(therm),
                transport_file=str(tran) if tran is not None else None,
                out_name=yaml_path,
                quiet=True,
                permissive=False,
            )
            gas = ct.Solution(yaml_path)
        except Exception as exc:  # the refusal itself is the result
            return {**base, "status": "refused", "error": f"{type(exc).__name__}: {str(exc)[:600]}"}
        gas.TP = temperature_k, ct.one_atm
        k = list(gas.forward_rate_constants)
        reactions = []
        for index, reaction in enumerate(gas.reactions()):
            order = sum(reaction.reactants.values())
            reactions.append(
                {
                    "equation": reaction.equation,
                    "reactants": sorted(reaction.reactants),
                    "products": sorted(reaction.products),
                    "order": order,
                    "k_forward_cm3_mol_s": (k[index] * BIMOLECULAR_FROM_CANTERA) if order == 2 else None,
                }
            )
        h298 = {
            name: gas.species(name).thermo.h(H298_TEMPERATURE_K) / 1.0e6 for name in gas.species_names
        }
    return {
        **base,
        "status": "loaded",
        "species": sorted(h298),
        "reactions": reactions,
        "h298_kj_mol": h298,
    }


# ---------------------------------------------------------------------------
# the generator
# ---------------------------------------------------------------------------


def _record_smiles(session: Session, thermo_ref: str) -> str | None:
    thermo = session.scalar(select(Thermo).where(Thermo.public_ref == thermo_ref))
    if thermo is None:
        return None
    return session.scalar(
        select(Species.smiles).join(SpeciesEntry, SpeciesEntry.species_id == Species.id).where(SpeciesEntry.id == thermo.species_entry_id)
    )


def _forward_reaction(cantera: dict[str, Any], names: dict[str, str]) -> dict[str, Any] | None:
    """The loaded reaction whose reactants and products are the use case's, by SMILES."""
    want = (sorted(USE_CASE_REACTANTS), sorted(USE_CASE_PRODUCTS))
    for reaction in cantera.get("reactions") or []:
        have = (
            sorted(names.get(n, n) for n in reaction["reactants"]),
            sorted(names.get(n, n) for n in reaction["products"]),
        )
        if have == want:
            return reaction
    return None


def resolve_request(session: Session) -> tuple[dict[str, Any] | None, str | None]:
    """The export request for the use case, found from the database; or ``(None, reason)``.

    The rate record and the [CH3] thermo record are the ones standing in release ``RELEASE_TAG``; the other species'
    thermo records are the ones the rate record's submission deposited.
    """
    release = session.scalar(select(DatasetRelease).where(DatasetRelease.tag == RELEASE_TAG))
    if release is None:
        return None, f"no release {RELEASE_TAG}"
    standing = _standing_selections(session, release)
    kinetics_ids = [s.record_id for s in standing if s.record_type == SubmissionRecordType.kinetics]
    if len(kinetics_ids) != 1:
        return None, f"release {RELEASE_TAG} selects {len(kinetics_ids)} rate records, not one"
    kinetics = session.get(Kinetics, kinetics_ids[0])
    submissions = _submissions_of(session, SubmissionRecordType.kinetics, kinetics.id)
    if len(submissions) != 1:
        return None, f"the selected rate record belongs to {len(submissions)} submissions, not one"
    selected_thermo = {s.record_id for s in standing if s.record_type == SubmissionRecordType.thermo}
    by_smiles: dict[str, list[Thermo]] = {}
    links = session.scalars(
        select(SubmissionRecordLink).where(
            SubmissionRecordLink.submission_id == submissions[0].id,
            SubmissionRecordLink.record_type == SubmissionRecordType.thermo,
        )
    )
    for link in links:
        thermo = session.get(Thermo, link.record_id)
        smiles = session.scalar(
            select(Species.smiles)
            .join(SpeciesEntry, SpeciesEntry.species_id == Species.id)
            .where(SpeciesEntry.id == thermo.species_entry_id)
        )
        by_smiles.setdefault(smiles, []).append(thermo)
    refs = []
    for smiles in NAMED_SPECIES:
        found = by_smiles.get(smiles, [])
        if smiles == SELECTED_SPECIES:
            found = [t for t in found if t.id in selected_thermo]
        if len(found) != 1:
            return None, f"{len(found)} thermo records for {smiles} (rate record's submission and release), not one"
        refs.append(found[0].public_ref)
    request = {
        "seed": {"reaction_refs": [kinetics.reaction_entry.public_ref]},
        "kinetics_refs": [kinetics.public_ref],
        "thermo_refs": sorted(refs),
        "energy_units": "cal/mol",
        "include_transport": True,
        "naming_policy": "formula",
    }
    return request, None


def export_in_process(session: Session, request: dict[str, Any]) -> dict[str, bytes]:
    """The files ``POST /scientific/export/chemkin`` returns for ``request``, from the route's own function."""
    from app.api.routes.scientific.export import ChemkinExportRequest, export_chemkin

    body = ChemkinExportRequest(**request)
    response = export_chemkin(body, session, None)

    async def collect() -> bytes:
        return b"".join([chunk async for chunk in response.body_iterator])

    archive = asyncio.run(collect())
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        return {name: zf.read(name) for name in sorted(zf.namelist())}


def unchanged_by_timestamp(name: str, saved: bytes, fresh: bytes) -> bool:
    """Equal bytes; ``manifest.json`` ignores its ``generated_at``, the only value that varies between calls."""
    if name != "manifest.json":
        return saved == fresh
    a, b = json.loads(saved), json.loads(fresh)
    a.pop("generated_at", None)
    b.pop("generated_at", None)
    return a == b


def use_case_chemkin_export(session: Session) -> dict[str, Any]:
    """The use case's CHEMKIN export: records named, files, gaps, Cantera load, k(300 K) and H298 of [CH3]."""
    request, reason = resolve_request(session)
    if request is None:
        return {"export": "unresolved", "reason": reason}
    try:
        files = export_in_process(session, request)
    except Exception as exc:  # the refusal itself is the result
        return {"export": "refused", "error": f"{type(exc).__name__}: {str(exc)[:600]}"}
    return analyse(session, request, files)


def analyse(session: Session, request: dict[str, Any], files: dict[str, bytes]) -> dict[str, Any]:
    """Describe ``files`` (an export of ``request``) and check them: species, digests, gaps, Cantera, rate and H298."""
    chem = parse_chem_inp(files["chem.inp"].decode("utf-8"))
    therm = parse_therm_dat(files["therm.dat"].decode("utf-8"))
    manifest = json.loads(files["manifest.json"])
    names = {row["name"]: row["smiles"] for row in chem["species"]}

    named: dict[str, list[str]] = {}
    for ref in request["thermo_refs"]:
        named.setdefault(_record_smiles(session, ref) or "", []).append(ref)

    rows = []
    for row in chem["species"]:
        refs = sorted(named.get(row["smiles"], []))
        records = [session.scalar(select(Thermo).where(Thermo.public_ref == r)) for r in refs]
        rows.append(
            {
                **row,
                "thermo_refs_named": refs,
                "has_nasa_in_therm_dat": row["name"] in therm,
                "released_in": sorted({tag for rec in records if rec is not None for tag in _released_in(session, SubmissionRecordType.thermo, rec.id)}),
            }
        )
    rows.sort(key=lambda r: (r["smiles"], r["multiplicity"]))
    unmatched = sorted(smiles for smiles in named if smiles not in names.values())

    digests = [{"file": name, "sha256_masked": masked_sha256(name, files[name])} for name in FILES if name in files]

    with tempfile.TemporaryDirectory(prefix="chemkin_export_") as tmp:
        for name, data in files.items():
            (Path(tmp) / name).write_bytes(data)
        tran = Path(tmp) / "tran.dat"
        cantera = cantera_load(
            Path(tmp) / "chem.inp", Path(tmp) / "therm.dat", tran if tran.is_file() else None, USE_CASE_TEMPERATURE_K
        )

    forward = _forward_reaction(cantera, names) if cantera["status"] == "loaded" else None
    kinetics = None
    for ref in request["kinetics_refs"]:
        kinetics = session.scalar(select(Kinetics).where(Kinetics.public_ref == ref))
    stored = _rate_at(kinetics, USE_CASE_TEMPERATURE_K) if kinetics is not None else None
    cantera_k = sig(forward["k_forward_cm3_mol_s"]) if forward else None
    written = next((r for r in chem["reactions"] if forward and r["equation"] == forward["equation"]), None)
    rate = {
        "temperature_k": USE_CASE_TEMPERATURE_K,
        "units": "cm3_mol_s",
        "cantera_k_forward": cantera_k,
        "stored_fit_k": stored["k"] if stored else None,
        "stored_fit_units": stored["units"] if stored else None,
        "cantera_over_stored": ratio(cantera_k, stored["k"] if stored else None),
        "stored_fit": (
            {"a": kinetics.a, "n": kinetics.n, "ea_kj_mol": kinetics.ea_kj_mol, "t0_k": kinetics.t0_k} if kinetics else None
        ),
        "written_in_chem_inp": written,
        "reaction_units_line": chem["reaction_units"],
    }

    ch3 = "[CH3]"
    ch3_name = next((n for n, s in names.items() if s == ch3), None)
    record = session.scalar(select(Thermo).where(Thermo.public_ref == named[ch3][0])) if named.get(ch3) else None
    stored_h = _thermo_value(record, "h", H298_TEMPERATURE_K) if record is not None else None
    exported_h = sig(nasa7_h_kj_mol(therm[ch3_name], H298_TEMPERATURE_K)) if ch3_name in therm else None
    h298 = {
        "species": ch3,
        "temperature_k": H298_TEMPERATURE_K,
        "record_h298_kj_mol": sig(stored_h["value"]) if stored_h else None,
        "record_h298_from": stored_h["from"] if stored_h else None,
        "exported_nasa_h298_kj_mol": exported_h,
        "exported_minus_record_kj_mol": (
            sig(exported_h - stored_h["value"]) if exported_h is not None and stored_h and stored_h["value"] is not None else None
        ),
        "cantera_h298_kj_mol": sig(cantera["h298_kj_mol"].get(ch3_name)) if cantera["status"] == "loaded" else None,
    }

    return {
        "export": "found",
        "request": {
            "seed_reaction_refs": sorted(request["seed"]["reaction_refs"]),
            "kinetics_refs": sorted(request["kinetics_refs"]),
            "thermo_refs": sorted(request["thermo_refs"]),
            "naming_policy": request.get("naming_policy"),
            "energy_units": request.get("energy_units"),
        },
        "named_thermo_refs_without_a_species_in_chem_inp": unmatched,
        "species": rows,
        "reactions_written": [r["equation"] for r in chem["reactions"]],
        "files": digests,
        "hash_rule": HASH_RULE,
        "manifest": manifest_summary(manifest),
        "cantera": {
            **{k: v for k, v in cantera.items() if k not in ("reactions", "h298_kj_mol")},
            "reactions_loaded": [r["equation"] for r in cantera.get("reactions") or []],
        },
        "forward_rate": rate,
        "ch3_h298": h298,
    }


def check_saved(session: Session, directory: Path) -> dict[str, Any]:
    """Compare the files in ``directory`` (and ``export_request.json``) with the in-process export."""
    request, reason = resolve_request(session)
    if request is None:
        return {"match": False, "reason": reason}
    fresh = export_in_process(session, request)
    saved_request = json.loads((directory / REQUEST_FILE).read_text())
    rows = {}
    for name in sorted(set(fresh) | {n for n in FILES if (directory / n).is_file()}):
        path = directory / name
        if name not in fresh or not path.is_file():
            rows[name] = "present in only one"
        else:
            rows[name] = "same" if unchanged_by_timestamp(name, path.read_bytes(), fresh[name]) else "DIFFERS"
    same_request = (
        sorted(saved_request["thermo_refs"]) == request["thermo_refs"]
        and saved_request["kinetics_refs"] == request["kinetics_refs"]
        and saved_request["seed"] == request["seed"]
    )
    return {"match": same_request and all(v == "same" for v in rows.values()), "request_same": same_request, "files": rows}


def main(argv: list[str] | None = None) -> int:
    import argparse

    from app.api.deps import SessionLocal

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", type=Path, required=True, help="directory of the files a build saved")
    args = parser.parse_args(argv)
    with SessionLocal() as session:
        result = check_saved(session, args.check)
        session.rollback()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["match"] else 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
