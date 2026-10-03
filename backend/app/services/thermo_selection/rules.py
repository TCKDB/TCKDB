"""The versioned, typed registry of contextual preference rules, shipped with the application.

A rule is code plus data that ships with a release: it is not a database row, and it is
separate from level-of-theory identities and from release selections. Registering one
means adding it to :func:`default_rules`; revoking one means setting its ``status``.
A rule states, for one quantity, which records it prefers over which others and where
that holds. It never scores a method and never compares across what it was not written
to cover: for each candidate it answers true, false or unknown, and only true on both
sides makes an edge. Unknown makes nothing and is disclosed.

One rule is registered: **E1, standard G4 over standard G3** for formation enthalpy at
298.15 K, restricted to the audited benchmark membership in
``app/chemistry/thermo_rules/e1_g4_over_g3_manifest.yaml``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from functools import lru_cache
from typing import Any

from app.chemistry.thermo_rules.e1_manifest import (
    MATCH_CONNECTIVITY_AND_FORMULA,
    E1Manifest,
    E1Member,
    load_e1_manifest,
)
from app.services.thermo_selection.models import QUANTITY, NormalizedCandidate, RuleMatch, Subject, Tri

RULE_ACTIVE = "active"
RULE_REVOKED = "revoked"

E1_RULE_ID = "E1"
#: Version of the rule's matching logic. Bump it on any change to :class:`E1Rule`, and whenever the
#: manifest version changes; a decision manifest records it so a replay can refuse a different one.
E1_RULE_VERSION = "1.0.0"

_ATOMIZATION = "atomization"
#: A thermal treatment beyond harmonic RRHO is a departure from the benchmarked recipes.
_BEYOND_RRHO = frozenset({"hindered_rotors", "anharmonic"})


class PreferenceRule(ABC):
    """One scoped, attributed comparison: ``preferred_side`` records precede ``yielding_side`` records.

    :cvar rule_id: Stable id. :cvar version: Version of this rule's logic and data.
    :cvar quantity: The quantity it compares on; a rule for another quantity is never applied.
    :cvar supersedes: Ids of rules whose opposing preference this one overrides when the two
        disagree. The only way a conflict resolves; recency and apparent specificity never do.
    """

    rule_id: str
    version: str
    status: str = RULE_ACTIVE
    quantity: str = QUANTITY
    objective: str = ""
    authority: str = ""
    evidence_type: str = ""
    interpretation: str = ""
    supersedes: tuple[str, ...] = ()

    @abstractmethod
    def scope(self, subject: Subject) -> RuleMatch:
        """Whether the rule's chemical scope contains this species/state."""

    @abstractmethod
    def preferred_side(self, candidate: NormalizedCandidate) -> RuleMatch:
        """Whether the candidate has the protocol this rule prefers."""

    @abstractmethod
    def yielding_side(self, candidate: NormalizedCandidate) -> RuleMatch:
        """Whether the candidate has the protocol this rule ranks below the preferred one."""

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
            "quantity": self.quantity,
            "objective": self.objective,
            "authority": self.authority,
            "evidence_type": self.evidence_type,
            "interpretation": self.interpretation,
            "supersedes": list(self.supersedes),
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


class E1Rule(PreferenceRule):
    """E1: faithfully executed standard G4 precedes standard G3 for audited hydrocarbon H298 records.

    Both sides require, of the record's own claims and links:

    * a computed origin;
    * a declared recipe that is exactly the standard one (``g4`` or ``g3``): G4(MP2), G4(complete),
      G3(MP2), G3B3, G3X and any ``other`` recipe do not match;
    * ``departures`` stated as an explicit empty list: omitting it is unknown, naming any is a no;
    * a formation enthalpy derived by atomization, the route the benchmark used: another derivation
      is a no, an undeclared one is unknown;
    * no thermal treatment beyond harmonic RRHO, which the benchmarked recipes use;
    * no contradiction from the record's own linked levels: a linked named composite method that is
      not the declared recipe is a no.

    A matching label alone does not prove the recipe was followed: the declaration is the
    depositor's claim, so a record resting on it alone is disclosed as ``recipe_declaration_only``
    rather than ``recipe_corroborated_by_linked_level``. Applied energy corrections are not read:
    they attach to a species entry, not to a thermo record, and borrowing them would break the
    never-borrow rule. A correction the depositor made is a declared departure.
    """

    rule_id = E1_RULE_ID
    version = E1_RULE_VERSION
    objective = (
        "expected accuracy of the gas-phase standard enthalpy of formation at 298.15 K, "
        "derived by atomization, for the audited hydrocarbon benchmark species"
    )
    authority = "repository owner, acting as curator (manifest 1.0.0, 2026-10-03)"
    evidence_type = "domain_benchmark_preference_development_set"
    interpretation = (
        "A development-set average-deviation comparison supports an expected-performance judgment "
        "within the audited species set. It is not a per-molecule guarantee or a per-record uncertainty, "
        "and it does not make the preferred record a curator recommendation."
    )

    def __init__(self, manifest: E1Manifest | None = None) -> None:
        self._manifest = manifest or load_e1_manifest()

    @property
    def manifest(self) -> E1Manifest:
        return self._manifest

    # -- scope ---------------------------------------------------------------------------------

    def _identity_matches(self, member: E1Member, subject: Subject) -> bool:
        if member.match_mode == MATCH_CONNECTIVITY_AND_FORMULA:
            block = subject.inchi_key.split("-", 1)[0]
            return block == member.connectivity_block and subject.molecular_formula == member.molecular_formula
        return subject.inchi_key == member.inchikey

    def scope(self, subject: Subject) -> RuleMatch:
        if subject.isotope_key is not None:
            return RuleMatch(Tri.false, ("isotopologue_is_not_a_manifest_member",))
        if subject.entry_kind != "minimum":
            return RuleMatch(Tri.false, (f"entry_kind_not_minimum:{subject.entry_kind}",))
        if subject.electronic_state_kind != "ground":
            return RuleMatch(Tri.false, (f"electronic_state_kind_not_ground:{subject.electronic_state_kind}",))
        same_identity = [m for m in self._manifest.members if self._identity_matches(m, subject)]
        if not same_identity:
            return RuleMatch(Tri.false, ("species_not_in_manifest",))
        for member in same_identity:
            if member.charge == subject.charge and member.multiplicity == subject.multiplicity:
                return RuleMatch(Tri.true, (f"manifest_member:{member.member_id}", f"membership_evidence:{member.membership_evidence}"))
        member = same_identity[0]
        if member.multiplicity != subject.multiplicity:
            return RuleMatch(
                Tri.false,
                (f"multiplicity_differs_from_member:{member.member_id}:{subject.multiplicity}_not_{member.multiplicity}",),
            )
        return RuleMatch(Tri.false, (f"charge_differs_from_member:{member.member_id}",))

    # -- sides ---------------------------------------------------------------------------------

    def preferred_side(self, candidate: NormalizedCandidate) -> RuleMatch:
        return self._side(candidate, "g4")

    def yielding_side(self, candidate: NormalizedCandidate) -> RuleMatch:
        return self._side(candidate, "g3")

    def _side(self, candidate: NormalizedCandidate, wanted: str) -> RuleMatch:
        refuted: list[str] = []
        unknown: list[str] = []
        satisfied: list[str] = []

        if candidate.scientific_origin != "computed":
            refuted.append(f"origin_not_computed:{candidate.scientific_origin}")
        if candidate.protocol_state == "absent":
            unknown.append("protocol_not_declared")
        elif candidate.protocol_state != "valid" or candidate.protocol is None:
            unknown.append("protocol_declaration_unreadable")
        else:
            self._check_protocol(candidate.protocol, wanted, refuted, unknown)
        self._check_linked_levels(candidate, wanted, refuted, satisfied)
        if not refuted and not unknown:
            satisfied.insert(0, f"standard_{wanted}_declared_without_departures_by_atomization")
        return _verdict(refuted, unknown, satisfied)

    @staticmethod
    def _check_protocol(protocol: dict[str, Any], wanted: str, refuted: list[str], unknown: list[str]) -> None:
        recipe = protocol.get("recipe")
        if recipe is None:
            unknown.append("recipe_not_declared")
        elif recipe.get("name") == "other":
            refuted.append(f"recipe_declared_other:{recipe.get('other_name')}")
        elif recipe.get("name") != wanted:
            refuted.append(f"recipe_declared:{recipe.get('name')}")

        departures = protocol.get("departures")
        if departures is None:
            unknown.append("departures_not_stated")
        elif departures:
            components = sorted({d["component"] for d in departures})
            refuted.append(f"departures_declared:{','.join(components)}")

        formation = protocol.get("formation_reference")
        if formation is None:
            unknown.append("formation_reference_not_declared")
        elif formation.get("derivation") != _ATOMIZATION:
            refuted.append(f"formation_derivation_not_atomization:{formation.get('derivation')}")

        motion = (protocol.get("thermal_approximation") or {}).get("internal_motion")
        if motion in _BEYOND_RRHO:
            refuted.append(f"thermal_treatment_beyond_recipe:{motion}")

    @staticmethod
    def _check_linked_levels(
        candidate: NormalizedCandidate, wanted: str, refuted: list[str], satisfied: list[str]
    ) -> None:
        keys = sorted(set(candidate.linked_recipe_keys))
        others = [k for k in keys if k != wanted]
        if others:
            refuted.append(f"linked_level_names_other_recipe:{','.join(others)}")
        elif keys:
            satisfied.append("recipe_corroborated_by_linked_level")
        else:
            satisfied.append("recipe_declaration_only")

    # -- registry entry ------------------------------------------------------------------------

    def evidence_limits(self) -> list[dict[str, Any]]:
        return [dict(e) for e in self._manifest.evidence_limits]

    def exclusions(self) -> list[str]:
        return list(self._manifest.scope_conditions.get("excluded_by_construction", []))

    def exceptions(self) -> list[str]:
        return []

    def describe(self) -> dict[str, Any]:
        entry = super().describe()
        entry["statement"] = self._manifest.rule_statement
        entry["manifest"] = self._manifest.describe()
        return entry


@lru_cache(maxsize=1)
def default_rules() -> tuple[PreferenceRule, ...]:
    """The rules registered in this release. E1 only."""
    return (E1Rule(),)


def active_rules(rules: tuple[PreferenceRule, ...] | None = None) -> tuple[PreferenceRule, ...]:
    """Rules with status ``active``; revoked rules are never applied."""
    return tuple(r for r in (default_rules() if rules is None else rules) if r.status == RULE_ACTIVE)
