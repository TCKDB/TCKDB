"""The reviewed-CSV reader for the species-name import. Local file only; nothing here opens a connection.

The columns are ``name``, ``kind``, ``record_key``, ``retrieved_at`` (ISO 8601) and an identity: ``inchikey``, or
``smiles`` with ``charge`` and ``multiplicity`` (all three), or both. A row without a record key and a retrieval time
is refused (``provenance_missing``). It resolves a species conservatively, acts only when exactly one live species
matches, and passes the name to :func:`app.services.species_name_import.write_name`.

An InChIKey is matched as stored, so it is blind to stereo and isotope marks that the stored key does not carry. A
SMILES is resolved the way an upload resolves it, so any spelling of the graph finds its species.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.common import SpeciesNameKind
from app.db.models.molecular_property_observation import MolecularPropertyObservation
from app.db.models.species import Species, SpeciesEntry
from app.db.models.species_name import SpeciesName
from app.importers.cccbdb import SOURCE_NAME as CCCBDB_SOURCE_NAME
from app.schemas.fragments.identity import SpeciesEntryIdentityPayload
from app.services.species_merge import live
from app.services.species_name_import import ImportReport, RowOutcome, SourceSpec, write_name
from app.services.species_name_normalisation import name_problem, normalise_name
from app.services.species_name_review import is_inchikey
from app.services.species_resolution import find_species


def read_rows(lines: Iterable[str]) -> list[dict[str, str]]:
    """The rows of a CSV with a header line. Raises ``ValueError`` when the header lacks a needed column."""
    reader = csv.DictReader(lines)
    fields = set(reader.fieldnames or [])
    if not {"name", "kind"} <= fields:
        raise ValueError("the CSV needs a header with the columns name and kind")
    if "inchikey" not in fields and not {"smiles", "charge", "multiplicity"} <= fields:
        raise ValueError("the CSV needs an inchikey column, or smiles, charge and multiplicity columns")
    return [{k: (v or "").strip() for k, v in row.items() if k is not None} for row in reader]


def _parse_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed


def _int(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None


def _resolve(session: Session, row: dict[str, str]) -> tuple[Species | None, str | None]:
    """The one live species the row's identity names, or ``(None, reason)``."""
    key = row.get("inchikey", "")
    smiles, charge, multiplicity = row.get("smiles", ""), row.get("charge", ""), row.get("multiplicity", "")
    by_key: list[Species] | None = None
    by_smiles: Species | None = None
    if key:
        if not is_inchikey(key):
            return None, "bad_inchikey"
        by_key = list(session.scalars(select(Species).where(Species.inchi_key == key, live(Species)).limit(2)))
    if smiles or charge or multiplicity:
        if not (smiles and charge and multiplicity):
            return None, "identity_incomplete"
        c, m = _int(charge), _int(multiplicity)
        if c is None or m is None:
            return None, "bad_identity"
        try:
            by_smiles = find_species(session, SpeciesEntryIdentityPayload(smiles=smiles, charge=c, multiplicity=m))
        except ValueError:
            return None, "bad_identity"
    if by_key is None and not (smiles and charge and multiplicity):
        return None, "identity_missing"
    if by_key is not None and (smiles and charge and multiplicity):
        if not by_key and by_smiles is None:
            return None, "species_not_found"
        if by_smiles is None or by_smiles.id not in {s.id for s in by_key}:
            return None, "identity_conflict"
        return by_smiles, None
    if by_smiles is not None:
        return by_smiles, None
    if not by_key:
        return None, "species_not_found"
    if len(by_key) > 1:
        return None, "species_ambiguous"
    return by_key[0], None


def _import_row(session: Session, row: dict[str, str], *, source: SourceSpec, dry_run: bool) -> RowOutcome:
    name, kind = row.get("name", ""), row.get("kind", "")
    out = RowOutcome(None, name or None, kind or None, None, "skipped")
    if kind not in {k.value for k in SpeciesNameKind}:
        out.reason = "bad_kind"
        return out
    problem = name_problem(name)
    if problem is not None:
        out.reason = problem
        return out
    if not row.get("record_key") or not row.get("retrieved_at"):
        out.reason = "provenance_missing"
        return out
    retrieved_at = _parse_time(row["retrieved_at"])
    if retrieved_at is None:
        out.reason = "bad_retrieved_at"
        return out
    species, reason = _resolve(session, row)
    if species is None:
        out.reason = reason
        return out
    out.species_ref = species.public_ref
    return write_name(
        session,
        species,
        out,
        name=name,
        kind=SpeciesNameKind(kind),
        source=source,
        record_key=row["record_key"],
        retrieved_at=retrieved_at,
        dry_run=dry_run,
    )


def import_species_names_csv(
    session: Session, rows: list[dict[str, str]], *, source: SourceSpec, dry_run: bool
) -> ImportReport:
    """Load ``rows`` (see :func:`read_rows`). The caller commits; a dry run leaves the session as it found it."""
    report = ImportReport(dry_run=dry_run)
    outer = session.begin_nested() if dry_run else None
    try:
        for row in rows:
            report.rows.append(_import_row(session, row, source=source, dry_run=dry_run))
    finally:
        if outer is not None:
            outer.rollback()
    return report


def propose_from_cccbdb(session: Session) -> list[dict[str, str]]:
    """Names held in stored CCCBDB observations (``raw_payload_json.identity_hint.name``) for a curator to review.

    Reads the database only. A proposal is one name for the live species the observation's entry belongs to, skipped
    when the species already holds the name live or the text could not be stored as a name. The ``kind``,
    ``record_key`` and ``retrieved_at`` columns are left for the reviewer.
    """
    hint_name = MolecularPropertyObservation.raw_payload_json["identity_hint"]["name"].astext
    found = session.execute(
        select(Species.public_ref, Species.inchi_key, hint_name)
        .select_from(MolecularPropertyObservation)
        .join(SpeciesEntry, SpeciesEntry.id == MolecularPropertyObservation.species_entry_id)
        .join(Species, Species.id == SpeciesEntry.species_id)
        .where(
            hint_name.is_not(None),
            MolecularPropertyObservation.external_source_name == CCCBDB_SOURCE_NAME,
            live(Species),
        )
        .order_by(Species.public_ref, hint_name)
    ).all()
    held = {
        (ref, key)
        for ref, key in session.execute(
            select(Species.public_ref, SpeciesName.name_key)
            .join(SpeciesName, SpeciesName.species_id == Species.id)
            .where(SpeciesName.retired_at.is_(None))
        )
    }
    proposals: dict[tuple[str, str], dict[str, str]] = {}
    for ref, inchi_key, name in found:
        if name_problem(name) is not None:
            continue
        key = normalise_name(name)
        if (ref, key) in held or (ref, key) in proposals:
            continue
        proposals[(ref, key)] = {
            "species_ref": ref,
            "inchikey": inchi_key,
            "name": name,
            "kind": "",
            "record_key": "",
            "retrieved_at": "",
        }
    return list(proposals.values())
