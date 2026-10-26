"""The species key is the constitution; stereochemistry is resolved on the species entry.

DR-0038. Species identity was ``(canonical SMILES with stereo marks, charge, multiplicity)``, and the entry's stereo came
from a free-text label (which won unconditionally) or from geometry; the SMILES marks never reached the entry. So
``C/C=C/C``, ``CC=CC`` and ``C/C=C\\C`` were three species, ``C/C=C/C`` with a cis geometry was accepted as ``Z``, and a
fully unknown configuration was NULL. Now:

* a species is the stereo-blind canonical SMILES (isotopes stripped) plus charge and multiplicity, its InChIKey taken from
  the same molecule;
* an entry's stereo key is ``stereo_descriptor``: one ``R``/``S``/``E``/``Z``/``?`` token per potential stereo element,
  ordered by canonical atom rank of the stereo-stripped constitution (NULL only for an achiral species). It replaces
  ``stereo_label`` in ``uq_species_entry_species_id``; ``stereo_label`` becomes derived display text, and
  ``stereo_resolution`` (``resolved``/``partial``/``unresolved``) and ``stereo_smiles`` (canonical isomeric SMILES, where
  one can be written) are derived beside it.

Schema
------
``species_entry`` gains ``stereo_descriptor`` (text), ``stereo_resolution`` (new enum ``stereo_resolution``) and
``stereo_smiles`` (text, indexed). ``stereo_label`` widens from ``varchar(64)`` to ``text`` (a descriptor has one token per
element). Two CHECK constraints pin the descriptor vocabulary and ``descriptor IS NULL <=> resolution IS NULL``.

The data step
-------------
``app.services.stereo_identity_merge.merge_stereo_identity`` recomputes every species key and merges the species that now
collide (oldest id survives; entries, reaction participants -- same-side collisions add their stoichiometry -- and third-body
colliders move to it); recomputes ``chem_reaction.stoichiometry_hash`` and merges the reactions that now collide; derives
every entry's descriptor from its own SMILES marks and its first linked geometry; merges entries whose key now collides,
re-pointing every foreign key to ``species_entry`` (read from the database catalogue, not from a list); recomputes the
``structure_determination.identity_hash`` and ``network_state.composition_hash`` values that cover a re-pointed id; resets
the review of each surviving entry; and re-runs the conformer reassignment on the survivors.

Accepted (approved) rows change only through the declared-repair path of ``e2c9a4f7b163``: one declaration per guarded
table, naming exactly the columns written, in this transaction; the trigger is not disabled or weakened. What that path
cannot express (a primary-key column under an approved record, or two rows that would become duplicates) stops the
migration with an error naming the table; nothing is half-applied, because the whole revision is one transaction.

An entry whose SMILES marks and geometry contradict each other is not relabelled by a guess: it gets the all-unknown
descriptor, keeps its old label text, and is listed in the printed summary for curation.

A loser row (species, entry, reaction) is deleted only after nothing references it; the loser -> survivor public refs are
printed. (A row cannot be kept alongside its survivor: it would share the survivor's unique key.)

Downgrade
---------
Restores the previous schema (the old unique key over ``stereo_label``, the old column width) and drops the new columns and
enum. It does **not** un-merge: which species, entries and reactions were once separate cannot be rebuilt (the merge
destroyed it), and the species key stays the constitution. ``stereo_label`` keeps its derived text.

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


def upgrade() -> None:
    bind = op.get_bind()
    postgresql.ENUM("resolved", "partial", "unresolved", name="stereo_resolution").create(bind, checkfirst=True)
    op.add_column("species_entry", sa.Column("stereo_descriptor", sa.Text(), nullable=True))
    op.add_column("species_entry", sa.Column("stereo_resolution", _RESOLUTION, nullable=True))
    op.add_column("species_entry", sa.Column("stereo_smiles", sa.Text(), nullable=True))
    op.alter_column("species_entry", "stereo_label", type_=sa.Text(), existing_nullable=True)

    # The old key (over the label) cannot stand while entries are re-described and merged.
    op.drop_constraint("uq_species_entry_species_id", "species_entry", type_="unique")

    # Imported here, not at module level: importing the app from a migration module at load time would run for every
    # alembic command, including ones that never reach this revision.
    from app.services.stereo_identity_merge import merge_stereo_identity

    session = Session(bind=bind)
    report = merge_stereo_identity(session, revision=revision)
    session.flush()
    print(f"[{revision}] species key / entry stereo: {report.summary()}")

    op.create_unique_constraint(
        "uq_species_entry_species_id",
        "species_entry",
        ["species_id", "stereo_descriptor", *_REST],
        postgresql_nulls_not_distinct=True,
    )
    op.create_check_constraint(
        "stereo_descriptor_vocabulary",
        "species_entry",
        "stereo_descriptor IS NULL OR stereo_descriptor ~ '^[RSEZ?](,[RSEZ?])*$'",
    )
    op.create_check_constraint(
        "stereo_resolution_iff_descriptor",
        "species_entry",
        "(stereo_descriptor IS NULL) = (stereo_resolution IS NULL)",
    )
    op.create_index("ix_species_entry_stereo_smiles", "species_entry", ["stereo_smiles"])


def downgrade() -> None:
    op.drop_index("ix_species_entry_stereo_smiles", table_name="species_entry")
    op.drop_constraint("stereo_resolution_iff_descriptor", "species_entry", type_="check")
    op.drop_constraint("stereo_descriptor_vocabulary", "species_entry", type_="check")
    op.drop_constraint("uq_species_entry_species_id", "species_entry", type_="unique")
    op.create_unique_constraint(
        "uq_species_entry_species_id",
        "species_entry",
        ["species_id", "stereo_label", *_REST],
        postgresql_nulls_not_distinct=True,
    )
    op.alter_column("species_entry", "stereo_label", type_=sa.String(64), existing_nullable=True,
                    postgresql_using="left(stereo_label, 64)")
    op.drop_column("species_entry", "stereo_smiles")
    op.drop_column("species_entry", "stereo_resolution")
    op.drop_column("species_entry", "stereo_descriptor")
    postgresql.ENUM(name="stereo_resolution").drop(op.get_bind(), checkfirst=True)
