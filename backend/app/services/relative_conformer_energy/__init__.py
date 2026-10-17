"""Relative conformer energies: how far above or below a *named* reference conformer each conformer lies.

The compared unit is the conformer group; its observations are repeats. A missing value is reported absent with a
coded reason, never as zero; the reference is never re-chosen; stored uncertainties are never combined. See
:mod:`~app.services.relative_conformer_energy.compute` for the semantics and
:mod:`~app.services.relative_conformer_energy.service` for the entry point and
:mod:`~app.services.relative_conformer_energy.public` for what the routes call. Nothing here persists anything.
"""

from app.services.relative_conformer_energy.manifest import (
    replay_comparison,
    replay_comparison_matches,
    replay_matches,
    replay_relative_energies,
)
from app.services.relative_conformer_energy.models import (
    RELATIVE_ENERGY_VERSION,
    Certificate,
    EnergyKind,
    LevelSelector,
    RelativeEnergyOutcome,
    RelativeEnergyRequest,
    RelativeEnergyResult,
)
from app.services.relative_conformer_energy.service import (
    ComparisonRun,
    RelativeEnergyRun,
    run_comparison,
    run_relative_energies,
)

__all__ = [
    "RELATIVE_ENERGY_VERSION",
    "Certificate",
    "ComparisonRun",
    "EnergyKind",
    "LevelSelector",
    "RelativeEnergyOutcome",
    "RelativeEnergyRequest",
    "RelativeEnergyResult",
    "RelativeEnergyRun",
    "replay_comparison",
    "replay_comparison_matches",
    "replay_matches",
    "replay_relative_energies",
    "run_comparison",
    "run_relative_energies",
]
