"""Resolve ``(record_type, record_id)`` to the record's public ref.

The private review surfaces address records by their internal database id:
``machine_review_curator_task`` stores ``record_id`` NOT NULL, and the
machine-review projection uses the stringified id as its matching key (see
``app.services.machine_review.audit_adapter``'s "Internal-id addressing").
That is workable for machinery, but a *reader* cannot look up a row id --
DR-0028 Req 2 keeps internal ids out of what they are shown, and
``public_ref`` is the identifier every scientific read route already answers
to. This module is the one place that crosses from one to the other.

Seventeen of the eighteen :class:`SubmissionRecordType` members name a table
carrying :class:`~app.db.base.PublicRefMixin`. One does not:
``applied_energy_correction`` (``AppliedEnergyCorrection`` has no
``public_ref`` column). That means there is nothing to return and callers
get ``None`` rather than a fabricated value or a stringified id. That is the
same refusal :mod:`app.services.scientific_read.supersession` makes about
``applied_energy_correction``, for the same reason -- giving a table a
public ref is the prerequisite for naming its rows, not something a caller
may work around.

``molecular_property_observation`` (``MolecularPropertyObservation``) used to
be the second exception -- Phase C-E1 added the enum member without a public
ref. Phase C-E5 gave the table ``public_ref`` (migration ``d2f4a7c1b8e6``,
``mpo_`` prefix) alongside its first public read
(``GET /scientific/species-entries/{id}/observations``), so it now resolves
through this module like every other ref-bearing type; nothing here had to
change to pick that up, since the set below is derived from the column, not
retyped.

The mapping from record type to table is not written here -- it is
:data:`app.services.record_models.RECORD_MODELS`, filtered. Writing it out
again would be a second place for one entry to name the wrong table, and that
error is silent: the query still succeeds and still returns a ref, just the
wrong record's.

Kept separate from :mod:`app.services.public_refs`, which mints refs at INSERT
time and is deliberately keyed by ORM *class name* so it can be imported
without pulling in every model module. This module has to reach the mapped
classes to query them, so it cannot live there without taking that away.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.common import SubmissionRecordType
from app.services.record_models import RECORD_MODELS

#: The subset of :data:`~app.services.record_models.RECORD_MODELS` whose table
#: carries ``public_ref``.
#:
#: **Derived, not retyped.** A second hand-written copy of the registry is a
#: second place for one entry to name the wrong table, and that mistake is
#: invisible: the lookup still succeeds and still returns a ref, just the wrong
#: record's. Filtering on the column the mixin adds also means the
#: ``applied_energy_correction`` exception is *measured* here rather than
#: asserted -- give that table a ``public_ref`` and it joins this set with no
#: edit to this file.
_REF_BEARING_MODELS: dict[SubmissionRecordType, type[Any]] = {
    record_type: model
    for record_type, model in RECORD_MODELS.items()
    if hasattr(model, "public_ref")
}

#: The record types this module can name. A caller that wants to explain the
#: ``None`` it got back tests membership here rather than hard-coding the one
#: exception.
REF_BEARING_RECORD_TYPES: frozenset[SubmissionRecordType] = frozenset(_REF_BEARING_MODELS)


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


#: Enum members by their wire value. A plain lookup, because the obvious
#: alternative -- ``SubmissionRecordType(name)`` inside ``try/except
#: ValueError`` -- silently swallows ``CodedValidationError``, which subclasses
#: ``ValueError``. A coded refusal raised anywhere beneath such a handler loses
#: its ``code`` and ``context``.
#:
#: ``tests/api/test_coded_exception_reraise_gate`` caught exactly that shape on
#: 2026-09-14, while this helper still lived in ``app/api/routes/admin.py``.
#: **It would not catch it here:** that gate walks ``backend/app/api`` only, so
#: moving the helper into the service layer moved it out of the gate's reach.
#: The lookup below is what keeps the property, not the gate.
_RECORD_TYPE_BY_VALUE: dict[str, SubmissionRecordType] = {
    record_type.value: record_type for record_type in SubmissionRecordType
}


def resolve_record_public_refs_by_name(
    session: Session,
    refs: Iterable[tuple[str, int]],
) -> dict[tuple[str, int], str]:
    """As :func:`resolve_record_public_refs`, but keyed by the raw type *name*.

    The private machine-review projection carries ``record_type`` as a plain
    ``str`` and tolerates a value it cannot parse rather than raising
    (``mapping``'s policy 3), so its callers have a string in hand and no
    guarantee it names a member. An unknown name resolves to no ref, which is
    the same answer a reader gets for any record that cannot be named.
    """
    typed: list[tuple[SubmissionRecordType, int]] = []
    for name, record_id in refs:
        record_type = _RECORD_TYPE_BY_VALUE.get(name)
        if record_type is not None:
            typed.append((record_type, record_id))
    return {
        (record_type.value, record_id): public_ref
        for (record_type, record_id), public_ref in resolve_record_public_refs(
            session, typed
        ).items()
    }


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
    "resolve_record_public_ref",
    "resolve_record_public_refs",
    "resolve_record_public_refs_by_name",
]
