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
from tests.services.scientific_read._factories import (
    attach_artifact,
    attach_conformer_selection,
    attach_dependency,
    attach_freq_result,
    attach_geometry_atoms,
    attach_geometry_validation,
    attach_network_kinetics_chebyshev,
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
    make_species,
    make_species_entry,
    make_statmech,
    make_thermo_scalar,
    make_transition_state,
    make_transition_state_entry,
    make_transport,
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
    calc_vf = make_calculation(session, type=CalculationType.freq, species_entry_id=sa.id, lot_id=lot.id)
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

    # ---- review states: hidden ones first (under_review for some), approvals last ---------------------------------
    under_review_keys = {"cg_h", "ts_h", "sm_h", "net_h", "kin_h", "tse_h"}
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
        if kind == "nkin" or key in ("thm_h", "kin_h"):
            continue  # nkin is reviewed through its solve; thm_h and kin_h are moved by the supersession blocks below
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
        if kind == "nkin" or key in ("thm_v", "kin_vs"):
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

    calc_h_tssp, calc_ts_sp_v = calc_on_tse_v2(CalculationType.sp, hidden=True), calc_on_tse_v2(CalculationType.sp)
    calc_h_irc, calc_irc_v = calc_on_tse_v2(CalculationType.irc, hidden=True), calc_on_tse_v2(CalculationType.irc)
    calc_h_ps, calc_ps_v = (
        calc_on_tse_v2(CalculationType.path_search, hidden=True),
        calc_on_tse_v2(CalculationType.path_search),
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
