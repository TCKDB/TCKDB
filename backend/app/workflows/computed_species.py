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
from app.schemas.fragments.calculation import (
    CalculationWithResultsPayload,
    SCFStabilityPayload,
)
from app.schemas.fragments.geometry import GeometryPayload
from app.schemas.fragments.refs import WorkflowToolReleaseRef
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
from app.services.calculation_ownership import (
    W_APPLIED_CORRECTION_SOURCE_CALCULATION_OWNER_MISMATCH,
    W_STATMECH_SOURCE_CALCULATION_OWNER_MISMATCH,
    W_STATMECH_TORSION_SCAN_CALCULATION_OWNER_MISMATCH,
    W_THERMO_SOURCE_CALCULATION_OWNER_MISMATCH,
    assert_calculation_owned_by,
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
from app.services.charge_multiplicity_extraction import (
    try_reconcile_charge_multiplicity_from_output_upload,
)
from app.services.conformer_resolution import resolve_conformer_group
from app.services.energy_correction_resolution import (
    create_applied_energy_correction,
    resolve_applied_correction_source_key,
    resolve_or_create_freq_scale_factor_ref,
)
from app.services.geometry_resolution import resolve_geometry_payload
from app.services.geometry_validation import run_and_persist_geometry_validation
from app.services.hessian_extraction import (
    try_extract_hessian_from_artifact_upload,
)
from app.services.literature_resolution import resolve_or_create_literature
from app.services.local_key_resolution import resolve_calculation_key
from app.services.provenance_warnings import (
    collect_provenance_warnings,
    collect_statmech_content_warnings,
    statmech_has_rotational_structure,
)
from app.services.record_review import (
    RecordRef,
    ReviewPolicy,
    apply_review_policy,
)
from app.services.sp_energy_extraction import (
    try_reconcile_sp_energy_from_output_upload,
)
from app.services.species_resolution import resolve_species_entry
from app.services.statmech_resolution import assert_statmech_role_compatible
from app.services.thermo_resolution import persist_thermo, resolve_thermo_upload
from app.workflows.thermo import assert_thermo_role_matches_calculation_type

#: How a computed-species bundle declares a name in the conformer
#: namespace, phrased as the object of the remedy sentence in
#: ``resolve_applied_correction_source_key``.
_BUNDLE_CONFORMER_KEY_REMEDY = (
    "Every conformer in this bundle carries a required 'key'; "
    "'source_conformer_key' must match one of them."
)

#: The same, for the calculation namespace. A correction's source key is
#: one repair whichever kind of name it uses, so it keeps
#: ``resolve_applied_correction_source_key`` and its published code
#: rather than the bundle-wide ``calculation_key_undeclared``; only the
#: remedy sentence changes.
_BUNDLE_CALCULATION_KEY_REMEDY = (
    "Every calculation in this bundle carries a required 'key'; "
    "'source_calculation_key' must match one of them."
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
) -> CalculationWithResultsPayload:
    """Build the existing primitive payload from a bundle calc block.

    Drops bundle-only fields (``key``, ``depends_on``, ``artifacts``) and
    forwards everything else — including the inline ``literature``
    fragment, which the shared payload now carries in place of a raw
    ``literature_id`` (#194) — so the existing
    ``resolve_and_persist_calculation_with_results`` service can be reused
    unchanged. The citation is resolved there, once, rather than here.
    """
    return CalculationWithResultsPayload(
        type=calc_in.type,
        quality=calc_in.quality,
        software_release=calc_in.software_release,
        workflow_tool_release=calc_in.workflow_tool_release,
        level_of_theory=calc_in.level_of_theory,
        literature=calc_in.literature,
        execution_environment=calc_in.execution_environment,
        opt_result=calc_in.opt_result,
        freq_result=calc_in.freq_result,
        sp_result=calc_in.sp_result,
        irc_result=calc_in.irc_result,
        path_search_result=calc_in.path_search_result,
        wavefunction_diagnostic=calc_in.wavefunction_diagnostic,
        spin_diagnostic=calc_in.spin_diagnostic,
        scf_stability=(
            None
            if calc_in.scf_stability is None
            else SCFStabilityPayload(**calc_in.scf_stability.model_dump())
        ),
        hessian=calc_in.hessian,
        input_geometries=calc_in.input_geometries,
        output_geometries=calc_in.output_geometries,
        parameters=calc_in.parameters,
        parameters_json=calc_in.parameters_json,
        parameters_parser_version=calc_in.parameters_parser_version,
        parameters_extracted_at=calc_in.parameters_extracted_at,
        constraints=calc_in.constraints,
    )


def _build_synthetic_thermo_upload_request(
    thermo_in: ThermoInBundle,
    *,
    species_entry_payload,
    default_workflow_tool_release: WorkflowToolReleaseRef | None = None,
) -> ThermoUploadRequest:
    """Construct a ``ThermoUploadRequest`` from the bundle's thermo block.

    The bundle's ``ThermoInBundle`` shape is intentionally a strict
    subset of ``ThermoUploadRequest`` (no inline ``calculations`` /
    ``source_calculations``) — those resolve from the bundle's calc-key
    namespace separately. The synthetic request is fed to
    ``resolve_thermo_upload`` to pick up provenance resolution for free.

    ``default_workflow_tool_release`` is the bundle root's value, applied
    only where the thermo block itself names none.
    """
    return ThermoUploadRequest(
        species_entry=species_entry_payload,
        scientific_origin=thermo_in.scientific_origin,
        literature=thermo_in.literature,
        software_release=thermo_in.software_release,
        workflow_tool_release=(
            thermo_in.workflow_tool_release or default_workflow_tool_release
        ),
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
        # The first conformer's geometry supplies 3D stereo perception; every
        # conformer's geometry is cross-checked against the isotope labels
        # declared in the SMILES.
        geometry=(request.conformers[0].geometry if request.conformers else None),
        additional_geometries=[conf.geometry for conf in request.conformers[1:]],
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
        primary_calc = resolve_and_persist_calculation_with_results(
            session,
            _to_calc_with_results_payload(conf_in.primary_calculation),
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
            child_calc = resolve_and_persist_calculation_with_results(
                session,
                _to_calc_with_results_payload(additional_in),
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

    # Build the local-key → row maps for cross-references. Conformer keys
    # are required and unique across the bundle
    # (``validate_unique_conformer_keys``), so this is a total namespace:
    # every conformer a depositor can name is in it, and nothing else is.
    conformer_keys_to_observation_id: dict[str, int] = {
        outcome.conformer_in_bundle.key: outcome.observation.id
        for outcome in conformer_outcomes
    }
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
                parent_calc = resolve_calculation_key(
                    dep.parent_calculation_key,
                    calc_keys_to_id,
                    field=(
                        f"calculations['{child_in.key}'].depends_on."
                        f"parent_calculation_key"
                    ),
                )
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
    # Non-blocking gaps surfaced on the upload response: single-point
    # energy reconciliation, absent statmech evidence, and absent
    # provenance on the scientific products this bundle carries.
    upload_warnings: list[UploadWarning] = []

    # Provenance-presence warnings, the same ones /uploads/thermo and
    # /uploads/statmech have always returned. They were never wired here,
    # so a depositor using the bundle route — which is the route the ARC
    # adapter uses — got neither the refusal nor the annotation ADR 0011
    # and ADR 0008 promise, and their record was silently less complete
    # than the identical field-by-field deposit.
    #
    # This bundle is one species, so ``thermo.``/``statmech.`` already
    # names its subject unambiguously and matches the real payload path.
    # The reaction bundle, which carries many species, prefixes with the
    # species key instead.
    #
    # ``collect_provenance_warnings`` documents that callers pass the
    # *effective* value rather than the raw field, which is now the
    # bundle-level fallback where the block itself is silent — warning
    # about provenance that was in fact recorded is the same lie in the
    # other direction.
    if request.thermo is not None:
        upload_warnings.extend(
            collect_provenance_warnings(
                scientific_origin=request.thermo.scientific_origin,
                software_release=request.thermo.software_release,
                workflow_tool_release=(
                    request.thermo.workflow_tool_release
                    or request.workflow_tool_release
                ),
                literature=request.thermo.literature,
                field_prefix="thermo.",
            )
        )
    if request.statmech is not None:
        upload_warnings.extend(
            collect_provenance_warnings(
                scientific_origin=request.statmech.scientific_origin,
                software_release=request.statmech.software_release,
                workflow_tool_release=(
                    request.statmech.workflow_tool_release
                    or request.workflow_tool_release
                ),
                literature=request.statmech.literature,
                freq_scale_factor=request.statmech.freq_scale_factor,
                field_prefix="statmech.",
            )
        )
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
                        session, calc_row, art_in, warnings=upload_warnings
                    )
                    sp_warning = try_reconcile_sp_energy_from_output_upload(
                        session, calc_row, art_in
                    )
                    if sp_warning is not None:
                        upload_warnings.append(sp_warning)
                    # Output logs also state the charge and spin
                    # multiplicity the run actually used; a contradiction
                    # with the declared identity is flagged for review.
                    upload_warnings.extend(
                        try_reconcile_charge_multiplicity_from_output_upload(
                            calc_row, art_in
                        )
                    )
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
            conformer_keys_to_observation_id=conformer_keys_to_observation_id,
            default_workflow_tool_release=request.workflow_tool_release,
            created_by=created_by,
        )

        statmech_row = _persist_statmech_block(
            session,
            request.statmech,
            species_entry_id=species_entry.id,
            calc_keys_to_id=calc_keys_to_id,
            default_workflow_tool_release=request.workflow_tool_release,
            created_by=created_by,
            warnings=upload_warnings,
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
            conformer_keys_to_observation_id=conformer_keys_to_observation_id,
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
        warnings=upload_warnings,
    )


def _persist_thermo_block(
    session: Session,
    request: ComputedSpeciesUploadRequest,
    *,
    species_entry_id: int,
    calc_keys_to_id: dict[str, Calculation],
    conformer_keys_to_observation_id: dict[str, int],
    default_workflow_tool_release: WorkflowToolReleaseRef | None = None,
    created_by: int | None,
) -> tuple[Thermo | None, list[int]]:
    """Persist optional thermo + nested AECs.

    Returns ``(thermo_row | None, applied_correction_ids)`` so the caller
    can record review state for both the thermo row and each AEC row.

    ``default_workflow_tool_release`` is the bundle-level fallback used
    when the thermo block names no workflow tool of its own.
    """
    if request.thermo is None:
        return None, []

    thermo_in = request.thermo

    # Resolve source_calculations by local key with role/type checks.
    resolved_sources: list[ThermoSourceCalculationCreate] = []
    for index, sc in enumerate(thermo_in.source_calculations):
        calc_row = resolve_calculation_key(
            sc.calculation_key,
            calc_keys_to_id,
            field=f"thermo.source_calculations[{index}].calculation_key",
        )
        assert_calculation_owned_by(
            calc_row,
            code=W_THERMO_SOURCE_CALCULATION_OWNER_MISMATCH,
            target="thermo",
            context=(
                f"thermo.source_calculations calculation_key="
                f"'{sc.calculation_key}'"
            ),
            species_entry_id=species_entry_id,
        )
        assert_thermo_role_matches_calculation_type(
            calc_row,
            role=sc.role,
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
        thermo_in,
        species_entry_payload=request.species_entry,
        default_workflow_tool_release=default_workflow_tool_release,
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
            calc_row = resolve_applied_correction_source_key(
                ac.source_calculation_key,
                calc_keys_to_id,
                field=(
                    f"thermo.applied_energy_corrections[{i}]."
                    f"source_calculation_key"
                ),
                declares=_BUNDLE_CALCULATION_KEY_REMEDY,
            )
            assert_calculation_owned_by(
                calc_row,
                code=W_APPLIED_CORRECTION_SOURCE_CALCULATION_OWNER_MISMATCH,
                target="applied energy correction",
                context=(
                    f"thermo.applied_energy_corrections[{i}]."
                    f"source_calculation_key='{ac.source_calculation_key}'"
                ),
                species_entry_id=species_entry_id,
            )
            source_calc_id = calc_row.id

        source_conf_id = resolve_applied_correction_source_key(
            ac.source_conformer_key,
            conformer_keys_to_observation_id,
            field=(
                f"thermo.applied_energy_corrections[{i}]."
                f"source_conformer_key"
            ),
            declares=_BUNDLE_CONFORMER_KEY_REMEDY,
        )

        applied = create_applied_energy_correction(
            session,
            ac,
            target_species_entry_id=species_entry_id,
            source_conformer_observation_id=source_conf_id,
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
    conformer_keys_to_observation_id: dict[str, int],
    created_by: int | None,
) -> list[int]:
    """Persist bundle-level applied energy corrections (AEC/BAC).

    Top-level applied corrections target the bundle's species entry.
    Each ``source_calculation_key`` is resolved against the bundle's
    global calc-key namespace and verified to belong to the same
    species entry; each ``source_conformer_key`` is resolved against the
    bundle's conformer keys. The row + optional component breakdown are
    written via the shared ``create_applied_energy_correction`` service.

    Returns the list of created AEC ids so the caller can record review
    state for each one.
    """
    if not request.applied_energy_corrections:
        return []

    applied_correction_ids: list[int] = []
    for i, ac in enumerate(request.applied_energy_corrections):
        source_calc_id: int | None = None
        if ac.source_calculation_key is not None:
            calc_row = resolve_applied_correction_source_key(
                ac.source_calculation_key,
                calc_keys_to_id,
                field=(
                    f"applied_energy_corrections[{i}]."
                    f"source_calculation_key"
                ),
                declares=_BUNDLE_CALCULATION_KEY_REMEDY,
            )
            assert_calculation_owned_by(
                calc_row,
                code=W_APPLIED_CORRECTION_SOURCE_CALCULATION_OWNER_MISMATCH,
                target="applied energy correction",
                context=(
                    f"applied_energy_corrections[{i}]."
                    f"source_calculation_key='{ac.source_calculation_key}'"
                ),
                species_entry_id=species_entry_id,
            )
            source_calc_id = calc_row.id

        source_conf_id = resolve_applied_correction_source_key(
            ac.source_conformer_key,
            conformer_keys_to_observation_id,
            field=f"applied_energy_corrections[{i}].source_conformer_key",
            declares=_BUNDLE_CONFORMER_KEY_REMEDY,
        )

        applied = create_applied_energy_correction(
            session,
            ac,
            target_species_entry_id=species_entry_id,
            source_conformer_observation_id=source_conf_id,
            source_calculation_id=source_calc_id,
            created_by=created_by,
        )
        applied_correction_ids.append(applied.id)
    return applied_correction_ids


def _persist_statmech_block(
    session: Session,
    statmech: StatmechInBundle | None,
    *,
    species_entry_id: int | None = None,
    transition_state_entry_id: int | None = None,
    calc_keys_to_id: dict[str, Calculation],
    default_workflow_tool_release: WorkflowToolReleaseRef | None = None,
    created_by: int | None,
    warnings: list[UploadWarning] | None = None,
) -> Statmech | None:
    """Persist an optional statmech block for exactly one species or TS subject.

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
    if (species_entry_id is None) == (transition_state_entry_id is None):
        raise ValueError("statmech persistence requires exactly one species or transition-state subject.")

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
    # The statmech block's own value wins; the caller's bundle-level
    # default fills in only where the block stays silent.
    statmech_workflow_tool_ref = (
        s.workflow_tool_release or default_workflow_tool_release
    )
    workflow_tool_release = (
        resolve_workflow_tool_release_ref(session, statmech_workflow_tool_ref)
        if statmech_workflow_tool_ref is not None
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
        transition_state_entry_id=transition_state_entry_id,
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

    if warnings is not None:
        warnings.extend(
            collect_statmech_content_warnings(
                scientific_origin=s.scientific_origin,
                source_calculation_roles={item.role.value for item in s.source_calculations},
                has_rotational_structure=statmech_has_rotational_structure(s),
            )
        )

    # The one seam in this file that two routes reach with two different
    # namespaces, and the reason the lookup below cannot be a subscript.
    # ``/uploads/computed-species`` hands it one species entry's own keys
    # and its schema has already refused an undeclared one;
    # ``/uploads/networks/pdep`` hands it a map spanning every species and
    # every transition state, and ``NetworkPDepUploadRequest`` narrows a
    # *species* statmech's keys and not a *transition state*'s. So a TS
    # statmech naming nothing declared arrives here, and used to leave as
    # a ``KeyError``.
    for index, sc in enumerate(s.source_calculations):
        calc_row = resolve_calculation_key(
            sc.calculation_key,
            calc_keys_to_id,
            field=f"statmech.source_calculations[{index}].calculation_key",
        )
        assert_calculation_owned_by(
            calc_row,
            code=W_STATMECH_SOURCE_CALCULATION_OWNER_MISMATCH,
            target="statmech",
            context=(
                f"statmech.source_calculations calculation_key="
                f"'{sc.calculation_key}'"
            ),
            species_entry_id=species_entry_id,
            transition_state_entry_id=transition_state_entry_id,
        )
        # The third statmech write path, and the third to need DR-0028
        # Requirement 1: a declared role must match the type of the job it
        # names. Shared with the conformer and standalone paths through
        # the statmech resolution service so all three refuse alike.
        assert_statmech_role_compatible(
            calc_row,
            role=sc.role,
            context=(
                f"statmech.source_calculations.calculation_key="
                f"'{sc.calculation_key}'"
            ),
        )
        session.add(
            StatmechSourceCalculation(
                statmech_id=statmech.id,
                calculation_id=calc_row.id,
                role=sc.role,
            )
        )

    for torsion_index, torsion_in in enumerate(s.torsions):
        scan_calc_id: int | None = None
        if torsion_in.source_scan_calculation_key is not None:
            scan_calc_row = resolve_calculation_key(
                torsion_in.source_scan_calculation_key,
                calc_keys_to_id,
                field=(
                    f"statmech.torsions[{torsion_index}]."
                    f"source_scan_calculation_key"
                ),
            )
            assert_calculation_owned_by(
                scan_calc_row,
                code=W_STATMECH_TORSION_SCAN_CALCULATION_OWNER_MISMATCH,
                target="statmech torsion",
                context=(
                    f"statmech.torsions source_scan_calculation_key="
                    f"'{torsion_in.source_scan_calculation_key}'"
                ),
                species_entry_id=species_entry_id,
                transition_state_entry_id=transition_state_entry_id,
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
