"""Declare the statistical model a statmech record supplies.

Adds ``statmech_model_declaration`` (at most one per statmech record), ``statmech_model_member`` (the members of an
explicit equilibrium ensemble) and ``statmech_model_source`` (what each part of the model rests on). See
``app/db/models/statmech_declaration.py`` for what each means and ``backend/docs/specs/statmech_selection_contract.md``
for why.

Additive only. No existing table or row is touched and nothing is backfilled: a statmech record deposited before this
revision declares no model, which reads as "not stated", never as an inferred ensemble, an unscaled model or a
projected one. ``downgrade()`` drops the three tables and the two enums, and prints how many declarations it forgets.

Owner rules are database facts (triggers below): a basin is a conformer group of the record's own species entry; a
transition-state declaration names a saddle determination of the record's own transition-state entry; an ensemble
member is another statmech record of the same species entry (never the record itself); a source calculation belongs
to the record's own subject, and a stated geometry is one the calculation ran on or produced.

Accepted-science protection: a declaration, its members and its sources are scientific content of their statmech
record. ``tckdb_guard_accepted_child`` / ``tckdb_guard_accepted_via_child`` (``c6f2a9d4e7b1``) refuse INSERT, UPDATE and
DELETE once that statmech record has ever been approved, and TRUNCATE is refused outright. Approving a record that is a
*member* of someone else's ensemble does not freeze that ensemble: the root is the declaring record. Correcting a
declared model means a new, superseding statmech record.

Revision ID: e6b3a9d1f742
Revises: e5b2c8a1d7f3
Create Date: 2026-10-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e6b3a9d1f742"
down_revision: Union[str, Sequence[str], None] = "e5b2c8a1d7f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SCOPE = postgresql.ENUM(
    "atom",
    "basin",
    "explicit_equilibrium_ensemble",
    "ts_determination",
    name="statmech_model_scope_kind",
    create_type=False,
)
_ROLE = postgresql.ENUM(
    "geometry",
    "curvature",
    "absolute_energy",
    "relative_energy",
    "electronic_levels",
    "torsional_surface",
    "scale_factor",
    "parameters",
    name="statmech_model_source_role",
    create_type=False,
)
_ENUMS = (_SCOPE, _ROLE)

_DECLARATION = "statmech_model_declaration"
_MEMBER = "statmech_model_member"
_SOURCE = "statmech_model_source"

#: The accepted-science registry this revision adds (``c6f2a9d4e7b1``'s regime). ``(table, record_type, column)``:
#: guarded by ``tckdb_guard_accepted_child`` against the statmech record named directly on the row.
_DIRECT_CHILDREN: tuple[tuple[str, str, str], ...] = (("statmech_model_declaration", "statmech", "statmech_id"),)
#: ``(table, record_type, child_column, parent_table, parent_pk, root_column)``: a member or a source names no statmech
#: record of its own and reaches the declaring one through its declaration. A *cited* member's record is deliberately not a
#: root of anything here: approving it freezes nothing of the ensemble that cites it.
_VIA_CHILDREN: tuple[tuple[str, str, str, str, str, str], ...] = (
    ("statmech_model_member", "statmech", "declaration_id", "statmech_model_declaration", "id", "statmech_id"),
    ("statmech_model_source", "statmech", "declaration_id", "statmech_model_declaration", "id", "statmech_id"),
)
#: TRUNCATE bypasses row triggers, so every table needs the statement-level refusal.
_TRUNCATE_TABLES: tuple[str, ...] = ("statmech_model_declaration", "statmech_model_member", "statmech_model_source")

_DECLARATION_GUARD = "tckdb_statmech_model_declaration_guard"
_MEMBER_GUARD = "tckdb_statmech_model_member_guard"
_SOURCE_GUARD = "tckdb_statmech_model_source_guard"
_IMMUTABLE = "tckdb_statmech_model_immutable"

#: A declared model is an immutable revision: its recipe, members and sources are never edited. A correction is a
#: superseding statmech record. Unconditional, so it holds before approval too and for every column (the owner guards
#: above run on INSERT and are therefore not bypassable by an UPDATE).
_IMMUTABLE_SQL = f"""
CREATE OR REPLACE FUNCTION public.{_IMMUTABLE}()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is an immutable declared model: supersede the statmech record instead of editing %', TG_TABLE_NAME, TG_TABLE_NAME
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$;
"""

_DECLARATION_GUARD_SQL = f"""
CREATE OR REPLACE FUNCTION public.{_DECLARATION_GUARD}()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE host_species bigint; host_ts bigint;
BEGIN
    SELECT species_entry_id, transition_state_entry_id INTO host_species, host_ts
    FROM public.statmech WHERE id = NEW.statmech_id;
    IF NEW.scope_kind = 'ts_determination' THEN
        IF host_ts IS NULL OR NOT EXISTS (
            SELECT 1 FROM public.structure_determination d
            WHERE d.id = NEW.structure_determination_id
              AND d.transition_state_entry_id = host_ts AND d.target_kind = 'saddle_point'
        ) THEN
            RAISE EXCEPTION
                'statmech_model_declaration_owner: a ts_determination scope names a saddle determination of the record''s own transition state entry'
                USING ERRCODE = '23514';
        END IF;
    ELSE
        IF host_species IS NULL THEN
            RAISE EXCEPTION
                'statmech_model_declaration_owner: an atom, basin or ensemble scope belongs to a species-owned statmech record'
                USING ERRCODE = '23514';
        END IF;
        IF NEW.scope_kind = 'basin' AND NOT EXISTS (
            SELECT 1 FROM public.conformer_group g
            WHERE g.id = NEW.conformer_group_id AND g.species_entry_id = host_species
        ) THEN
            RAISE EXCEPTION
                'statmech_model_declaration_owner: a basin scope names a conformer group of the record''s own species entry'
                USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$
"""

_MEMBER_GUARD_SQL = f"""
CREATE OR REPLACE FUNCTION public.{_MEMBER_GUARD}()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE host_id bigint; host_scope public.statmech_model_scope_kind; host_species bigint; member_species bigint;
BEGIN
    SELECT d.statmech_id, d.scope_kind INTO host_id, host_scope
    FROM public.statmech_model_declaration d WHERE d.id = NEW.declaration_id;
    IF host_scope IS DISTINCT FROM 'explicit_equilibrium_ensemble' THEN
        RAISE EXCEPTION
            'statmech_model_member_scope: only an explicit_equilibrium_ensemble declaration has members'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.member_statmech_id = host_id THEN
        RAISE EXCEPTION
            'statmech_model_member_self: a declaration is not a member of itself'
            USING ERRCODE = '23514';
    END IF;
    SELECT species_entry_id INTO host_species FROM public.statmech WHERE id = host_id;
    SELECT species_entry_id INTO member_species FROM public.statmech WHERE id = NEW.member_statmech_id;
    IF member_species IS NULL OR member_species IS DISTINCT FROM host_species THEN
        RAISE EXCEPTION
            'statmech_model_member_owner: a member is a statmech record of the same species entry'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.conformer_group_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM public.conformer_group g
        WHERE g.id = NEW.conformer_group_id AND g.species_entry_id = host_species
    ) THEN
        RAISE EXCEPTION
            'statmech_model_member_owner: a member''s basin is a conformer group of the same species entry'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$
"""

_SOURCE_GUARD_SQL = f"""
CREATE OR REPLACE FUNCTION public.{_SOURCE_GUARD}()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE host_species bigint; host_ts bigint; calc_species bigint; calc_ts bigint;
BEGIN
    IF NEW.calculation_id IS NOT NULL THEN
        SELECT s.species_entry_id, s.transition_state_entry_id INTO host_species, host_ts
        FROM public.statmech_model_declaration d JOIN public.statmech s ON s.id = d.statmech_id
        WHERE d.id = NEW.declaration_id;
        SELECT species_entry_id, transition_state_entry_id INTO calc_species, calc_ts
        FROM public.calculation WHERE id = NEW.calculation_id;
        IF NOT ((host_species IS NOT NULL AND calc_species = host_species)
                OR (host_ts IS NOT NULL AND calc_ts = host_ts)) THEN
            RAISE EXCEPTION
                'statmech_model_source_owner: a source calculation belongs to the declaring record''s own subject'
                USING ERRCODE = '23514';
        END IF;
        IF NEW.geometry_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM public.calculation_input_geometry i
            WHERE i.calculation_id = NEW.calculation_id AND i.geometry_id = NEW.geometry_id
        ) AND NOT EXISTS (
            SELECT 1 FROM public.calculation_output_geometry o
            WHERE o.calculation_id = NEW.calculation_id AND o.geometry_id = NEW.geometry_id
        ) THEN
            RAISE EXCEPTION
                'statmech_model_source_geometry: a stated geometry is one the source calculation ran on or produced'
                USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$
"""


def _trigger_name(prefix: str, table: str) -> str:
    """Mirror ``c6f2a9d4e7b1``'s naming, suffixed with the table and truncated to PostgreSQL's 63 bytes."""

    return f"trg_{prefix}_{table}"[:63]


def _child_groups() -> list[tuple[str, str, tuple[str, ...]]]:
    """Collapse the registry to one trigger per ``(table, record_type)``."""

    grouped: dict[tuple[str, str], list[str]] = {}
    for table, record_type, column in _DIRECT_CHILDREN:
        grouped.setdefault((table, record_type), []).append(column)
    return [(table, record_type, tuple(columns)) for (table, record_type), columns in grouped.items()]


def upgrade() -> None:
    bind = op.get_bind()
    for enum in _ENUMS:
        enum.create(bind, checkfirst=True)

    op.create_table(
        _DECLARATION,
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("statmech_id", sa.BigInteger(), nullable=False),
        sa.Column("schema_version", sa.SmallInteger(), nullable=False),
        sa.Column("scope_kind", _SCOPE, nullable=False),
        sa.Column("conformer_group_id", sa.BigInteger(), nullable=True),
        sa.Column("structure_determination_id", sa.BigInteger(), nullable=True),
        sa.Column("literature_id", sa.BigInteger(), nullable=True),
        sa.Column("workflow_tool_release_id", sa.BigInteger(), nullable=True),
        sa.Column("recipe", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("public_ref", sa.String(length=40), nullable=False),
        sa.CheckConstraint(
            "(scope_kind = 'basin') = (conformer_group_id IS NOT NULL) "
            "AND (scope_kind = 'ts_determination') = (structure_determination_id IS NOT NULL)",
            name=op.f("ck_statmech_model_declaration_scope_matches_pointers"),
        ),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_statmech_model_declaration_content_hash_sha256_hex")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(recipe) = 'object'", name=op.f("ck_statmech_model_declaration_recipe_object")
        ),
        sa.CheckConstraint(
            "literature_id IS NOT NULL OR workflow_tool_release_id IS NOT NULL",
            name=op.f("ck_statmech_model_declaration_attribution_required"),
        ),
        sa.CheckConstraint(
            "schema_version >= 1", name=op.f("ck_statmech_model_declaration_schema_version_positive")
        ),
        sa.ForeignKeyConstraint(
            ["conformer_group_id"],
            ["conformer_group.id"],
            name="fk_statmech_model_declaration_conformer_group",
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["app_user.id"],
            name=op.f("fk_statmech_model_declaration_created_by_app_user"),
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["literature_id"],
            ["literature.id"],
            name="fk_statmech_model_declaration_literature",
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["statmech_id"],
            ["statmech.id"],
            name=op.f("fk_statmech_model_declaration_statmech_id_statmech"),
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["structure_determination_id"],
            ["structure_determination.id"],
            name="fk_statmech_model_declaration_determination",
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["workflow_tool_release_id"],
            ["workflow_tool_release.id"],
            name="fk_statmech_model_declaration_workflow_tool_release",
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_statmech_model_declaration")),
        sa.UniqueConstraint("statmech_id", name=op.f("uq_statmech_model_declaration_statmech_id")),
    )
    op.create_index(
        op.f("ix_statmech_model_declaration_conformer_group_id"), _DECLARATION, ["conformer_group_id"], unique=False
    )
    op.create_index(op.f("ix_statmech_model_declaration_public_ref"), _DECLARATION, ["public_ref"], unique=True)
    op.create_index(
        op.f("ix_statmech_model_declaration_structure_determination_id"),
        _DECLARATION,
        ["structure_determination_id"],
        unique=False,
    )

    op.create_table(
        _MEMBER,
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("declaration_id", sa.BigInteger(), nullable=False),
        sa.Column("member_key", sa.Text(), nullable=False),
        sa.Column("member_statmech_id", sa.BigInteger(), nullable=False),
        sa.Column("conformer_group_id", sa.BigInteger(), nullable=True),
        sa.Column("counting_multiplicity", sa.SmallInteger(), nullable=True),
        sa.Column("relative_energy_kj_mol", sa.Double(), nullable=True),
        sa.Column("region_key", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "relative_energy_kj_mol IS NULL OR "
            "(relative_energy_kj_mol <> 'NaN' AND abs(relative_energy_kj_mol) < 'Infinity')",
            name=op.f("ck_statmech_model_member_relative_energy_finite"),
        ),
        sa.CheckConstraint(
            "counting_multiplicity IS NULL OR counting_multiplicity >= 1",
            name=op.f("ck_statmech_model_member_counting_multiplicity_ge_1"),
        ),
        sa.CheckConstraint(
            "length(btrim(member_key)) > 0 AND length(member_key) <= 64",
            name=op.f("ck_statmech_model_member_member_key_bounded"),
        ),
        sa.ForeignKeyConstraint(
            ["conformer_group_id"],
            ["conformer_group.id"],
            name="fk_statmech_model_member_conformer_group",
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["declaration_id"],
            ["statmech_model_declaration.id"],
            name="fk_statmech_model_member_declaration",
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["member_statmech_id"],
            ["statmech.id"],
            name="fk_statmech_model_member_statmech",
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_statmech_model_member")),
        sa.UniqueConstraint("declaration_id", "member_key", name="uq_statmech_model_member_key"),
        sa.UniqueConstraint("declaration_id", "member_statmech_id", name="uq_statmech_model_member_statmech"),
    )
    op.create_index(op.f("ix_statmech_model_member_declaration_id"), _MEMBER, ["declaration_id"], unique=False)
    op.create_index(
        op.f("ix_statmech_model_member_member_statmech_id"), _MEMBER, ["member_statmech_id"], unique=False
    )

    op.create_table(
        _SOURCE,
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("declaration_id", sa.BigInteger(), nullable=False),
        sa.Column("source_key", sa.Text(), nullable=False),
        sa.Column("role", _ROLE, nullable=False),
        sa.Column("calculation_id", sa.BigInteger(), nullable=True),
        sa.Column("geometry_id", sa.BigInteger(), nullable=True),
        sa.Column("literature_id", sa.BigInteger(), nullable=True),
        sa.Column("frequency_scale_factor_id", sa.BigInteger(), nullable=True),
        sa.Column("member_key", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "(role = 'scale_factor') = (frequency_scale_factor_id IS NOT NULL)",
            name=op.f("ck_statmech_model_source_scale_factor_role_matches_locator"),
        ),
        sa.CheckConstraint(
            "geometry_id IS NULL OR calculation_id IS NOT NULL",
            name=op.f("ck_statmech_model_source_geometry_needs_calculation"),
        ),
        sa.CheckConstraint(
            "length(btrim(source_key)) > 0 AND length(source_key) <= 64",
            name=op.f("ck_statmech_model_source_source_key_bounded"),
        ),
        sa.CheckConstraint(
            "num_nonnulls(calculation_id, literature_id, frequency_scale_factor_id) = 1",
            name=op.f("ck_statmech_model_source_exactly_one_locator"),
        ),
        sa.ForeignKeyConstraint(
            ["calculation_id"],
            ["calculation.id"],
            name="fk_statmech_model_source_calculation",
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["declaration_id", "member_key"],
            ["statmech_model_member.declaration_id", "statmech_model_member.member_key"],
            name="fk_statmech_model_source_member",
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["declaration_id"],
            ["statmech_model_declaration.id"],
            name="fk_statmech_model_source_declaration",
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["frequency_scale_factor_id"],
            ["frequency_scale_factor.id"],
            name="fk_statmech_model_source_frequency_scale_factor",
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["geometry_id"],
            ["geometry.id"],
            name="fk_statmech_model_source_geometry",
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["literature_id"],
            ["literature.id"],
            name="fk_statmech_model_source_literature",
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_statmech_model_source")),
        sa.UniqueConstraint("declaration_id", "source_key", name="uq_statmech_model_source_key"),
    )
    op.create_index(op.f("ix_statmech_model_source_calculation_id"), _SOURCE, ["calculation_id"], unique=False)
    op.create_index(op.f("ix_statmech_model_source_declaration_id"), _SOURCE, ["declaration_id"], unique=False)

    op.execute(_DECLARATION_GUARD_SQL)
    op.execute(_MEMBER_GUARD_SQL)
    op.execute(_SOURCE_GUARD_SQL)
    op.execute(_IMMUTABLE_SQL)
    for table in (_DECLARATION, _MEMBER, _SOURCE):
        op.execute(
            f"CREATE TRIGGER trg_{table}_immutable BEFORE UPDATE ON public.{table} "
            f"FOR EACH ROW EXECUTE FUNCTION public.{_IMMUTABLE}()"
        )
    for table, function in ((_DECLARATION, _DECLARATION_GUARD), (_MEMBER, _MEMBER_GUARD), (_SOURCE, _SOURCE_GUARD)):
        op.execute(
            f"CREATE TRIGGER trg_{table}_guard BEFORE INSERT ON public.{table} "
            f"FOR EACH ROW EXECUTE FUNCTION public.{function}()"
        )
    # Accepted-science protection: the declaring statmech record is the root.
    for table, record_type, columns in _child_groups():
        arguments = ", ".join(f"'{column}'" for column in columns)
        op.execute(
            f"""
            CREATE TRIGGER {_trigger_name("as_child", table)}
            BEFORE INSERT OR UPDATE OR DELETE ON public.{table}
            FOR EACH ROW
            EXECUTE FUNCTION public.tckdb_guard_accepted_child('{record_type}', {arguments})
            """
        )
    for table, record_type, child_column, parent_table, parent_pk, root_column in _VIA_CHILDREN:
        op.execute(
            f"""
            CREATE TRIGGER {_trigger_name("as_via", table)}
            BEFORE INSERT OR UPDATE OR DELETE ON public.{table}
            FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_via_child(
                '{record_type}', '{child_column}', '{parent_table}', '{parent_pk}', '{root_column}'
            )
            """
        )
    for table in _TRUNCATE_TABLES:
        op.execute(
            f"""
            CREATE TRIGGER {_trigger_name("as_truncate", table)}
            BEFORE TRUNCATE ON public.{table}
            FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate()
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    counts = {
        table: bind.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()
        for table in (_DECLARATION, _MEMBER, _SOURCE)
    }
    print(
        f"e6b3a9d1f742 downgrade forgets: {counts[_DECLARATION]} statmech model declaration(s), "
        f"{counts[_MEMBER]} member row(s) and {counts[_SOURCE]} source row(s)."
    )
    for table in reversed(_TRUNCATE_TABLES):
        op.execute(f"DROP TRIGGER IF EXISTS {_trigger_name('as_truncate', table)} ON public.{table}")
    for item in reversed(_VIA_CHILDREN):
        op.execute(f"DROP TRIGGER IF EXISTS {_trigger_name('as_via', item[0])} ON public.{item[0]}")
    for table, _, _ in _child_groups():
        op.execute(f"DROP TRIGGER IF EXISTS {_trigger_name('as_child', table)} ON public.{table}")
    for table in (_SOURCE, _MEMBER, _DECLARATION):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_guard ON public.{table}")
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_immutable ON public.{table}")
    op.execute(f"DROP FUNCTION IF EXISTS public.{_IMMUTABLE}()")
    for function in (_SOURCE_GUARD, _MEMBER_GUARD, _DECLARATION_GUARD):
        op.execute(f"DROP FUNCTION IF EXISTS public.{function}()")
    op.drop_table(_SOURCE)
    op.drop_table(_MEMBER)
    op.drop_table(_DECLARATION)
    for enum in reversed(_ENUMS):
        enum.drop(bind, checkfirst=True)
