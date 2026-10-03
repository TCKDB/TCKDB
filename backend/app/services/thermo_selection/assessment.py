"""The deterministic H298 applicability assessor.

Question asked of one thermo record: can it supply the *gas-phase standard enthalpy of
formation at 298.15 K* for the requested thermodynamic target? The answer is one of
``applicable``, ``incompatible``, ``unsupported`` or ``unresolved`` (see
:class:`~app.services.thermo_selection.models.Applicability`), with every finding that
led there.

What can answer: a finite stored H298 scalar; an enthalpy point stored at exactly 298.15 K;
a NASA-7 or NASA-9 fit evaluated at 298.15 K by the existing consistency engine
(``app.services.consistency.engine``, Cantera 3.2.0, never extrapolating across a bound or a
gap). Wilhoit is not evaluated. Nothing is interpolated.

What the answer needs, beyond a number:

* the enthalpy reference the record declares must be formation from the elements at 298.15 K
  (``enthalpy_reference_kind``). A field called ``h298`` is not evidence of that by itself, and
  an undeclared reference is *unresolved*, never assumed;
* a recorded gas phase (unrecorded is unresolved, any other phase incompatible);
* a declared thermodynamic target equal to the requested one (undeclared is unresolved; the
  other kind, or another conformer group, is incompatible). Never inferred from a statmech link.

What it does not need: interval metadata for a scalar, a recorded reference pressure (an
ideal-gas H carries none), or computational attachments for a record that is not computed.

Validation: a computed record whose evidence evaluation hard-fails (a structural failure or a
hard-failed required source calculation) is excluded; everything else the evidence evaluation
finds is disclosed as advisory. Experimental and estimated records are not graded by the
computed-thermo rubric at all.
"""

from __future__ import annotations

from math import isfinite
from typing import Any

from app.db.models.common import EnthalpyReferenceKind, ScientificOriginKind
from app.db.models.thermo import Thermo
from app.services.consistency import engine
from app.services.thermo_selection.models import (
    REFERENCE_TEMPERATURE_K,
    Applicability,
    CandidateAssessment,
    H298Request,
    Reason,
)
from app.services.trust.models import EvidenceEvaluation, EvidenceOutcome

#: Order in which an answering representation is preferred when several answer.
REPRESENTATION_ORDER = ("h298", "point", "nasa7", "nasa9")

_PHASE_REASONS = {"phase_not_recorded", "non_gas_phase_unsupported"}
_DOMAIN_REASONS = {"temperature_outside_record_range", "temperature_outside_fit_range", "temperature_outside_fit_or_in_gap"}
_DEFECT_REASONS = {
    "incomplete_nasa7",
    "invalid_nasa7_intervals",
    "incomplete_nasa9",
    "invalid_nasa9_intervals",
    "nonfinite_engine_result",
    "nonfinite_stored_value",
}
_WILHOIT_REASON = "wilhoit_not_evaluated"
_PRECEDENCE = (Applicability.incompatible, Applicability.unsupported, Applicability.unresolved)


def _entry(representation: str, value: float | None, reason: str | None) -> dict[str, Any]:
    if value is not None and not isfinite(value):
        value, reason = None, "nonfinite_stored_value" if representation in {"h298", "point"} else "nonfinite_engine_result"
    return {"representation": representation, "value_kj_mol": value, "reason": reason if value is None else None}


def evaluate_representations(thermo: Thermo) -> list[dict[str, Any]]:
    """Try every stored representation at exactly 298.15 K; one entry each, in :data:`REPRESENTATION_ORDER`.

    Reuses :func:`app.services.consistency.engine.evaluate` for the scalar, the exact point and the
    NASA fits. The engine's own phase gate is the record-level check below; its reasons are passed
    through here and ignored by the classifier so a phase problem is reported once.
    """
    out: list[dict[str, Any]] = []
    t = REFERENCE_TEMPERATURE_K
    if thermo.h298_kj_mol is not None:
        value, reason = engine.evaluate(thermo, "h298", t, "h")
        out.append(_entry("h298", value, reason))
    if thermo.points:
        value, reason = engine.evaluate(thermo, "point", t, "h")
        out.append(_entry("point", value, reason))
    if thermo.nasa is not None:
        value, reason = engine.evaluate(thermo, "nasa7", t, "h")
        out.append(_entry("nasa7", value, reason))
    if thermo.nasa9_intervals:
        value, reason = engine.evaluate(thermo, "nasa9", t, "h")
        out.append(_entry("nasa9", value, reason))
    if thermo.wilhoit is not None:
        out.append(_entry("wilhoit", None, _WILHOIT_REASON))
    return out


def _classify_unanswered(representations: list[dict[str, Any]]) -> list[Reason]:
    """Why no stored representation answered."""
    if not representations:
        return [Reason("no_h298_representation", Applicability.incompatible)]
    reasons: list[Reason] = []
    answerable = [r for r in representations if r["representation"] != "wilhoit"]
    for r in representations:
        code = r["reason"]
        if code in _PHASE_REASONS:
            continue  # reported once, at record level
        if code == _WILHOIT_REASON:
            # Unsupported only if nothing else is stored: a stored scalar that fails is a defect, not a gap.
            if not answerable:
                reasons.append(Reason(_WILHOIT_REASON, Applicability.unsupported))
            continue
        if code in _DOMAIN_REASONS:
            reasons.append(Reason(f"domain_excludes_298_15K:{r['representation']}:{code}", Applicability.incompatible))
        elif code in _DEFECT_REASONS:
            reasons.append(Reason(f"representation_defective:{r['representation']}:{code}", Applicability.incompatible))
        elif code == "no_exact_matching_point":
            reasons.append(Reason("no_enthalpy_point_at_298_15K", Applicability.incompatible))
        else:
            reasons.append(Reason(f"representation_cannot_answer:{r['representation']}:{code}", Applicability.incompatible))
    return reasons


def _record_level_reasons(thermo: Thermo, request: H298Request) -> list[Reason]:
    reasons: list[Reason] = []
    phase_reason = engine.gas_state_reason(thermo, quantity="h")
    if phase_reason == "phase_not_recorded":
        reasons.append(Reason("phase_not_recorded", Applicability.unresolved))
    elif phase_reason == "non_gas_phase_unsupported":
        reasons.append(Reason(f"phase_not_gas:{thermo.phase.value if thermo.phase else None}", Applicability.incompatible))

    if thermo.enthalpy_reference_kind is None:
        reasons.append(Reason("enthalpy_reference_not_declared", Applicability.unresolved))
    elif thermo.enthalpy_reference_kind is not EnthalpyReferenceKind.formation_298k:
        reasons.append(Reason(f"enthalpy_reference_not_formation_298k:{thermo.enthalpy_reference_kind.value}",
                              Applicability.incompatible))

    if thermo.thermodynamic_target_kind is None:
        reasons.append(Reason("thermodynamic_target_not_declared", Applicability.unresolved))
    elif thermo.thermodynamic_target_kind is not request.target_kind:
        reasons.append(Reason(
            f"target_kind_mismatch:declared_{thermo.thermodynamic_target_kind.value}_requested_{request.target_kind.value}",
            Applicability.incompatible,
        ))
    elif thermo.target_conformer_group_id != request.conformer_group_id:
        reasons.append(Reason("target_conformer_group_mismatch", Applicability.incompatible))
    return reasons


def _combine(reasons: list[Reason]) -> Applicability:
    present = {r.applicability for r in reasons}
    for level in _PRECEDENCE:
        if level in present:
            return level
    return Applicability.applicable


def assess_candidate(
    thermo: Thermo, *, request: H298Request, evidence: EvidenceEvaluation | None
) -> CandidateAssessment:
    """Assess one loaded thermo record for the request. Deterministic; reads only what is loaded.

    :param thermo: A thermo row with ``points``, ``nasa``, ``nasa9_intervals`` and ``wilhoit`` loaded.
    :param request: The normalised request (target and conformer group).
    :param evidence: The computed-thermo evidence evaluation for this record, or ``None`` when the
        record is not computed (experimental and estimated records are not graded by that rubric).
    """
    representations = evaluate_representations(thermo)
    answering = {r["representation"]: r["value_kj_mol"] for r in representations if r["value_kj_mol"] is not None}
    answer = next((name for name in REPRESENTATION_ORDER if name in answering), None)

    reasons = _record_level_reasons(thermo, request)
    if answer is None:
        reasons.extend(_classify_unanswered(representations))
    applicability = _combine(reasons)

    blocking: list[str] = []
    advisory: list[str] = []
    if thermo.reference_pressure_bar is None:
        advisory.append("reference_pressure_not_recorded")
    if evidence is None:
        origin = thermo.scientific_origin.value if isinstance(thermo.scientific_origin, ScientificOriginKind) else str(thermo.scientific_origin)
        advisory.append(f"evidence_rubric_not_applicable:{origin}")
    else:
        if evidence.hard_fail_reason is not None:
            blocking.append(f"evidence_hard_failed:{evidence.hard_fail_reason.value}")
        advisory.append(f"evidence_label:{evidence.label.value}")
        for name, outcome in evidence.checks.items():
            if outcome in (EvidenceOutcome.missing, EvidenceOutcome.warning):
                advisory.append(f"evidence_check_{outcome.value}:{name}")

    return CandidateAssessment(
        thermo_ref=thermo.public_ref,
        applicability=applicability,
        reasons=tuple(reasons),
        answer_representation=answer,
        value_kj_mol=answering.get(answer) if answer else None,
        representations=tuple(representations),
        blocking=tuple(blocking),
        advisory=tuple(advisory),
    )
