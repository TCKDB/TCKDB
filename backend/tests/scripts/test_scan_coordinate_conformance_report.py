"""The corpus report, driven against a database this suite populates itself.

Verify item 5 of the ADR 0020 conformance check brief: run the report
script against fixtures, never against a real deployment. This suite never
imports ``app.api.deps.SessionLocal`` against a live database -- it binds
the script's ``SessionLocal`` to this test's own transactional
``db_session``, the same technique
``tests/scripts/test_project_imaginary_modes.py`` uses, so the script's
``main()`` runs unmodified against rows this file builds and rolls them
back afterward.
"""

from __future__ import annotations

import contextlib
import importlib.util
import pathlib
import sys

import numpy as np
import pytest

from app.db.models.calculation import (
    Calculation,
    CalculationScanCoordinate,
    CalculationScanPoint,
    CalculationScanPointCoordinateValue,
)
from app.db.models.common import CalculationType, CoordinateUnit, ScanCoordinateKind
from app.db.models.geometry import Geometry, GeometryAtom
from tests.services.scientific_read._factories import make_species, make_species_entry

_REPORT = (
    pathlib.Path(__file__).parents[2] / "scripts" / "validation" / "scan_coordinate_conformance_report.py"
)


@pytest.fixture(scope="module")
def report_script():
    spec = importlib.util.spec_from_file_location("scan_coordinate_conformance_report", _REPORT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _SessionProxy:
    """The test's own session, wearing the shape ``main`` expects."""

    def __init__(self, session) -> None:
        self._session = session

    def __getattr__(self, name):
        return getattr(self._session, name)

    def __call__(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def begin(self):
        return contextlib.nullcontext()


def _run_main(report_script, monkeypatch, db_session, argv):
    import app.api.deps as deps

    monkeypatch.setattr(
        "sys.argv", ["scan_coordinate_conformance_report.py", *argv], raising=False
    )
    monkeypatch.setattr(deps, "SessionLocal", _SessionProxy(db_session))
    return report_script.main()


# ---------------------------------------------------------------------------
# Fixture builder: one real dihedral scan series, in the shape ADR 0020
# describes as the corpus's actual defect -- stored values are a sweep
# relative to the series' own start_value.
# ---------------------------------------------------------------------------


def _place_next_atom(a, b, c, *, bond_length, bond_angle_deg_, dihedral_deg_):
    import math

    theta = math.radians(bond_angle_deg_)
    phi = math.radians(-dihedral_deg_)
    bc = c - b
    bc_hat = bc / np.linalg.norm(bc)
    ab = b - a
    n = np.cross(ab, bc_hat)
    n_hat = n / np.linalg.norm(n)
    m = np.cross(n_hat, bc_hat)
    local = np.array(
        [
            -bond_length * math.cos(theta),
            bond_length * math.sin(theta) * math.cos(phi),
            bond_length * math.sin(theta) * math.sin(phi),
        ]
    )
    basis = np.column_stack([bc_hat, m, n_hat])
    return c + basis @ local


_A = np.array([0.0, 0.0, 0.0])
_B = np.array([1.50, 0.0, 0.0])
_C = np.array([2.50, 1.30, 0.0])

_geom_hash_counter = [0]


def _next_geom_hash() -> str:
    import hashlib

    _geom_hash_counter[0] += 1
    return hashlib.sha256(f"scan-report-test-{_geom_hash_counter[0]}".encode()).hexdigest()


def _make_legacy_relative_sweep_scan(
    session, *, start_value: float = 244.0, step: float = 8.0, n_points: int = 12
) -> Calculation:
    """A scan calculation shaped exactly like the 46 deposited series.

    Geometry at each point carries the true, absolute dihedral;
    ``coordinate_value`` stores ADR 0019's sweep relative to
    ``start_value`` -- the row the check is supposed to flag.
    """
    species = make_species(session)
    species_entry = make_species_entry(session, species)
    calc = Calculation(type=CalculationType.scan, species_entry_id=species_entry.id)
    session.add(calc)
    session.flush()

    coordinate = CalculationScanCoordinate(
        calculation_id=calc.id,
        coordinate_index=1,
        coordinate_kind=ScanCoordinateKind.dihedral,
        atom1_index=1,
        atom2_index=2,
        atom3_index=3,
        atom4_index=4,
        step_count=n_points,
        step_size=step,
        start_value=start_value,
        end_value=start_value + step * (n_points - 1),
        value_unit=CoordinateUnit.degree,
    )
    session.add(coordinate)
    session.flush()

    for i in range(n_points):
        relative_value = i * step
        absolute_dihedral = ((start_value + relative_value + 180.0) % 360.0) - 180.0
        d = _place_next_atom(
            _A, _B, _C, bond_length=1.09, bond_angle_deg_=109.5, dihedral_deg_=absolute_dihedral
        )
        geometry = Geometry(natoms=4, geom_hash=_next_geom_hash())
        session.add(geometry)
        session.flush()
        for idx, (element, coord) in enumerate(
            zip(["C", "C", "C", "H"], [_A, _B, _C, d], strict=True), start=1
        ):
            session.add(
                GeometryAtom(
                    geometry_id=geometry.id,
                    atom_index=idx,
                    element=element,
                    x=float(coord[0]),
                    y=float(coord[1]),
                    z=float(coord[2]),
                )
            )
        session.flush()

        point = CalculationScanPoint(calculation_id=calc.id, point_index=i + 1, geometry_id=geometry.id)
        session.add(point)
        session.flush()
        session.add(
            CalculationScanPointCoordinateValue(
                calculation_id=calc.id,
                point_index=i + 1,
                coordinate_index=1,
                coordinate_value=relative_value,
                value_unit=CoordinateUnit.degree,
            )
        )
    session.flush()
    return calc


def test_empty_scope_is_not_a_clean_report(db_session, report_script, monkeypatch, capsys):
    """Nothing in scope is a scope error, not a passing report."""
    code = _run_main(report_script, monkeypatch, db_session, ["--all"])
    assert code == report_script.EXIT_EMPTY_SCOPE
    assert "NOT CHECKED" in capsys.readouterr().out

    code = _run_main(report_script, monkeypatch, db_session, ["--all", "--allow-empty"])
    assert code == report_script.EXIT_OK


def test_legacy_relative_sweep_is_reported_nonconforming(db_session, report_script, monkeypatch, capsys):
    calc = _make_legacy_relative_sweep_scan(db_session)
    code = _run_main(report_script, monkeypatch, db_session, ["--all", "--verbose"])
    out = capsys.readouterr().out

    assert code == report_script.EXIT_NONCONFORMING
    assert calc.public_ref in out
    assert "classification=consistent_with_legacy_relative_axis" in out
    assert "implied constant:" in out
    assert "RESULT: NONCONFORMING" in out
    # Residual distribution is printed, not just the classification label.
    assert "residual distribution:" in out
    assert "not_applicable=0" in out
    assert "not_checkable=0" in out


def test_calculation_ref_scopes_to_one_calculation(db_session, report_script, monkeypatch, capsys):
    target = _make_legacy_relative_sweep_scan(db_session, start_value=100.0)
    _make_legacy_relative_sweep_scan(db_session, start_value=200.0)

    code = _run_main(
        report_script, monkeypatch, db_session, ["--calculation-ref", target.public_ref]
    )
    out = capsys.readouterr().out
    assert code == report_script.EXIT_NONCONFORMING
    assert "1 scan calculation(s)" in out
    assert target.public_ref in out


def test_the_report_writes_nothing(db_session, report_script, monkeypatch):
    """A diagnostic script must leave the corpus exactly as it found it."""
    _make_legacy_relative_sweep_scan(db_session)
    db_session.flush()
    _run_main(report_script, monkeypatch, db_session, ["--all"])
    assert not db_session.new
    assert not db_session.dirty
    assert not db_session.deleted
