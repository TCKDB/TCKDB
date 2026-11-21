"""Read and validate the method and basis-set catalogue seed (``app/data/catalogue/*.yml``).

Pure: no database. :func:`load_seed` returns a :class:`CatalogueSeed` or raises :class:`SeedError` naming every
problem it found. The database side is :mod:`app.services.method_catalogue_sync`.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.chemistry.basis_set_names import basis_identity_key
from app.chemistry.dispersion_names import dispersion_identity_key
from app.chemistry.method_families import MethodFamily
from app.chemistry.method_names import method_identity_key
from app.db.models.common import BasisSetRole, CatalogueMethodKind, CatalogueReferenceRole
from app.services.literature_metadata import normalize_doi

SEED_DIR = Path(__file__).resolve().parents[1] / "data" / "catalogue"

#: The basis-set families an entry may name.
BASIS_SET_FAMILIES: frozenset[str] = frozenset(
    {
        "Karlsruhe def2",
        "Dunning correlation-consistent",
        "Peterson correlation-consistent F12",
        "Pople",
    }
)


class SeedError(ValueError):
    """The seed files are inconsistent; the message lists every problem."""


@dataclass(frozen=True)
class SeedReference:
    """One article, as its Crossref record gives it."""

    doi: str  # normalised (lower-cased) DOI
    title: str
    journal: str
    year: int
    volume: str | None
    issue: str | None
    pages: str | None
    authors: tuple[str, ...]
    verified_source: str
    verified_date: datetime.date


@dataclass(frozen=True)
class SeedCitation:
    doi: str  # normalised DOI
    role: CatalogueReferenceRole


@dataclass(frozen=True)
class SeedMethod:
    key: str
    aliases: tuple[str, ...]
    kind: CatalogueMethodKind
    family: MethodFamily | None
    short_name: str
    full_name: str
    description: str
    references: tuple[SeedCitation, ...]

    @property
    def keys(self) -> tuple[str, ...]:
        return (self.key, *self.aliases)


@dataclass(frozen=True)
class SeedBasisSet:
    key: str
    aliases: tuple[str, ...]
    role: BasisSetRole
    family: str
    short_name: str
    full_name: str
    description: str
    references: tuple[SeedCitation, ...]

    @property
    def keys(self) -> tuple[str, ...]:
        return (self.key, *self.aliases)


@dataclass(frozen=True)
class CatalogueSeed:
    method_seed_version: int
    basis_set_seed_version: int
    methods: tuple[SeedMethod, ...]
    basis_sets: tuple[SeedBasisSet, ...]
    references: dict[str, SeedReference]  # by normalised DOI, in file order
    known_uncatalogued_basis_sets: tuple[str, ...]


def _read(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise SeedError(f"{path.name}: expected a mapping at the top level")
    return data


def _text(entry: dict[str, Any], field: str, where: str, problems: list[str]) -> str:
    value = entry.get(field)
    if not isinstance(value, str) or not value.strip():
        problems.append(f"{where}: {field} must be non-empty text")
        return ""
    return " ".join(value.split())


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def _seed_version(data: dict[str, Any], name: str, problems: list[str]) -> int:
    version = data.get("seed_version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        problems.append(f"{name}: seed_version must be an integer >= 1")
        return 1
    return version


def _references(data: dict[str, Any], problems: list[str]) -> dict[str, SeedReference]:
    out: dict[str, SeedReference] = {}
    for i, entry in enumerate(data.get("references") or []):
        where = f"literature.yml references[{i}]"
        doi = normalize_doi(entry.get("doi"))
        if doi is None:
            problems.append(f"{where}: doi is missing")
            continue
        if doi in out:
            problems.append(f"{where}: duplicate doi {doi}")
            continue
        if entry.get("kind") != "article":
            problems.append(f"{where}: kind must be article")
        verified = entry.get("verified") or {}
        year = entry.get("year")
        if not isinstance(year, int):
            problems.append(f"{where}: year must be an integer")
            year = 0
        if verified.get("source") != "crossref" or not isinstance(verified.get("date"), datetime.date):
            problems.append(f"{where}: verified must name source crossref and a date")
        authors = entry.get("authors") or []
        if not authors or not all(isinstance(a, str) and a.strip() for a in authors):
            problems.append(f"{where}: authors must list at least one family name")
        out[doi] = SeedReference(
            doi=doi,
            title=_text(entry, "title", where, problems),
            journal=_text(entry, "journal", where, problems),
            year=year,
            volume=_optional_text(entry.get("volume")),
            issue=_optional_text(entry.get("issue")),
            pages=_optional_text(entry.get("pages")),
            authors=tuple(str(a).strip() for a in authors),
            verified_source=str(verified.get("source")),
            verified_date=verified.get("date") or datetime.date.min,
        )
    return out


def _citations(entry: dict[str, Any], where: str, references: dict[str, SeedReference], problems: list[str]):
    citations: list[SeedCitation] = []
    seen: set[str] = set()
    raw = entry.get("references") or []
    if not raw:
        problems.append(f"{where}: at least one reference is required")
    for j, ref in enumerate(raw):
        doi = normalize_doi(str(ref.get("doi"))) if isinstance(ref, dict) else None
        if doi is None or doi not in references:
            problems.append(f"{where}: references[{j}] names a DOI not in literature.yml")
            continue
        if doi in seen:
            problems.append(f"{where}: references[{j}] repeats {doi}")
            continue
        try:
            role = CatalogueReferenceRole(ref.get("role"))
        except ValueError:
            problems.append(f"{where}: references[{j}] role must be one of {[r.value for r in CatalogueReferenceRole]}")
            continue
        seen.add(doi)
        citations.append(SeedCitation(doi=doi, role=role))
    return tuple(citations)


def _keys(entry: dict[str, Any], where: str, key_fn, problems: list[str]) -> tuple[str, tuple[str, ...]]:
    key = entry.get("key")
    aliases = tuple(str(a) for a in (entry.get("aliases") or []))
    for k in (key, *aliases):
        if not isinstance(k, str) or key_fn(k) != k:
            problems.append(f"{where}: {k!r} is not a fixed point of {key_fn.__name__}")
    return str(key), aliases


def _methods(data, references, problems) -> tuple[SeedMethod, ...]:
    out: list[SeedMethod] = []
    for i, entry in enumerate(data.get("methods") or []):
        where = f"methods.yml {entry.get('key', i)!s}"
        try:
            kind = CatalogueMethodKind(entry.get("kind"))
        except ValueError:
            problems.append(f"{where}: kind must be one of {[k.value for k in CatalogueMethodKind]}")
            continue
        key_fn = dispersion_identity_key if kind is CatalogueMethodKind.dispersion_correction else method_identity_key
        key, aliases = _keys(entry, where, key_fn, problems)
        family: MethodFamily | None = None
        if kind is CatalogueMethodKind.method:
            try:
                family = MethodFamily(entry.get("family"))
            except ValueError:
                problems.append(f"{where}: family must be one of {[f.value for f in MethodFamily]}")
        elif entry.get("family") is not None:
            problems.append(f"{where}: a dispersion correction has no family")
        out.append(
            SeedMethod(
                key=key,
                aliases=aliases,
                kind=kind,
                family=family,
                short_name=_text(entry, "short_name", where, problems),
                full_name=_text(entry, "full_name", where, problems),
                description=_text(entry, "description", where, problems),
                references=_citations(entry, where, references, problems),
            )
        )
    return tuple(out)


def _basis_sets(data, references, problems) -> tuple[SeedBasisSet, ...]:
    out: list[SeedBasisSet] = []
    for i, entry in enumerate(data.get("basis_sets") or []):
        where = f"basis_sets.yml {entry.get('key', i)!s}"
        try:
            role = BasisSetRole(entry.get("role"))
        except ValueError:
            problems.append(f"{where}: role must be one of {[r.value for r in BasisSetRole]}")
            continue
        key, aliases = _keys(entry, where, basis_identity_key, problems)
        family = _text(entry, "family", where, problems)
        if family and family not in BASIS_SET_FAMILIES:
            problems.append(f"{where}: family {family!r} is not in BASIS_SET_FAMILIES")
        out.append(
            SeedBasisSet(
                key=key,
                aliases=aliases,
                role=role,
                family=family,
                short_name=_text(entry, "short_name", where, problems),
                full_name=_text(entry, "full_name", where, problems),
                description=_text(entry, "description", where, problems),
                references=_citations(entry, where, references, problems),
            )
        )
    return tuple(out)


def _unique_keys(name: str, entries, problems: list[str]) -> None:
    owner: dict[str, str] = {}
    for entry in entries:
        for k in entry.keys:
            if k in owner:
                problems.append(f"{name}: key {k!r} names both {owner[k]!r} and {entry.key!r}")
            owner[k] = entry.key


def load_seed(seed_dir: Path = SEED_DIR) -> CatalogueSeed:
    """Read the three seed files and check them against each other.

    :raises SeedError: listing every inconsistency found.
    """
    problems: list[str] = []
    literature = _read(seed_dir / "literature.yml")
    methods_data = _read(seed_dir / "methods.yml")
    basis_data = _read(seed_dir / "basis_sets.yml")

    references = _references(literature, problems)
    methods = _methods(methods_data, references, problems)
    basis_sets = _basis_sets(basis_data, references, problems)
    _unique_keys("methods.yml", methods, problems)
    _unique_keys("basis_sets.yml", basis_sets, problems)

    cited = {c.doi for e in (*methods, *basis_sets) for c in e.references}
    for doi in references:
        if doi not in cited:
            problems.append(f"literature.yml: {doi} is cited by no entry")

    uncatalogued = tuple(str(k) for k in (basis_data.get("known_uncatalogued") or []))
    catalogued = {k for e in basis_sets for k in e.keys}
    for k in uncatalogued:
        if basis_identity_key(k) != k or k in catalogued:
            problems.append(f"basis_sets.yml: known_uncatalogued {k!r} must be an uncatalogued identity key")

    method_seed_version = _seed_version(methods_data, "methods.yml", problems)
    basis_set_seed_version = _seed_version(basis_data, "basis_sets.yml", problems)
    if problems:
        raise SeedError("method catalogue seed is inconsistent:\n  " + "\n  ".join(problems))
    return CatalogueSeed(
        method_seed_version=method_seed_version,
        basis_set_seed_version=basis_set_seed_version,
        methods=methods,
        basis_sets=basis_sets,
        references=references,
        known_uncatalogued_basis_sets=uncatalogued,
    )
