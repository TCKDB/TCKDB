"""Per-mode vibrational frequency storage tests.

Covers the FrequencyModePayload validator (sign/uniqueness/n_imag),
the persistence path through ``persist_calculation_result``, and the
``GET /api/v1/calculations/{id}/freq-result`` read endpoint.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.models.calculation import (
    CalculationFreqMode,
    CalculationFreqResult,
)
from app.schemas.fragments.calculation import (
    CalculationWithResultsPayload,
    FreqResultPayload,
    FrequencyModePayload,
)
from app.services.calculation_resolution import (
    resolve_and_persist_calculation_with_results,
)


_INCHI_COUNTER = 0


def _next_inchi_key(prefix: str) -> str:
    global _INCHI_COUNTER
    _INCHI_COUNTER += 1
    stem = f"{prefix}{_INCHI_COUNTER:0>21}"
    return stem[:27]


def _create_species_entry(connection, *, inchi_key: str) -> int:
    species_id = connection.execute(
        text(
            """
            INSERT INTO species (kind, smiles, inchi_key, charge, multiplicity, stereo_kind)
            VALUES ('molecule', '[H]', :inchi_key, 0, 1, 'achiral')
            RETURNING id
            """
        ),
        {"inchi_key": inchi_key},
    ).scalar_one()
    return connection.execute(
        text(
            "INSERT INTO species_entry (species_id) VALUES (:species_id) RETURNING id"
        ),
        {"species_id": species_id},
    ).scalar_one()


# ---------------------------------------------------------------------------
# Payload-level validation
# ---------------------------------------------------------------------------


class TestFrequencyModePayloadValidation:
    def test_real_mode_round_trips(self) -> None:
        m = FrequencyModePayload(mode_index=1, frequency_cm1=1100.0, is_imaginary=False)
        assert m.frequency_cm1 == 1100.0
        assert m.is_imaginary is False

    def test_imaginary_mode_with_negative_frequency_accepted(self) -> None:
        m = FrequencyModePayload(mode_index=1, frequency_cm1=-1500.0, is_imaginary=True)
        assert m.frequency_cm1 == -1500.0
        assert m.is_imaginary is True

    def test_imaginary_flag_with_positive_frequency_rejected(self) -> None:
        with pytest.raises(ValidationError, match="is_imaginary=True requires"):
            FrequencyModePayload(mode_index=1, frequency_cm1=1500.0, is_imaginary=True)

    def test_negative_frequency_without_imaginary_flag_rejected(self) -> None:
        with pytest.raises(ValidationError, match="requires is_imaginary=True"):
            FrequencyModePayload(
                mode_index=1, frequency_cm1=-1500.0, is_imaginary=False
            )

    def test_mode_index_below_one_rejected(self) -> None:
        with pytest.raises(ValidationError):
            FrequencyModePayload(mode_index=0, frequency_cm1=1100.0, is_imaginary=False)


class TestFreqResultPayloadModes:
    def test_no_modes_is_valid(self) -> None:
        payload = FreqResultPayload(n_imag=1, imag_freq_cm1=-1500.0)
        assert payload.modes is None

    def test_duplicate_mode_index_rejected(self) -> None:
        with pytest.raises(ValidationError, match="mode_index values must be unique"):
            FreqResultPayload(
                modes=[
                    FrequencyModePayload(
                        mode_index=1, frequency_cm1=1100.0, is_imaginary=False
                    ),
                    FrequencyModePayload(
                        mode_index=1, frequency_cm1=1200.0, is_imaginary=False
                    ),
                ]
            )

    def test_n_imag_mismatch_rejected(self) -> None:
        with pytest.raises(ValidationError, match="does not match imaginary mode count"):
            FreqResultPayload(
                n_imag=2,
                modes=[
                    FrequencyModePayload(
                        mode_index=1, frequency_cm1=-1500.0, is_imaginary=True
                    ),
                ],
            )

    def test_n_imag_matches_when_consistent(self) -> None:
        payload = FreqResultPayload(
            n_imag=1,
            modes=[
                FrequencyModePayload(
                    mode_index=1, frequency_cm1=-1500.0, is_imaginary=True
                ),
                FrequencyModePayload(
                    mode_index=2, frequency_cm1=1100.0, is_imaginary=False
                ),
            ],
        )
        assert len(payload.modes) == 2


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _freq_calc_payload(modes: list[dict] | None = None) -> CalculationWithResultsPayload:
    base = {
        "type": "freq",
        "software_release": {"name": "Gaussian", "version": "16"},
        "level_of_theory": {"method": "wB97X-D", "basis": "def2-TZVP"},
        "freq_result": {
            "n_imag": 1,
            "imag_freq_cm1": -1523.4,
            "zpe_hartree": 0.012,
        },
    }
    if modes is not None:
        base["freq_result"]["modes"] = modes
    return CalculationWithResultsPayload.model_validate(base)


def test_persist_freq_result_without_modes_keeps_existing_behavior(db_engine) -> None:
    with Session(db_engine) as session:
        with session.begin():
            species_entry_id = _create_species_entry(
                session.connection(), inchi_key=_next_inchi_key("FREQNOMODE")
            )
            calc = resolve_and_persist_calculation_with_results(
                session,
                _freq_calc_payload(modes=None),
                species_entry_id=species_entry_id,
            )
            session.flush()

            assert session.scalar(
                select(CalculationFreqResult).where(
                    CalculationFreqResult.calculation_id == calc.id
                )
            ) is not None
            assert session.scalars(
                select(CalculationFreqMode).where(
                    CalculationFreqMode.calculation_id == calc.id
                )
            ).all() == []


def test_persist_freq_result_with_modes_persists_rows(db_engine) -> None:
    with Session(db_engine) as session:
        with session.begin():
            species_entry_id = _create_species_entry(
                session.connection(), inchi_key=_next_inchi_key("FREQMODES")
            )
            modes = [
                {
                    "mode_index": 1,
                    "frequency_cm1": -1523.4,
                    "is_imaginary": True,
                    "reduced_mass_amu": 1.1,
                    "ir_intensity_km_mol": 12.3,
                    "symmetry_label": "A",
                },
                {
                    "mode_index": 2,
                    "frequency_cm1": 250.0,
                    "is_imaginary": False,
                },
                {
                    "mode_index": 3,
                    "frequency_cm1": 1100.0,
                    "is_imaginary": False,
                },
            ]
            calc = resolve_and_persist_calculation_with_results(
                session,
                _freq_calc_payload(modes=modes),
                species_entry_id=species_entry_id,
            )
            session.flush()

            stored = session.scalars(
                select(CalculationFreqMode)
                .where(CalculationFreqMode.calculation_id == calc.id)
                .order_by(CalculationFreqMode.mode_index)
            ).all()
            assert [m.mode_index for m in stored] == [1, 2, 3]
            assert stored[0].is_imaginary is True
            assert stored[0].frequency_cm1 == pytest.approx(-1523.4)
            assert stored[0].reduced_mass_amu == pytest.approx(1.1)
            assert stored[0].ir_intensity_km_mol == pytest.approx(12.3)
            assert stored[0].symmetry_label == "A"
            assert stored[1].is_imaginary is False
            assert stored[2].frequency_cm1 == pytest.approx(1100.0)


def test_freq_modes_round_trip_via_read_endpoint(client: TestClient, db_session) -> None:
    species_entry_id = _create_species_entry(
        db_session.connection(), inchi_key=_next_inchi_key("FREQAPI")
    )
    modes = [
        {"mode_index": 1, "frequency_cm1": -1500.0, "is_imaginary": True},
        {"mode_index": 2, "frequency_cm1": 1200.0, "is_imaginary": False},
    ]
    calc = resolve_and_persist_calculation_with_results(
        db_session,
        _freq_calc_payload(modes=modes),
        species_entry_id=species_entry_id,
    )
    db_session.flush()
    calc_id = calc.id

    response = client.get(f"/api/v1/calculations/{calc_id}/freq-result")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["calculation_id"] == calc_id
    assert body["n_imag"] == 1
    assert len(body["modes"]) == 2
    assert body["modes"][0]["mode_index"] == 1
    assert body["modes"][0]["frequency_cm1"] == pytest.approx(-1500.0)
    assert body["modes"][0]["is_imaginary"] is True
    assert body["modes"][1]["mode_index"] == 2
    assert body["modes"][1]["is_imaginary"] is False


def test_freq_modes_check_constraint_blocks_inconsistent_sign(db_engine) -> None:
    """The DB CHECK is a backstop if the Pydantic validator is ever bypassed."""
    with Session(db_engine) as session:
        with session.begin():
            species_entry_id = _create_species_entry(
                session.connection(), inchi_key=_next_inchi_key("FREQCHECK")
            )
            calc = resolve_and_persist_calculation_with_results(
                session,
                _freq_calc_payload(modes=None),
                species_entry_id=species_entry_id,
            )
            session.flush()
            calc_id = calc.id

        with pytest.raises(Exception):
            with session.begin():
                session.add(
                    CalculationFreqMode(
                        calculation_id=calc_id,
                        mode_index=1,
                        frequency_cm1=1500.0,  # positive...
                        is_imaginary=True,  # ...but flagged imaginary
                    )
                )
