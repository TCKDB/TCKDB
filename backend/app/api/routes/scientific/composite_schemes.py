"""Scientific composite-scheme endpoint -- the recipe behind a composite level (ADR 0021).

- ``GET /scientific/composite-schemes/{composite_scheme_ref}``

A scheme is identity data with no review history; the envelope carries an empty
``review_summary`` for shape parity. Unknown ref: 404 ``handle_not_found``.
Wrong-prefix ref: 422 ``handle_type_mismatch``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.routes.scientific._common import parse_include
from app.schemas.reads.scientific_composite_scheme import (
    ScientificCompositeSchemeDetailResponse,
)
from app.services.scientific_read.composite_schemes import get_composite_scheme
from app.services.scientific_read.internal_ids import apply_internal_ids_visibility

router = APIRouter(prefix="/composite-schemes")


@router.get(
    "/{composite_scheme_ref}",
    response_model=ScientificCompositeSchemeDetailResponse,
)
def scientific_composite_scheme_detail(
    composite_scheme_ref: str = Path(..., min_length=1, max_length=64),
    session: Session = Depends(get_db),
    include: list[str] | None = Query(None),
):
    """Return one composite scheme: its terms, their inputs, and the levels bound to it.

    The path handle is a public ref of the form ``csch_...`` (an integer id is
    accepted like on every scientific detail route, and never echoed).
    """
    payload = get_composite_scheme(
        session,
        composite_scheme_handle=composite_scheme_ref,
        include=parse_include(include),
    )
    return apply_internal_ids_visibility(payload)


__all__ = ["router"]
