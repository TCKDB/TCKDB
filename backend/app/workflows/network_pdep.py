"""Workflow orchestration for pressure-dependent network uploads.

Pipeline (single transaction):
1. Resolve species (local key → species_entry)
2. Process conformers (geometry + opt calc + conformer group/observation)
3. Process species-level additional calculations (sp, freq — with geometry_key
   lookups that anchor each calculation to the matching conformer observation)
3b. Process species-level transport (if provided)
4. Resolve micro reactions (local key → reaction_entry)
5. Process transition states (TS → TS entry → geometry → calcs)
6. Create network + states + channels + flat membership + reaction links
7. Create solve (with source_calculations using calc key→id map, plus
   fitted per-channel k(T,P) network kinetics referenced by state-key pair)
"""

from __future__ import annotations

import hashlib
import json

from sqlalchemy.orm import Session
from tckdb_schemas.upload_warning import UploadWarning

from app.chemistry.geometry import parse_xyz
from app.db.models.calculation import (
    Calculation,
    CalculationOutputGeometry,
)
from app.db.models.common import (
    CalculationGeometryRole,
    NetworkSpeciesRole,
    SubmissionRecordType,
)
from app.db.models.network import Network, NetworkReaction, NetworkSpecies
from app.db.models.network_pdep import (
    NetworkChannel,
    NetworkChannelMicroReaction,
    NetworkKinetics,
    NetworkKineticsChebyshev,
    NetworkKineticsPlog,
    NetworkSolve,
    NetworkSolveBathGas,
    NetworkSolveChannelBarrier,
    NetworkSolveEnergyTransfer,
    NetworkSolveSourceCalculation,
    NetworkSolveStateEnergy,
    NetworkState,
    NetworkStateParticipant,
)
from app.db.models.species import ConformerObservation
from app.db.models.transition_state import (
    TransitionState,
    TransitionStateEntry,
)
from app.schemas.fragments.geometry import GeometryPayload
from app.schemas.workflows.network_pdep_upload import (
    CalculationIn,
    NetworkPDepUploadRequest,
    calculation_in_to_with_results_payload,
)
from app.schemas.workflows.reaction_upload import (
    ReactionParticipantUpload,
    ReactionUploadRequest,
)
from app.services.artifact_persistence import persist_artifact
from app.services.calculation_geometry_composition import (
    assert_calculation_geometry_composition,
)
from app.services.calculation_resolution import (
    resolve_and_persist_calculation_with_results,
    resolve_workflow_tool_release_ref,
)
from app.services.conformer_resolution import resolve_conformer_group
from app.services.geometry_resolution import resolve_geometry_payload
from app.services.literature_resolution import resolve_or_create_literature
from app.services.local_key_resolution import (
    resolve_calculation_key,
    resolve_geometry_key,
    resolve_micro_reaction_key,
    resolve_network_channel_key,
    resolve_network_state_key,
    resolve_species_key,
    resolve_transition_state_key,
)
from app.services.provenance_warnings import (
    collect_network_energy_transfer_warnings,
    collect_network_solve_kind_warnings,
)
from app.services.reaction_atom_map import persist_reaction_atom_map
from app.services.reaction_resolution import (
    validate_transition_state_composition,
)
from app.services.record_review import (
    RecordRef,
    ReviewPolicy,
    apply_review_policy,
)
from app.services.software_resolution import resolve_software_release_ref
from app.services.species_resolution import resolve_species_entry
from app.services.transition_state_validation import (
    persist_transition_state_validation_evidence,
)
from app.services.transport_resolution import resolve_and_create_transport
from app.workflows.computed_species import _persist_statmech_block
from app.workflows.reaction import persist_reaction_upload

#: How the atom-map absence warning ends on this path.
#:
#: The generic remedy tells a depositor to supply ``atom_map``. The
#: pressure-dependent network bundle has no such field, so saying that here
#: would send them looking for something that does not exist — a warning that
#: cannot be acted on is the kind nobody reads, and ADR 0011 is explicit that a
#: warning nobody reads leaves the corpus splitting between mapped and unmapped
#: records for no reason. The honest statement is that this path cannot record
#: a map yet and which path can.
#:
#: Adding ``atom_map`` to the PDep bundle is the real fix. It is a wire-contract
#: change — new schema surface in ``tckdb_schemas``, a per-micro-reaction map
#: rather than the single per-bundle one the computed-reaction path takes, and
#: every client mirror — and is deliberately not folded into the change that
#: made the gap visible.
_PDEP_ABSENCE_REMEDY = (
    "The pressure-dependent network bundle cannot yet carry a map, so this "
    "gap cannot be closed on this deposit path: to record one for this micro "
    "reaction, deposit it through the computed-reaction upload, which accepts "
    "'atom_map' (ADR 0011)."
)


def _composition_hash(participants: list[tuple[int, int]]) -> str:
    """Compute a canonical SHA-256 hash for a network state composition."""
    canonical = sorted(participants)
    encoded = json.dumps(canonical, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _persist_calculation(
    session: Session,
    calc_in: CalculationIn,
    *,
    species_entry_id: int | None = None,
    transition_state_entry_id: int | None = None,
    geometry_id: int | None = None,
    geometry_key_map: dict[str, int],
    created_by: int | None = None,
) -> Calculation:
    """Persist one bundle-local calculation through the shared calculation seam.

    Routes provenance resolution, typed-result persistence, and parameter
    persistence through ``resolve_and_persist_calculation_with_results`` so
    bundle uploads inherit all shared-seam behavior. Bundle-specific
    orchestration (``geometry_key`` → ``geometry_id`` lookup and the
    ``CalculationOutputGeometry`` link with role=final) stays here.
    """

    effective_geometry_id = geometry_id
    if calc_in.geometry_key is not None:
        # Not merely defensive. ``validate_key_references`` checks a
        # calculation's geometry_key against the payload's *global* geometry
        # namespace, while this map holds only what the workflow has resolved
        # so far -- and ``TransitionStateIn``, unlike ``NetworkSpeciesIn``,
        # has no validator narrowing a TS calculation's geometry_key to its
        # own transition state. A TS naming a later TS's geometry therefore
        # passed the schema and reached a raw subscript here.
        effective_geometry_id = resolve_geometry_key(
            calc_in.geometry_key,
            geometry_key_map,
            field=f"calculations['{calc_in.key}'].geometry_key",
        )

    # The inline literature fragment is forwarded unresolved; the shared
    # persistence seam resolves it (#194). This route reaches the same
    # shared model as every other upload root, so the FK leak the
    # network-PDep no-FK gate used to exempt is gone with it.
    shared_payload = calculation_in_to_with_results_payload(calc_in)
    calculation = resolve_and_persist_calculation_with_results(
        session,
        shared_payload,
        species_entry_id=species_entry_id,
        transition_state_entry_id=transition_state_entry_id,
        created_by=created_by,
    )

    if effective_geometry_id is not None:
        # ``geometry_key`` resolves against a map spanning every species and
        # every transition state in the bundle, so this is a real refusal
        # rather than a restatement: a well's calculation naming another
        # well's geometry, or a transition state's naming a participant's,
        # reaches here with the wrong atoms.
        assert_calculation_geometry_composition(
            session,
            calc=calculation,
            geometry_id=effective_geometry_id,
            field=f"calculation '{calc_in.key}': geometry_key",
        )
        session.add(
            CalculationOutputGeometry(
                calculation_id=calculation.id,
                geometry_id=effective_geometry_id,
                output_order=1,
                role=CalculationGeometryRole.final,
            )
        )

    for artifact_in in calc_in.artifacts:
        persist_artifact(
            session,
            calculation_id=calculation.id,
            artifact_in=artifact_in,
            created_by=created_by,
        )

    session.flush()
    return calculation


def _anchor_species_calculation_to_observation(
    calculation: Calculation,
    calc_in: CalculationIn,
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


def _infer_species_role(
    state_kind: str,
    state_key: str,
    *,
    source_state_keys: set[str],
    sink_state_keys: set[str],
) -> NetworkSpeciesRole:
    """Infer a flat membership role for a species based on its state context."""
    if state_kind == "well":
        return NetworkSpeciesRole.well
    if state_key in source_state_keys and state_key not in sink_state_keys:
        return NetworkSpeciesRole.reactant
    if state_key in sink_state_keys and state_key not in source_state_keys:
        return NetworkSpeciesRole.product
    return NetworkSpeciesRole.reactant


def persist_network_pdep_upload(
    session: Session,
    request: NetworkPDepUploadRequest,
    *,
    created_by: int | None = None,
    review_policy: ReviewPolicy | None = ReviewPolicy(),
    warnings: list[UploadWarning] | None = None,
) -> Network:
    """Persist a complete pressure-dependent network upload workflow.

    Handles the full pipeline: species + conformers + calculations,
    transition states, micro reactions, network topology, and solve.

    :param warnings: Optional sink for non-blocking upload warnings (transition
        states deposited without IRC validation evidence; a network-wide
        ⟨ΔE⟩down declaration). Passed as an out-parameter so the return type
        stays the created ``Network``.
    """
    warning_sink = warnings if warnings is not None else []

    # Maps populated during resolution
    species_key_to_entry: dict[str, object] = {}
    geometry_key_to_id: dict[str, int] = {}
    calculation_key_to_id: dict[str, int] = {}
    # Parallel map of calc key → persisted Calculation ORM object, used by
    # the shared statmech seam (which resolves source calculations against
    # Calculation rows, checking species-entry ownership).
    calculation_key_to_calc: dict[str, Calculation] = {}
    reaction_key_to_entry: dict[str, object] = {}
    ts_key_to_entry: dict[str, TransitionStateEntry] = {}
    observation_id_by_geometry_key: dict[str, int] = {}
    # Review-row targets accumulated as records are written; used at the end
    # of the workflow to apply the caller's ReviewPolicy to all of them.
    review_targets: list[RecordRef] = []

    # ------------------------------------------------------------------
    # 1. Resolve species
    # ------------------------------------------------------------------
    for sp in request.species:
        conformer_geometries = [conf.geometry.to_payload() for conf in sp.conformers]
        species_entry = resolve_species_entry(
            session, sp.species_entry, created_by=created_by,
            # First geometry drives stereo perception; all of them are
            # isotope-checked against the declared identity.
            geometry=(conformer_geometries[0] if conformer_geometries else None),
            additional_geometries=conformer_geometries[1:],
        )
        species_key_to_entry[sp.key] = species_entry
        review_targets.append(
            RecordRef(SubmissionRecordType.species_entry, species_entry.id)
        )

    # ------------------------------------------------------------------
    # 2. Process conformers (geometry + opt calc + conformer observation)
    # ------------------------------------------------------------------
    for sp_index, sp in enumerate(request.species):
        species_entry = resolve_species_key(
            sp.key, species_key_to_entry, field=f"species[{sp_index}].key"
        )
        for conf in sp.conformers:
            # Resolve geometry
            geom_payload = conf.geometry.to_payload()
            geometry = resolve_geometry_payload(session, geom_payload)
            geometry_key_to_id[conf.geometry.key] = geometry.id

            # Create opt calculation
            calculation = _persist_calculation(
                session,
                conf.calculation,
                species_entry_id=species_entry.id,
                geometry_id=geometry.id,
                geometry_key_map=geometry_key_to_id,
                created_by=created_by,
            )
            calculation_key_to_id[conf.calculation.key] = calculation.id
            calculation_key_to_calc[conf.calculation.key] = calculation
            review_targets.append(
                RecordRef(SubmissionRecordType.calculation, calculation.id)
            )

            # Create conformer group + observation (with torsion matching)
            parsed = parse_xyz(GeometryPayload(xyz_text=conf.geometry.xyz_text))
            conformer_group, fingerprint, scheme = resolve_conformer_group(
                session,
                species_entry,
                label=conf.label,
                created_by=created_by,
                smiles=sp.species_entry.smiles,
                xyz_atoms=parsed.atoms,
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

            # Anchor the calculation to this conformer observation
            calculation.conformer_observation_id = observation.id

    # ------------------------------------------------------------------
    # 3. Process species-level additional calculations (sp, freq, etc.)
    # ------------------------------------------------------------------
    for sp_index, sp in enumerate(request.species):
        species_entry = resolve_species_key(
            sp.key, species_key_to_entry, field=f"species[{sp_index}].key"
        )
        for calc_in in sp.calculations:
            calculation = _persist_calculation(
                session,
                calc_in,
                species_entry_id=species_entry.id,
                geometry_key_map=geometry_key_to_id,
                created_by=created_by,
            )
            calculation_key_to_id[calc_in.key] = calculation.id
            calculation_key_to_calc[calc_in.key] = calculation
            review_targets.append(
                RecordRef(SubmissionRecordType.calculation, calculation.id)
            )
            _anchor_species_calculation_to_observation(
                calculation,
                calc_in,
                observation_id_by_geometry_key,
            )

    # ------------------------------------------------------------------
    # 3b. Process species-level transport
    # ------------------------------------------------------------------
    for sp_index, sp in enumerate(request.species):
        if sp.transport is not None:
            transport_row = resolve_and_create_transport(
                session,
                sp.transport,
                species_entry_id=resolve_species_key(
                    sp.key,
                    species_key_to_entry,
                    field=f"species[{sp_index}].key",
                ).id,
                created_by=created_by,
            )
            review_targets.append(
                RecordRef(SubmissionRecordType.transport, transport_row.id)
            )

    # ------------------------------------------------------------------
    # 3c. Process species-level statmech (reuses the bundle's shared seam)
    # ------------------------------------------------------------------
    for sp_index, sp in enumerate(request.species):
        if sp.statmech is None:
            continue
        statmech_row = _persist_statmech_block(
            session,
            sp.statmech,
            species_entry_id=resolve_species_key(
                sp.key, species_key_to_entry, field=f"species[{sp_index}].key"
            ).id,
            calc_keys_to_id=calculation_key_to_calc,
            created_by=created_by,
            warnings=warning_sink,
        )
        if statmech_row is not None:
            review_targets.append(
                RecordRef(SubmissionRecordType.statmech, statmech_row.id)
            )

    # ------------------------------------------------------------------
    # 4. Resolve micro reactions
    # ------------------------------------------------------------------
    for rxn_index, rxn in enumerate(request.micro_reactions):
        reaction_upload = ReactionUploadRequest(
            reversible=rxn.reversible,
            reaction_family=rxn.reaction_family,
            reaction_family_source_note=rxn.reaction_family_source_note,
            reactants=[
                ReactionParticipantUpload(
                    species_entry_id=resolve_species_key(
                        p.species_key,
                        species_key_to_entry,
                        field=(
                            f"micro_reactions[{rxn_index}].reactants[{i}]."
                            f"species_key"
                        ),
                    ).id,
                    note=p.note,
                )
                for i, p in enumerate(rxn.reactants)
            ],
            products=[
                ReactionParticipantUpload(
                    species_entry_id=resolve_species_key(
                        p.species_key,
                        species_key_to_entry,
                        field=(
                            f"micro_reactions[{rxn_index}].products[{i}]."
                            f"species_key"
                        ),
                    ).id,
                    note=p.note,
                )
                for i, p in enumerate(rxn.products)
            ],
        )
        reaction_entry = persist_reaction_upload(
            session,
            reaction_upload,
            created_by=created_by,
            review_policy=review_policy,
        )
        reaction_key_to_entry[rxn.key] = reaction_entry

    # ------------------------------------------------------------------
    # 5. Process transition states
    # ------------------------------------------------------------------
    for ts_index, ts_in in enumerate(request.transition_states):
        reaction_entry = resolve_micro_reaction_key(
            ts_in.micro_reaction_key,
            reaction_key_to_entry,
            field=f"transition_states[{ts_index}].micro_reaction_key",
        )

        # Create TransitionState (concept level)
        ts = TransitionState(
            reaction_entry_id=reaction_entry.id,
            label=ts_in.label,
            note=ts_in.note,
            created_by=created_by,
        )
        session.add(ts)
        session.flush()

        # Create TransitionStateEntry (candidate geometry)
        ts_entry = TransitionStateEntry(
            transition_state_id=ts.id,
            charge=ts_in.charge,
            multiplicity=ts_in.multiplicity,
            created_by=created_by,
        )
        session.add(ts_entry)
        session.flush()
        ts_key_to_entry[ts_in.key] = ts_entry
        review_targets.append(
            RecordRef(SubmissionRecordType.transition_state, ts.id)
        )
        review_targets.append(
            RecordRef(SubmissionRecordType.transition_state_entry, ts_entry.id)
        )

        # Resolve TS geometry
        ts_geom_payload = GeometryPayload(xyz_text=ts_in.geometry.xyz_text)
        ts_geometry = resolve_geometry_payload(session, ts_geom_payload)
        geometry_key_to_id[ts_in.geometry.key] = ts_geometry.id

        # The saddle point must be made of its micro reaction's atoms, at that
        # reaction's charge (ADR 0008: definitional, therefore blocking).
        validate_transition_state_composition(
            session,
            reaction_entry_id=reaction_entry.id,
            transition_state_charge=ts_in.charge,
            transition_state_geometry_id=ts_geometry.id,
            subject_label=ts_in.label or ts_in.key,
        )

        # Create TS opt calculation
        ts_calc = _persist_calculation(
            session,
            ts_in.calculation,
            transition_state_entry_id=ts_entry.id,
            geometry_id=ts_geometry.id,
            geometry_key_map=geometry_key_to_id,
            created_by=created_by,
        )
        calculation_key_to_id[ts_in.calculation.key] = ts_calc.id
        calculation_key_to_calc[ts_in.calculation.key] = ts_calc
        review_targets.append(
            RecordRef(SubmissionRecordType.calculation, ts_calc.id)
        )

        # Additional TS calculations (freq, sp, irc)
        for calc_in in ts_in.calculations:
            calc = _persist_calculation(
                session,
                calc_in,
                transition_state_entry_id=ts_entry.id,
                geometry_key_map=geometry_key_to_id,
                created_by=created_by,
            )
            calculation_key_to_id[calc_in.key] = calc.id
            calculation_key_to_calc[calc_in.key] = calc
            review_targets.append(
                RecordRef(SubmissionRecordType.calculation, calc.id)
            )

        if ts_in.statmech is not None:
            ts_statmech = _persist_statmech_block(
                session,
                ts_in.statmech,
                transition_state_entry_id=ts_entry.id,
                calc_keys_to_id=calculation_key_to_calc,
                created_by=created_by,
                warnings=warning_sink,
            )
            if ts_statmech is not None:
                review_targets.append(RecordRef(SubmissionRecordType.statmech, ts_statmech.id))

        persist_transition_state_validation_evidence(
            session,
            ts_in.validation_evidence,
            transition_state_entry_id=ts_entry.id,
            reconstruction_calculation_ids=[
                resolve_calculation_key(
                    evidence_in.source_calculation_key,
                    calculation_key_to_id,
                    field=(
                        f"transition_states['{ts_in.key}']."
                        f"validation_evidence[{index}].source_calculation_key"
                    ),
                )
                for index, evidence_in in enumerate(ts_in.validation_evidence)
            ],
            subject_label=ts_in.key,
            field_path=f"transition_states[{ts_in.key}].validation_evidence",
            reaction_entry_id=reaction_entry.id,
            transition_state_geometry_id=ts_geometry.id,
            created_by=created_by,
            warnings=warning_sink,
        )

        # Atom map (ADR 0011). A pressure-dependent network is a set of micro
        # reactions, and each saddle point here is one of them: the absence of
        # a map is exactly as invisible on this path as on any other, and the
        # ADR requires it be loud enough that a depositor who *has* the
        # mapping notices they are being asked for it. The map itself cannot
        # be deposited here yet — see ``_PDEP_ABSENCE_REMEDY`` — so the call
        # passes ``None`` unconditionally and exists to report the gap.
        persist_reaction_atom_map(
            session,
            None,
            reaction_entry_id=reaction_entry.id,
            transition_state_entry_id=ts_entry.id,
            transition_state_geometry_id=ts_geometry.id,
            participants=(),
            geometry_id_by_key={},
            # Points at the saddle point, not at ``.atom_map``: this bundle has
            # no such field, and a machine-readable pointer naming one would
            # send a client that highlights ``field`` to somewhere that does
            # not exist. ``absence_remedy`` carries where the map can go.
            field_path=f"transition_states[{ts_in.key}]",
            absence_remedy=_PDEP_ABSENCE_REMEDY,
            created_by=created_by,
            warnings=warning_sink,
        )

    # ------------------------------------------------------------------
    # 6. Resolve network-level provenance and create network
    # ------------------------------------------------------------------
    literature = (
        resolve_or_create_literature(session, request.literature)
        if request.literature is not None
        else None
    )
    software_release = (
        resolve_software_release_ref(session, request.software_release)
        if request.software_release is not None
        else None
    )
    workflow_tool_release = resolve_workflow_tool_release_ref(
        session, request.workflow_tool_release
    )

    network = Network(
        name=request.name,
        description=request.description,
        literature_id=literature.id if literature else None,
        software_release_id=software_release.id if software_release else None,
        workflow_tool_release_id=(
            workflow_tool_release.id if workflow_tool_release else None
        ),
        created_by=created_by,
    )
    session.add(network)
    session.flush()
    review_targets.append(RecordRef(SubmissionRecordType.network, network.id))

    # ------------------------------------------------------------------
    # 7. Create network states + participants
    # ------------------------------------------------------------------
    state_key_to_row: dict[str, NetworkState] = {}
    for state_index, state_in in enumerate(request.states):
        participants = [
            (
                resolve_species_key(
                    p.species_key,
                    species_key_to_entry,
                    field=(
                        f"states[{state_index}].participants[{i}].species_key"
                    ),
                ).id,
                p.stoichiometry,
            )
            for i, p in enumerate(state_in.participants)
        ]
        comp_hash = _composition_hash(participants)

        state = NetworkState(
            network_id=network.id,
            kind=state_in.kind,
            composition_hash=comp_hash,
            label=state_in.label,
        )
        session.add(state)
        session.flush()

        for i, p in enumerate(state_in.participants):
            session.add(
                NetworkStateParticipant(
                    state_id=state.id,
                    species_entry_id=resolve_species_key(
                        p.species_key,
                        species_key_to_entry,
                        field=(
                            f"states[{state_index}].participants[{i}]."
                            f"species_key"
                        ),
                    ).id,
                    stoichiometry=p.stoichiometry,
                )
            )

        state_key_to_row[state_in.key] = state

    session.flush()

    # ------------------------------------------------------------------
    # 8. Create channels
    # ------------------------------------------------------------------
    channel_key_to_row: dict[str, NetworkChannel] = {}
    for ch_index, ch_in in enumerate(request.channels):
        channel_row = NetworkChannel(
            network_id=network.id,
            source_state_id=resolve_network_state_key(
                ch_in.source_state_key,
                state_key_to_row,
                field=f"channels[{ch_index}].source_state_key",
            ).id,
            sink_state_id=resolve_network_state_key(
                ch_in.sink_state_key,
                state_key_to_row,
                field=f"channels[{ch_index}].sink_state_key",
            ).id,
            kind=ch_in.kind,
            mechanism=ch_in.mechanism,
            channel_key=ch_in.key,
        )
        session.add(channel_row)
    session.flush()
    channel_rows = session.query(NetworkChannel).filter(NetworkChannel.network_id == network.id).all()
    channel_key_to_row = {row.channel_key: row for row in channel_rows}
    for ch_index, ch_in in enumerate(request.channels):
        # Read back from the rows just inserted rather than from the payload,
        # so a channel the flush dropped is a named refusal and not a silent
        # miss.
        channel_row = resolve_network_channel_key(
            ch_in.key, channel_key_to_row, field=f"channels[{ch_index}].key"
        )
        for path_index, path in enumerate(ch_in.microreaction_paths):
            session.add(NetworkChannelMicroReaction(
                channel_id=channel_row.id,
                reaction_entry_id=resolve_micro_reaction_key(
                    path.micro_reaction_key,
                    reaction_key_to_entry,
                    field=(
                        f"channels[{ch_index}].microreaction_paths"
                        f"[{path_index}].micro_reaction_key"
                    ),
                ).id,
                transition_state_entry_id=(
                    resolve_transition_state_key(
                        path.transition_state_key,
                        ts_key_to_entry,
                        field=(
                            f"channels[{ch_index}].microreaction_paths"
                            f"[{path_index}].transition_state_key"
                        ),
                    ).id
                    if path.transition_state_key is not None
                    else None
                ),
            ))

    # ------------------------------------------------------------------
    # 9. Create flat membership (network_species + network_reaction)
    # ------------------------------------------------------------------
    source_state_keys = {ch.source_state_key for ch in request.channels}
    sink_state_keys = {ch.sink_state_key for ch in request.channels}

    seen_species_roles: set[tuple[int, NetworkSpeciesRole]] = set()
    for state_index, state_in in enumerate(request.states):
        role = _infer_species_role(
            state_in.kind,
            state_in.key,
            source_state_keys=source_state_keys,
            sink_state_keys=sink_state_keys,
        )
        for i, p in enumerate(state_in.participants):
            se_id = resolve_species_key(
                p.species_key,
                species_key_to_entry,
                field=f"states[{state_index}].participants[{i}].species_key",
            ).id
            pair = (se_id, role)
            if pair not in seen_species_roles:
                seen_species_roles.add(pair)
                session.add(
                    NetworkSpecies(
                        network_id=network.id,
                        species_entry_id=se_id,
                        role=role,
                    )
                )

    # Bath gas species
    if request.solve:
        for bg_index, bg in enumerate(request.solve.bath_gas):
            se_id = resolve_species_key(
                bg.species_key,
                species_key_to_entry,
                field=f"solve.bath_gas[{bg_index}].species_key",
            ).id
            pair = (se_id, NetworkSpeciesRole.bath_gas)
            if pair not in seen_species_roles:
                seen_species_roles.add(pair)
                session.add(
                    NetworkSpecies(
                        network_id=network.id,
                        species_entry_id=se_id,
                        role=NetworkSpeciesRole.bath_gas,
                    )
                )

    # Reaction links
    for _rxn_key, rxn_entry in reaction_key_to_entry.items():
        session.add(
            NetworkReaction(
                network_id=network.id,
                reaction_entry_id=rxn_entry.id,
            )
        )
    session.flush()

    # ------------------------------------------------------------------
    # 10. Create solve if provided
    # ------------------------------------------------------------------
    if request.solve:
        solve_in = request.solve

        solve_literature = (
            resolve_or_create_literature(session, solve_in.literature)
            if solve_in.literature is not None
            else None
        )
        solve_software = (
            resolve_software_release_ref(session, solve_in.software_release)
            if solve_in.software_release is not None
            else None
        )
        solve_workflow = resolve_workflow_tool_release_ref(
            session, solve_in.workflow_tool_release
        )

        solve = NetworkSolve(
            network_id=network.id,
            kind=solve_in.kind,
            me_method=solve_in.me_method,
            interpolation_model=solve_in.interpolation_model,
            tmin_k=solve_in.tmin_k,
            tmax_k=solve_in.tmax_k,
            pmin_bar=solve_in.pmin_bar,
            pmax_bar=solve_in.pmax_bar,
            grain_size_cm_inv=solve_in.grain_size_cm_inv,
            grain_count=solve_in.grain_count,
            emax_kj_mol=solve_in.emax_kj_mol,
            literature_id=solve_literature.id if solve_literature else None,
            software_release_id=solve_software.id if solve_software else None,
            workflow_tool_release_id=(
                solve_workflow.id if solve_workflow else None
            ),
            note=solve_in.note,
            created_by=created_by,
        )
        session.add(solve)
        session.flush()
        review_targets.append(
            RecordRef(SubmissionRecordType.network_solve, solve.id)
        )

        # Bath gas
        for bg_index, bg in enumerate(solve_in.bath_gas):
            session.add(
                NetworkSolveBathGas(
                    solve_id=solve.id,
                    species_entry_id=resolve_species_key(
                        bg.species_key,
                        species_key_to_entry,
                        field=f"solve.bath_gas[{bg_index}].species_key",
                    ).id,
                    mole_fraction=bg.mole_fraction,
                )
            )

        # Energy transfer. A network-wide declaration carries no state and no
        # collider by declaration; ``scope`` is what tells a later reader that
        # the NULLs are the record, not a dropped field (ADR 0009).
        for et_index, et in enumerate(solve_in.energy_transfer):
            session.add(
                NetworkSolveEnergyTransfer(
                    solve_id=solve.id,
                    scope=et.scope,
                    state_id=(
                        resolve_network_state_key(
                            et.state_key,
                            state_key_to_row,
                            field=(
                                f"solve.energy_transfer[{et_index}].state_key"
                            ),
                        ).id
                        if et.state_key is not None
                        else None
                    ),
                    collider_species_entry_id=(
                        resolve_species_key(
                            et.collider_species_key,
                            species_key_to_entry,
                            field=(
                                f"solve.energy_transfer[{et_index}]."
                                f"collider_species_key"
                            ),
                        ).id
                        if et.collider_species_key is not None
                        else None
                    ),
                    model=et.model,
                    alpha0_cm_inv=et.alpha0_cm_inv,
                    t_exponent=et.t_exponent,
                    t_ref_k=et.t_ref_k,
                    note=et.note,
                )
            )
        warning_sink.extend(
            collect_network_energy_transfer_warnings(solve_in)
        )
        warning_sink.extend(collect_network_solve_kind_warnings(solve_in))

        # The four lookups in this block and the next were guarded only
        # inside the ``kind == 'computed'`` branch of
        # ``validate_mechanistic_channel_evidence``: the coverage rules there
        # compare the supplied state/path keys against the topology, which
        # catches an undefined key as a side effect. A ``reported`` solve
        # (ADR 0010) is exempt from those rules and so was exempt from the
        # side effect, and reached these subscripts with whatever it wrote.
        for energy_index, energy_in in enumerate(solve_in.state_energies):
            session.add(NetworkSolveStateEnergy(
                solve_id=solve.id,
                state_id=resolve_network_state_key(
                    energy_in.state_key,
                    state_key_to_row,
                    field=f"solve.state_energies[{energy_index}].state_key",
                ).id,
                energy_kj_mol=energy_in.energy_kj_mol,
                energy_zero_convention=energy_in.energy_zero_convention,
                correction_convention=energy_in.correction_convention,
                convention_note=energy_in.convention_note,
                source_calculation_id=(
                    resolve_calculation_key(
                        energy_in.source_calculation_key,
                        calculation_key_to_id,
                        field=(
                            f"solve.state_energies[{energy_index}]."
                            f"source_calculation_key"
                        ),
                    )
                    if energy_in.source_calculation_key else None),
            ))

        for barrier_index, barrier_in in enumerate(solve_in.channel_barriers):
            session.add(NetworkSolveChannelBarrier(
                solve_id=solve.id,
                channel_id=resolve_network_channel_key(
                    barrier_in.channel_key,
                    channel_key_to_row,
                    field=f"solve.channel_barriers[{barrier_index}].channel_key",
                ).id,
                reaction_entry_id=resolve_micro_reaction_key(
                    barrier_in.micro_reaction_key,
                    reaction_key_to_entry,
                    field=(
                        f"solve.channel_barriers[{barrier_index}]."
                        f"micro_reaction_key"
                    ),
                ).id,
                transition_state_entry_id=resolve_transition_state_key(
                    barrier_in.transition_state_key,
                    ts_key_to_entry,
                    field=(
                        f"solve.channel_barriers[{barrier_index}]."
                        f"transition_state_key"
                    ),
                ).id,
                forward_barrier_kj_mol=barrier_in.forward_barrier_kj_mol,
                reverse_barrier_kj_mol=barrier_in.reverse_barrier_kj_mol,
                energy_zero_convention=barrier_in.energy_zero_convention,
                correction_convention=barrier_in.correction_convention,
                convention_note=barrier_in.convention_note,
                source_calculation_id=(
                    resolve_calculation_key(
                        barrier_in.source_calculation_key,
                        calculation_key_to_id,
                        field=(
                            f"solve.channel_barriers[{barrier_index}]."
                            f"source_calculation_key"
                        ),
                    )
                    if barrier_in.source_calculation_key else None),
            ))

        # Source calculations
        for sc_index, sc in enumerate(solve_in.source_calculations):
            session.add(
                NetworkSolveSourceCalculation(
                    solve_id=solve.id,
                    calculation_id=resolve_calculation_key(
                        sc.calculation_key,
                        calculation_key_to_id,
                        field=(
                            f"solve.source_calculations[{sc_index}]."
                            f"calculation_key"
                        ),
                    ),
                    role=sc.role,
                )
            )

        # Fitted phenomenological k(T,P) per channel. Schema validation
        # guarantees exactly one model sub-block matching ``model_kind``
        # (Chebyshev or PLOG); tabulated is rejected upstream.
        for nk_index, nk_in in enumerate(solve_in.channel_kinetics):
            channel_row = resolve_network_channel_key(
                nk_in.channel_key,
                channel_key_to_row,
                field=f"solve.channel_kinetics[{nk_index}].channel_key",
            )
            network_kinetics = NetworkKinetics(
                channel_id=channel_row.id,
                solve_id=solve.id,
                model_kind=nk_in.model_kind,
                tmin_k=nk_in.tmin_k,
                tmax_k=nk_in.tmax_k,
                pmin_bar=nk_in.pmin_bar,
                pmax_bar=nk_in.pmax_bar,
                rate_units=nk_in.rate_units,
                pressure_units=nk_in.pressure_units,
                temperature_units=nk_in.temperature_units,
                stores_log10_k=nk_in.stores_log10_k,
                note=nk_in.note,
            )
            session.add(network_kinetics)
            session.flush()

            cheb_in = nk_in.chebyshev
            if cheb_in is not None:
                session.add(
                    NetworkKineticsChebyshev(
                        network_kinetics_id=network_kinetics.id,
                        n_temperature=cheb_in.n_temperature,
                        n_pressure=cheb_in.n_pressure,
                        coefficients={"coeffs": cheb_in.coefficients},
                    )
                )

            # PLOG: one child row per pressure-indexed Arrhenius entry.
            # ``stores_log10_k`` stays None on the parent — PLOG carries a
            # real Arrhenius A, not a log10 fit.
            plog_in = nk_in.plog
            if plog_in is not None:
                for entry in plog_in.entries:
                    session.add(
                        NetworkKineticsPlog(
                            network_kinetics_id=network_kinetics.id,
                            pressure_bar=entry.pressure_bar,
                            entry_index=entry.entry_index,
                            a=entry.a,
                            a_units=entry.a_units,
                            n=entry.n,
                            ea_kj_mol=entry.ea_kj_mol,
                        )
                    )

        session.flush()

    apply_review_policy(
        session,
        targets=review_targets,
        policy=review_policy,
        created_by=created_by,
    )

    return network
