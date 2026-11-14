"""Load a reviewed species-name CSV into ``species_name``. Local file only; nothing here opens a connection.

The columns are ``name``, ``kind`` and an identity: ``inchikey``, or ``smiles`` with ``charge`` and ``multiplicity``
(all three), or both. An optional ``record_key`` and ``retrieved_at`` (ISO 8601) record where the name came from; the
defaults are ``<file name>#<row number>`` and the time of the import.

A row is written only when exactly one live species matches its identity. Otherwise it is reported and skipped, and
nothing is guessed. Rows are written with ``source_kind=offline_import`` and the given ``external_source``. A name the
species already holds live is a duplicate, and a retired name stays retired. A dry run does the same work inside a
savepoint and rolls it back.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.db.models.common import SpeciesNameKind, SpeciesNameSourceKind
from app.db.models.external_source import ExternalSource
from app.db.models.molecular_property_observation import MolecularPropertyObservation
from app.db.models.species import Species, SpeciesEntry
from app.db.models.species_name import SpeciesName
from app.services.species_name_import import ImportReport, RowOutcome
from app.services.species_name_normalisation import name_problem, normalise_name
from app.services.species_name_review import is_inchikey

COLUMNS = ("inchikey", "smiles", "charge", "multiplicity", "name", "kind", "record_key", "retrieved_at")


class SourceError(ValueError):
    """The named external source is missing or ambiguous."""


def resolve_source(session: Session, *, name: str, release: str | None) -> ExternalSource:
    """The one ``external_source`` row named ``name`` (and ``release`` when given)."""
    query = select(ExternalSource).where(ExternalSource.source_name == name)
    if release is not None:
        query = query.where(ExternalSource.source_release == release)
    rows = session.scalars(query).all()
    if not rows:
        raise SourceError("no external source matches; register it before importing")
    if len(rows) > 1:
        raise SourceError("several releases of this external source exist; pass the release")
    return rows[0]


def read_rows(lines: Iterable[str]) -> list[dict[str, str]]:
    """The rows of a CSV with a header line. Raises ``ValueError`` when the header lacks ``name`` or ``kind``."""
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


def _live_matches(session: Session, row: dict[str, str]) -> tuple[list[Species] | None, str | None]:
    """The live species the row's identity names, or ``(None, reason)`` when the identity is unusable."""
    key = row.get("inchikey", "")
    smiles = row.get("smiles", "")
    charge, multiplicity = row.get("charge", ""), row.get("multiplicity", "")
    query = select(Species).where(Species.merged_into_species_id.is_(None))
    given = False
    if key:
        if not is_inchikey(key):
            return None, "bad_inchikey"
        query = query.where(Species.inchi_key == key)
        given = True
    if smiles or charge or multiplicity:
        if not (smiles and charge and multiplicity):
            return None, "identity_incomplete"
        c, m = _int(charge), _int(multiplicity)
        if c is None or m is None:
            return None, "bad_identity"
        query = query.where(Species.smiles == smiles, Species.charge == c, Species.multiplicity == m)
        given = True
    if not given:
        return None, "identity_missing"
    return list(session.scalars(query.limit(2))), None


def _import_row(
    session: Session,
    row: dict[str, str],
    *,
    source: ExternalSource,
    record_prefix: str,
    number: int,
    dry_run: bool,
    now: datetime,
) -> RowOutcome:
    name, kind = row.get("name", ""), row.get("kind", "")
    out = RowOutcome(None, name or None, kind or None, None, "skipped")
    if kind not in {k.value for k in SpeciesNameKind}:
        out.reason = "bad_kind"
        return out
    problem = name_problem(name)
    if problem is not None:
        out.reason = problem
        return out
    retrieved_at: datetime | None = now
    if row.get("retrieved_at"):
        retrieved_at = _parse_time(row["retrieved_at"])
        if retrieved_at is None:
            out.reason = "bad_retrieved_at"
            return out
    matches, reason = _live_matches(session, row)
    if matches is None:
        out.reason = reason
        return out
    if not matches:
        out.reason = "species_not_found"
        return out
    if len(matches) > 1:
        out.reason = "species_ambiguous"
        return out
    species = matches[0]
    out.species_ref = species.public_ref
    key = normalise_name(name)
    stored = session.execute(
        select(SpeciesName.retired_at).where(SpeciesName.species_id == species.id, SpeciesName.name_key == key)
    ).all()
    if any(retired_at is None for (retired_at,) in stored):
        out.reason = "duplicate"
        return out
    if stored:
        out.reason = "retired_name_not_restored"
        return out
    try:
        with session.begin_nested():
            session.add(
                SpeciesName(
                    species_id=species.id,
                    name=" ".join(name.split()),
                    name_key=key,
                    kind=SpeciesNameKind(kind),
                    source_kind=SpeciesNameSourceKind.offline_import,
                    external_source_id=source.id,
                    external_record_key=row.get("record_key") or f"{record_prefix}#{number}",
                    retrieved_at=retrieved_at,
                )
            )
            session.flush()
    except DBAPIError:
        out.reason = "insert_refused"
        return out
    out.outcome = "would_insert" if dry_run else "inserted"
    return out


def propose_from_cccbdb(session: Session) -> list[dict[str, str]]:
    """Names held in stored CCCBDB observations (``raw_payload_json.identity_hint.name``) for a curator to review.

    Reads the database only. A proposal is one name for one live species the observation's entry belongs to, skipped
    when the species already holds the name live or the text could not be stored as a name. The ``kind`` column is left
    for the reviewer to confirm.
    """
    hint_name = MolecularPropertyObservation.raw_payload_json["identity_hint"]["name"].astext
    found = session.execute(
        select(Species.public_ref, Species.inchi_key, hint_name)
        .select_from(MolecularPropertyObservation)
        .join(SpeciesEntry, SpeciesEntry.id == MolecularPropertyObservation.species_entry_id)
        .join(Species, Species.id == SpeciesEntry.species_id)
        .where(hint_name.is_not(None), Species.merged_into_species_id.is_(None))
        .order_by(Species.public_ref, hint_name)
    ).all()
    live = {
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
        if (ref, key) in live or (ref, key) in proposals:
            continue
        proposals[(ref, key)] = {"species_ref": ref, "inchikey": inchi_key, "name": " ".join(name.split()), "kind": ""}
    return list(proposals.values())


def import_species_names_csv(
    session: Session, rows: list[dict[str, Any]], *, source: ExternalSource, record_prefix: str, dry_run: bool
) -> ImportReport:
    """Load ``rows`` (see :func:`read_rows`). The caller commits; a dry run leaves the session as it found it."""
    report = ImportReport(dry_run=dry_run)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    outer = session.begin_nested() if dry_run else None
    try:
        for number, row in enumerate(rows, start=2):  # row 1 is the header
            report.rows.append(
                _import_row(
                    session, row, source=source, record_prefix=record_prefix, number=number, dry_run=dry_run, now=now
                )
            )
    finally:
        if outer is not None:
            outer.rollback()
    return report
