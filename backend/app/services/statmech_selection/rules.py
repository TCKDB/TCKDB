"""The statmech rule registry: the shape of a rule, and nothing active.

A rule is a scoped, attributed comparison between two *whole models*. A model on a rule's preferred side precedes a
model on its yielding side inside the rule's audited scope, and only where both sides state what the rule needs.
The guards are the ones the structure and kinetics registries keep:

* **Unknown is not a vote.** A prerequisite is established (true), refuted (false) or not knowable from what the
  candidate records (unknown). An edge needs true on both sides, a verified-compatible pair and a rule whose audited
  coverage contains the entire requested scope. A candidate that says less never earns an edge, and a pair that
  *might* have one is reported as undecided rather than dropped.
* **A rule says what it is.** It names the task, observable, objective, the reference model when the objective is
  model fidelity, its evidence limits and, when it is not active, why.
* **A rule is inactive until a curator says otherwise.** Status ``active`` needs an objective, a pinned audited
  manifest digest and no inactive reasons. **This release registers no rule**: :func:`default_rules` is empty, so
  qualification and honest alternatives are the whole of what an unmodified service returns. Synthetic rules exist
  only in tests.

No edge follows from a level-of-theory label, a lower energy, a larger partition function, a rotor or field count,
a review status or recency. No component benchmark licenses an ensemble or full-model preference, and a rule never
composes across objectives or reference models: it applies to a request only when its objective (and reference
model) is the request's, so every edge in one decision answers one question. Per-member or per-temperature wins
cannot be assembled into a winner of a different scope, because a rule's coverage must contain the whole request.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from app.services.rule_registry import rules_registry
from app.services.selection_kernel import RuleMatch, Tri
from app.services.statmech_selection.assessment import NormalizedRecipe
from app.services.statmech_selection.facts import SubjectFacts
from app.services.statmech_selection.models import StatmechRequest
from app.services.statmech_selection.vocabulary import CandidateKind, Objective
from app.versioning import VersionRegistry

_SHA256 = re.compile(r"[0-9a-f]{64}")

RULE_ACTIVE = "active"
RULE_INACTIVE = "inactive"
RULE_REVOKED = "revoked"

#: What an edge from a statmech rule is, in the manifest and everywhere it is shown.
EDGE_LABEL = "audited whole-model preference"

#: Version of the rule-registry semantics (what a rule must state to be active, how scope and coverage are read).
RULES_VERSION = 1


@dataclass(frozen=True)
class ModelCandidate:
    """One whole model a rule is judged on: what it is, its actual normalised recipe, the evidence roles it rests on."""

    candidate_ref: str
    kind: CandidateKind
    owner_ref: str
    recipe: NormalizedRecipe
    evidence: tuple[tuple[str, tuple[str, ...]], ...]

    def fact(self, name: str) -> tuple[str, str | None]:
        """``(state, value)`` of one recipe fact; an absent fact is ``unknown``, never a default."""
        for f in self.recipe.facts:
            if f.name == name:
                return f.state, f.value
        return "unknown", None

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_ref": self.candidate_ref,
            "kind": self.kind.value,
            "owner_ref": self.owner_ref,
            "recipe": self.recipe.to_dict(),
            "evidence": {k: list(v) for k, v in self.evidence},
        }


class StatmechRule(ABC):
    """One scoped, attributed comparison; see the module docstring.

    :cvar rule_id: Stable id. :cvar version: Version of this rule's logic and data.
    :cvar tasks: The task tokens the rule is about; :cvar observables: the observable tokens.
    :cvar objective: The objective the rule compares on; a request applies the rule only under the same one.
    :cvar reference_model: The pinned reference model, for ``model_fidelity`` rules.
    :cvar manifest_sha256: Digest of the audited evidence manifest the rule rests on (required to be active).
    :cvar supersedes: Ids of rules whose opposing preference this one overrides when the two disagree.
    :cvar inactive_reasons: Why the rule is not active, when it is not.
    """

    rule_id: str
    version: int
    tasks: tuple[str, ...] = ()
    observables: tuple[str, ...] = ()
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
    def scope(self, subject: SubjectFacts, request: StatmechRequest) -> RuleMatch:
        """Whether the rule's chemical, state and request scope (task, observable, target) contains this request."""

    @abstractmethod
    def coverage(self, request: StatmechRequest) -> RuleMatch:
        """Whether the rule's audited evidence covers the *entire* requested temperature, member and domain scope."""

    @abstractmethod
    def preferred_side(self, candidate: ModelCandidate) -> RuleMatch:
        """Whether the model is the one this rule prefers."""

    @abstractmethod
    def yielding_side(self, candidate: ModelCandidate) -> RuleMatch:
        """Whether the model is the one this rule ranks below the preferred one."""

    def compatible(self, a: ModelCandidate, b: ModelCandidate) -> RuleMatch:
        """Whether the two models agree on everything the rule does not compare (default: nothing more is needed)."""
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
            "tasks": list(self.tasks),
            "observables": list(self.observables),
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


def validate_rules(rules: Sequence[StatmechRule]) -> None:
    """Refuse a registry that could make an unsupported edge: duplicate ids, or an active rule that is not pinned.

    :raises ValueError: duplicate ``rule_id``; an active rule with no objective, no tasks or observables, no pinned
        manifest digest, with inactive reasons still listed, or a model-fidelity rule with no reference model.
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
        if not rule.tasks or not rule.observables:
            problems.append("no task or observable it is about")
        if not rule.manifest_sha256 or not _SHA256.fullmatch(rule.manifest_sha256):
            problems.append("no pinned manifest digest")
        if rule.inactive_reasons:
            problems.append("inactive reasons still listed")
        if rule.objective is Objective.model_fidelity and not rule.reference_model:
            problems.append("a model_fidelity rule names no reference model")
        if problems:
            raise ValueError(f"rule {name} cannot be active: {', '.join(problems)}")


def default_rules() -> tuple[StatmechRule, ...]:
    """The rules this release ships: none.

    Council examples stay documentation until exact-scope primary evidence and an activation review are captured; an
    approved example alone applies nothing, and approving one rule never enables another.
    """
    return ()


@lru_cache(maxsize=1)
def default_rules_registry() -> VersionRegistry[Any]:
    """The registered rules keyed by ``(rule id, version)``: what a replay asks for the rule a manifest recorded."""
    return rules_registry("statmech_selection_rules", default_rules())


__all__ = [
    "EDGE_LABEL",
    "RULES_VERSION",
    "RULE_ACTIVE",
    "RULE_INACTIVE",
    "RULE_REVOKED",
    "ModelCandidate",
    "StatmechRule",
    "default_rules",
    "validate_rules",
]
