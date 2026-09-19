"""Resolve ``(record_type, record_id)`` to the record it is *part of*.

:mod:`app.services.record_refs` answers "what is this record called?". This
module answers the question a reader asks next and which a ref alone cannot:
**where do I go to see it?**

Six of the eighteen :class:`SubmissionRecordType` members name a table with
no page of its own -- ``thermo``, ``statmech``, ``kinetics``,
``transition_state``, ``network_solve``, ``applied_energy_correction``. A
review row carrying only ``(record_type, record_public_ref)`` addresses
nothing for those: ``thm_...`` resolves through no route, so the review queue
renders it as inert text and a curator asked to judge the claim cannot open
it. That is 30% of the queue, and it is the 30% holding the thermochemistry
and kinetics the archive exists to publish.

The content is not missing -- it is rendered inside its parent. Thermo and
statmech are tabs on the species entry page; kinetics and transition states
are sections on the reaction entry page. Every one of those tables already
carries the foreign key that says which parent, enforced NOT NULL or by an
XOR ``CHECK``. So the fix is not six special cases, it is one mapping from a
record type to the column naming its owner.

**This registry is an ORM fact, not a routing table.** It says what a row
belongs to, which is true regardless of what any client can display. Deciding
that ``thermo`` has no page while ``species_entry`` does is the *frontend's*
knowledge and stays there (``frontend/src/domain/recordRoute.ts``); were it
encoded here, giving ``thermo`` a page later would mean editing the backend
to describe a change it has no part in. That is why types which are perfectly
addressable in their own right -- ``species_entry``, ``reaction_entry`` --
are listed too: they have an owner, so the owner is reported, and a client
that can already open the record simply prefers it.

.. _container-null-causes:

``None`` is a normal answer, never a failure, and it has exactly **four**
causes. This list is the canonical one: the docstring of
:func:`resolve_record_containers` and the OpenAPI ``description`` on
``RecordReviewRead.container_type`` restate it, and all three must agree --
an earlier draft had three causes here, four there and two on the wire, which
left a reader of any one of them with a different idea of what null meant.

1. **The record type has no owning parent.** ``species``, ``reaction`` and
   ``network`` are roots of their own trees.
2. **The record itself no longer exists.** A review row outliving the record
   it was raised for. This is the one that actually happens in production.
3. **The record names no owner.** Reachable for one type:
   ``molecular_property_observation.species_entry_id`` is nullable by design
   (an importer may deposit an identity-unresolved observation, see that
   model's module docstring) -- every other owning key below is either NOT
   NULL or covered by an XOR ``CHECK``. The resolver does not assume a
   constraint it cannot see, so this was already handled correctly; it is
   just no longer hypothetical.
4. **The owner no longer exists,** so it cannot be named.

Callers get ``None`` for all four and must present it as "cannot be linked",
never as an error. The same refusal, and the same reason, as
:mod:`app.services.record_refs`.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import Any, NamedTuple

from sqlalchemy import select
from sqlalchemy.orm import InstrumentedAttribute, Session

from app.db.models.common import SubmissionRecordType
from app.services.record_models import RECORD_MODELS
from app.services.record_refs import resolve_record_public_refs


class RecordContainer(NamedTuple):
    """The record a row belongs to: what it is, and what it is called.

    Both halves or neither. A ref with no type cannot be turned into a link
    (``spc_`` and ``rxn_`` are conventions, not a contract a client may parse),
    and a type with no ref names nothing. Returning them as one value is what
    stops a caller from carrying a ref it cannot address.
    """

    container_type: SubmissionRecordType
    container_ref: str


#: Which column on each record's table names its owner, and which record type
#: that owner is.
#:
#: Ordered, and the order is load-bearing where a type has more than one
#: candidate: the first column that is non-NULL wins. Today no row can satisfy
#: two at once -- ``statmech`` and ``calculation`` carry an XOR ``CHECK``, and
#: ``applied_energy_correction`` a ``num_nonnulls(...) = 1`` -- so the order is
#: not currently observable. It is fixed anyway, because "whichever the dict
#: happened to yield first" is not an answer that survives a constraint being
#: relaxed.
#:
#: Written as *column names*, not attributes, so the table reads as a table.
#: They are resolved against the ORM at import below, which turns a misspelled
#: column into an ``AttributeError`` that fails every test that imports this
#: module -- rather than a silent ``None`` for one record type that no test
#: would notice unless it happened to cover that type.
_CONTAINER_COLUMNS: dict[SubmissionRecordType, tuple[tuple[str, SubmissionRecordType], ...]] = {
    # -- roots: no owning parent, deliberately absent rather than mapped --
    # SubmissionRecordType.species
    # SubmissionRecordType.reaction
    # SubmissionRecordType.network
    SubmissionRecordType.species_entry: (
        ("species_id", SubmissionRecordType.species),
    ),
    SubmissionRecordType.conformer_group: (
        ("species_entry_id", SubmissionRecordType.species_entry),
    ),
    SubmissionRecordType.conformer_observation: (
        ("conformer_group_id", SubmissionRecordType.conformer_group),
    ),
    SubmissionRecordType.reaction_entry: (
        ("reaction_id", SubmissionRecordType.reaction),
    ),
    SubmissionRecordType.transition_state: (
        ("reaction_entry_id", SubmissionRecordType.reaction_entry),
    ),
    SubmissionRecordType.transition_state_entry: (
        ("transition_state_id", SubmissionRecordType.transition_state),
    ),
    SubmissionRecordType.calculation: (
        ("species_entry_id", SubmissionRecordType.species_entry),
        ("transition_state_entry_id", SubmissionRecordType.transition_state_entry),
    ),
    SubmissionRecordType.statmech: (
        ("species_entry_id", SubmissionRecordType.species_entry),
        ("transition_state_entry_id", SubmissionRecordType.transition_state_entry),
    ),
    SubmissionRecordType.thermo: (
        ("species_entry_id", SubmissionRecordType.species_entry),
    ),
    SubmissionRecordType.kinetics: (
        ("reaction_entry_id", SubmissionRecordType.reaction_entry),
    ),
    SubmissionRecordType.transport: (
        ("species_entry_id", SubmissionRecordType.species_entry),
    ),
    SubmissionRecordType.network_solve: (
        ("network_id", SubmissionRecordType.network),
    ),
    # Three targets, not two. ``target_transition_state_entry_id`` is easy to
    # miss -- it is the only one of the three that does not appear in the
    # #262 brief, and dropping it would leave every correction applied to a
    # transition state silently unlinked while the other two worked.
    SubmissionRecordType.applied_energy_correction: (
        ("target_species_entry_id", SubmissionRecordType.species_entry),
        ("target_reaction_entry_id", SubmissionRecordType.reaction_entry),
        (
            "target_transition_state_entry_id",
            SubmissionRecordType.transition_state_entry,
        ),
    ),
    SubmissionRecordType.artifact: (
        ("calculation_id", SubmissionRecordType.calculation),
    ),
    # Nullable: see case 3 above. An identity-unresolved observation has no
    # owner to report, which the resolver already treats as "cannot be
    # linked" rather than an error.
    SubmissionRecordType.molecular_property_observation: (
        ("species_entry_id", SubmissionRecordType.species_entry),
    ),
}


def _resolve_columns() -> dict[SubmissionRecordType, tuple[tuple[InstrumentedAttribute[Any], SubmissionRecordType], ...]]:
    """Bind every declared column name to the real ORM attribute, at import.

    ``getattr`` without a default is the point: a column name that does not
    exist raises here, at import, instead of resolving to ``None`` forever for
    one record type.
    """
    bound: dict[
        SubmissionRecordType,
        tuple[tuple[InstrumentedAttribute[Any], SubmissionRecordType], ...],
    ] = {}
    for record_type, candidates in _CONTAINER_COLUMNS.items():
        model = RECORD_MODELS[record_type]
        bound[record_type] = tuple(
            (getattr(model, column_name), container_type)
            for column_name, container_type in candidates
        )
    return bound


_CONTAINER_ATTRS = _resolve_columns()

#: The record types that declare an owner. A caller explaining a ``None``
#: tests membership here rather than hard-coding the roots.
CONTAINED_RECORD_TYPES: frozenset[SubmissionRecordType] = frozenset(_CONTAINER_COLUMNS)

#: Every record type that appears as somebody's container. Its only use is the
#: test that each one can actually be named -- a container type with no
#: ``public_ref`` would resolve to ``None`` for every child, silently.
CONTAINER_RECORD_TYPES: frozenset[SubmissionRecordType] = frozenset(
    container_type
    for candidates in _CONTAINER_COLUMNS.values()
    for _, container_type in candidates
)


def resolve_record_containers(
    session: Session,
    refs: Iterable[tuple[SubmissionRecordType, int]],
) -> dict[tuple[SubmissionRecordType, int], RecordContainer]:
    """Resolve many records' containers at once.

    Two grouped passes, never a query per row:

    1. one SELECT per distinct *record* type, reading that table's owner
       columns for the ids asked about;
    2. one SELECT per distinct *container* type, naming the owners found --
       delegated to :func:`~app.services.record_refs.resolve_record_public_refs`,
       which already groups that way.

    So a 50-row page of one record type costs two queries, not 50, and a mixed
    page is bounded by the number of types present rather than by its length.

    Pairs that resolve to nothing are **absent** from the result, exactly as
    in ``record_refs``, for the four causes this module's docstring lists and
    no others: the record type has no owning parent, the record itself no
    longer exists, the record names no owner, or the owner no longer exists.
    All four read as "cannot be linked", which is the honest answer for every
    one of them. See :ref:`container-null-causes` -- that list is canonical
    and this paragraph must not drift from it.
    """
    by_type: dict[SubmissionRecordType, set[int]] = defaultdict(set)
    for record_type, record_id in refs:
        if record_type in _CONTAINER_ATTRS:
            by_type[record_type].add(record_id)

    # Pass 1: (record_type, record_id) -> (container_type, container_id)
    container_ids: dict[tuple[SubmissionRecordType, int], tuple[SubmissionRecordType, int]] = {}
    for record_type, record_ids in by_type.items():
        model = RECORD_MODELS[record_type]
        candidates = _CONTAINER_ATTRS[record_type]
        rows = session.execute(
            select(model.id, *(attr for attr, _ in candidates)).where(
                model.id.in_(record_ids)
            )
        ).all()
        for row in rows:
            record_id = row[0]
            # ``strict=True`` because the two really must be the same length:
            # the SELECT above asks for ``id`` plus exactly one column per
            # candidate. Zip's default would silently drop the tail if they
            # ever diverged, and the symptom would be one candidate column
            # never consulted -- that is, a record type quietly losing its
            # container, which is the defect this whole module repairs.
            for (_, container_type), container_id in zip(
                candidates, row[1:], strict=True
            ):
                if container_id is not None:
                    container_ids[(record_type, record_id)] = (
                        container_type,
                        container_id,
                    )
                    break

    # Pass 2: name those containers, grouped by container type.
    # One call with every (type, id) pair, NOT a call per pair. The whole set
    # goes in so that ``resolve_record_public_refs`` can group it; handing it
    # one pair at a time would look identical here and cost a round trip per
    # distinct parent -- which on a real page, where rows do not share a
    # parent, is a round trip per row. Reproduced 2026-09-16; it passed all
    # 44 tests until ``test_a_page_whose_rows_have_DIFFERENT_parents_still
    # _costs_two`` was written, because every cost test then in the file put
    # its whole page under one shared parent.
    container_refs = resolve_record_public_refs(session, set(container_ids.values()))

    resolved: dict[tuple[SubmissionRecordType, int], RecordContainer] = {}
    for key, (container_type, container_id) in container_ids.items():
        ref = container_refs.get((container_type, container_id))
        if ref is not None:
            resolved[key] = RecordContainer(container_type, ref)
    return resolved


def resolve_record_container(
    session: Session,
    *,
    record_type: SubmissionRecordType,
    record_id: int,
) -> RecordContainer | None:
    """The container of one record, or ``None`` if it has none that can be named."""
    return resolve_record_containers(session, [(record_type, record_id)]).get(
        (record_type, record_id)
    )


__all__ = [
    "CONTAINED_RECORD_TYPES",
    "CONTAINER_RECORD_TYPES",
    "RecordContainer",
    "resolve_record_container",
    "resolve_record_containers",
]
