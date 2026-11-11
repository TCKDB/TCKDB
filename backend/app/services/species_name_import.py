"""Load a reviewed species-name file (see ``app.importers.pubchem_names``) into ``species_name``.

Each row names a species by public ref and InChIKey; the row is written only when both still agree with the
stored species. Rows are written with ``source_kind=offline_import`` and an ``external_source`` row per PubChem CID.
A name already stored for the species (live or retired, curated or imported) is left alone, so a re-run writes
nothing new. A dry run does the same work inside a savepoint and rolls it back.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.db.models.common import SpeciesNameKind, SpeciesNameSourceKind
from app.db.models.external_source import ExternalSource
from app.db.models.species import Species
from app.db.models.species_name import SpeciesName
from app.services.species_name_normalisation import name_problem, normalise_name
from app.services.species_name_review import REVIEW_FORMAT, is_inchikey

SOURCE_NAME = "PubChem"
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


def _get_or_create_source(session: Session, cid: int) -> ExternalSource:
    release = f"CID {cid}"
    existing = session.scalar(
        select(ExternalSource).where(
            ExternalSource.source_name == SOURCE_NAME, ExternalSource.source_release == release
        )
    )
    if existing is not None:
        return existing
    row = ExternalSource(source_name=SOURCE_NAME, source_release=release, terms_url=SOURCE_TERMS_URL)
    session.add(row)
    session.flush()
    return row


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
    name_key = normalise_name(name)
    stored = session.execute(
        select(SpeciesName.retired_at, SpeciesName.source_kind).where(
            SpeciesName.species_id == species.id, SpeciesName.name_key == name_key
        )
    ).all()
    if any(retired_at is None for retired_at, _ in stored):
        out.reason = "already_present"
        return out
    if stored:
        out.reason = "retired_name_not_restored"
        return out
    try:
        with session.begin_nested():
            source = _get_or_create_source(session, cid)
            session.add(
                SpeciesName(
                    species_id=species.id,
                    name=name,
                    name_key=name_key,
                    kind=SpeciesNameKind(kind),
                    source_kind=SpeciesNameSourceKind.offline_import,
                    external_source_id=source.id,
                )
            )
            session.flush()
    except DBAPIError:
        session.refresh(species)
        out.reason = "species_merged" if species.merged_into_species_id is not None else "insert_refused"
        return out
    out.outcome = "would_insert" if dry_run else "inserted"
    return out


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
