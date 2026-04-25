"""Thermo upload workflow orchestrator."""

from __future__ import annotations

from sqlalchemy.orm import Session

import app.db.models  # noqa: F401
from app.db.models.calculation import Calculation
from app.db.models.thermo import Thermo
from app.schemas.entities.thermo import ThermoSourceCalculationCreate
from app.schemas.workflows.thermo_upload import ThermoUploadRequest
from app.services.calculation_resolution import (
    resolve_and_persist_calculation_with_results,
)
from app.services.energy_correction_resolution import create_applied_energy_correction
from app.services.species_resolution import resolve_species_entry
from app.services.thermo_resolution import persist_thermo, resolve_thermo_upload


def _assert_calculation_owned_by(
    calculation: Calculation,
    *,
    species_entry_id: int,
    context: str,
) -> None:
    """Defensive owner-consistency check for a resolved source calculation.

    Supporting calculations attached to a thermo record must belong to the
    same species entry as the thermo target; otherwise the provenance link
    would be scientifically meaningless.

    :raises ValueError: if the calculation does not belong to
        ``species_entry_id``.
    """
    if calculation.species_entry_id != species_entry_id:
        raise ValueError(
            f"{context}: calculation id={calculation.id} belongs to "
            f"species_entry_id={calculation.species_entry_id}, not to the "
            f"thermo target species_entry_id={species_entry_id}."
        )


def persist_thermo_upload(
    session: Session,
    request: ThermoUploadRequest,
    *,
    created_by: int | None = None,
) -> Thermo:
    """Persist a complete thermo upload workflow.

    Resolves the species entry, persists any inline supporting calculations,
    resolves provenance references, creates the thermo record with children
    (including ``thermo_source_calculation`` links), and processes applied
    energy corrections while resolving their ``source_calculation_key`` to
    a real calculation id rather than silently dropping it.

    :param session: Active SQLAlchemy session.
    :param request: Workflow-facing thermo upload payload.
    :param created_by: Optional application user id for newly created rows.
    :returns: Newly created ``Thermo`` row.
    :raises ValueError: If a resolved supporting calculation does not
        belong to the thermo target's species entry, or if an applied
        correction's ``source_calculation_key`` does not resolve.
    """
    species_entry = resolve_species_entry(
        session, request.species_entry, created_by=created_by
    )

    # Persist inline supporting calculations, keyed by their local keys.
    # Each is automatically scoped to the thermo target's species entry, so
    # owner-consistency is enforced by construction. The explicit check
    # below also guards any future path that reuses existing calculations.
    calculations_by_key: dict[str, Calculation] = {}
    for calc_in in request.calculations:
        calc_row = resolve_and_persist_calculation_with_results(
            session,
            calc_in.calculation,
            species_entry_id=species_entry.id,
            created_by=created_by,
        )
        _assert_calculation_owned_by(
            calc_row,
            species_entry_id=species_entry.id,
            context=f"thermo calculation '{calc_in.key}'",
        )
        calculations_by_key[calc_in.key] = calc_row

    # Resolve source_calculation links from local keys to real (id, role).
    resolved_source_calcs = [
        ThermoSourceCalculationCreate(
            calculation_id=calculations_by_key[sc.calculation_key].id,
            role=sc.role,
        )
        for sc in request.source_calculations
    ]

    thermo_create = resolve_thermo_upload(
        session,
        request,
        species_entry_id=species_entry.id,
    )
    # The upload service currently hardcodes an empty source_calculations
    # list on ThermoCreate; splice the resolved ones in here.
    thermo_create = thermo_create.model_copy(
        update={"source_calculations": resolved_source_calcs}
    )
    thermo = persist_thermo(session, thermo_create, created_by=created_by)

    for correction_payload in request.applied_energy_corrections:
        source_calc_id: int | None = None
        if correction_payload.source_calculation_key is not None:
            calc_row = calculations_by_key.get(
                correction_payload.source_calculation_key
            )
            if calc_row is None:
                # The schema validator normally prevents this, but defend
                # against future code paths that bypass validation.
                raise ValueError(
                    f"applied_energy_correction.source_calculation_key "
                    f"'{correction_payload.source_calculation_key}' did not "
                    f"resolve to a declared calculation."
                )
            _assert_calculation_owned_by(
                calc_row,
                species_entry_id=species_entry.id,
                context=(
                    "applied_energy_correction "
                    f"source_calculation_key='{correction_payload.source_calculation_key}'"
                ),
            )
            source_calc_id = calc_row.id

        create_applied_energy_correction(
            session,
            correction_payload,
            target_species_entry_id=species_entry.id,
            source_calculation_id=source_calc_id,
            created_by=created_by,
        )

    session.flush()
    return thermo
