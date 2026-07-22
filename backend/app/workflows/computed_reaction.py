"""Workflow orchestrator for the computed reaction upload.

Processes one complete Arkane-style kinetics run in a single transaction:
species → conformers → calculations → reaction → TS → thermo → kinetics fits.

Follows the same key-resolution pattern as the network PDep workflow.
"""

from __future__ import annotations

from sqlalchemy.orm import Session
from tckdb_schemas.upload_warning import UploadWarning

from app.chemistry.geometry import parse_xyz
from app.chemistry.units import convert_ea_to_kj_mol
from app.db.models.calculation import Calculation
from app.db.models.common import (
    CalculationType,
    KineticsCalculationRole,
    KineticsDegeneracyConvention,
    ReactionRole,
    ScientificOriginKind,
    SubmissionRecordType,
)
from app.db.models.kinetics import Kinetics, KineticsSourceCalculation
from app.db.models.reaction import ReactionEntry, ReactionEntryStructureParticipant
from app.db.models.species import ConformerObservation
from app.db.models.statmech import (
    Statmech,
    StatmechSourceCalculation,
    StatmechTorsion,
    StatmechTorsionDefinition,
)
from app.db.models.thermo import Thermo, ThermoNASA, ThermoPoint
from app.db.models.transition_state import TransitionState, TransitionStateEntry
from app.schemas.fragments.geometry import GeometryPayload
from app.schemas.workflows.computed_reaction_upload import (
    ComputedReactionCalculationIn,
    ComputedReactionUploadRequest,
    calculation_in_to_with_results_payload,
)
from app.services.artifact_persistence import persist_artifact
from app.services.calculation_parameter_extraction import (
    try_extract_parameters_from_input_upload,
)
from app.services.calculation_resolution import (
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
from app.services.kinetics_resolution import (
    assert_kinetics_source_role_compatible,
)
from app.services.literature_resolution import resolve_or_create_literature
from app.services.reaction_resolution import (
    compress_species_stoichiometry,
    resolve_chem_reaction,
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


def _persist_calculation(
    session: Session,
    calc_in: ComputedReactionCalculationIn,
    *,
    species_entry_id: int | None = None,
    transition_state_entry_id: int | None = None,
    geometry_id: int | None = None,
    geometry_key_map: dict[str, int],
    created_by: int | None = None,
    sp_energy_warnings: list[UploadWarning] | None = None,
) -> Calculation:
    """Persist one bundle-local calculation through the shared calculation seam.

    Routes provenance resolution, typed-result persistence, and parameter
    persistence through ``resolve_and_persist_calculation_with_results``.
    Bundle-specific concerns (local-key geometry resolution + fallback
    geometry attachment + artifact persistence) remain here as
    orchestration.

    Producer-declared ``input_geometries`` and ``output_geometries`` on
    ``calc_in`` flow through the shared payload to the persistence seam,
    which writes the corresponding rows. The fallback semantics follow
    the shared service's contract:

    * ``input_geometries`` empty → fallback links the resolved geometry
      for ``freq``/``sp`` calc types only.
    * ``output_geometries`` empty → fallback links the resolved geometry
      with role ``final`` for ``opt`` calc type only.

    The resolved fallback geometry is the calc's ``geometry_key`` (when
    present, looked up in ``geometry_key_map``) or the explicit
    ``geometry_id`` (used for primary opt calcs whose conformer geometry
    is not yet in the key map).
    """

    shared_payload = calculation_in_to_with_results_payload(calc_in)
    calculation = resolve_and_persist_calculation_with_results(
        session,
        shared_payload,
        species_entry_id=species_entry_id,
        transition_state_entry_id=transition_state_entry_id,
        created_by=created_by,
    )

    for artifact_in in calc_in.artifacts:
        persist_artifact(
            session,
            calculation_id=calculation.id,
            artifact_in=artifact_in,
            created_by=created_by,
        )
        # Opportunistic per-artifact extraction, both best-effort — never
        # abort the bundle. Input artifacts yield parameter rows; output
        # logs reconcile the single-point energy against the tool's
        # reported value (fill/mismatch), the same as the artifacts route.
        try_extract_parameters_from_input_upload(session, calculation, artifact_in)
        sp_warning = try_reconcile_sp_energy_from_output_upload(
            session, calculation, artifact_in
        )
        if sp_warning is not None and sp_energy_warnings is not None:
            sp_energy_warnings.append(sp_warning)

    resolved_geom_id = geometry_id
    if calc_in.geometry_key is not None:
        resolved_geom_id = geometry_key_map.get(calc_in.geometry_key, geometry_id)

    context = (
        f"calculation '{calc_in.key}' (type='{calc_in.type.value}')"
    )
    attach_calculation_input_geometries(
        session,
        calc=calculation,
        explicit_input_geometries=calc_in.input_geometries,
        fallback_geometry_id=resolved_geom_id,
        context=context,
    )
    attach_calculation_output_geometries(
        session,
        calc=calculation,
        explicit_output_geometries=calc_in.output_geometries,
        fallback_geometry_id=resolved_geom_id,
        context=context,
    )

    # Fill-when-absent Hessian extraction runs *after* input geometries are
    # attached (unlike the SP-energy hook above, which needs no geometry): a
    # freq log / ORCA .hess yields the Cartesian force-constant matrix, bound
    # to this calc's now-resolved input geometry.
    for artifact_in in calc_in.artifacts:
        try_extract_hessian_from_artifact_upload(session, calculation, artifact_in)

    # Persist scan_result for type=scan calcs. The schema layer guarantees
    # scan_result is only present when type=scan. Conformer/TS primaries
    # are constrained to type=opt, so scan rides only as additional calcs
    # in this workflow.
    if (
        calc_in.type == CalculationType.scan
        and calc_in.scan_result is not None
    ):
        persist_calculation_scan(session, calculation.id, calc_in.scan_result)

    return calculation


def _anchor_species_calculation_to_observation(
    calculation: Calculation,
    calc_in: ComputedReactionCalculationIn,
    observation_id_by_geometry_key: dict[str, int],
) -> None:
    """Anchor a species-owned calculation to the conformer observation for its geometry key."""
    if calc_in.geometry_key is None:
        return

    observation_id = observation_id_by_geometry_key.get(calc_in.geometry_key)
    if observation_id is None:
        raise ValueError(
            f"Species calculation '{calc_in.key}' geometry_key "
            f"'{calc_in.geometry_key}' does not resolve to a conformer observation."
        )
    calculation.conformer_observation_id = observation_id


def _index_species_sp_calcs(
    request: ComputedReactionUploadRequest,
    calculation_key_to_id: dict[str, int],
) -> dict[str, list[int]]:
    """Index species-owned SP calc ids by species key.

    Used by the legacy kinetics auto-link fallback when a producer does
    not declare ``source_calculations`` explicitly. Each species's
    ``calculations`` list is scanned for ``type=sp`` entries and their
    persisted calculation ids are returned in declaration order.
    """
    by_species: dict[str, list[int]] = {}
    for sp in request.species:
        sp_ids = [
            calculation_key_to_id[calc.key]
            for calc in sp.calculations
            if calc.type == CalculationType.sp
        ]
        if sp_ids:
            by_species[sp.key] = sp_ids
    return by_species


def persist_computed_reaction_upload(
    session: Session,
    request: ComputedReactionUploadRequest,
    *,
    created_by: int | None = None,
    review_policy: ReviewPolicy | None = ReviewPolicy(),
) -> dict:
    """Persist a complete computed reaction upload in one transaction.

    Returns a summary dict with the created row IDs.
    """

    # Key → resolved object maps
    species_key_to_entry: dict[str, object] = {}
    geometry_key_to_id: dict[str, int] = {}
    calculation_key_to_id: dict[str, int] = {}
    observation_id_by_geometry_key: dict[str, int] = {}
    # Review-row targets accumulated as records are written so the
    # caller's ReviewPolicy can be applied at end-of-workflow.
    review_targets: list[RecordRef] = []
    applied_correction_ids: list[int] = []
    # Single-point energy reconciliation warnings from inline output logs.
    sp_energy_warnings: list[UploadWarning] = []

    # ------------------------------------------------------------------
    # 1. Resolve species + conformers + calculations
    # ------------------------------------------------------------------
    for sp in request.species:
        # Use first conformer's XYZ for 3D stereo label derivation
        first_xyz = sp.conformers[0].geometry.xyz_text if sp.conformers else None
        species_entry = resolve_species_entry(
            session, sp.species_entry, created_by=created_by,
            xyz_text=first_xyz,
        )
        species_key_to_entry[sp.key] = species_entry
        review_targets.append(
            RecordRef(SubmissionRecordType.species_entry, species_entry.id)
        )

        # Conformers
        for conf in sp.conformers:
            geom_payload = GeometryPayload(xyz_text=conf.geometry.xyz_text)
            geometry = resolve_geometry_payload(session, geom_payload)
            geometry_key_to_id[conf.geometry.key] = geometry.id

            calculation = _persist_calculation(
                session,
                conf.calculation,
                species_entry_id=species_entry.id,
                geometry_id=geometry.id,
                geometry_key_map=geometry_key_to_id,
                created_by=created_by,
                sp_energy_warnings=sp_energy_warnings,
            )
            calculation_key_to_id[conf.calculation.key] = calculation.id
            review_targets.append(
                RecordRef(SubmissionRecordType.calculation, calculation.id)
            )

            parsed = parse_xyz(GeometryPayload(xyz_text=conf.geometry.xyz_text))
            conformer_group, fingerprint, scheme = resolve_conformer_group(
                session, species_entry, label=conf.label, created_by=created_by,
                smiles=sp.species_entry.smiles, xyz_atoms=parsed.atoms,
            )
            observation = ConformerObservation(
                conformer_group_id=conformer_group.id,
                scientific_origin=conf.scientific_origin,
                note=conf.note,
                created_by=created_by,
                assignment_scheme_id=scheme.id if scheme else None,
                torsion_fingerprint_json=(
                    fingerprint.to_dict() if fingerprint else None
                ),
            )
            session.add(observation)
            session.flush()
            observation_id_by_geometry_key[conf.geometry.key] = observation.id
            review_targets.append(
                RecordRef(SubmissionRecordType.conformer_group, conformer_group.id)
            )
            review_targets.append(
                RecordRef(
                    SubmissionRecordType.conformer_observation, observation.id
                )
            )

            # Anchor primary calc to this conformer observation
            calculation.conformer_observation_id = observation.id

        # Additional calculations (freq, sp at higher LOT)
        for calc_in in sp.calculations:
            calculation = _persist_calculation(
                session,
                calc_in,
                species_entry_id=species_entry.id,
                geometry_key_map=geometry_key_to_id,
                created_by=created_by,
                sp_energy_warnings=sp_energy_warnings,
            )
            calculation_key_to_id[calc_in.key] = calculation.id
            review_targets.append(
                RecordRef(SubmissionRecordType.calculation, calculation.id)
            )

            _anchor_species_calculation_to_observation(
                calculation,
                calc_in,
                observation_id_by_geometry_key,
            )

    session.flush()

    # Phase-1 geometry-identity validation for species-side opt calcs.
    # Best-effort: opt only, no-ops on missing data, never aborts the
    # upload, and a failed result is persisted as evidence rather than
    # used as a gate.
    #
    # TS geometry validation is intentionally deferred. A TS does not
    # have a single canonical species graph — its connectivity sits
    # between the reactant and product graphs — so feeding it through
    # the species-isomorphism validator would systematically mis-fire
    # as a fail. A reaction-aware TS validator (checking expected
    # forming/breaking bonds against the reaction's atom map and
    # ideally IRC endpoint geometries) is the right tool, and is
    # tracked as future work. Until then, no row is written for TS.
    for sp in request.species:
        species_smiles = sp.species_entry.smiles
        species_calc_keys: list[str] = [sp.conformers[0].calculation.key] if sp.conformers else []
        for conf in sp.conformers[1:]:
            species_calc_keys.append(conf.calculation.key)
        for calc_in in sp.calculations:
            species_calc_keys.append(calc_in.key)
        for calc_key in species_calc_keys:
            calc_id = calculation_key_to_id.get(calc_key)
            if calc_id is None:
                continue
            calc_row = session.get(Calculation, calc_id)
            if calc_row is None:
                continue
            run_and_persist_geometry_validation(
                session,
                calc_row,
                species_smiles=species_smiles,
            )

    # ------------------------------------------------------------------
    # 2. Resolve reaction
    # ------------------------------------------------------------------
    reactant_entries = [species_key_to_entry[k] for k in request.reactant_keys]
    product_entries = [species_key_to_entry[k] for k in request.product_keys]

    chem_reaction = resolve_chem_reaction(
        session,
        reversible=request.reversible,
        reaction_family=request.reaction_family,
        reaction_family_source_note=request.reaction_family_source_note,
        reactant_stoichiometry=compress_species_stoichiometry(reactant_entries),
        product_stoichiometry=compress_species_stoichiometry(product_entries),
    )

    # We create one reaction entry for the bundle's canonical direction
    canonical_reaction_entry = ReactionEntry(
        reaction_id=chem_reaction.id, created_by=created_by
    )
    session.add(canonical_reaction_entry)
    session.flush()
    review_targets.append(
        RecordRef(
            SubmissionRecordType.reaction_entry, canonical_reaction_entry.id
        )
    )

    for idx, key in enumerate(request.reactant_keys, start=1):
        session.add(
            ReactionEntryStructureParticipant(
                reaction_entry_id=canonical_reaction_entry.id,
                species_entry_id=species_key_to_entry[key].id,
                role=ReactionRole.reactant,
                participant_index=idx,
                created_by=created_by,
            )
        )
    for idx, key in enumerate(request.product_keys, start=1):
        session.add(
            ReactionEntryStructureParticipant(
                reaction_entry_id=canonical_reaction_entry.id,
                species_entry_id=species_key_to_entry[key].id,
                role=ReactionRole.product,
                participant_index=idx,
                created_by=created_by,
            )
        )
    session.flush()

    # ------------------------------------------------------------------
    # 3. Transition state (optional)
    # ------------------------------------------------------------------
    ts_entry = None
    if request.transition_state:
        ts_in = request.transition_state
        ts = TransitionState(
            reaction_entry_id=canonical_reaction_entry.id,
            label=ts_in.label,
            note=ts_in.note,
            created_by=created_by,
        )
        session.add(ts)
        session.flush()

        ts_entry = TransitionStateEntry(
            transition_state_id=ts.id,
            charge=ts_in.charge,
            multiplicity=ts_in.multiplicity,
            unmapped_smiles=ts_in.unmapped_smiles,
            created_by=created_by,
        )
        session.add(ts_entry)
        session.flush()
        review_targets.append(
            RecordRef(SubmissionRecordType.transition_state, ts.id)
        )
        review_targets.append(
            RecordRef(SubmissionRecordType.transition_state_entry, ts_entry.id)
        )

        ts_geom = resolve_geometry_payload(
            session, GeometryPayload(xyz_text=ts_in.geometry.xyz_text)
        )
        geometry_key_to_id[ts_in.geometry.key] = ts_geom.id

        ts_calc = _persist_calculation(
            session,
            ts_in.calculation,
            transition_state_entry_id=ts_entry.id,
            geometry_id=ts_geom.id,
            geometry_key_map=geometry_key_to_id,
            created_by=created_by,
            sp_energy_warnings=sp_energy_warnings,
        )
        calculation_key_to_id[ts_in.calculation.key] = ts_calc.id
        review_targets.append(
            RecordRef(SubmissionRecordType.calculation, ts_calc.id)
        )

        for calc_in in ts_in.calculations:
            calc = _persist_calculation(
                session,
                calc_in,
                transition_state_entry_id=ts_entry.id,
                geometry_key_map=geometry_key_to_id,
                created_by=created_by,
                sp_energy_warnings=sp_energy_warnings,
            )
            calculation_key_to_id[calc_in.key] = calc.id
            review_targets.append(
                RecordRef(SubmissionRecordType.calculation, calc.id)
            )

    session.flush()

    # ------------------------------------------------------------------
    # 3b. Resolve producer-declared calculation_dependency edges
    #
    # All bundle calculations are now persisted, so local-key references
    # in ``depends_on`` are guaranteed to resolve. The shared idempotent
    # helper rejects self-edges, role mismatches against existing edges,
    # and per-role one-parent-per-child violations.
    # ------------------------------------------------------------------
    def _persisted_calc(calc_key: str) -> Calculation:
        return session.get(Calculation, calculation_key_to_id[calc_key])

    def _wire_depends_on(calc_in: ComputedReactionCalculationIn) -> None:
        if not calc_in.depends_on:
            return
        child_calc = _persisted_calc(calc_in.key)
        for dep in calc_in.depends_on:
            parent_calc = _persisted_calc(dep.parent_calculation_key)
            context = (
                f"calculation '{calc_in.key}'.depends_on "
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

    for sp in request.species:
        for conf in sp.conformers:
            _wire_depends_on(conf.calculation)
        for calc_in in sp.calculations:
            _wire_depends_on(calc_in)
    if request.transition_state:
        _wire_depends_on(request.transition_state.calculation)
        for calc_in in request.transition_state.calculations:
            _wire_depends_on(calc_in)

    session.flush()

    # ------------------------------------------------------------------
    # 3c. Applied energy corrections (species-side + TS-side)
    #
    # Generic, workflow-tool-neutral payloads: producers explicitly
    # declare AEC/BAC/SOC totals plus optional component breakdowns.
    # ``source_calculation_key`` resolves through the bundle's global
    # calc-key namespace, but each correction targets a specific entry —
    # so we additionally enforce owner-consistency:
    #
    # * species correction → source calc must be owned by THIS species
    # * TS correction → source calc must be owned by THIS TS entry
    #
    # Frequency scale factors are intentionally not modeled here; they
    # continue to flow through ``statmech.frequency_scale_factor_id``.
    # ------------------------------------------------------------------
    for sp in request.species:
        if not sp.applied_energy_corrections:
            continue
        species_entry = species_key_to_entry[sp.key]
        for i, ac in enumerate(sp.applied_energy_corrections):
            source_calc_id: int | None = None
            if ac.source_calculation_key is not None:
                source_calc_id = calculation_key_to_id[ac.source_calculation_key]
                source_calc = session.get(Calculation, source_calc_id)
                if source_calc.species_entry_id != species_entry.id:
                    raise ValueError(
                        f"species[{sp.key!r}].applied_energy_corrections[{i}]."
                        f"source_calculation_key='{ac.source_calculation_key}': "
                        f"refers to a calculation that is not owned by this "
                        f"species entry."
                    )
            applied = create_applied_energy_correction(
                session,
                ac,
                target_species_entry_id=species_entry.id,
                source_calculation_id=source_calc_id,
                created_by=created_by,
            )
            applied_correction_ids.append(applied.id)

    if request.transition_state and ts_entry is not None:
        for i, ac in enumerate(
            request.transition_state.applied_energy_corrections
        ):
            source_calc_id = None
            if ac.source_calculation_key is not None:
                source_calc_id = calculation_key_to_id[ac.source_calculation_key]
                source_calc = session.get(Calculation, source_calc_id)
                if source_calc.transition_state_entry_id != ts_entry.id:
                    raise ValueError(
                        f"transition_state.applied_energy_corrections[{i}]."
                        f"source_calculation_key='{ac.source_calculation_key}': "
                        f"refers to a calculation that is not owned by this "
                        f"transition state entry."
                    )
            applied = create_applied_energy_correction(
                session,
                ac,
                target_transition_state_entry_id=ts_entry.id,
                source_calculation_id=source_calc_id,
                created_by=created_by,
            )
            applied_correction_ids.append(applied.id)

    session.flush()

    # ------------------------------------------------------------------
    # 4. Thermo (per species, if provided)
    # ------------------------------------------------------------------

    # Resolve bundle-level provenance once for thermo/statmech/kinetics
    # analysis_software_release = the code that computed statmech/thermo/kinetics
    #   (e.g. RMG-Py/Arkane, MESS, MultiWell) — not the ESS (Gaussian/ORCA)
    bundle_analysis_software_release = (
        resolve_software_release_ref(session, request.analysis_software_release)
        if request.analysis_software_release is not None
        else None
    )
    bundle_workflow_tool_release = resolve_workflow_tool_release_ref(
        session, request.workflow_tool_release
    )

    thermo_ids = []
    # Correlate each species' thermo with its statmech (persisted in a
    # separate loop below) so a bundle-created COMPUTED thermo can be
    # linked to the statmech it was derived from. Keyed by species
    # participant local key, which is used consistently in both loops.
    thermo_by_species_key: dict[str, Thermo] = {}
    for sp in request.species:
        if sp.thermo is not None:
            species_entry = species_key_to_entry[sp.key]
            t = sp.thermo

            thermo = Thermo(
                species_entry_id=species_entry.id,
                scientific_origin=t.scientific_origin,
                software_release_id=(
                    bundle_analysis_software_release.id
                    if bundle_analysis_software_release
                    else None
                ),
                workflow_tool_release_id=(
                    bundle_workflow_tool_release.id
                    if bundle_workflow_tool_release
                    else None
                ),
                h298_kj_mol=t.h298_kj_mol,
                s298_j_mol_k=t.s298_j_mol_k,
                tmin_k=t.tmin_k,
                tmax_k=t.tmax_k,
                note=t.note,
                created_by=created_by,
            )
            session.add(thermo)
            session.flush()
            thermo_ids.append(thermo.id)
            thermo_by_species_key[sp.key] = thermo

            if t.nasa is not None:
                session.add(ThermoNASA(thermo_id=thermo.id, **t.nasa.model_dump()))

            for pt in t.points:
                session.add(ThermoPoint(thermo_id=thermo.id, **pt.model_dump()))

    session.flush()

    # ------------------------------------------------------------------
    # 4b. Statmech (per species, if provided)
    # ------------------------------------------------------------------
    statmech_ids = []
    for sp in request.species:
        if sp.statmech is not None:
            species_entry = species_key_to_entry[sp.key]
            s = sp.statmech

            fsf_id = None
            if s.freq_scale_factor is not None:
                fsf = resolve_or_create_freq_scale_factor_ref(
                    session, s.freq_scale_factor, created_by=created_by
                )
                fsf_id = fsf.id

            statmech = Statmech(
                species_entry_id=species_entry.id,
                scientific_origin=s.scientific_origin,
                software_release_id=(
                    bundle_analysis_software_release.id
                    if bundle_analysis_software_release
                    else None
                ),
                workflow_tool_release_id=(
                    bundle_workflow_tool_release.id
                    if bundle_workflow_tool_release
                    else None
                ),
                is_linear=s.is_linear,
                rigid_rotor_kind=s.rigid_rotor_kind,
                external_symmetry=s.external_symmetry,
                optical_isomers=s.optical_isomers,
                point_group=s.point_group,
                statmech_treatment=s.statmech_treatment,
                frequency_scale_factor_id=fsf_id,
                uses_projected_frequencies=s.uses_projected_frequencies,
                note=s.note,
                created_by=created_by,
            )
            session.add(statmech)
            session.flush()
            statmech_ids.append(statmech.id)

            # Link this species' COMPUTED thermo (persisted above) to the
            # statmech it was derived from. Correlated by species key so
            # each participant links to its own statmech. Experimental,
            # literature, or group-additivity thermo keeps statmech_id NULL.
            linked = thermo_by_species_key.get(sp.key)
            if (
                linked is not None
                and linked.statmech_id is None
                and linked.scientific_origin == ScientificOriginKind.computed
            ):
                linked.statmech_id = statmech.id

            # Statmech → calculation links. Producer-declared by local
            # key; resolved against the bundle's global calc namespace.
            # Owner-consistency: each referenced calc must be owned by
            # THIS species entry — a TS-owned or sibling-species-owned
            # calc is rejected with 422 (mirrors the AEC ownership
            # check above).
            for i, sc in enumerate(s.source_calculations):
                calc_id = calculation_key_to_id[sc.calculation_key]
                calc_row = session.get(Calculation, calc_id)
                if calc_row.species_entry_id != species_entry.id:
                    flavor = (
                        "owned by a transition state"
                        if calc_row.transition_state_entry_id is not None
                        else "owned by a different species entry"
                    )
                    raise ValueError(
                        f"species[{sp.key!r}].statmech.source_calculations[{i}]."
                        f"calculation_key='{sc.calculation_key}': "
                        f"refers to a calculation {flavor}."
                    )
                session.add(
                    StatmechSourceCalculation(
                        statmech_id=statmech.id,
                        calculation_id=calc_id,
                        role=sc.role,
                    )
                )

            for torsion_in in s.torsions:
                scan_calc_id: int | None = None
                if torsion_in.source_scan_calculation_key is not None:
                    scan_calc_id = calculation_key_to_id[
                        torsion_in.source_scan_calculation_key
                    ]

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

    # ------------------------------------------------------------------
    # 5. Kinetics fits
    # ------------------------------------------------------------------
    canonical_reactant_keys = list(request.reactant_keys)
    canonical_product_keys = list(request.product_keys)

    kinetics_ids = []
    for kin in request.kinetics:
        # If the fit's participant ordering matches the bundle's canonical
        # direction exactly, reuse ``canonical_reaction_entry`` rather than
        # producing a duplicate row with identical participants. Reverse
        # (or otherwise reordered) fits still get their own entry: the
        # ``(reaction_entry_id, role, participant_index)`` uniqueness on
        # ``reaction_entry_structure_participant`` requires it, and the
        # direction-specific ordering is the scientific record we want
        # kinetics to point at.
        is_canonical_direction = (
            list(kin.reactant_keys) == canonical_reactant_keys
            and list(kin.product_keys) == canonical_product_keys
        )

        if is_canonical_direction:
            kin_entry = canonical_reaction_entry
        else:
            kin_reactant_entries = [
                species_key_to_entry[k] for k in kin.reactant_keys
            ]
            kin_product_entries = [
                species_key_to_entry[k] for k in kin.product_keys
            ]

            kin_chem_rxn = resolve_chem_reaction(
                session,
                reversible=request.reversible,
                reaction_family=request.reaction_family,
                reaction_family_source_note=request.reaction_family_source_note,
                reactant_stoichiometry=compress_species_stoichiometry(
                    kin_reactant_entries
                ),
                product_stoichiometry=compress_species_stoichiometry(
                    kin_product_entries
                ),
            )

            kin_entry = ReactionEntry(
                reaction_id=kin_chem_rxn.id, created_by=created_by
            )
            session.add(kin_entry)
            session.flush()

            for idx, key in enumerate(kin.reactant_keys, start=1):
                session.add(
                    ReactionEntryStructureParticipant(
                        reaction_entry_id=kin_entry.id,
                        species_entry_id=species_key_to_entry[key].id,
                        role=ReactionRole.reactant,
                        participant_index=idx,
                        created_by=created_by,
                    )
                )
            for idx, key in enumerate(kin.product_keys, start=1):
                session.add(
                    ReactionEntryStructureParticipant(
                        reaction_entry_id=kin_entry.id,
                        species_entry_id=species_key_to_entry[key].id,
                        role=ReactionRole.product,
                        participant_index=idx,
                        created_by=created_by,
                    )
                )
            session.flush()

        ea_kj_mol = (
            convert_ea_to_kj_mol(kin.reported_ea, kin.reported_ea_units)
            if kin.reported_ea is not None
            else None
        )
        ea_uncertainty_kj_mol = (
            convert_ea_to_kj_mol(kin.d_reported_ea, kin.reported_ea_units)
            if kin.d_reported_ea is not None
            else None
        )

        # Resolve bundle-level provenance
        literature = (
            resolve_or_create_literature(session, request.literature)
            if request.literature is not None
            else None
        )
        workflow_tool_release = resolve_workflow_tool_release_ref(
            session, request.workflow_tool_release
        )

        kinetics = Kinetics(
            reaction_entry_id=kin_entry.id,
            scientific_origin=kin.scientific_origin,
            model_kind=kin.model_kind,
            is_third_body=kin.is_third_body,
            literature_id=literature.id if literature else None,
            software_release_id=(
                bundle_analysis_software_release.id
                if bundle_analysis_software_release
                else None
            ),
            workflow_tool_release_id=(
                workflow_tool_release.id if workflow_tool_release else None
            ),
            a=kin.a,
            a_units=kin.a_units,
            n=kin.n,
            ea_kj_mol=ea_kj_mol,
            a_uncertainty=kin.a_uncertainty,
            a_uncertainty_kind=kin.a_uncertainty_kind,
            n_uncertainty=kin.n_uncertainty,
            ea_uncertainty_kj_mol=ea_uncertainty_kj_mol,
            tmin_k=kin.tmin_k,
            tmax_k=kin.tmax_k,
            degeneracy=kin.degeneracy,
            degeneracy_convention=KineticsDegeneracyConvention(
                kin.degeneracy_convention.value
            ),
            tunneling_model=kin.tunneling_model,
            pressure_context=kin.pressure_context,
            pressure_bar=kin.pressure_bar,
            note=kin.note,
            created_by=created_by,
        )
        session.add(kinetics)
        session.flush()
        kinetics_ids.append(kinetics.id)

        # Producer-controlled provenance takes precedence over the
        # legacy fallback. When ``kin.source_calculations`` is non-empty
        # we write exactly the declared rows (after role/type/owner
        # compatibility check) and skip the fallback. When it is empty
        # we run the legacy auto-link to preserve existing behavior for
        # producers that haven't migrated to declaring source calcs.
        if kin.source_calculations:
            for entry in kin.source_calculations:
                calc_id = calculation_key_to_id[entry.calculation_key]
                source_calc = session.get(Calculation, calc_id)
                assert_kinetics_source_role_compatible(
                    calculation=source_calc,
                    role=entry.role,
                    calculation_key=entry.calculation_key,
                )
                session.add(
                    KineticsSourceCalculation(
                        kinetics_id=kinetics.id,
                        calculation_id=calc_id,
                        role=entry.role,
                    )
                )
        else:
            # Legacy fallback: auto-link the first species-owned SP calc
            # found for each reactant/product as reactant_energy /
            # product_energy. Producer-declared source_calculations is
            # the preferred surface; this remains for backward
            # compatibility with producers that emit no explicit links.
            sp_calcs_by_species_key = _index_species_sp_calcs(
                request, calculation_key_to_id
            )
            for key, role in [
                *[
                    (k, KineticsCalculationRole.reactant_energy)
                    for k in kin.reactant_keys
                ],
                *[
                    (k, KineticsCalculationRole.product_energy)
                    for k in kin.product_keys
                ],
            ]:
                sp_calc_ids = sp_calcs_by_species_key.get(key, [])
                if sp_calc_ids:
                    session.add(
                        KineticsSourceCalculation(
                            kinetics_id=kinetics.id,
                            calculation_id=sp_calc_ids[0],
                            role=role,
                        )
                    )

    session.flush()

    review_targets.extend(
        RecordRef(SubmissionRecordType.kinetics, kid) for kid in kinetics_ids
    )
    review_targets.extend(
        RecordRef(SubmissionRecordType.thermo, tid) for tid in thermo_ids
    )
    review_targets.extend(
        RecordRef(SubmissionRecordType.statmech, sid) for sid in statmech_ids
    )
    review_targets.extend(
        RecordRef(SubmissionRecordType.applied_energy_correction, aid)
        for aid in applied_correction_ids
    )
    apply_review_policy(
        session,
        targets=review_targets,
        policy=review_policy,
        created_by=created_by,
    )

    return {
        "reaction_entry_id": canonical_reaction_entry.id,
        "reaction_id": chem_reaction.id,
        "transition_state_entry_id": ts_entry.id if ts_entry else None,
        "kinetics_ids": kinetics_ids,
        "thermo_ids": thermo_ids,
        "statmech_ids": statmech_ids,
        "species_entry_ids": [e.id for e in species_key_to_entry.values()],
        "species_count": len(request.species),
        # Expose the bundle-local calc-key → assigned-id map so the
        # client builder layer can plan second-phase artifact uploads
        # without re-walking the bundle. Response-only; unchanged
        # request payload.
        "calculation_keys": dict(calculation_key_to_id),
        "warnings": sp_energy_warnings,
    }
