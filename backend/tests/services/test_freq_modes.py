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
from tckdb_schemas.coded_error import CodedValidationError
from tckdb_schemas.fragments.calculation import (
    W_FREQ_MODE_INDEX_NOT_UNIQUE,
    W_FREQ_N_IMAG_DISAGREES_WITH_MODES,
)

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
            VALUES ('molecule', :smiles, :inchi_key, 0, 1, 'achiral')
            RETURNING id
            """
        ),
        {"smiles": inchi_key, "inchi_key": inchi_key},
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
        """The refusal is named, and names which index collided.

        The prose match is kept because the first sentence is published
        and attaching a code was meant to be additive; the code and the
        context are the new contract, and a client branching on either
        is the reason ``freq_mode_index_not_unique`` exists at all.
        """
        with pytest.raises(ValidationError, match="mode_index values must be unique") as excinfo:
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
        errors = excinfo.value.errors()
        assert len(errors) == 1, errors
        coded = errors[0]["ctx"]["error"]
        assert isinstance(coded, CodedValidationError)
        assert coded.code == W_FREQ_MODE_INDEX_NOT_UNIQUE
        assert coded.context == {
            "field": "modes",
            "duplicate_mode_indices": [1],
            "mode_count": 2,
        }

    def test_a_unique_mode_index_list_is_accepted(self) -> None:
        """The negative half. Same two rows, renumbered, and it validates.

        Without it the assertion above passes just as well against a
        validator that refuses every ``modes`` list it is given.
        """
        payload = FreqResultPayload(
            n_imag=0,
            modes=[
                FrequencyModePayload(
                    mode_index=1, frequency_cm1=1100.0, is_imaginary=False
                ),
                FrequencyModePayload(
                    mode_index=2, frequency_cm1=1200.0, is_imaginary=False
                ),
            ],
        )
        assert [m.mode_index for m in payload.modes or []] == [1, 2]

    def test_every_repeated_index_is_named_not_just_the_first(self) -> None:
        """Two collisions in one list report both, sorted.

        A depositor whose serialiser concatenated two blocks has more
        than one duplicate, and being told about one of them is a repair
        loop rather than a repair.
        """
        with pytest.raises(ValidationError) as excinfo:
            FreqResultPayload(
                modes=[
                    FrequencyModePayload(
                        mode_index=index, frequency_cm1=1000.0 + index,
                        is_imaginary=False,
                    )
                    for index in (3, 1, 3, 1, 2)
                ]
            )
        coded = excinfo.value.errors()[0]["ctx"]["error"]
        assert coded.context["duplicate_mode_indices"] == [1, 3]
        assert coded.context["mode_count"] == 5

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

    def test_the_mismatch_is_refused_under_its_own_code(self) -> None:
        """The refusal names itself, rather than arriving as prose.

        Asserted on the exception's ``code`` attribute, never on the
        message: Pydantic echoes rejected input back into its error
        string, so a substring test can be satisfied by a payload that
        was *accepted* and merely mentioned somewhere else in the
        report. The type and the code cannot be faked that way.
        """
        with pytest.raises(ValidationError) as excinfo:
            FreqResultPayload(
                n_imag=3,
                modes=[
                    FrequencyModePayload(
                        mode_index=1, frequency_cm1=-1300.0, is_imaginary=True
                    ),
                    FrequencyModePayload(
                        mode_index=2, frequency_cm1=800.0, is_imaginary=False
                    ),
                ],
            )
        errors = excinfo.value.errors()
        assert len(errors) == 1, errors
        coded = errors[0]["ctx"]["error"]
        assert isinstance(coded, CodedValidationError)
        assert coded.code == W_FREQ_N_IMAG_DISAGREES_WITH_MODES
        # What it is short of, structured rather than parsed out of prose.
        assert coded.context == {
            "n_imag": 3,
            "imaginary_mode_count": 1,
            "mode_count": 2,
        }

    def test_a_scalar_with_no_frequency_list_is_accepted(self) -> None:
        """Absence is not disagreement, and this is where the two part.

        A depositor who uploads no per-mode data has an incomplete
        record, not a contradictory one. The read API already takes this
        position from the other end by reporting
        ``n_imag_at_or_above_tau = null`` rather than ``0`` for exactly
        this state, and a rule reading "mode rows must exist" would
        refuse a record TCKDB has always accepted and still means to.
        """
        payload = FreqResultPayload(n_imag=3, imag_freq_cm1=-1300.0)
        assert payload.n_imag == 3
        assert payload.modes is None

    def test_an_empty_frequency_list_is_a_claim_and_is_judged(self) -> None:
        """``modes = []`` is not ``modes = null``, and is not treated as it.

        The distinction is worth pinning because persistence *does*
        collapse the two -- ``if calc_upload.freq_result.modes:`` writes
        no rows for either -- so it would be easy to argue the validator
        should collapse them as well. It must not. An empty list is a
        depositor handing over the frequency list and saying nothing in
        it is imaginary, which beside ``n_imag = 3`` is the same
        contradiction as any other disagreeing list; omitting the field
        is declining to say. Only the second is absence.
        """
        assert FreqResultPayload(n_imag=0, modes=[]).modes == []
        with pytest.raises(ValidationError) as excinfo:
            FreqResultPayload(n_imag=3, modes=[])
        coded = excinfo.value.errors()[0]["ctx"]["error"]
        assert coded.code == W_FREQ_N_IMAG_DISAGREES_WITH_MODES
        assert coded.context["imaginary_mode_count"] == 0
        assert coded.context["mode_count"] == 0

    def test_a_list_that_shows_more_than_the_scalar_claims(self) -> None:
        """The disagreement is refused in both directions.

        A rule written as "the list must not fall short" would pass this
        payload, and the record would then say one imaginary mode in its
        summary and show two in its evidence -- the same defect with the
        readers swapped.
        """
        with pytest.raises(ValidationError) as excinfo:
            FreqResultPayload(
                n_imag=1,
                modes=[
                    FrequencyModePayload(
                        mode_index=1, frequency_cm1=-1300.0, is_imaginary=True
                    ),
                    FrequencyModePayload(
                        mode_index=2, frequency_cm1=-42.0, is_imaginary=True
                    ),
                ],
            )
        coded = excinfo.value.errors()[0]["ctx"]["error"]
        assert coded.code == W_FREQ_N_IMAG_DISAGREES_WITH_MODES
        assert coded.context == {
            "n_imag": 1,
            "imaginary_mode_count": 2,
            "mode_count": 2,
        }

    def test_a_list_with_no_scalar_beside_it_is_accepted(self) -> None:
        """``n_imag = null`` has nothing to disagree with the list about."""
        payload = FreqResultPayload(
            modes=[
                FrequencyModePayload(
                    mode_index=1, frequency_cm1=-1300.0, is_imaginary=True
                ),
            ],
        )
        assert payload.n_imag is None

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


def test_persist_freq_result_without_modes_keeps_existing_behavior(db_conn) -> None:
    with Session(db_conn) as session:
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


def test_persist_freq_result_with_modes_persists_rows(db_conn) -> None:
    with Session(db_conn) as session:
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


def test_freq_modes_check_constraint_blocks_inconsistent_sign(db_conn) -> None:
    """The DB CHECK is a backstop if the Pydantic validator is ever bypassed."""
    with Session(db_conn) as session:
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
