"""Transport upload workflow orchestrator."""

from __future__ import annotations

import logging
from collections.abc import Mapping

from sqlalchemy.orm import Session
from tckdb_schemas.fragments.refs import SoftwareReleaseRef, WorkflowToolReleaseRef

from app.db.models.calculation import Calculation
from app.db.models.common import SubmissionRecordType, TransportCalculationRole
from app.db.models.transport import Transport
from app.schemas.entities.transport import TransportSourceCalculationCreate
from app.schemas.upload_warning import UploadWarning
from app.schemas.workflows.computed_species_upload import TransportInBundle
from app.schemas.workflows.transport_upload import (
    TransportUploadPayload,
    TransportUploadRequest,
)
from app.services.calculation_ownership import (
    W_TRANSPORT_SOURCE_CALCULATION_OWNER_MISMATCH,
    assert_calculation_owned_by,
)
from app.services.calculation_resolution import (
    resolve_and_persist_calculation_with_results,
)
from app.services.composite_input_resolution import finalize_composite_inputs
from app.services.composite_result_resolution import collect_named_composite_deposit_warnings
from app.services.local_key_resolution import resolve_calculation_key
from app.services.record_review import (
    RecordRef,
    ReviewPolicy,
    apply_review_policy,
)
from app.services.species_resolution import resolve_species_entry
from app.services.transport_resolution import resolve_and_create_transport

logger = logging.getLogger(__name__)


def persist_transport_upload(
    session: Session,
    request: TransportUploadRequest,
    *,
    created_by: int | None = None,
    review_policy: ReviewPolicy | None = ReviewPolicy(),
    warnings_out: list[UploadWarning] | None = None,
) -> Transport:
    """Persist a complete standalone transport upload workflow.

    Resolves the species entry, persists any inline supporting
    calculations, resolves provenance references, and creates a single
    ``Transport`` row with attached ``transport_source_calculation``
    links via the shared :func:`resolve_and_create_transport` service
    helper.

    Transport is append-only: multiple uploads against the same species
    entry produce independent rows.

    :param session: Active SQLAlchemy session.
    :param request: Workflow-facing transport upload payload.
    :param created_by: Optional application user id for newly created rows.
    :returns: Newly created ``Transport`` row.
    :raises ValueError: If a resolved supporting calculation does not
        belong to the transport target's species entry.
    """
    species_entry = resolve_species_entry(
        session, request.species_entry, created_by=created_by
    )

    # Persist inline supporting calculations scoped to the target species
    # entry. Owner-consistency is enforced by construction but also
    # double-checked below to guard any future path that reuses existing
    # calculations.
    calculations_by_key: dict[str, Calculation] = {}
    for calc_in in request.calculations:
        calc_row = resolve_and_persist_calculation_with_results(
            session,
            calc_in.calculation,
            species_entry_id=species_entry.id,
            created_by=created_by,
        )
        assert_calculation_owned_by(
            calc_row,
            code=W_TRANSPORT_SOURCE_CALCULATION_OWNER_MISMATCH,
            target="transport",
            context=f"transport calculation '{calc_in.key}'",
            species_entry_id=species_entry.id,
        )
        calculations_by_key[calc_in.key] = calc_row

    # An assembled composite's inputs are written, and its total checked, now
    # that every inline calculation exists and the key namespace is complete
    # (ADR 0021, P5). Always run: the write is not optional when the caller
    # collects no warnings.
    composite_input_warnings = finalize_composite_inputs(
        session,
        [calc.id for calc in calculations_by_key.values()],
        calculations_by_key=calculations_by_key,
    )
    if warnings_out is not None:
        warnings_out.extend(composite_input_warnings)

    # An inline opt or sp at a named composite method's level is the same
    # misshapen deposit the bundle routes warn about (ADR 0021, decision 7).
    if warnings_out is not None:
        warnings_out.extend(
            collect_named_composite_deposit_warnings(
                session, [calc.id for calc in calculations_by_key.values()]
            )
        )

    resolved_source_calcs = [
        TransportSourceCalculationCreate(
            calculation_id=resolve_calculation_key(
                sc.calculation_key,
                calculations_by_key,
                field=f"source_calculations[{index}].calculation_key",
            ).id,
            role=sc.role,
        )
        for index, sc in enumerate(request.source_calculations)
    ]

    transport = resolve_and_create_transport(
        session,
        request,
        species_entry_id=species_entry.id,
        source_calculations=resolved_source_calcs,
        created_by=created_by,
        warnings_out=warnings_out,
    )

    session.flush()

    targets = [
        RecordRef(SubmissionRecordType.transport, transport.id),
        RecordRef(SubmissionRecordType.species_entry, species_entry.id),
    ]
    targets.extend(
        RecordRef(SubmissionRecordType.calculation, c.id)
        for c in calculations_by_key.values()
    )
    apply_review_policy(
        session, targets=targets, policy=review_policy, created_by=created_by
    )

    return transport


def persist_bundle_transport(
    session: Session,
    transport_in: TransportInBundle,
    *,
    species_entry_id: int,
    calculations_by_key: Mapping[str, Calculation],
    default_software_release: SoftwareReleaseRef | None = None,
    default_workflow_tool_release: WorkflowToolReleaseRef | None = None,
    created_by: int | None = None,
    warnings_out: list[UploadWarning] | None = None,
    field_prefix: str = "transport",
) -> Transport:
    """Persist the transport block of a computed-species or computed-reaction bundle.

    The row is made by the same :func:`resolve_and_create_transport` the
    standalone route, the conformer route and the PDep route use, so
    provenance resolution, the append-only rule and the source-calculation
    links are one implementation. What this adds is what a bundle has and the
    standalone request does not: source calculations named by the bundle's
    local keys, and bundle-level provenance defaults.

    Provenance follows thermo's rule: the block's own ``software_release`` /
    ``workflow_tool_release`` win, and a default fills in only where the block
    names none.

    :param calculations_by_key: The bundle's calculation-key namespace, already
        populated with every persisted calculation.
    :param species_entry_id: The species entry this transport belongs to;
        every source calculation must belong to it too.
    :param field_prefix: Path naming the block in the depositor's payload, for
        refusal messages, e.g. ``"species['ch4'].transport"``.
    :raises CodedValueError: a source key names no declared calculation, or
        names one owned by another species entry.
    """
    resolved_sources = []
    for index, sc in enumerate(transport_in.source_calculations):
        field = f"{field_prefix}.source_calculations[{index}].calculation_key"
        calc_row = resolve_calculation_key(
            sc.calculation_key, calculations_by_key, field=field
        )
        assert_calculation_owned_by(
            calc_row,
            code=W_TRANSPORT_SOURCE_CALCULATION_OWNER_MISMATCH,
            target="transport",
            context=f"{field}='{sc.calculation_key}'",
            species_entry_id=species_entry_id,
        )
        resolved_sources.append(
            TransportSourceCalculationCreate(
                calculation_id=calc_row.id,
                # Wire enum -> backend enum by value; the two are kept in
                # lockstep by the enum drift test.
                role=TransportCalculationRole(sc.role.value),
            )
        )

    payload = TransportUploadPayload(
        scientific_origin=transport_in.scientific_origin,
        literature=transport_in.literature,
        software_release=(
            transport_in.software_release or default_software_release
        ),
        workflow_tool_release=(
            transport_in.workflow_tool_release or default_workflow_tool_release
        ),
        sigma_angstrom=transport_in.sigma_angstrom,
        epsilon_over_k_k=transport_in.epsilon_over_k_k,
        dipole_debye=transport_in.dipole_debye,
        polarizability_angstrom3=transport_in.polarizability_angstrom3,
        rotational_relaxation=transport_in.rotational_relaxation,
        note=transport_in.note,
    )
    transport = resolve_and_create_transport(
        session,
        payload,
        species_entry_id=species_entry_id,
        source_calculations=resolved_sources,
        created_by=created_by,
        warnings_out=warnings_out,
    )
    session.flush()
    return transport
