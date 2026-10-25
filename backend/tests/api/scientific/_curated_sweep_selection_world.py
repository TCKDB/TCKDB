"""Guard PR-3: the part of the sweep world that the selection documents are built from.

A selection engine is pure over the facts its loader captured, so the refs that matter are the ones nested in those
facts. The world holds one of each on a visible parent, so no curated crawl is green by looking at a world without the
shape (the lesson of #774):

* thermo: a visible record that targets a HIDDEN conformer group (``target_group_ref``), beside the one that cites a
  hidden calculation in its protocol (already in the world);
* kinetics: a visible record fitted from a HIDDEN network's channel (the composite ``network_channel_ref``, the
  determination's network and the solve), beside the ones that cite a hidden transition-state entry, solve and
  calculations (already in the world);
* structure and relative energies: a basin determination of a VISIBLE observation whose conformer group is hidden
  (``conformer_group_ref``), and one of the visible group, whose calculations each hang off a hidden parent;
* statmech: declared models whose basin and ensemble members sit in the hidden group, a legacy record anchored in it,
  and a visible record whose supersession notice names a hidden successor;
* network: a dedicated network whose visible solve is eligible and cites a HIDDEN solve of the same network as its
  model-fidelity reference, and that hidden solve (the export route needs an eligible node to serialise).
"""

from __future__ import annotations

import hashlib
from uuid import uuid4

from sqlalchemy import text

from app.db.models.common import (
    CalculationDependencyRole,
    CalculationType,
    StatmechCalculationRole,
    StatmechModelScopeKind,
    StatmechModelSourceRole,
    StructureDeterminationQuantity,
    StructureDeterminationTargetKind,
    StructureSourceRole,
    ThermoTargetKind,
)
from app.db.models.statmech_declaration import StatmechModelDeclaration, StatmechModelMember, StatmechModelSource
from app.db.models.structure_determination import StructureDetermination, StructureDeterminationSource
from tests.services.network_selection._world import add_solve, build_world, fit_spec, validity
from tests.services.scientific_read._factories import (
    attach_dependency,
    attach_freq_result,
    attach_input_geometry,
    attach_opt_result,
    attach_output_geometry,
    attach_sp_result,
    attach_statmech_source_calculation,
    make_calculation,
    make_frequency_scale_factor,
    make_geometry,
    make_statmech,
    make_thermo_scalar,
    make_workflow_tool_release,
)
from tests.services.statmech_selection._support import BASE_RECIPE


def _hash(text_: str) -> str:
    return hashlib.sha256(text_.encode()).hexdigest()


def basin_determination(session, *, entry, observation, sources, quantity, geometry):
    """A conformer-basin structure determination on ``observation`` (the owner of the claim) pinning ``sources``."""
    row = StructureDetermination(
        species_entry_id=entry.id,
        conformer_observation_id=observation.id,
        target_kind=StructureDeterminationTargetKind.conformer_basin,
        quantity=StructureDeterminationQuantity(quantity),
        evaluated_geometry_id=geometry.id,
        workflow_tool_release_id=make_workflow_tool_release(session).id,
        determination_key=uuid4().hex[:12],
        identity_hash=hashlib.sha256(uuid4().bytes).hexdigest(),
        content_hash=hashlib.sha256(uuid4().bytes).hexdigest(),
    )
    session.add(row)
    session.flush()
    session.execute(text("SELECT set_config('tckdb.structure_determination_writing', :id, true)"), {"id": str(row.id)})
    for role, calc in sources:
        session.add(
            StructureDeterminationSource(
                determination_id=row.id,
                role=StructureSourceRole(role),
                calculation_id=calc.id,
                conformer_observation_id=observation.id,
                species_entry_id=entry.id,
            )
        )
    session.flush()
    session.execute(text("SELECT set_config('tckdb.structure_determination_writing', '', true)"))
    session.refresh(row)
    return row


def extend_for_selection_documents(session, w, *, lot, lit, sa) -> None:
    vis, hid, ext = w.visible, w.hidden, w.extra
    cg_h, cg_v, co_v, co_v2 = hid["cg_h"], vis["cg_v"], vis["co_v"], vis["co_v2"]
    calc_h = hid["calc_h"]

    # ---- thermo ---------------------------------------------------------------------------------------------------
    thm_vg = make_thermo_scalar(session, species_entry=sa, h298_kj_mol=-17.5)
    thm_vg.thermodynamic_target_kind = ThermoTargetKind.single_conformer
    thm_vg.target_conformer_group_id = cg_h.id
    session.flush()
    vis["thm_vg"] = thm_vg

    # ---- kinetics: a fit of the HIDDEN network's channel, on a visible record -----------------------------------------
    vis["kin_vn"].network_kinetics_id = vis["nkin_v_under_hidden_net"].id
    session.flush()

    # ---- structure determinations and relative energies ----------------------------------------------------------------
    geometry = make_geometry(session)

    def basin(tag, observation):
        opt = make_calculation(
            session, type=CalculationType.opt, species_entry_id=sa.id, lot_id=lot.id, conformer_observation_id=observation.id
        )
        attach_opt_result(session, calculation=opt, final_energy_hartree=-76.39, converged=True)
        attach_output_geometry(session, calculation=opt, geometry=geometry)
        sp = make_calculation(
            session, type=CalculationType.sp, species_entry_id=sa.id, lot_id=lot.id, conformer_observation_id=observation.id
        )
        attach_sp_result(session, calculation=sp, electronic_energy_hartree=-76.4)
        attach_input_geometry(session, calculation=sp, geometry=geometry)
        freq = make_calculation(
            session, type=CalculationType.freq, species_entry_id=sa.id, lot_id=lot.id, conformer_observation_id=observation.id
        )
        attach_freq_result(session, calculation=freq, frequencies_cm1=[100.0, 200.0])
        attach_input_geometry(session, calculation=freq, geometry=geometry)
        # Each hangs off a hidden parent: the edge is dropped by the loader, and nothing names the parent.
        attach_dependency(session, parent=calc_h, child=freq, role=CalculationDependencyRole.freq_on)
        vis[f"calc_{tag}_opt"], vis[f"calc_{tag}_sp"], vis[f"calc_{tag}_freq"] = opt, sp, freq
        return basin_determination(
            session,
            entry=sa,
            observation=observation,
            sources=[("geometry_optimization", opt), ("energy", sp), ("curvature", freq)],
            quantity="electronic_energy",
            geometry=geometry,
        )

    ext["sdet_hidden_group"] = basin("selhg", co_v2)  # the observation is visible, its conformer group is not
    ext["sdet_visible_group"] = basin("selvg", co_v)

    # ---- statmech ----------------------------------------------------------------------------------------------------------
    # Its own scale factor: the world's ``fsf`` has a counted, listed set of users that other tests pin.
    fsf = make_frequency_scale_factor(session, lot=lot, source_literature=lit)
    sm_v2 = vis["sm_v2"]

    def declared_statmech(tag, scope, *, group=None, members=()):
        host = make_statmech(
            session, species_entry=sa, optical_isomers=1, external_symmetry=2, frequency_scale_factor_id=fsf.id
        )
        attach_statmech_source_calculation(
            session, statmech=host, calculation=vis["calc_vopt"], role=StatmechCalculationRole.opt
        )
        attach_statmech_source_calculation(
            session, statmech=host, calculation=vis["calc_vf"], role=StatmechCalculationRole.freq
        )
        declaration = StatmechModelDeclaration(
            statmech_id=host.id,
            schema_version=1,
            scope_kind=scope,
            conformer_group_id=group.id if group is not None else None,
            literature_id=lit.id,
            recipe=dict(BASE_RECIPE),
            content_hash=_hash(f"declaration-{tag}"),
        )
        session.add(declaration)
        session.flush()
        for key, role, kwargs in (
            ("geom", StatmechModelSourceRole.geometry, {"calculation_id": vis["calc_vopt"].id}),
            # a source calculation that is hidden: the role then reads as unavailable, with no ref
            ("curv", StatmechModelSourceRole.curvature, {"calculation_id": calc_h.id}),
            ("fsf", StatmechModelSourceRole.scale_factor, {"frequency_scale_factor_id": fsf.id}),
        ):
            session.add(StatmechModelSource(declaration_id=declaration.id, source_key=key, role=role, **kwargs))
        for key, member_statmech, member_group in members:
            session.add(
                StatmechModelMember(
                    declaration_id=declaration.id,
                    member_key=key,
                    member_statmech_id=member_statmech.id,
                    conformer_group_id=member_group.id if member_group is not None else None,
                    counting_multiplicity=1,
                    relative_energy_kj_mol=0.0,
                    relative_energy_convention="electronic_plus_zpe",
                )
            )
        session.flush()
        vis[f"sm_{tag}"] = host
        return host

    declared_statmech("vdecl", StatmechModelScopeKind.basin, group=cg_h)  # a basin that is the hidden group
    declared_statmech(
        "vens",
        StatmechModelScopeKind.explicit_equilibrium_ensemble,
        # one member in the hidden group; one whose own record is hidden (sm_h), in the visible group
        members=(("m1", sm_v2, cg_h), ("m2", hid["sm_h"], cg_v)),
    )
    # A visible statmech and a hidden successor, wired as a legacy supersession at the end of the world.
    vis["sm_vsup"] = make_statmech(session, species_entry=sa, external_symmetry=3)
    hid["sm_hsucc"] = make_statmech(session, species_entry=sa, external_symmetry=3)
    attach_statmech_source_calculation(
        session, statmech=vis["sm_vsup"], calculation=vis["calc_vopt"], role=StatmechCalculationRole.opt
    )

    # ---- network: a dedicated network with one eligible visible solve and a hidden one it cites ---------------------------------
    ns = build_world(session)
    ext["network_select_world"] = ns
    hidden_solve = add_solve(session, ns, fits=[fit_spec("assoc", det="hid", model="chebyshev")], review=None)
    visible_solve = add_solve(
        session,
        ns,
        fits=[fit_spec("assoc", det="vis", model="chebyshev")],
        validation={
            "version": 1,
            "entries": [
                {"kind": "model_fidelity", "domain": validity(), "reference_solve_ref": hidden_solve.public_ref}
            ],
        },
        review=None,
    )
    vis["net_sel"], vis["nsolve_vsel"] = ns.network, visible_solve
    hid["nsolve_hsel"] = hidden_solve
    session.flush()
