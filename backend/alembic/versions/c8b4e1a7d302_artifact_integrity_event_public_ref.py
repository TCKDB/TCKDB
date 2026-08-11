"""Give custody observations a citable handle.

The reproducibility rubric quotes the ``artifact_integrity_event`` it deferred
to, so a curator told that a record's evidence has rotted can go and read what
was actually seen. The citation it carried was the row's integer primary key,
which is an implementation detail of one database instance: it does not survive
a restore, it does not mean the same thing on the hosted deployment and on a lab
self-host, and the scientific read layer strips ``*_id`` keys by policy. So the
citation named something the reader could not look up.

This adds ``public_ref`` (``aie_``), backfilled here. Raw inserts get a
database-side 26-character lowercase RFC 4648 base32 fallback built from 128
unfixed UUID-random bits; ORM inserts take the same format from the global
listener in ``app.services.public_refs``. Downgrade drops the column and
therefore loses refs minted after this upgrade.

The table is append-only by convention rather than by trigger, so unlike
``6a9d2e4c7b1f`` there is nothing to drop and recreate around the backfill. On a
large deployed table the backfill, unique-index build and NOT NULL validation
still take locks; schedule the upgrade during a low-traffic window.

Revision ID: c8b4e1a7d302
Revises: a3d9f2c7b508
Create Date: 2026-08-11
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "c8b4e1a7d302"
down_revision: Union[str, Sequence[str], None] = "a3d9f2c7b508"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLE = "artifact_integrity_event"
_REF_FUNCTION = "aie_opaque_public_ref"
_INDEX = "ix_artifact_integrity_event_public_ref"
_SERVER_DEFAULT = sa.text(f"public.{_REF_FUNCTION}()")


def _create_ref_function() -> None:
    """Install an invoker-security, VOLATILE base32 function with 128 random bits.

    UUID v4 fixes hex nibbles 13 (version) and 17 (variant). Two UUIDs let this
    function discard those whole nibbles, retain 60 random hex chars, and take 32
    (128 bits) without adding a pgcrypto dependency. Same construction as
    ``6a9d2e4c7b1f``; only the prefix differs.
    """
    op.execute(
        f"""
        CREATE FUNCTION public.{_REF_FUNCTION}()
        RETURNS text LANGUAGE plpgsql VOLATILE AS $$
        DECLARE first_hex text; second_hex text; bytes bytea; out text := 'aie_'; bit_pos integer; value integer;
        BEGIN
          first_hex := replace(gen_random_uuid()::text, '-', '');
          second_hex := replace(gen_random_uuid()::text, '-', '');
          bytes := decode(substring(
            substring(first_hex, 1, 12) || substring(first_hex, 14, 3) || substring(first_hex, 18, 15) ||
            substring(second_hex, 1, 12) || substring(second_hex, 14, 3) || substring(second_hex, 18, 15),
            1, 32), 'hex');
          FOR char_pos IN 0..25 LOOP
            value := 0;
          FOR bit_offset IN 0..4 LOOP
            bit_pos := char_pos * 5 + bit_offset;
              IF bit_pos < 128 THEN
                value := value * 2 + ((get_byte(bytes, bit_pos / 8) >> (7 - (bit_pos % 8))) & 1);
              ELSE value := value * 2; END IF;
            END LOOP;
            out := out || substr('abcdefghijklmnopqrstuvwxyz234567', value + 1, 1);
          END LOOP;
          RETURN out;
        END $$
        """
    )


def upgrade() -> None:
    """Add and backfill a unique opaque ref for every recorded observation."""
    _create_ref_function()
    op.add_column(
        _TABLE,
        sa.Column(
            "public_ref",
            sa.String(length=40),
            nullable=True,
            server_default=_SERVER_DEFAULT,
        ),
    )
    op.execute(sa.text(f"UPDATE {_TABLE} SET public_ref = DEFAULT WHERE public_ref IS NULL"))
    op.alter_column(_TABLE, "public_ref", existing_type=sa.String(length=40), nullable=False)
    op.create_index(op.f(_INDEX), _TABLE, ["public_ref"], unique=True)


def downgrade() -> None:
    """Remove public refs; downgrade discards refs minted after this upgrade."""
    op.drop_index(op.f(_INDEX), table_name=_TABLE)
    op.drop_column(_TABLE, "public_ref")
    op.execute(f"DROP FUNCTION IF EXISTS public.{_REF_FUNCTION}()")
