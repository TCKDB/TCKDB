"""The composite annotations every ``ScientificLevelsSummary`` carries (ADR 0021, P7a).

Four builders (thermo, statmech, kinetics, and the shared summary they feed) derive a
record's levels. Two of the fields of that summary are not a function of the levels
alone, so the rule for each lives here once, in plain functions the builders call, and
``tests/invariants/test_levels_summary_notation_and_verification.py`` fails a builder
that does not pass them:

* ``composite_energy_verification`` -- how far the record's composite energy has been
  checked. A record can link several composite calculations at one level; the answer is
  the *least* verified of them, so a disagreement in the second is never hidden by a
  clean first.
* ``legacy_composite_shape`` -- an annotation for the shapes depositors used before the
  ``composite`` calculation type existed. Annotation only: it never changes a derived
  level (the P3a review pinned that).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from app.db.models.common import CompositeEnergyVerificationState, LegacyCompositeShape
from app.schemas.reads.scientific_common import CompositeEnergyVerification, LevelOfTheorySummary

__all__ = ["legacy_composite_shape", "record_composite_verification"]

#: Worst first. ``recomputed`` and ``log_reconciled`` are both "confirmed" and tie.
_SEVERITY: dict[CompositeEnergyVerificationState, int] = {
    CompositeEnergyVerificationState.recompute_mismatch: 0,
    CompositeEnergyVerificationState.unverifiable: 1,
    CompositeEnergyVerificationState.program_reported: 2,
    CompositeEnergyVerificationState.recomputed: 3,
    CompositeEnergyVerificationState.log_reconciled: 3,
}


def record_composite_verification(
    *,
    energy_source: str | None,
    typed_composite_ids: Sequence[int],
    verifications: Mapping[int, CompositeEnergyVerification],
) -> CompositeEnergyVerification | None:
    """The verification a record reports for its composite energy.

    :param energy_source: The record's derived ``energy_source``.
    :param typed_composite_ids: The calculations of type ``composite`` the record links
        as energy, lowest id first.
    :param verifications: ``{calculation id: verification}`` for those calculations.
    :returns: ``None`` unless the energy came from a composite
        (``energy_source == "composite"``) and at least one of the linked composites has a
        verification; otherwise the least verified of them (the first on a tie).
    """
    if energy_source != "composite":
        return None
    found = [verifications[cid] for cid in typed_composite_ids if cid in verifications]
    if not found:
        return None
    return min(found, key=lambda verification: _SEVERITY[verification.state])


def legacy_composite_shape(
    *,
    composite_role_on_non_composite: bool,
    geometry: LevelOfTheorySummary | None,
    geometry_source: str | None,
    frequency: LevelOfTheorySummary | None,
    frequency_source: str | None,
    energy: LevelOfTheorySummary | None,
    energy_source: str | None,
) -> LegacyCompositeShape | None:
    """Which legacy composite shape a record has, if any.

    :param composite_role_on_non_composite: A calculation whose type is not ``composite`` is
        linked under the role ``composite``.
    :param geometry: The derived geometry level and the ``geometry_source`` that answered it.
    :param frequency: The derived frequency level and its source.
    :param energy: The derived energy level and its source.
    :returns: ``composite_role_on_non_composite_calculation`` when the first holds;
        otherwise ``named_method_level_on_non_composite_calculation`` when an ``opt``, ``freq`` or
        ``sp`` answered a level that is bound to a recipe (a catalogued named method such as
        CBS-QB3, which only a composite calculation should sit at); otherwise ``None``. A level
        supplied by a recipe itself (``composite_recipe``) or by a ``composite`` calculation is
        the intended shape and is not annotated.
    """
    if composite_role_on_non_composite:
        return LegacyCompositeShape.composite_role_on_non_composite_calculation
    answered = (
        (geometry, geometry_source, ("opt",)),
        (frequency, frequency_source, ("freq", "opt")),
        (energy, energy_source, ("sp", "opt")),
    )
    for level, source, non_composite_sources in answered:
        if level is not None and level.composite_scheme is not None and source in non_composite_sources:
            return LegacyCompositeShape.named_method_level_on_non_composite_calculation
    return None
