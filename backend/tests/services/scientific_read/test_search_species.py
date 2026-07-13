"""Service-layer tests for search_species (/scientific/species/search)."""

from __future__ import annotations

import pytest

from app.db.models.common import (
    RecordReviewStatus,
    SpeciesEntryStateKind,
    SubmissionRecordType,
)
from app.schemas.reads.scientific_common import CollapseMode
from app.schemas.reads.scientific_species import SpeciesSearchRequest
from app.services.scientific_read.species import search_species
from tests.services.scientific_read._factories import (
    make_species,
    make_species_entry,
    make_thermo_scalar,
    next_inchi_key,
    set_review,
)

# ---------------------------------------------------------------------------
# Identity matching
# ---------------------------------------------------------------------------


def test_search_by_smiles_returns_canonical_species(db_session):
    species = make_species(db_session, smiles="C[CH2]", multiplicity=2)
    make_species_entry(db_session, species)

    response = search_species(db_session, SpeciesSearchRequest(smiles="C[CH2]"))

    assert len(response.records) == 1
    assert response.records[0].canonical_smiles == "C[CH2]"
    assert response.records[0].species_id == species.id


def test_search_by_inchi_key_matches(db_session):
    inchi_key = next_inchi_key("INCHI1")
    species = make_species(db_session, inchi_key=inchi_key)
    make_species_entry(db_session, species)

    response = search_species(
        db_session, SpeciesSearchRequest(inchi_key=inchi_key)
    )

    assert len(response.records) == 1
    assert response.records[0].inchi_key == inchi_key


def test_multiple_consistent_identifiers_and_combine(db_session):
    inchi_key = next_inchi_key("AND1")
    species = make_species(db_session, smiles="CCO", inchi_key=inchi_key)
    make_species_entry(db_session, species)

    response = search_species(
        db_session,
        SpeciesSearchRequest(smiles="CCO", inchi_key=inchi_key),
    )

    assert len(response.records) == 1


def test_multiple_inconsistent_identifiers_return_empty(db_session):
    inchi_key = next_inchi_key("BAD1")
    species = make_species(db_session, smiles="CCO", inchi_key=inchi_key)
    make_species_entry(db_session, species)

    response = search_species(
        db_session,
        SpeciesSearchRequest(smiles="CCO", inchi_key="ZZZZZZZZZZZZZZZZZZZZZZZZZZZ"),
    )

    assert response.records == []
    assert response.pagination.total == 0


def test_no_identifier_raises(db_session):
    with pytest.raises(ValueError, match="missing_identifier"):
        search_species(db_session, SpeciesSearchRequest())


# ---------------------------------------------------------------------------
# Formula filter (RDKit cartridge derived) — regression coverage for the
# "formula silently returns everything" bug.
# ---------------------------------------------------------------------------


def test_search_by_formula_returns_water_and_excludes_cyclopropane(db_session):
    water = make_species(db_session, smiles="O", inchi_key=next_inchi_key("H2O"))
    make_species_entry(db_session, water)
    cyclopropane = make_species(
        db_session, smiles="C1CC1", inchi_key=next_inchi_key("C3H6")
    )
    make_species_entry(db_session, cyclopropane)

    response = search_species(db_session, SpeciesSearchRequest(formula="H2O"))

    species_ids = {rec.species_id for rec in response.records}
    assert water.id in species_ids
    assert cyclopropane.id not in species_ids
    assert len(response.records) == 1


def test_search_by_formula_nonexistent_returns_empty(db_session):
    water = make_species(db_session, smiles="O", inchi_key=next_inchi_key("H2ONF"))
    make_species_entry(db_session, water)

    response = search_species(
        db_session, SpeciesSearchRequest(formula="XeF99999")
    )

    assert response.records == []
    assert response.pagination.total == 0


def test_search_by_formula_matches_charged_ion_suffix(db_session):
    hydroxide = make_species(
        db_session, smiles="[OH-]", charge=-1, inchi_key=next_inchi_key("OHION")
    )
    make_species_entry(db_session, hydroxide)
    water = make_species(db_session, smiles="O", inchi_key=next_inchi_key("H2OION"))
    make_species_entry(db_session, water)

    response = search_species(db_session, SpeciesSearchRequest(formula="HO-"))

    species_ids = {rec.species_id for rec in response.records}
    assert hydroxide.id in species_ids
    assert water.id not in species_ids


# ---------------------------------------------------------------------------
# InChI filter — no stored/derivable column, so an inchi-only query must
# never silently return the full unfiltered species table.
# ---------------------------------------------------------------------------


def test_search_by_inchi_only_returns_empty_not_everything(db_session):
    species_a = make_species(db_session, smiles="O", inchi_key=next_inchi_key("INCHIONLY1"))
    make_species_entry(db_session, species_a)
    species_b = make_species(db_session, smiles="C1CC1", inchi_key=next_inchi_key("INCHIONLY2"))
    make_species_entry(db_session, species_b)

    response = search_species(
        db_session,
        SpeciesSearchRequest(inchi="InChI=1S/H2O/h1H2"),
    )

    assert response.records == []
    assert response.pagination.total == 0


def test_search_by_inchi_with_smiles_still_filters_by_smiles(db_session):
    # inchi cannot be verified, but a co-supplied real identifier (smiles)
    # still narrows the query as usual — inchi is echoed, not filtered.
    species = make_species(db_session, smiles="CCO", inchi_key=next_inchi_key("INCHIWS"))
    make_species_entry(db_session, species)

    response = search_species(
        db_session,
        SpeciesSearchRequest(smiles="CCO", inchi="InChI=1S/C2H6O/..."),
    )

    assert len(response.records) == 1
    assert response.records[0].species_id == species.id


# ---------------------------------------------------------------------------
# Default trust posture
# ---------------------------------------------------------------------------


def test_default_excludes_rejected_and_deprecated(db_session):
    species = make_species(db_session, smiles="C", inchi_key=next_inchi_key("REJ"))
    e_approved = make_species_entry(db_session, species)
    e_rejected = make_species_entry(
        db_session, species, electronic_state_kind=SpeciesEntryStateKind.excited
    )
    set_review(
        db_session,
        record_type=SubmissionRecordType.species_entry,
        record_id=e_approved.id,
        status=RecordReviewStatus.approved,
    )
    set_review(
        db_session,
        record_type=SubmissionRecordType.species_entry,
        record_id=e_rejected.id,
        status=RecordReviewStatus.rejected,
    )

    response = search_species(db_session, SpeciesSearchRequest(smiles="C"))

    assert len(response.records) == 1
    entry_ids = [e.species_entry_id for e in response.records[0].entries]
    assert e_approved.id in entry_ids
    assert e_rejected.id not in entry_ids


def test_include_rejected_surfaces_rejected_entries(db_session):
    species = make_species(db_session, smiles="N", inchi_key=next_inchi_key("REJINC"))
    e_rejected = make_species_entry(db_session, species)
    set_review(
        db_session,
        record_type=SubmissionRecordType.species_entry,
        record_id=e_rejected.id,
        status=RecordReviewStatus.rejected,
    )

    response = search_species(
        db_session, SpeciesSearchRequest(smiles="N", include_rejected=True)
    )

    entry_ids = [e.species_entry_id for e in response.records[0].entries]
    assert e_rejected.id in entry_ids


def test_min_review_status_approved_filters_to_approved(db_session):
    species = make_species(db_session, smiles="O", inchi_key=next_inchi_key("MIN"))
    e_approved = make_species_entry(db_session, species)
    e_under = make_species_entry(
        db_session, species, electronic_state_kind=SpeciesEntryStateKind.excited
    )
    set_review(
        db_session,
        record_type=SubmissionRecordType.species_entry,
        record_id=e_approved.id,
        status=RecordReviewStatus.approved,
    )
    set_review(
        db_session,
        record_type=SubmissionRecordType.species_entry,
        record_id=e_under.id,
        status=RecordReviewStatus.under_review,
    )

    response = search_species(
        db_session,
        SpeciesSearchRequest(smiles="O", min_review_status=RecordReviewStatus.approved),
    )

    entry_ids = [e.species_entry_id for e in response.records[0].entries]
    assert e_approved.id in entry_ids
    assert e_under.id not in entry_ids


# ---------------------------------------------------------------------------
# Empty result, pagination, sort
# ---------------------------------------------------------------------------


def test_empty_result_returns_empty_records_not_404(db_session):
    response = search_species(
        db_session, SpeciesSearchRequest(smiles="THIS_DOES_NOT_EXIST_SMILES")
    )
    assert response.records == []
    assert response.pagination.total == 0
    assert response.pagination.returned == 0


def test_pagination_default_limit(db_session):
    species = make_species(db_session, smiles="P", inchi_key=next_inchi_key("PAG"))
    make_species_entry(db_session, species)

    response = search_species(db_session, SpeciesSearchRequest(smiles="P"))

    assert response.pagination.offset == 0
    assert response.pagination.limit == 50


def test_sort_is_deterministic_across_two_calls(db_session):
    inchi_key = next_inchi_key("SORT")
    species = make_species(db_session, smiles="S", inchi_key=inchi_key)
    make_species_entry(db_session, species)

    r1 = search_species(db_session, SpeciesSearchRequest(smiles="S"))
    r2 = search_species(db_session, SpeciesSearchRequest(smiles="S"))

    assert r1.model_dump() == r2.model_dump()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_client_supplied_sort_rejected(db_session):
    with pytest.raises(ValueError, match="client_sort_not_supported"):
        search_species(db_session, SpeciesSearchRequest(smiles="X", sort="anything"))


def test_unknown_include_token_rejected(db_session):
    with pytest.raises(ValueError, match="unknown_include_token"):
        search_species(
            db_session, SpeciesSearchRequest(smiles="X", include=["banana"])
        )


def test_invalid_pagination_limit_rejected(db_session):
    with pytest.raises(ValueError, match="invalid_pagination"):
        search_species(db_session, SpeciesSearchRequest(smiles="X", limit=999))


# ---------------------------------------------------------------------------
# Availability + include sections
# ---------------------------------------------------------------------------


def test_availability_reports_thermo_when_attached(db_session):
    species = make_species(db_session, smiles="CCC", inchi_key=next_inchi_key("AV"))
    entry = make_species_entry(db_session, species)
    make_thermo_scalar(db_session, species_entry=entry)

    response = search_species(db_session, SpeciesSearchRequest(smiles="CCC"))

    avail = response.records[0].entries[0].availability
    assert avail.has_thermo is True
    assert avail.has_statmech is False


def test_include_thermo_populates_thermo_summary_with_ids(db_session):
    species = make_species(
        db_session, smiles="CCCC", inchi_key=next_inchi_key("INCT")
    )
    entry = make_species_entry(db_session, species)
    thermo = make_thermo_scalar(db_session, species_entry=entry)

    response = search_species(
        db_session,
        SpeciesSearchRequest(smiles="CCCC", include=["thermo"]),
    )

    summary = response.records[0].entries[0].thermo_summary
    assert summary is not None
    assert summary.ids == [thermo.id]


# ---------------------------------------------------------------------------
# Collapse / pagination total semantics
# ---------------------------------------------------------------------------


def test_collapse_first_returns_at_most_one_with_pre_collapse_total(db_session):
    # Two spin variants of the same structure: same smiles, different
    # multiplicity. Under DR-0031 these are distinct species (identity =
    # smiles + charge + multiplicity) that both match a by-smiles search,
    # so the search yields two pre-collapse candidates.
    species_a = make_species(
        db_session, smiles="C1", inchi_key=next_inchi_key("CO1"), multiplicity=1
    )
    make_species_entry(db_session, species_a)
    species_b = make_species(
        db_session, smiles="C1", inchi_key=next_inchi_key("CO2"), multiplicity=3
    )
    make_species_entry(db_session, species_b)

    response = search_species(
        db_session,
        SpeciesSearchRequest(smiles="C1", collapse=CollapseMode.first),
    )

    assert len(response.records) == 1
    # Pre-collapse total should reflect both candidates.
    assert response.pagination.total == 2
    assert response.pagination.returned == 1
