"""Let a calculation declare its actual protocol and a record declare structure determinations.

Additive, with no data step. Three new tables, seven new enums, one nullable column and two
composite-key targets on ``calculation``:

``structure_determination`` (public ref prefix ``sdet``)
    One source-attributed claim about a defined geometry, conformer basin or saddle, and the
    quantity it supplies. Its identity is content -- the owner (a species entry, or a transition
    state entry), the target kind, the conformer observation of a basin, the source attribution
    (literature, workflow-tool release), a source-scoped key, the evaluated geometry and the pinned
    calculations -- and ``identity_hash`` is the unique digest of that content, so the same content
    resolves to one row. ``content_hash`` digests what it claims (quantity, energy convention,
    recipe), so a restatement of one identity that says something different is refused by the write
    path rather than merged. **Immutable from creation:** ``trg_structure_determination_immutable``
    refuses every UPDATE, including while the row is shared.

``structure_determination_source``
    One calculation pinned to one role of a determination. The owner columns repeat the
    determination's owner so four composite foreign keys make "this determination's source is a
    calculation of the determination's own owner" a database fact (whichever owner column is set,
    its two keys to the determination and to the calculation are checked; a NULL column skips its keys). **Immutable:**
    ``trg_structure_determination_source_immutable`` refuses every UPDATE.

``structure_evidence_finding`` (public ref prefix ``sfnd``)
    An append-only event pinned to a geometry, a calculation or a determination.
    ``trg_structure_evidence_finding_append_only`` refuses every UPDATE and DELETE; a correction is a
    new finding that names the one it supersedes.

``calculation`` gains ``actual_protocol_declaration`` (JSONB, nullable), a versioned object whose
shape is owned by ``tckdb_schemas.structure_declarations`` (the database checks only that it is an
object carrying a numeric ``version``; the ``coalesce`` matters, because an object with no ``version``
key would otherwise make the predicate NULL and a CHECK passes on NULL), and two unique constraints
``(id, species_entry_id)`` and ``(id, transition_state_entry_id)``: ``id`` is already unique, the pairs
exist only so the source table's composite keys have something to point at.

What the database does not check
--------------------------------
That a basin's conformer observation belongs to the determination's species entry, that the evaluated
geometry is one of a source calculation's own geometries, and that a transition-state determination's
owner matches an upload's are cross-table facts a CHECK cannot state. The services that write the rows
enforce them and refuse with a code (``app.services.structure_determination_resolution``).

What this revision deliberately does not do
-------------------------------------------
* **No backfill.** Existing calculations keep NULL in the new column. A protocol, a determination and a
  finding are attributed claims, never inferred from a level of theory, a job type or a link; "not stated"
  is the honest reading of everything deposited before this revision.
* **No change to the accepted-science guards.** ``trg_as_root_calculation`` refuses any UPDATE of an
  accepted calculation whichever column it touches, so the new column is frozen with the rest of the row
  the moment it is accepted; ``ADD COLUMN`` of a nullable column with no default fires no UPDATE trigger,
  so the upgrade does not touch an approved row. Adding the two unique constraints rewrites no row.
  A determination and a source only *cite* a calculation; they are not its children, and adding one under
  an accepted calculation changes nothing about it.

Downgrade drops the triggers, the tables, the unique constraints, the check and the column, then the
enums. It forgets every determination, source and finding made after the upgrade and every actual-protocol
declaration, and prints how many it is forgetting first.

Revision ID: d3a8f6c1b952
Revises: e5b9c2a7d4f1
Create Date: 2026-10-05
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d3a8f6c1b952"
down_revision: Union[str, Sequence[str], None] = "e5b9c2a7d4f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _enum(name: str, *values: str) -> postgresql.ENUM:
    return postgresql.ENUM(*values, name=name, create_type=False)


_TARGET_KIND = _enum("structure_determination_target_kind", "geometry", "conformer_basin", "saddle_point")
_QUANTITY = _enum("structure_determination_quantity", "electronic_energy", "zero_kelvin_energy")
_ROLE = _enum(
    "structure_source_role",
    "energy",
    "geometry_optimization",
    "curvature",
    "correction",
    "connectivity",
    "alternative_characterization",
)
_FINDING_KIND = _enum(
    "structure_finding_kind",
    "identity_incompatibility",
    "state_incompatibility",
    "path_incompatibility",
    "role_invalidation",
    "contradictory_characterization",
    "adjudication",
)
_FINDING_SCOPE = _enum("structure_finding_scope", "geometry", "calculation", "determination")
_FINDING_VERDICT = _enum("structure_finding_verdict", "invalidates", "does_not_invalidate", "unresolved")
_FINDING_AUTHORITY = _enum("structure_finding_authority", "producer_assertion", "authorized_adjudication")
_ENUMS = (_TARGET_KIND, _QUANTITY, _ROLE, _FINDING_KIND, _FINDING_SCOPE, _FINDING_VERDICT, _FINDING_AUTHORITY)

_DETERMINATION = "structure_determination"
_SOURCE = "structure_determination_source"
_FINDING = "structure_evidence_finding"

_IMMUTABLE_FUNCTION = "tckdb_structure_row_immutable"
_APPEND_ONLY_FUNCTION = "tckdb_structure_finding_append_only"

_IMMUTABLE_SQL = f"""
CREATE OR REPLACE FUNCTION public.{_IMMUTABLE_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    RAISE EXCEPTION
        'structure_row_is_immutable: a row of % cannot be changed. A structure determination is identity: '
        'it is shared by every upload that states it, so a change would silently re-describe all of them.',
        TG_TABLE_NAME
        USING ERRCODE = '23514',
              HINT = 'Join or create a different determination instead of editing this one.';
END;
$$
"""

_APPEND_ONLY_SQL = f"""
CREATE OR REPLACE FUNCTION public.{_APPEND_ONLY_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    RAISE EXCEPTION
        'structure_evidence_finding_is_append_only: a finding cannot be %d. A correction is a new '
        'finding that names the one it supersedes.', lower(TG_OP)
        USING ERRCODE = '23514',
              HINT = 'Append a superseding finding instead.';
END;
$$
"""


def upgrade() -> None:
    bind = op.get_bind()
    for enum in _ENUMS:
        enum.create(bind, checkfirst=True)

    op.add_column(
        "calculation",
        sa.Column(
            "actual_protocol_declaration",
            postgresql.JSONB(none_as_null=True, astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        op.f("ck_calculation_actual_protocol_declaration_versioned_object"),
        "calculation",
        "actual_protocol_declaration IS NULL OR (jsonb_typeof(actual_protocol_declaration) = 'object' "
        "AND coalesce(jsonb_typeof(actual_protocol_declaration -> 'version'), '') = 'number')",
    )
    op.create_unique_constraint("uq_calculation_scope_species", "calculation", ["id", "species_entry_id"])
    op.create_unique_constraint("uq_calculation_scope_ts", "calculation", ["id", "transition_state_entry_id"])

    op.create_table(
        _DETERMINATION,
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("species_entry_id", sa.BigInteger(), nullable=True),
        sa.Column("transition_state_entry_id", sa.BigInteger(), nullable=True),
        sa.Column("conformer_observation_id", sa.BigInteger(), nullable=True),
        sa.Column("target_kind", _TARGET_KIND, nullable=False),
        sa.Column("quantity", _QUANTITY, nullable=True),
        sa.Column("evaluated_geometry_id", sa.BigInteger(), nullable=False),
        sa.Column("literature_id", sa.BigInteger(), nullable=True),
        sa.Column("workflow_tool_release_id", sa.BigInteger(), nullable=True),
        sa.Column("determination_key", sa.Text(), nullable=False),
        sa.Column("energy_convention", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
        sa.Column("actual_recipe", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
        sa.Column("identity_hash", sa.String(length=64), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("public_ref", sa.String(length=40), nullable=False),
        sa.CheckConstraint(
            "num_nonnulls(species_entry_id, transition_state_entry_id) = 1",
            name=op.f("ck_structure_determination_one_owner"),
        ),
        sa.CheckConstraint(
            "(target_kind = 'conformer_basin' AND species_entry_id IS NOT NULL "
            "AND conformer_observation_id IS NOT NULL) "
            "OR (target_kind = 'saddle_point' AND transition_state_entry_id IS NOT NULL "
            "AND conformer_observation_id IS NULL) "
            "OR (target_kind = 'geometry' AND conformer_observation_id IS NULL)",
            name=op.f("ck_structure_determination_target_matches_owner"),
        ),
        sa.CheckConstraint(
            "coalesce(quantity = 'zero_kelvin_energy', false) = (energy_convention IS NOT NULL)",
            name=op.f("ck_structure_determination_convention_iff_zero_kelvin"),
        ),
        sa.CheckConstraint(
            "literature_id IS NOT NULL OR workflow_tool_release_id IS NOT NULL",
            name=op.f("ck_structure_determination_source_required"),
        ),
        sa.CheckConstraint(
            "length(btrim(determination_key)) > 0 AND length(determination_key) <= 128",
            name=op.f("ck_structure_determination_key_bounded"),
        ),
        sa.CheckConstraint(
            "identity_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_structure_determination_identity_hash_sha256_hex")
        ),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_structure_determination_content_hash_sha256_hex")
        ),
        sa.CheckConstraint(
            "energy_convention IS NULL OR (jsonb_typeof(energy_convention) = 'object')",
            name=op.f("ck_structure_determination_energy_convention_object"),
        ),
        sa.CheckConstraint(
            "actual_recipe IS NULL OR (jsonb_typeof(actual_recipe) = 'object' "
            "AND coalesce(jsonb_typeof(actual_recipe -> 'version'), '') = 'number')",
            name=op.f("ck_structure_determination_actual_recipe_versioned_object"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["app_user.id"],
            name=op.f("fk_structure_determination_created_by_app_user"),
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["species_entry_id"],
            ["species_entry.id"],
            name=op.f("fk_structure_determination_species_entry_id_species_entry"),
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["transition_state_entry_id"],
            ["transition_state_entry.id"],
            name="fk_structure_determination_ts_entry",
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["conformer_observation_id"],
            ["conformer_observation.id"],
            name="fk_structure_determination_conformer_observation",
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["evaluated_geometry_id"],
            ["geometry.id"],
            name=op.f("fk_structure_determination_evaluated_geometry_id_geometry"),
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["literature_id"],
            ["literature.id"],
            name=op.f("fk_structure_determination_literature_id_literature"),
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["workflow_tool_release_id"],
            ["workflow_tool_release.id"],
            name="fk_structure_determination_workflow_tool_release",
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_structure_determination")),
        sa.UniqueConstraint("identity_hash", name=op.f("uq_structure_determination_identity_hash")),
        sa.UniqueConstraint("id", "species_entry_id", name="uq_structure_determination_scope_species"),
        sa.UniqueConstraint("id", "transition_state_entry_id", name="uq_structure_determination_scope_ts"),
    )
    op.create_index(op.f("ix_structure_determination_public_ref"), _DETERMINATION, ["public_ref"], unique=True)
    for column in ("species_entry_id", "transition_state_entry_id", "conformer_observation_id", "evaluated_geometry_id"):
        op.create_index(op.f(f"ix_structure_determination_{column}"), _DETERMINATION, [column], unique=False)

    op.create_table(
        _SOURCE,
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("determination_id", sa.BigInteger(), nullable=False),
        sa.Column("role", _ROLE, nullable=False),
        sa.Column("calculation_id", sa.BigInteger(), nullable=False),
        sa.Column("species_entry_id", sa.BigInteger(), nullable=True),
        sa.Column("transition_state_entry_id", sa.BigInteger(), nullable=True),
        sa.Column("geometry_id", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(
            "num_nonnulls(species_entry_id, transition_state_entry_id) = 1",
            name=op.f("ck_structure_determination_source_one_owner"),
        ),
        sa.ForeignKeyConstraint(
            ["determination_id"],
            ["structure_determination.id"],
            name="fk_structure_determination_source_determination",
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["calculation_id"],
            ["calculation.id"],
            name="fk_structure_determination_source_calculation",
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["species_entry_id"],
            ["species_entry.id"],
            name="fk_structure_determination_source_species_entry",
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["transition_state_entry_id"],
            ["transition_state_entry.id"],
            name="fk_structure_determination_source_ts_entry",
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["determination_id", "species_entry_id"],
            ["structure_determination.id", "structure_determination.species_entry_id"],
            name="fk_structure_determination_source_scope_det_species",
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["determination_id", "transition_state_entry_id"],
            ["structure_determination.id", "structure_determination.transition_state_entry_id"],
            name="fk_structure_determination_source_scope_det_ts",
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["calculation_id", "species_entry_id"],
            ["calculation.id", "calculation.species_entry_id"],
            name="fk_structure_determination_source_scope_calc_species",
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["calculation_id", "transition_state_entry_id"],
            ["calculation.id", "calculation.transition_state_entry_id"],
            name="fk_structure_determination_source_scope_calc_ts",
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["geometry_id"],
            ["geometry.id"],
            name=op.f("fk_structure_determination_source_geometry_id_geometry"),
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_structure_determination_source")),
        sa.UniqueConstraint(
            "determination_id", "role", "calculation_id", name="uq_structure_determination_source_role"
        ),
    )
    op.create_index(
        op.f("ix_structure_determination_source_determination_id"), _SOURCE, ["determination_id"], unique=False
    )
    op.create_index(
        op.f("ix_structure_determination_source_calculation_id"), _SOURCE, ["calculation_id"], unique=False
    )

    op.create_table(
        _FINDING,
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("kind", _FINDING_KIND, nullable=False),
        sa.Column("scope", _FINDING_SCOPE, nullable=False),
        sa.Column("subject_geometry_id", sa.BigInteger(), nullable=True),
        sa.Column("subject_calculation_id", sa.BigInteger(), nullable=True),
        sa.Column("subject_determination_id", sa.BigInteger(), nullable=True),
        sa.Column("role", _ROLE, nullable=True),
        sa.Column("verdict", _FINDING_VERDICT, nullable=False),
        sa.Column("authority", _FINDING_AUTHORITY, nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("semantic_version", sa.SmallInteger(), nullable=False),
        sa.Column("source_calculation_id", sa.BigInteger(), nullable=True),
        sa.Column("literature_id", sa.BigInteger(), nullable=True),
        sa.Column("supersedes_finding_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("public_ref", sa.String(length=40), nullable=False),
        sa.CheckConstraint(
            "(scope = 'geometry' AND subject_geometry_id IS NOT NULL "
            "AND subject_calculation_id IS NULL AND subject_determination_id IS NULL) "
            "OR (scope = 'calculation' AND subject_calculation_id IS NOT NULL "
            "AND subject_geometry_id IS NULL AND subject_determination_id IS NULL) "
            "OR (scope = 'determination' AND subject_determination_id IS NOT NULL "
            "AND subject_geometry_id IS NULL AND subject_calculation_id IS NULL)",
            name=op.f("ck_structure_evidence_finding_subject_matches_scope"),
        ),
        sa.CheckConstraint(
            "(kind = 'role_invalidation') = (role IS NOT NULL)",
            name=op.f("ck_structure_evidence_finding_role_iff_role_invalidation"),
        ),
        sa.CheckConstraint(
            "kind <> 'adjudication' OR (supersedes_finding_id IS NOT NULL "
            "AND authority = 'authorized_adjudication')",
            name=op.f("ck_structure_evidence_finding_adjudication_needs_authority"),
        ),
        sa.CheckConstraint(
            "length(btrim(rationale)) > 0 AND length(rationale) <= 2000",
            name=op.f("ck_structure_evidence_finding_rationale_bounded"),
        ),
        sa.CheckConstraint(
            "semantic_version >= 1", name=op.f("ck_structure_evidence_finding_semantic_version_positive")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["app_user.id"],
            name=op.f("fk_structure_evidence_finding_created_by_app_user"),
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["subject_geometry_id"],
            ["geometry.id"],
            name="fk_structure_evidence_finding_subject_geometry",
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["subject_calculation_id"],
            ["calculation.id"],
            name="fk_structure_evidence_finding_subject_calculation",
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["subject_determination_id"],
            ["structure_determination.id"],
            name="fk_structure_evidence_finding_subject_determination",
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["source_calculation_id"],
            ["calculation.id"],
            name="fk_structure_evidence_finding_source_calculation",
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["literature_id"],
            ["literature.id"],
            name=op.f("fk_structure_evidence_finding_literature_id_literature"),
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_finding_id"],
            ["structure_evidence_finding.id"],
            name="fk_structure_evidence_finding_supersedes",
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_structure_evidence_finding")),
    )
    op.create_index(op.f("ix_structure_evidence_finding_public_ref"), _FINDING, ["public_ref"], unique=True)
    for column in (
        "subject_geometry_id",
        "subject_calculation_id",
        "subject_determination_id",
        "supersedes_finding_id",
    ):
        op.create_index(op.f(f"ix_structure_evidence_finding_{column}"), _FINDING, [column], unique=False)

    op.execute(_IMMUTABLE_SQL)
    op.execute(_APPEND_ONLY_SQL)
    for table in (_DETERMINATION, _SOURCE):
        op.execute(
            f"CREATE TRIGGER trg_{table}_immutable BEFORE UPDATE ON public.{table} "
            f"FOR EACH ROW EXECUTE FUNCTION public.{_IMMUTABLE_FUNCTION}()"
        )
    op.execute(
        f"CREATE TRIGGER trg_{_FINDING}_append_only BEFORE UPDATE OR DELETE ON public.{_FINDING} "
        f"FOR EACH ROW EXECUTE FUNCTION public.{_APPEND_ONLY_FUNCTION}()"
    )


def downgrade() -> None:
    bind = op.get_bind()
    counts = {
        table: bind.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()
        for table in (_DETERMINATION, _SOURCE, _FINDING)
    }
    declared = bind.execute(
        sa.text("SELECT count(*) FROM calculation WHERE actual_protocol_declaration IS NOT NULL")
    ).scalar_one()
    print(
        f"d3a8f6c1b952 downgrade forgets: {counts[_DETERMINATION]} structure determination(s), "
        f"{counts[_SOURCE]} source row(s), {counts[_FINDING]} finding(s) and the actual-protocol declaration "
        f"on {declared} calculation(s)."
    )

    op.execute(f"DROP TRIGGER IF EXISTS trg_{_FINDING}_append_only ON public.{_FINDING}")
    for table in (_SOURCE, _DETERMINATION):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_immutable ON public.{table}")
    op.execute(f"DROP FUNCTION IF EXISTS public.{_APPEND_ONLY_FUNCTION}()")
    op.execute(f"DROP FUNCTION IF EXISTS public.{_IMMUTABLE_FUNCTION}()")

    op.drop_table(_FINDING)
    op.drop_table(_SOURCE)
    op.drop_table(_DETERMINATION)

    op.drop_constraint("uq_calculation_scope_ts", "calculation", type_="unique")
    op.drop_constraint("uq_calculation_scope_species", "calculation", type_="unique")
    op.drop_constraint(op.f("ck_calculation_actual_protocol_declaration_versioned_object"), "calculation", type_="check")
    op.drop_column("calculation", "actual_protocol_declaration")

    for enum in reversed(_ENUMS):
        enum.drop(bind, checkfirst=True)
