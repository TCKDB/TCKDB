"""The QCSchema interchange demonstration, run on its committed fixtures.

``backend/scripts/validation/qcschema_interchange_report.py`` imports a Psi4
B3LYP/def2-TZVP water Hessian through the real adapter and upload route,
exports it back, and measures it against the Gaussian record at the same
geometry. These tests run it against the per-test database (the round trip
lives in a SAVEPOINT the script rolls back itself) and check three things:

* the round trip is exact -- and stops being exact when the adapter is
  perturbed. Two mutations are landed: the importer packing the Hessian in
  the transposed order, and the exporter unpacking it that way. Each turns
  its own check red, and only the checks it should;
* every measurement the validation doc reports is in the report;
* the frequency analysis is not vacuous: water has exactly three
  vibrations, on both sides, none imaginary.

The adapter is ``tckdb-qcschema`` from tckdb-adapters, pinned in
``backend/requirements-adapters.txt``, and it needs ``qcelemental`` (the
backend ``[dev]`` extra pins it). A workstation without either skips; CI
installs both, so there a missing one fails instead of skipping.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import shutil
import sys

import numpy as np
import pytest

from tests._ci_dependency import require_module
from tests.services._live_object_store import on_ci

_REPORT = pathlib.Path(__file__).parents[2] / "scripts" / "validation" / "qcschema_interchange_report.py"


def _load():
    name = "qcschema_interchange_report"
    module = sys.modules.get(name)
    if module is None:
        spec = importlib.util.spec_from_file_location(name, _REPORT)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return module


report_script = _load()


@pytest.fixture
def require_qcelemental() -> None:
    if report_script.qcelemental_available():
        return
    message = "qcelemental is not installed (pip install -e 'backend[dev]')"
    if on_ci():
        pytest.fail(message + " -- CI installs the [dev] extra, so this is a broken setup, not a skip")
    pytest.skip(message)


@pytest.fixture
def require_adapter(require_qcelemental) -> None:
    require_module("tckdb_qcschema", install="pip install --no-deps -r backend/requirements-adapters.txt")


def _transposed_packing(flat, natoms):
    """Lower triangle walked column by column: the transposed packing order."""
    dim = 3 * natoms
    values = [float(v) for v in np.asarray(flat, dtype=float).reshape(-1)]
    rows = [values[r * dim : (r + 1) * dim] for r in range(dim)]
    return [rows[r][c] for c in range(dim) for r in range(c, dim)]


def _transposed_unpacking(packed, natoms):
    """Places a packed triangle column by column: the exporter-side mirror mutation."""
    dim = 3 * natoms
    matrix = [[0.0] * dim for _ in range(dim)]
    index = 0
    for c in range(dim):
        for r in range(c, dim):
            matrix[r][c] = matrix[c][r] = float(packed[index])
            index += 1
    return [matrix[r][c] for r in range(dim) for c in range(dim)]


#: Every key the validation doc's tables read, by path.
REQUIRED_KEYS = (
    ("fixtures", "sha256"),
    ("fixtures", "psi4_environment_lockfile_sha256"),
    ("versions", "qcelemental"),
    ("versions", "tckdb_qcschema"),
    ("versions", "psi4"),
    ("versions", "gaussian"),
    ("round_trip", "checks"),
    ("round_trip", "hessian", "export_vs_document_differing_elements"),
    ("round_trip", "hessian", "document_max_asymmetry_hartree_bohr2"),
    ("round_trip", "geometry", "stored_max_abs_deviation_angstrom"),
    ("round_trip", "geometry", "exported_max_abs_deviation_bohr"),
    ("round_trip", "energy", "exported_return_energy_hartree"),
    ("round_trip", "loss_list", "lost"),
    ("round_trip", "loss_list", "changed"),
    ("round_trip", "loss_list", "lost_by_import_accounting"),
    ("round_trip", "hessian_reanalysis_status"),
    ("cross_program", "energy", "delta_hartree"),
    ("cross_program", "energy", "delta_kj_mol"),
    ("cross_program", "frequencies"),
    ("cross_program", "imaginary_mode_count", "psi4"),
    ("cross_program", "imaginary_mode_count", "gaussian"),
    ("cross_program", "zpe", "psi4_hartree"),
    ("cross_program", "zpe", "gaussian_recomputed_hartree"),
    ("cross_program", "zpe", "psi4_minus_gaussian_stored_hartree"),
    ("cross_program", "zpe", "gaussian_recomputed_minus_stored_hartree"),
    ("cross_program", "hessian_symmetry", "psi4_document_max_abs_asymmetry_hartree_bohr2"),
    ("cross_program", "hessian_elementwise_difference", "max_abs_hartree_bohr2"),
    ("cross_program", "geometry", "max_abs_difference_angstrom"),
    ("cross_program", "rigid_body_residue", "psi4", "lowest_six_unprojected_eigenvalues_cm1"),
    ("cross_program", "rigid_body_residue", "gaussian", "lowest_six_unprojected_eigenvalues_cm1"),
    ("cross_program", "rigid_body_residue", "psi4", "rigid_body_curvature_cm1"),
    ("cross_program", "rigid_body_residue", "frame_consistency_bound_cm1"),
)


def _lookup(report, path):
    node = report
    for key in path:
        assert isinstance(node, dict) and key in node, f"report is missing {'.'.join(path)}"
        node = node[key]
    return node


def test_round_trip_is_exact_and_every_measurement_is_reported(db_session, require_adapter):
    report = report_script.build_report(db_session, include_commit=False)

    checks = report["round_trip"]["checks"]
    assert list(checks) == list(report_script.ROUND_TRIP_CHECKS)
    assert report_script.failed_checks(report) == [], report["round_trip"]
    assert report["round_trip"]["passed"] is True

    for path in REQUIRED_KEYS:
        _lookup(report, path)

    hessian = report["round_trip"]["hessian"]
    assert hessian["stored_length"] == hessian["packed_length"] == 45
    # The only export/document differences are the document's own sub-ulp
    # asymmetry, which packed storage cannot hold.
    assert hessian["export_vs_document_differing_elements"] == hessian["document_asymmetric_pairs"] == 4
    assert hessian["export_vs_document_max_abs_difference_hartree_bohr2"] == hessian[
        "document_max_asymmetry_hartree_bohr2"
    ]
    assert hessian["document_max_asymmetry_hartree_bohr2"] < 1e-15

    assert report["round_trip"]["energy"]["exported_return_energy_hartree"] is None
    assert report["round_trip"]["hessian_reanalysis_status"] == "frequency_list_missing"
    assert report["round_trip"]["loss_list"]["lost"] == sorted(report_script.EXPECTED_LOST_PATHS)
    # Every loss is named by the import report, and the Hessian
    # document's energy is named retained_only rather than dropped silently.
    accounting = report["round_trip"]["loss_list"]["lost_by_import_accounting"]
    assert accounting["not_named_by_import_report"] == []
    assert "properties.return_energy" in accounting["reported_retained_only"]
    assert report["round_trip"]["energy"]["import_report_bucket"] == "retained_only"
    assert report["round_trip"]["exported_frame"] == {
        "molecule": {"fix_com": True, "fix_orientation": True},
        "input_data.molecule": {"fix_com": True, "fix_orientation": True},
    }

    # Non-vacuity: water is non-linear, so 3N - 6 = 3 vibrations per side.
    cross = report["cross_program"]
    assert cross["vibrational_mode_count"] == {"psi4": 3, "gaussian": 3}
    assert [mode["mode"] for mode in cross["frequencies"]] == [1, 2, 3]
    for mode in cross["frequencies"]:
        assert 1000.0 < mode["gaussian_cm1"] < 4500.0
        assert 1000.0 < mode["psi4_cm1"] < 4500.0
    assert cross["imaginary_mode_count"] == {"psi4": 0, "gaussian": 0}
    for side in ("psi4", "gaussian"):
        residue = cross["rigid_body_residue"][side]
        assert len(residue["lowest_six_unprojected_eigenvalues_cm1"]) == 6
        assert len(residue["rigid_body_curvature_cm1"]) == residue["rigid_body_dimension"] == 6


def test_report_is_byte_stable_and_include_commit_false_drops_only_the_commit(db_session, require_adapter):
    first = report_script.build_report(db_session, include_commit=True)
    second = report_script.build_report(db_session, include_commit=False)
    assert "tckdb_commit" in first
    assert "tckdb_commit" not in second
    first.pop("tckdb_commit")
    assert report_script.canonical_json(first) == report_script.canonical_json(second)


def test_importer_packing_in_transposed_order_goes_red(db_session, monkeypatch, require_adapter):
    with report_script.adapter_modules() as adapter:
        monkeypatch.setattr(adapter.mapping, "pack_lower_triangle", _transposed_packing)
        report = report_script.build_report(db_session, adapter=adapter, include_commit=False)

    failed = report_script.failed_checks(report)
    assert "stored_triangle_exact" in failed
    assert "exported_hessian_exact_after_pack_unpack" in failed
    # Geometry, identity and the loss list do not depend on packing order.
    assert "stored_geometry_within_bound" not in failed
    assert "identity_preserved" not in failed


def test_exporter_unpacking_in_transposed_order_goes_red(db_session, monkeypatch, require_adapter):
    with report_script.adapter_modules() as adapter:
        monkeypatch.setattr(adapter.exporter, "unpack_lower_triangle", _transposed_unpacking)
        report = report_script.build_report(db_session, adapter=adapter, include_commit=False)

    # The stored triangle is still right; only the export is wrong.
    assert report_script.failed_checks(report) == ["exported_hessian_exact_after_pack_unpack"]


def test_nothing_is_left_in_the_database(db_session, require_adapter):
    from sqlalchemy import func, select

    from app.db.models.app_user import AppUser
    from app.db.models.calculation import Calculation

    def counts():
        return (
            db_session.scalar(select(func.count()).select_from(Calculation)),
            db_session.scalar(select(func.count()).select_from(AppUser)),
        )

    before = counts()
    report_script.build_report(db_session, include_commit=False)
    db_session.expire_all()
    assert counts() == before


def test_a_fixture_that_disagrees_with_meta_is_reported(tmp_path):
    copy = tmp_path / "case"
    shutil.copytree(report_script.FIXTURE_DIR, copy)
    reference = copy / report_script.REFERENCE_NAME
    data = json.loads(reference.read_text())
    data["zpe_hartree"] = 0.02
    reference.write_text(json.dumps(data))

    fixtures = report_script.load_fixtures(copy)
    assert fixtures.digest_mismatches == [report_script.REFERENCE_NAME]
    assert report_script.load_fixtures().digest_mismatches == []


def test_the_adapter_import_leaves_no_trace(require_adapter):
    before_path = list(sys.path)
    before = {name: module for name, module in sys.modules.items() if name.split(".")[0] in {"tckdb_client", "tckdb_qcschema"}}
    with report_script.adapter_modules() as adapter:
        assert pathlib.Path(adapter.client.__file__).is_relative_to(report_script.REPO_ROOT / "clients" / "python")
    after = {name: module for name, module in sys.modules.items() if name.split(".")[0] in {"tckdb_client", "tckdb_qcschema"}}
    assert sys.path == before_path
    assert after == before


def _seed_level(db_session, *, method: str = "b3lyp", basis: str = "def2-tzvp"):
    from app.schemas.fragments.refs import LevelOfTheoryRef
    from app.services.calculation_resolution import resolve_level_of_theory_ref

    row = resolve_level_of_theory_ref(db_session, LevelOfTheoryRef(method=method, basis=basis))
    db_session.flush()
    return row


def _report_and_exported_level(db_session, monkeypatch):
    """The report, and the method/basis spelling its export carried."""
    seen = {}
    with report_script.adapter_modules() as adapter:
        original = adapter.exporter.export_calculation

        def capture(client, calculation_id, *args, **kwargs):
            document = original(client, calculation_id, *args, **kwargs)
            model = document["input_data"]["specification"]["model"]
            seen.update(method=model["method"], basis=model["basis"])
            return document

        monkeypatch.setattr(adapter.exporter, "export_calculation", capture)
        report = report_script.build_report(db_session, adapter=adapter, include_commit=False)
    return report, seen


@pytest.mark.parametrize(
    ("method", "basis"),
    [
        ("b3lyp", "def2-TZVP"),
        ("b3lyp", "Def2TZVP"),
        ("b3lyp", "DEF2TZVP"),
        ("B3LYP", "def2-tzvp"),
        ("B3lyp", "def2-tzvp"),
        ("B3LYP", "Def2TZVP"),
    ],
)
def test_round_trip_passes_when_the_level_exists_under_another_spelling(
    db_session, monkeypatch, require_adapter, method, basis
):
    """The shared level-of-theory row keeps its first spelling; the export reads it back."""
    seeded = _seed_level(db_session, method=method, basis=basis)

    report, exported = _report_and_exported_level(db_session, monkeypatch)

    assert report_script.failed_checks(report) == [], report["round_trip"]["loss_list"]
    assert report["round_trip"]["passed"] is True
    # The seeded row was reused: the export carries its spelling, not the fixture's.
    assert exported == {"method": method, "basis": basis}
    assert (seeded.method, seeded.basis) == (method, basis)


def test_report_is_the_same_on_an_empty_and_a_populated_database(db_session, monkeypatch, require_adapter):
    empty, empty_level = _report_and_exported_level(db_session, monkeypatch)
    _seed_level(db_session, method="B3LYP", basis="Def2TZVP")
    populated, populated_level = _report_and_exported_level(db_session, monkeypatch)

    # The populated run reused the seeded row; the spelling is not written into the report.
    assert empty_level == {"method": "b3lyp", "basis": "def2-tzvp"}
    assert populated_level == {"method": "B3LYP", "basis": "Def2TZVP"}
    assert report_script.canonical_json(populated) == report_script.canonical_json(empty)


def test_round_trip_passes_when_the_software_name_differs_in_case(db_session, require_adapter):
    from app.db.models.software import Software

    db_session.add(Software(name="PSI4"))
    db_session.flush()
    report = report_script.build_report(db_session, include_commit=False)
    assert report["round_trip"]["passed"] is True


@pytest.mark.parametrize(
    ("path", "wrong"),
    [
        ("basis", "def2-qzvp"),
        ("method", "ub3lyp"),
        ("method", "b3lyp5"),
    ],
)
def test_a_different_basis_or_method_still_fails_the_round_trip(db_session, monkeypatch, require_adapter, path, wrong):
    """A real change of basis or method, not a spelling, is an unexpected change."""
    with report_script.adapter_modules() as adapter:
        original = adapter.exporter.export_calculation

        def wrong_value(client, calculation_id, *args, **kwargs):
            document = original(client, calculation_id, *args, **kwargs)
            document["input_data"]["specification"]["model"][path] = wrong
            return document

        monkeypatch.setattr(adapter.exporter, "export_calculation", wrong_value)
        report = report_script.build_report(db_session, adapter=adapter, include_commit=False)

    assert report_script.failed_checks(report) == ["loss_list_complete"]
    assert report["round_trip"]["loss_list"]["unexpected_changed"] == [f"input_data.specification.model.{path}"]
