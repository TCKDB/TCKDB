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
from typing import Any

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


def default_rules() -> tuple[StructureRule, ...]:
    """The rules registered in this release. None is active."""
    return ()
