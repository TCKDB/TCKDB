"""Add opaque public refs to immutable reproducibility assessments.

Existing rows are backfilled in this revision.  The append-only trigger is
temporarily removed only for that backfill and is recreated before the upgrade
finishes. Raw SQL inserts retain a database-side 26-character lowercase RFC
4648 base32 fallback built from 128 unfixed UUID-random bits; ORM inserts use the global listener and receive the same
opaque ``rpa_`` format. Downgrade drops the column and therefore loses refs
created after this upgrade. On large deployed tables the backfill, unique-index
build, and NOT NULL validation can take time and acquire table locks; schedule
the upgrade during a low-traffic window.

Revision ID: 6a9d2e4c7b1f
Revises: f2a4c6e8b0d1
Create Date: 2026-07-22
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "6a9d2e4c7b1f"
down_revision: Union[str, Sequence[str], None] = "f2a4c6e8b0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLE = "record_reproducibility_assessment"
_TRIGGER = "trg_repro_assessment_append_only"
_FUNCTION = "reject_reproducibility_assessment_mutation"
_REF_FUNCTION = "rpa_opaque_public_ref"
_SERVER_DEFAULT = sa.text(f"public.{_REF_FUNCTION}()")


def _create_ref_function() -> None:
    """Install an invoker-security, VOLATILE base32 function with 128 random bits.

    UUID v4 fixes hex nibbles 13 (version) and 17 (variant). Two UUIDs let
    this function discard those whole nibbles, retain 60 random hex chars,
    and take 32 (128 bits) without adding a pgcrypto dependency.
    """
    op.execute(
        f"""
        CREATE FUNCTION public.{_REF_FUNCTION}()
        RETURNS text LANGUAGE plpgsql VOLATILE AS $$
        DECLARE first_hex text; second_hex text; bytes bytea; out text := 'rpa_'; bit_pos integer; value integer;
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


def _create_append_only_trigger() -> None:
    op.execute(
        f"""
        CREATE TRIGGER {_TRIGGER}
        BEFORE UPDATE OR DELETE ON {_TABLE}
        FOR EACH ROW
        EXECUTE FUNCTION {_FUNCTION}()
        """
    )


def upgrade() -> None:
    """Backfill a unique opaque ref without weakening append-only storage."""
    _create_ref_function()
    op.add_column(
        _TABLE,
        sa.Column("public_ref", sa.String(length=40), nullable=True, server_default=_SERVER_DEFAULT),
    )
    op.execute(f"DROP TRIGGER IF EXISTS {_TRIGGER} ON {_TABLE}")
    op.execute(sa.text(f"UPDATE {_TABLE} SET public_ref = DEFAULT WHERE public_ref IS NULL"))
    op.alter_column(_TABLE, "public_ref", existing_type=sa.String(length=40), nullable=False)
    op.create_index(op.f("ix_record_reproducibility_assessment_public_ref"), _TABLE, ["public_ref"], unique=True)
    _create_append_only_trigger()


def downgrade() -> None:
    """Remove public refs; downgrade discards refs written after this upgrade."""
    op.execute(f"DROP TRIGGER IF EXISTS {_TRIGGER} ON {_TABLE}")
    op.drop_index(op.f("ix_record_reproducibility_assessment_public_ref"), table_name=_TABLE)
    op.drop_column(_TABLE, "public_ref")
    op.execute(f"DROP FUNCTION IF EXISTS public.{_REF_FUNCTION}()")
    _create_append_only_trigger()
