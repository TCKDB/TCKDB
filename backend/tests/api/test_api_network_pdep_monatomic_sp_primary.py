"""A monatomic species may be deposited with an ``sp`` primary on the PDep route (#615).

#610 (PR #613) let a single atom be deposited with an ``sp`` primary on
``/uploads/computed-species`` and ``/uploads/computed-reaction``. The
pressure-dependent network route still required an ``opt`` primary, so the H
atom of a hydrazine-style network could not be sent honestly. These tests build
a small ``C2H5 <=> C2H4 + H`` network whose hydrogen atom is the single point
alone (no optimisation, no second copy of the same energy) and pin:

* the atom is accepted, stored as one conformer observation with one ``sp``
  calculation anchored to it, carrying the atom as its final geometry, linked
  from the species' statmech as role ``sp`` and from the solve as
  ``well_energy``, with no warning about a missing optimisation;
* a primary that is an ``sp`` on two or more atoms, or a one-atom
  ``freq``/``scan``, is still refused;
* the fabricated-opt shape producers send today keeps working;
* the duplicate-single-point rule of #610 holds on this route.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.models.calculation import (
    Calculation,
    CalculationInputGeometry,
    CalculationOutputGeometry,
)
from app.db.models.common import CalculationType
from app.db.models.geometry import Geometry
from app.db.models.network_pdep import NetworkSolveSourceCalculation
from app.db.models.species import ConformerObservation, Species, SpeciesEntry
from app.db.models.statmech import Statmech, StatmechSourceCalculation
from tests.workflows.test_network_pdep_upload import (
    _CONVENTIONS,
    _LOT_CC,
    _LOT_DFT,
    _SOFTWARE,
    _XYZ_ETHENE,
    _XYZ_ETHYL,
)

_PDEP_URL = "/api/v1/uploads/networks/pdep"
_H_XYZ = "1\nH\nH 0.0 0.0 0.0"
_H2_XYZ = "2\nH2\nH 0.0 0.0 0.0\nH 0.0 0.0 0.74"
_E_H = -0.4998


def _h_sp(key: str = "H_sp", energy: float = _E_H, lot: dict = _LOT_CC) -> dict:
    return {
        "key": key,
        "type": "sp",
        "software_release": _SOFTWARE,
        "level_of_theory": lot,
        "sp_electronic_energy_hartree": energy,
    }


def _payload(*, honest: bool = True) -> dict:
    """``C2H5 <=> C2H4 + H`` with the hydrogen atom as an sp-only primary.

    ``honest=False`` is the shape producers send today: the atom's single point
    relabelled as an opt, plus the real sp as a species calculation.
    """
    atom_sp = _h_sp()
    if honest:
        conformer_calc = atom_sp
        atom_calcs: list[dict] = []
    else:
        conformer_calc = {
            "key": "H_opt",
            "type": "opt",
            "software_release": _SOFTWARE,
            "level_of_theory": _LOT_DFT,
            "opt_converged": True,
        }
        atom_sp["geometry_key"] = "H_geom"
        atom_calcs = [atom_sp]
    return {
        "name": "ethyl <=> ethene + H",
        "species": [
            {
                "key": "ethyl",
                "species_entry": {"smiles": "C[CH2]", "charge": 0, "multiplicity": 2},
                "conformers": [
                    {
                        "key": "ethyl_conf1",
                        "geometry": {"key": "ethyl_geom", "xyz_text": _XYZ_ETHYL},
                        "calculation": {
                            "key": "ethyl_opt",
                            "type": "opt",
                            "software_release": _SOFTWARE,
                            "level_of_theory": _LOT_DFT,
                            "opt_converged": True,
                        },
                    }
                ],
                "calculations": [
                    {
                        "key": "ethyl_sp",
                        "type": "sp",
                        "geometry_key": "ethyl_geom",
                        "software_release": _SOFTWARE,
                        "level_of_theory": _LOT_CC,
                        "sp_electronic_energy_hartree": -79.8,
                    }
                ],
            },
            {
                "key": "ethene",
                "species_entry": {"smiles": "C=C", "charge": 0, "multiplicity": 1},
                "conformers": [
                    {
                        "key": "ethene_conf1",
                        "geometry": {"key": "ethene_geom", "xyz_text": _XYZ_ETHENE},
                        "calculation": {
                            "key": "ethene_opt",
                            "type": "opt",
                            "software_release": _SOFTWARE,
                            "level_of_theory": _LOT_DFT,
                            "opt_converged": True,
                        },
                    }
                ],
                "calculations": [
                    {
                        "key": "ethene_sp",
                        "type": "sp",
                        "geometry_key": "ethene_geom",
                        "software_release": _SOFTWARE,
                        "level_of_theory": _LOT_CC,
                        "sp_electronic_energy_hartree": -78.4,
                    }
                ],
            },
            {
                "key": "H",
                "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
                "conformers": [
                    {
                        "key": "H_conf1",
                        "geometry": {"key": "H_geom", "xyz_text": _H_XYZ},
                        "calculation": conformer_calc,
                    }
                ],
                "calculations": atom_calcs,
                "statmech": {
                    "statmech_treatment": "rrho",
                    "rigid_rotor_kind": "atom",
                    "source_calculations": [{"calculation_key": "H_sp", "role": "sp"}],
                },
            },
        ],
        "micro_reactions": [
            {
                "key": "rxn_h_loss",
                "reversible": True,
                "reactants": [{"species_key": "ethyl"}],
                "products": [{"species_key": "ethene"}, {"species_key": "H"}],
            }
        ],
        "states": [
            {"key": "well_ethyl", "kind": "well", "participants": [{"species_key": "ethyl"}]},
            {
                "key": "exit_H",
                "kind": "bimolecular",
                "participants": [{"species_key": "ethene"}, {"species_key": "H"}],
            },
        ],
        "channels": [
            {
                "key": "h_loss_path",
                "source_state_key": "well_ethyl",
                "sink_state_key": "exit_H",
                "kind": "dissociation",
                "microreaction_paths": [{"micro_reaction_key": "rxn_h_loss"}],
            }
        ],
        "solve": {
            "me_method": "reservoir_state",
            "tmin_k": 300,
            "tmax_k": 2000,
            "pmin_bar": 0.01,
            "pmax_bar": 100,
            "grain_count": 250,
            "bath_gas": [{"species_key": "ethene", "mole_fraction": 1.0}],
            "energy_transfer": [
                {
                    "model": "single_exponential_down",
                    "alpha0_cm_inv": 300,
                    "t_ref_k": 300,
                    "state_key": "well_ethyl",
                    "collider_species_key": "ethene",
                }
            ],
            "state_energies": [
                {
                    "state_key": "well_ethyl",
                    "energy_kj_mol": 0.0,
                    **_CONVENTIONS,
                    "source_calculation_key": "ethyl_sp",
                },
                {
                    "state_key": "exit_H",
                    "energy_kj_mol": 150.0,
                    **_CONVENTIONS,
                    "source_calculation_key": "H_sp",
                },
            ],
            "source_calculations": [
                {"calculation_key": "ethyl_sp", "role": "well_energy"},
                {"calculation_key": "H_sp", "role": "well_energy"},
            ],
        },
    }


def _h_species(payload: dict) -> dict:
    (species,) = [s for s in payload["species"] if s["key"] == "H"]
    return species


def _codes(resp) -> set[str]:
    return {w["code"] for w in resp.json().get("warnings", [])}


def _atom_rows(db_session) -> dict:
    (species,) = db_session.scalars(select(Species).where(Species.smiles == "[H]")).all()
    entry_ids = db_session.scalars(
        select(SpeciesEntry.id).where(SpeciesEntry.species_id == species.id)
    ).all()
    calcs = db_session.scalars(
        select(Calculation)
        .where(Calculation.species_entry_id.in_(entry_ids))
        .order_by(Calculation.id)
    ).all()
    calc_ids = [c.id for c in calcs]
    return {
        "calcs": calcs,
        "observations": db_session.scalars(
            select(ConformerObservation).where(
                ConformerObservation.id.in_(
                    [c.conformer_observation_id for c in calcs if c.conformer_observation_id]
                )
            )
        ).all(),
        "inputs": db_session.execute(
            select(CalculationInputGeometry.calculation_id, Geometry.natoms)
            .join(Geometry, Geometry.id == CalculationInputGeometry.geometry_id)
            .where(CalculationInputGeometry.calculation_id.in_(calc_ids))
        ).all(),
        "outputs": db_session.execute(
            select(CalculationOutputGeometry.calculation_id, Geometry.natoms)
            .join(Geometry, Geometry.id == CalculationOutputGeometry.geometry_id)
            .where(CalculationOutputGeometry.calculation_id.in_(calc_ids))
        ).all(),
        "statmech_sources": db_session.execute(
            select(StatmechSourceCalculation.calculation_id, StatmechSourceCalculation.role)
            .join(Statmech, Statmech.id == StatmechSourceCalculation.statmech_id)
            .where(Statmech.species_entry_id.in_(entry_ids))
        ).all(),
        "solve_sources": db_session.execute(
            select(NetworkSolveSourceCalculation.calculation_id, NetworkSolveSourceCalculation.role)
            .where(NetworkSolveSourceCalculation.calculation_id.in_(calc_ids))
        ).all(),
    }


def test_sp_only_atom_is_accepted_and_stored_honestly(client, db_session):
    resp = client.post(_PDEP_URL, json=_payload())
    assert resp.status_code == 201, resp.text[:1500]
    rows = _atom_rows(db_session)
    (calc,) = rows["calcs"]
    (observation,) = rows["observations"]
    assert calc.type is CalculationType.sp
    assert calc.conformer_observation_id == observation.id
    # One atom, stored once, as the conformer's only calculation. It carries
    # the atom as its final geometry, so the conformer reads back with one.
    assert [tuple(r) for r in rows["outputs"]] == [(calc.id, 1)]
    assert [(cid, role.value) for cid, role in rows["statmech_sources"]] == [(calc.id, "sp")]
    assert [(cid, role.value) for cid, role in rows["solve_sources"]] == [
        (calc.id, "well_energy")
    ]
    # Nothing about a missing optimisation, a missing edge to one, or an
    # unanchored calculation.
    assert not _codes(resp) & {
        "dependency_edge_not_inferred",
        "converged_opt_no_usable_energy",
        "calculation_conformer_anchor_unresolved",
    }, resp.json()["warnings"]


def test_polyatomic_sp_primary_is_still_refused(client):
    payload = _payload()
    _h_species(payload)["conformers"][0]["geometry"]["xyz_text"] = _H2_XYZ
    resp = client.post(_PDEP_URL, json=payload)
    assert resp.status_code == 422, resp.text[:800]
    assert "primary calculation type must be 'opt', got 'sp'" in resp.text
    assert "this geometry has 2 atoms" in resp.text


@pytest.mark.parametrize("primary_type", ["freq", "scan"])
def test_one_atom_non_sp_primary_is_still_refused(client, primary_type):
    payload = _payload()
    _h_species(payload)["conformers"][0]["calculation"]["type"] = primary_type
    resp = client.post(_PDEP_URL, json=payload)
    assert resp.status_code == 422, resp.text[:800]
    assert f"must be 'opt', got '{primary_type}'" in resp.text


def test_fabricated_opt_atom_keeps_working(client, db_session):
    resp = client.post(_PDEP_URL, json=_payload(honest=False))
    assert resp.status_code == 201, resp.text[:1500]
    rows = _atom_rows(db_session)
    assert sorted(c.type.value for c in rows["calcs"]) == ["opt", "sp"]


def test_second_sp_at_another_level_linked_to_the_atoms_statmech_is_refused(client):
    """The shared role rules still judge an sp-only atom on this route.

    A further sp at another level of theory belongs in the species'
    ``calculations`` without a statmech link; linking both as role ``sp`` gives
    the record no single energy level, which the shared
    ``assert_role_consistency`` refuses. (The #610 duplicate-on-one-geometry
    rule is inert on this route: it compares *input* geometries, and this route
    links a geometry as a calculation's final *output* only, for opt and sp
    alike. That is how the route has always stored geometries, not something
    the atom changes.)
    """
    payload = _payload()
    species = _h_species(payload)
    second = _h_sp("H_sp2", energy=-0.4999, lot=_LOT_DFT)
    second["geometry_key"] = "H_geom"
    species["calculations"] = [second]
    species["statmech"]["source_calculations"].append(
        {"calculation_key": "H_sp2", "role": "sp"}
    )
    resp = client.post(_PDEP_URL, json=payload)
    assert resp.status_code == 422, resp.text[:1000]
    assert resp.json()["code"] == "statmech_energy_level_ambiguous", resp.text[:600]


def test_second_sp_at_another_level_unlinked_is_accepted(client, db_session):
    payload = _payload()
    species = _h_species(payload)
    second = _h_sp("H_sp2", energy=-0.4999, lot=_LOT_DFT)
    second["geometry_key"] = "H_geom"
    species["calculations"] = [second]
    resp = client.post(_PDEP_URL, json=payload)
    assert resp.status_code == 201, resp.text[:1000]
    assert [c.type.value for c in _atom_rows(db_session)["calcs"]] == ["sp", "sp"]


def test_network_read_back_names_the_atoms_single_point(client, db_session):
    """The network reads carry the atom's sp as a well-energy source, typed ``sp``."""
    from app.db.models.network import Network

    assert client.post(_PDEP_URL, json=_payload()).status_code == 201
    ref = db_session.scalars(select(Network.public_ref)).one()
    resp = client.get(f"/api/v1/scientific/networks/{ref}?include=source_calculations,species")
    assert resp.status_code == 200, resp.text[:800]
    record = resp.json()["record"]
    sources = record["source_calculations"]
    assert sorted((s["role"], s["calculation_type"]) for s in sources) == [
        ("well_energy", "sp"),
        ("well_energy", "sp"),
    ]
    assert record["evidence_summary"]["source_calculation_count"] == 2
