"""Load reviewed species names into ``species_name``: a PubChem review file (see ``app.importers.pubchem_names``) or
a reviewed CSV (``app.services.species_name_csv_import``). Both readers resolve a species and hand it to one write
core, :func:`write_name`.

Rows are written with ``source_kind=offline_import`` and an ``external_source`` row. A name already stored for the
species (live or retired, curated or imported) is left alone, so a re-run writes nothing new. A dry run does the same
work inside a savepoint and rolls it back, so it creates no source either.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.db.models.common import SpeciesNameKind, SpeciesNameSourceKind
from app.db.models.external_source import ExternalSource
from app.db.models.species import Species
from app.db.models.species_name import SpeciesName
from app.services.species_name_normalisation import display_name, name_problem, normalise_name
from app.services.species_name_review import REVIEW_FORMAT, is_inchikey

SOURCE_NAME = "PubChem Compound"
SOURCE_RELEASE = "rolling"
SOURCE_TERMS_URL = "https://www.ncbi.nlm.nih.gov/home/about/policies/"
IMPORTABLE_KINDS = {SpeciesNameKind.common.value, SpeciesNameKind.systematic.value}


class ReviewFileError(ValueError):
    """The file is not a review file this import understands."""


@dataclass
class RowOutcome:
    species_ref: str | None
    name: str | None
    kind: str | None
    cid: int | None
    outcome: str  # "inserted" | "would_insert" | "skipped"
    reason: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class ImportReport:
    dry_run: bool
    rows: list[RowOutcome] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        tally = Counter(r.outcome if r.outcome != "skipped" else f"skipped:{r.reason}" for r in self.rows)
        return dict(sorted(tally.items()))

    def to_json(self) -> dict[str, Any]:
        return {"dry_run": self.dry_run, "counts": self.counts, "rows": [r.to_json() for r in self.rows]}


@dataclass(frozen=True)
class SourceSpec:
    """The ``external_source`` a load cites: name and release identify it, the rest fills a new row."""

    name: str
    release: str
    terms_url: str | None = None
    citation: str | None = None


PUBCHEM_SOURCE = SourceSpec(SOURCE_NAME, SOURCE_RELEASE, SOURCE_TERMS_URL)


def get_or_create_source(session: Session, spec: SourceSpec) -> ExternalSource:
    existing = session.scalar(
        select(ExternalSource).where(
            ExternalSource.source_name == spec.name, ExternalSource.source_release == spec.release
        )
    )
    if existing is not None:
        return existing
    row = ExternalSource(
        source_name=spec.name, source_release=spec.release, terms_url=spec.terms_url, citation_text=spec.citation
    )
    session.add(row)
    session.flush()
    return row


def write_name(
    session: Session,
    species: Species,
    out: RowOutcome,
    *,
    name: str,
    kind: SpeciesNameKind,
    source: SourceSpec,
    record_key: str,
    retrieved_at: datetime,
    dry_run: bool,
) -> RowOutcome:
    """Store ``name`` (whitespace collapsed, as the curator route does) on ``species`` as an offline import, or say why not on ``out``.

    Skips a merged species and a name the species already holds, live or retired. The insert runs in a savepoint, so
    a species merged since it was read is reported as ``species_merged``.
    """
    if _is_merged(species):
        out.reason = "species_merged"
        return out
    name_key = normalise_name(name)
    stored = session.execute(
        select(SpeciesName.retired_at).where(SpeciesName.species_id == species.id, SpeciesName.name_key == name_key)
    ).all()
    if any(retired_at is None for (retired_at,) in stored):
        out.reason = "already_present"
        return out
    if stored:
        out.reason = "retired_name_not_restored"
        return out
    try:
        with session.begin_nested():
            external = get_or_create_source(session, source)
            session.add(
                SpeciesName(
                    species_id=species.id,
                    name=display_name(name),
                    name_key=name_key,
                    kind=kind,
                    source_kind=SpeciesNameSourceKind.offline_import,
                    external_source_id=external.id,
                    external_record_key=record_key,
                    retrieved_at=retrieved_at,
                )
            )
            session.flush()
    except DBAPIError:
        session.refresh(species)
        out.reason = "species_merged" if species.merged_into_species_id is not None else "insert_refused"
        return out
    out.outcome = "would_insert" if dry_run else "inserted"
    return out


def _parse_retrieved_at(value: Any) -> datetime | None:
    """The review row's retrieval time as a naive UTC datetime, or ``None`` when absent or not an ISO timestamp."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed


def _is_merged(species: Species) -> bool:
    return species.merged_into_species_id is not None


def _import_row(session: Session, raw: Any, *, dry_run: bool) -> RowOutcome:
    if not isinstance(raw, dict):
        return RowOutcome(None, None, None, None, "skipped", "bad_row")
    ref, name, kind, cid, key = (raw.get(k) for k in ("species_ref", "name", "kind", "cid", "inchi_key"))
    out = RowOutcome(
        ref if isinstance(ref, str) else None,
        name if isinstance(name, str) else None,
        kind if isinstance(kind, str) else None,
        cid if isinstance(cid, int) else None,
        "skipped",
    )
    if (
        not isinstance(ref, str)
        or not isinstance(name, str)
        or kind not in IMPORTABLE_KINDS
        or not isinstance(cid, int)
        or isinstance(cid, bool)
        or cid <= 0
        or not is_inchikey(key)
    ):
        out.reason = "bad_row"
        return out
    retrieved_at = _parse_retrieved_at(raw.get("retrieved_at"))
    if retrieved_at is None:
        out.reason = "bad_row"
        return out
    problem = name_problem(name)
    if problem is not None:
        out.reason = problem
        return out
    species = session.scalar(select(Species).where(Species.public_ref == ref))
    if species is None:
        out.reason = "species_not_found"
        return out
    if species.inchi_key != key:
        out.reason = "inchikey_mismatch"
        return out
    if _is_merged(species):
        out.reason = "species_merged"
        return out
    sharing = session.scalar(
        select(func.count())
        .select_from(Species)
        .where(Species.inchi_key == key, Species.merged_into_species_id.is_(None))
    )
    if sharing is not None and sharing > 1:
        out.reason = "inchikey_shared_by_species"
        return out
    return write_name(
        session,
        species,
        out,
        name=name,
        kind=SpeciesNameKind(kind),
        source=PUBCHEM_SOURCE,
        record_key=str(cid),
        retrieved_at=retrieved_at,
        dry_run=dry_run,
    )


def import_species_names(session: Session, document: Any, *, dry_run: bool) -> ImportReport:
    """Load the rows of a review ``document``. ``dry_run`` reports what would be written and writes nothing.

    The caller commits; a dry run leaves the session as it found it.
    """
    if (
        not isinstance(document, dict)
        or document.get("format") != REVIEW_FORMAT
        or not isinstance(document.get("rows"), list)
    ):
        raise ReviewFileError(f"not a {REVIEW_FORMAT} review file")
    report = ImportReport(dry_run=dry_run)
    outer = session.begin_nested() if dry_run else None
    try:
        for raw in document["rows"]:
            report.rows.append(_import_row(session, raw, dry_run=dry_run))
    finally:
        if outer is not None:
            outer.rollback()
    return report
