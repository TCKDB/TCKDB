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
7. Create solve (with source_calculations using calc key→id map)
"""

from __future__ import annotations

import hashlib
import json

from sqlalchemy.orm import Session

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
    NetworkSolve,
    NetworkSolveBathGas,
    NetworkSolveEnergyTransfer,
    NetworkSolveSourceCalculation,
    NetworkState,
    NetworkStateParticipant,
)
from app.db.models.species import ConformerObservation
from app.db.models.transition_state import TransitionState, TransitionStateEntry
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
from app.services.calculation_resolution import (
    resolve_and_persist_calculation_with_results,
    resolve_workflow_tool_release_ref,
)
from app.services.conformer_resolution import resolve_conformer_group
from app.services.geometry_resolution import resolve_geometry_payload
from app.services.literature_resolution import resolve_or_create_literature
from app.services.record_review import (
    RecordRef,
    ReviewPolicy,
    apply_review_policy,
)
from app.services.software_resolution import resolve_software_release_ref
from app.services.species_resolution import resolve_species_entry
from app.services.transport_resolution import resolve_and_create_transport
from app.workflows.reaction import persist_reaction_upload


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
        effective_geometry_id = geometry_key_map[calc_in.geometry_key]

    shared_payload = calculation_in_to_with_results_payload(calc_in)
    calculation = resolve_and_persist_calculation_with_results(
        session,
        shared_payload,
        species_entry_id=species_entry_id,
        transition_state_entry_id=transition_state_entry_id,
        created_by=created_by,
    )

    if effective_geometry_id is not None:
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
) -> Network:
    """Persist a complete pressure-dependent network upload workflow.

    Handles the full pipeline: species + conformers + calculations,
    transition states, micro reactions, network topology, and solve.
    """

    # Maps populated during resolution
    species_key_to_entry: dict[str, object] = {}
    geometry_key_to_id: dict[str, int] = {}
    calculation_key_to_id: dict[str, int] = {}
    reaction_key_to_entry: dict[str, object] = {}
    observation_id_by_geometry_key: dict[str, int] = {}
    # Review-row targets accumulated as records are written; used at the end
    # of the workflow to apply the caller's ReviewPolicy to all of them.
    review_targets: list[RecordRef] = []

    # ------------------------------------------------------------------
    # 1. Resolve species
    # ------------------------------------------------------------------
    for sp in request.species:
        first_xyz = sp.conformers[0].geometry.xyz_text if sp.conformers else None
        species_entry = resolve_species_entry(
            session, sp.species_entry, created_by=created_by,
            xyz_text=first_xyz,
        )
        species_key_to_entry[sp.key] = species_entry
        review_targets.append(
            RecordRef(SubmissionRecordType.species_entry, species_entry.id)
        )

    # ------------------------------------------------------------------
    # 2. Process conformers (geometry + opt calc + conformer observation)
    # ------------------------------------------------------------------
    for sp in request.species:
        species_entry = species_key_to_entry[sp.key]
        for conf in sp.conformers:
            # Resolve geometry
            geom_payload = GeometryPayload(xyz_text=conf.geometry.xyz_text)
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
    for sp in request.species:
        species_entry = species_key_to_entry[sp.key]
        for calc_in in sp.calculations:
            calculation = _persist_calculation(
                session,
                calc_in,
                species_entry_id=species_entry.id,
                geometry_key_map=geometry_key_to_id,
                created_by=created_by,
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

    # ------------------------------------------------------------------
    # 3b. Process species-level transport
    # ------------------------------------------------------------------
    for sp in request.species:
        if sp.transport is not None:
            transport_row = resolve_and_create_transport(
                session,
                sp.transport,
                species_entry_id=species_key_to_entry[sp.key].id,
                created_by=created_by,
            )
            review_targets.append(
                RecordRef(SubmissionRecordType.transport, transport_row.id)
            )

    # ------------------------------------------------------------------
    # 4. Resolve micro reactions
    # ------------------------------------------------------------------
    for rxn in request.micro_reactions:
        reaction_upload = ReactionUploadRequest(
            reversible=rxn.reversible,
            reaction_family=rxn.reaction_family,
            reaction_family_source_note=rxn.reaction_family_source_note,
            reactants=[
                ReactionParticipantUpload(
                    species_entry_id=species_key_to_entry[p.species_key].id,
                    note=p.note,
                )
                for p in rxn.reactants
            ],
            products=[
                ReactionParticipantUpload(
                    species_entry_id=species_key_to_entry[p.species_key].id,
                    note=p.note,
                )
                for p in rxn.products
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
    for ts_in in request.transition_states:
        reaction_entry = reaction_key_to_entry[ts_in.micro_reaction_key]

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
            review_targets.append(
                RecordRef(SubmissionRecordType.calculation, calc.id)
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
    for state_in in request.states:
        participants = [
            (species_key_to_entry[p.species_key].id, p.stoichiometry)
            for p in state_in.participants
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

        for p in state_in.participants:
            session.add(
                NetworkStateParticipant(
                    state_id=state.id,
                    species_entry_id=species_key_to_entry[p.species_key].id,
                    stoichiometry=p.stoichiometry,
                )
            )

        state_key_to_row[state_in.key] = state

    session.flush()

    # ------------------------------------------------------------------
    # 8. Create channels
    # ------------------------------------------------------------------
    for ch_in in request.channels:
        session.add(
            NetworkChannel(
                network_id=network.id,
                source_state_id=state_key_to_row[ch_in.source_state_key].id,
                sink_state_id=state_key_to_row[ch_in.sink_state_key].id,
                kind=ch_in.kind,
            )
        )
    session.flush()

    # ------------------------------------------------------------------
    # 9. Create flat membership (network_species + network_reaction)
    # ------------------------------------------------------------------
    source_state_keys = {ch.source_state_key for ch in request.channels}
    sink_state_keys = {ch.sink_state_key for ch in request.channels}

    seen_species_roles: set[tuple[int, NetworkSpeciesRole]] = set()
    for state_in in request.states:
        role = _infer_species_role(
            state_in.kind,
            state_in.key,
            source_state_keys=source_state_keys,
            sink_state_keys=sink_state_keys,
        )
        for p in state_in.participants:
            se_id = species_key_to_entry[p.species_key].id
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
        for bg in request.solve.bath_gas:
            se_id = species_key_to_entry[bg.species_key].id
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
        for bg in solve_in.bath_gas:
            session.add(
                NetworkSolveBathGas(
                    solve_id=solve.id,
                    species_entry_id=species_key_to_entry[bg.species_key].id,
                    mole_fraction=bg.mole_fraction,
                )
            )

        # Energy transfer
        if solve_in.energy_transfer:
            et = solve_in.energy_transfer
            session.add(
                NetworkSolveEnergyTransfer(
                    solve_id=solve.id,
                    model=et.model,
                    alpha0_cm_inv=et.alpha0_cm_inv,
                    t_exponent=et.t_exponent,
                    t_ref_k=et.t_ref_k,
                    note=et.note,
                )
            )

        # Source calculations
        for sc in solve_in.source_calculations:
            session.add(
                NetworkSolveSourceCalculation(
                    solve_id=solve.id,
                    calculation_id=calculation_key_to_id[sc.calculation_key],
                    role=sc.role,
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
