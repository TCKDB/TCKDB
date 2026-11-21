"""Record the unit of path-search and IRC coordinates.

Adds the ``path_coordinate_unit`` enum and two nullable columns using it:
``calc_path_search_result.path_coordinate_unit`` (unit of every point's ``path_coordinate``) and
``calc_irc_result.reaction_coordinate_unit`` (unit of every point's ``reaction_coordinate``). Existing rows stay NULL,
meaning the unit was not recorded.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e3b7c1a9d452"
down_revision: Union[str, Sequence[str], None] = "a6fc04f5667e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UNIT = postgresql.ENUM(
    "angstrom",
    "bohr",
    "amu_half_bohr",
    "amu_half_angstrom",
    "fraction",
    "step",
    name="path_coordinate_unit",
    create_type=False,
)


def upgrade() -> None:
    _UNIT.create(op.get_bind(), checkfirst=False)
    op.add_column("calc_path_search_result", sa.Column("path_coordinate_unit", _UNIT, nullable=True))
    op.add_column("calc_irc_result", sa.Column("reaction_coordinate_unit", _UNIT, nullable=True))


def downgrade() -> None:
    op.drop_column("calc_irc_result", "reaction_coordinate_unit")
    op.drop_column("calc_path_search_result", "path_coordinate_unit")
    _UNIT.drop(op.get_bind(), checkfirst=False)
