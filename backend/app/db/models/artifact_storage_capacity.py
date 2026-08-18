"""Durable observations about whether the object store will accept bytes.

TCKDB learned, in the work that produced the ``artifact_storage_full``
code, that a full object store is *invisible to inspection*: measured
against MinIO on a volume filled to its free-space threshold,
``head_bucket`` returns 200, every read succeeds, and a 1-byte write
succeeds on the same store that refuses a 4 MiB one. The S3 API exposes
no capacity query. So the signal has to be what the **real write path**
was told.

That fact then lived in a module global, and a restart forgot it: the API
came back up reporting healthy while every artifact upload still failed.
This table is where the fact stops being process state.

Append-only, and deliberately without a boolean
-----------------------------------------------
There is no ``is_full`` column. "Is the store currently full?" is
computed as head-of-log by
:func:`app.services.artifact_storage_capacity.current_full_state`. A
stored flag would be a second source of truth able to disagree with the
log it summarises, which is the ``is_current`` shape ADR 0007 rejected
for curated selections and ADR 0003 for accepted science. The same
reasoning applies here for the same reason: a claim about a subject is
stored beside it, never inside it, and a summary that can drift from its
evidence is worse than no summary.

Nothing here is ever updated or deleted. Recovery is a **new**
observation that supersedes the older refusal, exactly as a repaired
object is a new ``artifact_integrity_event`` rather than an edit to the
break.

Why the size is the load-bearing column
---------------------------------------
Because clearing on any successful write is wrong, and it is wrong in the
only direction that matters. The same store refused 8 MiB and accepted 1
byte in the same second. A latch cleared by the 1-byte write would report
a healthy store while every real ESS log was still being rejected — a
false negative in a health signal, which is strictly worse than no signal
because it is *confidently* wrong.

So a refusal records the size it was refused at, and only a later
observation of at least that size answers it. See
:class:`~app.db.models.common.ArtifactStorageCapacityObservation` for what
the byte count means for each kind.

Deployment-local, not science
-----------------------------
Excluded from ``tckdb.archive.v1``: this is an account of one
deployment's object store, and restoring it elsewhere would import a
storage incident that never happened to the restoring cluster.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import BigInteger, CheckConstraint, Index, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedByMixin, TimestampMixin
from app.db.models.common import ArtifactStorageCapacityObservation


class ArtifactStorageCapacityEvent(Base, TimestampMixin, CreatedByMixin):
    """One observation about the object store's capacity to accept a write.

    A row says: *at this moment, the store refused / accepted a write of
    this many bytes, or reported this much room, or an operator declared
    the matter closed.*
    """

    __tablename__ = "artifact_storage_capacity_event"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    observation: Mapped[ArtifactStorageCapacityObservation] = mapped_column(
        SAEnum(
            ArtifactStorageCapacityObservation,
            name="artifact_storage_capacity_observation",
        ),
        nullable=False,
    )

    #: The byte count this observation is about. Its meaning depends on
    #: ``observation`` — see the enum. NULL is meaningful and not merely
    #: absent: on an ``operator_clear`` it is structural, and on a
    #: ``refused`` it means the refusing call never knew the object's size
    #: (the server-side-copy arm), which makes that refusal unanswerable
    #: by any size comparison and therefore clearable only by an operator.
    observed_bytes: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

    #: The store's own error code on a refusal, e.g. ``XMinioStorageFull``.
    #: Kept because it decides what may clear the refusal: a bucket-quota
    #: refusal is invisible to a free-space report (measured — 418 MiB
    #: free while a 2 MiB write was refused), so a capacity report must
    #: not be allowed to answer one.
    s3_code: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    #: Prose for the operator. Every machine-readable fact is a column
    #: above; nothing reads this, which is why it never reaches ``/status``
    #: — the store's message can carry a content digest, and ``/status``
    #: is public.
    detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # created_by from CreatedByMixin — the operator, on an operator_clear.
    # NULL on every machine-made observation, which is most of them.

    __table_args__ = (
        # An operator clear is not about a size, and a capacity report is
        # useless without one. Constraining both directions keeps the
        # head-of-log computation from having to guess what a NULL meant.
        CheckConstraint(
            "(observation <> 'operator_clear' OR observed_bytes IS NULL) "
            "AND (observation <> 'capacity_report' OR observed_bytes IS NOT NULL) "
            "AND (observation <> 'accepted' OR observed_bytes IS NOT NULL)",
            name="bytes_match_observation",
        ),
        # Only a refusal has a store error code to carry.
        CheckConstraint(
            "observation = 'refused' OR s3_code IS NULL",
            name="s3_code_only_on_refused",
        ),
        # The head-of-log query is "the newest refusal, then anything
        # after it", so both halves are served by ordering within a kind.
        Index(
            "ix_artifact_storage_capacity_event_observation_id",
            "observation",
            "id",
        ),
    )
