from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql import ColumnElement

from app.db.models.calculation import (
    Calculation,
    CalculationConstraint,
    CalculationDependency,
    CalculationFreqMode,
    CalculationFreqResult,
    CalculationHessian,
    CalculationInputGeometry,
    CalculationIRCPoint,
    CalculationIRCResult,
    CalculationOptResult,
    CalculationOutputGeometry,
    CalculationParameter,
    CalculationParameterVocab,
    CalculationPathSearchPoint,
    CalculationPathSearchResult,
    CalculationSCFStability,
    CalculationSPResult,
    CalculationWavefunctionDiagnostic,
)
from app.db.models.common import (
    CalculationDependencyRole,
    CalculationGeometryRole,
    CalculationType,
    IRCDirection,
    ParameterSource,
)
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.workflow import WorkflowTool, WorkflowToolRelease
from app.schemas.entities.calculation import CalculationCreateResolved
from app.schemas.fragments.calculation import (
    CalculationCreateRequest,
    CalculationParameterObservation,
    CalculationWithResultsPayload,
    IRCResultPayload,
    OutputGeometryEntry,
    PathSearchResultPayload,
)
from app.schemas.fragments.geometry import GeometryPayload
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
        # DR-0034: spin treatment is part of LOT identity. NULL folds to
        # "unknown" in the hash so a row that omits it and a row that says
        # "unknown" are the same level of theory.
        "spin_treatment": (
            getattr(ref.spin_treatment, "value", ref.spin_treatment)
            or "unknown"
        ),
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
                    spin_treatment=ref.spin_treatment,
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


def _persist_path_search_result(
    session: Session,
    calculation: Calculation,
    payload: PathSearchResultPayload,
) -> None:
    """Persist a path-search result bundle and its per-point rows.

    Generalizes NEB / GSM / string-method TS-search outputs. Resolves
    optional inline point geometries via the shared geometry resolution
    service and links each resolved geometry through
    ``calculation_output_geometry`` with role ``path_search_point``.
    Duplicate ``(calculation_id, geometry_id)`` pairs are silently
    collapsed to a single link to honour the table uniqueness constraint.

    :param session: Active SQLAlchemy session.
    :param calculation: The owning path-search calculation row.
    :param payload: Upload-facing path-search result bundle.
    """

    session.add(
        CalculationPathSearchResult(
            calculation_id=calculation.id,
            method=payload.method,
            is_double_ended=payload.is_double_ended,
            converged=payload.converged,
            n_points=payload.n_points,
            selected_ts_point_index=payload.selected_ts_point_index,
            climbing_image_index=payload.climbing_image_index,
            source_endpoint_count=payload.source_endpoint_count,
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
            CalculationPathSearchPoint(
                calculation_id=calculation.id,
                point_index=point.point_index,
                electronic_energy_hartree=point.electronic_energy_hartree,
                relative_energy_kj_mol=point.relative_energy_kj_mol,
                path_coordinate=point.path_coordinate,
                max_force=point.max_force,
                rms_force=point.rms_force,
                max_gradient=point.max_gradient,
                rms_gradient=point.rms_gradient,
                is_ts_guess=point.is_ts_guess,
                is_climbing_image=point.is_climbing_image,
                geometry_id=geometry_id,
                note=point.note,
            )
        )

        if geometry_id is not None and geometry_id not in linked_geometry_ids:
            session.add(
                CalculationOutputGeometry(
                    calculation_id=calculation.id,
                    geometry_id=geometry_id,
                    output_order=point.point_index + 2,
                    role=CalculationGeometryRole.path_search_point,
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
        if calc_upload.freq_result.modes:
            for mode in calc_upload.freq_result.modes:
                session.add(
                    CalculationFreqMode(
                        calculation_id=calculation.id,
                        mode_index=mode.mode_index,
                        frequency_cm1=mode.frequency_cm1,
                        is_imaginary=mode.is_imaginary,
                        reduced_mass_amu=mode.reduced_mass_amu,
                        force_constant_mdyne_angstrom=mode.force_constant_mdyne_angstrom,
                        ir_intensity_km_mol=mode.ir_intensity_km_mol,
                        raman_activity=mode.raman_activity,
                        symmetry_label=mode.symmetry_label,
                        note=mode.note,
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

    if calc_upload.path_search_result is not None:
        if calculation.type != CalculationType.path_search:
            raise ValueError(
                f"path_search_result is only allowed on path_search "
                f"calculations (got type '{calculation.type.value}')."
            )
        _persist_path_search_result(
            session, calculation, calc_upload.path_search_result
        )

    if calc_upload.scf_stability is not None:
        scf = calc_upload.scf_stability
        session.add(
            CalculationSCFStability(
                calculation_id=calculation.id,
                status=scf.status,
                lowest_eigenvalue=scf.lowest_eigenvalue,
                instability_count=scf.instability_count,
                instability_type=scf.instability_type,
                reoptimized_wavefunction=scf.reoptimized_wavefunction,
                source_calculation_id=scf.source_calculation_id,
                source_artifact_id=scf.source_artifact_id,
                note=scf.note,
            )
        )

    if calc_upload.wavefunction_diagnostic is not None:
        wfn = calc_upload.wavefunction_diagnostic
        session.add(
            CalculationWavefunctionDiagnostic(
                calculation_id=calculation.id,
                t1_diagnostic=wfn.t1_diagnostic,
                d1_diagnostic=wfn.d1_diagnostic,
                t1_norm=wfn.t1_norm,
                largest_t2_amplitude=wfn.largest_t2_amplitude,
                note=wfn.note,
            )
        )

    if calc_upload.hessian is not None:
        hess = calc_upload.hessian
        # Bind the Hessian to the exact geometry it was computed at. The
        # content-addressed geometry seam dedupes by XYZ hash, so this
        # normally resolves to the same row as the calc's input geometry.
        hess_geom = resolve_geometry_payload(session, hess.geometry)
        session.add(
            CalculationHessian(
                calculation_id=calculation.id,
                geometry_id=hess_geom.id,
                natoms=hess_geom.natoms,
                lower_triangle_hartree_bohr2=hess.lower_triangle_hartree_bohr2,
                source=hess.source,
                parser_version=hess.parser_version,
                note=hess.note,
            )
        )

    for constraint in calc_upload.constraints:
        session.add(
            CalculationConstraint(
                calculation_id=calculation.id,
                constraint_index=constraint.constraint_index,
                constraint_kind=constraint.constraint_kind,
                atom1_index=constraint.atom1_index,
                atom2_index=constraint.atom2_index,
                atom3_index=constraint.atom3_index,
                atom4_index=constraint.atom4_index,
                target_value=constraint.target_value,
            )
        )


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
    source: ParameterSource = ParameterSource.upload,
    parser_version: str | None = None,
) -> list[CalculationParameter]:
    """Persist parsed execution-control parameters for a calculation.

    Writes one ``CalculationParameter`` row per supplied observation and,
    when provided, mirrors the parser snapshot/version/extracted-at fields
    onto the ``Calculation`` row. Canonical keys not present in
    ``calculation_parameter_vocab`` are silently demoted to ``NULL`` so the
    upload never fails on unmapped parameters; the raw key/value pair is
    always preserved.

    When ``source`` is :data:`ParameterSource.parser` the call performs
    true replace-all by deleting every existing row on this calculation
    that has ``source='parser'`` *before* inserting the new batch.
    Upload-supplied and curated rows are never touched. The deletion is
    intentionally not scoped by ``parser_version``: the table represents
    the current parsed snapshot, not parser history, so a new parse
    fully supersedes whatever the previous parser version wrote.

    :param session: Active SQLAlchemy session.
    :param calculation: The owning calculation row.
    :param observations: Parameter observations (may be ``None`` or empty).
        With ``source=parser``, an empty/None batch still triggers the
        replace-all delete so re-parsing a file that yields no recognised
        parameters clears any stale parser rows.
    :param parameters_json: Optional JSON snapshot from the parser to mirror
        onto ``calculation.parameters_json``.
    :param parameters_parser_version: Optional parser version tag mirrored
        onto ``calculation.parameters_parser_version``. Distinct from
        ``parser_version`` (per-row) so callers can record both, but most
        callers will pass the same string for both.
    :param parameters_extracted_at: Optional extraction timestamp.
    :param source: Provenance applied to every newly inserted row.
    :param parser_version: Per-row parser version tag, used only when
        ``source=parser``.
    :returns: Newly created parameter rows in the order they were supplied.
    """

    if parameters_json is not None:
        calculation.parameters_json = parameters_json
    if parameters_parser_version is not None:
        calculation.parameters_parser_version = parameters_parser_version
    if parameters_extracted_at is not None:
        calculation.parameters_extracted_at = parameters_extracted_at

    # Replace-all is bound to source=parser. Run the delete before the
    # early-return guard so an empty re-parse still clears stale rows.
    if source is ParameterSource.parser:
        _delete_parser_parameter_rows(session, calculation.id)

    if not observations:
        return []

    obs_list = list(observations)
    candidate_keys = {
        obs.canonical_key for obs in obs_list if obs.canonical_key is not None
    }
    known_keys = _resolve_canonical_keys(session, candidate_keys)

    row_parser_version = parser_version if source is ParameterSource.parser else None

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
            source=source,
            parser_version=row_parser_version,
        )
        session.add(row)
        rows.append(row)

    return rows


def _delete_parser_parameter_rows(session: Session, calculation_id: int) -> None:
    """Drop every parser-derived parameter row on this calculation.

    Pending in-session parser rows on this same calc are also expunged so
    a caller that re-parses twice in one transaction does not get a flush
    ordering surprise where the delete races the previous insert.
    """

    for obj in list(session.new):
        if (
            isinstance(obj, CalculationParameter)
            and obj.calculation_id == calculation_id
            and obj.source is ParameterSource.parser
        ):
            session.expunge(obj)

    session.query(CalculationParameter).filter(
        CalculationParameter.calculation_id == calculation_id,
        CalculationParameter.source == ParameterSource.parser,
    ).delete(synchronize_session=False)


# Mapping from calculation type to the dependency role when the child
# depends on the primary calculation.
#
# Path-search calculations are intentionally not in this table — the edge
# direction is inverted (path-search TS guess is the *parent* of the TS
# optimization, via ``optimized_from``). See ``_INVERTED_DEPENDENCY_ROLE_FOR_TYPE``.
_DEPENDENCY_ROLE_FOR_TYPE: dict[CalculationType, CalculationDependencyRole] = {
    CalculationType.freq: CalculationDependencyRole.freq_on,
    CalculationType.sp: CalculationDependencyRole.single_point_on,
    CalculationType.irc: CalculationDependencyRole.irc_start,
}


# Mapping from calculation type to the dependency role when the additional
# calculation is a *parent* of the primary calculation (edge inverted vs.
# ``_DEPENDENCY_ROLE_FOR_TYPE``). Path-search TS-guess generators (NEB,
# GSM, ...) live here: the path search runs first, produces the TS guess,
# and the primary opt is then ``optimized_from`` that guess.
_INVERTED_DEPENDENCY_ROLE_FOR_TYPE: dict[
    CalculationType, CalculationDependencyRole
] = {
    CalculationType.path_search: CalculationDependencyRole.optimized_from,
}


# Mapping from dependency role to the parent calculation's required type.
# Mirrors DR-0028 Requirement 1's compatibility table for the parent-side
# check on ``calculation_dependency`` rows. Roles not in the table (e.g.
# ``arkane_source``) are scientific metadata that does not pin a specific
# parent ``CalculationType`` and pass unconditionally.
_DEPENDENCY_ROLE_TO_PARENT_TYPE: dict[
    CalculationDependencyRole, CalculationType
] = {
    CalculationDependencyRole.freq_on: CalculationType.opt,
    CalculationDependencyRole.single_point_on: CalculationType.opt,
    CalculationDependencyRole.irc_start: CalculationType.opt,
    CalculationDependencyRole.scan_parent: CalculationType.opt,
    CalculationDependencyRole.irc_followup: CalculationType.irc,
}
# Note: ``optimized_from`` is intentionally *not* pinned to a single
# parent type. Its parent may be either ``opt`` (a previous geometry
# optimisation that the next opt restarts from) or ``path_search`` (a
# NEB/GSM TS-guess that feeds a TS optimisation). Validation is policy
# below at call sites instead of in this static table.


# Roles that enforce one-parent-per-child via partial unique indexes
# on ``calculation_dependency`` (see ``app/db/models/calculation.py``).
# A second edge with the same ``(child, role)`` from a different parent
# would be rejected by the DB at flush — callers surface it as a clean 422
# instead of letting the constraint raise ``IntegrityError``.
_ONE_PARENT_PER_CHILD_ROLES: frozenset[CalculationDependencyRole] = frozenset(
    {
        CalculationDependencyRole.optimized_from,
        CalculationDependencyRole.freq_on,
        CalculationDependencyRole.single_point_on,
        CalculationDependencyRole.scan_parent,
    }
)


_OPTIMIZED_FROM_PARENT_TYPES: frozenset[CalculationType] = frozenset(
    {CalculationType.opt, CalculationType.path_search}
)


def assert_dependency_role_type_compatible(
    parent_calc: Calculation,
    role: CalculationDependencyRole,
    *,
    context: str,
) -> None:
    """Verify the parent calculation's type is compatible with ``role``.

    Roles not present in ``_DEPENDENCY_ROLE_TO_PARENT_TYPE`` (e.g.
    ``arkane_source``) are scientific metadata that does not pin a specific
    parent ``CalculationType``; those are accepted unconditionally.
    ``optimized_from`` accepts a parent of either ``opt`` (restart-from)
    or ``path_search`` (TS-guess generator). Bundle workflows surface
    incompatibilities as 422 to mirror DR-0028 error semantics.
    """
    # Use ``==`` rather than ``is`` so wire-enum role values from
    # ``tckdb_schemas.enums`` (passed in via bundle workflows) compare
    # equal to the backend DB enum member. Two mirrored enum classes
    # share ``.value`` and ``__hash__`` but are distinct Python objects,
    # so ``is`` silently returned False here and skipped the
    # opt/path_search parent-type check for ``optimized_from`` edges.
    if role == CalculationDependencyRole.optimized_from:
        if parent_calc.type not in _OPTIMIZED_FROM_PARENT_TYPES:
            raise ValueError(
                f"{context}: role='optimized_from' requires a parent of "
                f"type 'opt' or 'path_search', got "
                f"'{parent_calc.type.value}'."
            )
        return
    expected = _DEPENDENCY_ROLE_TO_PARENT_TYPE.get(role)
    if expected is None:
        return
    if parent_calc.type != expected:
        raise ValueError(
            f"{context}: role='{role.value}' is incompatible with the "
            f"resolved parent calculation type."
        )


def add_dependency_edge_idempotent(
    session: Session,
    *,
    parent_calculation_id: int,
    child_calculation_id: int,
    dependency_role: CalculationDependencyRole,
    context: str,
) -> CalculationDependency:
    """Insert a ``CalculationDependency`` edge idempotently.

    Two distinct constraints are checked before the row is added — both
    in pending-in-session state and against already-persisted rows —
    so the helper never relies on the database to raise:

    1. **Composite PK** ``(parent_calculation_id, child_calculation_id)``:
       * same role → no-op, return the existing row.
       * different role → ``ValueError``.

    2. **Per-role child uniqueness** for the roles in
       ``_ONE_PARENT_PER_CHILD_ROLES``:
       * same parent (caught above as the PK case) → no-op.
       * different parent → ``ValueError``.

    Self-edges (``parent == child``) are rejected with ``ValueError`` — the
    DB also rejects them via a CHECK constraint, but this gives a clean 422.

    ``no_autoflush`` is used around the DB lookups so unrelated pending
    edges are not flushed mid-check and racing the duplicate-insert
    this helper exists to prevent.
    """
    if parent_calculation_id == child_calculation_id:
        raise ValueError(
            f"{context}: a calculation cannot depend on itself."
        )

    for obj in session.new:
        if not isinstance(obj, CalculationDependency):
            continue
        if (
            obj.parent_calculation_id == parent_calculation_id
            and obj.child_calculation_id == child_calculation_id
        ):
            if obj.dependency_role == dependency_role:
                return obj
            raise ValueError(
                f"{context}: a dependency edge between the same parent "
                f"and child is already pending in this transaction with "
                f"a different role='{obj.dependency_role.value}' "
                f"(requested role='{dependency_role.value}')."
            )
        if (
            dependency_role in _ONE_PARENT_PER_CHILD_ROLES
            and obj.dependency_role == dependency_role
            and obj.child_calculation_id == child_calculation_id
        ):
            raise ValueError(
                f"{context}: another dependency edge with role="
                f"'{dependency_role.value}' targeting the same child "
                f"calculation is already pending in this transaction "
                f"from a different parent. The schema permits at most "
                f"one '{dependency_role.value}' parent per child."
            )

    with session.no_autoflush:
        existing_pair = session.get(
            CalculationDependency,
            {
                "parent_calculation_id": parent_calculation_id,
                "child_calculation_id": child_calculation_id,
            },
        )
        existing_other_parent: CalculationDependency | None = None
        if (
            existing_pair is None
            and dependency_role in _ONE_PARENT_PER_CHILD_ROLES
        ):
            existing_other_parent = session.scalar(
                select(CalculationDependency).where(
                    CalculationDependency.child_calculation_id
                    == child_calculation_id,
                    CalculationDependency.dependency_role == dependency_role,
                )
            )

    if existing_pair is not None:
        if existing_pair.dependency_role == dependency_role:
            return existing_pair
        raise ValueError(
            f"{context}: a dependency edge between the same parent and "
            f"child already exists with a different role="
            f"'{existing_pair.dependency_role.value}' "
            f"(requested role='{dependency_role.value}')."
        )

    if existing_other_parent is not None:
        raise ValueError(
            f"{context}: another dependency edge with role="
            f"'{dependency_role.value}' targeting the same child "
            f"calculation already exists from a different parent. The "
            f"schema permits at most one '{dependency_role.value}' "
            f"parent per child."
        )

    edge = CalculationDependency(
        parent_calculation_id=parent_calculation_id,
        child_calculation_id=child_calculation_id,
        dependency_role=dependency_role,
    )
    session.add(edge)
    return edge

# Calculation types whose input geometry equals the conformer's optimized
# geometry (i.e. the geometry the calc actually ran on). For ``opt``, the
# true input is the pre-opt xyz (RDKit guess, restart-from, etc.), which
# the producer does not currently surface; populating opt's input row with
# the conformer geometry would be wrong (that's opt's output). Skipped here
# until the producer exposes the pre-opt geometry.
_INPUT_GEOMETRY_TYPES: frozenset[CalculationType] = frozenset(
    {CalculationType.freq, CalculationType.sp}
)

# Calculation types whose converged output IS the conformer geometry. Only
# ``opt`` qualifies: by construction the conformer geometry is opt's
# ``final`` output, so when a producer omits ``output_geometries`` we can
# safely synthesize that single row. Freq, sp, scan, irc, path_search and
# conf do NOT have a producer-agnostic universal mapping from calc type to
# a specific output geometry/role — the producer must declare explicitly.
_OUTPUT_GEOMETRY_TYPES: frozenset[CalculationType] = frozenset(
    {CalculationType.opt}
)


def attach_calculation_input_geometries(
    session: Session,
    *,
    calc: Calculation,
    explicit_input_geometries: list[GeometryPayload],
    fallback_geometry_id: int | None,
    context: str,
) -> None:
    """Attach ``calculation_input_geometry`` rows for one calc.

    Producer-explicit path: when ``explicit_input_geometries`` is
    non-empty, each payload is resolved and linked at
    ``input_order = 1, 2, 3, ...`` in list order. Two payloads that
    canonicalize to the same Geometry row would violate the table's
    ``UNIQUE (calculation_id, geometry_id)`` constraint; we surface
    that as a 422 (``ValueError``) before the insert rather than
    letting it bubble out as a generic ``IntegrityError``.

    Fallback path: when the list is empty, ``calc.type`` is in
    ``_INPUT_GEOMETRY_TYPES``, and ``fallback_geometry_id`` is provided,
    one row is added at ``input_order = 1`` pointing at that geometry
    (preserves the prior PR's freq+sp default).

    The two paths are mutually exclusive — declaring even one explicit
    geometry suppresses the fallback for that calc.
    """
    if explicit_input_geometries:
        seen_geometry_ids: set[int] = set()
        for input_order, geom_payload in enumerate(
            explicit_input_geometries, start=1
        ):
            geom = resolve_geometry_payload(session, geom_payload)
            if geom.id in seen_geometry_ids:
                raise ValueError(
                    f"{context}: input_geometries declares the same "
                    f"geometry more than once. The "
                    f"calculation_input_geometry table requires unique "
                    f"(calculation_id, geometry_id) pairs; declare each "
                    f"geometry at most once per calculation."
                )
            seen_geometry_ids.add(geom.id)
            session.add(
                CalculationInputGeometry(
                    calculation_id=calc.id,
                    geometry_id=geom.id,
                    input_order=input_order,
                )
            )
        return

    if fallback_geometry_id is not None and calc.type in _INPUT_GEOMETRY_TYPES:
        session.add(
            CalculationInputGeometry(
                calculation_id=calc.id,
                geometry_id=fallback_geometry_id,
                input_order=1,
            )
        )


def attach_calculation_output_geometries(
    session: Session,
    *,
    calc: Calculation,
    explicit_output_geometries: list[OutputGeometryEntry],
    fallback_geometry_id: int | None,
    context: str,
) -> None:
    """Attach ``calculation_output_geometry`` rows for one calc.

    Producer-explicit path: when ``explicit_output_geometries`` is
    non-empty, each entry is resolved and linked at
    ``output_order = 1, 2, 3, ...`` in list order with the
    producer-declared role. Two payloads that canonicalize to the same
    Geometry row would violate the table's
    ``UNIQUE (calculation_id, geometry_id)`` constraint; we surface that
    as a 422 (``ValueError``) before the insert rather than letting it
    bubble out as a generic ``IntegrityError``. Geometries already linked
    in the same session (e.g. by ``_persist_irc_result`` or
    ``_persist_path_search_result``) are also rejected with the same error.

    Fallback path: when the list is empty, ``calc.type`` is in
    ``_OUTPUT_GEOMETRY_TYPES``, and ``fallback_geometry_id`` is provided,
    one row is added at ``output_order = 1`` with role ``final``. Only
    ``opt`` qualifies — the conformer geometry IS opt's converged output
    by construction; any other type's output role would be a guess.

    The two paths are mutually exclusive — declaring even one explicit
    output geometry suppresses the fallback for that calc.
    """
    if explicit_output_geometries:
        already_linked = _pending_output_geometry_ids(session, calc.id)
        seen_geometry_ids: set[int] = set()
        for output_order, entry in enumerate(
            explicit_output_geometries, start=1
        ):
            geom = resolve_geometry_payload(session, entry.geometry)
            if geom.id in seen_geometry_ids or geom.id in already_linked:
                raise ValueError(
                    f"{context}: output_geometries declares the same "
                    f"geometry more than once. The "
                    f"calculation_output_geometry table requires unique "
                    f"(calculation_id, geometry_id) pairs; declare each "
                    f"geometry at most once per calculation."
                )
            seen_geometry_ids.add(geom.id)
            session.add(
                CalculationOutputGeometry(
                    calculation_id=calc.id,
                    geometry_id=geom.id,
                    output_order=output_order,
                    role=entry.role,
                )
            )
        return

    if fallback_geometry_id is not None and calc.type in _OUTPUT_GEOMETRY_TYPES:
        if fallback_geometry_id not in _pending_output_geometry_ids(
            session, calc.id
        ):
            session.add(
                CalculationOutputGeometry(
                    calculation_id=calc.id,
                    geometry_id=fallback_geometry_id,
                    output_order=1,
                    role=CalculationGeometryRole.final,
                )
            )


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

        # Producer-explicit output_geometries take precedence. Otherwise
        # the narrowed fallback only fires for opt (the one calc type
        # whose converged output IS the conformer geometry); freq, sp,
        # and all other types now produce zero output_geometry rows
        # unless the producer declares them explicitly.
        attach_calculation_output_geometries(
            session,
            calc=child_calc,
            explicit_output_geometries=calc_upload.output_geometries,
            fallback_geometry_id=geometry_id,
            context=(
                f"additional calculation (type='{calc_upload.type.value}', "
                f"id={child_calc.id})"
            ),
        )

        attach_calculation_input_geometries(
            session,
            calc=child_calc,
            explicit_input_geometries=calc_upload.input_geometries,
            fallback_geometry_id=geometry_id,
            context=(
                f"additional calculation (type='{calc_upload.type.value}', "
                f"id={child_calc.id})"
            ),
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

        # Inverted-edge case: path_search is a TS-guess generator, so the
        # primary opt is ``optimized_from`` the path search rather than the
        # other way around.
        inverted_role = _INVERTED_DEPENDENCY_ROLE_FOR_TYPE.get(calc_upload.type)
        if inverted_role is not None:
            session.add(
                CalculationDependency(
                    parent_calculation_id=child_calc.id,
                    child_calculation_id=primary_calc.id,
                    dependency_role=inverted_role,
                )
            )

        results.append(child_calc)

    session.flush()
    return results
