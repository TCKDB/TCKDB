"""Give uploaded artifacts a citable handle.

The live calculation page renders an Artifacts table (bytes-on-S3 metadata:
kind, filename, sha256, size) but had nothing to cite each row by --
``calculation_artifact`` had no ``public_ref`` column, so the read layer
hard-coded ``artifact_ref=None`` for every row (see
``backend/docs/specs/scientific_calculation_reads.md`` open question 1). The
owner wants artifacts citable like every other record: species, reactions,
calculations, geometries, and so on.

This adds ``public_ref`` (``art_`` prefix), backfilled here. Raw inserts get a
database-side 26-character lowercase RFC 4648 base32 fallback built from 128
unfixed UUID-random bits; ORM inserts take the same format from the global
listener in ``app.services.public_refs``. Downgrade drops the column and
therefore loses refs minted after this upgrade.

The table is append-only by convention rather than by trigger (see the
``CalculationArtifact`` model docstring), so unlike ``6a9d2e4c7b1f`` there is
nothing to drop and recreate around the backfill -- same shape as
``c8b4e1a7d302`` (``artifact_integrity_event``), just a different table and
prefix. The ref is opaque, not content-derived: two rows with identical bytes
(same ``sha256``) are two distinct upload events and must stay separately
citable.

On a large deployed table the backfill, unique-index build and NOT NULL
validation still take locks; schedule the upgrade during a low-traffic
window.

Revision ID: c41dfc710b81
Revises: ae3d607cc9f8
Create Date: 2026-09-07
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "c41dfc710b81"
down_revision: Union[str, Sequence[str], None] = "ae3d607cc9f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLE = "calculation_artifact"
_REF_FUNCTION = "art_opaque_public_ref"
_INDEX = "ix_calculation_artifact_public_ref"
_SERVER_DEFAULT = sa.text(f"public.{_REF_FUNCTION}()")


def _create_ref_function() -> None:
    """Install an invoker-security, VOLATILE base32 function with 128 random bits.

    UUID v4 fixes hex nibbles 13 (version) and 17 (variant). Two UUIDs let this
    function discard those whole nibbles, retain 60 random hex chars, and take 32
    (128 bits) without adding a pgcrypto dependency. Same construction as
    ``c8b4e1a7d302`` / ``6a9d2e4c7b1f``; only the prefix differs.
    """
    op.execute(
        f"""
        CREATE FUNCTION public.{_REF_FUNCTION}()
        RETURNS text LANGUAGE plpgsql VOLATILE AS $$
        DECLARE first_hex text; second_hex text; bytes bytea; out text := 'art_'; bit_pos integer; value integer;
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
    """Add and backfill a unique opaque ref for every recorded artifact upload."""
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
