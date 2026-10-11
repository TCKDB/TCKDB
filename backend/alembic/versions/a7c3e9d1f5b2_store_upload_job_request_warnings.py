"""Let an upload job carry the warnings its request earned at enqueue.

Additive, no data step: one nullable ``upload_job.request_warnings`` JSONB column.

``POST /jobs/<kind>`` stores the request re-dumped as JSON and the worker parses that stored payload
again. A warning raised while parsing the original request is lost when the parse also *rewrote* the
field (a banner-style software version split into version and revision), because the second parse finds
nothing left to warn about (#670). The enqueue route now derives the request's warnings once, from the
request the depositor actually sent, and stores them here as a JSON list of ``{field, code, message}``;
the worker reports them first.

NULL means "not recorded": every job enqueued before this revision, for which the worker keeps deriving
the warnings from the stored payload as it always did. An empty list means "derived, and there were none".

Downgrade drops the column; a queued job then falls back to deriving its warnings from its payload.

Revision ID: a7c3e9d1f5b2
Revises: d3a8f6c1b952
Create Date: 2026-10-11
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a7c3e9d1f5b2"
down_revision: Union[str, Sequence[str], None] = "d3a8f6c1b952"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("upload_job", sa.Column("request_warnings", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("upload_job", "request_warnings")
