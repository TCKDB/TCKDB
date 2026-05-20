"""Scientific literature endpoints — detail + inverse records.

Two public endpoints:

- ``GET /scientific/literature/{literature_ref_or_id}``
- ``GET /scientific/literature/{literature_ref_or_id}/records``

The records endpoint is the literature-centered *inverse* query: a
caller starts from a literature row and pages through every
scientific record citing it.

See ``backend/docs/specs/scientific_literature_reads.md``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.routes.scientific._common import parse_include
from app.schemas.reads.scientific_literature import (
    ScientificLiteratureDetailResponse,
)
from app.schemas.reads.scientific_literature_records import (
    LiteratureRecordsRequest,
    ScientificLiteratureRecordsResponse,
)
from app.services.scientific_read.internal_ids import (
    apply_internal_ids_visibility,
)
from app.services.scientific_read.literature import get_literature
from app.services.scientific_read.literature_records import (
    get_literature_records,
)


router = APIRouter(prefix="/literature")


@router.get(
    "/{literature_ref_or_id}/records",
    response_model=ScientificLiteratureRecordsResponse,
)
def scientific_literature_records(
    literature_ref_or_id: str = Path(..., min_length=1, max_length=64),
    session: Session = Depends(get_db),
    record_type: str | None = Query(None),
    include_rejected: bool = Query(False),
    include_deprecated: bool = Query(False),
    sort: str | None = Query(None),
    include: list[str] | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    """Inverse records: list scientific records citing this literature.

    Returns a flattened, paginated list of public-ref summaries across
    every record type with a direct ``literature_id`` FK to the
    target. Clients follow each item's ``endpoint`` for the full
    record.
    """
    request_obj = LiteratureRecordsRequest(
        record_type=record_type,
        include_rejected=include_rejected,
        include_deprecated=include_deprecated,
        sort=sort,
        include=parse_include(include),
        offset=offset,
        limit=limit,
    )
    return apply_internal_ids_visibility(
        get_literature_records(
            session,
            request_obj,
            literature_handle=literature_ref_or_id,
        )
    )


@router.get(
    "/{literature_ref_or_id}",
    response_model=ScientificLiteratureDetailResponse,
)
def scientific_literature_detail(
    literature_ref_or_id: str = Path(..., min_length=1, max_length=64),
    session: Session = Depends(get_db),
    include: list[str] | None = Query(None),
):
    """Return one literature row as a scientific record.

    Path handle accepts an integer ``literature.id`` or a public ref
    of the form ``lit_…``. Wrong-prefix refs return 422
    ``handle_type_mismatch``; unknown refs / ids return 404.

    Default response surfaces citation metadata, authors, identifiers
    (DOI/ISBN/URL), and an inverse record-count summary. Literature is
    not a reviewable record type, so no review badge is returned at
    the literature level.
    """
    return apply_internal_ids_visibility(
        get_literature(
            session,
            literature_handle=literature_ref_or_id,
            include=parse_include(include),
        )
    )
