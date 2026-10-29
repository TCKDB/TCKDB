"""Allene Ra/Sa and odd-cumulene E/Z in the entry stereo descriptor (#827).

The descriptor vocabulary gains two whole tokens, ``Ra`` and ``Sa`` (an allene's axis; ``Ra`` is IUPAC's ``M``
and ``Sa`` is ``P``). ``species_entry`` is a deployed table, so this is a new revision:

1. ``ck_species_entry_stereo_descriptor_vocabulary`` is replaced by one that accepts the alternation
   ``(Ra|Sa|[RSrsEZez?-])`` per token.
2. Data step: every live entry that carries a ``?`` for an allene or an odd cumulene is re-read from the
   geometries linked to it (:mod:`app.services.cumulene_stereo_fill`). Unanimous geometries fill the ``?`` in
   place; entries whose geometries disagree, entries without a geometry and merged-away entries (frozen, never
   touched) are listed and left. A fill that would collide with an existing live entry stops the migration
   with a list. Each change prints as ``entry ref: old -> new``; ``0 changed`` when nothing changes.
   ``species_entry`` has no accepted-science guard, so it is a plain UPDATE (checked at run time).

``downgrade()`` restores the old vocabulary. It **stops with a list** if any entry (merged-away ones included)
holds ``Ra`` or ``Sa``: those rows cannot satisfy the old constraint and are never rewritten silently. Odd-cumulene
``E``/``Z`` fills use letters the old vocabulary already had, so they stay; they read as an ordinary double bond's
tokens to the old code, which is the one thing the downgrade cannot undo.

Revision ID: e5b2c8d1f947
Revises: a7c4e1f9b268
"""

from typing import Sequence, Union

from sqlalchemy import text

from alembic import op

revision: str = "e5b2c8d1f947"
down_revision: Union[str, Sequence[str], None] = "a7c4e1f9b268"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW = "stereo_descriptor IS NULL OR stereo_descriptor ~ '^(Ra|Sa|[RSrsEZez?-])(,(Ra|Sa|[RSrsEZez?-]))*$'"
_OLD = "stereo_descriptor IS NULL OR stereo_descriptor ~ '^[RSrsEZez?-](,[RSrsEZez?-])*$'"


def _swap_constraint(expression: str) -> None:
    op.drop_constraint("stereo_descriptor_vocabulary", "species_entry", type_="check")
    op.create_check_constraint("stereo_descriptor_vocabulary", "species_entry", expression)


def upgrade() -> None:
    _swap_constraint(_NEW)
    # Imported here, not at module level: importing the app from a migration module at load time would run for
    # every alembic command, including ones that never reach this revision.
    from app.services.cumulene_stereo_fill import fill_cumulene_stereo, print_report

    bind = op.get_bind()
    print_report(fill_cumulene_stereo(bind, revision=revision), revision)


def downgrade() -> None:
    bind = op.get_bind()
    holders = bind.execute(
        text(
            "SELECT public_ref, stereo_descriptor, merged_into_species_entry_id IS NOT NULL AS merged "
            "FROM species_entry WHERE stereo_descriptor ~ '(^|,)(Ra|Sa)(,|$)' ORDER BY id"
        )
    ).all()
    if holders:
        listing = "; ".join(f"{r.public_ref} ({r.stereo_descriptor}{', merged' if r.merged else ''})" for r in holders)
        raise RuntimeError(
            f"{len(holders)} species_entry row(s) hold an allene axis token (Ra/Sa), which the previous descriptor "
            f"vocabulary cannot store: {listing}. They are not rewritten silently (a merged-away row is frozen and "
            "cannot be edited at all); resolve them deliberately and re-run."
        )
    _swap_constraint(_OLD)
