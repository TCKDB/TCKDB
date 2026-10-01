"""Read-side projection of a depositor-declared energy level of theory.

``thermo.energy_level_of_theory_id`` and ``statmech.energy_level_of_theory_id``
store what a depositor declared for the record's energy (#619). The read
layer reports it as ``levels.declared_energy``, deliberately apart from
``levels.energy``: the latter is re-derived from the linked calculations on
every read, the former is a stored claim, and a record can carry one without
the other.

A stored id can name a row later merged into another
(``level_of_theory_merge``); the summary names the row it was merged into,
the same canonicalisation every other level-of-theory read applies.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.level_of_theory import LevelOfTheory
from app.schemas.reads.scientific_common import LevelOfTheorySummary
from app.services.scientific_read.composite_binding import composite_scheme_summaries
from app.services.scientific_read.handles import canonical_level_of_theory_id


def load_declared_energy_summaries(
    session: Session, stored_lot_ids: Iterable[int | None]
) -> dict[int, LevelOfTheorySummary]:
    """Stored level-of-theory id -> summary of the level it stands for.

    Keys are the ids as stored (so a caller looks up the row's own
    ``energy_level_of_theory_id`` directly); values describe the canonical
    row. Ids naming no row are absent, which the caller reads as "nothing
    to report".
    """
    wanted = {i for i in stored_lot_ids if i is not None}
    if not wanted:
        return {}
    canonical = {i: canonical_level_of_theory_id(session, i) for i in wanted}
    rows = {
        lot.id: lot
        for lot in session.scalars(
            select(LevelOfTheory).where(LevelOfTheory.id.in_(set(canonical.values())))
        ).all()
    }
    schemes = composite_scheme_summaries(session, rows)
    out: dict[int, LevelOfTheorySummary] = {}
    for stored, canon in canonical.items():
        lot = rows.get(canon)
        if lot is None:
            continue
        out[stored] = LevelOfTheorySummary(
            level_of_theory_id=lot.id,
            level_of_theory_ref=lot.public_ref,
            method=lot.method,
            basis=lot.basis,
            dispersion=lot.dispersion,
            solvent=lot.solvent,
            spin_treatment=lot.spin_treatment,
            label=None,
            composite_scheme=schemes.get(lot.id),
        )
    return out
