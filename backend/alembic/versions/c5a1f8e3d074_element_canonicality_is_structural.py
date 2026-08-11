"""make one spelling of an element symbol an invariant, not a convention

``b4e7c1d20f83`` made ``geometry_atom.element`` canonical -- ``parse_xyz``
capitalises every symbol before it becomes a row, and that revision brought the
older rows into the same form. It said, in its own docstring, exactly what it
had not done:

    It does **not** establish an invariant. No CHECK constraint requires
    ``geometry_atom.element`` to be canonical, so canonicality is a convention
    held by application code, and a restore from an older backup or a bulk
    import can break it at any time.

This revision supplies the constraint. Three CHECKs, and one existing CHECK
tightened because they hold.

Why the constraint could be added after all
-------------------------------------------
``b4e7c1d20f83`` recorded that "a CHECK constraint cannot be added until the
accepted rows are already canonical, which is exactly the thing this revision
refuses to force". That reads as a blocker on rows nobody may rewrite, and it
is not one. It conflates two different operations on an accepted row:

* ``UPDATE geometry_atom SET element = ...`` is **DML**. It fires
  ``trg_as_geometry_atom``, which refuses it with ``accepted calculation
  record N is immutable``. That is what ``b4e7c1d20f83`` ran into, correctly.
* ``ALTER TABLE ... VALIDATE CONSTRAINT`` is **DDL**. It reads every row and
  writes none, so a row-level DML trigger never fires.

Measured on a scratch database at ``e2c9a4f7b163`` holding a ``geometry_atom``
row inside an approved calculation, with the guard proved armed on the very
same rows (an ordinary ``UPDATE`` to them raises ``accepted calculation record
N is immutable``):

* canonical accepted row -- ``ADD CONSTRAINT ... NOT VALID`` succeeds,
  ``VALIDATE CONSTRAINT`` succeeds, ``pg_constraint.convalidated`` becomes
  ``t``. The guard does not fire.
* a *non-canonical* accepted row present -- ``VALIDATE`` fails with ``check
  constraint ... is violated by some row``. A **data** error, naming the
  defect, never the guard's immutability message.

So the constraint can be established without touching a single accepted row,
and a database that genuinely holds a non-canonical symbol inside approved
science reports that as the data problem it is. That report is the correct
outcome and must not be worked around: the repair-declaration mechanism added
by ``e2c9a4f7b163`` exists for repairs that are demonstrably non-scientific,
and rewriting a stored element symbol to satisfy a constraint added in the same
breath is not one of those. An operator who meets this failure has stored
science whose spelling nobody has ever verified, and deciding what to do about
it is their call.

``NOT VALID`` then ``VALIDATE``, deliberately in two steps
-----------------------------------------------------------
Not to dodge anything -- the measurement above shows a single validating ``ADD
CONSTRAINT`` would also have worked. It is for the lock. ``ADD CONSTRAINT ...
NOT VALID`` takes ``ACCESS EXCLUSIVE`` for the catalogue write alone and
returns immediately; ``VALIDATE CONSTRAINT`` takes only ``SHARE UPDATE
EXCLUSIVE`` and lets readers and writers through while it scans. A single
validating ``ADD CONSTRAINT`` holds ``ACCESS EXCLUSIVE`` for the whole scan --
46,566 rows on the deployed database, on hardware where that is not free.

What the CHECK says
-------------------
``btrim(element) ~ '^[A-Z][a-z]?$'`` -- one or two characters, first a capital
letter, second (if present) a lower-case letter. That is the definition of an
element symbol, and it is exactly the output shape of
``normalize_element_symbol`` (``str.strip().capitalize()``) for every token
``parse_xyz`` can hand it, so no row the ingestion path can write is refused.

``btrim`` is load-bearing and was found the hard way. ``element`` is
``character(2)``, so a one-letter symbol is stored blank-padded as ``'C '``.
Comparison operators on ``bpchar`` ignore trailing blanks, but the regex
operator does **not**: measured, ``element ~ '^[A-Z][a-z]?$'`` is *false* for
every one-letter symbol in the table, and true only for ``Cl``. Without the
``btrim`` this CHECK would refuse ``H``, ``C``, ``O``, ``N``, ``S`` and ``F``
-- 46,180 of the 46,566 rows on the deployed database -- while reading as
though it accepted them. ``b4e7c1d20f83`` already ``btrim``s for the same
reason.

Beyond capitalisation the pattern also refuses a symbol containing anything
that is not a letter (``1h``, ``@c``, ``C:``). That is deliberate rather than
incidental scope: ``character(2)`` already states "at most two characters", and
the pattern completes the same sentence. A value that is not an element symbol
cannot be compared to anything *as* an element, and this column exists to be
compared -- it is the target of ``reaction_atom_map_pair``'s two composite
foreign keys and the value every element comparison in the service layer reads.
Under ADR 0008 that makes it definitional and therefore fit to block.

The other two CHECKs, and why the foreign key is not enough
------------------------------------------------------------
``reaction_atom_map_pair`` carries ``element`` and ``ts_element`` into two
composite foreign keys onto ``geometry_atom (geometry_id, atom_index,
element)``. It is tempting to argue that those foreign keys already make both
columns canonical for free, since the value they point at now has to be -- and
that argument fails on precisely the path this revision exists to defend
against.

A foreign key in PostgreSQL is implemented as a system trigger. ``SET
session_replication_role = replica`` suspends system triggers and leaves CHECK
constraints armed; it is what bulk loaders and restore paths run under, and
this repository's own ``tests/api/test_api_database_constraint_codes.py``
relies on that split. Measured at ``e2c9a4f7b163``:

    normal session   -- a pair row naming an atom that does not exist:
                        refused, ForeignKeyViolation
    replica session  -- the same row:                        **accepted**
    replica session  -- a pair row mapping ``C`` onto ``N``: refused,
                        CheckViolation on ``ck_..._element_matches``

So under a bulk load the foreign keys hold nothing and the CHECKs hold
everything. Since the whole point of this revision is that bulk imports and
restores bypass application code, the two element columns get their own CHECK
rather than inheriting one through a trigger that the bulk path turns off.
``reaction_atom_map_pair`` is **empty** on the deployed database, so this costs
nothing today and would cost a backfill later.

Tightening ``ck_reaction_atom_map_pair_element_matches``
---------------------------------------------------------
It was ``upper(element) = upper(ts_element)``. The ``upper()`` was there
because the two ends quote two different geometries and nothing guaranteed the
two spelled an element the same way: ``Cl`` becoming ``CL`` is one program
shouting where another did not, and refusing that map would have refused
correct chemistry.

Both sides are now canonical unconditionally -- by their own CHECK, not by way
of the foreign keys -- so two atoms of the same element carry byte-identical
values on every path, including a bulk load. The ``upper()`` can only be a
no-op, and dropping it removes a case-blindness that would otherwise quietly
accept a pair of non-canonical spellings if either CHECK were ever removed.
Rewritten as plain ``element = ts_element``.

The two columns stay. See ``app.db.models.reaction_atom_map`` for the full
argument: their original reason is gone, and the reason they have now is that
this rule is a CHECK, and a CHECK is the only form of it that survives the
bulk-load path measured above. Collapsing them onto one column would make an
element change unrepresentable in a normal session and unpoliced in a replica
one, and would take the rule's name off the error a client is handed.

What is deliberately NOT constrained
-------------------------------------
``energy_correction_scheme_atom_param.element``, for the reasons
``b4e7c1d20f83`` gives: it is ``text``, it is half of a primary key, and it is
a *reference library* key rather than a parsed index. Nothing joins it to
``geometry_atom``. Constraining a primary-key column is also not the same
operation -- it needs a merge policy, not a CHECK.

``geometry.xyz_text``, for the same reason it was not rewritten:
``geom_hash`` is a sha256 over it and a citable public ref, so the deposited
spelling has to survive verbatim. ``geometry.xyz_text`` may read ``CL`` for an
atom whose ``geometry_atom.element`` reads ``Cl``, and that divergence is the
design.

Downgrade
---------
Drops the three CHECKs and puts ``upper()`` back. No row is read or written in
either direction -- both halves are pure DDL -- so neither one can interact
with ``trg_as_geometry_atom`` or the atom-map guards, and neither can fail on
data.

Revision ID: c5a1f8e3d074
Revises: e2c9a4f7b163
Create Date: 2026-08-11

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c5a1f8e3d074"
down_revision: Union[str, Sequence[str], None] = "e2c9a4f7b163"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: An element symbol: one capital letter, optionally followed by one lower-case
#: letter. ``btrim`` because the columns are ``character(2)`` and the regex
#: operator, unlike the comparison operators, sees the blank padding.
def _canonical(column: str) -> str:
    return f"btrim({column}) ~ '^[A-Z][a-z]?$'"


#: ``(table, constraint name, predicate)`` for the three canonicality CHECKs.
_CANONICALITY_CHECKS = (
    ("geometry_atom", "ck_geometry_atom_element_canonical", "element"),
    (
        "reaction_atom_map_pair",
        "ck_reaction_atom_map_pair_element_canonical",
        "element",
    ),
    (
        "reaction_atom_map_pair",
        "ck_reaction_atom_map_pair_ts_element_canonical",
        "ts_element",
    ),
)

_ELEMENT_MATCHES = "ck_reaction_atom_map_pair_element_matches"


def _add_validated(table: str, name: str, predicate: str) -> None:
    """Add a CHECK ``NOT VALID``, then validate it in a second statement.

    Two statements rather than one so the exclusive lock covers the catalogue
    write only and the row scan runs under ``SHARE UPDATE EXCLUSIVE``. See the
    module docstring.
    """

    op.execute(
        f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({predicate}) NOT VALID"
    )
    op.execute(f"ALTER TABLE {table} VALIDATE CONSTRAINT {name}")


def upgrade() -> None:
    for table, name, column in _CANONICALITY_CHECKS:
        _add_validated(table, name, _canonical(column))

    # Only now, with both element columns canonical on every write path, is
    # case-blindness safe to drop: two atoms of the same element can no longer
    # carry two different strings.
    op.execute(f"ALTER TABLE reaction_atom_map_pair DROP CONSTRAINT {_ELEMENT_MATCHES}")
    _add_validated(
        "reaction_atom_map_pair", _ELEMENT_MATCHES, "element = ts_element"
    )


def downgrade() -> None:
    op.execute(f"ALTER TABLE reaction_atom_map_pair DROP CONSTRAINT {_ELEMENT_MATCHES}")
    op.execute(
        f"ALTER TABLE reaction_atom_map_pair ADD CONSTRAINT {_ELEMENT_MATCHES} "
        "CHECK (upper(element) = upper(ts_element))"
    )
    for table, name, _column in reversed(_CANONICALITY_CHECKS):
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {name}")
