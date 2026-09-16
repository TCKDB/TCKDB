"""Group ``record_review`` rows into the unit a curator actually judges.

Task #269, following the owner's rejection of the flat record-review queue
("terrible design and unusable... why am I seeing the same pub ref in two
lines"). The flat list read direct from :func:`app.services.record_review.
list_record_reviews` pages one *record* at a time: two rows for the same
species (an atom-energy correction and a bond-additivity correction) render
as two lines with nothing telling them apart, and paging by record can split
one subject's records across a page boundary.

**The subject is the container, with one deliberate exception.**
:mod:`app.services.record_containers` already resolves every record to the
record it belongs to (PR #488) -- ``thermo`` and ``statmech`` belong to a
``species_entry``, ``kinetics`` and ``transition_state`` to a
``reaction_entry``, and so on. For most types that container IS the subject:
grouping a ``calculation`` or an ``applied_energy_correction`` under the
``species_entry`` it targets is exactly "judge this species' evidence as one
act".

The exception is ``species_entry`` and ``transition_state_entry`` themselves.
Both have their OWN container (``species`` and ``transition_state``
respectively) -- but both are also the disambiguating identity table the rest
of the schema exists to distinguish: two ``species_entry`` rows under one
``species`` are cis- and trans-diazene, two different scientific claims. Take
the container-is-the-subject rule literally for a ``species_entry``'s own
review row and both isomers collapse into one ``species`` bucket, exactly
the confusion ``species_entry`` was built to prevent. So these two types are
never sent to their container: a review row FOR a ``species_entry`` or a
``transition_state_entry`` is its own subject, always.

Every other contained type (``reaction_entry``, ``conformer_group``,
``transition_state``, ``network``) has no such per-row disambiguating axis,
so the plain rule applies to their own review rows too: a ``reaction_entry``'s
own row groups under its ``reaction``, a ``conformer_group``'s own row groups
under its ``species_entry``, and so on.

**What a subject looks like when it has no formula.** Two record types
answer the brief's open question about "a record whose container is itself
already independently addressable" (``calculation``, ``conformer_observation``
both already have their own page):

* ``calculation``'s container resolves in ONE hop to ``species_entry`` or
  ``transition_state_entry`` -- exactly the subject type this module already
  knows how to render with a formula. It groups, the same as ``thermo``.
* ``conformer_observation``'s container resolves in ONE hop to
  ``conformer_group`` -- which is not itself a formula-bearing type; reaching
  the species behind it needs a SECOND hop this module deliberately does not
  take (see :mod:`app.services.record_containers`'s own docstring: "the
  container IS the subject", not a second mapping). So a conformer group
  becomes a subject with no formula and no facets -- an honest, if less
  informative, block: "Conformer group cfg_..." with its own link, and the
  observations nested under it. This is a real gap, not a silent one; closing
  it would mean composing two container lookups, which is worth doing only if
  conformer_observation review rows turn out to be a meaningful fraction of
  a real queue.

**Naming a transition-state subject.** A ``transition_state_entry`` has no
formula of its own via any container walk -- reaching a reaction's
participants is three hops away (``transition_state_entry`` ->
``transition_state`` -> ``reaction_entry`` -> ``chem_reaction`` ->
``reaction_participant`` -> ``species``) and would need a formula computed
over a set of species, not one. But the table is not reaching for a name it
doesn't have: it carries its OWN structural identity, ``unmapped_smiles``
(nullable, depositor-supplied, un-atom-mapped) plus ``charge``/
``multiplicity`` -- exactly the columns ``species`` carries for the same
purpose, and already read the same way by
:mod:`app.services.scientific_read.geometry` (``_formula_expr`` over
``TransitionStateEntry.unmapped_smiles``, see that module's
``ix_...`` sibling code). This module reuses that, not
``reaction_entry_id``: the reaction is not the TS candidate's identity, its
own saddle-point structure is. When ``unmapped_smiles`` is null (never
recorded), the formula is honestly absent -- never invented from the
reaction it sits on.

**Cost.** Grouping requires knowing every filtered row's subject, not just
one page's, because a page boundary must fall between subjects. That means
reading every row matching the status filter (bounded by
``ix_record_review_status_record_type``, an index scan, not a sequential
scan of the table) and resolving all of their containers in the same
grouped-by-type batches :func:`app.services.record_containers.
resolve_record_containers` already uses elsewhere on this route. The one
step bounded by the FULL filtered set rather than the page is that resolve;
the formula/facet lookup below it only ever touches the page's own subjects.
Measured against the seed data this module's tests build (dozens of rows
across a dozen distinct types), the whole call resolves in a small, fixed
number of grouped queries -- see
``tests/services/test_review_queue.py::TestQueryCost``. The defensive cap
below exists for the day the backlog itself grows past what "read it all and
group in Python" should do to a request; the fix at that point is a
denormalized subject column on ``record_review``, not a bigger cap.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.error_contract import CodedValueError
from app.db.models.common import (
    RecordReviewStatus,
    SpeciesEntryStateKind,
    StationaryPointKind,
    SubmissionRecordType,
)
from app.db.models.record_review import RecordReview
from app.db.models.species import Species, SpeciesEntry
from app.db.models.transition_state import TransitionStateEntry
from app.services.record_containers import RecordContainer, resolve_record_containers
from app.services.record_refs import resolve_record_public_refs
from app.services.scientific_read.common import molecular_formula_expr

#: Record types with a per-row disambiguating identity: grouping their OWN
#: review row under their container would collide siblings that are
#: different scientific claims into one subject. See the module docstring.
_SELF_SUBJECT_TYPES: frozenset[SubmissionRecordType] = frozenset(
    {
        SubmissionRecordType.species_entry,
        SubmissionRecordType.transition_state_entry,
    }
)

#: Defensive cap on how many review rows one queue request will read and
#: group. Not a page size -- a page is subjects, this counts records across
#: every subject matching the filter. See the "Cost" section above.
_ROW_RESOLUTION_CAP = 5000


@dataclass(frozen=True)
class SubjectKey:
    """What tells one subject apart from another.

    ``subject_ref`` is ``None`` only for the orphan case: a row whose own
    record has no public ref (``applied_energy_correction``) AND whose
    container could not be resolved either -- the record_containers.py
    ``None`` case that "actually happens in production" (a review row
    outliving its record). ``review_row_id`` disambiguates that case so two
    unrelated orphaned rows of the same type never merge into one subject;
    it is carried for grouping only and is never put on the wire (DR-0028
    Req 2).
    """

    subject_type: Optional[SubmissionRecordType]
    subject_ref: Optional[str]
    review_row_id: Optional[int] = None


@dataclass
class SubjectChemistry:
    """Best-effort chemistry context for one subject block. All optional."""

    formula: Optional[str] = None
    multiplicity: Optional[int] = None
    species_entry_kind: Optional[StationaryPointKind] = None
    electronic_state_kind: Optional[SpeciesEntryStateKind] = None
    electronic_state_label: Optional[str] = None
    term_symbol: Optional[str] = None
    stereo_label: Optional[str] = None
    isotope_key: Optional[str] = None


@dataclass
class ReviewQueueSubject:
    subject_type: Optional[SubmissionRecordType]
    subject_ref: Optional[str]
    chemistry: SubjectChemistry
    rows: list[RecordReview] = field(default_factory=list)


@dataclass
class ReviewQueueResult:
    subjects: list[ReviewQueueSubject]
    #: Distinct subjects matching the filter -- the WHOLE backlog, not just
    #: this page. Honest because it is computed, not estimated: every
    #: matching row was read to build it.
    subject_total: int
    #: Records matching the filter, same honesty, same scope.
    record_total: int
    #: Refs/containers resolved for the rows returned on THIS page, handed
    #: back so the route can build ``RecordReviewRead`` without re-resolving.
    refs: dict[tuple[SubmissionRecordType, int], str]
    containers: dict[tuple[SubmissionRecordType, int], RecordContainer]


def _subject_key_for(
    row: RecordReview,
    *,
    refs: dict[tuple[SubmissionRecordType, int], str],
    containers: dict[tuple[SubmissionRecordType, int], RecordContainer],
) -> SubjectKey:
    row_key = (row.record_type, row.record_id)
    if row.record_type not in _SELF_SUBJECT_TYPES:
        container = containers.get(row_key)
        if container is not None:
            return SubjectKey(container.container_type, container.container_ref)
    own_ref = refs.get(row_key)
    if own_ref is not None:
        return SubjectKey(row.record_type, own_ref)
    # Orphan: no container, no ref of its own. Each such row is its own
    # subject -- see SubjectKey's docstring.
    return SubjectKey(row.record_type, None, review_row_id=row.id)


def _resolve_species_entry_chemistry(
    session: Session, refs: list[str]
) -> dict[str, SubjectChemistry]:
    if not refs:
        return {}
    rows = session.execute(
        select(
            SpeciesEntry.public_ref,
            SpeciesEntry.kind,
            SpeciesEntry.electronic_state_kind,
            SpeciesEntry.electronic_state_label,
            SpeciesEntry.term_symbol,
            SpeciesEntry.stereo_label,
            SpeciesEntry.isotope_key,
            Species.multiplicity,
            molecular_formula_expr(Species.smiles).label("formula"),
        )
        .join(Species, Species.id == SpeciesEntry.species_id)
        .where(SpeciesEntry.public_ref.in_(refs))
    ).all()
    return {
        row.public_ref: SubjectChemistry(
            formula=row.formula,
            multiplicity=row.multiplicity,
            species_entry_kind=row.kind,
            electronic_state_kind=row.electronic_state_kind,
            electronic_state_label=row.electronic_state_label,
            term_symbol=row.term_symbol,
            stereo_label=row.stereo_label,
            isotope_key=row.isotope_key,
        )
        for row in rows
    }


def _resolve_transition_state_entry_chemistry(
    session: Session, refs: list[str]
) -> dict[str, SubjectChemistry]:
    if not refs:
        return {}
    rows = session.execute(
        select(
            TransitionStateEntry.public_ref,
            TransitionStateEntry.multiplicity,
            molecular_formula_expr(TransitionStateEntry.unmapped_smiles).label(
                "formula"
            ),
        ).where(TransitionStateEntry.public_ref.in_(refs))
    ).all()
    return {
        row.public_ref: SubjectChemistry(formula=row.formula, multiplicity=row.multiplicity)
        for row in rows
    }


def list_review_queue(
    session: Session,
    *,
    status: Optional[RecordReviewStatus],
    limit: int,
    offset: int,
) -> ReviewQueueResult:
    """Page the review queue by subject.

    ``limit``/``offset`` count SUBJECTS, never records -- a caller asking
    for subjects 0-9 gets every record under all ten, however many that is.
    """
    stmt = select(RecordReview).order_by(
        RecordReview.created_at.desc(), RecordReview.id.desc()
    )
    if status is not None:
        stmt = stmt.where(RecordReview.status == status)
    all_rows = list(session.scalars(stmt).all())

    if len(all_rows) > _ROW_RESOLUTION_CAP:
        # Reusing composed_search_candidate_limit_exceeded rather than
        # minting a new catalogue entry: the shape is identical (a filtered
        # set that grew past what this endpoint will traverse) and the
        # remedy is identical (narrow the query -- here, by status).
        raise CodedValueError(
            "composed_search_candidate_limit_exceeded",
            f"The review queue matched more than {_ROW_RESOLUTION_CAP} "
            "records, which is too many to group by subject in one "
            "request; filter by status to narrow it.",
            context={
                "resource": "record_review_queue",
                "max_traversable": _ROW_RESOLUTION_CAP,
            },
        )

    keys = [(row.record_type, row.record_id) for row in all_rows]
    containers = resolve_record_containers(session, keys)
    refs = resolve_record_public_refs(session, keys)

    groups: "OrderedDict[SubjectKey, list[RecordReview]]" = OrderedDict()
    for row in all_rows:
        key = _subject_key_for(row, refs=refs, containers=containers)
        groups.setdefault(key, []).append(row)

    subject_total = len(groups)
    record_total = len(all_rows)

    page_keys = list(groups.keys())[offset : offset + limit]

    species_entry_refs = [
        key.subject_ref
        for key in page_keys
        if key.subject_type is SubmissionRecordType.species_entry
        and key.subject_ref is not None
    ]
    ts_entry_refs = [
        key.subject_ref
        for key in page_keys
        if key.subject_type is SubmissionRecordType.transition_state_entry
        and key.subject_ref is not None
    ]
    species_entry_chem = _resolve_species_entry_chemistry(session, species_entry_refs)
    ts_entry_chem = _resolve_transition_state_entry_chemistry(session, ts_entry_refs)

    subjects = [
        ReviewQueueSubject(
            subject_type=key.subject_type,
            subject_ref=key.subject_ref,
            chemistry=(
                species_entry_chem.get(key.subject_ref, SubjectChemistry())
                if key.subject_type is SubmissionRecordType.species_entry
                else ts_entry_chem.get(key.subject_ref, SubjectChemistry())
                if key.subject_type is SubmissionRecordType.transition_state_entry
                else SubjectChemistry()
            ),
            rows=groups[key],
        )
        for key in page_keys
    ]

    page_refs = {
        (row.record_type, row.record_id): refs[(row.record_type, row.record_id)]
        for subject in subjects
        for row in subject.rows
        if (row.record_type, row.record_id) in refs
    }
    page_containers = {
        (row.record_type, row.record_id): containers[(row.record_type, row.record_id)]
        for subject in subjects
        for row in subject.rows
        if (row.record_type, row.record_id) in containers
    }

    return ReviewQueueResult(
        subjects=subjects,
        subject_total=subject_total,
        record_total=record_total,
        refs=page_refs,
        containers=page_containers,
    )


__all__ = [
    "ReviewQueueResult",
    "ReviewQueueSubject",
    "SubjectChemistry",
    "SubjectKey",
    "list_review_queue",
]
