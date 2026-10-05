"""Let a calculation declare its actual protocol and a record declare structure determinations.

Additive, with no data step. Three new tables, seven new enums, one nullable column and three
composite-key targets on ``calculation``:

``structure_determination`` (public ref prefix ``sdet``)
    One source-attributed claim about a defined geometry, conformer basin or saddle, and the
    quantity it supplies. Its identifier is the owner (a species entry, or a transition state
    entry; for a basin also its conformer observation), the source attribution (literature,
    workflow-tool release) and a source-scoped key, unique (``uq_structure_determination_key``,
    NULLS NOT DISTINCT; ``identity_hash`` is its digest). Stating the key again with the same
    content resolves to the existing row; with different content it is refused by the write path.
    ``content_hash`` digests everything else (target kind, quantity, energy convention, recipe,
    evaluated geometry, pinned calculations). **Immutable from creation:**
    ``trg_structure_determination_immutable`` refuses every UPDATE, including while the row is
    shared. A BEFORE INSERT trigger refuses a basin whose observation is not of its own species entry.

``structure_determination_source``
    One calculation pinned to one role of a determination. The owner columns (and, for a basin, the
    observation) repeat the determination's, so composite foreign keys make "this determination's source
    is a calculation of the determination's own owner, and for a basin of its observation" a database
    fact (whichever owner column is set, its keys to the determination and to the calculation are
    checked; a NULL column skips its keys). **Pinned at creation:** a source may be inserted only in the
    transaction that creates its determination (the write path sets the transaction-local
    ``tckdb.structure_determination_writing`` to the determination id, and an archive restore, which re-creates
    determinations and their sources together in one transaction, to the ids it is restoring; a tripwire against
    accidental or scripted edits, not access control) and may never be deleted, because the determination's content
    digest and key cover its pinned calculations. **Immutable:** no UPDATE.

``structure_evidence_finding`` (public ref prefix ``sfnd``)
    An append-only event pinned to a geometry, a calculation or a determination.
    ``trg_structure_evidence_finding_append_only`` refuses every UPDATE and DELETE. Only an authorized
    adjudication may name an earlier finding it supersedes (``only_adjudication_supersedes`` and
    ``adjudication_needs_authority``); any other finding stands beside the ones it disagrees with, because a
    finding that could erase an earlier one by naming it would let a producer's assertion remove a disproof.

Accepted-science protection. A determination and its sources belong to the transition state entry or the
conformer observation they are about, both accepted-science roots (``c6f2a9d4e7b1``). The shared guards
``tckdb_guard_accepted_child`` (determination) and ``tckdb_guard_accepted_via_child`` (source, through its
determination) are installed on both roots, INSERT, UPDATE and DELETE, and TRUNCATE is refused on all three
tables, as for ``network_kinetics_determination`` (``e5b9c2a7d4f1``). The owner columns are nullable by
design (a determination has one of two owners, and the guard function skips a NULL one), so they are
registered in this revision's own tuples (``_DETERMINATION_GUARDS``, ``_SOURCE_GUARDS``), which
``tests/db/test_structure_determination_migration.py`` checks against the model and ``pg_trigger``; the
shared registry's NOT NULL rule is not applicable to them. A source cites its calculation without being its
child, so an accepted calculation can still be cited, and a finding can still be appended about accepted
science.

``calculation`` gains ``actual_protocol_declaration`` (JSONB, nullable), a versioned object whose
shape is owned by ``tckdb_schemas.structure_declarations`` (the database checks only that it is an
object carrying a numeric ``version``; the ``coalesce`` matters, because an object with no ``version``
key would otherwise make the predicate NULL and a CHECK passes on NULL), and three unique constraints
``(id, species_entry_id)``, ``(id, transition_state_entry_id)`` and ``(id, conformer_observation_id)``:
``id`` is already unique, the pairs exist only so the source table's composite keys have something to
point at.

What the database does not check
--------------------------------
That the evaluated geometry is one of a source calculation's own geometries (a geometry is shared,
content-addressed content, so the database cannot check its owner), that the calculation it is read from
is one of the pinned sources, and that a transition-state determination's owner matches an upload's are
cross-table facts a CHECK cannot state. The services that write the rows enforce them and refuse with a
code (``app.services.structure_determination_resolution``).

What this revision deliberately does not do
-------------------------------------------
* **No backfill.** Existing calculations keep NULL in the new column. A protocol, a determination and a
  finding are attributed claims, never inferred from a level of theory, a job type or a link; "not stated"
  is the honest reading of everything deposited before this revision.
* **No change to the existing accepted-science guards.** ``trg_as_root_calculation`` refuses any UPDATE of an
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
        'structure_evidence_finding_is_append_only: a finding cannot be %d. Only an authorized adjudication '
        'settles an earlier finding.', lower(TG_OP)
        USING ERRCODE = '23514',
              HINT = 'Append a finding (or, with authority, an adjudication) instead.';
END;
$$
"""


_DETERMINATION_GUARD_FUNCTION = "tckdb_structure_determination_guard"
_SOURCE_INSERT_GUARD_FUNCTION = "tckdb_structure_source_insert_guard"
_SOURCE_DELETE_GUARD_FUNCTION = "tckdb_structure_source_delete_guard"

#: ``(trigger name, record type, column)``: the accepted-science roots a determination belongs to.
_DETERMINATION_GUARDS = (
    ("trg_structure_determination_accepted_ts_entry", "transition_state_entry", "transition_state_entry_id"),
    ("trg_structure_determination_accepted_observation", "conformer_observation", "conformer_observation_id"),
)
#: The same roots, reached from a source row through its determination.
_SOURCE_GUARDS = (
    ("trg_structure_determination_source_accepted_ts_entry", "transition_state_entry", "transition_state_entry_id"),
    ("trg_structure_determination_source_accepted_observation", "conformer_observation", "conformer_observation_id"),
)

_DETERMINATION_GUARD_SQL = f"""
CREATE OR REPLACE FUNCTION public.{_DETERMINATION_GUARD_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    -- A basin whose owner is not a species entry is refused by the target check, with its own message.
    IF NEW.conformer_observation_id IS NOT NULL AND NEW.species_entry_id IS NOT NULL AND NOT EXISTS (
        SELECT 1
        FROM public.conformer_observation o
        JOIN public.conformer_group g ON g.id = o.conformer_group_id
        WHERE o.id = NEW.conformer_observation_id AND g.species_entry_id = NEW.species_entry_id
    ) THEN
        RAISE EXCEPTION
            'structure_determination_observation_owner: a basin claim names an observation of its own species entry'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$
"""

_SOURCE_INSERT_GUARD_SQL = f"""
CREATE OR REPLACE FUNCTION public.{_SOURCE_INSERT_GUARD_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    -- A determination is its pinned calculations: its content digest and its key cover them, so a source added or
    -- removed afterwards would change what it says under an unchanged identity. The write path pins every source in
    -- the transaction that creates the determination and says so here (an archive restore does the same for the
    -- determinations it re-creates, as a comma-separated list); nothing else may add one. This is a tripwire against
    -- accidental or scripted edits, not access control: whoever sets the variable is acting on purpose.
    IF NOT (
        NEW.determination_id::text = ANY (
            string_to_array(coalesce(current_setting('tckdb.structure_determination_writing', true), ''), ',')
        )
    ) THEN
        RAISE EXCEPTION
            'structure_determination_sources_are_pinned_at_creation: determination % takes no further source',
            NEW.determination_id
            USING ERRCODE = '23514',
                  HINT = 'State a different determination key for a different set of calculations.';
    END IF;
    IF NEW.conformer_observation_id IS DISTINCT FROM (
        SELECT d.conformer_observation_id FROM public.structure_determination d WHERE d.id = NEW.determination_id
    ) THEN
        RAISE EXCEPTION
            'structure_determination_source_observation: a source names the observation of its determination'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$
"""

_SOURCE_DELETE_GUARD_SQL = f"""
CREATE OR REPLACE FUNCTION public.{_SOURCE_DELETE_GUARD_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    RAISE EXCEPTION
        'structure_determination_sources_are_pinned_at_creation: a source of determination % cannot be removed',
        OLD.determination_id
        USING ERRCODE = '23514';
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
    op.create_unique_constraint("uq_calculation_scope_observation", "calculation", ["id", "conformer_observation_id"])

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
        sa.UniqueConstraint("id", "conformer_observation_id", name="uq_structure_determination_scope_observation"),
        sa.UniqueConstraint(
            "species_entry_id",
            "transition_state_entry_id",
            "conformer_observation_id",
            "literature_id",
            "workflow_tool_release_id",
            "determination_key",
            name="uq_structure_determination_key",
            postgresql_nulls_not_distinct=True,
        ),
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
        sa.Column("conformer_observation_id", sa.BigInteger(), nullable=True),
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
            ["conformer_observation_id"],
            ["conformer_observation.id"],
            name="fk_structure_determination_source_observation",
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["determination_id", "conformer_observation_id"],
            ["structure_determination.id", "structure_determination.conformer_observation_id"],
            name="fk_structure_determination_source_scope_det_observation",
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["calculation_id", "conformer_observation_id"],
            ["calculation.id", "calculation.conformer_observation_id"],
            name="fk_structure_determination_source_scope_calc_observation",
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
            "supersedes_finding_id IS NULL OR kind = 'adjudication'",
            name=op.f("ck_structure_evidence_finding_only_adjudication_supersedes"),
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
    op.execute(_DETERMINATION_GUARD_SQL)
    op.execute(_SOURCE_INSERT_GUARD_SQL)
    op.execute(_SOURCE_DELETE_GUARD_SQL)
    for table in (_DETERMINATION, _SOURCE):
        op.execute(
            f"CREATE TRIGGER trg_{table}_immutable BEFORE UPDATE ON public.{table} "
            f"FOR EACH ROW EXECUTE FUNCTION public.{_IMMUTABLE_FUNCTION}()"
        )
    op.execute(
        f"CREATE TRIGGER trg_{_FINDING}_append_only BEFORE UPDATE OR DELETE ON public.{_FINDING} "
        f"FOR EACH ROW EXECUTE FUNCTION public.{_APPEND_ONLY_FUNCTION}()"
    )
    op.execute(
        f"CREATE TRIGGER trg_{_DETERMINATION}_guard BEFORE INSERT ON public.{_DETERMINATION} "
        f"FOR EACH ROW EXECUTE FUNCTION public.{_DETERMINATION_GUARD_FUNCTION}()"
    )
    op.execute(
        f"CREATE TRIGGER trg_{_SOURCE}_insert_guard BEFORE INSERT ON public.{_SOURCE} "
        f"FOR EACH ROW EXECUTE FUNCTION public.{_SOURCE_INSERT_GUARD_FUNCTION}()"
    )
    op.execute(
        f"CREATE TRIGGER trg_{_SOURCE}_delete_guard BEFORE DELETE ON public.{_SOURCE} "
        f"FOR EACH ROW EXECUTE FUNCTION public.{_SOURCE_DELETE_GUARD_FUNCTION}()"
    )
    # Accepted-science protection. A determination and its sources belong to the transition state entry or the
    # conformer observation they are about, both accepted-science roots: once one is accepted, nothing may be added to,
    # changed on or removed from what it says. The guards are the shared ones (``c6f2a9d4e7b1``); they skip a NULL
    # column, so one function serves a table whose owner is one of two columns.
    for name, record_type, column in _DETERMINATION_GUARDS:
        op.execute(
            f"CREATE TRIGGER {name} BEFORE INSERT OR UPDATE OR DELETE ON public.{_DETERMINATION} "
            f"FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_child('{record_type}', '{column}')"
        )
    for name, record_type, column in _SOURCE_GUARDS:
        op.execute(
            f"CREATE TRIGGER {name} BEFORE INSERT OR UPDATE OR DELETE ON public.{_SOURCE} "
            f"FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_accepted_via_child("
            f"'{record_type}', 'determination_id', '{_DETERMINATION}', 'id', '{column}')"
        )
    for table in (_DETERMINATION, _SOURCE, _FINDING):
        op.execute(
            f"CREATE TRIGGER trg_{table}_truncate BEFORE TRUNCATE ON public.{table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION public.tckdb_reject_truncate()"
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

    for table in (_FINDING, _SOURCE, _DETERMINATION):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_truncate ON public.{table}")
    for name, _, _ in _SOURCE_GUARDS:
        op.execute(f"DROP TRIGGER IF EXISTS {name} ON public.{_SOURCE}")
    for name, _, _ in _DETERMINATION_GUARDS:
        op.execute(f"DROP TRIGGER IF EXISTS {name} ON public.{_DETERMINATION}")
    op.execute(f"DROP TRIGGER IF EXISTS trg_{_SOURCE}_delete_guard ON public.{_SOURCE}")
    op.execute(f"DROP TRIGGER IF EXISTS trg_{_SOURCE}_insert_guard ON public.{_SOURCE}")
    op.execute(f"DROP TRIGGER IF EXISTS trg_{_DETERMINATION}_guard ON public.{_DETERMINATION}")
    op.execute(f"DROP TRIGGER IF EXISTS trg_{_FINDING}_append_only ON public.{_FINDING}")
    for table in (_SOURCE, _DETERMINATION):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_immutable ON public.{table}")
    op.execute(f"DROP FUNCTION IF EXISTS public.{_SOURCE_DELETE_GUARD_FUNCTION}()")
    op.execute(f"DROP FUNCTION IF EXISTS public.{_SOURCE_INSERT_GUARD_FUNCTION}()")
    op.execute(f"DROP FUNCTION IF EXISTS public.{_DETERMINATION_GUARD_FUNCTION}()")
    op.execute(f"DROP FUNCTION IF EXISTS public.{_APPEND_ONLY_FUNCTION}()")
    op.execute(f"DROP FUNCTION IF EXISTS public.{_IMMUTABLE_FUNCTION}()")

    op.drop_table(_FINDING)
    op.drop_table(_SOURCE)
    op.drop_table(_DETERMINATION)

    op.drop_constraint("uq_calculation_scope_observation", "calculation", type_="unique")
    op.drop_constraint("uq_calculation_scope_ts", "calculation", type_="unique")
    op.drop_constraint("uq_calculation_scope_species", "calculation", type_="unique")
    op.drop_constraint(op.f("ck_calculation_actual_protocol_declaration_versioned_object"), "calculation", type_="check")
    op.drop_column("calculation", "actual_protocol_declaration")

    for enum in reversed(_ENUMS):
        enum.drop(bind, checkfirst=True)
