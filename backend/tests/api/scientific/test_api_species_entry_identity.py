"""Two entries of one species must never come back indistinguishable.

The defect this file exists for was live and anonymous. ``GET
/api/v1/scientific/species/search?smiles=N=N`` returned two records whose
*every served field* was byte-identical apart from an opaque
``species_entry_ref``:

    {"species_entry_ref": "spe_cft35qrk...", "species_entry_kind": "minimum",
     "electronic_state_kind": "ground", "availability": {...}, "review": {...}}
    {"species_entry_ref": "spe_qefrbgmp...", "species_entry_kind": "minimum",
     "electronic_state_kind": "ground", "availability": {...}, "review": {...}}

The database knew perfectly well what they were: ``stereo_label = Z``
(cis-diazene) and ``stereo_label = E`` (trans-diazene). Different molecules,
different thermochemistry. A reader who wanted diazene statmech picked a ref
from a list of two identical rows and had even odds of citing the isomer they
did not want -- and nothing on the wire hinted that anything had been
withheld. That is a wrong scientific answer, not a missing feature.

The fixtures below rebuild that shape from scratch rather than querying the
deployed database, so the guarantee does not evaporate the day the hydrazine
network is re-seeded.

What each test pins:

``test_two_entries_...`` -- the justifying case, once per surface. It asserts
    the two records differ, and *how*: dropping the identity fields from the
    projection makes the served dicts equal and fails it.
``test_unlabelled_...`` -- an entry with no label renders as ``None``: the
    key is present (so a reader can tell "there is none" from "I did not ask
    for it") and it is not ``""``.
``test_..._agrees_with_network`` -- the species surface and the
    pressure-dependent network surface derive the label from the same
    function. Two functions producing species labels that can disagree is a
    failure this project has hit repeatedly.
"""

from __future__ import annotations

import pytest

from app.db.models.common import SpeciesEntryStateKind, StereoKind
from app.services.scientific_read.species_identity import (
    species_entry_label,
    species_entry_label_for,
)
from tests.services.scientific_read._factories import (
    make_species,
    make_species_entry,
    make_statmech,
    next_inchi_key,
)

#: Every identity field the entry block gained, in the order the schema
#: declares them. Named once so a test that means "all of them" cannot
#: quietly enumerate fewer.
IDENTITY_FIELDS = (
    "stereo_label",
    "electronic_state_label",
    "term_symbol",
    "isotope_key",
    "species_entry_label",
)


@pytest.fixture
def diazene(db_session):
    """One ``N=N`` species carrying the cis and trans entries.

    ``stereo_kind=ez_isomer`` because that is what the species is: the
    parent row's own answer to "does this molecule have stereoisomers at
    all", which is what makes a *null* stereo label on some other species
    readable rather than ambiguous.
    """
    species = make_species(
        db_session,
        smiles="N=N",
        inchi_key=next_inchi_key("DZID"),
        multiplicity=1,
        stereo_kind=StereoKind.ez_isomer,
    )
    cis = make_species_entry(db_session, species, stereo_label="Z")
    trans = make_species_entry(db_session, species, stereo_label="E")
    assert cis.id != trans.id, "fixture built one entry, not two"
    return {"species": species, "cis": cis, "trans": trans}


def _species_record(body, species_ref):
    matching = [r for r in body["records"] if r["species_ref"] == species_ref]
    assert len(matching) == 1, f"expected one record for {species_ref}"
    return matching[0]


# ---------------------------------------------------------------------------
# /scientific/species/search
# ---------------------------------------------------------------------------


def test_two_entries_differing_only_by_stereo_label_are_distinguishable(
    client, diazene
):
    """The justifying test. Serve cis and trans; tell them apart."""
    resp = client.get("/api/v1/scientific/species/search?smiles=N%3DN")
    assert resp.status_code == 200

    record = _species_record(resp.json(), diazene["species"].public_ref)
    entries = record["entries"]
    assert len(entries) == 2

    by_ref = {e["species_entry_ref"]: e for e in entries}
    cis = by_ref[diazene["cis"].public_ref]
    trans = by_ref[diazene["trans"].public_ref]

    # The property first, so that *any* way of losing the discriminator --
    # dropping a field from the projection, substituting a constant,
    # rendering both as null -- trips this assertion and its message,
    # rather than an incidental one about a single field's value.
    def without_ref(entry):
        return {k: v for k, v in entry.items() if k != "species_entry_ref"}

    assert without_ref(cis) != without_ref(trans), (
        "cis- and trans-diazene are served as indistinguishable records; a "
        "reader picking a ref gets the wrong molecule half the time"
    )

    # Then which molecule is which, because "they differ" is not yet an
    # answer -- a projection that swapped the two labels would satisfy the
    # property above and still miscite.
    assert cis["stereo_label"] == "Z"
    assert trans["stereo_label"] == "E"

    # And the derived one-string spelling, so a consumer that renders a
    # label rather than a field still separates them.
    assert cis["species_entry_label"] == "Z"
    assert trans["species_entry_label"] == "E"


def test_species_record_carries_stereo_kind(client, diazene):
    """The parent species says whether stereoisomers exist at all."""
    resp = client.get("/api/v1/scientific/species/search?smiles=N%3DN")
    record = _species_record(resp.json(), diazene["species"].public_ref)
    assert record["stereo_kind"] == "ez_isomer"


def test_unlabelled_entry_serves_null_not_empty_string(client, db_session):
    """51 of 60 deployed entries have no stereo label. Say so, honestly."""
    species = make_species(
        db_session,
        smiles="PLAIN_ID",
        inchi_key=next_inchi_key("PLID"),
        stereo_kind=StereoKind.achiral,
    )
    make_species_entry(db_session, species)

    resp = client.get("/api/v1/scientific/species/search?smiles=PLAIN_ID")
    entry = _species_record(resp.json(), species.public_ref)["entries"][0]

    for field in IDENTITY_FIELDS:
        # Present, so "there is none" is distinguishable from "I did not
        # ask for it" -- these are default-projection fields, and an
        # absent key on this surface means an unrequested include section.
        assert field in entry, f"{field} was omitted, not served as null"
        # ``is None`` and not merely falsy: ``""`` is the tempting stand-in
        # and it reads as a label that happens to be blank.
        assert entry[field] is None, f"{field} was {entry[field]!r}, want None"

    # An empty tuple would sail through the loop above having checked
    # nothing, which is this repository's most-repeated test defect.
    assert len(IDENTITY_FIELDS) == 5, "IDENTITY_FIELDS stopped covering the set"


def test_identity_fields_need_no_include_token(client, diazene):
    """They are default projection, not an opt-in.

    A reader who does not know to ask is exactly the reader who gets the
    wrong molecule, so asking must not be the price of a right answer. The
    request below sends no ``include=`` at all and the echo proves it.
    """
    resp = client.get("/api/v1/scientific/species/search?smiles=N%3DN")
    body = resp.json()
    assert body["request"]["include"] == []

    record = _species_record(body, diazene["species"].public_ref)
    labels = {e["species_entry_label"] for e in record["entries"]}
    assert labels == {"E", "Z"}


# ---------------------------------------------------------------------------
# /scientific/species/structure-search
# ---------------------------------------------------------------------------


def test_structure_search_distinguishes_the_two_entries(client, diazene):
    """Same defect, flatter shape -- and worse, because there is no parent.

    Structure-search rows are species-entry grain and carry the *parent
    species'* ``smiles`` and ``inchi_key``, which are equal on both. Two
    sibling rows with nothing to separate them is the same wrong answer.
    """
    # ``substructure`` rather than ``exact``: exact mode matches on
    # ``species.inchi_key``, which the factory mints synthetically, so an
    # exact query would test the fixture's InChIKey rather than the
    # projection. Substructure runs against the cartridge ``mol`` column
    # the factory populates from the parent SMILES, which is the path
    # production takes.
    resp = client.get(
        "/api/v1/scientific/species/structure-search"
        "?query_smiles=N%3DN&mode=substructure"
    )
    assert resp.status_code == 200

    rows = {
        r["species_entry_ref"]: r
        for r in resp.json()["records"]
        if r["species_ref"] == diazene["species"].public_ref
    }
    cis = rows[diazene["cis"].public_ref]
    trans = rows[diazene["trans"].public_ref]

    # The property first, for the reason given on the species-search case:
    # every way of losing the discriminator should trip this one assertion.
    # ``endpoint`` is derived from the ref, so it goes with the refs.
    volatile = {"species_entry_ref", "species_entry_id", "endpoint"}

    def identity_of(row):
        return {k: v for k, v in row.items() if k not in volatile}

    assert identity_of(cis) != identity_of(trans), (
        "structure search serves cis- and trans-diazene as two rows that "
        "differ only in an opaque ref"
    )

    assert cis["stereo_label"] == "Z"
    assert trans["stereo_label"] == "E"
    assert cis["species_entry_label"] == "Z"
    assert trans["species_entry_label"] == "E"
    # Shared, because it is the parent species' -- which is exactly why it
    # cannot be the thing that separates them.
    assert cis["stereo_kind"] == trans["stereo_kind"] == "ez_isomer"


def test_structure_search_unlabelled_entry_serves_null(client, db_session):
    species = make_species(
        db_session,
        smiles="CCO",
        inchi_key=next_inchi_key("ETID"),
        stereo_kind=StereoKind.achiral,
    )
    make_species_entry(db_session, species)

    resp = client.get(
        "/api/v1/scientific/species/structure-search"
        "?query_smiles=CCO&mode=substructure"
    )
    rows = [
        r
        for r in resp.json()["records"]
        if r["species_ref"] == species.public_ref
    ]
    assert len(rows) == 1
    for field in IDENTITY_FIELDS:
        assert field in rows[0]
        assert rows[0][field] is None


# ---------------------------------------------------------------------------
# The surface where the number lives
# ---------------------------------------------------------------------------


def test_statmech_search_says_which_diazene_each_record_is_for(
    client, db_session, diazene
):
    """The harm, end to end: eight statmech records all reading ``N=N``.

    This is the scenario the fix exists for, not an analogue of it. A
    reader asks for diazene statmech, gets records for both entries, and
    every one of them reports ``canonical_smiles: "N=N"`` -- the *species'*
    graph identity, shared by both. Without the entry label they cannot
    tell which partition function belongs to which molecule, and the
    ``species_entry_ref`` that distinguishes them is opaque.
    """
    make_statmech(db_session, species_entry=diazene["cis"])
    make_statmech(db_session, species_entry=diazene["trans"])

    resp = client.get(
        "/api/v1/scientific/statmech/search",
        params={"species_ref": diazene["species"].public_ref},
    )
    assert resp.status_code == 200

    contexts = {
        r["species"]["species_entry_ref"]: r["species"]
        for r in resp.json()["records"]
    }
    cis = contexts[diazene["cis"].public_ref]
    trans = contexts[diazene["trans"].public_ref]

    # The species-level identity is equal on both, which is the whole
    # problem -- assert it rather than assume it.
    assert cis["canonical_smiles"] == trans["canonical_smiles"] == "N=N"

    assert cis["species_entry_label"] == "Z"
    assert trans["species_entry_label"] == "E"


def test_statmech_context_label_is_null_for_an_unlabelled_entry(
    client, db_session
):
    species = make_species(
        db_session,
        smiles="SM_PLAIN",
        inchi_key=next_inchi_key("SMPL"),
        stereo_kind=StereoKind.achiral,
    )
    entry = make_species_entry(db_session, species)
    make_statmech(db_session, species_entry=entry)

    resp = client.get(
        "/api/v1/scientific/statmech/search",
        params={"species_ref": species.public_ref},
    )
    context = resp.json()["records"][0]["species"]
    assert "species_entry_label" in context
    assert context["species_entry_label"] is None


# ---------------------------------------------------------------------------
# One derivation, three surfaces
# ---------------------------------------------------------------------------


def test_species_surface_label_agrees_with_network_surface(client, diazene):
    """The label a species search serves is the one a network state renders.

    ``app.services.scientific_read.network_channel_chemistry`` imports the
    derivation rather than defining a second one; this asserts the identity
    of the two rather than trusting the import to stay.
    """
    from app.services.scientific_read import network_channel_chemistry

    assert (
        network_channel_chemistry.species_entry_label is species_entry_label
    )

    resp = client.get("/api/v1/scientific/species/search?smiles=N%3DN")
    record = _species_record(resp.json(), diazene["species"].public_ref)
    served = {
        e["species_entry_ref"]: e["species_entry_label"]
        for e in record["entries"]
    }
    for entry in (diazene["cis"], diazene["trans"]):
        assert served[entry.public_ref] == species_entry_label_for(entry)


def test_every_species_context_block_carries_the_discriminator():
    """Any read block that names a species *and* an entry must say which entry.

    ``canonical_smiles`` (or ``smiles``) is the *species'* graph identity, so
    a block that pairs it with a ``species_entry_ref`` and stops there labels
    two entries of one species identically. That is the defect this file
    exists for, and it was present on seven blocks at once, not one -- so the
    guard is written over the schema module rather than over a list of
    surfaces somebody has to remember to extend.

    A new read surface that copies an existing context block inherits the
    fix, or fails here on the day it is added.
    """
    import importlib
    import pkgutil

    from pydantic import BaseModel

    import app.schemas.reads as reads_pkg

    offenders: list[str] = []
    checked: list[str] = []
    for mod_info in pkgutil.iter_modules(reads_pkg.__path__):
        module = importlib.import_module(f"app.schemas.reads.{mod_info.name}")
        for name, obj in vars(module).items():
            if not isinstance(obj, type) or not issubclass(obj, BaseModel):
                continue
            if obj is BaseModel or obj.__module__ != module.__name__:
                continue
            # Request models carry the same field names as *filters* --
            # ``smiles=`` and ``species_entry_ref=`` are things a caller
            # sends, not an identity the response asserts. Nothing is
            # rendered from them, so there is nothing to disambiguate.
            if name.endswith("Request"):
                continue
            fields = set(obj.model_fields)
            names_a_species = bool(fields & {"canonical_smiles", "smiles"})
            names_an_entry = "species_entry_ref" in fields
            if not (names_a_species and names_an_entry):
                continue
            checked.append(f"{mod_info.name}.{name}")
            if "species_entry_label" not in fields:
                offenders.append(f"{mod_info.name}.{name}")

    assert checked, "the scan found no species-context blocks at all"
    assert not offenders, (
        "these read blocks name a species and an entry but nothing that "
        f"tells two entries of one species apart: {sorted(offenders)}"
    )


def test_derivation_covers_the_whole_unique_constraint():
    """The label is a discriminator only while it reads every identity column.

    ``species_entry_label`` can promise that two entries of one species
    never render the same *because* it is built from every column of
    ``uq_species_entry_species_id`` except the species. Add a sixth column
    to that constraint and leave the derivation alone, and the promise
    quietly becomes false: two rows the database considers distinct render
    identically again. This reads the constraint off the ORM model rather
    than restating it, so that cannot happen silently.
    """
    from app.db.models.species import SpeciesEntry
    from app.services.scientific_read.species_identity import IDENTITY_COLUMNS

    constraint = next(
        c
        for c in SpeciesEntry.__table__.constraints
        if c.name == "uq_species_entry_species_id"
    )
    columns = {c.name for c in constraint.columns} - {"species_id"}
    assert columns == set(IDENTITY_COLUMNS), (
        "uq_species_entry_species_id and species_entry_label disagree about "
        "what makes one entry a different row from another"
    )

    # And the derivation's own signature takes exactly those, so a column
    # named in IDENTITY_COLUMNS but never read would not slip through.
    import inspect

    params = set(inspect.signature(species_entry_label).parameters)
    assert params == set(IDENTITY_COLUMNS)


@pytest.mark.parametrize(
    "kwargs, expected",
    [
        (
            {
                "stereo_label": None,
                "electronic_state_kind": SpeciesEntryStateKind.ground,
                "electronic_state_label": None,
                "term_symbol": None,
                "isotope_key": None,
            },
            None,
        ),
        (
            {
                "stereo_label": "E",
                "electronic_state_kind": SpeciesEntryStateKind.ground,
                "electronic_state_label": None,
                "term_symbol": None,
                "isotope_key": None,
            },
            "E",
        ),
        (
            {
                "stereo_label": None,
                "electronic_state_kind": SpeciesEntryStateKind.excited,
                "electronic_state_label": "T1",
                "term_symbol": None,
                "isotope_key": None,
            },
            "excited T1",
        ),
        (
            {
                "stereo_label": "S",
                "electronic_state_kind": SpeciesEntryStateKind.ground,
                "electronic_state_label": None,
                "term_symbol": "3Sigma-g",
                "isotope_key": "[2H]C([2H])([2H])O",
            },
            "S 3Sigma-g [2H]C([2H])([2H])O",
        ),
    ],
    ids=["all-null", "stereo-only", "excited-state", "every-component"],
)
def test_label_derivation_cases(kwargs, expected):
    """``ground`` is omitted as the default; a bare entry is None, not ''."""
    assert species_entry_label(**kwargs) == expected
