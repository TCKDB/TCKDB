"""Upload endpoints — the primary write path into TCKDB.

Each route wraps a workflow orchestrator. Transaction management is handled by
the ``get_write_db`` dependency (commit on success, rollback on exception).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_write_db
from app.api.idempotency import IdempotencyContext, idempotency_dependency
from app.db.models.app_user import AppUser
from app.db.models.common import SubmissionKind
from app.schemas.entities.calculation import CalculationUploadRef
from app.schemas.upload_warning import UploadWarning
from app.schemas.workflows.computed_reaction_upload import ComputedReactionUploadRequest

# -- Request schema imports --------------------------------------------------
from app.schemas.workflows.computed_species_upload import (
    CalculationUploadRefInBundle,
    ComputedSpeciesUploadRequest,
    ComputedSpeciesUploadResult,
    ConformerUploadRefInBundle,
    StatmechUploadRefInBundle,
    ThermoUploadRefInBundle,
)
from app.schemas.workflows.conformer_upload import ConformerUploadRequest
from app.schemas.workflows.kinetics_upload import KineticsUploadRequest
from app.schemas.workflows.network_pdep_upload import NetworkPDepUploadRequest
from app.schemas.workflows.network_upload import NetworkUploadRequest
from app.schemas.workflows.reaction_upload import ReactionUploadRequest
from app.schemas.workflows.statmech_upload import StatmechUploadRequest
from app.schemas.workflows.thermo_upload import ThermoUploadRequest
from app.schemas.workflows.transition_state_upload import (
    TransitionStateUploadRequest,
)
from app.schemas.workflows.transport_upload import TransportUploadRequest
from app.services.provenance_warnings import (
    collect_kinetics_provenance_warnings,
    collect_statmech_provenance_warnings,
    collect_thermo_provenance_warnings,
    collect_transport_provenance_warnings,
)
from app.services.upload_reconciliation import (
    reconcile_species_entry,
    reconcile_species_entry_full,
)
from app.services.upload_submission import (
    audit_sync_upload_failure,
    mark_upload_ingested,
    open_upload_submission,
)
from app.workflows.computed_reaction import persist_computed_reaction_upload

# -- Workflow imports --------------------------------------------------------
from app.workflows.computed_species import persist_computed_species_upload
from app.workflows.conformer import persist_conformer_upload
from app.workflows.kinetics import persist_kinetics_upload
from app.workflows.network import persist_network_upload
from app.workflows.network_pdep import persist_network_pdep_upload
from app.workflows.reaction import persist_reaction_upload
from app.workflows.statmech import persist_statmech_upload
from app.workflows.thermo import persist_thermo_upload
from app.workflows.transition_state import persist_transition_state_upload
from app.workflows.transport import persist_transport_upload

router = APIRouter()


# ---------------------------------------------------------------------------
# Response models (minimal identity + key links)
# ---------------------------------------------------------------------------


class ConformerUploadResult(BaseModel):
    id: int
    type: str = "conformer_observation"
    submission_id: int | None = None
    species_entry_id: int
    conformer_group_id: int
    primary_calculation: CalculationUploadRef
    additional_calculations: list[CalculationUploadRef] = []
    warnings: list[UploadWarning] = []


class ReactionUploadResult(BaseModel):
    id: int
    type: str = "reaction_entry"
    submission_id: int | None = None
    reaction_id: int
    warnings: list[UploadWarning] = []


class KineticsUploadResult(BaseModel):
    id: int
    type: str = "kinetics"
    submission_id: int | None = None
    reaction_entry_id: int
    warnings: list[UploadWarning] = []


class NetworkUploadResult(BaseModel):
    id: int
    type: str = "network"
    submission_id: int | None = None
    warnings: list[UploadWarning] = []


class NetworkPDepUploadResult(BaseModel):
    id: int
    type: str = "network_pdep"
    submission_id: int | None = None
    solve_id: int | None = None
    warnings: list[UploadWarning] = []


class StatmechUploadResult(BaseModel):
    id: int
    type: str = "statmech"
    submission_id: int | None = None
    species_entry_id: int
    warnings: list[UploadWarning] = []


class ThermoUploadResult(BaseModel):
    id: int
    type: str = "thermo"
    submission_id: int | None = None
    species_entry_id: int
    warnings: list[UploadWarning] = []


class TransitionStateUploadResult(BaseModel):
    id: int
    type: str = "transition_state_entry"
    submission_id: int | None = None
    transition_state_id: int
    reaction_entry_id: int
    warnings: list[UploadWarning] = []


class TransportUploadResult(BaseModel):
    id: int
    type: str = "transport"
    submission_id: int | None = None
    species_entry_id: int
    warnings: list[UploadWarning] = []


class ComputedReactionUploadResult(BaseModel):
    type: str = "computed_reaction"
    submission_id: int | None = None
    reaction_entry_id: int
    reaction_id: int
    transition_state_entry_id: int | None = None
    kinetics_ids: list[int]
    thermo_ids: list[int]
    species_entry_ids: list[int]
    species_count: int
    # Bundle-local calc key → assigned ``calculation.id`` for every
    # calculation persisted (or reused) by this upload. Enables
    # second-phase artifact uploads on the client: the builder mints
    # the local key, the bundle workflow records the resolved id, and
    # ``upload_artifacts(plan)`` glues the two together. Response-only
    # field; the request payload shape is unchanged.
    calculation_keys: dict[str, int] = Field(default_factory=dict)
    warnings: list[UploadWarning] = []


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/conformers",
    response_model=ConformerUploadResult,
    status_code=201,
)
@audit_sync_upload_failure(SubmissionKind.conformer)
def upload_conformer(
    request: ConformerUploadRequest,
    session: Session = Depends(get_write_db),
    current_user: AppUser = Depends(get_current_user),
    idem: IdempotencyContext = Depends(idempotency_dependency),
):
    if (replay := idem.maybe_replay()) is not None:
        return replay
    warnings = reconcile_species_entry_full(
        request.species_entry,
        primary_calc=request.calculation,
        additional_calcs=request.additional_calculations,
        statmech=request.statmech,
    )
    sub = open_upload_submission(
        session, created_by=current_user.id, kind=SubmissionKind.conformer
    )
    outcome = persist_conformer_upload(
        session, request, created_by=current_user.id, review_policy=sub.policy
    )
    observation = outcome.observation
    result = ConformerUploadResult(
        id=observation.id,
        submission_id=sub.submission_id,
        species_entry_id=observation.conformer_group.species_entry_id,
        conformer_group_id=observation.conformer_group_id,
        primary_calculation=CalculationUploadRef(
            request_index=outcome.primary_calculation.request_index,
            calculation_id=outcome.primary_calculation.calculation_id,
            type=outcome.primary_calculation.type,
            role=outcome.primary_calculation.role,
        ),
        additional_calculations=[
            CalculationUploadRef(
                request_index=ref.request_index,
                calculation_id=ref.calculation_id,
                type=ref.type,
                role=ref.role,
            )
            for ref in outcome.additional_calculations
        ],
        warnings=warnings,
    )
    mark_upload_ingested(session, sub)
    idem.record(session, status_code=201, body=result.model_dump(mode="json"))
    return result


@router.post(
    "/reactions",
    response_model=ReactionUploadResult,
    status_code=201,
)
@audit_sync_upload_failure(SubmissionKind.reaction)
def upload_reaction(
    request: ReactionUploadRequest,
    session: Session = Depends(get_write_db),
    current_user: AppUser = Depends(get_current_user),
    idem: IdempotencyContext = Depends(idempotency_dependency),
):
    if (replay := idem.maybe_replay()) is not None:
        return replay
    warnings: list[UploadWarning] = []
    for i, p in enumerate(request.reactants):
        if p.species_entry is not None:
            ws = reconcile_species_entry(p.species_entry)
            for w in ws:
                warnings.append(w.model_copy(update={"field": f"reactants[{i}].{w.field}"}))
    for i, p in enumerate(request.products):
        if p.species_entry is not None:
            ws = reconcile_species_entry(p.species_entry)
            for w in ws:
                warnings.append(w.model_copy(update={"field": f"products[{i}].{w.field}"}))
    sub = open_upload_submission(
        session, created_by=current_user.id, kind=SubmissionKind.reaction
    )
    reaction_entry = persist_reaction_upload(
        session, request, created_by=current_user.id, review_policy=sub.policy
    )
    result = ReactionUploadResult(
        id=reaction_entry.id,
        submission_id=sub.submission_id,
        reaction_id=reaction_entry.reaction_id,
        warnings=warnings,
    )
    mark_upload_ingested(session, sub)
    idem.record(session, status_code=201, body=result.model_dump(mode="json"))
    return result


@router.post(
    "/kinetics",
    response_model=KineticsUploadResult,
    status_code=201,
)
@audit_sync_upload_failure(SubmissionKind.kinetics)
def upload_kinetics(
    request: KineticsUploadRequest,
    session: Session = Depends(get_write_db),
    current_user: AppUser = Depends(get_current_user),
    idem: IdempotencyContext = Depends(idempotency_dependency),
):
    if (replay := idem.maybe_replay()) is not None:
        return replay
    warnings: list[UploadWarning] = []
    for i, p in enumerate(request.reaction.reactants):
        ws = reconcile_species_entry(p.species_entry)
        for w in ws:
            warnings.append(w.model_copy(update={"field": f"reaction.reactants[{i}].{w.field}"}))
    for i, p in enumerate(request.reaction.products):
        ws = reconcile_species_entry(p.species_entry)
        for w in ws:
            warnings.append(w.model_copy(update={"field": f"reaction.products[{i}].{w.field}"}))
    warnings.extend(collect_kinetics_provenance_warnings(request))
    sub = open_upload_submission(
        session, created_by=current_user.id, kind=SubmissionKind.kinetics
    )
    kinetics = persist_kinetics_upload(
        session, request, created_by=current_user.id, review_policy=sub.policy
    )
    result = KineticsUploadResult(
        id=kinetics.id,
        submission_id=sub.submission_id,
        reaction_entry_id=kinetics.reaction_entry_id,
        warnings=warnings,
    )
    mark_upload_ingested(session, sub)
    idem.record(session, status_code=201, body=result.model_dump(mode="json"))
    return result


@router.post(
    "/networks",
    response_model=NetworkUploadResult,
    status_code=201,
)
@audit_sync_upload_failure(SubmissionKind.network)
def upload_network(
    request: NetworkUploadRequest,
    session: Session = Depends(get_write_db),
    current_user: AppUser = Depends(get_current_user),
    idem: IdempotencyContext = Depends(idempotency_dependency),
):
    if (replay := idem.maybe_replay()) is not None:
        return replay
    sub = open_upload_submission(
        session, created_by=current_user.id, kind=SubmissionKind.network
    )
    network = persist_network_upload(
        session, request, created_by=current_user.id, review_policy=sub.policy
    )
    result = NetworkUploadResult(id=network.id, submission_id=sub.submission_id)
    mark_upload_ingested(session, sub)
    idem.record(session, status_code=201, body=result.model_dump(mode="json"))
    return result


@router.post(
    "/networks/pdep",
    response_model=NetworkPDepUploadResult,
    status_code=201,
)
@audit_sync_upload_failure(SubmissionKind.network_pdep)
def upload_network_pdep(
    request: NetworkPDepUploadRequest,
    session: Session = Depends(get_write_db),
    current_user: AppUser = Depends(get_current_user),
    idem: IdempotencyContext = Depends(idempotency_dependency),
):
    if (replay := idem.maybe_replay()) is not None:
        return replay
    sub = open_upload_submission(
        session, created_by=current_user.id, kind=SubmissionKind.network_pdep
    )
    network = persist_network_pdep_upload(
        session, request, created_by=current_user.id, review_policy=sub.policy
    )
    solve_id = network.solves[0].id if network.solves else None
    result = NetworkPDepUploadResult(
        id=network.id, submission_id=sub.submission_id, solve_id=solve_id
    )
    mark_upload_ingested(session, sub)
    idem.record(session, status_code=201, body=result.model_dump(mode="json"))
    return result


@router.post(
    "/statmech",
    response_model=StatmechUploadResult,
    status_code=201,
)
@audit_sync_upload_failure(SubmissionKind.statmech)
def upload_statmech(
    request: StatmechUploadRequest,
    session: Session = Depends(get_write_db),
    current_user: AppUser = Depends(get_current_user),
    idem: IdempotencyContext = Depends(idempotency_dependency),
):
    """Create a standalone statmech record for a resolvable species entry.

    The request carries the target species-entry identity, statmech
    scientific fields, provenance references, optional inline
    supporting calculations keyed by local string, and optional
    torsions. Statmech is append-only — repeated uploads against the
    same species entry create independent rows.
    """
    if (replay := idem.maybe_replay()) is not None:
        return replay
    warnings = reconcile_species_entry(request.species_entry)
    warnings.extend(collect_statmech_provenance_warnings(request))
    sub = open_upload_submission(
        session, created_by=current_user.id, kind=SubmissionKind.statmech
    )
    statmech = persist_statmech_upload(
        session, request, created_by=current_user.id, review_policy=sub.policy
    )
    result = StatmechUploadResult(
        id=statmech.id,
        submission_id=sub.submission_id,
        species_entry_id=statmech.species_entry_id,
        warnings=warnings,
    )
    mark_upload_ingested(session, sub)
    idem.record(session, status_code=201, body=result.model_dump(mode="json"))
    return result


@router.post(
    "/thermo",
    response_model=ThermoUploadResult,
    status_code=201,
)
@audit_sync_upload_failure(SubmissionKind.thermo)
def upload_thermo(
    request: ThermoUploadRequest,
    session: Session = Depends(get_write_db),
    current_user: AppUser = Depends(get_current_user),
    idem: IdempotencyContext = Depends(idempotency_dependency),
):
    if (replay := idem.maybe_replay()) is not None:
        return replay
    warnings = reconcile_species_entry(request.species_entry)
    warnings.extend(collect_thermo_provenance_warnings(request))
    sub = open_upload_submission(
        session, created_by=current_user.id, kind=SubmissionKind.thermo
    )
    thermo = persist_thermo_upload(
        session, request, created_by=current_user.id, review_policy=sub.policy
    )
    result = ThermoUploadResult(
        id=thermo.id,
        submission_id=sub.submission_id,
        species_entry_id=thermo.species_entry_id,
        warnings=warnings,
    )
    mark_upload_ingested(session, sub)
    idem.record(session, status_code=201, body=result.model_dump(mode="json"))
    return result


@router.post(
    "/transition-states",
    response_model=TransitionStateUploadResult,
    status_code=201,
)
@audit_sync_upload_failure(SubmissionKind.transition_state)
def upload_transition_state(
    request: TransitionStateUploadRequest,
    session: Session = Depends(get_write_db),
    current_user: AppUser = Depends(get_current_user),
    idem: IdempotencyContext = Depends(idempotency_dependency),
):
    if (replay := idem.maybe_replay()) is not None:
        return replay
    warnings: list[UploadWarning] = []
    for i, p in enumerate(request.reaction.reactants):
        ws = reconcile_species_entry(p.species_entry)
        for w in ws:
            warnings.append(w.model_copy(update={"field": f"reaction.reactants[{i}].{w.field}"}))
    for i, p in enumerate(request.reaction.products):
        ws = reconcile_species_entry(p.species_entry)
        for w in ws:
            warnings.append(w.model_copy(update={"field": f"reaction.products[{i}].{w.field}"}))
    sub = open_upload_submission(
        session, created_by=current_user.id, kind=SubmissionKind.transition_state
    )
    ts_entry = persist_transition_state_upload(
        session, request, created_by=current_user.id, review_policy=sub.policy
    )
    result = TransitionStateUploadResult(
        id=ts_entry.id,
        submission_id=sub.submission_id,
        transition_state_id=ts_entry.transition_state_id,
        reaction_entry_id=ts_entry.transition_state.reaction_entry_id,
        warnings=warnings,
    )
    mark_upload_ingested(session, sub)
    idem.record(session, status_code=201, body=result.model_dump(mode="json"))
    return result


@router.post(
    "/transport",
    response_model=TransportUploadResult,
    status_code=201,
)
@audit_sync_upload_failure(SubmissionKind.transport)
def upload_transport(
    request: TransportUploadRequest,
    session: Session = Depends(get_write_db),
    current_user: AppUser = Depends(get_current_user),
    idem: IdempotencyContext = Depends(idempotency_dependency),
):
    """Create a standalone transport record for a resolvable species entry.

    The request carries the target species-entry identity, transport
    properties, provenance references, and optional inline supporting
    calculations with role links. Transport is append-only — repeated
    uploads against the same species entry create independent rows.
    """
    if (replay := idem.maybe_replay()) is not None:
        return replay
    warnings = reconcile_species_entry(request.species_entry)
    warnings.extend(collect_transport_provenance_warnings(request))
    sub = open_upload_submission(
        session, created_by=current_user.id, kind=SubmissionKind.transport
    )
    transport = persist_transport_upload(
        session, request, created_by=current_user.id, review_policy=sub.policy
    )
    result = TransportUploadResult(
        id=transport.id,
        submission_id=sub.submission_id,
        species_entry_id=transport.species_entry_id,
        warnings=warnings,
    )
    mark_upload_ingested(session, sub)
    idem.record(session, status_code=201, body=result.model_dump(mode="json"))
    return result


@router.post(
    "/computed-species",
    response_model=ComputedSpeciesUploadResult,
    status_code=201,
)
@audit_sync_upload_failure(SubmissionKind.computed_species)
def upload_computed_species(
    request: ComputedSpeciesUploadRequest,
    session: Session = Depends(get_write_db),
    current_user: AppUser = Depends(get_current_user),
    idem: IdempotencyContext = Depends(idempotency_dependency),
):
    """Bundle upload: identity + conformers + per-conformer calcs +
    artifacts + optional thermo, atomic in one transaction (DR-0029)."""
    if (replay := idem.maybe_replay()) is not None:
        return replay
    warnings = reconcile_species_entry(request.species_entry)
    sub = open_upload_submission(
        session, created_by=current_user.id, kind=SubmissionKind.computed_species
    )
    outcome = persist_computed_species_upload(
        session, request, created_by=current_user.id, review_policy=sub.policy
    )
    conformer_refs = [
        ConformerUploadRefInBundle(
            key=co.conformer_in_bundle.key,
            conformer_group_id=co.group_id,
            conformer_observation_id=co.observation.id,
            primary_calculation=CalculationUploadRefInBundle(
                key=co.conformer_in_bundle.primary_calculation.key,
                calculation_id=co.primary_calculation.id,
                type=co.primary_calculation.type,
                role="primary",
            ),
            additional_calculations=[
                CalculationUploadRefInBundle(
                    key=add_in.key,
                    calculation_id=add_calc.id,
                    type=add_calc.type,
                    role="additional",
                )
                for add_in, add_calc in zip(
                    co.conformer_in_bundle.additional_calculations,
                    co.additional_calculations,
                    strict=True,
                )
            ],
        )
        for co in outcome.conformers
    ]
    thermo_ref = (
        ThermoUploadRefInBundle(thermo_id=outcome.thermo.id)
        if outcome.thermo is not None
        else None
    )
    statmech_ref = (
        StatmechUploadRefInBundle(statmech_id=outcome.statmech.id)
        if outcome.statmech is not None
        else None
    )
    result = ComputedSpeciesUploadResult(
        species_entry_id=outcome.species_entry_id,
        submission_id=sub.submission_id,
        conformers=conformer_refs,
        thermo=thermo_ref,
        statmech=statmech_ref,
        warnings=warnings,
    )
    mark_upload_ingested(session, sub)
    idem.record(session, status_code=201, body=result.model_dump(mode="json"))
    return result


@router.post(
    "/computed-reaction",
    response_model=ComputedReactionUploadResult,
    status_code=201,
)
@audit_sync_upload_failure(SubmissionKind.computed_reaction)
def upload_computed_reaction(
    request: ComputedReactionUploadRequest,
    session: Session = Depends(get_write_db),
    current_user: AppUser = Depends(get_current_user),
    idem: IdempotencyContext = Depends(idempotency_dependency),
):
    if (replay := idem.maybe_replay()) is not None:
        return replay
    sub = open_upload_submission(
        session, created_by=current_user.id, kind=SubmissionKind.computed_reaction
    )
    result_dict = persist_computed_reaction_upload(
        session, request, created_by=current_user.id, review_policy=sub.policy
    )
    result = ComputedReactionUploadResult(
        **result_dict, submission_id=sub.submission_id
    )
    mark_upload_ingested(session, sub)
    idem.record(session, status_code=201, body=result.model_dump(mode="json"))
    return result
