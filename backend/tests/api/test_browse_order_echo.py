"""The order a response echoes is the order it served (``request.sort``, ``request.effective_order``).

Three layers. The static ones read the source: every key a ``browse_order(...)`` call names is explained by the
catalog, and every catalog key is used by something. The behavioural ones sort real rows and compare the echoed keys
with the positions the endpoint returned, for the three reads whose echo had drifted from their query.
"""

from __future__ import annotations

import ast
from pathlib import Path

from app.db.models.common import RecordReviewStatus, SubmissionRecordType
from app.schemas.reads import scientific_common
from app.schemas.reads.scientific_common import ORDER_KEY_CATALOG
from tests.services.scientific_read._factories import (
    make_species,
    make_species_entry,
    make_thermo_scalar,
    make_transport,
    next_inchi_key,
    set_review,
)

APP = Path(scientific_common.__file__).resolve().parents[2]


def _keys(order) -> list[tuple[str, str]]:
    return [(k["key"], k["direction"]) for k in order["keys"]]


# --- static: the catalog and the calls agree -------------------------------------------------------------------------


def _named_keys() -> set[str]:
    """Every string literal passed to ``browse_order(...)``, plus the two shared order tuples in the common module."""
    named: set[str] = set()
    calls = 0
    for path in APP.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", None)) == "browse_order":
                calls += 1
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        named.add(arg.value)
                    elif isinstance(arg, ast.Tuple) and arg.elts and isinstance(arg.elts[0], ast.Constant):
                        named.add(arg.elts[0].value)
                    elif isinstance(arg, ast.Starred):
                        pass
    assert calls > 20, f"only {calls} browse_order calls found: the scan reached too little to prove anything"
    named |= set(scientific_common._REVIEW_FIRST_ORDER) | set(scientific_common._RECENCY_ONLY_ORDER)
    return named


def test_every_key_a_call_names_is_explained_by_the_catalog():
    assert _named_keys() <= set(ORDER_KEY_CATALOG)


def test_every_catalog_key_is_used_by_some_order():
    unused = set(ORDER_KEY_CATALOG) - _named_keys()
    assert not unused, f"catalog keys no response echoes: {sorted(unused)}"


# --- behavioural: species browse with a structure query leads with size ------------------------------------------------


def _species_with_entry(db_session, smiles):
    species = make_species(db_session, smiles=smiles, inchi_key=next_inchi_key("BOE"))
    make_species_entry(db_session, species)
    return species


def test_a_substructure_browse_echoes_the_size_key_first_and_the_records_follow_it(client, db_session):
    """Kills: echoing the review-first order while the query leads with ``heavy_atom_count ASC``."""
    small = _species_with_entry(db_session, "CCO")  # created first: newest-first alone would list it last
    mid = _species_with_entry(db_session, "CCCO")
    large = _species_with_entry(db_session, "CCCCO")

    body = client.get("/api/v1/scientific/species/browse?query_smiles=CCO&mode=substructure").json()

    refs = [r["species_ref"] for r in body["records"]]
    assert refs == [small.public_ref, mid.public_ref, large.public_ref]
    assert _keys(body["request"]["sort"])[0] == ("heavy_atom_count", "asc")
    assert [k for k, _ in _keys(body["request"]["sort"])] == [
        "heavy_atom_count",
        "review_rank",
        "has_entries",
        "created_at",
        "id",
    ]


def test_a_browse_without_a_structure_query_does_not_claim_a_size_key(client, db_session):
    _species_with_entry(db_session, "CCO")
    body = client.get("/api/v1/scientific/species/browse").json()
    assert "heavy_atom_count" not in [k for k, _ in _keys(body["request"]["sort"])]


def test_an_empty_substructure_browse_still_echoes_the_size_key(client, db_session):
    body = client.get("/api/v1/scientific/species/browse?query_smiles=C%23CC%23CC%23CC%23C&mode=substructure").json()
    assert body["records"] == []
    assert _keys(body["request"]["sort"])[0] == ("heavy_atom_count", "asc")


# --- behavioural: thermo search orders entries by rank then entry id -------------------------------------------------


def test_thermo_search_echoes_the_entry_level_order_it_actually_sorts_by(client, db_session):
    """Kills: echoing ``species_created_at, species_id`` while the sort is ``species_entry_id DESC``."""
    species = make_species(db_session, smiles="C=C", inchi_key=next_inchi_key("BTS"))
    older = make_species_entry(db_session, species, stereo_label="a")
    newer = make_species_entry(db_session, species, stereo_label="b")
    approved = make_species_entry(db_session, species, stereo_label="c")
    for entry in (approved, older, newer):  # approved is created last: only the review rank can put it first
        make_thermo_scalar(db_session, species_entry=entry)
    set_review(
        db_session, record_type=SubmissionRecordType.species_entry, record_id=approved.id,
        status=RecordReviewStatus.approved,
    )

    body = client.get("/api/v1/scientific/thermo/search?smiles=C=C").json()

    order = [r["species"]["species_entry_ref"] for r in body["records"]]
    assert order == [approved.public_ref, newer.public_ref, older.public_ref]
    assert _keys(body["request"]["sort"])[:2] == [("species_review_rank", "asc"), ("species_entry_id", "desc")]
    assert not {"species_created_at", "species_id"} & {k for k, _ in _keys(body["request"]["sort"])}


# --- behavioural: collapse=first with selection_policy=latest drops the review rank ------------------------------------


def _older_approved_newer_unreviewed_thermo(db_session):
    species = make_species(db_session, smiles="CC", inchi_key=next_inchi_key("BEC"))
    entry = make_species_entry(db_session, species)
    older = make_thermo_scalar(db_session, species_entry=entry)
    newer = make_thermo_scalar(db_session, species_entry=entry, h298_kj_mol=-20.0)
    set_review(
        db_session, record_type=SubmissionRecordType.thermo, record_id=older.id, status=RecordReviewStatus.approved
    )
    return entry, older, newer


def test_thermo_collapse_first_latest_reports_the_order_that_chose_the_record(client, db_session):
    """Kills: ``request.sort`` alone claiming review rank first while ``latest`` chose by time."""
    entry, older, newer = _older_approved_newer_unreviewed_thermo(db_session)
    base = f"/api/v1/scientific/species-entries/{entry.public_ref}/thermo"

    latest = client.get(base + "?collapse=first&selection_policy=latest").json()
    assert [r["thermo"]["thermo_ref"] if "thermo" in r else r["thermo_ref"] for r in latest["records"]] == [newer.public_ref]
    assert _keys(latest["request"]["effective_order"]) == [("created_at", "desc"), ("id", "desc")]

    default = client.get(base + "?collapse=first").json()
    assert [r["thermo_ref"] if "thermo_ref" in r else r["thermo"]["thermo_ref"] for r in default["records"]] == [
        older.public_ref
    ]
    assert [k for k, _ in _keys(default["request"]["effective_order"])] == ["review_rank", "created_at", "id"]

    every = client.get(base + "?selection_policy=latest").json()
    assert [k for k, _ in _keys(every["request"]["effective_order"])] == ["review_rank", "created_at", "id"]


def test_transport_collapse_first_latest_reports_the_order_that_chose_the_record(client, db_session):
    species = make_species(db_session, smiles="CCC", inchi_key=next_inchi_key("BET"))
    entry = make_species_entry(db_session, species)
    older = make_transport(db_session, species_entry=entry)
    newer = make_transport(db_session, species_entry=entry, sigma_angstrom=3.9)
    set_review(
        db_session, record_type=SubmissionRecordType.transport, record_id=older.id, status=RecordReviewStatus.approved
    )
    base = f"/api/v1/scientific/species-entries/{entry.public_ref}/transport"

    latest = client.get(base + "?collapse=first&selection_policy=latest").json()
    assert [r["transport"]["transport_ref"] for r in latest["records"]] == [newer.public_ref]
    assert _keys(latest["request"]["effective_order"]) == [("created_at", "desc"), ("id", "desc")]

    default = client.get(base + "?collapse=first").json()
    assert [r["transport"]["transport_ref"] for r in default["records"]] == [older.public_ref]
    assert [k for k, _ in _keys(default["request"]["effective_order"])] == ["review_rank", "created_at", "id"]
