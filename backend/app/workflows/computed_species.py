"""Bundle workflow for ``POST /api/v1/uploads/computed-species`` (DR-0029).

Self-contained: identity + conformers + per-conformer calcs + artifacts +
optional thermo, persisted in one SQL transaction with bundle-level
artifact compensation. Local string keys are the only cross-references
inside the bundle — there are no DB FK ids in the request payload.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

import app.db.models  # noqa: F401
from app.chemistry.geometry import parse_xyz
from app.db.models.calculation import (
    Calculation,
    CalculationDependency,
    CalculationOutputGeometry,
)
from app.db.models.common import (
    CalculationDependencyRole,
    CalculationGeometryRole,
    CalculationType,
    ThermoCalculationRole,
)
from app.db.models.species import ConformerObservation
from app.db.models.thermo import Thermo
from app.schemas.entities.thermo import ThermoSourceCalculationCreate
from app.schemas.fragments.calculation import CalculationWithResultsPayload
from app.schemas.fragments.geometry import GeometryPayload
from app.schemas.workflows.computed_species_upload import (
    CalculationDependencyInBundle,
    CalculationInBundle,
    ComputedSpeciesUploadRequest,
    ConformerInBundle,
    ThermoInBundle,
)
from app.schemas.workflows.thermo_upload import ThermoUploadRequest
from app.services.artifact_persistence import (
    _compensate_stored_objects,
    persist_artifact_batch,
    validate_and_decode_all_artifacts,
)
from app.services.calculation_resolution import (
    resolve_and_persist_calculation_with_results,
)
from app.services.conformer_resolution import resolve_conformer_group
from app.services.energy_correction_resolution import create_applied_energy_correction
from app.services.geometry_resolution import resolve_geometry_payload
from app.services.literature_resolution import resolve_or_create_literature
from app.services.species_resolution import resolve_species_entry
from app.services.thermo_resolution import persist_thermo, resolve_thermo_upload


# ---------------------------------------------------------------------------
# Role/type compatibility tables (mirror DR-0028 helpers in workflows/thermo)
# ---------------------------------------------------------------------------

_THERMO_ROLE_TO_CALC_TYPE: dict[ThermoCalculationRole, CalculationType] = {
    ThermoCalculationRole.opt: CalculationType.opt,
    ThermoCalculationRole.freq: CalculationType.freq,
    ThermoCalculationRole.sp: CalculationType.sp,
}

# Mapping from dependency role to the parent calculation's required type.
# Mirrors DR-0028 Requirement 1's compatibility table for the
# parent-side check on ``calculation_dependency`` rows.
_DEPENDENCY_ROLE_TO_PARENT_TYPE: dict[
    CalculationDependencyRole, CalculationType
] = {
    CalculationDependencyRole.optimized_from: CalculationType.opt,
    CalculationDependencyRole.freq_on: CalculationType.opt,
    CalculationDependencyRole.single_point_on: CalculationType.opt,
    CalculationDependencyRole.irc_start: CalculationType.opt,
    CalculationDependencyRole.scan_parent: CalculationType.opt,
    CalculationDependencyRole.neb_parent: CalculationType.opt,
    CalculationDependencyRole.irc_followup: CalculationType.irc,
}


def _assert_dependency_role_type_compatible(
    parent_calc: Calculation,
    role: CalculationDependencyRole,
    *,
    context: str,
) -> None:
    """Verify the parent calculation's type is compatible with ``role``.

    Roles not present in the table (e.g. ``arkane_source``) are scientific
    metadata that does not pin a specific parent ``CalculationType``;
    those are accepted unconditionally. The bundle workflow surfaces
    incompatibilities as 422 to mirror DR-0028 error semantics.
    """
    expected = _DEPENDENCY_ROLE_TO_PARENT_TYPE.get(role)
    if expected is None:
        return
    if parent_calc.type != expected:
        raise ValueError(
            f"{context}: role='{role.value}' is incompatible with the "
            f"resolved parent calculation type."
        )


def _assert_thermo_role_type_compatible(
    calc: Calculation,
    role: ThermoCalculationRole,
    *,
    context: str,
) -> None:
    """Verify a thermo source calc's type is compatible with the role.

    Mirrors ``app.workflows.thermo._assert_calculation_role_compatible``.
    """
    expected = _THERMO_ROLE_TO_CALC_TYPE.get(role)
    if expected is None:
        return
    if calc.type != expected:
        raise ValueError(
            f"{context}: role='{role.value}' is incompatible with the "
            f"resolved calculation type."
        )


# ---------------------------------------------------------------------------
# Outcome dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ConformerUploadOutcomeInBundle:
    conformer_in_bundle: ConformerInBundle
    observation: ConformerObservation
    group_id: int
    primary_calculation: Calculation
    additional_calculations: list[Calculation] = field(default_factory=list)


@dataclass
class ComputedSpeciesUploadOutcome:
    species_entry_id: int
    conformers: list[ConformerUploadOutcomeInBundle]
    thermo: Thermo | None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_calc_with_results_payload(
    calc_in: CalculationInBundle,
    *,
    literature_id: int | None,
) -> CalculationWithResultsPayload:
    """Build the existing primitive payload from a bundle calc block.

    Drops bundle-only fields (``key``, ``depends_on``, ``artifacts``,
    inline ``literature``) and substitutes the resolved ``literature_id``
    so the existing ``resolve_and_persist_calculation_with_results``
    service can be reused unchanged.
    """
    return CalculationWithResultsPayload(
        type=calc_in.type,
        quality=calc_in.quality,
        software_release=calc_in.software_release,
        workflow_tool_release=calc_in.workflow_tool_release,
        level_of_theory=calc_in.level_of_theory,
        literature_id=literature_id,
        opt_result=calc_in.opt_result,
        freq_result=calc_in.freq_result,
        sp_result=calc_in.sp_result,
        irc_result=calc_in.irc_result,
        neb_result=calc_in.neb_result,
        parameters=calc_in.parameters,
        parameters_json=calc_in.parameters_json,
        parameters_parser_version=calc_in.parameters_parser_version,
        parameters_extracted_at=calc_in.parameters_extracted_at,
    )


def _resolve_inline_literature_id(
    session: Session, calc_in: CalculationInBundle
) -> int | None:
    if calc_in.literature is None:
        return None
    lit = resolve_or_create_literature(session, calc_in.literature)
    return lit.id


def _build_synthetic_thermo_upload_request(
    thermo_in: ThermoInBundle,
    *,
    species_entry_payload,
) -> ThermoUploadRequest:
    """Construct a ``ThermoUploadRequest`` from the bundle's thermo block.

    The bundle's ``ThermoInBundle`` shape is intentionally a strict
    subset of ``ThermoUploadRequest`` (no inline ``calculations`` /
    ``source_calculations``) — those resolve from the bundle's calc-key
    namespace separately. The synthetic request is fed to
    ``resolve_thermo_upload`` to pick up provenance resolution for free.
    """
    return ThermoUploadRequest(
        species_entry=species_entry_payload,
        scientific_origin=thermo_in.scientific_origin,
        literature=thermo_in.literature,
        software_release=thermo_in.software_release,
        workflow_tool_release=thermo_in.workflow_tool_release,
        h298_kj_mol=thermo_in.h298_kj_mol,
        s298_j_mol_k=thermo_in.s298_j_mol_k,
        h298_uncertainty_kj_mol=thermo_in.h298_uncertainty_kj_mol,
        s298_uncertainty_j_mol_k=thermo_in.s298_uncertainty_j_mol_k,
        tmin_k=thermo_in.tmin_k,
        tmax_k=thermo_in.tmax_k,
        note=thermo_in.note,
        points=thermo_in.points,
        nasa=thermo_in.nasa,
        calculations=[],
        source_calculations=[],
        applied_energy_corrections=[],
    )


# ---------------------------------------------------------------------------
# Main workflow
# ---------------------------------------------------------------------------


def persist_computed_species_upload(
    session: Session,
    request: ComputedSpeciesUploadRequest,
    *,
    created_by: int | None = None,
) -> ComputedSpeciesUploadOutcome:
    """Persist a complete computed-species bundle.

    Order:
      1. Pass 1 — decode + validate every artifact across the bundle
         in memory, before any DB or S3 write.
      2. Resolve the species entry.
      3. Per conformer: resolve geometry + conformer group + create the
         observation row.
      4. Per conformer: persist the primary calculation (type=opt) and
         any additional calculations; auto-edges to primary fire as
         usual via ``resolve_and_persist_calculation_with_results`` /
         ``persist_additional_calculations``-equivalent logic.
      5. Resolve every ``depends_on`` edge by local key; insert
         non-duplicate ``CalculationDependency`` rows.
      6. Persist artifacts per calc using ``persist_artifact_batch`` and
         accumulate stored shas across the whole bundle for cross-step
         compensation.
      7. If ``thermo`` provided: build the synthetic ThermoUploadRequest,
         resolve and splice in resolved source calc links, persist.
      8. If ``thermo.applied_energy_corrections`` non-empty: resolve each
         ``source_calculation_key`` and persist the applied row.
      9. Final ``session.flush()``.
    """
    # Pass 1: decode + validate artifacts before any DB or S3 write.
    all_artifacts = []
    for conf in request.conformers:
        all_artifacts.extend(conf.primary_calculation.artifacts)
        for calc_in in conf.additional_calculations:
            all_artifacts.extend(calc_in.artifacts)
    validate_and_decode_all_artifacts(all_artifacts)

    # Step 2: resolve the species entry.
    species_entry = resolve_species_entry(
        session,
        request.species_entry,
        created_by=created_by,
        xyz_text=(
            request.conformers[0].geometry.xyz_text if request.conformers else None
        ),
    )

    # Step 3: per conformer, resolve geometry + group + observation.
    conformer_outcomes: list[ConformerUploadOutcomeInBundle] = []
    for conf_in in request.conformers:
        geometry = resolve_geometry_payload(session, conf_in.geometry)

        parsed = parse_xyz(GeometryPayload(xyz_text=conf_in.geometry.xyz_text))
        conformer_group, fingerprint, scheme = resolve_conformer_group(
            session,
            species_entry,
            label=conf_in.label,
            created_by=created_by,
            smiles=request.species_entry.smiles,
            xyz_atoms=parsed.atoms,
        )
        from app.db.models.common import ScientificOriginKind

        observation = ConformerObservation(
            conformer_group_id=conformer_group.id,
            scientific_origin=ScientificOriginKind.computed,
            note=conf_in.note,
            created_by=created_by,
            assignment_scheme_id=scheme.id if scheme is not None else None,
            torsion_fingerprint_json=fingerprint.to_dict()
            if fingerprint is not None
            else None,
        )
        session.add(observation)
        session.flush()

        # Step 4: primary opt + additionals. We replicate the
        # /uploads/conformers anchor-and-link logic here because the
        # bundle wraps multiple conformers in one transaction.
        primary_lit_id = _resolve_inline_literature_id(
            session, conf_in.primary_calculation
        )
        primary_calc = resolve_and_persist_calculation_with_results(
            session,
            _to_calc_with_results_payload(
                conf_in.primary_calculation, literature_id=primary_lit_id
            ),
            species_entry_id=species_entry.id,
            created_by=created_by,
        )
        primary_calc.conformer_observation_id = observation.id
        session.add(
            CalculationOutputGeometry(
                calculation_id=primary_calc.id,
                geometry_id=geometry.id,
                output_order=1,
                role=CalculationGeometryRole.final,
            )
        )

        # FOLLOW-UP (DR-0029): the additional-calc anchor logic below
        # (output-geometry link + auto-edge to primary opt) duplicates
        # ``app.services.calculation_resolution.persist_additional_calculations``.
        # Inline here because the bundle needs to thread observation_id
        # and run before its own ``session.flush()``, which the existing
        # service does internally and would force a different ordering.
        # Refactor target: extract a shared "attach-additional" helper
        # that takes ``observation_id`` and returns the row without
        # flushing, then have both the bundle and the primitive
        # ``/uploads/conformers`` workflows call it. Tracked separately;
        # acceptable as v0 inline duplication.
        additional_calcs: list[Calculation] = []
        for additional_in in conf_in.additional_calculations:
            child_lit_id = _resolve_inline_literature_id(session, additional_in)
            child_calc = resolve_and_persist_calculation_with_results(
                session,
                _to_calc_with_results_payload(
                    additional_in, literature_id=child_lit_id
                ),
                species_entry_id=species_entry.id,
                created_by=created_by,
            )
            child_calc.conformer_observation_id = observation.id

            # Anchor the additional calc to the same final geometry (the
            # uniqueness guard in calculation_resolution covers result
            # blocks that already linked it).
            from app.services.calculation_resolution import (
                _pending_output_geometry_ids,
            )

            if geometry.id not in _pending_output_geometry_ids(
                session, child_calc.id
            ):
                session.add(
                    CalculationOutputGeometry(
                        calculation_id=child_calc.id,
                        geometry_id=geometry.id,
                        output_order=1,
                        role=CalculationGeometryRole.final,
                    )
                )

            # Auto-edge to primary opt when the additional type maps to
            # a known dependency role (mirrors persist_additional_calculations).
            from app.services.calculation_resolution import (
                _DEPENDENCY_ROLE_FOR_TYPE,
            )

            dep_role = _DEPENDENCY_ROLE_FOR_TYPE.get(additional_in.type)
            if dep_role is not None:
                session.add(
                    CalculationDependency(
                        parent_calculation_id=primary_calc.id,
                        child_calculation_id=child_calc.id,
                        dependency_role=dep_role,
                    )
                )

            additional_calcs.append(child_calc)

        session.flush()

        conformer_outcomes.append(
            ConformerUploadOutcomeInBundle(
                conformer_in_bundle=conf_in,
                observation=observation,
                group_id=conformer_group.id,
                primary_calculation=primary_calc,
                additional_calculations=additional_calcs,
            )
        )

    # Build the local-key → Calculation map for cross-references.
    calc_keys_to_id: dict[str, Calculation] = {}
    for outcome in conformer_outcomes:
        calc_keys_to_id[outcome.conformer_in_bundle.primary_calculation.key] = (
            outcome.primary_calculation
        )
        for additional_in, calc_row in zip(
            outcome.conformer_in_bundle.additional_calculations,
            outcome.additional_calculations,
            strict=True,
        ):
            calc_keys_to_id[additional_in.key] = calc_row

    # Step 5: explicit dependency edges. Skip pairs that would duplicate
    # an existing (parent, child, role) row already added by the
    # auto-edge logic above.
    pending_edges: set[tuple[int, int, CalculationDependencyRole]] = {
        (e.parent_calculation_id, e.child_calculation_id, e.dependency_role)
        for e in session.new
        if isinstance(e, CalculationDependency)
    }
    for outcome in conformer_outcomes:
        for child_in, child_calc in (
            (
                outcome.conformer_in_bundle.primary_calculation,
                outcome.primary_calculation,
            ),
            *zip(
                outcome.conformer_in_bundle.additional_calculations,
                outcome.additional_calculations,
                strict=True,
            ),
        ):
            for dep in child_in.depends_on:
                parent_calc = calc_keys_to_id[dep.parent_calculation_key]
                _assert_dependency_role_type_compatible(
                    parent_calc,
                    dep.role,
                    context=(
                        f"calculation '{child_in.key}'.depends_on "
                        f"parent='{dep.parent_calculation_key}'"
                    ),
                )
                edge_key = (parent_calc.id, child_calc.id, dep.role)
                if edge_key in pending_edges:
                    continue
                session.add(
                    CalculationDependency(
                        parent_calculation_id=parent_calc.id,
                        child_calculation_id=child_calc.id,
                        dependency_role=dep.role,
                    )
                )
                pending_edges.add(edge_key)

    session.flush()

    # Step 6: artifacts. Cross-batch compensation tracks all stored
    # shas across all calcs in the bundle so a post-step-6 failure can
    # delete them.
    bundle_stored_shas: list[str] = []
    try:
        for outcome in conformer_outcomes:
            for calc_in, calc_row in (
                (
                    outcome.conformer_in_bundle.primary_calculation,
                    outcome.primary_calculation,
                ),
                *zip(
                    outcome.conformer_in_bundle.additional_calculations,
                    outcome.additional_calculations,
                    strict=True,
                ),
            ):
                if not calc_in.artifacts:
                    continue
                rows = persist_artifact_batch(
                    session,
                    calculation_id=calc_row.id,
                    artifacts=calc_in.artifacts,
                    created_by=created_by,
                )
                bundle_stored_shas.extend(r.sha256 for r in rows if r.sha256)

        thermo_row = _persist_thermo_block(
            session,
            request,
            species_entry_id=species_entry.id,
            calc_keys_to_id=calc_keys_to_id,
            created_by=created_by,
        )

        session.flush()
    except Exception:
        # SQL rollback is the route's job; clean up cross-batch S3
        # leakage here so a failure mid-bundle does not leave orphan
        # bytes behind.
        _compensate_stored_objects(bundle_stored_shas)
        raise

    return ComputedSpeciesUploadOutcome(
        species_entry_id=species_entry.id,
        conformers=conformer_outcomes,
        thermo=thermo_row,
    )


def _persist_thermo_block(
    session: Session,
    request: ComputedSpeciesUploadRequest,
    *,
    species_entry_id: int,
    calc_keys_to_id: dict[str, Calculation],
    created_by: int | None,
) -> Thermo | None:
    if request.thermo is None:
        return None

    thermo_in = request.thermo

    # Resolve source_calculations by local key with role/type checks.
    resolved_sources: list[ThermoSourceCalculationCreate] = []
    for sc in thermo_in.source_calculations:
        calc_row = calc_keys_to_id[sc.calculation_key]
        if calc_row.species_entry_id != species_entry_id:
            raise ValueError(
                f"thermo.source_calculations calculation_key="
                f"'{sc.calculation_key}': refers to a calculation owned "
                f"by a different species entry."
            )
        _assert_thermo_role_type_compatible(
            calc_row,
            sc.role,
            context=(
                f"thermo.source_calculations calculation_key="
                f"'{sc.calculation_key}'"
            ),
        )
        resolved_sources.append(
            ThermoSourceCalculationCreate(
                calculation_id=calc_row.id,
                role=sc.role,
            )
        )

    synthetic = _build_synthetic_thermo_upload_request(
        thermo_in, species_entry_payload=request.species_entry
    )
    thermo_create = resolve_thermo_upload(
        session, synthetic, species_entry_id=species_entry_id
    )
    thermo_create = thermo_create.model_copy(
        update={"source_calculations": resolved_sources}
    )
    thermo_row = persist_thermo(session, thermo_create, created_by=created_by)

    # Step 8: applied energy corrections — resolve each
    # source_calculation_key by the bundle's global namespace, validate
    # owner-consistency, and persist.
    for i, ac in enumerate(thermo_in.applied_energy_corrections):
        source_calc_id: int | None = None
        if ac.source_calculation_key is not None:
            calc_row = calc_keys_to_id[ac.source_calculation_key]
            if calc_row.species_entry_id != species_entry_id:
                raise ValueError(
                    f"thermo.applied_energy_corrections[{i}]."
                    f"source_calculation_key='{ac.source_calculation_key}': "
                    f"refers to a calculation owned by a different species entry."
                )
            source_calc_id = calc_row.id

        create_applied_energy_correction(
            session,
            ac,
            target_species_entry_id=species_entry_id,
            source_calculation_id=source_calc_id,
            created_by=created_by,
        )

    return thermo_row
