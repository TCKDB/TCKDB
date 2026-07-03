"""Service implementation for the inverse literature → records endpoint.

One endpoint:

- ``GET /scientific/literature/{ref_or_id}/records``

Returns public-ref summaries of every scientific record with a
direct ``literature_id`` FK to the given literature row. Records
are flattened across types, filtered by review-visibility, ordered
deterministically, and paginated.

See ``backend/docs/specs/scientific_literature_reads.md``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import NotFoundError
from app.db.models.calculation import Calculation
from app.db.models.common import (
    RecordReviewStatus,
    SubmissionRecordType,
)
from app.db.models.kinetics import Kinetics
from app.db.models.literature import Literature
from app.db.models.network import Network
from app.db.models.network_pdep import NetworkSolve
from app.db.models.reaction import ChemReaction, ReactionEntry
from app.db.models.species import Species, SpeciesEntry
from app.db.models.statmech import Statmech
from app.db.models.thermo import Thermo
from app.db.models.transport import Transport
from app.schemas.reads.scientific_common import RecordReviewBadge
from app.schemas.reads.scientific_literature_records import (
    SUPPORTED_RECORD_TYPES,
    LiteratureLinkedRecordSummary,
    LiteratureRecordsRequest,
    RequestEcho,
    ScientificLiteratureRecordsResponse,
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
from app.services.scientific_read.handles import resolve_literature_handle
from app.services.scientific_read.internal_ids import (
    filter_internal_ids_from_resolved,
)

_LEGAL_INCLUDE_TOKENS: set[str] = {
    "review",
    "internal_ids",
    "all",
}
_INTERNAL_INCLUDE_TOKENS: set[str] = {"internal_ids"}


# ---------------------------------------------------------------------------
# Internal collected row
# ---------------------------------------------------------------------------


@dataclass
class _LinkedRow:
    """Flattened representation used during merge/sort/paginate.

    Holds the fields needed for the public ``LiteratureLinkedRecordSummary``
    plus auxiliary keys (``record_type``, ``record_id``,
    ``created_at``) used for visibility filtering and the
    deterministic sort.
    """

    record_type: str
    record_id: int
    record_ref: str
    created_at: datetime | None
    title: str | None = None
    label: str | None = None
    species_ref: str | None = None
    species_entry_ref: str | None = None
    reaction_ref: str | None = None
    reaction_entry_ref: str | None = None
    calculation_ref: str | None = None
    network_ref: str | None = None
    network_solve_ref: str | None = None
    role: str | None = None


# ---------------------------------------------------------------------------
# Public service
# ---------------------------------------------------------------------------


def get_literature_records(
    session: Session,
    request: LiteratureRecordsRequest,
    *,
    literature_handle: str,
) -> ScientificLiteratureRecordsResponse:
    """Resolve the literature handle and return its linked-record list.

    Behavior:

    - Wrong-prefix / malformed handle → 422.
    - Unknown handle → 404.
    - ``sort`` non-None → 422 ``client_sort_not_supported``.
    - ``record_type`` outside :data:`SUPPORTED_RECORD_TYPES` → 422
      ``unknown_record_type``.
    """
    reject_client_sort(request.sort)
    offset, limit = validate_pagination(request.offset, request.limit)

    includes = validate_includes(
        request.include,
        _LEGAL_INCLUDE_TOKENS,
        "/scientific/literature/{literature_ref_or_id}/records",
        internal_tokens=_INTERNAL_INCLUDE_TOKENS,
    )
    includes = filter_internal_ids_from_resolved(includes)

    if request.record_type is not None and request.record_type not in SUPPORTED_RECORD_TYPES:
        raise ValueError(
            "unknown_record_type: "
            f"{request.record_type!r} is not a supported record_type for "
            "/scientific/literature/{{handle}}/records. "
            f"Supported: {sorted(SUPPORTED_RECORD_TYPES)!r}"
        )

    lit_id = resolve_literature_handle(session, literature_handle)
    lit = session.get(Literature, lit_id)
    if lit is None:  # pragma: no cover — defended by resolver 404
        raise NotFoundError(
            f"literature not found (literature_id={lit_id})",
            code="handle_not_found",
        )

    selected_types: tuple[str, ...]
    if request.record_type is None:
        selected_types = SUPPORTED_RECORD_TYPES
    else:
        selected_types = (request.record_type,)

    rows = _collect_rows(session, literature_id=lit.id, types=selected_types)

    # Review visibility filtering. Each record_type whose record table
    # is reviewable gets its badge looked up; rejected/deprecated are
    # hidden unless the caller opts in.
    visible = visible_statuses(
        min_review_status=None,
        include_rejected=request.include_rejected,
        include_deprecated=request.include_deprecated,
    )
    badges = _load_badges(session, rows)
    rows = [
        r for r in rows
        if _is_visible(r.record_type, r.record_id, badges, visible)
    ]

    # Deterministic ordering: record_type ASC, created_at DESC NULLS LAST,
    # record_id DESC.
    rows.sort(
        key=lambda r: (
            r.record_type,
            r.created_at is None,
            -(r.created_at.timestamp() if r.created_at is not None else 0),
            -r.record_id,
        )
    )

    total = len(rows)
    page = rows[offset : offset + limit]

    want_review = "review" in includes
    summaries = [
        _to_summary(r, badge=badges.get((r.record_type, r.record_id)), want_review=want_review)
        for r in page
    ]

    rs = review_summary(
        [b for (rt, _rid), b in badges.items() if _is_reviewable(rt)]
    )

    return ScientificLiteratureRecordsResponse(
        request=RequestEcho(
            filter=_echo_filter(request, lit_ref=lit.public_ref, lit_id=lit.id),
            sort="default",
            include=sorted(includes),
        ),
        review_summary=rs,
        records=summaries,
        pagination=build_pagination(
            offset=offset, limit=limit, returned=len(summaries), total=total
        ),
    )


# ---------------------------------------------------------------------------
# Per-type loaders → _LinkedRow
# ---------------------------------------------------------------------------


def _collect_rows(
    session: Session, *, literature_id: int, types: Iterable[str]
) -> list[_LinkedRow]:
    rows: list[_LinkedRow] = []
    types_set = set(types)
    if "calculation" in types_set:
        rows.extend(_load_calculations(session, literature_id))
    if "thermo" in types_set:
        rows.extend(_load_thermo(session, literature_id))
    if "kinetics" in types_set:
        rows.extend(_load_kinetics(session, literature_id))
    if "statmech" in types_set:
        rows.extend(_load_statmech(session, literature_id))
    if "transport" in types_set:
        rows.extend(_load_transport(session, literature_id))
    if "network" in types_set:
        rows.extend(_load_networks(session, literature_id))
    if "network_solve" in types_set:
        rows.extend(_load_network_solves(session, literature_id))
    return rows


def _load_calculations(
    session: Session, literature_id: int
) -> list[_LinkedRow]:
    rows = session.execute(
        select(
            Calculation.id,
            Calculation.public_ref,
            Calculation.type,
            Calculation.created_at,
            Calculation.species_entry_id,
            Calculation.transition_state_entry_id,
        ).where(Calculation.literature_id == literature_id)
    ).all()
    out: list[_LinkedRow] = []
    for r in rows:
        out.append(
            _LinkedRow(
                record_type="calculation",
                record_id=r.id,
                record_ref=r.public_ref,
                created_at=r.created_at,
                label=(
                    r.type.value if hasattr(r.type, "value") else str(r.type)
                ),
                calculation_ref=r.public_ref,
            )
        )
    return out


def _load_thermo(session: Session, literature_id: int) -> list[_LinkedRow]:
    rows = session.execute(
        select(
            Thermo.id,
            Thermo.public_ref,
            Thermo.species_entry_id,
            Thermo.created_at,
            SpeciesEntry.public_ref.label("species_entry_ref"),
            Species.public_ref.label("species_ref"),
            Species.smiles.label("smiles"),
        )
        .join(SpeciesEntry, SpeciesEntry.id == Thermo.species_entry_id)
        .join(Species, Species.id == SpeciesEntry.species_id)
        .where(Thermo.literature_id == literature_id)
    ).all()
    return [
        _LinkedRow(
            record_type="thermo",
            record_id=r.id,
            record_ref=r.public_ref,
            created_at=r.created_at,
            species_ref=r.species_ref,
            species_entry_ref=r.species_entry_ref,
            label=r.smiles,
        )
        for r in rows
    ]


def _load_kinetics(session: Session, literature_id: int) -> list[_LinkedRow]:
    rows = session.execute(
        select(
            Kinetics.id,
            Kinetics.public_ref,
            Kinetics.created_at,
            Kinetics.model_kind,
            ReactionEntry.public_ref.label("reaction_entry_ref"),
            ChemReaction.public_ref.label("reaction_ref"),
        )
        .join(ReactionEntry, ReactionEntry.id == Kinetics.reaction_entry_id)
        .join(ChemReaction, ChemReaction.id == ReactionEntry.reaction_id)
        .where(Kinetics.literature_id == literature_id)
    ).all()
    return [
        _LinkedRow(
            record_type="kinetics",
            record_id=r.id,
            record_ref=r.public_ref,
            created_at=r.created_at,
            reaction_ref=r.reaction_ref,
            reaction_entry_ref=r.reaction_entry_ref,
            label=(
                r.model_kind.value
                if hasattr(r.model_kind, "value")
                else str(r.model_kind)
            ),
        )
        for r in rows
    ]


def _load_statmech(session: Session, literature_id: int) -> list[_LinkedRow]:
    rows = session.execute(
        select(
            Statmech.id,
            Statmech.public_ref,
            Statmech.created_at,
            Statmech.statmech_treatment,
            SpeciesEntry.public_ref.label("species_entry_ref"),
            Species.public_ref.label("species_ref"),
        )
        .join(SpeciesEntry, SpeciesEntry.id == Statmech.species_entry_id)
        .join(Species, Species.id == SpeciesEntry.species_id)
        .where(Statmech.literature_id == literature_id)
    ).all()
    return [
        _LinkedRow(
            record_type="statmech",
            record_id=r.id,
            record_ref=r.public_ref,
            created_at=r.created_at,
            species_ref=r.species_ref,
            species_entry_ref=r.species_entry_ref,
            label=(
                r.statmech_treatment.value
                if hasattr(r.statmech_treatment, "value")
                else (
                    str(r.statmech_treatment)
                    if r.statmech_treatment is not None
                    else None
                )
            ),
        )
        for r in rows
    ]


def _load_transport(session: Session, literature_id: int) -> list[_LinkedRow]:
    rows = session.execute(
        select(
            Transport.id,
            Transport.public_ref,
            Transport.created_at,
            SpeciesEntry.public_ref.label("species_entry_ref"),
            Species.public_ref.label("species_ref"),
            Species.smiles.label("smiles"),
        )
        .join(SpeciesEntry, SpeciesEntry.id == Transport.species_entry_id)
        .join(Species, Species.id == SpeciesEntry.species_id)
        .where(Transport.literature_id == literature_id)
    ).all()
    return [
        _LinkedRow(
            record_type="transport",
            record_id=r.id,
            record_ref=r.public_ref,
            created_at=r.created_at,
            species_ref=r.species_ref,
            species_entry_ref=r.species_entry_ref,
            label=r.smiles,
        )
        for r in rows
    ]


def _load_networks(session: Session, literature_id: int) -> list[_LinkedRow]:
    rows = session.execute(
        select(
            Network.id,
            Network.public_ref,
            Network.created_at,
            Network.name,
        ).where(Network.literature_id == literature_id)
    ).all()
    return [
        _LinkedRow(
            record_type="network",
            record_id=r.id,
            record_ref=r.public_ref,
            created_at=r.created_at,
            title=r.name,
            network_ref=r.public_ref,
        )
        for r in rows
    ]


def _load_network_solves(
    session: Session, literature_id: int
) -> list[_LinkedRow]:
    rows = session.execute(
        select(
            NetworkSolve.id,
            NetworkSolve.public_ref,
            NetworkSolve.created_at,
            NetworkSolve.me_method,
            Network.public_ref.label("network_ref"),
        )
        .join(Network, Network.id == NetworkSolve.network_id)
        .where(NetworkSolve.literature_id == literature_id)
    ).all()
    return [
        _LinkedRow(
            record_type="network_solve",
            record_id=r.id,
            record_ref=r.public_ref,
            created_at=r.created_at,
            label=r.me_method,
            network_ref=r.network_ref,
            network_solve_ref=r.public_ref,
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Review handling
# ---------------------------------------------------------------------------


# Map record_type → SubmissionRecordType when the type is reviewable.
_REVIEWABLE: dict[str, SubmissionRecordType] = {
    "calculation": SubmissionRecordType.calculation,
    "thermo": SubmissionRecordType.thermo,
    "kinetics": SubmissionRecordType.kinetics,
    "statmech": SubmissionRecordType.statmech,
    "transport": SubmissionRecordType.transport,
    "network": SubmissionRecordType.network,
    "network_solve": SubmissionRecordType.network_solve,
}


def _is_reviewable(record_type: str) -> bool:
    return record_type in _REVIEWABLE


def _load_badges(
    session: Session, rows: list[_LinkedRow]
) -> dict[tuple[str, int], RecordReviewBadge]:
    """Bulk-load review badges keyed by ``(record_type, record_id)``."""
    by_type: dict[str, list[int]] = {}
    for r in rows:
        if _is_reviewable(r.record_type):
            by_type.setdefault(r.record_type, []).append(r.record_id)

    out: dict[tuple[str, int], RecordReviewBadge] = {}
    for record_type, ids in by_type.items():
        badges = fetch_review_badges(
            session,
            record_type=_REVIEWABLE[record_type],
            record_ids=ids,
        )
        for rid, badge in badges.items():
            out[(record_type, rid)] = badge
    return out


def _is_visible(
    record_type: str,
    record_id: int,
    badges: dict[tuple[str, int], RecordReviewBadge],
    visible: set[RecordReviewStatus],
) -> bool:
    if not _is_reviewable(record_type):
        return True
    badge = badges.get((record_type, record_id))
    if badge is None:
        # Reviewable but no row — default ``not_reviewed`` (visible).
        return RecordReviewStatus.not_reviewed in visible
    return badge.status in visible


# ---------------------------------------------------------------------------
# Summary projection
# ---------------------------------------------------------------------------


def _endpoint_for(record_type: str, record_ref: str) -> str:
    """Build a ref-based detail URL for *record_type*.

    Always uses the public ref — never an integer ID — so the URL
    survives the Phase D internal-ID stripping policy.
    """
    base = "/api/v1/scientific"
    if record_type == "calculation":
        return f"{base}/calculations/{record_ref}"
    if record_type == "thermo":
        # Thermo uses the species-entry-scoped endpoint in v0.
        return f"{base}/thermo/{record_ref}"
    if record_type == "kinetics":
        return f"{base}/kinetics/{record_ref}"
    if record_type == "statmech":
        return f"{base}/statmech/{record_ref}"
    if record_type == "transport":
        return f"{base}/transport/{record_ref}"
    if record_type == "network":
        return f"{base}/networks/{record_ref}"
    if record_type == "network_solve":
        return f"{base}/network-solves/{record_ref}"
    # _collect_rows guards against unknown types upstream, but keep a
    # safe fallback so the response is still structurally valid.
    return f"{base}/{record_type}/{record_ref}"  # pragma: no cover


def _to_summary(
    row: _LinkedRow,
    *,
    badge: RecordReviewBadge | None,
    want_review: bool,
) -> LiteratureLinkedRecordSummary:
    summary_review: RecordReviewBadge | None
    if not _is_reviewable(row.record_type):
        summary_review = None
    elif want_review:
        summary_review = badge
    else:
        summary_review = None
    return LiteratureLinkedRecordSummary(
        record_type=row.record_type,  # type: ignore[arg-type]
        record_ref=row.record_ref,
        record_id=row.record_id,
        relationship_kind="direct",
        role=row.role,
        title=row.title,
        label=row.label,
        species_ref=row.species_ref,
        species_entry_ref=row.species_entry_ref,
        reaction_ref=row.reaction_ref,
        reaction_entry_ref=row.reaction_entry_ref,
        calculation_ref=row.calculation_ref,
        network_ref=row.network_ref,
        network_solve_ref=row.network_solve_ref,
        review=summary_review,
        created_at=row.created_at,
        endpoint=_endpoint_for(row.record_type, row.record_ref),
    )


def _echo_filter(
    request: LiteratureRecordsRequest,
    *,
    lit_ref: str,
    lit_id: int,
) -> dict[str, Any]:
    return {
        "literature_ref": lit_ref,
        "literature_id": lit_id,
        "record_type": request.record_type,
        "include_rejected": request.include_rejected,
        "include_deprecated": request.include_deprecated,
        "offset": request.offset,
        "limit": request.limit,
    }


__all__ = [
    "_INTERNAL_INCLUDE_TOKENS",
    "_LEGAL_INCLUDE_TOKENS",
    "get_literature_records",
]
