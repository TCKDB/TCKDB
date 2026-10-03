"""The warnings an upload request earns before its workflow runs.

Every ``POST /uploads/<kind>`` route builds its response warnings in two
halves: the ones derivable from the *request alone* (identity reconciliation,
stationary-point findings, linearity, provenance, reference shape), and the
ones the persistence workflow appends through ``warnings_out`` while it
resolves rows. Those routes used to spell the first half inline, so any other
path that persists the same request -- ``/bundles/submit``, the background job
worker -- could not reproduce it and silently dropped it (#647).

This module is the one place the first half is assembled, in the order the
direct routes always produced it. The routes, the bundle importer and the
worker all call it, so a depositor sees the same warnings, in the same order,
however the record arrived.

A function here is pure: it reads the request and returns a fresh list that the
caller owns and may extend. It never touches the database.
"""

from __future__ import annotations

from app.schemas.fragments.refs import collect_ref_warnings
from app.schemas.upload_warning import UploadWarning
from app.schemas.workflows.computed_reaction_upload import ComputedReactionUploadRequest
from app.schemas.workflows.conformer_upload import ConformerUploadRequest
from app.schemas.workflows.kinetics_upload import KineticsUploadRequest
from app.schemas.workflows.network_pdep_upload import NetworkPDepUploadRequest
from app.schemas.workflows.network_upload import NetworkUploadRequest
from app.schemas.workflows.reaction_upload import ReactionUploadRequest
from app.schemas.workflows.thermo_upload import ThermoUploadRequest
from app.schemas.workflows.transition_state_upload import TransitionStateUploadRequest
from app.schemas.workflows.transport_upload import TransportUploadRequest
from app.services.frequency_geometry_linearity import (
    computed_reaction_linearity_warnings,
    inline_calculation_linearity_warnings,
    network_pdep_linearity_warnings,
    transition_state_upload_linearity_warnings,
)
from app.services.provenance_warnings import (
    collect_kinetics_content_warnings,
    collect_kinetics_provenance_warnings,
    collect_thermo_provenance_warnings,
    collect_transport_provenance_warnings,
)
from app.services.upload_reconciliation import (
    reconcile_species_entry,
    reconcile_species_entry_full,
    stationary_point_warnings,
)


def _prefixed_reaction_warnings(reaction, *, prefix: str = "reaction") -> list[UploadWarning]:
    """Reconcile every reactant and product species entry of ``reaction``."""
    warnings: list[UploadWarning] = []
    for i, p in enumerate(reaction.reactants):
        for w in reconcile_species_entry(p.species_entry):
            warnings.append(w.model_copy(update={"field": f"{prefix}.reactants[{i}].{w.field}"}))
    for i, p in enumerate(reaction.products):
        for w in reconcile_species_entry(p.species_entry):
            warnings.append(w.model_copy(update={"field": f"{prefix}.products[{i}].{w.field}"}))
    return warnings


def conformer_request_warnings(request: ConformerUploadRequest) -> list[UploadWarning]:
    # ``reconcile_species_entry_full`` already runs the stationary-point check
    # as part of its Layer-1 pass, so this deliberately does not also call
    # ``request.stationary_point_findings()`` -- that would emit each finding
    # twice under two different ``field`` paths.
    warnings = reconcile_species_entry_full(
        request.species_entry,
        primary_calc=request.calculation,
        additional_calcs=request.additional_calculations,
        statmech=request.statmech,
        reference_xyz_text=request.geometry.xyz_text,
    )
    warnings.extend(collect_ref_warnings(request))
    return warnings


def reaction_request_warnings(request: ReactionUploadRequest) -> list[UploadWarning]:
    warnings: list[UploadWarning] = []
    for i, p in enumerate(request.reactants):
        if p.species_entry is not None:
            for w in reconcile_species_entry(p.species_entry):
                warnings.append(w.model_copy(update={"field": f"reactants[{i}].{w.field}"}))
    for i, p in enumerate(request.products):
        if p.species_entry is not None:
            for w in reconcile_species_entry(p.species_entry):
                warnings.append(w.model_copy(update={"field": f"products[{i}].{w.field}"}))
    warnings.extend(collect_ref_warnings(request))
    return warnings


def kinetics_request_warnings(request: KineticsUploadRequest) -> list[UploadWarning]:
    warnings = _prefixed_reaction_warnings(request.reaction)
    warnings.extend(collect_kinetics_provenance_warnings(request))
    warnings.extend(collect_kinetics_content_warnings(request))
    warnings.extend(collect_ref_warnings(request))
    return warnings


def network_request_warnings(request: NetworkUploadRequest) -> list[UploadWarning]:
    return list(collect_ref_warnings(request))


def network_pdep_request_warnings(request: NetworkPDepUploadRequest) -> list[UploadWarning]:
    warnings: list[UploadWarning] = stationary_point_warnings(request.stationary_point_findings())
    warnings.extend(network_pdep_linearity_warnings(request))
    warnings.extend(collect_ref_warnings(request))
    return warnings


def thermo_request_warnings(request: ThermoUploadRequest) -> list[UploadWarning]:
    warnings = reconcile_species_entry(request.species_entry)
    warnings.extend(stationary_point_warnings(request.stationary_point_findings()))
    warnings.extend(inline_calculation_linearity_warnings(request.calculations))
    warnings.extend(collect_thermo_provenance_warnings(request))
    warnings.extend(collect_ref_warnings(request))
    return warnings


def transition_state_request_warnings(request: TransitionStateUploadRequest) -> list[UploadWarning]:
    warnings = _prefixed_reaction_warnings(request.reaction)
    warnings.extend(stationary_point_warnings(request.stationary_point_findings()))
    warnings.extend(transition_state_upload_linearity_warnings(request))
    warnings.extend(collect_ref_warnings(request))
    return warnings


def transport_request_warnings(request: TransportUploadRequest) -> list[UploadWarning]:
    warnings = reconcile_species_entry(request.species_entry)
    warnings.extend(stationary_point_warnings(request.stationary_point_findings()))
    warnings.extend(inline_calculation_linearity_warnings(request.calculations))
    warnings.extend(collect_transport_provenance_warnings(request))
    warnings.extend(collect_ref_warnings(request))
    return warnings


def computed_reaction_request_warnings(request: ComputedReactionUploadRequest) -> list[UploadWarning]:
    # Derived from the request, not the persisted rows, so a caller merges
    # these *ahead of* whatever the workflow reported.
    return [
        *stationary_point_warnings(request.stationary_point_findings()),
        *computed_reaction_linearity_warnings(request),
        *collect_ref_warnings(request),
    ]
