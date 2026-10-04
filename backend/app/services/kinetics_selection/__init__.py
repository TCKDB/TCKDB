"""Kinetics selection (service layer): which stored rates answer a requested gas-phase rate coefficient.

See :mod:`app.services.kinetics_selection.assessment` for the applicability semantics,
:mod:`~app.services.kinetics_selection.grouping` for determinations and
:mod:`~app.services.kinetics_selection.service` for the entry point. The graph kernel (conflicts,
fronts, outcomes) is shared with the thermo selector in :mod:`app.services.selection_kernel`.
Nothing here is wired to a route yet, and nothing here persists a selection.
"""

from app.services.kinetics_selection.models import (
    MAX_CANDIDATES,
    POLICY_NAME,
    POLICY_VERSION,
    ColliderRequest,
    KineticsAssessment,
    KineticsAssessmentResult,
    KineticsRequest,
    PressureKind,
    PressureRequest,
    TargetRequest,
)
from app.services.kinetics_selection.service import assess_reaction_entry_kinetics

__all__ = [
    "MAX_CANDIDATES",
    "POLICY_NAME",
    "POLICY_VERSION",
    "ColliderRequest",
    "KineticsAssessment",
    "KineticsAssessmentResult",
    "KineticsRequest",
    "PressureKind",
    "PressureRequest",
    "TargetRequest",
    "assess_reaction_entry_kinetics",
]
