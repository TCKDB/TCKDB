"""A database world for the curated-ref guard: every gated type has a hidden instance linked from a visible one.

Used by the all-routes sweep (``test_curated_ref_sweep.py``) and by the guard's database tests. Built to be reused
by every later family PR: add a link here, not a new world.

Conventions
-----------
* ``*_v`` is visible under ``profile=curated`` (approved); ``*_h`` is hidden (``not_reviewed`` with no review row, or
  ``under_review``). Both hidden statuses stay visible under the default exploratory profile, so the exploratory pass
  can see every hidden record and the positive control means something.
* Link first, approve last: approval switches on accepted-science immutability, so a record's links exist before it
  is approved.
* Every hidden record has a visible sibling of the same role, so a "withhold" that quietly *substitutes* the sibling
  is distinguishable from one that withholds.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from sqlalchemy import text

from app.db.models.app_user import AppUser
from app.db.models.calculation import (
    CalculationCompositeInput,
    CalculationCompositeResult,
    CalculationCompositeTerm,
    CalculationIRCPoint,
    CalculationIRCResult,
    CalculationPathSearchPoint,
    CalculationPathSearchResult,
    CalculationScanCoordinate,
    CalculationScanPoint,
    CalculationScanResult,
)
from app.db.models.common import (
    AppUserRole,
    ArrheniusAUnits,
    AtomMapSource,
    CalculationDependencyRole,
    CalculationType,
    CompositeAssembly,
    CompositeInputSlot,
    IRCDirection,
    KineticsCalculationRole,
    KineticsDeterminationTargetKind,
    KineticsDirection,
    KineticsRepresentationRole,
    NetworkChannelKind,
    NetworkKineticsModelKind,
    NetworkSolveCalculationRole,
    NetworkSpeciesRole,
    NetworkStateKind,
    PathSearchMethod,
    ReactionRole,
    RecordReviewStatus,
    ScanCoordinateKind,
    StatmechCalculationRole,
    SubmissionRecordType,
    ThermoCalculationRole,
    TransportCalculationRole,
)
from app.services.record_review import ensure_record_review, set_record_review_status
from app.services.scientific_record_supersession import supersede_scientific_record
from tests.api.scientific._curated_sweep_selection_world import extend_for_selection_documents
from tests.services.scientific_read._factories import (
    attach_artifact,
    attach_conformer_selection,
    attach_dependency,
    attach_freq_result,
    attach_geometry_atoms,
    attach_geometry_validation,
    attach_network_kinetics_chebyshev,
    attach_network_kinetics_plog,
    attach_network_reaction,
    attach_network_solve_source_calculation,
    attach_network_species,
    attach_network_state_participant,
    attach_output_geometry,
    attach_scf_stability,
    attach_statmech_source_calculation,
    attach_statmech_torsion,
    attach_thermo_source_calculation,
    attach_transport_source_calculation,
    make_applied_energy_correction,
    make_calculation,
    make_chem_reaction,
    make_conformer_group,
    make_conformer_observation,
    make_energy_correction_scheme,
    make_frequency_scale_factor,
    make_geometry,
    make_kinetics,
    make_literature,
    make_lot,
    make_network,
    make_network_channel,
    make_network_kinetics,
    make_network_solve,
    make_network_state,
    make_observation,
    make_reaction_entry,
    make_software_release,
    make_species,
    make_species_entry,
    make_statmech,
    make_thermo_scalar,
    make_transition_state,
    make_transition_state_entry,
    make_transport,
    make_workflow_tool_release,
    next_inchi_key,
    set_review,
)

R = SubmissionRecordType
APPROVED = RecordReviewStatus.approved
UNDER_REVIEW = RecordReviewStatus.under_review

#: The record kinds that are gated, in the order the world names them. One hidden instance of each must exist.
GATED_KINDS = ("calc", "sm", "trn", "cg", "co", "ts", "tse", "net", "nsolve", "nkin", "thm", "kin", "mpo")


@dataclass
class SweepWorld:
    """Named records of the world. ``hidden``/``visible`` map a key to the ORM row."""

    hidden: dict[str, object] = field(default_factory=dict)
    visible: dict[str, object] = field(default_factory=dict)
    #: Anything else a route provider needs (entries, reaction, literature, geometry, ...).
    extra: dict[str, object] = field(default_factory=dict)

    def hidden_refs(self) -> dict[str, str]:
        return {k: v.public_ref for k, v in self.hidden.items()}

    def kind_of(self, key: str) -> str:
        return key.split("_", 1)[0]


def _approve(session, record_type, row):
    set_review(session, record_type=record_type, record_id=row.id, status=APPROVED)


def _hide(session, record_type, row, *, under_review: bool):
    """Leave a record hidden: no review row (not_reviewed), or an explicit under_review row."""
    if under_review:
        set_review(session, record_type=record_type, record_id=row.id, status=UNDER_REVIEW)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def build_sweep_world(session) -> SweepWorld:
    w = SweepWorld()
    hid, vis, ext = w.hidden, w.visible, w.extra

    lot = make_lot(session)
    lit = make_literature(session)
    ext["lot"], ext["literature"] = lot, lit
    ext["ecs"] = make_energy_correction_scheme(session, lot=lot, source_literature=lit)
    ext["fsf"] = make_frequency_scale_factor(session, lot=lot, source_literature=lit)

    # ---- species side ---------------------------------------------------------------------------------
    sa = make_species_entry(session, make_species(session, inchi_key=next_inchi_key("SWA")))
    ext["species_entry"] = sa

    # Conformers: cg_v holds co_v and the hidden observation co_h; cg_h (hidden) holds the visible observation co_v2.
    cg_v = make_conformer_group(session, sa, label="visible-group")
    cg_h = make_conformer_group(session, sa, label="hidden-group")
    co_v = make_conformer_observation(session, conformer_group=cg_v)
    co_h = make_conformer_observation(session, conformer_group=cg_v)
    co_v2 = make_conformer_observation(session, conformer_group=cg_h)
    vis["cg_v"], vis["co_v"], vis["co_v2"] = cg_v, co_v, co_v2
    hid["cg_h"], hid["co_h"] = cg_h, co_h

    # Calculations. calc_h is the hidden freq calculation most surfaces name; calc_vf is its visible sibling.
    calc_vopt = make_calculation(
        session, type=CalculationType.opt, species_entry_id=sa.id, lot_id=lot.id, conformer_observation_id=co_v2.id
    )  # visible calc whose conformer group is hidden
    calc_vsp = make_calculation(
        session, type=CalculationType.sp, species_entry_id=sa.id, lot_id=lot.id, conformer_observation_id=co_h.id
    )  # visible calc whose observation is hidden
    calc_h = make_calculation(session, type=CalculationType.freq, species_entry_id=sa.id, lot_id=lot.id)
    # calc_vf is run at its own level of theory, so a level derived from a substituted sibling differs from the true one.
    lot_vf = make_lot(session, method="sweepfreq", basis="def2svp")
    ext["lot_vf"] = lot_vf
    calc_vf = make_calculation(session, type=CalculationType.freq, species_entry_id=sa.id, lot_id=lot_vf.id)
    vis["calc_vopt"], vis["calc_vsp"], vis["calc_vf"] = calc_vopt, calc_vsp, calc_vf
    hid["calc_h"] = calc_h
    geom_h = make_geometry(session)
    attach_output_geometry(session, calculation=calc_h, geometry=geom_h)
    ext["geometry_hidden_calc"] = geom_h
    geom_v = make_geometry(session)
    attach_output_geometry(session, calculation=calc_vopt, geometry=geom_v)
    ext["geometry_visible_calc"] = geom_v
    ext["artifact_sha_visible"], ext["artifact_sha_hidden"] = "a" * 64, "b" * 64
    attach_artifact(session, calculation=calc_vopt, sha256="a" * 64, uri="s3://bucket/visible.log", filename="v.log")
    attach_artifact(session, calculation=calc_h, sha256="b" * 64, uri="s3://bucket/hidden.log", filename="h.log")
    attach_dependency(session, parent=calc_vopt, child=calc_h, role=CalculationDependencyRole.freq_on)
    attach_dependency(session, parent=calc_vopt, child=calc_vsp, role=CalculationDependencyRole.single_point_on)

    # Statmech: sm_v names the hidden freq calc and a hidden scan calc; sm_v2 is a visible sibling on the visible freq.
    sm_v = make_statmech(session, species_entry=sa)
    sm_v2 = make_statmech(session, species_entry=sa)
    sm_h = make_statmech(session, species_entry=sa)
    attach_statmech_source_calculation(session, statmech=sm_v, calculation=calc_vopt, role=StatmechCalculationRole.opt)
    attach_statmech_source_calculation(session, statmech=sm_v, calculation=calc_h, role=StatmechCalculationRole.freq)
    attach_statmech_source_calculation(session, statmech=sm_v, calculation=calc_vsp, role=StatmechCalculationRole.sp)
    attach_statmech_source_calculation(session, statmech=sm_v2, calculation=calc_vf, role=StatmechCalculationRole.freq)
    # sm_v also links the visible freq sibling, behind the thermo's own hidden freq link: a hide that filters the links before
    # picking falls through to calc_vf (the #767 bug class); the guard must leave that slot empty instead.
    attach_statmech_source_calculation(session, statmech=sm_v, calculation=calc_vf, role=StatmechCalculationRole.freq)
    attach_statmech_torsion(session, statmech=sm_v, source_scan_calculation=calc_h)
    vis["sm_v"], vis["sm_v2"] = sm_v, sm_v2
    hid["sm_h"] = sm_h

    # Transport: the hidden record shares the entry with the visible one; the visible one names the hidden calc.
    trn_v = make_transport(session, species_entry=sa)
    trn_h = make_transport(session, species_entry=sa, sigma_angstrom=3.9)
    attach_transport_source_calculation(
        session, transport=trn_v, calculation=calc_h, role=TransportCalculationRole.full_transport
    )
    vis["trn_v"] = trn_v
    hid["trn_h"] = trn_h

    # Thermo: thm_v rests on sm_v and names the hidden freq calc; thm_v2 rests on the hidden statmech; thm_h is hidden.
    thm_v = make_thermo_scalar(session, species_entry=sa, statmech_id=sm_v.id)
    thm_v2 = make_thermo_scalar(session, species_entry=sa, statmech_id=sm_h.id, h298_kj_mol=-20.0)
    thm_h = make_thermo_scalar(session, species_entry=sa, h298_kj_mol=-30.0)
    thm_h.literature_id = lit.id  # so /literature/{ref}/records names it
    session.flush()
    attach_thermo_source_calculation(session, thermo=thm_v, calculation=calc_vsp, role=ThermoCalculationRole.sp)
    attach_thermo_source_calculation(session, thermo=thm_v, calculation=calc_h, role=ThermoCalculationRole.freq)
    attach_thermo_source_calculation(session, thermo=thm_v, calculation=calc_vopt, role=ThermoCalculationRole.opt)
    vis["thm_v"], vis["thm_v2"] = thm_v, thm_v2
    hid["thm_h"] = thm_h

    # Molecular-property observations (literature-linked).
    mpo_v = make_observation(session, species_entry=sa, literature_id=lit.id)
    mpo_h = make_observation(session, species_entry=sa, scalar_value=2.5, literature_id=lit.id)
    vis["mpo_v"] = mpo_v
    hid["mpo_h"] = mpo_h

    # ---- reaction / transition-state side -----------------------------------------------------------
    r1 = make_species_entry(session, make_species(session, inchi_key=next_inchi_key("SWR1")))
    r2 = make_species_entry(session, make_species(session, inchi_key=next_inchi_key("SWR2")))
    p1 = make_species_entry(session, make_species(session, inchi_key=next_inchi_key("SWP1")))
    reaction = make_chem_reaction(session, reactants=[r1.species, r2.species], products=[p1.species], reversible=False)
    rxe = make_reaction_entry(session, reaction=reaction, reactant_entries=[r1, r2], product_entries=[p1])
    ext["reaction_entry"], ext["reaction"] = rxe, reaction

    # ts_v owns the hidden entry tse_h and a visible entry tse_v2; ts_h (hidden) owns the visible entry tse_v.
    ts_v = make_transition_state(session, reaction_entry=rxe, label="visible-ts")
    ts_h = make_transition_state(session, reaction_entry=rxe, label="hidden-ts")
    tse_v2 = make_transition_state_entry(session, transition_state=ts_v)
    tse_h = make_transition_state_entry(session, transition_state=ts_v, multiplicity=4)
    tse_v = make_transition_state_entry(session, transition_state=ts_h)
    vis["ts_v"], vis["tse_v"], vis["tse_v2"] = ts_v, tse_v, tse_v2
    hid["ts_h"], hid["tse_h"] = ts_h, tse_h

    calc_ts_v = make_calculation(session, type=CalculationType.opt, transition_state_entry_id=tse_v2.id, lot_id=lot.id)
    # A visible freq calculation at its own level of theory, for the visible same-role sibling shapes (a second TS statmech,
    # a second kinetics record). It is owned by the species entry, not by a TS entry of the reaction: a visible freq on the
    # reaction's entries would make the kinetics checklist (``has_ts_freq_evidence``) true under curated and defeat the D5
    # tests that rest on the reaction having only hidden freq calculations.
    lot_tsvf = make_lot(session, method="sweeptsfreq", basis="def2svp")
    calc_ts_vf = make_calculation(session, type=CalculationType.freq, species_entry_id=sa.id, lot_id=lot_tsvf.id)
    calc_ts_h = make_calculation(session, type=CalculationType.freq, transition_state_entry_id=tse_v2.id, lot_id=lot.id)
    calc_ts_v_on_h = make_calculation(
        session, type=CalculationType.opt, transition_state_entry_id=tse_h.id, lot_id=lot.id
    )
    vis["calc_ts_v"], vis["calc_ts_on_hidden_entry"] = calc_ts_v, calc_ts_v_on_h
    hid["calc_h_ts"] = calc_ts_h
    sm_ts_v = make_statmech_for_ts(session, tse_v2)
    sm_ts_h = make_statmech_for_ts(session, tse_v2)
    attach_statmech_source_calculation(
        session, statmech=sm_ts_v, calculation=calc_ts_v, role=StatmechCalculationRole.opt
    )
    attach_statmech_source_calculation(
        session, statmech=sm_ts_v, calculation=calc_ts_h, role=StatmechCalculationRole.freq
    )
    vis["sm_ts_v"] = sm_ts_v
    hid["sm_ts_h"] = sm_ts_h
    # A visible same-role sibling on the same entry: a second visible TS statmech that links a visible freq calculation
    # (at its own level of theory), so a hide that falls through to a sibling of the same role is visible as a change.
    sm_ts_v2 = make_statmech_for_ts(session, tse_v2)
    attach_statmech_source_calculation(
        session, statmech=sm_ts_v2, calculation=calc_ts_vf, role=StatmechCalculationRole.freq
    )
    vis["calc_ts_vf"], vis["sm_ts_v2"] = calc_ts_vf, sm_ts_v2

    # Kinetics: a visible and a hidden record on the reaction entry.
    kin_v = make_kinetics(session, reaction_entry=rxe)
    kin_h = make_kinetics(session, reaction_entry=rxe, a=3.0e-12)
    kin_h.literature_id = lit.id  # so /literature/{ref}/records names it
    session.flush()
    vis["kin_v"] = kin_v
    hid["kin_h"] = kin_h

    # ---- networks ---------------------------------------------------------------------------------------
    # net_h (hidden) has a visible solve + kinetics; net_v (visible) has a hidden solve + kinetics and a visible pair.
    net_h = make_network(session, name="hidden-net", literature_id=lit.id)
    net_v = make_network(session, name="visible-net", literature_id=lit.id)
    for net in (net_h, net_v):
        attach_network_species(session, network=net, species_entry=sa, role=NetworkSpeciesRole.well)
        attach_network_reaction(session, network=net, reaction_entry=rxe)
    vis["net_v"] = net_v
    hid["net_h"] = net_h

    def solve_with_kinetics(net, tag, source_calc):
        state_a = make_network_state(
            session, network=net, kind=NetworkStateKind.well, composition_hash=_hash(f"a-{tag}"), label="A"
        )
        state_b = make_network_state(
            session, network=net, kind=NetworkStateKind.well, composition_hash=_hash(f"b-{tag}"), label="B"
        )
        attach_network_state_participant(session, state=state_a, species_entry=sa)
        attach_network_state_participant(session, state=state_b, species_entry=sa)
        channel = make_network_channel(
            session,
            network=net,
            source_state=state_a,
            sink_state=state_b,
            kind=NetworkChannelKind.isomerization,
            channel_key=f"ch-{tag}",
        )
        solve = make_network_solve(session, network=net, literature_id=lit.id)
        attach_network_solve_source_calculation(
            session, solve=solve, calculation=source_calc, role=NetworkSolveCalculationRole.fit_source
        )
        nk = make_network_kinetics(
            session,
            channel=channel,
            solve=solve,
            model_kind=NetworkKineticsModelKind.chebyshev,
            rate_units=ArrheniusAUnits.cm3_mol_s,
        )
        attach_network_kinetics_chebyshev(session, kinetics=nk)
        return solve, nk

    nsolve_vh, nkin_vh = solve_with_kinetics(net_h, "hidden-net-visible-solve", calc_vopt)
    nsolve_v, nkin_v = solve_with_kinetics(net_v, "visible-net-visible-solve", calc_vopt)
    nsolve_h, nkin_h = solve_with_kinetics(net_v, "visible-net-hidden-solve", calc_h)
    vis["nsolve_v_under_hidden_net"], vis["nkin_v_under_hidden_net"] = nsolve_vh, nkin_vh
    vis["nsolve_v"], vis["nkin_v"] = nsolve_v, nkin_v
    hid["nsolve_h"], hid["nkin_h"] = nsolve_h, nkin_h

    # ---- shapes that need their own hidden-record link (PR-2a review) -----------------------------------------------
    # A visible calculation whose dependency PARENT is hidden: names it through ``supporting_calculations``.
    calc_vdep = make_calculation(session, type=CalculationType.sp, species_entry_id=sa.id, lot_id=lot.id)
    attach_dependency(session, parent=calc_h, child=calc_vdep, role=CalculationDependencyRole.single_point_on)
    vis["calc_vdep"] = calc_vdep

    # An assembled composite with one hidden and one visible input calculation.
    calc_comp = make_calculation(session, type=CalculationType.composite, species_entry_id=sa.id, lot_id=lot.id)
    session.add(CalculationCompositeResult(calculation_id=calc_comp.id, assembly=CompositeAssembly.assembled))
    session.flush()
    for position, inp in enumerate((calc_h, calc_vf)):
        session.add(
            CalculationCompositeInput(
                calculation_id=calc_comp.id,
                term_position=position,
                slot=CompositeInputSlot.value,
                input_calculation_id=inp.id,
            )
        )
        session.add(CalculationCompositeTerm(calculation_id=calc_comp.id, term_position=position, value_hartree=-1.0))
        attach_dependency(session, parent=inp, child=calc_comp, role=CalculationDependencyRole.composite_input)
    session.flush()
    vis["calc_comp"] = calc_comp

    # A hidden calculation with an output geometry, on a VISIBLE observation: reached through the conformer's
    # calculations and geometries includes.
    calc_hgeo = make_calculation(
        session, type=CalculationType.opt, species_entry_id=sa.id, lot_id=lot.id, conformer_observation_id=co_v.id
    )
    attach_output_geometry(session, calculation=calc_hgeo, geometry=make_geometry(session))
    hid["calc_hgeo"] = calc_hgeo

    # SCF stability whose origin calculation is hidden (the integer id sits beside the ref).
    scf = attach_scf_stability(session, calculation=calc_vopt)
    scf.source_calculation_id = calc_h.id
    session.flush()

    # Thermo protocol declaration citing a hidden and a visible calculation.
    thm_v.protocol_declaration = {
        "version": 1,
        "supporting_calculations": [{"calculation_ref": calc_h.public_ref}, {"calculation_ref": calc_vf.public_ref}],
    }
    session.flush()

    # A correction (from a visible calculation) that targets the HIDDEN entry tse_h.
    make_applied_energy_correction(
        session, target_transition_state_entry=tse_h, scheme=ext["ecs"], source_calculation=calc_vopt
    )

    # Geometry of a visible calculation owned by the hidden entry tse_h: names the entry's identity.
    geom_ts = make_geometry(session)
    attach_output_geometry(session, calculation=calc_ts_v_on_h, geometry=geom_ts)
    ext["geometry_hidden_ts_entry"] = geom_ts
    # A geometry shared by a species calculation and a calculation of tse_h: owners are ambiguous, one is hidden.
    geom_amb = make_geometry(session)
    calc_vamb = make_calculation(session, type=CalculationType.sp, species_entry_id=sa.id, lot_id=lot.id)
    calc_tsamb = make_calculation(session, type=CalculationType.sp, transition_state_entry_id=tse_h.id, lot_id=lot.id)
    attach_output_geometry(session, calculation=calc_vamb, geometry=geom_amb)
    attach_output_geometry(session, calculation=calc_tsamb, geometry=geom_amb)
    ext["geometry_ambiguous_owners"] = geom_amb
    vis["calc_vamb"], vis["calc_tsamb"] = calc_vamb, calc_tsamb

    # Scan / IRC / path-search results: one visible calculation each (the IRC and the path search belong to tse_h).
    calc_scan = make_calculation(session, type=CalculationType.scan, species_entry_id=sa.id, lot_id=lot.id)
    session.add(CalculationScanResult(calculation_id=calc_scan.id, dimension=1, is_relaxed=True))
    session.flush()
    session.add(
        CalculationScanCoordinate(
            calculation_id=calc_scan.id,
            coordinate_index=1,
            coordinate_kind=ScanCoordinateKind.bond,
            atom1_index=1,
            atom2_index=2,
        )
    )
    session.flush()
    for i in (1, 2):
        session.add(
            CalculationScanPoint(
                calculation_id=calc_scan.id, point_index=i, electronic_energy_hartree=-1.0 * i, geometry_id=geom_amb.id
            )
        )
    attach_dependency(session, parent=calc_h, child=calc_scan, role=CalculationDependencyRole.freq_on)
    calc_irc = make_calculation(session, type=CalculationType.irc, transition_state_entry_id=tse_h.id, lot_id=lot.id)
    session.add(CalculationIRCResult(calculation_id=calc_irc.id, point_count=2, zero_energy_reference_hartree=-1.0))
    session.flush()
    for i in (1, 2):
        session.add(
            CalculationIRCPoint(
                calculation_id=calc_irc.id,
                point_index=i,
                direction=IRCDirection.forward,
                is_ts=(i == 1),
                electronic_energy_hartree=-1.0 * i,
                geometry_id=geom_ts.id,
            )
        )
    calc_ps = make_calculation(
        session, type=CalculationType.path_search, transition_state_entry_id=tse_h.id, lot_id=lot.id
    )
    session.add(
        CalculationPathSearchResult(
            calculation_id=calc_ps.id,
            method=PathSearchMethod.neb,
            is_double_ended=True,
            converged=True,
            n_points=2,
        )
    )
    session.flush()
    for i in (1, 2):
        session.add(
            CalculationPathSearchPoint(
                calculation_id=calc_ps.id, point_index=i, electronic_energy_hartree=-1.0 * i, geometry_id=geom_ts.id
            )
        )
    session.flush()
    vis["calc_scan"], vis["calc_irc"], vis["calc_ps"] = calc_scan, calc_irc, calc_ps
    ext["scan_calc"], ext["irc_calc"], ext["path_search_calc"] = calc_scan, calc_irc, calc_ps

    # ---- PR-2b: the transition-state and reaction families --------------------------------------------------------
    _extend_for_ts_and_reaction_family(
        session,
        w,
        lot=lot,
        lit=lit,
        sa=sa,
        rxe=rxe,
        r1=r1,
        ts_v=ts_v,
        tse_v2=tse_v2,
        tse_h=tse_h,
        calc_ts_h=calc_ts_h,
        calc_ts_v=calc_ts_v,
        calc_vopt=calc_vopt,
        calc_h=calc_h,
        sm_h=sm_h,
        cg_h=cg_h,
        kin_v=kin_v,
        nkin_h=nkin_h,
        nkin_vh=nkin_vh,
        geom_ts=geom_ts,
    )

    # ---- PR-2c: the network, literature, level-of-theory, correction and analytics families -------------------------
    _extend_for_network_and_provenance_family(session, w, lot=lot, lit=lit, sa=sa, rxe=rxe)

    # ---- PR-4: the export streams ---------------------------------------------------------------------------------------
    _extend_for_export_family(session, w, lot=lot, rxe=rxe, r1=r1, r2=r2, tse_v2=tse_v2)

    # ---- guard PR-3: the selection documents (thermo, kinetics, network, structure, statmech, relative energies) ------
    ext["session"] = session
    extend_for_selection_documents(session, w, lot=lot, lit=lit, sa=sa)

    # ---- review states: hidden ones first (under_review for some), approvals last ---------------------------------
    under_review_keys = {"cg_h", "ts_h", "sm_h", "net_h", "kin_h", "tse_h", "nsolve_hsel"}
    kind_to_type = {
        "calc": R.calculation,
        "sm": R.statmech,
        "trn": R.transport,
        "cg": R.conformer_group,
        "co": R.conformer_observation,
        "ts": R.transition_state,
        "tse": R.transition_state_entry,
        "net": R.network,
        "nsolve": R.network_solve,
        "thm": R.thermo,
        "kin": R.kinetics,
        "mpo": R.molecular_property_observation,
    }
    for key, row in hid.items():
        kind = key.split("_", 1)[0]
        if kind == "nkin" or key in ("thm_h", "kin_h", "sm_hsucc"):
            continue  # nkin is reviewed through its solve; thm_h, kin_h and sm_hsucc are moved by the supersession blocks below
        _hide(session, kind_to_type[kind], row, under_review=key in under_review_keys)

    # Scoping parents are approved too: some reads (structure selection) gate the entry they are scoped to.
    for scoping, record_type in (
        (sa, R.species_entry),
        (rxe, R.reaction_entry),
        (r1, R.species_entry),
        (r2, R.species_entry),
        (p1, R.species_entry),
    ):
        _approve(session, record_type, scoping)
    for record_type, scoping in ext["scoping_approved_extra"]:
        _approve(session, record_type, scoping)

    # Visible: order matters only in that links already exist (they do). ``thm_v`` is approved below, through the
    # service, because it is also the legacy superseded record.
    for key, row in vis.items():
        kind = key.split("_", 1)[0]
        if kind == "nkin" or key in ("thm_v", "kin_vs", "sm_vsup"):
            continue
        _approve(session, kind_to_type[kind], row)

    # Legacy supersession: an approved predecessor whose successor is hidden. The write path deprecates the
    # predecessor, and since #769 refuses to re-approve a superseded record. Rows approved before that guard are the
    # state being reproduced (a curated-visible record names a hidden successor in its supersession notice), so the
    # predecessor is put back to approved with a direct write, as #769's own legacy-row test does.
    curator = AppUser(username="sweep-world-curator", role=AppUserRole.curator)
    session.add(curator)
    session.flush()
    ext["curator"] = curator
    # The write path requires an approved successor, so thm_h is approved for the edge and moved back to under_review.
    for row in (thm_v, thm_h):
        ensure_record_review(session, record_type=R.thermo, record_id=row.id)
        set_record_review_status(session, record_type=R.thermo, record_id=row.id, status=APPROVED, actor=curator)
    supersede_scientific_record(
        session,
        record_type=R.thermo,
        superseded_record_id=thm_v.id,
        superseding_record_id=thm_h.id,
        actor=curator,
        reason="legacy edge: approved predecessor names a hidden successor",
    )
    session.execute(
        text("UPDATE record_review SET status = 'approved' WHERE record_type = 'thermo' AND record_id = :id"),
        {"id": thm_v.id},
    )
    session.expire_all()
    set_record_review_status(session, record_type=R.thermo, record_id=thm_h.id, status=UNDER_REVIEW, actor=curator)

    # The same legacy edge for kinetics: an approved predecessor (kin_vs) names a hidden successor (kin_h).
    kin_vs, kin_h = vis["kin_vs"], hid["kin_h"]
    for row in (kin_vs, kin_h):
        ensure_record_review(session, record_type=R.kinetics, record_id=row.id)
        set_record_review_status(session, record_type=R.kinetics, record_id=row.id, status=APPROVED, actor=curator)
    supersede_scientific_record(
        session,
        record_type=R.kinetics,
        superseded_record_id=kin_vs.id,
        superseding_record_id=kin_h.id,
        actor=curator,
        reason="legacy edge: approved predecessor names a hidden successor",
    )
    session.execute(
        text("UPDATE record_review SET status = 'approved' WHERE record_type = 'kinetics' AND record_id = :id"),
        {"id": kin_vs.id},
    )
    session.expire_all()
    set_record_review_status(session, record_type=R.kinetics, record_id=kin_h.id, status=UNDER_REVIEW, actor=curator)

    # The same legacy edge for statmech (guard PR-3): an approved predecessor whose supersession notice names a hidden
    # successor, so a statmech selection candidate carries that ref in its notice.
    sm_vsup, sm_hsucc = vis["sm_vsup"], hid["sm_hsucc"]
    for row in (sm_vsup, sm_hsucc):
        ensure_record_review(session, record_type=R.statmech, record_id=row.id)
        set_record_review_status(session, record_type=R.statmech, record_id=row.id, status=APPROVED, actor=curator)
    supersede_scientific_record(
        session,
        record_type=R.statmech,
        superseded_record_id=sm_vsup.id,
        superseding_record_id=sm_hsucc.id,
        actor=curator,
        reason="legacy edge: approved predecessor names a hidden successor",
    )
    session.execute(
        text("UPDATE record_review SET status = 'approved' WHERE record_type = 'statmech' AND record_id = :id"),
        {"id": sm_vsup.id},
    )
    session.expire_all()
    set_record_review_status(session, record_type=R.statmech, record_id=sm_hsucc.id, status=UNDER_REVIEW, actor=curator)
    return w


def make_statmech_for_ts(session, tse):
    from app.db.models.common import ScientificOriginKind, StatmechTreatmentKind
    from app.db.models.statmech import Statmech

    sm = Statmech(
        transition_state_entry_id=tse.id,
        scientific_origin=ScientificOriginKind.computed,
        statmech_treatment=StatmechTreatmentKind.rrho,
    )
    session.add(sm)
    session.flush()
    return sm


def _add_irc(session, calc, geometry) -> None:
    session.add(CalculationIRCResult(calculation_id=calc.id, point_count=2, zero_energy_reference_hartree=-1.0))
    session.flush()
    for i in (1, 2):
        session.add(
            CalculationIRCPoint(
                calculation_id=calc.id,
                point_index=i,
                direction=IRCDirection.forward,
                is_ts=(i == 1),
                electronic_energy_hartree=-1.0 * i,
                geometry_id=geometry.id,
            )
        )
    session.flush()


def _add_path_search(session, calc, geometry) -> None:
    session.add(
        CalculationPathSearchResult(
            calculation_id=calc.id, method=PathSearchMethod.neb, is_double_ended=True, converged=True, n_points=2
        )
    )
    session.flush()
    for i in (1, 2):
        session.add(
            CalculationPathSearchPoint(
                calculation_id=calc.id, point_index=i, electronic_energy_hartree=-1.0 * i, geometry_id=geometry.id
            )
        )
    session.flush()


def _atom_map(session, *, reaction_entry, tse, participant):
    """A declared one-pair atom map of ``tse`` (the pair binds a reactant atom to a saddle-point atom)."""
    from app.db.models.reaction_atom_map import ReactionAtomMap, ReactionAtomMapPair

    reactant_geometry = make_geometry(session, natoms=2)
    ts_geometry = make_geometry(session, natoms=2)
    for geometry in (reactant_geometry, ts_geometry):
        attach_geometry_atoms(session, geometry=geometry, symbols=["H", "H"], coords=[[0.0, 0.0, 0.0], [0.0, 0.0, 0.9]])
    atom_map = ReactionAtomMap(
        reaction_entry_id=reaction_entry.id,
        transition_state_entry_id=tse.id,
        transition_state_geometry_id=ts_geometry.id,
        source=AtomMapSource.declared,
    )
    session.add(atom_map)
    session.flush()
    session.add(
        ReactionAtomMapPair(
            atom_map_id=atom_map.id,
            side=ReactionRole.reactant,
            structure_participant_id=participant.id,
            geometry_id=reactant_geometry.id,
            atom_index=1,
            transition_state_geometry_id=ts_geometry.id,
            ts_atom_index=1,
            element="H",
            ts_element="H",
        )
    )
    session.flush()
    return atom_map


def _extend_for_ts_and_reaction_family(
    session,
    w: SweepWorld,
    *,
    lot,
    lit,
    sa,
    rxe,
    r1,
    ts_v,
    tse_v2,
    tse_h,
    calc_ts_h,
    calc_ts_v,
    calc_vopt,
    calc_h,
    sm_h,
    cg_h,
    kin_v,
    nkin_h,
    nkin_vh,
    geom_ts,
) -> None:
    """Every response shape of the transition-state, reaction-entry, reaction and kinetics routes.

    PR-2a's review found seven blocks that failed closed under curated because the world never built them, and every
    route "answered 200" on a world without the shape. So the world holds, for this family, a hidden record in each
    place a visible one can name it:

    * a hidden TS with a visible entry (``ts_h`` / ``tse_v``), a hidden entry under a visible TS (``tse_h`` under
      ``ts_v``), and a visible TS whose entries are one of each;
    * hidden IRC, path-search, single-point and freq calculations on the VISIBLE entry ``tse_v2``, each with a visible
      sibling where the shape has one, and the validation evidence that cites them;
    * statmech on a visible entry, and a VISIBLE statmech on the HIDDEN entry;
    * visible kinetics that cite hidden calculations, a hidden entry, a hidden statmech and conformer group, a hidden
      network and a hidden network kinetics, and a superseded kinetics naming a hidden successor;
    * an atom map on a hidden entry and on a visible one;
    * free text (a TS note, a kinetics note) that names a hidden record;
    * UNREVIEWED reaction and species entries. They are scoping parents, not gated records: the curated profile hides
      no route of theirs, so the world holds them to show the guard hides nothing about them either.
    """

    from app.db.models.kinetics import (
        KineticsDetermination,
        KineticsInterpretationAssignment,
        KineticsSourceCalculation,
        KineticsTunnelingApplication,
    )
    from app.db.models.transition_state import TransitionStateValidationEnergy, TransitionStateValidationEvidence

    vis, hid, ext = w.visible, w.hidden, w.extra

    # -- hidden calculations on the visible entry tse_v2, each with a visible sibling ---------------------------------
    def calc_on_tse_v2(type_, *, hidden=False):
        # The hidden ones carry no level of theory: their shape here is the entry's evidence, and a hidden calculation
        # with a level would add the same ``used_by`` leaks the level-of-theory family (PR-2c) already lists.
        return make_calculation(
            session, type=type_, transition_state_entry_id=tse_v2.id, lot_id=None if hidden else lot.id
        )

    # The visible sibling is created first, so the hidden calculation is the most recent of its type on the entry (what a
    # "most recent wins" slot picks) and a hide that falls through to a sibling lands on the visible one.
    calc_ts_sp_v, calc_h_tssp = calc_on_tse_v2(CalculationType.sp), calc_on_tse_v2(CalculationType.sp, hidden=True)
    calc_irc_v, calc_h_irc = calc_on_tse_v2(CalculationType.irc), calc_on_tse_v2(CalculationType.irc, hidden=True)
    calc_ps_v, calc_h_ps = (
        calc_on_tse_v2(CalculationType.path_search),
        calc_on_tse_v2(CalculationType.path_search, hidden=True),
    )
    for calc in (calc_h_irc, calc_irc_v):
        _add_irc(session, calc, geom_ts)
    for calc in (calc_h_ps, calc_ps_v):
        _add_path_search(session, calc, geom_ts)
    hid["calc_h_tssp"], hid["calc_h_irc"], hid["calc_h_ps"] = calc_h_tssp, calc_h_irc, calc_h_ps
    vis["calc_ts_sp_v"], vis["calc_irc_v"], vis["calc_ps_v"] = calc_ts_sp_v, calc_irc_v, calc_ps_v
    ext["irc_calc_on_visible_entry"], ext["path_search_calc_on_visible_entry"] = calc_irc_v, calc_ps_v
    # Artifacts: the tunneling application below cites the freq's and the IRC's.
    freq_artifact = attach_artifact(
        session, calculation=calc_ts_h, sha256="c" * 64, uri="s3://bucket/ts-freq.log", filename="tf.log"
    )
    irc_artifact = attach_artifact(
        session, calculation=calc_h_irc, sha256="d" * 64, uri="s3://bucket/ts-irc.dat", filename="ti.dat"
    )
    # Geometry validation and SCF stability on the hidden calculation the kinetics provenance resolves to.
    attach_geometry_validation(session, calculation=calc_h_tssp)
    attach_scf_stability(session, calculation=calc_h_tssp)

    # -- TS validation evidence on tse_v2 citing them ----------------------------------------------------------------
    irc_ev = TransitionStateValidationEvidence(
        transition_state_entry_id=tse_v2.id,
        kind="irc",
        passed=True,
        rationale="IRC connects the declared wells",
        reconstruction_calculation_id=calc_h_irc.id,
    )
    mode_ev = TransitionStateValidationEvidence(
        transition_state_entry_id=tse_v2.id,
        kind="imaginary_mode",
        passed=True,
        rationale="one imaginary mode along the reaction coordinate",
        reconstruction_calculation_id=calc_ts_h.id,
        imaginary_frequency_count=1,
        imaginary_frequency_cm1=-1234.5,
        mode_displacement_agrees=True,
    )
    order_ev = TransitionStateValidationEvidence(
        transition_state_entry_id=tse_v2.id,
        kind="energy_ordering",
        passed=True,
        rationale="the saddle point lies above both wells",
    )
    session.add_all([irc_ev, mode_ev, order_ev])
    session.flush()
    session.add_all(
        [
            TransitionStateValidationEnergy(
                evidence_id=order_ev.id,
                participant="ts",
                energy_kind="electronic",
                energy_hartree=-1.0,
                source_calculation_id=calc_h_tssp.id,
            ),
            TransitionStateValidationEnergy(
                evidence_id=order_ev.id,
                participant="ts",
                energy_kind="e0",
                energy_hartree=-0.9,
                source_calculation_id=calc_ts_sp_v.id,
            ),
        ]
    )
    session.flush()

    # -- more shapes of the entry's evidence: a hidden scan and optimisation, the dependency edges between the entry's
    #    calculations, and the freq result the saddle-point verdict is read from --------------------------------------
    calc_h_scan, calc_scan_v = calc_on_tse_v2(CalculationType.scan, hidden=True), calc_on_tse_v2(CalculationType.scan)
    for calc in (calc_h_scan, calc_scan_v):
        session.add(CalculationScanResult(calculation_id=calc.id, dimension=1, is_relaxed=True))
        session.flush()
        session.add(
            CalculationScanCoordinate(
                calculation_id=calc.id,
                coordinate_index=1,
                coordinate_kind=ScanCoordinateKind.bond,
                atom1_index=1,
                atom2_index=2,
            )
        )
        session.flush()
        for i in (1, 2):
            session.add(
                CalculationScanPoint(
                    calculation_id=calc.id, point_index=i, electronic_energy_hartree=-1.0 * i, geometry_id=geom_ts.id
                )
            )
        session.flush()
    calc_h_tsopt = calc_on_tse_v2(CalculationType.opt, hidden=True)
    hid["calc_h_scan"], hid["calc_h_tsopt"] = calc_h_scan, calc_h_tsopt
    vis["calc_scan_on_visible_entry"] = calc_scan_v
    attach_geometry_validation(session, calculation=calc_h_tsopt)
    attach_dependency(session, parent=calc_ts_v, child=calc_ts_sp_v, role=CalculationDependencyRole.single_point_on)
    attach_dependency(session, parent=calc_ts_h, child=calc_ts_sp_v, role=CalculationDependencyRole.freq_on)
    attach_dependency(session, parent=calc_ts_v, child=calc_ts_h, role=CalculationDependencyRole.freq_on)
    attach_freq_result(session, calculation=calc_ts_h, frequencies_cm1=[-1234.5, 345.0, 678.0])
    # Conformers of a reaction participant: a hidden group and a visible one (reached through /full?include=conformers).
    cg_h_rxn = make_conformer_group(session, r1, label="hidden-participant-group")
    cg_v_rxn = make_conformer_group(session, r1, label="visible-participant-group")
    hid["cg_h_rxn"], vis["cg_v_rxn"] = cg_h_rxn, cg_v_rxn

    # -- a VISIBLE statmech on the hidden entry, and free text that names a hidden record ----------------------------
    sm_ts_on_hidden = make_statmech_for_ts(session, tse_h)
    attach_statmech_source_calculation(
        session,
        statmech=sm_ts_on_hidden,
        calculation=make_calculation(
            session, type=CalculationType.freq, transition_state_entry_id=tse_h.id, lot_id=lot.id
        ),
        role=StatmechCalculationRole.freq,
    )
    vis["sm_ts_on_hidden_entry"] = sm_ts_on_hidden
    ts_v.note = f"reviewed against {calc_ts_h.public_ref}"

    # -- atom maps: one on the hidden entry, one on the visible entry --------------------------------------------------
    participant = rxe.structure_participants[0]
    _atom_map(session, reaction_entry=rxe, tse=tse_h, participant=participant)
    _atom_map(session, reaction_entry=rxe, tse=tse_v2, participant=participant)

    # -- kinetics ---------------------------------------------------------------------------------------------------
    def source(kinetics, calc, role):
        session.add(KineticsSourceCalculation(kinetics_id=kinetics.id, calculation_id=calc.id, role=role))

    source(kin_v, calc_ts_h, KineticsCalculationRole.freq)
    source(kin_v, calc_h_tssp, KineticsCalculationRole.ts_energy)
    source(kin_v, calc_h_irc, KineticsCalculationRole.irc)
    source(kin_v, calc_h_ps, KineticsCalculationRole.fit_source)
    source(kin_v, calc_h_tsopt, KineticsCalculationRole.fit_source)  # the optimisation the geometry validation hangs on
    # A second visible record whose single-slot picks (first freq / ts-energy / irc / path search / opt by role order)
    # resolve to HIDDEN calculations while a visible calculation of the same type is linked after them: a hide that
    # filters the links before picking would resolve to the visible one, which the withholding-only check catches.
    kin_vsl = make_kinetics(session, reaction_entry=rxe, a=4.0e-12, ea_kj_mol=25.0)
    session.flush()
    for calc, role in (
        (calc_ts_h, KineticsCalculationRole.freq),
        (calc_h_tssp, KineticsCalculationRole.ts_energy),
        (calc_h_irc, KineticsCalculationRole.irc),
        (calc_h_ps, KineticsCalculationRole.fit_source),
        (calc_h_tsopt, KineticsCalculationRole.fit_source),
        (vis["calc_ts_vf"], KineticsCalculationRole.fit_source),
        (calc_ts_sp_v, KineticsCalculationRole.ts_energy),
        (calc_irc_v, KineticsCalculationRole.fit_source),
        (calc_ps_v, KineticsCalculationRole.fit_source),
        (vis["calc_ts_v"], KineticsCalculationRole.fit_source),
    ):
        source(kin_vsl, calc, role)
    vis["kin_vsl"] = kin_vsl
    session.flush()
    kin_v.network_kinetics_id = nkin_h.id  # the bridge to a hidden network kinetics
    kin_v.note = f"fit to {calc_h_tssp.public_ref}"
    kin_v.protocol_declaration = {
        "version": 1,
        "supporting_calculations": [
            {"calculation_ref": calc_h.public_ref, "purpose": "frequency"},
            {"calculation_ref": calc_vopt.public_ref, "purpose": "geometry"},
        ],
    }
    determination = KineticsDetermination(
        reaction_entry_id=rxe.id,
        direction=KineticsDirection.forward,
        target_kind=KineticsDeterminationTargetKind.resolved_channel,
        target_transition_state_entry_id=tse_h.id,
        literature_id=lit.id,
        determination_key="sweep-hidden-ts-entry",
        identity_hash=_hash("sweep-hidden-ts-entry"),
    )
    session.add(determination)
    session.flush()
    kin_v.determination_id = determination.id
    kin_v.representation_role = KineticsRepresentationRole.complete
    selection = attach_conformer_selection(session, conformer_group=cg_h)
    session.add_all(
        [
            KineticsInterpretationAssignment(
                kinetics_id=kin_v.id,
                subject_key="transition_state",
                role="transition_state",
                statmech_id=sm_h.id,
                transition_state_entry_id=tse_h.id,
                ensemble_policy="lowest_energy_conformer",
                standard_state_convention="ideal_gas_1_bar",
                degeneracy_interpretation="reaction_path_degeneracy",
            ),
            KineticsInterpretationAssignment(
                kinetics_id=kin_v.id,
                subject_key="reactant:1",
                role="reactant",
                statmech_id=vis["sm_v"].id,
                conformer_selection_id=selection.id,
                ensemble_policy="lowest_energy_conformer",
                standard_state_convention="ideal_gas_1_bar",
                degeneracy_interpretation="reaction_path_degeneracy",
            ),
            KineticsTunnelingApplication(
                kinetics_id=kin_v.id,
                model="sct",
                transition_state_entry_id=tse_h.id,
                source_calculation_id=calc_h_tssp.id,
                imaginary_frequency_cm1=-1234.5,
                result_artifact_id=freq_artifact.id,
                sct_path_integral_artifact_id=irc_artifact.id,
            ),
        ]
    )
    session.flush()

    # A second visible record whose determination targets a HIDDEN network's channel.
    kin_vn = make_kinetics(session, reaction_entry=rxe, a=2.0e-12, ea_kj_mol=20.0)
    channel_determination = KineticsDetermination(
        reaction_entry_id=rxe.id,
        direction=KineticsDirection.forward,
        target_kind=KineticsDeterminationTargetKind.resolved_channel,
        target_network_channel_id=nkin_vh.channel_id,
        literature_id=lit.id,
        determination_key="sweep-hidden-network-channel",
        identity_hash=_hash("sweep-hidden-network-channel"),
    )
    session.add(channel_determination)
    session.flush()
    kin_vn.determination_id = channel_determination.id
    kin_vn.representation_role = KineticsRepresentationRole.complete
    vis["kin_vn"] = kin_vn

    # A visible predecessor naming the hidden kin_h through its supersession notice (wired at the end of the world).
    vis["kin_vs"] = make_kinetics(session, reaction_entry=rxe, a=5.0e-12, ea_kj_mol=12.0)

    # -- unreviewed scoping parents -----------------------------------------------------------------------------------
    # rxe_u and every species entry of it are unreviewed; rxe_a is APPROVED but has an unreviewed reactant (ru1).
    ru1, ru2, pu1, pa1 = (
        make_species_entry(session, make_species(session, inchi_key=next_inchi_key(tag)))
        for tag in ("SWU1", "SWU2", "SWU3", "SWU4")
    )
    reaction_u = make_chem_reaction(
        session, reactants=[ru1.species, ru2.species], products=[pu1.species], reversible=False
    )
    rxe_u = make_reaction_entry(session, reaction=reaction_u, reactant_entries=[ru1, ru2], product_entries=[pu1])
    reaction_a = make_chem_reaction(session, reactants=[ru1.species], products=[pa1.species], reversible=False)
    rxe_a = make_reaction_entry(session, reaction=reaction_a, reactant_entries=[ru1], product_entries=[pa1])
    ts_u = make_transition_state(session, reaction_entry=rxe_u, label="unreviewed-entry-ts")
    tse_vu = make_transition_state_entry(session, transition_state=ts_u)
    kin_vu = make_kinetics(session, reaction_entry=rxe_u)
    kin_va = make_kinetics(session, reaction_entry=rxe_a)
    vis["ts_vu"], vis["tse_vu"], vis["kin_vu"], vis["kin_va"] = ts_u, tse_vu, kin_vu, kin_va
    ext["reaction_entry_unreviewed"], ext["reaction_entry_hidden_participant"] = rxe_u, rxe_a
    ext["species_entry_unreviewed"] = ru1
    # pa1 is the only one of these approved (below, with the world's scoping approvals).
    ext["scoping_approved_extra"] = [(R.reaction_entry, rxe_a), (R.species_entry, pa1)]

    # -- counts and flags (D5): records whose every child is hidden -------------------------------------------------------
    # A visible TS (ts_vw) with a visible entry (tse_vw) whose ONLY calculations and evidence are hidden, and a hidden
    # sibling entry (tse_hw2). Under curated nothing about those children may be counted or flagged.
    ts_vw = make_transition_state(session, reaction_entry=rxe, label="visible-ts-hidden-children")
    tse_vw = make_transition_state_entry(session, transition_state=ts_vw)
    tse_hw2 = make_transition_state_entry(session, transition_state=ts_vw, multiplicity=6)
    calc_h_w1 = make_calculation(session, type=CalculationType.irc, transition_state_entry_id=tse_vw.id, lot_id=None)
    calc_h_w2 = make_calculation(session, type=CalculationType.opt, transition_state_entry_id=tse_vw.id, lot_id=None)
    _add_irc(session, calc_h_w1, geom_ts)
    attach_geometry_validation(session, calculation=calc_h_w2)
    attach_scf_stability(session, calculation=calc_h_w2)
    attach_output_geometry(session, calculation=calc_h_w2, geometry=make_geometry(session))
    session.add(
        TransitionStateValidationEvidence(
            transition_state_entry_id=tse_vw.id,
            kind="irc",
            passed=True,
            rationale="IRC connects the declared wells",
            reconstruction_calculation_id=calc_h_w1.id,
        )
    )
    session.flush()
    vis["ts_vw"], vis["tse_vw"] = ts_vw, tse_vw
    hid["tse_hw2"], hid["calc_h_w1"], hid["calc_h_w2"] = tse_hw2, calc_h_w1, calc_h_w2

    # A reaction entry (approved) that has ONLY hidden things: a hidden TS with a hidden path search, a hidden kinetics.
    wr1, wp1 = (
        make_species_entry(session, make_species(session, inchi_key=next_inchi_key(t))) for t in ("SWW1", "SWW2")
    )
    reaction_w = make_chem_reaction(session, reactants=[wr1.species], products=[wp1.species], reversible=False)
    rxe_w = make_reaction_entry(session, reaction=reaction_w, reactant_entries=[wr1], product_entries=[wp1])
    ts_hw = make_transition_state(session, reaction_entry=rxe_w, label="hidden-only-ts")
    tse_hw = make_transition_state_entry(session, transition_state=ts_hw)
    calc_h_psw = make_calculation(
        session, type=CalculationType.path_search, transition_state_entry_id=tse_hw.id, lot_id=None
    )
    _add_path_search(session, calc_h_psw, geom_ts)
    kin_hw = make_kinetics(session, reaction_entry=rxe_w, a=7.0e-12)
    # The only atom map of this reaction entry belongs to the hidden entry: ``has_atom_map`` must follow the floor.
    _atom_map(session, reaction_entry=rxe_w, tse=tse_hw, participant=rxe_w.structure_participants[0])
    hid["ts_hw"], hid["tse_hw"], hid["calc_h_psw"], hid["kin_hw"] = ts_hw, tse_hw, calc_h_psw, kin_hw
    ext["reaction_entry_all_hidden"] = rxe_w
    ext["scoping_approved_extra"].append((R.reaction_entry, rxe_w))

    # -- a reaction with a visible same-role sibling for every single-slot pick of its TS entry -----------------------------
    # Its own reaction, so the D5 tests that rest on ``rxe`` having ONLY hidden freq calculations keep their premise. The
    # visible freq calculation is created first and the hidden one second, so the hidden one is the entry's most recent
    # freq (what the ``/full`` ``ts_freq`` slot picks, and what kinetics provenance's first freq resolves to): a hide that
    # falls through to the sibling names ``calc_s_vf`` where exploratory names ``calc_h_tsfq``.
    sr1, sp1 = (
        make_species_entry(session, make_species(session, inchi_key=next_inchi_key(tag))) for tag in ("SWS1", "SWS2")
    )
    reaction_s = make_chem_reaction(session, reactants=[sr1.species], products=[sp1.species], reversible=False)
    rxe_s = make_reaction_entry(session, reaction=reaction_s, reactant_entries=[sr1], product_entries=[sp1])
    ts_s = make_transition_state(session, reaction_entry=rxe_s, label="visible-sibling-ts")
    tse_s = make_transition_state_entry(session, transition_state=ts_s)
    calc_s_vf = make_calculation(
        session, type=CalculationType.freq, transition_state_entry_id=tse_s.id, lot_id=lot.id
    )
    calc_h_tsfq = make_calculation(
        session, type=CalculationType.freq, transition_state_entry_id=tse_s.id, lot_id=None
    )
    kin_sib = make_kinetics(session, reaction_entry=rxe_s, a=6.0e-12, ea_kj_mol=14.0)
    session.flush()
    source(kin_sib, calc_h_tsfq, KineticsCalculationRole.freq)
    source(kin_sib, calc_s_vf, KineticsCalculationRole.fit_source)
    vis["ts_vsb"], vis["tse_vsb"], vis["calc_s_vf"], vis["kin_vsb"] = ts_s, tse_s, calc_s_vf, kin_sib
    hid["calc_h_tsfq"] = calc_h_tsfq
    ext["reaction_entry_siblings"] = rxe_s
    ext["scoping_approved_extra"].append((R.reaction_entry, rxe_s))

    # -- a hidden PARENT of a visible TS optimisation (``optimized_from``): the only path search of its entry ---------------
    ts_vp = make_transition_state(session, reaction_entry=rxe, label="visible-ts-hidden-parent")
    tse_vp = make_transition_state_entry(session, transition_state=ts_vp)
    calc_vp_opt = make_calculation(
        session, type=CalculationType.opt, transition_state_entry_id=tse_vp.id, lot_id=lot.id
    )
    calc_h_psp = make_calculation(
        session, type=CalculationType.path_search, transition_state_entry_id=tse_vp.id, lot_id=None
    )
    _add_path_search(session, calc_h_psp, geom_ts)
    attach_dependency(session, parent=calc_h_psp, child=calc_vp_opt, role=CalculationDependencyRole.optimized_from)
    vis["ts_vp"], vis["tse_vp"], vis["calc_vp_opt"] = ts_vp, tse_vp, calc_vp_opt
    hid["calc_h_psp"] = calc_h_psp


_LINEAR_XYZ = "3\n\nO 0.0 0.0 -1.16\nC 0.0 0.0 0.0\nO 0.0 0.0 1.16\n"
_BENT_XYZ = "3\n\nO 0.0 0.0 0.0\nH 0.76 0.59 0.0\nH -0.76 0.59 0.0\n"


def _extend_for_export_family(session, w: SweepWorld, *, lot, rxe, r1, r2, tse_v2) -> None:
    """A hidden record in every position the four export streams can name one (PR-4).

    Before this the exports met the hidden records of the older families only, and the one hidden transition state
    they named came from a reaction whose every record was hidden. Now:

    * ``sx``: a hidden calculation that is the only linearity witness of a visible transport (its input geometry is
      collinear), that carries an energy, a frequency block and a Hessian beside a visible calculation of the same
      species, and a hidden single point at the same geometry;
    * ``sy``: a visible transport whose only witnesses are two statmech records that disagree about linearity, one of
      them hidden (a *conflict* that names the hidden one);
    * ``sz``: a visible statmech whose only link to the transport is a hidden calculation;
    * ``sg``: a species whose only thermo record is hidden (an export reports a gap exactly as for a species with none);
    * the reaction entry ``rxe``: a hidden single point on a visible transition-state entry that is the lowest TS
      energy (it would set the barrier), with reactant energies at the same level of theory; and a reaction whose
      selected transition-state entry is visible but whose transition state is hidden.
    """
    from tests.services.scientific_read._factories import (
        attach_hessian,
        attach_input_geometry,
        attach_sp_result,
    )

    hid, vis, ext = w.hidden, w.visible, w.extra

    def entry(tag):
        return make_species_entry(session, make_species(session, inchi_key=next_inchi_key(tag)))

    def geometry(xyz, symbols, coords):
        g = make_geometry(session, xyz_text=xyz)
        attach_geometry_atoms(session, geometry=g, symbols=symbols, coords=coords)
        return g

    # -- sx --------------------------------------------------------------------------------------------------------------
    sx = entry("SWX")
    g_lin = geometry(_LINEAR_XYZ, ["O", "C", "O"], [[0, 0, -1.16], [0, 0, 0], [0, 0, 1.16]])
    g_bent = geometry(_BENT_XYZ, ["O", "H", "H"], [[0, 0, 0], [0.76, 0.59, 0], [-0.76, 0.59, 0]])
    calc_h_xlin = make_calculation(session, type=CalculationType.freq, species_entry_id=sx.id, lot_id=lot.id)
    attach_input_geometry(session, calculation=calc_h_xlin, geometry=g_lin)
    attach_freq_result(session, calculation=calc_h_xlin, frequencies_cm1=[600.0, 600.0, 1300.0, 2400.0], zpe_hartree=0.01)
    attach_hessian(session, calculation=calc_h_xlin, geometry=g_lin, natoms=3)
    calc_h_xsp = make_calculation(session, type=CalculationType.sp, species_entry_id=sx.id, lot_id=lot.id)
    attach_input_geometry(session, calculation=calc_h_xsp, geometry=g_lin)
    attach_sp_result(session, calculation=calc_h_xsp, electronic_energy_hartree=-188.5)
    calc_xv = make_calculation(session, type=CalculationType.sp, species_entry_id=sx.id, lot_id=lot.id)
    attach_input_geometry(session, calculation=calc_xv, geometry=g_bent)
    attach_sp_result(session, calculation=calc_xv, electronic_energy_hartree=-76.4)
    trx_v = make_transport(session, species_entry=sx)
    attach_transport_source_calculation(session, transport=trx_v, calculation=calc_h_xlin)
    hid["calc_h_xlin"], hid["calc_h_xsp"] = calc_h_xlin, calc_h_xsp
    vis["calc_xv"], vis["trn_xv"] = calc_xv, trx_v

    # -- sa: a visible calculation (calc_vsp) on a HIDDEN observation (co_h) in a VISIBLE group (cg_v). Until now it had no
    # geometry, so the ML species export never made a record of it and never met the shape: a group named through a
    # hidden observation (the group ref beside an empty observation list).
    g_vsp = geometry(_BENT_XYZ, ["O", "H", "H"], [[0, 0, 0], [0.8, 0.6, 0], [-0.8, 0.6, 0]])
    attach_input_geometry(session, calculation=vis["calc_vsp"], geometry=g_vsp)
    ext["geometry_vsp"] = g_vsp

    # -- sy: two statmech records that disagree, one hidden ----------------------------------------------------------------
    sy = entry("SWY")
    calc_yv = make_calculation(session, type=CalculationType.freq, species_entry_id=sy.id, lot_id=lot.id)
    try_v = make_transport(session, species_entry=sy)
    attach_transport_source_calculation(session, transport=try_v, calculation=calc_yv)
    sm_h_y = make_statmech(session, species_entry=sy, is_linear=True)
    sm_v_y = make_statmech(session, species_entry=sy, is_linear=False)
    for sm in (sm_h_y, sm_v_y):
        attach_statmech_source_calculation(session, statmech=sm, calculation=calc_yv, role=StatmechCalculationRole.freq)
    vis["calc_yv"], vis["trn_yv"], vis["sm_v_y"] = calc_yv, try_v, sm_v_y
    hid["sm_h_y"] = sm_h_y

    # -- sz: a visible statmech reachable only through a hidden calculation ---------------------------------------------------
    sz = entry("SWZ")
    calc_h_zh = make_calculation(session, type=CalculationType.freq, species_entry_id=sz.id, lot_id=lot.id)
    trz_v = make_transport(session, species_entry=sz)
    attach_transport_source_calculation(session, transport=trz_v, calculation=calc_h_zh)
    sm_v_z = make_statmech(session, species_entry=sz, is_linear=True)
    attach_statmech_source_calculation(session, statmech=sm_v_z, calculation=calc_h_zh, role=StatmechCalculationRole.freq)
    vis["trn_zv"], vis["sm_v_z"] = trz_v, sm_v_z
    hid["calc_h_zh"] = calc_h_zh

    # -- sg: a species whose only thermo is hidden (the export gap count must follow the floor) ---------------------------
    sg = entry("SWG")
    thm_h_g = make_thermo_scalar(session, species_entry=sg, h298_kj_mol=-11.0)
    hid["thm_h_g"] = thm_h_g
    ext["species_entry_hidden_thermo_only"] = sg
    ext["scoping_approved_extra"].append((R.species_entry, sg))

    # -- ML reactions: TS energy and barrier ----------------------------------------------------------------------------------
    for reactant, energy in ((r1, -50.0), (r2, -40.0)):
        calc_r = make_calculation(session, type=CalculationType.sp, species_entry_id=reactant.id, lot_id=lot.id)
        attach_sp_result(session, calculation=calc_r, electronic_energy_hartree=energy)
        vis[f"calc_r{reactant.id}"] = calc_r
    # A hidden reactant single point at the same level of theory, lower than the shown one: unfloored, the reactant
    # energy (and so the barrier) follows it. Hidden by having no review row; it names nowhere, only its value could leak.
    calc_h_r1 = make_calculation(session, type=CalculationType.sp, species_entry_id=r1.id, lot_id=lot.id)
    attach_sp_result(session, calculation=calc_h_r1, electronic_energy_hartree=-60.0)
    ext["calc_hidden_reactant_sp"] = calc_h_r1
    calc_ts_vsp = make_calculation(session, type=CalculationType.sp, transition_state_entry_id=tse_v2.id, lot_id=lot.id)
    attach_sp_result(session, calculation=calc_ts_vsp, electronic_energy_hartree=-89.0)
    calc_h_xts = make_calculation(session, type=CalculationType.sp, transition_state_entry_id=tse_v2.id, lot_id=lot.id)
    attach_sp_result(session, calculation=calc_h_xts, electronic_energy_hartree=-91.0)
    vis["calc_ts_vsp"] = calc_ts_vsp
    hid["calc_h_xts"] = calc_h_xts

    # -- ML reactions: a visible transition-state entry whose transition state is hidden -------------------------------------
    dr, dp = entry("SWD1"), entry("SWD2")
    reaction_d = make_chem_reaction(session, reactants=[dr.species], products=[dp.species], reversible=False)
    rxe_d = make_reaction_entry(session, reaction=reaction_d, reactant_entries=[dr], product_entries=[dp])
    ts_h_x = make_transition_state(session, reaction_entry=rxe_d, label="hidden-owner-of-visible-entry")
    tse_xd = make_transition_state_entry(session, transition_state=ts_h_x)
    hid["ts_h_x"] = ts_h_x
    vis["tse_xd"] = tse_xd
    ext["reaction_entry_hidden_ts_owner"] = rxe_d
    ext["species_entry_export"] = sx
    ext["species_entry_conflict"], ext["species_entry_hidden_link"] = sy, sz
    ext["scoping_approved_extra"].extend(
        [(R.species_entry, sx), (R.species_entry, sy), (R.species_entry, sz), (R.reaction_entry, rxe_d)]
    )
    session.flush()


def _extend_for_network_and_provenance_family(session, w: SweepWorld, *, lot, lit, sa, rxe) -> None:
    """Every response shape of the network, literature, level-of-theory, correction and analytics routes (PR-2c).

    Before this the world held one hidden solve under a visible network, one visible solve under a hidden network and
    one fit on each, and every one of the family's routes "answered 200" without ever meeting the shapes the guard has
    to place. So the world now holds, for this family:

    * a hidden solve on a visible network, a visible solve on a hidden network, and a hidden solve whose fits are the
      only ones of their kind (a PLOG fit) and whose temperature/pressure envelope reaches past every visible solve;
      a fit is reviewed through its solve, so a hidden fit on a VISIBLE solve cannot exist (the brief's shape list
      has one, the data model does not);
    * channels whose microreactions and solve barriers cite a hidden transition-state entry beside a visible one and a
      barrierless path, and a state energy whose sources are hidden calculations (the per-participant rows, the
      zero-point calculation and the older single slot) beside visible ones;
    * a solve's validation entry that pins a hidden solve as its reference, and source calculations that are hidden;
    * literature cited only by hidden records, a level of theory used only by hidden calculations, an
      energy-correction scheme and a frequency scale factor applied only to hidden records, a composite scheme bound
      to a level of theory used only by a hidden calculation, and a visible application whose source calculation is
      hidden;
    * analytics rows whose provenance flags rest on hidden statmech and hidden entries.

    A hidden-only level of theory is a vocabulary row (no review gate, no 404); what the guard owes it is that nothing
    about the hidden calculations is counted or listed under it.
    """
    from app.db.models.common import (
        CompositeBindingSource,
        CompositeExtrapolationFormula,
        CompositeSchemeKind,
        CompositeTermOperation,
        EnergyComponentKind,
        EnergyCorrectionApplicationRole,
        EnergyCorrectionConvention,
        EnergyZeroConvention,
    )
    from app.db.models.composite_scheme import (
        CompositeScheme,
        CompositeSchemeTerm,
        CompositeSchemeTermInput,
        LevelOfTheoryComposite,
    )
    from app.db.models.kinetics import KineticsInterpretationAssignment
    from app.db.models.network_pdep import (
        NetworkChannel,
        NetworkChannelMicroReaction,
        NetworkSolveChannelBarrier,
        NetworkSolveStateEnergy,
        NetworkSolveStateEnergySource,
        NetworkState,
    )

    vis, hid, ext = w.visible, w.hidden, w.extra
    tse_h, tse_v2 = hid["tse_h"], vis["tse_v2"]
    calc_h, calc_vopt, calc_vf = hid["calc_h"], vis["calc_vopt"], vis["calc_vf"]
    net_v = vis["net_v"]
    nsolve_v, nsolve_h = vis["nsolve_v"], hid["nsolve_h"]

    # -- networks --------------------------------------------------------------------------------------------------
    channel_v = session.get(NetworkChannel, vis["nkin_v"].channel_id)
    channel_h = session.get(NetworkChannel, hid["nkin_h"].channel_id)
    channel_vh = session.get(NetworkChannel, vis["nkin_v_under_hidden_net"].channel_id)
    for channel in (channel_v, channel_h, channel_vh):
        session.add_all(
            [
                NetworkChannelMicroReaction(
                    channel_id=channel.id, reaction_entry_id=rxe.id, transition_state_entry_id=tse_v2.id
                ),
                NetworkChannelMicroReaction(
                    channel_id=channel.id, reaction_entry_id=rxe.id, transition_state_entry_id=tse_h.id
                ),
                NetworkChannelMicroReaction(
                    channel_id=channel.id, reaction_entry_id=rxe.id, transition_state_entry_id=None
                ),
            ]
        )
    session.flush()

    # Barriers of the visible solve: one on a visible entry whose energy rests on a hidden calculation, one on the
    # hidden entry.
    for entry, source in ((tse_v2, calc_h), (tse_h, calc_vopt)):
        session.add(
            NetworkSolveChannelBarrier(
                solve_id=nsolve_v.id,
                channel_id=channel_v.id,
                reaction_entry_id=rxe.id,
                transition_state_entry_id=entry.id,
                forward_barrier_kj_mol=100.0,
                reverse_barrier_kj_mol=80.0,
                energy_zero_convention=EnergyZeroConvention.lowest_state,
                correction_convention=EnergyCorrectionConvention.electronic_only,
                source_calculation_id=source.id,
            )
        )
    session.flush()

    # State energies of the visible solve. A: the participant's source is hidden and its zero-point calculation
    # visible; B: the source is visible and the zero-point calculation hidden; C: only the older single slot, hidden;
    # D: all visible.
    state_a = session.get(NetworkState, channel_v.source_state_id)
    state_b = session.get(NetworkState, channel_v.sink_state_id)
    state_c = make_network_state(session, network=net_v, kind=NetworkStateKind.well, composition_hash=_hash("c-state"))
    state_d = make_network_state(session, network=net_v, kind=NetworkStateKind.well, composition_hash=_hash("d-state"))
    attach_network_state_participant(session, state=state_c, species_entry=sa)
    attach_network_state_participant(session, state=state_d, species_entry=sa)
    for state, single in ((state_a, None), (state_b, None), (state_c, calc_h), (state_d, None)):
        session.add(
            NetworkSolveStateEnergy(
                solve_id=nsolve_v.id,
                state_id=state.id,
                energy_kj_mol=-10.0,
                energy_zero_convention=EnergyZeroConvention.lowest_state,
                correction_convention=EnergyCorrectionConvention.electronic_plus_zpe,
                source_calculation_id=None if single is None else single.id,
            )
        )
    session.flush()
    for state, calc, zpe in ((state_a, calc_h, calc_vf), (state_b, calc_vopt, calc_h), (state_d, calc_vopt, calc_vf)):
        session.add(
            NetworkSolveStateEnergySource(
                solve_id=nsolve_v.id,
                state_id=state.id,
                species_entry_id=sa.id,
                calculation_id=calc.id,
                zpe_calculation_id=zpe.id,
            )
        )
    session.flush()

    # Two states with two participants each, so ``partial_sources`` has something to be true about. E: the second
    # participant's zero-point calculation is hidden; F: its source calculation is hidden. Under curated the second
    # source is withheld and the state says its sources are incomplete.
    p2 = make_species_entry(session, make_species(session, inchi_key=next_inchi_key("SWP2")))
    state_e = make_network_state(session, network=net_v, kind=NetworkStateKind.well, composition_hash=_hash("e-state"))
    state_f = make_network_state(session, network=net_v, kind=NetworkStateKind.well, composition_hash=_hash("f-state"))
    for state in (state_e, state_f):
        attach_network_state_participant(session, state=state, species_entry=sa)
        attach_network_state_participant(session, state=state, species_entry=p2)
        session.add(
            NetworkSolveStateEnergy(
                solve_id=nsolve_v.id,
                state_id=state.id,
                energy_kj_mol=-5.0,
                energy_zero_convention=EnergyZeroConvention.lowest_state,
                correction_convention=EnergyCorrectionConvention.electronic_plus_zpe,
            )
        )
    session.flush()
    for state, entry, calc, zpe in (
        (state_e, sa, calc_vopt, None),
        (state_e, p2, calc_vf, calc_h),
        (state_f, sa, calc_vopt, None),
        (state_f, p2, calc_h, None),
    ):
        session.add(
            NetworkSolveStateEnergySource(
                solve_id=nsolve_v.id,
                state_id=state.id,
                species_entry_id=entry.id,
                calculation_id=calc.id,
                zpe_calculation_id=None if zpe is None else zpe.id,
            )
        )
    session.flush()

    # The older single slot, hidden, in the two shapes where it feeds ``partial_sources``: G, a two-participant state
    # whose only source it is; H, a one-participant state whose single slot is owned by no participant of the state.
    calc_h_p2 = make_calculation(session, type=CalculationType.sp, species_entry_id=p2.id, lot_id=None)
    hid["calc_h_p2"] = calc_h_p2
    state_g = make_network_state(session, network=net_v, kind=NetworkStateKind.well, composition_hash=_hash("g-state"))
    state_h = make_network_state(session, network=net_v, kind=NetworkStateKind.well, composition_hash=_hash("h-state"))
    attach_network_state_participant(session, state=state_g, species_entry=sa)
    attach_network_state_participant(session, state=state_g, species_entry=p2)
    attach_network_state_participant(session, state=state_h, species_entry=sa)
    for state, single in ((state_g, calc_h), (state_h, calc_h_p2)):
        session.add(
            NetworkSolveStateEnergy(
                solve_id=nsolve_v.id,
                state_id=state.id,
                energy_kj_mol=-4.0,
                energy_zero_convention=EnergyZeroConvention.lowest_state,
                correction_convention=EnergyCorrectionConvention.electronic_plus_zpe,
                source_calculation_id=single.id,
            )
        )
    session.flush()

    # An elementary channel whose ONLY microreaction cites the hidden entry: upload cannot produce a channel with a
    # microreaction-less elementary mechanism, curated serves one (owner decision D1: the record stays, its link goes).
    session.add(
        NetworkChannelMicroReaction(
            channel_id=make_network_channel(
                session,
                network=net_v,
                source_state=state_c,
                sink_state=state_d,
                kind=NetworkChannelKind.isomerization,
                channel_key="ch-only-hidden-entry",
            ).id,
            reaction_entry_id=rxe.id,
            transition_state_entry_id=tse_h.id,
        )
    )
    session.flush()

    # A validation entry pinning the hidden solve; hidden and visible source calculations of the visible solve.
    nsolve_v.validation_declaration = {
        "version": 1,
        "entries": [
            {
                "kind": "model_fidelity",
                "metric": "max_relative_error",
                "value": 0.1,
                "domain": {
                    "temperature_min_k": 300.0,
                    "temperature_max_k": 2000.0,
                    "pressure_min_bar": 0.01,
                    "pressure_max_bar": 100.0,
                },
                "reference_solve_ref": nsolve_h.public_ref,
            }
        ],
    }
    attach_network_solve_source_calculation(
        session, solve=nsolve_v, calculation=calc_h, role=NetworkSolveCalculationRole.fit_source
    )
    attach_network_solve_source_calculation(
        session, solve=nsolve_v, calculation=calc_vf, role=NetworkSolveCalculationRole.fit_source
    )
    attach_network_solve_source_calculation(
        session, solve=nsolve_h, calculation=calc_vf, role=NetworkSolveCalculationRole.fit_source
    )

    # The hidden solve alone carries a PLOG fit and the widest envelope: nothing about either may reach the network.
    nsolve_h.tmax_k, nsolve_h.pmax_bar = 5000.0, 1000.0
    nkin_h_plog = make_network_kinetics(
        session,
        channel=channel_h,
        solve=nsolve_h,
        model_kind=NetworkKineticsModelKind.plog,
        rate_units=ArrheniusAUnits.cm3_mol_s,
    )
    attach_network_kinetics_plog(session, kinetics=nkin_h_plog)
    hid["nkin_h_plog"] = nkin_h_plog
    session.flush()

    # A second visible solve of the visible network, with a fit, whose ONLY source calculation is hidden; and free text
    # on the visible solve and network that names hidden records.
    nsolve_v2 = make_network_solve(session, network=net_v, literature_id=lit.id)
    nkin_v2 = make_network_kinetics(
        session,
        channel=channel_v,
        solve=nsolve_v2,
        model_kind=NetworkKineticsModelKind.chebyshev,
        rate_units=ArrheniusAUnits.cm3_mol_s,
    )
    attach_network_kinetics_chebyshev(session, kinetics=nkin_v2)
    vis["nsolve_v_hidden_sources"], vis["nkin_v_hidden_sources"] = nsolve_v2, nkin_v2
    # That solve's only barrier is on the hidden entry: it has barriers, none of them shown.
    session.add(
        NetworkSolveChannelBarrier(
            solve_id=nsolve_v2.id,
            channel_id=channel_v.id,
            reaction_entry_id=rxe.id,
            transition_state_entry_id=tse_h.id,
            forward_barrier_kj_mol=90.0,
            reverse_barrier_kj_mol=70.0,
            energy_zero_convention=EnergyZeroConvention.lowest_state,
            correction_convention=EnergyCorrectionConvention.electronic_only,
        )
    )
    # A VISIBLE network whose only solve (and its only fit) is hidden: nothing under it may be counted or found.
    net_v3 = make_network(session, name="visible-net-hidden-solves-only", literature_id=lit.id)
    state_a3 = make_network_state(
        session, network=net_v3, kind=NetworkStateKind.well, composition_hash=_hash("a-v3"), label="A"
    )
    state_b3 = make_network_state(
        session, network=net_v3, kind=NetworkStateKind.well, composition_hash=_hash("b-v3"), label="B"
    )
    attach_network_state_participant(session, state=state_a3, species_entry=sa)
    attach_network_state_participant(session, state=state_b3, species_entry=sa)
    channel_v3 = make_network_channel(
        session,
        network=net_v3,
        source_state=state_a3,
        sink_state=state_b3,
        kind=NetworkChannelKind.isomerization,
        channel_key="ch-hidden-solves-only",
    )
    nsolve_h3 = make_network_solve(session, network=net_v3, literature_id=lit.id)
    nkin_h3 = make_network_kinetics(
        session,
        channel=channel_v3,
        solve=nsolve_h3,
        model_kind=NetworkKineticsModelKind.chebyshev,
        rate_units=ArrheniusAUnits.cm3_mol_s,
    )
    attach_network_kinetics_chebyshev(session, kinetics=nkin_h3)
    vis["net_v_hidden_solves_only"] = net_v3
    hid["nsolve_h3"], hid["nkin_h3"] = nsolve_h3, nkin_h3
    # A VISIBLE calculation with a level of its own, cited only by the hidden solve: a search by that level must not find
    # the visible network through it.
    lot_so = make_lot(session, method="solveonly", basis="def2svp")
    calc_v_so = make_calculation(session, type=CalculationType.sp, species_entry_id=sa.id, lot_id=lot_so.id)
    vis["calc_v_solveonly"] = calc_v_so
    attach_network_solve_source_calculation(
        session, solve=nsolve_h, calculation=calc_v_so, role=NetworkSolveCalculationRole.well_energy
    )
    nsolve_v.note = f"fit compared with {calc_h.public_ref}"
    net_v.description = f"see also {nsolve_h.public_ref}"
    session.flush()

    # -- levels of theory, literature, corrections, composite scheme ---------------------------------------------------
    lot_ho = make_lot(session, method="hiddenonly", basis="def2svp")
    lit_ho = make_literature(session, title="Cited only by hidden records")
    ext["lot_hidden_only"], ext["literature_hidden_only"] = lot_ho, lit_ho
    calc_h_lot = make_calculation(session, type=CalculationType.sp, species_entry_id=sa.id, lot_id=lot_ho.id)
    calc_h_lot.literature_id = lit_ho.id
    calc_h_lot_ts = make_calculation(
        session, type=CalculationType.sp, transition_state_entry_id=tse_v2.id, lot_id=lot_ho.id
    )
    hid["calc_h_lot"], hid["calc_h_lot_ts"] = calc_h_lot, calc_h_lot_ts
    # The software and workflow tool that ran them: the level's software breakdown rests on hidden calculations only.
    software = make_software_release(session, name="hiddensoft", version="1")
    workflow = make_workflow_tool_release(session, name="hiddenflow", version="1")
    for calc in (calc_h_lot, calc_h_lot_ts):
        calc.software_release_id = software.id
        calc.workflow_tool_release_id = workflow.id
    for solve in (nsolve_v, nsolve_v2):
        attach_network_solve_source_calculation(
            session, solve=solve, calculation=calc_h_lot, role=NetworkSolveCalculationRole.well_energy
        )
    hid["trn_h"].literature_id = lit_ho.id
    hid["sm_h"].literature_id = lit_ho.id

    ecs_ho = make_energy_correction_scheme(session, name="hidden_only_scheme", lot=lot_ho, source_literature=lit_ho)
    ext["ecs_hidden_only"] = ecs_ho
    # Free text on the vocabulary rows that names hidden records.
    ext["ecs"].note = f"parameters fitted on {hid['sm_h'].public_ref}"
    ext["fsf"].note = f"derived at {tse_h.public_ref}"
    make_applied_energy_correction(
        session, target_transition_state_entry=tse_h, scheme=ecs_ho, source_calculation=calc_h_lot
    )
    # A VISIBLE application of the shared scheme whose source calculation is hidden: the application stays, the source
    # goes.
    make_applied_energy_correction(
        session, target_species_entry=sa, scheme=ext["ecs"], source_calculation=calc_h, value=0.002
    )

    fsf_ho = make_frequency_scale_factor(session, lot=lot_ho, source_literature=lit_ho)
    ext["fsf_hidden_only"] = fsf_ho
    hid["sm_h"].frequency_scale_factor_id = ext["fsf"].id
    vis["sm_v2"].frequency_scale_factor_id = ext["fsf"].id
    hid["sm_ts_h"].frequency_scale_factor_id = fsf_ho.id
    make_applied_energy_correction(
        session,
        target_transition_state_entry=tse_h,
        frequency_scale_factor=fsf_ho,
        application_role=EnergyCorrectionApplicationRole.zpe,
    )

    # A scale factor whose users start with a hidden statmech and a hidden application, followed by shown ones: with a
    # short ``used_by`` limit a hidden row must not displace a shown one.
    fsf_lim = make_frequency_scale_factor(session, lot=lot)
    ext["fsf_limit"] = fsf_lim
    sm_h2 = make_statmech(session, species_entry=sa)
    sm_v3 = make_statmech(session, species_entry=sa)
    sm_h2.frequency_scale_factor_id = fsf_lim.id
    sm_v3.frequency_scale_factor_id = fsf_lim.id
    hid["sm_h2"], vis["sm_v3"] = sm_h2, sm_v3
    for target in ("hidden", "visible"):
        make_applied_energy_correction(
            session,
            target_transition_state_entry=tse_h if target == "hidden" else None,
            target_species_entry=sa if target == "visible" else None,
            frequency_scale_factor=fsf_lim,
            application_role=EnergyCorrectionApplicationRole.zpe,
        )
    session.flush()

    lot_comp = make_lot(session, method="sweepcomposite", basis=None)
    calc_h_comp = make_calculation(session, type=CalculationType.sp, species_entry_id=sa.id, lot_id=lot_comp.id)
    hid["calc_h_comp"] = calc_h_comp
    scheme = CompositeScheme(
        kind=CompositeSchemeKind.extrapolation,
        name="sweep-scheme",
        definition_hash=_hash("sweep-scheme"),
        source_literature_id=lit_ho.id,
    )
    session.add(scheme)
    session.flush()
    base_term = CompositeSchemeTerm(
        scheme_id=scheme.id,
        position=0,
        operation=CompositeTermOperation.base,
        energy_component=EnergyComponentKind.reference,
    )
    extrapolated = CompositeSchemeTerm(
        scheme_id=scheme.id,
        position=1,
        operation=CompositeTermOperation.extrapolation,
        energy_component=EnergyComponentKind.correlation,
        formula=CompositeExtrapolationFormula.inverse_power,
        exponent=3.0,
    )
    session.add_all([base_term, extrapolated])
    session.flush()
    session.add_all(
        [
            CompositeSchemeTermInput(term_id=base_term.id, slot=CompositeInputSlot.value, level_of_theory_id=lot.id),
            CompositeSchemeTermInput(
                term_id=extrapolated.id, slot=CompositeInputSlot.cardinal, level_of_theory_id=lot.id, cardinal_number=3
            ),
            CompositeSchemeTermInput(
                term_id=extrapolated.id,
                slot=CompositeInputSlot.cardinal,
                level_of_theory_id=lot_ho.id,
                cardinal_number=4,
            ),
        ]
    )
    session.add(
        LevelOfTheoryComposite(
            level_of_theory_id=lot_comp.id, scheme_id=scheme.id, binding_source=CompositeBindingSource.declared
        )
    )
    ext["composite_scheme"] = scheme
    session.flush()

    # -- analytics: a visible kinetics whose only interpretation assignment names a hidden statmech and a hidden entry --
    kin_vh = make_kinetics(session, reaction_entry=rxe, a=4.0e-12, ea_kj_mol=14.0)
    session.add(
        KineticsInterpretationAssignment(
            kinetics_id=kin_vh.id,
            subject_key="transition_state",
            role="transition_state",
            statmech_id=hid["sm_h"].id,
            transition_state_entry_id=tse_h.id,
            ensemble_policy="lowest_energy_conformer",
            standard_state_convention="ideal_gas_1_bar",
            degeneracy_interpretation="reaction_path_degeneracy",
        )
    )
    vis["kin_vh"] = kin_vh
    # ... and one whose assignment names a VISIBLE statmech and a VISIBLE entry (the flags stay true).
    kin_vt = make_kinetics(session, reaction_entry=rxe, a=6.0e-12, ea_kj_mol=16.0)
    session.add(
        KineticsInterpretationAssignment(
            kinetics_id=kin_vt.id,
            subject_key="transition_state",
            role="transition_state",
            statmech_id=vis["sm_ts_v"].id,
            transition_state_entry_id=tse_v2.id,
            ensemble_policy="lowest_energy_conformer",
            standard_state_convention="ideal_gas_1_bar",
            degeneracy_interpretation="reaction_path_degeneracy",
        )
    )
    vis["kin_vt"] = kin_vt
    # The newest kinetics record of the world is hidden: the analytics watermark must not reveal its id.
    hid["kin_h_new"] = make_kinetics(session, reaction_entry=rxe, a=8.0e-12, ea_kj_mol=18.0)
    session.flush()
