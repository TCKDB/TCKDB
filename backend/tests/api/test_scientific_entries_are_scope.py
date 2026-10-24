"""Species entries and reaction entries are scoping parents: a scientific search never drops a record because the entry it
sits on is unreviewed or rejected (the curated floor and the default exclusions alike). Only the species search's ENTRY
listing follows the entry visibility, because it is a list of entries.
"""

from __future__ import annotations

import pytest

from app.db.models.common import CalculationType, RecordReviewStatus, SubmissionRecordType
from tests.services.scientific_read._factories import (
    make_calculation,
    make_chem_reaction,
    make_kinetics,
    make_lot,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_thermo_scalar,
    next_inchi_key,
    set_review,
)

S = RecordReviewStatus
T = SubmissionRecordType
API = "/api/v1/scientific"
ENTRY_STATES = [None, S.rejected, S.deprecated]  # never reviewed (below the floor), rejected, deprecated


def _species_world(db_session, entry_status):
    species = make_species(db_session, smiles="C=O", inchi_key=next_inchi_key("SE"))
    entry = make_species_entry(db_session, species)
    if entry_status is not None:
        set_review(db_session, record_type=T.species_entry, record_id=entry.id, status=entry_status)
    thermo = make_thermo_scalar(db_session, species_entry=entry)
    set_review(db_session, record_type=T.thermo, record_id=thermo.id, status=S.approved)
    lot = make_lot(db_session)
    calc = make_calculation(db_session, type=CalculationType.sp, species_entry_id=entry.id, lot_id=lot.id)
    set_review(db_session, record_type=T.calculation, record_id=calc.id, status=S.approved)
    return entry, thermo, calc, lot


def _profiles():
    return [{}, {"profile": "curated"}]


@pytest.mark.parametrize("entry_status", ENTRY_STATES)
@pytest.mark.parametrize("profile", ["exploratory", "curated"])
def test_thermo_search_returns_an_approved_record_on_an_unreviewed_or_rejected_entry(
    entry_status, profile, client, db_session
):
    """Kills: discovering entries through the entry visibility (the search used to return 0 records)."""
    entry, thermo, _calc, _lot = _species_world(db_session, entry_status)
    for query in (f"species_entry_ref={entry.public_ref}", "smiles=C=O"):
        body = client.get(f"{API}/thermo/search?{query}&profile={profile}").json()
        assert [r["thermo"]["thermo_ref"] for r in body["records"]] == [thermo.public_ref], (query, body["records"])
        assert body["records"][0]["species"]["species_entry_ref"] == entry.public_ref  # the record names its entry


@pytest.mark.parametrize("entry_status", ENTRY_STATES)
@pytest.mark.parametrize("profile", ["exploratory", "curated"])
def test_species_calculations_search_returns_a_record_on_an_unreviewed_or_rejected_entry(
    entry_status, profile, client, db_session
):
    entry, _thermo, calc, lot = _species_world(db_session, entry_status)
    body = client.get(
        f"{API}/species-calculations/search?smiles=C=O&calculation_type=sp&level_of_theory_ref={lot.public_ref}"
        f"&profile={profile}"
    ).json()
    assert [r["calculation"]["calculation_ref"] for r in body["records"]] == [calc.public_ref], body


@pytest.mark.parametrize("entry_status", ENTRY_STATES)
@pytest.mark.parametrize("profile", ["exploratory", "curated"])
def test_kinetics_search_returns_an_approved_record_on_an_unreviewed_or_rejected_reaction_entry(
    entry_status, profile, client, db_session
):
    rs = make_species(db_session, smiles="CC", inchi_key=next_inchi_key("KR"))
    ps = make_species(db_session, smiles="CCC", inchi_key=next_inchi_key("KP"))
    chem = make_chem_reaction(db_session, reactants=[rs], products=[ps])
    reaction_entry = make_reaction_entry(
        db_session,
        reaction=chem,
        reactant_entries=[make_species_entry(db_session, rs)],
        product_entries=[make_species_entry(db_session, ps)],
    )
    if entry_status is not None:
        set_review(db_session, record_type=T.reaction_entry, record_id=reaction_entry.id, status=entry_status)
    kinetics = make_kinetics(db_session, reaction_entry=reaction_entry, a=1e-12)
    set_review(db_session, record_type=T.kinetics, record_id=kinetics.id, status=S.approved)
    for query in (f"reaction_entry_ref={reaction_entry.public_ref}", "reactants=CC"):
        body = client.get(f"{API}/kinetics/search?{query}&profile={profile}").json()
        assert [r["kinetics"]["kinetics_ref"] for r in body["records"]] == [kinetics.public_ref], (query, body)


def test_the_species_search_entry_listing_still_follows_the_entry_visibility(client, db_session):
    """It is a list of entries, so the owner rule is unchanged: a rejected entry is not listed by default."""
    entry, *_ = _species_world(db_session, S.rejected)
    body = client.get(f"{API}/species/search?smiles=C=O").json()
    entries = [e for r in body["records"] for e in r["entries"]]
    assert entry.public_ref not in [e["species_entry_ref"] for e in entries]
    body = client.get(f"{API}/species/search?smiles=C=O&include_rejected=true").json()
    assert entry.public_ref in [e["species_entry_ref"] for r in body["records"] for e in r["entries"]]
