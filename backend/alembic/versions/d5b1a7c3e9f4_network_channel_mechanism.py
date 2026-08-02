"""Declare whether a network channel is elementary or well-skipping.

Adds ``network_channel.mechanism``, a non-null machine token with two members:

``elementary``
    The channel is one elementary step (or several parallel elementary steps),
    each named on ``network_channel_microreaction``.

``well_skipping``
    A chemically-activated / well-skipping channel. Reactants form an energized
    intermediate that reacts onward before collisional stabilization, so the
    phenomenological k(T,P) comes out of the master equation and there is no
    single elementary step or saddle point behind it. Such a channel carries
    zero ``network_channel_microreaction`` rows *by declaration*.

Why a stored column rather than inferring it from an empty child collection:
"this rate is a multi-step ME channel" is a scientific claim about the channel,
not an absence of data, and a reader must be able to tell it apart from "the
producer forgot to supply the paths". ``network_channel_microreaction`` already
permits zero rows, so without this column the two cases are indistinguishable
in the database and on the read surface.

Backfill design
---------------
``server_default='elementary'`` is correct and unambiguous rather than a guess.
Revision ``c1d2e3f4a5b6`` refuses to run at all unless ``network_channel`` is
empty (pre-v2 channels have no producer-visible identity and must be exported,
deleted, and re-uploaded). Any ``network_channel`` row reachable by this
revision was therefore written under the v2 contract, which required at least
one ``microreaction_paths`` entry per channel — i.e. every existing row is
genuinely elementary. No legacy row is mislabelled.

The default is kept on the column so that a client of the ORM which does not
know about this axis still writes the conservative, evidence-bearing value.

Downgrade drops the column and the enum type; the mechanistic declaration is
lost, and well-skipping channels become indistinguishable from channels whose
paths were omitted. That is inherent to removing the axis.

Revision ID: d5b1a7c3e9f4
Revises: e3f4a5b6c7d8
Create Date: 2026-08-02
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d5b1a7c3e9f4"
down_revision: Union[str, Sequence[str], None] = "e3f4a5b6c7d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


network_channel_mechanism = postgresql.ENUM(
    "elementary",
    "well_skipping",
    name="network_channel_mechanism",
    create_type=False,
)


def upgrade() -> None:
    """Add the mechanistic-attribution token to ``network_channel``."""
    bind = op.get_bind()
    network_channel_mechanism.create(bind, checkfirst=True)
    op.add_column(
        "network_channel",
        sa.Column(
            "mechanism",
            network_channel_mechanism,
            nullable=False,
            server_default="elementary",
        ),
    )


def downgrade() -> None:
    """Remove the mechanism column and its enum type."""
    op.drop_column("network_channel", "mechanism")
    bind = op.get_bind()
    network_channel_mechanism.drop(bind, checkfirst=True)
