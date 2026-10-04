"""Plain values every selector shares: the outcome vocabulary, a rule prerequisite, a preference edge."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class Applicability(str, Enum):
    """Whether one record can supply the requested quantity.

    ``applicable``   every requirement is established.
    ``incompatible`` something is known to be wrong for this request (another phase, target,
                     direction or pressure; a domain that excludes the request; a defective fit).
    ``unsupported``  the record may hold the answer but in a form this release does not evaluate.
    ``unresolved``   a required fact was never recorded. Never guessed.
    Precedence when several apply: incompatible, unsupported, unresolved.
    """

    applicable = "applicable"
    incompatible = "incompatible"
    unsupported = "unsupported"
    unresolved = "unresolved"


class Outcome(str, Enum):
    policy_preferred = "policy_preferred"
    incomparable_alternatives = "incomparable_alternatives"
    sole_eligible_candidate = "sole_eligible_candidate"
    no_applicable_candidate = "no_applicable_candidate"
    policy_conflict = "policy_conflict"
    #: The visible population exceeded the cap, so nothing was assessed and nothing is selected.
    bounded_search_exceeded = "bounded_search_exceeded"


class Tri(str, Enum):
    """A rule prerequisite: established, refuted, or not knowable from what the record links."""

    true = "true"
    false = "false"
    unknown = "unknown"


@dataclass(frozen=True)
class RuleMatch:
    """One side of a rule evaluated on one candidate, with the reasons for the verdict."""

    state: Tri
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"state": self.state.value, "reasons": list(self.reasons)}


@dataclass(frozen=True)
class Edge:
    """A preference: ``preferred`` precedes ``dispreferred`` under one rule version."""

    preferred: str
    dispreferred: str
    rule_id: str
    rule_version: str

    def to_dict(self) -> dict[str, str]:
        return {
            "preferred": self.preferred,
            "dispreferred": self.dispreferred,
            "rule_id": self.rule_id,
            "rule_version": self.rule_version,
        }
