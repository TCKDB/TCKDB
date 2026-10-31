"""ADR 0012's assumed Hessian default is applied where tau is *decided*.

Owner decision 2026-10-31 (follow-up to #834): when a frequency job's Hessian
method is not recorded, tau resolves through the producing program's assumed
default (``Gaussian`` + DFT -> ``assumed_analytic_default``, 30 cm-1) at
validation, so that what a deposit is judged at is exactly what is stored.
Software and level-of-theory method that are unknown stay at
``protocol_not_recorded``, 50 cm-1.
"""

from __future__ import annotations

from sqlalchemy.orm import Session
from tckdb_schemas.stationary_point import TauBasis

from app.db.models.calculation import CalculationFreqResult
from app.schemas.fragments.calculation import CalculationWithResultsPayload
from app.services.calculation_resolution import (
    resolve_and_persist_calculation_with_results,
)
from tests.services.test_calculation_resolution import (
    _create_species,
    _create_species_entry,
    _next_inchi_key,
)


def _ts_like_payload(software: str = "Gaussian", method: str = "B3LYP"):
    """-1300 reaction coordinate plus an extra -40 cm-1 mode."""
    return CalculationWithResultsPayload(
        type="freq",
        software_release={"name": software, "version": "16"},
        level_of_theory={"method": method, "basis": "6-31G(d)"},
        freq_result={
            "n_imag": 2,
            "imag_freq_cm1": -1300.0,
            "reaction_coordinate_mode_index": 1,
            "modes": [
                {"mode_index": 1, "frequency_cm1": -1300.0, "is_imaginary": True},
                {
                    "mode_index": 2,
                    "frequency_cm1": -40.0,
                    "is_imaginary": True,
                    "imaginary_disposition": "torsion",
                },
                {"mode_index": 3, "frequency_cm1": 600.0, "is_imaginary": False},
            ],
        },
    )


def _persist(session: Session, payload) -> CalculationFreqResult:
    species_id = _create_species(
        session.connection(), inchi_key=_next_inchi_key("ASSUMEDTAU")
    )
    entry_id = _create_species_entry(session.connection(), species_id)
    calc = resolve_and_persist_calculation_with_results(
        session, payload, species_entry_id=entry_id
    )
    session.flush()
    return session.get(CalculationFreqResult, calc.id)


def test_a_transition_state_flag_agrees_with_its_own_stored_tau(db_conn) -> None:
    """Reproduction (b): the extra -40 cm-1 mode was judged at 50 (not
    flagged) while the stored tau was the assumed 30."""
    with Session(db_conn) as session:
        with session.begin():
            stored = _persist(session, _ts_like_payload())
            assert stored.imaginary_mode_tau_basis == "assumed_analytic_default"
            assert stored.imaginary_mode_tau_cm1 == 30.0
            # 40 >= 30, so the extra mode is real curvature and the record
            # carries the structural flag. It must not contradict its tau.
            assert stored.imaginary_mode_structural_flag is True


def test_an_unknown_program_is_judged_and_stored_at_the_conservative_tau(
    db_conn,
) -> None:
    with Session(db_conn) as session:
        with session.begin():
            stored = _persist(session, _ts_like_payload(software="Molpro"))
            assert stored.imaginary_mode_tau_basis == TauBasis.protocol_not_recorded.value
            assert stored.imaginary_mode_tau_cm1 == 50.0
            assert stored.imaginary_mode_structural_flag is False
