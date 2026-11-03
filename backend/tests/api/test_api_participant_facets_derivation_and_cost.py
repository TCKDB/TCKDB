"""The served ``stereo_label`` is derived from the descriptor, and serving the facets costs no per-participant query.

Derivation: before the descriptor existed ``species_entry.stereo_label`` was free text (up to 64 characters), and a
contradicted legacy row kept whatever text it was deposited with (``stereo_identity_merge.py``). A participant must
never serve that text as a stereo mark, so the label is derived from the descriptor and resolution the way
``ResolvedStereo.label`` derives it.

Cost: the facets come from the same row that already built the joined label. The slope tests add participants and
require the statement count to stay put, so a facet lookup that quietly becomes one query per participant fails here.
"""

from __future__ import annotations

from sqlalchemy import event

from app.db.models.common import SpeciesEntryStateKind, StationaryPointKind, StereoResolution
from app.db.models.species import SpeciesEntry
from tests.services.scientific_read._factories import (
    make_chem_reaction,
    make_reaction_entry,
    make_species,
    next_inchi_key,
)

_LEGACY_TEXT = "cis-ish (typed, free text)"


def _entry(db_session, species, *, label, descriptor, resolution) -> SpeciesEntry:
    row = SpeciesEntry(
        species_id=species.id,
        kind=StationaryPointKind.minimum,
        stereo_label=label,
        stereo_descriptor=descriptor,
        stereo_resolution=resolution,
        electronic_state_kind=SpeciesEntryStateKind.ground,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _reaction(db_session, tag: str, *, entries_per_side: int = 1, label="E", descriptor="E", resolution=StereoResolution.resolved):
    """One reaction entry with ``entries_per_side`` distinct species entries on each side."""
    reactants, products, reactant_entries, product_entries = [], [], [], []
    for side, species_list, entry_list in (("r", reactants, reactant_entries), ("p", products, product_entries)):
        for index in range(entries_per_side):
            species = make_species(
                db_session, smiles=f"C{'C' * index}={side}{tag}", inchi_key=next_inchi_key(f"{tag}{side}{index}"), multiplicity=1
            )
            species_list.append(species)
            entry_list.append(_entry(db_session, species, label=label, descriptor=descriptor, resolution=resolution))
    reaction = make_chem_reaction(db_session, reactants=reactants, products=products)
    return make_reaction_entry(
        db_session, reaction=reaction, reactant_entries=reactant_entries, product_entries=product_entries
    )


def _statements(db_session, call) -> int:
    count = 0
    bind = db_session.get_bind()

    def capture(*_args):
        nonlocal count
        count += 1

    event.listen(bind, "before_cursor_execute", capture)
    try:
        call()
    finally:
        event.remove(bind, "before_cursor_execute", capture)
    return count


def _search(client, ref: str):
    response = client.get("/api/v1/scientific/reactions/search", params={"reaction_entry_ref": ref})
    assert response.status_code == 200, response.text
    return response


def _full(client, ref: str):
    response = client.get(f"/api/v1/scientific/reaction-entries/{ref}/full", params={"include": "species"})
    assert response.status_code == 200, response.text
    return response


def test_a_legacy_free_text_stereo_label_is_never_served(client, db_session) -> None:
    """Raw free text in ``stereo_label`` beside descriptor ``?`` serves None, not the text."""
    entry = _reaction(
        db_session, "legacy", label=_LEGACY_TEXT, descriptor="?", resolution=StereoResolution.unresolved
    )
    ref = entry.public_ref

    record = _search(client, ref).json()["records"][0]
    full = _full(client, ref).json()["species"]
    for participant in (*record["reactants"], *record["products"], *full["reactants"], *full["products"]):
        assert participant["stereo_descriptor"] == "?"
        assert participant["stereo_resolution"] == "unresolved"
        assert participant["stereo_label"] is None
    assert _LEGACY_TEXT not in _search(client, ref).text
    assert _LEGACY_TEXT not in _full(client, ref).text


def test_a_resolved_descriptor_is_served_as_the_label_whatever_the_raw_column_holds(client, db_session) -> None:
    entry = _reaction(db_session, "mismatch", label=_LEGACY_TEXT, descriptor="E", resolution=StereoResolution.resolved)

    participant = _search(client, entry.public_ref).json()["records"][0]["reactants"][0]
    assert participant["stereo_label"] == "E"
    assert participant["stereo_descriptor"] == "E"


def test_reaction_search_statements_do_not_grow_with_participants(client, db_session) -> None:
    small = _reaction(db_session, "small", entries_per_side=1)
    large = _reaction(db_session, "large", entries_per_side=4)

    # Warm any per-process caches (ref listeners, vocab) so the two measurements compare like with like.
    _search(client, small.public_ref)
    few = _statements(db_session, lambda: _search(client, small.public_ref))
    many = _statements(db_session, lambda: _search(client, large.public_ref))
    assert many == few, (few, many)


def test_reaction_full_statements_do_not_grow_with_participants(client, db_session) -> None:
    small = _reaction(db_session, "fsmall", entries_per_side=1)
    large = _reaction(db_session, "flarge", entries_per_side=4)

    _full(client, small.public_ref)
    few = _statements(db_session, lambda: _full(client, small.public_ref))
    many = _statements(db_session, lambda: _full(client, large.public_ref))
    assert many == few, (few, many)
