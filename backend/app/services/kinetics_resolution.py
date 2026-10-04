from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.chemistry.units import convert_ea_to_kj_mol
from app.db.models.calculation import Calculation
from app.db.models.common import CalculationType, KineticsCalculationRole
from app.db.models.kinetics import Kinetics
from app.db.models.network_pdep import NetworkKinetics
from app.db.models.reaction import ReactionEntry
from app.schemas.entities.kinetics import KineticsCreate
from app.schemas.upload_warning import UploadWarning
from app.schemas.workflows.kinetics_upload import KineticsUploadRequest
from app.services.calculation_resolution import resolve_workflow_tool_release_ref
from app.services.kinetics_declaration_resolution import (
    assert_kinetics_declaration_columns,
    resolve_kinetics_declarations,
)
from app.services.literature_resolution import resolve_or_create_literature
from app.services.software_resolution import resolve_software_release_ref
from app.services.upload_reference import (
    W_UNKNOWN_NETWORK_KINETICS_REF,
    unknown_reference,
)

# ---------------------------------------------------------------------------
# Kinetics source-calculation role/type/owner compatibility
# ---------------------------------------------------------------------------
#
# Strict scientific roles bind a kinetics source link to a specific
# calculation type and owner kind. The loose roles (master_equation,
# fit_source) are intentionally unrestricted in v0 so workflow-tool-
# specific analysis/fitting calculations can be referenced without us
# pre-committing to type semantics that have not yet been standardized.
#
# Owner sentinel values:
#   "species_entry"          — calculation.species_entry_id must be set
#   "transition_state_entry" — calculation.transition_state_entry_id must be set
#   None                     — owner is not constrained for this role
#
# v0 interpretation notes:
#   * ``freq`` means the TS frequency calculation supporting the kinetics
#     fit. Reactant/product frequency provenance belongs in
#     thermo_source_calculation / statmech provenance, not here.
#   * ``reactant_energy`` and ``product_energy`` require type=sp because
#     "energy source" is the high-level single-point role, not opt's
#     incidentally-reported converged energy. Use ``fit_source`` if a
#     workflow truly needs to reference a non-sp calculation as the
#     supporting energy source.
#
#: The calculation types a kinetics *energy* role accepts: a single point, or a
#: program-run composite energy (ADR 0021; composite outranks sp in every
#: energy-level rule, ``app.services.calculation_levels``). Listed in the order
#: a refusal names them.
_ENERGY_CALCULATION_TYPES: frozenset[CalculationType] = frozenset(
    {CalculationType.sp, CalculationType.composite}
)

#: The order an accepted-types phrase names types in, so a refusal about a role
#: that accepts several reads the same on every run (a frozenset has no order).
_TYPE_PHRASE_ORDER: tuple[CalculationType, ...] = (
    CalculationType.sp,
    CalculationType.composite,
    CalculationType.freq,
    CalculationType.irc,
)


def _accepted_type_phrase(allowed_types: object) -> str:
    """``sp``, or ``sp or composite``: the accepted types, for a refusal message."""
    if not isinstance(allowed_types, frozenset):
        return "any"
    ordered = [t for t in _TYPE_PHRASE_ORDER if t in allowed_types]
    ordered += sorted((t for t in allowed_types if t not in _TYPE_PHRASE_ORDER), key=lambda t: t.value)
    return " or ".join(t.value for t in ordered)


_KINETICS_ROLE_COMPATIBILITY: dict[
    KineticsCalculationRole, dict[str, object]
] = {
    KineticsCalculationRole.reactant_energy: {
        "calculation_types": _ENERGY_CALCULATION_TYPES,
        "owner": "species_entry",
    },
    KineticsCalculationRole.product_energy: {
        "calculation_types": _ENERGY_CALCULATION_TYPES,
        "owner": "species_entry",
    },
    KineticsCalculationRole.ts_energy: {
        "calculation_types": _ENERGY_CALCULATION_TYPES,
        "owner": "transition_state_entry",
    },
    KineticsCalculationRole.freq: {
        "calculation_types": frozenset({CalculationType.freq}),
        "owner": "transition_state_entry",
    },
    KineticsCalculationRole.irc: {
        "calculation_types": frozenset({CalculationType.irc}),
        "owner": "transition_state_entry",
    },
    # master_equation and fit_source are intentionally unrestricted in v0.
    # They are broad provenance roles for analysis/fitting workflows whose
    # calculation type and owner semantics are not yet standardized. Strict
    # scientific roles above remain constrained.
    KineticsCalculationRole.master_equation: {
        "calculation_types": None,
        "owner": None,
    },
    KineticsCalculationRole.fit_source: {
        "calculation_types": None,
        "owner": None,
    },
}


def _describe_owner(calc: Calculation) -> str:
    if calc.species_entry_id is not None:
        return "species-owned"
    if calc.transition_state_entry_id is not None:
        return "transition-state-owned"
    return "unowned"


def assert_kinetics_source_role_compatible(
    *,
    calculation: Calculation,
    role: KineticsCalculationRole,
    calculation_key: str | None = None,
) -> None:
    """Validate a calculation is scientifically compatible with a role.

    Strict roles (reactant_energy, product_energy, ts_energy, freq, irc)
    pin both a calculation type and an owner kind (species vs TS).
    Loose roles (master_equation, fit_source) accept any calculation.

    Raises ``ValueError`` with a clear, producer-readable message that
    includes the offending key (when supplied), the role, the actual
    type, and the actual owner. Workflows surface this as 422 to the
    API.
    """
    spec = _KINETICS_ROLE_COMPATIBILITY[role]
    allowed_types = spec["calculation_types"]
    required_owner = spec["owner"]

    key_part = f" key='{calculation_key}'" if calculation_key else ""
    actual_owner = _describe_owner(calculation)

    if allowed_types is not None and calculation.type not in allowed_types:
        expected_owner_str = (
            "transition-state-owned"
            if required_owner == "transition_state_entry"
            else "species-owned"
            if required_owner == "species_entry"
            else "any-owner"
        )
        expected_type = _accepted_type_phrase(allowed_types)
        raise ValueError(
            f"kinetics source role {role.value} requires a "
            f"{expected_owner_str} {expected_type} calculation; got "
            f"{actual_owner} {calculation.type.value} calculation"
            f"{key_part}."
        )

    if required_owner == "species_entry" and calculation.species_entry_id is None:
        expected_type = _accepted_type_phrase(allowed_types)
        raise ValueError(
            f"kinetics source role {role.value} requires a species-owned "
            f"{expected_type} calculation; got {actual_owner} "
            f"{calculation.type.value} calculation{key_part}."
        )
    if (
        required_owner == "transition_state_entry"
        and calculation.transition_state_entry_id is None
    ):
        expected_type = _accepted_type_phrase(allowed_types)
        raise ValueError(
            f"kinetics source role {role.value} requires a "
            f"transition-state-owned {expected_type} calculation; got "
            f"{actual_owner} {calculation.type.value} calculation"
            f"{key_part}."
        )


def resolve_network_kinetics_ref(
    session: Session,
    ref: str | None,
    *,
    field: str = "network_kinetics_ref",
) -> int | None:
    """Resolve a pressure-dependent network counterpart's public ref to its id.

    Shared by the standalone kinetics route and the reaction bundle so both
    refuse an unknown ref the same way (``unknown_network_kinetics_ref``).

    :param ref: The public ref, or ``None`` (most rates have no counterpart).
    :param field: The payload path named in the refusal.
    """
    if ref is None:
        return None
    network_kinetics = session.scalar(
        select(NetworkKinetics).where(NetworkKinetics.public_ref == ref)
    )
    if network_kinetics is None:
        raise unknown_reference(
            code=W_UNKNOWN_NETWORK_KINETICS_REF,
            field=field,
            kind="network_kinetics",
            ref=ref,
            remedy=(
                "Deposit the pressure-dependent network solve this rate "
                "came out of first, or correct the ref."
            ),
        )
    return network_kinetics.id


def resolve_kinetics_upload(
    session: Session,
    request: KineticsUploadRequest,
    *,
    reaction_entry_id: int,
    warnings_out: list[UploadWarning] | None = None,
    created_by: int | None = None,
) -> KineticsCreate:
    """Resolve workflow-facing kinetics upload data into an internal create schema.

    :param session: Active SQLAlchemy session.
    :param request: Workflow-facing kinetics upload payload.
    :param reaction_entry_id: Resolved reaction-entry id from backend workflow logic.
    :param warnings_out: Optional sink for non-blocking warnings, including
        a depositor-supplied literature title/year that disagrees with the
        metadata fetched from a supplied DOI/ISBN.
    :param created_by: Application user id recorded on a determination this upload creates.
    :returns: Internal ``KineticsCreate`` payload with resolved foreign-key ids.
    """

    literature = (
        resolve_or_create_literature(
            session, request.literature, warnings_out=warnings_out
        )
        if request.literature is not None
        else None
    )
    software_release = (
        resolve_software_release_ref(session, request.software_release)
        if request.software_release is not None
        else None
    )
    workflow_tool_release = resolve_workflow_tool_release_ref(
        session,
        request.workflow_tool_release,
    )

    network_kinetics_id = resolve_network_kinetics_ref(
        session, request.network_kinetics_ref
    )

    reaction_entry = session.get(ReactionEntry, reaction_entry_id)
    assert reaction_entry is not None
    declarations = resolve_kinetics_declarations(
        session,
        request,
        reaction_entry=reaction_entry,
        literature_id=literature.id if literature is not None else None,
        workflow_tool_release_id=(
            workflow_tool_release.id if workflow_tool_release is not None else None
        ),
        network_kinetics_id=network_kinetics_id,
        ts_scope="entry",
        created_by=created_by,
    )

    return KineticsCreate(
        reaction_entry_id=reaction_entry_id,
        scientific_origin=request.scientific_origin,
        model_kind=request.model_kind,
        direction=request.direction,
        is_third_body=request.is_third_body,
        determination_id=declarations.determination_id,
        representation_role=declarations.representation_role,
        applicability_declaration=declarations.applicability_declaration,
        protocol_declaration=declarations.protocol_declaration,
        literature_id=literature.id if literature is not None else None,
        software_release_id=(
            software_release.id if software_release is not None else None
        ),
        workflow_tool_release_id=(
            workflow_tool_release.id if workflow_tool_release is not None else None
        ),
        network_kinetics_id=network_kinetics_id,
        a=request.a,
        a_units=request.a_units,
        n=request.n,
        t0_k=request.t0_k,
        ea_kj_mol=(
            convert_ea_to_kj_mol(request.reported_ea, request.reported_ea_units)
            if request.reported_ea is not None
            else None
        ),
        a_uncertainty=request.a_uncertainty,
        a_uncertainty_kind=request.a_uncertainty_kind,
        n_uncertainty=request.n_uncertainty,
        ea_uncertainty_kj_mol=(
            convert_ea_to_kj_mol(request.d_reported_ea, request.reported_ea_units)
            if request.d_reported_ea is not None
            else None
        ),
        tmin_k=request.tmin_k,
        tmax_k=request.tmax_k,
        degeneracy=request.degeneracy,
        degeneracy_convention=request.degeneracy_convention,
        tunneling_model=request.tunneling_model,
        pressure_context=request.pressure_context,
        pressure_bar=request.pressure_bar,
        note=request.note,
        source_calculations=[],
    )


def persist_kinetics(
    session: Session,
    kinetics_create: KineticsCreate,
    *,
    created_by: int | None = None,
) -> Kinetics:
    """Persist a resolved kinetics create payload.

    :param session: Active SQLAlchemy session.
    :param kinetics_create: Internal resolved kinetics payload.
    :param created_by: Optional application user id for the created row.
    :returns: Newly created ``Kinetics`` row.
    :raises CodedValueError: the resolved declaration columns contradict each other or the
        record (see :func:`assert_kinetics_declaration_columns`).
    """

    # The last stop before the row is written: a resolved payload built without
    # validation, or by a caller that never went through a workflow, is judged the
    # same as one a workflow produced.
    applicability, protocol = assert_kinetics_declaration_columns(
        session,
        reaction_entry_id=kinetics_create.reaction_entry_id,
        direction=kinetics_create.direction,
        scientific_origin=kinetics_create.scientific_origin,
        model_kind=kinetics_create.model_kind,
        is_third_body=kinetics_create.is_third_body,
        pressure_context=kinetics_create.pressure_context,
        pressure_bar=kinetics_create.pressure_bar,
        literature_id=kinetics_create.literature_id,
        workflow_tool_release_id=kinetics_create.workflow_tool_release_id,
        network_kinetics_id=kinetics_create.network_kinetics_id,
        determination_id=kinetics_create.determination_id,
        representation_role=kinetics_create.representation_role,
        applicability_declaration=kinetics_create.applicability_declaration,
        protocol_declaration=kinetics_create.protocol_declaration,
    )

    kinetics = Kinetics(
        reaction_entry_id=kinetics_create.reaction_entry_id,
        scientific_origin=kinetics_create.scientific_origin,
        model_kind=kinetics_create.model_kind,
        direction=kinetics_create.direction,
        is_third_body=kinetics_create.is_third_body,
        determination_id=kinetics_create.determination_id,
        representation_role=kinetics_create.representation_role,
        applicability_declaration=applicability,
        protocol_declaration=protocol,
        literature_id=kinetics_create.literature_id,
        workflow_tool_release_id=kinetics_create.workflow_tool_release_id,
        software_release_id=kinetics_create.software_release_id,
        network_kinetics_id=kinetics_create.network_kinetics_id,
        a=kinetics_create.a,
        a_units=kinetics_create.a_units,
        n=kinetics_create.n,
        t0_k=kinetics_create.t0_k,
        ea_kj_mol=kinetics_create.ea_kj_mol,
        a_uncertainty=kinetics_create.a_uncertainty,
        a_uncertainty_kind=kinetics_create.a_uncertainty_kind,
        n_uncertainty=kinetics_create.n_uncertainty,
        ea_uncertainty_kj_mol=kinetics_create.ea_uncertainty_kj_mol,
        tmin_k=kinetics_create.tmin_k,
        tmax_k=kinetics_create.tmax_k,
        degeneracy=kinetics_create.degeneracy,
        degeneracy_convention=kinetics_create.degeneracy_convention,
        tunneling_model=kinetics_create.tunneling_model,
        pressure_context=kinetics_create.pressure_context,
        pressure_bar=kinetics_create.pressure_bar,
        note=kinetics_create.note,
        created_by=created_by,
    )
    session.add(kinetics)
    session.flush()
    return kinetics
