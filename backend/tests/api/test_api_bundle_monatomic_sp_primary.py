"""A monatomic species may be deposited with an ``sp`` primary (#610).

Both bundle routes required an ``opt`` primary. ARC runs only a single point
for an atom, so its adapter relabelled the atom's single point as an
"optimisation": same log, same energy, ``converged=false``, and the wrong
level of theory and software. These tests take the real ARC hydrogen atom
from the ``rotor_scan_3``/``5``/``8`` fixtures, reshape it to what an honest
producer sends (the single point, once, as the primary), and pin:

* the atom is accepted on ``/uploads/computed-reaction`` and
  ``/uploads/computed-species``, stored as one conformer observation, one
  ``sp`` calculation anchored to it, the statmech/thermo source links, and no
  warning that concerns the missing optimisation or a missing dependency edge;
* anything with two or more atoms is still refused when its primary is not
  an ``opt``;
* the fabricated-opt shape the adapter sends today keeps working, and lands in
  the same conformer group as the honest shape;
* the atom's thermo exports to a contribution bundle.
"""

from __future__ import annotations

import copy
import glob
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db.models.calculation import (
    Calculation,
    CalculationDependency,
    CalculationInputGeometry,
    CalculationOutputGeometry,
)
from app.db.models.common import CalculationType
from app.db.models.geometry import Geometry
from app.db.models.species import (
    ConformerGroup,
    ConformerObservation,
    Species,
    SpeciesEntry,
)
from app.db.models.statmech import Statmech, StatmechSourceCalculation
from app.db.models.thermo import Thermo

_ARC_RUNS = Path(__file__).resolve().parents[1] / "fixtures" / "arc_runs"
_SCENARIOS = ("rotor_scan_3", "rotor_scan_5", "rotor_scan_8")
_REACTION_URL = "/api/v1/uploads/computed-reaction"
_SPECIES_URL = "/api/v1/uploads/computed-species"

_H2_XYZ = "2\nH2\nH 0.0 0.0 0.0\nH 0.0 0.0 0.74"


def _reaction_payload(scenario: str) -> dict:
    """The real ARC bundle, or its task-264 trimmed copy where one exists.

    The trimmed copy differs from the original only in the transition state's
    componentless ``bac_total`` (see ``test_api_arc_run_fixtures``), which is
    refused for a reason that has nothing to do with the atom under test.
    """
    for directory in ("tckdb_payloads_trimmed_264", "tckdb_payloads"):
        found = glob.glob(
            str(_ARC_RUNS / scenario / directory / "computed_reaction" / "*.payload.json")
        )
        if found:
            break
    (path,) = found
    return json.loads(Path(path).read_text())


def _h_species(payload: dict) -> dict:
    (species,) = [s for s in payload["species"] if s["species_entry"]["smiles"] == "[H]"]
    return species


def _reshape_reaction_atom_to_sp_only(payload: dict) -> dict:
    """What an honest producer sends for the atom: the single point, once.

    The fixture carries the fabricated shape: a conformer whose primary is a
    relabelled ``opt`` and a separate ``sp`` in ``calculations`` that depends
    on it. Promote the ``sp`` to the primary, drop the ``opt`` and the edge to
    it, and point the statmech source link at the single point alone.
    """
    payload = copy.deepcopy(payload)
    atom = _h_species(payload)
    (conformer,) = atom["conformers"]
    (sp,) = [c for c in atom["calculations"] if c["type"] == "sp"]
    opt_key = conformer["calculation"]["key"]
    sp.pop("depends_on", None)
    sp.pop("geometry_key", None)
    conformer["calculation"] = sp
    atom["calculations"] = [c for c in atom["calculations"] if c is not sp]
    atom["statmech"]["source_calculations"] = [
        sc for sc in atom["statmech"]["source_calculations"] if sc["calculation_key"] != opt_key
    ]
    assert atom["statmech"]["source_calculations"] == [
        {"calculation_key": sp["key"], "role": "sp"}
    ]
    assert opt_key not in json.dumps(payload), "a reference to the dropped opt remains"
    return payload


def _species_bundle_from_reaction_atom(payload: dict, *, sp_only: bool = True) -> dict:
    """The same atom as a ``/uploads/computed-species`` bundle.

    ``sp_only=False`` keeps the fabricated shape (an ``opt`` primary and the
    ``sp`` as an additional calculation) for the backward-compatibility test.
    """
    atom = _h_species(_reshape_reaction_atom_to_sp_only(payload) if sp_only else copy.deepcopy(payload))
    (conformer,) = atom["conformers"]
    primary = conformer["calculation"]
    additional: list[dict] = []
    if sp_only:
        sp = primary
    else:
        (sp,) = [c for c in atom["calculations"] if c["type"] == "sp"]
        sp.pop("geometry_key", None)
        sp.pop("depends_on", None)
        additional = [sp]
    energy = sp.pop("sp_electronic_energy_hartree")
    sp["sp_result"] = {"electronic_energy_hartree": energy}
    if not sp_only:
        primary["opt_result"] = {
            "converged": primary.pop("opt_converged"),
            "final_energy_hartree": primary.pop("opt_final_energy_hartree"),
        }
        primary.pop("output_geometries", None)
    software = {"name": "orca", "version": "6.0.0"}
    tool = {"name": "ARC", "version": "1.1.0"}
    sources = [{"calculation_key": sp["key"], "role": "sp"}]
    if not sp_only:
        sources.insert(0, {"calculation_key": primary["key"], "role": "opt"})
    return {
        "species_entry": atom["species_entry"],
        "conformers": [
            {
                "key": conformer["key"],
                "geometry": {"xyz_text": conformer["geometry"]["xyz_text"]},
                "primary_calculation": primary,
                "additional_calculations": additional,
            }
        ],
        "workflow_tool_release": tool,
        "statmech": {
            **atom["statmech"],
            "software_release": software,
            "source_calculations": sources,
        },
        "thermo": {
            **atom["thermo"],
            "software_release": software,
            "source_calculations": sources,
        },
        "applied_energy_corrections": atom["applied_energy_corrections"],
    }


def _atom_state(db_session) -> dict:
    """What the database holds for the hydrogen atom after an upload."""
    (species,) = db_session.scalars(select(Species).where(Species.smiles == "[H]")).all()
    entries = db_session.scalars(
        select(SpeciesEntry).where(SpeciesEntry.species_id == species.id)
    ).all()
    entry_ids = [e.id for e in entries]
    calcs = db_session.scalars(
        select(Calculation)
        .where(Calculation.species_entry_id.in_(entry_ids))
        .order_by(Calculation.id)
    ).all()
    groups = db_session.scalars(
        select(ConformerGroup).where(ConformerGroup.species_entry_id.in_(entry_ids))
    ).all()
    observations = db_session.scalars(
        select(ConformerObservation).where(
            ConformerObservation.conformer_group_id.in_([g.id for g in groups])
        )
    ).all()
    calc_ids = [c.id for c in calcs]
    inputs = db_session.execute(
        select(CalculationInputGeometry.calculation_id, Geometry.natoms)
        .join(Geometry, Geometry.id == CalculationInputGeometry.geometry_id)
        .where(CalculationInputGeometry.calculation_id.in_(calc_ids))
    ).all()
    outputs = db_session.scalars(
        select(CalculationOutputGeometry.calculation_id).where(
            CalculationOutputGeometry.calculation_id.in_(calc_ids)
        )
    ).all()
    statmech_sources = db_session.execute(
        select(StatmechSourceCalculation.calculation_id, StatmechSourceCalculation.role)
        .join(Statmech, Statmech.id == StatmechSourceCalculation.statmech_id)
        .where(Statmech.species_entry_id.in_(entry_ids))
    ).all()
    thermo_ids = db_session.scalars(
        select(Thermo.id).where(Thermo.species_entry_id.in_(entry_ids))
    ).all()
    return {
        "calcs": calcs,
        "groups": groups,
        "observations": observations,
        "inputs": inputs,
        "outputs": outputs,
        "statmech_sources": statmech_sources,
        "thermo_ids": thermo_ids,
    }


def _assert_sp_only_atom_stored(db_session) -> None:
    state = _atom_state(db_session)
    (calc,) = state["calcs"]
    (observation,) = state["observations"]
    assert calc.type is CalculationType.sp
    assert calc.conformer_observation_id == observation.id
    # The atom's geometry is a point, stored once. The single point ran on it
    # (input) and, being the conformer's only calculation, also carries it as
    # its final geometry, so the conformer reads back with a geometry.
    assert [(cid, natoms) for cid, natoms in state["inputs"]] == [(calc.id, 1)]
    assert state["outputs"] == [calc.id]
    assert [(cid, role.value) for cid, role in state["statmech_sources"]] == [
        (calc.id, "sp")
    ]
    assert len(state["thermo_ids"]) == 1


def _atom_warnings(resp, payload: dict) -> list[dict]:
    """The reaction-route warnings that name the hydrogen atom, by species index or key."""
    key = _h_species(payload)["key"]
    index = [s["key"] for s in payload["species"]].index(key)
    return [
        w
        for w in resp.json().get("warnings", [])
        if f"species[{index}]" in w.get("field", "") or f"'{key}'" in w.get("field", "")
    ]


@pytest.mark.parametrize("scenario", _SCENARIOS)
def test_sp_only_atom_is_accepted_on_the_reaction_route(client, db_session, scenario):
    payload = _reshape_reaction_atom_to_sp_only(_reaction_payload(scenario))
    resp = client.post(_REACTION_URL, json=payload)
    assert resp.status_code == 201, resp.text[:1500]
    _assert_sp_only_atom_stored(db_session)
    # The whole bundle, then the atom's own share of it: nothing about a
    # missing optimisation, a missing edge to one, or an unanchored calculation.
    silent = {
        "dependency_edge_not_inferred",
        "converged_opt_no_usable_energy",
        "calculation_conformer_anchor_unresolved",
    }
    atom_codes = {w["code"] for w in _atom_warnings(resp, payload)}
    assert not atom_codes & silent, _atom_warnings(resp, payload)
    # What is said belongs to the fixture: its "ORCA 6.0.0" version string and
    # the thermo/statmech software it never declared.
    assert atom_codes == {
        "software_release_version_is_composite",
        "missing_software_release_provenance",
    }, _atom_warnings(resp, payload)


@pytest.mark.parametrize("scenario", _SCENARIOS)
def test_sp_only_atom_is_accepted_on_the_species_route(client, db_session, scenario):
    payload = _species_bundle_from_reaction_atom(_reaction_payload(scenario))
    resp = client.post(_SPECIES_URL, json=payload)
    assert resp.status_code == 201, resp.text[:1500]
    _assert_sp_only_atom_stored(db_session)
    # Nothing is said about the missing optimisation or an edge to it. What is
    # said belongs to the fixture: its "ORCA 6.0.0" version string, and an
    # energy-correction scheme that names no software and no citation.
    assert {w["code"] for w in resp.json()["warnings"]} == {
        "software_release_version_is_composite",
        "missing_energy_correction_scheme_software",
        "missing_literature_provenance",
    }, resp.json()["warnings"]


def test_fabricated_opt_atom_keeps_working_on_both_routes(client, db_session):
    reaction = client.post(_REACTION_URL, json=_reaction_payload("rotor_scan_5"))
    assert reaction.status_code == 201, reaction.text[:1500]
    species = client.post(
        _SPECIES_URL,
        json=_species_bundle_from_reaction_atom(_reaction_payload("rotor_scan_5"), sp_only=False),
    )
    assert species.status_code == 201, species.text[:1500]


def test_sp_only_atom_joins_the_conformer_group_the_fabricated_opt_created(
    client, db_session
):
    """One atom, two deposits in two shapes: one group, two observations.

    An atom still gets a (rotor-less) fingerprint, so the fingerprint match in
    ``resolve_conformer_group`` joins the existing group; the no-fingerprint
    fallback is not the path taken. Neither looks at the primary's type.
    """
    first = client.post(
        _SPECIES_URL,
        json=_species_bundle_from_reaction_atom(_reaction_payload("rotor_scan_5"), sp_only=False),
    )
    assert first.status_code == 201, first.text[:1500]
    second = client.post(
        _SPECIES_URL,
        json=_species_bundle_from_reaction_atom(_reaction_payload("rotor_scan_8")),
    )
    assert second.status_code == 201, second.text[:1500]
    state = _atom_state(db_session)
    assert len(state["groups"]) == 1
    assert len(state["observations"]) == 2
    assert sorted(c.type.value for c in state["calcs"]) == ["opt", "sp", "sp"]


def _two_atom_species_bundle(primary_type: str) -> dict:
    bundle = _species_bundle_from_reaction_atom(_reaction_payload("rotor_scan_5"))
    bundle["species_entry"] = {"smiles": "[H][H]", "charge": 0, "multiplicity": 1}
    bundle["conformers"][0]["geometry"] = {"xyz_text": _H2_XYZ}
    bundle["conformers"][0]["primary_calculation"]["type"] = primary_type
    for block in ("thermo", "statmech", "applied_energy_corrections"):
        bundle.pop(block)
    return bundle


def test_two_atom_sp_primary_is_still_refused_on_the_species_route(client):
    resp = client.post(_SPECIES_URL, json=_two_atom_species_bundle("sp"))
    assert resp.status_code == 422, resp.text[:800]
    (detail,) = resp.json()["detail"]
    assert detail["loc"] == ["body", "conformers", 0]
    assert "primary_calculation.type must be 'opt', got 'sp'" in detail["msg"]
    assert "this geometry has 2 atoms" in detail["msg"]


def test_two_atom_sp_primary_is_still_refused_on_the_reaction_route(client):
    payload = _reshape_reaction_atom_to_sp_only(_reaction_payload("rotor_scan_5"))
    atom = _h_species(payload)
    atom["species_entry"] = {"smiles": "[H][H]", "charge": 0, "multiplicity": 1}
    atom["conformers"][0]["geometry"]["xyz_text"] = _H2_XYZ
    resp = client.post(_REACTION_URL, json=payload)
    assert resp.status_code == 422, resp.text[:800]
    (detail,) = resp.json()["detail"]
    assert detail["loc"][:2] == ["body", "species"]
    assert "primary calculation type must be 'opt', got 'sp'" in detail["msg"]
    assert "this geometry has 2 atoms" in detail["msg"]


@pytest.mark.parametrize("primary_type", ["freq", "scan"])
def test_a_one_atom_primary_that_is_neither_opt_nor_sp_is_refused(client, primary_type):
    """Only ``sp`` is exempt: the exemption is for what an atom really runs."""
    bundle = _species_bundle_from_reaction_atom(_reaction_payload("rotor_scan_5"))
    primary = bundle["conformers"][0]["primary_calculation"]
    primary["type"] = primary_type
    primary.pop("sp_result")
    resp = client.post(_SPECIES_URL, json=bundle)
    assert resp.status_code == 422, resp.text[:800]
    messages = " ".join(d["msg"] for d in resp.json()["detail"])
    assert f"must be 'opt', got '{primary_type}'" in messages


def test_the_atom_thermo_exports_to_a_contribution_bundle(client, db_session):
    """The export never reads a calculation, so the primary's type cannot matter.

    Pinned rather than assumed: deposit the sp-only atom, export its thermo,
    and require a valid bundle carrying that species.
    """
    from app.services.contribution_bundle_export import export_thermo_bundle

    resp = client.post(
        _SPECIES_URL, json=_species_bundle_from_reaction_atom(_reaction_payload("rotor_scan_5"))
    )
    assert resp.status_code == 201, resp.text[:1500]
    (thermo_id,) = _atom_state(db_session)["thermo_ids"]
    result = export_thermo_bundle(
        db_session,
        thermo_ids=[thermo_id],
        title="atom",
        summary="sp-only hydrogen atom",
        exporter_label="tests",
    )
    assert result.omissions == []
    assert result.bundle is not None
    (thermo,) = result.bundle.records.thermo_uploads
    assert thermo.species_entry.smiles == "[H]"


def _read_conformer_group(client, db_session) -> dict:
    (group,) = _atom_state(db_session)["groups"]
    resp = client.get(
        f"/api/v1/scientific/conformer-groups/{group.public_ref}?include=geometries"
    )
    assert resp.status_code == 200, resp.text[:800]
    return resp.json()["record"]


@pytest.mark.parametrize("route", ["species", "reaction"])
def test_sp_only_atom_reads_back_with_its_geometry_and_levels(client, db_session, route):
    """The read surface sees what a relabelled ``opt`` used to supply by accident.

    ``geometry_count`` and ``geometries`` come from output-geometry links, so
    an sp primary that carried none would read back as a conformer with no
    geometry at all.
    """
    reaction_payload = _reaction_payload("rotor_scan_5")
    if route == "species":
        resp = client.post(_SPECIES_URL, json=_species_bundle_from_reaction_atom(reaction_payload))
    else:
        resp = client.post(_REACTION_URL, json=_reshape_reaction_atom_to_sp_only(reaction_payload))
    assert resp.status_code == 201, resp.text[:1500]
    record = _read_conformer_group(client, db_session)
    evidence = record["evidence_summary"]
    assert evidence["evidence_coverage"]["opt"] == 0
    assert evidence["evidence_coverage"]["sp"] == 1
    assert evidence["geometry_count"] == 1
    assert record["available_sections"]["has_geometries"] is True
    (link,) = record["geometries"]
    assert link["geometry"]["natoms"] == 1
    assert link["geometry"]["role"] == "final"

    # The statmech's derived levels: no optimisation ran, so no geometry or
    # frequency level is claimed; the energy level is the single point's.
    (statmech,) = db_session.scalars(
        select(Statmech)
        .join(SpeciesEntry, SpeciesEntry.id == Statmech.species_entry_id)
        .join(Species, Species.id == SpeciesEntry.species_id)
        .where(Species.smiles == "[H]")
    ).all()
    read = client.get(f"/api/v1/scientific/statmech/{statmech.public_ref}")
    assert read.status_code == 200, read.text[:800]
    statmech_record = read.json()["record"]
    assert statmech_record["levels"]["geometry"] is None
    assert statmech_record["levels"]["frequency"] is None
    assert statmech_record["levels"]["energy"]["method"] == "dlpno-ccsd(t)-f12"
    assert statmech_record["evidence_summary"]["has_opt_calculation"] is False
    assert statmech_record["evidence_summary"]["has_sp_calculation"] is True


def _conformer_upload(smiles: str, multiplicity: int, xyz: str) -> dict:
    return {
        "species_entry": {"smiles": smiles, "charge": 0, "multiplicity": multiplicity},
        "geometry": {"xyz_text": xyz},
        "calculation": {
            "type": "sp",
            "software_release": {"name": "orca", "version": "6.0.0"},
            "level_of_theory": {"method": "dlpno-ccsd(t)-f12", "basis": "cc-pvtz-f12"},
            "sp_result": {"electronic_energy_hartree": -0.49994557},
        },
    }


def test_conformers_route_gives_an_atoms_sp_its_geometry_but_not_a_molecules(client, db_session):
    """The primitive route matches the bundle routes for an atom (#610), and only for an atom."""
    atom = client.post(
        "/api/v1/uploads/conformers", json=_conformer_upload("[H]", 2, "1\nH atom\nH 0.0 0.0 0.0")
    )
    assert atom.status_code == 201, atom.text[:800]
    assert _read_conformer_group(client, db_session)["evidence_summary"]["geometry_count"] == 1

    molecule = client.post(
        "/api/v1/uploads/conformers", json=_conformer_upload("[H][H]", 1, _H2_XYZ)
    )
    assert molecule.status_code == 201, molecule.text[:800]
    (h2_calc,) = db_session.scalars(
        select(Calculation)
        .join(SpeciesEntry, SpeciesEntry.id == Calculation.species_entry_id)
        .join(Species, Species.id == SpeciesEntry.species_id)
        .where(Species.smiles == "[H][H]")
    ).all()
    assert (
        db_session.scalars(
            select(CalculationOutputGeometry).where(
                CalculationOutputGeometry.calculation_id == h2_calc.id
            )
        ).all()
        == []
    )


def _second_sp(primary: dict) -> dict:
    """A further single point on the atom, at another level of theory."""
    extra = copy.deepcopy(primary)
    extra["key"] = "r_extra_sp"
    extra["level_of_theory"] = {"method": "ccsd(t)", "basis": "cc-pvqz"}
    extra.pop("artifacts", None)
    extra.pop("depends_on", None)
    return extra


def _dependency_edges_touching(db_session, calc_ids: list[int]) -> list:
    return db_session.scalars(
        select(CalculationDependency).where(
            CalculationDependency.child_calculation_id.in_(calc_ids)
            | CalculationDependency.parent_calculation_id.in_(calc_ids)
        )
    ).all()


def test_a_second_sp_on_the_atom_is_anchored_and_raises_no_edge_warning_species_route(
    client, db_session
):
    """The inferred ``single_point_on`` edge needs an ``opt`` parent; an atom has none.

    The bundle route skips the edge without a warning, which is right here:
    there is no optimisation for the edge to be missing from. The extra
    calculation is stored and anchored to the atom's conformer observation.
    """
    bundle = _species_bundle_from_reaction_atom(_reaction_payload("rotor_scan_5"))
    bundle["conformers"][0]["additional_calculations"] = [
        _second_sp(bundle["conformers"][0]["primary_calculation"])
    ]
    bundle["conformers"][0]["additional_calculations"][0]["sp_result"] = {
        "electronic_energy_hartree": -0.49999
    }
    resp = client.post(_SPECIES_URL, json=bundle)
    assert resp.status_code == 201, resp.text[:1500]
    assert "dependency_edge_not_inferred" not in {w["code"] for w in resp.json()["warnings"]}
    state = _atom_state(db_session)
    assert [c.type.value for c in state["calcs"]] == ["sp", "sp"]
    (observation,) = state["observations"]
    assert {c.conformer_observation_id for c in state["calcs"]} == {observation.id}
    assert _dependency_edges_touching(db_session, [c.id for c in state["calcs"]]) == []


def test_a_second_sp_on_the_atom_is_anchored_and_raises_no_edge_warning_reaction_route(
    client, db_session
):
    payload = _reshape_reaction_atom_to_sp_only(_reaction_payload("rotor_scan_5"))
    atom = _h_species(payload)
    (conformer,) = atom["conformers"]
    extra = _second_sp(conformer["calculation"])
    extra["geometry_key"] = conformer["geometry"]["key"]
    extra["conformer_key"] = conformer["key"]
    atom["calculations"].append(extra)
    resp = client.post(_REACTION_URL, json=payload)
    assert resp.status_code == 201, resp.text[:1500]
    assert "dependency_edge_not_inferred" not in {w["code"] for w in resp.json()["warnings"]}
    state = _atom_state(db_session)
    assert [c.type.value for c in state["calcs"]] == ["sp", "sp"]
    (observation,) = state["observations"]
    assert {c.conformer_observation_id for c in state["calcs"]} == {observation.id}
    assert _dependency_edges_touching(db_session, [c.id for c in state["calcs"]]) == []
