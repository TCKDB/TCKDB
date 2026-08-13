"""Thermo upload workflow orchestrator."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.api.error_contract import CodedValueError
from app.api.errors import NotFoundError
from app.db.models.calculation import Calculation
from app.db.models.common import (
    CalculationType,
    SubmissionRecordType,
    ThermoCalculationRole,
)
from app.db.models.statmech import Statmech
from app.db.models.thermo import Thermo
from app.schemas.entities.thermo import ThermoSourceCalculationCreate
from app.schemas.workflows.thermo_upload import (
    ThermoSourceCalculationIn,
    ThermoUploadRequest,
)
from app.services.calculation_resolution import (
    resolve_and_persist_calculation_with_results,
)
from app.services.energy_correction_resolution import create_applied_energy_correction
from app.services.group_additivity_resolution import create_applied_group_additivity
from app.services.record_review import (
    RecordRef,
    ReviewPolicy,
    apply_review_policy,
)
from app.services.species_resolution import resolve_species_entry
from app.services.statmech_resolution import accepted_types_phrase
from app.services.thermo_resolution import persist_thermo, resolve_thermo_upload

logger = logging.getLogger(__name__)

#: A thermo source link declares a role the resolved calculation cannot
#: play, because its ``Calculation.type`` is a different kind of job.
#:
#: Deliberately *not* the statmech code
#: (``statmech_source_role_type_mismatch``): the two refusals are about
#: different subjects — which supporting calculations a partition function
#: was built from, versus which ones an enthalpy was — and a client that
#: repairs one may have nothing to say about the other. Same rule, same
#: shape of context, two codes a client can tell apart.
W_THERMO_SOURCE_ROLE_TYPE_MISMATCH = "thermo_source_role_type_mismatch"

#: Roles that name a specific kind of job, and the ``CalculationType``\ s
#: each accepts; the first entry is the canonical one the role is named
#: after. Mirrors ``_STATMECH_ROLE_TO_CALC_TYPES`` including the ``sp``
#: exception — there is no reason for the two products to disagree about
#: what a role means, and making them agree was the whole point of #126.
_THERMO_ROLE_TO_CALC_TYPES: dict[
    ThermoCalculationRole, tuple[CalculationType, ...]
] = {
    ThermoCalculationRole.opt: (CalculationType.opt,),
    ThermoCalculationRole.freq: (CalculationType.freq,),
    ThermoCalculationRole.sp: (CalculationType.sp, CalculationType.opt),
}


def _assert_calculation_owned_by(
    calculation: Calculation,
    *,
    species_entry_id: int,
    context: str,
) -> None:
    """Defensive owner-consistency check for a resolved source calculation.

    Supporting calculations attached to a thermo record must belong to the
    same species entry as the thermo target; otherwise the provenance link
    would be scientifically meaningless.

    :raises ValueError: if the calculation does not belong to
        ``species_entry_id``. The error detail names the field that was
        wrong but does not leak internal row identifiers (DR-0028 Req 2).
    """
    if calculation.species_entry_id != species_entry_id:
        raise ValueError(
            f"{context}: refers to a calculation owned by a different "
            f"species entry."
        )


def assert_thermo_role_matches_calculation_type(
    calculation: Calculation,
    *,
    role: ThermoCalculationRole,
    context: str,
) -> None:
    """Verify resolved ``Calculation.type`` is compatible with declared role.

    The single owner of this rule, for every route that links thermo to a
    supporting calculation: the standalone ``/uploads/thermo`` endpoint,
    the computed-species bundle and the computed-reaction bundle. It was
    written twice before — once here and once in ``computed_species`` —
    and a third copy went in with the reaction route's ``thermo.
    source_calculations``, at which point three copies of one rule could
    disagree about the same deposit.

    DR-0028 Requirement 1: a single typo in ``existing_calculation_id`` (or
    a mis-keyed inline calc) would otherwise silently link thermo to the
    wrong supporting calc within the same species. ``opt``/``freq``
    require exact ``CalculationType`` match. ``composite`` and ``imported``
    accept any type for v0 — those roles describe a scientific origin
    rather than a specific job type, and the existing test/usage corpus
    has no precedent constraining them.

    ``sp`` additionally accepts an ``opt``: a depositor who ran no separate
    single point still has the number, because the optimisation's final
    energy *is* the single-point value at the optimisation's own level of
    theory. The reverse is refused — an ``sp`` job optimised nothing, so it
    cannot serve the ``opt`` role — and the exception generalises no
    further. :func:`app.services.statmech_resolution.assert_statmech_role_compatible`
    carries the full argument and holds the identical map; the two products
    must not disagree about what a role means.

    :raises CodedValueError: if ``role`` is one of opt/freq/sp and the
        calculation's type is none of the ones that role accepts. The
        detail names the offending field but never a row id (DR-0028
        Req 2); the id goes to the log instead.
    """
    accepted = _THERMO_ROLE_TO_CALC_TYPES.get(role)
    if accepted is None or calculation.type in accepted:
        return
    logger.info(
        "thermo role/type mismatch at %s: calculation id=%s type=%s, role=%s",
        context,
        calculation.id,
        calculation.type.value,
        role.value,
    )
    raise CodedValueError(
        W_THERMO_SOURCE_ROLE_TYPE_MISMATCH,
        f"{context}: role='{role.value}' is incompatible with the "
        f"resolved calculation type '{calculation.type.value}'. A thermo "
        f"record must not claim evidence it does not have. Point the link "
        f"at a {accepted_types_phrase(accepted)} calculation, or declare "
        f"the role the named calculation actually played.",
        context={
            "field": context,
            "declared_role": role.value,
            "expected_calculation_type": accepted[0].value,
            "accepted_calculation_types": [
                calc_type.value for calc_type in accepted
            ],
            "actual_calculation_type": calculation.type.value,
        },
        message_prefix=False,
    )


def _resolve_source_calculation(
    session: Session,
    entry: ThermoSourceCalculationIn,
    *,
    index: int,
    calculations_by_key: dict[str, Calculation],
    species_entry_id: int,
) -> Calculation:
    """Resolve a ``source_calculations`` entry to a ``Calculation`` row.

    Schema validation guarantees exactly one of ``calculation_key`` or
    ``existing_calculation_id`` is set. Inline keys are looked up in the
    map of just-persisted calculations. Existing ids are loaded from the
    database; missing rows produce 404, cross-species or role/type
    mismatches produce 422 (DR-0028 Requirement 2).
    """
    field_path = f"source_calculations[{index}]"
    if entry.calculation_key is not None:
        calc_row = calculations_by_key[entry.calculation_key]
        context = f"{field_path}.calculation_key='{entry.calculation_key}'"
    else:
        calc_row = session.get(Calculation, entry.existing_calculation_id)
        if calc_row is None:
            raise NotFoundError(
                f"{field_path}.existing_calculation_id refers to a "
                f"calculation that does not exist."
            )
        context = f"{field_path}.existing_calculation_id"
        _assert_calculation_owned_by(
            calc_row,
            species_entry_id=species_entry_id,
            context=context,
        )

    assert_thermo_role_matches_calculation_type(
        calc_row,
        role=entry.role,
        context=context,
    )
    return calc_row


def _resolve_statmech_id(
    session: Session,
    existing_statmech_id: int | None,
    *,
    species_entry_id: int,
) -> int | None:
    """Resolve an optional ``existing_statmech_id`` to a statmech row id.

    Mirrors the ``existing_calculation_id`` handling for source
    calculations (DR-0028): a missing row produces 404, a row owned by a
    different species entry produces 422 (ValueError). The error detail
    names the field but does not leak internal identifiers.
    """
    if existing_statmech_id is None:
        return None
    statmech = session.get(Statmech, existing_statmech_id)
    if statmech is None:
        raise NotFoundError(
            "existing_statmech_id refers to a statmech record that does "
            "not exist."
        )
    if statmech.species_entry_id != species_entry_id:
        raise ValueError(
            "existing_statmech_id refers to a statmech record owned by a "
            "different species entry."
        )
    return statmech.id


def persist_thermo_upload(
    session: Session,
    request: ThermoUploadRequest,
    *,
    created_by: int | None = None,
    review_policy: ReviewPolicy | None = ReviewPolicy(),
) -> Thermo:
    """Persist a complete thermo upload workflow.

    Resolves the species entry, persists any inline supporting calculations,
    resolves provenance references, creates the thermo record with children
    (including ``thermo_source_calculation`` links), and processes applied
    energy corrections while resolving their ``source_calculation_key`` to
    a real calculation id rather than silently dropping it.

    :param session: Active SQLAlchemy session.
    :param request: Workflow-facing thermo upload payload.
    :param created_by: Optional application user id for newly created rows.
    :returns: Newly created ``Thermo`` row.
    :raises ValueError: If a resolved supporting calculation does not
        belong to the thermo target's species entry, or if an applied
        correction's ``source_calculation_key`` does not resolve.
    """
    species_entry = resolve_species_entry(
        session, request.species_entry, created_by=created_by
    )

    # Persist inline supporting calculations, keyed by their local keys.
    # Each is automatically scoped to the thermo target's species entry, so
    # owner-consistency is enforced by construction. The explicit check
    # below also guards any future path that reuses existing calculations.
    calculations_by_key: dict[str, Calculation] = {}
    for calc_in in request.calculations:
        calc_row = resolve_and_persist_calculation_with_results(
            session,
            calc_in.calculation,
            species_entry_id=species_entry.id,
            created_by=created_by,
        )
        _assert_calculation_owned_by(
            calc_row,
            species_entry_id=species_entry.id,
            context=f"thermo calculation '{calc_in.key}'",
        )
        calculations_by_key[calc_in.key] = calc_row

    # Resolve source_calculation links. Each entry uses either a local
    # calculation_key (inline path) or an existing_calculation_id (DR-0028
    # path that lets ARC link thermo to calcs already uploaded by the
    # conformer step). Both paths run owner-consistency and role/type
    # compatibility checks before becoming a thermo_source_calculation row.
    resolved_source_calcs: list[ThermoSourceCalculationCreate] = []
    for index, sc in enumerate(request.source_calculations):
        calc_row = _resolve_source_calculation(
            session,
            sc,
            index=index,
            calculations_by_key=calculations_by_key,
            species_entry_id=species_entry.id,
        )
        resolved_source_calcs.append(
            ThermoSourceCalculationCreate(
                calculation_id=calc_row.id,
                role=sc.role,
            )
        )

    # Resolve the optional statmech basis for this (computed) thermo. The
    # upload carries it as an existing-row reference (programmatic path,
    # like existing_calculation_id); the owner-consistency check keeps a
    # thermo from citing another species' statmech record.
    resolved_statmech_id = _resolve_statmech_id(
        session,
        request.existing_statmech_id,
        species_entry_id=species_entry.id,
    )

    thermo_create = resolve_thermo_upload(
        session,
        request,
        species_entry_id=species_entry.id,
    )
    # The upload service currently hardcodes an empty source_calculations
    # list and a null statmech_id on ThermoCreate; splice the resolved
    # values in here.
    thermo_create = thermo_create.model_copy(
        update={
            "source_calculations": resolved_source_calcs,
            "statmech_id": resolved_statmech_id,
        }
    )
    thermo = persist_thermo(session, thermo_create, created_by=created_by)

    applied_corrections: list = []
    for correction_payload in request.applied_energy_corrections:
        source_calc_id: int | None = None
        if correction_payload.source_calculation_key is not None:
            calc_row = calculations_by_key.get(
                correction_payload.source_calculation_key
            )
            if calc_row is None:
                # The schema validator normally prevents this, but defend
                # against future code paths that bypass validation.
                raise ValueError(
                    f"applied_energy_correction.source_calculation_key "
                    f"'{correction_payload.source_calculation_key}' did not "
                    f"resolve to a declared calculation."
                )
            _assert_calculation_owned_by(
                calc_row,
                species_entry_id=species_entry.id,
                context=(
                    "applied_energy_correction "
                    f"source_calculation_key='{correction_payload.source_calculation_key}'"
                ),
            )
            source_calc_id = calc_row.id

        applied_corrections.append(
            create_applied_energy_correction(
                session,
                correction_payload,
                target_species_entry_id=species_entry.id,
                source_calculation_id=source_calc_id,
                created_by=created_by,
            )
        )

    # Group-additivity estimation provenance (estimated thermo only; the
    # upload schema enforces the origin match). Reifies which GA scheme +
    # per-group contributions produced the estimated H298/S298 (DR-0035).
    if request.group_additivity is not None:
        create_applied_group_additivity(
            session,
            request.group_additivity,
            thermo_id=thermo.id,
            created_by=created_by,
        )

    session.flush()

    targets: list[RecordRef] = [
        RecordRef(SubmissionRecordType.thermo, thermo.id),
        RecordRef(SubmissionRecordType.species_entry, species_entry.id),
    ]
    targets.extend(
        RecordRef(SubmissionRecordType.calculation, c.id)
        for c in calculations_by_key.values()
    )
    targets.extend(
        RecordRef(SubmissionRecordType.applied_energy_correction, aec.id)
        for aec in applied_corrections
    )
    apply_review_policy(
        session, targets=targets, policy=review_policy, created_by=created_by
    )

    return thermo
