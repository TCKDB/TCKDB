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

from app.chemistry.manifest_attestation import is_attested
from app.chemistry.thermo_rules.e1_manifest import (
    MATCH_CONNECTIVITY_AND_FORMULA,
    E1Manifest,
    E1Member,
    ManifestError,
    load_e1_manifest,
)
from app.services.thermo_selection.models import QUANTITY, NormalizedCandidate, RuleMatch, Subject, Tri

RULE_ACTIVE = "active"
RULE_REVOKED = "revoked"

E1_RULE_ID = "E1"
#: Version of the rule's matching logic. Bump it on any change to :class:`E1Rule`, and whenever the
#: manifest version changes; a decision manifest records it so a replay can refuse a different one.
E1_RULE_VERSION = "1.0.0"

#: SHA-256 of ``e1_g4_over_g3_manifest.yaml`` as approved by the curator (manifest 1.0.0). The rule refuses
#: to load over any other bytes; a change to a member or a recipe fact is a new manifest version, a new
#: pin and a new ``E1_RULE_VERSION``.
E1_MANIFEST_SHA256 = "f2265d40e1d314985b7f23c9cba98fe0efaf6327980789071e19a400830d25a0"

_ATOMIZATION = "atomization"
#: The route the benchmark used (manifest recipe_facts.formation_enthalpy and molecular_thermal_correction):
#: harmonic RRHO with every mode harmonic, one (lowest) conformer, JANAF atomic data. ATcT and JANAF carbon
#: differ by about 0.04 kcal/mol per carbon, about 0.4 for the C10 members, which exceeds the G3 to G4 margin.
#: CODATA key values for C(g) and H(g) agree with JANAF within their stated uncertainties (716.68 vs 716.67
#: kJ/mol for C(g)), so CODATA counts as the benchmark source; ATcT and ``other`` do not.
_BENCHMARK_COMPONENTS = (
    ("internal_motion", ("harmonic",)),
    ("ensemble_representation", ("lowest_conformer",)),
    ("reference_data_source", ("nist_janaf", "codata")),
)


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
    * the benchmark route, component by component (harmonic internal motion, the single lowest
      conformer, JANAF or CODATA atomic data = ``nist_janaf`` or ``codata``): stated and equal is true, stated and different
      (hindered rotors, anharmonic, Boltzmann conformers, ATcT, other) is a no, unstated is
      unknown. Saying less never earns an edge;
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
        if manifest is None:
            manifest = load_e1_manifest(expected_sha256=E1_MANIFEST_SHA256)
        elif manifest.sha256 != E1_MANIFEST_SHA256:
            raise ManifestError(
                f"E1 manifest content does not match its pinned digest (expected {E1_MANIFEST_SHA256}, got {manifest.sha256})"
            )
        elif not is_attested(manifest):
            # The digest field survives dataclasses.replace; what the manifest holds does not have to.
            raise ManifestError("E1 manifest is not what the pinned file parses to: its content was edited after loading")
        self._manifest = manifest

    @property
    def manifest(self) -> E1Manifest:
        return self._manifest

    # -- scope ---------------------------------------------------------------------------------

    @staticmethod
    def _identity(member: E1Member, subject: Subject) -> Tri:
        if member.match_mode == MATCH_CONNECTIVITY_AND_FORMULA:
            if subject.inchi_key.split("-", 1)[0] != member.connectivity_block:
                return Tri.false
            if subject.molecular_formula is None:  # RDKit could not read the SMILES: unknowable, not a refusal
                return Tri.unknown
            return Tri.true if subject.molecular_formula == member.molecular_formula else Tri.false
        return Tri.true if subject.inchi_key == member.inchikey else Tri.false

    def scope(self, subject: Subject) -> RuleMatch:
        if subject.isotope_key is not None:
            return RuleMatch(Tri.false, ("isotopologue_is_not_a_manifest_member",))
        if subject.entry_kind != "minimum":
            return RuleMatch(Tri.false, (f"entry_kind_not_minimum:{subject.entry_kind}",))
        verdicts = [(m, self._identity(m, subject)) for m in self._manifest.members]
        same_identity = [m for m, t in verdicts if t is Tri.true]
        undecidable = [m for m, t in verdicts if t is Tri.unknown]
        if not same_identity:
            if undecidable:
                return RuleMatch(Tri.unknown, (f"formula_not_derivable_for_connectivity_match:{undecidable[0].member_id}",))
            return RuleMatch(Tri.false, ("species_not_in_manifest",))
        for member in same_identity:
            if member.charge == subject.charge and member.multiplicity == subject.multiplicity:
                # A state-specific member (CH2(1A1)) is told apart by its multiplicity; the manifest itself
                # calls it an excited singlet, so an electronic-state label must not refute it. Every other
                # member is the ground state.
                if subject.electronic_state_kind != "ground" and not member.state_specific:
                    return RuleMatch(Tri.false, (f"electronic_state_kind_not_ground:{subject.electronic_state_kind}",))
                return RuleMatch(
                    Tri.true, (f"manifest_member:{member.member_id}", f"membership_evidence:{member.membership_evidence}")
                )
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

        # The benchmark route is fixed: harmonic RRHO, the single lowest conformer, JANAF atomic data.
        # Each component is stated-and-equal (true), stated-and-different (false) or unstated (unknown):
        # saying less never earns an edge.
        thermal = protocol.get("thermal_approximation") or {}
        for field, benchmark in _BENCHMARK_COMPONENTS:
            container = formation if field == "reference_data_source" else thermal
            value = (container or {}).get(field)
            if value is None:
                unknown.append(f"{field}_not_stated")
            elif value not in benchmark:
                refuted.append(f"{field}_differs_from_benchmark:{value}")

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
        entry["manifest_sha256"] = self._manifest.sha256
        entry["benchmark_route"] = {field: list(accepted) for field, accepted in _BENCHMARK_COMPONENTS}
        entry["benchmark_route"]["formation_derivation"] = _ATOMIZATION
        entry["benchmark_route_rule"] = (
            "each component: stated and equal is true, stated and different is false, unstated is unknown"
        )
        return entry


@lru_cache(maxsize=1)
def default_rules() -> tuple[PreferenceRule, ...]:
    """The rules registered in this release. E1 only."""
    return (E1Rule(),)


def active_rules(rules: tuple[PreferenceRule, ...] | None = None) -> tuple[PreferenceRule, ...]:
    """Rules with status ``active``; revoked rules are never applied."""
    return tuple(r for r in (default_rules() if rules is None else rules) if r.status == RULE_ACTIVE)
