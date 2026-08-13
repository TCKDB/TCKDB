"""Service helpers for creating statmech records.

Statmech is a result table — every upload creates a new row.
No deduplication against existing records.

Upload payloads name their supporting calculations by **local key**, never
by row id. Turning a key into a real ``calculation.id`` happens here, from
the ``calculations_by_key`` map the calling workflow builds after it has
persisted the request's own calculations. That is the whole reason the
schema layer never needs to know a primary key.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from sqlalchemy.orm import Session

from app.api.error_contract import CodedValueError
from app.db.models.calculation import Calculation
from app.db.models.common import CalculationType, StatmechCalculationRole
from app.db.models.statmech import (
    Statmech,
    StatmechElectronicLevel,
    StatmechSourceCalculation,
    StatmechTorsion,
    StatmechTorsionDefinition,
)
from app.schemas.workflows.conformer_upload import ConformerUploadStatmechPayload
from app.services.calculation_resolution import resolve_workflow_tool_release_ref
from app.services.energy_correction_resolution import resolve_or_create_freq_scale_factor_ref
from app.services.literature_resolution import resolve_or_create_literature
from app.services.software_resolution import resolve_software_release_ref

logger = logging.getLogger(__name__)

#: A statmech source link names a calculation the upload never declared.
W_STATMECH_CALCULATION_KEY_UNDECLARED = "statmech_calculation_key_undeclared"

#: A statmech source link declares a role the resolved calculation cannot
#: play, because its ``Calculation.type`` is a different kind of job.
W_STATMECH_SOURCE_ROLE_TYPE_MISMATCH = "statmech_source_role_type_mismatch"

#: Roles that name a specific kind of job, and the ``CalculationType`` each
#: one requires. ``composite`` and ``imported`` are deliberately absent:
#: they describe a scientific origin rather than a job type, so no single
#: ``CalculationType`` is the right one for them (the same carve-out
#: ``_THERMO_ROLE_TO_CALC_TYPE`` makes in :mod:`app.workflows.thermo`).
_STATMECH_ROLE_TO_CALC_TYPE: dict[StatmechCalculationRole, CalculationType] = {
    StatmechCalculationRole.opt: CalculationType.opt,
    StatmechCalculationRole.freq: CalculationType.freq,
    StatmechCalculationRole.sp: CalculationType.sp,
    StatmechCalculationRole.scan: CalculationType.scan,
}


def _resolve_calculation_key(
    key: str,
    calculations_by_key: Mapping[str, int],
    *,
    context: str,
) -> int:
    """Translate one local calculation key into a persisted row id.

    The schema layer of every upload path already refuses a key it cannot
    match, so reaching this error means a workflow built an incomplete
    map. It is still raised rather than allowed to ``KeyError``: a missing
    provenance link must be a named failure, not a 500.

    :raises CodedValueError: if the key is absent from
        ``calculations_by_key``. The sentence is unchanged from when this
        rejection carried no code; only the envelope's ``code`` moves,
        from the generic ``validation_error`` to one a client can branch
        on.
    """
    calculation_id = calculations_by_key.get(key)
    if calculation_id is None:
        raise CodedValueError(
            W_STATMECH_CALCULATION_KEY_UNDECLARED,
            f"{context}: calculation_key '{key}' does not name a calculation "
            f"declared in this upload.",
            context={"field": context, "calculation_key": key},
            message_prefix=False,
        )
    return calculation_id


def assert_statmech_role_compatible(
    calculation: Calculation,
    *,
    role: StatmechCalculationRole,
    context: str,
) -> None:
    """Refuse a statmech source link whose role contradicts the calc's type.

    Blocking, and ADR 0008 permits that here because this asserts a
    *contract* rather than an expectation: ``role='freq'`` is the claim
    that the linked job produced the vibrational frequencies this
    partition function was built from, and an ``sp`` job did not produce
    any. No correct calculation can satisfy the link as written, so there
    is nothing to warn about — the record would assert evidence that does
    not exist.

    This is DR-0028 Requirement 1, applied to the path it was written for
    but never reached: thermo has enforced it since DR-0028
    (``app.workflows.thermo._assert_calculation_role_compatible``) while
    statmech accepted any pairing. Local string keys made the gap sharper
    than row ids did — a plausible-looking wrong key resolves silently
    where a wrong integer would more often have failed a foreign key.

    ``composite`` and ``imported`` accept any type, as they do for thermo:
    they name where a number came from, not which job produced it.

    :param calculation: The resolved source calculation row.
    :param role: The role the upload declared for it.
    :param context: Field path naming the offending link, copied verbatim
        into the 422 body — so it must name the caller's own key, never a
        row id.
    :raises CodedValueError: if ``role`` names a job type and the
        calculation is not of that type.
    """
    expected = _STATMECH_ROLE_TO_CALC_TYPE.get(role)
    if expected is None or calculation.type == expected:
        return
    # The row id is the operator's handle, not the depositor's: it goes to
    # the log, while the 422 names only the key the depositor wrote.
    logger.info(
        "statmech role/type mismatch at %s: calculation id=%s type=%s, role=%s",
        context,
        calculation.id,
        calculation.type.value,
        role.value,
    )
    raise CodedValueError(
        W_STATMECH_SOURCE_ROLE_TYPE_MISMATCH,
        f"{context}: role='{role.value}' claims a "
        f"'{expected.value}' calculation, but the calculation it names has "
        f"type '{calculation.type.value}'. A statmech record must not claim "
        f"evidence it does not have. Point the link at the "
        f"'{expected.value}' calculation, or declare the role the named "
        f"calculation actually played.",
        context={
            "field": context,
            "declared_role": role.value,
            "expected_calculation_type": expected.value,
            "actual_calculation_type": calculation.type.value,
        },
        message_prefix=False,
    )


def _assert_role_compatible_by_id(
    session: Session,
    calculation_id: int,
    *,
    role: StatmechCalculationRole,
    context: str,
) -> None:
    """Load a source calculation and run :func:`assert_statmech_role_compatible`.

    Every id reaching here was either persisted by the calling workflow in
    this same session or handed over as its uploaded calculation, so the
    ``session.get`` is an identity-map hit rather than a query. A row that
    is genuinely absent is left to the foreign key: this function's job is
    the role/type contract, not existence.
    """
    calculation = session.get(Calculation, calculation_id)
    if calculation is None:  # pragma: no cover - FK enforces existence
        return
    assert_statmech_role_compatible(calculation, role=role, context=context)


def resolve_or_create_statmech(
    session: Session,
    payload: ConformerUploadStatmechPayload,
    *,
    species_entry_id: int,
    uploaded_calculation_id: int | None = None,
    calculations_by_key: Mapping[str, int] | None = None,
    created_by: int | None = None,
) -> Statmech:
    """Create a statmech record and attach nested provenance.

    Always creates a new row — statmech records are provenance-bearing
    scientific results and multiple records per species entry are valid.

    The ``uploaded_calculation_id`` is only used when
    ``payload.uploaded_calculation_role`` is set (nested conformer upload
    path). Standalone statmech uploads leave both unset and declare any
    supporting calculations explicitly via ``payload.source_calculations``.

    :param session: Active SQLAlchemy session.
    :param payload: Workflow-facing statmech payload.
    :param species_entry_id: Resolved owner species-entry id.
    :param uploaded_calculation_id: Optional calculation id produced by
        the caller workflow; linked as a source calculation only when
        ``payload.uploaded_calculation_role`` is also set.
    :param calculations_by_key: Local calculation key → persisted
        ``calculation.id``, for the request's own calculations. Required
        whenever the payload carries ``source_calculations`` or a torsion
        ``source_scan_calculation_key``.
    :param created_by: Optional application user id for newly created rows.
    :returns: Newly created ``Statmech`` row with linked sources/torsions.
    :raises ValueError: If ``uploaded_calculation_role`` is set but
        ``uploaded_calculation_id`` is not supplied.
    :raises CodedValueError: If a local calculation key does not resolve,
        or if a declared role contradicts the resolved calculation's type.
    """
    key_map: Mapping[str, int] = calculations_by_key or {}

    literature = (
        resolve_or_create_literature(session, payload.literature)
        if payload.literature is not None
        else None
    )
    software_release = (
        resolve_software_release_ref(session, payload.software_release)
        if payload.software_release is not None
        else None
    )
    workflow_tool_release = resolve_workflow_tool_release_ref(
        session, payload.workflow_tool_release
    )

    fsf_id = None
    if payload.freq_scale_factor is not None:
        fsf = resolve_or_create_freq_scale_factor_ref(
            session, payload.freq_scale_factor, created_by=created_by
        )
        fsf_id = fsf.id

    statmech = Statmech(
        species_entry_id=species_entry_id,
        scientific_origin=payload.scientific_origin,
        literature_id=literature.id if literature is not None else None,
        workflow_tool_release_id=(
            workflow_tool_release.id if workflow_tool_release is not None else None
        ),
        software_release_id=(
            software_release.id if software_release is not None else None
        ),
        external_symmetry=payload.external_symmetry,
        point_group=payload.point_group,
        is_linear=payload.is_linear,
        rigid_rotor_kind=payload.rigid_rotor_kind,
        statmech_treatment=payload.statmech_treatment,
        frequency_scale_factor_id=fsf_id,
        uses_projected_frequencies=payload.uses_projected_frequencies,
        optical_isomers=payload.optical_isomers,
        note=payload.note,
        created_by=created_by,
    )
    session.add(statmech)
    session.flush()

    for level in payload.electronic_levels:
        session.add(
            StatmechElectronicLevel(
                statmech_id=statmech.id,
                level_index=level.level_index,
                energy_cm1=level.energy_cm1,
                degeneracy=level.degeneracy,
            )
        )

    # Attach source calculations. Every link is role/type checked here
    # rather than in each calling workflow, so all three statmech upload
    # paths -- nested conformer, standalone statmech, and the bundle via
    # ``_persist_statmech_block`` -- inherit the same refusal.
    if payload.uploaded_calculation_role is not None:
        if uploaded_calculation_id is None:
            raise ValueError(
                "uploaded_calculation_role is set but no uploaded_calculation_id "
                "was provided to resolve_or_create_statmech."
            )
        _assert_role_compatible_by_id(
            session,
            uploaded_calculation_id,
            role=payload.uploaded_calculation_role,
            context="statmech.uploaded_calculation_role",
        )
        session.add(
            StatmechSourceCalculation(
                statmech_id=statmech.id,
                calculation_id=uploaded_calculation_id,
                role=payload.uploaded_calculation_role,
            )
        )

    for index, source in enumerate(payload.source_calculations):
        context = f"statmech.source_calculations[{index}]"
        calculation_id = _resolve_calculation_key(
            source.calculation_key,
            key_map,
            context=context,
        )
        _assert_role_compatible_by_id(
            session,
            calculation_id,
            role=source.role,
            context=f"{context}.calculation_key='{source.calculation_key}'",
        )
        session.add(
            StatmechSourceCalculation(
                statmech_id=statmech.id,
                calculation_id=calculation_id,
                role=source.role,
            )
        )

    # Attach torsions and coordinates
    for torsion_index, torsion_payload in enumerate(payload.torsions):
        scan_calculation_id: int | None = None
        if torsion_payload.source_scan_calculation_key is not None:
            scan_calculation_id = _resolve_calculation_key(
                torsion_payload.source_scan_calculation_key,
                key_map,
                context=(
                    f"statmech.torsions[{torsion_index}]"
                    f".source_scan_calculation_key"
                ),
            )
        torsion = StatmechTorsion(
            statmech_id=statmech.id,
            torsion_index=torsion_payload.torsion_index,
            symmetry_number=torsion_payload.symmetry_number,
            treatment_kind=torsion_payload.treatment_kind,
            dimension=torsion_payload.dimension,
            top_description=torsion_payload.top_description,
            invalidated_reason=torsion_payload.invalidated_reason,
            note=torsion_payload.note,
            source_scan_calculation_id=scan_calculation_id,
        )
        session.add(torsion)
        session.flush()

        for coordinate_payload in torsion_payload.coordinates:
            session.add(
                StatmechTorsionDefinition(
                    torsion_id=torsion.id,
                    coordinate_index=coordinate_payload.coordinate_index,
                    atom1_index=coordinate_payload.atom1_index,
                    atom2_index=coordinate_payload.atom2_index,
                    atom3_index=coordinate_payload.atom3_index,
                    atom4_index=coordinate_payload.atom4_index,
                )
            )

    session.flush()
    return statmech
