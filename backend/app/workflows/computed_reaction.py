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
from app.db.models.thermo import (
    Thermo,
    ThermoNASA,
    ThermoPoint,
    ThermoSourceCalculation,
)
from app.db.models.transition_state import TransitionState, TransitionStateEntry
from app.schemas.fragments.geometry import GeometryPayload
from app.schemas.workflows.computed_reaction_upload import (
    ComputedReactionCalculationIn,
    ComputedReactionUploadRequest,
    calculation_in_to_with_results_payload,
)
from app.services.artifact_persistence import persist_artifact
from app.services.calculation_ownership import (
    W_STATMECH_TORSION_SCAN_CALCULATION_OWNER_MISMATCH,
    W_THERMO_SOURCE_CALCULATION_OWNER_MISMATCH,
    assert_calculation_owned_by,
)
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
from app.services.kinetics_resolution import (
    assert_kinetics_source_role_compatible,
)
from app.services.literature_resolution import resolve_or_create_literature
from app.services.provenance_warnings import (
    NOT_APPLICABLE,
    collect_provenance_warnings,
    collect_statmech_content_warnings,
    statmech_has_rotational_structure,
)
from app.services.reaction_atom_map import (
    ResolvedAtomMapParticipant,
    persist_reaction_atom_map,
)
from app.services.reaction_resolution import (
    compress_species_stoichiometry,
    resolve_chem_reaction,
    validate_transition_state_composition,
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
from app.services.transition_state_validation import (
    persist_transition_state_validation_evidence,
)
from app.workflows.thermo import assert_thermo_role_matches_calculation_type


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

    # The calculation's citation arrives as an inline literature fragment
    # (it used to arrive as a raw ``literature_id``, which only a client
    # that had already queried this database could supply). Resolve it to a
    # row here — the wire package has no session — and hand the id to the
    # adapter, which refuses the pair "fragment present, id absent".
    literature_id = (
        resolve_or_create_literature(session, calc_in.literature).id
        if calc_in.literature is not None
        else None
    )
    shared_payload = calculation_in_to_with_results_payload(
        calc_in, literature_id=literature_id
    )
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
        # Output logs also state the charge and spin multiplicity the run
        # actually used; a contradiction with the declared identity is
        # flagged for review. (``sp_energy_warnings`` is the shared
        # per-artifact warning accumulator, not single-point-energy only.)
        if sp_energy_warnings is not None:
            sp_energy_warnings.extend(
                try_reconcile_charge_multiplicity_from_output_upload(
                    calculation, artifact_in
                )
            )

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


def _collect_bundle_provenance_warnings(
    request: ComputedReactionUploadRequest,
) -> list[UploadWarning]:
    """Report provenance this bundle's products could carry and do not.

    The same warnings ``/uploads/thermo``, ``/uploads/statmech`` and
    ``/uploads/kinetics`` have always returned, which this route returned
    none of. A bundle deposit was therefore silently less complete than
    the identical field-by-field deposit, with nothing saying so —
    contrary to ADR 0011 and ADR 0008, under which absence is
    incompleteness and incompleteness is annotated rather than refused.

    Three things this has to get right that the standalone routes do not:

    **Naming the subject.** A bundle carries many species. A bare
    "missing workflow tool" on a twenty-species deposit is true and
    useless, so every warning is prefixed with the species key it
    concerns, in the ``species['ch4'].statmech.…`` form this payload's
    own validators already use for error paths.

    **Effective, not raw, values.** A per-species ``software_release``
    falls back to the bundle-level ``analysis_software_release``, and the
    workflow persists the fallback. Warning on the raw per-species field
    would name provenance that was in fact recorded.

    **Kinetics' level of theory is not applicable here.**
    ``collect_kinetics_provenance_warnings`` asks the standalone route
    for ``energy_level_of_theory``, and ``BundleKineticsIn`` has no such
    field — nor does the bundle root, nor is it a column on ``kinetics``.
    On the standalone route it is a resolution hint that
    ``app.workflows.kinetics`` uses to auto-resolve source SP
    calculations; a bundle names its source calculations by key and has
    no use for it. Warning about it would be exactly the un-actionable
    warning this wiring exists to avoid, so it is passed as
    ``NOT_APPLICABLE`` rather than as ``None``.
    """
    warnings: list[UploadWarning] = []

    for sp in request.species:
        if sp.thermo is not None:
            warnings.extend(
                collect_provenance_warnings(
                    scientific_origin=sp.thermo.scientific_origin,
                    software_release=(
                        sp.thermo.software_release or request.analysis_software_release
                    ),
                    workflow_tool_release=(
                        sp.thermo.workflow_tool_release or request.workflow_tool_release
                    ),
                    literature=sp.thermo.literature,
                    field_prefix=f"species[{sp.key!r}].thermo.",
                )
            )
        if sp.statmech is not None:
            warnings.extend(
                collect_provenance_warnings(
                    scientific_origin=sp.statmech.scientific_origin,
                    software_release=(
                        sp.statmech.software_release
                        or request.analysis_software_release
                    ),
                    workflow_tool_release=(
                        sp.statmech.workflow_tool_release
                        or request.workflow_tool_release
                    ),
                    literature=sp.statmech.literature,
                    freq_scale_factor=sp.statmech.freq_scale_factor,
                    field_prefix=f"species[{sp.key!r}].statmech.",
                )
            )
            # The species route has reported this since statmech landed
            # there; the reaction route reported nothing, so the same
            # untraceable partition function was named on one route and
            # silent on the other.
            #
            # ``statmech_has_rotational_structure`` only became answerable
            # on this route with #142: it reads the rotational constants,
            # which ``BundleStatmechIn`` could not carry until now. Before
            # that it could only ever see torsions, so a polyatomic
            # deposited with constants and no torsions — the ordinary ARC
            # shape — looked monatomic to it.
            warnings.extend(
                collect_statmech_content_warnings(
                    scientific_origin=sp.statmech.scientific_origin,
                    source_calculation_roles={
                        item.role.value for item in sp.statmech.source_calculations
                    },
                    has_rotational_structure=statmech_has_rotational_structure(
                        sp.statmech
                    ),
                    field=f"species[{sp.key!r}].statmech",
                )
            )

    # Kinetics provenance is bundle-scoped, and the field paths say so.
    # ``BundleKineticsIn`` carries no provenance fields at all; the workflow
    # writes ``request.literature``, ``request.analysis_software_release`` and
    # ``request.workflow_tool_release`` onto every kinetics row it creates.
    # So the actionable field is the bundle root, and a path like
    # ``kinetics[0].software_release`` would name a field that does not
    # exist — ``SchemaBase`` is extra="forbid", so a depositor following
    # that advice would get a 422. Naming the subject is not lost by using
    # root paths: one bundle is one reaction, declared once at the root.
    #
    # Deduplicated because several fits share one set of root fields, and N
    # identical warnings for one missing field is noise. Iterating the fits
    # rather than testing a single origin keeps a mixed-origin bundle
    # honest: a computed fit wants software provenance, a non-computed one
    # wants a literature anchor, and a bundle carrying both should say both.
    seen: set[tuple[str, str]] = set()
    for kin in request.kinetics:
        for warning in collect_provenance_warnings(
            scientific_origin=kin.scientific_origin,
            software_release=request.analysis_software_release,
            workflow_tool_release=request.workflow_tool_release,
            literature=request.literature,
            energy_level_of_theory=NOT_APPLICABLE,
        ):
            if (warning.field, warning.code) not in seen:
                seen.add((warning.field, warning.code))
                warnings.append(warning)

    return warnings


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
    # Conformer keys are scoped to the species that declared them, unlike
    # calc and geometry keys which are globally unique across the bundle.
    # That scoping is what makes an applied correction's
    # ``source_conformer_key`` owner-correct by construction: a species's
    # correction can only name that species's own conformers, so there is
    # no sibling-species conformer to borrow and no separate owner check
    # to write. Uniqueness within a species is enforced by the request
    # model, so this map loses nothing.
    observation_id_by_conformer_key: dict[str, dict[str, int]] = {}
    # Review-row targets accumulated as records are written so the
    # caller's ReviewPolicy can be applied at end-of-workflow.
    review_targets: list[RecordRef] = []
    applied_correction_ids: list[int] = []
    # Single-point energy reconciliation warnings from inline output logs.
    sp_energy_warnings: list[UploadWarning] = []
    # Provenance-presence warnings for the scientific products this bundle
    # carries. Request-derived only, so they are collected up front.
    sp_energy_warnings.extend(_collect_bundle_provenance_warnings(request))

    # ------------------------------------------------------------------
    # 1. Resolve species + conformers + calculations
    # ------------------------------------------------------------------
    for sp in request.species:
        # The first conformer's geometry drives 3D stereo label derivation;
        # every conformer's geometry is isotope-checked against the identity.
        conformer_geometries = [conf.geometry.to_payload() for conf in sp.conformers]
        species_entry = resolve_species_entry(
            session, sp.species_entry, created_by=created_by,
            geometry=(conformer_geometries[0] if conformer_geometries else None),
            additional_geometries=conformer_geometries[1:],
        )
        species_key_to_entry[sp.key] = species_entry
        review_targets.append(
            RecordRef(SubmissionRecordType.species_entry, species_entry.id)
        )

        # Conformers
        conformers_by_key = observation_id_by_conformer_key.setdefault(sp.key, {})
        for conf in sp.conformers:
            geom_payload = conf.geometry.to_payload()
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
            conformers_by_key[conf.key] = observation.id
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

    # Participant rows are kept, not just added: the atom map identifies an
    # atom by the participant *molecule* it belongs to, because the same
    # species may appear twice on one side and the two copies map to different
    # saddle-point atoms.
    structure_participants: list[tuple[str, ReactionEntryStructureParticipant]] = []
    for role, keys in (
        (ReactionRole.reactant, request.reactant_keys),
        (ReactionRole.product, request.product_keys),
    ):
        for idx, key in enumerate(keys, start=1):
            participant_row = ReactionEntryStructureParticipant(
                reaction_entry_id=canonical_reaction_entry.id,
                species_entry_id=species_key_to_entry[key].id,
                role=role,
                participant_index=idx,
                created_by=created_by,
            )
            session.add(participant_row)
            structure_participants.append((key, participant_row))
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

        # The saddle point must be made of this reaction's atoms, at this
        # reaction's charge. Checked for every deposit, mapped or not: an atom
        # map would catch a formula mismatch implicitly, but only where one was
        # supplied.
        validate_transition_state_composition(
            session,
            reaction_entry_id=canonical_reaction_entry.id,
            transition_state_charge=ts_in.charge,
            transition_state_smiles=ts_in.unmapped_smiles,
            transition_state_geometry_id=ts_geom.id,
            subject_label=ts_in.label or "transition state",
        )

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

        # Structured IRC evidence for this saddle point. Optional on every
        # path; its absence is reported, never rejected.
        persist_transition_state_validation_evidence(
            session,
            ts_in.validation_evidence,
            transition_state_entry_id=ts_entry.id,
            reconstruction_calculation_ids=[
                calculation_key_to_id[record.source_calculation_key]
                for record in ts_in.validation_evidence
            ],
            subject_label=ts_in.label or "transition state",
            field_path="transition_state.validation_evidence",
            reaction_entry_id=canonical_reaction_entry.id,
            transition_state_geometry_id=ts_geom.id,
            created_by=created_by,
            warnings=sp_energy_warnings,
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
    # 3b-bis. Atom map (ADR 0011)
    #
    # Sits outside the transition-state branch on purpose: the map belongs
    # to the micro reaction, and the *absence* of one has to be reported
    # for a reaction that has a saddle point but no map — which is exactly
    # the case the branch above would skip. Placed here because it needs
    # the reaction entry, its participant rows, the TS entry and every
    # geometry id at once, and all of those exist by this point.
    # ------------------------------------------------------------------
    atom_map_row = persist_reaction_atom_map(
        session,
        request.atom_map,
        reaction_entry_id=canonical_reaction_entry.id,
        transition_state_entry_id=ts_entry.id if ts_entry is not None else None,
        transition_state_geometry_id=(
            geometry_key_to_id[request.transition_state.geometry.key]
            if request.transition_state is not None
            else None
        ),
        participants=[
            ResolvedAtomMapParticipant(
                side=row.role,
                species_key=species_key,
                participant_index=row.participant_index,
                structure_participant_id=row.id,
            )
            for species_key, row in structure_participants
        ],
        geometry_id_by_key=geometry_key_to_id,
        subject_label=(
            (request.transition_state.label or "transition state")
            if request.transition_state is not None
            else "transition state"
        ),
        created_by=created_by,
        warnings=sp_energy_warnings,
    )

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
            source_conf_id = resolve_applied_correction_source_key(
                ac.source_conformer_key,
                observation_id_by_conformer_key.get(sp.key, {}),
                field=(
                    f"species['{sp.key}'].applied_energy_corrections[{i}]."
                    f"source_conformer_key"
                ),
                declares=(
                    "Every conformer under this species carries a required "
                    "'key'; 'source_conformer_key' must match one of them. "
                    "A sibling species's conformer is not in scope -- a "
                    "correction targeting one species entry cannot cite "
                    "another's structure."
                ),
            )
            applied = create_applied_energy_correction(
                session,
                ac,
                target_species_entry_id=species_entry.id,
                source_conformer_observation_id=source_conf_id,
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
            # A transition state in this bundle declares no conformers at
            # all -- it carries one geometry and its calculations, and no
            # ``ConformerObservation`` is written for it. So the namespace
            # is empty by construction, and any key names nothing. It is
            # refused rather than ignored for the same reason every other
            # source key is: a link the depositor believes they recorded
            # and did not is worse than a link they were told they cannot
            # make.
            source_conf_id = resolve_applied_correction_source_key(
                ac.source_conformer_key,
                {},
                field=(
                    f"transition_state.applied_energy_corrections[{i}]."
                    f"source_conformer_key"
                ),
                declares=(
                    "A computed-reaction bundle declares conformers only "
                    "under a species; its transition state has none, so a "
                    "TS-side correction cannot name one."
                ),
            )
            applied = create_applied_energy_correction(
                session,
                ac,
                target_transition_state_entry_id=ts_entry.id,
                source_conformer_observation_id=source_conf_id,
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

            # Per-thermo provenance overrides the bundle-level default.
            # The bundle value describes the run; a species whose thermo
            # came out of a different code or a paper says so here, and
            # falling back only when it stays silent keeps every deposit
            # written before these fields existed reading the same way.
            thermo_software_release = (
                resolve_software_release_ref(session, t.software_release)
                if t.software_release is not None
                else bundle_analysis_software_release
            )
            thermo_workflow_tool_release = (
                resolve_workflow_tool_release_ref(session, t.workflow_tool_release)
                if t.workflow_tool_release is not None
                else bundle_workflow_tool_release
            )
            thermo_literature = (
                resolve_or_create_literature(session, t.literature)
                if t.literature is not None
                else None
            )

            thermo = Thermo(
                species_entry_id=species_entry.id,
                scientific_origin=t.scientific_origin,
                literature_id=(
                    thermo_literature.id if thermo_literature is not None else None
                ),
                software_release_id=(
                    thermo_software_release.id
                    if thermo_software_release
                    else None
                ),
                workflow_tool_release_id=(
                    thermo_workflow_tool_release.id
                    if thermo_workflow_tool_release
                    else None
                ),
                h298_kj_mol=t.h298_kj_mol,
                s298_j_mol_k=t.s298_j_mol_k,
                h298_uncertainty_kj_mol=t.h298_uncertainty_kj_mol,
                s298_uncertainty_j_mol_k=t.s298_uncertainty_j_mol_k,
                tmin_k=t.tmin_k,
                tmax_k=t.tmax_k,
                note=t.note,
                created_by=created_by,
            )
            session.add(thermo)
            session.flush()
            thermo_ids.append(thermo.id)
            thermo_by_species_key[sp.key] = thermo

            # Which calculations produced this number. The schema already
            # refused a key that names nothing in the bundle; ownership is
            # checked here because this is the layer that knows which
            # species entry each key resolved to.
            for index, sc in enumerate(t.source_calculations):
                source_calc_id = calculation_key_to_id[sc.calculation_key]
                source_calc = session.get(Calculation, source_calc_id)
                assert_calculation_owned_by(
                    source_calc,
                    code=W_THERMO_SOURCE_CALCULATION_OWNER_MISMATCH,
                    target="thermo",
                    context=(
                        f"species[{sp.key!r}].thermo.source_calculations"
                        f"[{index}].calculation_key='{sc.calculation_key}'"
                    ),
                    species_entry_id=species_entry.id,
                )
                assert_thermo_role_matches_calculation_type(
                    source_calc,
                    role=sc.role,
                    context=(
                        f"species[{sp.key!r}].thermo.source_calculations"
                        f"[{index}].calculation_key='{sc.calculation_key}'"
                    ),
                )
                session.add(
                    ThermoSourceCalculation(
                        thermo_id=thermo.id,
                        calculation_id=source_calc_id,
                        role=sc.role,
                    )
                )

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

            # Per-species provenance overrides the bundle-level value, the
            # same precedence thermo uses above. Falling back rather than
            # replacing keeps every bundle that already relied on the
            # bundle-level values persisting exactly what it did before.
            statmech_software_release = (
                resolve_software_release_ref(session, s.software_release)
                if s.software_release is not None
                else bundle_analysis_software_release
            )
            statmech_workflow_tool_release = (
                resolve_workflow_tool_release_ref(session, s.workflow_tool_release)
                if s.workflow_tool_release is not None
                else bundle_workflow_tool_release
            )
            statmech_literature = (
                resolve_or_create_literature(session, s.literature)
                if s.literature is not None
                else None
            )

            statmech = Statmech(
                species_entry_id=species_entry.id,
                scientific_origin=s.scientific_origin,
                literature_id=(
                    statmech_literature.id if statmech_literature is not None else None
                ),
                software_release_id=(
                    statmech_software_release.id if statmech_software_release else None
                ),
                workflow_tool_release_id=(
                    statmech_workflow_tool_release.id
                    if statmech_workflow_tool_release
                    else None
                ),
                is_linear=s.is_linear,
                rigid_rotor_kind=s.rigid_rotor_kind,
                external_symmetry=s.external_symmetry,
                optical_isomers=s.optical_isomers,
                point_group=s.point_group,
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
                # The fourth statmech write path, and the one ARC actually
                # deposits through. Same DR-0028 Requirement 1 as the other
                # three, from the same shared service.
                assert_statmech_role_compatible(
                    calc_row,
                    role=sc.role,
                    context=(
                        f"species[{sp.key!r}].statmech.source_calculations[{i}]."
                        f"calculation_key='{sc.calculation_key}'"
                    ),
                )
                session.add(
                    StatmechSourceCalculation(
                        statmech_id=statmech.id,
                        calculation_id=calc_id,
                        role=sc.role,
                    )
                )

            for ti, torsion_in in enumerate(s.torsions):
                scan_calc_id: int | None = None
                if torsion_in.source_scan_calculation_key is not None:
                    scan_calc_id = calculation_key_to_id[
                        torsion_in.source_scan_calculation_key
                    ]
                    # ``calculation_key_to_id`` spans the whole bundle --
                    # every species and the transition state -- so a key
                    # resolving here says nothing about who owns what it
                    # resolved to. Without this, one species' hindered
                    # rotor can be parameterised by another species' scan
                    # and the record looks entirely well-formed. A torsion
                    # is a result and its scan is that result's
                    # provenance; a rotor potential borrowed from a
                    # different molecule is not weaker evidence, it is
                    # evidence about something else.
                    assert_calculation_owned_by(
                        session.get(Calculation, scan_calc_id),
                        code=W_STATMECH_TORSION_SCAN_CALCULATION_OWNER_MISMATCH,
                        target="statmech torsion",
                        context=(
                            f"species[{sp.key!r}].statmech.torsions[{ti}]."
                            f"source_scan_calculation_key="
                            f"'{torsion_in.source_scan_calculation_key}'"
                        ),
                        species_entry_id=species_entry.id,
                    )

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
        "atom_map_id": atom_map_row.id if atom_map_row is not None else None,
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
