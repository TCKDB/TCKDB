"""Scientific conformer detail endpoints (v0 — search ships separately).

Two detail surfaces:

- ``GET /scientific/conformer-groups/{conformer_group_ref_or_id}``
- ``GET /scientific/conformer-observations/{conformer_observation_ref_or_id}``

See ``backend/docs/specs/scientific_conformer_reads.md``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.routes.scientific._common import parse_include
from app.schemas.reads.scientific_conformer import (
    ScientificConformerGroupDetailResponse,
    ScientificConformerObservationDetailResponse,
)
from app.services.scientific_read.conformers import (
    get_conformer_group,
    get_conformer_observation,
)
from app.services.scientific_read.internal_ids import (
    apply_internal_ids_visibility,
)


cg_router = APIRouter(prefix="/conformer-groups")
co_router = APIRouter(prefix="/conformer-observations")


@cg_router.get(
    "/{conformer_group_ref_or_id}",
    response_model=ScientificConformerGroupDetailResponse,
)
def scientific_conformer_group_detail(
    conformer_group_ref_or_id: str = Path(..., min_length=1, max_length=64),
    session: Session = Depends(get_db),
    include: list[str] | None = Query(None),
):
    """Return one conformer group as a scientific record.

    Path handle accepts an integer ``conformer_group.id`` or a public
    ref of the form ``cg_…``. Wrong-prefix refs return 422
    ``handle_type_mismatch``; unknown refs / ids return 404.
    """
    return apply_internal_ids_visibility(
        get_conformer_group(
            session,
            conformer_group_handle=conformer_group_ref_or_id,
            include=parse_include(include),
        )
    )


@co_router.get(
    "/{conformer_observation_ref_or_id}",
    response_model=ScientificConformerObservationDetailResponse,
)
def scientific_conformer_observation_detail(
    conformer_observation_ref_or_id: str = Path(
        ..., min_length=1, max_length=64
    ),
    session: Session = Depends(get_db),
    include: list[str] | None = Query(None),
):
    """Return one conformer observation as a scientific record.

    Path handle accepts an integer ``conformer_observation.id`` or a
    public ref of the form ``co_…``. Same 422 / 404 contract as the
    group surface.
    """
    return apply_internal_ids_visibility(
        get_conformer_observation(
            session,
            conformer_observation_handle=conformer_observation_ref_or_id,
            include=parse_include(include),
        )
    )
