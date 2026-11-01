"""A scan whose stored coordinate values disagree with its own geometries earns a warning (#835, ADR 0020).

``scan_point_coordinate_disagrees_with_geometry`` is the deposit-time form of the conformance check in
``app.services.scan_coordinate_conformance``. The pair of fixture tests is the proof that it speaks when it should and
is silent when it should be: the real payloads under ``tests/fixtures/arc_runs`` still hold ADR 0019's superseded
relative-sweep convention (they are kept in it on purpose), and the QCSchema torsion-drive fixtures hold the coordinate
itself. The route tests prove the check runs on every route that persists a scan; the rounding tests prove a value
that is merely rounded is not called a disagreement, and a small constant offset is not called a relative sweep.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy import select
from tckdb_schemas.deposit_warning_messages import render_message

from app.db.models.calculation import Calculation
from app.db.models.common import CalculationType, SubmissionRecordType
from app.db.models.deposit_warning import DepositWarning
from app.services.scan_coordinate_conformance import dihedral_deg
from tests.api.test_api_auth import _hydrogen_conformer_payload

CODE = "scan_point_coordinate_disagrees_with_geometry"
_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_ARC_SCAN_1 = (
    _FIXTURES
    / "arc_runs"
    / "scan_1"
    / "tckdb_payloads"
    / "computed_reaction"
    / "rmg_rxn_1142_r1_sbr-O-sbr-rmg_rxn_1142_r2_CCCC-rmg_rxn_1142_p1_sbr-OH-sbr-rmg_rxn_1142_p2_sbr-CH2-sbrCCC"
)
_CONFORMERS = "/api/v1/uploads/conformers"
_SOFTWARE = {"name": "Gaussian", "version": "16"}
_LOT = {"method": "B3LYP", "basis": "6-31G(d)"}
_H2_XYZ = "2\nH2\nH 0.0 0.0 0.0\nH 0.0 0.0 0.74"


def _warnings(response) -> list[dict]:
    assert response.status_code in (200, 201), response.text[:800]
    return [w for w in response.json()["warnings"] if w["code"] == CODE]


# ---------------------------------------------------------------------------
# scan builders: the stored values are measured from the point's own geometry, plus a chosen offset
# ---------------------------------------------------------------------------


def _atoms(xyz_text: str) -> tuple[list[str], list[list[str]]]:
    lines = xyz_text.split("\n")
    return lines[:2], [line.split() for line in lines[2:]]


def _xyz(header: list[str], atoms: list[list[str]]) -> str:
    return "\n".join([*header, *(" ".join(row) for row in atoms)])


def _coords(atoms: list[list[str]], index: int) -> np.ndarray:
    return np.array([float(x) for x in atoms[index - 1][1:4]])


def _scan_result(points: list[tuple[str, float]], coordinate: dict, *, decimals: int | None, offset: float) -> dict:
    """``points`` are ``(xyz_text, measured_value)``; the stored value is the measurement plus ``offset``."""

    def stored(value: float) -> float:
        value += offset
        return round(value, decimals) if decimals is not None else value

    return {
        "dimension": 1,
        "is_relaxed": False,
        "coordinates": [{"coordinate_index": 1, "step_count": len(points), **coordinate}],
        "points": [
            {
                "point_index": i + 1,
                "electronic_energy_hartree": -1.0 - 1e-3 * i,
                "geometry": {"xyz_text": xyz},
                "coordinate_values": [
                    {"coordinate_index": 1, "coordinate_value": stored(value), "value_unit": coordinate["value_unit"]}
                ],
            }
            for i, (xyz, value) in enumerate(points)
        ],
    }


def bond_scan(xyz_text: str, a: int, b: int, *, offset: float, decimals: int | None = None) -> dict:
    """Three points that stretch atom ``b`` away from atom ``a``; the coordinate is the a-b distance in angstrom."""
    header, atoms = _atoms(xyz_text)
    pa, pb = _coords(atoms, a), _coords(atoms, b)
    unit = (pb - pa) / np.linalg.norm(pb - pa)
    points = []
    for delta in (-0.0512883, 0.0007141, 0.0487137):
        moved = [list(row) for row in atoms]
        moved[b - 1] = [moved[b - 1][0], *(f"{x:.6f}" for x in pb + unit * delta)]
        distance = float(np.linalg.norm(_coords(moved, b) - pa))
        points.append((_xyz(header, moved), distance))
    coordinate = {
        "coordinate_kind": "bond",
        "atom1_index": a,
        "atom2_index": b,
        "step_size": 0.05,
        "start_value": points[0][1],
        "end_value": points[-1][1],
        "value_unit": "angstrom",
    }
    return _scan_result(points, coordinate, decimals=decimals, offset=offset)


def _hooh_xyz(phi_deg: float) -> str:
    phi = math.radians(phi_deg)
    s, c = math.sin(math.radians(100.0)), math.cos(math.radians(100.0))
    h1 = (0.97 * c, 0.97 * s, 0.0)
    h2 = (1.45 - 0.97 * c, 0.97 * s * math.cos(phi), 0.97 * s * math.sin(phi))
    rows = [
        ["H", *(f"{x:.6f}" for x in h1)],
        ["O", "0.000000", "0.000000", "0.000000"],
        ["O", "1.450000", "0.000000", "0.000000"],
        ["H", *(f"{x:.6f}" for x in h2)],
    ]
    return _xyz(["4", "HOOH"], rows)


def dihedral_scan(*, offset: float, decimals: int | None) -> dict:
    """A HOOH torsion scan; stored values are the dihedral measured on each point's geometry.

    The angles sit just under half a step of the last stored decimal from a round number, so rounding them moves
    each value by almost the largest amount rounding can: a test that is not fooled by values that happen to be round.
    """
    step = 10.0 ** -(decimals if decimals is not None else 2)
    points = []
    for phi in (60.0 + 0.46 * step, 90.0 + 0.49 * step, 120.0 + 0.43 * step):
        xyz = _hooh_xyz(phi)
        _, atoms = _atoms(xyz)
        value = dihedral_deg(*(_coords(atoms, i) for i in (1, 2, 3, 4)))
        points.append((xyz, value))
    coordinate = {
        "coordinate_kind": "dihedral",
        "atom1_index": 1,
        "atom2_index": 2,
        "atom3_index": 3,
        "atom4_index": 4,
        "step_size": 30.0,
        "start_value": 60.0,
        "end_value": 120.0,
        "value_unit": "degree",
    }
    return _scan_result(points, coordinate, decimals=decimals, offset=offset)


def _hooh_conformer(scan: dict) -> dict:
    payload = _hydrogen_conformer_payload()
    payload["species_entry"] = {"smiles": "OO", "charge": 0, "multiplicity": 1}
    payload["geometry"] = {"xyz_text": _hooh_xyz(90.0)}
    payload["calculation"]["type"] = "scan"
    payload["calculation"]["scan_result"] = scan
    return payload


def h2_scan_conformer(offset: float, *, decimals: int | None = None) -> dict:
    payload = _hydrogen_conformer_payload()
    payload["species_entry"] = {"smiles": "[H][H]", "charge": 0, "multiplicity": 1}
    payload["geometry"] = {"xyz_text": _H2_XYZ}
    payload["calculation"]["type"] = "scan"
    payload["calculation"]["scan_result"] = bond_scan(_H2_XYZ, 1, 2, offset=offset, decimals=decimals)
    return payload


# ---------------------------------------------------------------------------
# fixtures: old convention fires, the coordinate itself is silent
# ---------------------------------------------------------------------------


def test_a_legacy_arc_scan_fixture_earns_the_warning(client) -> None:
    meta = json.loads(_ARC_SCAN_1.with_suffix(".meta.json").read_text())
    payload = json.loads(_ARC_SCAN_1.with_suffix(".payload.json").read_text())
    endpoint = meta["endpoint"] if meta["endpoint"].startswith("/api/") else f"/api/v1{meta['endpoint']}"
    found = _warnings(client.post(endpoint, json=payload))
    assert found, "the old-convention scan fixture should earn the warning"
    for warning in found:
        context = warning["context"]
        assert context["pattern"] == "consistent_with_legacy_relative_axis"
        assert context["coordinate_kind"] == "dihedral"
        assert context["n_points_disagreeing"] >= 1
        assert context["max_residual"] > context["tolerance"] > 0
        assert warning["message"] == render_message(CODE, context)
        assert "measured from the scan's start" in warning["message"], "the pattern's own phrase"
    assert sorted(w["context"]["scan_number"] for w in found) == list(range(1, len(found) + 1))

    session = client._db_session
    scan_ids = set(session.scalars(select(Calculation.id).where(Calculation.type == CalculationType.scan)))
    rows = session.scalars(select(DepositWarning).where(DepositWarning.code == CODE)).all()
    assert len(rows) == len(found)
    for row in rows:
        assert row.record_type is SubmissionRecordType.calculation
        assert row.record_id in scan_ids


def test_a_qcschema_scan_fixture_earns_none(client, monkeypatch) -> None:
    monkeypatch.setattr("app.services.artifact_persistence.store_artifact", lambda content, sha256: f"s3://t/{sha256}")
    payload = json.loads((_FIXTURES / "qcschema_scan" / "torsiondrive_v2" / "payload.json").read_text())
    response = client.post("/api/v1/uploads/computed-species", json=payload, headers={"Idempotency-Key": "scan-warning-negative"})
    assert response.status_code == 201, response.text[:800]
    assert _warnings(response) == []
    assert client._db_session.scalars(select(Calculation).where(Calculation.type == CalculationType.scan)).first()


# ---------------------------------------------------------------------------
# what the warning says, and what it must not say
# ---------------------------------------------------------------------------


def test_a_scan_with_offset_values_warns_with_the_numbers_it_measured(client) -> None:
    response = client.post(_CONFORMERS, json=h2_scan_conformer(0.5))
    (warning,) = _warnings(response)
    context = warning["context"]
    assert context["scan_number"] == 1
    assert context["coordinate_index"] == 1
    assert context["coordinate_kind"] == "bond"
    assert context["pattern"] == "consistent_with_legacy_relative_axis"
    assert context["n_points_disagreeing"] == context["n_points_checked"] == 3
    assert context["max_residual"] == 0.5
    assert 0 < context["tolerance"] < 0.1
    assert warning["message"] == render_message(CODE, context)
    assert "scan 1 in this deposit" in warning["message"]


def test_a_scan_whose_values_match_its_geometries_earns_none(client) -> None:
    assert _warnings(client.post(_CONFORMERS, json=h2_scan_conformer(0.0))) == []


@pytest.mark.parametrize("decimals", [2, 1])
def test_a_dihedral_rounded_to_few_decimals_is_not_a_disagreement(client, decimals) -> None:
    """Values that are the exact measurements, rounded, differ from them by up to half a step of the last decimal."""
    scan = dihedral_scan(offset=0.0, decimals=decimals)
    for point in scan["points"]:
        _, atoms = _atoms(point["geometry"]["xyz_text"])
        exact = dihedral_deg(*(_coords(atoms, i) for i in (1, 2, 3, 4)))
        stored = point["coordinate_values"][0]["coordinate_value"]
        assert 0.4 * 10.0**-decimals < abs(stored - exact) <= 0.5 * 10.0**-decimals, "rounding must matter here"
    assert _warnings(client.post(_CONFORMERS, json=_hooh_conformer(scan))) == []


def test_a_bond_rounded_to_three_decimals_is_not_a_disagreement(client) -> None:
    payload = h2_scan_conformer(0.0, decimals=3)
    values = [p["coordinate_values"][0]["coordinate_value"] for p in payload["calculation"]["scan_result"]["points"]]
    assert all(round(v, 3) == v for v in values)
    for point in payload["calculation"]["scan_result"]["points"]:
        _, atoms = _atoms(point["geometry"]["xyz_text"])
        exact = float(np.linalg.norm(_coords(atoms, 2) - _coords(atoms, 1)))
        stored = point["coordinate_values"][0]["coordinate_value"]
        assert abs(stored - exact) > 2e-4, "rounding must move the value by more than the geometry's own allowance"
    assert _warnings(client.post(_CONFORMERS, json=payload)) == []


def test_a_small_constant_offset_is_not_called_a_relative_sweep(client) -> None:
    """0.02 degrees is above the tolerance but far below a scan's start value: it is not the relative-sweep shape."""
    (warning,) = _warnings(client.post(_CONFORMERS, json=_hooh_conformer(dihedral_scan(offset=0.02, decimals=None))))
    context = warning["context"]
    assert context["pattern"] == "small_constant_offset"
    assert context["n_points_disagreeing"] == 3
    assert "rounding or a small difference" in warning["message"]
    assert "relative sweep" not in warning["message"]
    assert warning["message"] == render_message(CODE, context)


def test_a_large_constant_offset_is_still_the_relative_sweep(client) -> None:
    (warning,) = _warnings(client.post(_CONFORMERS, json=_hooh_conformer(dihedral_scan(offset=47.0, decimals=None))))
    assert warning["context"]["pattern"] == "consistent_with_legacy_relative_axis"


# ---------------------------------------------------------------------------
# every route that persists a scan runs the check (behaviour, not source text)
# ---------------------------------------------------------------------------


def _scan_calc(scan: dict) -> dict:
    return {"type": "scan", "software_release": _SOFTWARE, "level_of_theory": _LOT, "scan_result": scan}


def _route_conformer():
    return _CONFORMERS, h2_scan_conformer(0.5)


def _route_computed_species():
    scan = bond_scan(_H2_XYZ, 1, 2, offset=0.5)
    opt = {"key": "opt0", "type": "opt", "software_release": _SOFTWARE, "level_of_theory": _LOT, "opt_result": {"converged": True}}
    payload = {
        "species_entry": {"smiles": "[H][H]", "charge": 0, "multiplicity": 1},
        "conformers": [
            {
                "key": "c0",
                "geometry": {"xyz_text": _H2_XYZ},
                "primary_calculation": opt,
                "additional_calculations": [{"key": "scan0", **_scan_calc(scan)}],
            }
        ],
    }
    return "/api/v1/uploads/computed-species", payload


def _route_computed_reaction():
    from tests.workflows.test_computed_reaction_upload import _XYZ_CH4, _payload_with_ch4_scan

    payload = _payload_with_ch4_scan()
    calcs = payload["species"][2]["calculations"]
    index = next(i for i, c in enumerate(calcs) if c["type"] == "scan")
    calcs[index] = {**calcs[index], "scan_result": bond_scan(_XYZ_CH4, 1, 2, offset=0.5)}
    return "/api/v1/uploads/computed-reaction", payload


def _inline(scan: dict) -> list[dict]:
    return [{"key": "scan0", "calculation": _scan_calc(scan)}]


_H2_ENTRY = {"smiles": "[H][H]", "charge": 0, "multiplicity": 1}


def _route_thermo():
    payload = {
        "enthalpy_reference_kind": "formation_298k",
        "species_entry": dict(_H2_ENTRY),
        "scientific_origin": "computed",
        "h298_kj_mol": 0.0,
        "calculations": _inline(bond_scan(_H2_XYZ, 1, 2, offset=0.5)),
    }
    return "/api/v1/uploads/thermo", payload


def _route_statmech():
    payload = {
        "species_entry": dict(_H2_ENTRY),
        "scientific_origin": "computed",
        "statmech_treatment": "rrho",
        "external_symmetry": 2,
        "calculations": _inline(bond_scan(_H2_XYZ, 1, 2, offset=0.5)),
    }
    return "/api/v1/uploads/statmech", payload


def _route_transport():
    payload = {
        "species_entry": dict(_H2_ENTRY),
        "scientific_origin": "computed",
        "sigma_angstrom": 2.05,
        "epsilon_over_k_k": 145.0,
        "dipole_debye": 0.0,
        "polarizability_angstrom3": 0.667,
        "rotational_relaxation": 0.0,
        "calculations": _inline(bond_scan(_H2_XYZ, 1, 2, offset=0.5)),
    }
    return "/api/v1/uploads/transport", payload


def _route_transition_state():
    from tests.api.test_api_uploads import _transition_state_payload

    payload = _transition_state_payload(n_imag=1, imag_freq_cm1=-1500.0)
    scan = bond_scan(payload["geometry"]["xyz_text"], 1, 2, offset=0.5)
    payload["additional_calculations"].append(_scan_calc(scan))
    return "/api/v1/uploads/transition-states", payload


def test_a_network_calculation_cannot_carry_a_scan_result(client) -> None:
    """The one persisting route with no scan: its calculation schema forbids ``scan_result``, so there is nothing to run."""
    from tests.api.test_api_network_reads import _XYZ_ETHYL, _pdep_payload

    payload = _pdep_payload()
    scan = bond_scan(_XYZ_ETHYL, 1, 2, offset=0.5)
    payload["species"][0]["calculations"].append({"key": "ethyl_scan", "geometry_key": "ethyl_geom", **_scan_calc(scan)})
    response = client.post("/api/v1/uploads/networks/pdep", json=payload)
    assert response.status_code == 422, response.text[:400]
    assert "extra_forbidden" in response.text and "scan_result" in response.text


ROUTES = {
    "conformer": _route_conformer,
    "computed_species": _route_computed_species,
    "computed_reaction": _route_computed_reaction,
    "thermo": _route_thermo,
    "statmech": _route_statmech,
    "transport": _route_transport,
    "transition_state": _route_transition_state,
}


@pytest.mark.parametrize("route", list(ROUTES))
def test_every_route_that_persists_a_scan_runs_the_check(client, route) -> None:
    url, payload = ROUTES[route]()
    response = client.post(url, json=payload)
    (warning,) = _warnings(response)
    assert warning["context"]["scan_number"] == 1
    assert warning["context"]["max_residual"] == pytest.approx(0.5, abs=1e-5)

    session = client._db_session
    scan_ids = set(session.scalars(select(Calculation.id).where(Calculation.type == CalculationType.scan)))
    assert len(scan_ids) == 1
    (row,) = session.scalars(select(DepositWarning).where(DepositWarning.code == CODE)).all()
    assert row.record_type is SubmissionRecordType.calculation
    assert row.record_id in scan_ids


def test_every_workflow_that_reports_named_composite_deposits_reports_scan_coordinates_too() -> None:
    """A cheap tripwire for a new route; the behavioural proof is the parametrised test above."""
    workflows = Path(__file__).resolve().parents[2] / "app" / "workflows"
    reporting = {p.name for p in workflows.glob("*.py") if "collect_named_composite_deposit_warnings(" in p.read_text()}
    scanning = {p.name for p in workflows.glob("*.py") if "collect_scan_coordinate_warnings(" in p.read_text()}
    assert len(reporting) >= 8, sorted(reporting)
    # network_pdep reports composite deposits but its calculation schema forbids a scan_result (tested above).
    assert reporting - {"network_pdep.py"} <= scanning, sorted(reporting - scanning)
