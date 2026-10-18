"""The descriptive benchmark comparison of two stored protocols: pure statistics and pairing.

Two protocols (each a level and an optional recipe) are evaluated by the relative-energy computation over one
population, against one named reference conformer, one energy kind and one certificate. This module pairs their
members by conformer ref and summarises the deviations ``candidate - reference``. It reads no database and works on
the plain answers (``RelativeEnergyResult.to_dict()``), so a recorded comparison replays from its manifest alone.

What is decided here, and nowhere else:

* **The reference conformer is not a pair.** Its deviation is zero by construction, so counting it would only dilute
  every statistic. It is echoed, never counted. It is the anchor of every relative energy, so when it has no value on a
  side, or (for ``same_geometry``) is evaluated at a different geometry on the two sides, no pair satisfies the pairing
  and the problem is named once at the top level, not blamed on the members.
* **A pair with no value on either side, or failing the pairing, is excluded with a coded reason.** It is never a zero
  deviation and never enters a statistic.
* **No statistic over a subset when a complete answer was asked for.** Under ``all_requested_members`` any excluded
  pair gives ``incomplete_pairing`` and no statistics.
* **The comparison is descriptive.** It never says that one protocol is better, and it does not rank. The evidence a
  superiority claim would need (E1 to E7) is listed as missing, always, in this version. Nothing here builds a
  selection-kernel edge or consults the rule registry; a test pins that this module imports from neither.
* **Two stored uncertainties are never combined.** Each side's is echoed as the engine stores it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Any

from app.api.error_contract import CodedValueError
from app.services.relative_conformer_energy.models import RelativeEnergyRequest
from app.services.structure_selection.models import CoverageRequirement
from app.services.structure_selection.normalizer import (
    ENDPOINT_FACTS,
    OPTIONAL_KEYED_FACTS,
    REQUIRED_FACTS,
    declared_fact,
)

#: Version of the comparison semantics (pairing, exclusion, statistics, claims).
COMPARISON_VERSION = "1"

CODE_IDENTICAL_PROTOCOLS = "relative_conformer_energy_compare_identical_protocols"

SIGN_CONVENTION = "candidate minus reference"
REFERENCE_CONFORMER_DEPENDENCE = (
    "these statistics change if another reference conformer is named; compare them only with figures measured from "
    "the same named conformer"
)
CLAIMS_STATEMENT = (
    "A descriptive summary of deviations between two stored protocols. It does not show that either protocol is more "
    "accurate, and does not rank protocols whose figures are close."
)
#: What a future superiority claim would need. In this version all seven are always missing.
MISSING_EVIDENCE = ("E1", "E2", "E3", "E4", "E5", "E6", "E7")


class Pairing(str, Enum):
    """How the two protocols' values of one member are matched. Required; there is no default."""

    same_geometry = "same_geometry"
    same_basin = "same_basin"


class ComparisonOutcome(str, Enum):
    compared = "compared"
    no_pairs = "no_pairs"
    incomplete_pairing = "incomplete_pairing"


# Closed vocabulary of reasons a pair is excluded.
NEITHER_PROTOCOL_HAS_VALUE = "neither_protocol_has_value"
CANDIDATE_VALUE_MISSING = "candidate_value_missing"
REFERENCE_PROTOCOL_VALUE_MISSING = "reference_protocol_value_missing"
GEOMETRY_NOT_RECORDED = "geometry_not_recorded"  # a value not tied to one recorded geometry cannot satisfy same_geometry
GEOMETRY_DIFFERS = "geometry_differs"
#: The named reference conformer is the anchor of every relative energy, so a problem with it is a problem with every pair.
REFERENCE_CONFORMER_VALUE_MISSING = "reference_conformer_value_missing"
REFERENCE_CONFORMER_GEOMETRY_NOT_RECORDED = "reference_conformer_geometry_not_recorded"
REFERENCE_CONFORMER_GEOMETRY_DIFFERS = "reference_conformer_geometry_differs"

EXCLUDED_REASONS = frozenset(
    {
        NEITHER_PROTOCOL_HAS_VALUE,
        CANDIDATE_VALUE_MISSING,
        REFERENCE_PROTOCOL_VALUE_MISSING,
        GEOMETRY_NOT_RECORDED,
        GEOMETRY_DIFFERS,
        REFERENCE_CONFORMER_VALUE_MISSING,
        REFERENCE_CONFORMER_GEOMETRY_NOT_RECORDED,
        REFERENCE_CONFORMER_GEOMETRY_DIFFERS,
    }
)
REFERENCE_CONFORMER_PROBLEMS = frozenset(
    {REFERENCE_CONFORMER_VALUE_MISSING, REFERENCE_CONFORMER_GEOMETRY_NOT_RECORDED, REFERENCE_CONFORMER_GEOMETRY_DIFFERS}
)

#: The fields of a relative-energy request that differ between a comparison's two runs.
PROTOCOL_FIELDS = ("level", "recipe")


def protocol_of(request: RelativeEnergyRequest) -> dict[str, Any]:
    return {"level": request.level.to_dict(), "recipe": request.recipe}


def shared_fields(request: RelativeEnergyRequest) -> dict[str, Any]:
    return {k: v for k, v in request.to_dict().items() if k not in PROTOCOL_FIELDS}


def narrowing_of(recipe: dict[str, Any] | None) -> tuple[tuple[str, str, str | None], ...]:
    """What a requested recipe narrows by: the facts it states as known, as the assessment reads them.

    A fact stated ``unknown`` (or not at all) narrows nothing, and neither do ``source`` or ``supporting_dois``, which
    are about who said it. Two recipes with the same narrowing select the same units.
    """
    out = []
    for name in (*REQUIRED_FACTS, *OPTIONAL_KEYED_FACTS, *ENDPOINT_FACTS):
        wanted = declared_fact(recipe, name)
        if wanted is not None and wanted[0] != "unknown":
            out.append((name, wanted[0], wanted[1]))
    return tuple(out)


def protocols_identical(a: RelativeEnergyRequest, b: RelativeEnergyRequest) -> bool:
    """The same level and the same effective narrowing: a protocol is not compared with itself."""
    return a.level == b.level and narrowing_of(a.recipe) == narrowing_of(b.recipe)


def identical_protocols() -> CodedValueError:
    return CodedValueError(
        CODE_IDENTICAL_PROTOCOLS,
        "the candidate and the reference name the same level and narrow it by the same recipe facts; a protocol is not "
        "compared with itself and nothing was compared.",
        context={},
        message_prefix=False,
    )


@dataclass(frozen=True)
class ComparisonRequest:
    """Two relative-energy requests that differ only in their protocol (level and recipe), and the pairing."""

    candidate: RelativeEnergyRequest
    reference: RelativeEnergyRequest
    pairing: Pairing

    def __post_init__(self) -> None:
        if shared_fields(self.candidate) != shared_fields(self.reference):
            raise ValueError("the candidate and reference requests differ in more than their protocol")
        if protocols_identical(self.candidate, self.reference):
            raise identical_protocols()

    def to_dict(self) -> dict[str, Any]:
        return {
            "shared": shared_fields(self.candidate),
            "candidate": protocol_of(self.candidate),
            "reference": protocol_of(self.reference),
            "pairing": self.pairing.value,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ComparisonRequest:
        def request(protocol: dict[str, Any]) -> RelativeEnergyRequest:
            return RelativeEnergyRequest.from_dict({**raw["shared"], **protocol})

        return cls(candidate=request(raw["candidate"]), reference=request(raw["reference"]), pairing=Pairing(raw["pairing"]))


def _member_map(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {m["conformer_ref"]: m for m in result["members"]}


def _has_value(member: dict[str, Any] | None) -> bool:
    return member is not None and member["value_hartree"] is not None


def _geometry_of(member: dict[str, Any] | None) -> str | None:
    """The one geometry a member's value came from; ``None`` for no member, no value, or a value tied to none."""
    if member is None or member["value_hartree"] is None:
        return None
    geometry: str | None = member["value_geometry_ref"]
    return geometry


def _reference_conformer(
    candidate: dict[str, Any], reference: dict[str, Any], anchor: str, pairing: Pairing
) -> dict[str, Any]:
    """What the named reference conformer allows: its value on both sides and, under ``same_geometry``, one geometry.

    A relative energy is the member's value minus the reference conformer's, so a reference conformer that is missing
    on a side, or (for ``same_geometry``) evaluated at a different geometry on the two sides, makes every pair unusable.
    """
    c, r = _member_map(candidate).get(anchor), _member_map(reference).get(anchor)
    c_geo, r_geo = _geometry_of(c), _geometry_of(r)
    problem: str | None = None
    if not (_has_value(c) and _has_value(r)):
        problem = REFERENCE_CONFORMER_VALUE_MISSING
    elif pairing is Pairing.same_geometry:
        if c_geo is None or r_geo is None:
            problem = REFERENCE_CONFORMER_GEOMETRY_NOT_RECORDED
        elif c_geo != r_geo:
            problem = REFERENCE_CONFORMER_GEOMETRY_DIFFERS
    return {
        "conformer_ref": anchor,
        "problem": problem,
        "candidate_value_present": _has_value(c),
        "reference_value_present": _has_value(r),
        "candidate_geometry_ref": c_geo,
        "reference_geometry_ref": r_geo,
    }


def _pair(
    g: str, c: dict[str, Any] | None, r: dict[str, Any] | None, pairing: Pairing, anchor_problem: str | None
) -> dict[str, Any]:
    """One member's pair. The geometry is the one each value came from, never a sibling record's."""
    c_has, r_has = _has_value(c), _has_value(r)
    c_geo, r_geo = _geometry_of(c), _geometry_of(r)
    reason: str | None = None
    if not c_has and not r_has:
        reason = NEITHER_PROTOCOL_HAS_VALUE
    elif not c_has:
        reason = CANDIDATE_VALUE_MISSING
    elif not r_has:
        reason = REFERENCE_PROTOCOL_VALUE_MISSING
    elif anchor_problem is not None:
        # The member has a value on both sides; what is missing is the anchor, and that is what is said.
        reason = anchor_problem
    elif pairing is Pairing.same_geometry:
        if c_geo is None or r_geo is None:
            reason = GEOMETRY_NOT_RECORDED
        elif c_geo != r_geo:
            reason = GEOMETRY_DIFFERS
    c_kj = c["relative_energy_kj_mol"] if c else None
    r_kj = r["relative_energy_kj_mol"] if r else None
    deviation = (c_kj - r_kj) if reason is None and c_kj is not None and r_kj is not None else None
    return {
        "conformer_ref": g,
        "candidate_kj_mol": c_kj,
        "reference_kj_mol": r_kj,
        "deviation_kj_mol": deviation,
        "excluded_reason": reason,
        "candidate_uncertainty_hartree": c["value_uncertainty_hartree"] if c else None,
        "reference_uncertainty_hartree": r["value_uncertainty_hartree"] if r else None,
        "candidate_geometry_ref": c_geo,
        "reference_geometry_ref": r_geo,
    }


def statistics_of(deviations: list[tuple[str, float]], anchor: str) -> dict[str, Any]:
    """Descriptive statistics of ``(conformer_ref, deviation)``; every figure ``None`` when there is no pair."""
    n = len(deviations)
    out: dict[str, Any] = {
        "n": n,
        "msd_kj_mol": None,
        "mad_kj_mol": None,
        "rmsd_kj_mol": None,
        "max_abs_deviation_kj_mol": None,
        "max_abs_deviation_conformer_ref": None,
        "sign_convention": SIGN_CONVENTION,
        "reference_conformer_ref": anchor,
        "reference_conformer_dependence": REFERENCE_CONFORMER_DEPENDENCE,
    }
    if n:
        values = [d for _, d in deviations]
        out["msd_kj_mol"] = math.fsum(values) / n
        out["mad_kj_mol"] = math.fsum(abs(d) for d in values) / n
        out["rmsd_kj_mol"] = math.sqrt(math.fsum(d * d for d in values) / n)
        # Ties go to the lowest conformer ref, so the answer does not depend on iteration order.
        ref, worst = min(sorted(deviations), key=lambda p: -abs(p[1]))
        out["max_abs_deviation_kj_mol"] = abs(worst)
        out["max_abs_deviation_conformer_ref"] = ref
    return out


def _protocol_block(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "level": {k: result["level"][k] for k in ("level_of_theory_ref", "composite_scheme_ref")},
        "recipe_cohort_id": result["level"]["recipe_cohort_id"],
        "recipe_facts": result["level"]["recipe_facts"],
        "outcome": result["outcome"],
        "reference": result["reference"],
        "coverage": result["coverage"],
        "unresolved": result["unresolved"],
    }


def compare_results(
    candidate: dict[str, Any],
    reference: dict[str, Any],
    *,
    pairing: Pairing,
    coverage_requirement: CoverageRequirement,
    reference_conformer_ref: str,
) -> dict[str, Any]:
    """The comparison of two relative-energy answers (``RelativeEnergyResult.to_dict()``), pure.

    :returns: ``outcome``, ``pairing``, ``statistics`` (``None`` under ``incomplete_pairing``), ``pairs`` ordered by
        conformer ref, the two protocols' blocks and ``claims``.
    """
    cm, rm = _member_map(candidate), _member_map(reference)
    refs = sorted((set(cm) | set(rm)) - {reference_conformer_ref})
    anchor = _reference_conformer(candidate, reference, reference_conformer_ref, pairing)
    pairs = [_pair(g, cm.get(g), rm.get(g), pairing, anchor["problem"]) for g in refs]
    paired = [(p["conformer_ref"], p["deviation_kj_mol"]) for p in pairs if p["excluded_reason"] is None]
    incomplete = coverage_requirement is CoverageRequirement.all_requested_members and (
        anchor["problem"] is not None or any(p["excluded_reason"] is not None for p in pairs)
    )
    if incomplete:
        outcome, stats = ComparisonOutcome.incomplete_pairing, None
    else:
        stats = statistics_of(paired, reference_conformer_ref)
        outcome = ComparisonOutcome.compared if paired else ComparisonOutcome.no_pairs
    return {
        "comparison_version": COMPARISON_VERSION,
        "outcome": outcome.value,
        "pairing": pairing.value,
        "coverage_requirement": coverage_requirement.value,
        "units": {"deviation": "kJ/mol"},
        "reference_conformer": anchor,
        "statistics": stats,
        "pairs": pairs,
        "candidate": _protocol_block(candidate),
        "reference": _protocol_block(reference),
        "claims": {
            "kind": "descriptive",
            "superiority": "not_licensed",
            "ranking": "not_licensed",
            "missing_evidence": list(MISSING_EVIDENCE),
            "statement": CLAIMS_STATEMENT,
        },
        "notes": ["pairs are ordered by conformer ref; the order is not a ranking"],
    }
