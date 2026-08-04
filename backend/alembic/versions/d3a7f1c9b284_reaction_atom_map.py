"""Record which atom of a reaction is which atom of its transition state.

Adds ``reaction_atom_map`` and ``reaction_atom_map_pair``, implementing
ADR 0011. Before this revision a reaction said which species react, which
saddle point sits between them and what rate results, but nothing anywhere
said that the hydrogen leaving carbon 3 of the reactant is the hydrogen
half-transferred in the transition state and the hydrogen bonded to oxygen 1
of the product — and the absence was invisible, because an unmapped reaction
looked exactly like a mapped one nobody had asked about.

Not the ``atom_mapping`` that already exists
--------------------------------------------
``calc_geometry_validation.atom_mapping`` maps an *input* geometry to an
*output* geometry for the **same species**, beside ``is_isomorphic``, ``rmsd``
and ``n_mappings``. It answers "did the optimiser hand back the molecule I gave
it" — conformer alignment. It says nothing across a reaction. The new tables
are named ``reaction_atom_map*`` so the two cannot be read as one another;
ADR 0011 leaves renaming the older column undecided, and this revision does not
touch it.

Nor is it ``transition_state_validation_evidence``'s participant mappings,
which *partition* the saddle point's atoms among participant molecules without
saying which atom each one is. The atom map is the refinement of that partition
into a bijection, and it belongs to the micro reaction rather than to one IRC
evidence record.

Shape, and why
--------------
The map hangs off ``reaction_entry`` — the object that already ties reactants,
transition state and products together. Not off a species, which is deduped and
shared and maps differently in every reaction it appears in; not off a
geometry, which knows nothing of the reaction.

``reaction_atom_map_pair`` stores two legs, both **toward** the saddle point:
reactants→TS and products→TS. The transition state is the physical pivot, both
legs are what the IRC actually traverses, and composing them yields
reactants→products for free. Storing reactants→products directly instead would
discard the very thing that makes the map worth having.

Indices are ``geometry_atom.atom_index`` values and every geometry they count
into is named on the row, because an atom index is a property of a geometry,
not of a species. The alternative — indices against a canonical per-species
ordering — is conformer-independent and survives re-optimisation, but its
failure mode is a map that looks fine and silently refers to a different atom
order than the depositor intended. Geometry-relative indexing is checkable at
deposit time, and correctness cannot be retrofitted onto records nobody can
verify.

``source`` carries how the map was obtained and has **no default**.
``declared`` as a default would manufacture human attribution for a row nobody
wrote by hand — the failure ADR 0009 removed for energy-transfer scope and
ADR 0011 names again for inference — and ``inferred`` as a default would
understate a real declaration. ``ck_reaction_atom_map_inferred_requires_note``
makes the other half structural: an inferred map must name the algorithm that
produced it, because an inferred map whose origin is anonymous is one careless
consumer away from being read as a human assertion.

What the database refuses on its own
------------------------------------
Following ``f9b2e6c4a1d7``'s reasoning, three of ADR 0011's blocking rules are
in the record rather than only in one Pydantic validator on one write path:

* **an index that is not a real atom of the named geometry** — the two
  ``geometry_atom`` foreign keys;
* **an element changing across the map** — those same two foreign keys carry
  the *same* ``element`` column, so a pair whose two ends are different
  elements cannot be written at all. This is why
  ``uq_geometry_atom_geometry_id_element`` is added: it is redundant with
  ``geometry_atom``'s primary key on its own terms and exists solely to be the
  target of those keys. ``isotope_mass_number`` is deliberately *not* carried
  the same way — it is nullable, and a NULL column silently disables a MATCH
  SIMPLE foreign key, so an isotope check written this way would quietly not
  run on the ordinary case;
* **an atom claimed twice** — one unique constraint per direction, per leg.

``uq_reaction_entry_structure_participant_id`` is added for the same kind of
reason: it lets ``(structure_participant_id, side)`` be a foreign key, so a
pair's declared leg cannot drift from the role of the participant it belongs
to.

Completeness — "a reactant atom unaccounted for in the products where no
species is missing" — is a cross-row aggregate over both legs and cannot be a
constraint. It stays in ``tckdb_schemas.fragments.reaction_atom_map``, which
decides the hedge "where no species is missing" by asking whether the declared
participants conserve every element rather than by judgement.

Backfill design: there is none, and that is the decision
--------------------------------------------------------
Every reaction already in this database has no atom map, and **that is
correct, not a gap to fill**. A map is a scientific claim about a mechanism,
and for anything but the simplest abstraction several chemically distinct maps
are consistent with the same reactants and products. Generating one here and
storing it would put an unattributed mechanism claim into a record whose
depositor never made it — the same manufacture of provenance that duplicating
a network-wide ⟨ΔE⟩down across wells committed before ADR 0009, and worse than
the gap it would close, because an absent map is honest while a guessed one is
a false positive every downstream consumer will faithfully propagate.
ADR 0011 puts retroactive mapping explicitly out of scope; the corpus splits
between mapped and unmapped records and that is accepted rather than solved.

So ``upgrade()`` adds structure and no rows. The two additive unique
constraints cannot fail: each is a strict superset of a key the target table
already enforces — ``geometry_atom``'s primary key is ``(geometry_id,
atom_index)`` and ``reaction_entry_structure_participant``'s is ``(id)`` — so
no existing row can duplicate either, whatever the deployment holds.

``downgrade()`` refuses while any map exists. A map cannot be reconstructed:
it came from a depositor who followed an intrinsic reaction coordinate, and
ADR 0011 rules out re-deriving one. Dropping the tables would therefore destroy
assertions this project cannot get back, silently, in a step whose stated
purpose is to undo a schema change.

Revision ID: d3a7f1c9b284
Revises: f9b2e6c4a1d7
Create Date: 2026-08-05
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d3a7f1c9b284"
down_revision: Union[str, Sequence[str], None] = "f9b2e6c4a1d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


atom_map_source = postgresql.ENUM(
    "declared",
    "inferred",
    name="atom_map_source",
    create_type=False,
)

# Already created by the initial schema for ``reaction_participant.role``.
reaction_role = postgresql.ENUM(
    "reactant",
    "product",
    name="reaction_role",
    create_type=False,
)


def upgrade() -> None:
    """Add the atom-map tables and the two keys that make them enforceable."""
    bind = op.get_bind()
    atom_map_source.create(bind, checkfirst=True)

    # Targets of ``reaction_atom_map_pair``'s composite foreign keys. Both are
    # supersets of an existing key, so neither can fail on existing rows; see
    # the module docstring.
    op.create_unique_constraint(
        "uq_geometry_atom_geometry_id_element",
        "geometry_atom",
        ["geometry_id", "atom_index", "element"],
    )
    op.create_unique_constraint(
        "uq_reaction_entry_structure_participant_id",
        "reaction_entry_structure_participant",
        ["id", "role"],
    )

    op.create_table(
        "reaction_atom_map",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("reaction_entry_id", sa.BigInteger(), nullable=False),
        sa.Column("transition_state_entry_id", sa.BigInteger(), nullable=False),
        sa.Column("transition_state_geometry_id", sa.BigInteger(), nullable=False),
        sa.Column("source", atom_map_source, nullable=False),
        sa.Column("equivalent_map_count", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(
            "source <> 'inferred' OR note IS NOT NULL",
            name=op.f("ck_reaction_atom_map_inferred_requires_note"),
        ),
        sa.CheckConstraint(
            "equivalent_map_count IS NULL OR equivalent_map_count >= 1",
            name=op.f("ck_reaction_atom_map_equivalent_map_count_ge_1"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["app_user.id"],
            name=op.f("fk_reaction_atom_map_created_by_app_user"),
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["reaction_entry_id"],
            ["reaction_entry.id"],
            name="fk_reaction_atom_map_reaction_entry",
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["transition_state_entry_id"],
            ["transition_state_entry.id"],
            name="fk_reaction_atom_map_ts_entry",
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["transition_state_geometry_id"],
            ["geometry.id"],
            name="fk_reaction_atom_map_ts_geometry",
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reaction_atom_map")),
        # One map per saddle-point candidate. A deposit records the map it used
        # and, where it knows, how many equivalent maps exist; ADR 0011 does not
        # attempt to canonicalise among symmetry-equivalent alternatives, so
        # there is no second map to store.
        sa.UniqueConstraint(
            "reaction_entry_id",
            "transition_state_entry_id",
            name="uq_reaction_atom_map_reaction_entry_id",
        ),
        # Target of the pair rows' composite foreign key: a pair cannot name a
        # transition-state geometry its own map does not.
        sa.UniqueConstraint(
            "id",
            "transition_state_geometry_id",
            name="uq_reaction_atom_map_id",
        ),
    )

    op.create_table(
        "reaction_atom_map_pair",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("atom_map_id", sa.BigInteger(), nullable=False),
        sa.Column("side", reaction_role, nullable=False),
        sa.Column("structure_participant_id", sa.BigInteger(), nullable=False),
        sa.Column("geometry_id", sa.BigInteger(), nullable=False),
        sa.Column("atom_index", sa.Integer(), nullable=False),
        sa.Column("transition_state_geometry_id", sa.BigInteger(), nullable=False),
        sa.Column("ts_atom_index", sa.Integer(), nullable=False),
        sa.Column("element", sa.CHAR(length=2), nullable=False),
        sa.CheckConstraint(
            "atom_index >= 1",
            name=op.f("ck_reaction_atom_map_pair_atom_index_ge_1"),
        ),
        sa.CheckConstraint(
            "ts_atom_index >= 1",
            name=op.f("ck_reaction_atom_map_pair_ts_atom_index_ge_1"),
        ),
        sa.ForeignKeyConstraint(
            ["atom_map_id", "transition_state_geometry_id"],
            [
                "reaction_atom_map.id",
                "reaction_atom_map.transition_state_geometry_id",
            ],
            name="fk_reaction_atom_map_pair_atom_map_id_reaction_atom_map",
            initially="IMMEDIATE",
            deferrable=True,
        ),
        # Keeps a pair's declared leg in step with the role of the participant
        # molecule it belongs to.
        sa.ForeignKeyConstraint(
            ["structure_participant_id", "side"],
            [
                "reaction_entry_structure_participant.id",
                "reaction_entry_structure_participant.role",
            ],
            name="fk_reaction_atom_map_pair_structure_participant",
            initially="IMMEDIATE",
            deferrable=True,
        ),
        # These two carry the *same* ``element`` column, which is what makes
        # "an element does not change across the map" a thing the database
        # refuses rather than a thing one validator checks.
        sa.ForeignKeyConstraint(
            ["geometry_id", "atom_index", "element"],
            [
                "geometry_atom.geometry_id",
                "geometry_atom.atom_index",
                "geometry_atom.element",
            ],
            name="fk_reaction_atom_map_pair_geometry_id_geometry_atom",
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["transition_state_geometry_id", "ts_atom_index", "element"],
            [
                "geometry_atom.geometry_id",
                "geometry_atom.atom_index",
                "geometry_atom.element",
            ],
            name="fk_reaction_atom_map_pair_ts_geometry_id_geometry_atom",
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reaction_atom_map_pair")),
        # Injectivity, both directions, per leg.
        sa.UniqueConstraint(
            "atom_map_id",
            "structure_participant_id",
            "atom_index",
            name="uq_reaction_atom_map_pair_atom_map_id",
        ),
        sa.UniqueConstraint(
            "atom_map_id",
            "side",
            "ts_atom_index",
            name="uq_reaction_atom_map_pair_ts_atom_index",
        ),
    )


def downgrade() -> None:
    """Remove the tables, refusing to destroy maps that cannot be recovered."""
    bind = op.get_bind()
    stored = bind.execute(
        sa.text("SELECT count(*) FROM reaction_atom_map")
    ).scalar_one()
    if stored:
        raise RuntimeError(
            f"Cannot downgrade from d3a7f1c9b284: this database holds {stored} "
            "reaction atom map(s). Each one is a depositor's statement about "
            "which atom of the reactants is which atom of the transition "
            "state, made by someone who followed the intrinsic reaction "
            "coordinate. ADR 0011 rules out re-deriving a map — several "
            "chemically distinct maps are usually consistent with the same "
            "reactants and products — so dropping these tables destroys "
            "assertions this project cannot get back.\n"
            "Operator steps:\n"
            "  1. Back up the database.\n"
            "  2. Export the affected reactions "
            "(GET /api/v1/scientific/reaction-entries/{id}/full"
            "?include=atom_map).\n"
            "  3. Delete the atom maps.\n"
            "  4. Re-run the downgrade."
        )

    op.drop_table("reaction_atom_map_pair")
    op.drop_table("reaction_atom_map")
    op.drop_constraint(
        "uq_reaction_entry_structure_participant_id",
        "reaction_entry_structure_participant",
        type_="unique",
    )
    op.drop_constraint(
        "uq_geometry_atom_geometry_id_element",
        "geometry_atom",
        type_="unique",
    )
    atom_map_source.drop(bind, checkfirst=True)
