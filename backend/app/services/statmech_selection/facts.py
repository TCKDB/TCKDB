"""The normalised facts a statmech assessment reads: plain values over public refs, with a JSON round trip.

Nothing here is a database id or an ORM row. That is what lets a manifest carry exactly what an assessment looked at and
a replay recompute the assessment from it without a database. A fact a record does not state is ``None`` (or an empty
tuple where a collection is meant), never a default: an omitted scale application is not "unscaled", an omitted
multiplicity is not one.

``id_rank`` is a candidate's position when the population is ordered by database id. It stands in for the id as the
last administrative tie-break and cannot be turned back into one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.db.models.common import RecordReviewStatus
from app.services.statmech_selection.legacy import LegacyFacts
from app.services.statmech_selection.vocabulary import CandidateKind, OwnerKind


@dataclass(frozen=True)
class SubjectFacts:
    """The entry every candidate belongs to, with the identity facts a claim is judged against."""

    kind: OwnerKind
    entry_ref: str
    stationary_point_kind: str | None
    electronic_state_kind: str | None
    isotope_key: str | None
    charge: int | None
    multiplicity: int | None
    #: True/False when the species' identity says so; ``None`` when it does not (a transition state is never an atom).
    is_atom: bool | None

    def to_dict(self) -> dict[str, Any]:
        return {**self.__dict__, "kind": self.kind.value}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> SubjectFacts:
        return cls(**{**raw, "kind": OwnerKind(raw["kind"])})


@dataclass(frozen=True)
class ScaleFactorFacts:
    ref: str
    kind: str
    value: float

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ScaleFactorFacts:
        return cls(**raw)


@dataclass(frozen=True)
class TorsionFacts:
    torsion_index: int
    treatment_kind: str | None
    dimension: int
    symmetry_number: int | None
    invalidated_reason: str | None
    scan_calculation_ref: str | None

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> TorsionFacts:
        return cls(**raw)


@dataclass(frozen=True)
class RecordFacts:
    """What the statmech row itself, and the rows it owns, state."""

    statmech_ref: str
    scientific_origin: str
    rigid_rotor_kind: str | None
    is_linear: bool | None
    n_rotational_constants: int
    external_symmetry: int | None
    optical_isomers: int | None
    statmech_treatment: str | None
    uses_projected_frequencies: bool | None
    scale_factor: ScaleFactorFacts | None
    #: ``(level_index, energy_cm1, degeneracy)``.
    electronic_levels: tuple[tuple[int, float, int], ...]
    torsions: tuple[TorsionFacts, ...]
    #: ``(role, calculation_ref)`` of the legacy source links.
    source_links: tuple[tuple[str, str], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.__dict__,
            "scale_factor": self.scale_factor.to_dict() if self.scale_factor else None,
            "electronic_levels": [list(level) for level in self.electronic_levels],
            "torsions": [t.to_dict() for t in self.torsions],
            "source_links": [list(link) for link in self.source_links],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> RecordFacts:
        return cls(
            **{
                **raw,
                "scale_factor": ScaleFactorFacts.from_dict(raw["scale_factor"]) if raw["scale_factor"] else None,
                "electronic_levels": tuple((int(i), float(e), int(g)) for i, e, g in raw["electronic_levels"]),
                "torsions": tuple(TorsionFacts.from_dict(t) for t in raw["torsions"]),
                "source_links": tuple((r, c) for r, c in raw["source_links"]),
            }
        )


@dataclass(frozen=True)
class SourceFacts:
    """One declared source, resolved. ``unavailable_reason`` is set when the calculation it names is not available to
    this request (hidden by the read profile, below a floor, terminal): the role then reads as unavailable with no
    ref and no reason that would name a record the caller cannot see."""

    source_key: str
    role: str
    member_key: str | None
    locator: str
    calculation_ref: str | None
    geometry_ref: str | None
    literature_ref: str | None
    scale_factor: ScaleFactorFacts | None
    unavailable_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {**self.__dict__, "scale_factor": self.scale_factor.to_dict() if self.scale_factor else None}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> SourceFacts:
        return cls(**{**raw, "scale_factor": ScaleFactorFacts.from_dict(raw["scale_factor"]) if raw["scale_factor"] else None})


@dataclass(frozen=True)
class MemberFacts:
    """One declared ensemble member. ``review_status`` is ``None`` when the member's own record is not available to
    this request (hidden, or no longer there): a required member that cannot be seen cannot be counted."""

    member_key: str
    statmech_ref: str | None
    conformer_group_ref: str | None
    counting_multiplicity: int | None
    relative_energy_kj_mol: float | None
    region_key: str | None
    review_status: RecordReviewStatus | None

    def to_dict(self) -> dict[str, Any]:
        return {**self.__dict__, "review_status": self.review_status.value if self.review_status else None}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> MemberFacts:
        status = RecordReviewStatus(raw["review_status"]) if raw["review_status"] else None
        return cls(**{**raw, "review_status": status})


@dataclass(frozen=True)
class DeterminationFacts:
    ref: str
    owner_ref: str
    target_kind: str
    evaluated_geometry_ref: str
    review_status: RecordReviewStatus | None

    def to_dict(self) -> dict[str, Any]:
        return {**self.__dict__, "review_status": self.review_status.value if self.review_status else None}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> DeterminationFacts:
        status = RecordReviewStatus(raw["review_status"]) if raw["review_status"] else None
        return cls(**{**raw, "review_status": status})


@dataclass(frozen=True)
class DeclarationFacts:
    ref: str
    schema_version: int
    scope_kind: str
    conformer_group_ref: str | None
    determination_ref: str | None
    recipe: dict[str, Any]
    content_hash: str
    members: tuple[MemberFacts, ...]
    sources: tuple[SourceFacts, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.__dict__,
            "members": [m.to_dict() for m in self.members],
            "sources": [s.to_dict() for s in self.sources],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> DeclarationFacts:
        return cls(
            **{
                **raw,
                "members": tuple(MemberFacts.from_dict(m) for m in raw["members"]),
                "sources": tuple(SourceFacts.from_dict(s) for s in raw["sources"]),
            }
        )


@dataclass(frozen=True)
class CandidateFacts:
    """One candidate model: a declared revision, or a legacy record read as it is.

    ``anchor_group_refs`` are the distinct conformer-group refs the record's own source calculations are anchored to
    (through their observations); a legacy record is tied to a basin only through them.
    """

    candidate_ref: str
    kind: CandidateKind
    review_status: RecordReviewStatus
    created_at: datetime
    id_rank: int
    owner_ref: str
    record: RecordFacts
    declaration: DeclarationFacts | None
    legacy: LegacyFacts | None
    anchor_group_refs: tuple[str, ...] = ()
    determination: DeterminationFacts | None = None
    #: The correction notice of a superseded record (``superseded_by``/``current`` refs), or ``None``.
    supersession: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_ref": self.candidate_ref,
            "kind": self.kind.value,
            "review_status": self.review_status.value,
            "created_at": self.created_at.isoformat(),
            "id_rank": self.id_rank,
            "owner_ref": self.owner_ref,
            "record": self.record.to_dict(),
            "declaration": self.declaration.to_dict() if self.declaration else None,
            "legacy": self.legacy.to_dict() if self.legacy else None,
            "anchor_group_refs": list(self.anchor_group_refs),
            "determination": self.determination.to_dict() if self.determination else None,
            "supersession": self.supersession,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> CandidateFacts:
        return cls(
            candidate_ref=raw["candidate_ref"],
            kind=CandidateKind(raw["kind"]),
            review_status=RecordReviewStatus(raw["review_status"]),
            created_at=datetime.fromisoformat(raw["created_at"]),
            id_rank=raw["id_rank"],
            owner_ref=raw["owner_ref"],
            record=RecordFacts.from_dict(raw["record"]),
            declaration=DeclarationFacts.from_dict(raw["declaration"]) if raw["declaration"] else None,
            legacy=LegacyFacts.from_dict(raw["legacy"]) if raw["legacy"] else None,
            anchor_group_refs=tuple(raw["anchor_group_refs"]),
            determination=DeterminationFacts.from_dict(raw["determination"]) if raw["determination"] else None,
            supersession=raw["supersession"],
        )
