"""Literature read endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import PaginationParams, get_db
from app.api.errors import NotFoundError
from app.api.routes._pagination import PaginatedResponse
from app.db.models.common import LiteratureKind
from app.db.models.literature import Literature
from app.schemas.entities.literature import LiteratureRead
from app.services.literature_metadata import normalize_doi, normalize_isbn

router = APIRouter()


@router.get("", response_model=PaginatedResponse[LiteratureRead])
def list_literature(
    session: Session = Depends(get_db),
    pagination: PaginationParams = Depends(),
    kind: LiteratureKind | None = Query(None),
    year: int | None = Query(None),
    doi: str | None = Query(None),
    isbn: str | None = Query(None),
    title: str | None = Query(None),
    journal: str | None = Query(None),
):
    base = select(Literature.id)
    if kind is not None:
        base = base.where(Literature.kind == kind)
    if year is not None:
        base = base.where(Literature.year == year)
    if doi is not None:
        normalized = normalize_doi(doi)
        if normalized is None:
            raise ValueError(f"Invalid DOI: {doi}")
        base = base.where(Literature.doi == normalized)
    if isbn is not None:
        normalized = normalize_isbn(isbn)
        if normalized is None:
            raise ValueError(f"Invalid ISBN: {isbn}")
        base = base.where(Literature.isbn == normalized)
    if title is not None:
        base = base.where(Literature.title == title)
    if journal is not None:
        base = base.where(Literature.journal == journal)

    total = session.scalar(
        select(func.count()).select_from(base.subquery())
    ) or 0
    rows = session.scalars(
        select(Literature)
        .where(Literature.id.in_(base))
        .options(selectinload(Literature.authors))
        .order_by(Literature.id)
        .offset(pagination.skip)
        .limit(pagination.limit)
    ).all()
    return PaginatedResponse(
        items=[LiteratureRead.model_validate(r) for r in rows],
        total=total,
        skip=pagination.skip,
        limit=pagination.limit,
    )


@router.get("/{literature_id}", response_model=LiteratureRead)
def get_literature(literature_id: int, session: Session = Depends(get_db)):
    row = session.scalar(
        select(Literature)
        .where(Literature.id == literature_id)
        .options(selectinload(Literature.authors))
    )
    if row is None:
        raise NotFoundError(f"Literature {literature_id} not found")
    return LiteratureRead.model_validate(row)
