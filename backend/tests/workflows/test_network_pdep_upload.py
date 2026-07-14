"""Integration tests for the unified pressure-dependent network upload workflow."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.app_user import AppUser
from app.db.models.calculation import (
    Calculation,
    CalculationFreqResult,
    CalculationOptResult,
    CalculationOutputGeometry,
    CalculationSPResult,
)
from app.db.models.common import CalculationType
from app.db.models.network import NetworkReaction, NetworkSpecies
from app.db.models.network_pdep import (
    NetworkChannel,
    NetworkSolve,
    NetworkSolveBathGas,
    NetworkSolveEnergyTransfer,
    NetworkSolveSourceCalculation,
    NetworkState,
)
from app.db.models.species import (
    ConformerGroup,
    ConformerObservation,
)
from app.db.models.transition_state import TransitionState, TransitionStateEntry
from app.schemas.workflows.network_pdep_upload import NetworkPDepUploadRequest
from app.workflows.network_pdep import persist_network_pdep_upload

_XYZ_ETHYL = "3\n\nC 0.0 0.0 0.0\nC 1.54 0.0 0.0\nH 2.0 1.0 0.0"
_XYZ_O2 = "2\n\nO 0.0 0.0 0.0\nO 1.21 0.0 0.0"
_XYZ_ETOO = "4\n\nC 0.0 0.0 0.0\nC 1.54 0.0 0.0\nO 2.5 0.0 0.0\nO 3.7 0.0 0.0"
_XYZ_TS = "4\n\nC 0.0 0.0 0.0\nC 1.54 0.0 0.0\nO 2.2 0.0 0.0\nO 3.4 0.0 0.0"
_XYZ_AR = "1\n\nAr 0.0 0.0 0.0"

_SOFTWARE = {"name": "Gaussian", "version": "16"}
_LOT_DFT = {"method": "B3LYP", "basis": "6-31G(d)"}
_LOT_CC = {"method": "CCSD(T)", "basis": "cc-pVTZ"}


def _full_payload(*, include_solve: bool = True) -> dict:
    """Build a full unified PDep payload with conformers, calcs, TS, and solve."""
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
        "transition_states": [{
            "key": "ts_assoc",
            "micro_reaction_key": "rxn_assoc",
            "charge": 0,
            "multiplicity": 2,
            "geometry": {"key": "ts_assoc_geom", "xyz_text": _XYZ_TS},
            "calculation": {
                "key": "ts_assoc_opt", "type": "opt",
                "software_release": _SOFTWARE, "level_of_theory": _LOT_DFT,
                "opt_converged": True,
            },
            "calculations": [
                {
                    "key": "ts_assoc_freq", "type": "freq",
                    "geometry_key": "ts_assoc_geom",
                    "software_release": _SOFTWARE, "level_of_theory": _LOT_DFT,
                    "freq_n_imag": 1, "freq_imag_freq_cm1": -1500.0,
                },
                {
                    "key": "ts_assoc_sp", "type": "sp",
                    "geometry_key": "ts_assoc_geom",
                    "software_release": _SOFTWARE, "level_of_theory": _LOT_CC,
                    "sp_electronic_energy_hartree": -229.5,
                },
            ],
        }],
        "micro_reactions": [{
            "key": "rxn_assoc",
            "reversible": True,
            "reactants": [{"species_key": "ethyl"}, {"species_key": "O2"}],
            "products": [{"species_key": "ethylperoxy"}],
        }],
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
        ],
        "channels": [
            {"source_state_key": "entrance", "sink_state_key": "well_RO2", "kind": "association"},
            {"source_state_key": "well_RO2", "sink_state_key": "entrance", "kind": "dissociation"},
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
            "energy_transfer": {
                "model": "single_exponential_down",
                "alpha0_cm_inv": 300,
                "t_ref_k": 300,
            },
            "source_calculations": [
                {"calculation_key": "ethyl_sp", "role": "well_energy"},
                {"calculation_key": "O2_sp", "role": "well_energy"},
                {"calculation_key": "etoo_sp", "role": "well_energy"},
                {"calculation_key": "ts_assoc_sp", "role": "barrier_energy"},
                {"calculation_key": "ts_assoc_freq", "role": "barrier_freq"},
            ],
        }

    return payload


def test_full_end_to_end_upload(db_engine) -> None:
    """Full PDep upload creates all entities end-to-end."""
    with Session(db_engine) as session, session.begin():
        session.add(AppUser(id=30, username="e2e_tester"))
        session.flush()

        request = NetworkPDepUploadRequest(**_full_payload())
        network = persist_network_pdep_upload(session, request, created_by=30)

        # -- Network --
        assert network.id is not None
        assert network.name == "ethyl + O2"

        # -- States: 2 --
        states = session.scalars(
            select(NetworkState).where(NetworkState.network_id == network.id)
        ).all()
        assert len(states) == 2

        # -- Channels: 2 --
        channels = session.scalars(
            select(NetworkChannel).where(NetworkChannel.network_id == network.id)
        ).all()
        assert len(channels) == 2

        # -- Micro reactions: 1 --
        rxn_links = session.scalars(
            select(NetworkReaction).where(NetworkReaction.network_id == network.id)
        ).all()
        assert len(rxn_links) == 1

        # -- Conformers: 4 (ethyl, O2, ethylperoxy, Ar) --
        conformers = session.scalars(select(ConformerObservation)).all()
        assert len(conformers) >= 4

        # -- Calculations total: 4 opts + 3 sp + 1 freq (species-side)
        #                        + 1 opt + 1 freq + 1 sp (TS-side) = 11
        all_calcs = session.scalars(select(Calculation)).all()
        assert len(all_calcs) >= 11

        # -- Calculation results --
        sp_results = session.scalars(select(CalculationSPResult)).all()
        assert len(sp_results) >= 4  # ethyl, O2, etoo, ts_assoc

        opt_results = session.scalars(select(CalculationOptResult)).all()
        assert len(opt_results) >= 2  # ethyl (converged), ts_assoc (converged)

        freq_results = session.scalars(select(CalculationFreqResult)).all()
        assert len(freq_results) >= 2  # ethyl (n_imag=0), ts_assoc (n_imag=1)

        # -- Geometry linkage --
        output_geoms = session.scalars(select(CalculationOutputGeometry)).all()
        assert len(output_geoms) >= 11  # every calculation has a geometry link

        # -- Transition state --
        ts_list = session.scalars(select(TransitionState)).all()
        assert len(ts_list) == 1
        assert ts_list[0].reaction_entry_id == rxn_links[0].reaction_entry_id

        ts_entries = session.scalars(select(TransitionStateEntry)).all()
        assert len(ts_entries) == 1
        assert ts_entries[0].charge == 0
        assert ts_entries[0].multiplicity == 2

        # TS calculations belong to TS entry
        ts_calcs = session.scalars(
            select(Calculation).where(
                Calculation.transition_state_entry_id == ts_entries[0].id
            )
        ).all()
        assert len(ts_calcs) == 3  # opt, freq, sp

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
        session.add(AppUser(id=31, username="anchor_tester"))
        session.flush()
        request = NetworkPDepUploadRequest(**payload)
        persist_network_pdep_upload(session, request, created_by=31)

        target_entry_id = session.execute(
            select(Calculation.species_entry_id)
            .where(
                Calculation.created_by == 31,
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
                ConformerObservation.created_by == 31,
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
                Calculation.created_by == 31,
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

        # Freq results: ethyl_freq and ts_assoc_freq.
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
    # association channel still keeps the two states connected.
    payload["channels"] = [
        {
            "source_state_key": "entrance",
            "sink_state_key": "well_RO2",
            "kind": "association",
        }
    ]
    payload["solve"]["channel_kinetics"] = [
        {
            "source_state_key": "well_RO2",
            "sink_state_key": "entrance",
            "model_kind": "chebyshev",
            "chebyshev": {
                "n_temperature": 2,
                "n_pressure": 2,
                "coefficients": [[1.0, 2.0], [3.0, 4.0]],
            },
        }
    ]
    with pytest.raises(ValueError, match="references undefined channel"):
        NetworkPDepUploadRequest(**payload)


def test_pdep_channel_kinetics_rejects_duplicate_within_payload() -> None:
    """Two channel_kinetics entries for the same (source, sink) pair within one
    payload are rejected (would silently write two rows for one channel/solve)."""
    payload = _full_payload(include_solve=True)
    entry = {
        "source_state_key": "entrance",
        "sink_state_key": "well_RO2",
        "model_kind": "chebyshev",
        "chebyshev": {
            "n_temperature": 2,
            "n_pressure": 2,
            "coefficients": [[1.0, 2.0], [3.0, 4.0]],
        },
    }
    payload["solve"]["channel_kinetics"] = [entry, {**entry}]
    with pytest.raises(ValueError, match="unique"):
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
            "chebyshev": {
                "n_temperature": 2,
                "n_pressure": 2,
                "coefficients": [[1.0, float("nan")], [3.0, 4.0]],
            },
        }
    ]
    with pytest.raises(ValueError, match="finite"):
        NetworkPDepUploadRequest(**payload)


def test_pdep_channel_kinetics_rejects_plog_model_kind() -> None:
    """Phase A is Chebyshev-only: PLOG/tabulated uploads are rejected."""
    payload = _full_payload(include_solve=True)
    payload["solve"]["channel_kinetics"] = [
        {
            "source_state_key": "entrance",
            "sink_state_key": "well_RO2",
            "model_kind": "plog",
        }
    ]
    with pytest.raises(ValueError, match="not yet supported"):
        NetworkPDepUploadRequest(**payload)
