"""The bath gas a reaction-level rate was obtained for (``kinetics_bath_gas``).

A PLOG table fitted for Ar is not one for N2. The record states its bath gas only where the
depositor does, and every refusal below is coded so a client can branch on it. The rules mirror a
network solve's bath gas (mole fractions in (0, 1], distinct components, sum 1 within 1e-9).
"""

from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.error_contract import CodedValueError, validation_detail_code
from app.db.models.kinetics import KineticsBathGas
from app.db.models.species import SpeciesEntry
from app.schemas.reads.scientific_kinetics import KineticsReadRequest
from app.schemas.workflows.kinetics_upload import KineticsUploadRequest
from app.services.scientific_read.kinetics import get_reaction_kinetics
from app.workflows.kinetics import persist_kinetics_upload
from tests.workflows.test_kinetics_upload import _patch_doi, _shape_kinetics_request

AR = {"smiles": "[Ar]", "charge": 0, "multiplicity": 1}
N2 = {"smiles": "N#N", "charge": 0, "multiplicity": 1}

_PLOG = [
    {"entry_index": 1, "pressure_bar": 0.1, "a": 1.0e10, "n": 0.0, "ea_kj_mol": 50.0, "a_units": "cm3_mol_s"},
    {"entry_index": 2, "pressure_bar": 1.0, "a": 2.0e10, "n": 0.1, "ea_kj_mol": 52.0, "a_units": "cm3_mol_s"},
]


def _plog_request(**overrides) -> KineticsUploadRequest:
    return _shape_kinetics_request(
        model_kind="plog",
        plog_entries=copy.deepcopy(_PLOG),
        pressure_context="pressure_dependent",
        **overrides,
    )


def _error_code(exc: ValidationError) -> str | None:
    return validation_detail_code(exc.errors(), fallback="request_validation_error")


# ---------------------------------------------------------------------------
# Accepted
# ---------------------------------------------------------------------------


def test_plog_with_a_bath_gas_persists_and_reads_back(db_conn, monkeypatch) -> None:
    _patch_doi(monkeypatch)
    request = _plog_request(
        bath_gas=[{"species_entry": AR, "mole_fraction": 0.75}, {"species_entry": N2, "mole_fraction": 0.25}]
    )
    with Session(db_conn) as session, session.begin():
        kinetics = persist_kinetics_upload(session, request)
        session.flush()
        rows = session.scalars(select(KineticsBathGas).where(KineticsBathGas.kinetics_id == kinetics.id)).all()
        smiles_to_fraction = {session.get(SpeciesEntry, r.species_entry_id).species.smiles: r.mole_fraction for r in rows}
        assert smiles_to_fraction == {"[Ar]": 0.75, "N#N": 0.25}

        response = get_reaction_kinetics(
            session, reaction_entry_id=kinetics.reaction_entry_id, request=KineticsReadRequest()
        )
        record = next(item for item in response.records if item.kinetics_id == kinetics.id)
        assert record.bath_gas is not None
        assert sorted(b.mole_fraction for b in record.bath_gas) == [0.25, 0.75]
        refs = {b.species_entry_ref for b in record.bath_gas}
        assert refs == {session.get(SpeciesEntry, r.species_entry_id).public_ref for r in rows}


def test_a_record_that_states_no_bath_gas_has_none(db_conn, monkeypatch) -> None:
    """Absent means not stated: nothing is inferred, from a falloff's efficiencies or anything else."""
    _patch_doi(monkeypatch)
    with Session(db_conn) as session, session.begin():
        kinetics = persist_kinetics_upload(session, _plog_request())
        session.flush()
        assert session.scalars(select(KineticsBathGas).where(KineticsBathGas.kinetics_id == kinetics.id)).all() == []
        response = get_reaction_kinetics(
            session, reaction_entry_id=kinetics.reaction_entry_id, request=KineticsReadRequest()
        )
        record = next(item for item in response.records if item.kinetics_id == kinetics.id)
        assert record.bath_gas is None


def test_apparent_rate_at_a_pressure_may_state_a_bath_gas() -> None:
    request = _shape_kinetics_request(
        pressure_context="apparent_at_pressure",
        pressure_bar=1.0,
        bath_gas=[{"species_entry": N2, "mole_fraction": 1.0}],
    )
    assert len(request.bath_gas) == 1


def test_mole_fractions_summing_to_one_within_the_network_tolerance_are_accepted() -> None:
    third = 1.0 / 3.0
    request = _plog_request(
        bath_gas=[
            {"species_entry": AR, "mole_fraction": third},
            {"species_entry": N2, "mole_fraction": 1.0 - third + 5e-10},
        ]
    )
    assert len(request.bath_gas) == 2


# ---------------------------------------------------------------------------
# Refused, each with its code
# ---------------------------------------------------------------------------


def test_bath_gas_at_the_high_pressure_limit_is_refused() -> None:
    with pytest.raises(ValidationError) as caught:
        _shape_kinetics_request(
            pressure_context="high_p_limit", bath_gas=[{"species_entry": AR, "mole_fraction": 1.0}]
        )
    assert _error_code(caught.value) == "kinetics_bath_gas_at_high_pressure_limit"


def test_mole_fractions_summing_to_more_than_one_are_refused() -> None:
    with pytest.raises(ValidationError) as caught:
        _plog_request(
            bath_gas=[{"species_entry": AR, "mole_fraction": 0.8}, {"species_entry": N2, "mole_fraction": 0.5}]
        )
    assert _error_code(caught.value) == "kinetics_bath_gas_mole_fraction_sum"


def test_mole_fractions_summing_to_less_than_one_are_refused_as_for_a_network_solve() -> None:
    """A network solve's bath gas must sum to 1; so must this one."""
    with pytest.raises(ValidationError) as caught:
        _plog_request(
            bath_gas=[{"species_entry": AR, "mole_fraction": 0.4}, {"species_entry": N2, "mole_fraction": 0.4}]
        )
    assert _error_code(caught.value) == "kinetics_bath_gas_mole_fraction_sum"


def test_the_same_species_named_twice_is_refused() -> None:
    with pytest.raises(ValidationError) as caught:
        _plog_request(
            bath_gas=[{"species_entry": AR, "mole_fraction": 0.5}, {"species_entry": AR, "mole_fraction": 0.5}]
        )
    assert _error_code(caught.value) == "kinetics_bath_gas_duplicate_species"


def test_two_spellings_of_one_species_entry_are_refused_at_write_time(db_conn, monkeypatch) -> None:
    """Different payloads, one resolved species entry: only the write path can see it."""
    _patch_doi(monkeypatch)
    explicit_ground_state = {**N2, "smiles": "N#N", "multiplicity": 1}
    reordered = {"smiles": "[N]#[N]", "charge": 0, "multiplicity": 1}
    request = _plog_request(
        bath_gas=[
            {"species_entry": explicit_ground_state, "mole_fraction": 0.5},
            {"species_entry": reordered, "mole_fraction": 0.5},
        ]
    )
    with Session(db_conn) as session, session.begin():
        with pytest.raises(CodedValueError) as caught:
            persist_kinetics_upload(session, request)
    assert caught.value.code == "kinetics_bath_gas_duplicate_species"
    assert "id" not in caught.value.context


@pytest.mark.parametrize("fraction", [0.0, -0.1, 1.5])
def test_a_component_outside_zero_to_one_is_refused(fraction: float) -> None:
    with pytest.raises(ValidationError):
        _plog_request(bath_gas=[{"species_entry": AR, "mole_fraction": fraction}])
