"""Resolve ``(record_type, record_id)`` to the record's public ref.

The private review surfaces address records by their internal database id:
``machine_review_curator_task`` stores ``record_id`` NOT NULL, and the
machine-review projection uses the stringified id as its matching key (see
``app.services.machine_review.audit_adapter``'s "Internal-id addressing").
That is workable for machinery, but a *reader* cannot look up a row id --
DR-0028 Req 2 keeps internal ids out of what they are shown, and
``public_ref`` is the identifier every scientific read route already answers
to. This module is the one place that crosses from one to the other.

Sixteen of the seventeen :class:`SubmissionRecordType` members name a table
carrying :class:`~app.db.base.PublicRefMixin`. The seventeenth,
``applied_energy_correction``, does not: ``AppliedEnergyCorrection`` has no
``public_ref`` column, so there is nothing to return and callers get ``None``
rather than a fabricated value or a stringified id. That is the same refusal
:mod:`app.services.scientific_read.supersession` makes about the same table,
for the same reason -- giving that table a public ref is the prerequisite for
naming its rows, not something a caller may work around.

Kept separate from :mod:`app.services.public_refs`, which mints refs at INSERT
time and is deliberately keyed by ORM *class name* so it can be imported
without pulling in every model module. This module has to import the models to
query them, so it cannot live there without taking that property away.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.calculation import Calculation, CalculationArtifact
from app.db.models.common import SubmissionRecordType
from app.db.models.kinetics import Kinetics
from app.db.models.network import Network
from app.db.models.network_pdep import NetworkSolve
from app.db.models.reaction import ChemReaction, ReactionEntry
from app.db.models.species import (
    ConformerGroup,
    ConformerObservation,
    Species,
    SpeciesEntry,
)
from app.db.models.statmech import Statmech
from app.db.models.thermo import Thermo
from app.db.models.transition_state import TransitionState, TransitionStateEntry
from app.db.models.transport import Transport

#: Record types whose table carries ``public_ref``. ``applied_energy_correction``
#: is absent on purpose -- see the module docstring.
_REF_BEARING_MODELS: dict[SubmissionRecordType, type[Any]] = {
    SubmissionRecordType.species: Species,
    SubmissionRecordType.species_entry: SpeciesEntry,
    SubmissionRecordType.conformer_group: ConformerGroup,
    SubmissionRecordType.conformer_observation: ConformerObservation,
    SubmissionRecordType.reaction: ChemReaction,
    SubmissionRecordType.reaction_entry: ReactionEntry,
    SubmissionRecordType.transition_state: TransitionState,
    SubmissionRecordType.transition_state_entry: TransitionStateEntry,
    SubmissionRecordType.calculation: Calculation,
    SubmissionRecordType.statmech: Statmech,
    SubmissionRecordType.thermo: Thermo,
    SubmissionRecordType.kinetics: Kinetics,
    SubmissionRecordType.transport: Transport,
    SubmissionRecordType.network: Network,
    SubmissionRecordType.network_solve: NetworkSolve,
    SubmissionRecordType.artifact: CalculationArtifact,
}

#: The record types this module can name. A caller that wants to explain the
#: ``None`` it got back tests membership here rather than hard-coding the one
#: exception.
REF_BEARING_RECORD_TYPES: frozenset[SubmissionRecordType] = frozenset(_REF_BEARING_MODELS)


def has_public_ref(record_type: SubmissionRecordType) -> bool:
    """Return whether rows of ``record_type`` can be named by a public ref."""
    return record_type in _REF_BEARING_MODELS


def resolve_record_public_refs(
    session: Session,
    refs: Iterable[tuple[SubmissionRecordType, int]],
) -> dict[tuple[SubmissionRecordType, int], str]:
    """Resolve many records at once: one SELECT per distinct record type.

    A list surface resolving refs one row at a time would issue a query per
    task; at the curator queue's 200-row page cap that is 200 round trips for
    a page. Grouping by type bounds it at the number of types actually present
    (at most 16, in practice one or two).

    Pairs that resolve to nothing are simply **absent** from the result: a
    ``record_type`` with no public ref, and an id naming no row (a record
    deleted since the task was raised). Callers read a missing key as "cannot
    be named", which is the honest answer for both.
    """
    by_type: dict[SubmissionRecordType, set[int]] = defaultdict(set)
    for record_type, record_id in refs:
        if record_type in _REF_BEARING_MODELS:
            by_type[record_type].add(record_id)

    resolved: dict[tuple[SubmissionRecordType, int], str] = {}
    for record_type, record_ids in by_type.items():
        model = _REF_BEARING_MODELS[record_type]
        rows = session.execute(
            select(model.id, model.public_ref).where(model.id.in_(record_ids))
        ).all()
        for record_id, public_ref in rows:
            resolved[(record_type, record_id)] = public_ref
    return resolved


def resolve_record_public_ref(
    session: Session,
    *,
    record_type: SubmissionRecordType,
    record_id: int,
) -> str | None:
    """The public ref naming one record, or ``None`` if it cannot be named.

    ``None`` covers both reasons a record has no ref to show: its table has no
    ``public_ref`` column (``applied_energy_correction``), or no row with that
    id exists any more.
    """
    return resolve_record_public_refs(session, [(record_type, record_id)]).get(
        (record_type, record_id)
    )


__all__ = [
    "REF_BEARING_RECORD_TYPES",
    "has_public_ref",
    "resolve_record_public_ref",
    "resolve_record_public_refs",
]
