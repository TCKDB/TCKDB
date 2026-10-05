"""Structure selection (service layer): which stored energies, basins and saddles answer a stated question.

See :mod:`app.services.structure_selection.assessment` for the eligibility semantics,
:mod:`~app.services.structure_selection.normalizer` for what makes two recipes comparable and
:mod:`~app.services.structure_selection.service` for the entry point. The graph kernel (conflicts, fronts,
outcomes) is shared with the thermo, kinetics and network selectors in :mod:`app.services.selection_kernel`.
Nothing here is wired to a route yet, and nothing here persists a selection.
"""

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
from app.services.structure_selection.service import assess_entry_structures

__all__ = [
    "ASSESSMENT_VERSION",
    "NORMALIZER_VERSION",
    "AdminPolicy",
    "CoverageRequirement",
    "Grain",
    "Intent",
    "Quantity",
    "ResultMode",
    "SelectionBounds",
    "StructureAssessment",
    "StructureAssessmentResult",
    "StructureRequest",
    "ValidationClaim",
    "assess_entry_structures",
]
