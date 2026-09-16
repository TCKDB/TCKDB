"""Pydantic schemas for the record-review (per-record trust state) API."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.db.models.common import (
    RecordReviewStatus,
    SpeciesEntryStateKind,
    StationaryPointKind,
    SubmissionRecordType,
)
from app.schemas.common import (
    SchemaBase,
    TimestampedCreatedByReadSchema,
)


class RecordReviewRead(TimestampedCreatedByReadSchema):
    """Read schema for one ``record_review`` row."""

    record_type: SubmissionRecordType
    record_id: int
    #: The handle the archive is addressed by, resolved at read time.
    #:
    #: ``record_id`` alone names a row and nothing a reader can use: it
    #: cannot be pasted into a URL, quoted in an issue, or looked up
    #: through any public route. A review queue whose whole job is "go and
    #: look at this record" is unusable without this field, which is why
    #: it is resolved here rather than left to the client.
    #:
    #: ``None`` for either of the two reasons a record cannot be named:
    #: its table has no ``public_ref`` column
    #: (``applied_energy_correction``), or the row is gone. The same
    #: contract ``AdminCuratorTaskResponse.record_public_ref`` carries.
    #:
    #: The ``description`` is not a duplicate of the above: these ``#:``
    #: comments reach a reader of this file, and the description is what
    #: reaches a reader of the published OpenAPI, who has neither this
    #: source nor any other statement of when the field is null.
    record_public_ref: str | None = Field(
        default=None,
        description=(
            "The public handle naming this record, resolved at read time. "
            "Null when the record cannot be named: its table has no "
            "public_ref column (applied_energy_correction), or the row no "
            "longer exists. Prefer this over record_id, which is an "
            "internal row id and resolves through no public route."
        ),
    )
    #: Where this record can be *seen*, when it has no page of its own.
    #:
    #: ``record_public_ref`` names the record; these two say where to go and
    #: look at it. Six record types are rendered only inside their parent --
    #: thermo and statmech on the species entry page, kinetics and transition
    #: states on the reaction entry page -- so for those a ref addresses no
    #: route and a review queue built on the ref alone can point at nothing.
    #:
    #: The pair moves together on purpose. A ref with no type cannot be turned
    #: into a link, because the ``spc_``/``rxn_`` prefixes are a naming
    #: convention and not a contract a client may parse; a type with no ref
    #: names nothing. Either both are set or both are null.
    #:
    #: This is the record's owner in the schema (``thermo.species_entry_id``
    #: and its siblings), NOT a statement about which client can display what.
    #: Types that are perfectly addressable on their own carry it too, and a
    #: client that can already open the record simply prefers the record.
    #:
    #: Null is a NORMAL answer, not a failure, and it has exactly four causes.
    #: They are listed canonically in
    #: :mod:`app.services.record_containers` (``container-null-causes``); the
    #: ``description`` below restates the same four, in the same order, and
    #: must not drift from them -- an earlier draft said three in the module,
    #: four in the resolver and two here, so a reader of any one of them came
    #: away with a different idea of what null meant.
    #:
    #: The ``description`` is not a duplicate of this comment: it is what
    #: reaches a reader of the published OpenAPI, who has neither this source
    #: nor any other statement of when the field is null.
    container_type: SubmissionRecordType | None = Field(
        default=None,
        description=(
            "The record type of the record this one belongs to and is "
            "displayed inside, e.g. species_entry for a thermo or statmech, "
            "reaction_entry for a kinetics or transition state. Null for any "
            "of four reasons, all of them normal answers rather than errors: "
            "the record type has no owning parent (species, reaction and "
            "network are roots of their own trees); the record itself no "
            "longer exists; the record names no owner (not reachable for any "
            "type today, since every owning key is NOT NULL or covered by an "
            "exclusive-or constraint); or the owner no longer exists. Always "
            "null or non-null together with container_ref."
        ),
    )
    container_ref: str | None = Field(
        default=None,
        description=(
            "The public handle naming the record given by container_type, "
            "resolved at read time. This is what to open to see a record "
            "that has no page of its own: an applied_energy_correction "
            "cannot be named at all, but 'a correction on species entry "
            "spc_...' still says where to look. Null under exactly the "
            "conditions container_type is null."
        ),
    )
    status: RecordReviewStatus
    submission_id: int | None = None
    reviewed_by: int | None = None
    reviewed_at: datetime | None = None
    first_approved_at: datetime | None = None
    note: str | None = None


class RecordReviewSetStatusRequest(SchemaBase):
    """Payload for a curator manually setting a record's review status."""

    status: RecordReviewStatus
    submission_id: int | None = None
    note: str | None = Field(default=None)


class ReviewQueueSubjectChemistry(SchemaBase):
    """Best-effort chemistry context for a review-queue subject block.

    Every field is independently optional. A ``None`` here is never a
    failure to compute -- it is one of two honest facts: this subject type
    carries no such axis at all (a conformer group has no stereo label to
    report, ever), or the value genuinely was never recorded (a
    transition-state entry's ``unmapped_smiles`` is optional and often
    absent). See :mod:`app.services.review_queue` for which subject types
    populate which fields and why.
    """

    formula: str | None = Field(
        default=None,
        description=(
            "Hill-notation molecular formula, derived by the RDKit "
            "cartridge from the subject's own identity SMILES -- "
            "species.smiles for a species_entry subject, "
            "transition_state_entry.unmapped_smiles for a "
            "transition_state_entry subject. Null when the subject type "
            "has no such column, the SMILES was never recorded, or it "
            "failed to parse."
        ),
    )
    multiplicity: int | None = Field(
        default=None,
        description="Spin multiplicity (2S+1) of the subject, when it has one.",
    )
    species_entry_kind: StationaryPointKind | None = None
    electronic_state_kind: SpeciesEntryStateKind | None = None
    electronic_state_label: str | None = None
    term_symbol: str | None = None
    stereo_label: str | None = None
    isotope_key: str | None = None
    unmapped_smiles: str | None = Field(
        default=None,
        description=(
            "The transition-state entry's OWN structural SMILES, as "
            "deposited -- never null merely because `formula` is null. "
            "The two can disagree: a depositor can record a "
            "reaction-shaped string (e.g. '[CH3].[H]>>C') that the RDKit "
            "single-molecule parser rejects, which leaves `formula` null "
            "while this field is set. A client should use this to tell "
            "'nothing was ever recorded' (both null) apart from "
            "'recorded, but no formula could be derived from it' (this "
            "set, formula null) -- the two are different facts and read "
            "as different sentences. Always null for a species_entry "
            "subject, which has no analogous raw-text fallback."
        ),
    )


class ReviewQueueReactionParticipant(SchemaBase):
    """One participant in a reaction_entry subject's own equation."""

    species_entry_ref: str
    species_entry_label: str | None = None
    smiles: str
    formula: str | None = None
    stoichiometry: int
    participant_index: int


class ReviewQueueReactionEquation(SchemaBase):
    """A reaction_entry subject's own chemistry: the equation itself.

    A reaction_entry has no formula -- it is not one molecule -- so this
    is what fills the role `chemistry` plays for a species_entry or
    transition_state_entry subject. Field names match
    `frontend/src/domain/reactionEquation.ts`'s `EquationParticipantInput`
    exactly so the client's existing `<ReactionEquation>` component (the
    same one the reaction entry page renders) draws this one directly,
    rather than a second reaction-equation vocabulary being invented here.
    """

    reversible: bool
    reactants: list[ReviewQueueReactionParticipant]
    products: list[ReviewQueueReactionParticipant]


class ReviewQueueSubjectRead(SchemaBase):
    """One subject block: the record a reviewer judges as one unit, and
    every review row nested under it.

    ``subject_type``/``subject_ref`` are NOT the same null contract as
    ``container_type``/``container_ref`` on :class:`RecordReviewRead` --
    an earlier draft of this docstring claimed they were, which was wrong
    and caught in review. Three shapes, not two:

    * both set -- the ordinary case, a named, addressable subject;
    * both null -- the orphan case: a record with no public ref of its
      own whose container also could not be resolved (see
      :mod:`app.services.review_queue`'s ``SubjectKey`` docstring) --
      still one block, just one this page cannot link anywhere;
    * ``subject_type`` set, ``subject_ref`` null -- a ``species_entry``
      or ``transition_state_entry`` review row whose OWN record has since
      been deleted. Both types are their own subject unconditionally (see
      :mod:`app.services.review_queue`'s module docstring on the
      container-is-the-subject exception), so losing the record loses
      the ref but not the type -- there is nowhere else for that row's
      subject identity to fall back to.

    A client must therefore treat ``subject_ref is None`` as "cannot
    link", exactly as for the orphan case, but must not infer from a null
    ref that ``subject_type`` is null too.
    """

    subject_type: SubmissionRecordType | None = None
    subject_ref: str | None = None
    chemistry: ReviewQueueSubjectChemistry
    #: Set only for a `reaction_entry` subject whose participants could be
    #: resolved. `chemistry` above is a species/TS-shaped fact and does
    #: not apply to a reaction, which is not one molecule; this is what
    #: names a reaction_entry subject instead, the same way `chemistry`
    #: names a species_entry or transition_state_entry one.
    reaction: ReviewQueueReactionEquation | None = None
    records: list[RecordReviewRead]


class ReviewQueuePageRead(SchemaBase):
    """One page of the subject-grouped review queue.

    ``offset``/``limit`` count SUBJECTS. ``subject_total`` and
    ``record_total`` are computed over every row matching the filter, not
    just this page -- both are honest counts, never estimates, because
    building this page required reading every matching row to know where
    the subject boundaries fall. See :func:`app.services.review_queue.
    list_review_queue`.
    """

    subjects: list[ReviewQueueSubjectRead]
    subject_total: int
    record_total: int
    offset: int
    limit: int
