"""Service helpers for creating statmech records.

Statmech is a result table — every upload creates a new row.
No deduplication against existing records.

Upload payloads name their supporting calculations by **local key**, never
by row id. Turning a key into a real ``calculation.id`` happens here, from
the ``calculations_by_key`` map the calling workflow builds after it has
persisted the request's own calculations. That is the whole reason the
schema layer never needs to know a primary key.

The standalone statmech upload adds one second way to name a calculation:
``existing_calculation_id``, citing a row an *earlier request* deposited.
It exists because calculations are append-only and never deduplicated, so
a deposit forced to re-send calculations it already stored would mint
duplicate rows for the same job and turn "how many distinct calculations
support this" into "how many times someone re-uploaded". It is handled
here, next to the key path and not in the calling workflow, so that the
two ways of naming a calculation cannot drift apart in what they check —
a citation that is cheaper to validate is the route depositors use to get
around validation. Bundle paths keep the key-only component; see
``app.schemas.workflows.statmech_upload.StatmechSourceCalculationIn``.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from sqlalchemy.orm import Session

from app.api.error_contract import CodedValueError
from app.api.errors import NotFoundError
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

#: Roles that name a specific kind of job, and the ``CalculationType``\ s
#: each one accepts. The first entry is the canonical one — the type the
#: role is named after — and is what the refusal reports as *expected*.
#:
#: ``composite`` and ``imported`` are deliberately absent: they describe a
#: scientific origin rather than a job type, so no ``CalculationType`` is
#: the right one for them (the same carve-out
#: ``_THERMO_ROLE_TO_CALC_TYPES`` makes in :mod:`app.workflows.thermo`).
#:
#: ``sp`` is the one role with a second accepted type, and the asymmetry is
#: deliberate — see :func:`assert_statmech_role_compatible`.
_STATMECH_ROLE_TO_CALC_TYPES: dict[
    StatmechCalculationRole, tuple[CalculationType, ...]
] = {
    StatmechCalculationRole.opt: (CalculationType.opt,),
    StatmechCalculationRole.freq: (CalculationType.freq,),
    StatmechCalculationRole.sp: (CalculationType.sp, CalculationType.opt),
    StatmechCalculationRole.scan: (CalculationType.scan,),
}


def accepted_types_phrase(accepted: tuple[CalculationType, ...]) -> str:
    """``"'sp' or 'opt'"`` — the accepted types, for a refusal message."""
    return " or ".join(f"'{calc_type.value}'" for calc_type in accepted)


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


def _chained_calculation_id(source: object) -> int | None:
    """Read the optional ``existing_calculation_id`` off a source-calc link.

    Read reflectively, and the indirection is the point rather than
    laziness. Only the standalone statmech upload's
    ``StatmechSourceCalculationIn`` declares this field; the shared wire
    component ``tckdb_schemas.statmech_bits.StatmechSourceCalcIn`` that
    the conformer and bundle paths use stays key-only, so there is no
    single static type this function could take that spans all three.
    Widening the shared component instead would hand chaining to the
    bundle routes, which do not need it and are deliberately denied it.

    ``payload.source_calculations`` is annotated with the key-only base
    class, so this also documents *why* an attribute the annotation does
    not mention is nonetheless present at runtime: Pydantic v2 defaults to
    ``revalidate_instances="never"``, so a subclass instance assigned to a
    parent-typed field passes through intact rather than being narrowed to
    the parent. ``test_statmech_upload_chained_id_survives_payload_handover``
    pins that, because it is the kind of behaviour that would otherwise
    change under us silently and drop the link with no error at all.
    """
    value = getattr(source, "existing_calculation_id", None)
    return value if isinstance(value, int) else None


def assert_statmech_calculation_owned_by(
    calculation: Calculation,
    *,
    species_entry_id: int,
    context: str,
) -> None:
    """Refuse a supporting calculation belonging to another species entry.

    Supporting calculations attached to a statmech record must belong to
    the same species entry as the statmech target; otherwise the
    provenance link is scientifically meaningless — the record would cite
    evidence about a different molecule.

    Lives here rather than in ``app.workflows.statmech`` because the two
    ways of naming a calculation must be judged by the same code. For an
    inline calculation the check is defensive (the workflow persists it
    against the target's own species entry, so it cannot fail); for an
    ``existing_calculation_id`` it is the real thing, because a chained id
    can name any row in the database.

    :raises ValueError: if the calculation does not belong to
        ``species_entry_id``. Surfaces as HTTP 422.
    """
    if calculation.species_entry_id == species_entry_id:
        return
    # ``context`` already names the calculation the way the depositor
    # wrote it, which is the identifier they can act on. The row ids go to
    # the log instead — a 422 body must not hand out primary keys.
    logger.warning(
        "%s: calculation id=%s owned by species_entry_id=%s, not %s",
        context,
        calculation.id,
        calculation.species_entry_id,
        species_entry_id,
    )
    raise ValueError(
        f"{context}: this calculation belongs to another species entry, "
        "not to the statmech target. A supporting calculation must be one "
        "of the target species entry's own."
    )


def _resolve_existing_calculation(
    session: Session,
    calculation_id: int,
    *,
    species_entry_id: int,
    context: str,
) -> Calculation:
    """Load a chained ``existing_calculation_id`` and check it may be cited.

    The counterpart of :func:`_resolve_calculation_key` for the citation
    that reaches outside this request. A row that is absent is a 404 (the
    depositor named something that is not there); a row owned by another
    species entry is a 422. Neither message discloses a row id the caller
    did not supply — the id in ``existing_calculation_id`` is echoed back
    only because they wrote it.

    Returning the row rather than its id is deliberate: the caller needs
    it for :func:`assert_statmech_role_compatible`, and re-fetching would
    invite the two checks to run against different rows.
    """
    calculation = session.get(Calculation, calculation_id)
    if calculation is None:
        raise NotFoundError(
            f"{context}: refers to a calculation that does not exist."
        )
    assert_statmech_calculation_owned_by(
        calculation,
        species_entry_id=species_entry_id,
        context=context,
    )
    return calculation


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
    (:func:`app.workflows.thermo.assert_thermo_role_matches_calculation_type`)
    while statmech accepted any pairing. Local string keys made the gap sharper
    than row ids did — a plausible-looking wrong key resolves silently
    where a wrong integer would more often have failed a foreign key.

    ``composite`` and ``imported`` accept any type, as they do for thermo:
    they name where a number came from, not which job produced it.

    One named exception, and only one: **``role='sp'`` accepts an ``opt``
    calculation.** A depositor who never ran a separate single point has
    not lost the number — an optimisation's final energy *is* the
    single-point value, at the optimisation's own level of theory, which
    the calculation row already carries. Refusing that rejects a
    legitimate and common deposit. The mixed-method case is not a
    counter-example but the other branch: someone optimising at
    wB97X-D/def2-TZVP and refining at DLPNO-CCSD(T)/def2-TZVP *has* a
    separate ``sp`` calculation and links the ``sp`` role to it, so there
    is never a second level of theory to reconcile here.

    The exception does not run in reverse, and the asymmetry is the point:
    an ``sp`` calculation cannot serve the ``opt`` role, because it
    optimised nothing and there is no converged geometry to claim. Nor
    does it generalise — ``role='freq'`` on an ``sp`` job is still a
    record asserting vibrational evidence that was never produced.
    ``backend/tests/api/test_api_upload_key_and_role_contracts.py`` pins
    both directions so that "restoring symmetry" fails a test rather than
    a deposit.

    :param calculation: The resolved source calculation row.
    :param role: The role the upload declared for it.
    :param context: Field path naming the offending link, copied verbatim
        into the 422 body — so it must name the caller's own key, never a
        row id.
    :raises CodedValueError: if ``role`` names a job type and the
        calculation is of none of the types that role accepts.
    """
    accepted = _STATMECH_ROLE_TO_CALC_TYPES.get(role)
    if accepted is None or calculation.type in accepted:
        return
    expected = accepted[0]
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
        f"evidence it does not have. Point the link at a "
        f"{accepted_types_phrase(accepted)} calculation, or declare the role "
        f"the named calculation actually played.",
        context={
            "field": context,
            "declared_role": role.value,
            "expected_calculation_type": expected.value,
            "accepted_calculation_types": [
                calc_type.value for calc_type in accepted
            ],
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
        chained_id = _chained_calculation_id(source)
        if chained_id is not None:
            context = f"{context}.existing_calculation_id"
            calculation = _resolve_existing_calculation(
                session,
                chained_id,
                species_entry_id=species_entry_id,
                context=context,
            )
            calculation_id = calculation.id
            # Same function, same map, same refusal as the local path.
            # Routing the chained citation around this check is exactly
            # how it would become the way to deposit an unchecked link.
            assert_statmech_role_compatible(
                calculation, role=source.role, context=context
            )
        else:
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
