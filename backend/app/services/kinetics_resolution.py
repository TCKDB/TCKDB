from __future__ import annotations

from sqlalchemy.orm import Session

from app.chemistry.units import convert_ea_to_kj_mol
from app.db.models.calculation import Calculation
from app.db.models.common import CalculationType, KineticsCalculationRole
from app.db.models.kinetics import Kinetics
from app.schemas.entities.kinetics import KineticsCreate
from app.schemas.workflows.kinetics_upload import KineticsUploadRequest
from app.services.calculation_resolution import resolve_workflow_tool_release_ref
from app.services.literature_resolution import resolve_or_create_literature
from app.services.software_resolution import resolve_software_release_ref

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
_KINETICS_ROLE_COMPATIBILITY: dict[
    KineticsCalculationRole, dict[str, object]
] = {
    KineticsCalculationRole.reactant_energy: {
        "calculation_types": frozenset({CalculationType.sp}),
        "owner": "species_entry",
    },
    KineticsCalculationRole.product_energy: {
        "calculation_types": frozenset({CalculationType.sp}),
        "owner": "species_entry",
    },
    KineticsCalculationRole.ts_energy: {
        "calculation_types": frozenset({CalculationType.sp}),
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
        # Pick the single expected type label (all v0 strict roles bind one)
        expected_type = next(iter(allowed_types)).value
        raise ValueError(
            f"kinetics source role {role.value} requires a "
            f"{expected_owner_str} {expected_type} calculation; got "
            f"{actual_owner} {calculation.type.value} calculation"
            f"{key_part}."
        )

    if required_owner == "species_entry" and calculation.species_entry_id is None:
        expected_type = (
            next(iter(allowed_types)).value if allowed_types is not None else "any"
        )
        raise ValueError(
            f"kinetics source role {role.value} requires a species-owned "
            f"{expected_type} calculation; got {actual_owner} "
            f"{calculation.type.value} calculation{key_part}."
        )
    if (
        required_owner == "transition_state_entry"
        and calculation.transition_state_entry_id is None
    ):
        expected_type = (
            next(iter(allowed_types)).value if allowed_types is not None else "any"
        )
        raise ValueError(
            f"kinetics source role {role.value} requires a "
            f"transition-state-owned {expected_type} calculation; got "
            f"{actual_owner} {calculation.type.value} calculation"
            f"{key_part}."
        )


def resolve_kinetics_upload(
    session: Session,
    request: KineticsUploadRequest,
    *,
    reaction_entry_id: int,
) -> KineticsCreate:
    """Resolve workflow-facing kinetics upload data into an internal create schema.

    :param session: Active SQLAlchemy session.
    :param request: Workflow-facing kinetics upload payload.
    :param reaction_entry_id: Resolved reaction-entry id from backend workflow logic.
    :returns: Internal ``KineticsCreate`` payload with resolved foreign-key ids.
    """

    literature = (
        resolve_or_create_literature(session, request.literature)
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

    return KineticsCreate(
        reaction_entry_id=reaction_entry_id,
        scientific_origin=request.scientific_origin,
        model_kind=request.model_kind,
        is_third_body=request.is_third_body,
        literature_id=literature.id if literature is not None else None,
        software_release_id=(
            software_release.id if software_release is not None else None
        ),
        workflow_tool_release_id=(
            workflow_tool_release.id if workflow_tool_release is not None else None
        ),
        a=request.a,
        a_units=request.a_units,
        n=request.n,
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
    """

    kinetics = Kinetics(
        reaction_entry_id=kinetics_create.reaction_entry_id,
        scientific_origin=kinetics_create.scientific_origin,
        model_kind=kinetics_create.model_kind,
        is_third_body=kinetics_create.is_third_body,
        literature_id=kinetics_create.literature_id,
        workflow_tool_release_id=kinetics_create.workflow_tool_release_id,
        software_release_id=kinetics_create.software_release_id,
        a=kinetics_create.a,
        a_units=kinetics_create.a_units,
        n=kinetics_create.n,
        ea_kj_mol=kinetics_create.ea_kj_mol,
        a_uncertainty=kinetics_create.a_uncertainty,
        a_uncertainty_kind=kinetics_create.a_uncertainty_kind,
        n_uncertainty=kinetics_create.n_uncertainty,
        ea_uncertainty_kj_mol=kinetics_create.ea_uncertainty_kj_mol,
        tmin_k=kinetics_create.tmin_k,
        tmax_k=kinetics_create.tmax_k,
        degeneracy=kinetics_create.degeneracy,
        tunneling_model=kinetics_create.tunneling_model,
        pressure_context=kinetics_create.pressure_context,
        pressure_bar=kinetics_create.pressure_bar,
        note=kinetics_create.note,
        created_by=created_by,
    )
    session.add(kinetics)
    session.flush()
    return kinetics
