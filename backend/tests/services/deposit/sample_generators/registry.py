"""A two-generator registry for the deposit tests."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.species import Species, SpeciesEntry
from app.db.models.thermo import Thermo


def row_counts(session: Session) -> dict[str, Any]:
    return {
        "species": session.scalar(select(func.count()).select_from(Species)),
        "species_entries": session.scalar(select(func.count()).select_from(SpeciesEntry)),
    }


def thermo_values(session: Session) -> dict[str, Any]:
    rows = session.execute(select(Thermo.h298_kj_mol).order_by(Thermo.h298_kj_mol)).all()
    return {"h298_kj_mol": [row[0] for row in rows]}


GENERATORS = {"row_counts": row_counts, "thermo_values": thermo_values}
