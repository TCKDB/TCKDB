"""Service implementation for /api/v1/scientific/species/structure-search.

Public RDKit-backed structure search over species entries. The service is
the first consumer of the PostgreSQL RDKit cartridge in the read layer —
substructure matching uses the cartridge's ``mol @> qmol`` operator and
similarity uses ``tanimoto_sml(morganbv_fp(...), morganbv_fp(...))`` over
Morgan-bit fingerprints.

Query parsing and InChIKey canonicalization happen on the Python side
via RDKit; the canonicalized SMILES (or SMARTS / InChIKey) is then bound
into a parameterized SQL statement that performs the actual filter and
score computation database-side. We never load every species row into
Python — that would defeat the cartridge.

Substructure and similarity queries read from the stored
``species_entry.mol`` cartridge column. The write path
(``app/services/species_resolution.py``) canonicalizes SMILES into
``mol`` on insert, and the
``d4e5f6a7b8c9_add_species_entry_mol_gist_index`` migration creates a
GiST index on the column and backfills any pre-existing NULL rows.
Rows whose ``mol`` is NULL (e.g. a SMILES the cartridge cannot parse)
are excluded from results — a single seq-scan-per-row fallback would
defeat the index. Exact mode keeps the indexed ``species.inchi_key``
path; it does not need the cartridge.

See ``backend/docs/specs/scientific_structure_search.md`` for the full
contract.
"""

from __future__ import annotations

from typing import Any

from rdkit import Chem
from rdkit.Chem import inchi as _inchi
from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from app.db.models.common import (
    RecordReviewStatus,
    SubmissionRecordType,
)
from app.db.models.species import Species, SpeciesEntry
from app.schemas.reads.scientific_common import (
    REVIEW_RANK,
    RecordReviewBadge,
)
from app.schemas.reads.scientific_structure_search import (
    DEFAULT_SIMILARITY_THRESHOLD,
    RequestEcho,
    ScientificSpeciesStructureSearchRecord,
    ScientificSpeciesStructureSearchRequest,
    ScientificSpeciesStructureSearchResponse,
    StructureMatchSummary,
    StructureQueryKind,
    StructureSearchMode,
)
from app.services.scientific_read.common import (
    build_pagination,
    fetch_review_badges,
    reject_client_sort,
    review_summary,
    validate_includes,
    validate_pagination,
    visible_statuses,
)
from app.services.scientific_read.internal_ids import (
    filter_internal_ids_from_resolved,
)


_LEGAL_INCLUDE_TOKENS: set[str] = {"review", "internal_ids", "all"}
_INTERNAL_INCLUDE_TOKENS: set[str] = {"internal_ids"}


_DEFAULT_SORT_BY_MODE: dict[StructureSearchMode, str] = {
    StructureSearchMode.substructure: "review_rank,species_entry_id",
    StructureSearchMode.similarity: (
        "similarity_score_desc,review_rank,species_entry_id"
    ),
    StructureSearchMode.exact: "review_rank,species_entry_id",
}


# Mode → which query fields are accepted.
_MODE_QUERY_KIND_RULES: dict[StructureSearchMode, set[StructureQueryKind]] = {
    StructureSearchMode.substructure: {
        StructureQueryKind.smiles,
        StructureQueryKind.smarts,
    },
    StructureSearchMode.similarity: {
        StructureQueryKind.smiles,
        StructureQueryKind.inchi,
    },
    StructureSearchMode.exact: {
        StructureQueryKind.smiles,
        StructureQueryKind.inchi,
        StructureQueryKind.inchi_key,
    },
}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def search_species_by_structure(
    session: Session,
    request: ScientificSpeciesStructureSearchRequest,
) -> ScientificSpeciesStructureSearchResponse:
    """Run an RDKit-backed structure search at species-entry grain.

    :raises ValueError: 422 for sort, pagination, include, missing /
        multiple structure queries, mode-query mismatch, invalid
        structure, similarity-threshold violations.
    """
    reject_client_sort(request.sort)
    offset, limit = validate_pagination(request.offset, request.limit)
    includes = validate_includes(
        request.include,
        _LEGAL_INCLUDE_TOKENS,
        "/scientific/species/structure-search",
        internal_tokens=_INTERNAL_INCLUDE_TOKENS,
    )
    includes = filter_internal_ids_from_resolved(includes)

    query_kind, query_value = _select_structure_query(request)
    _enforce_mode_query_compatibility(request.mode, query_kind)
    threshold = _resolve_similarity_threshold(request)

    # Dispatch to the per-mode SQL builder. Each builder returns a list
    # of ``(species_entry_id, species_id, similarity_score)`` rows (with
    # ``similarity_score`` None for non-similarity modes).
    if request.mode is StructureSearchMode.substructure:
        candidate_rows = _run_substructure_query(
            session,
            query_kind=query_kind,
            query_value=query_value,
        )
    elif request.mode is StructureSearchMode.similarity:
        candidate_rows = _run_similarity_query(
            session,
            query_kind=query_kind,
            query_value=query_value,
            threshold=threshold,
        )
    else:  # exact
        candidate_rows = _run_exact_query(
            session,
            query_kind=query_kind,
            query_value=query_value,
        )

    if not candidate_rows:
        return _empty_response(request, includes, offset, limit, threshold)

    entry_ids = [row[0] for row in candidate_rows]
    score_by_entry: dict[int, float | None] = {
        row[0]: row[2] for row in candidate_rows
    }

    # Trust gate: hide rejected / deprecated entries unless opted in.
    badges = fetch_review_badges(
        session,
        record_type=SubmissionRecordType.species_entry,
        record_ids=entry_ids,
    )
    visible = visible_statuses(
        min_review_status=request.min_review_status,
        include_rejected=request.include_rejected,
        include_deprecated=request.include_deprecated,
    )
    visible_entry_ids = [
        eid for eid in entry_ids if badges[eid].status in visible
    ]
    if not visible_entry_ids:
        return _empty_response(request, includes, offset, limit, threshold)

    # Materialize the visible entries with their parent species so we
    # can build the record envelope. One round trip each.
    entries = _load_entries(session, visible_entry_ids)
    species_by_id = _load_species(
        session, [e.species_id for e in entries.values()]
    )

    summary = review_summary(badges[eid] for eid in visible_entry_ids)

    # Deterministic ordering per mode.
    sorted_ids = _apply_sort(
        request.mode,
        visible_entry_ids,
        badges=badges,
        score_by_entry=score_by_entry,
    )
    total = len(sorted_ids)
    page_ids = sorted_ids[offset : offset + limit]

    records: list[ScientificSpeciesStructureSearchRecord] = []
    for eid in page_ids:
        entry = entries.get(eid)
        if entry is None:  # pragma: no cover — race with delete
            continue
        species = species_by_id.get(entry.species_id)
        if species is None:  # pragma: no cover — FK guarantees presence
            continue
        records.append(
            _build_record(
                entry=entry,
                species=species,
                badge=badges[eid],
                mode=request.mode,
                similarity_score=score_by_entry.get(eid),
                query_kind=query_kind,
                query_value=query_value,
            )
        )

    return ScientificSpeciesStructureSearchResponse(
        request=RequestEcho(
            filter=_request_filter_echo(request, threshold=threshold),
            mode=request.mode,
            sort=_DEFAULT_SORT_BY_MODE[request.mode],
            include=sorted(includes),
        ),
        review_summary=summary,
        records=records,
        pagination=build_pagination(
            offset=offset, limit=limit, returned=len(records), total=total
        ),
    )


# ---------------------------------------------------------------------------
# Query selection & validation
# ---------------------------------------------------------------------------


def _select_structure_query(
    request: ScientificSpeciesStructureSearchRequest,
) -> tuple[StructureQueryKind, str]:
    """Pick the single structure query field and return (kind, value).

    Raises ValueError → 422 if zero or more than one query field is
    supplied.
    """
    supplied: list[tuple[StructureQueryKind, str]] = []
    if request.query_smiles is not None:
        supplied.append((StructureQueryKind.smiles, request.query_smiles))
    if request.query_smarts is not None:
        supplied.append((StructureQueryKind.smarts, request.query_smarts))
    if request.query_inchi is not None:
        supplied.append((StructureQueryKind.inchi, request.query_inchi))
    if request.query_inchi_key is not None:
        supplied.append(
            (StructureQueryKind.inchi_key, request.query_inchi_key)
        )

    if not supplied:
        raise ValueError(
            "missing_structure_query: exactly one of {query_smiles, "
            "query_smarts, query_inchi, query_inchi_key} must be supplied."
        )
    if len(supplied) > 1:
        names = sorted(k.value for k, _ in supplied)
        raise ValueError(
            "multiple_structure_queries: exactly one structure query "
            f"field is allowed; got {names!r}."
        )
    return supplied[0]


def _enforce_mode_query_compatibility(
    mode: StructureSearchMode, kind: StructureQueryKind
) -> None:
    """Reject mode/query-field combinations that the cartridge does not
    support cleanly (e.g. similarity-by-InChIKey)."""
    allowed = _MODE_QUERY_KIND_RULES[mode]
    if kind not in allowed:
        raise ValueError(
            f"invalid_structure_query: mode={mode.value!r} does not "
            f"accept query_{kind.value}; supported query kinds for this "
            f"mode are {sorted(k.value for k in allowed)!r}."
        )


def _resolve_similarity_threshold(
    request: ScientificSpeciesStructureSearchRequest,
) -> float:
    """Resolve the effective similarity threshold for a request.

    For non-similarity modes the value is unused; we still return the
    default so the request echo stays consistent.
    """
    if request.similarity_threshold is not None:
        return request.similarity_threshold
    return DEFAULT_SIMILARITY_THRESHOLD


# ---------------------------------------------------------------------------
# RDKit parsing helpers
# ---------------------------------------------------------------------------


def _parse_smiles_to_canonical(smiles: str) -> str:
    """Parse a SMILES via RDKit and return its canonical SMILES.

    Used to normalize callers' inputs before binding into SQL so the
    cartridge sees a parseable molecule we already validated client-side.
    """
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise ValueError(
            "invalid_structure_query: RDKit could not parse the SMILES "
            "supplied as query_smiles."
        )
    canonical = Chem.MolToSmiles(mol, canonical=True)
    if not canonical:
        raise ValueError(
            "invalid_structure_query: RDKit produced an empty canonical "
            "SMILES from query_smiles."
        )
    return canonical


def _parse_smarts(smarts: str) -> Chem.Mol:
    mol = Chem.MolFromSmarts(smarts)
    if mol is None:
        raise ValueError(
            "invalid_structure_query: RDKit could not parse the SMARTS "
            "supplied as query_smarts."
        )
    return mol


def _parse_inchi_to_canonical_smiles(inchi_str: str) -> str:
    mol = Chem.MolFromInchi(inchi_str)
    if mol is None:
        raise ValueError(
            "invalid_structure_query: RDKit could not parse the InChI "
            "supplied as query_inchi."
        )
    return Chem.MolToSmiles(mol, canonical=True)


def _inchi_key_from_query(
    kind: StructureQueryKind, value: str
) -> str:
    """Compute the canonical InChIKey for an exact-mode query."""
    if kind is StructureQueryKind.inchi_key:
        return value
    if kind is StructureQueryKind.smiles:
        mol = Chem.MolFromSmiles(value)
        if mol is None:
            raise ValueError(
                "invalid_structure_query: RDKit could not parse the "
                "SMILES supplied as query_smiles."
            )
        return _inchi.MolToInchiKey(mol)
    if kind is StructureQueryKind.inchi:
        mol = Chem.MolFromInchi(value)
        if mol is None:
            raise ValueError(
                "invalid_structure_query: RDKit could not parse the "
                "InChI supplied as query_inchi."
            )
        return _inchi.MolToInchiKey(mol)
    raise ValueError(
        "invalid_structure_query: exact mode does not accept this "
        "query kind."
    )


# ---------------------------------------------------------------------------
# Per-mode SQL execution
# ---------------------------------------------------------------------------


# Stored cartridge column. Reading from ``se.mol`` directly lets the
# GiST index (``ix_species_entry_mol_gist``) drive substructure /
# similarity queries instead of running ``mol_from_smiles(sp.smiles)``
# per row at request time. The write path (``species_resolution.py``)
# populates the column on insert; the d4e5f6a7b8c9 migration backfilled
# any pre-existing NULL rows and created the index. NULL ``se.mol``
# rows (e.g. unparseable SMILES) are excluded from matches in the
# ``WHERE`` clause below — a row-level fallback would defeat the index.
_STORED_MOL_EXPR = "se.mol"


def _run_substructure_query(
    session: Session,
    *,
    query_kind: StructureQueryKind,
    query_value: str,
) -> list[tuple[int, int, float | None]]:
    """Return matching ``(species_entry_id, species_id, None)`` rows.

    The cartridge ``@>`` operator evaluates the substructure containment
    test database-side; we never iterate Python-side over species.
    """
    if query_kind is StructureQueryKind.smarts:
        # Parse client-side to surface a 422 before issuing SQL.
        _parse_smarts(query_value)
        query_expr = "qmol_from_smarts(:query_text)"
        bound_value = query_value
    elif query_kind is StructureQueryKind.smiles:
        bound_value = _parse_smiles_to_canonical(query_value)
        query_expr = "mol_from_smiles(:query_text)"
    else:  # pragma: no cover — guarded by _enforce_mode_query_compatibility
        raise ValueError(
            "invalid_structure_query: substructure mode requires "
            "query_smiles or query_smarts."
        )

    sql = text(
        f"""
        SELECT se.id AS species_entry_id, sp.id AS species_id
        FROM species_entry AS se
        JOIN species AS sp ON sp.id = se.species_id
        WHERE {_STORED_MOL_EXPR} IS NOT NULL
          AND {_STORED_MOL_EXPR} @> {query_expr}
        """
    )
    rows = session.execute(sql, {"query_text": bound_value}).all()
    return [(row.species_entry_id, row.species_id, None) for row in rows]


def _run_similarity_query(
    session: Session,
    *,
    query_kind: StructureQueryKind,
    query_value: str,
    threshold: float,
) -> list[tuple[int, int, float | None]]:
    """Return ``(entry_id, species_id, score)`` rows above ``threshold``.

    Uses ``tanimoto_sml(morganbv_fp(stored_mol), morganbv_fp(query_mol))``;
    the threshold filter is applied database-side via a parameterized
    HAVING-style ``WHERE``.
    """
    if query_kind is StructureQueryKind.smiles:
        canonical_smiles = _parse_smiles_to_canonical(query_value)
    elif query_kind is StructureQueryKind.inchi:
        canonical_smiles = _parse_inchi_to_canonical_smiles(query_value)
    else:  # pragma: no cover — guarded by _enforce_mode_query_compatibility
        raise ValueError(
            "invalid_structure_query: similarity mode requires "
            "query_smiles or query_inchi."
        )

    sql = text(
        f"""
        SELECT
            se.id AS species_entry_id,
            sp.id AS species_id,
            tanimoto_sml(
                morganbv_fp({_STORED_MOL_EXPR}),
                morganbv_fp(mol_from_smiles(:query_text))
            ) AS similarity_score
        FROM species_entry AS se
        JOIN species AS sp ON sp.id = se.species_id
        WHERE {_STORED_MOL_EXPR} IS NOT NULL
          AND tanimoto_sml(
                morganbv_fp({_STORED_MOL_EXPR}),
                morganbv_fp(mol_from_smiles(:query_text))
              ) >= :threshold
        """
    ).bindparams(bindparam("threshold", value=threshold))

    rows = session.execute(
        sql, {"query_text": canonical_smiles, "threshold": threshold}
    ).all()
    return [
        (row.species_entry_id, row.species_id, float(row.similarity_score))
        for row in rows
    ]


def _run_exact_query(
    session: Session,
    *,
    query_kind: StructureQueryKind,
    query_value: str,
) -> list[tuple[int, int, float | None]]:
    """Return entries whose canonical InChIKey matches the query.

    Computes the query's InChIKey via RDKit and matches against the
    indexed ``species.inchi_key`` column — no cartridge call required
    for the actual lookup. SMARTS is rejected upstream by the mode/query
    compatibility check.
    """
    target_key = _inchi_key_from_query(query_kind, query_value)
    sql = text(
        """
        SELECT se.id AS species_entry_id, sp.id AS species_id
        FROM species_entry AS se
        JOIN species AS sp ON sp.id = se.species_id
        WHERE sp.inchi_key = :inchi_key
        """
    )
    rows = session.execute(sql, {"inchi_key": target_key}).all()
    return [(row.species_entry_id, row.species_id, None) for row in rows]


# ---------------------------------------------------------------------------
# Loading + record building
# ---------------------------------------------------------------------------


def _load_entries(
    session: Session, entry_ids: list[int]
) -> dict[int, SpeciesEntry]:
    if not entry_ids:
        return {}
    rows = (
        session.query(SpeciesEntry)
        .filter(SpeciesEntry.id.in_(entry_ids))
        .all()
    )
    return {e.id: e for e in rows}


def _load_species(
    session: Session, species_ids: list[int]
) -> dict[int, Species]:
    if not species_ids:
        return {}
    rows = (
        session.query(Species).filter(Species.id.in_(species_ids)).all()
    )
    return {s.id: s for s in rows}


def _apply_sort(
    mode: StructureSearchMode,
    entry_ids: list[int],
    *,
    badges: dict[int, RecordReviewBadge],
    score_by_entry: dict[int, float | None],
) -> list[int]:
    """Return ``entry_ids`` ordered per the per-mode default sort."""
    if mode is StructureSearchMode.similarity:
        # similarity_score DESC, review_rank ASC, species_entry_id DESC
        def key(eid: int) -> tuple:
            score = score_by_entry.get(eid) or 0.0
            return (-score, REVIEW_RANK[badges[eid].status], -eid)

        return sorted(entry_ids, key=key)
    # substructure / exact: review_rank ASC, species_entry_id DESC
    return sorted(
        entry_ids,
        key=lambda eid: (REVIEW_RANK[badges[eid].status], -eid),
    )


def _build_record(
    *,
    entry: SpeciesEntry,
    species: Species,
    badge: RecordReviewBadge,
    mode: StructureSearchMode,
    similarity_score: float | None,
    query_kind: StructureQueryKind,
    query_value: str,
) -> ScientificSpeciesStructureSearchRecord:
    return ScientificSpeciesStructureSearchRecord(
        species_ref=species.public_ref,
        species_id=species.id,
        species_entry_ref=entry.public_ref,
        species_entry_id=entry.id,
        smiles=species.smiles,
        inchi_key=species.inchi_key,
        charge=species.charge,
        multiplicity=species.multiplicity,
        species_entry_kind=entry.kind,
        electronic_state_kind=entry.electronic_state_kind,
        match=StructureMatchSummary(
            mode=mode,
            similarity_score=similarity_score,
            matched_query=query_value,
            matched_query_kind=query_kind,
        ),
        review=badge,
        endpoint=f"/api/v1/scientific/species-entries/{entry.public_ref}",
    )


# ---------------------------------------------------------------------------
# Echo + empty
# ---------------------------------------------------------------------------


def _request_filter_echo(
    request: ScientificSpeciesStructureSearchRequest,
    *,
    threshold: float,
) -> dict[str, Any]:
    echo: dict[str, Any] = {}
    for name in (
        "query_smiles",
        "query_smarts",
        "query_inchi",
        "query_inchi_key",
    ):
        value = getattr(request, name)
        if value is not None:
            echo[name] = value
    if request.mode is StructureSearchMode.similarity:
        # Always echo the effective threshold for similarity searches so
        # callers see the value they (implicitly or explicitly) ran with.
        echo["similarity_threshold"] = threshold
    if request.min_review_status is not None:
        echo["min_review_status"] = request.min_review_status.value
    if request.include_rejected:
        echo["include_rejected"] = True
    if request.include_deprecated:
        echo["include_deprecated"] = True
    return echo


def _empty_response(
    request: ScientificSpeciesStructureSearchRequest,
    includes: set[str],
    offset: int,
    limit: int,
    threshold: float,
) -> ScientificSpeciesStructureSearchResponse:
    return ScientificSpeciesStructureSearchResponse(
        request=RequestEcho(
            filter=_request_filter_echo(request, threshold=threshold),
            mode=request.mode,
            sort=_DEFAULT_SORT_BY_MODE[request.mode],
            include=sorted(includes),
        ),
        review_summary=review_summary([]),
        records=[],
        pagination=build_pagination(
            offset=offset, limit=limit, returned=0, total=0
        ),
    )


__all__ = ["search_species_by_structure"]
