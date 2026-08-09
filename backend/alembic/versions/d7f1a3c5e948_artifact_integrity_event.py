"""record observed breaks in custody of stored artifacts

ADR 0014. ``ArtifactIntegrityError`` means the bytes retrieved from
object storage do not hash to the key they are stored under. That is
corruption of stored evidence or a swapped object, and until now it
produced a 502 and a log line and nothing durable. The next reader got
another 502 and no one was any wiser.

This revision adds ``artifact_integrity_event``: one append-only row per
observed break, carrying enough to tell the three causes apart (the
object was modified after write / we never stored what we said we did /
the store returned wrong bytes on this read) without a second
investigation.

Keyed on ``sha256``, with ``artifact_id`` nullable. The store is
content-addressed, so the unit of corruption is the object, and an
object may be shared by many ``calculation_artifact`` rows or — on the
``store_artifact`` dedup path, where the upload that would have created
the row is being refused — by none yet.

No backfill. Every historical integrity failure that has happened so far
exists only as a log line, if that; inventing rows for them would be
fabricating detections that carry a read-time trust consequence. The
table starts empty and honest, and the verification pass in
``backend/scripts/ops/verify_artifact_integrity.py`` is how an operator
finds out what is actually in the bucket.

Revision ID: d7f1a3c5e948
Revises: c2f7a4e8d1b6
Create Date: 2026-08-09 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d7f1a3c5e948"
down_revision: Union[str, Sequence[str], None] = "c2f7a4e8d1b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_FINDING_ENUM = "artifact_integrity_finding"
_FINDING_VALUES = (
    "digest_mismatch",
    "size_mismatch",
    "object_missing",
    "verified",
)

_CONTEXT_ENUM = "artifact_integrity_detection_context"
_CONTEXT_VALUES = (
    "download",
    "verification_sweep",
    "store_dedup_verification",
    "parameter_extraction",
    "archive",
)


def upgrade() -> None:
    """Create the integrity-event table and its two enums."""
    # ``create_type=False`` on the column objects: the types are created
    # explicitly just below, and ``op.create_table`` would otherwise
    # auto-create them a second time and fail on DuplicateObject.
    finding = postgresql.ENUM(*_FINDING_VALUES, name=_FINDING_ENUM, create_type=False)
    context = postgresql.ENUM(*_CONTEXT_VALUES, name=_CONTEXT_ENUM, create_type=False)
    finding.create(op.get_bind(), checkfirst=True)
    context.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "artifact_integrity_event",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.CHAR(64), nullable=False),
        sa.Column("artifact_id", sa.BigInteger(), nullable=True),
        sa.Column("finding", finding, nullable=False),
        sa.Column("detected_during", context, nullable=False),
        sa.Column("expected_bytes", sa.BigInteger(), nullable=True),
        sa.Column("observed_sha256", sa.CHAR(64), nullable=True),
        sa.Column("observed_bytes", sa.BigInteger(), nullable=True),
        sa.Column(
            "object_last_modified_at", sa.DateTime(timezone=False), nullable=True
        ),
        sa.Column("object_etag", sa.Text(), nullable=True),
        sa.Column("object_content_length", sa.BigInteger(), nullable=True),
        sa.Column("artifact_recorded_at", sa.DateTime(timezone=False), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=False),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_artifact_integrity_event")),
        sa.ForeignKeyConstraint(
            ["artifact_id"],
            ["calculation_artifact.id"],
            name=op.f(
                "fk_artifact_integrity_event_artifact_id_calculation_artifact"
            ),
            deferrable=True,
            initially="IMMEDIATE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["app_user.id"],
            name=op.f("fk_artifact_integrity_event_created_by_app_user"),
            deferrable=True,
            initially="IMMEDIATE",
        ),
        sa.CheckConstraint(
            "sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_artifact_integrity_event_sha256_lower_hex"),
        ),
        sa.CheckConstraint(
            "observed_sha256 IS NULL OR observed_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_artifact_integrity_event_observed_sha256_lower_hex"),
        ),
        sa.CheckConstraint(
            "(finding = 'object_missing' AND observed_sha256 IS NULL) "
            "OR (finding <> 'object_missing' AND observed_sha256 IS NOT NULL)",
            name=op.f("ck_artifact_integrity_event_observed_digest_present_iff_read"),
        ),
        sa.CheckConstraint(
            "finding <> 'verified' OR observed_sha256 = sha256",
            name=op.f("ck_artifact_integrity_event_verified_requires_matching_digest"),
        ),
    )
    op.create_index(
        op.f("ix_artifact_integrity_event_sha256"),
        "artifact_integrity_event",
        ["sha256"],
    )
    op.create_index(
        op.f("ix_artifact_integrity_event_artifact_id"),
        "artifact_integrity_event",
        ["artifact_id"],
    )
    # The trust evaluator asks "does this digest have any recorded break,
    # and when was the most recent one" on the read path. Composite so
    # that question is answered from the index alone.
    op.create_index(
        "ix_artifact_integrity_event_sha256_created_at",
        "artifact_integrity_event",
        ["sha256", "created_at"],
    )


def downgrade() -> None:
    """Drop the integrity-event table and its enums."""
    op.drop_index(
        "ix_artifact_integrity_event_sha256_created_at",
        table_name="artifact_integrity_event",
    )
    op.drop_index(
        op.f("ix_artifact_integrity_event_artifact_id"),
        table_name="artifact_integrity_event",
    )
    op.drop_index(
        op.f("ix_artifact_integrity_event_sha256"),
        table_name="artifact_integrity_event",
    )
    op.drop_table("artifact_integrity_event")
    sa.Enum(name=_CONTEXT_ENUM).drop(op.get_bind(), checkfirst=False)
    sa.Enum(name=_FINDING_ENUM).drop(op.get_bind(), checkfirst=False)
