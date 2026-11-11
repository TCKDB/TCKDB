"""Find species by name (``GET /scientific/species/name-search``).

The query text is only ever a bound parameter: exact is an equality on the normalised key, prefix is a code-point
range (``>= q`` and ``< q_next``). No ``LIKE``, no regular expression, no outbound lookup. A species merged into
another is answered with the survivor; a species is listed only when it has a live entry the read profile shows.
"""

from __future__ import annotations

from sqlalchemy import and_, collate, exists, func, select, text
from sqlalchemy.orm import Session, aliased

from app.api.config import settings
from app.db.models.common import RecordReviewStatus, SubmissionRecordType
from app.db.models.species import Species, SpeciesEntry
from app.db.models.species_name import SpeciesName
from app.schemas.reads.scientific_common import OrderEcho, browse_order
from app.schemas.reads.scientific_species_name_search import (
    NAME_ORDER_STATEMENT,
    NameMatchMode,
    SpeciesNameMatch,
    SpeciesNameSearchRequestEcho,
    SpeciesNameSearchResponse,
)
from app.services.scientific_read.common import molecular_formula_expr, visible_statuses
from app.services.scientific_read.hidden_by_default import record_forwarded_ref
from app.services.scientific_read.sql_review import join_review, visible_review_filter
from app.services.species_name_normalisation import (
    is_valid_query_key,
    normalise_name,
    prefix_upper_bound,
)

QUERY_INVALID_MESSAGE = (
    "species_name_query_invalid: the query must be 2 to 64 characters of letters, digits, spaces and - , ' ( ) [ ] + . :"
)


#: Longer raw input is refused before it is normalised.
_MAX_RAW_LENGTH = 256


def _name_order() -> OrderEcho:
    return browse_order("name_key", "species_ref").model_copy(update={"statement": NAME_ORDER_STATEMENT})


def validate_query(raw: str) -> str:
    """The normalised key of ``raw``. Raises ``ValueError`` (422) without echoing the input."""
    if len(raw) > _MAX_RAW_LENGTH:
        raise ValueError(QUERY_INVALID_MESSAGE)
    key = normalise_name(raw)
    if not is_valid_query_key(raw, key):
        raise ValueError(QUERY_INVALID_MESSAGE)
    return key


def _statement(*, key: str, match: NameMatchMode, visible: set[RecordReviewStatus], limit: int):
    """One row per (normalised name, surviving species), alphabetical, at most ``limit`` rows."""
    named = aliased(Species)
    survivor = aliased(Species)
    name_key_c = SpeciesName.name_key  # "C" collation by column definition

    if match is NameMatchMode.exact:
        key_predicate = name_key_c == key
    else:
        key_predicate = name_key_c >= key
        upper = prefix_upper_bound(key)
        if upper is not None:
            key_predicate = and_(key_predicate, name_key_c < upper)

    entries = select(SpeciesEntry.id).where(
        SpeciesEntry.species_id == survivor.id, SpeciesEntry.merged_into_species_entry_id.is_(None)
    )
    entries, review = join_review(entries, SpeciesEntry.id, SubmissionRecordType.species_entry)
    entries = entries.where(visible_review_filter(review, visible))

    inner = (
        select(
            SpeciesName.id.label("name_id"),
            SpeciesName.name.label("name"),
            SpeciesName.name_key.label("name_key"),
            SpeciesName.kind.label("kind"),
            SpeciesName.source_kind.label("source_kind"),
            survivor.id.label("species_id"),
            survivor.public_ref.label("species_ref"),
            survivor.smiles.label("smiles"),
            molecular_formula_expr(survivor.smiles).label("formula"),
            named.public_ref.label("named_ref"),
        )
        .join(named, named.id == SpeciesName.species_id)
        .join(survivor, survivor.id == func.coalesce(named.merged_into_species_id, named.id))
        .where(
            SpeciesName.retired_at.is_(None),
            key_predicate,
            survivor.merged_into_species_id.is_(None),
            exists(entries),
        )
        # The same name on a merged species and on its survivor is one match.
        .distinct(SpeciesName.name_key, survivor.id)
        .order_by(SpeciesName.name_key, survivor.id, SpeciesName.id)
        .subquery("name_matches")
    )
    return (
        select(inner)
        .order_by(inner.c.name_key, collate(inner.c.species_ref, "C"), inner.c.name_id)
        .limit(limit + 1)
    )


def search_species_names(session: Session, *, q: str, match: NameMatchMode, limit: int) -> SpeciesNameSearchResponse:
    key = validate_query(q)
    visible = visible_statuses(min_review_status=None, include_rejected=False, include_deprecated=False)
    # A bounded statement time for this read only (an integer from settings), put back afterwards so a session that
    # outlives the read keeps its own limit.
    previous = session.scalar(text("SELECT current_setting('statement_timeout')"))
    session.execute(text(f"SET LOCAL statement_timeout = {int(settings.name_search_statement_timeout_ms)}"))
    rows = session.execute(_statement(key=key, match=match, visible=visible, limit=limit)).all()
    session.execute(text("SELECT set_config('statement_timeout', :previous, true)"), {"previous": previous})
    truncated = len(rows) > limit
    matches = []
    for row in rows[:limit]:
        if row.named_ref != row.species_ref:
            record_forwarded_ref(row.named_ref, row.species_ref)
        matches.append(
            SpeciesNameMatch(
                species_ref=row.species_ref,
                formula=row.formula,
                smiles=row.smiles,
                name=row.name,
                kind=row.kind,
                source_kind=row.source_kind,
            )
        )
    return SpeciesNameSearchResponse(
        request=SpeciesNameSearchRequestEcho(query_key=key, match=match, limit=limit),
        matches=matches,
        truncated=truncated,
        order=_name_order(),
    )
