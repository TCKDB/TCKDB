"""Integration tests for the unified pressure-dependent network upload workflow."""

from __future__ import annotations

from copy import deepcopy

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from tckdb_schemas.upload_warning import UploadWarning

from app.db.models.app_user import AppUser
from app.db.models.calculation import (
    Calculation,
    CalculationFreqResult,
    CalculationOptResult,
    CalculationOutputGeometry,
    CalculationSPResult,
)
from app.db.models.common import (
    CalculationType,
    NetworkEnergyTransferScope,
    NetworkSolveKind,
)
from app.db.models.network import Network, NetworkReaction, NetworkSpecies
from app.db.models.network_pdep import (
    NetworkChannel,
    NetworkChannelMicroReaction,
    NetworkSolve,
    NetworkSolveBathGas,
    NetworkSolveChannelBarrier,
    NetworkSolveEnergyTransfer,
    NetworkSolveSourceCalculation,
    NetworkState,
)
from app.db.models.species import (
    ConformerGroup,
    ConformerObservation,
)
from app.db.models.statmech import Statmech, StatmechSourceCalculation
from app.db.models.transition_state import (
    TransitionState,
    TransitionStateEntry,
    TransitionStateValidationEvidence,
)
from app.schemas.workflows.network_pdep_upload import NetworkPDepUploadRequest
from app.services.provenance_warnings import (
    W_NETWORK_WIDE_ENERGY_TRANSFER,
    W_REPORTED_NETWORK_SOLVE,
)
from app.services.scientific_read.networks import get_network, get_network_solve
from app.workflows.network_pdep import persist_network_pdep_upload

# Every geometry below carries the atoms its species actually has. They used
# to omit hydrogen entirely -- ethyl was "C C H", the elimination saddle point
# was "C C O O" -- which left the fixture's transition state as C2O2 for a
# C2H5O2 reaction. Nothing checked that until
# ``validate_transition_state_composition``, which refuses a saddle point that
# is not made of its own reaction's atoms. The coordinates stay schematic; the
# composition does not.
_XYZ_ETHYL = (
    "7\nC2H5\n"
    "C  0.00  0.00  0.00\n"
    "C  1.49  0.00  0.00\n"
    "H -0.38  1.01  0.00\n"
    "H -0.38 -0.51  0.88\n"
    "H -0.38 -0.51 -0.88\n"
    "H  1.99  0.94  0.00\n"
    "H  1.99 -0.94  0.00"
)
_XYZ_O2 = "2\nO2\nO 0.0 0.0 0.0\nO 1.21 0.0 0.0"
_XYZ_ETOO = (
    "9\nC2H5OO\n"
    "C  0.00  0.00  0.00\n"
    "C  1.51  0.00  0.00\n"
    "O  2.05  1.28  0.00\n"
    "O  3.44  1.30  0.00\n"
    "H -0.38  1.01  0.00\n"
    "H -0.38 -0.51  0.88\n"
    "H -0.38 -0.51 -0.88\n"
    "H  1.88 -0.53  0.88\n"
    "H  1.88 -0.53 -0.88"
)
_XYZ_TS = (
    "9\nTS for C2H5OO -> C2H4 + HO2\n"
    "C  0.00  0.00  0.00\n"
    "C  1.42  0.00  0.00\n"
    "O  2.14  1.24  0.00\n"
    "O  3.42  1.06  0.00\n"
    "H -0.32  1.03  0.00\n"
    "H -0.42 -0.55  0.86\n"
    "H -0.42 -0.55 -0.86\n"
    "H  1.92 -0.61  0.78\n"
    "H  2.71 -0.74 -0.42"
)
_XYZ_ETHENE = (
    "6\nC2H4\n"
    "C  0.00  0.00  0.00\n"
    "C  1.33  0.00  0.00\n"
    "H -0.57  0.92  0.00\n"
    "H -0.57 -0.92  0.00\n"
    "H  1.90  0.92  0.00\n"
    "H  1.90 -0.92  0.00"
)
_XYZ_HO2 = "3\nHO2\nO 0.0 0.0 0.0\nO 1.33 0.0 0.0\nH -0.35 0.92 0.0"
_XYZ_AR = "1\nAr\nAr 0.0 0.0 0.0"

_SOFTWARE = {"name": "Gaussian", "version": "16"}
_LOT_DFT = {"method": "B3LYP", "basis": "6-31G(d)"}
_LOT_CC = {"method": "CCSD(T)", "basis": "cc-pVTZ"}

# Machine-token energy conventions (free text is no longer accepted).
_CONVENTIONS = {
    "energy_zero_convention": "entrance_channel",
    "correction_convention": "electronic_plus_zpe",
}

# The elimination TS has nine atoms (C2H5O2). Passing IRC evidence must
# account for every one of them on BOTH sides: the whole C2H5OO skeleton on
# the reactant side, splitting into C2H4 and HO2 on the product side.
#
# ``_XYZ_TS`` lists them in the order ``1 C, 2 C, 3 O, 4 O, 5-9 H``, and the
# partition has to follow the *atoms*, not just their count. The micro
# reaction is ethylperoxy -> ethene + HO2, so ``product:1`` (C2H4) takes both
# carbons and four hydrogens, and ``product:2`` (HO2) takes both oxygens and
# the fifth hydrogen.
#
# Handing ethene the oxygens instead would still cover nine atoms exactly once,
# and for a while that was enough to pass: the partition was bounds-checked and
# never element-checked, so the indices below were only as trustworthy as this
# comment. ``validate_ts_evidence_participant_composition`` now checks them
# against the geometry, and
# ``test_pdep_ts_evidence_refuses_ethene_made_of_oxygens`` holds that door shut.
_ELIM_TS_ATOMS = list(range(1, 10))
_ELIM_REACTANT_MAP = {"reactant:1": _ELIM_TS_ATOMS}
_ELIM_PRODUCT_MAP = {
    "product:1": [1, 2, 5, 6, 7, 8],  # C2H4: C, C, H, H, H, H
    "product:2": [3, 4, 9],  # HO2: O, O, H
}


def _full_payload(*, include_solve: bool = True) -> dict:
    """Build a full unified PDep payload with conformers, calcs, TS, and solve.

    Models the textbook C2H5 + O2 surface honestly:

    - ``entrance -> well_RO2`` (and its reverse) is a **barrierless**
      radical-radical association. There is no saddle point, so the path
      declares ``transition_state_key: None`` and carries no barrier — the
      earlier fixture's ``forward_barrier_kj_mol: 15.0`` was an invented
      number for a reaction that has no barrier at all.
    - ``well_RO2 -> exit`` is the concerted HO2 elimination to C2H4 + HO2,
      which does have a genuine saddle point, IRC evidence, and a barrier.
    """
    species_list = [
        {
            "key": "ethyl",
            "species_entry": {"smiles": "C[CH2]", "charge": 0, "multiplicity": 2},
            "conformers": [{
                "key": "ethyl_conf1",
                "geometry": {"key": "ethyl_geom", "xyz_text": _XYZ_ETHYL},
                "calculation": {
                    "key": "ethyl_opt", "type": "opt",
                    "software_release": _SOFTWARE, "level_of_theory": _LOT_DFT,
                    "opt_converged": True, "opt_final_energy_hartree": -79.5,
                },
            }],
            "calculations": [
                {
                    "key": "ethyl_freq", "type": "freq", "geometry_key": "ethyl_geom",
                    "software_release": _SOFTWARE, "level_of_theory": _LOT_DFT,
                    "freq_n_imag": 0, "freq_zpe_hartree": 0.05,
                },
                {
                    "key": "ethyl_sp", "type": "sp", "geometry_key": "ethyl_geom",
                    "software_release": _SOFTWARE, "level_of_theory": _LOT_CC,
                    "sp_electronic_energy_hartree": -79.8,
                },
            ],
        },
        {
            "key": "O2",
            "species_entry": {"smiles": "[O][O]", "charge": 0, "multiplicity": 3},
            "conformers": [{
                "key": "O2_conf1",
                "geometry": {"key": "O2_geom", "xyz_text": _XYZ_O2},
                "calculation": {
                    "key": "O2_opt", "type": "opt",
                    "software_release": _SOFTWARE, "level_of_theory": _LOT_DFT,
                },
            }],
            "calculations": [
                {
                    "key": "O2_sp", "type": "sp", "geometry_key": "O2_geom",
                    "software_release": _SOFTWARE, "level_of_theory": _LOT_CC,
                    "sp_electronic_energy_hartree": -150.2,
                },
            ],
        },
        {
            "key": "ethylperoxy",
            "species_entry": {"smiles": "CCO[O]", "charge": 0, "multiplicity": 2},
            "label": "C2H5OO",
            "conformers": [{
                "key": "etoo_conf1",
                "geometry": {"key": "etoo_geom", "xyz_text": _XYZ_ETOO},
                "calculation": {
                    "key": "etoo_opt", "type": "opt",
                    "software_release": _SOFTWARE, "level_of_theory": _LOT_DFT,
                },
            }],
            "calculations": [
                {
                    "key": "etoo_sp", "type": "sp", "geometry_key": "etoo_geom",
                    "software_release": _SOFTWARE, "level_of_theory": _LOT_CC,
                    "sp_electronic_energy_hartree": -229.1,
                },
            ],
        },
    ]
    species_list.extend(
        [
            {
                "key": "ethene",
                "species_entry": {"smiles": "C=C", "charge": 0, "multiplicity": 1},
                "conformers": [{
                    "key": "ethene_conf1",
                    "geometry": {"key": "ethene_geom", "xyz_text": _XYZ_ETHENE},
                    "calculation": {
                        "key": "ethene_opt", "type": "opt",
                        "software_release": _SOFTWARE, "level_of_theory": _LOT_DFT,
                    },
                }],
                "calculations": [
                    {
                        "key": "ethene_sp", "type": "sp", "geometry_key": "ethene_geom",
                        "software_release": _SOFTWARE, "level_of_theory": _LOT_CC,
                        "sp_electronic_energy_hartree": -78.4,
                    },
                ],
            },
            {
                "key": "HO2",
                "species_entry": {"smiles": "O[O]", "charge": 0, "multiplicity": 2},
                "conformers": [{
                    "key": "HO2_conf1",
                    "geometry": {"key": "HO2_geom", "xyz_text": _XYZ_HO2},
                    "calculation": {
                        "key": "HO2_opt", "type": "opt",
                        "software_release": _SOFTWARE, "level_of_theory": _LOT_DFT,
                    },
                }],
                "calculations": [
                    {
                        "key": "HO2_sp", "type": "sp", "geometry_key": "HO2_geom",
                        "software_release": _SOFTWARE, "level_of_theory": _LOT_CC,
                        "sp_electronic_energy_hartree": -150.8,
                    },
                ],
            },
        ]
    )
    if include_solve:
        species_list.append(
            {
                "key": "Ar",
                "species_entry": {"smiles": "[Ar]", "charge": 0, "multiplicity": 1},
                "conformers": [{
                    "key": "Ar_conf1",
                    "geometry": {"key": "Ar_geom", "xyz_text": _XYZ_AR},
                    "calculation": {
                        "key": "Ar_opt", "type": "opt",
                        "software_release": _SOFTWARE, "level_of_theory": _LOT_DFT,
                    },
                }],
            }
        )

    payload = {
        "name": "ethyl + O2",
        "species": species_list,
        "transition_states": [_elimination_ts()],
        "micro_reactions": [
            {
                "key": "rxn_assoc",
                "reversible": True,
                "reactants": [{"species_key": "ethyl"}, {"species_key": "O2"}],
                "products": [{"species_key": "ethylperoxy"}],
            },
            {
                "key": "rxn_ho2_elim",
                "reversible": True,
                "reactants": [{"species_key": "ethylperoxy"}],
                "products": [{"species_key": "ethene"}, {"species_key": "HO2"}],
            },
        ],
        "states": [
            {
                "key": "entrance",
                "kind": "bimolecular",
                "participants": [
                    {"species_key": "ethyl"},
                    {"species_key": "O2"},
                ],
            },
            {
                "key": "well_RO2",
                "kind": "well",
                "label": "C2H5OO*",
                "participants": [{"species_key": "ethylperoxy"}],
            },
            {
                "key": "exit",
                "kind": "bimolecular",
                "label": "C2H4 + HO2",
                "participants": [
                    {"species_key": "ethene"},
                    {"species_key": "HO2"},
                ],
            },
        ],
        "channels": [
            # Barrierless association/dissociation: no saddle point exists, so
            # the path declares none rather than inventing one.
            {"key": "association_path", "source_state_key": "entrance", "sink_state_key": "well_RO2", "kind": "association", "microreaction_paths": [{"micro_reaction_key": "rxn_assoc"}]},
            {"key": "dissociation_path", "source_state_key": "well_RO2", "sink_state_key": "entrance", "kind": "dissociation", "microreaction_paths": [{"micro_reaction_key": "rxn_assoc"}]},
            {"key": "elimination_path", "source_state_key": "well_RO2", "sink_state_key": "exit", "kind": "dissociation", "microreaction_paths": [{"micro_reaction_key": "rxn_ho2_elim", "transition_state_key": "ts_elim"}]},
        ],
    }

    if include_solve:
        payload["solve"] = {
            "me_method": "reservoir_state",
            "tmin_k": 300,
            "tmax_k": 2000,
            "pmin_bar": 0.01,
            "pmax_bar": 100,
            "grain_count": 250,
            "bath_gas": [{"species_key": "Ar", "mole_fraction": 1.0}],
            "energy_transfer": [{
                "model": "single_exponential_down",
                "alpha0_cm_inv": 300,
                "t_ref_k": 300,
                "state_key": "well_RO2",
                "collider_species_key": "Ar",
            }],
            "state_energies": [
                {"state_key": "entrance", "energy_kj_mol": 0.0, **_CONVENTIONS, "source_calculation_key": "ethyl_sp"},
                {"state_key": "well_RO2", "energy_kj_mol": -120.0, **_CONVENTIONS, "source_calculation_key": "etoo_sp"},
                {"state_key": "exit", "energy_kj_mol": -60.0, **_CONVENTIONS, "source_calculation_key": "ethene_sp"},
            ],
            "channel_barriers": [
                # Submerged: the elimination saddle point sits 15 kJ/mol BELOW
                # the entrance channel, so the forward barrier is negative on
                # this zero. That is physically routine and must be storable.
                {"channel_key": "elimination_path", "micro_reaction_key": "rxn_ho2_elim", "transition_state_key": "ts_elim", "forward_barrier_kj_mol": 105.0, "reverse_barrier_kj_mol": 45.0, **_CONVENTIONS, "source_calculation_key": "ts_elim_sp"},
            ],
            "source_calculations": [
                {"calculation_key": "ethyl_sp", "role": "well_energy"},
                {"calculation_key": "O2_sp", "role": "well_energy"},
                {"calculation_key": "etoo_sp", "role": "well_energy"},
                {"calculation_key": "ts_elim_sp", "role": "barrier_energy"},
                {"calculation_key": "ts_elim_freq", "role": "barrier_freq"},
            ],
        }

    return payload


def _elimination_ts(
    *,
    key: str = "ts_elim",
    geometry_key: str = "ts_elim_geom",
    prefix: str = "ts_elim",
    imag_freq: float = -1500.0,
    sp_hartree: float = -229.5,
) -> dict:
    """One concerted-HO2-elimination saddle point with complete IRC evidence."""
    return {
        "key": key,
        "micro_reaction_key": "rxn_ho2_elim",
        "charge": 0,
        "multiplicity": 2,
        "geometry": {"key": geometry_key, "xyz_text": _XYZ_TS},
        "calculation": {
            "key": f"{prefix}_opt", "type": "opt",
            "software_release": _SOFTWARE, "level_of_theory": _LOT_DFT,
            "opt_converged": True,
        },
        "calculations": [
            {
                "key": f"{prefix}_freq", "type": "freq",
                "geometry_key": geometry_key,
                "software_release": _SOFTWARE, "level_of_theory": _LOT_DFT,
                "freq_n_imag": 1, "freq_imag_freq_cm1": imag_freq,
            },
            {
                "key": f"{prefix}_sp", "type": "sp",
                "geometry_key": geometry_key,
                "software_release": _SOFTWARE, "level_of_theory": _LOT_CC,
                "sp_electronic_energy_hartree": sp_hartree,
            },
            {
                "key": f"{prefix}_irc", "type": "irc",
                "geometry_key": geometry_key,
                "software_release": _SOFTWARE, "level_of_theory": _LOT_DFT,
            },
        ],
        "statmech": {
            "statmech_treatment": "rrho",
            "source_calculations": [{"calculation_key": f"{prefix}_freq", "role": "freq"}],
        },
        "validation_evidence": [
            {
                "kind": "irc",
                "passed": True,
                "rationale": "IRC descends to C2H5OO one way and C2H4 + HO2 the other.",
                "source_calculation_key": f"{prefix}_irc",
                "reactant_participant_mapping": _ELIM_REACTANT_MAP,
                "product_participant_mapping": _ELIM_PRODUCT_MAP,
            }
        ],
    }


def _parallel_path_payload() -> dict:
    """Two saddle points for ONE elementary step, over one channel.

    ``network_channel_microreaction``'s primary key is
    ``(channel, reaction_entry, transition_state_entry)`` precisely to allow
    this: a single elementary reaction proceeding through two distinct
    conformational transition states (syn/anti HO2 elimination). It is
    modelled with ONE micro reaction, never two byte-identical ones — a
    duplicate reaction would manufacture a second row in an identity table.
    """
    payload = deepcopy(_full_payload())
    payload["transition_states"].append(
        _elimination_ts(
            key="ts_elim_anti",
            geometry_key="ts_elim_anti_geom",
            prefix="ts_elim_anti",
            imag_freq=-1200.0,
            sp_hartree=-229.45,
        )
    )
    elimination = next(
        channel for channel in payload["channels"] if channel["key"] == "elimination_path"
    )
    elimination["microreaction_paths"].append(
        {"micro_reaction_key": "rxn_ho2_elim", "transition_state_key": "ts_elim_anti"}
    )
    payload["solve"]["channel_barriers"].append({
        "channel_key": "elimination_path", "micro_reaction_key": "rxn_ho2_elim",
        "transition_state_key": "ts_elim_anti",
        "forward_barrier_kj_mol": 118.0, "reverse_barrier_kj_mol": 58.0,
        **_CONVENTIONS, "source_calculation_key": "ts_elim_anti_sp",
    })
    # A second C2H5O2 well makes this a genuine multi-well network rather
    # than merely a two-path elimination example.
    payload["species"].append({
        "key": "ethylperoxy_isomer", "species_entry": {"smiles": "C[CH]OO", "charge": 0, "multiplicity": 2},
        "conformers": [{"key": "etoo_iso_conf", "geometry": {"key": "etoo_iso_geom", "xyz_text": _XYZ_ETOO}, "calculation": {"key": "etoo_iso_opt", "type": "opt", "software_release": _SOFTWARE, "level_of_theory": _LOT_DFT}}],
        "calculations": [{"key": "etoo_iso_sp", "type": "sp", "geometry_key": "etoo_iso_geom", "software_release": _SOFTWARE, "level_of_theory": _LOT_CC, "sp_electronic_energy_hartree": -229.08}],
    })
    payload["states"].append({"key": "well_iso", "kind": "well", "participants": [{"species_key": "ethylperoxy_isomer"}]})
    payload["micro_reactions"].append({"key": "rxn_isomer", "reversible": True, "reactants": [{"species_key": "ethylperoxy"}], "products": [{"species_key": "ethylperoxy_isomer"}]})
    payload["transition_states"].append({
        "key": "ts_isomer", "micro_reaction_key": "rxn_isomer", "charge": 0, "multiplicity": 2,
        "geometry": {"key": "ts_isomer_geom", "xyz_text": _XYZ_TS},
        "calculation": {"key": "ts_isomer_opt", "type": "opt", "software_release": _SOFTWARE, "level_of_theory": _LOT_DFT},
        "calculations": [
            {"key": "ts_isomer_freq", "type": "freq", "geometry_key": "ts_isomer_geom", "software_release": _SOFTWARE, "level_of_theory": _LOT_DFT, "freq_n_imag": 1, "freq_imag_freq_cm1": -900.0},
            {"key": "ts_isomer_sp", "type": "sp", "geometry_key": "ts_isomer_geom", "software_release": _SOFTWARE, "level_of_theory": _LOT_CC, "sp_electronic_energy_hartree": -229.4},
            {"key": "ts_isomer_irc", "type": "irc", "geometry_key": "ts_isomer_geom", "software_release": _SOFTWARE, "level_of_theory": _LOT_DFT},
        ],
        "statmech": {"statmech_treatment": "rrho", "source_calculations": [{"calculation_key": "ts_isomer_freq", "role": "freq"}]},
        # A 1->1 isomerization: every one of the nine TS atoms belongs to the
        # single reactant and, after the H shift, to the single product.
        "validation_evidence": [{"kind": "irc", "passed": True, "rationale": "IRC connects the two C2H5O2 wells.", "source_calculation_key": "ts_isomer_irc", "reactant_participant_mapping": {"reactant:1": _ELIM_TS_ATOMS}, "product_participant_mapping": {"product:1": _ELIM_TS_ATOMS}}],
    })
    payload["channels"].append({"key": "isomerization_path", "source_state_key": "well_RO2", "sink_state_key": "well_iso", "kind": "isomerization", "microreaction_paths": [{"micro_reaction_key": "rxn_isomer", "transition_state_key": "ts_isomer"}]})
    payload["solve"]["state_energies"].append({"state_key": "well_iso", "energy_kj_mol": -110.0, **_CONVENTIONS, "source_calculation_key": "etoo_iso_sp"})
    payload["solve"]["channel_barriers"].append({"channel_key": "isomerization_path", "micro_reaction_key": "rxn_isomer", "transition_state_key": "ts_isomer", "forward_barrier_kj_mol": 25.0, "reverse_barrier_kj_mol": 35.0, **_CONVENTIONS, "source_calculation_key": "ts_isomer_sp"})
    # Two wells now, so the (well, collider) cross product needs both.
    payload["solve"]["energy_transfer"].append({
        "model": "single_exponential_down", "alpha0_cm_inv": 280, "t_ref_k": 300,
        "state_key": "well_iso", "collider_species_key": "Ar",
    })
    payload["solve"]["source_calculations"].extend([{"calculation_key": "etoo_iso_sp", "role": "well_energy"}, {"calculation_key": "ts_isomer_sp", "role": "barrier_energy"}, {"calculation_key": "ts_isomer_freq", "role": "barrier_freq"}])
    return payload


def test_parallel_path_upload_persists_ts_subject_and_distinct_barriers(db_engine) -> None:
    """One elementary step retains two distinct saddle-point pathways.

    Regression guard for the schema rule that used to forbid a second TS per
    micro reaction: the only way to express parallel paths was to declare a
    duplicate micro reaction with byte-identical stoichiometry, which injects
    a duplicate row into an identity table. Here the two paths share ONE
    reaction entry and differ only by transition state.
    """
    with Session(db_engine) as session, session.begin():
        # Let the sequence assign the id: hard-coded ids do not advance the
        # app_user sequence and collide with other files that insert without one.
        actor = AppUser(username="parallel_path_tester")
        session.add(actor)
        session.flush()
        network = persist_network_pdep_upload(
            session, NetworkPDepUploadRequest(**_parallel_path_payload()), created_by=actor.id
        )
        elimination = session.scalars(
            select(NetworkChannel).where(NetworkChannel.network_id == network.id, NetworkChannel.channel_key == "elimination_path")
        ).one()
        links = session.scalars(
            select(NetworkChannelMicroReaction).where(NetworkChannelMicroReaction.channel_id == elimination.id)
        ).all()
        assert len(links) == 2
        # ONE reaction identity, TWO transition states.
        assert len({link.reaction_entry_id for link in links}) == 1
        assert len({link.transition_state_entry_id for link in links}) == 2
        solve = session.scalars(select(NetworkSolve).where(NetworkSolve.network_id == network.id)).one()
        barriers = session.scalars(
            select(NetworkSolveChannelBarrier).where(NetworkSolveChannelBarrier.solve_id == solve.id, NetworkSolveChannelBarrier.channel_id == elimination.id)
        ).all()
        assert len(barriers) == 2
        assert {barrier.forward_barrier_kj_mol for barrier in barriers} == {105.0, 118.0}
        wells = session.scalars(
            select(NetworkState).where(NetworkState.network_id == network.id, NetworkState.kind == "well")
        ).all()
        assert len(wells) >= 2
        ts_statmech = session.scalars(
            select(Statmech).where(Statmech.transition_state_entry_id.in_({link.transition_state_entry_id for link in links}))
        ).all()
        assert len(ts_statmech) == 2
        assert all(record.species_entry_id is None for record in ts_statmech)
        assert all(
            source.calculation.transition_state_entry_id == source.statmech.transition_state_entry_id
            for source in session.scalars(
                select(StatmechSourceCalculation).join(Statmech).where(Statmech.id.in_({record.id for record in ts_statmech}))
            ).all()
        )

        # Public scientific reads preserve the macroscopic channel identity,
        # every microscopic path, solve-local energies/ET, and path barriers.
        network_read = get_network(
            session, network_handle=network.public_ref, include=["states", "channels"]
        )
        assert len(network_read.record.states or []) == 4
        elimination_read = next(
            channel for channel in network_read.record.channels or []
            if channel.channel_key == "elimination_path"
        )
        assert len(elimination_read.microreactions) == 2
        assert len({path.reaction_entry_ref for path in elimination_read.microreactions}) == 1
        assert len({path.transition_state_entry_ref for path in elimination_read.microreactions}) == 2
        assert {path.path_kind for path in elimination_read.microreactions} == {"saddle_point"}

        # The barrierless association channel round-trips as such.
        association_read = next(
            channel for channel in network_read.record.channels or []
            if channel.channel_key == "association_path"
        )
        assert len(association_read.microreactions) == 1
        assert association_read.microreactions[0].path_kind == "barrierless"
        assert association_read.microreactions[0].transition_state_entry_ref is None

        solve_read = get_network_solve(
            session,
            network_solve_handle=solve.public_ref,
            include=["state_energies", "energy_transfer", "channel_barriers"],
        )
        assert len(solve_read.record.state_energies or []) == 4
        # Both wells declare their own ⟨ΔE⟩down against the argon bath.
        assert len(solve_read.record.energy_transfer or []) == 2
        assert all(
            row.state_composition_hash is not None
            for row in solve_read.record.energy_transfer
        )
        barriers_read = solve_read.record.channel_barriers or []
        assert len(barriers_read) == 3
        assert {
            (barrier.channel_key, barrier.forward_barrier_kj_mol, barrier.reverse_barrier_kj_mol)
            for barrier in barriers_read
        } == {
            ("elimination_path", 105.0, 45.0),
            ("elimination_path", 118.0, 58.0),
            ("isomerization_path", 25.0, 35.0),
        }
        # Every barrier names the calculation it came from.
        assert all(
            barrier.source_calculation_ref is not None for barrier in barriers_read
        )


def test_full_end_to_end_upload(db_engine) -> None:
    """Full PDep upload creates all entities end-to-end."""
    with Session(db_engine) as session, session.begin():
        actor = AppUser(username="e2e_tester")
        session.add(actor)
        session.flush()

        request = NetworkPDepUploadRequest(**_full_payload())
        network = persist_network_pdep_upload(session, request, created_by=actor.id)

        # -- Network --
        assert network.id is not None
        assert network.name == "ethyl + O2"

        # -- States: entrance + well_RO2 + exit --
        states = session.scalars(
            select(NetworkState).where(NetworkState.network_id == network.id)
        ).all()
        assert len(states) == 3

        # -- Channels: association, dissociation, HO2 elimination --
        channels = session.scalars(
            select(NetworkChannel).where(NetworkChannel.network_id == network.id)
        ).all()
        assert len(channels) == 3

        # -- Micro reactions: association + elimination --
        rxn_links = session.scalars(
            select(NetworkReaction).where(NetworkReaction.network_id == network.id)
        ).all()
        assert len(rxn_links) == 2

        # -- Conformers: ethyl, O2, ethylperoxy, ethene, HO2, Ar --
        conformers = session.scalars(select(ConformerObservation)).all()
        assert len(conformers) >= 6

        # -- Calculations: 6 opts + 5 sp + 1 freq (species-side)
        #                  + 1 opt + 1 freq + 1 sp + 1 irc (TS-side) = 16
        all_calcs = session.scalars(select(Calculation)).all()
        assert len(all_calcs) >= 16

        # -- Calculation results --
        sp_results = session.scalars(select(CalculationSPResult)).all()
        assert len(sp_results) >= 6  # ethyl, O2, etoo, ethene, HO2, ts_elim

        opt_results = session.scalars(select(CalculationOptResult)).all()
        assert len(opt_results) >= 2  # ethyl (converged), ts_elim (converged)

        freq_results = session.scalars(select(CalculationFreqResult)).all()
        assert len(freq_results) >= 2  # ethyl (n_imag=0), ts_elim (n_imag=1)

        # -- Geometry linkage --
        output_geoms = session.scalars(select(CalculationOutputGeometry)).all()
        assert len(output_geoms) >= 16  # every calculation has a geometry link

        # -- Transition state: only the elimination step carries one --
        elimination_entry_id = session.scalars(
            select(NetworkChannelMicroReaction.transition_state_entry_id)
            .join(NetworkChannel, NetworkChannel.id == NetworkChannelMicroReaction.channel_id)
            .where(
                NetworkChannel.network_id == network.id,
                NetworkChannel.channel_key == "elimination_path",
            )
        ).one()
        assert elimination_entry_id is not None
        elimination_reaction_id = session.scalars(
            select(NetworkChannelMicroReaction.reaction_entry_id)
            .join(NetworkChannel, NetworkChannel.id == NetworkChannelMicroReaction.channel_id)
            .where(
                NetworkChannel.network_id == network.id,
                NetworkChannel.channel_key == "elimination_path",
            )
        ).one()

        # The barrierless association path stores a NULL transition state.
        association_ts_ids = session.scalars(
            select(NetworkChannelMicroReaction.transition_state_entry_id)
            .join(NetworkChannel, NetworkChannel.id == NetworkChannelMicroReaction.channel_id)
            .where(
                NetworkChannel.network_id == network.id,
                NetworkChannel.channel_key == "association_path",
            )
        ).all()
        assert association_ts_ids == [None]

        ts_list = session.scalars(
            select(TransitionState).where(
                TransitionState.reaction_entry_id == elimination_reaction_id
            )
        ).all()
        assert len(ts_list) == 1

        ts_entries = session.scalars(
            select(TransitionStateEntry).join(TransitionState).where(
                TransitionState.reaction_entry_id == elimination_reaction_id
            )
        ).all()
        assert len(ts_entries) == 1
        assert ts_entries[0].id == elimination_entry_id
        assert ts_entries[0].charge == 0
        assert ts_entries[0].multiplicity == 2
        evidence = session.scalars(
            select(TransitionStateValidationEvidence).where(
                TransitionStateValidationEvidence.transition_state_entry_id == ts_entries[0].id
            )
        ).all()
        assert len(evidence) == 1
        assert evidence[0].kind == "irc"
        # Passing evidence accounts for every TS atom on both sides.
        assert sorted(
            i for atoms in evidence[0].reactant_participant_mapping.values() for i in atoms
        ) == _ELIM_TS_ATOMS
        assert sorted(
            i for atoms in evidence[0].product_participant_mapping.values() for i in atoms
        ) == _ELIM_TS_ATOMS

        # TS calculations belong to TS entry
        ts_calcs = session.scalars(
            select(Calculation).where(
                Calculation.transition_state_entry_id == ts_entries[0].id
            )
        ).all()
        assert len(ts_calcs) == 4  # opt, freq, sp, irc

        # -- Solve --
        solves = session.scalars(
            select(NetworkSolve).where(NetworkSolve.network_id == network.id)
        ).all()
        assert len(solves) == 1
        solve = solves[0]
        assert solve.me_method == "reservoir_state"

        # Source calculations linked
        source_calcs = session.scalars(
            select(NetworkSolveSourceCalculation).where(
                NetworkSolveSourceCalculation.solve_id == solve.id
            )
        ).all()
        assert len(source_calcs) == 5

        # Verify roles
        roles = sorted(sc.role.value for sc in source_calcs)
        assert roles == [
            "barrier_energy",
            "barrier_freq",
            "well_energy",
            "well_energy",
            "well_energy",
        ]

        # Bath gas
        bath_gases = session.scalars(
            select(NetworkSolveBathGas).where(
                NetworkSolveBathGas.solve_id == solve.id
            )
        ).all()
        assert len(bath_gases) == 1

        # Energy transfer
        energy_transfers = session.scalars(
            select(NetworkSolveEnergyTransfer).where(
                NetworkSolveEnergyTransfer.solve_id == solve.id
            )
        ).all()
        assert len(energy_transfers) == 1


def test_upload_without_solve(db_engine) -> None:
    """Upload without solve creates species, calcs, TS, but no solve."""
    with Session(db_engine) as session, session.begin():
        request = NetworkPDepUploadRequest(**_full_payload(include_solve=False))
        network = persist_network_pdep_upload(session, request)

        assert network.id is not None

        solves = session.scalars(
            select(NetworkSolve).where(NetworkSolve.network_id == network.id)
        ).all()
        assert len(solves) == 0

        # TS still created
        ts_list = session.scalars(select(TransitionState)).all()
        assert len(ts_list) >= 1


def test_composition_hash_order_independent() -> None:
    """Composition hash is the same regardless of participant order."""
    from app.workflows.network_pdep import _composition_hash

    hash_a = _composition_hash([(1, 1), (2, 1)])
    hash_b = _composition_hash([(2, 1), (1, 1)])
    assert hash_a == hash_b
    assert len(hash_a) == 64


def test_geometry_reuse_via_key(db_engine) -> None:
    """A species freq calculation using geometry_key should share the geometry."""
    with Session(db_engine) as session, session.begin():
        request = NetworkPDepUploadRequest(**_full_payload(include_solve=False))
        network = persist_network_pdep_upload(session, request)

        # Get species_entry_ids for this network's species
        species_links = session.scalars(
            select(NetworkSpecies).where(NetworkSpecies.network_id == network.id)
        ).all()
        network_se_ids = {sl.species_entry_id for sl in species_links}

        # Get all calculations owned by those species entries
        network_calcs = session.scalars(
            select(Calculation).where(
                Calculation.species_entry_id.in_(network_se_ids)
            )
        ).all()

        # Get geometry links for those calculations
        calc_ids = [c.id for c in network_calcs]
        output_geoms = session.scalars(
            select(CalculationOutputGeometry).where(
                CalculationOutputGeometry.calculation_id.in_(calc_ids)
            )
        ).all()
        geom_ids_by_calc = {og.calculation_id: og.geometry_id for og in output_geoms}

        # Group by species_entry_id
        by_species: dict[int, list[int]] = {}
        for c in network_calcs:
            by_species.setdefault(c.species_entry_id, []).append(c.id)

        # For each species with calcs, all calcs should share the same geometry
        for se_id, calc_ids_for_species in by_species.items():
            geom_ids = {
                geom_ids_by_calc[cid]
                for cid in calc_ids_for_species
                if cid in geom_ids_by_calc
            }
            assert len(geom_ids) == 1, (
                f"Species entry {se_id} has calcs pointing to {len(geom_ids)} "
                f"different geometries — expected 1"
            )


def test_same_basin_species_conformers_keep_distinct_observations_and_calc_anchors(
    db_engine,
) -> None:
    """Species-side calculations should anchor to the observation for their geometry key."""
    payload = _full_payload(include_solve=False)
    payload["species"][1]["conformers"] = [
        {
            "key": "o2_conf_a",
            "geometry": {"key": "o2_geom_a", "xyz_text": _XYZ_O2},
            "calculation": {
                "key": "o2_opt_a",
                "type": "opt",
                "software_release": _SOFTWARE,
                "level_of_theory": _LOT_DFT,
                "opt_converged": True,
            },
            "note": "observation a",
        },
        {
            "key": "o2_conf_b",
            "geometry": {"key": "o2_geom_b", "xyz_text": _XYZ_O2},
            "calculation": {
                "key": "o2_opt_b",
                "type": "opt",
                "software_release": _SOFTWARE,
                "level_of_theory": _LOT_DFT,
                "opt_converged": True,
            },
            "note": "observation b",
        },
    ]
    payload["species"][1]["calculations"] = [
        {
            "key": "o2_freq_a",
            "type": "freq",
            "geometry_key": "o2_geom_a",
            "software_release": _SOFTWARE,
            "level_of_theory": _LOT_DFT,
            "freq_n_imag": 0,
            "freq_zpe_hartree": 0.05,
        },
        {
            "key": "o2_sp_b",
            "type": "sp",
            "geometry_key": "o2_geom_b",
            "software_release": _SOFTWARE,
            "level_of_theory": _LOT_CC,
            "sp_electronic_energy_hartree": -150.2,
        },
    ]

    with Session(db_engine) as session, session.begin():
        actor = AppUser(username="anchor_tester")
        session.add(actor)
        session.flush()
        actor_id = actor.id
        request = NetworkPDepUploadRequest(**payload)
        persist_network_pdep_upload(session, request, created_by=actor_id)

        target_entry_id = session.execute(
            select(Calculation.species_entry_id)
            .where(
                Calculation.created_by == actor_id,
                Calculation.type == CalculationType.opt,
                Calculation.species_entry_id.is_not(None),
            )
            .group_by(Calculation.species_entry_id)
            .having(func.count(Calculation.id) == 2)
        ).scalar_one()

        ethyl_observations = session.scalars(
            select(ConformerObservation)
            .join(
                ConformerGroup,
                ConformerGroup.id == ConformerObservation.conformer_group_id,
            )
            .where(
                ConformerGroup.species_entry_id == target_entry_id,
                ConformerObservation.created_by == actor_id,
            )
        ).all()
        assert len(ethyl_observations) == 2
        observation_ids = {obs.id for obs in ethyl_observations}
        assert len({obs.conformer_group_id for obs in ethyl_observations}) == 1

        anchored_calcs = session.scalars(
            select(Calculation).where(
                Calculation.conformer_observation_id.in_(observation_ids),
                Calculation.type.in_([CalculationType.freq, CalculationType.sp]),
                Calculation.species_entry_id == target_entry_id,
                Calculation.created_by == actor_id,
            )
        ).all()
        assert len(anchored_calcs) == 2
        assert {calc.conformer_observation_id for calc in anchored_calcs} == observation_ids


# ---------------------------------------------------------------------------
# Bundle-to-shared-seam convergence regressions
# ---------------------------------------------------------------------------


from contextlib import contextmanager
from typing import Iterator as _Iterator


@contextmanager
def _rolled_back_session(db_engine) -> _Iterator[Session]:
    """Connection-bound session that always rolls back, to isolate tests
    that exercise the bundle workflow without committing to the shared DB."""
    connection = db_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


def test_bundle_calculation_parameters_persist_via_shared_seam(db_engine) -> None:
    """Parsed parameters on a bundle CalculationIn now flow through the shared
    seam and land as ``calculation_parameter`` rows plus snapshot metadata."""
    from datetime import datetime, timezone

    from app.db.models.calculation import (
        CalculationParameter,
        CalculationParameterVocab,
    )

    extracted_at = datetime(2026, 4, 23, 12, 0, 0, tzinfo=timezone.utc)
    canonical_key = "bundle_network_pdep_opt_convergence"

    payload = _full_payload(include_solve=False)
    # Attach parameters + snapshot to the first conformer's opt calculation.
    payload["species"][0]["conformers"][0]["calculation"].update(
        {
            "parameters": [
                {
                    "raw_key": "tight",
                    "raw_value": "tight",
                    "canonical_key": canonical_key,
                    "canonical_value": "tight",
                    "section": "opt",
                    "value_type": "enum",
                },
                {
                    "raw_key": "%mem",
                    "raw_value": "8GB",
                    "section": "resource",
                    "value_type": "string",
                    "unit": "GB",
                },
            ],
            "parameters_json": {"route": "# B3LYP/6-31G(d) opt=tight"},
            "parameters_parser_version": "bundle-test-1",
            "parameters_extracted_at": extracted_at.isoformat(),
        }
    )

    with _rolled_back_session(db_engine) as session:
        session.add(CalculationParameterVocab(canonical_key=canonical_key))
        session.flush()

        request = NetworkPDepUploadRequest(**payload)
        persist_network_pdep_upload(session, request, created_by=None)

        # Scope the query to the distinctive parser_version set by this test
        # so earlier committed test data does not interfere with counts.
        with_params = session.scalars(
            select(Calculation).where(
                Calculation.parameters_parser_version == "bundle-test-1"
            )
        ).all()
        assert len(with_params) == 1
        calc = with_params[0]
        assert calc.parameters_json == {"route": "# B3LYP/6-31G(d) opt=tight"}
        assert calc.parameters_extracted_at is not None

        rows = session.scalars(
            select(CalculationParameter)
            .where(CalculationParameter.calculation_id == calc.id)
            .order_by(CalculationParameter.id)
        ).all()
        assert len(rows) == 2

        first, second = rows
        assert first.raw_key == "tight"
        assert first.canonical_key == canonical_key
        assert first.canonical_value == "tight"

        assert second.raw_key == "%mem"
        # Unknown canonical key is silently demoted by the shared seam.
        assert second.canonical_key is None
        assert second.canonical_value is None
        assert second.unit == "GB"


def test_bundle_unknown_canonical_key_demoted_through_shared_seam(db_engine) -> None:
    """Unknown canonical_key observations still persist (with canonical_key=NULL)
    — shared-seam vocab demotion applies through the bundle path."""
    from app.db.models.calculation import CalculationParameter

    payload = _full_payload(include_solve=False)
    payload["species"][0]["conformers"][0]["calculation"]["parameters"] = [
        {
            "raw_key": "madeup_option",
            "raw_value": "on",
            "canonical_key": "this_does_not_exist",
            "canonical_value": "on",
        }
    ]

    with _rolled_back_session(db_engine) as session:
        request = NetworkPDepUploadRequest(**payload)
        persist_network_pdep_upload(session, request, created_by=None)

        rows = session.scalars(
            select(CalculationParameter).where(
                CalculationParameter.raw_key == "madeup_option"
            )
        ).all()
        assert len(rows) == 1
        assert rows[0].canonical_key is None
        assert rows[0].canonical_value is None


def test_bundle_owner_semantics_preserved_after_convergence(db_engine) -> None:
    """Species-owned and TS-owned calculations keep their exclusive-owner FKs
    after routing through the shared seam."""
    with _rolled_back_session(db_engine) as session:
        baseline_calc_id = session.scalar(select(func.max(Calculation.id))) or 0

        request = NetworkPDepUploadRequest(**_full_payload(include_solve=False))
        persist_network_pdep_upload(session, request, created_by=None)

        new_calcs = session.scalars(
            select(Calculation).where(Calculation.id > baseline_calc_id)
        ).all()
        assert len(new_calcs) > 0
        for c in new_calcs:
            owner_count = (
                (1 if c.species_entry_id is not None else 0)
                + (1 if c.transition_state_entry_id is not None else 0)
            )
            assert owner_count == 1, (
                f"calc {c.id} type={c.type} has {owner_count} owners"
            )

        # TS calculations in this payload are owned by the TS entry only.
        ts_calcs = [
            c for c in new_calcs if c.transition_state_entry_id is not None
        ]
        assert len(ts_calcs) >= 1
        assert all(c.species_entry_id is None for c in ts_calcs)


def test_bundle_inline_results_and_geometry_links_preserved(db_engine) -> None:
    """Inline opt/freq/sp results and the CalculationOutputGeometry link still
    persist correctly after routing through the shared seam."""
    with _rolled_back_session(db_engine) as session:
        # Record the highest calculation.id before the upload so we can scope
        # subsequent queries to just-created rows and ignore any state that
        # prior committed tests may have left behind.
        baseline_calc_id = session.scalar(select(func.max(Calculation.id))) or 0

        request = NetworkPDepUploadRequest(**_full_payload(include_solve=False))
        persist_network_pdep_upload(session, request, created_by=None)

        new_calc_ids = {
            c.id
            for c in session.scalars(
                select(Calculation).where(Calculation.id > baseline_calc_id)
            ).all()
        }
        assert len(new_calc_ids) > 0

        # Opt result for the ethyl conformer, scoped to this test's calcs.
        opt_rows = session.scalars(
            select(CalculationOptResult).where(
                CalculationOptResult.calculation_id.in_(new_calc_ids)
            )
        ).all()
        assert any(r.converged is True for r in opt_rows)

        # SP results: one per species (ethyl, O2, ethylperoxy).
        sp_rows = session.scalars(
            select(CalculationSPResult).where(
                CalculationSPResult.calculation_id.in_(new_calc_ids)
            )
        ).all()
        assert len(sp_rows) >= 3

        # Freq results: ethyl_freq and ts_elim_freq.
        freq_rows = session.scalars(
            select(CalculationFreqResult).where(
                CalculationFreqResult.calculation_id.in_(new_calc_ids)
            )
        ).all()
        assert len(freq_rows) >= 2

        linked_calc_ids = {
            row[0]
            for row in session.execute(
                select(CalculationOutputGeometry.calculation_id)
                .where(CalculationOutputGeometry.calculation_id.in_(new_calc_ids))
                .distinct()
            ).all()
        }
        # Every calculation in this payload has a geometry (directly or via
        # geometry_key), so every new calc should be linked.
        assert linked_calc_ids == new_calc_ids


# ---------------------------------------------------------------------------
# Strict elemental-balance policy also applies inside PDep workflows
# ---------------------------------------------------------------------------


def test_pdep_workflow_rejects_imbalanced_micro_reaction(db_engine) -> None:
    """PDep uploads reuse the shared reaction seam and must enforce
    strict elemental balance on their micro reactions.

    Construct an otherwise-valid payload but drop ``O2`` from the
    association reactants so that ``ethyl -> ethylperoxy`` is no longer
    element-balanced (2 O atoms appear on the product side with no
    matching source on the reactant side).
    """
    payload = _full_payload(include_solve=False)
    payload["micro_reactions"][0]["reactants"] = [{"species_key": "ethyl"}]

    with Session(db_engine) as session, session.begin():
        request = NetworkPDepUploadRequest(**payload)
        with pytest.raises(ValueError, match="not element-balanced"):
            persist_network_pdep_upload(session, request)


def test_pdep_workflow_allows_balanced_micro_reaction(db_engine) -> None:
    """Regression guard: the canonical balanced PDep payload
    (``ethyl + O2 -> ethylperoxy``) must still succeed under the strict
    elemental-balance rule."""
    with Session(db_engine) as session, session.begin():
        request = NetworkPDepUploadRequest(**_full_payload(include_solve=False))
        network = persist_network_pdep_upload(session, request)
        assert network.id is not None


def test_pdep_workflow_persists_calculation_artifacts(
    db_engine, monkeypatch,
) -> None:
    """Inline ``calc_in.artifacts`` on a PDep calculation must produce
    a real ``CalculationArtifact`` row.

    Before the shared persistence refactor the network-pdep workflow
    silently dropped this field; this test pins the new behaviour.
    """
    import base64

    from app.db.models.calculation import CalculationArtifact

    written: list[str] = []

    def _fake_store(content: bytes, sha256: str) -> str:
        uri = f"s3://test-bucket/{sha256[:2]}/{sha256}"
        written.append(uri)
        return uri

    monkeypatch.setattr(
        "app.services.artifact_persistence.store_artifact", _fake_store
    )

    payload = _full_payload(include_solve=False)
    payload["species"][0]["conformers"][0]["calculation"]["artifacts"] = [
        {
            "kind": "ancillary",
            "filename": "note.txt",
            "content_base64": base64.b64encode(b"hello-pdep-art").decode("ascii"),
        }
    ]

    # Use a connection-bound rollback so this artifact row does not leak
    # into other workflow tests sharing the session-scoped ``db_engine``.
    connection = db_engine.connect()
    transaction = connection.begin()
    try:
        session = Session(bind=connection, expire_on_commit=False)
        try:
            request = NetworkPDepUploadRequest(**payload)
            persist_network_pdep_upload(session, request)
            session.flush()
            rows = session.scalars(
                select(CalculationArtifact).where(
                    CalculationArtifact.uri.like("s3://test-bucket/%")
                )
            ).all()
            assert len(rows) == 1
        finally:
            session.close()
    finally:
        transaction.rollback()
        connection.close()


def test_pdep_workflow_persists_and_reads_back_channel_kinetics(db_engine) -> None:
    """A Chebyshev ``channel_kinetics`` entry on the solve produces a
    ``NetworkKinetics`` + ``NetworkKineticsChebyshev`` row for the referenced
    channel, and round-trips through the existing network-kinetics read path.
    """
    from app.db.models.network_pdep import (
        NetworkKinetics,
        NetworkKineticsChebyshev,
    )
    from app.services.scientific_read.network_kinetics import (
        get_network_kinetics,
    )

    n_t, n_p = 6, 4
    # Distinct values so the round-trip is unambiguous.
    grid = [[float(t * 10 + p) for p in range(n_p)] for t in range(n_t)]

    payload = _full_payload(include_solve=True)
    payload["solve"]["channel_kinetics"] = [
            {
                "channel_key": "association_path",
                "source_state_key": "entrance",
            "sink_state_key": "well_RO2",
            "model_kind": "chebyshev",
            "chebyshev": {
                "n_temperature": n_t,
                "n_pressure": n_p,
                "coefficients": grid,
            },
            "tmin_k": 300.0,
            "tmax_k": 2000.0,
            "pmin_bar": 0.01,
            "pmax_bar": 100.0,
            "rate_units": "cm3_mol_s",
            "pressure_units": "bar",
            "temperature_units": "kelvin",
            "stores_log10_k": True,
            "note": "fitted from ME solve",
        }
    ]

    with Session(db_engine) as session, session.begin():
        request = NetworkPDepUploadRequest(**payload)
        network = persist_network_pdep_upload(session, request)
        session.flush()

        solve = session.scalars(
            select(NetworkSolve).where(NetworkSolve.network_id == network.id)
        ).one()

        # The association channel (entrance -> well_RO2) it references.
        assoc_channel = session.scalars(
            select(NetworkChannel).where(
                NetworkChannel.network_id == network.id,
                NetworkChannel.kind == "association",
            )
        ).one()

        # -- NetworkKinetics parent row --
        nk_rows = session.scalars(select(NetworkKinetics)).all()
        assert len(nk_rows) == 1
        nk = nk_rows[0]
        assert nk.channel_id == assoc_channel.id
        assert nk.solve_id == solve.id
        assert nk.model_kind.value == "chebyshev"
        assert nk.tmin_k == 300.0
        assert nk.tmax_k == 2000.0
        assert nk.pmin_bar == 0.01
        assert nk.pmax_bar == 100.0
        assert nk.rate_units.value == "cm3_mol_s"
        assert nk.pressure_units.value == "bar"
        assert nk.temperature_units.value == "kelvin"
        assert nk.stores_log10_k is True

        # -- Chebyshev child row: stored JSONB shape --
        cheb = session.scalars(select(NetworkKineticsChebyshev)).one()
        assert cheb.network_kinetics_id == nk.id
        assert cheb.n_temperature == n_t
        assert cheb.n_pressure == n_p
        assert cheb.coefficients == {"coeffs": grid}

        # -- Read back through the existing read service (round-trip) --
        resp = get_network_kinetics(
            session,
            network_kinetics_handle=str(nk.id),
            include=["coefficients"],
        )
        core = resp.record.network_kinetics
        assert core.model_kind.value == "chebyshev"
        assert core.chebyshev_shape == f"{n_t}x{n_p}"
        # Units survive the round-trip.
        assert core.rate_units.value == "cm3_mol_s"
        assert core.pressure_units.value == "bar"
        assert core.temperature_units.value == "kelvin"
        assert core.stores_log10_k is True
        assert core.tmin_k == 300.0
        assert core.pmax_bar == 100.0

        # Coefficients survive the round-trip: read side flattens the matrix
        # into (temperature_order, pressure_order, coefficient) triples.
        coeff_block = resp.record.coefficients
        assert coeff_block is not None
        assert coeff_block.n_temperature == n_t
        assert coeff_block.n_pressure == n_p
        assert len(coeff_block.coefficients) == n_t * n_p
        read_back = {
            (c.temperature_order, c.pressure_order): c.coefficient
            for c in coeff_block.coefficients
        }
        for t in range(n_t):
            for p in range(n_p):
                assert read_back[(t, p)] == grid[t][p]


def test_pdep_channel_kinetics_rejects_undefined_channel() -> None:
    """A ``channel_kinetics`` entry referencing a distinct state pair with no
    matching ``channels`` entry is rejected by the parent's channel-reference
    integrity validator (not the source!=sink guard)."""
    payload = _full_payload(include_solve=True)
    # Drop the reverse (dissociation) channel so (well_RO2 -> entrance) is a
    # valid distinct-state pair that is NOT a declared channel. The remaining
    # association and elimination channels still keep every state connected.
    payload["channels"] = [
        channel
        for channel in payload["channels"]
        if channel["key"] != "dissociation_path"
    ]
    payload["solve"]["channel_kinetics"] = [
        {
            "source_state_key": "well_RO2",
            "sink_state_key": "entrance",
            "model_kind": "chebyshev",
            # A Chebyshev surface is fit in reduced variables; without bounds
            # it cannot be evaluated at any (T, P).
            "tmin_k": 300.0,
            "tmax_k": 2000.0,
            "pmin_bar": 0.01,
            "pmax_bar": 100.0,
            "chebyshev": {
                "n_temperature": 2,
                "n_pressure": 2,
                "coefficients": [[1.0, 2.0], [3.0, 4.0]],
            },
        }
    ]
    with pytest.raises(ValueError, match="references undefined channel"):
        NetworkPDepUploadRequest(**payload)


def test_pdep_channel_kinetics_rejects_duplicate_chebyshev_within_payload() -> None:
    """Two *chebyshev* channel_kinetics entries for the same (source, sink) pair
    within one payload are rejected (would write two redundant chebyshev rows
    for one channel/solve/kind)."""
    payload = _full_payload(include_solve=True)
    entry = {
        "source_state_key": "entrance",
        "sink_state_key": "well_RO2",
        "model_kind": "chebyshev",
        "tmin_k": 300.0, "tmax_k": 2000.0, "pmin_bar": 0.01, "pmax_bar": 100.0,
        "chebyshev": {
            "n_temperature": 2,
            "n_pressure": 2,
            "coefficients": [[1.0, 2.0], [3.0, 4.0]],
        },
    }
    payload["solve"]["channel_kinetics"] = [entry, {**entry}]
    with pytest.raises(ValueError, match="unique"):
        NetworkPDepUploadRequest(**payload)


def _plog_entry_dict(source: str, sink: str) -> dict:
    """A minimal valid ``model_kind=plog`` channel_kinetics dict."""
    return {
        "source_state_key": source,
        "sink_state_key": sink,
        "model_kind": "plog",
        "plog": {
            "entries": [
                {
                    "pressure_bar": 1.0,
                    "a": 1.0e13,
                    "a_units": "cm3_mol_s",
                    "n": 0.0,
                    "ea_kj_mol": 40.0,
                },
                {
                    "pressure_bar": 10.0,
                    "a": 2.0e13,
                    "a_units": "cm3_mol_s",
                    "n": 0.1,
                    "ea_kj_mol": 42.0,
                },
            ]
        },
    }


def test_pdep_channel_kinetics_rejects_duplicate_plog_within_payload() -> None:
    """Two *plog* channel_kinetics entries for the same (source, sink) pair are
    rejected (double-plog on one channel is still user error)."""
    payload = _full_payload(include_solve=True)
    entry = _plog_entry_dict("entrance", "well_RO2")
    payload["solve"]["channel_kinetics"] = [entry, {**entry}]
    with pytest.raises(ValueError, match="unique"):
        NetworkPDepUploadRequest(**payload)


def test_pdep_channel_kinetics_accepts_chebyshev_and_plog_on_one_channel() -> None:
    """One channel may carry BOTH a chebyshev and a plog parameterization: the
    uniqueness key is (source, sink, model_kind), so cheb+plog coexist."""
    payload = _full_payload(include_solve=True)
    payload["solve"]["channel_kinetics"] = [
        {
            "source_state_key": "entrance",
            "sink_state_key": "well_RO2",
            "model_kind": "chebyshev",
            # A Chebyshev surface is fit in reduced variables; without bounds
            # it cannot be evaluated at any (T, P).
            "tmin_k": 300.0,
            "tmax_k": 2000.0,
            "pmin_bar": 0.01,
            "pmax_bar": 100.0,
            "chebyshev": {
                "n_temperature": 2,
                "n_pressure": 2,
                "coefficients": [[1.0, 2.0], [3.0, 4.0]],
            },
        },
        _plog_entry_dict("entrance", "well_RO2"),
    ]
    request = NetworkPDepUploadRequest(**payload)
    assert request.solve is not None
    assert len(request.solve.channel_kinetics) == 2
    assert {nk.model_kind.value for nk in request.solve.channel_kinetics} == {
        "chebyshev",
        "plog",
    }


def test_pdep_parallel_paths_require_stable_channel_identity() -> None:
    """Parallel source/sink paths stay distinct and their fits are unambiguous."""
    payload = _full_payload(include_solve=True)
    elimination = next(
        channel for channel in payload["channels"] if channel["key"] == "elimination_path"
    )
    alternate = deepcopy(elimination)
    alternate["key"] = "elimination_path_alt"
    payload["channels"].append(alternate)
    payload["solve"]["channel_barriers"].append({
        "channel_key": "elimination_path_alt", "micro_reaction_key": "rxn_ho2_elim",
        "transition_state_key": "ts_elim", "forward_barrier_kj_mol": 105.0,
        "reverse_barrier_kj_mol": 45.0,
        **_CONVENTIONS,
        "source_calculation_key": "ts_elim_sp",
    })
    payload["solve"]["channel_kinetics"] = [{
        "channel_key": "elimination_path_alt", "model_kind": "chebyshev",
        "tmin_k": 300.0, "tmax_k": 2000.0, "pmin_bar": 0.01, "pmax_bar": 100.0,
        "chebyshev": {"n_temperature": 1, "n_pressure": 1, "coefficients": [[1.0]]},
    }]
    request = NetworkPDepUploadRequest(**payload)
    assert request.solve.channel_kinetics[0].channel_key == "elimination_path_alt"


def test_pdep_rejects_incomplete_solve_scientific_inputs() -> None:
    payload = _full_payload(include_solve=True)
    payload["solve"]["state_energies"] = payload["solve"]["state_energies"][:1]
    with pytest.raises(ValueError, match="exactly one energy"):
        NetworkPDepUploadRequest(**payload)


def test_pdep_channel_kinetics_rejects_non_finite_coefficient() -> None:
    """A NaN Chebyshev coefficient is rejected at the schema layer (not a
    500 at JSONB insert time)."""
    payload = _full_payload(include_solve=True)
    payload["solve"]["channel_kinetics"] = [
        {
            "source_state_key": "entrance",
            "sink_state_key": "well_RO2",
            "model_kind": "chebyshev",
            "tmin_k": 300.0,
            "tmax_k": 2000.0,
            "pmin_bar": 0.01,
            "pmax_bar": 100.0,
            "chebyshev": {
                "n_temperature": 2,
                "n_pressure": 2,
                "coefficients": [[1.0, float("nan")], [3.0, 4.0]],
            },
        }
    ]
    with pytest.raises(ValueError, match="finite"):
        NetworkPDepUploadRequest(**payload)


def test_pdep_channel_kinetics_rejects_plog_without_sub_block() -> None:
    """``model_kind=plog`` with no ``plog`` sub-block is a clean 422."""
    payload = _full_payload(include_solve=True)
    payload["solve"]["channel_kinetics"] = [
        {
            "source_state_key": "entrance",
            "sink_state_key": "well_RO2",
            "model_kind": "plog",
        }
    ]
    with pytest.raises(ValueError, match="plog entries are required"):
        NetworkPDepUploadRequest(**payload)


def test_pdep_channel_kinetics_rejects_tabulated_model_kind() -> None:
    """Tabulated network kinetics upload is still not supported."""
    payload = _full_payload(include_solve=True)
    payload["solve"]["channel_kinetics"] = [
        {
            "source_state_key": "entrance",
            "sink_state_key": "well_RO2",
            "model_kind": "tabulated",
        }
    ]
    with pytest.raises(ValueError, match="not yet supported"):
        NetworkPDepUploadRequest(**payload)


def test_pdep_channel_kinetics_rejects_plog_with_chebyshev_block() -> None:
    """``model_kind=plog`` may not also carry a ``chebyshev`` sub-block."""
    payload = _full_payload(include_solve=True)
    payload["solve"]["channel_kinetics"] = [
        {
            "source_state_key": "entrance",
            "sink_state_key": "well_RO2",
            "model_kind": "plog",
            "plog": {
                "entries": [
                    {"pressure_bar": 1.0, "a": 1.0e13, "n": 0.0, "ea_kj_mol": 50.0},
                ]
            },
            "chebyshev": {
                "n_temperature": 2,
                "n_pressure": 2,
                "coefficients": [[1.0, 2.0], [3.0, 4.0]],
            },
        }
    ]
    with pytest.raises(ValueError, match="chebyshev must be omitted"):
        NetworkPDepUploadRequest(**payload)


def test_pdep_channel_kinetics_rejects_chebyshev_with_plog_block() -> None:
    """``model_kind=chebyshev`` may not also carry a ``plog`` sub-block
    (symmetric converse of the plog+chebyshev rejection)."""
    payload = _full_payload(include_solve=True)
    payload["solve"]["channel_kinetics"] = [
        {
            "source_state_key": "entrance",
            "sink_state_key": "well_RO2",
            "model_kind": "chebyshev",
            # A Chebyshev surface is fit in reduced variables; without bounds
            # it cannot be evaluated at any (T, P).
            "tmin_k": 300.0,
            "tmax_k": 2000.0,
            "pmin_bar": 0.01,
            "pmax_bar": 100.0,
            "chebyshev": {
                "n_temperature": 2,
                "n_pressure": 2,
                "coefficients": [[1.0, 2.0], [3.0, 4.0]],
            },
            "plog": {
                "entries": [
                    {"pressure_bar": 1.0, "a": 1.0e13, "n": 0.0, "ea_kj_mol": 50.0},
                ]
            },
        }
    ]
    with pytest.raises(ValueError, match="plog must be omitted"):
        NetworkPDepUploadRequest(**payload)


def test_pdep_channel_kinetics_rejects_plog_with_stores_log10_k() -> None:
    """``stores_log10_k`` is Chebyshev-only; setting it on a PLOG payload is a
    clean 422 rather than a semantically meaningless flag on the parent row."""
    payload = _full_payload(include_solve=True)
    payload["solve"]["channel_kinetics"] = [
        {
            "source_state_key": "entrance",
            "sink_state_key": "well_RO2",
            "model_kind": "plog",
            "plog": {
                "entries": [
                    {"pressure_bar": 1.0, "a": 1.0e13, "n": 0.0, "ea_kj_mol": 50.0},
                ]
            },
            "stores_log10_k": True,
        }
    ]
    with pytest.raises(ValueError, match="stores_log10_k must be omitted"):
        NetworkPDepUploadRequest(**payload)


def test_pdep_channel_kinetics_rejects_duplicate_plog_pressure_index() -> None:
    """Two PLOG entries sharing ``(pressure_bar, entry_index)`` collide on the
    child composite primary key; reject them at the schema layer as a 422."""
    payload = _full_payload(include_solve=True)
    payload["solve"]["channel_kinetics"] = [
        {
            "source_state_key": "entrance",
            "sink_state_key": "well_RO2",
            "model_kind": "plog",
            "plog": {
                "entries": [
                    {"pressure_bar": 1.0, "a": 1.0e13, "n": 0.0, "ea_kj_mol": 50.0},
                    {"pressure_bar": 1.0, "a": 2.0e13, "n": 0.1, "ea_kj_mol": 60.0},
                ]
            },
        }
    ]
    with pytest.raises(ValueError, match="unique by \\(pressure_bar, entry_index\\)"):
        NetworkPDepUploadRequest(**payload)


def test_pdep_workflow_persists_and_reads_back_plog_channel_kinetics(
    db_engine,
) -> None:
    """A ``model_kind=plog`` ``channel_kinetics`` entry produces a
    ``NetworkKinetics`` (model_kind=plog) + one ``NetworkKineticsPlog`` row per
    pressure-indexed Arrhenius entry, and round-trips through the existing
    network-kinetics read path.
    """
    from app.db.models.network_pdep import (
        NetworkKinetics,
        NetworkKineticsPlog,
    )
    from app.services.scientific_read.network_kinetics import (
        get_network_kinetics,
    )

    # Real Arkane-shape PLOG: 5 pressures, each a modified-Arrhenius term.
    pressures = [0.01, 0.1, 1.0, 10.0, 100.0]
    plog_entries = [
        {
            "pressure_bar": p,
            "a": 1.0e12 * (i + 1),
            "a_units": "cm3_mol_s",
            "n": 0.5 + 0.1 * i,
            "ea_kj_mol": 40.0 + 5.0 * i,
        }
        for i, p in enumerate(pressures)
    ]

    payload = _full_payload(include_solve=True)
    payload["solve"]["channel_kinetics"] = [
        {
            "source_state_key": "entrance",
            "sink_state_key": "well_RO2",
            "model_kind": "plog",
            "plog": {"entries": plog_entries},
            "tmin_k": 300.0,
            "tmax_k": 2000.0,
            "pmin_bar": 0.01,
            "pmax_bar": 100.0,
            "rate_units": "cm3_mol_s",
            "pressure_units": "bar",
            "temperature_units": "kelvin",
            "note": "PLOG fit from ME solve",
        }
    ]

    with _rolled_back_session(db_engine) as session:
        request = NetworkPDepUploadRequest(**payload)
        network = persist_network_pdep_upload(session, request)
        session.flush()

        solve = session.scalars(
            select(NetworkSolve).where(NetworkSolve.network_id == network.id)
        ).one()
        assoc_channel = session.scalars(
            select(NetworkChannel).where(
                NetworkChannel.network_id == network.id,
                NetworkChannel.kind == "association",
            )
        ).one()

        # -- NetworkKinetics parent row (scoped to this solve) --
        nk = session.scalars(
            select(NetworkKinetics).where(NetworkKinetics.solve_id == solve.id)
        ).one()
        assert nk.channel_id == assoc_channel.id
        assert nk.solve_id == solve.id
        assert nk.model_kind.value == "plog"
        # PLOG stores a real Arrhenius A, not a log10 fit.
        assert nk.stores_log10_k is None
        assert nk.rate_units.value == "cm3_mol_s"

        # -- One NetworkKineticsPlog row per entry, values intact --
        rows = session.scalars(
            select(NetworkKineticsPlog)
            .where(NetworkKineticsPlog.network_kinetics_id == nk.id)
            .order_by(NetworkKineticsPlog.pressure_bar.asc())
        ).all()
        assert len(rows) == 5
        for row, expected in zip(rows, plog_entries, strict=True):
            assert row.pressure_bar == expected["pressure_bar"]
            assert row.a == expected["a"]
            assert row.a_units.value == "cm3_mol_s"
            assert row.n == expected["n"]
            assert row.ea_kj_mol == expected["ea_kj_mol"]
            assert row.entry_index == 1

        # -- Read back through the existing read service (round-trip) --
        resp = get_network_kinetics(
            session,
            network_kinetics_handle=str(nk.id),
            include=["plog"],
        )
        core = resp.record.network_kinetics
        assert core.model_kind.value == "plog"
        assert core.plog_entry_count == 5
        assert core.rate_units.value == "cm3_mol_s"

        plog_block = resp.record.plog
        assert plog_block is not None
        assert len(plog_block) == 5
        read_back = {e.pressure_bar: e for e in plog_block}
        for expected in plog_entries:
            e = read_back[expected["pressure_bar"]]
            assert e.a == expected["a"]
            assert e.a_units.value == "cm3_mol_s"
            assert e.n == expected["n"]
            assert e.ea_kj_mol == expected["ea_kj_mol"]
            assert e.entry_index == 1


def test_pdep_workflow_persists_mixed_chebyshev_and_plog_channel_kinetics(
    db_engine,
) -> None:
    """One payload may carry a Chebyshev fit on one channel and a PLOG fit on
    another; both persist to their respective child tables."""
    from app.db.models.network_pdep import (
        NetworkKinetics,
        NetworkKineticsChebyshev,
        NetworkKineticsPlog,
    )

    n_t, n_p = 3, 2
    grid = [[float(t * 10 + p) for p in range(n_p)] for t in range(n_t)]

    payload = _full_payload(include_solve=True)
    payload["solve"]["channel_kinetics"] = [
        {
            # Association (entrance -> well_RO2): Chebyshev.
            "source_state_key": "entrance",
            "sink_state_key": "well_RO2",
            "model_kind": "chebyshev",
            "tmin_k": 300.0,
            "tmax_k": 2000.0,
            "pmin_bar": 0.01,
            "pmax_bar": 100.0,
            "chebyshev": {
                "n_temperature": n_t,
                "n_pressure": n_p,
                "coefficients": grid,
            },
            "stores_log10_k": True,
        },
        {
            # Dissociation (well_RO2 -> entrance): PLOG (unimolecular).
            "source_state_key": "well_RO2",
            "sink_state_key": "entrance",
            "model_kind": "plog",
            "plog": {
                "entries": [
                    {
                        "pressure_bar": 1.0,
                        "a": 1.0e13,
                        "a_units": "per_s",
                        "n": 0.0,
                        "ea_kj_mol": 120.0,
                    },
                    {
                        "pressure_bar": 10.0,
                        "a": 2.0e13,
                        "a_units": "per_s",
                        "n": 0.1,
                        "ea_kj_mol": 125.0,
                    },
                ]
            },
        },
    ]

    with _rolled_back_session(db_engine) as session:
        request = NetworkPDepUploadRequest(**payload)
        network = persist_network_pdep_upload(session, request)
        session.flush()

        solve = session.scalars(
            select(NetworkSolve).where(NetworkSolve.network_id == network.id)
        ).one()
        nk_rows = session.scalars(
            select(NetworkKinetics).where(NetworkKinetics.solve_id == solve.id)
        ).all()
        assert len(nk_rows) == 2
        by_kind = {nk.model_kind.value: nk for nk in nk_rows}
        assert set(by_kind) == {"chebyshev", "plog"}

        cheb = session.scalars(
            select(NetworkKineticsChebyshev).where(
                NetworkKineticsChebyshev.network_kinetics_id
                == by_kind["chebyshev"].id
            )
        ).one()
        assert cheb.coefficients == {"coeffs": grid}

        plog_rows = session.scalars(
            select(NetworkKineticsPlog).where(
                NetworkKineticsPlog.network_kinetics_id == by_kind["plog"].id
            )
        ).all()
        assert len(plog_rows) == 2
        assert {r.a_units.value for r in plog_rows} == {"per_s"}
        # The Chebyshev record has no PLOG rows and vice versa.
        cheb_plog = session.scalars(
            select(NetworkKineticsPlog).where(
                NetworkKineticsPlog.network_kinetics_id
                == by_kind["chebyshev"].id
            )
        ).all()
        assert cheb_plog == []


def test_pdep_species_statmech_persists_via_shared_seam(db_engine) -> None:
    """A network species carrying a statmech block persists a Statmech row
    (external_symmetry / optical_isomers) with a resolved source-calc link,
    reusing the computed-species bundle's shared statmech seam."""
    from app.db.models.statmech import Statmech, StatmechSourceCalculation

    payload = _full_payload(include_solve=False)
    # Attach statmech to the ethyl species, referencing its own freq calc.
    ethyl = next(sp for sp in payload["species"] if sp["key"] == "ethyl")
    ethyl["statmech"] = {
        "statmech_treatment": "rrho",
        "external_symmetry": 2,
        "optical_isomers": 2,
        "point_group": "C2",
        "source_calculations": [
            {"calculation_key": "ethyl_freq", "role": "freq"},
        ],
    }

    with _rolled_back_session(db_engine) as session:
        baseline_statmech_id = session.scalar(select(func.max(Statmech.id))) or 0
        request = NetworkPDepUploadRequest(**payload)
        persist_network_pdep_upload(session, request, created_by=None)

        statmechs = session.scalars(
            select(Statmech).where(
                Statmech.id > baseline_statmech_id,
                Statmech.species_entry_id.is_not(None),
            )
        ).all()
        assert len(statmechs) == 1
        sm = statmechs[0]
        assert sm.external_symmetry == 2
        assert sm.optical_isomers == 2
        assert sm.point_group == "C2"

        # The statmech is owned by a species entry, and its source-calc link
        # resolved to a calculation owned by that same species entry.
        source_links = session.scalars(
            select(StatmechSourceCalculation).where(
                StatmechSourceCalculation.statmech_id == sm.id
            )
        ).all()
        assert len(source_links) == 1
        link = source_links[0]
        assert link.role.value == "freq"

        linked_calc = session.get(Calculation, link.calculation_id)
        assert linked_calc is not None
        assert linked_calc.species_entry_id == sm.species_entry_id


def test_pdep_species_statmech_rejects_cross_species_source_calculation() -> None:
    """A species statmech may only source from that species's OWN calcs.

    Referencing a transition-state calc key (defined globally but owned by a
    TS, not the species) must be rejected at request construction, not left
    to blow up as a KeyError during persistence.
    """
    payload = _full_payload(include_solve=False)
    ethyl = next(sp for sp in payload["species"] if sp["key"] == "ethyl")
    ethyl["statmech"] = {
        "statmech_treatment": "rrho",
        "external_symmetry": 2,
        "optical_isomers": 2,
        # ts_elim_sp is a defined global calc key, but it belongs to a TS.
        "source_calculations": [
            {"calculation_key": "ethyl_freq", "role": "freq"},
            {"calculation_key": "ts_elim_sp", "role": "sp"},
        ],
    }
    with pytest.raises(ValueError, match="not one of that species's own"):
        NetworkPDepUploadRequest(**payload)


def _payload_with_ethyl_scan() -> dict:
    """``_full_payload`` with a scan-type calc added to the ethyl species so
    torsion source_scan_calculation_key references can resolve."""
    payload = _full_payload(include_solve=False)
    ethyl = next(sp for sp in payload["species"] if sp["key"] == "ethyl")
    ethyl["calculations"].append(
        {
            "key": "ethyl_scan", "type": "scan", "geometry_key": "ethyl_geom",
            "software_release": _SOFTWARE, "level_of_theory": _LOT_DFT,
        }
    )
    return payload


def test_pdep_species_statmech_torsion_scan_persists(db_engine) -> None:
    """A per-species torsion with a scan-type source persists and links the
    scan calculation owned by the same species entry."""
    from app.db.models.statmech import Statmech, StatmechTorsion

    payload = _payload_with_ethyl_scan()
    ethyl = next(sp for sp in payload["species"] if sp["key"] == "ethyl")
    ethyl["statmech"] = {
        "statmech_treatment": "rrho_1d",
        "source_calculations": [{"calculation_key": "ethyl_freq", "role": "freq"}],
        "external_symmetry": 1,
        "optical_isomers": 1,
        "torsions": [
            {
                "torsion_index": 1,
                "symmetry_number": 3,
                "treatment_kind": "hindered_rotor",
                "dimension": 1,
                "top_description": "CH3 about C-C",
                "source_scan_calculation_key": "ethyl_scan",
            }
        ],
    }

    with _rolled_back_session(db_engine) as session:
        baseline_statmech_id = session.scalar(select(func.max(Statmech.id))) or 0
        request = NetworkPDepUploadRequest(**payload)
        persist_network_pdep_upload(session, request, created_by=None)

        sm = session.scalars(
            select(Statmech).where(
                Statmech.id > baseline_statmech_id,
                Statmech.species_entry_id.is_not(None),
            )
        ).one()
        torsions = session.scalars(
            select(StatmechTorsion).where(StatmechTorsion.statmech_id == sm.id)
        ).all()
        assert len(torsions) == 1
        torsion = torsions[0]
        assert torsion.source_scan_calculation_id is not None
        scan_calc = session.get(Calculation, torsion.source_scan_calculation_id)
        assert scan_calc is not None
        assert scan_calc.type == CalculationType.scan
        # The scan calc is owned by the same species entry as the statmech.
        assert scan_calc.species_entry_id == sm.species_entry_id


def test_pdep_species_statmech_torsion_rejects_undefined_scan_key() -> None:
    """An undefined torsion scan key is rejected at construction, not left to
    a persist-time KeyError."""
    payload = _payload_with_ethyl_scan()
    ethyl = next(sp for sp in payload["species"] if sp["key"] == "ethyl")
    ethyl["statmech"] = {
        "statmech_treatment": "rrho_1d",
        "source_calculations": [{"calculation_key": "ethyl_freq", "role": "freq"}],
        "torsions": [
            {"torsion_index": 1, "source_scan_calculation_key": "no_such_scan"}
        ],
    }
    with pytest.raises(ValueError, match="not one of that species's own"):
        NetworkPDepUploadRequest(**payload)


def test_pdep_species_statmech_torsion_rejects_non_scan_type_key() -> None:
    """A torsion scan key that resolves to a non-scan calc is rejected at
    construction (otherwise the seam would silently link a wrong calc)."""
    payload = _payload_with_ethyl_scan()
    ethyl = next(sp for sp in payload["species"] if sp["key"] == "ethyl")
    ethyl["statmech"] = {
        "statmech_treatment": "rrho_1d",
        "source_calculations": [{"calculation_key": "ethyl_freq", "role": "freq"}],
        # ethyl_freq is one of ethyl's own calcs, but it is a freq, not a scan.
        "torsions": [
            {"torsion_index": 1, "source_scan_calculation_key": "ethyl_freq"}
        ],
    }
    with pytest.raises(ValueError, match="must reference a scan-type"):
        NetworkPDepUploadRequest(**payload)


def test_pdep_species_statmech_torsion_rejects_cross_species_scan_key() -> None:
    """A torsion scan key owned by ANOTHER species is rejected at construction
    (species-local scoping), rather than being silently persisted as a
    cross-species torsion->scan link."""
    payload = _payload_with_ethyl_scan()
    # Add a scan calc to O2 as well, so the key is scan-type but foreign.
    o2 = next(sp for sp in payload["species"] if sp["key"] == "O2")
    o2["calculations"].append(
        {
            "key": "O2_scan", "type": "scan", "geometry_key": "O2_geom",
            "software_release": _SOFTWARE, "level_of_theory": _LOT_DFT,
        }
    )
    o2["calculations"].append(
        {
            "key": "O2_freq", "type": "freq", "geometry_key": "O2_geom",
            "software_release": _SOFTWARE, "level_of_theory": _LOT_DFT,
            "freq_n_imag": 0,
        }
    )
    ethyl = next(sp for sp in payload["species"] if sp["key"] == "ethyl")
    ethyl["statmech"] = {
        "statmech_treatment": "rrho_1d",
        "source_calculations": [{"calculation_key": "ethyl_freq", "role": "freq"}],
        "torsions": [
            {"torsion_index": 1, "source_scan_calculation_key": "O2_scan"}
        ],
    }
    with pytest.raises(ValueError, match="not one of that species's own"):
        NetworkPDepUploadRequest(**payload)


def test_seam_torsion_ownership_check_rejects_cross_species_scan(db_engine) -> None:
    """Direct unit test of the shared seam's torsion ownership guard.

    Bypasses the network request validator to prove the seam itself rejects a
    torsion whose scan calc is owned by a different species entry with a clean
    ValueError (not a silent cross-species link). The single-species bundle
    path can never hit this branch, so it is a strict no-op there.
    """
    from app.db.models.statmech import StatmechTorsion
    from app.schemas.workflows.network_pdep_upload import StatmechInBundle
    from app.workflows.computed_species import _persist_statmech_block

    payload = _full_payload(include_solve=False)
    # Give O2 a scan calc owned by the O2 species entry (the only scan calc).
    o2 = next(sp for sp in payload["species"] if sp["key"] == "O2")
    o2["calculations"].append(
        {
            "key": "O2_scan", "type": "scan", "geometry_key": "O2_geom",
            "software_release": _SOFTWARE, "level_of_theory": _LOT_DFT,
        }
    )
    o2["calculations"].append(
        {
            "key": "O2_freq", "type": "freq", "geometry_key": "O2_geom",
            "software_release": _SOFTWARE, "level_of_theory": _LOT_DFT,
            "freq_n_imag": 0,
        }
    )

    with _rolled_back_session(db_engine) as session:
        baseline_calc_id = session.scalar(select(func.max(Calculation.id))) or 0
        baseline_torsion_id = (
            session.scalar(select(func.max(StatmechTorsion.id))) or 0
        )
        request = NetworkPDepUploadRequest(**payload)
        network = persist_network_pdep_upload(session, request)
        assert network.id is not None

        # Resolve the O2 scan calc and a species entry that is NOT its owner.
        o2_scan = session.scalars(
            select(Calculation).where(
                Calculation.id > baseline_calc_id,
                Calculation.type == CalculationType.scan,
            )
        ).one()
        foreign_calc = session.scalars(
            select(Calculation).where(
                Calculation.id > baseline_calc_id,
                Calculation.species_entry_id.isnot(None),
                Calculation.species_entry_id != o2_scan.species_entry_id,
            )
        ).first()
        assert foreign_calc is not None
        foreign_entry_id = foreign_calc.species_entry_id
        assert foreign_entry_id != o2_scan.species_entry_id

        foreign_freq = session.scalars(
            select(Calculation).where(
                Calculation.id > baseline_calc_id,
                Calculation.type == CalculationType.freq,
                Calculation.species_entry_id == foreign_entry_id,
            )
        ).one()
        statmech = StatmechInBundle(
            statmech_treatment="rrho_1d",
            source_calculations=[{"calculation_key": "foreign_freq", "role": "freq"}],
            torsions=[
                {"torsion_index": 1, "source_scan_calculation_key": "O2_scan"}
            ],
        )
        # Feed the seam a calc-key map pointing at O2's scan calc while
        # claiming a different species entry owns the statmech.
        with pytest.raises(ValueError, match="different subject"):
            _persist_statmech_block(
                session,
                statmech,
                species_entry_id=foreign_entry_id,
                calc_keys_to_id={"O2_scan": o2_scan, "foreign_freq": foreign_freq},
                created_by=None,
            )
        # No torsion row was persisted for a foreign scan link.
        leaked = session.scalars(
            select(StatmechTorsion).where(
                StatmechTorsion.id > baseline_torsion_id
            )
        ).all()
        assert leaked == []


@pytest.mark.parametrize(
    ("mutate", "label"),
    [
        (lambda p: p["channels"][0].pop("microreaction_paths"), "missing channel path"),
        (
            lambda p: p["channels"][0]["microreaction_paths"][0].update(
                {"micro_reaction_key": "missing_reaction"}
            ),
            "undefined channel reaction path",
        ),
        (lambda p: p["solve"].update({"channel_barriers": []}), "missing solve barriers"),
        (
            lambda p: p["solve"]["channel_barriers"][0].update(
                {"transition_state_key": "missing_ts"}
            ),
            "mismatched barrier TS path",
        ),
        (
            lambda p: p["solve"]["energy_transfer"][0].update({"state_key": "missing_state"}),
            "undefined energy-transfer scope",
        ),
        (
            lambda p: p["solve"]["state_energies"][0].update({"state_key": "missing_state"}),
            "undefined state-energy state",
        ),
        (
            lambda p: p["transition_states"][0]["validation_evidence"][0].update(
                {"reactant_participant_mapping": {"reactant:3": [1]}}
            ),
            "out-of-bounds IRC participant mapping",
        ),
        (
            # A map covering 2 of the TS's 9 atoms proves nothing about the
            # other 7 and can never be passing evidence.
            lambda p: p["transition_states"][0]["validation_evidence"][0].update(
                {"reactant_participant_mapping": {"reactant:1": [1, 2]}}
            ),
            "partial IRC participant mapping",
        ),
        (
            # Product side must cover the full TS atom set too.
            lambda p: p["transition_states"][0]["validation_evidence"][0].update(
                {"product_participant_mapping": {"product:1": [1, 2], "product:2": [3]}}
            ),
            "partial IRC product mapping",
        ),
        (
            lambda p: p["transition_states"][0]["validation_evidence"][0].pop(
                "product_participant_mapping"
            ),
            "one-sided IRC participant mapping",
        ),
        (
            lambda p: p["solve"]["state_energies"][0].update(
                {"energy_zero_convention": "banana"}
            ),
            "unknown energy-zero convention token",
        ),
        (
            lambda p: p["solve"]["channel_barriers"][0].update(
                {"correction_convention": "electronic-plus-zpe"}
            ),
            "free-text correction convention",
        ),
        (
            # A five-well network with one well's Delta-E-down is not coverage.
            lambda p: p["solve"].update({"energy_transfer": []}),
            "missing energy-transfer coverage",
        ),
        (
            # A barrierless path must not also declare a barrier.
            lambda p: p["solve"]["channel_barriers"].append(
                {
                    "channel_key": "association_path",
                    "micro_reaction_key": "rxn_assoc",
                    "transition_state_key": "ts_elim",
                    "forward_barrier_kj_mol": 15.0,
                    "reverse_barrier_kj_mol": 135.0,
                    **_CONVENTIONS,
                }
            ),
            "barrier on a barrierless path",
        ),
    ],
    ids=lambda case: case if isinstance(case, str) else None,
)
def test_pdep_strict_v2_schema_rejects_incomplete_path_and_evidence_matrix(
    db_engine, mutate, label
) -> None:
    """Strict v2 rejects malformed path, solve-scope, and TS evidence input before writes."""
    payload = _full_payload()
    mutate(payload)
    with Session(db_engine) as session, session.begin():
        before = session.scalar(select(func.count()).select_from(Network))
        with pytest.raises(ValueError):
            NetworkPDepUploadRequest(**payload)
        assert session.scalar(select(func.count()).select_from(Network)) == before, label


# ---------------------------------------------------------------------------
# Barrierless paths, optional IRC evidence, and machine-token conventions
# ---------------------------------------------------------------------------


def test_barrierless_channel_round_trips_without_a_transition_state(db_engine) -> None:
    """A barrierless association persists with a NULL TS and no barrier row.

    Radical-radical association has no saddle point. Before this, the only
    way to deposit such a channel was to invent a transition state and a
    barrier height for it.
    """
    with _rolled_back_session(db_engine) as session:
        request = NetworkPDepUploadRequest(**_full_payload())
        network = persist_network_pdep_upload(session, request)
        session.flush()

        association = session.scalars(
            select(NetworkChannel).where(
                NetworkChannel.network_id == network.id,
                NetworkChannel.channel_key == "association_path",
            )
        ).one()
        links = session.scalars(
            select(NetworkChannelMicroReaction).where(
                NetworkChannelMicroReaction.channel_id == association.id
            )
        ).all()
        assert len(links) == 1
        assert links[0].transition_state_entry_id is None

        solve = session.scalars(
            select(NetworkSolve).where(NetworkSolve.network_id == network.id)
        ).one()
        barriers = session.scalars(
            select(NetworkSolveChannelBarrier).where(
                NetworkSolveChannelBarrier.solve_id == solve.id,
                NetworkSolveChannelBarrier.channel_id == association.id,
            )
        ).all()
        assert barriers == []

        # And the read surface says so explicitly rather than by omission.
        read = get_network(
            session, network_handle=network.public_ref, include=["channels"]
        )
        association_read = next(
            channel
            for channel in read.record.channels or []
            if channel.channel_key == "association_path"
        )
        assert association_read.microreactions[0].path_kind == "barrierless"
        assert association_read.microreactions[0].transition_state_entry_ref is None


def test_submerged_barrier_is_accepted(db_engine) -> None:
    """A barrier below the declared zero is legitimate, not a validation error."""
    payload = _full_payload()
    payload["solve"]["channel_barriers"][0]["forward_barrier_kj_mol"] = -8.0
    with _rolled_back_session(db_engine) as session:
        request = NetworkPDepUploadRequest(**payload)
        network = persist_network_pdep_upload(session, request)
        session.flush()
        solve = session.scalars(
            select(NetworkSolve).where(NetworkSolve.network_id == network.id)
        ).one()
        barrier = session.scalars(
            select(NetworkSolveChannelBarrier).where(
                NetworkSolveChannelBarrier.solve_id == solve.id
            )
        ).one()
        assert barrier.forward_barrier_kj_mol == -8.0


def test_non_finite_barrier_is_rejected() -> None:
    payload = _full_payload()
    payload["solve"]["channel_barriers"][0]["forward_barrier_kj_mol"] = float("nan")
    with pytest.raises(ValueError, match="finite"):
        NetworkPDepUploadRequest(**payload)


def test_pdep_ts_evidence_refuses_ethene_made_of_oxygens(db_engine) -> None:
    """The partition that used to pass, refused end to end on this path.

    ``product:1`` is declared ``C=C`` and handed atoms 1-6 of a saddle point
    listed ``1 C, 2 C, 3 O, 4 O, 5-9 H`` — two carbons, two oxygens and two
    hydrogens. Every one of the nine atoms is still claimed exactly once, so the
    shape rule has nothing to object to; only the element rule sees it. This is
    the exact mapping a fixture "correction" once wrote here, under a comment
    that correctly said "C2H4 (six atoms)".
    """
    payload = _full_payload()
    payload["transition_states"][0]["validation_evidence"][0].update(
        {
            "product_participant_mapping": {
                "product:1": [1, 2, 3, 4, 5, 6],
                "product:2": [7, 8, 9],
            }
        }
    )

    with _rolled_back_session(db_engine) as session:
        request = NetworkPDepUploadRequest(**payload)
        with pytest.raises(ValueError) as excinfo:
            persist_network_pdep_upload(session, request, warnings=[])

    message = str(excinfo.value)
    assert "transition_state_irc_mapping_element_mismatch" in message
    # Named per formula, so the depositor can correct it per atom.
    assert "C2H2O2" in message and "C2H4" in message


def test_pdep_ts_evidence_accepts_the_correct_partition(db_engine) -> None:
    """The other half: the same saddle point, partitioned per atom, deposits.

    Guards against closing the gap by refusing the whole family — the check has
    to distinguish ethene from C2O2H2, not merely notice that a mapping exists.
    """
    payload = _full_payload()
    assert payload["transition_states"][0]["validation_evidence"][0][
        "product_participant_mapping"
    ] == _ELIM_PRODUCT_MAP

    with _rolled_back_session(db_engine) as session:
        warnings: list[UploadWarning] = []
        network = persist_network_pdep_upload(
            session, NetworkPDepUploadRequest(**payload), warnings=warnings
        )
        session.flush()
        assert network.id is not None
        assert "transition_state_missing_irc_evidence" not in [
            w.code for w in warnings
        ]


def test_transition_state_without_irc_evidence_succeeds_with_a_warning(
    db_engine,
) -> None:
    """IRC evidence is recommended, not required — but its absence is stated."""
    payload = _full_payload()
    payload["transition_states"][0]["validation_evidence"] = []

    with _rolled_back_session(db_engine) as session:
        warnings: list[UploadWarning] = []
        request = NetworkPDepUploadRequest(**payload)
        network = persist_network_pdep_upload(session, request, warnings=warnings)
        session.flush()
        assert network.id is not None
        # Two absences, both about the same saddle point and both stated:
        # nothing evidences that it connects its declared endpoints, and
        # nothing says which atom of the reactants is which atom of it.
        assert [w.code for w in warnings] == [
            "transition_state_missing_irc_evidence",
            "reaction_atom_map_absent",
        ]
        assert all("ts_elim" in w.field for w in warnings)


def test_every_pdep_saddle_point_reports_its_missing_atom_map(
    db_engine,
) -> None:
    """A network's micro reactions are reactions, and their gaps are visible.

    ADR 0011 accepts an unmapped reaction — the rate constant is still the rate
    constant — on condition that the absence is *stated*, loudly enough that a
    depositor who has the mapping notices they are being asked for it. Before
    this warning existed, every micro reaction in a pressure-dependent network
    deposited unmapped and silent, which is precisely the invisible absence the
    decision was written to remove.
    """
    payload = _full_payload()

    with _rolled_back_session(db_engine) as session:
        warnings: list[UploadWarning] = []
        request = NetworkPDepUploadRequest(**payload)
        persist_network_pdep_upload(session, request, warnings=warnings)
        session.flush()

    absent = [w for w in warnings if w.code == "reaction_atom_map_absent"]
    # One per saddle point. The barrierless association path declares no
    # transition state, so it is correctly not warned about: both legs of a map
    # run toward a saddle point there is none of.
    assert len(absent) == len(request.transition_states)
    # The pointer names the saddle point, not ``.atom_map``. The prose below
    # is careful never to promise a field this bundle lacks, and the
    # machine-readable pointer has to keep the same promise -- a client that
    # highlights ``field`` in the submitted payload would otherwise be sent to
    # a path that does not resolve.
    assert {w.field for w in absent} == {
        f"transition_states[{ts.key}]" for ts in request.transition_states
    }
    assert not any(w.field.endswith(".atom_map") for w in absent)
    # And it does not send a depositor looking for a field this bundle has not
    # got: a warning that cannot be acted on is the kind nobody reads.
    assert "cannot yet carry a map" in absent[0].message
    assert "computed-reaction upload" in absent[0].message
    assert "atom_map" not in set(NetworkPDepUploadRequest.model_fields)


def test_multiple_transition_states_per_micro_reaction_are_accepted() -> None:
    """The schema no longer forbids what the database PK exists to allow."""
    request = NetworkPDepUploadRequest(**_parallel_path_payload())
    per_reaction = [
        ts.micro_reaction_key for ts in request.transition_states
    ]
    assert per_reaction.count("rxn_ho2_elim") == 2
    # And exactly one reaction identity carries them.
    assert len({r.key for r in request.micro_reactions}) == len(request.micro_reactions)


def test_energy_transfer_must_cover_every_well_collider_pair() -> None:
    """A *per-well* claim must be made for every well it claims to describe.

    Dropping one well's entry while leaving the other scoped ``per_well`` says
    the second well's ⟨ΔE⟩down was resolved and the first one's was simply
    lost. That is still refused. What is no longer refused is declaring one
    model for the whole network — see the ``network_wide`` tests below.
    """
    payload = _parallel_path_payload()
    payload["solve"]["energy_transfer"] = payload["solve"]["energy_transfer"][:1]
    with pytest.raises(ValueError, match="energy_transfer must cover"):
        NetworkPDepUploadRequest(**payload)


def test_upload_schema_exposes_no_fk_ids_or_hashes() -> None:
    """Upload payloads carry local keys and scientific content only.

    Ported from the standalone wire-package copy of this schema when that copy
    was deleted for drifting behind the server contract. The invariant is a
    project rule, not a PDep detail: a depositor names things with their own
    local keys and the workflow resolves them, so a database id or a derived
    hash appearing in an upload schema means someone has to know our primary
    keys to contribute.
    """

    def _nested_models(annotation) -> list:
        found = []
        stack = [annotation]
        while stack:
            current = stack.pop()
            if isinstance(current, type) and hasattr(current, "model_fields"):
                found.append(current)
                continue
            stack.extend(getattr(current, "__args__", ()) or ())
        return found

    def _walk(model_cls, seen: set) -> list[str]:
        if model_cls in seen:
            return []
        seen.add(model_cls)
        offenders: list[str] = []
        for name, field in model_cls.model_fields.items():
            if name.endswith("_hash") or name in {"id", "public_ref"}:
                offenders.append(f"{model_cls.__name__}.{name}")
            elif name.endswith("_id") and not name.endswith("_uuid"):
                offenders.append(f"{model_cls.__name__}.{name}")
            for sub in _nested_models(field.annotation):
                offenders.extend(_walk(sub, seen))
        return offenders

    offenders = _walk(NetworkPDepUploadRequest, set())
    # ``CalculationIn.literature_id`` is a pre-existing FK leak inherited from
    # the shared calculation fragment; it is not introduced by this schema.
    assert [o for o in offenders if o != "CalculationIn.literature_id"] == []


def test_chebyshev_grid_dimensions_must_match_declared_orders() -> None:
    """A coefficient grid that contradicts its own declared shape is unreadable.

    Also ported from the deleted wire-package copy. ``n_temperature`` and
    ``n_pressure`` are how a reader knows how to index the flat coefficient
    grid, so a grid whose shape disagrees with them cannot be evaluated at any
    (T, P) -- it is not merely suspect.
    """
    payload = _full_payload(include_solve=True)
    payload["solve"]["channel_kinetics"] = [
        {
            "source_state_key": "entrance",
            "sink_state_key": "well_RO2",
            "model_kind": "chebyshev",
            "tmin_k": 300.0, "tmax_k": 2000.0, "pmin_bar": 0.01, "pmax_bar": 100.0,
            "chebyshev": {
                # Declares a 2x3 grid but supplies 2x2 coefficients.
                "n_temperature": 2,
                "n_pressure": 3,
                "coefficients": [[1.0, 2.0], [3.0, 4.0]],
            },
        }
    ]
    with pytest.raises(ValueError, match="n_pressure=3 columns"):
        NetworkPDepUploadRequest(**payload)


def test_per_well_energy_transfer_entry_requires_both_scoping_keys() -> None:
    """``per_well`` is a claim about a pair, so it must name the pair.

    Definitional under ADR 0008: an entry that declares itself well-resolved
    and then names no well is internally contradictory, and the error points at
    the honest alternative rather than at a dead end.
    """
    payload = _full_payload()
    del payload["solve"]["energy_transfer"][0]["collider_species_key"]
    with pytest.raises(ValueError, match="a per_well energy_transfer entry requires"):
        NetworkPDepUploadRequest(**payload)

    payload = _full_payload()
    del payload["solve"]["energy_transfer"][0]["state_key"]
    with pytest.raises(ValueError, match="scope='network_wide'"):
        NetworkPDepUploadRequest(**payload)


def test_network_wide_energy_transfer_is_accepted_without_per_well_coverage() -> None:
    """One ⟨ΔE⟩down for a two-well network is accepted when declared as such.

    Arkane, RMG and MESS inputs routinely specify a single
    ``SingleExponentialDown`` for the whole network. Refusing that used to force
    the depositor to paste one number once per well, fabricating specificity the
    run never had (ADR 0009).
    """
    payload = _parallel_path_payload()
    payload["solve"]["energy_transfer"] = [
        {
            "scope": "network_wide",
            "model": "single_exponential_down",
            "alpha0_cm_inv": 300,
            "t_ref_k": 300,
        }
    ]
    request = NetworkPDepUploadRequest(**payload)
    assert len(request.solve.energy_transfer) == 1
    entry = request.solve.energy_transfer[0]
    assert entry.scope == NetworkEnergyTransferScope.network_wide
    assert entry.state_key is None
    assert entry.collider_species_key is None
    # The declaration survives a serialize/validate round trip: a client that
    # dumps and re-submits a payload must not silently lose the scope and land
    # back on the per_well default.
    revalidated = NetworkPDepUploadRequest.model_validate(
        request.model_dump(mode="json")
    )
    assert (
        revalidated.solve.energy_transfer[0].scope
        == NetworkEnergyTransferScope.network_wide
    )


def test_per_well_is_the_default_energy_transfer_scope() -> None:
    """Omitting ``scope`` keeps the preferred, more informative reading.

    The default matters for compatibility in both directions: every payload
    written before this axis existed stays valid and keeps meaning per-well,
    and a producer who does not know about the axis cannot accidentally
    deposit the weaker claim.
    """
    request = NetworkPDepUploadRequest(**_full_payload())
    entry = request.solve.energy_transfer[0]
    assert "scope" not in _full_payload()["solve"]["energy_transfer"][0]
    assert entry.scope == NetworkEnergyTransferScope.per_well
    assert entry.state_key == "well_RO2"
    assert entry.collider_species_key == "Ar"


def test_network_wide_energy_transfer_must_not_name_a_scope() -> None:
    """Naming a well while claiming network-wide scope contradicts itself."""
    payload = _full_payload()
    payload["solve"]["energy_transfer"] = [
        {
            "scope": "network_wide",
            "state_key": "well_RO2",
            "model": "single_exponential_down",
            "alpha0_cm_inv": 300,
        }
    ]
    with pytest.raises(ValueError, match="must not name a"):
        NetworkPDepUploadRequest(**payload)


def test_mixed_energy_transfer_scopes_are_refused() -> None:
    """Half per-well and half global genuinely is ambiguous.

    Nothing in such a payload says which wells the network-wide entry covers,
    or whether it overrides the specific ones. Unlike the pure network-wide
    case, no correct calculation produces this, so it blocks.
    """
    payload = _parallel_path_payload()
    payload["solve"]["energy_transfer"] = [
        payload["solve"]["energy_transfer"][0],
        {
            "scope": "network_wide",
            "model": "single_exponential_down",
            "alpha0_cm_inv": 280,
            "t_ref_k": 300,
        },
    ]
    with pytest.raises(ValueError, match="must all share one scope"):
        NetworkPDepUploadRequest(**payload)


def test_two_network_wide_energy_transfer_entries_are_refused() -> None:
    """Two global declarations say nothing about which applies where."""
    payload = _full_payload()
    payload["solve"]["energy_transfer"] = [
        {
            "scope": "network_wide",
            "model": "single_exponential_down",
            "alpha0_cm_inv": 300,
            "t_ref_k": 300,
        },
        {
            "scope": "network_wide",
            "model": "single_exponential_down",
            "alpha0_cm_inv": 280,
            "t_ref_k": 300,
        },
    ]
    with pytest.raises(ValueError, match="a network_wide energy_transfer declaration"):
        NetworkPDepUploadRequest(**payload)


def test_network_wide_energy_transfer_round_trips_and_warns(db_engine) -> None:
    """Accepted, annotated, and readable back as network-wide.

    The three things a reader needs: the row survives with its scope intact,
    the read surface says ``network_wide`` with no state or collider attached,
    and the upload carried a warning saying the well-to-well variation was
    never determined.
    """
    payload = _parallel_path_payload()
    payload["solve"]["energy_transfer"] = [
        {
            "scope": "network_wide",
            "model": "single_exponential_down",
            "alpha0_cm_inv": 300,
            "t_ref_k": 300,
            "note": "one energyTransferModel declared for the whole network",
        }
    ]
    with Session(db_engine) as session, session.begin():
        request = NetworkPDepUploadRequest(**payload)
        warnings: list[UploadWarning] = []
        network = persist_network_pdep_upload(session, request, warnings=warnings)
        session.flush()

        assert W_NETWORK_WIDE_ENERGY_TRANSFER in {w.code for w in warnings}

        solve = session.scalars(
            select(NetworkSolve).where(NetworkSolve.network_id == network.id)
        ).one()
        rows = session.scalars(
            select(NetworkSolveEnergyTransfer).where(
                NetworkSolveEnergyTransfer.solve_id == solve.id
            )
        ).all()
        assert len(rows) == 1
        assert rows[0].scope == NetworkEnergyTransferScope.network_wide
        assert rows[0].state_id is None
        assert rows[0].collider_species_entry_id is None
        assert rows[0].alpha0_cm_inv == 300

        solve_read = get_network_solve(
            session,
            network_solve_handle=solve.public_ref,
            include=["energy_transfer"],
        )
        read_rows = solve_read.record.energy_transfer or []
        assert len(read_rows) == 1
        assert read_rows[0].scope == NetworkEnergyTransferScope.network_wide
        assert read_rows[0].state_composition_hash is None
        assert read_rows[0].collider_species_entry_ref is None


def test_per_well_energy_transfer_round_trips_and_does_not_warn(db_engine) -> None:
    """The preferred form keeps working and stays unannotated.

    Lowering the barrier for the network-wide case must not blur the two: a
    per-well deposit still resolves both axes on the read surface and carries
    no completeness warning.
    """
    with Session(db_engine) as session, session.begin():
        request = NetworkPDepUploadRequest(**_parallel_path_payload())
        warnings: list[UploadWarning] = []
        network = persist_network_pdep_upload(session, request, warnings=warnings)
        session.flush()

        assert W_NETWORK_WIDE_ENERGY_TRANSFER not in {w.code for w in warnings}

        solve = session.scalars(
            select(NetworkSolve).where(NetworkSolve.network_id == network.id)
        ).one()
        rows = session.scalars(
            select(NetworkSolveEnergyTransfer).where(
                NetworkSolveEnergyTransfer.solve_id == solve.id
            )
        ).all()
        assert len(rows) == 2
        assert all(
            row.scope == NetworkEnergyTransferScope.per_well for row in rows
        )
        assert all(row.state_id is not None for row in rows)
        assert all(row.collider_species_entry_id is not None for row in rows)

        solve_read = get_network_solve(
            session,
            network_solve_handle=solve.public_ref,
            include=["energy_transfer"],
        )
        read_rows = solve_read.record.energy_transfer or []
        assert len(read_rows) == 2
        assert all(
            row.scope == NetworkEnergyTransferScope.per_well for row in read_rows
        )
        assert all(row.state_composition_hash is not None for row in read_rows)


def test_other_convention_requires_a_note() -> None:
    payload = _full_payload()
    payload["solve"]["state_energies"][0]["energy_zero_convention"] = "other"
    with pytest.raises(ValueError, match="convention_note is required"):
        NetworkPDepUploadRequest(**payload)

    payload = _full_payload()
    payload["solve"]["state_energies"][0]["energy_zero_convention"] = "other"
    payload["solve"]["state_energies"][0]["convention_note"] = (
        "Zero at the C2H5OO well minimum."
    )
    assert NetworkPDepUploadRequest(**payload) is not None


def test_network_chebyshev_requires_temperature_and_pressure_bounds() -> None:
    """NB4: a Chebyshev surface with no bounds is not evaluable anywhere.

    The coefficients are fit in reduced variables mapped from the fit's own
    T/P bounds, so omitting them stores a surface that can never be read back
    as a rate. The standalone kinetics path already enforced this; the network
    path did not.
    """
    payload = _full_payload(include_solve=True)
    payload["solve"]["channel_kinetics"] = [
        {
            "channel_key": "association_path",
            "model_kind": "chebyshev",
            "chebyshev": {
                "n_temperature": 2,
                "n_pressure": 2,
                "coefficients": [[1.0, 2.0], [3.0, 4.0]],
            },
        }
    ]
    with pytest.raises(ValueError, match="requires finite T and P bounds"):
        NetworkPDepUploadRequest(**payload)

    # A partial set is rejected too, and the message names what is missing.
    payload["solve"]["channel_kinetics"][0].update({"tmin_k": 300.0, "tmax_k": 2000.0})
    with pytest.raises(ValueError, match=r"missing: \['pmin_bar', 'pmax_bar'\]"):
        NetworkPDepUploadRequest(**payload)

    payload["solve"]["channel_kinetics"][0].update({"pmin_bar": 0.01, "pmax_bar": 100.0})
    assert NetworkPDepUploadRequest(**payload) is not None


# ---------------------------------------------------------------------------
# Well-skipping (chemically-activated) channels
# ---------------------------------------------------------------------------


def _well_skipping_payload() -> dict:
    """``_full_payload`` plus the chemically-activated entrance -> exit channel.

    ``C2H5 + O2 -> C2H4 + HO2`` is the textbook well-skipping rate: the
    reactants associate into energized ``C2H5OO*``, which eliminates HO2 before
    it is collisionally stabilized. No single elementary step joins the
    entrance and exit configurations, so the master equation's phenomenological
    k(T,P) is the only thing that describes it.
    """
    payload = _full_payload(include_solve=True)
    payload["channels"].append(
        {
            "key": "chemically_activated_path",
            "source_state_key": "entrance",
            "sink_state_key": "exit",
            "kind": "exchange",
            "mechanism": "well_skipping",
        }
    )
    payload["solve"]["channel_kinetics"] = [
        {
            "channel_key": "chemically_activated_path",
            "model_kind": "chebyshev",
            "chebyshev": {
                "n_temperature": 2,
                "n_pressure": 2,
                "coefficients": [[7.0, 0.5], [-0.25, 0.125]],
            },
            "tmin_k": 300.0,
            "tmax_k": 2000.0,
            "pmin_bar": 0.01,
            "pmax_bar": 100.0,
            "rate_units": "cm3_mol_s",
            "pressure_units": "bar",
            "temperature_units": "kelvin",
            "stores_log10_k": True,
            "note": "chemically activated; no single elementary step",
        }
    ]
    return payload


def test_pathless_channel_without_a_declaration_is_still_rejected() -> None:
    """Omitting the paths is an incomplete deposit, never an implicit claim.

    This is the guarantee that keeps the well-skipping relaxation additive:
    silence still fails, and it fails with a message naming the declaration the
    producer would have to make.
    """
    payload = _full_payload()
    payload["channels"][2].pop("microreaction_paths")
    with pytest.raises(ValueError, match="must declare mechanism='well_skipping'"):
        NetworkPDepUploadRequest(**payload)

    # Explicitly empty is not a loophole either.
    payload["channels"][2]["microreaction_paths"] = []
    with pytest.raises(ValueError, match="must declare mechanism='well_skipping'"):
        NetworkPDepUploadRequest(**payload)


def test_well_skipping_channel_must_not_supply_paths() -> None:
    """If a single elementary step joins the endpoints, it is not well-skipping."""
    payload = _well_skipping_payload()
    payload["channels"][-1]["microreaction_paths"] = [
        {"micro_reaction_key": "rxn_assoc"}
    ]
    with pytest.raises(ValueError, match="declares mechanism='well_skipping' but"):
        NetworkPDepUploadRequest(**payload)


def test_well_skipping_rejected_when_an_elementary_step_joins_the_endpoints() -> None:
    """A directly-connected pair must be attributed, not declared multi-step."""
    payload = _full_payload()
    # entrance -> well_RO2 IS rxn_assoc; calling it well-skipping hides evidence.
    payload["channels"][0] = {
        "key": "association_path",
        "source_state_key": "entrance",
        "sink_state_key": "well_RO2",
        "kind": "association",
        "mechanism": "well_skipping",
    }
    with pytest.raises(ValueError, match="directly connected by an elementary"):
        NetworkPDepUploadRequest(**payload)


def test_well_skipping_rejected_without_a_well_intermediate() -> None:
    """The intermediate must be an energized well, not a separated product set.

    Flux that reaches a bimolecular configuration has separated; it is a
    reservoir in the master equation, not an energized intermediate. Reclassify
    the RO2 well as bimolecular and the declaration loses its backing.
    """
    payload = _well_skipping_payload()
    payload["states"][1]["kind"] = "bimolecular"
    # With no well states left, the (well, collider) transfer cross product is
    # empty, so the transfer rows must go too.
    payload["solve"]["energy_transfer"] = []
    with pytest.raises(ValueError, match="through well states"):
        NetworkPDepUploadRequest(**payload)


def test_well_skipping_channel_round_trips(db_engine) -> None:
    """A declared chemically-activated channel uploads, persists, and reads back.

    It stores zero ``network_channel_microreaction`` rows and zero barriers —
    correctly, because it has no elementary step and no saddle point — while
    still carrying the master equation's k(T,P). The stored ``mechanism`` is
    what keeps that emptiness readable as a claim rather than as a gap.
    """
    from app.db.models.common import NetworkChannelMechanism
    from app.db.models.network_pdep import NetworkKinetics

    with _rolled_back_session(db_engine) as session:
        request = NetworkPDepUploadRequest(**_well_skipping_payload())
        network = persist_network_pdep_upload(session, request)
        session.flush()

        channel = session.scalars(
            select(NetworkChannel).where(
                NetworkChannel.network_id == network.id,
                NetworkChannel.channel_key == "chemically_activated_path",
            )
        ).one()
        assert channel.mechanism is NetworkChannelMechanism.well_skipping
        assert (
            session.scalars(
                select(NetworkChannelMicroReaction).where(
                    NetworkChannelMicroReaction.channel_id == channel.id
                )
            ).all()
            == []
        )

        solve = session.scalars(
            select(NetworkSolve).where(NetworkSolve.network_id == network.id)
        ).one()
        assert (
            session.scalars(
                select(NetworkSolveChannelBarrier).where(
                    NetworkSolveChannelBarrier.solve_id == solve.id,
                    NetworkSolveChannelBarrier.channel_id == channel.id,
                )
            ).all()
            == []
        )
        # The rate the master equation produced is what makes it worth storing.
        kinetics = session.scalars(
            select(NetworkKinetics).where(NetworkKinetics.channel_id == channel.id)
        ).all()
        assert len(kinetics) == 1

        # Every elementary channel in the same payload is untouched.
        elementary = session.scalars(
            select(NetworkChannel).where(
                NetworkChannel.network_id == network.id,
                NetworkChannel.channel_key != "chemically_activated_path",
            )
        ).all()
        assert len(elementary) == 3
        assert all(
            c.mechanism is NetworkChannelMechanism.elementary for c in elementary
        )

        # The read surface states the mechanism instead of leaving the caller
        # to infer it from an empty microreaction list.
        read = get_network(
            session, network_handle=network.public_ref, include=["channels"]
        )
        by_key = {c.channel_key: c for c in read.record.channels or []}
        activated = by_key["chemically_activated_path"]
        assert activated.mechanism is NetworkChannelMechanism.well_skipping
        assert activated.microreactions == []
        assert activated.has_kinetics is True
        assert (
            by_key["elimination_path"].mechanism
            is NetworkChannelMechanism.elementary
        )


# ---------------------------------------------------------------------------
# Reported (literature) pressure-dependent kinetics — ADR 0010
# ---------------------------------------------------------------------------

_REPORTED_LITERATURE = {
    "doi": "10.1000/reported.pdep",
    "title": "Pressure-dependent kinetics of C2H5 + O2",
}

_REPORTED_KINETICS = {
    "channel_key": "association_path",
    "source_state_key": "entrance",
    "sink_state_key": "well_RO2",
    "model_kind": "plog",
    "plog": {
        "entries": [
            {
                "entry_index": 1,
                "pressure_bar": 0.01,
                "a": 1.2e12,
                "n": 0.4,
                "ea_kj_mol": 5.0,
            },
            {
                "entry_index": 2,
                "pressure_bar": 1.0,
                "a": 3.4e12,
                "n": 0.2,
                "ea_kj_mol": 4.0,
            },
        ]
    },
    "tmin_k": 300.0,
    "tmax_k": 2000.0,
    "pmin_bar": 0.01,
    "pmax_bar": 1.0,
    "rate_units": "cm3_mol_s",
    "pressure_units": "bar",
    "temperature_units": "kelvin",
    "note": "Table 3 of the cited paper",
}


def _reported_payload(*, with_bath_gas: bool = False) -> dict:
    """A PDep payload whose k(T,P) were transcribed out of a paper.

    Deliberately carries *none* of the master-equation inputs: no state
    energies, no channel barriers, no energy transfer, no bath gas, no source
    calculations. That is the whole point — a depositor reading a
    supplementary table has none of them, and before ADR 0010 this payload was
    unrepresentable, so the data simply stayed out of the database.

    The network topology is still required and still checked: a reported rate
    has to attach to a channel that exists.

    Dropping the bath gas also drops the Ar species, which the base fixture
    declares *only* as a collider. The existing orphan-species rule still
    applies to a reported payload, and that is the correct outcome: a paper
    that does not state its bath gas gives you no reason to name one.
    ``with_bath_gas=True`` keeps both, for the cases that check a reported
    solve is still held to the rules for evidence it does supply.
    """
    payload = _full_payload(include_solve=True)
    solve: dict = {
        "kind": "reported",
        "tmin_k": 300,
        "tmax_k": 2000,
        "pmin_bar": 0.01,
        "pmax_bar": 1.0,
        "literature": dict(_REPORTED_LITERATURE),
        "channel_kinetics": [deepcopy(_REPORTED_KINETICS)],
    }
    if with_bath_gas:
        solve["bath_gas"] = [{"species_key": "Ar", "mole_fraction": 1.0}]
    else:
        payload["species"] = [
            sp for sp in payload["species"] if sp["key"] != "Ar"
        ]
    payload["solve"] = solve
    return payload


def test_reported_solve_is_accepted_without_master_equation_inputs() -> None:
    """Published k(T,P) is depositable at all — the point of ADR 0010.

    Every coverage rule the old contract applied to a solve asked for
    something a paper's supplementary table does not contain. The result was
    not a stricter database, it was an emptier one: literature
    pressure-dependent kinetics could not be entered by any route.
    """
    request = NetworkPDepUploadRequest(**_reported_payload())
    assert request.solve.kind is NetworkSolveKind.reported
    assert request.solve.state_energies == []
    assert request.solve.channel_barriers == []
    assert request.solve.energy_transfer == []
    assert request.solve.bath_gas == []
    assert request.solve.source_calculations == []
    assert len(request.solve.channel_kinetics) == 1


def test_computed_is_the_default_solve_kind() -> None:
    """Omitting ``kind`` keeps the stronger claim and the stricter rules.

    The default matters in both directions: every payload written before this
    axis existed stays valid and keeps meaning "solved here", and a producer
    who has never heard of the axis cannot accidentally deposit the weaker
    record.
    """
    payload = _full_payload()
    assert "kind" not in payload["solve"]
    request = NetworkPDepUploadRequest(**payload)
    assert request.solve.kind is NetworkSolveKind.computed


def test_reported_solve_without_literature_is_refused() -> None:
    """A reported solve's entire warrant is the paper it cites.

    Relaxing the master-equation coverage rules is only defensible because
    something else stands behind the numbers. With no literature the record
    would assert rates carrying neither a derivation nor a source, which is
    weaker than either form this decision meant to admit, so it blocks.
    """
    payload = _reported_payload()
    del payload["solve"]["literature"]
    with pytest.raises(ValueError, match="a reported solve must supply literature"):
        NetworkPDepUploadRequest(**payload)


def test_reported_solve_without_kinetics_is_refused() -> None:
    """A record that reports nothing contradicts its own kind."""
    payload = _reported_payload()
    payload["solve"]["channel_kinetics"] = []
    with pytest.raises(ValueError, match="must supply channel_kinetics"):
        NetworkPDepUploadRequest(**payload)


def test_computed_solve_still_requires_every_coverage_rule() -> None:
    """Nothing is relaxed for the preferred form.

    Each of the three rules is dropped independently from an otherwise valid
    computed payload. All three must still refuse it — a reported escape hatch
    that quietly loosened the computed contract would make the two
    indistinguishable, which is the failure ADR 0010 exists to avoid.
    """
    payload = _full_payload()
    payload["solve"]["state_energies"] = payload["solve"]["state_energies"][:1]
    with pytest.raises(ValueError, match="exactly one energy for every network state"):
        NetworkPDepUploadRequest(**payload)

    payload = _full_payload()
    payload["solve"]["channel_barriers"] = []
    with pytest.raises(ValueError, match="channel_barriers must provide"):
        NetworkPDepUploadRequest(**payload)

    payload = _full_payload()
    payload["solve"]["energy_transfer"] = []
    with pytest.raises(ValueError, match="energy_transfer must cover"):
        NetworkPDepUploadRequest(**payload)

    # And the two lists that stopped being ``min_length=1`` fields are still
    # mandatory for a computed solve — the requirement moved, it did not go.
    payload = _full_payload()
    payload["solve"]["state_energies"] = []
    with pytest.raises(ValueError, match="a computed solve must supply state_energies"):
        NetworkPDepUploadRequest(**payload)

    payload = _full_payload()
    payload["solve"]["source_calculations"] = []
    with pytest.raises(
        ValueError, match="a computed solve must supply source_calculations"
    ):
        NetworkPDepUploadRequest(**payload)


def test_reported_solve_still_validates_what_it_does_supply() -> None:
    """Relaxed means not required, never unvalidated.

    A reported solve that volunteers a bath-gas composition or a state energy
    is held to the same referential and physical rules as a computed one. The
    relaxation is about which evidence must be present, not about whether
    present evidence is checked.
    """
    payload = _reported_payload(with_bath_gas=True)
    payload["solve"]["bath_gas"][0]["mole_fraction"] = 0.5
    with pytest.raises(ValueError, match="mole fractions must sum to 1.0"):
        NetworkPDepUploadRequest(**payload)

    # A complete one is fine, and proves the check ran rather than being
    # skipped wholesale for reported solves.
    request = NetworkPDepUploadRequest(**_reported_payload(with_bath_gas=True))
    assert len(request.solve.bath_gas) == 1

    payload = _reported_payload()
    payload["solve"]["state_energies"] = [
        {
            "state_key": "well_RO2",
            "energy_kj_mol": -120.0,
            **_CONVENTIONS,
            "source_calculation_key": "no_such_calculation",
        }
    ]
    with pytest.raises(
        ValueError, match="state_energies references undefined source_calculation_key"
    ):
        NetworkPDepUploadRequest(**payload)

    # A partial set of state energies is accepted, though: for a reported
    # solve the coverage rule does not apply, so "some of what the paper
    # printed" is a legitimate deposit.
    payload = _reported_payload()
    payload["solve"]["state_energies"] = [
        {"state_key": "well_RO2", "energy_kj_mol": -120.0, **_CONVENTIONS}
    ]
    request = NetworkPDepUploadRequest(**payload)
    assert len(request.solve.state_energies) == 1


def test_reported_solve_round_trips_and_warns(db_engine) -> None:
    """The kind survives persistence and the read path, and is annotated.

    Both halves are load-bearing. A reported record that could not be told
    apart from a computed one on read would be worse than refusing the
    deposit, and the warning is what tells a depositor that the record they
    just made is one nobody can re-derive.
    """
    from app.db.models.network_pdep import NetworkKinetics

    # Rolled back rather than committed: ``db_engine`` is session-scoped, and
    # a leaked NetworkKinetics row breaks
    # ``test_pdep_workflow_persists_and_reads_back_channel_kinetics``, which
    # counts them across the whole table.
    with _rolled_back_session(db_engine) as session:
        request = NetworkPDepUploadRequest(**_reported_payload())
        warnings: list[UploadWarning] = []
        network = persist_network_pdep_upload(session, request, warnings=warnings)
        session.flush()

        assert W_REPORTED_NETWORK_SOLVE in {w.code for w in warnings}
        warning = next(w for w in warnings if w.code == W_REPORTED_NETWORK_SOLVE)
        assert warning.field == "solve.kind"
        assert "transcribed" in warning.message

        solve = session.scalars(
            select(NetworkSolve).where(NetworkSolve.network_id == network.id)
        ).one()
        assert solve.kind is NetworkSolveKind.reported
        # The relaxation really did produce a solve with no ME inputs, and the
        # required literature really is attached.
        assert solve.literature_id is not None
        assert (
            session.scalar(
                select(func.count())
                .select_from(NetworkSolveChannelBarrier)
                .where(NetworkSolveChannelBarrier.solve_id == solve.id)
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(NetworkSolveEnergyTransfer)
                .where(NetworkSolveEnergyTransfer.solve_id == solve.id)
            )
            == 0
        )

        # ...and the rates it exists to carry did persist.
        kinetics = session.scalars(
            select(NetworkKinetics).where(NetworkKinetics.solve_id == solve.id)
        ).all()
        assert len(kinetics) == 1

        solve_read = get_network_solve(
            session, network_solve_handle=solve.public_ref, include=[]
        )
        assert solve_read.record.network_solve.kind is NetworkSolveKind.reported


def test_computed_solve_carries_no_reported_warning(db_engine) -> None:
    """The preferred form stays unannotated.

    Admitting the weaker record must not blur the two: a computed deposit
    reads back as computed and carries no completeness warning about its
    origin.
    """
    with _rolled_back_session(db_engine) as session:
        request = NetworkPDepUploadRequest(**_full_payload())
        warnings: list[UploadWarning] = []
        network = persist_network_pdep_upload(session, request, warnings=warnings)
        session.flush()

        assert W_REPORTED_NETWORK_SOLVE not in {w.code for w in warnings}
        solve = session.scalars(
            select(NetworkSolve).where(NetworkSolve.network_id == network.id)
        ).one()
        assert solve.kind is NetworkSolveKind.computed

        solve_read = get_network_solve(
            session, network_solve_handle=solve.public_ref, include=[]
        )
        assert solve_read.record.network_solve.kind is NetworkSolveKind.computed
