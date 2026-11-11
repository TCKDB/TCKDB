"""Loading a reviewed species-name file: matching, idempotency, guards, dry run, and name search finding the result."""

from __future__ import annotations

import itertools
import string
from datetime import datetime

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.db.models.common import SpeciesNameKind, SpeciesNameSourceKind
from app.db.models.external_source import ExternalSource
from app.db.models.species_name import SpeciesName
from app.importers.pubchem_names.fetch import REVIEW_FORMAT
from app.services.species_name_import import ReviewFileError, import_species_names
from app.services.species_name_normalisation import normalise_name
from tests.services.scientific_read._factories import make_species, make_species_entry

_COUNTER = itertools.count(1)


def _key() -> str:
    n = next(_COUNTER)
    letters = "".join(string.ascii_uppercase[(n // 26**i) % 26] for i in range(4))
    return f"IMPORTTEST{letters}-ABCDEFGHIJ-N"


def _species(db_session, *, entry=True):
    sp = make_species(db_session, inchi_key=_key())
    if entry:
        make_species_entry(db_session, sp)
    return sp


def _row(sp, name="Ammonia", kind="common", cid=222, **over):
    row = {"species_ref": sp.public_ref, "inchi_key": sp.inchi_key, "name": name, "kind": kind, "cid": cid,
           "url": f"https://pubchem.ncbi.nlm.nih.gov/compound/{cid}", "retrieved_at": "2026-11-11T10:30:00Z"}
    row.update(over)
    return row


def _doc(*rows):
    return {"format": REVIEW_FORMAT, "source": "PubChem", "rows": list(rows), "skipped": []}


def _names(db_session, sp):
    return list(db_session.scalars(select(SpeciesName).where(SpeciesName.species_id == sp.id).order_by(SpeciesName.id)))


def _reasons(report):
    return [r.reason for r in report.rows]


def test_an_import_writes_offline_import_names_with_the_pubchem_source_cid_and_retrieval_time(db_session):
    sp = _species(db_session)
    report = import_species_names(db_session, _doc(_row(sp), _row(sp, "azane", "systematic")), dry_run=False)
    assert [r.outcome for r in report.rows] == ["inserted", "inserted"]
    rows = _names(db_session, sp)
    assert [(r.name, r.kind, r.source_kind, r.name_key) for r in rows] == [
        ("Ammonia", SpeciesNameKind.common, SpeciesNameSourceKind.offline_import, "ammonia"),
        ("azane", SpeciesNameKind.systematic, SpeciesNameSourceKind.offline_import, "azane"),
    ]
    sources = {db_session.get(ExternalSource, r.external_source_id) for r in rows}
    assert len(sources) == 1
    (source,) = sources
    assert (source.source_name, source.source_release) == ("PubChem Compound", "rolling")
    assert all(r.external_record_key == "222" and r.retrieved_at == datetime(2026, 11, 11, 10, 30) for r in rows)
    assert all(r.created_by_user_id is None and r.retired_at is None for r in rows)


def test_species_with_different_cids_share_one_source_and_each_name_keeps_its_own_cid_and_time(db_session):
    a, b = _species(db_session), _species(db_session)
    import_species_names(
        db_session,
        _doc(_row(a, cid=9001, retrieved_at="2026-11-11T01:00:00Z"), _row(b, cid=9002, retrieved_at="2026-11-12T02:00:00+00:00")),
        dry_run=False,
    )
    (na,), (nb,) = _names(db_session, a), _names(db_session, b)
    assert na.external_source_id == nb.external_source_id
    assert (na.external_record_key, na.retrieved_at) == ("9001", datetime(2026, 11, 11, 1, 0))
    assert (nb.external_record_key, nb.retrieved_at) == ("9002", datetime(2026, 11, 12, 2, 0))
    assert db_session.scalar(
        select(func.count()).select_from(ExternalSource).where(ExternalSource.source_name == "PubChem Compound")
    ) == 1


@pytest.mark.parametrize("value", [None, "", "yesterday", 5])
def test_a_row_without_a_usable_retrieval_time_is_skipped(db_session, value):
    sp = _species(db_session)
    report = import_species_names(db_session, _doc(_row(sp, retrieved_at=value)), dry_run=False)
    assert _reasons(report) == ["bad_row"] and _names(db_session, sp) == []


def test_the_database_refuses_an_offline_import_without_a_record_key_and_a_curated_name_with_one(db_session):
    sp = _species(db_session)
    source = ExternalSource(source_name="PubChem Compound", source_release="rolling-check")
    db_session.add(source)
    db_session.flush()

    def attempt(**fields):
        base = {"species_id": sp.id, "name": "x one", "name_key": "x one", "kind": SpeciesNameKind.common}
        with pytest.raises(IntegrityError), db_session.begin_nested():
            db_session.add(SpeciesName(**{**base, **fields}))
            db_session.flush()

    attempt(source_kind=SpeciesNameSourceKind.offline_import)
    attempt(source_kind=SpeciesNameSourceKind.offline_import, external_source_id=source.id, retrieved_at=datetime(2026, 1, 1))
    attempt(source_kind=SpeciesNameSourceKind.offline_import, external_source_id=source.id, external_record_key="1")
    attempt(source_kind=SpeciesNameSourceKind.curated, external_record_key="1")
    attempt(source_kind=SpeciesNameSourceKind.curated, retrieved_at=datetime(2026, 1, 1))


def test_a_rerun_inserts_nothing_new(db_session):
    sp = _species(db_session)
    doc = _doc(_row(sp), _row(sp, "azane", "systematic"))
    import_species_names(db_session, doc, dry_run=False)
    before = db_session.scalar(select(func.count()).select_from(SpeciesName))
    sources = db_session.scalar(select(func.count()).select_from(ExternalSource))
    report = import_species_names(db_session, doc, dry_run=False)
    assert _reasons(report) == ["already_present", "already_present"]
    assert db_session.scalar(select(func.count()).select_from(SpeciesName)) == before
    assert db_session.scalar(select(func.count()).select_from(ExternalSource)) == sources


def test_a_curated_name_is_never_touched_and_an_equal_name_is_not_added(db_session):
    sp = _species(db_session)
    curated = SpeciesName(species_id=sp.id, name="Ammonia", name_key="ammonia", kind=SpeciesNameKind.common,
                          source_kind=SpeciesNameSourceKind.curated)
    db_session.add(curated)
    db_session.flush()
    report = import_species_names(db_session, _doc(_row(sp, "AMMONIA", "systematic")), dry_run=False)
    assert _reasons(report) == ["already_present"]
    (only,) = _names(db_session, sp)
    assert (only.name, only.kind, only.source_kind, only.external_source_id) == (
        "Ammonia", SpeciesNameKind.common, SpeciesNameSourceKind.curated, None)


def test_a_retired_name_is_not_brought_back(db_session):
    sp = _species(db_session)
    import_species_names(db_session, _doc(_row(sp)), dry_run=False)
    db_session.execute(text("UPDATE species_name SET retired_at = now() WHERE species_id = :s"), {"s": sp.id})
    report = import_species_names(db_session, _doc(_row(sp)), dry_run=False)
    assert _reasons(report) == ["retired_name_not_restored"]
    (only,) = _names(db_session, sp)
    assert only.retired_at is not None


def test_an_inchikey_mismatch_is_skipped_and_reported(db_session):
    sp = _species(db_session)
    report = import_species_names(db_session, _doc(_row(sp, inchi_key="AAAAAAAAAAAAAA-AAAAAAAAAA-N")), dry_run=False)
    assert _reasons(report) == ["inchikey_mismatch"]
    assert _names(db_session, sp) == []


def test_an_unknown_species_ref_is_skipped(db_session):
    sp = _species(db_session)
    report = import_species_names(db_session, _doc(_row(sp, species_ref="spc_doesnotexist")), dry_run=False)
    assert _reasons(report) == ["species_not_found"]


def test_a_merged_species_is_reported_and_the_run_goes_on(db_session):
    survivor, loser, other = _species(db_session), _species(db_session, entry=False), _species(db_session)
    db_session.execute(text("UPDATE species SET merged_into_species_id = :s WHERE id = :l"),
                       {"s": survivor.id, "l": loser.id})
    db_session.expire_all()
    report = import_species_names(db_session, _doc(_row(loser), _row(other)), dry_run=False)
    assert _reasons(report) == ["species_merged", None]
    assert [r.outcome for r in report.rows] == ["skipped", "inserted"]
    assert _names(db_session, loser) == []


def test_the_database_trigger_is_the_backstop_for_a_merged_species(db_session, monkeypatch):
    """With the pre-check gone the trigger still refuses, and the import reports it instead of crashing."""
    survivor, loser = _species(db_session), _species(db_session, entry=False)
    db_session.execute(text("UPDATE species SET merged_into_species_id = :s WHERE id = :l"),
                       {"s": survivor.id, "l": loser.id})
    db_session.expire_all()
    monkeypatch.setattr("app.services.species_name_import._is_merged", lambda species: False)
    report = import_species_names(db_session, _doc(_row(loser)), dry_run=False)
    assert (report.rows[0].outcome, report.rows[0].reason) == ("skipped", "species_merged")
    assert _names(db_session, loser) == []


def test_dry_run_reports_exactly_what_it_would_write_and_writes_nothing(db_session):
    sp = _species(db_session)
    doc = _doc(_row(sp, cid=777001), _row(sp, "azane", "systematic", cid=777001))
    names = db_session.scalar(select(func.count()).select_from(SpeciesName))
    sources = db_session.scalar(select(func.count()).select_from(ExternalSource))
    dry = import_species_names(db_session, doc, dry_run=True)
    assert [r.outcome for r in dry.rows] == ["would_insert", "would_insert"]
    assert db_session.scalar(select(func.count()).select_from(SpeciesName)) == names
    assert db_session.scalar(select(func.count()).select_from(ExternalSource)) == sources
    real = import_species_names(db_session, doc, dry_run=False)
    assert [(r.species_ref, r.name, r.kind) for r in real.rows] == [(r.species_ref, r.name, r.kind) for r in dry.rows]
    assert [r.outcome for r in real.rows] == ["inserted", "inserted"]


@pytest.mark.parametrize(
    ("over", "reason"),
    [
        ({"name": "bad<name>"}, "name_characters"),
        ({"name": "---"}, "name_no_letter_or_digit"),
        ({"name": "x" * 129}, "name_length"),
        ({"kind": "abbreviation"}, "bad_row"),
        ({"cid": 0}, "bad_row"),
        ({"cid": True}, "bad_row"),
        ({"inchi_key": "nope"}, "bad_row"),
    ],
)
def test_a_row_failing_validation_is_skipped_even_when_the_review_file_was_edited(db_session, over, reason):
    sp = _species(db_session)
    report = import_species_names(db_session, _doc(_row(sp, **over)), dry_run=False)
    assert _reasons(report) == [reason]
    assert _names(db_session, sp) == []


def test_a_file_in_another_format_is_refused(db_session):
    with pytest.raises(ReviewFileError):
        import_species_names(db_session, {"format": "other", "rows": []}, dry_run=False)
    with pytest.raises(ReviewFileError):
        import_species_names(db_session, [], dry_run=False)


def test_name_search_finds_an_imported_name_and_says_where_it_came_from(client, db_session):
    sp = _species(db_session)
    import_species_names(db_session, _doc(_row(sp, "Ammonia Test Spirit")), dry_run=False)
    response = client.get("/api/v1/scientific/species/name-search", params={"q": "ammonia test", "match": "prefix"})
    assert response.status_code == 200, response.text
    (match,) = [m for m in response.json()["matches"] if m["species_ref"] == sp.public_ref]
    assert (match["name"], match["kind"], match["source_kind"]) == ("Ammonia Test Spirit", "common", "offline_import")
    assert normalise_name(match["name"]) == "ammonia test spirit"


def test_a_species_whose_inchikey_is_shared_with_another_live_species_is_not_named(db_session):
    """Singlet and triplet methylene share an InChIKey; PubChem cannot say which one a name belongs to."""
    key = _key()
    singlet = make_species(db_session, smiles="[CH2]", inchi_key=key, multiplicity=1)
    triplet = make_species(db_session, smiles="[CH2]", inchi_key=key, multiplicity=3)
    other = _species(db_session)
    report = import_species_names(db_session, _doc(_row(singlet), _row(triplet), _row(other)), dry_run=False)
    assert _reasons(report) == ["inchikey_shared_by_species", "inchikey_shared_by_species", None]
    assert _names(db_session, singlet) == [] and _names(db_session, triplet) == []


def test_a_merged_away_species_does_not_make_an_inchikey_shared(db_session):
    survivor, loser = _species(db_session), _species(db_session, entry=False)
    db_session.execute(
        text("UPDATE species SET merged_into_species_id = :s, inchi_key = :k WHERE id = :l"),
        {"s": survivor.id, "l": loser.id, "k": survivor.inchi_key},
    )
    db_session.expire_all()
    report = import_species_names(db_session, _doc(_row(survivor)), dry_run=False)
    assert [r.outcome for r in report.rows] == ["inserted"]
