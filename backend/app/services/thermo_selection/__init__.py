"""Method-aware selection of a thermo record for formation enthalpy at 298.15 K (service layer).

See :mod:`app.services.thermo_selection.service` for the entry point and the module
docstrings of ``assessment``, ``engine``, ``rules`` and ``manifest`` for the semantics.
Nothing here is wired to a route yet, and nothing here persists a selection.
"""

from app.services.thermo_selection.manifest import ReplayError, replay_decision, replay_matches
from app.services.thermo_selection.models import (
    MAX_CANDIDATES,
    POLICY_NAME,
    POLICY_VERSION,
    Applicability,
    CandidateAssessment,
    H298Request,
    H298Selection,
    Outcome,
)
from app.services.thermo_selection.service import select_h298

__all__ = [
    "MAX_CANDIDATES",
    "POLICY_NAME",
    "POLICY_VERSION",
    "Applicability",
    "CandidateAssessment",
    "H298Request",
    "H298Selection",
    "Outcome",
    "ReplayError",
    "replay_decision",
    "replay_matches",
    "select_h298",
]
