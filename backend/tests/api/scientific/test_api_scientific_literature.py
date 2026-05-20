"""API tests for the scientific literature detail + inverse records endpoints.

Covers:

- GET /api/v1/scientific/literature/{literature_ref_or_id}
- GET /api/v1/scientific/literature/{literature_ref_or_id}/records
"""

from __future__ import annotations

from app.db.models.author import Author
from app.db.models.common import (
    CalculationType,
    LiteratureKind,
    RecordReviewStatus,
    SubmissionRecordType,
)
from app.db.models.literature import Literature
from app.db.models.literature_author import LiteratureAuthor
from tests.services.scientific_read._factories import (
    make_calculation,
    make_chem_reaction,
    make_kinetics,
    make_lot,
    make_network,
    make_network_solve,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_statmech,
    make_thermo_scalar,
    make_transport,
    next_inchi_key,
    set_review,
)


# ---------------------------------------------------------------------------
# Local fixtures
# ---------------------------------------------------------------------------


_LIT_COUNTER = 0


def _make_literature(
    db_session,
    *,
    title: str = "Test Paper",
    year: int | None = 2024,
    doi: str | None = None,
    isbn: str | None = None,
    journal: str | None = "J. Test",
) -> Literature:
    global _LIT_COUNTER
    _LIT_COUNTER += 1
    suffix = f"{_LIT_COUNTER:04d}"
    lit = Literature(
        kind=LiteratureKind.article,
        title=f"{title} {suffix}",
        year=year,
        journal=journal,
        doi=doi if doi is not None else f"10.1234/test.{suffix}",
        isbn=isbn,
    )
    db_session.add(lit)
    db_session.flush()
    return lit


def _attach_authors(db_session, lit: Literature, names: list[tuple[str, str]]):
    out = []
    for idx, (given, family) in enumerate(names, start=1):
        a = Author(
            given_name=given,
            family_name=family,
            full_name=f"{given} {family}",
        )
        db_session.add(a)
        db_session.flush()
        db_session.add(
            LiteratureAuthor(
                literature_id=lit.id, author_id=a.id, author_order=idx
            )
        )
        out.append(a)
    db_session.flush()
    return out


def _make_species_entry(db_session, *, smiles: str = "CCO"):
    species = make_species(
        db_session, smiles=smiles, inchi_key=next_inchi_key("LIT")
    )
    return species, make_species_entry(db_session, species)


def _detail_url(handle: str, **params) -> str:
    base = f"/api/v1/scientific/literature/{handle}"
    if not params:
        return base
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{base}?{qs}"


def _records_url(handle: str, **params) -> str:
    base = f"/api/v1/scientific/literature/{handle}/records"
    if not params:
        return base
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{base}?{qs}"


# ===========================================================================
# Detail endpoint
# ===========================================================================


def test_detail_by_ref_returns_record(client, db_session):
    lit = _make_literature(db_session)
    resp = client.get(_detail_url(lit.public_ref))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["record"]["literature"]["literature_ref"] == lit.public_ref
    assert body["record"]["literature"]["title"].startswith("Test Paper")
    assert body["record"]["literature"]["year"] == 2024


def test_detail_by_integer_id_works(client, db_session, allow_internal_ids):
    lit = _make_literature(db_session)
    resp = client.get(
        _detail_url(str(lit.id), include="internal_ids")
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["record"]["literature"]["literature_id"] == lit.id
    assert body["record"]["literature"]["literature_ref"] == lit.public_ref


def test_detail_unknown_ref_returns_404(client):
    resp = client.get(_detail_url("lit_doesnotexist000000000000"))
    assert resp.status_code == 404


def test_detail_wrong_prefix_returns_422(client, db_session):
    species, _ = _make_species_entry(db_session)
    resp = client.get(_detail_url(species.public_ref))
    assert resp.status_code == 422
    assert "handle_type_mismatch" in resp.json()["detail"]


def test_detail_malformed_handle_returns_422(client):
    resp = client.get(_detail_url("not-a-valid-handle!"))
    assert resp.status_code == 422
    assert "invalid_handle" in resp.json()["detail"]


def test_detail_authors_present_in_default_response(client, db_session):
    lit = _make_literature(db_session)
    _attach_authors(db_session, lit, [("Jane", "Doe"), ("John", "Smith")])
    body = client.get(_detail_url(lit.public_ref)).json()
    authors = body["record"]["authors"]
    assert len(authors) == 2
    assert authors[0]["full_name"] == "Jane Doe"
    assert authors[0]["position"] == 1
    assert authors[1]["full_name"] == "John Smith"
    assert authors[1]["position"] == 2


def test_detail_authors_include_token_is_noop(client, db_session):
    """``include=authors`` is legal as an explicit affordance."""
    lit = _make_literature(db_session)
    _attach_authors(db_session, lit, [("Jane", "Doe")])
    body = client.get(_detail_url(lit.public_ref, include="authors")).json()
    assert len(body["record"]["authors"]) == 1


def test_detail_record_counts_reflect_linked_records(client, db_session):
    lit = _make_literature(db_session)
    species, entry = _make_species_entry(db_session)
    make_thermo_scalar(db_session, species_entry=entry).literature_id = lit.id
    db_session.flush()

    # Two transports tied to the same literature.
    make_transport(db_session, species_entry=entry, literature_id=lit.id)
    make_transport(db_session, species_entry=entry, literature_id=lit.id)

    body = client.get(_detail_url(lit.public_ref)).json()
    counts = body["record"]["record_counts"]
    assert counts["thermo"] == 1
    assert counts["transport"] == 2
    assert counts["total_records"] == 3


def test_detail_include_all_does_not_restore_internal_ids(client, db_session):
    lit = _make_literature(db_session)
    body = client.get(_detail_url(lit.public_ref, include="all")).json()
    assert "literature_id" not in body["record"]["literature"]
    # Refs remain visible.
    assert body["record"]["literature"]["literature_ref"] == lit.public_ref


def test_detail_include_all_internal_ids_obeys_policy(
    client, db_session, allow_internal_ids
):
    lit = _make_literature(db_session)
    body = client.get(
        _detail_url(lit.public_ref, include="all,internal_ids")
    ).json()
    assert body["record"]["literature"]["literature_id"] == lit.id


def test_detail_internal_ids_hidden_by_default(client, db_session):
    lit = _make_literature(db_session)
    body = client.get(_detail_url(lit.public_ref)).json()
    assert "literature_id" not in body["record"]["literature"]


def test_detail_no_forbidden_payload_keys(client, db_session):
    lit = _make_literature(db_session)
    _attach_authors(db_session, lit, [("Jane", "Doe")])
    body = client.get(_detail_url(lit.public_ref, include="all")).json()
    forbidden = {
        "abstract",
        "fulltext",
        "pdf",
        "body",
        "content",
        "data",
        "presigned_url",
        "download_url",
        "xyz_text",
        "atoms",
        "coords",
    }

    def _walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                assert k not in forbidden, (
                    f"literature detail leaked forbidden key {k!r}"
                )
                _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(body)


def test_detail_review_summary_is_empty(client, db_session):
    """Literature is not reviewable; review_summary stays empty."""
    lit = _make_literature(db_session)
    body = client.get(_detail_url(lit.public_ref)).json()
    rs = body["review_summary"]
    assert rs["total"] == 0
    assert rs["approved"] == 0


def test_detail_identifiers_block(client, db_session):
    lit = _make_literature(
        db_session, doi="10.9999/identifiers.test", isbn=None
    )
    body = client.get(_detail_url(lit.public_ref)).json()
    ids = body["record"]["identifiers"]
    assert ids["doi"] == "10.9999/identifiers.test"
    assert ids["isbn"] is None


# ===========================================================================
# Records endpoint
# ===========================================================================


def _make_calculation_with_lit(db_session, lit_id: int, *, species_entry):
    lot = make_lot(db_session)
    calc = make_calculation(
        db_session,
        type=CalculationType.sp,
        species_entry_id=species_entry.id,
        lot_id=lot.id,
    )
    calc.literature_id = lit_id
    db_session.flush()
    return calc


def _make_kinetics_with_lit(db_session, lit_id: int):
    species_a, entry_a = _make_species_entry(db_session, smiles="CC")
    species_b, entry_b = _make_species_entry(db_session, smiles="CCC")
    reaction = make_chem_reaction(
        db_session, reactants=[species_a], products=[species_b]
    )
    re = make_reaction_entry(
        db_session,
        reaction=reaction,
        reactant_entries=[entry_a],
        product_entries=[entry_b],
    )
    k = make_kinetics(db_session, reaction_entry=re)
    k.literature_id = lit_id
    db_session.flush()
    return reaction, re, k


def test_records_endpoint_by_ref_returns_empty_for_unused_literature(
    client, db_session
):
    lit = _make_literature(db_session)
    body = client.get(_records_url(lit.public_ref)).json()
    assert body["records"] == []
    assert body["pagination"]["total"] == 0


def test_records_endpoint_by_integer_id(client, db_session):
    lit = _make_literature(db_session)
    species, entry = _make_species_entry(db_session)
    make_transport(db_session, species_entry=entry, literature_id=lit.id)
    body = client.get(_records_url(str(lit.id))).json()
    assert body["pagination"]["total"] == 1
    assert body["records"][0]["record_type"] == "transport"


def test_records_endpoint_unknown_literature_returns_404(client):
    resp = client.get(_records_url("lit_doesnotexist000000000000"))
    assert resp.status_code == 404


def test_records_endpoint_wrong_prefix_returns_422(client, db_session):
    species, _ = _make_species_entry(db_session)
    resp = client.get(_records_url(species.public_ref))
    assert resp.status_code == 422
    assert "handle_type_mismatch" in resp.json()["detail"]


def test_records_endpoint_malformed_handle_returns_422(client):
    resp = client.get(_records_url("not-a-handle"))
    assert resp.status_code == 422
    assert "invalid_handle" in resp.text


def test_records_endpoint_returns_calculation_summary(client, db_session):
    lit = _make_literature(db_session)
    _, entry = _make_species_entry(db_session)
    calc = _make_calculation_with_lit(db_session, lit.id, species_entry=entry)
    body = client.get(_records_url(lit.public_ref)).json()
    found = [r for r in body["records"] if r["record_type"] == "calculation"]
    assert len(found) == 1
    assert found[0]["record_ref"] == calc.public_ref
    assert found[0]["endpoint"] == (
        f"/api/v1/scientific/calculations/{calc.public_ref}"
    )


def test_records_endpoint_returns_thermo_summary(client, db_session):
    lit = _make_literature(db_session)
    species, entry = _make_species_entry(db_session)
    t = make_thermo_scalar(db_session, species_entry=entry)
    t.literature_id = lit.id
    db_session.flush()
    body = client.get(_records_url(lit.public_ref)).json()
    rows = [r for r in body["records"] if r["record_type"] == "thermo"]
    assert len(rows) == 1
    assert rows[0]["species_ref"] == species.public_ref
    assert rows[0]["species_entry_ref"] == entry.public_ref
    assert rows[0]["endpoint"] == f"/api/v1/scientific/thermo/{t.public_ref}"


def test_records_endpoint_returns_kinetics_summary(client, db_session):
    lit = _make_literature(db_session)
    reaction, re, k = _make_kinetics_with_lit(db_session, lit.id)
    body = client.get(_records_url(lit.public_ref)).json()
    rows = [r for r in body["records"] if r["record_type"] == "kinetics"]
    assert len(rows) == 1
    assert rows[0]["reaction_ref"] == reaction.public_ref
    assert rows[0]["reaction_entry_ref"] == re.public_ref


def test_records_endpoint_returns_statmech_summary(client, db_session):
    lit = _make_literature(db_session)
    _, entry = _make_species_entry(db_session)
    sm = make_statmech(db_session, species_entry=entry, literature_id=lit.id)
    body = client.get(_records_url(lit.public_ref)).json()
    rows = [r for r in body["records"] if r["record_type"] == "statmech"]
    assert len(rows) == 1
    assert rows[0]["record_ref"] == sm.public_ref


def test_records_endpoint_returns_transport_summary(client, db_session):
    lit = _make_literature(db_session)
    _, entry = _make_species_entry(db_session)
    tr = make_transport(db_session, species_entry=entry, literature_id=lit.id)
    body = client.get(_records_url(lit.public_ref)).json()
    rows = [r for r in body["records"] if r["record_type"] == "transport"]
    assert len(rows) == 1
    assert rows[0]["record_ref"] == tr.public_ref


def test_records_endpoint_returns_network_summary(client, db_session):
    lit = _make_literature(db_session)
    net = make_network(db_session, literature_id=lit.id, name="my-network")
    body = client.get(_records_url(lit.public_ref)).json()
    rows = [r for r in body["records"] if r["record_type"] == "network"]
    assert len(rows) == 1
    assert rows[0]["title"] == "my-network"
    assert rows[0]["record_ref"] == net.public_ref


def test_records_endpoint_returns_network_solve_summary(client, db_session):
    lit = _make_literature(db_session)
    net = make_network(db_session)
    solve = make_network_solve(
        db_session, network=net, literature_id=lit.id
    )
    body = client.get(_records_url(lit.public_ref)).json()
    rows = [r for r in body["records"] if r["record_type"] == "network_solve"]
    assert len(rows) == 1
    assert rows[0]["network_ref"] == net.public_ref
    assert rows[0]["record_ref"] == solve.public_ref


def test_records_endpoint_record_type_filter_works(client, db_session):
    lit = _make_literature(db_session)
    _, entry = _make_species_entry(db_session)
    make_transport(db_session, species_entry=entry, literature_id=lit.id)
    make_statmech(db_session, species_entry=entry, literature_id=lit.id)
    body = client.get(
        _records_url(lit.public_ref, record_type="transport")
    ).json()
    assert body["pagination"]["total"] == 1
    assert {r["record_type"] for r in body["records"]} == {"transport"}


def test_records_endpoint_unknown_record_type_returns_422(client, db_session):
    lit = _make_literature(db_session)
    resp = client.get(
        _records_url(lit.public_ref, record_type="not_a_thing")
    )
    assert resp.status_code == 422
    assert "unknown_record_type" in resp.json()["detail"]


def test_records_endpoint_pagination_envelope(client, db_session):
    lit = _make_literature(db_session)
    _, entry = _make_species_entry(db_session)
    for _ in range(3):
        make_transport(db_session, species_entry=entry, literature_id=lit.id)
    body = client.get(_records_url(lit.public_ref, limit=2)).json()
    pag = body["pagination"]
    assert pag["offset"] == 0
    assert pag["limit"] == 2
    assert pag["returned"] == 2
    assert pag["total"] == 3


def test_records_endpoint_deterministic_ordering(client, db_session):
    lit = _make_literature(db_session)
    _, entry = _make_species_entry(db_session)
    make_thermo_scalar(db_session, species_entry=entry).literature_id = lit.id
    db_session.flush()
    make_transport(db_session, species_entry=entry, literature_id=lit.id)
    make_statmech(db_session, species_entry=entry, literature_id=lit.id)

    body = client.get(_records_url(lit.public_ref)).json()
    types = [r["record_type"] for r in body["records"]]
    # record_type ASC: statmech < thermo < transport
    assert types == sorted(types)


def test_records_endpoint_client_sort_rejected(client, db_session):
    lit = _make_literature(db_session)
    resp = client.get(_records_url(lit.public_ref, sort="created_at"))
    assert resp.status_code == 422
    assert "client_sort_not_supported" in resp.json()["detail"]


def test_records_endpoint_endpoints_are_ref_based(client, db_session):
    lit = _make_literature(db_session)
    _, entry = _make_species_entry(db_session)
    tr = make_transport(db_session, species_entry=entry, literature_id=lit.id)
    body = client.get(_records_url(lit.public_ref)).json()
    endpoints = [r["endpoint"] for r in body["records"]]
    # Refs only; no integer IDs in URLs.
    assert all("/" + tr.public_ref in ep for ep in endpoints)
    for ep in endpoints:
        # No bare integer segment.
        tail = ep.rsplit("/", 1)[-1]
        assert not tail.isdigit(), f"endpoint {ep!r} ends in an integer id"


def test_records_endpoint_internal_ids_hidden_by_default(client, db_session):
    lit = _make_literature(db_session)
    _, entry = _make_species_entry(db_session)
    make_transport(db_session, species_entry=entry, literature_id=lit.id)
    body = client.get(_records_url(lit.public_ref)).json()
    for rec in body["records"]:
        assert "record_id" not in rec


def test_records_endpoint_internal_ids_restored_under_policy(
    client, db_session, allow_internal_ids
):
    lit = _make_literature(db_session)
    _, entry = _make_species_entry(db_session)
    tr = make_transport(db_session, species_entry=entry, literature_id=lit.id)
    body = client.get(
        _records_url(lit.public_ref, include="internal_ids")
    ).json()
    assert body["records"][0]["record_id"] == tr.id


def test_records_endpoint_include_review_adds_badges(client, db_session):
    lit = _make_literature(db_session)
    _, entry = _make_species_entry(db_session)
    tr = make_transport(db_session, species_entry=entry, literature_id=lit.id)
    set_review(
        db_session,
        record_type=SubmissionRecordType.transport,
        record_id=tr.id,
        status=RecordReviewStatus.approved,
    )
    body_no = client.get(_records_url(lit.public_ref)).json()
    assert body_no["records"][0]["review"] is None

    body_yes = client.get(
        _records_url(lit.public_ref, include="review")
    ).json()
    assert body_yes["records"][0]["review"]["status"] == "approved"


def test_records_endpoint_rejected_records_hidden_by_default(
    client, db_session
):
    lit = _make_literature(db_session)
    _, entry = _make_species_entry(db_session)
    tr = make_transport(db_session, species_entry=entry, literature_id=lit.id)
    set_review(
        db_session,
        record_type=SubmissionRecordType.transport,
        record_id=tr.id,
        status=RecordReviewStatus.rejected,
    )
    body = client.get(_records_url(lit.public_ref)).json()
    assert body["pagination"]["total"] == 0


def test_records_endpoint_include_rejected_restores_them(client, db_session):
    lit = _make_literature(db_session)
    _, entry = _make_species_entry(db_session)
    tr = make_transport(db_session, species_entry=entry, literature_id=lit.id)
    set_review(
        db_session,
        record_type=SubmissionRecordType.transport,
        record_id=tr.id,
        status=RecordReviewStatus.rejected,
    )
    body = client.get(
        _records_url(lit.public_ref, include_rejected=True)
    ).json()
    assert body["pagination"]["total"] == 1


def test_records_endpoint_no_forbidden_payload_keys(client, db_session):
    lit = _make_literature(db_session)
    _, entry = _make_species_entry(db_session)
    make_transport(db_session, species_entry=entry, literature_id=lit.id)
    body = client.get(_records_url(lit.public_ref, include="all")).json()
    forbidden = {
        "abstract",
        "fulltext",
        "pdf",
        "body",
        "content",
        "data",
        "presigned_url",
        "download_url",
        "xyz_text",
        "atoms",
        "coords",
    }

    def _walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                assert k not in forbidden, (
                    f"literature records leaked forbidden key {k!r}"
                )
                _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(body)
