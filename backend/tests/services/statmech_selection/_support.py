"""Builders for the statmech assessment tests: plain normalised candidates, calculations and requests (no database)."""

from __future__ import annotations

import copy
from datetime import datetime
from typing import Any

from app.db.models.common import RecordReviewStatus
from app.services.statmech_selection.assessment import StatmechAssessment, assess_candidate
from app.services.statmech_selection.facts import (
    CandidateFacts,
    DeclarationFacts,
    DeterminationFacts,
    MemberFacts,
    RecordFacts,
    ScaleFactorFacts,
    SourceFacts,
    SubjectFacts,
    TorsionFacts,
)
from app.services.statmech_selection.legacy import LegacyFacts
from app.services.statmech_selection.models import StatmechRequest
from app.services.statmech_selection.vocabulary import CandidateKind, OwnerKind
from app.services.structure_selection.models import CurvatureFacts
from tests.services.statmech_selection.test_request import CG, SDET, SMD, SPE, THERMAL, TS, TSE, make
from tests.services.structure_selection._support import G1, calc, finding_facts, freq

__all__ = [
    "CG",
    "G1",
    "SDET",
    "SMD",
    "SPE",
    "TSE",
    "approved_calcs",
    "assess",
    "calc",
    "codes",
    "declared",
    "finding_facts",
    "freq",
    "legacy",
    "make",
    "member",
    "req",
    "src",
    "subject",
    "ts_declared",
    "ts_req",
]

T0 = datetime(2026, 6, 1, 12, 0, 0)
APPROVED = RecordReviewStatus.approved
EFFECTIVE = frozenset({RecordReviewStatus.approved, RecordReviewStatus.under_review, RecordReviewStatus.not_reviewed})
FSF_ENTROPY = ScaleFactorFacts("fsf_entropy", "entropy", 0.99)

BASE_RECIPE: dict[str, Any] = {
    "energy_zero": "e0",
    "zpe_included_in_energies": True,
    "scale_application": "applied",
    "scale_contributions": ["vibrational_entropy"],
    "vibrational_scheme": "harmonic",
    "external_symmetry_state": "counted",
    "optical_state": "counted",
    "electronic_levels_state": "counted",
    "isotope_masses": "entry_documented_masses",
    "translation_included": False,
}


def subject(**changes: Any) -> SubjectFacts:
    base: dict[str, Any] = {
        "kind": OwnerKind.species_entry,
        "entry_ref": SPE,
        "stationary_point_kind": "minimum",
        "electronic_state_kind": "ground",
        "isotope_key": None,
        "charge": 0,
        "multiplicity": 1,
        "is_atom": False,
    }
    base.update(changes)
    return SubjectFacts(**base)


def req(body: dict | None = None, **changes: Any) -> StatmechRequest:
    from app.schemas.reads.scientific_statmech_selection import StatmechSelectionRequest

    return StatmechSelectionRequest.model_validate(make(body or THERMAL, **changes)).service_request()


def ts_req(**changes: Any) -> StatmechRequest:
    return req(TS, **changes)


def src(key: str, role: str, ref: str | None = None, **changes: Any) -> SourceFacts:
    base: dict[str, Any] = {
        "source_key": key,
        "role": role,
        "member_key": None,
        "locator": "calculation",
        "calculation_ref": ref,
        "geometry_ref": None,
        "literature_ref": None,
        "scale_factor": None,
    }
    base.update(changes)
    if base["locator"] == "literature" and "literature_identified" not in changes:
        base["literature_identified"] = True
    return SourceFacts(**base)


def member(key: str = "m1", **changes: Any) -> MemberFacts:
    base: dict[str, Any] = {
        "member_key": key,
        "statmech_ref": "sm_" + key,
        "conformer_group_ref": None,
        "counting_multiplicity": 1,
        "relative_energy_kj_mol": 0.0,
        "relative_energy_convention": "electronic_plus_zpe",
        "region_key": None,
        "review_status": APPROVED,
    }
    base.update(changes)
    return MemberFacts(**base)


def record(**changes: Any) -> RecordFacts:
    base: dict[str, Any] = {
        "statmech_ref": "sm_a",
        "scientific_origin": "computed",
        "rigid_rotor_kind": "asymmetric_top",
        "is_linear": False,
        "n_rotational_constants": 3,
        "external_symmetry": 2,
        "optical_isomers": 1,
        "statmech_treatment": "rrho",
        "uses_projected_frequencies": False,
        "scale_factor": None,
        "electronic_levels": ((1, 0.0, 1),),
        "torsions": (),
        "source_links": (),
    }
    base.update(changes)
    return RecordFacts(**base)


def declared(
    *, recipe: dict[str, Any] | None = None, sources: tuple[SourceFacts, ...] | None = None, scope: str = "basin", members=(), **changes: Any
) -> CandidateFacts:
    """A declared basin model that answers a within-basin entropy request completely; every keyword changes one fact."""
    merged = copy.deepcopy(BASE_RECIPE)
    merged.update(recipe or {})
    merged = {k: v for k, v in merged.items() if v is not Ellipsis}
    default_sources = (
        src("geom", "geometry", "calc_o"),
        src("curv", "curvature", "calc_f"),
        src("fsf", "scale_factor", None, locator="scale_factor", scale_factor=FSF_ENTROPY),
    )
    declaration = DeclarationFacts(
        ref=SMD,
        schema_version=1,
        scope_kind=scope,
        conformer_group_ref=CG if scope == "basin" else None,
        determination_ref=None,
        recipe=merged,
        content_hash="a" * 64,
        members=tuple(members),
        sources=default_sources if sources is None else sources,
    )
    base: dict[str, Any] = {
        "candidate_ref": SMD,
        "kind": CandidateKind.declared_model,
        "review_status": APPROVED,
        "created_at": T0,
        "id_rank": 1,
        "owner_ref": SPE,
        "record": record(),
        "declaration": declaration,
        "legacy": None,
    }
    base.update(changes)
    return CandidateFacts(**base)


def ts_declared(*, recipe: dict[str, Any] | None = None, **changes: Any) -> CandidateFacts:
    merged = {
        **BASE_RECIPE,
        "dividing_surface": "conventional_saddle_point",
        "reaction_coordinate_excluded": True,
        "scale_contributions": ["fundamental"],
        **(recipe or {}),
    }
    merged = {k: v for k, v in merged.items() if v is not Ellipsis}
    sources = (
        src("geom", "geometry", "calc_o"),
        src("curv", "curvature", "calc_f"),
        src("fsf", "scale_factor", None, locator="scale_factor", scale_factor=ScaleFactorFacts("fsf_f", "fundamental", 0.97)),
    )
    declaration = DeclarationFacts(SMD, 1, "ts_determination", None, SDET, merged, "b" * 64, (), sources)
    base: dict[str, Any] = {
        "candidate_ref": SMD,
        "kind": CandidateKind.declared_model,
        "review_status": APPROVED,
        "created_at": T0,
        "id_rank": 1,
        "owner_ref": TSE,
        "record": record(),
        "declaration": declaration,
        "legacy": None,
        "determination": DeterminationFacts(SDET, TSE, "saddle_point", G1, APPROVED),
    }
    base.update(changes)
    return CandidateFacts(**base)


def legacy(*, links=(("opt", "calc_o"), ("freq", "calc_f")), facts: dict[str, Any] | None = None, **changes: Any) -> CandidateFacts:
    """A legacy record whose stored facts establish the within-basin entropy claim, anchored to one basin."""
    legacy_facts = LegacyFacts(
        **{
            "owner_kind": OwnerKind.species_entry,
            "scientific_origin": "computed",
            "rigid_rotor_kind": "asymmetric_top",
            "n_rotational_constants": 3,
            "external_symmetry": 2,
            "optical_isomers": 1,
            "statmech_treatment": "rrho",
            "uses_projected_frequencies": False,
            "has_scale_factor": True,
            "scale_factor_kind": "entropy",
            "applied_scale_kinds": frozenset({"entropy"}),
            "n_electronic_levels": 1,
            "n_curvature_sources": 1,
            "n_source_geometries": 1,
            **(facts or {}),
        }
    )
    base: dict[str, Any] = {
        "candidate_ref": "sm_a",
        "kind": CandidateKind.legacy_record,
        "review_status": APPROVED,
        "created_at": T0,
        "id_rank": 1,
        "owner_ref": SPE,
        "record": record(source_links=tuple(links)),
        "declaration": None,
        "legacy": legacy_facts,
        "anchor_group_refs": (CG,),
    }
    base.update(changes)
    return CandidateFacts(**base)


def approved_calcs(*extra) -> dict[str, Any]:
    """The geometry optimisation and the frequency job every default candidate pins, plus any ``extra`` calculations."""
    opt = calc("calc_o", type="opt", energy=-76.4)
    fq = freq("calc_f", n_imag=0)
    return {c.calculation_ref: c for c in (opt, fq, *extra)}


def assess(
    candidate: CandidateFacts,
    request: StatmechRequest | None = None,
    calcs: dict[str, Any] | None = None,
    *,
    subj: SubjectFacts | None = None,
    effective: frozenset[RecordReviewStatus] = EFFECTIVE,
) -> StatmechAssessment:
    return assess_candidate(
        candidate,
        request=request or req(),
        subject=subj or subject(),
        calculations=approved_calcs() if calcs is None else calcs,
        effective_statuses=effective,
    )


def codes(assessment: StatmechAssessment, applicability=None) -> list[str]:
    return [r.code for r in assessment.reasons if applicability is None or r.applicability is applicability]


# kept for tests that build a curvature fact directly
__all__ += ["APPROVED", "BASE_RECIPE", "EFFECTIVE", "FSF_ENTROPY", "T0", "CurvatureFacts", "TorsionFacts", "record"]


#: A declared model whose geometry and curvature rest on imported (identified) literature parameters.
IMPORTED_SOURCES = (
    src("geom", "geometry", None, locator="literature", literature_ref="lit_a"),
    src("curv", "curvature", None, locator="literature", literature_ref="lit_b"),
    src("fsf", "scale_factor", None, locator="scale_factor", scale_factor=FSF_ENTROPY),
)
__all__ += ["IMPORTED_SOURCES"]
