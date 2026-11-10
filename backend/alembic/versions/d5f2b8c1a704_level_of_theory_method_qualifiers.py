"""replace level-of-theory keywords with typed method qualifiers

``level_of_theory.keywords`` (free text, hashed as written) is removed from the level's identity and dropped.
Four typed fields take over what it was used to carry, each part of the hash **only when stated** (the ADR 0021
``core_treatment`` pattern: a NULL adds no key):

* ``relativistic_hamiltonian`` (new enum): none, dkh0, dkh2, dkhso, resc, x2c, zora, iora.
* ``pno_truncation`` (new enum): loose, normal, tight.
* ``ri_approximation`` (new enum): none, rij, rijcosx, rijk, rijonx.
* ``core_treatment`` gains the value ``window`` (a partial frozen core).

What this revision does NOT do: re-key. The hash formula changed (the ``"keywords": null`` key is gone from every
payload), so every stored ``lot_hash`` is stale under the new formula. The deployed instance is dev data and is
reseeded rather than re-keyed, so ``upgrade()`` **refuses to run while ``level_of_theory`` holds any row**, with a
message naming the reseed. There is no backfill and no merge. Run it on an empty database (a fresh ``alembic
upgrade head``, or after the reseed's truncate).

Downgrade: refuses while ``level_of_theory`` holds any row, for the same reason in reverse (the old formula would
find every hash stale, and a stated qualifier has nowhere to go). On an empty table it re-adds the nullable
``keywords`` column and drops the three columns and their types. The value ``window`` stays in the
``core_treatment`` type: PostgreSQL cannot drop one enum value without rebuilding the type, the same asymmetry
``5eaf03c94f9b`` documents for ``artifact_kind``. ``ADD VALUE IF NOT EXISTS`` makes upgrading again a no-op.

The ``window`` value is added inside ``autocommit_block()``: PostgreSQL does not let a value added by
``ALTER TYPE ... ADD VALUE`` be used in the transaction that added it, and the autocommit block is Alembic's
escape hatch for that (see ``0b4a3afabfd3``). Nothing in this revision uses the value.

Revision ID: d5f2b8c1a704
Revises: c8e2a6d1f305
Create Date: 2026-11-05 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d5f2b8c1a704"
down_revision: Union[str, Sequence[str], None] = "c8e2a6d1f305"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ENUMS = {
    "relativistic_hamiltonian": ("none", "dkh0", "dkh2", "dkhso", "resc", "x2c", "zora", "iora"),
    "pno_truncation": ("loose", "normal", "tight"),
    "ri_approximation": ("none", "rij", "rijcosx", "rijk", "rijonx"),
}


def _refuse_if_rows(direction: str, why: str) -> None:
    count = op.get_bind().execute(sa.text("SELECT count(*) FROM level_of_theory")).scalar_one()
    if count:
        raise RuntimeError(
            f"Cannot {direction}: level_of_theory holds {count} row(s). {why} "
            "This revision does not re-key or merge rows. Reseed the instance (empty level_of_theory and "
            "every table that references it, then deposit again) and run the migration on the empty database."
        )


def upgrade() -> None:
    _refuse_if_rows(
        "upgrade",
        "Their lot_hash values were computed with a free-text keywords key that the new formula no longer "
        "contains, so every one of them would be stale.",
    )
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE core_treatment ADD VALUE IF NOT EXISTS 'window'")
    for name, values in _ENUMS.items():
        postgresql.ENUM(*values, name=name).create(op.get_bind(), checkfirst=True)
        op.add_column(
            "level_of_theory",
            sa.Column(name, postgresql.ENUM(*values, name=name, create_type=False), nullable=True),
        )
    op.drop_column("level_of_theory", "keywords")


def downgrade() -> None:
    _refuse_if_rows(
        "downgrade",
        "Under the previous formula every stored lot_hash would be stale, and a stated method qualifier "
        "(including core_treatment 'window') has no place in the previous schema.",
    )
    op.add_column("level_of_theory", sa.Column("keywords", sa.Text(), nullable=True))
    for name, values in _ENUMS.items():
        op.drop_column("level_of_theory", name)
        postgresql.ENUM(*values, name=name).drop(op.get_bind(), checkfirst=True)
