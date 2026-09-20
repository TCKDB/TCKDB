"""Compare a computed thermo record's heat capacity with external observations.

This is the runner behind the first ``review``-tier scientific check
(:mod:`app.scientific_checks.external_comparison`,
``docs/research/tckdb-phase-c-implementation-plan.md`` C4). Per ADR 0008 the
``review`` tier never blocks an upload, carries no error-envelope code, and
has no approval effect: :func:`compare_thermo_with_cp_observations` is pure
(no writes) and :func:`run_and_record` appends exactly one
``record_machine_review`` row through the existing persistence helper. Nothing
here mutates ``thermo``, ``molecular_property_observation``, or any other
scientific record.

What is compared
-----------------
A thermo with ``scientific_origin=computed`` and a NASA-7
(:class:`~app.db.models.thermo.ThermoNASA`), NASA-9
(:class:`~app.db.models.thermo.ThermoNASA9Interval`), or tabulated-point
(:class:`~app.db.models.thermo.ThermoPoint`) representation, against every
``molecular_property_observation`` row on the same ``species_entry`` with
``property_kind=heat_capacity_cp``. NASA representations are evaluated with
Cantera (lazy import — an absent engine is a configuration error, never a
finding, see :class:`ExternalCpComparisonConfigurationError`); a point
representation is compared only at an exactly matching temperature, with no
Cantera dependency. Preference order when a thermo carries more than one
representation is NASA-7, then NASA-9, then point.

No ratio is judged and no threshold is applied anywhere in this module: a
finding always reports the residual and lets a human curator judge it.
``real_gas``-basis observations (``ObservedStateBasis.real_gas``) are still
compared, flagged ``non_ideality: "unquantified"`` with the observation's own
recorded pressure alongside the residual (Phase C amendment to the
implementation plan's C2 "State" bullet); ``not_comparable`` is reserved for a
temperature outside the fit's range, or (for a point representation) no
exactly matching stored point.

Observation and custody references
-----------------------------------
``molecular_property_observation`` does not carry a
:class:`~app.db.base.PublicRefMixin` public ref (Phase C-E1 did not add one,
and this change may not add a migration), so :func:`_observation_ref` cites
the linked ``external_source_record.source_record_key`` (the natural,
content-derived per-value key ThermoML custody rows carry, see the C2 design
note) when custody exists, and otherwise a deterministic
``property@temperature=value`` label built from the same fields that
participate in the observation's own dedupe key. Neither branch ever
surfaces ``molecular_property_observation.id``.

Units
-----
Cantera's ``cp_mole`` is J/(kmol*K); TCKDB stores and reports J/(mol*K)
everywhere. The single conversion point is :data:`_J_PER_KMOL_K_TO_J_PER_MOL_K`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.common import MolecularPropertyKind, ObservedStateBasis, ScientificOriginKind
from app.db.models.molecular_property_observation import MolecularPropertyObservation
from app.db.models.record_machine_review import RecordMachineReviewRow
from app.db.models.thermo import Thermo, ThermoNASA, ThermoNASA9Interval, ThermoPoint
from app.services.machine_review.context_hash import (
    MachineReviewEvidenceContext,
    build_machine_review_context_hash,
)
from app.services.machine_review.derivation import (
    MachineReviewOutcome,
    derive_machine_review_status,
)
from app.services.machine_review.persistence import create_record_machine_review_row
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
from app.services.trust.rubrics import EXTERNAL_CP_COMPARISON_V1

#: Cantera's ``ThermoPhase.cp_mole`` is J/(kmol*K); TCKDB's fixed unit for
#: this quantity is J/(mol*K) (unit policy, ``docs/unit_policy.md``). The one
#: conversion point -- see ``test_j_per_kmol_k_to_j_per_mol_k_constant`` for
#: the mutation guard (a wrong constant must fail the NASA-7 hand-calc check).
_J_PER_KMOL_K_TO_J_PER_MOL_K = 1.0 / 1000.0

#: A neutral, real periodic-table element used only to satisfy Cantera's
#: element bookkeeping when building a throwaway single-species ideal-gas
#: ``Solution``. ``cp_mole`` for a single species depends only on its NASA
#: polynomial, never on this placeholder composition, and no reaction or
#: elemental-balance check ever runs against this solution.
_PLACEHOLDER_ELEMENT = "Ar"

#: Model/prompt-version identity for this deterministic runner. There is no
#: LLM prompt involved, so this single string stands in for both the
#: ``model`` and ``prompt_version`` axes ``create_record_machine_review_row``
#: expects: "the version of the logic that produced this review."
RUNNER_VERSION = "external_cp_comparison_v1"

PROVIDER = "tckdb.scientific_checks"

_MESSAGE_METHOD_NOTE_MAX_CHARS = 120
_MESSAGE_MAX_BYTES = 1000


class ExternalCpComparisonConfigurationError(RuntimeError):
    """Cantera is required to evaluate a NASA representation and is absent.

    Never raised for a scientific reason -- an absent optional dependency is
    a deployment configuration problem, not a finding about the record, so it
    is never wrapped into a :class:`~app.services.machine_review.schemas.
    MachineReviewFinding` or a ``record_machine_review`` row.
    """


@dataclass(frozen=True)
class CpObservationComparison:
    """One observation's comparison against the thermo's computed Cp(T)."""

    observation_ref: str
    external_source_record_ref: str | None
    temperature_k: float
    pressure_bar: float | None
    state_basis: str | None
    cp_observed_j_mol_k: float
    cp_computed_j_mol_k: float | None
    residual_j_mol_k: float | None
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


# ---------------------------------------------------------------------------
# Representation evaluators
# ---------------------------------------------------------------------------

# ``(cp_j_mol_k, in_range) | None`` isn't expressible as a single alias
# without importing typing.Optional gymnastics; spelled out at each use site.
_Evaluator = Callable[[float], tuple[float | None, bool]]


def _import_cantera():
    try:
        import cantera as ct
    except Exception as exc:  # pragma: no cover - exercised via monkeypatch
        raise ExternalCpComparisonConfigurationError(
            "Cantera is required for the external Cp comparison runner. "
            "Install the 'chemkin' extra: pip install 'tckdb-backend[chemkin]' "
            "(or `mamba install -n tckdb_env -c conda-forge cantera`)."
        ) from exc
    return ct


def _nasa7_complete(nasa: ThermoNASA) -> bool:
    bounds = (nasa.t_low, nasa.t_mid, nasa.t_high)
    low = (nasa.a1, nasa.a2, nasa.a3, nasa.a4, nasa.a5, nasa.a6, nasa.a7)
    high = (nasa.b1, nasa.b2, nasa.b3, nasa.b4, nasa.b5, nasa.b6, nasa.b7)
    return all(v is not None for v in bounds + low + high)


def _build_nasa7_evaluator(nasa: ThermoNASA) -> _Evaluator:
    ct = _import_cantera()
    t_low, t_mid, t_high = nasa.t_low, nasa.t_mid, nasa.t_high
    # TCKDB convention: a1..a7 is the LOW-temperature interval, b1..b7 the
    # HIGH-temperature interval (see the same convention documented at
    # ``app.services.scientific_read.chemkin_serialize._nasa_card``, which
    # this mirrors). Cantera's ``NasaPoly2`` coeffs array is
    # ``[Tmid] + high(7) + low(7)``.
    high = [nasa.b1, nasa.b2, nasa.b3, nasa.b4, nasa.b5, nasa.b6, nasa.b7]
    low = [nasa.a1, nasa.a2, nasa.a3, nasa.a4, nasa.a5, nasa.a6, nasa.a7]
    poly = ct.NasaPoly2(t_low, t_high, ct.one_atm, [t_mid, *high, *low])
    species = ct.Species("X", {_PLACEHOLDER_ELEMENT: 1})
    species.thermo = poly
    gas = ct.Solution(thermo="ideal-gas", species=[species])

    def evaluate(temperature_k: float) -> tuple[float | None, bool]:
        if not (t_low <= temperature_k <= t_high):
            return None, False
        gas.TP = temperature_k, ct.one_atm
        return gas.cp_mole * _J_PER_KMOL_K_TO_J_PER_MOL_K, True

    return evaluate


def _build_nasa9_evaluator(intervals: list[ThermoNASA9Interval]) -> _Evaluator:
    ct = _import_cantera()
    ordered = sorted(intervals, key=lambda iv: iv.interval_index)
    t_low = min(iv.t_min_k for iv in ordered)
    t_high = max(iv.t_max_k for iv in ordered)
    coeffs: list[float] = [float(len(ordered))]
    for iv in ordered:
        coeffs.extend(
            [iv.t_min_k, iv.t_max_k, iv.a1, iv.a2, iv.a3, iv.a4, iv.a5, iv.a6, iv.a7, iv.a8, iv.a9]
        )
    poly = ct.Nasa9PolyMultiTempRegion(t_low, t_high, ct.one_atm, coeffs)
    species = ct.Species("X", {_PLACEHOLDER_ELEMENT: 1})
    species.thermo = poly
    gas = ct.Solution(thermo="ideal-gas", species=[species])

    def evaluate(temperature_k: float) -> tuple[float | None, bool]:
        if not (t_low <= temperature_k <= t_high):
            return None, False
        gas.TP = temperature_k, ct.one_atm
        return gas.cp_mole * _J_PER_KMOL_K_TO_J_PER_MOL_K, True

    return evaluate


def _build_point_evaluator(points: list[ThermoPoint]) -> _Evaluator:
    by_temperature = {p.temperature_k: p.cp_j_mol_k for p in points}

    def evaluate(temperature_k: float) -> tuple[float | None, bool]:
        cp = by_temperature.get(temperature_k)
        if cp is None:
            return None, False
        return cp, True

    return evaluate


def _resolve_evaluator(thermo: Thermo) -> tuple[str, _Evaluator]:
    """Pick the thermo's representation and build its Cp(T) evaluator.

    Preference order (also the order named in the C4 plan): NASA-7, then
    NASA-9, then tabulated point. Raises :class:`ValueError` -- a caller
    precondition failure, not a finding -- when none is usable.
    """
    if thermo.nasa is not None and _nasa7_complete(thermo.nasa):
        return "nasa7", _build_nasa7_evaluator(thermo.nasa)
    if thermo.nasa9_intervals:
        return "nasa9", _build_nasa9_evaluator(list(thermo.nasa9_intervals))
    if thermo.points:
        return "point", _build_point_evaluator(list(thermo.points))
    raise ValueError(
        f"compare_thermo_with_cp_observations: thermo {thermo.public_ref!r} has no "
        "NASA-7, NASA-9, or tabulated-point representation to compare against"
    )


# ---------------------------------------------------------------------------
# Observation references (never the internal id)
# ---------------------------------------------------------------------------


def _observation_ref(observation: MolecularPropertyObservation) -> str:
    if observation.external_source_record is not None:
        return observation.external_source_record.source_record_key
    return f"heat_capacity_cp@{observation.temperature_k:g}K={observation.scalar_value:g}"


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
) -> CpObservationComparison:
    temperature_k = observation.temperature_k
    cp_observed = observation.scalar_value
    cp_computed, usable = evaluate(temperature_k)

    state_basis = observation.state_basis
    non_ideality = "unquantified" if state_basis is ObservedStateBasis.real_gas else None

    if usable:
        comparability = "comparable"
        comparability_reason = None
        residual = cp_computed - cp_observed
        t_in_range = True if representation != "point" else None
    else:
        comparability = "not_comparable"
        comparability_reason = (
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
    thermo = session.get(Thermo, thermo_id)
    if thermo is None:
        raise ValueError(f"compare_thermo_with_cp_observations: no thermo with id {thermo_id!r}")
    if thermo.scientific_origin is not ScientificOriginKind.computed:
        raise ValueError(
            "compare_thermo_with_cp_observations requires a computed thermo record "
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

    comparisons = tuple(_compare_one(observation, representation, evaluate) for observation in observations)
    return ComparisonResult(thermo=thermo, representation=representation, comparisons=comparisons)


# ---------------------------------------------------------------------------
# Persistence: one record_machine_review row
# ---------------------------------------------------------------------------


def _truncate(text: str | None, max_chars: int) -> str | None:
    if text is None or len(text) <= max_chars:
        return text
    return text[:max_chars]


def _finding_payload(comparison: CpObservationComparison) -> dict:
    return {
        "observation_ref": comparison.observation_ref,
        "external_source_record_ref": comparison.external_source_record_ref,
        "temperature_k": comparison.temperature_k,
        "pressure_bar": comparison.pressure_bar,
        "state_basis": comparison.state_basis,
        "cp_observed_j_mol_k": comparison.cp_observed_j_mol_k,
        "cp_computed_j_mol_k": comparison.cp_computed_j_mol_k,
        "residual_j_mol_k": comparison.residual_j_mol_k,
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


def _context_note(comparison: CpObservationComparison) -> str:
    """One order-insensitive evidence-context token per observation.

    Folds in the observation ref, its value, and (when custody-backed) the
    custody row's content digest and parser/mapping versions -- so the
    context hash changes whenever an observation is added, removed, or
    reparsed under a new importer version, and never on wall-clock data.
    """
    parts = [
        f"observation:{comparison.observation_ref}",
        f"cp_observed_j_mol_k:{comparison.cp_observed_j_mol_k!r}",
        f"temperature_k:{comparison.temperature_k!r}",
    ]
    if comparison.external_source_record_ref is not None:
        parts.append(f"external_source_record:{comparison.external_source_record_ref}")
    return "|".join(parts)


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

    findings = tuple(_finding_from_comparison(c, thermo) for c in result.comparisons)
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

    notes = tuple(sorted(_context_note(c) for c in result.comparisons))
    context = MachineReviewEvidenceContext(
        record_type="thermo",
        record_ref=thermo.public_ref,
        rubric_name=EXTERNAL_CP_COMPARISON_V1.name,
        rubric_version=EXTERNAL_CP_COMPARISON_V1.version,
        notes=(*notes, f"representation:{result.representation}"),
    )
    digest = build_machine_review_context_hash(context)

    rubric_key = public_rubric_name(EXTERNAL_CP_COMPARISON_V1)
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


__all__ = [
    "PROVIDER",
    "RUNNER_VERSION",
    "ComparisonResult",
    "CpObservationComparison",
    "ExternalCpComparisonConfigurationError",
    "compare_thermo_with_cp_observations",
    "run_and_record",
]
