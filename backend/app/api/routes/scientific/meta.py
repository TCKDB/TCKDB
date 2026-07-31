"""GET /api/v1/scientific/meta/* — vocabulary / discovery endpoints.

Exact-string filters on the scientific search endpoints (method, basis,
reaction family, software) are only usable if a client can discover the
values that actually exist. These endpoints return the distinct stored
values with usage counts. Thin handlers over
``app.services.scientific_read.meta``.

These four return a bare ``{"results": [...]}`` rather than the enveloped
scientific response shape, so they do not pass through the boundary that
stamps the read profile. They add the ``request`` echo explicitly instead —
"every scientific response echoes the resolved profile" has to hold here
too, or it does not hold at all.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.services.scientific_read import meta as meta_service
from app.services.scientific_read.profile import current_read_profile

router = APIRouter(prefix="/meta")


def _echo() -> dict:
    """The profile echo block, in the same shape as the enveloped responses."""
    return {"request": current_read_profile().echo()}


@router.get("/methods")
def get_methods(db: Session = Depends(get_db)) -> dict:
    """Distinct level-of-theory methods with usage counts."""
    return {"results": meta_service.list_methods(db), **_echo()}


@router.get("/basis-sets")
def get_basis_sets(db: Session = Depends(get_db)) -> dict:
    """Distinct level-of-theory basis sets with usage counts."""
    return {"results": meta_service.list_basis_sets(db), **_echo()}


@router.get("/software")
def get_software(db: Session = Depends(get_db)) -> dict:
    """Distinct software names with usage counts."""
    return {"results": meta_service.list_software(db), **_echo()}


@router.get("/reaction-families")
def get_reaction_families(db: Session = Depends(get_db)) -> dict:
    """Canonical reaction families with usage counts (0 if unused)."""
    return {"results": meta_service.list_reaction_families(db), **_echo()}
