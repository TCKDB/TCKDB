"""Typed public scientific-query responses derived from hosted OpenAPI.

The client deliberately keeps wire values as dictionaries.  These
``TypedDict`` models add static guidance without introducing a runtime schema
dependency or changing the objects returned by existing methods.
"""

from __future__ import annotations

from typing import Any, Generic, NotRequired, Required, TypeAlias, TypeVar, TypedDict

JSONDict: TypeAlias = dict[str, Any]


class Pagination(TypedDict):
    offset: int
    limit: int
    returned: int
    total: int


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


class ThermoRecord(TypedDict):
    species: JSONDict
    thermo: JSONDict


class KineticsRecord(TypedDict):
    reaction: JSONDict
    kinetics: JSONDict


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


class NetworkRecord(TypedDict, total=False):
    network: Required[JSONDict]
    evidence_summary: Required[JSONDict]
    available_sections: Required[JSONDict]
    species: list[JSONDict] | None
    reactions: list[JSONDict] | None
    states: list[JSONDict] | None
    channels: list[JSONDict] | None
    solves: list[JSONDict] | None
    kinetics: list[JSONDict] | None
    source_calculations: list[JSONDict] | None
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


class ArtifactRecord(TypedDict, total=False):
    artifact: Required[JSONDict]
    calculation: Required[JSONDict]
    available_sections: Required[JSONDict]
    owner: JSONDict | None


SpeciesSearchResponse: TypeAlias = ScientificSearchResponse[SpeciesRecord]
ReactionSearchResponse: TypeAlias = ScientificSearchResponse[ReactionRecord]
ThermoSearchResponse: TypeAlias = ScientificSearchResponse[ThermoRecord]


class SpeciesThermoResponse(ScientificSearchResponse[ThermoRecord]):
    species_entry_ref: str
    species_entry_id: NotRequired[int]


KineticsSearchResponse: TypeAlias = ScientificSearchResponse[KineticsRecord]


class ReactionKineticsResponse(ScientificSearchResponse[KineticsRecord]):
    reaction_entry_ref: str
    reaction_entry_id: NotRequired[int]


SpeciesCalculationsSearchResponse: TypeAlias = ScientificSearchResponse[
    SpeciesCalculationRecord
]
NetworkSearchResponse: TypeAlias = ScientificSearchResponse[NetworkRecord]
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
    "KineticsRecord",
    "KineticsSearchResponse",
    "NetworkKineticsRecord",
    "NetworkKineticsSearchResponse",
    "NetworkRecord",
    "NetworkSearchResponse",
    "Pagination",
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
    "ThermoSearchResponse",
    "TransportRecord",
    "TransportSearchResponse",
]
