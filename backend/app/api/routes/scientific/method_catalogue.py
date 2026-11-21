"""Scientific catalogue endpoints -- what a method or basis-set spelling names, and the articles that define it.

- ``GET /scientific/methods``
- ``GET /scientific/methods/{method_handle}``
- ``GET /scientific/basis-sets``
- ``GET /scientific/basis-sets/{basis_set_handle}``

A handle is a catalogue ref (``mcat_...`` / ``bcat_...``) or any spelling that keys to an entry (``gfn2``,
``def2tzvp``). A name that contains ``/`` (``aug-cc-pVTZ/C``) cannot be one path segment: reach it by its ref, which
every card carries. Unknown handle: 404 ``handle_not_found``.
Lists are the whole catalogue in key order, unpaged, and carry no ranking.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.server_faults import ServerFaultRoute
from app.chemistry.method_families import MethodFamily
from app.db.models.common import BasisSetRole, CatalogueMethodKind
from app.schemas.reads.scientific_method_catalogue import (
    BasisSetCatalogueBrowseResponse,
    BasisSetCatalogueDetailResponse,
    MethodCatalogueBrowseResponse,
    MethodCatalogueDetailResponse,
)
from app.services.scientific_read.internal_ids import apply_internal_ids_visibility
from app.services.scientific_read.method_catalogue import (
    browse_basis_sets,
    browse_methods,
    get_basis_set,
    get_method,
)

methods_router = APIRouter(route_class=ServerFaultRoute, prefix="/methods")
basis_sets_router = APIRouter(route_class=ServerFaultRoute, prefix="/basis-sets")


@methods_router.get("", response_model=MethodCatalogueBrowseResponse)
def scientific_methods_browse(
    session: Session = Depends(get_db),
    kind: CatalogueMethodKind | None = Query(None, description="Only methods, or only dispersion corrections."),
    method_family: MethodFamily | None = Query(None, description="Only entries of this method family."),
):
    """List the methods and dispersion corrections in the catalogue, in key order, with their references and usage."""
    return apply_internal_ids_visibility(browse_methods(session, kind=kind, family=method_family))


@methods_router.get("/{method_handle}", response_model=MethodCatalogueDetailResponse)
def scientific_method_detail(
    method_handle: str = Path(..., min_length=1, max_length=128),
    session: Session = Depends(get_db),
):
    """Return one method or dispersion correction and the levels of theory that use it."""
    return apply_internal_ids_visibility(get_method(session, handle=method_handle))


@basis_sets_router.get("", response_model=BasisSetCatalogueBrowseResponse)
def scientific_basis_sets_browse(
    session: Session = Depends(get_db),
    role: BasisSetRole | None = Query(None, description="Only basis sets with this role."),
    family: str | None = Query(None, max_length=128, description="Only basis sets of this family, as written."),
):
    """List the basis sets in the catalogue, in key order, with their references and usage."""
    return apply_internal_ids_visibility(browse_basis_sets(session, role=role, family=family))


@basis_sets_router.get("/{basis_set_handle}", response_model=BasisSetCatalogueDetailResponse)
def scientific_basis_set_detail(
    basis_set_handle: str = Path(..., min_length=1, max_length=128),
    session: Session = Depends(get_db),
):
    """Return one basis set and the levels of theory that use it."""
    return apply_internal_ids_visibility(get_basis_set(session, handle=basis_set_handle))


__all__ = ["basis_sets_router", "methods_router"]
