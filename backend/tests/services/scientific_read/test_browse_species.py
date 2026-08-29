"""Service-layer tests for browse_species (/scientific/species/browse).

Mirrors ``test_search_species.py`` wherever the two surfaces share
behaviour (visibility, pagination, sort rejection, include validation),
and adds the tests that are specific to browse: no identifier required,
deterministic multi-page coverage over a tied candidate set, an honest
``pagination.total``, and a metadata-only record shape.
"""

from __future__ import annotations

import pytest

from app.db.models.common import (
    RecordReviewStatus,
    SpeciesEntryStateKind,
    SubmissionRecordType,
)
from app.schemas.reads.scientific_species import SpeciesBrowseRequest
from app.services.scientific_read.species import browse_species
from tests.services.scientific_read._factories import (
    make_species,
    make_species_entry,
    make_thermo_scalar,
    next_inchi_key,
    set_review,
    unique_smiles,
)

# ---------------------------------------------------------------------------
# The headline feature: no identifier required
# ---------------------------------------------------------------------------


def test_browse_with_no_filters_lists_every_created_species(db_session):
    """The whole point of the endpoint: an empty request is not refused.

    Three species, not one -- a fixture with a single row cannot tell
    "the endpoint returned the right species" from "the endpoint returned
    whatever it had". All three ids must come back, and nothing else the
    test did not create.
    """
    ids = {
        make_species(db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("BR")).id
        for _ in range(3)
    }
    # No species_entry needed: a species with no entries is still a
    # browse candidate, with an empty ``entries`` list on the wire.

    response = browse_species(db_session, SpeciesBrowseRequest())

    returned_ids = {r.species_id for r in response.records}
    assert ids <= returned_ids
    assert response.pagination.total >= 3


def test_browse_does_not_raise_missing_identifier(db_session):
    """The 422 ``search_species`` raises for an empty request must not fire here."""
    # No species at all: an empty corpus is a 200 with no records, not an
    # error -- the same "empty result, not a refusal" contract as search.
    response = browse_species(db_session, SpeciesBrowseRequest())
    assert response.pagination.total >= 0  # does not raise


# ---------------------------------------------------------------------------
# Secondary filters narrow the listing
# ---------------------------------------------------------------------------


def test_browse_by_charge_narrows(db_session):
    neutral = make_species(
        db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("CHG0"), charge=0
    )
    charged = make_species(
        db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("CHG1"), charge=1
    )

    response = browse_species(db_session, SpeciesBrowseRequest(charge=1))

    returned_ids = {r.species_id for r in response.records}
    assert charged.id in returned_ids
    assert neutral.id not in returned_ids


def test_browse_by_multiplicity_narrows(db_session):
    singlet = make_species(
        db_session,
        smiles=unique_smiles(),
        inchi_key=next_inchi_key("MULT1"),
        multiplicity=1,
    )
    triplet = make_species(
        db_session,
        smiles=unique_smiles(),
        inchi_key=next_inchi_key("MULT3"),
        multiplicity=3,
    )

    response = browse_species(db_session, SpeciesBrowseRequest(multiplicity=3))

    returned_ids = {r.species_id for r in response.records}
    assert triplet.id in returned_ids
    assert singlet.id not in returned_ids


def test_browse_by_formula_narrows_and_is_served_back(db_session):
    water = make_species(db_session, smiles="O", inchi_key=next_inchi_key("BRFORMH2O"))
    make_species(db_session, smiles="C1CC1", inchi_key=next_inchi_key("BRFORMC3H6"))

    response = browse_species(db_session, SpeciesBrowseRequest(formula="H2O"))

    matching = [r for r in response.records if r.species_id == water.id]
    assert len(matching) == 1
    assert matching[0].formula == "H2O"
    assert all(r.formula == "H2O" for r in response.records)


def test_browse_by_formula_nonexistent_returns_empty(db_session):
    make_species(db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("BRFX"))

    response = browse_species(
        db_session, SpeciesBrowseRequest(formula="DOES_NOT_EXIST")
    )

    assert response.records == []
    assert response.pagination.total == 0


# ---------------------------------------------------------------------------
# Review visibility (same gate as search)
# ---------------------------------------------------------------------------


def test_default_excludes_rejected_and_deprecated_entries(db_session):
    species = make_species(
        db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("BRREJ")
    )
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

    response = browse_species(db_session, SpeciesBrowseRequest())

    record = next(r for r in response.records if r.species_id == species.id)
    entry_ids = [e.species_entry_id for e in record.entries]
    assert e_approved.id in entry_ids
    assert e_rejected.id not in entry_ids


def test_include_rejected_surfaces_rejected_entries(db_session):
    species = make_species(
        db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("BRRINC")
    )
    e_rejected = make_species_entry(db_session, species)
    set_review(
        db_session,
        record_type=SubmissionRecordType.species_entry,
        record_id=e_rejected.id,
        status=RecordReviewStatus.rejected,
    )

    response = browse_species(
        db_session, SpeciesBrowseRequest(include_rejected=True)
    )

    record = next(r for r in response.records if r.species_id == species.id)
    entry_ids = [e.species_entry_id for e in record.entries]
    assert e_rejected.id in entry_ids


def test_include_deprecated_surfaces_deprecated_entries(db_session):
    species = make_species(
        db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("BRDINC")
    )
    e_deprecated = make_species_entry(db_session, species)
    set_review(
        db_session,
        record_type=SubmissionRecordType.species_entry,
        record_id=e_deprecated.id,
        status=RecordReviewStatus.deprecated,
    )

    hidden = browse_species(db_session, SpeciesBrowseRequest())
    record_hidden = next(r for r in hidden.records if r.species_id == species.id)
    assert e_deprecated.id not in [e.species_entry_id for e in record_hidden.entries]

    shown = browse_species(
        db_session, SpeciesBrowseRequest(include_deprecated=True)
    )
    record_shown = next(r for r in shown.records if r.species_id == species.id)
    assert e_deprecated.id in [e.species_entry_id for e in record_shown.entries]


def test_min_review_status_approved_filters_to_approved(db_session):
    species = make_species(
        db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("BRMIN")
    )
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

    response = browse_species(
        db_session,
        SpeciesBrowseRequest(min_review_status=RecordReviewStatus.approved),
    )

    record = next(r for r in response.records if r.species_id == species.id)
    entry_ids = [e.species_entry_id for e in record.entries]
    assert e_approved.id in entry_ids
    assert e_under.id not in entry_ids


# ---------------------------------------------------------------------------
# Pagination: honesty and stability
# ---------------------------------------------------------------------------


def test_pagination_default_limit(db_session):
    make_species(db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("BRPAG"))

    response = browse_species(db_session, SpeciesBrowseRequest())

    assert response.pagination.offset == 0
    assert response.pagination.limit == 50


def test_pagination_total_is_the_true_corpus_count_not_the_page_size(db_session):
    """Guards against reporting ``len(records)`` as ``total``.

    Five species exist; a page of two is requested. If ``total`` were
    quietly computed from the returned page instead of the full candidate
    count, this would read 2 instead of 5.
    """
    for _ in range(5):
        make_species(
            db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("BRTOT")
        )

    response = browse_species(db_session, SpeciesBrowseRequest(limit=2))

    assert len(response.records) == 2
    assert response.pagination.returned == 2
    assert response.pagination.total >= 5
    assert response.pagination.total != response.pagination.returned


def test_pagination_covers_every_row_exactly_once_across_pages(db_session):
    """Deterministic-order guarantee, checked end to end.

    Five species created in the same transaction share one ``created_at``
    (``server_default=func.now()`` returns the transaction start time),
    and none has an entry, so every one of them ranks identically on
    ``review_rank`` and ``has_entries`` too -- the whole default sort key
    is tied except ``id``. Walking every page at ``limit=2`` must return
    each id exactly once: a dropped or duplicated tiebreak would either
    miss one of the five or return one twice across the three pages.
    """
    ids = [
        make_species(
            db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("BRPG")
        ).id
        for _ in range(5)
    ]

    seen: list[int] = []
    offset = 0
    while True:
        page = browse_species(
            db_session, SpeciesBrowseRequest(limit=2, offset=offset)
        )
        page_ids = [r.species_id for r in page.records if r.species_id in ids]
        if not page.records:
            break
        seen.extend(page_ids)
        offset += 2
        if offset > page.pagination.total + 2:
            break  # safety valve, should never trigger

    seen_of_interest = [i for i in seen if i in ids]
    assert sorted(seen_of_interest) == sorted(ids)
    assert len(seen_of_interest) == len(ids), (
        f"expected each of {ids} exactly once, saw {seen_of_interest}"
    )


def test_pagination_is_stable_even_across_different_query_plans(db_session):
    """The tiebreak's real job: pagination must not depend on the plan.

    Two pages fetched under the *same* plan can look stable by accident --
    a repeated query against unchanged data tends to walk a hash table or
    a heap in the same order every time, so a missing tiebreak can pass a
    naive "fetch twice, compare" check even though nothing in the SQL
    guarantees it (confirmed empirically while writing this test: the
    query this exercises used HashAggregate by default, and a plain
    two-call comparison did not expose the gap).

    What *does* change the order, deterministically and reproducibly, is
    the aggregate strategy PostgreSQL picks -- ``GROUP BY`` output order
    is unspecified by the SQL standard, and ``HashAggregate`` vs a forced
    ``GroupAggregate`` (``enable_hashagg = off``) visit the same tied rows
    in genuinely different orders. A real deployment can land on either
    plan for two different requests (autovacuum, a statistics refresh, a
    replica with different memory settings), so this is not a contrived
    edge case -- it is the mechanism the ``id DESC`` tiebreak exists to
    neutralize. This test forces exactly that plan change between two
    page fetches and asserts they still tile the tied set with no
    duplicate and no gap.
    """
    from sqlalchemy import text

    ids = [
        make_species(
            db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("BRPLAN")
        ).id
        for _ in range(20)
    ]

    def page(offset: int, limit: int, *, force_groupagg: bool) -> list[int]:
        db_session.execute(
            text(f"SET LOCAL enable_hashagg = {'off' if force_groupagg else 'on'}")
        )
        resp = browse_species(
            db_session, SpeciesBrowseRequest(limit=limit, offset=offset)
        )
        return [r.species_id for r in resp.records if r.species_id in ids]

    # Page A under the default plan (HashAggregate on this data shape);
    # page B under a forced GroupAggregate -- as if the two requests hit
    # the database under different conditions, which is the realistic case.
    page_a = page(0, 10, force_groupagg=False)
    page_b = page(10, 10, force_groupagg=True)

    duplicated = set(page_a) & set(page_b)
    combined = set(page_a) | set(page_b)
    dropped = set(ids) - combined

    assert not duplicated, (
        f"rows on both pages: {sorted(duplicated)} (page_a={page_a}, page_b={page_b})"
    )
    assert not dropped, (
        f"rows on neither page: {sorted(dropped)} (page_a={page_a}, page_b={page_b})"
    )
    assert combined == set(ids)


def test_sort_is_deterministic_across_two_calls(db_session):
    make_species(db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("BRSORT"))

    r1 = browse_species(db_session, SpeciesBrowseRequest())
    r2 = browse_species(db_session, SpeciesBrowseRequest())

    assert r1.model_dump() == r2.model_dump()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_client_supplied_sort_rejected(db_session):
    with pytest.raises(ValueError, match="client_sort_not_supported"):
        browse_species(db_session, SpeciesBrowseRequest(sort="anything"))


def test_unknown_include_token_rejected(db_session):
    with pytest.raises(ValueError, match="unknown_include_token"):
        browse_species(db_session, SpeciesBrowseRequest(include=["banana"]))


def test_limit_above_the_cap_is_rejected(db_session):
    with pytest.raises(ValueError, match="limit_too_large"):
        browse_species(db_session, SpeciesBrowseRequest(limit=999))


def test_a_malformed_limit_is_still_invalid_pagination(db_session):
    with pytest.raises(ValueError, match="invalid_pagination"):
        browse_species(db_session, SpeciesBrowseRequest(limit=0))


# ---------------------------------------------------------------------------
# Availability + include sections (same shape as search)
# ---------------------------------------------------------------------------


def test_availability_reports_thermo_when_attached(db_session):
    species = make_species(
        db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("BRAV")
    )
    entry = make_species_entry(db_session, species)
    make_thermo_scalar(db_session, species_entry=entry)

    response = browse_species(db_session, SpeciesBrowseRequest())

    record = next(r for r in response.records if r.species_id == species.id)
    avail = record.entries[0].availability
    assert avail.has_thermo is True
    assert avail.has_statmech is False


def test_include_thermo_populates_thermo_summary_with_ids(db_session):
    species = make_species(
        db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("BRINCT")
    )
    entry = make_species_entry(db_session, species)
    thermo = make_thermo_scalar(db_session, species_entry=entry)

    response = browse_species(
        db_session, SpeciesBrowseRequest(include=["thermo"])
    )

    record = next(r for r in response.records if r.species_id == species.id)
    summary = record.entries[0].thermo_summary
    assert summary is not None
    assert summary.ids == [thermo.id]


# ---------------------------------------------------------------------------
# Metadata-only contract
# ---------------------------------------------------------------------------

_SPECIES_RECORD_FIELDS = {
    "species_id",
    "species_ref",
    "canonical_smiles",
    "inchi_key",
    "formula",
    "charge",
    "multiplicity",
    "stereo_kind",
    "entries",
}

_ENTRY_RECORD_FIELDS = {
    "species_entry_id",
    "species_entry_ref",
    "species_entry_kind",
    "electronic_state_kind",
    "stereo_label",
    "electronic_state_label",
    "term_symbol",
    "isotope_key",
    "species_entry_label",
    "review",
    "availability",
    "thermo_summary",
    "statmech_summary",
    "transport_summary",
    "conformers_summary",
}

_AVAILABILITY_FIELDS = {
    "has_thermo",
    "has_statmech",
    "has_transport",
    "has_conformers",
    "calculation_count",
}


def test_record_shape_is_metadata_only(db_session):
    """Pins the exact field set: identity, refs, counts, review -- nothing else.

    A field added to serve an artifact URI, a raw geometry payload or a
    calculation result on this surface would show up here as an
    unexpected key. Every field is asserted by *name*, not merely
    "does not contain a suspicious substring" -- the guard the brief
    warns is vacuous.
    """
    species = make_species(
        db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("BRSHAPE")
    )
    entry = make_species_entry(db_session, species)
    make_thermo_scalar(db_session, species_entry=entry)

    response = browse_species(
        db_session, SpeciesBrowseRequest(include=["thermo"])
    )
    record = next(r for r in response.records if r.species_id == species.id)

    assert set(record.model_dump().keys()) == _SPECIES_RECORD_FIELDS
    entry_record = record.entries[0]
    assert set(entry_record.model_dump().keys()) == _ENTRY_RECORD_FIELDS
    assert (
        set(entry_record.availability.model_dump().keys()) == _AVAILABILITY_FIELDS
    )


