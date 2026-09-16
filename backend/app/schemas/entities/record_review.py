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


class ReviewQueueSubjectRead(SchemaBase):
    """One subject block: the record a reviewer judges as one unit, and
    every review row nested under it.

    ``subject_type``/``subject_ref`` are always null or non-null together,
    same contract as ``container_type``/``container_ref`` on
    :class:`RecordReviewRead`. Both null is the orphan case: a record with
    no public ref of its own whose container also could not be resolved
    (see :mod:`app.services.review_queue`'s ``SubjectKey`` docstring) --
    still one block, just one this page cannot link anywhere.
    """

    subject_type: SubmissionRecordType | None = None
    subject_ref: str | None = None
    chemistry: ReviewQueueSubjectChemistry
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
