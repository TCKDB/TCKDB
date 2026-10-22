"""The domain-neutral core of selection.

Eligible nodes and an administrative key go in; an outcome and the display order come out. TCKDB carries no
method-versus-method ranking, so there are no preference edges, rules or conflicts here: one eligible candidate is a
sole eligible candidate, several are incomparable alternatives listed in administrative order, none is no applicable
candidate. Nothing here knows what a node *is* (a formation enthalpy, a rate coefficient, a determination): each
selector keeps its own quantity checks, candidate types and serialization, and passes this module only refs and the
keys the administrative order needs.
"""

from app.services.selection_kernel.graph import (
    ADMIN_FIRST_BASIS,
    BASIS_INCOMPARABLE,
    BASIS_NONE_ELIGIBLE,
    BASIS_SOLE,
    AdminNode,
    GraphVerdict,
    decide_alternatives,
    order_admin,
)
from app.services.selection_kernel.vocabulary import Applicability, Outcome

__all__ = [
    "ADMIN_FIRST_BASIS",
    "BASIS_INCOMPARABLE",
    "BASIS_NONE_ELIGIBLE",
    "BASIS_SOLE",
    "AdminNode",
    "Applicability",
    "GraphVerdict",
    "Outcome",
    "decide_alternatives",
    "order_admin",
]
