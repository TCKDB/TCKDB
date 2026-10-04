"""Pressure-dependent network selection.

Chunk 2 of the network plan: a bounded, consistent snapshot of one network's authorized population and
a deterministic applicability assessment of its determinations (one channel) or declared product sets
(a requested bundle). Nothing here ranks, chooses or evaluates a rate; the engine, rules and replay are
the next stage and reuse :mod:`app.services.selection_kernel`.
"""

from app.services.network_selection.assessment import assess_bundle_scope, assess_channel_scope, assess_determination
from app.services.network_selection.bounds import (
    CODE_POPULATION_TOO_LARGE,
    CODE_SNAPSHOT_TOO_LARGE,
    check_bound,
    check_snapshot_size,
)
from app.services.network_selection.models import (
    BOUNDS_V1,
    POLICY_NAME,
    POLICY_VERSION,
    BathRequest,
    BundleAssessment,
    DeterminationAssessment,
    NetworkAssessmentResult,
    NetworkRequest,
    OutputRequest,
    PartitionRequest,
    Scope,
    SelectionBounds,
)
from app.services.network_selection.service import assess_network

__all__ = [
    "BOUNDS_V1",
    "CODE_POPULATION_TOO_LARGE",
    "CODE_SNAPSHOT_TOO_LARGE",
    "POLICY_NAME",
    "POLICY_VERSION",
    "BathRequest",
    "BundleAssessment",
    "DeterminationAssessment",
    "NetworkAssessmentResult",
    "NetworkRequest",
    "OutputRequest",
    "PartitionRequest",
    "Scope",
    "SelectionBounds",
    "assess_bundle_scope",
    "assess_channel_scope",
    "assess_determination",
    "assess_network",
    "check_bound",
    "check_snapshot_size",
]
