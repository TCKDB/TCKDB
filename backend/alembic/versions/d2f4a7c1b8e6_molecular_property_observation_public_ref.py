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

Review round 2 adds a second, independent change riding the same revision
(the revision is still in-flight/unmerged, so this is an in-place edit, not
a new revision -- see ``.claude/rules/migration-rules.md``): one new
``submission_audit_event_kind`` enum member, ``observation_identity_attached``,
for the curator identity-attach service (``app/services/
observation_identity_attach.py``) to record its curation fact as a
``SubmissionAuditEvent`` rather than by mutating the observation's
``raw_payload_json`` (a provenance column that must stay byte-identical
across a later curation act). ``ALTER TYPE ... ADD VALUE`` cannot be
undone short of rebuilding the enum type (same limitation documented in
``a7b8c9d0e1f2_add_llm_precheck_recorded_audit_event.py``, the precedent
for this addition); this revision's ``downgrade()`` still reverses the
``public_ref`` column, index and function, but the enum member is left in
an otherwise-harmless state: an unused value in a Postgres enum type costs
nothing and never gets written by code below the previous revision.

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

    # Curator identity-attach records its curation fact as a
    # SubmissionAuditEvent (app/services/observation_identity_attach.py)
    # instead of mutating raw_payload_json.
    op.execute(
        "ALTER TYPE submission_audit_event_kind "
        "ADD VALUE IF NOT EXISTS 'observation_identity_attached'"
    )


def downgrade() -> None:
    """Remove public refs; downgrade discards refs minted after this upgrade.

    The ``observation_identity_attached`` enum member added in ``upgrade()``
    is NOT removed: PostgreSQL cannot drop a single value from an enum type
    without rebuilding it, the same limitation documented in
    ``a7b8c9d0e1f2_add_llm_precheck_recorded_audit_event.py``. Leaving it in
    place is harmless -- an unreferenced enum value is inert -- so this
    downgrade still reverses everything that can be reversed rather than
    refusing outright.
    """
    op.drop_index(op.f(_INDEX), table_name=_TABLE)
    op.drop_column(_TABLE, "public_ref")
    op.execute(f"DROP FUNCTION IF EXISTS public.{_REF_FUNCTION}()")
