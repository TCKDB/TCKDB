"""``list_tau_redecided.py``: lists, by public ref, what the new tau would re-decide.

Read-only by construction; the test also proves it wrote nothing.
"""

from __future__ import annotations

import contextlib
import importlib.util
import pathlib
import sys

import pytest

from app.db.models.calculation import CalculationFreqResult
from app.db.models.common import CalculationType, ImaginaryModeDisposition
from tests.services.scientific_read._factories import (
    attach_freq_result,
    make_calculation,
    make_lot,
    make_software_release,
    make_species,
    make_species_entry,
    next_inchi_key,
)

_SCRIPT = pathlib.Path(__file__).parents[2] / "scripts" / "ops" / "list_tau_redecided.py"


@pytest.fixture(scope="module")
def lister():
    spec = importlib.util.spec_from_file_location("list_tau_redecided", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _SessionProxy:
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


@pytest.fixture
def rows(db_session):
    entry = make_species_entry(
        db_session, make_species(db_session, inchi_key=next_inchi_key("LSTTAU"))
    )
    gaussian = make_software_release(db_session, name="Gaussian", version="16")
    molpro = make_software_release(db_session, name="Molpro", version="2022.1")
    lot = make_lot(db_session, method="b3lyp-lsttau", basis="def2tzvp")

    def freq(software, frequencies, **judged):
        calc = make_calculation(
            db_session,
            type=CalculationType.freq,
            species_entry_id=entry.id,
            lot_id=lot.id,
            software_release_id=software.id,
        )
        attach_freq_result(db_session, calculation=calc, frequencies_cm1=frequencies, **judged)
        return calc

    # Minimum accepted at the old 50 with a -40 mode: now refused (assumed tau 30).
    min_refused = freq(
        gaussian,
        [-40.0, 500.0],
        imaginary_mode_tau_cm1=50.0,
        imaginary_mode_tau_basis="protocol_not_recorded",
        imaginary_mode_structural_flag=False,
    )
    # Minimum with a -20 mode: tau differs (50 -> 30) but still accepted.
    min_kept = freq(
        gaussian,
        [-20.0, 500.0],
        imaginary_mode_tau_cm1=50.0,
        imaginary_mode_tau_basis="protocol_not_recorded",
        imaginary_mode_structural_flag=False,
    )
    # Minimum on a program the table does not know: tau unchanged.
    min_unknown = freq(
        molpro,
        [-40.0, 500.0],
        imaginary_mode_tau_cm1=50.0,
        imaginary_mode_tau_basis="protocol_not_recorded",
        imaginary_mode_structural_flag=False,
    )
    # Saddle with an extra -40 mode judged at 50 (unflagged), stored basis old.
    ts_flips = freq(
        gaussian,
        [-1300.0, -40.0, 600.0],
        imaginary_dispositions=[None, ImaginaryModeDisposition.torsion, None],
        reaction_coordinate_mode_index=1,
        imaginary_mode_tau_cm1=50.0,
        imaginary_mode_tau_basis="protocol_not_recorded",
        imaginary_mode_structural_flag=False,
    )
    # Saddle already stored at the assumed tau and flagged: nothing changes.
    ts_same = freq(
        gaussian,
        [-1300.0, -40.0, 600.0],
        imaginary_dispositions=[None, ImaginaryModeDisposition.torsion, None],
        reaction_coordinate_mode_index=1,
        imaginary_mode_tau_cm1=30.0,
        imaginary_mode_tau_basis="assumed_analytic_default",
        imaginary_mode_structural_flag=True,
    )
    # Never judged: out of scope.
    never = freq(gaussian, [-40.0, 500.0])
    db_session.flush()
    return {
        "min_refused": min_refused,
        "min_kept": min_kept,
        "min_unknown": min_unknown,
        "ts_flips": ts_flips,
        "ts_same": ts_same,
        "never": never,
    }


def test_lists_what_the_new_tau_would_re_decide_and_writes_nothing(
    lister, monkeypatch, db_session, rows, capsys
):
    import app.api.deps as deps

    monkeypatch.setattr(deps, "SessionLocal", _SessionProxy(db_session))
    assert lister.main() == 0
    out = capsys.readouterr().out

    refused = [line for line in out.splitlines() if rows["min_refused"].public_ref in line]
    flips = [line for line in out.splitlines() if rows["ts_flips"].public_ref in line]
    assert len(refused) == 1 and "50 (protocol_not_recorded) -> 30 (assumed_analytic_default)" in refused[0]
    assert len(flips) == 1 and "flag False -> True" in flips[0]

    # Not listed: still accepted, unknown program, already consistent, never judged.
    for key in ("min_kept", "min_unknown", "ts_same", "never"):
        assert rows[key].public_ref not in out

    assert "minimum_would_now_be_refused: 1" in out
    assert "transition_state_flag_would_flip: 1" in out
    # min_kept (50 -> 30, still accepted) and nothing else differs without a change.
    assert "tau_differs_decision_unchanged (count only): 1" in out
    assert "Read-only" in out

    # Nothing written.
    stored = db_session.get(CalculationFreqResult, rows["min_refused"].id)
    assert stored.imaginary_mode_tau_cm1 == 50.0
    assert stored.imaginary_mode_tau_basis == "protocol_not_recorded"
