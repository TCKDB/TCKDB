"""The versioned network rule registry, separate from thermo's and from elementary kinetics'.

Nothing transfers automatically: a thermo rule, an elementary-rate rule or an H298 preference says
nothing about a coupled network observable, so this registry imports none of them and no rule of one
registry is ever applied by another. A network rule needs evidence about the *network* observable it
speaks for, and says which of the three comparison objectives it is evidence for:

* ``physical_accuracy``: against appropriate independent observations;
* ``model_fidelity``: fidelity to a pinned physical or master-equation model;
* ``representation_fidelity``: fidelity of a fit to pinned supplied outputs.

Agreement on one objective never establishes another, and a rule is applied only to a request that
states its objective.

Two levels exist. A :class:`NetworkRule` compares *candidates* (a determination, or a declared product
set) within a request; a :class:`NetworkRepresentationRule` compares the alternate *fits* inside one
determination, and only for a ``representation_fidelity`` request: a fit preference never ranks the
underlying physical solve and never becomes independent confirmation.

Three things keep a rule honest, as in the other registries:

* **Unknown is not a vote.** A prerequisite is stated-and-equal (true), stated-and-different (false)
  or unstated (unknown). An edge needs true on its side and true on the compatibility of the pair.
* **A rule says what it is.** Its edges are labelled, and its entry carries its objective, evidence
  limits and, when it is not active, why.
* **A rule is inactive until the owner signs its audited manifest.** An agent never activates one.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from tckdb_schemas.network_declarations import NetworkComparisonObjective

from app.chemistry.network_rules.manifest import (
    NetworkRuleManifest,
    RuleCandidate,
    load_network_rule_manifest,
)
from app.schemas.reads.scientific_common import SelectionPolicy  # noqa: F401  (re-exported for rule authors)
from app.services.network_selection.models import FitFacts, NetworkFacts, NetworkRequest, SolveFacts
from app.services.selection_kernel import AdminNode, RuleMatch, Tri

RULE_ACTIVE = "active"
RULE_INACTIVE = "inactive"
RULE_REVOKED = "revoked"
LEVEL_CANDIDATE = "candidate"
LEVEL_REPRESENTATION = "representation"

#: What an edge from a benchmark rule is, in the manifest and everywhere it is shown.
EDGE_LABEL = "expected-performance inference"


@dataclass(frozen=True)
class MemberFacts:
    """One determination of a candidate: its channel, observable and the eligible fits that represent it."""

    determination_ref: str
    channel_key: str
    observable: dict[str, Any]
    fits: tuple[FitFacts, ...]


@dataclass(frozen=True)
class NetworkCandidate:
    """What a rule may look at: one eligible decision node, its generating solve and its members.

    A channel-scope node has one member (the determination); a bundle node has one member per required
    output answered by a determination. ``node`` is the kernel's administrative view of it.
    """

    node_ref: str
    scope: str
    solve: SolveFacts
    members: tuple[MemberFacts, ...]
    node: AdminNode


class NetworkRule(ABC):
    """One scoped, attributed comparison between two *protocols* of candidates; see the module docstring.

    :cvar rule_id: Stable id. :cvar version: Version of this rule's logic and data.
    :cvar objective: Which of the three comparison objectives the rule is evidence for.
    :cvar objective_key: A short key naming what the rule compares on (a convergence criterion, a reduction);
        edges from rules with different keys are never composed into one preference path. Never empty.
    :cvar supersedes: Ids of rules whose opposing preference this one overrides when the two disagree.
    :cvar level: :data:`LEVEL_CANDIDATE`.
    """

    rule_id: str
    version: str
    level: str = LEVEL_CANDIDATE
    objective: NetworkComparisonObjective = NetworkComparisonObjective.physical_accuracy
    objective_key: str = ""
    observable: str = "rate_coefficient"
    authority: str = ""
    evidence_type: str = ""
    interpretation: str = ""
    supersedes: tuple[str, ...] = ()
    inactive_reasons: tuple[str, ...] = ()
    #: SHA-256 of the audited manifest the rule rests on. An *active* rule must carry one (see :func:`validate_rules`).
    manifest_sha256: str | None = None

    @property
    @abstractmethod
    def status(self) -> str:
        """``active``, ``inactive`` or ``revoked``."""

    @abstractmethod
    def scope(self, network: NetworkFacts, request: NetworkRequest) -> RuleMatch:
        """Whether the rule's chemical, domain and request scope contains this network and this request."""

    @abstractmethod
    def preferred_side(self, member: MemberFacts, solve: SolveFacts) -> RuleMatch:
        """Whether one member has the protocol this rule prefers."""

    @abstractmethod
    def yielding_side(self, member: MemberFacts, solve: SolveFacts) -> RuleMatch:
        """Whether one member has the protocol this rule ranks below the preferred one."""

    def compatible(self, a: MemberFacts, a_solve: SolveFacts, b: MemberFacts, b_solve: SolveFacts) -> RuleMatch:
        """Whether two members of the same output agree on everything the rule does not compare."""
        return RuleMatch(Tri.true, ())

    def evidence_limits(self) -> list[dict[str, Any]]:
        return []

    def exclusions(self) -> list[str]:
        return []

    def exceptions(self) -> list[str]:
        return []

    def describe(self) -> dict[str, Any]:
        """The registry entry as a decision manifest records it."""
        entry: dict[str, Any] = {
            "rule_id": self.rule_id,
            "version": self.version,
            "level": self.level,
            "status": self.status,
            "observable": self.observable,
            "objective": self.objective.value,
            "objective_key": self.objective_key,
            "authority": self.authority,
            "evidence_type": self.evidence_type,
            "interpretation": self.interpretation,
            "edge_label": EDGE_LABEL,
            "supersedes": list(self.supersedes),
            "inactive_reasons": list(self.inactive_reasons),
            "evidence_limits": self.evidence_limits(),
            "exclusions": self.exclusions(),
            "exceptions": self.exceptions(),
        }
        if self.manifest_sha256 is not None:
            entry["manifest_sha256"] = self.manifest_sha256
        return entry


class NetworkRepresentationRule(NetworkRule):
    """A comparison between two alternate fits of one determination (``representation_fidelity`` only).

    Judged on :class:`FitFacts` with their solve; it never ranks the solves themselves.
    """

    level = LEVEL_REPRESENTATION
    objective = NetworkComparisonObjective.representation_fidelity

    def preferred_side(self, member: MemberFacts, solve: SolveFacts) -> RuleMatch:  # pragma: no cover - fit level
        raise NotImplementedError("a representation rule judges fits, not members")

    def yielding_side(self, member: MemberFacts, solve: SolveFacts) -> RuleMatch:  # pragma: no cover - fit level
        raise NotImplementedError("a representation rule judges fits, not members")

    @abstractmethod
    def fit_preferred(self, fit: FitFacts, solve: SolveFacts) -> RuleMatch:
        """Whether the fit has the property this rule prefers."""

    @abstractmethod
    def fit_yielding(self, fit: FitFacts, solve: SolveFacts) -> RuleMatch:
        """Whether the fit has the property this rule ranks below the preferred one."""

    def fit_compatible(self, a: FitFacts, b: FitFacts, solve: SolveFacts) -> RuleMatch:
        return RuleMatch(Tri.true, ())


_SHA256 = re.compile(r"[0-9a-f]{64}")


def validate_rules(rules: tuple[NetworkRule, ...]) -> None:
    """Refuse a registry that cannot be applied honestly: duplicate ids, or a rule with no objective key.

    An empty ``objective_key`` would let edges of unrelated comparisons be composed into one path, so it is
    refused rather than defaulted.
    """
    seen: set[str] = set()
    for rule in rules:
        name = rule.rule_id  # a rule's name, not a database key
        if name in seen:
            raise ValueError(f"rule names must be distinct: {name!r} is registered twice")
        seen.add(name)
        if not rule.objective_key.strip():
            raise ValueError(f"rule {name!r} has an empty objective_key")
        if not isinstance(rule.objective, NetworkComparisonObjective):
            raise ValueError(f"rule {name!r} names no comparison objective")
        pin = rule.manifest_sha256
        if rule.status == RULE_ACTIVE and not (isinstance(pin, str) and _SHA256.fullmatch(pin)):
            raise ValueError(f"active rule {name!r} rests on no pinned audited manifest (a SHA-256 is required)")


#: SHA-256 of ``network_rule_candidates.yaml`` as shipped (manifest 0.1.0). The registry refuses to load over any
#: other bytes; a change to an entry, a blocker or a source list is a new manifest version, a new pin and a new rule
#: version. A manifest handed to a rule must be the one this pins.
NETWORK_RULE_MANIFEST_SHA256 = "179894910990759943752fb2afb1887707dc9ac206e8d1440163421affc3a834"


#: Ids of audited rules whose predicates have been written and reviewed. None yet: approval in the manifest alone
#: applies nothing, and approving one rule never enables another.
RULES_WITH_IMPLEMENTED_PREDICATES: frozenset[str] = frozenset()


class AuditedNetworkRule(NetworkRule):
    """A council example that is registered, audited and **inactive**, with the reasons.

    It exists so that the registry says plainly which examples were considered and what is missing for each,
    rather than the example being silently absent or half-implemented. It has no predicates: its sides and scope
    are unknown, so it can never make an edge, and that stays true even if the owner one day approves its manifest
    entry, until the predicates are written and reviewed (``predicates_implemented``). An agent never activates one.
    """

    @property
    def predicates_implemented(self) -> bool:
        """Per rule: only a rule id listed in :data:`RULES_WITH_IMPLEMENTED_PREDICATES` has its predicates written."""
        return self.rule_id in RULES_WITH_IMPLEMENTED_PREDICATES

    def __init__(self, candidate: RuleCandidate, manifest: NetworkRuleManifest) -> None:
        name = candidate.rule_id  # a rule's name, not a database key
        if candidate.level != self.level:
            raise ValueError(
                f"rule {name} is {candidate.level}-level in the manifest and cannot be built as a "
                f"{self.level}-level rule"
            )
        if manifest.sha256 != NETWORK_RULE_MANIFEST_SHA256:
            raise ValueError(
                f"the manifest handed to rule {name} is not the pinned one "
                f"(expected {NETWORK_RULE_MANIFEST_SHA256}, got {manifest.sha256})"
            )
        if candidate not in manifest.candidates:
            raise ValueError(f"rule {name} is not an entry of the manifest it was built with")
        self._candidate = candidate
        self._manifest = manifest
        self.manifest_sha256 = manifest.sha256
        self.rule_id = candidate.rule_id
        self.version = candidate.version
        self.objective = candidate.objective
        self.objective_key = candidate.objective_key
        self.observable = candidate.observable
        self.authority = f"none: not approved (manifest {manifest.version})"
        self.evidence_type = "audited_candidate_inactive"
        self.interpretation = (
            "Not applied: no comparison is made until the owner signs the manifest entry and the predicates exist."
        )

    @property
    def status(self) -> str:
        return RULE_ACTIVE if self._candidate.activatable and self.predicates_implemented else RULE_INACTIVE

    @property
    def inactive_reasons(self) -> tuple[str, ...]:  # type: ignore[override]
        reasons = [f"{b['id']}: {b['text']}" for b in self._candidate.activation_blockers]
        if not self._candidate.activation_approved:
            reasons.append("not approved for activation by the owner")
        if not self.predicates_implemented:
            reasons.append("the rule's predicates are not implemented")
        return tuple(reasons)

    def scope(self, network: NetworkFacts, request: NetworkRequest) -> RuleMatch:
        return RuleMatch(Tri.unknown, ("rule_inactive",))

    def preferred_side(self, member: MemberFacts, solve: SolveFacts) -> RuleMatch:
        return RuleMatch(Tri.unknown, ("rule_inactive",))

    def yielding_side(self, member: MemberFacts, solve: SolveFacts) -> RuleMatch:
        return RuleMatch(Tri.unknown, ("rule_inactive",))

    def describe(self) -> dict[str, Any]:
        entry = super().describe()
        entry.update(self._candidate.describe())
        entry["manifest_version"] = self._manifest.version
        entry["manifest_sha256"] = self._manifest.sha256
        return entry


class AuditedNetworkRepresentationRule(NetworkRepresentationRule, AuditedNetworkRule):
    """An audited, inactive rule that compares two alternate fits of one solve and can never rank solves.

    Registered at the representation level, so the engine judges it on fits only and never on candidates or
    members; its fit predicates, like the candidate rules' sides, are unknown until written and reviewed.
    """

    def fit_preferred(self, fit: FitFacts, solve: SolveFacts) -> RuleMatch:
        return RuleMatch(Tri.unknown, ("rule_inactive",))

    def fit_yielding(self, fit: FitFacts, solve: SolveFacts) -> RuleMatch:
        return RuleMatch(Tri.unknown, ("rule_inactive",))


@lru_cache(maxsize=1)
def default_rules() -> tuple[NetworkRule, ...]:
    """The rules registered in this release: the audited council examples. **None is active.**"""
    manifest = load_network_rule_manifest(expected_sha256=NETWORK_RULE_MANIFEST_SHA256)
    return tuple(
        (AuditedNetworkRepresentationRule if candidate.level == LEVEL_REPRESENTATION else AuditedNetworkRule)(
            candidate, manifest
        )
        for candidate in manifest.candidates
    )


def active_rules(rules: tuple[NetworkRule, ...] | None = None) -> tuple[NetworkRule, ...]:
    """Rules with status ``active``; inactive and revoked rules are never applied.

    :raises ValueError: an active rule that rests on no pinned audited manifest (see :func:`validate_rules`).
    """
    registry = tuple(default_rules() if rules is None else rules)
    validate_rules(registry)
    return tuple(r for r in registry if r.status == RULE_ACTIVE)
