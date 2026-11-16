"""A helper in a subpackage, so the deposit's layout has to survive for the registry to import."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session


def count_rows(session: Session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))
