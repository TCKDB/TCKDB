"""Service-layer tests for search_thermo (chemistry-first thermo search)."""

from __future__ import annotations

import pytest

from app.db.models.common import RecordReviewStatus, SubmissionRecordType
from app.schemas.reads.scientific_common import CollapseMode
from app.schemas.reads.scientific_thermo import ThermoModelKindQuery
from app.schemas.reads.scientific_thermo_search import ThermoSearchRequest
from app.services.scientific_read.thermo_search import search_thermo
from tests.services.scientific_read._factories import (
    attach_thermo_nasa,
    make_species,
    make_species_entry,
    make_thermo_scalar,
    next_inchi_key,
    set_review,
)


def _entry_with_thermo(db_session, *, smiles: str = "CC", with_nasa: bool = False):
    species = make_species(db_session, smiles=smiles, inchi_key=next_inchi_key("TS"))
    entry = make_species_entry(db_session, species)
    thermo = make_thermo_scalar(db_session, species_entry=entry)
    if with_nasa:
        attach_thermo_nasa(db_session, thermo=thermo)
    return species, entry, thermo


# ---------------------------------------------------------------------------
# Identity resolution + composition
# ---------------------------------------------------------------------------


def test_search_by_smiles_returns_thermo_with_species_context(db_session):
    species, entry, thermo = _entry_with_thermo(db_session, smiles="C[CH2]")

    response = search_thermo(db_session, ThermoSearchRequest(smiles="C[CH2]"))

    assert len(response.records) == 1
    rec = response.records[0]
    assert rec.species.species_id == species.id
    assert rec.species.species_entry_id == entry.id
    assert rec.species.canonical_smiles == "C[CH2]"
    assert rec.thermo.thermo_id == thermo.id


def test_inconsistent_identifiers_return_empty(db_session):
    _entry_with_thermo(db_session, smiles="CCO")

    response = search_thermo(
        db_session,
        ThermoSearchRequest(
            smiles="CCO", inchi_key="ZZZZZZZZZZZZZZZZZZZZZZZZZZZ"
        ),
    )

    assert response.records == []
    assert response.pagination.total == 0


def test_missing_identifier_raises(db_session):
    with pytest.raises(ValueError, match="missing_identifier"):
        search_thermo(db_session, ThermoSearchRequest())


# ---------------------------------------------------------------------------
# Provenance + evidence
# ---------------------------------------------------------------------------


def test_thermo_record_includes_evidence_completeness(db_session):
    _entry_with_thermo(db_session, smiles="O")

    response = search_thermo(db_session, ThermoSearchRequest(smiles="O"))

    breakdown = response.records[0].thermo.evidence_completeness
    assert breakdown.max == 8
    assert "has_temperature_dependent_model" in breakdown.checklist


def test_nasa_thermo_classified_correctly(db_session):
    _entry_with_thermo(db_session, smiles="N", with_nasa=True)

    response = search_thermo(db_session, ThermoSearchRequest(smiles="N"))

    rec = response.records[0]
    assert rec.thermo.model_kind == ThermoModelKindQuery.nasa
    assert rec.thermo.nasa is not None


# ---------------------------------------------------------------------------
# Collapse + pagination
# ---------------------------------------------------------------------------


def test_collapse_first_preserves_plural_records_with_pre_collapse_total(db_session):
    species = make_species(db_session, smiles="P", inchi_key=next_inchi_key("CT1"))
    entry = make_species_entry(db_session, species)
    make_thermo_scalar(db_session, species_entry=entry)
    make_thermo_scalar(db_session, species_entry=entry, h298_kj_mol=-99.0)

    response = search_thermo(
        db_session, ThermoSearchRequest(smiles="P", collapse=CollapseMode.first)
    )

    assert len(response.records) == 1
    assert response.pagination.total == 2
    assert response.pagination.returned == 1


def test_collapse_first_applies_offset_after_collapse(db_session):
    species = make_species(
        db_session,
        smiles="[SiH4]",
        inchi_key=next_inchi_key("CTO"),
    )
    entry = make_species_entry(db_session, species)
    make_thermo_scalar(db_session, species_entry=entry)
    make_thermo_scalar(db_session, species_entry=entry, h298_kj_mol=-99.0)

    response = search_thermo(
        db_session,
        ThermoSearchRequest(
            smiles="[SiH4]",
            collapse=CollapseMode.first,
            offset=1,
        ),
    )

    assert response.records == []
    assert response.pagination.total == 2
    assert response.pagination.returned == 0


# ---------------------------------------------------------------------------
# Review behavior (shallow on the thermo record)
# ---------------------------------------------------------------------------


def test_min_review_status_filters_thermo_record_only(db_session):
    species = make_species(db_session, smiles="S", inchi_key=next_inchi_key("REV"))
    entry = make_species_entry(db_session, species)
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

    response = search_thermo(
        db_session,
        ThermoSearchRequest(
            smiles="S", min_review_status=RecordReviewStatus.approved
        ),
    )

    assert [r.thermo.thermo_id for r in response.records] == [t_approved.id]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_client_supplied_sort_rejected(db_session):
    with pytest.raises(ValueError, match="client_sort_not_supported"):
        search_thermo(db_session, ThermoSearchRequest(smiles="X", sort="anything"))


def test_unknown_include_token_rejected(db_session):
    _entry_with_thermo(db_session, smiles="K1")
    with pytest.raises(ValueError, match="unknown_include_token"):
        search_thermo(
            db_session,
            ThermoSearchRequest(smiles="K1", include=["banana"]),
        )


def test_temperature_min_greater_than_max_rejected(db_session):
    _entry_with_thermo(db_session, smiles="K2")
    with pytest.raises(ValueError, match="invalid_temperature_range"):
        search_thermo(
            db_session,
            ThermoSearchRequest(
                smiles="K2", temperature_min=3000, temperature_max=300
            ),
        )


def test_sort_is_deterministic_across_calls(db_session):
    _entry_with_thermo(db_session, smiles="DET")
    r1 = search_thermo(db_session, ThermoSearchRequest(smiles="DET"))
    r2 = search_thermo(db_session, ThermoSearchRequest(smiles="DET"))
    assert r1.model_dump() == r2.model_dump()
