"""Make IRC evidence name the geometry its atom indices count into.

``c1d2e3f4a5b6`` gave ``transition_state_validation_evidence`` two JSONB
mappings — ``reactant_participant_mapping`` and
``product_participant_mapping`` — whose values are saddle-point atom indices:
"these atoms of the transition state become reactant 1". It did not record
which saddle-point *geometry* those indices count into.

An atom index is not a property of a transition state. ``geometry_atom
.atom_index`` counts into one specific set of coordinates, and a
``transition_state_entry`` can accumulate several geometries — re-optimised,
recomputed at another level of theory — with nothing guaranteeing a later one
lists its atoms in the same order. So "atom 3" with no geometry beside it does
not identify an atom; it identifies a position in an ordering the reader has to
infer, and an inference that lands on the wrong geometry silently means a
different atom. That is the failure mode ADR 0011 calls "the hard part:
indices relative to what", and it resolved it for ``reaction_atom_map`` by
making the map name its geometries explicitly rather than by making one
derivable.

The two surfaces make the *same* claim in the *same* units: a
``reaction_atom_map_pair``'s ``ts_atom_index`` and this table's mapping values
both index the saddle point, and
``app.services.reaction_atom_map.validate_atom_map_agrees_with_irc_evidence``
already holds them against each other. Comparing two index sets is only
meaningful once both say what they counted in, so this closes a gap in a check
that already shipped, not only in a projection.

What the column does not do
---------------------------
It carries no composite foreign key into ``geometry_atom``, unlike
``reaction_atom_map_pair``'s two. The indices live inside JSONB rather than in
a column, so there is nothing for a foreign key to point at; naming the
geometry is the half that can be enforced declaratively. The bounds and
element halves are unchanged and still run in
``validate_ts_evidence_participant_composition`` against this same geometry.

Nullable, with the rule carried by a CHECK
------------------------------------------
The mappings are optional: evidence may be a ``rationale`` and a ``passed``
flag with no per-atom partition, and such a row has no indices and needs no
geometry. Requiring one there would demand data the record does not use. What
cannot be allowed is the unreadable combination, so
``ck_transition_state_validation_evidence_mapping_names_geometry`` requires a
geometry exactly when a mapping is present.

"Present" is tested with ``jsonb_typeof``, not with ``IS NOT NULL``.
SQLAlchemy's JSONB type persists a Python ``None`` as JSON ``null`` rather
than as SQL NULL, so this column already holds both spellings of absence
depending on whether the writer set the attribute to ``None`` or left it
unset. A constraint reading only ``IS NULL`` would refuse every mapping-free
row the service writes, and a backfill reading only ``IS NOT NULL`` would try
to ``jsonb_each`` a JSON scalar and raise.

Backfill, and the rows it deliberately declines to guess at
-----------------------------------------------------------
Existing mapped rows are resolved from the deposit paths' own construction:
every calculation of a transition-state entry is linked to the saddle-point
geometry through ``calculation_output_geometry`` (``persist_ts_calculations``
passes one ``geometry_id`` to the primary opt and to every additional
calculation), so an entry whose calculations reference exactly one distinct
output geometry names that geometry unambiguously. Two further conditions
apply before a row is written: the geometry's ``natoms`` must equal the number
of distinct saddle-point indices the mapping actually uses, and the row must
not already name a geometry. A row failing any of them is left NULL rather
than filled with the most likely candidate — a wrong geometry here is exactly
the silent atom substitution the column exists to prevent, and ADR 0011 is
explicit that a mechanism claim is not a thing to guess at.

The CHECK is therefore added ``NOT VALID`` and validated only if the backfill
left nothing unresolved. On an empty or fully-resolvable database — every
test database, and any deployment whose evidence was written by the three
current paths — it ends up validated and indistinguishable from a plain
CHECK. On one holding a mapped row whose geometry cannot be derived, the
constraint still governs every insert and update from here on while the
legacy row is left alone and reported, rather than the upgrade aborting on
data nobody can reconstruct.

Ordering against ``a1f6c3e9b527``
---------------------------------
That revision brings this table under the accepted-science immutability
regime, whose ``BEFORE UPDATE`` guard would refuse the backfill above on any
row under an approved transition-state entry. It is therefore a child of this
one, and the two must not be reordered or merged.

``downgrade()`` drops the constraint and the column. It loses the recorded
geometry bindings, which cannot be recovered from what remains — the same
information loss the pre-upgrade schema had by construction.

Revision ID: f3b7d2c8a419
Revises: c8e2a7d41b96
Create Date: 2026-08-11
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "f3b7d2c8a419"
down_revision: Union[str, Sequence[str], None] = "c8e2a7d41b96"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLE = "transition_state_validation_evidence"
_CONSTRAINT = "ck_transition_state_validation_evidence_mapping_names_geometry"

#: "Absent" has two spellings in these columns. SQLAlchemy's JSONB type
#: persists a Python ``None`` as JSON ``null``, so a row written by
#: ``persist_transition_state_validation_evidence`` with no mapping holds
#: ``'null'::jsonb``; a row whose attribute was never set holds SQL NULL,
#: because the column is then omitted from the INSERT altogether. Both mean
#: the record partitions no atoms. Every predicate below goes through
#: ``jsonb_typeof`` so neither spelling is mistaken for a real mapping -- a
#: plain ``IS NOT NULL`` would treat ``'null'::jsonb`` as one, and
#: ``jsonb_each`` on a JSON scalar raises rather than returning nothing.
def _has_mapping(prefix: str = "") -> str:
    """Predicate for a row carrying at least one real participant mapping.

    :param prefix: Table alias and dot, where more than one table is in scope.

    The two sides are tested independently rather than assuming the wire
    schema's pairing, so a half-populated legacy row still counts as having
    indices and still needs a geometry.

    This is the exact negation of the CHECK's "absent" arm, deliberately: the
    two must partition every row between them. Were "present" written as
    ``= 'object'`` instead, a mapping holding some other JSON type would be
    absent to this predicate and present to the constraint, so it would be
    neither backfilled nor counted as unresolved -- and ``VALIDATE CONSTRAINT``
    would then run and abort the upgrade on it. Written as a negation, such a
    row lands in the unresolved count and the constraint simply stays
    ``NOT VALID``, which is the failure this revision is built to prefer.
    """

    return (
        f"(coalesce(jsonb_typeof({prefix}reactant_participant_mapping), 'null') <> 'null' "
        f"OR coalesce(jsonb_typeof({prefix}product_participant_mapping), 'null') <> 'null')"
    )


_HAS_MAPPING = _has_mapping()
_EVIDENCE_HAS_MAPPING = _has_mapping("evidence.")

#: Distinct saddle-point indices a row's mappings actually use, across both
#: sides. Compared against ``geometry.natoms`` so a candidate geometry that
#: cannot be what the indices were counted in is rejected rather than assumed.
#: The ``CASE`` normalises both spellings of absence, and a one-sided legacy
#: row, to an empty object that contributes no indices.
_MAPPED_INDEX_COUNT = """
    SELECT count(DISTINCT element.value::bigint)
    FROM (
        SELECT evidence.reactant_participant_mapping AS mapping
        UNION ALL
        SELECT evidence.product_participant_mapping
    ) AS side
    CROSS JOIN LATERAL jsonb_each(
        CASE WHEN jsonb_typeof(side.mapping) = 'object'
             THEN side.mapping ELSE '{}'::jsonb END
    ) AS mapped(participant_key, atom_indices)
    CROSS JOIN LATERAL jsonb_array_elements_text(mapped.atom_indices)
        AS element(value)
"""

_BACKFILL = f"""
UPDATE {_TABLE} AS target
SET transition_state_geometry_id = resolved.geometry_id
FROM (
    SELECT
        evidence.id AS evidence_id,
        min(output.geometry_id) AS geometry_id
    FROM {_TABLE} AS evidence
    JOIN calculation
        ON calculation.transition_state_entry_id
           = evidence.transition_state_entry_id
    JOIN calculation_output_geometry AS output
        ON output.calculation_id = calculation.id
    JOIN geometry ON geometry.id = output.geometry_id
    WHERE evidence.transition_state_geometry_id IS NULL
      AND {_EVIDENCE_HAS_MAPPING}
    GROUP BY evidence.id
    HAVING count(DISTINCT output.geometry_id) = 1
       AND min(geometry.natoms) = ({_MAPPED_INDEX_COUNT})
) AS resolved
WHERE target.id = resolved.evidence_id
  AND target.transition_state_geometry_id IS NULL
"""

_VALIDATE_IF_CLEAN = f"""
DO $$
DECLARE
    unresolved bigint;
BEGIN
    SELECT count(*) INTO unresolved
    FROM {_TABLE}
    WHERE {_HAS_MAPPING}
      AND transition_state_geometry_id IS NULL;

    IF unresolved = 0 THEN
        ALTER TABLE public.{_TABLE} VALIDATE CONSTRAINT {_CONSTRAINT};
    ELSE
        RAISE WARNING
            'transition_state_validation_evidence: % row(s) carry participant '
            'mappings whose saddle-point geometry could not be derived, so '
            '{_CONSTRAINT} stays NOT VALID. It governs every insert and update '
            'from here on. To finish, set transition_state_geometry_id on those '
            'rows from the geometry their indices were counted in, then run '
            'ALTER TABLE public.{_TABLE} VALIDATE CONSTRAINT {_CONSTRAINT};',
            unresolved;
    END IF;
END
$$
"""


def upgrade() -> None:
    """Add the geometry binding, backfill what is derivable, then constrain."""

    op.add_column(
        _TABLE,
        sa.Column("transition_state_geometry_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_ts_validation_evidence_ts_geometry",
        _TABLE,
        "geometry",
        ["transition_state_geometry_id"],
        ["id"],
        deferrable=True,
        initially="IMMEDIATE",
    )
    op.execute(_BACKFILL)
    op.execute(
        f"""
        ALTER TABLE public.{_TABLE}
        ADD CONSTRAINT {_CONSTRAINT}
        CHECK (
            (coalesce(jsonb_typeof(reactant_participant_mapping), 'null') = 'null'
             AND coalesce(jsonb_typeof(product_participant_mapping), 'null') = 'null')
            OR transition_state_geometry_id IS NOT NULL
        ) NOT VALID
        """
    )
    op.execute(_VALIDATE_IF_CLEAN)


def downgrade() -> None:
    """Drop the constraint and the column, restoring the unbound mappings."""

    op.execute(f"ALTER TABLE public.{_TABLE} DROP CONSTRAINT IF EXISTS {_CONSTRAINT}")
    op.drop_constraint(
        "fk_ts_validation_evidence_ts_geometry", _TABLE, type_="foreignkey"
    )
    op.drop_column(_TABLE, "transition_state_geometry_id")
