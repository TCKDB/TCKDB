"""The legacy entity lists obey the read profile and the default visibility, and say what they did.

``GET /api/v1/{thermo,kinetics,statmech,transport,calculations,transition-states,conformer-groups,networks}`` are the
raw entity layer: ``id`` ascending, internal ids. They used to show every record whatever its review status, and
ignored ``profile``, so ``?profile=curated`` did not bypass anything only because it did nothing. Now:

* rejected and deprecated records are hidden unless included, and the response counts what it hid (exploratory);
* ``?profile=curated`` shows approved records only, in the list, its total and the record's own detail route, which
  answers exactly as an id that names nothing (decision D6); and says nothing about the records below the floor;
* every list echoes its order (``id`` ascending, administrative) and the profile it answered under.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import event

from app.db.models.common import RecordReviewStatus, SubmissionRecordType
from tests.api.test_browse_order_echo import (
    ROW_SPECS,
    Seeded,
    seed_calculations,
    seed_conformers,
    seed_kinetics,
    seed_networks,
    seed_statmech,
    seed_thermo,
    seed_transport,
    skew,
)
from tests.services.scientific_read._factories import (
    attach_thermo_source_calculation,
    make_calculation,
    make_chem_reaction,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_thermo_scalar,
    make_transition_state,
    next_inchi_key,
    set_review,
)

S = RecordReviewStatus
LEGACY = "/api/v1"
#: row 0 rejected, row 1 deprecated, then three visible (one approved): the same shape the scientific lists are tested with.
HIDING_SPECS = [(S.rejected, 1), (S.deprecated, 2), (S.approved, 3), (S.not_reviewed, 4), (S.under_review, 5)]


def seed_transition_states_legacy(db_session) -> Seeded:
    rs = make_species(db_session, smiles="CC", inchi_key=next_inchi_key("LTR"))
    ps = make_species(db_session, smiles="CCC", inchi_key=next_inchi_key("LTP"))
    chem = make_chem_reaction(db_session, reactants=[rs], products=[ps])
    reaction_entry = make_reaction_entry(
        db_session,
        reaction=chem,
        reactant_entries=[make_species_entry(db_session, rs)],
        product_entries=[make_species_entry(db_session, ps)],
    )
    rows = [make_transition_state(db_session, reaction_entry=reaction_entry, label=f"t{i}") for i in range(5)]
    skew(db_session, rows, ROW_SPECS)
    return Seeded(
        "", rows, lambda rec: str(rec["id"]), [(SubmissionRecordType.transition_state, r.id) for r in rows]
    )


#: name -> (seed, legacy list path, legacy detail path format)
CASES = {
    "thermo": (lambda db: seed_thermo(db, search=False), "thermo"),
    "kinetics": (lambda db: seed_kinetics(db, search=False), "kinetics"),
    "statmech": (lambda db: seed_statmech(db, search=False), "statmech"),
    "transport": (lambda db: seed_transport(db, search=False), "transport"),
    "calculations": (lambda db: seed_calculations(db, route="calculations"), "calculations"),
    "transition-states": (seed_transition_states_legacy, "transition-states"),
    "conformer-groups": (seed_conformers, "conformer-groups"),
    "networks": (lambda db: seed_networks(db, browse=False), "networks"),
}


def _seed(case, db_session):
    seed, path = CASES[case]
    seeded = seed(db_session)
    seeded.specs = list(HIDING_SPECS)
    for (record_type, record_id), (status, _age) in zip(seeded.review_targets, seeded.specs, strict=True):
        if status is not S.not_reviewed:
            set_review(db_session, record_type=record_type, record_id=record_id, status=status)
    return seeded, f"{LEGACY}/{path}"


def _ids(body):
    return [item["id"] for item in body["items"]]


@pytest.mark.parametrize("case", sorted(CASES))
def test_a_legacy_list_hides_rejected_and_deprecated_by_default_and_counts_them(case, client, db_session):
    seeded, url = _seed(case, db_session)
    body = client.get(url, params={"limit": 200}).json()
    assert _ids(body) == [seeded.rows[i].id for i in (2, 3, 4)]  # id ascending, the visible rows only
    assert body["total"] == 3
    assert body["hidden_by_default"]["counts"] == {"rejected": 1, "deprecated": 1}
    assert body["hidden_by_default"]["include_with"] == {
        "rejected": "include_rejected=true",
        "deprecated": "include_deprecated=true",
    }
    assert body["profile"] == "exploratory"


@pytest.mark.parametrize("case", sorted(CASES))
def test_a_legacy_list_includes_what_the_request_asks_for(case, client, db_session):
    seeded, url = _seed(case, db_session)
    body = client.get(url, params={"limit": 200, "include_rejected": "true"}).json()
    assert _ids(body) == [seeded.rows[i].id for i in (0, 2, 3, 4)]
    assert body["hidden_by_default"]["counts"] == {"deprecated": 1}
    body = client.get(url, params={"limit": 200, "include_rejected": "true", "include_deprecated": "true"}).json()
    assert _ids(body) == [r.id for r in seeded.rows]
    assert body["total"] == 5
    assert body["hidden_by_default"] is None


@pytest.mark.parametrize("case", sorted(CASES))
def test_a_legacy_list_echoes_its_administrative_id_order(case, client, db_session):
    """The order is id ascending whatever the review status and deposition time say (the seed disagrees with it)."""
    seeded, url = _seed(case, db_session)
    body = client.get(url, params={"limit": 200, "include_rejected": "true", "include_deprecated": "true"}).json()
    order = body["order"]
    assert [(k["key"], k["direction"]) for k in order["keys"]] == [("id", "asc")]
    assert "administrative" in order["keys"][0]["meaning"]
    assert "not the browse display order" in order["statement"]
    ids = _ids(body)
    assert ids == sorted(ids)
    # The echo is true: review rank and time would order these rows differently.
    assert ids != [seeded.rows[i].id for i in (2, 3, 4, 0, 1)]


@pytest.mark.parametrize("case", sorted(CASES))
def test_the_curated_profile_shows_approved_records_only_and_counts_nothing_else(case, client, db_session):
    """Kills: a legacy list that ignores ``profile`` (before this change it returned all five rows), and a count of
    the records below the floor in the total or the disclosure block."""
    seeded, url = _seed(case, db_session)
    body = client.get(
        url, params={"limit": 200, "profile": "curated", "include_rejected": "true", "include_deprecated": "true"}
    ).json()
    assert _ids(body) == [seeded.rows[2].id]
    assert body["total"] == 1
    assert body["profile"] == "curated"
    assert body["profile_recommendation"] == "approved_floor_only"
    assert body["hidden_by_default"] is None
    text = json.dumps(body)
    assert '"counts"' not in text and "include_rejected=true" not in text


@pytest.mark.parametrize("case", sorted(CASES))
def test_a_record_below_the_floor_answers_its_detail_route_as_an_unknown_id_does(case, client, db_session):
    seeded, url = _seed(case, db_session)
    hidden_id = seeded.rows[3].id  # not_reviewed: visible by default, below the curated floor
    assert client.get(f"{url}/{hidden_id}").status_code == 200
    curated = client.get(f"{url}/{hidden_id}", params={"profile": "curated"})
    unknown = client.get(f"{url}/999999999", params={"profile": "curated"})
    assert curated.status_code == unknown.status_code == 404
    assert curated.json() == unknown.json()
    approved = client.get(f"{url}/{seeded.rows[2].id}", params={"profile": "curated"})
    assert approved.status_code == 200


def test_a_curated_thermo_does_not_name_a_calculation_the_floor_hides(client, db_session):
    """An approved thermo cites two calculations; the curated read shows the approved one and is silent about the
    other (decision D6), in the list and on the detail route."""
    species = make_species(db_session, smiles="C=O", inchi_key=next_inchi_key("LNK"))
    entry = make_species_entry(db_session, species)
    thermo = make_thermo_scalar(db_session, species_entry=entry)
    shown = make_calculation(db_session, species_entry_id=entry.id)
    hidden = make_calculation(db_session, species_entry_id=entry.id)
    attach_thermo_source_calculation(db_session, thermo=thermo, calculation=shown)
    attach_thermo_source_calculation(db_session, thermo=thermo, calculation=hidden)
    set_review(db_session, record_type=SubmissionRecordType.thermo, record_id=thermo.id, status=S.approved)
    set_review(db_session, record_type=SubmissionRecordType.calculation, record_id=shown.id, status=S.approved)

    def linked(body):
        record = body["items"][0] if "items" in body else body
        return sorted(sc["calculation_id"] for sc in record["source_calculations"])

    assert linked(client.get(f"{LEGACY}/thermo").json()) == sorted([shown.id, hidden.id])  # exploratory: both
    assert linked(client.get(f"{LEGACY}/thermo", params={"profile": "curated"}).json()) == [shown.id]
    assert linked(client.get(f"{LEGACY}/thermo/{thermo.id}", params={"profile": "curated"}).json()) == [shown.id]


def test_the_entry_sub_lists_obey_the_profile_and_the_default_visibility(client, db_session):
    species = make_species(db_session, smiles="C=O", inchi_key=next_inchi_key("LSB"))
    entry = make_species_entry(db_session, species)
    rows = [make_thermo_scalar(db_session, species_entry=entry, h298_kj_mol=-1.0 - i) for i in range(3)]
    set_review(db_session, record_type=SubmissionRecordType.thermo, record_id=rows[0].id, status=S.rejected)
    set_review(db_session, record_type=SubmissionRecordType.thermo, record_id=rows[1].id, status=S.approved)
    url = f"{LEGACY}/species-entries/{entry.id}/thermo"

    exploratory = client.get(url)
    assert [r["id"] for r in exploratory.json()] == [rows[1].id, rows[2].id]
    assert exploratory.headers["X-TCKDB-Hidden-By-Default"] == "deprecated=0; rejected=1"
    assert [r["id"] for r in client.get(url, params={"include_rejected": "true"}).json()] == [r.id for r in rows]

    curated = client.get(url, params={"profile": "curated", "include_rejected": "true"})
    assert [r["id"] for r in curated.json()] == [rows[1].id]
    assert "X-TCKDB-Hidden-By-Default" not in curated.headers


def test_a_curated_entry_summary_counts_only_what_the_curated_list_shows(client, db_session):
    from tests.services.scientific_read._factories import make_conformer_group

    species = make_species(db_session, smiles="C=O", inchi_key=next_inchi_key("LCS"))
    entry = make_species_entry(db_session, species)
    groups = [make_conformer_group(db_session, entry, label=f"g{i}") for i in range(3)]
    set_review(db_session, record_type=SubmissionRecordType.conformer_group, record_id=groups[0].id, status=S.approved)
    set_review(db_session, record_type=SubmissionRecordType.species_entry, record_id=entry.id, status=S.approved)

    exploratory = client.get(f"{LEGACY}/species-entries/{entry.id}").json()["conformer_summary"]
    curated = client.get(f"{LEGACY}/species-entries/{entry.id}", params={"profile": "curated"}).json()["conformer_summary"]
    assert exploratory["conformer_group_count"] == 3
    assert curated["conformer_group_count"] == 1


def test_a_write_route_takes_no_profile(client):
    """The profile belongs to reads: the write routes sharing a path prefix do not declare it."""
    spec = client.get("/openapi.json").json()
    for path, method in [
        ("/api/v1/conformer-groups/{conformer_group_id}/selections", "post"),
        ("/api/v1/species-entries/{species_entry_id}/reviews", "post"),
    ]:
        names = {p["name"] for p in spec["paths"][path][method].get("parameters", [])}
        assert "profile" not in names, (path, names)
    names = {p["name"] for p in spec["paths"]["/api/v1/thermo"]["get"]["parameters"]}
    assert {"profile", "include_rejected", "include_deprecated"} <= names


# --- statement counts --------------------------------------------------------------------------------------------


def _count_statements(db_session, call):
    seen: list[str] = []

    def hook(conn, cursor, statement, *a):
        seen.append(statement)

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", hook)
    try:
        call()
    finally:
        event.remove(bind, "before_cursor_execute", hook)
    return seen


def test_the_disclosure_costs_one_statement_per_list_and_none_under_curated(client, db_session):
    """Before this change a legacy list issued 2 statements (count, page). The visibility joins go into those two; the
    disclosure is the one added aggregate. Curated issues no aggregate: it must not count what it withholds."""
    seeded, url = _seed("thermo", db_session)
    client.get(url)  # warm one-time lookups
    default = _count_statements(db_session, lambda: client.get(url))
    both_lifted = _count_statements(db_session, lambda: client.get(url, params={"include_rejected": "true", "include_deprecated": "true"}))
    curated = _count_statements(db_session, lambda: client.get(url, params={"profile": "curated"}))
    aggregate = [x for x in default if "GROUP BY record_review.status" in x]
    assert len(aggregate) == 1, aggregate
    # count, page, and the one aggregate (the per-row loads of a thermo page are not part of this arithmetic).
    assert len(default) == 3, default
    assert len(curated) == 2, curated
    assert not any("GROUP BY record_review.status" in x for x in curated + both_lifted)


# --- structural nets: a route added tomorrow is covered the day it exists ------------------------------------------

LEGACY_PREFIXES = (
    "/api/v1/lookup",
    "/api/v1/calculations",
    "/api/v1/species",
    "/api/v1/species-entries",
    "/api/v1/reactions",
    "/api/v1/reaction-entries",
    "/api/v1/kinetics",
    "/api/v1/thermo",
    "/api/v1/transition-states",
    "/api/v1/geometries",
    "/api/v1/levels-of-theory",
    "/api/v1/software",
    "/api/v1/software-releases",
    "/api/v1/literature",
    "/api/v1/conformer-groups",
    "/api/v1/conformer-observations",
    "/api/v1/energy-correction-schemes",
    "/api/v1/frequency-scale-factors",
    "/api/v1/applied-energy-corrections",
    "/api/v1/workflow-tools",
    "/api/v1/workflow-tool-releases",
    "/api/v1/statmech",
    "/api/v1/transport",
    "/api/v1/networks",
)


def _legacy_get_operations(spec):
    for path, item in spec["paths"].items():
        if any(path == p or path.startswith(p + "/") for p in LEGACY_PREFIXES) and "get" in item:
            yield path, item["get"]


def test_every_lookup_and_legacy_read_declares_the_read_profile(client):
    """Kills: a legacy or lookup router added without the profile dependency (a read the curated profile cannot reach)."""
    spec = client.get("/openapi.json").json()
    operations = list(_legacy_get_operations(spec))
    assert len(operations) > 60, len(operations)  # the scan reached the surface, not an empty prefix
    missing = [path for path, op in operations if "profile" not in {p["name"] for p in op.get("parameters", [])}]
    assert not missing, missing


def test_every_calculation_sub_route_answers_404_for_a_calculation_below_the_floor(client, db_session):
    """Every GET under ``/calculations/{calculation_id}/...``, called for a calculation the curated floor hides, answers
    as an unknown id does. Kills: a sub-route added outside the router-level gate."""
    species = make_species(db_session, smiles="C=O", inchi_key=next_inchi_key("LSW"))
    entry = make_species_entry(db_session, species)
    calc = make_calculation(db_session, species_entry_id=entry.id)  # never reviewed: below the curated floor
    spec = client.get("/openapi.json").json()
    paths = [
        path
        for path, item in spec["paths"].items()
        if path.startswith("/api/v1/calculations/{calculation_id}") and "get" in item
    ]
    assert len(paths) > 15, paths
    for path in paths:
        response = client.get(path.replace("{calculation_id}", str(calc.id)), params={"profile": "curated"})
        assert response.status_code == 404, (path, response.status_code)
        unknown = client.get(path.replace("{calculation_id}", "999999999"), params={"profile": "curated"})
        assert response.json() == unknown.json(), path
