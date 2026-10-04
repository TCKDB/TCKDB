"""Read-only coverage inventory: how many stored kinetics records can answer the question they state, and why not.

A kinetics selection needs a stated question (direction, target, coefficient basis, temperature window, pressure,
collider) and a record that states what it answers. Most records deposited before this release state neither. This
inventory runs the selection's own assessor over every stored kinetics record, each against the question *its own
declarations pose*, and counts. It writes nothing, backfills nothing, and states no claim for a record that did not
state it: a record whose own question cannot be formed (no determination, no applicability block, no temperature
window) is counted ``unresolved`` with the reasons it lacks, never assumed standard.

The numbers describe what the stored data supports today, which is the point: they show how much of the corpus
method-aware selection could act on before depositors declare anything more.

How a record's own question is formed (every item must be stated by the record itself; none is defaulted):

* direction and target: the record's determination;
* coefficient basis, pressure dependence, collider: the record's applicability declaration;
* temperature window: the record's own ``tmin_k``/``tmax_k``;
* pressure: independent or high-pressure limit as declared; a fixed-pressure fit at its own pressure; a
  pressure-dependent surface over its declared domain.

For a finite pressure the question needs a collider. A record that names a specified collider or a mixture is asked
about that; one that declares itself independent of the collider is asked about a probe collider no record names.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from tckdb_schemas.kinetics_declarations import KineticsCoefficientBasis

from app.db.models.common import (
    KineticsDeterminationTargetKind,
    KineticsDirection,
    RecordReviewStatus,
    ScientificOriginKind,
    SubmissionRecordType,
)
from app.db.models.kinetics import Kinetics
from app.db.models.reaction import ReactionEntry
from app.services.kinetics_selection.assessment import assess_candidate
from app.services.kinetics_selection.loader import _LOAD_OPTIONS, load_subject, normalize_rows
from app.services.kinetics_selection.models import (
    MAX_CANDIDATES,
    ColliderRequest,
    KineticsRequest,
    NormalizedKinetics,
    PressureKind,
    PressureRequest,
    TargetRequest,
)
from app.services.kinetics_selection.rules import default_rules
from app.services.scientific_read.common import fetch_review_badges
from app.services.selection_kernel import Applicability
from app.services.trust import evaluate_loaded_kinetics

#: A collider ref no stored record names, to ask "can this record answer for any collider?".
PROBE_COLLIDER = "spc_inventory_probe"

NOT_STATED = "question_not_stated"


def _batches(session: Session, size: int) -> Iterator[list[int]]:
    """Reaction-entry ids that have at least one kinetics record, ``size`` at a time."""
    last = 0
    while True:
        ids = list(
            session.scalars(
                select(Kinetics.reaction_entry_id)
                .where(Kinetics.reaction_entry_id > last)
                .group_by(Kinetics.reaction_entry_id)
                .order_by(Kinetics.reaction_entry_id)
                .limit(size)
            )
        )
        if not ids:
            return
        yield ids
        last = ids[-1]


def _code(reason_code: str) -> str:
    """The reason's stable head (``phase_not_gas:liquid`` counts as ``phase_not_gas``)."""
    return reason_code.split(":", 1)[0]


def own_question(c: NormalizedKinetics) -> tuple[KineticsRequest | None, list[str]]:
    """The question a record's own declarations pose, or ``None`` and what it fails to state."""
    missing: list[str] = []
    det = c.determination
    if det is None:
        missing.append("determination")
    block = c.applicability if c.applicability_state == "valid" else None
    if block is None:
        missing.append("applicability_declaration")
    if c.tmin_k is None or c.tmax_k is None:
        missing.append("temperature_window")
    if missing or det is None or block is None or c.tmin_k is None or c.tmax_k is None:
        return None, missing
    try:
        direction = KineticsDirection(det.direction)
        target = TargetRequest(
            kind=KineticsDeterminationTargetKind(det.target_kind),
            transition_state_entry_ref=det.transition_state_entry_ref,
            network_ref=det.network_ref,
            channel_key=det.channel_key,
        )
        basis = KineticsCoefficientBasis(block["coefficient_basis"]) if block.get("coefficient_basis") else None
    except (ValueError, KeyError):
        return None, ["determination_or_basis_unreadable"]
    if basis is None:
        return None, ["coefficient_basis"]
    dependence = block.get("pressure_dependence")
    pressure: PressureRequest | None = None
    if dependence == "independent":
        pressure = PressureRequest(PressureKind.independent)
    elif dependence == "high_pressure_limit":
        pressure = PressureRequest(PressureKind.high_pressure_limit)
    elif dependence == "fixed_pressure" and c.pressure_bar is not None and c.pressure_bar > 0:
        pressure = PressureRequest(PressureKind.finite, c.pressure_bar, c.pressure_bar)
    elif dependence == "pressure_dependent":
        low, high = block.get("pressure_domain_min_bar"), block.get("pressure_domain_max_bar")
        if low is not None and high is not None and 0 < low <= high:
            pressure = PressureRequest(PressureKind.finite, low, high)
    if pressure is None:
        return None, ["pressure"]
    collider: ColliderRequest | None = None
    if pressure.kind is PressureKind.finite or basis is KineticsCoefficientBasis.composition_effective_coefficient:
        named = [x for x in block.get("colliders") or [] if x.get("species_ref")]
        try:
            if len(named) == 1:
                collider = ColliderRequest((named[0]["species_ref"],))
            elif len(named) > 1 and all(x.get("mole_fraction") is not None for x in named):
                collider = ColliderRequest(
                    tuple(x["species_ref"] for x in named), tuple(x["mole_fraction"] for x in named)
                )
            elif not named and block.get("collider_kind") == "not_dependent":
                collider = ColliderRequest((PROBE_COLLIDER,))
        except ValueError:
            collider = None
        if collider is None:
            return None, ["collider"]
    try:
        return (
            KineticsRequest(
                direction=direction,
                target=target,
                coefficient_basis=basis,
                temperature_min_k=c.tmin_k,
                temperature_max_k=c.tmax_k,
                pressure=pressure,
                collider=collider,
                max_candidates=MAX_CANDIDATES,
            ),
            [],
        )
    except ValueError:
        return None, ["question_not_well_posed"]


def kinetics_coverage_inventory(session: Session, *, batch_size: int = 100) -> dict[str, Any]:
    """Count stored kinetics records by applicability to their own stated question, and why not. Read-only."""
    rules = default_rules()
    applicability: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    blocking: Counter[str] = Counter()
    not_stated: Counter[str] = Counter()
    origin: Counter[str] = Counter()
    review: Counter[str] = Counter()
    model_kind: Counter[str] = Counter()
    qualifying_determinations: set[str] = set()
    determinations: set[str] = set()
    entries_with_a_qualifying_record = 0
    qualifying = 0
    entries = 0
    total = 0
    protocol_stated = 0
    declared_energy_levels = 0
    rule_scope: dict[str, Counter[str]] = {r.rule_id: Counter() for r in rules}

    for entry_ids in _batches(session, batch_size):
        for entry_id in entry_ids:
            entry = session.get(ReactionEntry, entry_id)
            assert entry is not None
            rows = list(
                session.scalars(
                    select(Kinetics).where(Kinetics.reaction_entry_id == entry_id).options(*_LOAD_OPTIONS).order_by(Kinetics.id)
                )
            )
            ids = [k.id for k in rows]
            badges = fetch_review_badges(session, record_type=SubmissionRecordType.kinetics, record_ids=ids)
            statuses: dict[int, RecordReviewStatus] = {i: badges[i].status for i in ids}
            normalized = normalize_rows(session, entry, rows, statuses)
            entries += 1
            any_applicable = False
            subject = None
            for k in rows:
                total += 1
                c = normalized[k.id]
                origin[c.scientific_origin] += 1
                review[statuses[k.id].value] += 1
                model_kind[c.model_kind] += 1
                if c.determination is not None:
                    determinations.add(c.determination.determination_ref)
                if c.protocol_state == "valid":
                    protocol_stated += 1
                if any(level["source"] == "protocol_declared" for level in c.energy_levels):
                    declared_energy_levels += 1
                request, missing = own_question(c)
                if request is None:
                    applicability[Applicability.unresolved.value] += 1
                    for item in missing:
                        not_stated[item] += 1
                    reasons[NOT_STATED] += 1
                    continue
                evidence = (
                    evaluate_loaded_kinetics(k) if k.scientific_origin is ScientificOriginKind.computed else None
                )
                assessment = assess_candidate(c, request=request, evidence=evidence)
                applicability[assessment.applicability.value] += 1
                for reason in assessment.reasons:
                    reasons[_code(reason.code)] += 1
                for item in assessment.blocking:
                    blocking[_code(item)] += 1
                if assessment.physically_eligible:
                    any_applicable = True
                    qualifying += 1
                    if c.determination is not None:
                        qualifying_determinations.add(c.determination.determination_ref)
                if subject is None:
                    subject = load_subject(session, entry)
                for rule in rules:
                    rule_scope[rule.rule_id][rule.scope(subject, request).state.value] += 1
            if any_applicable:
                entries_with_a_qualifying_record += 1

    entries_over_cap = session.scalar(
        select(func.count()).select_from(
            select(Kinetics.reaction_entry_id)
            .group_by(Kinetics.reaction_entry_id)
            .having(func.count() > MAX_CANDIDATES)
            .subquery()
        )
    )
    return {
        "kinetics_records": total,
        "reaction_entries_with_kinetics": entries,
        "by_origin": dict(sorted(origin.items())),
        "by_review_status": dict(sorted(review.items())),
        "by_model_kind": dict(sorted(model_kind.items())),
        "applicability_to_own_question": {a.value: applicability.get(a.value, 0) for a in Applicability},
        "qualifying_records": qualifying,
        "unresolved_records": applicability.get(Applicability.unresolved.value, 0),
        "unsupported_records": applicability.get(Applicability.unsupported.value, 0),
        "not_applicable_reasons": dict(sorted(reasons.items())),
        "question_not_stated_because": dict(sorted(not_stated.items())),
        "blocking_validation": dict(sorted(blocking.items())),
        "determinations_declared": len(determinations),
        "determinations_with_a_qualifying_record": len(qualifying_determinations),
        "reaction_entries_with_a_qualifying_record": entries_with_a_qualifying_record,
        "records_with_a_stated_protocol": protocol_stated,
        "records_naming_a_declared_energy_calculation": declared_energy_levels,
        "reaction_entries_over_candidate_cap": int(entries_over_cap or 0),
        "candidate_cap": MAX_CANDIDATES,
        "rules": {
            rule.rule_id: {
                "status": rule.status,
                "inactive_reasons": list(rule.inactive_reasons),
                "scope_of_stated_questions": dict(sorted(rule_scope[rule.rule_id].items())),
            }
            for rule in rules
        },
        "notes": [
            "Each record is assessed against the question its own declarations pose; a record that does not state one "
            "is counted unresolved with the facts it lacks, never assumed standard.",
            "A record counted qualifying can answer the question it states. Whether any rule ranks it needs a stated "
            "protocol on every competing record, and no rule is active in this release.",
            "Nothing is written and nothing is backfilled.",
        ],
    }
