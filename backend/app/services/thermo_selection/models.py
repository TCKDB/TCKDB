"""Values shared by the H298 candidate assessor and the ordering engine.

Everything here is plain data. The normalised candidate (:class:`NormalizedCandidate`)
is the whole of what the ordering engine may look at: public
refs, review state, timestamps and declared claims, never a database id or an ORM
row. That is what lets a decision manifest be replayed without a database.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.db.models.common import RecordReviewStatus, ThermoTargetKind
from app.schemas.reads.scientific_common import SelectionPolicy
from app.services.manifest_formats import new_current_only_registry, new_format_registry
from app.services.selection_kernel import Applicability, Outcome
from app.services.selection_kernel.requested import Eligibility

#: The one quantity this selector answers.
QUANTITY = "formation_enthalpy_298k"
#: The temperature, in kelvin, at which that quantity is defined.
REFERENCE_TEMPERATURE_K = 298.15
#: The only phase this selector answers for.
PHASE = "gas"
#: Candidate populations larger than this are not assessed; the search is refused instead.
MAX_CANDIDATES = 500
#: Version of the DECISION PROCEDURE only: outcomes and administrative ordering, which is what
#: ``replay_decision`` re-runs. How a candidate's eligibility is *assessed* (the evidence rubric version, the
#: source-finding gate) is a separate contract, ``ASSESSMENT_SEMANTICS_VERSION`` below, recorded in its own
#: structured manifest block; replay reads the recorded ``eligible`` flags and never reruns the assessment.
#: A decision manifest records both versions. The procedure carries no method-versus-method rule.
POLICY_NAME = "h298_selection"
#: A manifest made under another name or version is refused by ``replay_decision`` rather than silently re-answered.
#: The name changed with the removal of the method-preference rules (it was ``h298_method_preferred``).
POLICY_VERSION = 1
#: Version of how eligibility is assessed: an automated geometry-validation fail is advisory and a live confirmed
#: finding about a source calculation excludes the record (``structure_selection.source_findings``). Version 2: each of
#: those exclusions, and the rejected and deprecated review statuses, is a named default the request may lift, and the
#: recorded ``eligible`` flag is the verdict under the request's own settings (``selection_kernel.requested``). A manifest with no
#: block, or one recording a version the registry does not hold, is refused on replay.
ASSESSMENT_SEMANTICS_VERSION = 2
ASSESSMENT_SEMANTICS_NAME = "thermo_selection_assessment"
ASSESSMENT_SEMANTICS = new_current_only_registry(
    "thermo_selection_assessment_semantics", ASSESSMENT_SEMANTICS_NAME, ASSESSMENT_SEMANTICS_VERSION
)
#: A hidden ref in the captured facts is a placeholder token (`withheld:<n>`) under a profile with a review floor. Replay
#: reads exactly this format; a manifest in any other is refused. Version 2: the decision no longer holds rules,
#: rule matches, edges, overridden edges, opposing pairs or cycles. Version 3: the request records its filters and its
#: eligibility settings, each candidate records which defaults it failed or passed only by override, and the population
#: records the funnel. Version 4: the manifest records its ``read_profile`` and, in it, the review statuses whose
#: linked evidence that profile withheld (ADR 0023); under ``curated`` a not-reviewed or under-review record is shown
#: as evidence and only a rejected or deprecated one is a token.
MANIFEST_FORMAT_VERSION = 4
MANIFEST_FORMAT_NAME = "thermo_selection_manifest"
MANIFEST_FORMATS = new_format_registry(
    "thermo_selection_manifest_formats", MANIFEST_FORMAT_NAME, MANIFEST_FORMAT_VERSION
)


@dataclass(frozen=True)
class ThermoFilters:
    """The caller's filters, with the per-entry thermo browse's vocabulary and meaning (public refs only).

    :param model_kind: The stored model form (``scalar``, ``nasa``, ``nasa9``, ``wilhoit``, ``points``).
    :param level_of_theory_ref: ANY shown source calculation ran at this level of theory.
    :param software: ANY shown source calculation ran in a software of this name.
    """

    model_kind: str | None = None
    level_of_theory_ref: str | None = None
    software: str | None = None

    def to_record(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}

    @classmethod
    def from_record(cls, raw: dict[str, Any]) -> ThermoFilters:
        return cls(**raw)

    @property
    def active(self) -> bool:
        return any(v is not None for v in self.__dict__.values())


@dataclass(frozen=True)
class H298Request:
    """The normalised request: formation enthalpy at 298.15 K, gas phase, for one declared target.

    :param target_kind: ``equilibrium_ensemble`` or ``single_conformer``.
    :param conformer_group_id: The conformer group a ``single_conformer`` target names (same species entry);
        refused for an equilibrium target.
    :param min_review_status: Optional caller floor; the read-profile floor is applied on top.
    :param admin_policy: Administrative order of the eligible candidates. Display order, never a method preference.
    :param max_candidates: Population cap; the endpoint contract is 500.
    :param filters: The caller's filters, applied before eligibility.
    :param eligibility: The eligibility defaults the caller lifted.
    """

    target_kind: ThermoTargetKind
    conformer_group_id: int | None = None
    min_review_status: RecordReviewStatus | None = None
    admin_policy: SelectionPolicy = SelectionPolicy.default
    max_candidates: int = MAX_CANDIDATES
    filters: ThermoFilters = field(default_factory=ThermoFilters)
    eligibility: Eligibility = field(default_factory=Eligibility)

    def __post_init__(self) -> None:
        if self.target_kind is ThermoTargetKind.single_conformer and self.conformer_group_id is None:
            raise ValueError("a single_conformer target requires conformer_group_id")
        if self.target_kind is ThermoTargetKind.equilibrium_ensemble and self.conformer_group_id is not None:
            raise ValueError("an equilibrium_ensemble target does not name a conformer group")
        if self.max_candidates < 1:
            raise ValueError("max_candidates must be at least 1")


@dataclass(frozen=True)
class Subject:
    """The species entry every candidate belongs to: the identity context the manifest records."""

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
    """What the ordering engine knows about one eligible thermo record.

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

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "selected_ref": self.selected_ref,
            "basis": self.basis,
            "fronts": [list(f) for f in self.fronts],
            "administrative_order": list(self.administrative_order),
            "administrative_first": self.administrative_first,
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
