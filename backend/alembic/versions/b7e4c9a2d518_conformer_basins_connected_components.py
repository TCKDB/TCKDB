"""Conformer basins are connected components: scheme ``torsion_basin`` version 3, its columns, and the reassignment.

What was wrong with version 2
-----------------------------
* A basin was matched against ONE stored reference, the first structure ever deposited into it. Three structures
  18 degrees apart (0, 18, 36 against a 20 degree tolerance) made three different partitions from the six orders they
  can arrive in, and a recompute reproduced the defect instead of repairing it, because it regrouped in observation
  order and rebuilt the reference from the first member.
* Any calculation type could create or extend a basin, with no convergence check, so a scan point or a partial
  optimisation could bridge two minima.
* The two hydrogens of an ``-NH2`` or radical ``-CH2`` top were each allowed to stand for the slot on its own, so
  trans and gauche ethylamine, which share one N-H position, shared a basin.

Version 3 (parameters as stored, below)
---------------------------------------
* a basin is a connected component of the entry's *defining* observations (an ``opt`` or ``composite`` primary with
  a final geometry that is not stated as unconverged), two linked when the unchanged ``symmetry_existence`` rule
  says they share a basin; evaluated symmetrically, so the cap on the symmetry search cannot make linkage depend on
  which of two structures came first. Everything else is filed into a basin but never links two.
* the stored representative is the *medoid* of the defining members: derived, never identity, not what an upload is
  matched against;
* the two hydrogens of a two-hydrogen top are compared as a pair under one assignment.
* a component whose members are not all within tolerance of each other (a chain) is a *wide basin*: stored as
  ``basin_is_clique = false`` with its span, never split automatically.

Schema
------
Nullable columns only, so every existing row stays valid:

* ``conformer_group.representative_observation_id`` (the observation the representative was taken from),
  ``merged_into_conformer_group_id`` (a group emptied by a merge says where its members went, so a cited ``cg_`` ref
  resolves forward), ``basin_is_clique``, ``basin_span_deg``;
* ``conformer_observation.mapped_coords_json`` (the structure in the species' canonical atom order, so an upload does
  not re-read ``geometry_atom`` for every member). Filled by the data step below;
* the table ``conformer_group_merge_pending``: a merge that a live upload decided on but could not make because
  approved records point at the younger group, which only a declared repair may move and only an owner role may
  declare. The upload files its observation under the older group and writes a row here; the data step (or
  ``reassign_conformer_groups`` over the entries listed) completes it and stamps ``completed_at``.

The data step
-------------
``app.services.conformer_reassignment.reassign_conformer_groups`` recomputes every species entry's basins under the
new default scheme, under the same per-entry advisory lock live uploads take, and moves what pointed at a changed
group (thermo targets, statmech basin declarations and ensemble members, conformer selections) to the group holding
the observation its own calculations came from, or, where that cannot be determined, to the group holding the
original representative. Merged groups land on the older group. Every group whose membership changed has its review
reset to ``not_reviewed`` with a ``record_review_event``. Most changes are merges, but a version 2 group held together
only by a structure that does not define a basin can split; both go through the same machinery.

Approved rows are frozen by trigger (``conformer_observation`` and ``thermo`` are accepted-science roots; the statmech
declaration and its members are frozen with their statmech record). They are changed only through the declared-repair
path of ``e2c9a4f7b163``: one ``accepted_science_repair`` declaration per table, naming the exact columns, in this
transaction, each changed accepted row recorded old -> new in ``accepted_science_repair_change``. The trigger is not
disabled or weakened. A repair is declared only for a table that actually needs a write, so a database with nothing to
move declares nothing. Statmech declarations and members use the narrow exception of ``a5d9c3e7b1f2`` (only
``conformer_group_id``, plus the declaration's ``content_hash`` recomputed in the same repair).

No group is deleted: an emptied group keeps its row, review history and public ref.

The step is a no-op unless the default scheme is exactly (``torsion_basin``, 3). It composes with other data steps
that call it (for instance one that merges species entries): it recomputes whatever entries it is given from the
database as it finds it. Idempotent: running it again changes nothing.

``d3f8a1c6e92b`` (version 2) calls the same service, which now declines unless the default is version 3, so on a
database that has not yet run it, it does nothing and this revision does the work once. On a database that already
ran it, nothing changes.

Downgrade
---------
Makes version 2 the default again, drops the columns and the pending table. It does **not** un-merge: the grouping
before the upgrade cannot be rebuilt (a merge destroys which observation was in which group), and version 3's own
grouping stays in place. The version 3 row is kept because observations and selections reference it. Upgrading again
re-asserts version 3 as the default and re-runs the (idempotent) step.

Revision ID: b7e4c9a2d518
Revises: f3a8c5d1e947
Create Date: 2026-10-26
"""

import base64
import hashlib
import json
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from alembic import op

revision: str = "b7e4c9a2d518"
down_revision: Union[str, Sequence[str], None] = "f3a8c5d1e947"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NAME = "torsion_basin"
_VERSION = 3

#: Frozen copy of the version 3 parameters (``TORSION_BASIN_PARAMS`` in ``app.services.conformer_resolution``): what
#: this revision stored is what this revision stored, whatever that constant later becomes. Version 2's parameters,
#: stored by ``d3f8a1c6e92b``, are not touched.
_PARAMETERS = {
    "matching_rule": "symmetry_existence",
    "require_all_comparable_torsions_within_threshold": True,
    "use_circular_difference": True,
    "torsion_tolerance_heavy_degrees": 20,
    "torsion_tolerance_terminal_h_degrees": 30,
    "exclude_methyl_rotors": False,
    "exclude_terminal_noisy_rotors": False,
    "methyl_symmetry_fold": 3,
    "max_symmetry_mappings": 4096,
    "quantization_bin_degrees": 15,
    "rigid_fallback_use_rmsd": True,
    "rmsd_threshold_angstrom": 0.5,
    "tie_break": "closest_torsional_distance",
    "linkage": "connected_components",
    "reference": "medoid",
    "defining_members": "converged_opt",
}

_DESCRIPTION = (
    "Basins are connected components: the converged optimised structures of a species entry are linked when the "
    "symmetry-aware matching rule says they share a basin (some graph symmetry puts every rotor within its own "
    "tolerance: 20 degrees for heavy-atom and methyl rotors, 30 degrees for one- and two-hydrogen tops), and a basin "
    "is what the links reach, so the order of deposition cannot change it. The two hydrogens of an -NH2 or radical "
    "-CH2 top are compared as a pair. Structures that are not converged optimisations are filed into a basin but never "
    "link two. The stored representative is the medoid of the defining members, derived and not identity. A basin "
    "whose members are not all within tolerance of each other is flagged as wide, never split. The symmetry search is "
    "capped; linkage is evaluated both ways round so the cap cannot make it order-dependent."
)


def _content_ref(prefix: str, canonical_identity: str) -> str:
    """Frozen copy of ``app.services.public_refs.make_content_ref`` (sha256, first 16 bytes, base32, 26 chars)."""
    digest = hashlib.sha256(canonical_identity.encode("utf-8")).digest()[:16]
    return f"{prefix}_{base64.b32encode(digest).decode('ascii').lower().rstrip('=')[:26]}"


def upgrade() -> None:
    op.add_column("conformer_group", sa.Column("representative_observation_id", sa.BigInteger(), nullable=True))
    op.add_column("conformer_group", sa.Column("merged_into_conformer_group_id", sa.BigInteger(), nullable=True))
    op.add_column("conformer_group", sa.Column("basin_is_clique", sa.Boolean(), nullable=True))
    op.add_column("conformer_group", sa.Column("basin_span_deg", sa.Float(), nullable=True))
    op.create_foreign_key(
        "fk_conformer_group_representative_observation_id",
        "conformer_group",
        "conformer_observation",
        ["representative_observation_id"],
        ["id"],
        deferrable=True,
        initially="IMMEDIATE",
    )
    op.create_foreign_key(
        "fk_conformer_group_merged_into_conformer_group_id",
        "conformer_group",
        "conformer_group",
        ["merged_into_conformer_group_id"],
        ["id"],
        deferrable=True,
        initially="IMMEDIATE",
    )
    op.add_column("conformer_observation", sa.Column("mapped_coords_json", postgresql.JSONB(), nullable=True))

    op.create_table(
        "conformer_group_merge_pending",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("species_entry_id", sa.BigInteger(), nullable=False),
        sa.Column("kept_conformer_group_id", sa.BigInteger(), nullable=False),
        sa.Column("absorbed_conformer_group_id", sa.BigInteger(), nullable=False),
        sa.Column("conformer_observation_id", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=False), nullable=True),
        sa.Column("completed_by_revision", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=False), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "kept_conformer_group_id <> absorbed_conformer_group_id",
            name=op.f("ck_conformer_group_merge_pending_kept_differs_from_absorbed"),
        ),
        sa.ForeignKeyConstraint(
            ["species_entry_id"],
            ["species_entry.id"],
            name=op.f("fk_conformer_group_merge_pending_species_entry_id_species_entry"),
            deferrable=True,
            initially="IMMEDIATE",
        ),
        sa.ForeignKeyConstraint(
            ["kept_conformer_group_id"],
            ["conformer_group.id"],
            name="fk_conformer_group_merge_pending_kept_group",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        sa.ForeignKeyConstraint(
            ["absorbed_conformer_group_id"],
            ["conformer_group.id"],
            name="fk_conformer_group_merge_pending_absorbed_group",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        sa.ForeignKeyConstraint(
            ["conformer_observation_id"],
            ["conformer_observation.id"],
            name="fk_conformer_group_merge_pending_observation",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conformer_group_merge_pending")),
        sa.UniqueConstraint(
            "kept_conformer_group_id",
            "absorbed_conformer_group_id",
            "conformer_observation_id",
            name="uq_conformer_group_merge_pending_merge",
        ),
    )
    op.create_index(
        "ix_conformer_group_merge_pending_open",
        "conformer_group_merge_pending",
        ["species_entry_id"],
        postgresql_where=sa.text("completed_at IS NULL"),
    )

    bind = op.get_bind()
    bind.execute(sa.text("UPDATE conformer_assignment_scheme SET is_default = false WHERE is_default"))
    bind.execute(
        sa.text(
            """
            INSERT INTO conformer_assignment_scheme
                (name, version, scope, description, parameters_json, is_default, public_ref)
            VALUES
                (:name, :version, CAST('canonical' AS conformer_assignment_scope_kind), :description,
                 CAST(:parameters AS jsonb), true, :ref)
            ON CONFLICT ON CONSTRAINT uq_conformer_assignment_scheme_name
            DO UPDATE SET is_default = true
            """
        ),
        {
            "name": _NAME,
            "version": _VERSION,
            "description": _DESCRIPTION,
            "parameters": json.dumps(_PARAMETERS),
            "ref": _content_ref("cas", f"cas:name={_NAME};version={_VERSION};scope=canonical"),
        },
    )

    # Imported here, not at module level: importing the app from a migration module at load time would run for
    # every alembic command, including ones that never reach this revision.
    from app.services.conformer_reassignment import reassign_conformer_groups

    session = Session(bind=bind)
    report = reassign_conformer_groups(session, repair_revision=revision)
    session.flush()
    print(f"[{revision}] conformer reassignment: {report.summary()}")


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("UPDATE conformer_assignment_scheme SET is_default = false WHERE is_default"))
    bind.execute(
        sa.text("UPDATE conformer_assignment_scheme SET is_default = true WHERE name = :name AND version = 2"),
        {"name": _NAME},
    )
    op.drop_index("ix_conformer_group_merge_pending_open", table_name="conformer_group_merge_pending")
    op.drop_table("conformer_group_merge_pending")
    op.drop_column("conformer_observation", "mapped_coords_json")
    op.drop_constraint("fk_conformer_group_merged_into_conformer_group_id", "conformer_group", type_="foreignkey")
    op.drop_constraint("fk_conformer_group_representative_observation_id", "conformer_group", type_="foreignkey")
    op.drop_column("conformer_group", "basin_span_deg")
    op.drop_column("conformer_group", "basin_is_clique")
    op.drop_column("conformer_group", "merged_into_conformer_group_id")
    op.drop_column("conformer_group", "representative_observation_id")
