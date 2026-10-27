"""The species key is the constitution; stereochemistry is resolved on the species entry.

DR-0038. Species identity was ``(canonical SMILES with stereo marks, charge, multiplicity)``, and the entry's stereo came
from a free-text label (which won unconditionally) or from geometry; the SMILES marks never reached the entry. So
``C/C=C/C``, ``CC=CC`` and ``C/C=C\\C`` were three species, ``C/C=C/C`` with a cis geometry was accepted as ``Z``, and a
fully unknown configuration was NULL. Now:

* a species is the stereo-blind canonical SMILES (isotopes stripped) plus charge and multiplicity, its InChIKey taken from
  the same molecule;
* an entry's stereo key is ``stereo_descriptor``: one ``R``/``S``/``r``/``s``/``E``/``Z``/``-``/``?`` token per potential
  stereo element, the lexicographically smallest string over every automorphism of the constitution (NULL only for an
  achiral species). It replaces ``stereo_label`` in ``uq_species_entry_species_id``; ``stereo_label`` becomes derived
  display text, and ``stereo_resolution`` (``resolved``/``partial``/``unresolved``) and ``stereo_smiles`` (canonical
  isomeric SMILES, where one can be written) are derived beside it.

Schema
------
``species_entry`` gains ``stereo_descriptor`` (text), ``stereo_resolution`` (new enum ``stereo_resolution``) and
``stereo_smiles`` (text, indexed). ``stereo_label`` widens from ``varchar(64)`` to ``text`` (a descriptor has one token per
element). Two CHECK constraints pin the descriptor vocabulary and ``descriptor IS NULL <=> resolution IS NULL``.

``species``, ``species_entry`` and ``chem_reaction`` each gain a nullable ``merged_into_<table>_id`` self-reference.
A merged-away row is **kept** with its public ref and the curation recorded about it, and forwards one hop to the row that
holds the results. The three unique keys (``uq_species_identity``, ``uq_species_entry_species_id``,
``uq_chem_reaction_stoichiometry_hash``) become partial unique indexes over live rows (``... WHERE merged_into_... IS NULL``)
so the kept row can share its survivor's key. Two triggers per table keep the markers honest (no chain, no loop) and a third
refuses a new row, or a re-pointed one, that names a merged-away row. A column rather than a side table, unlike
``level_of_theory_merge``, because a uniqueness predicate can only look at the row itself; the columns are registered as
derived in ``snapshot_defaults`` so no stored review restales.

The data step
-------------
``app.services.stereo_identity_merge.merge_stereo_identity`` recomputes every species key and marks the species that now
collide as merged into a survivor (the row already holding the key's content ref, else the key's SMILES, else the oldest);
rewrites a live reaction's participants to the survivor species (same-side collisions add their stoichiometry), recomputes
``chem_reaction.stoichiometry_hash`` and merges reactions the same way; derives every entry's descriptor from its own SMILES
marks, **every** linked geometry and a usable old label (a label already in the descriptor vocabulary, or ``cis``/``trans``
where the words mean one thing, computed by CIP); marks entries whose live key now collides as merged into the oldest and
moves every foreign key to them (read from the database catalogue, not a list) to the survivor; recomputes the
``structure_determination.identity_hash`` and ``network_state.composition_hash`` values of the rows that moved; resets the
survivor's review; and re-runs the conformer reassignment on the survivors. Nothing is deleted.

Accepted (approved) rows change only through the declared-repair path of ``e2c9a4f7b163``: one declaration per guarded table,
naming exactly the columns written; the trigger is not disabled or weakened. A row the path cannot express (a primary-key
column under an approved record) or that would duplicate one the survivor already holds is left on the kept row and listed.
The curation recorded about a kept row (reviews, machine reviews, release selections, supersession edges, assessments,
idempotency bodies, per-user entry reviews) is never moved: it describes that row.

The migration stops, naming the rows by public ref, when the data needs a decision it may not make: entries that would merge
although their old labels differ (or one is labelled and one is not) and nothing in the data separates them; an entry whose
marks, geometries and label disagree that would share a key; entries of different stationary-point kinds that would merge;
entries whose merge would leave one subject with two standing release selections of one record type in one release.
Allene and cumulene axes are stated as ``?`` (never refused), so differently labelled allene entries stop it too; an entry
whose symmetry is past the automorphism cap is described with ``?`` at its symmetric positions and listed.

Downgrade
---------
Restores the previous schema (the old unique keys, the old column width) and drops the new columns, triggers and enum. It
**does not un-merge**: which rows were once separate cannot be rebuilt, and the forwarding is forgotten with the markers.
A merged-away row that would collide with another under the old unique keys is renamed so the old keys can be restored
(``species.smiles`` and ``species_entry.stereo_label`` become ``merged:<public_ref>``, a colliding
``chem_reaction.stoichiometry_hash`` becomes NULL); the rows, their refs and their history stay. After the downgrade a
merged row is an ordinary empty-looking row; the results stay on the survivor. Because the markers are forgotten,
the upgrade refuses, loudly, a database that still carries the ``merged:`` sentinels the downgrade leaves.

Revision ID: c7e2a9f4d138
Revises: f3a8c5d1e947
Create Date: 2026-10-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from alembic import op

revision: str = "c7e2a9f4d138"
down_revision: Union[str, Sequence[str], None] = "f3a8c5d1e947"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_RESOLUTION = postgresql.ENUM("resolved", "partial", "unresolved", name="stereo_resolution", create_type=False)
_REST = ["electronic_state_kind", "electronic_state_label", "term_symbol", "isotope_key"]

#: ``(table, marker column)`` of the three identity tables that can be merged.
_MERGEABLE = (
    ("species", "merged_into_species_id"),
    ("species_entry", "merged_into_species_entry_id"),
    ("chem_reaction", "merged_into_chem_reaction_id"),
)


def _add_marker(table: str, column: str) -> None:
    op.add_column(table, sa.Column(column, sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        f"fk_{table}_{column}_{table}",
        table,
        table,
        [column],
        ["id"],
        deferrable=True,
        initially="IMMEDIATE",
    )


def _install_guards(bind) -> None:
    op.execute(
        """
        CREATE FUNCTION public.tckdb_guard_merge_marker() RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
            marker text := TG_ARGV[0];
            target_id bigint := (to_jsonb(NEW) ->> marker)::bigint;
            chained boolean;
        BEGIN
            IF target_id IS NULL THEN
                RETURN NEW;
            END IF;
            -- A merge is one hop: the target is live, and nothing still forwards to the row being merged.
            EXECUTE format('SELECT %I IS NOT NULL FROM public.%I WHERE id = $1', marker, TG_TABLE_NAME)
               INTO chained USING target_id;
            IF coalesce(chained, false) THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = format('a %I row cannot be merged into one that is itself merged', TG_TABLE_NAME),
                    HINT = 'merge into the row the target was merged into; a merge is one hop';
            END IF;
            EXECUTE format('SELECT EXISTS (SELECT 1 FROM public.%I WHERE %I = $1)', TG_TABLE_NAME, marker)
               INTO chained USING NEW.id;
            IF chained THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = format('a %I row that other rows were merged into cannot itself be merged', TG_TABLE_NAME),
                    HINT = 're-point the rows that forward to it first; a merge is one hop';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.tckdb_refuse_merged_target() RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
            target_table text := TG_ARGV[0];
            fk_column text := TG_ARGV[1];
            marker text := TG_ARGV[2];
            target_id bigint := (to_jsonb(NEW) ->> fk_column)::bigint;
            merged bigint;
        BEGIN
            IF target_id IS NULL THEN
                RETURN NEW;
            END IF;
            IF TG_OP = 'UPDATE' AND (to_jsonb(OLD) ->> fk_column) IS NOT DISTINCT FROM (to_jsonb(NEW) ->> fk_column) THEN
                RETURN NEW;
            END IF;
            EXECUTE format('SELECT %I FROM public.%I WHERE id = $1', marker, target_table) INTO merged USING target_id;
            IF merged IS NOT NULL THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = format('%I.%I names a merged-away %I row', TG_TABLE_NAME, fk_column, target_table),
                    HINT = 'use the row it was merged into; a merged row forwards to it';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.tckdb_freeze_merged_row() RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
            marker text := TG_ARGV[0];
        BEGIN
            -- A merged-away row is a record of what it was: it may not be edited, except by a repair someone declared
            -- in this transaction (the accepted-science path), and an update that changes nothing is let through.
            IF (to_jsonb(OLD) ->> marker) IS NULL OR to_jsonb(NEW) = to_jsonb(OLD) THEN
                RETURN NEW;
            END IF;
            IF EXISTS (
                SELECT 1 FROM public.accepted_science_repair
                 WHERE target_table = TG_TABLE_NAME AND xact_id = (pg_current_xact_id())::text::bigint
            ) THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION USING ERRCODE = '23514',
                MESSAGE = format('a merged-away %I row cannot be edited', TG_TABLE_NAME),
                HINT = 'it is kept as it was; edit the row it was merged into, or declare a repair';
        END;
        $$
        """
    )
    for table, marker in _MERGEABLE:
        op.execute(
            f"CREATE TRIGGER trg_{table}_merge_marker BEFORE INSERT OR UPDATE OF {marker} ON public.{table} "
            f"FOR EACH ROW EXECUTE FUNCTION public.tckdb_guard_merge_marker('{marker}')"
        )
        op.execute(
            f"CREATE TRIGGER trg_{table}_freeze_merged BEFORE UPDATE ON public.{table} "
            f"FOR EACH ROW EXECUTE FUNCTION public.tckdb_freeze_merged_row('{marker}')"
        )
    for table, column, target, marker in _foreign_keys_into_mergeable(bind):
        name = f"trg_mt_{table}_{column}"[:63]
        op.execute(
            f"CREATE TRIGGER {name} BEFORE INSERT OR UPDATE OF {column} ON public.{table} "
            f"FOR EACH ROW EXECUTE FUNCTION public.tckdb_refuse_merged_target('{target}', '{column}', '{marker}')"
        )


def _foreign_keys_into_mergeable(bind):
    markers = dict(_MERGEABLE)
    rows = bind.execute(
        sa.text(
            "SELECT c.conrelid::regclass::text AS tbl, a.attname AS col, c.confrelid::regclass::text AS target "
            "FROM pg_constraint c "
            "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1] "
            "WHERE c.contype = 'f' AND array_length(c.conkey, 1) = 1 "
            "AND c.confrelid = ANY(ARRAY['species'::regclass, 'species_entry'::regclass, 'chem_reaction'::regclass]) "
            "ORDER BY 1, 2"
        )
    ).all()
    return [(r.tbl, r.col, r.target, markers[r.target]) for r in rows if r.col not in markers.values()]


def upgrade() -> None:
    bind = op.get_bind()
    # A database that went down through this revision's downgrade carries ``merged:<ref>`` sentinels where merged-away rows
    # were renamed to restore the old keys, and has forgotten which rows were merged. Applying the step again would treat
    # those rows as live and unparseable, so it refuses, loudly, rather than guess.
    sentinels = bind.execute(
        sa.text(
            "SELECT (SELECT count(*) FROM species WHERE smiles LIKE 'merged:%') "
            "     + (SELECT count(*) FROM species_entry WHERE stereo_label LIKE 'merged:%')"
        )
    ).scalar_one()
    if sentinels:
        raise RuntimeError(
            f"{sentinels} row(s) carry a 'merged:' sentinel left by the downgrade of revision {revision}; this database "
            "has been through that downgrade and cannot be upgraded through it again. Restore it from a backup taken "
            "before the first upgrade."
        )
    postgresql.ENUM("resolved", "partial", "unresolved", name="stereo_resolution").create(bind, checkfirst=True)
    op.add_column("species_entry", sa.Column("stereo_descriptor", sa.Text(), nullable=True))
    op.add_column("species_entry", sa.Column("stereo_resolution", _RESOLUTION, nullable=True))
    op.add_column("species_entry", sa.Column("stereo_smiles", sa.Text(), nullable=True))
    op.alter_column("species_entry", "stereo_label", type_=sa.Text(), existing_nullable=True)
    for table, marker in _MERGEABLE:
        _add_marker(table, marker)

    # The old keys cannot stand while rows are re-keyed and merged; the live-row keys replace them below.
    op.drop_constraint("uq_species_identity", "species", type_="unique")
    op.drop_constraint("uq_species_entry_species_id", "species_entry", type_="unique")
    op.drop_constraint("uq_chem_reaction_stoichiometry_hash", "chem_reaction", type_="unique")

    # Imported here, not at module level: importing the app from a migration module at load time would run for every
    # alembic command, including ones that never reach this revision.
    from app.services.stereo_identity_merge import merge_stereo_identity

    session = Session(bind=bind)
    report = merge_stereo_identity(session, revision=revision)
    session.flush()
    print(f"[{revision}] species key / entry stereo: {report.summary()}")

    op.create_index(
        "uq_species_identity",
        "species",
        ["smiles", "charge", "multiplicity"],
        unique=True,
        postgresql_where=sa.text("merged_into_species_id IS NULL"),
    )
    op.create_index(
        "uq_species_entry_species_id",
        "species_entry",
        ["species_id", "stereo_descriptor", *_REST],
        unique=True,
        postgresql_nulls_not_distinct=True,
        postgresql_where=sa.text("merged_into_species_entry_id IS NULL"),
    )
    op.create_index(
        "uq_chem_reaction_stoichiometry_hash",
        "chem_reaction",
        ["stoichiometry_hash"],
        unique=True,
        postgresql_where=sa.text("merged_into_chem_reaction_id IS NULL"),
    )
    for table, marker in _MERGEABLE:
        op.create_index(f"ix_{table}_{marker}", table, [marker])
        op.create_check_constraint("not_merged_into_itself", table, f"{marker} IS NULL OR {marker} <> id")
    op.create_check_constraint(
        "stereo_descriptor_vocabulary",
        "species_entry",
        "stereo_descriptor IS NULL OR stereo_descriptor ~ '^[RSrsEZez?-](,[RSrsEZez?-])*$'",
    )
    op.create_check_constraint(
        "stereo_resolution_iff_descriptor",
        "species_entry",
        "(stereo_descriptor IS NULL) = (stereo_resolution IS NULL)",
    )
    op.create_index("ix_species_entry_stereo_smiles", "species_entry", ["stereo_smiles"])
    _install_guards(bind)


def downgrade() -> None:
    bind = op.get_bind()
    for table, column, _target, _marker in _foreign_keys_into_mergeable(bind):
        op.execute(f"DROP TRIGGER IF EXISTS {f'trg_mt_{table}_{column}'[:63]} ON public.{table}")
    for table, _marker in _MERGEABLE:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_merge_marker ON public.{table}")
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_freeze_merged ON public.{table}")
    op.execute("DROP FUNCTION IF EXISTS public.tckdb_freeze_merged_row()")
    op.execute("DROP FUNCTION IF EXISTS public.tckdb_refuse_merged_target()")
    op.execute("DROP FUNCTION IF EXISTS public.tckdb_guard_merge_marker()")

    op.drop_index("ix_species_entry_stereo_smiles", table_name="species_entry")
    op.drop_constraint("stereo_resolution_iff_descriptor", "species_entry", type_="check")
    op.drop_constraint("stereo_descriptor_vocabulary", "species_entry", type_="check")
    for table, marker in _MERGEABLE:
        op.drop_constraint("not_merged_into_itself", table, type_="check")
        op.drop_index(f"ix_{table}_{marker}", table_name=table)
    op.drop_index("uq_chem_reaction_stoichiometry_hash", table_name="chem_reaction")
    op.drop_index("uq_species_entry_species_id", table_name="species_entry")
    op.drop_index("uq_species_identity", table_name="species")

    # The old keys cover every row, merged or not. A merged-away row that shares one with another is renamed (its row, ref
    # and history stay) so the keys can come back; nothing is deleted and nothing is un-merged.
    op.execute(
        """
        UPDATE species a SET smiles = 'merged:' || a.public_ref
         WHERE a.merged_into_species_id IS NOT NULL
           AND EXISTS (SELECT 1 FROM species b WHERE b.id <> a.id AND b.smiles = a.smiles
                          AND b.charge = a.charge AND b.multiplicity = a.multiplicity)
        """
    )
    op.execute(
        """
        UPDATE species_entry a SET stereo_label = left('merged:' || a.public_ref, 64)
         WHERE a.merged_into_species_entry_id IS NOT NULL
           AND EXISTS (SELECT 1 FROM species_entry b WHERE b.id <> a.id AND b.species_id = a.species_id
                          AND b.stereo_label IS NOT DISTINCT FROM a.stereo_label
                          AND b.electronic_state_kind = a.electronic_state_kind
                          AND b.electronic_state_label IS NOT DISTINCT FROM a.electronic_state_label
                          AND b.term_symbol IS NOT DISTINCT FROM a.term_symbol
                          AND b.isotope_key IS NOT DISTINCT FROM a.isotope_key)
        """
    )
    op.execute(
        """
        UPDATE chem_reaction a SET stoichiometry_hash = NULL
         WHERE a.merged_into_chem_reaction_id IS NOT NULL
           AND EXISTS (SELECT 1 FROM chem_reaction b WHERE b.id <> a.id AND b.stoichiometry_hash = a.stoichiometry_hash)
        """
    )
    # Two live rows cannot collide (their keys were unique), but a merged-away entry's label and a live one's can be equal
    # even when the descriptors differ; the update above keyed on the label, so it already covered that.
    for table, marker in _MERGEABLE:
        op.drop_constraint(f"fk_{table}_{marker}_{table}", table, type_="foreignkey")
        op.drop_column(table, marker)
    op.create_unique_constraint("uq_species_identity", "species", ["smiles", "charge", "multiplicity"])
    op.create_unique_constraint("uq_chem_reaction_stoichiometry_hash", "chem_reaction", ["stoichiometry_hash"])
    op.alter_column(
        "species_entry", "stereo_label", type_=sa.String(64), existing_nullable=True, postgresql_using="left(stereo_label, 64)"
    )
    op.create_unique_constraint(
        "uq_species_entry_species_id",
        "species_entry",
        ["species_id", "stereo_label", *_REST],
        postgresql_nulls_not_distinct=True,
    )
    op.drop_column("species_entry", "stereo_smiles")
    op.drop_column("species_entry", "stereo_resolution")
    op.drop_column("species_entry", "stereo_descriptor")
    postgresql.ENUM(name="stereo_resolution").drop(bind, checkfirst=True)
