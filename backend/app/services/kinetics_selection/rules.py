"""The versioned kinetics rule registry, separate from thermo's.

A rule is a scoped, attributed comparison between two *protocols*: records on its preferred side
precede records on its yielding side, inside the audited scope and only where both sides state what the
rule needs. Three things keep a rule honest:

* **Unknown is not a vote.** Every prerequisite is stated-and-equal (true), stated-and-different (false)
  or unstated (unknown). An edge needs true on its side. A record that says less never earns an edge, and
  a rule that cannot establish its prerequisites makes no edge at all.
* **A rule says what it is.** Its edges are labelled an *expected-performance inference* (the evidence is a
  benchmark, not a measurement of the individual rate) and its entry carries the evidence limits, the
  objective it compares on and, when it is not active, why.
* **A rule is inactive until a curator says otherwise.** Status ``active`` needs the audited manifest
  to be approved, signed and free of blockers. Everything shipped in this release is inactive, with the reasons
  recorded; the council's examples that were not audited from primary sources are registered inactive too.

Pairs are compared through :meth:`KineticsRule.compatible`: a rule that rests on one component of a
protocol (a barrier) says nothing about the rest unless the rest is verified the same on both sides.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from functools import lru_cache
from typing import Any

from app.chemistry.kinetics_rules.xyg3_barrier_manifest import (
    BarrierMember,
    ManifestError,
    XYG3BarrierManifest,
    load_xyg3_barrier_manifest,
)
from app.services.kinetics_selection.models import KineticsRequest, KineticsSubject, NormalizedKinetics, SpeciesFact
from app.services.selection_kernel import RuleMatch, Tri

RULE_ACTIVE = "active"
RULE_INACTIVE = "inactive"
RULE_REVOKED = "revoked"

#: What an edge from a benchmark rule is, in the manifest and everywhere it is shown.
EDGE_LABEL = "expected-performance inference"

#: SHA-256 of ``xyg3_b3lyp_barrier_manifest.yaml`` as shipped (manifest 0.3.0). The rule refuses to load over any
#: other bytes; a change to a member, a barrier height or a blocker is a new manifest version, a new pin and a new
#: rule version.
XYG3_MANIFEST_SHA256 = "1ce59bde6133851d0b92324e6a563f9d7b43c5e2813226fdc91dfcfef1aedd96"

#: The protocol components, other than the one a rule compares, that must be stated and equal on both sides for the
#: rule's evidence to speak about the whole rate.
COMPATIBILITY_FIELDS = (
    "zero_point_treatment",
    "geometry_relation",
    "rotor_treatment",
    "conformer_treatment",
    "path_treatment",
)


class KineticsRule(ABC):
    """One scoped, attributed comparison; see the module docstring.

    :cvar rule_id: Stable id. :cvar version: Version of this rule's logic and data.
    :cvar objective: What the rule compares on, in words. :cvar objective_key: A short key; edges from rules with
        different keys are never composed into one preference path.
    :cvar supersedes: Ids of rules whose opposing preference this one overrides when the two disagree.
    :cvar inactive_reasons: Why the rule is not active, when it is not.
    """

    rule_id: str
    version: str
    observable: str = "rate_coefficient"
    objective: str = ""
    objective_key: str = ""
    authority: str = ""
    evidence_type: str = ""
    interpretation: str = ""
    supersedes: tuple[str, ...] = ()
    inactive_reasons: tuple[str, ...] = ()

    @property
    @abstractmethod
    def status(self) -> str:
        """``active``, ``inactive`` or ``revoked``."""

    @abstractmethod
    def scope(self, subject: KineticsSubject, request: KineticsRequest) -> RuleMatch:
        """Whether the rule's chemical and request scope contains this reaction and this request."""

    @abstractmethod
    def preferred_side(self, candidate: NormalizedKinetics) -> RuleMatch:
        """Whether the record has the protocol this rule prefers."""

    @abstractmethod
    def yielding_side(self, candidate: NormalizedKinetics) -> RuleMatch:
        """Whether the record has the protocol this rule ranks below the preferred one."""

    def compatible(self, a: NormalizedKinetics, b: NormalizedKinetics) -> RuleMatch:
        """Whether the two records agree on everything the rule does not compare (default: nothing more is needed)."""
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
            "objective": self.objective,
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


def _verdict(refuted: list[str], unknown: list[str], satisfied: list[str]) -> RuleMatch:
    """False beats unknown beats true: a refuted prerequisite is final; an unknown one cannot be assumed."""
    if refuted:
        return RuleMatch(Tri.false, tuple(refuted + unknown))
    if unknown:
        return RuleMatch(Tri.unknown, tuple(unknown))
    return RuleMatch(Tri.true, tuple(satisfied))


def _norm(text: str | None) -> str | None:
    """A method or basis label compared without case, spaces or the star of an older notation."""
    if text is None:
        return None
    return "".join(text.split()).lower().replace("*", "")


class XYG3B3LYPBarrierRule(KineticsRule):
    """K-XYG3-B3LYP-BARRIER: an XYG3 classical barrier precedes a B3LYP one, for the audited benchmark reactions.

    A benchmark of 76 classical barrier heights (38 reactions, both directions) found a much smaller mean
    error for XYG3 than for B3LYP in every subset. The rule would turn that into an expected-performance edge
    between two *rates*, which needs, of each record's own claims and links:

    * a computed origin and a saddle-point TST method;
    * a stated, classical-electronic barrier basis (a zero-point-corrected barrier is another quantity);
    * a stated geometry relation;
    * the electronic method and basis the record **declared for its energies** (``kinetics.energy_level_of_theory_id``):
      stated and equal is true, stated and different is false, unstated is unknown. The declaration covers the whole
      barrier, so it is the only thing that can supply the method. The record's own protocol-declared electronic-energy
      calculations and source-calculation links can corroborate it (the side then reads ``verified``) or contradict
      it (false: the record matches no method rule), but never supply it alone: neither carries a role saying which
      side of the barrier (transition state, reactants, products) it covers, so one calculation cannot show the whole
      barrier was evaluated at its level;

    and, of the *pair*, every other component of the protocol (zero-point treatment, geometry relation, rotor,
    conformer and path treatment, departures such as tunneling) stated and equal on both sides.

    **The rule is inactive.** The audited manifest lists its blockers; among them, the paper's B3LYP numbers are
    not shown to be in the basis the rule requires. See ``activation_blockers`` in the manifest.
    """

    rule_id = "K-XYG3-B3LYP-BARRIER"
    version = "0.2.0"
    objective = (
        "expected accuracy of the classical electronic barrier of a computed rate, for the audited HTBH38/04 and "
        "NHTBH38/04 benchmark reactions"
    )
    objective_key = "classical_electronic_barrier"
    authority = "none: the rule is not approved (manifest 0.1.0)"
    evidence_type = "domain_benchmark_preference_out_of_sample_aggregate"
    interpretation = (
        "A subset mean-error comparison supports an expected-performance judgment about the classical barrier "
        "within the audited reaction set. It is not a per-reaction guarantee, a per-record uncertainty, a statement "
        "about the rate's other contributions, or a curator recommendation."
    )

    def __init__(self, manifest: XYG3BarrierManifest | None = None) -> None:
        if manifest is None:
            manifest = load_xyg3_barrier_manifest(expected_sha256=XYG3_MANIFEST_SHA256)
        elif manifest.sha256 != XYG3_MANIFEST_SHA256:
            # A manifest passed in is held to the same pin as the shipped one: a rule never runs over bytes the
            # audit did not pin, so an approval cannot be supplied by constructing an edited copy.
            raise ManifestError(
                "XYG3 barrier manifest content does not match its pinned digest "
                f"(expected {XYG3_MANIFEST_SHA256}, got {manifest.sha256})"
            )
        self._manifest = manifest
        facts = self._manifest.protocol_facts
        self._preferred = ("xyg3", _norm(facts["xyg3"]["basis"]))
        self._yielding = ("b3lyp", _norm(facts["b3lyp"]["basis_as_footnoted_by_zhang"].split(" (")[0]))

    @property
    def manifest(self) -> XYG3BarrierManifest:
        return self._manifest

    @property
    def status(self) -> str:
        return RULE_ACTIVE if self._manifest.activatable else RULE_INACTIVE

    @property
    def inactive_reasons(self) -> tuple[str, ...]:  # type: ignore[override]
        if self._manifest.activatable:
            return ()
        reasons = [f"{b['id']}: {b['text']}" for b in self._manifest.activation_blockers]
        if not self._manifest.activation_approved:
            reasons.append("not approved for activation by an owner or curator")
        return tuple(reasons)

    # -- scope ---------------------------------------------------------------------------------

    @staticmethod
    def _signature(side: tuple[SpeciesFact, ...], kinds: dict[str, str]) -> tuple[tuple[str, int, int, str], ...]:
        return tuple(sorted((s.inchi_key, s.charge, s.multiplicity, kinds[s.inchi_key]) for s in side))

    def _match(self, member: BarrierMember, subject: KineticsSubject) -> str | None:
        """``"as_listed"``, ``"reversed"`` or ``None``: how the reaction entry's sides line up with the member."""
        benchmark_kinds = {s.inchikey: s.kind for s in (*member.reactants, *member.products)}

        def key(fact: SpeciesFact) -> tuple[str, int, int, str] | None:
            kind = benchmark_kinds.get(fact.inchi_key)
            if kind is None:
                return None
            wanted_entry_kind = "vdw_complex" if kind == "complex" else "minimum"
            if fact.entry_kind != wanted_entry_kind or fact.isotope_key is not None:
                return None
            return (fact.inchi_key, fact.charge, fact.multiplicity, kind)

        reactants = [key(s) for s in subject.reactants]
        products = [key(s) for s in subject.products]
        if None in reactants or None in products:
            return None
        if sorted(reactants) == list(member.reactant_signature) and sorted(products) == list(member.product_signature):  # type: ignore[type-var]
            return "as_listed"
        if sorted(reactants) == list(member.product_signature) and sorted(products) == list(member.reactant_signature):  # type: ignore[type-var]
            return "reversed"
        return None

    def scope(self, subject: KineticsSubject, request: KineticsRequest) -> RuleMatch:
        if any(s.isotope_key is not None for s in (*subject.reactants, *subject.products)):
            return RuleMatch(Tri.false, ("isotopologue_is_not_a_manifest_member",))
        if any(s.electronic_state_kind != "ground" for s in (*subject.reactants, *subject.products)):
            return RuleMatch(Tri.false, ("excited_state_is_not_a_manifest_member",))
        for member in self._manifest.members:
            how = self._match(member, subject)
            if how is not None:
                return RuleMatch(Tri.true, (f"manifest_member:{member.member_id}", f"orientation:{how}"))
        return RuleMatch(Tri.false, ("reaction_not_in_manifest",))

    # -- sides ---------------------------------------------------------------------------------

    def preferred_side(self, candidate: NormalizedKinetics) -> RuleMatch:
        return self._side(candidate, self._preferred)

    def yielding_side(self, candidate: NormalizedKinetics) -> RuleMatch:
        return self._side(candidate, self._yielding)

    def _side(self, candidate: NormalizedKinetics, wanted: tuple[str, str | None]) -> RuleMatch:
        refuted: list[str] = []
        unknown: list[str] = []
        satisfied: list[str] = []
        if candidate.scientific_origin != "computed":
            refuted.append(f"origin_not_computed:{candidate.scientific_origin}")
        protocol = candidate.protocol if candidate.protocol_state == "valid" else None
        if candidate.protocol_state == "absent":
            unknown.append("protocol_not_declared")
        elif protocol is None:
            unknown.append("protocol_declaration_unreadable")
        else:
            self._check_protocol(protocol, refuted, unknown)
        self._check_energy_levels(candidate, wanted, refuted, unknown, satisfied)
        if not refuted and not unknown:
            satisfied.insert(0, f"{wanted[0]}_classical_electronic_barrier_declared")
        return _verdict(refuted, unknown, satisfied)

    @staticmethod
    def _check_protocol(protocol: dict[str, Any], refuted: list[str], unknown: list[str]) -> None:
        method = protocol.get("method_kind")
        if method is None:
            unknown.append("method_kind_not_stated")
        elif method != "saddle_point_tst":
            refuted.append(f"method_kind:{method}")
        basis = protocol.get("barrier_basis")
        if basis is None:
            unknown.append("barrier_basis_not_stated")
        elif basis != "classical_electronic":
            refuted.append(f"barrier_basis:{basis}")
        if protocol.get("geometry_relation") is None:
            unknown.append("geometry_relation_not_stated")

    @staticmethod
    def _check_energy_levels(
        candidate: NormalizedKinetics,
        wanted: tuple[str, str | None],
        refuted: list[str],
        unknown: list[str],
        satisfied: list[str],
    ) -> None:
        """The record's declared level is the evidence; its calculations can only corroborate or contradict it.

        Everything is compared with the level this side wants, so a calculation that disagrees with a declaration that
        matches the side is a contradiction (false), and a declaration that does not match the side is false already.
        """
        record = [lv for lv in candidate.energy_levels if lv["source"] == "record_declared"]
        calcs = [lv for lv in candidate.energy_levels if lv["source"] == "protocol_declared"]
        linked = [lv for lv in candidate.energy_levels if lv["source"] == "source_link"]
        before = len(refuted)

        declared = False
        if not record:
            unknown.append("electronic_energy_level_not_declared_on_the_record")
        else:
            method, basis = _norm(record[0]["method"]), _norm(record[0]["basis"])
            if method is None or basis is None:
                unknown.append("declared_energy_level_incomplete")
            elif (method, basis) != wanted:
                refuted.append(f"declared_energy_level:{record[0]['method']}/{record[0]['basis']}")
            else:
                declared = True

        corroborating = 0
        for level in calcs:
            method, basis = _norm(level["method"]), _norm(level["basis"])
            if method is None or basis is None:
                continue  # a calculation with no recorded level says nothing either way
            if (method, basis) != wanted:
                refuted.append(f"supporting_energy_calculation_level:{level['method']}/{level['basis']}")
            else:
                corroborating += 1
        for level in linked:
            method, basis = _norm(level["method"]), _norm(level["basis"])
            if method is None:
                continue
            if method != wanted[0]:
                refuted.append(f"linked_energy_level_names_another_method:{level['method']}")
            elif basis is None:
                continue  # the link cannot establish the basis, so it cannot verify (and does not contradict)
            elif basis != wanted[1]:
                refuted.append(f"linked_energy_level_basis_differs:{level['basis']}")
            else:
                corroborating += 1

        if declared and len(refuted) == before:
            satisfied.append("energy_level_verified" if corroborating else "energy_level_declared")

    # -- the pair ------------------------------------------------------------------------------

    def compatible(self, a: NormalizedKinetics, b: NormalizedKinetics) -> RuleMatch:
        if a.protocol_state != "valid" or b.protocol_state != "valid" or a.protocol is None or b.protocol is None:
            return RuleMatch(Tri.unknown, ("protocol_not_declared_on_both_sides",))
        refuted: list[str] = []
        unknown: list[str] = []
        satisfied: list[str] = []
        for name in COMPATIBILITY_FIELDS:
            left, right = a.protocol.get(name), b.protocol.get(name)
            if left is None or right is None:
                unknown.append(f"{name}_not_stated_on_both_sides")
            elif left != right:
                refuted.append(f"{name}_differs:{left}|{right}")
            else:
                satisfied.append(f"{name}_equal")
        left_dep, right_dep = a.protocol.get("departures"), b.protocol.get("departures")
        if left_dep is None or right_dep is None:
            unknown.append("departures_not_stated_on_both_sides")
        elif sorted(left_dep) != sorted(right_dep):
            refuted.append("departures_differ")
        else:
            satisfied.append("departures_equal")
        return _verdict(refuted, unknown, satisfied)

    # -- registry entry ------------------------------------------------------------------------

    def evidence_limits(self) -> list[dict[str, Any]]:
        return [dict(e) for e in self._manifest.evidence_limits]

    def exclusions(self) -> list[str]:
        return ["a reaction not in the manifest (no 'similar reaction' predicate)", "a zero-point-corrected barrier"]

    def describe(self) -> dict[str, Any]:
        entry = super().describe()
        entry["statement"] = self._manifest.rule_statement
        entry["manifest"] = self._manifest.describe()
        entry["manifest_sha256"] = self._manifest.sha256
        entry["compatibility_fields"] = [*COMPATIBILITY_FIELDS, "departures"]
        return entry


class InactiveCouncilRule(KineticsRule):
    """A council example that is registered but not audited from primary sources: no scope, no edge, with reasons.

    It exists so that the registry says plainly which examples were considered and why none is applied,
    rather than the example being silently absent or half-implemented.
    """

    def __init__(
        self,
        rule_id: str,
        *,
        objective: str,
        objective_key: str,
        citation: str,
        reasons: tuple[str, ...],
    ) -> None:
        self.rule_id = rule_id
        self.version = "0.0.0"
        self.objective = objective
        self.objective_key = objective_key
        self.evidence_type = "citation_only"
        self.interpretation = "Not audited: no comparison is applied."
        self.authority = "none"
        self._citation = citation
        self._reasons = reasons

    @property
    def status(self) -> str:
        return RULE_INACTIVE

    @property
    def inactive_reasons(self) -> tuple[str, ...]:  # type: ignore[override]
        return self._reasons

    def scope(self, subject: KineticsSubject, request: KineticsRequest) -> RuleMatch:
        return RuleMatch(Tri.unknown, ("rule_inactive",))

    def preferred_side(self, candidate: NormalizedKinetics) -> RuleMatch:
        return RuleMatch(Tri.unknown, ("rule_inactive",))

    def yielding_side(self, candidate: NormalizedKinetics) -> RuleMatch:
        return RuleMatch(Tri.unknown, ("rule_inactive",))

    def describe(self) -> dict[str, Any]:
        entry = super().describe()
        entry["citation"] = self._citation
        return entry


_ABSTRACT_ONLY = (
    "abstract-level evidence only: the exact channels, temperatures and controlled comparisons were not extracted, and "
    "the paper was not supplied for audit",
    "not activated: an example is never activated from an abstract",
)


@lru_cache(maxsize=1)
def default_rules() -> tuple[KineticsRule, ...]:
    """The rules registered in this release. None is active."""
    return (
        XYG3B3LYPBarrierRule(),
        InactiveCouncilRule(
            "K-HO2-TBA-MULTIPATH",
            objective="a full multipath treatment precedes a demonstrated inadequate path truncation of the same model",
            objective_key="path_treatment",
            citation="Bao, Sripa, Truhlar, Phys. Chem. Chem. Phys. 2016 (HO2 + tert-butanol), doi 10.1039/C5CP05780A",
            reasons=_ABSTRACT_ONLY,
        ),
        InactiveCouncilRule(
            "K-MBH-TUNNELING",
            objective="a scoped tunneling treatment preference for a multistructural CVT protocol",
            objective_key="tunneling_treatment",
            citation="Li et al., Phys. Chem. Chem. Phys. 2017 (methyl butenoate + H), doi 10.1039/C7CP01686G",
            reasons=_ABSTRACT_ONLY,
        ),
        InactiveCouncilRule(
            "K-ME-CONVERGENCE",
            objective="a converged master-equation solve precedes a demonstrated unconverged one for the same observable",
            objective_key="solve_convergence",
            citation="proposed evidence rule; RMG master-equation theory documentation is background, not a comparison",
            reasons=(
                "a proposed evidence rule with no source comparison",
                "needs controlled-convergence evidence and compatible fit and non-energy contributions",
            ),
        ),
    )


def active_rules(rules: tuple[KineticsRule, ...] | None = None) -> tuple[KineticsRule, ...]:
    """Rules with status ``active``; inactive and revoked rules are never applied."""
    return tuple(r for r in (default_rules() if rules is None else rules) if r.status == RULE_ACTIVE)
