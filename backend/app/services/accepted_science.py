"""Registry and locking helpers for immutable accepted-science records."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import DomainError
from app.db.models.calculation import Calculation
from app.db.models.common import SubmissionRecordType
from app.db.models.energy_correction import AppliedEnergyCorrection
from app.db.models.kinetics import Kinetics
from app.db.models.network import Network
from app.db.models.network_pdep import NetworkSolve
from app.db.models.species import ConformerObservation
from app.db.models.statmech import Statmech
from app.db.models.thermo import Thermo
from app.db.models.transition_state import TransitionStateEntry
from app.db.models.transport import Transport


class ScientificRecordRef(Protocol):
    """Structural type accepted by :func:`lock_scientific_records`."""

    record_type: SubmissionRecordType
    record_id: int


_ROOT_MODELS: dict[SubmissionRecordType, type[Any]] = {
    SubmissionRecordType.calculation: Calculation,
    SubmissionRecordType.thermo: Thermo,
    SubmissionRecordType.statmech: Statmech,
    SubmissionRecordType.kinetics: Kinetics,
    SubmissionRecordType.transport: Transport,
    SubmissionRecordType.network: Network,
    SubmissionRecordType.network_solve: NetworkSolve,
    SubmissionRecordType.applied_energy_correction: AppliedEnergyCorrection,
    SubmissionRecordType.transition_state_entry: TransitionStateEntry,
    SubmissionRecordType.conformer_observation: ConformerObservation,
}


def is_accepted_science_type(record_type: SubmissionRecordType) -> bool:
    """Return whether v1 immutability covers ``record_type``."""

    return record_type in _ROOT_MODELS


def lock_scientific_records(
    session: Session,
    refs: Iterable[ScientificRecordRef],
) -> dict[tuple[SubmissionRecordType, int], Any]:
    """Lock supported roots in deterministic order and return their rows."""

    keys = sorted(
        {(ref.record_type, ref.record_id) for ref in refs},
        key=lambda item: (item[0].value, item[1]),
    )
    rows: dict[tuple[SubmissionRecordType, int], Any] = {}
    for record_type, record_id in keys:
        model = _ROOT_MODELS.get(record_type)
        if model is None:
            continue
        row = session.scalar(select(model).where(model.id == record_id).with_for_update())
        if row is None:
            raise DomainError(f"{record_type.value} record {record_id} does not exist")
        rows[(record_type, record_id)] = row
    return rows


def supersession_subject(row: Any, record_type: SubmissionRecordType) -> tuple[Any, ...]:
    """Return the stable subject identity used to validate a replacement."""

    if record_type in {
        SubmissionRecordType.thermo,
        SubmissionRecordType.statmech,
        SubmissionRecordType.transport,
    }:
        return (row.species_entry_id,)
    if record_type is SubmissionRecordType.kinetics:
        return (row.reaction_entry_id, row.direction)
    if record_type is SubmissionRecordType.calculation:
        return (
            row.species_entry_id,
            row.transition_state_entry_id,
            row.type,
        )
    if record_type is SubmissionRecordType.network:
        # Networks have no stable parent concept in v1. Curator authority,
        # an explicit reason, and same-type replacement are the boundary.
        return (SubmissionRecordType.network,)
    if record_type is SubmissionRecordType.network_solve:
        return (row.network_id,)
    if record_type is SubmissionRecordType.applied_energy_correction:
        return (
            row.target_species_entry_id,
            row.target_reaction_entry_id,
            row.target_transition_state_entry_id,
            row.application_role,
        )
    if record_type is SubmissionRecordType.transition_state_entry:
        return (row.transition_state_id,)
    if record_type is SubmissionRecordType.conformer_observation:
        return (row.conformer_group_id,)
    raise DomainError(f"Unsupported supersession type: {record_type.value}")
