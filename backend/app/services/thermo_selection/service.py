"""H298 candidate selection: load, assess, order, and record the decision.

Service layer only. No HTTP, no persistence, no change to curator endorsements: the
function reads, decides, and returns a result whose ``manifest`` is the replayable
decision record. Browse order and every existing read are untouched.

Flow: scan the visible population (review floor first, rejected and deprecated out) and
refuse above the cap; load and normalise it; assess every record for H298 applicability;
hand the physically eligible ones to the preference engine; build the manifest.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import not_found
from app.db.models.common import ScientificOriginKind
from app.db.models.species import ConformerGroup
from app.db.models.thermo import Thermo
from app.services.calculation_ownership import assert_owned_by
from app.services.thermo_declaration_resolution import W_THERMO_TARGET_GROUP_OWNER_MISMATCH
from app.services.thermo_selection.assessment import assess_candidate
from app.services.thermo_selection.engine import decide
from app.services.thermo_selection.loader import (
    PopulationScan,
    load_population,
    load_subject,
    scan_population,
)
from app.services.thermo_selection.manifest import build_manifest
from app.services.thermo_selection.models import (
    Applicability,
    CandidateAssessment,
    H298Request,
    H298Selection,
    Outcome,
)
from app.services.thermo_selection.rules import PreferenceRule, default_rules
from app.services.trust import evaluate_loaded_thermo


def _excluded_refs(session: Session, scan: PopulationScan) -> list[dict[str, str]]:
    if not scan.excluded:
        return []
    refs: dict[int, str] = {}
    for thermo_id, public_ref in session.execute(
        select(Thermo.id, Thermo.public_ref).where(Thermo.id.in_([i for i, _, _ in scan.excluded]))
    ):
        refs[thermo_id] = public_ref
    return [
        {"thermo_ref": refs[i], "review_status": status.value, "reason": reason}
        for i, status, reason in scan.excluded
    ]


def _group_ref(session: Session, request: H298Request, species_entry_id: int) -> str | None:
    if request.conformer_group_id is None:
        return None
    group = session.get(ConformerGroup, request.conformer_group_id)
    if group is None:
        raise not_found("conformer_group", row_id=request.conformer_group_id)
    assert_owned_by(
        subject_noun="conformer group",
        row_id=group.id,
        row_species_entry_id=group.species_entry_id,
        row_transition_state_entry_id=None,
        code=W_THERMO_TARGET_GROUP_OWNER_MISMATCH,
        target="selection request",
        context="thermodynamic_target.conformer_group_ref",
        species_entry_id=species_entry_id,
    )
    return group.public_ref


def select_h298(
    session: Session,
    *,
    species_entry_id: int,
    request: H298Request,
    rules: Sequence[PreferenceRule] | None = None,
) -> H298Selection:
    """Assess every visible thermo record of a species entry for H298 and decide among the eligible.

    :param rules: The registry to apply; defaults to the rules shipped with this release (E1).
        Tests pass additional rules to build conflicts and cycles.
    :raises NotFoundError: unknown species entry or conformer group.
    :raises CodedValueError: the conformer group belongs to another species entry
        (``thermo_target_group_owner_mismatch``).
    """
    registry = tuple(default_rules() if rules is None else rules)
    group_ref = _group_ref(session, request, species_entry_id)
    scan = scan_population(session, species_entry_id=species_entry_id, request=request)
    excluded = _excluded_refs(session, scan)

    if scan.over_cap:
        # Refuse before loading or assessing anything: a winner chosen from a prefix would be a claim
        # about a population that was never examined.
        manifest = build_manifest(
            request=request, conformer_group_ref=group_ref, effective_statuses=scan.effective_statuses,
            subject=load_subject(session, species_entry_id)[1], total_rows=scan.total_rows,
            visible_candidates=len(scan.population_ids), cap=scan.cap,
            excluded_by_review=excluded, candidates=[], decision=None, outcome=Outcome.bounded_search_exceeded,
        )
        return H298Selection(
            outcome=Outcome.bounded_search_exceeded, selected_ref=None, assessments=(), decision=None,
            manifest=manifest,
            notes=(f"{len(scan.population_ids)} visible candidates exceed the limit of {scan.cap}; nothing was assessed",),
        )

    loaded = load_population(session, scan)
    assessments: list[CandidateAssessment] = []
    rows = []
    for thermo in loaded.rows:
        evidence = evaluate_loaded_thermo(thermo) if thermo.scientific_origin is ScientificOriginKind.computed else None
        assessment = assess_candidate(thermo, request=request, evidence=evidence)
        assessments.append(assessment)
        rows.append((loaded.candidates[thermo.id], assessment, assessment.physically_eligible))

    eligible = [norm for norm, _, ok in rows if ok]
    decision = decide(eligible, subject=loaded.subject, admin_policy=request.admin_policy, rules=registry)
    manifest = build_manifest(
        request=request, conformer_group_ref=group_ref, effective_statuses=scan.effective_statuses,
        subject=loaded.subject, total_rows=scan.total_rows,
        visible_candidates=len(scan.population_ids), cap=scan.cap, excluded_by_review=excluded,
        candidates=rows, decision=decision, outcome=decision.outcome,
    )
    unresolved = tuple(a.thermo_ref for a in assessments if a.applicability is Applicability.unresolved)
    unsupported = tuple(a.thermo_ref for a in assessments if a.applicability is Applicability.unsupported)
    manifest["disclosures"] = {"unresolved_refs": list(unresolved), "unsupported_refs": list(unsupported)}
    notes: list[str] = []
    if unresolved or unsupported:
        # Uniform across every outcome: a result is scoped to what is eligible now, and these records
        # might compete if their missing facts were recorded or their form were evaluated.
        notes.append(
            f"{len(unresolved)} candidate(s) are unresolved and {len(unsupported)} unsupported; they do not compete "
            "in this result and may be applicable if their missing facts were recorded or their form evaluated"
        )
    return H298Selection(
        outcome=decision.outcome, selected_ref=decision.selected_ref, assessments=tuple(assessments),
        decision=decision, manifest=manifest, unresolved_refs=unresolved, unsupported_refs=unsupported,
        notes=tuple(notes),
    )
