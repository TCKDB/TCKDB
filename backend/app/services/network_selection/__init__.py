"""Pressure-dependent network selection.

Chunk 2: a bounded, consistent snapshot of one network's authorized population and a deterministic applicability
assessment of its determinations (one channel) or declared product sets (a requested bundle). Chunk 3: the
preference engine and the replayable manifest, on the shared :mod:`app.services.selection_kernel` (conflicts, fronts
and outcomes are the kernel's and are not re-implemented here), with two replay levels. No rule is active in this
release, so a decision is an honest statement of which candidates are eligible and why none is ranked.
"""

from app.services.network_selection.assessment import (
    ASSESSMENT_VERSION,
    assess_bundle_scope,
    assess_channel_scope,
    assess_determination,
)
from app.services.network_selection.bounds import (
    CODE_POPULATION_TOO_LARGE,
    CODE_SNAPSHOT_TOO_LARGE,
    check_bound,
    check_snapshot_size,
)
from app.services.network_selection.engine import NetworkDecision, build_candidates, decide
from app.services.network_selection.manifest import (
    ReplayError,
    build_manifest,
    replay_matches,
    replay_network,
    replay_network_assessment,
    replay_network_decision,
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
from app.services.network_selection.rules import NetworkRepresentationRule, NetworkRule, default_rules
from app.services.network_selection.selection import NetworkSelection, select_network
from app.services.network_selection.service import assess_network

__all__ = [
    "ASSESSMENT_VERSION",
    "BOUNDS_V1",
    "CODE_POPULATION_TOO_LARGE",
    "CODE_SNAPSHOT_TOO_LARGE",
    "POLICY_NAME",
    "POLICY_VERSION",
    "BathRequest",
    "BundleAssessment",
    "DeterminationAssessment",
    "NetworkAssessmentResult",
    "NetworkDecision",
    "NetworkRepresentationRule",
    "NetworkRequest",
    "NetworkRule",
    "NetworkSelection",
    "OutputRequest",
    "PartitionRequest",
    "ReplayError",
    "Scope",
    "SelectionBounds",
    "assess_bundle_scope",
    "assess_channel_scope",
    "assess_determination",
    "assess_network",
    "build_candidates",
    "build_manifest",
    "check_bound",
    "check_snapshot_size",
    "decide",
    "default_rules",
    "replay_matches",
    "replay_network",
    "replay_network_assessment",
    "replay_network_decision",
    "select_network",
]
