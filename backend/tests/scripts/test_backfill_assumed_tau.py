"""``backfill_assumed_tau.py``: dry-run, apply, idempotence, and the guard.

Mirrors the dynamic-import / ``_SessionProxy`` pattern
``test_project_imaginary_modes.py`` uses to run an ops script's ``main()``
against the pytest transaction, so nothing here writes to a real
database and every write rolls back with the enclosing test.
"""

from __future__ import annotations

import contextlib
import importlib.util
import pathlib
import sys

import pytest
from tckdb_schemas.stationary_point import (
    TAU_ANALYTIC_DEFAULT_CM1,
    TAU_FINITE_DIFFERENCE_ENERGY_CM1,
    TAU_FINITE_DIFFERENCE_GRADIENT_CM1,
    TAU_PROTOCOL_NOT_RECORDED_CM1,
)

from app.db.models.calculation import CalculationFreqResult, CalculationParameter, CalculationParameterVocab
from app.db.models.common import CalculationType
from tests.services.scientific_read._factories import (
    attach_freq_result,
    make_calculation,
    make_lot,
    make_software_release,
    make_species,
    make_species_entry,
    next_inchi_key,
)

_SCRIPT = pathlib.Path(__file__).parents[2] / "scripts" / "ops" / "backfill_assumed_tau.py"


@pytest.fixture(scope="module")
def backfill():
    spec = importlib.util.spec_from_file_location("backfill_assumed_tau", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _SessionProxy:
    """The test's own session, wearing the shape ``main`` expects.

    Same device ``test_project_imaginary_modes.py`` uses: ``main()`` does
    ``with SessionLocal() as session:``, so the proxy has to behave as
    both the factory (callable, returns itself) and the context manager.
    """

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


def _run_main(backfill, monkeypatch, db_session, argv, *, db_name: str = "tckdb_test_backfill"):
    import app.api.deps as deps
    from app.api.config import settings

    monkeypatch.setattr("sys.argv", ["backfill_assumed_tau.py", *argv], raising=False)
    monkeypatch.setattr(deps, "SessionLocal", _SessionProxy(db_session))
    monkeypatch.setattr(settings, "db_name", db_name)
    return backfill.main()


def _seed_parameter(session, *, calculation, canonical_key, canonical_value):
    if session.get(CalculationParameterVocab, canonical_key) is None:
        session.add(CalculationParameterVocab(canonical_key=canonical_key))
        session.flush()
    session.add(
        CalculationParameter(
            calculation_id=calculation.id,
            raw_key=canonical_key,
            raw_value=canonical_value,
            canonical_key=canonical_key,
            canonical_value=canonical_value,
        )
    )
    session.flush()


@pytest.fixture
def scope(db_session):
    """Four never-judged rows plus one already-judged row, out of scope."""
    entry = make_species_entry(
        db_session, make_species(db_session, inchi_key=next_inchi_key("BKFLTAU"))
    )

    gaussian = make_software_release(db_session, name="Gaussian", version="16")
    molpro = make_software_release(db_session, name="Molpro", version="2022.1")

    lot_b3lyp = make_lot(db_session, method="b3lyp-bkfl", basis="def2tzvp")
    lot_ccsd_t = make_lot(db_session, method="ccsd(t)-bkfl", basis="def2tzvp")

    # A: Gaussian + b3lyp, nothing recorded -> assumed_analytic_default.
    calc_a = make_calculation(
        db_session,
        type=CalculationType.freq,
        species_entry_id=entry.id,
        lot_id=lot_b3lyp.id,
        software_release_id=gaussian.id,
    )
    row_a = attach_freq_result(db_session, calculation=calc_a, frequencies_cm1=[])

    # B: Gaussian + CCSD(T), nothing recorded -> assumed_finite_difference_energy.
    calc_b = make_calculation(
        db_session,
        type=CalculationType.freq,
        species_entry_id=entry.id,
        lot_id=lot_ccsd_t.id,
        software_release_id=gaussian.id,
    )
    row_b = attach_freq_result(db_session, calculation=calc_b, frequencies_cm1=[])

    # C: Molpro + b3lyp -- not in the assumption table -> stays protocol_not_recorded.
    calc_c = make_calculation(
        db_session,
        type=CalculationType.freq,
        species_entry_id=entry.id,
        lot_id=lot_b3lyp.id,
        software_release_id=molpro.id,
    )
    row_c = attach_freq_result(db_session, calculation=calc_c, frequencies_cm1=[])

    # D: Gaussian + b3lyp, but freq.hessian_method IS recorded (as
    # finite_difference_gradient) -- the recorded parameter must win over
    # what the table would otherwise assume for b3lyp (analytic).
    calc_d = make_calculation(
        db_session,
        type=CalculationType.freq,
        species_entry_id=entry.id,
        lot_id=lot_b3lyp.id,
        software_release_id=gaussian.id,
    )
    row_d = attach_freq_result(db_session, calculation=calc_d, frequencies_cm1=[])
    _seed_parameter(
        db_session,
        calculation=calc_d,
        canonical_key="freq.hessian_method",
        canonical_value="finite_difference_gradient",
    )

    # E: already judged (a modern, post-ADR-0012 record) -- out of scope.
    calc_e = make_calculation(
        db_session,
        type=CalculationType.freq,
        species_entry_id=entry.id,
        lot_id=lot_b3lyp.id,
        software_release_id=gaussian.id,
    )
    row_e = attach_freq_result(
        db_session,
        calculation=calc_e,
        frequencies_cm1=[-20.0],
        reaction_coordinate_mode_index=1,
        imaginary_mode_tau_cm1=99.0,
        imaginary_mode_tau_basis="analytic_tight",
        imaginary_mode_structural_flag=False,
    )

    db_session.flush()
    return {"a": row_a, "b": row_b, "c": row_c, "d": row_d, "e": row_e}


def _reload(db_session, row: CalculationFreqResult) -> CalculationFreqResult:
    """Fetch the current state of ``row`` from the shared session.

    Deliberately *not* ``session.expire()`` + refetch: this session is
    ``expire_on_commit=False`` (see the ``client`` fixture), and the
    script under test runs in the same session/identity map as
    ``db_session`` via ``_SessionProxy``, so a write the script makes
    mutates this exact object in place. ``get()`` alone is enough to
    read it back, and avoids expiring an instance the dry-run path
    never touched (nothing to refresh from, since nothing was flushed).
    """
    return db_session.get(CalculationFreqResult, row.calculation_id)


def test_dry_run_changes_nothing(backfill, monkeypatch, db_session, scope, capsys):
    rc = _run_main(backfill, monkeypatch, db_session, [])
    assert rc == 0

    for key in ("a", "b", "c", "d"):
        row = _reload(db_session, scope[key])
        assert row.imaginary_mode_tau_cm1 is None
        assert row.imaginary_mode_tau_basis is None

    row_e = _reload(db_session, scope["e"])
    assert row_e.imaginary_mode_tau_cm1 == 99.0
    assert row_e.imaginary_mode_tau_basis == "analytic_tight"

    out = capsys.readouterr().out
    assert "Would write tau on 4 calc_freq_result row(s)" in out
    assert "assumed_analytic_default: 1" in out
    assert "assumed_finite_difference_energy: 1" in out
    assert "finite_difference_gradient: 1" in out
    assert "protocol_not_recorded: 1" in out
    assert "Dry run" in out
    # No FK/PK id anywhere in the output -- checked structurally rather
    # than by substring (a small test-database id can coincide with a
    # tally count by chance): every line is either the header or
    # "<basis token>: <count>", never a bare number naming a row.
    for line in out.splitlines():
        if ": " in line:
            basis, _, count = line.strip().rpartition(": ")
            assert basis, f"unexpected output line shape: {line!r}"
            assert count.isdigit(), f"unexpected output line shape: {line!r}"


def test_apply_fills_only_null_rows(backfill, monkeypatch, db_session, scope, capsys):
    rc = _run_main(backfill, monkeypatch, db_session, ["--apply"])
    assert rc == 0

    row_a = _reload(db_session, scope["a"])
    assert row_a.imaginary_mode_tau_basis == "assumed_analytic_default"
    assert row_a.imaginary_mode_tau_cm1 == TAU_ANALYTIC_DEFAULT_CM1
    assert row_a.imaginary_mode_structural_flag is None  # never touched

    row_b = _reload(db_session, scope["b"])
    assert row_b.imaginary_mode_tau_basis == "assumed_finite_difference_energy"
    assert row_b.imaginary_mode_tau_cm1 == TAU_FINITE_DIFFERENCE_ENERGY_CM1

    row_c = _reload(db_session, scope["c"])
    assert row_c.imaginary_mode_tau_basis == "protocol_not_recorded"
    assert row_c.imaginary_mode_tau_cm1 == TAU_PROTOCOL_NOT_RECORDED_CM1

    row_d = _reload(db_session, scope["d"])
    # Recorded parameter wins over what the table would have assumed.
    assert row_d.imaginary_mode_tau_basis == "finite_difference_gradient"
    assert row_d.imaginary_mode_tau_cm1 == TAU_FINITE_DIFFERENCE_GRADIENT_CM1

    # E was already judged and stays exactly as it was.
    row_e = _reload(db_session, scope["e"])
    assert row_e.imaginary_mode_tau_cm1 == 99.0
    assert row_e.imaginary_mode_tau_basis == "analytic_tight"
    assert row_e.imaginary_mode_structural_flag is False

    out = capsys.readouterr().out
    assert "Wrote tau on 4 calc_freq_result row(s)" in out


def test_a_second_apply_is_a_no_op(backfill, monkeypatch, db_session, scope, capsys):
    first = _run_main(backfill, monkeypatch, db_session, ["--apply"])
    assert first == 0
    capsys.readouterr()  # discard the first run's output

    second = _run_main(backfill, monkeypatch, db_session, ["--apply"])
    assert second == 0
    out = capsys.readouterr().out
    assert "Nothing to do" in out

    # Still exactly what the first apply wrote.
    row_a = _reload(db_session, scope["a"])
    assert row_a.imaginary_mode_tau_basis == "assumed_analytic_default"


def test_apply_refuses_a_non_test_database_without_the_override(
    backfill, monkeypatch, db_session, scope, capsys
):
    rc = _run_main(
        backfill, monkeypatch, db_session, ["--apply"], db_name="tckdb_prod"
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert "--i-know-this-is-deployed" in err

    # Nothing was written.
    row_a = _reload(db_session, scope["a"])
    assert row_a.imaginary_mode_tau_cm1 is None


def test_apply_proceeds_against_a_non_test_database_with_the_override(
    backfill, monkeypatch, db_session, scope
):
    rc = _run_main(
        backfill,
        monkeypatch,
        db_session,
        ["--apply", "--i-know-this-is-deployed"],
        db_name="tckdb_prod",
    )
    assert rc == 0
    row_a = _reload(db_session, scope["a"])
    assert row_a.imaginary_mode_tau_basis == "assumed_analytic_default"


def test_apply_and_dry_run_together_is_rejected(backfill, monkeypatch, db_session, scope, capsys):
    rc = _run_main(backfill, monkeypatch, db_session, ["--apply", "--dry-run"])
    assert rc == 2
    row_a = _reload(db_session, scope["a"])
    assert row_a.imaginary_mode_tau_cm1 is None
