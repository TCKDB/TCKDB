"""Bundle workflow for ``POST /api/v1/uploads/computed-species`` (DR-0029).

Self-contained: identity + conformers + per-conformer calcs + artifacts +
optional thermo, persisted in one SQL transaction with bundle-level
artifact compensation. Local string keys are the only cross-references
inside the bundle — there are no DB FK ids in the request payload.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session
from tckdb_schemas.upload_warning import UploadWarning

from app.chemistry.geometry import parse_xyz
from app.db.models.calculation import Calculation
from app.db.models.common import (
    CalculationType,
    ScientificOriginKind,
    SubmissionRecordType,
    ThermoCalculationRole,
)
from app.db.models.species import ConformerObservation
from app.db.models.statmech import (
    Statmech,
    StatmechSourceCalculation,
    StatmechTorsion,
    StatmechTorsionDefinition,
)
from app.db.models.thermo import Thermo
from app.schemas.entities.thermo import ThermoSourceCalculationCreate
from app.schemas.fragments.calculation import CalculationWithResultsPayload
from app.schemas.fragments.geometry import GeometryPayload
from app.schemas.workflows.computed_species_upload import (
    CalculationInBundle,
    ComputedSpeciesUploadRequest,
    ConformerInBundle,
    StatmechInBundle,
    ThermoInBundle,
)
from app.schemas.workflows.thermo_upload import ThermoUploadRequest
from app.services.artifact_persistence import (
    _compensate_stored_objects,
    persist_artifact_batch,
    validate_and_decode_all_artifacts,
)
from app.services.calculation_parameter_extraction import (
    try_extract_parameters_from_input_upload,
)
from app.services.calculation_resolution import (
    _DEPENDENCY_ROLE_FOR_TYPE,
    _INVERTED_DEPENDENCY_ROLE_FOR_TYPE,
    add_dependency_edge_idempotent,
    assert_dependency_role_type_compatible,
    attach_calculation_input_geometries,
    attach_calculation_output_geometries,
    resolve_and_persist_calculation_with_results,
    resolve_software_release_ref,
    resolve_workflow_tool_release_ref,
)
from app.services.calculation_scan_resolution import persist_calculation_scan
from app.services.conformer_resolution import resolve_conformer_group
from app.services.energy_correction_resolution import (
    create_applied_energy_correction,
    resolve_or_create_freq_scale_factor_ref,
)
from app.services.geometry_resolution import resolve_geometry_payload
from app.services.geometry_validation import run_and_persist_geometry_validation
from app.services.hessian_extraction import (
    try_extract_hessian_from_artifact_upload,
)
from app.services.literature_resolution import resolve_or_create_literature
from app.services.record_review import (
    RecordRef,
    ReviewPolicy,
    apply_review_policy,
)
from app.services.sp_energy_extraction import (
    try_reconcile_sp_energy_from_output_upload,
)
from app.services.species_resolution import resolve_species_entry
from app.services.thermo_resolution import persist_thermo, resolve_thermo_upload

# ---------------------------------------------------------------------------
# Thermo role/type compatibility (mirror DR-0028 helpers in workflows/thermo)
# ---------------------------------------------------------------------------

_THERMO_ROLE_TO_CALC_TYPE: dict[ThermoCalculationRole, CalculationType] = {
    ThermoCalculationRole.opt: CalculationType.opt,
    ThermoCalculationRole.freq: CalculationType.freq,
    ThermoCalculationRole.sp: CalculationType.sp,
}


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
    statmech: Statmech | None = None
    #: Non-blocking warnings raised while persisting inline artifacts —
    #: currently single-point energy reconciliation (fill/mismatch). The
    #: route merges these into the upload response.
    warnings: list[UploadWarning] = field(default_factory=list)


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
        path_search_result=calc_in.path_search_result,
        wavefunction_diagnostic=calc_in.wavefunction_diagnostic,
        spin_diagnostic=calc_in.spin_diagnostic,
        hessian=calc_in.hessian,
        input_geometries=calc_in.input_geometries,
        output_geometries=calc_in.output_geometries,
        parameters=calc_in.parameters,
        parameters_json=calc_in.parameters_json,
        parameters_parser_version=calc_in.parameters_parser_version,
        parameters_extracted_at=calc_in.parameters_extracted_at,
        constraints=calc_in.constraints,
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
    review_policy: ReviewPolicy | None = ReviewPolicy(),
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
        # Producer-explicit output_geometries take precedence. Otherwise
        # the narrowed fallback only fires for opt (the one calc type
        # whose converged output IS the conformer geometry); freq, sp,
        # and all other types now produce zero output_geometry rows
        # unless the producer declares them explicitly. Bundle's primary
        # calc is required to be type=opt so this fallback always fires
        # for the primary slot.
        attach_calculation_output_geometries(
            session,
            calc=primary_calc,
            explicit_output_geometries=conf_in.primary_calculation.output_geometries,
            fallback_geometry_id=geometry.id,
            context=(
                f"calculation '{conf_in.primary_calculation.key}' "
                f"(type='{primary_calc.type.value}')"
            ),
        )
        # Producer-explicit input_geometries take precedence; otherwise
        # the freq/sp fallback links the conformer geometry. opt skips
        # the fallback (its real input is the pre-opt xyz, not the
        # conformer geometry) and only gets a row when the producer
        # declares one.
        attach_calculation_input_geometries(
            session,
            calc=primary_calc,
            explicit_input_geometries=conf_in.primary_calculation.input_geometries,
            fallback_geometry_id=geometry.id,
            context=(
                f"calculation '{conf_in.primary_calculation.key}' "
                f"(type='{primary_calc.type.value}')"
            ),
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

            # Producer-explicit output_geometries take precedence. The
            # fallback only fires for opt; freq, sp, and all other types
            # produce zero output_geometry rows unless the producer
            # declares them explicitly.
            attach_calculation_output_geometries(
                session,
                calc=child_calc,
                explicit_output_geometries=additional_in.output_geometries,
                fallback_geometry_id=geometry.id,
                context=(
                    f"calculation '{additional_in.key}' "
                    f"(type='{additional_in.type.value}')"
                ),
            )

            # Producer-explicit input_geometries take precedence; the
            # fallback links the conformer geometry only for freq/sp.
            attach_calculation_input_geometries(
                session,
                calc=child_calc,
                explicit_input_geometries=additional_in.input_geometries,
                fallback_geometry_id=geometry.id,
                context=(
                    f"calculation '{additional_in.key}' "
                    f"(type='{additional_in.type.value}')"
                ),
            )

            # Persist scan_result for type=scan calcs after the calc row
            # exists. The schema layer guarantees scan_result is only
            # present for type=scan; the primary calc is type=opt by
            # construction so scan only ever rides as an additional.
            if (
                additional_in.type == CalculationType.scan
                and additional_in.scan_result is not None
            ):
                persist_calculation_scan(
                    session, child_calc.id, additional_in.scan_result
                )

            # Auto-edge to primary opt when the additional type maps to
            # a known dependency role (mirrors persist_additional_calculations).
            dep_role = _DEPENDENCY_ROLE_FOR_TYPE.get(additional_in.type)
            if dep_role is not None:
                add_dependency_edge_idempotent(
                    session,
                    parent_calculation_id=primary_calc.id,
                    child_calculation_id=child_calc.id,
                    dependency_role=dep_role,
                    context=(
                        f"auto-dependency for calculation '{additional_in.key}' "
                        f"(role='{dep_role.value}')"
                    ),
                )

            # Inverted-edge case: path_search TS-guess is the parent of
            # the primary opt (optimized_from), not the other way around.
            inverted_role = _INVERTED_DEPENDENCY_ROLE_FOR_TYPE.get(
                additional_in.type
            )
            if inverted_role is not None:
                add_dependency_edge_idempotent(
                    session,
                    parent_calculation_id=child_calc.id,
                    child_calculation_id=primary_calc.id,
                    dependency_role=inverted_role,
                    context=(
                        f"auto-dependency for calculation '{additional_in.key}' "
                        f"(role='{inverted_role.value}', inverted)"
                    ),
                )

            additional_calcs.append(child_calc)

        session.flush()

        # Phase-1 geometry-identity validation. Best-effort: opt calcs
        # only, skips when output geometry / SMILES is unavailable, and
        # never aborts the upload. A failed/warned result is recorded as
        # evidence; it does NOT gate persistence of the calculation.
        run_and_persist_geometry_validation(
            session,
            primary_calc,
            species_smiles=request.species_entry.smiles,
        )
        for child_calc in additional_calcs:
            run_and_persist_geometry_validation(
                session,
                child_calc,
                species_smiles=request.species_entry.smiles,
            )

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

    # Step 5: explicit dependency edges. The idempotent helper handles
    # both same-transaction and already-persisted duplicates, and rejects
    # role mismatches with a clear 422.
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
                context = (
                    f"calculation '{child_in.key}'.depends_on "
                    f"parent='{dep.parent_calculation_key}'"
                )
                assert_dependency_role_type_compatible(
                    parent_calc, dep.role, context=context
                )
                add_dependency_edge_idempotent(
                    session,
                    parent_calculation_id=parent_calc.id,
                    child_calculation_id=child_calc.id,
                    dependency_role=dep.role,
                    context=context,
                )

    session.flush()

    # Step 6: artifacts. Cross-batch compensation tracks all stored
    # shas across all calcs in the bundle so a post-step-6 failure can
    # delete them.
    bundle_stored_shas: list[str] = []
    sp_energy_warnings: list[UploadWarning] = []
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
                # Opportunistic per-artifact extraction, both best-effort —
                # never abort the bundle. Input artifacts yield parameter
                # rows; output logs reconcile the single-point energy
                # against the tool's reported value (fill/mismatch), the
                # same as the standalone artifacts route.
                for art_in in calc_in.artifacts:
                    try_extract_parameters_from_input_upload(
                        session, calc_row, art_in
                    )
                    sp_warning = try_reconcile_sp_energy_from_output_upload(
                        session, calc_row, art_in
                    )
                    if sp_warning is not None:
                        sp_energy_warnings.append(sp_warning)
                    # Input geometries for this calc were attached in an
                    # earlier pass, so the Hessian can bind to them here.
                    try_extract_hessian_from_artifact_upload(
                        session, calc_row, art_in
                    )

        thermo_row, thermo_aec_ids = _persist_thermo_block(
            session,
            request,
            species_entry_id=species_entry.id,
            calc_keys_to_id=calc_keys_to_id,
            created_by=created_by,
        )

        statmech_row = _persist_statmech_block(
            session,
            request.statmech,
            species_entry_id=species_entry.id,
            calc_keys_to_id=calc_keys_to_id,
            created_by=created_by,
        )

        # Link a bundle-created COMPUTED thermo to the statmech it was
        # derived from (same species entry). Without this, the read layer
        # falls back to min(statmech_id) when a species entry has multiple
        # statmech rows. Only computed thermo is linked; experimental,
        # literature, or group-additivity thermo keeps statmech_id NULL.
        if (
            thermo_row is not None
            and statmech_row is not None
            and thermo_row.statmech_id is None
            and thermo_row.scientific_origin == ScientificOriginKind.computed
        ):
            thermo_row.statmech_id = statmech_row.id
            session.flush()

        top_level_aec_ids = _persist_top_level_applied_corrections(
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

    review_targets: list[RecordRef] = [
        RecordRef(SubmissionRecordType.species_entry, species_entry.id),
    ]
    for outcome in conformer_outcomes:
        review_targets.append(
            RecordRef(SubmissionRecordType.conformer_group, outcome.group_id)
        )
        review_targets.append(
            RecordRef(
                SubmissionRecordType.conformer_observation,
                outcome.observation.id,
            )
        )
        review_targets.append(
            RecordRef(
                SubmissionRecordType.calculation,
                outcome.primary_calculation.id,
            )
        )
        review_targets.extend(
            RecordRef(SubmissionRecordType.calculation, c.id)
            for c in outcome.additional_calculations
        )
    if thermo_row is not None:
        review_targets.append(
            RecordRef(SubmissionRecordType.thermo, thermo_row.id)
        )
    if statmech_row is not None:
        review_targets.append(
            RecordRef(SubmissionRecordType.statmech, statmech_row.id)
        )
    review_targets.extend(
        RecordRef(SubmissionRecordType.applied_energy_correction, aec_id)
        for aec_id in (*thermo_aec_ids, *top_level_aec_ids)
    )
    apply_review_policy(
        session,
        targets=review_targets,
        policy=review_policy,
        created_by=created_by,
    )

    return ComputedSpeciesUploadOutcome(
        species_entry_id=species_entry.id,
        conformers=conformer_outcomes,
        thermo=thermo_row,
        statmech=statmech_row,
        warnings=sp_energy_warnings,
    )


def _persist_thermo_block(
    session: Session,
    request: ComputedSpeciesUploadRequest,
    *,
    species_entry_id: int,
    calc_keys_to_id: dict[str, Calculation],
    created_by: int | None,
) -> tuple[Thermo | None, list[int]]:
    """Persist optional thermo + nested AECs.

    Returns ``(thermo_row | None, applied_correction_ids)`` so the caller
    can record review state for both the thermo row and each AEC row.
    """
    if request.thermo is None:
        return None, []

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
    applied_correction_ids: list[int] = []
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

        applied = create_applied_energy_correction(
            session,
            ac,
            target_species_entry_id=species_entry_id,
            source_calculation_id=source_calc_id,
            created_by=created_by,
        )
        applied_correction_ids.append(applied.id)

    return thermo_row, applied_correction_ids


def _persist_top_level_applied_corrections(
    session: Session,
    request: ComputedSpeciesUploadRequest,
    *,
    species_entry_id: int,
    calc_keys_to_id: dict[str, Calculation],
    created_by: int | None,
) -> list[int]:
    """Persist bundle-level applied energy corrections (AEC/BAC).

    Top-level applied corrections target the bundle's species entry.
    Each ``source_calculation_key`` is resolved against the bundle's
    global calc-key namespace and verified to belong to the same
    species entry; the row + optional component breakdown are written
    via the shared ``create_applied_energy_correction`` service.

    Returns the list of created AEC ids so the caller can record review
    state for each one.
    """
    if not request.applied_energy_corrections:
        return []

    applied_correction_ids: list[int] = []
    for i, ac in enumerate(request.applied_energy_corrections):
        source_calc_id: int | None = None
        if ac.source_calculation_key is not None:
            calc_row = calc_keys_to_id[ac.source_calculation_key]
            if calc_row.species_entry_id != species_entry_id:
                raise ValueError(
                    f"applied_energy_corrections[{i}]."
                    f"source_calculation_key='{ac.source_calculation_key}': "
                    f"refers to a calculation owned by a different species entry."
                )
            source_calc_id = calc_row.id

        applied = create_applied_energy_correction(
            session,
            ac,
            target_species_entry_id=species_entry_id,
            source_calculation_id=source_calc_id,
            created_by=created_by,
        )
        applied_correction_ids.append(applied.id)
    return applied_correction_ids


def _persist_statmech_block(
    session: Session,
    statmech: StatmechInBundle | None,
    *,
    species_entry_id: int,
    calc_keys_to_id: dict[str, Calculation],
    created_by: int | None,
) -> Statmech | None:
    """Persist an optional statmech block.

    Shared seam consumed by both the computed-species bundle workflow and
    the pressure-dependent network workflow. The frequency scale factor is
    resolved through the unified ``resolve_or_create_freq_scale_factor_ref``
    and linked through ``statmech.frequency_scale_factor_id``. Source
    calculations are resolved against the caller's global calc-key namespace
    and written as ``StatmechSourceCalculation`` rows; an applied
    energy-correction row is never produced for FSF here.
    """
    if statmech is None:
        return None

    s: StatmechInBundle = statmech

    literature = (
        resolve_or_create_literature(session, s.literature)
        if s.literature is not None
        else None
    )
    software_release = (
        resolve_software_release_ref(session, s.software_release)
        if s.software_release is not None
        else None
    )
    workflow_tool_release = (
        resolve_workflow_tool_release_ref(session, s.workflow_tool_release)
        if s.workflow_tool_release is not None
        else None
    )

    fsf_id: int | None = None
    if s.freq_scale_factor is not None:
        fsf = resolve_or_create_freq_scale_factor_ref(
            session, s.freq_scale_factor, created_by=created_by
        )
        fsf_id = fsf.id

    statmech = Statmech(
        species_entry_id=species_entry_id,
        scientific_origin=s.scientific_origin,
        literature_id=literature.id if literature is not None else None,
        software_release_id=(
            software_release.id if software_release is not None else None
        ),
        workflow_tool_release_id=(
            workflow_tool_release.id if workflow_tool_release is not None else None
        ),
        external_symmetry=s.external_symmetry,
        optical_isomers=s.optical_isomers,
        point_group=s.point_group,
        is_linear=s.is_linear,
        rigid_rotor_kind=s.rigid_rotor_kind,
        statmech_treatment=s.statmech_treatment,
        rotational_constant_a_cm1=s.rotational_constant_a_cm1,
        rotational_constant_b_cm1=s.rotational_constant_b_cm1,
        rotational_constant_c_cm1=s.rotational_constant_c_cm1,
        frequency_scale_factor_id=fsf_id,
        uses_projected_frequencies=s.uses_projected_frequencies,
        note=s.note,
        created_by=created_by,
    )
    session.add(statmech)
    session.flush()

    for sc in s.source_calculations:
        calc_row = calc_keys_to_id[sc.calculation_key]
        if calc_row.species_entry_id != species_entry_id:
            raise ValueError(
                f"statmech.source_calculations calculation_key="
                f"'{sc.calculation_key}': refers to a calculation owned "
                f"by a different species entry."
            )
        session.add(
            StatmechSourceCalculation(
                statmech_id=statmech.id,
                calculation_id=calc_row.id,
                role=sc.role,
            )
        )

    for torsion_in in s.torsions:
        scan_calc_id: int | None = None
        if torsion_in.source_scan_calculation_key is not None:
            scan_calc_row = calc_keys_to_id[torsion_in.source_scan_calculation_key]
            if scan_calc_row.species_entry_id != species_entry_id:
                raise ValueError(
                    f"statmech.torsions source_scan_calculation_key="
                    f"'{torsion_in.source_scan_calculation_key}': refers to a "
                    f"calculation owned by a different species entry."
                )
            scan_calc_id = scan_calc_row.id

        torsion = StatmechTorsion(
            statmech_id=statmech.id,
            torsion_index=torsion_in.torsion_index,
            symmetry_number=torsion_in.symmetry_number,
            treatment_kind=torsion_in.treatment_kind,
            dimension=torsion_in.dimension,
            top_description=torsion_in.top_description,
            source_scan_calculation_id=scan_calc_id,
        )
        session.add(torsion)
        if torsion_in.coordinates:
            session.flush()
            for coord in torsion_in.coordinates:
                session.add(
                    StatmechTorsionDefinition(
                        torsion_id=torsion.id,
                        coordinate_index=coord.coordinate_index,
                        atom1_index=coord.atom1_index,
                        atom2_index=coord.atom2_index,
                        atom3_index=coord.atom3_index,
                        atom4_index=coord.atom4_index,
                    )
                )

    session.flush()
    return statmech
