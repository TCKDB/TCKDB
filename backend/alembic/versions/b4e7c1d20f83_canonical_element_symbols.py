"""store one spelling of an element symbol, not three

``parse_xyz`` used to put ``parts[0]`` into ``geometry_atom.element``
exactly as the depositor's file wrote it. Electronic-structure codes do
not agree about capitalisation, so a ``character(2)`` column that every
element comparison in the codebase reads could hold ``Cl``, ``CL``,
``cl`` and ``bR`` for one element. Several sites normalise at comparison
time to cope with that, and the class of bug they cope with had already
shipped twice: a saddle point written ``CL`` contradicted its own
reaction, and a mixed-case deposit was accepted at the blocking tier while
``calc_geometry_validation`` recorded ``fail`` on the same bytes.

``parse_xyz`` now canonicalises the symbol before it becomes a row
(``normalize_element_symbol`` -- first character upper, rest lower, and
nothing else). This revision brings the rows written before it into the
same form.

What that does and does not buy
-------------------------------
It makes the common case clean: one spelling per element in the column
joins and comparisons run against. It does **not** establish an invariant.
No CHECK constraint requires ``geometry_atom.element`` to be canonical, so
canonicality is a convention held by application code, and a restore from
an older backup or a bulk import can break it at any time. The
comparison-time normalisation therefore **stays** everywhere it was --
notably the blocking element-conservation check in
``app.services.reaction_atom_map``, which has to be correct on rows the
running process never wrote. Removing it would trade a tidy column for a
false refusal on correct chemistry, which ADR 0008 puts out of bounds for
a blocking check. On canonical input it is a no-op and costs nothing.

Making canonicality structural is a separate question with a separate
cost: a CHECK constraint cannot be added until the accepted rows are
already canonical, which is exactly the thing this revision refuses to
force (below). It can be its own task.

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

Why the accepted-science guard is left standing
-----------------------------------------------
``trg_as_geometry_atom`` (from ``c6f2a9d4e7b1``) refuses any UPDATE to a
``geometry_atom`` row whose geometry is attached to an **accepted**
calculation. This revision does **not** disable it, and deliberately does
not: it is a scientific-integrity control, and a migration that stands one
down mutates approved science without anybody being asked.

Measured on the deployed database before writing this, read-only:
``geometry_atom`` holds 46,566 rows over 3,653 geometries -- C 14368,
H 28844, O 2415, S 331, N 222, Cl 202, F 184 -- and **every one is already
canonical**. ``reaction_atom_map_pair`` holds 0 rows.
``geometry.xyz_text`` is non-NULL everywhere. So on the database this was
written for, the backfill below matches nothing, updates nothing, and the
guard never fires. Standing a control down to repair rows that do not
exist is not a trade-off; it is a straight loss.

What happens on a database that *does* hold a non-canonical accepted row:
the UPDATE trips the guard and the migration fails, loudly, with the
guard's own ``accepted calculation record N is immutable`` message. That
is the correct outcome rather than a shortcoming. The decision to rewrite
a value inside an approved record belongs to the operator who has one, not
to this file -- and nothing downstream depends on the rewrite happening,
because the code that ships with this revision still normalises at
comparison time (see ``app.services.reaction_atom_map``). Canonical
storage is the common case made clean, not an invariant anything blocking
relies on.

The upgrade re-checks its own work before returning and raises if any
non-canonical row survives, so a partial backfill -- for any reason, guard
or otherwise -- fails the migration instead of silently half-applying it.

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


def upgrade() -> None:
    element = _canonical("element")
    ts_element = _canonical("ts_element")

    # Both foreign keys are DEFERRABLE INITIALLY IMMEDIATE. Deferring them here
    # holds the referential check until COMMIT, which is the only way the
    # parent and the child can be rewritten in one step -- see the module
    # docstring for why no ordering of undeferred updates works.
    op.execute(f"SET CONSTRAINTS {', '.join(_GEOMETRY_ATOM_FKS)} DEFERRED")

    # `trg_as_geometry_atom` is left enabled on purpose. If a row here belongs
    # to an accepted calculation, this statement fails with the guard's own
    # message and the whole revision rolls back -- see the module docstring.
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

    # Back to IMMEDIATE, which runs the referential check here, against the
    # finished state. Two reasons it is worth an explicit statement rather than
    # letting COMMIT do it:
    #
    # * Failure mode. A violation is reported at this statement, with this
    #   revision's own frame on the traceback, instead of surfacing from an
    #   Alembic COMMIT with no indication of which migration produced it.
    # * Pending trigger events are not free. A deferred constraint leaves them
    #   queued on `geometry_atom`, and PostgreSQL refuses `ALTER TABLE` on a
    #   table that has any ("cannot ALTER TABLE because it has pending trigger
    #   events"). Nothing here alters the table any more, but a follow-up
    #   revision that stacks onto this one would hit it, and draining the queue
    #   at a known point is what makes the deferral local to these two
    #   statements rather than to the whole transaction.
    op.execute(f"SET CONSTRAINTS {', '.join(_GEOMETRY_ATOM_FKS)} IMMEDIATE")

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
                "non-canonical element symbol after the backfill. An UPDATE "
                "that matched fewer rows than it selected means something "
                "silently refused part of the rewrite, and a half-canonical "
                "table is a worse state to leave behind than an untouched "
                "one. Nothing is broken by rolling back: comparisons still "
                "normalise, so non-canonical rows read correctly either way."
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
