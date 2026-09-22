"""D2: computed Cp against stored external observations.

The Phase C preference order (NASA7, NASA9, exact points) remains this
check's contract. D1 separately compares every fit without choosing one.
Cantera 3.2.0 preserves the recorded reference pressure; unsupported state
and units remain unavailable. Real-gas observations retain unquantified
non-ideality. Values and supplied uncertainty are informational only.

The pure read and append wrapper are separate. Rubric v2 hashes actual
inputs and custody, leaves historical rows intact, and reads only this
runner in the scientific-check family. Observation citations use public refs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.common import MolecularPropertyKind, ObservedStateBasis, ScientificOriginKind
from app.db.models.molecular_property_observation import MolecularPropertyObservation
from app.db.models.record_machine_review import RecordMachineReviewRow
from app.db.models.thermo import Thermo, ThermoNASA, ThermoPoint
from app.services.consistency import engine
from app.services.consistency.core import encoded, finding, snapshot, thermo_inputs
from app.services.machine_review.context_hash import (
    MachineReviewEvidenceContext,
    build_machine_review_context_hash,
)
from app.services.machine_review.derivation import (
    MachineReviewOutcome,
    derive_machine_review_status,
)
from app.services.machine_review.persistence import create_record_machine_review_row
from app.services.machine_review.query import (
    SCIENTIFIC_CHECK_PROVIDER,
    MachineReviewRecordFamily,
    get_latest_record_machine_review_row,
    get_record_machine_review_currency_for_record,
)
from app.services.machine_review.read_model import RecordMachineReview
from app.services.machine_review.recipe import (
    ACTIVE_MACHINE_REVIEW_RUBRIC_VERSIONS,
    public_rubric_name,
)
from app.services.machine_review.schemas import (
    MachineReviewCategory,
    MachineReviewFinding,
    MachineReviewSeverity,
)
from app.services.trust.rubrics import EXTERNAL_CP_COMPARISON_V2

#: Cantera's ``ThermoPhase.cp_mole`` is J/(kmol*K); TCKDB's fixed unit for
#: this quantity is J/(mol*K) (unit policy, ``docs/unit_policy.md``). The one
#: conversion point -- see ``test_j_per_kmol_k_to_j_per_mol_k_constant`` for
#: the mutation guard (a wrong constant must fail the NASA-7 hand-calc check).
_J_PER_KMOL_K_TO_J_PER_MOL_K = 1.0 / 1000.0

#: Model/prompt-version identity for this deterministic runner. There is no
#: LLM prompt involved, so this single string stands in for both the
#: ``model`` and ``prompt_version`` axes ``create_record_machine_review_row``
#: expects: "the version of the logic that produced this review."
RUNNER_VERSION = "external_cp_comparison_v1"

#: Single source of truth is ``app.services.machine_review.query`` (imported,
#: never redefined here) -- see
#: :data:`~app.services.machine_review.query.SCIENTIFIC_CHECK_PROVIDER`
#: and :class:`~app.services.machine_review.query.MachineReviewRecordFamily`.
PROVIDER = SCIENTIFIC_CHECK_PROVIDER

_MESSAGE_METHOD_NOTE_MAX_CHARS = 120
_MESSAGE_MAX_BYTES = 1000
#: Deterministic bound on each ref string embedded in ``message`` (never on
#: ``evidence_keys``, which keeps the untruncated value for exact matching).
#: ``external_source_record_ref`` is an externally supplied source key
#: with no length limit in the DB (``Text`` column) --
#: so an unbounded key can otherwise push ``message`` past its 1000-char
#: schema limit (``MachineReviewFinding.message``) and make the whole run
#: raise instead of recording a finding.
_MESSAGE_REF_MAX_CHARS = 120
_TRUNCATION_MARKER = "...(truncated)"


ExternalCpComparisonConfigurationError = engine.ConfigurationError


@dataclass(frozen=True)
class CpObservationComparison:
    """One observation's comparison against the thermo's computed Cp(T)."""

    observation_ref: str
    external_source_record_ref: str | None
    temperature_k: float
    pressure_bar: float | None
    state_basis: str | None
    cp_observed_j_mol_k: float | None
    cp_computed_j_mol_k: float | None
    residual_j_mol_k: float | None
    scalar_uncertainty: float | None
    uncertainty_kind: str | None
    uncertainty_coverage_factor: float | None
    uncertainty_level_of_confidence_pct: float | None
    uncertainty_assessor: str | None
    method_note: str | None
    representation: str
    t_in_range: bool | None
    comparability: str
    comparability_reason: str | None
    non_ideality: str | None


@dataclass(frozen=True)
class ComparisonResult:
    """The full comparison for one thermo record."""

    thermo: Thermo
    representation: str
    comparisons: tuple[CpObservationComparison, ...]
    inputs_json: str


# ---------------------------------------------------------------------------
# Representation evaluators
# ---------------------------------------------------------------------------

# ``(cp_j_mol_k, in_range) | None`` isn't expressible as a single alias
# without importing typing.Optional gymnastics; spelled out at each use site.
_Evaluator = Callable[[float], tuple[float | None, bool, str | None]]


def _import_cantera():
    return engine.cantera()


def _nasa7_complete(nasa: ThermoNASA) -> bool:
    bounds = (nasa.t_low, nasa.t_mid, nasa.t_high)
    low = (nasa.a1, nasa.a2, nasa.a3, nasa.a4, nasa.a5, nasa.a6, nasa.a7)
    high = (nasa.b1, nasa.b2, nasa.b3, nasa.b4, nasa.b5, nasa.b6, nasa.b7)
    return all(v is not None for v in bounds + low + high)


def _build_nasa7_evaluator(thermo: Thermo) -> _Evaluator:
    _import_cantera()
    def evaluate(temperature_k):
        poly, reason = engine.polynomial(thermo, "nasa7", temperature_k, quantity="cp")
        if reason:
            return None, False, reason
        value = poly.cp(temperature_k) * _J_PER_KMOL_K_TO_J_PER_MOL_K
        return (value, True, None) if isfinite(value) else (None, False, "nonfinite_engine_result")
    return evaluate


def _build_nasa9_evaluator(thermo: Thermo) -> _Evaluator:
    _import_cantera()
    def evaluate(temperature_k):
        poly, reason = engine.polynomial(thermo, "nasa9", temperature_k, quantity="cp")
        if reason:
            return None, False, reason
        value = poly.cp(temperature_k) * _J_PER_KMOL_K_TO_J_PER_MOL_K
        return (value, True, None) if isfinite(value) else (None, False, "nonfinite_engine_result")
    return evaluate


def _build_point_evaluator(points: list[ThermoPoint]) -> _Evaluator:
    by_temperature = {p.temperature_k: p.cp_j_mol_k for p in points}

    def evaluate(temperature_k: float) -> tuple[float | None, bool, str | None]:
        cp = by_temperature.get(temperature_k)
        if cp is None:
            return None, False, "no_exact_matching_point"
        if not isfinite(cp):
            return None, False, "nonfinite_point_value"
        return cp, True, None

    return evaluate


def _resolve_evaluator(thermo: Thermo) -> tuple[str, _Evaluator]:
    """Pick the thermo's representation and build its Cp(T) evaluator.

    Preference order (also the order named in the C4 plan): NASA-7, then
    NASA-9, then tabulated point. Raises :class:`ValueError` -- a caller
    precondition failure, not a finding -- when none is usable.
    """
    if thermo.nasa is not None and _nasa7_complete(thermo.nasa):
        return "nasa7", _build_nasa7_evaluator(thermo)
    if thermo.nasa9_intervals:
        return "nasa9", _build_nasa9_evaluator(thermo)
    if thermo.points:
        return "point", _build_point_evaluator(list(thermo.points))
    raise ValueError(
        f"cannot compare Cp for thermo {thermo.public_ref!r}: it has no "
        "NASA-7, NASA-9, or tabulated-point representation to compare against"
    )


# ---------------------------------------------------------------------------
# Observation references (never the internal id)
# ---------------------------------------------------------------------------


def _observation_ref(observation: MolecularPropertyObservation) -> str:
    """Cite the participating observation by its stable public reference."""
    return observation.public_ref


def _external_source_record_ref(observation: MolecularPropertyObservation) -> str | None:
    if observation.external_source_record is None:
        return None
    return observation.external_source_record.source_record_key


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------


def _compare_one(
    observation: MolecularPropertyObservation,
    representation: str,
    evaluate: _Evaluator,
    applicability_reason: str | None = None,
) -> CpObservationComparison:
    temperature_k = observation.temperature_k
    cp_observed = observation.scalar_value
    reason = applicability_reason
    if cp_observed is None or not isfinite(cp_observed):
        reason = "missing_or_nonfinite_observation_scalar"
        cp_observed = None
    elif temperature_k is None or not isfinite(temperature_k) or temperature_k <= 0:
        reason = "missing_or_invalid_observation_temperature"
    elif observation.scalar_unit != "J/mol/K":
        reason = "unsupported_or_missing_observation_unit"
    elif observation.state_basis not in (ObservedStateBasis.ideal_gas, ObservedStateBasis.real_gas):
        reason = "missing_or_incompatible_observation_state"
    cp_computed, usable, evaluation_reason = (
        evaluate(temperature_k) if reason is None else (None, False, reason)
    )

    state_basis = observation.state_basis
    non_ideality = "unquantified" if state_basis is ObservedStateBasis.real_gas else None

    if usable:
        comparability = "comparable"
        comparability_reason = None
        residual = cp_computed - cp_observed
        t_in_range = True if representation != "point" else None
    else:
        comparability = "not_comparable"
        comparability_reason = reason or evaluation_reason or (
            "no_exact_matching_point" if representation == "point" else "temperature_outside_fit_range"
        )
        cp_computed = None
        residual = None
        t_in_range = False if representation != "point" else None

    return CpObservationComparison(
        observation_ref=_observation_ref(observation),
        external_source_record_ref=_external_source_record_ref(observation),
        temperature_k=temperature_k,
        pressure_bar=observation.pressure_bar,
        state_basis=state_basis.value if state_basis is not None else None,
        cp_observed_j_mol_k=cp_observed,
        cp_computed_j_mol_k=cp_computed,
        residual_j_mol_k=residual,
        scalar_uncertainty=observation.scalar_uncertainty,
        uncertainty_kind=(
            observation.uncertainty_kind.value if observation.uncertainty_kind is not None else None
        ),
        uncertainty_coverage_factor=observation.uncertainty_coverage_factor,
        uncertainty_level_of_confidence_pct=observation.uncertainty_level_of_confidence_pct,
        uncertainty_assessor=(
            observation.uncertainty_assessor.value
            if observation.uncertainty_assessor is not None
            else None
        ),
        method_note=observation.method_note,
        representation=representation,
        t_in_range=t_in_range,
        comparability=comparability,
        comparability_reason=comparability_reason,
        non_ideality=non_ideality,
    )


def compare_thermo_with_cp_observations(session: Session, thermo_id: int) -> ComparisonResult:
    """Compare one computed thermo's Cp(T) against its heat-capacity observations.

    Pure read: no row is written. Raises :class:`ValueError` if ``thermo_id``
    does not resolve, is not ``scientific_origin=computed``, or carries none
    of NASA-7/NASA-9/point; raises
    :class:`ExternalCpComparisonConfigurationError` if a NASA representation
    is present but Cantera is not installed. Neither is a finding.
    """
    with session.no_autoflush:
        thermo = session.get(Thermo, thermo_id)
        if thermo is None:
            # The id stays out of the message: callers name the thermo by public
            # ref, and a row id in user-facing text is a catalogued house defect.
            raise ValueError("cannot compare Cp: the requested thermo does not exist")
        if thermo.scientific_origin is not ScientificOriginKind.computed:
            raise ValueError(
                "cannot compare Cp: this requires a computed thermo record "
                f"({thermo.public_ref} has scientific_origin={thermo.scientific_origin.value!r})"
            )

        representation, evaluate = _resolve_evaluator(thermo)

        observations = list(
            session.scalars(
                select(MolecularPropertyObservation)
                .where(
                    MolecularPropertyObservation.species_entry_id == thermo.species_entry_id,
                    MolecularPropertyObservation.property_kind == MolecularPropertyKind.heat_capacity_cp,
                )
                .order_by(MolecularPropertyObservation.id)
            )
        )

        comparisons = tuple(
            _compare_one(observation, representation, evaluate, engine.gas_state_reason(thermo, quantity="cp"))
            for observation in observations
        )
        inputs = {
            "thermo": thermo_inputs(thermo), "representation": representation,
            "observations": sorted((snapshot(o, ("external_source_record", "literature")) for o in observations), key=encoded),
            "sources": sorted((snapshot(o.external_source_record.external_source) for o in observations
                               if o.external_source_record is not None), key=encoded),
            "engine": f"cantera/{engine.ENGINE_VERSION}" if representation != "point" else "exact-points/1",
            "units": "J/mol/K;bar;K",
        }
        return ComparisonResult(thermo=thermo, representation=representation, comparisons=comparisons, inputs_json=encoded(inputs))


# ---------------------------------------------------------------------------
# Persistence: one record_machine_review row
# ---------------------------------------------------------------------------


def _truncate(text: str | None, max_chars: int) -> str | None:
    if text is None or len(text) <= max_chars:
        return text
    return text[:max_chars]


def _bounded_ref(ref: str | None, max_chars: int = _MESSAGE_REF_MAX_CHARS) -> str | None:
    """Deterministically bound a ref string for embedding in ``message``.

    Plain prefix truncation plus a fixed marker -- the same input always
    yields the same output, so this never makes ``message`` (or the byte-
    length fallback below) non-deterministic across runs. Only affects the
    value embedded in ``message``; ``evidence_keys`` always carries the
    untruncated ref (see :func:`_finding_from_comparison`).
    """
    if ref is None or len(ref) <= max_chars:
        return ref
    keep = max_chars - len(_TRUNCATION_MARKER)
    return ref[:keep] + _TRUNCATION_MARKER


def _finding_payload(comparison: CpObservationComparison) -> dict:
    return {
        "observation_ref": _bounded_ref(comparison.observation_ref),
        "external_source_record_ref": _bounded_ref(comparison.external_source_record_ref),
        "temperature_k": comparison.temperature_k,
        "pressure_bar": comparison.pressure_bar,
        "state_basis": comparison.state_basis,
        "cp_observed_j_mol_k": comparison.cp_observed_j_mol_k,
        "cp_computed_j_mol_k": comparison.cp_computed_j_mol_k,
        "residual_j_mol_k": comparison.residual_j_mol_k,
        "scalar_uncertainty": comparison.scalar_uncertainty,
        "uncertainty_kind": comparison.uncertainty_kind,
        "uncertainty_coverage_factor": comparison.uncertainty_coverage_factor,
        "uncertainty_level_of_confidence_pct": comparison.uncertainty_level_of_confidence_pct,
        "uncertainty_assessor": comparison.uncertainty_assessor,
        "method_note": _truncate(comparison.method_note, _MESSAGE_METHOD_NOTE_MAX_CHARS),
        "representation": comparison.representation,
        "t_in_range": comparison.t_in_range,
        "comparability": comparison.comparability,
        "comparability_reason": comparison.comparability_reason,
        "non_ideality": comparison.non_ideality,
    }


def _finding_from_comparison(comparison: CpObservationComparison, thermo: Thermo) -> MachineReviewFinding:
    """Build the finding carrying one observation's comparison.

    ``MachineReviewFinding`` (``app.services.machine_review.schemas``) has a
    fixed field set with no free-form numeric payload, so the full per-
    observation detail the C4 plan asks for is carried as canonical
    (sorted-key, compact) JSON in ``message`` -- recoverable by
    ``json.loads`` -- with short pointer strings duplicated into
    ``evidence_keys``. Severity is always ``info``: no ratio is judged here,
    so nothing about this finding is more or less severe than another.
    """
    payload = _finding_payload(comparison)
    message = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    if len(message.encode("utf-8")) > _MESSAGE_MAX_BYTES:
        payload["method_note"] = None
        message = json.dumps(payload, sort_keys=True, separators=(",", ":"))

    evidence_keys = tuple(
        key
        for key in (
            f"observation:{comparison.observation_ref}",
            (
                f"external_source_record:{comparison.external_source_record_ref}"
                if comparison.external_source_record_ref is not None
                else None
            ),
        )
        if key is not None
    )

    return MachineReviewFinding(
        severity=MachineReviewSeverity.info,
        category=MachineReviewCategory.thermo,
        record_type="thermo",
        record_ref=thermo.public_ref,
        message=message,
        evidence_keys=evidence_keys,
        recommended_action=None,
    )



def findings_for_result(result):
    """No observations is an explicit unavailable finding, never an empty pass."""
    if not result.comparisons:
        return (finding(result.thermo, {"reason": "no_cp_observations"}, [result.thermo.public_ref]),)
    return tuple(_finding_from_comparison(c, result.thermo) for c in result.comparisons)


def run_and_record(
    session: Session,
    thermo_id: int,
    *,
    actor_kind: str = "system",
) -> RecordMachineReviewRow:
    """Run the comparison and append one ``record_machine_review`` row.

    ``actor_kind`` documents who triggered this pass (``"cli"``, ``"admin"``,
    ``"system"``); ``record_machine_review`` has no column for it today and
    this runner does not invent one (no migration is in scope for C4), so it
    is accepted for callers that want to say why they ran this and is not
    otherwise persisted.

    Writes exactly one row, through :func:`create_record_machine_review_row`
    -- no other table, and no field on ``thermo`` or
    ``molecular_property_observation`` is touched.
    """
    del actor_kind  # see docstring: accepted, not yet persisted anywhere
    result = compare_thermo_with_cp_observations(session, thermo_id)
    thermo = result.thermo

    findings = findings_for_result(result)
    status = derive_machine_review_status(findings, MachineReviewOutcome.completed)
    reviewed_at = datetime.now(timezone.utc).replace(tzinfo=None)

    review = RecordMachineReview(
        record_type="thermo",
        record_ref=thermo.public_ref,
        status=status,
        findings=findings,
        model=RUNNER_VERSION,
        provider=PROVIDER,
        reviewed_at=reviewed_at,
        record_id=thermo.id,
    )

    context = MachineReviewEvidenceContext(
        record_type="thermo",
        record_ref=thermo.public_ref,
        rubric_name=EXTERNAL_CP_COMPARISON_V2.name,
        rubric_version=EXTERNAL_CP_COMPARISON_V2.version,
        notes=(result.inputs_json,),
    )
    digest = build_machine_review_context_hash(context)

    rubric_key = public_rubric_name(EXTERNAL_CP_COMPARISON_V2)
    rubric_versions = {rubric_key: ACTIVE_MACHINE_REVIEW_RUBRIC_VERSIONS[rubric_key]}

    return create_record_machine_review_row(
        session,
        record_type="thermo",
        record_id=thermo.id,
        review=review,
        context_digest=digest,
        prompt_version=RUNNER_VERSION,
        rubric_versions=rubric_versions,
    )


def latest_cp_comparison_for_thermo(session: Session, thermo_id: int) -> RecordMachineReviewRow | None:
    """Return the latest recorded external-Cp-comparison row for one thermo, or ``None``.

    Reads its own family (:attr:`~app.services.machine_review.query.
    MachineReviewRecordFamily.scientific_check`) and model
    (:data:`RUNNER_VERSION`), so "the latest recorded Cp comparison for this thermo"
    is a well-defined notion independent of any reviewer-family (LLM /
    fake-provider) row that may also exist for the same ``(thermo, "thermo")``
    key. Callers that want the latest Cp comparison (the CLI, the paper
    generator) should use this rather than the generic
    :func:`~app.services.machine_review.query.get_latest_record_machine_review_row`
    call with no family, which would default to the reviewer family and
    never see a Cp row at all. Read-only.
    """
    return get_latest_record_machine_review_row(
        session,
        record_type="thermo",
        record_id=thermo_id,
        family=MachineReviewRecordFamily.scientific_check,
        model=RUNNER_VERSION,
    )


def cp_comparison_currency(session, thermo_id):
    """Re-read live inputs; latest recorded alone does not establish currency."""
    result = compare_thermo_with_cp_observations(session, thermo_id)
    rubric = EXTERNAL_CP_COMPARISON_V2
    digest = build_machine_review_context_hash(MachineReviewEvidenceContext(
        record_type="thermo", record_ref=result.thermo.public_ref,
        rubric_name=rubric.name, rubric_version=rubric.version, notes=(result.inputs_json,),
    ))
    key = public_rubric_name(rubric)
    return get_record_machine_review_currency_for_record(
        session, record_type="thermo", record_id=thermo_id, current_context=digest,
        active_prompt_version=RUNNER_VERSION, active_rubric_versions={key: ACTIVE_MACHINE_REVIEW_RUBRIC_VERSIONS[key]},
        family=MachineReviewRecordFamily.scientific_check, model=RUNNER_VERSION,
    )


__all__ = [
    "PROVIDER",
    "RUNNER_VERSION",
    "ComparisonResult",
    "CpObservationComparison",
    "ExternalCpComparisonConfigurationError",
    "compare_thermo_with_cp_observations",
    "cp_comparison_currency",
    "latest_cp_comparison_for_thermo",
    "run_and_record",
]
