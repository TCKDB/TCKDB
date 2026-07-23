"""Typed public scientific-query responses derived from hosted OpenAPI.

The client deliberately keeps wire values as dictionaries.  These
``TypedDict`` models add static guidance without introducing a runtime schema
dependency or changing the objects returned by existing methods.
"""

from __future__ import annotations

from typing import Any, Generic, NotRequired, Required, TypeAlias, TypeVar, TypedDict

JSONDict: TypeAlias = dict[str, Any]


class ReproducibilityAssessmentSummary(TypedDict):
    """Compact immutable assessment identity and freshness state."""

    state: Required[str]
    assessment_ref: str | None
    rubric: str | None
    rubric_version: str | None
    grade: str | None
    assessed_at: str | None


class PublicAssessmentSummary(TypedDict):
    deterministic_trust: Required[JSONDict]
    reproducibility: Required[ReproducibilityAssessmentSummary]


class Pagination(TypedDict):
    offset: int
    limit: int
    returned: int
    total: int
    post_collapse_total: NotRequired[int]


class ScientificRequestEcho(TypedDict, total=False):
    filter: JSONDict
    sort: str
    collapse: str
    ranking: str
    include: list[str]


class ReviewStatusSummary(TypedDict, total=False):
    approved: int
    under_review: int
    not_reviewed: int
    deprecated: int
    rejected: int
    total: int


class ErrorEnvelope(TypedDict):
    code: str
    detail: object
    context: dict[str, Any]


RecordT = TypeVar("RecordT")


class ScientificSearchResponse(TypedDict, Generic[RecordT]):
    request: ScientificRequestEcho
    review_summary: ReviewStatusSummary
    records: list[RecordT]
    pagination: Pagination


class SpeciesRecord(TypedDict, total=False):
    species_ref: Required[str]
    canonical_smiles: Required[str]
    inchi_key: Required[str]
    charge: Required[int]
    multiplicity: Required[int]
    formula: str | None
    entries: list[JSONDict]
    species_id: int


class ReactionRecord(TypedDict, total=False):
    reaction_ref: Required[str]
    reaction_entry_ref: Required[str]
    equation: Required[str]
    matched_direction: Required[str]
    reversible: Required[bool]
    review: Required[JSONDict]
    reactants: Required[list[JSONDict]]
    products: Required[list[JSONDict]]
    availability: Required[JSONDict]
    family: str | None
    reaction_id: int
    reaction_entry_id: int


class ThermoSearchRecord(TypedDict):
    """One composed thermo-search row with resolved species context."""

    species: JSONDict
    thermo: JSONDict  # Nested wire block remains unexpanded for backward-compatible search typing.


class ThermoDetailRecord(TypedDict, total=False):
    """One flat record from the species-entry thermo subresource."""

    thermo_ref: Required[str]
    scientific_origin: Required[str]
    model_kind: Required[str]
    review: Required[JSONDict]
    evidence_completeness: Required[JSONDict]
    provenance: Required[JSONDict]
    thermo_id: int
    h298_kj_mol: float | None
    s298_j_mol_k: float | None
    temperature_coverage: JSONDict | None
    trust: JSONDict | None
    assessments: PublicAssessmentSummary | None


class KineticsSearchRecord(TypedDict):
    """One composed kinetics-search row with resolved reaction context."""

    reaction: JSONDict
    kinetics: JSONDict  # Nested wire block remains unexpanded for backward-compatible search typing.


class KineticsDetailRecord(TypedDict, total=False):
    """One flat record from the reaction-entry kinetics subresource."""

    kinetics_ref: Required[str]
    scientific_origin: Required[str]
    model_kind: Required[str]
    review: Required[JSONDict]
    parameters: Required[JSONDict]
    uncertainty: Required[JSONDict]
    evidence_completeness: Required[JSONDict]
    provenance: Required[JSONDict]
    kinetics_id: int
    direction: str | None
    pressure_bar: float | None
    temperature_coverage: JSONDict | None
    trust: JSONDict | None
    assessments: PublicAssessmentSummary | None


# Backward-compatible names for the composed search-row shapes published in
# tckdb-client 0.27.x. Detail methods now use explicit flat-record types.
ThermoRecord: TypeAlias = ThermoSearchRecord
KineticsRecord: TypeAlias = KineticsSearchRecord


class SpeciesCalculationRecord(TypedDict, total=False):
    species: Required[JSONDict]
    calculation: Required[JSONDict]
    geometry: Required[JSONDict]
    validation: Required[JSONDict]
    provenance: Required[JSONDict]
    energy: JSONDict | None
    level_of_theory: JSONDict | None
    software_release: JSONDict | None
    workflow_tool_release: JSONDict | None
    conformer: JSONDict | None


class NetworkStateCompositionParticipant(TypedDict):
    species_entry_ref: str
    species_ref: str
    canonical_smiles: str
    stoichiometry: int


class NetworkStateComposition(TypedDict):
    participants: list[NetworkStateCompositionParticipant]
    participant_count_total: int
    participants_truncated: bool


class NetworkStateSummary(TypedDict, total=False):
    composition_hash: Required[str]
    kind: Required[str]
    participant_count: Required[int]
    composition: Required[NetworkStateComposition]
    label: str | None
    network_state_id: int


class NetworkRecord(TypedDict, total=False):
    network: Required[JSONDict]
    evidence_summary: Required[JSONDict]
    available_sections: Required[JSONDict]
    species: list[JSONDict] | None
    reactions: list[JSONDict] | None
    states: list[NetworkStateSummary] | None
    channels: list[JSONDict] | None
    solves: list[JSONDict] | None
    kinetics: list[JSONDict] | None
    source_calculations: list[JSONDict] | None
    review_history: list[JSONDict] | None
    software_release: JSONDict | None
    workflow_tool_release: JSONDict | None
    literature: JSONDict | None


class NetworkSolveRecord(TypedDict, total=False):
    """One scientific network-solve record from search or detail reads."""

    network_solve: Required[JSONDict]
    network: Required[JSONDict]
    evidence_summary: Required[JSONDict]
    available_sections: Required[JSONDict]
    bath_gas: list[JSONDict] | None
    energy_transfer: list[JSONDict] | None
    source_calculations: list[JSONDict] | None
    kinetics: list[JSONDict] | None
    review_history: list[JSONDict] | None
    software_release: JSONDict | None
    workflow_tool_release: JSONDict | None
    literature: JSONDict | None


class NetworkKineticsRecord(TypedDict, total=False):
    network_kinetics: Required[JSONDict]
    network: Required[JSONDict]
    network_solve: Required[JSONDict]
    network_channel: Required[JSONDict]
    evidence_summary: Required[JSONDict]
    available_sections: Required[JSONDict]
    coefficients: JSONDict | None
    plog: list[JSONDict] | None
    plog_entry_count_total: int | None
    plog_entries_truncated: bool | None
    points: list[JSONDict] | None
    point_count_total: int | None
    points_truncated: bool | None
    source_calculations: list[JSONDict] | None
    review_history: list[JSONDict] | None


class StatmechRecord(TypedDict, total=False):
    statmech: Required[JSONDict]
    species: Required[JSONDict]
    evidence_summary: Required[JSONDict]
    available_sections: Required[JSONDict]
    frequency_scale_factor: JSONDict | None
    source_calculations: list[JSONDict] | None
    conformers: list[JSONDict] | None
    torsions: list[JSONDict] | None
    electronic_levels: list[JSONDict] | None
    frequencies: JSONDict | None
    review_history: list[JSONDict] | None
    software_release: JSONDict | None
    workflow_tool_release: JSONDict | None
    literature: JSONDict | None
    trust: JSONDict | None
    assessments: PublicAssessmentSummary | None


class TransportRecord(TypedDict, total=False):
    transport: Required[JSONDict]
    species: Required[JSONDict]
    evidence_summary: Required[JSONDict]
    available_sections: Required[JSONDict]
    source_calculations: list[JSONDict] | None
    review_history: list[JSONDict] | None
    software_release: JSONDict | None
    workflow_tool_release: JSONDict | None
    literature: JSONDict | None
    trust: JSONDict | None
    assessments: PublicAssessmentSummary | None


class ArtifactRecord(TypedDict, total=False):
    artifact: Required[JSONDict]
    calculation: Required[JSONDict]
    available_sections: Required[JSONDict]
    owner: JSONDict | None


SpeciesSearchResponse: TypeAlias = ScientificSearchResponse[SpeciesRecord]
ReactionSearchResponse: TypeAlias = ScientificSearchResponse[ReactionRecord]
ThermoSearchResponse: TypeAlias = ScientificSearchResponse[ThermoSearchRecord]


class SpeciesThermoResponse(ScientificSearchResponse[ThermoDetailRecord]):
    species_entry_ref: str
    species_entry_id: NotRequired[int]


KineticsSearchResponse: TypeAlias = ScientificSearchResponse[KineticsSearchRecord]


class ReactionKineticsResponse(ScientificSearchResponse[KineticsDetailRecord]):
    reaction_entry_ref: str
    reaction_entry_id: NotRequired[int]


SpeciesCalculationsSearchResponse: TypeAlias = ScientificSearchResponse[
    SpeciesCalculationRecord
]
NetworkSearchResponse: TypeAlias = ScientificSearchResponse[NetworkRecord]
NetworkSolveSearchResponse: TypeAlias = ScientificSearchResponse[NetworkSolveRecord]
NetworkKineticsSearchResponse: TypeAlias = ScientificSearchResponse[
    NetworkKineticsRecord
]
StatmechSearchResponse: TypeAlias = ScientificSearchResponse[StatmechRecord]
TransportSearchResponse: TypeAlias = ScientificSearchResponse[TransportRecord]
ArtifactSearchResponse: TypeAlias = ScientificSearchResponse[ArtifactRecord]


__all__ = [
    "ArtifactRecord",
    "ArtifactSearchResponse",
    "ErrorEnvelope",
    "JSONDict",
    "KineticsDetailRecord",
    "KineticsRecord",
    "KineticsSearchRecord",
    "KineticsSearchResponse",
    "NetworkKineticsRecord",
    "NetworkKineticsSearchResponse",
    "NetworkRecord",
    "NetworkSearchResponse",
    "NetworkSolveRecord",
    "NetworkSolveSearchResponse",
    "NetworkStateComposition",
    "NetworkStateCompositionParticipant",
    "NetworkStateSummary",
    "Pagination",
    "PublicAssessmentSummary",
    "ReproducibilityAssessmentSummary",
    "ReactionKineticsResponse",
    "ReactionRecord",
    "ReactionSearchResponse",
    "ReviewStatusSummary",
    "ScientificRequestEcho",
    "ScientificSearchResponse",
    "SpeciesCalculationRecord",
    "SpeciesCalculationsSearchResponse",
    "SpeciesRecord",
    "SpeciesSearchResponse",
    "SpeciesThermoResponse",
    "StatmechRecord",
    "StatmechSearchResponse",
    "ThermoRecord",
    "ThermoDetailRecord",
    "ThermoSearchRecord",
    "ThermoSearchResponse",
    "TransportRecord",
    "TransportSearchResponse",
]
