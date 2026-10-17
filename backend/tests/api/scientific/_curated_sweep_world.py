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
    CalculationDependencyRole,
    CalculationType,
    CompositeAssembly,
    CompositeInputSlot,
    IRCDirection,
    NetworkChannelKind,
    NetworkKineticsModelKind,
    NetworkSolveCalculationRole,
    NetworkSpeciesRole,
    NetworkStateKind,
    PathSearchMethod,
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
    attach_dependency,
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

    # ---- review states: hidden ones first (under_review for some), approvals last ---------------------------------
    under_review_keys = {"cg_h", "ts_h", "sm_h", "net_h", "kin_h"}
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
        if kind == "nkin" or key == "thm_h":
            continue  # nkin is reviewed through its solve; thm_h is moved by the supersession block below
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

    # Visible: order matters only in that links already exist (they do). ``thm_v`` is approved below, through the
    # service, because it is also the legacy superseded record.
    for key, row in vis.items():
        kind = key.split("_", 1)[0]
        if kind == "nkin" or key == "thm_v":
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
