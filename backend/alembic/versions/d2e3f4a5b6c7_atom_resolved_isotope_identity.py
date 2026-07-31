"""Atom-resolved isotope identity for geometries and species entries.

Closes Stage 2 blocker B10. Before this revision the only isotope-aware
thing in the schema was ``species_entry.isotopologue_label``: a free-text
``VARCHAR(64)`` that participated in ``uq_species_entry_species_id``. A
producer could mint two distinct species identities that differed only by
an arbitrary string, and nothing anywhere recorded *which atom* carried
*which nuclide* — so isotope-specific frequencies, rotational constants,
ZPE, Hessian reuse and kinetic isotope effects were unreconstructible.

This revision:

1. Adds ``geometry_atom.isotope_mass_number`` (nullable ``SMALLINT``).
   ``NULL`` means "this nucleus is at the element's most abundant natural
   isotope" — it is not "unknown".
2. Adds ``species_entry.isotope_key`` (nullable ``TEXT``): the canonical
   SMILES of the isotope-labelled identity molecule, derived server-side
   by ``app.chemistry.species.canonical_isotope_key``. ``NULL`` is the
   all-standard key.
3. Replaces ``isotopologue_label`` with ``isotope_key`` inside
   ``uq_species_entry_species_id``.
4. Leaves the ``isotopologue_label`` *column* in place, demoted to a
   deprecated, non-identity annotation. Dropping it would destroy
   producer-supplied text on any deployment that populated it, and the
   column can no longer fork identity once it is out of the constraint.

**Backfill design.** No backfill is performed, and none is required:

* ``geometry_atom.isotope_mass_number`` is nullable and ``NULL`` already
  carries the correct meaning for every pre-existing row. Every geometry
  stored before atom-resolved isotope support is, by construction, an
  ordinary isotopologue. Writing an explicit mass number onto 41k+
  existing atom rows would assert provenance the database never had.
* ``species_entry.isotope_key`` is nullable and ``NULL`` is the canonical
  all-standard key. Pre-existing entries therefore keep byte-identical
  identity tuples across the constraint swap. Deriving a key from
  ``isotopologue_label`` is impossible in SQL and would be a guess: the
  label is free text with no defined grammar.
* ``geometry.geom_hash`` is unchanged for every existing row. The
  application only appends an isotope suffix to the hashed text when a
  substitution is present, so no rehash migration is needed.

**Collision guard.** Removing a column from a ``NULLS NOT DISTINCT``
unique constraint can merge rows that previously differed only on that
column. ``upgrade()`` refuses to run if that would happen, naming the
affected species. It cannot fire on a deployment whose
``isotopologue_label`` values are all ``NULL``.

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "d2e3f4a5b6c7"
down_revision: Union[str, Sequence[str], None] = "c1d2e3f4a5b6"
branch_labels = None
depends_on = None


_IDENTITY_COLUMNS_WITHOUT_LABEL = (
    "species_id",
    "stereo_label",
    "electronic_state_kind",
    "electronic_state_label",
    "term_symbol",
)

# Rows that collide once `isotopologue_label` leaves the identity tuple.
# `IS NOT DISTINCT FROM` reproduces the constraint's NULLS NOT DISTINCT
# semantics, which plain `=` would not.
_COLLISION_QUERY = sa.text(
    """
    SELECT
        species.smiles AS smiles,
        species_entry.species_id AS species_id,
        count(*) AS entry_count,
        array_agg(species_entry.isotopologue_label ORDER BY species_entry.id) AS labels
    FROM species_entry
    JOIN species ON species.id = species_entry.species_id
    GROUP BY
        species.smiles,
        species_entry.species_id,
        species_entry.stereo_label,
        species_entry.electronic_state_kind,
        species_entry.electronic_state_label,
        species_entry.term_symbol
    HAVING count(*) > 1
    """
)


def _assert_no_identity_collisions(bind) -> None:
    """Refuse the constraint swap when it would merge distinct entries."""

    collisions = bind.execute(_COLLISION_QUERY).mappings().all()
    if not collisions:
        return

    details = "; ".join(
        "species_id={species_id} (smiles={smiles!r}): {entry_count} entries "
        "distinguished only by isotopologue_label={labels}".format(**row)
        for row in collisions
    )
    raise RuntimeError(
        "Cannot upgrade to d2e3f4a5b6c7: removing isotopologue_label from "
        "uq_species_entry_species_id would merge species entries that are "
        "currently distinct. Affected: "
        f"{details}. "
        "Re-express each of those entries with atom-resolved isotope content "
        "(SMILES isotope notation such as [2H]/[13C]) and re-run the upgrade, "
        "or merge the duplicates first. This migration will not silently "
        "collapse two scientific identities into one."
    )


def upgrade() -> None:
    bind = op.get_bind()
    _assert_no_identity_collisions(bind)

    op.add_column(
        "geometry_atom",
        sa.Column("isotope_mass_number", sa.SmallInteger(), nullable=True),
    )
    op.create_check_constraint(
        "isotope_mass_number_ge_1",
        "geometry_atom",
        "isotope_mass_number IS NULL OR isotope_mass_number >= 1",
    )

    op.add_column(
        "species_entry",
        sa.Column("isotope_key", sa.Text(), nullable=True),
    )

    op.drop_constraint(
        "uq_species_entry_species_id", "species_entry", type_="unique"
    )
    op.create_unique_constraint(
        "uq_species_entry_species_id",
        "species_entry",
        [*_IDENTITY_COLUMNS_WITHOUT_LABEL, "isotope_key"],
        postgresql_nulls_not_distinct=True,
    )


def downgrade() -> None:
    bind = op.get_bind()
    # The prior schema has nowhere to put an atom-resolved isotope key, so a
    # rollback would silently merge every isotopologue of a species onto one
    # entry (and drop the per-atom nuclide labels outright). Refuse rather
    # than corrupt, symmetrically with the upgrade guard.
    if bind.execute(
        sa.text("SELECT EXISTS (SELECT 1 FROM species_entry WHERE isotope_key IS NOT NULL)")
    ).scalar():
        raise RuntimeError(
            "Cannot downgrade d2e3f4a5b6c7 while isotopically resolved species "
            "entries exist: the prior schema has no atom-resolved isotope "
            "identity and the rollback would merge distinct isotopologues. "
            "Export or delete those entries first."
        )
    if bind.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM geometry_atom WHERE isotope_mass_number IS NOT NULL)"
        )
    ).scalar():
        raise RuntimeError(
            "Cannot downgrade d2e3f4a5b6c7 while isotopically labelled geometry "
            "atoms exist: the prior schema cannot store per-atom nuclides and "
            "the rollback would discard them. Export those geometries first."
        )

    op.drop_constraint(
        "uq_species_entry_species_id", "species_entry", type_="unique"
    )
    op.create_unique_constraint(
        "uq_species_entry_species_id",
        "species_entry",
        [*_IDENTITY_COLUMNS_WITHOUT_LABEL, "isotopologue_label"],
        postgresql_nulls_not_distinct=True,
    )
    op.drop_column("species_entry", "isotope_key")

    # Bare name: NAMING_CONVENTION expands it to
    # `ck_geometry_atom_isotope_mass_number_ge_1` on both create and drop.
    op.drop_constraint("isotope_mass_number_ge_1", "geometry_atom", type_="check")
    op.drop_column("geometry_atom", "isotope_mass_number")
