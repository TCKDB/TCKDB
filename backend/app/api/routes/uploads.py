"""Upload endpoints — the primary write path into TCKDB.

Each route wraps a workflow orchestrator. Transaction management is handled by
the ``get_write_db`` dependency (commit on success, rollback on exception).
"""

from __future__ import annotations

import base64
import hashlib
from typing import Any

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from tckdb_schemas.coded_error import CodedValidationError

from app.api.deps import get_current_user, get_write_db
from app.api.idempotency import IdempotencyContext, idempotency_dependency
from app.db.models.app_user import AppUser
from app.db.models.common import SubmissionKind
from app.db.models.species import SpeciesEntry
from app.importers.thermoml.archive import ArticleBytes
from app.schemas.entities.calculation import CalculationUploadRef
from app.schemas.fragments.refs import collect_software_release_version_warnings
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
from app.schemas.workflows.thermoml_upload import ThermoMLUploadRequest
from app.schemas.workflows.transition_state_upload import (
    TransitionStateUploadRequest,
)
from app.schemas.workflows.transport_upload import TransportUploadRequest
from app.services.artifact_storage import (
    MAX_ARTIFACT_BYTES,
    MAX_ENCODED_ARTIFACT_LEN,
)
from app.services.frequency_geometry_linearity import (
    computed_reaction_linearity_warnings,
    computed_species_linearity_warnings,
    inline_calculation_linearity_warnings,
    network_pdep_linearity_warnings,
    transition_state_upload_linearity_warnings,
)
from app.services.idempotency import IDEMPOTENCY_HEADER
from app.services.provenance_warnings import (
    collect_kinetics_content_warnings,
    collect_kinetics_provenance_warnings,
    collect_statmech_content_warnings,
    collect_statmech_provenance_warnings,
    collect_thermo_provenance_warnings,
    collect_transport_provenance_warnings,
    statmech_has_rotational_structure,
)
from app.services.statmech_resolution import (
    collect_frequency_scale_factor_software_mismatch_warnings,
)
from app.services.thermoml_cp_import import (
    ThermoMLDoiConflictError,
    import_thermoml_cp_upload,
)
from app.services.upload_reconciliation import (
    reconcile_species_entry,
    reconcile_species_entry_full,
    stationary_point_warnings,
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
    """What the reaction bundle wrote, named back to the depositor.

    ``extra="forbid"`` is load-bearing rather than tidy. This model is
    built as ``ComputedReactionUploadResult(**result_dict)`` from the
    workflow's return value, so under pydantic's default ``extra="ignore"``
    a key the workflow computes but the model does not declare is dropped
    in silence — with a 201 that looks complete. That is exactly how
    ``statmech_ids`` and ``atom_map_id`` went missing: both were collected
    by the workflow, both were discarded here, and no test could fail
    because the seam had no failure mode. Forbidding extras turns the next
    such omission into an error at construction time.
    """

    model_config = ConfigDict(extra="forbid")

    type: str = "computed_reaction"
    submission_id: int | None = None
    reaction_entry_id: int
    reaction_id: int
    transition_state_entry_id: int | None = None
    #: The ``reaction_atom_map`` row this bundle wrote, if it carried an
    #: ``atom_map`` block. Previously computed and dropped, which is why
    #: every atom-map test in the tree reads it back through
    #: ``/reactions/{id}/full`` instead of taking it from the 201.
    atom_map_id: int | None = None
    kinetics_ids: list[int]
    thermo_ids: list[int]
    #: One id per ``statmech`` row written for a species in this bundle.
    #: A bundle depositing all three product types now gets all three
    #: named; before, statmech had to be found by querying back through
    #: ``species_entry_ids``.
    statmech_ids: list[int] = Field(default_factory=list)
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
    # ``reconcile_species_entry_full`` already runs the stationary-point
    # check as part of its Layer-1 pass, so this route deliberately does
    # *not* also call ``request.stationary_point_findings()`` — that would
    # emit each finding twice under two different ``field`` paths. The
    # other species routes do call it, because their
    # ``reconcile_species_entry`` call has no frequency evidence to work
    # from.
    warnings = reconcile_species_entry_full(
        request.species_entry,
        primary_calc=request.calculation,
        additional_calcs=request.additional_calculations,
        statmech=request.statmech,
        reference_xyz_text=request.geometry.xyz_text,
    )
    warnings.extend(collect_software_release_version_warnings(request))
    sub = open_upload_submission(
        session,
        created_by=current_user.id,
        kind=SubmissionKind.conformer,
        rights=request.rights,
    )
    outcome = persist_conformer_upload(
        session, request, created_by=current_user.id, review_policy=sub.policy
    )
    warnings.extend(outcome.warnings)
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
    warnings.extend(collect_software_release_version_warnings(request))
    sub = open_upload_submission(
        session,
        created_by=current_user.id,
        kind=SubmissionKind.reaction,
        rights=request.rights,
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
    warnings.extend(collect_kinetics_content_warnings(request))
    warnings.extend(collect_software_release_version_warnings(request))
    sub = open_upload_submission(
        session,
        created_by=current_user.id,
        kind=SubmissionKind.kinetics,
        rights=request.rights,
    )
    kinetics = persist_kinetics_upload(
        session,
        request,
        created_by=current_user.id,
        review_policy=sub.policy,
        warnings=warnings,
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
    warnings = collect_software_release_version_warnings(request)
    sub = open_upload_submission(
        session,
        created_by=current_user.id,
        kind=SubmissionKind.network,
        rights=request.rights,
    )
    network = persist_network_upload(
        session,
        request,
        created_by=current_user.id,
        review_policy=sub.policy,
        warnings_out=warnings,
    )
    result = NetworkUploadResult(
        id=network.id, submission_id=sub.submission_id, warnings=warnings
    )
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
        session,
        created_by=current_user.id,
        kind=SubmissionKind.network_pdep,
        rights=request.rights,
    )
    pdep_warnings: list[UploadWarning] = stationary_point_warnings(
        request.stationary_point_findings()
    )
    pdep_warnings.extend(network_pdep_linearity_warnings(request))
    pdep_warnings.extend(collect_software_release_version_warnings(request))
    network = persist_network_pdep_upload(
        session,
        request,
        created_by=current_user.id,
        review_policy=sub.policy,
        warnings=pdep_warnings,
    )
    solve_id = network.solves[0].id if network.solves else None
    result = NetworkPDepUploadResult(
        id=network.id,
        submission_id=sub.submission_id,
        solve_id=solve_id,
        warnings=pdep_warnings,
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
    warnings.extend(stationary_point_warnings(request.stationary_point_findings()))
    warnings.extend(inline_calculation_linearity_warnings(request.calculations))
    warnings.extend(collect_statmech_provenance_warnings(request))
    warnings.extend(collect_software_release_version_warnings(request))
    warnings.extend(
        collect_statmech_content_warnings(
            scientific_origin=request.scientific_origin,
            source_calculation_roles={
                item.role.value for item in request.source_calculations
            },
            has_rotational_structure=statmech_has_rotational_structure(request),
        )
    )
    sub = open_upload_submission(
        session,
        created_by=current_user.id,
        kind=SubmissionKind.statmech,
        rights=request.rights,
    )
    statmech = persist_statmech_upload(
        session,
        request,
        created_by=current_user.id,
        review_policy=sub.policy,
        warnings_out=warnings,
    )
    warnings.extend(
        collect_frequency_scale_factor_software_mismatch_warnings(
            session, [statmech.id]
        )
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
    warnings.extend(stationary_point_warnings(request.stationary_point_findings()))
    warnings.extend(inline_calculation_linearity_warnings(request.calculations))
    warnings.extend(collect_thermo_provenance_warnings(request))
    warnings.extend(collect_software_release_version_warnings(request))
    sub = open_upload_submission(
        session,
        created_by=current_user.id,
        kind=SubmissionKind.thermo,
        rights=request.rights,
    )
    thermo = persist_thermo_upload(
        session,
        request,
        created_by=current_user.id,
        review_policy=sub.policy,
        warnings_out=warnings,
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
    warnings.extend(stationary_point_warnings(request.stationary_point_findings()))
    warnings.extend(transition_state_upload_linearity_warnings(request))
    warnings.extend(collect_software_release_version_warnings(request))
    sub = open_upload_submission(
        session,
        created_by=current_user.id,
        kind=SubmissionKind.transition_state,
        rights=request.rights,
    )
    ts_entry = persist_transition_state_upload(
        session,
        request,
        created_by=current_user.id,
        review_policy=sub.policy,
        warnings=warnings,
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
    warnings.extend(stationary_point_warnings(request.stationary_point_findings()))
    warnings.extend(inline_calculation_linearity_warnings(request.calculations))
    warnings.extend(collect_transport_provenance_warnings(request))
    warnings.extend(collect_software_release_version_warnings(request))
    sub = open_upload_submission(
        session,
        created_by=current_user.id,
        kind=SubmissionKind.transport,
        rights=request.rights,
    )
    transport = persist_transport_upload(
        session,
        request,
        created_by=current_user.id,
        review_policy=sub.policy,
        warnings_out=warnings,
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
    warnings.extend(stationary_point_warnings(request.stationary_point_findings()))
    # The sharper half of the completeness question, which needs the
    # geometry's coordinates and a collinearity tolerance and therefore
    # cannot live in the wire package alongside the ``3N - 6`` floor.
    warnings.extend(computed_species_linearity_warnings(request))
    warnings.extend(collect_software_release_version_warnings(request))
    sub = open_upload_submission(
        session,
        created_by=current_user.id,
        kind=SubmissionKind.computed_species,
        rights=request.rights,
    )
    outcome = persist_computed_species_upload(
        session, request, created_by=current_user.id, review_policy=sub.policy
    )
    # Single-point energy reconciliation on inline output-log artifacts.
    warnings = warnings + outcome.warnings
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
        session,
        created_by=current_user.id,
        kind=SubmissionKind.computed_reaction,
        rights=request.rights,
    )
    result_dict = persist_computed_reaction_upload(
        session, request, created_by=current_user.id, review_policy=sub.policy
    )
    # Stationary-point warnings are derived from the request, not the
    # persisted rows, so they are merged on top of whatever the workflow
    # reported rather than being produced inside it.
    result_dict["warnings"] = [
        *stationary_point_warnings(request.stationary_point_findings()),
        *computed_reaction_linearity_warnings(request),
        *collect_software_release_version_warnings(request),
        *result_dict.get("warnings", []),
    ]
    result = ComputedReactionUploadResult(
        **result_dict, submission_id=sub.submission_id
    )
    mark_upload_ingested(session, sub)
    idem.record(session, status_code=201, body=result.model_dump(mode="json"))
    return result


# ---------------------------------------------------------------------------
# ThermoML file upload (Phase C-E6)
# ---------------------------------------------------------------------------

#: Reuses the artifact-upload cap (``app.services.artifact_storage``)
#: rather than minting a second size policy for one more upload route.
MAX_THERMOML_UPLOAD_BYTES = MAX_ARTIFACT_BYTES


class ThermoMLUploadDisposition(BaseModel):
    """One mapped Cp(T) row's outcome. Never a database id -- see
    ``ThermoMLUploadResult``."""

    property_kind: str | None = None
    action: str
    identity_status: str
    species_entry_ref: str | None = None
    observation_ref: str | None = None
    warnings: list[str] = []


class ThermoMLUploadResult(BaseModel):
    """What ``POST /uploads/thermoml`` wrote, named back to the depositor
    entirely in public refs.

    Deliberately carries no database id anywhere, unlike the ``id``/
    ``submission_id`` fields on every sibling result above: this route
    was written after ``docs/specs/public_identifier_policy.md`` landed
    and after the C-E5 read route set the ``public_ref`` precedent for
    this exact table, so it follows that precedent rather than repeating
    the older routes' leak.
    """

    model_config = ConfigDict(extra="forbid")

    type: str = "thermoml_import"
    submission_ref: str | None = None
    doi: str | None = None
    schema_valid: bool
    payload_count: int
    would_insert_count: int
    inserted_count: int
    duplicate_count: int
    skipped_count: int
    resolved_identity_count: int
    unresolved_identity_count: int
    ambiguous_identity_count: int
    not_found_identity_count: int
    mapping_report: dict[str, Any] | None = None
    dispositions: list[ThermoMLUploadDisposition] = []
    warnings: list[str] = []


def _thermoml_species_entry_ref(
    session: Session, species_entry_id: int | None
) -> str | None:
    """Resolve a resolved-identity row's public ref for the response.

    The service's ``ObservationDisposition.species_entry_id`` is an
    internal int (kept for the CLI's operator-facing JSON summary,
    printed to a terminal the depositor does not see); this route never
    puts that int on the wire, only the ref it names.
    """
    if species_entry_id is None:
        return None
    return session.scalar(
        select(SpeciesEntry.public_ref).where(SpeciesEntry.id == species_entry_id)
    )


@router.post(
    "/thermoml",
    response_model=ThermoMLUploadResult,
    status_code=201,
)
@audit_sync_upload_failure(SubmissionKind.other)
def upload_thermoml(
    request: ThermoMLUploadRequest,
    session: Session = Depends(get_write_db),
    current_user: AppUser = Depends(get_current_user),
    idem: IdempotencyContext = Depends(idempotency_dependency),
    # Required, unlike every sibling route's optional Idempotency-Key
    # (``idempotency_dependency`` itself treats a missing header as "not
    # idempotent, proceed anyway" -- see that dependency's own
    # docstring). DR-0024 and this work package's brief both require an
    # idempotency key on every upload here; declaring the SAME header a
    # second time, this time required, makes FastAPI's ordinary
    # missing-required-field 422 fire before the route body runs -- the
    # existing validation mechanism every required field already uses,
    # not a new bespoke refusal. The two parameters read one header once;
    # neither shadows the other.
    _idempotency_key_required: str = Header(..., alias=IDEMPOTENCY_HEADER),
):
    """Accept one ThermoML XML document directly -- not from the NIST bulk
    archive that ``backend/scripts/thermoml_cp_import.py --archive``
    reads. Anyone authenticated may deposit one (Phase C-E6, "let anyone
    ingest a ThermoML file, not only the NIST archive").

    Transport mirrors ``ArtifactIn.content_base64`` (the only other
    inline-file-bytes schema in this codebase): JSON body,
    base64-encoded content, decoded and size-checked here before the
    pipeline runs at all -- the encoded-length pre-check
    (``MAX_ENCODED_ARTIFACT_LEN``) rejects an oversized payload before
    the decode allocation, the decoded-length check catches base64
    padding slack.

    Idempotency: unlike every sibling ``/uploads/*`` route (where the
    ``Idempotency-Key`` header is optional), this route requires it --
    DR-0024 and this work package's brief both call for every upload
    here to carry one. A request without the header never reaches this
    function's body at all: FastAPI's own required-header validation
    (see the ``_idempotency_key_required`` parameter above) answers with
    its ordinary 422 first.

    Rights: ``request.rights`` (``DepositRights``) is required on this
    schema -- unlike every sibling upload schema, where it is optional
    and absence "bites at release time, not at upload". This route's
    whole content is a third-party document the depositor is asserting
    the right to submit, with no fallback source terms to stand on
    instead (see ``ThermoMLUploadRequest.rights`` for the full
    reasoning), so omitting it is refused by ordinary Pydantic
    field-requiredness before this function's body runs.

    No preview: unlike the CLI (``--commit`` default-off), this route
    always commits, like every sibling ``/uploads/*`` route -- none of
    them preview a request before writing it (see
    ``ThermoMLUploadRequest``'s docstring for the one preview mechanism
    that does exist in this codebase, and why it does not apply here).
    """
    if (replay := idem.maybe_replay()) is not None:
        return replay

    if len(request.content_base64) > MAX_ENCODED_ARTIFACT_LEN:
        raise CodedValidationError(
            "thermoml_file_too_large",
            "ThermoML file exceeds the maximum upload size of "
            f"{MAX_THERMOML_UPLOAD_BYTES:,} bytes.",
            context={"max_bytes": MAX_THERMOML_UPLOAD_BYTES},
        )
    try:
        xml_bytes = base64.b64decode(request.content_base64, validate=True)
    except Exception as exc:
        raise CodedValidationError(
            "thermoml_invalid_base64",
            f"content_base64 is not valid base64: {exc}",
        ) from exc
    if len(xml_bytes) > MAX_THERMOML_UPLOAD_BYTES:
        raise CodedValidationError(
            "thermoml_file_too_large",
            "ThermoML file exceeds the maximum upload size of "
            f"{MAX_THERMOML_UPLOAD_BYTES:,} bytes "
            f"({len(xml_bytes):,} bytes given).",
            context={
                "max_bytes": MAX_THERMOML_UPLOAD_BYTES,
                "given_bytes": len(xml_bytes),
            },
        )

    placeholder_json = b"{}"
    article = ArticleBytes(
        xml=xml_bytes,
        json_bytes=placeholder_json,
        xml_sha256=hashlib.sha256(xml_bytes).hexdigest(),
        json_sha256=hashlib.sha256(placeholder_json).hexdigest(),
        member_paths=(f"upload:{request.filename}", ""),
    )

    try:
        result = import_thermoml_cp_upload(
            session,
            article=article,
            doi=request.doi,
            actor=current_user,
            rights=request.rights,
            commit=True,
        )
    except ThermoMLDoiConflictError as exc:
        raise CodedValidationError("thermoml_doi_conflict", str(exc)) from exc

    if not result.schema_valid:
        raise CodedValidationError(
            "thermoml_schema_invalid",
            "ThermoML file failed XSD validation: "
            + "; ".join(result.warnings),
        )

    if result.payload_count == 0:
        report = result.mapping_report or {}
        reasons = sorted(
            {
                str(entry.get("reason"))
                for entry in (*report.get("unsupported", []), *report.get("rejected", []))
                if entry.get("reason")
            }
        )
        raise CodedValidationError(
            "thermoml_no_supported_content",
            "No mappable ideal-gas or real-gas Cp(T) content was found in "
            "this file.",
            context={"reasons": reasons},
        )

    dispositions = [
        ThermoMLUploadDisposition(
            property_kind=d.property_kind,
            action=d.action,
            identity_status=d.identity_status,
            species_entry_ref=_thermoml_species_entry_ref(
                session, d.species_entry_id
            ),
            observation_ref=d.observation_ref,
            warnings=list(d.warnings),
        )
        for d in result.dispositions
    ]

    response_body = ThermoMLUploadResult(
        submission_ref=result.submission_ref,
        doi=result.doi or None,
        schema_valid=result.schema_valid,
        payload_count=result.payload_count,
        would_insert_count=result.would_insert_count,
        inserted_count=result.inserted_count,
        duplicate_count=result.duplicate_count,
        skipped_count=result.skipped_count,
        resolved_identity_count=result.resolved_identity_count,
        unresolved_identity_count=result.unresolved_identity_count,
        ambiguous_identity_count=result.ambiguous_identity_count,
        not_found_identity_count=result.not_found_identity_count,
        mapping_report=result.mapping_report,
        dispositions=dispositions,
        warnings=list(result.warnings),
    )
    idem.record(session, status_code=201, body=response_body.model_dump(mode="json"))
    return response_body
