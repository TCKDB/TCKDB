"""Service-layer tests for get_species_thermo."""

from __future__ import annotations

import pytest

from app.api.errors import NotFoundError
from app.db.models.common import RecordReviewStatus, SubmissionRecordType
from app.schemas.reads.scientific_thermo import (
    ThermoModelKindQuery,
    ThermoReadRequest,
)
from app.services.scientific_read.thermo import get_species_thermo
from tests.services.scientific_read._factories import (
    attach_thermo_nasa,
    attach_thermo_points,
    make_species,
    make_species_entry,
    make_thermo_scalar,
    next_inchi_key,
    set_review,
)


def _entry_with_smiles(db_session, smiles: str = "CC"):
    species = make_species(
        db_session, smiles=smiles, inchi_key=next_inchi_key("TH")
    )
    return make_species_entry(db_session, species)


# ---------------------------------------------------------------------------
# Path scope + 404
# ---------------------------------------------------------------------------


def test_unknown_species_entry_id_raises_not_found(db_session):
    with pytest.raises(NotFoundError):
        get_species_thermo(
            db_session,
            species_entry_id=999_999,
            request=ThermoReadRequest(),
        )


def test_empty_thermo_returns_empty_records(db_session):
    entry = _entry_with_smiles(db_session)
    response = get_species_thermo(
        db_session,
        species_entry_id=entry.id,
        request=ThermoReadRequest(),
    )
    assert response.records == []
    assert response.species_entry_id == entry.id


# ---------------------------------------------------------------------------
# Model shapes
# ---------------------------------------------------------------------------


def test_scalar_thermo_returns_scalar_block_only(db_session):
    entry = _entry_with_smiles(db_session)
    make_thermo_scalar(db_session, species_entry=entry)

    response = get_species_thermo(
        db_session, species_entry_id=entry.id, request=ThermoReadRequest()
    )
    record = response.records[0]
    assert record.model_kind == ThermoModelKindQuery.scalar
    assert record.h298_kj_mol == -12.3
    assert record.s298_j_mol_k == 250.1
    assert record.nasa is None
    assert record.points is None


def test_nasa_thermo_returns_nasa_block(db_session):
    entry = _entry_with_smiles(db_session)
    thermo = make_thermo_scalar(db_session, species_entry=entry)
    attach_thermo_nasa(db_session, thermo=thermo)

    response = get_species_thermo(
        db_session, species_entry_id=entry.id, request=ThermoReadRequest()
    )
    record = response.records[0]
    assert record.model_kind == ThermoModelKindQuery.nasa
    assert record.nasa is not None
    assert record.nasa.t_low == 200.0
    assert record.nasa.t_high == 6000.0
    assert len(record.nasa.low_temperature_coefficients) == 7
    assert len(record.nasa.high_temperature_coefficients) == 7


def test_points_thermo_returns_points_array(db_session):
    entry = _entry_with_smiles(db_session)
    thermo = make_thermo_scalar(db_session, species_entry=entry)
    attach_thermo_points(db_session, thermo=thermo, temperatures_k=[300.0, 400.0, 500.0])

    response = get_species_thermo(
        db_session, species_entry_id=entry.id, request=ThermoReadRequest()
    )
    record = response.records[0]
    assert record.model_kind == ThermoModelKindQuery.points
    assert record.points is not None
    assert [p.temperature_k for p in record.points] == [300.0, 400.0, 500.0]


def test_model_kind_filter_excludes_other_shapes(db_session):
    entry = _entry_with_smiles(db_session)
    # NASA record + scalar record on same entry — not fully realistic but valid.
    nasa_thermo = make_thermo_scalar(db_session, species_entry=entry)
    attach_thermo_nasa(db_session, thermo=nasa_thermo)
    make_thermo_scalar(db_session, species_entry=entry, h298_kj_mol=-99.0)

    response = get_species_thermo(
        db_session,
        species_entry_id=entry.id,
        request=ThermoReadRequest(model_kind=ThermoModelKindQuery.nasa),
    )
    assert len(response.records) == 1
    assert response.records[0].thermo_id == nasa_thermo.id


# ---------------------------------------------------------------------------
# Review (shallow)
# ---------------------------------------------------------------------------


def test_default_excludes_deprecated_thermo(db_session):
    entry = _entry_with_smiles(db_session)
    t = make_thermo_scalar(db_session, species_entry=entry)
    set_review(
        db_session,
        record_type=SubmissionRecordType.thermo,
        record_id=t.id,
        status=RecordReviewStatus.deprecated,
    )

    response = get_species_thermo(
        db_session, species_entry_id=entry.id, request=ThermoReadRequest()
    )
    assert response.records == []


def test_min_review_status_uses_shallow_thermo_review(db_session):
    entry = _entry_with_smiles(db_session)
    t_approved = make_thermo_scalar(db_session, species_entry=entry)
    t_under = make_thermo_scalar(
        db_session, species_entry=entry, h298_kj_mol=-99.0
    )
    set_review(
        db_session,
        record_type=SubmissionRecordType.thermo,
        record_id=t_approved.id,
        status=RecordReviewStatus.approved,
    )
    set_review(
        db_session,
        record_type=SubmissionRecordType.thermo,
        record_id=t_under.id,
        status=RecordReviewStatus.under_review,
    )

    response = get_species_thermo(
        db_session,
        species_entry_id=entry.id,
        request=ThermoReadRequest(min_review_status=RecordReviewStatus.approved),
    )
    assert [r.thermo_id for r in response.records] == [t_approved.id]


# ---------------------------------------------------------------------------
# Evidence completeness
# ---------------------------------------------------------------------------


def test_evidence_completeness_returns_score_and_checklist(db_session):
    entry = _entry_with_smiles(db_session)
    make_thermo_scalar(db_session, species_entry=entry)

    response = get_species_thermo(
        db_session, species_entry_id=entry.id, request=ThermoReadRequest()
    )
    breakdown = response.records[0].evidence_completeness
    expected = {
        "has_source_calculations",
        "has_statmech_source",
        "has_frequency_evidence",
        "has_sp_or_energy_evidence",
        "has_temperature_dependent_model",
        "has_uncertainty",
        "has_geometry_validation",
        "has_scf_stability",
    }
    assert set(breakdown.checklist.keys()) == expected
    assert breakdown.max == 8


def test_evidence_completeness_temperature_model_predicate_true_for_nasa(db_session):
    entry = _entry_with_smiles(db_session)
    thermo = make_thermo_scalar(db_session, species_entry=entry)
    attach_thermo_nasa(db_session, thermo=thermo)

    response = get_species_thermo(
        db_session, species_entry_id=entry.id, request=ThermoReadRequest()
    )
    cl = response.records[0].evidence_completeness.checklist
    assert cl["has_temperature_dependent_model"] is True


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_client_supplied_sort_rejected(db_session):
    entry = _entry_with_smiles(db_session)
    with pytest.raises(ValueError, match="client_sort_not_supported"):
        get_species_thermo(
            db_session,
            species_entry_id=entry.id,
            request=ThermoReadRequest(sort="anything"),
        )


def test_unknown_include_token_rejected(db_session):
    entry = _entry_with_smiles(db_session)
    with pytest.raises(ValueError, match="unknown_include_token"):
        get_species_thermo(
            db_session,
            species_entry_id=entry.id,
            request=ThermoReadRequest(include=["banana"]),
        )


def test_temperature_min_greater_than_max_rejected(db_session):
    entry = _entry_with_smiles(db_session)
    with pytest.raises(ValueError, match="invalid_temperature_range"):
        get_species_thermo(
            db_session,
            species_entry_id=entry.id,
            request=ThermoReadRequest(temperature_min=2000.0, temperature_max=300.0),
        )


def test_sort_is_deterministic(db_session):
    entry = _entry_with_smiles(db_session)
    make_thermo_scalar(db_session, species_entry=entry)
    make_thermo_scalar(db_session, species_entry=entry, h298_kj_mol=-99.0)

    r1 = get_species_thermo(
        db_session, species_entry_id=entry.id, request=ThermoReadRequest()
    )
    r2 = get_species_thermo(
        db_session, species_entry_id=entry.id, request=ThermoReadRequest()
    )
    assert r1.model_dump() == r2.model_dump()
