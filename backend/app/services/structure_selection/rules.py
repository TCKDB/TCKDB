"""The structure-specific rule registry: the shape of a rule, and nothing active.

A rule is a scoped, attributed comparison between two *protocols* (cohorts of units that share an actual recipe).
Protocol candidates on its preferred side precede candidates on its yielding side, inside the rule's audited
scope, and only where both sides state what the rule needs. The same three guards as the kinetics registry keep it
honest:

* **Unknown is not a vote.** A prerequisite is established (true), refuted (false) or not knowable from what the
  candidate records (unknown). An edge needs true on both sides and a verified-compatible pair. A candidate that
  says less never earns an edge.
* **A rule says what it is.** Its entry names the objective it compares on, the reference model when the objective
  is model fidelity, the evidence limits and, when it is not active, why.
* **A rule is inactive until a curator says otherwise.** Status ``active`` needs an objective, a pinned audited
  manifest digest and no inactive reasons. Whatever this release registers is inactive; see
  :func:`default_rules`.

Edges from a structure rule are never composed across objectives or reference models: a rule applies to a request
only when its objective (and reference model) is the request's, so every edge in one decision answers one
question. A tighter keyword, a bigger basis, more attachments or a lower absolute total energy are not rules and
create no edge.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from app.chemistry.structure_rules.manifest import (
    RuleCandidate,
    StructureRuleManifest,
    load_structure_rule_manifest,
)
from app.services.selection_kernel import RuleMatch, Tri
from app.services.structure_selection.models import (
    NormalizedRecipe,
    Objective,
    StructureRequest,
    StructureSubject,
)

RULE_ACTIVE = "active"
RULE_INACTIVE = "inactive"
RULE_REVOKED = "revoked"

#: What an edge from a structure rule is, in the manifest and everywhere it is shown.
EDGE_LABEL = "audited protocol preference"

#: Version of the rule-registry semantics (what a rule must state to be active, how scope is evaluated).
RULES_VERSION = "1"


@dataclass(frozen=True)
class ProtocolCandidate:
    """One protocol a rule is judged on: a cohort of units, its shared recipe, and which units it holds."""

    cohort_id: str
    recipe: NormalizedRecipe
    unit_refs: tuple[str, ...]
    #: Public refs of the targets (basins, geometries, saddles) the cohort holds a unit for.
    target_keys: tuple[str, ...]

    def fact(self, name: str) -> tuple[str, str | None]:
        """``(state, value)`` of one recipe fact; an absent fact is ``unknown``, never a default."""
        for f in self.recipe.facts:
            if f.name == name:
                return f.state, f.value
        return "unknown", None

    def to_dict(self) -> dict[str, Any]:
        return {
            "cohort_id": self.cohort_id,
            "unit_refs": list(self.unit_refs),
            "target_keys": list(self.target_keys),
            "recipe": self.recipe.to_dict(),
        }


class StructureRule(ABC):
    """One scoped, attributed comparison; see the module docstring.

    :cvar rule_id: Stable id. :cvar version: Version of this rule's logic and data.
    :cvar objective: The objective the rule compares on; a request applies the rule only under the same one.
    :cvar reference_model: The pinned reference model, for ``model_fidelity`` rules.
    :cvar manifest_sha256: Digest of the audited evidence manifest the rule rests on (required to be active).
    :cvar supersedes: Ids of rules whose opposing preference this one overrides when the two disagree.
    :cvar inactive_reasons: Why the rule is not active, when it is not.
    :cvar domain: What the rule is about, in words: protocols, observable, chemistry, geometry, settings.
    """

    rule_id: str
    version: str
    observable: str = "structure_energy"
    objective: Objective | None = None
    reference_model: str | None = None
    manifest_sha256: str | None = None
    authority: str = ""
    evidence_type: str = ""
    interpretation: str = ""
    domain: str = ""
    supersedes: tuple[str, ...] = ()
    inactive_reasons: tuple[str, ...] = ()

    @property
    @abstractmethod
    def status(self) -> str:
        """``active``, ``inactive`` or ``revoked``."""

    @abstractmethod
    def scope(self, subject: StructureSubject, request: StructureRequest) -> RuleMatch:
        """Whether the rule's chemical, state, geometry and request scope contains this request."""

    @abstractmethod
    def preferred_side(self, candidate: ProtocolCandidate) -> RuleMatch:
        """Whether the protocol is the one this rule prefers."""

    @abstractmethod
    def yielding_side(self, candidate: ProtocolCandidate) -> RuleMatch:
        """Whether the protocol is the one this rule ranks below the preferred one."""

    def compatible(self, a: ProtocolCandidate, b: ProtocolCandidate) -> RuleMatch:
        """Whether the two protocols agree on everything the rule does not compare (default: nothing more is needed)."""
        return RuleMatch(Tri.true, ())

    def evidence_limits(self) -> list[dict[str, Any]]:
        return []

    def exclusions(self) -> list[str]:
        return []

    def exceptions(self) -> list[str]:
        return []

    def describe(self) -> dict[str, Any]:
        """The registry entry as a decision manifest records it."""
        return {
            "rule_id": self.rule_id,
            "version": self.version,
            "status": self.status,
            "observable": self.observable,
            "objective": self.objective.value if self.objective is not None else None,
            "reference_model": self.reference_model,
            "manifest_sha256": self.manifest_sha256,
            "authority": self.authority,
            "evidence_type": self.evidence_type,
            "interpretation": self.interpretation,
            "domain": self.domain,
            "edge_label": EDGE_LABEL,
            "supersedes": list(self.supersedes),
            "inactive_reasons": list(self.inactive_reasons),
            "evidence_limits": self.evidence_limits(),
            "exclusions": self.exclusions(),
            "exceptions": self.exceptions(),
        }


def validate_rules(rules: Sequence[StructureRule]) -> None:
    """Refuse a registry that could make an unsupported edge: duplicate ids, or an active rule that is not pinned.

    :raises ValueError: duplicate ``(rule_id)``; an active rule with no objective, no pinned manifest digest or
        with inactive reasons still listed; a model-fidelity rule with no reference model.
    """
    seen: set[str] = set()
    for rule in rules:
        name = rule.rule_id  # a registry name, not a database row id
        if name in seen:
            raise ValueError(f"rule names must be distinct: {name}")
        seen.add(name)
        if rule.status != RULE_ACTIVE:
            continue
        problems = []
        if rule.objective is None:
            problems.append("no objective")
        if not rule.manifest_sha256 or len(rule.manifest_sha256) != 64:
            problems.append("no pinned manifest digest")
        if rule.inactive_reasons:
            problems.append("inactive reasons still listed")
        if rule.objective is Objective.model_fidelity and not rule.reference_model:
            problems.append("a model_fidelity rule names no reference model")
        if problems:
            raise ValueError(f"rule {name} cannot be active: {', '.join(problems)}")


#: SHA-256 of ``structure_rule_candidates.yaml`` as shipped (manifest 0.1.0). The registry refuses to load over any
#: other bytes; a change to an entry, a blocker or a source list is a new manifest version, a new pin and a new rule
#: version. A manifest handed to a rule must be the one this pins.
STRUCTURE_RULE_MANIFEST_SHA256 = "cff519d81754e1cb42ac52d86d6a9988b7702455cc96d699f3d7c3413e535048"

#: Ids of audited rules whose predicates have been written and reviewed. None yet: approval in the manifest alone
#: applies nothing, and approving one rule never enables another.
RULES_WITH_IMPLEMENTED_PREDICATES: frozenset[str] = frozenset()


class AuditedStructureRule(StructureRule):
    """A council example that is registered, audited and **inactive**, with the reasons.

    It exists so that the registry says plainly which examples were considered and what is missing for each, rather
    than the example being silently absent or half-implemented. It has no predicates: its sides and scope are unknown,
    so it can never make an edge, and that stays true even if the owner one day approves its manifest entry, until the
    predicates are written and reviewed (``predicates_implemented``). An agent never activates one.
    """

    @property
    def predicates_implemented(self) -> bool:
        """Per rule: only a rule id listed in :data:`RULES_WITH_IMPLEMENTED_PREDICATES` has its predicates written."""
        return self.rule_id in RULES_WITH_IMPLEMENTED_PREDICATES

    def __init__(self, candidate: RuleCandidate, manifest: StructureRuleManifest) -> None:
        name = candidate.rule_id  # a rule's name, not a database key
        if manifest.sha256 != STRUCTURE_RULE_MANIFEST_SHA256:
            raise ValueError(
                f"the manifest handed to rule {name} is not the pinned one "
                f"(expected {STRUCTURE_RULE_MANIFEST_SHA256}, got {manifest.sha256})"
            )
        if candidate not in manifest.candidates:
            raise ValueError(f"rule {name} is not an entry of the manifest it was built with")
        self._candidate = candidate
        self._manifest = manifest
        self.manifest_sha256 = manifest.sha256
        self.rule_id = candidate.rule_id
        self.version = candidate.version
        self.objective = Objective(candidate.objective)
        self.observable = candidate.observable
        self.authority = f"none: not approved (manifest {manifest.version})"
        self.evidence_type = "audited_candidate_inactive"
        self.interpretation = (
            "Not applied: no comparison is made until the owner signs the manifest entry and the predicates exist."
        )
        self.domain = candidate.scope["domain"]

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

    def scope(self, subject: StructureSubject, request: StructureRequest) -> RuleMatch:
        return RuleMatch(Tri.unknown, ("rule_inactive",))

    def preferred_side(self, candidate: ProtocolCandidate) -> RuleMatch:
        return RuleMatch(Tri.unknown, ("rule_inactive",))

    def yielding_side(self, candidate: ProtocolCandidate) -> RuleMatch:
        return RuleMatch(Tri.unknown, ("rule_inactive",))

    def describe(self) -> dict[str, Any]:
        entry = super().describe()
        entry.update(self._candidate.describe())
        entry["manifest_version"] = self._manifest.version
        entry["manifest_sha256"] = self._manifest.sha256
        return entry


@lru_cache(maxsize=1)
def default_rules() -> tuple[StructureRule, ...]:
    """The rules registered in this release: the audited council examples. **None is active.**"""
    manifest = load_structure_rule_manifest(expected_sha256=STRUCTURE_RULE_MANIFEST_SHA256)
    return tuple(AuditedStructureRule(candidate, manifest) for candidate in manifest.candidates)


def active_rules(rules: Sequence[StructureRule] | None = None) -> tuple[StructureRule, ...]:
    """Rules with status ``active``; inactive and revoked rules are never applied.

    :raises ValueError: an active rule that is not pinned (see :func:`validate_rules`).
    """
    registry = tuple(default_rules() if rules is None else rules)
    validate_rules(registry)
    return tuple(r for r in registry if r.status == RULE_ACTIVE)
