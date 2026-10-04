"""The domain-neutral core of method-aware selection.

Nodes, attributed preference edges, supersession information and administrative keys go in;
conflicts, preference fronts and an outcome come out. Nothing here knows what a node *is*
(a formation enthalpy, a rate coefficient, a determination): the thermo and kinetics selectors
each keep their own quantity checks, candidate types, rules and serialization, and pass this
module only refs and the keys the administrative order needs.
"""

from app.services.selection_kernel.graph import (
    ADMIN_FIRST_BASIS,
    BASIS_CONFLICT,
    BASIS_INCOMPARABLE,
    BASIS_INCOMPARABLE_NO_RULE,
    BASIS_NONE_ELIGIBLE,
    BASIS_PREFERRED,
    BASIS_SOLE,
    AdminNode,
    GraphVerdict,
    decide_graph,
    fronts,
    order_admin,
    resolve_opposing,
    strongly_connected,
)
from app.services.selection_kernel.vocabulary import Edge, Outcome, RuleMatch, Tri

__all__ = [
    "ADMIN_FIRST_BASIS",
    "BASIS_CONFLICT",
    "BASIS_INCOMPARABLE",
    "BASIS_INCOMPARABLE_NO_RULE",
    "BASIS_NONE_ELIGIBLE",
    "BASIS_PREFERRED",
    "BASIS_SOLE",
    "AdminNode",
    "Edge",
    "GraphVerdict",
    "Outcome",
    "RuleMatch",
    "Tri",
    "decide_graph",
    "fronts",
    "order_admin",
    "resolve_opposing",
    "strongly_connected",
]
