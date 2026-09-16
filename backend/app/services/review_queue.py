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

The exception is ``species_entry``, ``transition_state_entry`` AND
``reaction_entry``. All three have their OWN container (``species``,
``transition_state`` and ``reaction`` respectively) -- but all three are
also a disambiguating identity table the rest of the schema exists to
distinguish, not a bare pointer to one:

* two ``species_entry`` rows under one ``species`` are cis- and
  trans-diazene, two different scientific claims;
* a ``reaction_entry`` is bound to specific species entries via
  ``reaction_entry_structure_participant`` -- the SAME kind of axis, one
  level up: two ``reaction_entry`` rows under one ``reaction`` can differ
  in which stereoisomer or electronic state of a participant reacted, not
  merely in which submission deposited them.

Take the container-is-the-subject rule literally for any of the three own
review rows and the disambiguation collapses into the root bucket --
``species``, ``transition_state`` or ``reaction`` -- exactly what each
identity table exists to prevent. So none of the three is ever sent to its
container: a review row FOR a ``species_entry``, a ``transition_state_entry``
or a ``reaction_entry`` is its own subject, always. This was under-scoped
in an earlier revision of this module, which grouped a ``reaction_entry``'s
own row under ``reaction`` on the (false) claim that it "has no per-row
disambiguating axis" -- review #492 caught the resulting defect: a deposited
reaction produced two blocks, one of them a bare "Reaction rxn_..." with
nothing else to say about it, and the SAME ``rxe_...`` ref printed in both
-- the owner's original complaint, reappearing at the reaction root exactly
where the species root had already been fixed.

``conformer_group``, ``transition_state`` and ``network`` are different:
none of them has a per-row axis distinguishing one from a sibling under the
same parent (a reaction's transition_state rows are told apart by which
channel they represent, not by a column this registry can read), so the
plain rule applies to their own review rows too -- a ``conformer_group``'s
own row groups under its ``species_entry``, and so on.

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

**Naming a reaction subject.** ``kinetics`` and ``transition_state`` both
container to ``reaction_entry`` (see :mod:`app.services.record_containers`),
and -- since the self-subject fix above -- so does a ``reaction_entry``'s
OWN review row, which every reaction workflow writes one of. A
``reaction_entry`` is therefore a common subject type in real data --
review #492 measured a kinetics reviewer seeing "Reaction entry rxe_..."
and nothing else, the exact defect the species side of this module exists
to avoid, just not yet closed on the reaction side. Fixed the same way: a
reaction_entry has no formula (it is not one molecule), but it does have
the fact a reviewer actually wants, the equation itself -- reactants,
products and reversibility, resolved via ``reaction_entry_structure_
participant`` -> ``species_entry`` -> ``species`` (one hop) plus
``chem_reaction`` for ``reversible`` and its own ``reaction_participant``
table for graph-identity stoichiometry. See :func:`_resolve_reaction_entry_
chemistry` and :class:`ReactionEquation`.

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
    ReactionRole,
    RecordReviewStatus,
    SpeciesEntryStateKind,
    StationaryPointKind,
    SubmissionRecordType,
)
from app.db.models.reaction import (
    ChemReaction,
    ReactionEntry,
    ReactionEntryStructureParticipant,
    ReactionParticipant,
)
from app.db.models.record_review import RecordReview
from app.db.models.species import Species, SpeciesEntry
from app.db.models.transition_state import TransitionStateEntry
from app.services.record_containers import RecordContainer, resolve_record_containers
from app.services.record_refs import resolve_record_public_refs
from app.services.scientific_read.common import molecular_formula_expr
from app.services.scientific_read.species_identity import species_entry_label_for

#: Record types with a per-row disambiguating identity: grouping their OWN
#: review row under their container would collide siblings that are
#: different scientific claims into one subject. See the module docstring.
_SELF_SUBJECT_TYPES: frozenset[SubmissionRecordType] = frozenset(
    {
        SubmissionRecordType.species_entry,
        SubmissionRecordType.transition_state_entry,
        SubmissionRecordType.reaction_entry,
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
    #: The transition-state entry's OWN structural SMILES, as deposited --
    #: distinct from ``formula``, which is only ``mol_from_smiles(this)``
    #: successfully parsed. Carried separately because the two can and do
    #: disagree: a depositor can record a reaction-shaped string
    #: (``"[CH3].[H]>>C"``) that RDKit's single-molecule parser rejects,
    #: which yields ``formula=None`` while this field is very much set.
    #: Without it, review #492 caught the client unable to tell "nothing
    #: was ever recorded" from "something was recorded but no formula
    #: could be derived from it" -- two different facts that had been
    #: collapsed into the same caveat sentence. Never populated for a
    #: species_entry subject, which has no analogous raw-text fallback
    #: (species.smiles is NOT NULL by the time a species exists at all).
    unmapped_smiles: Optional[str] = None


@dataclass
class ReactionEquationParticipant:
    """One side's-worth of one species in a reaction equation."""

    species_entry_ref: str
    species_entry_label: Optional[str]
    smiles: str
    formula: Optional[str]
    stoichiometry: int
    participant_index: int


@dataclass
class ReactionEquation:
    """A reaction_entry subject's own chemistry: what it says, not a ref.

    Review #492, finding 6: a kinetics reviewer saw "Reaction entry
    rxe_..." and nothing else -- the species-only half of this module's
    "the page names species well and everything else not at all". A
    reaction_entry has no formula of its own (it is not one molecule),
    but it has exactly the fact a reviewer needs in its place: the
    equation. Reused wire shape, not invented: the fields below match
    `frontend/src/domain/reactionEquation.ts`'s `EquationParticipantInput`
    field-for-field, so the SAME `<ReactionEquation>` component the
    reaction entry page already renders draws this one too.
    """

    reversible: bool
    reactants: list[ReactionEquationParticipant]
    products: list[ReactionEquationParticipant]


@dataclass
class ReviewQueueSubject:
    subject_type: Optional[SubmissionRecordType]
    subject_ref: Optional[str]
    chemistry: SubjectChemistry
    rows: list[RecordReview] = field(default_factory=list)
    #: Set only for a ``reaction_entry`` subject with resolvable
    #: participants. ``None`` for every other subject type, AND for a
    #: reaction_entry whose participants could not be resolved -- the
    #: caller renders the generic type-label fallback in that case,
    #: same as any other subject with no chemistry to show.
    reaction: Optional[ReactionEquation] = None


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
            TransitionStateEntry.unmapped_smiles,
            molecular_formula_expr(TransitionStateEntry.unmapped_smiles).label(
                "formula"
            ),
        ).where(TransitionStateEntry.public_ref.in_(refs))
    ).all()
    return {
        row.public_ref: SubjectChemistry(
            formula=row.formula,
            multiplicity=row.multiplicity,
            unmapped_smiles=row.unmapped_smiles,
        )
        for row in rows
    }


def _resolve_reaction_entry_chemistry(
    session: Session, refs: list[str]
) -> dict[str, ReactionEquation]:
    """A reaction_entry subject's equation: its participants and roles.

    Deliberately NOT :func:`app.services.scientific_read.provenance.
    _build_species_section`, which this closely resembles: that function
    filters participants by the CALLER's visible review statuses (a
    public-read policy -- an unreviewed participant is hidden from an
    anonymous reader). This is a curator surface with the opposite job --
    show every participant so there is something to review -- so it would
    be the wrong filter re-applied, not a shortcut. One hop
    (``reaction_entry_structure_participant`` -> ``species_entry`` ->
    ``species``) plus a second lookup for the graph-identity stoichiometry
    (``chem_reaction.reaction_participant``, keyed by species rather than
    by structure-participant row -- see that function's own comment on
    why the two are not the same table).
    """
    if not refs:
        return {}
    rows = session.execute(
        select(
            ReactionEntry.public_ref.label("reaction_entry_ref"),
            ChemReaction.id.label("reaction_id"),
            ChemReaction.reversible,
            ReactionEntryStructureParticipant.role,
            ReactionEntryStructureParticipant.participant_index,
            SpeciesEntry.public_ref.label("species_entry_ref"),
            SpeciesEntry.stereo_label,
            SpeciesEntry.electronic_state_kind,
            SpeciesEntry.electronic_state_label,
            SpeciesEntry.term_symbol,
            SpeciesEntry.isotope_key,
            Species.id.label("species_id"),
            Species.smiles,
            molecular_formula_expr(Species.smiles).label("formula"),
        )
        .select_from(ReactionEntryStructureParticipant)
        .join(
            ReactionEntry,
            ReactionEntry.id == ReactionEntryStructureParticipant.reaction_entry_id,
        )
        .join(ChemReaction, ChemReaction.id == ReactionEntry.reaction_id)
        .join(
            SpeciesEntry,
            SpeciesEntry.id == ReactionEntryStructureParticipant.species_entry_id,
        )
        .join(Species, Species.id == SpeciesEntry.species_id)
        .where(ReactionEntry.public_ref.in_(refs))
    ).all()

    reaction_ids = {row.reaction_id for row in rows}
    stoichiometry_by_key: dict[tuple[int, int, ReactionRole], int] = {}
    if reaction_ids:
        stoich_rows = session.execute(
            select(
                ReactionParticipant.reaction_id,
                ReactionParticipant.species_id,
                ReactionParticipant.role,
                ReactionParticipant.stoichiometry,
            ).where(ReactionParticipant.reaction_id.in_(reaction_ids))
        ).all()
        stoichiometry_by_key = {
            (r.reaction_id, r.species_id, r.role): r.stoichiometry for r in stoich_rows
        }

    reversible_by_ref: dict[str, bool] = {}
    reactants_by_ref: dict[str, list[ReactionEquationParticipant]] = {}
    products_by_ref: dict[str, list[ReactionEquationParticipant]] = {}
    for row in rows:
        reversible_by_ref[row.reaction_entry_ref] = row.reversible
        participant = ReactionEquationParticipant(
            species_entry_ref=row.species_entry_ref,
            species_entry_label=species_entry_label_for(row),
            smiles=row.smiles,
            formula=row.formula,
            stoichiometry=stoichiometry_by_key.get(
                (row.reaction_id, row.species_id, row.role), 1
            ),
            participant_index=row.participant_index,
        )
        bucket = (
            reactants_by_ref
            if row.role is ReactionRole.reactant
            else products_by_ref
        )
        bucket.setdefault(row.reaction_entry_ref, []).append(participant)

    result: dict[str, ReactionEquation] = {}
    for ref, reversible in reversible_by_ref.items():
        reactants = sorted(
            reactants_by_ref.get(ref, []), key=lambda p: p.participant_index
        )
        products = sorted(
            products_by_ref.get(ref, []), key=lambda p: p.participant_index
        )
        result[ref] = ReactionEquation(
            reversible=reversible, reactants=reactants, products=products
        )
    return result


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
    A page never splits one subject's records across its own boundary.

    **What this does NOT promise: that paging is a stable walk.** The
    subject order is recomputed fresh on every call from whatever matches
    the filter *at that moment* -- there is no cursor or snapshot held
    between requests. If a record is judged (and so leaves the default
    ``not_reviewed`` filter) between a curator reading page 1 and
    requesting page 2, every subject's position can shift, and a subject
    that was about to cross the page-1/page-2 boundary can be skipped
    entirely -- read on neither page -- exactly as the flat list this
    replaces already warned about for its own offset paging. This is the
    ordinary cost of offset pagination over a set that changes underneath
    it, not specific to grouping by subject; grouping just makes the unit
    that can go missing bigger than one row. Closing it needs a stable
    walk -- a keyset cursor on the subject's newest ``(created_at, id)``
    rather than a plain integer offset -- which is not built here.
    """
    stmt = select(RecordReview).order_by(
        RecordReview.created_at.desc(), RecordReview.id.desc()
    )
    if status is not None:
        stmt = stmt.where(RecordReview.status == status)
    all_rows = list(session.scalars(stmt).all())

    if len(all_rows) > _ROW_RESOLUTION_CAP:
        # Reusing composed_search_candidate_limit_exceeded's CODE (the
        # shape -- a filtered set too large to traverse -- is the same),
        # but not its usual message. That message says "narrow the query
        # by status", and review of #492 caught that here the caller has
        # typically already done exactly that: the default and most
        # common filter IS one status (``not_reviewed``), and there is no
        # narrower one below it. Telling a curator to do the thing they
        # already did, while handing back no subjects, no pager and no
        # total, reads as a bug report against the caller. The message
        # below says only what a curator needs: how many records, and
        # that this route cannot group that many yet with nothing
        # narrower to try. The engineering fix stays here, in a comment,
        # not in a string shown on screen: the real answer is a
        # denormalized subject column on record_review, so this route
        # stops needing to read the whole filtered set to find the
        # subject boundaries. Not built yet.
        #
        # No literal double hyphen below -- review of #492 caught one
        # here, in a Python string the prose guard cannot see (it scans
        # only .ts/.tsx; tracked separately as #271). This route's own
        # DR-0028-adjacent copy rule still applies even where no gate
        # enforces it.
        status_desc = f"status '{status.value}'" if status is not None else "every status"
        raise CodedValueError(
            "composed_search_candidate_limit_exceeded",
            f"More than {_ROW_RESOLUTION_CAP} records currently have "
            f"{status_desc}. This page cannot group that many into "
            "subjects yet, and there is no narrower filter to try.",
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
    reaction_entry_refs = [
        key.subject_ref
        for key in page_keys
        if key.subject_type is SubmissionRecordType.reaction_entry
        and key.subject_ref is not None
    ]
    species_entry_chem = _resolve_species_entry_chemistry(session, species_entry_refs)
    ts_entry_chem = _resolve_transition_state_entry_chemistry(session, ts_entry_refs)
    reaction_entry_chem = _resolve_reaction_entry_chemistry(session, reaction_entry_refs)

    def _chemistry_for(key: SubjectKey) -> SubjectChemistry:
        # `key.subject_ref` is `Optional[str]`; each dict below is keyed by
        # plain `str`. Narrowed explicitly per branch rather than passed
        # through as-is -- the comprehensions above already guarantee a
        # non-None ref wherever a key actually lands in one of these dicts,
        # so this was never reachable with a None key, but a correct-by-
        # construction argument is not the same as a correctly-typed one.
        if key.subject_type is SubmissionRecordType.species_entry and key.subject_ref is not None:
            return species_entry_chem.get(key.subject_ref, SubjectChemistry())
        if (
            key.subject_type is SubmissionRecordType.transition_state_entry
            and key.subject_ref is not None
        ):
            return ts_entry_chem.get(key.subject_ref, SubjectChemistry())
        return SubjectChemistry()

    def _reaction_for(key: SubjectKey) -> Optional[ReactionEquation]:
        if key.subject_type is SubmissionRecordType.reaction_entry and key.subject_ref is not None:
            return reaction_entry_chem.get(key.subject_ref)
        return None

    subjects = [
        ReviewQueueSubject(
            subject_type=key.subject_type,
            subject_ref=key.subject_ref,
            chemistry=_chemistry_for(key),
            reaction=_reaction_for(key),
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
    "ReactionEquation",
    "ReactionEquationParticipant",
    "ReviewQueueResult",
    "ReviewQueueSubject",
    "SubjectChemistry",
    "SubjectKey",
    "list_review_queue",
]
