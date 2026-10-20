"""Structure selection (service layer): which stored energies, basins and saddles answer a stated question.

See :mod:`app.services.structure_selection.assessment` for the eligibility semantics,
:mod:`~app.services.structure_selection.normalizer` for what makes two recipes comparable and
:mod:`~app.services.structure_selection.service` for the entry point. The graph kernel (conflicts, fronts,
outcomes) is shared with the thermo, kinetics and network selectors in :mod:`app.services.selection_kernel`.
Decision and replay are here too; nothing is wired to a route yet, and nothing here persists a selection.
"""

from app.services.structure_selection.decision import DECISION_VERSION, StructureDecision, decide_structures
from app.services.structure_selection.manifest import (
    ReplayError,
    replay_matches,
    replay_structure_assessment,
    replay_structure_decision,
)
from app.services.structure_selection.models import (
    ASSESSMENT_VERSION,
    NORMALIZER_VERSION,
    AdminPolicy,
    CoverageRequirement,
    Grain,
    Intent,
    Quantity,
    ResultMode,
    SelectionBounds,
    StructureAssessment,
    StructureAssessmentResult,
    StructureRequest,
    ValidationClaim,
)
from app.services.structure_selection.selection import StructureSelection, select_entry_structures
from app.services.structure_selection.service import assess_entry_structures

__all__ = [
    "ASSESSMENT_VERSION",
    "DECISION_VERSION",
    "NORMALIZER_VERSION",
    "AdminPolicy",
    "CoverageRequirement",
    "Grain",
    "Intent",
    "Quantity",
    "ReplayError",
    "ResultMode",
    "SelectionBounds",
    "StructureAssessment",
    "StructureAssessmentResult",
    "StructureDecision",
    "StructureRequest",
    "StructureSelection",
    "ValidationClaim",
    "assess_entry_structures",
    "decide_structures",
    "replay_matches",
    "replay_structure_assessment",
    "replay_structure_decision",
    "select_entry_structures",
]
