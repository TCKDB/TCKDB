"""store one spelling of an element symbol, not three

``parse_xyz`` used to put ``parts[0]`` into ``geometry_atom.element``
exactly as the depositor's file wrote it. Electronic-structure codes do
not agree about capitalisation, so a ``character(2)`` column that every
element comparison in the codebase reads could hold ``Cl``, ``CL``,
``cl`` and ``bR`` for one element. Three separate sites existed only to
paper over that at comparison time, and the class of bug they papered
over had already shipped twice: a saddle point written ``CL`` contradicted
its own reaction, and a mixed-case deposit was accepted at the blocking
tier while ``calc_geometry_validation`` recorded ``fail`` on the same
bytes.

``parse_xyz`` now canonicalises the symbol before it becomes a row
(``normalize_element_symbol`` -- first character upper, rest lower, and
nothing else). This revision brings the rows written before it into the
same form, so that "a symbol read out of ``geometry_atom`` is canonical"
is true of the whole table rather than only of its future.

Case only, deliberately
-----------------------
``D`` and ``T`` are left as ``D`` and ``T``. They are legal XYZ tokens
that every major ESS emits or accepts, and they are the depositor's own
isotope labelling; collapsing them to ``H`` here would destroy a fact
about the deposit rather than settle a spelling of one. Code that counts
elements resolves them at the point of counting
(``app.chemistry.geometry.resolve_element_symbol``).

What is deliberately NOT rewritten
----------------------------------
``geometry.xyz_text``. ``geom_hash`` is a sha256 over that text, it is
the dedupe key in ``app.services.geometry_resolution``, and
``app.services.public_refs`` mints ``geometry:geom_hash=<hash>`` as a
citable public ref. Rewriting a symbol inside the hashed text would
re-key every already-stored geometry whose XYZ shouted an element:
published refs would dangle, and a re-upload of the very same file would
compute the old hash and fail to dedupe onto its own row. So
``geometry.xyz_text`` may read ``CL`` for an atom whose
``geometry_atom.element`` reads ``Cl``, and that divergence is the point:
``xyz_text`` is the deposited evidence and has to read back as deposited,
``geometry_atom`` is the parsed index that joins and comparisons run
against, and only the second one has to be canonical for the first one to
stay citable.

``energy_correction_scheme_atom_param.element`` is also NOT rewritten,
and that is a decision rather than an oversight. It is ``text``, it is
half of that table's primary key, and it is a *reference library* key --
the element label a published correction scheme (AEC, SOC, BAC) uses for
its own parameters. Nothing in the tree joins it to ``geometry_atom`` or
compares it against a deposited geometry today, so there is no comparison
being papered over. Normalising a primary-key column is also not the same
operation: two rows of one scheme spelled ``C`` and ``c`` would collide on
update, which needs a merge policy rather than an ``UPDATE``. When a
correction is actually applied to a geometry's atom counts, that join is
where the canonical form has to be settled, and it can carry its own
revision.

Ordering, and why it is safe
----------------------------
``reaction_atom_map_pair`` carries ``element`` and ``ts_element`` into
two composite foreign keys onto
``geometry_atom (geometry_id, atom_index, element)``. The element column
is part of the referenced key, so *no* ordering of two plain UPDATEs is
valid: rewriting the parent first orphans every child row that quotes the
old spelling, and rewriting a child first points it at a parent key that
does not exist yet. Both directions fail at statement end.

Both foreign keys were created ``DEFERRABLE INITIALLY IMMEDIATE`` (see
``d3a7f1c9b284``), so this revision defers them by name, updates both
tables, and then sets them back to IMMEDIATE. The referential check runs
once, at that point, against the finished state -- in which every child
triple ``(geometry_id, atom_index, element)`` again names a real
``geometry_atom`` row, because both sides received the *same*
transformation. Deferral is what makes the two updates atomic with
respect to the constraint; the order of the statements between them is
therefore irrelevant, and ``geometry_atom`` is updated first only because
it is the side that defines the truth.

Two constraints that could have been a problem and are not:

* ``ck_reaction_atom_map_pair_element_matches`` (``upper(element) =
  upper(ts_element)``) is a CHECK and cannot be deferred. It does not need
  to be: ``upper()`` is invariant under this transformation, so every
  intermediate row satisfies it exactly as it did before.
* ``uq_geometry_atom_geometry_id_element`` cannot be violated --
  ``(geometry_id, atom_index)`` is already the primary key, so the
  triple's uniqueness never depended on ``element``.

Why the accepted-science guard is stood down for this UPDATE
------------------------------------------------------------
``trg_as_geometry_atom`` (from ``c6f2a9d4e7b1``) refuses any UPDATE to a
``geometry_atom`` row whose geometry is attached to an **accepted**
calculation. On the deployed database that is most of the interesting
rows, and skipping them is not an option: a backfill that leaves accepted
geometries non-canonical leaves the invariant this revision exists to
establish false, and the code change that goes with it -- deleting the
comparison-time normalisation in ``app.services.reaction_atom_map`` --
would then produce a *false refusal* on exactly the oldest, most-cited
data. Partial is worse than either whole or none.

The guard is therefore disabled for the duration of this transaction and
re-enabled inside it. That is defensible here and would not be for an
ordinary data edit, for a specific reason: **the accepted record does not
change.** ``geom_hash`` is untouched, so the geometry's identity and its
citable public ref are the same before and after; ``geometry.xyz_text``
is untouched, so the evidence the record was approved on still reads back
byte-for-byte as deposited; the coordinates, the isotope labels and the
atom ordering are untouched. What changes is the case of a symbol in a
derived index column that every consumer in the tree already read
case-insensitively -- this revision makes the storage agree with the
meaning the code always gave it. Reviewers approved the chlorine, not the
shift key.

``ALTER TABLE ... DISABLE TRIGGER`` needs table ownership, which the
migration role has and the application role does not; see
``backend/docs/deployment/database_roles.md`` and the ownership boundary
recorded in ``backend/docs/specs/dataset_release_and_profiles.md``.

The upgrade re-checks its own work before returning and raises if any
non-canonical row survives, so a guard that could not be stood down
fails the migration instead of silently half-applying it.

Revision ID: b4e7c1d20f83
Revises: d7f1a3c5e948
Create Date: 2026-08-10

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4e7c1d20f83"
down_revision: Union[str, Sequence[str], None] = "d7f1a3c5e948"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: SQL spelling of ``str.strip().capitalize()`` for a one- or two-character
#: element symbol: upper-case the first character, lower-case the rest.
#: ``btrim`` first, because ``geometry_atom.element`` is ``character(2)`` and a
#: one-letter symbol is stored blank-padded as ``'C '``.
def _canonical(column: str) -> str:
    return (
        f"upper(substr(btrim({column}), 1, 1)) || lower(substr(btrim({column}), 2))"
    )


_GEOMETRY_ATOM_FKS = (
    "fk_reaction_atom_map_pair_geometry_id_geometry_atom",
    "fk_reaction_atom_map_pair_ts_geometry_id_geometry_atom",
)

_ACCEPTED_SCIENCE_TRIGGER = "trg_as_geometry_atom"


def upgrade() -> None:
    element = _canonical("element")
    ts_element = _canonical("ts_element")

    # Both foreign keys are DEFERRABLE INITIALLY IMMEDIATE. Deferring them here
    # holds the referential check until COMMIT, which is the only way the
    # parent and the child can be rewritten in one step -- see the module
    # docstring for why no ordering of undeferred updates works.
    op.execute(f"SET CONSTRAINTS {', '.join(_GEOMETRY_ATOM_FKS)} DEFERRED")

    # Stood down, not removed: re-enabled below, in the same transaction, so a
    # failure anywhere in this revision rolls the guard back on with the data.
    op.execute(
        f"ALTER TABLE public.geometry_atom "
        f"DISABLE TRIGGER {_ACCEPTED_SCIENCE_TRIGGER}"
    )

    op.execute(
        f"""
        UPDATE geometry_atom
        SET element = {element}
        WHERE btrim(element) <> {element}
        """
    )
    op.execute(
        f"""
        UPDATE reaction_atom_map_pair
        SET element = {element},
            ts_element = {ts_element}
        WHERE btrim(element) <> {element}
           OR btrim(ts_element) <> {ts_element}
        """
    )

    # Back to IMMEDIATE before the guard goes back on, and not only for
    # tidiness: deferring the two foreign keys leaves pending trigger events on
    # `geometry_atom`, and PostgreSQL refuses `ALTER TABLE ... ENABLE TRIGGER`
    # on a table that has any ("cannot ALTER TABLE because it has pending
    # trigger events"). Setting them IMMEDIATE runs the referential check here,
    # against the finished state, which drains the queue -- and has the better
    # failure mode besides: a violation is reported at this statement rather
    # than at COMMIT, with the revision's own frame on the traceback.
    op.execute(f"SET CONSTRAINTS {', '.join(_GEOMETRY_ATOM_FKS)} IMMEDIATE")

    op.execute(
        f"ALTER TABLE public.geometry_atom "
        f"ENABLE TRIGGER {_ACCEPTED_SCIENCE_TRIGGER}"
    )

    connection = op.get_bind()
    for table, columns in (
        ("geometry_atom", ("element",)),
        ("reaction_atom_map_pair", ("element", "ts_element")),
    ):
        predicate = " OR ".join(
            f"btrim({column}) <> {_canonical(column)}" for column in columns
        )
        remaining = connection.exec_driver_sql(
            f"SELECT count(*) FROM {table} WHERE {predicate}"
        ).scalar()
        if remaining:
            raise RuntimeError(
                f"b4e7c1d20f83: {remaining} row(s) of {table} still hold a "
                "non-canonical element symbol after the backfill. The "
                "migration refuses to leave the table half-canonical, "
                "because the code that ships with it stops normalising at "
                "comparison time."
            )


def downgrade() -> None:
    """Deliberately a no-op, and the reason is not squeamishness.

    The upgrade is lossy in the only direction that matters: ``Cl`` does
    not record whether the file it came from wrote ``Cl``, ``CL`` or
    ``cl``, and nothing else in the database remembers per row either.
    ``geometry.xyz_text`` keeps the deposited spelling, but it is
    deliberately not the column being rewritten, and recovering the old
    casing through it means re-parsing every geometry and would silently
    do nothing wherever ``xyz_text`` is NULL.

    Re-deriving the old casing was therefore considered and rejected on
    its merits as well as its cost: it would restore the exact defect this
    revision exists to remove, during a downgrade that is normally run to
    get *back* to a working state. Leaving the canonical form in place is
    forward-compatible -- every pre-upgrade code path normalised at
    comparison time, so canonical rows are precisely what it already
    handled correctly.

    Nothing structural was added, so there is nothing structural to drop.
    """
