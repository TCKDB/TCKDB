"""Give molecular-property observations a citable handle.

Phase C-E5 adds a species-entry-scoped read of ``molecular_property_observation``
(``GET /api/v1/scientific/species-entries/{id}/observations``), and every other
scientific read on this surface answers with a public ref, never a database
primary key (see ``docs/specs/public_identifier_policy.md`` and
``app/services/scientific_read/internal_ids.py``). ``molecular_property_observation``
had no ``public_ref`` column, so the new route would have had to either leak an
integer id or omit the ref field the sibling reads all carry.

This adds ``public_ref`` (``mpo_`` prefix), backfilled here. Raw inserts get a
database-side 26-character lowercase RFC 4648 base32 fallback built from 128
unfixed UUID-random bits; ORM inserts take the same format from the global
listener in ``app.services.public_refs``. Downgrade drops the column and
therefore loses refs minted after this upgrade.

Same shape as ``c41dfc710b81`` (``calculation_artifact``) and ``c8b4e1a7d302``
(``artifact_integrity_event``) — table-scoped column add, function-backed
server-side backfill, opaque (not content-derived) ref: two rows with
identical scalar bytes from two distinct import or curator-attach events must
stay separately citable, same reasoning as those two tables.

On a large deployed table the backfill, unique-index build and NOT NULL
validation still take locks; schedule the upgrade during a low-traffic
window. As of this revision the table is CCCBDB-importer-sized (not yet
ThermoML-scale), so this is expected to be fast in practice.

Revision ID: d2f4a7c1b8e6
Revises: 0b4a3afabfd3
Create Date: 2026-09-20
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "d2f4a7c1b8e6"
down_revision: Union[str, Sequence[str], None] = "0b4a3afabfd3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLE = "molecular_property_observation"
_REF_FUNCTION = "mpo_opaque_public_ref"
_INDEX = "ix_molecular_property_observation_public_ref"
_SERVER_DEFAULT = sa.text(f"public.{_REF_FUNCTION}()")


def _create_ref_function() -> None:
    """Install an invoker-security, VOLATILE base32 function with 128 random bits.

    UUID v4 fixes hex nibbles 13 (version) and 17 (variant). Two UUIDs let this
    function discard those whole nibbles, retain 60 random hex chars, and take 32
    (128 bits) without adding a pgcrypto dependency. Same construction as
    ``c41dfc710b81`` / ``c8b4e1a7d302`` / ``6a9d2e4c7b1f``; only the prefix differs.
    """
    op.execute(
        f"""
        CREATE FUNCTION public.{_REF_FUNCTION}()
        RETURNS text LANGUAGE plpgsql VOLATILE AS $$
        DECLARE first_hex text; second_hex text; bytes bytea; out text := 'mpo_'; bit_pos integer; value integer;
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
