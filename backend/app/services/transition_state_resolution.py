"""Service helpers for standalone transition-state uploads.

Responsibilities:
- Create ``TransitionState`` (concept) and ``TransitionStateEntry`` rows.
- Orchestrate calculation persistence via shared helpers in
  ``calculation_resolution``.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models.calculation import Calculation, CalculationOutputGeometry
from app.db.models.common import CalculationGeometryRole
from app.db.models.transition_state import TransitionState, TransitionStateEntry
from app.schemas.fragments.calculation import CalculationWithResultsPayload
from app.services.calculation_resolution import (
    persist_additional_calculations,
    resolve_and_persist_calculation_with_results,
)


def create_transition_state_and_entry(
    session: Session,
    *,
    reaction_entry_id: int,
    charge: int,
    multiplicity: int,
    unmapped_smiles: str | None = None,
    label: str | None = None,
    note: str | None = None,
    created_by: int | None = None,
) -> tuple[TransitionState, TransitionStateEntry]:
    """Create a TS concept and a single candidate entry underneath it.

    :param session: Active SQLAlchemy session.
    :param reaction_entry_id: The reaction entry this TS belongs to.
    :param charge: Net charge of the TS structure.
    :param multiplicity: Spin multiplicity.
    :param unmapped_smiles: Optional SMILES (no atom maps).
    :param label: Optional human-readable label for the TS concept.
    :param note: Optional free-text note on the TS concept.
    :param created_by: Optional application user id.
    :returns: ``(TransitionState, TransitionStateEntry)`` tuple.
    """

    ts = TransitionState(
        reaction_entry_id=reaction_entry_id,
        label=label,
        note=note,
        created_by=created_by,
    )
    session.add(ts)
    session.flush()

    ts_entry = TransitionStateEntry(
        transition_state_id=ts.id,
        charge=charge,
        multiplicity=multiplicity,
        unmapped_smiles=unmapped_smiles,
        created_by=created_by,
    )
    session.add(ts_entry)
    session.flush()

    return ts, ts_entry


def persist_ts_calculations(
    session: Session,
    *,
    primary_opt_upload: CalculationWithResultsPayload,
    additional_uploads: list[CalculationWithResultsPayload],
    transition_state_entry_id: int,
    geometry_id: int,
    created_by: int | None = None,
) -> Calculation:
    """Persist the primary opt and all additional calculations for a TS entry.

    :param session: Active SQLAlchemy session.
    :param primary_opt_upload: Required primary opt calculation upload.
    :param additional_uploads: Additional calculation uploads (freq, sp, irc).
    :param transition_state_entry_id: Owner TS entry id.
    :param geometry_id: Resolved geometry id for the TS saddle point.
    :param created_by: Optional application user id.
    :returns: The primary ``Calculation`` row.
    """

    primary_calc = resolve_and_persist_calculation_with_results(
        session,
        primary_opt_upload,
        transition_state_entry_id=transition_state_entry_id,
        created_by=created_by,
    )
    session.add(
        CalculationOutputGeometry(
            calculation_id=primary_calc.id,
            geometry_id=geometry_id,
            output_order=1,
            role=CalculationGeometryRole.final,
        )
    )
    session.flush()

    persist_additional_calculations(
        session,
        primary_calc=primary_calc,
        additional_uploads=additional_uploads,
        geometry_id=geometry_id,
        transition_state_entry_id=transition_state_entry_id,
        created_by=created_by,
    )

    return primary_calc
