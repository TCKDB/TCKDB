"""record which artifact kind a parser-read parameter row came from

Parameters are read from an uploaded input deck and, from this revision on, from
the input a calculation's output log echoes. The two must not erase each other:
``calculation_parameter`` gains a nullable ``artifact_kind`` (the existing
``artifact_kind`` enum), so a re-parse replaces only the rows of its own kind.

Backfill: parameter extraction only ever ran on ``input`` artifacts, so every
existing ``source = 'parser'`` row is stamped ``input``. That is a statement
about the code that wrote them, not a guess. ``upload`` and ``curated`` rows
stay NULL, and a check constraint keeps it that way: ``artifact_kind`` is set
only on parser rows, and only to ``input`` or ``output_log``.

Downgrade deletes the ``output_log`` rows (they did not exist before this
revision and, without the column, could not be told from deck rows) and then
drops the constraint and the column.

Revision ID: a3d7f1c5b904
Revises: e5b1c8a3d702
Create Date: 2026-11-03 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3d7f1c5b904"
down_revision: Union[str, Sequence[str], None] = "e5b1c8a3d702"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# NAMING_CONVENTION expands the short name to ck_calculation_parameter_artifact_kind_parser_input_or_log.
_CONSTRAINT = "artifact_kind_parser_input_or_log"


def upgrade() -> None:
    """Add the column, stamp existing parser rows ``input``, add the constraint."""
    op.add_column(
        "calculation_parameter",
        sa.Column(
            "artifact_kind",
            postgresql.ENUM(name="artifact_kind", create_type=False),
            nullable=True,
        ),
    )
    op.execute("UPDATE calculation_parameter SET artifact_kind = 'input' WHERE source = 'parser'")
    op.create_check_constraint(
        _CONSTRAINT,
        "calculation_parameter",
        "artifact_kind IS NULL OR (source = 'parser' AND artifact_kind IN ('input', 'output_log'))",
    )


def downgrade() -> None:
    """Drop the rows only this revision could have written, then the constraint and the column."""
    op.drop_constraint(op.f("ck_calculation_parameter_" + _CONSTRAINT), "calculation_parameter", type_="check")
    op.execute("DELETE FROM calculation_parameter WHERE artifact_kind = 'output_log'")
    op.drop_column("calculation_parameter", "artifact_kind")
