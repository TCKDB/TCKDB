"""Link an SCF stability verdict to the job that measured it, by local key.

A bundle's ``scf_stability`` block hangs off one calculation. When that
verdict was measured by a *different* job (ARC's stability analysis is its
own job, run on the optimisation's wavefunction), the block names that job
with ``source_calculation_key``. The database column it lands in,
``calc_scf_stability.source_calculation_id``, has always existed and the
read API already returns it as ``source_calculation_ref``; what was missing
was a way for a bundle to fill it without knowing a database id.

Why this is a second pass and not part of persisting the block
---------------------------------------------------------------
The block is written while its own calculation is being persisted, and the
job it names may be declared later in the same payload, so it does not exist
yet. The pass therefore runs once every calculation in the bundle has been
persisted, the same point at which ``depends_on`` edges are wired.

No new calculation type
-----------------------
The measuring job keeps the type it really has (an ``sp`` or ``freq`` whose
log carries the stability analysis). A stability calculation type would add a
Postgres enum value, a migration and a third way to say the same thing, for a
verdict that is already a typed row attached to a calculation.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from sqlalchemy.orm import Session
from tckdb_schemas.fragments.calculation import SCFStabilityContent

from app.db.models.calculation import Calculation, CalculationSCFStability
from app.services.local_key_resolution import resolve_calculation_key


def link_scf_stability_sources(
    session: Session,
    declared: Iterable[tuple[str, SCFStabilityContent | None]],
    calculations_by_key: Mapping[str, Calculation],
) -> int:
    """Set ``source_calculation_id`` on each stability row that names a source key.

    :param session: Active SQLAlchemy session.
    :param declared: ``(calculation_key, scf_stability block)`` for every
        calculation in the bundle; blocks that are absent or name no source
        are skipped.
    :param calculations_by_key: The bundle's calculation-key namespace, which
        must already hold every persisted calculation.
    :returns: How many stability rows were linked.
    :raises CodedValueError: a key names no declared calculation.
    :raises ValueError: the named job belongs to a different species entry or
        transition state than the calculation carrying the verdict. The
        request schemas refuse this first; this is the layer that knows which
        entry each key resolved to.
    """
    linked = 0
    for owner_key, stability in declared:
        if stability is None or stability.source_calculation_key is None:
            continue
        field = f"calculations['{owner_key}'].scf_stability.source_calculation_key"
        owner = resolve_calculation_key(
            owner_key, calculations_by_key, field=f"calculations['{owner_key}'].key"
        )
        source = resolve_calculation_key(
            stability.source_calculation_key, calculations_by_key, field=field
        )
        if (
            source.species_entry_id != owner.species_entry_id
            or source.transition_state_entry_id != owner.transition_state_entry_id
        ):
            raise ValueError(
                f"{field}='{stability.source_calculation_key}' names a "
                f"calculation that belongs to a different species entry or "
                f"transition state than '{owner_key}'; the job that measured "
                f"a stability verdict must be one of the same subject's own."
            )
        row = session.get(CalculationSCFStability, owner.id)
        if row is None:
            # The block was declared, so its row was written when the
            # calculation was persisted. Its absence is a defect here, not
            # a depositor mistake, and must not pass as "nothing to link".
            raise RuntimeError(
                f"calculation '{owner_key}' declared an scf_stability block "
                f"but no calc_scf_stability row exists to link."
            )
        row.source_calculation_id = source.id
        linked += 1
    if linked:
        session.flush()
    return linked
