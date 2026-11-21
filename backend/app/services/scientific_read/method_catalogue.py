"""Service implementation for the method and basis-set catalogue reads.

- ``GET /scientific/methods`` and ``GET /scientific/methods/{handle}``
- ``GET /scientific/basis-sets`` and ``GET /scientific/basis-sets/{handle}``
- ``include=catalogue`` on the level-of-theory record (:meth:`CatalogueIndex.level_catalogue`)

An entry is matched to a level of theory by identity key: a level's method and dispersion key
(:func:`~app.chemistry.dispersion_names.level_identity_keys`) and the key of each of its basis columns
(:func:`~app.chemistry.basis_set_names.basis_identity_key`) are looked up in the ``*_catalogue_key`` tables. Usage
is derived at read time from the calculations a list read shows (the record floor), the same predicate the
level-of-theory usage count uses. A key such a level uses that no entry names is listed as uncatalogued, counted the
same way. Entries and levels are listed in key and label order; nothing is ranked.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.api.errors import not_found
from app.chemistry.basis_set_names import basis_identity_key
from app.chemistry.dispersion_names import dispersion_identity_key, level_identity_keys
from app.chemistry.level_label import render_level_label
from app.chemistry.method_families import UNCLASSIFIED, MethodFamily, classify_method
from app.chemistry.method_names import method_identity_key
from app.db.models.author import Author
from app.db.models.calculation import Calculation
from app.db.models.common import (
    BasisSetRole,
    CatalogueMethodKind,
    LiteratureTitleSource,
    SubmissionRecordType,
)
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.literature import Literature
from app.db.models.literature_author import LiteratureAuthor
from app.db.models.method_catalogue import (
    BasisSetCatalogue,
    BasisSetCatalogueLiterature,
    MethodCatalogue,
    MethodCatalogueLiterature,
)
from app.schemas.reads.scientific_common import ReviewStatusSummary
from app.schemas.reads.scientific_method_catalogue import (
    BasisSetCatalogueBrowseResponse,
    BasisSetCatalogueCard,
    BasisSetCatalogueDetailRecord,
    BasisSetCatalogueDetailResponse,
    CatalogueCitation,
    CatalogueLevelUsage,
    CatalogueRequestEcho,
    CatalogueUsage,
    LevelOfTheoryCatalogue,
    MethodCatalogueBrowseResponse,
    MethodCatalogueCard,
    MethodCatalogueDetailRecord,
    MethodCatalogueDetailResponse,
    UncataloguedBasisSet,
    UncataloguedMethod,
)
from app.services.scientific_read.common import shown_predicate, where_visible

_METHOD_PREFIX = "mcat_"
_BASIS_PREFIX = "bcat_"


@dataclass(frozen=True)
class _UsedLevel:
    """A level of theory with at least one shown calculation, with its identity keys."""

    ref: str
    label: str
    calculations: int
    method: str
    dispersion: str | None
    method_key: str
    dispersion_key: str | None
    basis_columns: tuple[tuple[str, str], ...]  # (stored spelling, key) for basis, aux_basis, cabs_basis


@dataclass
class _Usage:
    levels: list[_UsedLevel] = field(default_factory=list)
    spellings: set[str] = field(default_factory=set)

    def add(self, level: _UsedLevel, spelling: str) -> None:
        if all(existing.ref != level.ref for existing in self.levels):
            self.levels.append(level)
        self.spellings.add(spelling)

    def summary(self) -> CatalogueUsage:
        return CatalogueUsage(
            level_usage_count=len(self.levels),
            calculation_usage_count=sum(level.calculations for level in self.levels),
        )

    def level_rows(self) -> list[CatalogueLevelUsage]:
        return [
            CatalogueLevelUsage(
                level_of_theory_ref=level.ref, label=level.label, calculation_usage_count=level.calculations
            )
            for level in sorted(self.levels, key=lambda level: (level.label, level.ref))
        ]


def _verified_doi(lit: Literature) -> str | None:
    return lit.doi if lit.title_source == LiteratureTitleSource.resolved_metadata else None


def _authors(session: Session, literature_ids: set[int]) -> dict[int, list[str]]:
    if not literature_ids:
        return {}
    rows = session.execute(
        select(LiteratureAuthor.literature_id, Author.full_name)
        .join(Author, Author.id == LiteratureAuthor.author_id)
        .where(LiteratureAuthor.literature_id.in_(literature_ids))
        .order_by(LiteratureAuthor.literature_id, LiteratureAuthor.author_order)
    ).all()
    out: dict[int, list[str]] = defaultdict(list)
    for literature_id, name in rows:
        out[literature_id].append(name)
    return out


def _citations(
    links: list[MethodCatalogueLiterature] | list[BasisSetCatalogueLiterature], authors: dict[int, list[str]]
) -> list[CatalogueCitation]:
    return [
        CatalogueCitation(
            literature_ref=link.literature.public_ref,
            role=link.role,
            position=link.position,
            title=link.literature.title,
            title_source=link.literature.title_source,
            journal=link.literature.journal,
            year=link.literature.year,
            volume=link.literature.volume,
            issue=link.literature.issue,
            pages=link.literature.pages,
            authors=authors.get(link.literature_id, []),
            doi=_verified_doi(link.literature),
        )
        for link in sorted(links, key=lambda link: link.position)
    ]


def _classification(keys: list[str]) -> tuple[MethodFamily, int | None]:
    """The class the entry's keys share: the first classified key's, else ``unclassified``."""
    for key in keys:
        cls = classify_method(key)
        if cls != UNCLASSIFIED:
            return cls.family, cls.dft_rung
    return UNCLASSIFIED.family, None


def _used_levels(session: Session) -> list[_UsedLevel]:
    """Every level of theory with at least one calculation a list read shows, with its keys."""
    visible = shown_predicate(Calculation.id, SubmissionRecordType.calculation, detail=False)
    rows = session.execute(
        where_visible(
            select(LevelOfTheory, func.count(Calculation.id))
            .join(Calculation, Calculation.lot_id == LevelOfTheory.id)
            .group_by(LevelOfTheory.id),
            visible,
        )
    ).all()
    out = []
    for lot, count in rows:
        method_key, dispersion_key = level_identity_keys(lot.method, lot.dispersion)
        basis_columns = tuple(
            (spelling, key)
            for spelling in (lot.basis, lot.aux_basis, lot.cabs_basis)
            if spelling is not None and (key := basis_identity_key(spelling)) is not None
        )
        out.append(
            _UsedLevel(
                ref=lot.public_ref,
                label=_label(lot),
                calculations=int(count),
                method=lot.method,
                dispersion=lot.dispersion,
                method_key=method_key,
                dispersion_key=dispersion_key,
                basis_columns=basis_columns,
            )
        )
    return out


def _label(lot: LevelOfTheory) -> str:
    return render_level_label(
        method=lot.method,
        basis=lot.basis,
        aux_basis=lot.aux_basis,
        cabs_basis=lot.cabs_basis,
        dispersion=lot.dispersion,
        solvent=lot.solvent,
        solvent_model=lot.solvent_model,
        spin_treatment=lot.spin_treatment,
        core_treatment=lot.core_treatment,
        relativistic_hamiltonian=lot.relativistic_hamiltonian,
        pno_truncation=lot.pno_truncation,
        ri_approximation=lot.ri_approximation,
    )


@dataclass
class CatalogueIndex:
    """Every catalogue entry as a card, with its usage, and the identity-key maps that reach them."""

    methods: dict[str, MethodCatalogueCard]
    basis_sets: dict[str, BasisSetCatalogueCard]
    method_usage: dict[str, _Usage]
    basis_set_usage: dict[str, _Usage]
    method_by_key: dict[str, str]
    dispersion_by_key: dict[str, str]
    basis_set_by_key: dict[str, str]
    uncatalogued_methods: dict[tuple[str, CatalogueMethodKind], _Usage] = field(default_factory=dict)
    uncatalogued_basis_sets: dict[str, _Usage] = field(default_factory=dict)

    def level_catalogue(self, lot: LevelOfTheory) -> LevelOfTheoryCatalogue:
        """The cards a level of theory's spellings key to."""
        method_key, dispersion_key = level_identity_keys(lot.method, lot.dispersion)

        def basis_card(spelling: str | None) -> BasisSetCatalogueCard | None:
            key = basis_identity_key(spelling)
            entry = self.basis_set_by_key.get(key) if key is not None else None
            return self.basis_sets[entry] if entry is not None else None

        method_entry = self.method_by_key.get(method_key)
        dispersion_entry = self.dispersion_by_key.get(dispersion_key) if dispersion_key is not None else None
        return LevelOfTheoryCatalogue(
            method=self.methods[method_entry] if method_entry is not None else None,
            dispersion=self.methods[dispersion_entry] if dispersion_entry is not None else None,
            basis=basis_card(lot.basis),
            aux_basis=basis_card(lot.aux_basis),
            cabs_basis=basis_card(lot.cabs_basis),
        )


def load_catalogue_index(session: Session) -> CatalogueIndex:
    """Build every card once: entries, references, usage and spellings seen."""
    method_rows = session.scalars(
        select(MethodCatalogue)
        .options(
            selectinload(MethodCatalogue.keys),
            selectinload(MethodCatalogue.references).selectinload(MethodCatalogueLiterature.literature),
        )
        .order_by(MethodCatalogue.key)
    ).all()
    basis_rows = session.scalars(
        select(BasisSetCatalogue)
        .options(
            selectinload(BasisSetCatalogue.keys),
            selectinload(BasisSetCatalogue.references).selectinload(BasisSetCatalogueLiterature.literature),
        )
        .order_by(BasisSetCatalogue.key)
    ).all()
    authors = _authors(session, {link.literature_id for row in (*method_rows, *basis_rows) for link in row.references})

    method_by_key: dict[str, str] = {}
    dispersion_by_key: dict[str, str] = {}
    for row in method_rows:
        target = dispersion_by_key if row.kind is CatalogueMethodKind.dispersion_correction else method_by_key
        for k in row.keys:
            target[k.key] = row.key
    basis_set_by_key = {k.key: row.key for row in basis_rows for k in row.keys}

    method_usage: dict[str, _Usage] = {row.key: _Usage() for row in method_rows}
    basis_set_usage: dict[str, _Usage] = {row.key: _Usage() for row in basis_rows}
    uncatalogued_methods: dict[tuple[str, CatalogueMethodKind], _Usage] = defaultdict(_Usage)
    uncatalogued_basis_sets: dict[str, _Usage] = defaultdict(_Usage)
    for level in _used_levels(session):
        if (entry := method_by_key.get(level.method_key)) is not None:
            method_usage[entry].add(level, level.method)
        elif level.method_key:
            uncatalogued_methods[(level.method_key, CatalogueMethodKind.method)].add(level, level.method)
        if level.dispersion_key is not None:
            spelling = level.dispersion or level.method
            if (entry := dispersion_by_key.get(level.dispersion_key)) is not None:
                method_usage[entry].add(level, spelling)
            else:
                uncatalogued = (level.dispersion_key, CatalogueMethodKind.dispersion_correction)
                uncatalogued_methods[uncatalogued].add(level, spelling)
        for spelling, key in level.basis_columns:
            if (entry := basis_set_by_key.get(key)) is not None:
                basis_set_usage[entry].add(level, spelling)
            else:
                uncatalogued_basis_sets[key].add(level, spelling)

    methods: dict[str, MethodCatalogueCard] = {}
    for row in method_rows:
        keys = sorted(k.key for k in row.keys)
        family, rung = (
            (None, None) if row.kind is CatalogueMethodKind.dispersion_correction else _classification([row.key, *keys])
        )
        methods[row.key] = MethodCatalogueCard(
            catalogue_ref=row.public_ref,
            key=row.key,
            keys=keys,
            kind=row.kind,
            short_name=row.short_name,
            full_name=row.full_name,
            description=row.description,
            family=family,
            dft_rung=rung,
            references=_citations(row.references, authors),
            spellings_seen=sorted(method_usage[row.key].spellings),
            usage=method_usage[row.key].summary(),
        )
    basis_sets = {
        row.key: BasisSetCatalogueCard(
            catalogue_ref=row.public_ref,
            key=row.key,
            keys=sorted(k.key for k in row.keys),
            role=row.role,
            family=row.family,
            short_name=row.short_name,
            full_name=row.full_name,
            description=row.description,
            references=_citations(row.references, authors),
            spellings_seen=sorted(basis_set_usage[row.key].spellings),
            usage=basis_set_usage[row.key].summary(),
        )
        for row in basis_rows
    }
    return CatalogueIndex(
        methods=methods,
        basis_sets=basis_sets,
        method_usage=method_usage,
        basis_set_usage=basis_set_usage,
        method_by_key=method_by_key,
        dispersion_by_key=dispersion_by_key,
        basis_set_by_key=basis_set_by_key,
        uncatalogued_methods=dict(uncatalogued_methods),
        uncatalogued_basis_sets=dict(uncatalogued_basis_sets),
    )


def _uncatalogued_method(key: str, kind: CatalogueMethodKind, usage: _Usage) -> UncataloguedMethod:
    family, rung = (None, None) if kind is CatalogueMethodKind.dispersion_correction else _classification([key])
    return UncataloguedMethod(
        key=key,
        kind=kind,
        family=family,
        dft_rung=rung,
        spellings_seen=sorted(usage.spellings),
        usage=usage.summary(),
    )


def browse_methods(
    session: Session,
    *,
    kind: CatalogueMethodKind | None = None,
    family: MethodFamily | None = None,
) -> MethodCatalogueBrowseResponse:
    """Every method and dispersion correction in the catalogue, in key order, and the keys in use that none names."""
    index = load_catalogue_index(session)
    cards = [
        card
        for card in index.methods.values()
        if (kind is None or card.kind == kind) and (family is None or card.family == family)
    ]
    uncatalogued = [
        _uncatalogued_method(key, item_kind, usage)
        for (key, item_kind), usage in sorted(
            index.uncatalogued_methods.items(), key=lambda kv: (kv[0][0], kv[0][1].value)
        )
    ]
    return MethodCatalogueBrowseResponse(
        request=CatalogueRequestEcho(),
        review_summary=ReviewStatusSummary(),
        records=cards,
        uncatalogued=[
            item
            for item in uncatalogued
            if (kind is None or item.kind == kind) and (family is None or item.family == family)
        ],
    )


def browse_basis_sets(
    session: Session,
    *,
    role: BasisSetRole | None = None,
    family: str | None = None,
) -> BasisSetCatalogueBrowseResponse:
    """Every basis set in the catalogue, in key order, and the keys in use that none names."""
    index = load_catalogue_index(session)
    cards = [
        card
        for card in index.basis_sets.values()
        if (role is None or card.role == role) and (family is None or card.family == family)
    ]
    uncatalogued = (
        [
            UncataloguedBasisSet(key=key, spellings_seen=sorted(usage.spellings), usage=usage.summary())
            for key, usage in sorted(index.uncatalogued_basis_sets.items())
        ]
        if role is None and family is None
        else []
    )
    return BasisSetCatalogueBrowseResponse(
        request=CatalogueRequestEcho(), review_summary=ReviewStatusSummary(), records=cards, uncatalogued=uncatalogued
    )


def _resolve(handle: str, prefix: str, refs: dict[str, str], key_maps: tuple[tuple[dict[str, str], object], ...]):
    """The entry key a handle names: a catalogue ref, or a spelling that keys to an entry."""
    handle = handle.strip()
    if handle.lower().startswith(prefix):
        return refs.get(handle.lower())
    for key_map, key_fn in key_maps:
        key = key_fn(handle)  # type: ignore[operator]
        if key is not None and key in key_map:
            return key_map[key]
    return None


def get_method(session: Session, *, handle: str) -> MethodCatalogueDetailResponse:
    """One method or dispersion correction, by ``mcat_`` ref or by any spelling that keys to it."""
    index = load_catalogue_index(session)
    entry = _resolve(
        handle,
        _METHOD_PREFIX,
        {card.catalogue_ref: key for key, card in index.methods.items()},
        ((index.method_by_key, method_identity_key), (index.dispersion_by_key, dispersion_identity_key)),
    )
    if entry is None:
        raise not_found("method", ref=handle, code="handle_not_found")
    return MethodCatalogueDetailResponse(
        request=CatalogueRequestEcho(),
        review_summary=ReviewStatusSummary(),
        record=MethodCatalogueDetailRecord(method=index.methods[entry], levels=index.method_usage[entry].level_rows()),
    )


def get_basis_set(session: Session, *, handle: str) -> BasisSetCatalogueDetailResponse:
    """One basis set, by ``bcat_`` ref or by any spelling that keys to it."""
    index = load_catalogue_index(session)
    entry = _resolve(
        handle,
        _BASIS_PREFIX,
        {card.catalogue_ref: key for key, card in index.basis_sets.items()},
        ((index.basis_set_by_key, basis_identity_key),),
    )
    if entry is None:
        raise not_found("basis_set", ref=handle, code="handle_not_found")
    return BasisSetCatalogueDetailResponse(
        request=CatalogueRequestEcho(),
        review_summary=ReviewStatusSummary(),
        record=BasisSetCatalogueDetailRecord(
            basis_set=index.basis_sets[entry], levels=index.basis_set_usage[entry].level_rows()
        ),
    )


__all__ = [
    "CatalogueIndex",
    "browse_basis_sets",
    "browse_methods",
    "get_basis_set",
    "get_method",
    "load_catalogue_index",
]
