from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql import ColumnElement

import app.db.models  # noqa: F401
from app.db.models.calculation import (
    Calculation,
    CalculationDependency,
    CalculationFreqResult,
    CalculationIRCPoint,
    CalculationIRCResult,
    CalculationNEBImageResult,
    CalculationOptResult,
    CalculationOutputGeometry,
    CalculationParameter,
    CalculationParameterVocab,
    CalculationSPResult,
)
from app.db.models.common import (
    CalculationDependencyRole,
    CalculationGeometryRole,
    CalculationType,
    IRCDirection,
)
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.workflow import WorkflowTool, WorkflowToolRelease
from app.schemas.entities.calculation import CalculationCreateResolved
from app.schemas.fragments.calculation import (
    CalculationCreateRequest,
    CalculationParameterObservation,
    CalculationWithResultsPayload,
    IRCResultPayload,
    NEBResultPayload,
)
from app.schemas.fragments.refs import (
    LevelOfTheoryRef,
    WorkflowToolReleaseRef,
)
from app.services.geometry_resolution import resolve_geometry_payload
from app.services.software_resolution import resolve_software_release_ref


def _null_safe_equals(column: ColumnElement, value: str | None) -> ColumnElement[bool]:
    """Build a nullable equality predicate for dedupe lookups.

    :param column: SQLAlchemy column expression to compare.
    :param value: Candidate value, possibly ``None``.
    :returns: ``column IS NULL`` when ``value`` is ``None``, otherwise ``column = value``.
    """

    return column.is_(None) if value is None else column == value


def _level_of_theory_hash(ref: LevelOfTheoryRef) -> str:
    """Compute the canonical level-of-theory hash used for dedupe.

    :param ref: Upload-facing level-of-theory reference.
    :returns: SHA-256 hash of the canonicalized level-of-theory payload.
    """

    payload = {
        "method": ref.method,
        "basis": ref.basis,
        "aux_basis": ref.aux_basis,
        "cabs_basis": ref.cabs_basis,
        "dispersion": ref.dispersion,
        "solvent": ref.solvent,
        "solvent_model": ref.solvent_model,
        "keywords": ref.keywords,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def resolve_workflow_tool_release_ref(
    session: Session,
    ref: WorkflowToolReleaseRef | None,
) -> WorkflowToolRelease | None:
    """Resolve or create a workflow-tool release row.

    :param session: Active SQLAlchemy session.
    :param ref: Optional upload-facing workflow-tool release reference.
    :returns: Existing/new ``WorkflowToolRelease`` row, or ``None`` when omitted.
    """

    if ref is None:
        return None

    workflow_tool = session.scalar(
        select(WorkflowTool).where(WorkflowTool.name == ref.name)
    )
    if workflow_tool is None:
        try:
            with session.begin_nested():
                workflow_tool = WorkflowTool(name=ref.name)
                session.add(workflow_tool)
                session.flush()
        except IntegrityError:
            workflow_tool = session.scalar(
                select(WorkflowTool).where(WorkflowTool.name == ref.name)
            )

    release = session.scalar(
        select(WorkflowToolRelease).where(
            WorkflowToolRelease.workflow_tool_id == workflow_tool.id,
            _null_safe_equals(WorkflowToolRelease.version, ref.version),
            _null_safe_equals(WorkflowToolRelease.git_commit, ref.git_commit),
        )
    )
    if release is None:
        try:
            with session.begin_nested():
                release = WorkflowToolRelease(
                    workflow_tool_id=workflow_tool.id,
                    version=ref.version,
                    git_commit=ref.git_commit,
                    release_date=ref.release_date,
                    notes=ref.notes,
                )
                session.add(release)
                session.flush()
        except IntegrityError:
            release = session.scalar(
                select(WorkflowToolRelease).where(
                    WorkflowToolRelease.workflow_tool_id == workflow_tool.id,
                    _null_safe_equals(WorkflowToolRelease.version, ref.version),
                    _null_safe_equals(WorkflowToolRelease.git_commit, ref.git_commit),
                )
            )

    return release


def resolve_level_of_theory_ref(
    session: Session,
    ref: LevelOfTheoryRef,
) -> LevelOfTheory:
    """Resolve or create a level-of-theory row.

    :param session: Active SQLAlchemy session.
    :param ref: Upload-facing level-of-theory reference.
    :returns: Existing or newly created ``LevelOfTheory`` row.
    """

    lot_hash = _level_of_theory_hash(ref)
    level_of_theory = session.scalar(
        select(LevelOfTheory).where(LevelOfTheory.lot_hash == lot_hash)
    )
    if level_of_theory is None:
        try:
            with session.begin_nested():
                level_of_theory = LevelOfTheory(
                    method=ref.method,
                    basis=ref.basis,
                    aux_basis=ref.aux_basis,
                    cabs_basis=ref.cabs_basis,
                    dispersion=ref.dispersion,
                    solvent=ref.solvent,
                    solvent_model=ref.solvent_model,
                    keywords=ref.keywords,
                    lot_hash=lot_hash,
                )
                session.add(level_of_theory)
                session.flush()
        except IntegrityError:
            level_of_theory = session.scalar(
                select(LevelOfTheory).where(LevelOfTheory.lot_hash == lot_hash)
            )

    return level_of_theory


def resolve_calculation_create_request(
    session: Session,
    request: CalculationCreateRequest,
) -> CalculationCreateResolved:
    """Resolve upload-facing calculation provenance into foreign-key ids.

    :param session: Active SQLAlchemy session.
    :param request: Upload-facing calculation create request.
    :returns: Internal resolved calculation payload with database ids.
    """

    software_release = resolve_software_release_ref(session, request.software_release)
    workflow_tool_release = resolve_workflow_tool_release_ref(
        session, request.workflow_tool_release
    )
    level_of_theory = resolve_level_of_theory_ref(session, request.level_of_theory)

    return CalculationCreateResolved(
        type=request.type,
        quality=request.quality,
        species_entry_id=request.species_entry_id,
        transition_state_entry_id=request.transition_state_entry_id,
        software_release_id=software_release.id,
        workflow_tool_release_id=(
            workflow_tool_release.id if workflow_tool_release else None
        ),
        lot_id=level_of_theory.id,
        literature_id=request.literature_id,
    )


def persist_calculation(
    session: Session,
    resolved: CalculationCreateResolved,
    *,
    created_by: int | None = None,
) -> Calculation:
    """Persist a resolved calculation payload.

    :param session: Active SQLAlchemy session.
    :param resolved: Internal resolved calculation payload.
    :param created_by: Optional application user id for the created row.
    :returns: Newly created ``Calculation`` row.
    """

    calculation = Calculation(
        type=resolved.type,
        quality=resolved.quality,
        species_entry_id=resolved.species_entry_id,
        transition_state_entry_id=resolved.transition_state_entry_id,
        software_release_id=resolved.software_release_id,
        workflow_tool_release_id=resolved.workflow_tool_release_id,
        lot_id=resolved.lot_id,
        literature_id=resolved.literature_id,
        created_by=created_by,
    )
    session.add(calculation)
    session.flush()
    return calculation


# ---------------------------------------------------------------------------
# Generic "calculation + typed results + dependency edges" helpers
# ---------------------------------------------------------------------------


_IRC_POINT_DIRECTION_TO_ROLE: dict[IRCDirection, CalculationGeometryRole] = {
    IRCDirection.forward: CalculationGeometryRole.irc_forward,
    IRCDirection.reverse: CalculationGeometryRole.irc_reverse,
}


def _pending_output_geometry_ids(
    session: Session, calculation_id: int
) -> set[int]:
    """Collect geometry ids already linked to a calculation in this session.

    Inspects both newly-added pending rows and any persisted rows for the
    calculation so callers can skip rows that would violate the
    ``(calculation_id, geometry_id)`` uniqueness constraint.

    :param session: Active SQLAlchemy session.
    :param calculation_id: The calculation being inspected.
    :returns: Set of geometry ids already linked (pending or persisted).
    """

    linked: set[int] = set()
    for obj in session.new:
        if (
            isinstance(obj, CalculationOutputGeometry)
            and obj.calculation_id == calculation_id
            and obj.geometry_id is not None
        ):
            linked.add(obj.geometry_id)

    persisted = session.scalars(
        select(CalculationOutputGeometry.geometry_id).where(
            CalculationOutputGeometry.calculation_id == calculation_id
        )
    ).all()
    linked.update(persisted)
    return linked


def _persist_irc_result(
    session: Session,
    calculation: Calculation,
    payload: IRCResultPayload,
) -> None:
    """Persist an IRC result bundle and its sampled points.

    Resolves optional inline point geometries via the shared geometry
    resolution service. Forward- and reverse-direction points additionally
    produce a ``CalculationOutputGeometry`` row with the matching role;
    the TS-marker point is intentionally not linked through
    ``calculation_output_geometry`` since there is no clean role mapping
    (the geometry is still preserved on ``calc_irc_point.geometry_id``).

    :param session: Active SQLAlchemy session.
    :param calculation: The owning IRC calculation row.
    :param payload: Upload-facing IRC result bundle.
    """

    session.add(
        CalculationIRCResult(
            calculation_id=calculation.id,
            direction=payload.direction,
            has_forward=payload.has_forward,
            has_reverse=payload.has_reverse,
            ts_point_index=payload.ts_point_index,
            point_count=(
                payload.point_count
                if payload.point_count is not None
                else (len(payload.points) if payload.points else None)
            ),
            zero_energy_reference_hartree=payload.zero_energy_reference_hartree,
            note=payload.note,
        )
    )

    linked_geometry_ids = _pending_output_geometry_ids(session, calculation.id)

    for point in sorted(payload.points, key=lambda p: p.point_index):
        geometry_id: int | None = None
        if point.geometry is not None:
            geometry_id = resolve_geometry_payload(session, point.geometry).id

        session.add(
            CalculationIRCPoint(
                calculation_id=calculation.id,
                point_index=point.point_index,
                direction=point.direction,
                is_ts=point.is_ts,
                reaction_coordinate=point.reaction_coordinate,
                electronic_energy_hartree=point.electronic_energy_hartree,
                relative_energy_kj_mol=point.relative_energy_kj_mol,
                max_gradient=point.max_gradient,
                rms_gradient=point.rms_gradient,
                geometry_id=geometry_id,
                note=point.note,
            )
        )

        role = _IRC_POINT_DIRECTION_TO_ROLE.get(point.direction) if point.direction else None
        if (
            geometry_id is not None
            and role is not None
            and geometry_id not in linked_geometry_ids
        ):
            session.add(
                CalculationOutputGeometry(
                    calculation_id=calculation.id,
                    geometry_id=geometry_id,
                    output_order=point.point_index + 2,
                    role=role,
                )
            )
            linked_geometry_ids.add(geometry_id)


def _persist_neb_result(
    session: Session,
    calculation: Calculation,
    payload: NEBResultPayload,
) -> None:
    """Persist a NEB result bundle and its per-image rows.

    Resolves optional inline image geometries via the shared geometry
    resolution service and links each resolved geometry through
    ``calculation_output_geometry`` with role ``neb_image``. Duplicate
    ``(calculation_id, geometry_id)`` pairs are silently collapsed to a
    single link to honour the table uniqueness constraint.

    :param session: Active SQLAlchemy session.
    :param calculation: The owning NEB calculation row.
    :param payload: Upload-facing NEB result bundle.
    """

    linked_geometry_ids = _pending_output_geometry_ids(session, calculation.id)

    for image in sorted(payload.images, key=lambda img: img.image_index):
        geometry_id: int | None = None
        if image.geometry is not None:
            geometry_id = resolve_geometry_payload(session, image.geometry).id

        session.add(
            CalculationNEBImageResult(
                calculation_id=calculation.id,
                image_index=image.image_index,
                electronic_energy_hartree=image.electronic_energy_hartree,
                relative_energy_kj_mol=image.relative_energy_kj_mol,
                path_distance_angstrom=image.path_distance_angstrom,
                max_force=image.max_force,
                rms_force=image.rms_force,
                is_climbing_image=image.is_climbing_image,
            )
        )

        if geometry_id is not None and geometry_id not in linked_geometry_ids:
            session.add(
                CalculationOutputGeometry(
                    calculation_id=calculation.id,
                    geometry_id=geometry_id,
                    output_order=image.image_index + 2,
                    role=CalculationGeometryRole.neb_image,
                )
            )
            linked_geometry_ids.add(geometry_id)


def persist_calculation_result(
    session: Session,
    calculation: Calculation,
    calc_upload: CalculationWithResultsPayload,
) -> None:
    """Persist an optional typed result block for a calculation.

    :param session: Active SQLAlchemy session.
    :param calculation: The owning calculation row.
    :param calc_upload: Upload payload (may have one result block set).
    :raises ValueError: If a result block's type does not match the
        calculation's ``type`` — a defensive check that mirrors the
        schema-layer validator.
    """

    if calc_upload.opt_result is not None:
        session.add(
            CalculationOptResult(
                calculation_id=calculation.id,
                converged=calc_upload.opt_result.converged,
                n_steps=calc_upload.opt_result.n_steps,
                final_energy_hartree=calc_upload.opt_result.final_energy_hartree,
            )
        )

    if calc_upload.freq_result is not None:
        session.add(
            CalculationFreqResult(
                calculation_id=calculation.id,
                n_imag=calc_upload.freq_result.n_imag,
                imag_freq_cm1=calc_upload.freq_result.imag_freq_cm1,
                zpe_hartree=calc_upload.freq_result.zpe_hartree,
            )
        )

    if calc_upload.sp_result is not None:
        session.add(
            CalculationSPResult(
                calculation_id=calculation.id,
                electronic_energy_hartree=calc_upload.sp_result.electronic_energy_hartree,
            )
        )

    if calc_upload.irc_result is not None:
        if calculation.type != CalculationType.irc:
            raise ValueError(
                f"irc_result is only allowed on irc calculations "
                f"(got type '{calculation.type.value}')."
            )
        _persist_irc_result(session, calculation, calc_upload.irc_result)

    if calc_upload.neb_result is not None:
        if calculation.type != CalculationType.neb:
            raise ValueError(
                f"neb_result is only allowed on neb calculations "
                f"(got type '{calculation.type.value}')."
            )
        _persist_neb_result(session, calculation, calc_upload.neb_result)


def _resolve_canonical_keys(
    session: Session, candidate_keys: set[str]
) -> set[str]:
    """Return the subset of candidate canonical keys present in the vocab.

    Used to decide whether a parsed parameter's ``canonical_key`` can be
    written through the FK or must be demoted to ``NULL``. The vocab is
    intentionally not auto-populated: missing keys yield ``NULL`` so the
    raw observation still persists.

    :param session: Active SQLAlchemy session.
    :param candidate_keys: Canonical-key strings emitted by the parser.
    :returns: Subset of ``candidate_keys`` that already exist in vocab.
    """

    if not candidate_keys:
        return set()

    return set(
        session.scalars(
            select(CalculationParameterVocab.canonical_key).where(
                CalculationParameterVocab.canonical_key.in_(candidate_keys)
            )
        ).all()
    )


def persist_calculation_parameters(
    session: Session,
    calculation: Calculation,
    observations: Iterable[CalculationParameterObservation] | None,
    *,
    parameters_json: dict | None = None,
    parameters_parser_version: str | None = None,
    parameters_extracted_at: datetime | None = None,
) -> list[CalculationParameter]:
    """Persist parsed execution-control parameters for a calculation.

    Writes one ``CalculationParameter`` row per supplied observation and,
    when provided, mirrors the parser snapshot/version/extracted-at fields
    onto the ``Calculation`` row. Canonical keys not present in
    ``calculation_parameter_vocab`` are silently demoted to ``NULL`` so the
    upload never fails on unmapped parameters; the raw key/value pair is
    always preserved.

    :param session: Active SQLAlchemy session.
    :param calculation: The owning calculation row.
    :param observations: Parsed parameter observations (may be ``None`` or empty).
    :param parameters_json: Optional JSON snapshot from the parser to mirror onto
        ``calculation.parameters_json``.
    :param parameters_parser_version: Optional parser version tag.
    :param parameters_extracted_at: Optional extraction timestamp.
    :returns: Newly created parameter rows in the order they were supplied.
    """

    if parameters_json is not None:
        calculation.parameters_json = parameters_json
    if parameters_parser_version is not None:
        calculation.parameters_parser_version = parameters_parser_version
    if parameters_extracted_at is not None:
        calculation.parameters_extracted_at = parameters_extracted_at

    if not observations:
        return []

    obs_list = list(observations)
    candidate_keys = {
        obs.canonical_key for obs in obs_list if obs.canonical_key is not None
    }
    known_keys = _resolve_canonical_keys(session, candidate_keys)

    rows: list[CalculationParameter] = []
    for obs in obs_list:
        canonical_key = (
            obs.canonical_key
            if obs.canonical_key is not None and obs.canonical_key in known_keys
            else None
        )
        canonical_value = obs.canonical_value if canonical_key is not None else None

        row = CalculationParameter(
            calculation_id=calculation.id,
            raw_key=obs.raw_key,
            raw_value=obs.raw_value,
            canonical_key=canonical_key,
            canonical_value=canonical_value,
            section=obs.section,
            value_type=obs.value_type,
            unit=obs.unit,
            parameter_index=obs.parameter_index,
        )
        session.add(row)
        rows.append(row)

    return rows


# Mapping from calculation type to the dependency role when the child
# depends on the primary calculation.
_DEPENDENCY_ROLE_FOR_TYPE: dict[CalculationType, CalculationDependencyRole] = {
    CalculationType.freq: CalculationDependencyRole.freq_on,
    CalculationType.sp: CalculationDependencyRole.single_point_on,
    CalculationType.irc: CalculationDependencyRole.irc_start,
    CalculationType.neb: CalculationDependencyRole.neb_parent,
}


def resolve_and_persist_calculation_with_results(
    session: Session,
    calc_upload: CalculationWithResultsPayload,
    *,
    species_entry_id: int | None = None,
    transition_state_entry_id: int | None = None,
    created_by: int | None = None,
) -> Calculation:
    """Resolve provenance, persist a calculation, and attach typed results.

    :param session: Active SQLAlchemy session.
    :param calc_upload: Upload-facing calculation block with optional results.
    :param species_entry_id: Owner species-entry id (mutually exclusive with TS).
    :param transition_state_entry_id: Owner TS-entry id.
    :param created_by: Optional application user id.
    :returns: Persisted ``Calculation`` row.
    """

    request = CalculationCreateRequest(
        type=calc_upload.type,
        quality=calc_upload.quality,
        species_entry_id=species_entry_id,
        transition_state_entry_id=transition_state_entry_id,
        software_release=calc_upload.software_release,
        workflow_tool_release=calc_upload.workflow_tool_release,
        level_of_theory=calc_upload.level_of_theory,
        literature_id=calc_upload.literature_id,
    )
    resolved = resolve_calculation_create_request(session, request)
    calculation = persist_calculation(session, resolved, created_by=created_by)
    persist_calculation_result(session, calculation, calc_upload)
    persist_calculation_parameters(
        session,
        calculation,
        calc_upload.parameters,
        parameters_json=calc_upload.parameters_json,
        parameters_parser_version=calc_upload.parameters_parser_version,
        parameters_extracted_at=calc_upload.parameters_extracted_at,
    )
    return calculation


def persist_additional_calculations(
    session: Session,
    *,
    primary_calc: Calculation,
    additional_uploads: list[CalculationWithResultsPayload],
    geometry_id: int,
    species_entry_id: int | None = None,
    transition_state_entry_id: int | None = None,
    created_by: int | None = None,
) -> list[Calculation]:
    """Persist additional calculations with dependency edges to a primary.

    Creates each additional calculation, links it to the shared output
    geometry, attaches typed results, and wires a ``CalculationDependency``
    edge back to the primary calculation.

    :param session: Active SQLAlchemy session.
    :param primary_calc: The primary calculation row (parent for deps).
    :param additional_uploads: Additional calculation uploads.
    :param geometry_id: Shared output geometry id.
    :param species_entry_id: Owner species-entry id (mutually exclusive with TS).
    :param transition_state_entry_id: Owner TS-entry id.
    :param created_by: Optional application user id.
    :returns: List of newly created ``Calculation`` rows.
    """

    results: list[Calculation] = []
    for calc_upload in additional_uploads:
        child_calc = resolve_and_persist_calculation_with_results(
            session,
            calc_upload,
            species_entry_id=species_entry_id,
            transition_state_entry_id=transition_state_entry_id,
            created_by=created_by,
        )

        # Result persistence (e.g. NEB climbing image on the TS geometry) may
        # have already linked the shared geometry. Skip the role=final row
        # when that happens to honour the (calculation_id, geometry_id)
        # uniqueness constraint.
        if geometry_id not in _pending_output_geometry_ids(session, child_calc.id):
            session.add(
                CalculationOutputGeometry(
                    calculation_id=child_calc.id,
                    geometry_id=geometry_id,
                    output_order=1,
                    role=CalculationGeometryRole.final,
                )
            )

        dep_role = _DEPENDENCY_ROLE_FOR_TYPE.get(calc_upload.type)
        if dep_role is not None:
            session.add(
                CalculationDependency(
                    parent_calculation_id=primary_calc.id,
                    child_calculation_id=child_calc.id,
                    dependency_role=dep_role,
                )
            )

        results.append(child_calc)

    session.flush()
    return results
