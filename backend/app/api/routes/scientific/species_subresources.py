"""Species-entry-scoped scientific subresource reads.

Two thin list endpoints that mirror
``GET /scientific/species-entries/{id}/thermo``:

- ``GET /scientific/species-entries/{species_entry_id}/statmech``
- ``GET /scientific/species-entries/{species_entry_id}/transport``

They close the species-centered read asymmetry: thermo already had a
per-entry read while statmech / transport only had record-grain detail
+ broad search. Each returns the existing statmech / transport search
response envelope with records pinned to the species entry, and honours
the same ``include=trust`` policy as the per-entry thermo endpoint
(trust is opt-in only; ``include=all`` does not surface it).

The path parameter accepts either the integer ``species_entry.id`` or
a public ref of the form ``spe_…`` (the historical ``{species_entry_id}``
template name is kept). See
``docs/specs/public_identifier_policy.md`` and
``backend/docs/specs/trust_read_api_current.md``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.routes.scientific._common import parse_include
from app.api.routes.scientific._response import omit_trust_unless_requested
from app.db.models.common import RecordReviewStatus
from app.schemas.reads.scientific_statmech_search import (
    ScientificStatmechSearchResponse,
)
from app.schemas.reads.scientific_transport_search import (
    ScientificTransportSearchResponse,
)
from app.services.scientific_read.handles import resolve_species_entry_handle
from app.services.scientific_read.internal_ids import (
    apply_internal_ids_visibility,
)
from app.services.scientific_read.species_statmech import get_species_statmech
from app.services.scientific_read.species_transport import (
    get_species_transport,
)

router = APIRouter(prefix="/species-entries")


@router.get(
    "/{species_entry_id}/statmech",
    response_model=ScientificStatmechSearchResponse,
)
def species_statmech(
    species_entry_id: str = Path(..., min_length=1, max_length=64),
    session: Session = Depends(get_db),
    min_review_status: RecordReviewStatus | None = Query(None),
    include_rejected: bool = Query(False),
    include_deprecated: bool = Query(False),
    sort: str | None = Query(None),
    include: list[str] | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    """Return statmech records for a species entry.

    Path handle is the species-entry resource: an integer
    ``species_entry.id`` or a public ref starting with ``spe_``. A
    wrong-prefix ref returns 422; an unknown entry returns 404.
    ``sort=`` is rejected (v0). ``include=trust`` adds the
    ``computed_statmech_v1`` fragment per record; ``include=all`` does
    not include trust.
    """
    resolved_species_entry_id = resolve_species_entry_handle(
        session, species_entry_id
    )
    payload = get_species_statmech(
        session,
        species_entry_id=resolved_species_entry_id,
        include=parse_include(include),
        min_review_status=min_review_status,
        include_rejected=include_rejected,
        include_deprecated=include_deprecated,
        sort=sort,
        offset=offset,
        limit=limit,
    )
    visibility = apply_internal_ids_visibility(payload)
    return omit_trust_unless_requested(visibility, payload, scope="search")


@router.get(
    "/{species_entry_id}/transport",
    response_model=ScientificTransportSearchResponse,
)
def species_transport(
    species_entry_id: str = Path(..., min_length=1, max_length=64),
    session: Session = Depends(get_db),
    min_review_status: RecordReviewStatus | None = Query(None),
    include_rejected: bool = Query(False),
    include_deprecated: bool = Query(False),
    sort: str | None = Query(None),
    include: list[str] | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    """Return transport records for a species entry.

    Path handle is the species-entry resource: an integer
    ``species_entry.id`` or a public ref starting with ``spe_``. A
    wrong-prefix ref returns 422; an unknown entry returns 404.
    ``sort=`` is rejected (v0). ``include=trust`` adds the
    ``computed_transport_v1`` fragment per record; ``include=all`` does
    not include trust.
    """
    resolved_species_entry_id = resolve_species_entry_handle(
        session, species_entry_id
    )
    payload = get_species_transport(
        session,
        species_entry_id=resolved_species_entry_id,
        include=parse_include(include),
        min_review_status=min_review_status,
        include_rejected=include_rejected,
        include_deprecated=include_deprecated,
        sort=sort,
        offset=offset,
        limit=limit,
    )
    visibility = apply_internal_ids_visibility(payload)
    return omit_trust_unless_requested(visibility, payload, scope="search")
