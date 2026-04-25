"""Health check endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_db

router = APIRouter()


@router.get("/health")
def health(session: Session = Depends(get_db)) -> dict:
    """Verify that the API can reach the database."""
    session.execute(text("SELECT 1"))
    return {"status": "ok"}
