"""SYNTHETIC rules for the engine tests. They are test fixtures, not scientific benchmarks, and say nothing about
any real solver, reduction or fit: they exist to build edges, conflicts, cycles and fronts so the engine's logic
can be exercised. The shipped registry has no active rule."""

from __future__ import annotations

from tckdb_schemas.network_declarations import NetworkComparisonObjective

from app.services.network_selection.models import FitFacts, NetworkFacts, NetworkRequest, SolveFacts
from app.services.network_selection.rules import (
    RULE_ACTIVE,
    MemberFacts,
    NetworkRepresentationRule,
    NetworkRule,
)
from app.services.selection_kernel import RuleMatch, Tri

REDUCTION = {"version": 1, "reduction_method": "chemically_significant_eigenvalues"}
OTHER_REDUCTION = {"version": 1, "reduction_method": "modified_strong_collision"}
THIRD_REDUCTION = {"version": 1, "reduction_method": "reservoir_state"}


def protocol(reduction: str, **more: str) -> dict:
    return {"version": 1, "reduction_method": reduction, **more}


def _field(solve: SolveFacts, name: str):
    if solve.protocol_state != "valid" or solve.protocol is None:
        return None
    return solve.protocol.get(name)


class ProtocolRule(NetworkRule):
    """TEST FIXTURE: prefers solves whose protocol ``field`` equals ``prefer`` over those equal to ``yield_``."""

    def __init__(
        self,
        rule_id: str,
        *,
        prefer: str,
        yield_: str,
        field: str = "reduction_method",
        objective: NetworkComparisonObjective = NetworkComparisonObjective.physical_accuracy,
        objective_key: str = "test_objective",
        status: str = RULE_ACTIVE,
        supersedes: tuple[str, ...] = (),
        compat: tuple[str, ...] = (),
        channels: dict[str, tuple[str, str]] | None = None,
        manifest_sha256: str | None = None,
    ) -> None:
        self.rule_id = rule_id
        self.version = "0.0.0-test"
        self.objective = objective
        self.objective_key = objective_key
        self._prefer, self._yield, self._field = prefer, yield_, field
        self._status = status
        self.supersedes = supersedes
        self._compat = compat
        # channel -> (preferred value, yielding value); overrides the global pair for that channel
        self._channels = channels or {}
        self._sha = manifest_sha256
        self.inactive_reasons = ("synthetic test rule",) if status != RULE_ACTIVE else ()

    @property
    def status(self) -> str:
        return self._status

    def scope(self, network: NetworkFacts, request: NetworkRequest) -> RuleMatch:
        return RuleMatch(Tri.true, ("test_scope",))

    def _match(self, member: MemberFacts, solve: SolveFacts, which: int) -> RuleMatch:
        pair = self._channels.get(member.channel_key, (self._prefer, self._yield))
        value = _field(solve, self._field)
        if value is None:
            return RuleMatch(Tri.unknown, (f"{self._field}_not_declared",))
        return RuleMatch(Tri.true if value == pair[which] else Tri.false, (f"{self._field}={value}",))

    def preferred_side(self, member: MemberFacts, solve: SolveFacts) -> RuleMatch:
        return self._match(member, solve, 0)

    def yielding_side(self, member: MemberFacts, solve: SolveFacts) -> RuleMatch:
        return self._match(member, solve, 1)

    def compatible(self, a: MemberFacts, a_solve: SolveFacts, b: MemberFacts, b_solve: SolveFacts) -> RuleMatch:
        for name in self._compat:
            left, right = _field(a_solve, name), _field(b_solve, name)
            if left is None or right is None:
                return RuleMatch(Tri.unknown, (f"{name}_not_declared_on_both_sides",))
            if left != right:
                return RuleMatch(Tri.false, (f"{name}_differs",))
        return RuleMatch(Tri.true, ())

    def describe(self) -> dict:
        entry = super().describe()
        if self._sha is not None:
            entry["manifest_sha256"] = self._sha
        return entry


class FitKindRule(NetworkRepresentationRule):
    """TEST FIXTURE: among alternate fits of one determination, prefer ``prefer`` model kind over ``yield_``."""

    def __init__(self, rule_id: str, *, prefer: str, yield_: str, objective_key: str = "test_fit_kind") -> None:
        self.rule_id = rule_id
        self.version = "0.0.0-test"
        self.objective_key = objective_key
        self._prefer, self._yield = prefer, yield_

    @property
    def status(self) -> str:
        return RULE_ACTIVE

    def scope(self, network: NetworkFacts, request: NetworkRequest) -> RuleMatch:
        return RuleMatch(Tri.true, ())

    def fit_preferred(self, fit: FitFacts, solve: SolveFacts) -> RuleMatch:
        return RuleMatch(Tri.true if fit.model_kind == self._prefer else Tri.false, (fit.model_kind,))

    def fit_yielding(self, fit: FitFacts, solve: SolveFacts) -> RuleMatch:
        return RuleMatch(Tri.true if fit.model_kind == self._yield else Tri.false, (fit.model_kind,))
