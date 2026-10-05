"""Values shared by the H298 candidate assessor, the rule registry and the preference engine.

Everything here is plain data. The normalised candidate (:class:`NormalizedCandidate`)
is the whole of what a preference rule and the ordering engine may look at: public
refs, review state, timestamps and declared claims, never a database id or an ORM
row. That is what lets a decision manifest be replayed without a database.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.db.models.common import RecordReviewStatus, ThermoTargetKind
from app.schemas.reads.scientific_common import SelectionPolicy
from app.services.selection_kernel import (  # noqa: F401  (re-exported: the vocabulary moved to the kernel)
    Applicability,
    Edge,
    Outcome,
    RuleMatch,
    Tri,
)

#: The one quantity this selector answers.
QUANTITY = "formation_enthalpy_298k"
#: The temperature, in kelvin, at which that quantity is defined.
REFERENCE_TEMPERATURE_K = 298.15
#: The only phase this selector answers for.
PHASE = "gas"
#: Candidate populations larger than this are not assessed; the search is refused instead.
MAX_CANDIDATES = 500
#: Version of the DECISION PROCEDURE only: outcomes, front construction and ordering, which is what
#: ``replay_decision`` re-runs. How a candidate's eligibility is *assessed* (the evidence rubric version, the
#: source-finding gate) is a separate contract, ``ASSESSMENT_SEMANTICS_VERSION`` below, recorded in its own
#: structured manifest block; replay reads the recorded ``eligible`` flags and never reruns the assessment.
#: A decision manifest records both versions.
POLICY_NAME = "h298_method_preferred"
#: 2: a superseded rule is removed before conflicts are judged (the shared kernel's fix; it cannot change an answer
#: while the registry holds one rule, but a manifest made under version 1 was decided under the old semantics and
#: is refused by ``replay_decision`` rather than silently re-answered).
POLICY_VERSION = "2"
#: Version of how eligibility is assessed. 2: an automated geometry-validation fail is advisory (computed rubrics
#: at version 2 / transition state 3) and a live confirmed finding about a source calculation excludes the record
#: (``structure_selection.source_findings``). A manifest with no block was made under ``pre_v2_assessment``.
ASSESSMENT_SEMANTICS_VERSION = "2"
SUPPORTED_ASSESSMENT_SEMANTICS = frozenset({"pre_v2_assessment", ASSESSMENT_SEMANTICS_VERSION})
MANIFEST_FORMAT_VERSION = 1


@dataclass(frozen=True)
class H298Request:
    """The normalised request: formation enthalpy at 298.15 K, gas phase, for one declared target.

    :param target_kind: ``equilibrium_ensemble`` or ``single_conformer``.
    :param conformer_group_id: The conformer group a ``single_conformer`` target names (same species entry);
        refused for an equilibrium target.
    :param min_review_status: Optional caller floor; the read-profile floor is applied on top.
    :param admin_policy: Administrative order within a preference front. Never reorders fronts.
    :param max_candidates: Population cap; the endpoint contract is 500.
    """

    target_kind: ThermoTargetKind
    conformer_group_id: int | None = None
    min_review_status: RecordReviewStatus | None = None
    admin_policy: SelectionPolicy = SelectionPolicy.default
    max_candidates: int = MAX_CANDIDATES

    def __post_init__(self) -> None:
        if self.target_kind is ThermoTargetKind.single_conformer and self.conformer_group_id is None:
            raise ValueError("a single_conformer target requires conformer_group_id")
        if self.target_kind is ThermoTargetKind.equilibrium_ensemble and self.conformer_group_id is not None:
            raise ValueError("an equilibrium_ensemble target does not name a conformer group")
        if self.max_candidates < 1:
            raise ValueError("max_candidates must be at least 1")


@dataclass(frozen=True)
class Subject:
    """The species entry every candidate belongs to, as the rules need to see it."""

    species_entry_ref: str
    inchi_key: str
    molecular_formula: str | None
    charge: int
    multiplicity: int
    isotope_key: str | None
    electronic_state_kind: str
    entry_kind: str

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Subject:
        return cls(**raw)


@dataclass(frozen=True)
class NormalizedCandidate:
    """What the ordering engine and the rules know about one eligible thermo record.

    Built from the record's own columns and the links it actually holds; a thermo's evidence
    is only what it links (#645/#646), so nothing here comes from a sibling record of the
    same species entry. ``id_rank`` is the record's position when the population is ordered
    by database id: it stands in for the id as the last tie-break and is not an id.

    :param protocol_state: ``absent`` (no declaration), ``valid`` (parsed) or ``unreadable``
        (stored but not a version-1 declaration this server can read).
    :param linked_recipe_keys: Catalogue keys of the named composite methods the record's own
        declared energy level and own source calculations carry. Empty when none does.
    """

    thermo_ref: str
    review_status: RecordReviewStatus
    created_at: datetime
    id_rank: int
    scientific_origin: str
    phase: str | None
    enthalpy_reference_kind: str | None
    target_kind: str | None
    target_group_ref: str | None
    protocol_state: str
    protocol: dict[str, Any] | None
    linked_recipe_keys: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "thermo_ref": self.thermo_ref,
            "review_status": self.review_status.value,
            "created_at": self.created_at.isoformat(),
            "id_rank": self.id_rank,
            "scientific_origin": self.scientific_origin,
            "phase": self.phase,
            "enthalpy_reference_kind": self.enthalpy_reference_kind,
            "target_kind": self.target_kind,
            "target_group_ref": self.target_group_ref,
            "protocol_state": self.protocol_state,
            "protocol": self.protocol,
            "linked_recipe_keys": list(self.linked_recipe_keys),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> NormalizedCandidate:
        return cls(
            thermo_ref=raw["thermo_ref"],
            review_status=RecordReviewStatus(raw["review_status"]),
            created_at=datetime.fromisoformat(raw["created_at"]),
            id_rank=int(raw["id_rank"]),
            scientific_origin=raw["scientific_origin"],
            phase=raw["phase"],
            enthalpy_reference_kind=raw["enthalpy_reference_kind"],
            target_kind=raw["target_kind"],
            target_group_ref=raw["target_group_ref"],
            protocol_state=raw["protocol_state"],
            protocol=raw["protocol"],
            linked_recipe_keys=tuple(raw["linked_recipe_keys"]),
        )


@dataclass(frozen=True)
class Reason:
    """One finding that moved a candidate away from ``applicable``."""

    code: str
    applicability: Applicability

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "applicability": self.applicability.value}


@dataclass(frozen=True)
class CandidateAssessment:
    """The H298 applicability verdict for one record, with everything that fed it.

    :param representations: One entry per stored representation tried, in a fixed order
        (``h298`` scalar, ``point`` at exactly 298.15 K, ``nasa7``, ``nasa9``, ``wilhoit``):
        its value in kJ/mol or the reason it cannot answer. Disclosed whether or not another
        representation answered.
    :param blocking: Validation failures that exclude the record (a hard-failed evidence
        evaluation). Excluding, not advisory.
    :param advisory: Findings disclosed without excluding the record.
    """

    thermo_ref: str
    applicability: Applicability
    reasons: tuple[Reason, ...]
    answer_representation: str | None
    value_kj_mol: float | None
    representations: tuple[dict[str, Any], ...]
    blocking: tuple[str, ...] = ()
    advisory: tuple[str, ...] = ()

    @property
    def physically_eligible(self) -> bool:
        return self.applicability is Applicability.applicable and not self.blocking

    def to_dict(self) -> dict[str, Any]:
        return {
            "thermo_ref": self.thermo_ref,
            "applicability": self.applicability.value,
            "reasons": [r.to_dict() for r in self.reasons],
            "answer_representation": self.answer_representation,
            "value_kj_mol": self.value_kj_mol,
            "representations": [dict(r) for r in self.representations],
            "blocking": list(self.blocking),
            "advisory": list(self.advisory),
            "physically_eligible": self.physically_eligible,
        }


@dataclass(frozen=True)
class Decision:
    """The ordering engine's verdict over a set of eligible candidates (public refs only)."""

    outcome: Outcome
    selected_ref: str | None
    basis: str
    fronts: tuple[tuple[str, ...], ...]
    administrative_order: tuple[str, ...]
    administrative_first: dict[str, str] | None
    edges: tuple[Edge, ...]
    overridden_edges: tuple[dict[str, Any], ...]
    opposing_pairs: tuple[dict[str, Any], ...]
    cycles: tuple[tuple[str, ...], ...]
    rule_matches: tuple[dict[str, Any], ...]
    rules: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "selected_ref": self.selected_ref,
            "basis": self.basis,
            "fronts": [list(f) for f in self.fronts],
            "administrative_order": list(self.administrative_order),
            "administrative_first": self.administrative_first,
            "edges": [e.to_dict() for e in self.edges],
            "overridden_edges": [dict(e) for e in self.overridden_edges],
            "opposing_pairs": [dict(p) for p in self.opposing_pairs],
            "cycles": [list(c) for c in self.cycles],
            "rule_matches": [dict(m) for m in self.rule_matches],
            "rules": [dict(r) for r in self.rules],
        }


@dataclass(frozen=True)
class H298Selection:
    """The selection service's result.

    ``manifest`` is the replayable decision manifest (JSON-ready, public refs only).
    The other fields are views onto it for callers that do not want to dig.
    """

    outcome: Outcome
    selected_ref: str | None
    assessments: tuple[CandidateAssessment, ...]
    decision: Decision | None
    manifest: dict[str, Any]
    unresolved_refs: tuple[str, ...] = ()
    unsupported_refs: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)
