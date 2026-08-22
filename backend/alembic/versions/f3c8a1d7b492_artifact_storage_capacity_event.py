"""Make "the object store has no room" survive a restart.

The condition was already detected and already reported on ``/status``,
but it lived in a module global. A restart forgot it, and the API came
back up reporting healthy while every artifact upload still failed with
507. This revision gives the observation somewhere durable to live.

Append-only, and deliberately without an ``is_full`` boolean: "is the
store currently full?" is computed as head-of-log. A stored flag would be
a second source of truth able to disagree with the log it summarises,
which is the shape ADR 0007 rejected for curated selections.

The one column that carries the design is ``observed_bytes``. Measured
against MinIO on a filled volume, an 8 MiB write was refused while a
1-byte write succeeded in the same second, so a refusal is answered only
by later evidence of *at least that size*. Clearing on any successful
write would restore a green light while every real upload still failed.

Identifier lengths were checked rather than assumed — the longest name
here is 58 characters against Postgres' 63-character limit — because
revision ``b7e4d1a9c026`` exists to repair six constraint names that were
silently truncated.

Revision ID: f3c8a1d7b492
Revises: b7e4d1a9c026
Create Date: 2026-08-18

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f3c8a1d7b492"
down_revision: Union[str, Sequence[str], None] = "b7e4d1a9c026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ENUM_NAME = "artifact_storage_capacity_observation"
_ENUM_VALUES = ("refused", "accepted", "capacity_report", "operator_clear")


def upgrade() -> None:
    # ``create_type=False`` is a ``postgresql.ENUM`` keyword and NOT an
    # ``sa.Enum`` one: spelled with ``sa.Enum`` it is silently ignored,
    # ``op.create_table`` emits a second ``CREATE TYPE``, and the upgrade
    # dies on DuplicateObject. Revision ``d7f1a3c5e948`` carries the same
    # warning; this revision reproduced the failure before heeding it.
    observation = postgresql.ENUM(*_ENUM_VALUES, name=_ENUM_NAME, create_type=False)
    observation.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "artifact_storage_capacity_event",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("observation", observation, nullable=False),
        # NULL is meaningful: structural on an operator_clear, and on a
        # refusal it means the refusing call never knew the object's size,
        # which makes that refusal clearable only by an operator.
        sa.Column("observed_bytes", sa.BigInteger(), nullable=True),
        sa.Column("s3_code", sa.Text(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=False),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        # SHORT names, matching the model. ``NAMING_CONVENTION`` templates
        # CHECK names as ``ck_%(table_name)s_%(constraint_name)s``, and a
        # convention containing ``%(constraint_name)s`` is applied to
        # *explicitly* named constraints too. Passing the already-expanded
        # name here produced
        # ``ck_artifact_storage_capacity_event_ck_artifact_storage__4af9``
        # -- prefix twice, then truncated to a hash. That is the exact
        # defect revision ``b7e4d1a9c026`` exists to repair, reproduced in a
        # brand-new revision; ``alembic check`` does not compare CHECK names
        # and passed throughout, so only
        # ``tests/db/test_constraint_names_match_the_model.py`` caught it.
        sa.CheckConstraint(
            "(observation <> 'operator_clear' OR observed_bytes IS NULL) "
            "AND (observation <> 'capacity_report' OR observed_bytes IS NOT NULL) "
            "AND (observation <> 'accepted' OR observed_bytes IS NOT NULL)",
            name="bytes_match_observation",
        ),
        sa.CheckConstraint(
            "observation = 'refused' OR s3_code IS NULL",
            name="s3_code_only_on_refused",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["app_user.id"],
            name="fk_artifact_storage_capacity_event_created_by_app_user",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        sa.PrimaryKeyConstraint(
            "id", name="pk_artifact_storage_capacity_event"
        ),
    )
    # Serves both halves of the head-of-log query: the newest ``refused``
    # row, then whether anything after it answers that refusal.
    op.create_index(
        "ix_artifact_storage_capacity_event_observation_id",
        "artifact_storage_capacity_event",
        ["observation", "id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_artifact_storage_capacity_event_observation_id",
        table_name="artifact_storage_capacity_event",
    )
    op.drop_table("artifact_storage_capacity_event")
    # The enum type is owned by this revision and by nothing else, so it
    # goes with the table. Dropping it after the table, never before: a
    # type still referenced by a column cannot be dropped.
    postgresql.ENUM(name=_ENUM_NAME).drop(op.get_bind(), checkfirst=True)
