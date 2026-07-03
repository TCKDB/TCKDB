"""Phase D acceptance tests: scientific responses hide internal IDs by default.

Verifies the visibility contract defined in
``docs/specs/internal_ids_visibility_policy.md``:

1. Default responses omit every ``*_id`` field, every bare integer-id
   array, and the non-suffix internal-PK fields
   (``LiteratureSummary.id``, ``ReactionEntrySummary.id``,
   ``ReviewRecordEntry.record_id``).
2. Public ref siblings remain visible.
3. ``include=internal_ids`` is silently dropped (no 4xx, no IDs)
   unless ``ALLOW_PUBLIC_INTERNAL_IDS`` is set.
4. With ``ALLOW_PUBLIC_INTERNAL_IDS=True`` and explicit
   ``include=internal_ids``, all integer IDs and bare arrays come
   back.
5. ``include=all`` does **not** expand to include ``internal_ids``.
6. Request echo never leaks IDs that were resolved from refs.
7. Integer and ref path handles both keep working as inputs.
"""

from __future__ import annotations

from app.db.models.common import (
    CalculationType,
    RecordReviewStatus,
    SubmissionRecordType,
)
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

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _collect_keys(payload, *, into=None, skip_request=True):
    """Walk a nested dict/list structure and collect every dict key seen.

    Skips the top-level ``request`` block — request echoes mirror
    caller input and are deliberately preserved (Phase D contract).
    """
    keys = set() if into is None else into
    if isinstance(payload, dict):
        for k, v in payload.items():
            keys.add(k)
            if skip_request and k == "request":
                continue
            _collect_keys(v, into=keys, skip_request=False)
    elif isinstance(payload, list):
        for item in payload:
            _collect_keys(item, into=keys, skip_request=False)
    return keys


def _assert_no_internal_id_keys(payload):
    """Assert no ``*_id`` / ``*_ids`` / literal-internal key survives.

    Skips the ``request`` top-level block.
    """
    keys = _collect_keys(payload)
    leaked = sorted(
        k
        for k in keys
        if k.endswith("_id")
        or k.endswith("_ids")
        or k in {"id", "record_id", "reviewed_by", "created_by"}
    )
    assert not leaked, (
        f"Phase D: integer-ID keys leaked into default response: {leaked!r}"
    )


# ---------------------------------------------------------------------------
# Default response strips integer IDs across all endpoints
# ---------------------------------------------------------------------------


def test_species_search_default_omits_ids(client, db_session):
    species = make_species(
        db_session, smiles="C#CCC", inchi_key=next_inchi_key("PD_SS")
    )
    make_species_entry(db_session, species)
    resp = client.get("/api/v1/scientific/species/search?smiles=C%23CCC")
    assert resp.status_code == 200
    body = resp.json()
    _assert_no_internal_id_keys(body)
    # Refs remain visible.
    assert body["records"][0]["species_ref"] == species.public_ref
    entry_refs = [
        e["species_entry_ref"] for e in body["records"][0]["entries"]
    ]
    assert all(ref.startswith("spe_") for ref in entry_refs)


def test_thermo_search_default_omits_ids(client, db_session):
    species = make_species(
        db_session, smiles="C#CCCC", inchi_key=next_inchi_key("PD_TS")
    )
    entry = make_species_entry(db_session, species)
    thermo = make_thermo_scalar(db_session, species_entry=entry)
    resp = client.get("/api/v1/scientific/thermo/search?smiles=C%23CCCC")
    assert resp.status_code == 200
    body = resp.json()
    _assert_no_internal_id_keys(body)
    rec = body["records"][0]
    assert rec["species"]["species_ref"] == species.public_ref
    assert rec["species"]["species_entry_ref"] == entry.public_ref
    assert rec["thermo"]["thermo_ref"] == thermo.public_ref


def test_species_thermo_detail_default_omits_ids(client, db_session):
    species = make_species(
        db_session, smiles="C#CCCCC", inchi_key=next_inchi_key("PD_TD")
    )
    entry = make_species_entry(db_session, species)
    make_thermo_scalar(db_session, species_entry=entry)
    resp = client.get(
        f"/api/v1/scientific/species-entries/{entry.public_ref}/thermo"
    )
    assert resp.status_code == 200
    body = resp.json()
    _assert_no_internal_id_keys(body)
    assert body["species_entry_ref"] == entry.public_ref


def test_reactions_search_default_omits_ids(client, db_session):
    a = make_species(db_session, smiles="C#CCO", inchi_key=next_inchi_key("PD_RA"))
    b = make_species(db_session, smiles="C#CCN", inchi_key=next_inchi_key("PD_RB"))
    ae = make_species_entry(db_session, a)
    be = make_species_entry(db_session, b)
    chem = make_chem_reaction(db_session, reactants=[a], products=[b])
    re = make_reaction_entry(
        db_session,
        reaction=chem,
        reactant_entries=[ae],
        product_entries=[be],
    )
    resp = client.post(
        "/api/v1/scientific/reactions/search",
        json={"reactants": ["C#CCO"], "products": ["C#CCN"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    _assert_no_internal_id_keys(body)
    matched = [
        r for r in body["records"] if r["reaction_entry_ref"] == re.public_ref
    ]
    assert matched


def test_kinetics_search_default_omits_ids(client, db_session):
    a = make_species(db_session, smiles="C#CCOC", inchi_key=next_inchi_key("PD_KA"))
    b = make_species(db_session, smiles="C#CCNC", inchi_key=next_inchi_key("PD_KB"))
    chem = make_chem_reaction(db_session, reactants=[a], products=[b])
    re = make_reaction_entry(
        db_session,
        reaction=chem,
        reactant_entries=[make_species_entry(db_session, a)],
        product_entries=[make_species_entry(db_session, b)],
    )
    make_kinetics(db_session, reaction_entry=re)
    resp = client.post(
        "/api/v1/scientific/kinetics/search",
        json={"reactants": ["C#CCOC"], "products": ["C#CCNC"]},
    )
    assert resp.status_code == 200
    _assert_no_internal_id_keys(resp.json())


def test_reaction_kinetics_detail_default_omits_ids(client, db_session):
    a = make_species(db_session, smiles="C#CN", inchi_key=next_inchi_key("PD_KDA"))
    b = make_species(db_session, smiles="C#CO", inchi_key=next_inchi_key("PD_KDB"))
    chem = make_chem_reaction(db_session, reactants=[a], products=[b])
    re = make_reaction_entry(
        db_session,
        reaction=chem,
        reactant_entries=[make_species_entry(db_session, a)],
        product_entries=[make_species_entry(db_session, b)],
    )
    make_kinetics(db_session, reaction_entry=re)
    resp = client.get(
        f"/api/v1/scientific/reaction-entries/{re.public_ref}/kinetics"
    )
    assert resp.status_code == 200
    body = resp.json()
    _assert_no_internal_id_keys(body)
    assert body["reaction_entry_ref"] == re.public_ref


def test_reaction_full_default_omits_ids(client, db_session):
    a = make_species(db_session, smiles="C#NCC", inchi_key=next_inchi_key("PD_FA"))
    b = make_species(db_session, smiles="C#NCO", inchi_key=next_inchi_key("PD_FB"))
    chem = make_chem_reaction(db_session, reactants=[a], products=[b])
    re = make_reaction_entry(
        db_session,
        reaction=chem,
        reactant_entries=[make_species_entry(db_session, a)],
        product_entries=[make_species_entry(db_session, b)],
    )
    make_kinetics(db_session, reaction_entry=re)
    resp = client.get(
        f"/api/v1/scientific/reaction-entries/{re.public_ref}/full"
    )
    assert resp.status_code == 200
    body = resp.json()
    _assert_no_internal_id_keys(body)
    assert body["reaction_entry"]["reaction_entry_ref"] == re.public_ref


def test_species_calculations_search_default_omits_ids(client, db_session):
    species = make_species(
        db_session, smiles="C#CCCN", inchi_key=next_inchi_key("PD_SC")
    )
    entry = make_species_entry(db_session, species)
    lot = make_lot(db_session, method="b3lyp", basis="def2tzvp")
    make_calculation(
        db_session,
        type=CalculationType.opt,
        species_entry_id=entry.id,
        lot_id=lot.id,
    )
    resp = client.get(
        "/api/v1/scientific/species-calculations/search?smiles=C%23CCCN"
    )
    assert resp.status_code == 200
    body = resp.json()
    _assert_no_internal_id_keys(body)
    rec = body["records"][0]
    # Object-array ref forms survive; bare integer arrays do not.
    assert "input_geometries" in rec["geometry"]
    assert "output_geometries" in rec["geometry"]
    assert "input_geometry_ids" not in rec["geometry"]
    assert "output_geometry_ids" not in rec["geometry"]
    assert "supporting_calculations" in rec["provenance"]
    assert "supporting_calculation_ids" not in rec["provenance"]


# ---------------------------------------------------------------------------
# include=internal_ids opt-in
# ---------------------------------------------------------------------------


def test_include_internal_ids_silently_dropped_when_disallowed(client, db_session):
    species = make_species(
        db_session, smiles="C#CCCNC", inchi_key=next_inchi_key("PD_SD")
    )
    make_species_entry(db_session, species)
    resp = client.get(
        "/api/v1/scientific/species/search?smiles=C%23CCCNC&include=internal_ids"
    )
    # Not a 4xx — the token is silently dropped.
    assert resp.status_code == 200
    body = resp.json()
    # The dropped token does not appear in the request echo.
    assert "internal_ids" not in body["request"]["include"]
    # IDs still hidden.
    _assert_no_internal_id_keys(body)


def test_include_internal_ids_restores_ids_when_allowed(
    client, db_session, allow_internal_ids
):
    species = make_species(
        db_session, smiles="C#CCCNCC", inchi_key=next_inchi_key("PD_SA")
    )
    entry = make_species_entry(db_session, species)
    resp = client.get(
        "/api/v1/scientific/species/search?smiles=C%23CCCNCC&include=internal_ids"
    )
    assert resp.status_code == 200
    body = resp.json()
    # The token is now in the resolved include echo.
    assert "internal_ids" in body["request"]["include"]
    rec = body["records"][0]
    # Integer ids restored; refs still present.
    assert rec["species_id"] == species.id
    assert rec["species_ref"] == species.public_ref
    entry_blocks = rec["entries"]
    assert entry_blocks[0]["species_entry_id"] == entry.id


def test_include_internal_ids_restores_bare_id_arrays_when_allowed(
    client, db_session, allow_internal_ids
):
    species = make_species(
        db_session, smiles="C#CCCNCCO", inchi_key=next_inchi_key("PD_BA")
    )
    entry = make_species_entry(db_session, species)
    lot = make_lot(db_session)
    make_calculation(
        db_session,
        type=CalculationType.opt,
        species_entry_id=entry.id,
        lot_id=lot.id,
    )
    resp = client.post(
        "/api/v1/scientific/species-calculations/search",
        json={"smiles": "C#CCCNCCO", "include": ["internal_ids"]},
    )
    assert resp.status_code == 200
    rec = resp.json()["records"][0]
    # Bare integer arrays are back.
    assert "input_geometry_ids" in rec["geometry"]
    assert "output_geometry_ids" in rec["geometry"]
    assert "supporting_calculation_ids" in rec["provenance"]
    # Object-array forms still present.
    assert "input_geometries" in rec["geometry"]
    assert "supporting_calculations" in rec["provenance"]


def test_include_all_does_not_restore_internal_ids(client, db_session):
    species = make_species(
        db_session, smiles="C#CCONC", inchi_key=next_inchi_key("PD_AL")
    )
    make_species_entry(db_session, species)
    resp = client.get(
        "/api/v1/scientific/species/search?smiles=C%23CCONC&include=all"
    )
    assert resp.status_code == 200
    body = resp.json()
    # `internal_ids` is not in the `all` expansion.
    assert "internal_ids" not in body["request"]["include"]
    _assert_no_internal_id_keys(body)


def test_include_all_plus_internal_ids_explicit(
    client, db_session, allow_internal_ids
):
    species = make_species(
        db_session, smiles="C#CONCC", inchi_key=next_inchi_key("PD_AI")
    )
    make_species_entry(db_session, species)
    resp = client.get(
        "/api/v1/scientific/species/search"
        "?smiles=C%23CONCC&include=all&include=internal_ids"
    )
    assert resp.status_code == 200
    body = resp.json()
    # Explicit opt-in survives `include=all`.
    assert "internal_ids" in body["request"]["include"]
    assert body["records"][0]["species_id"] == species.id


# ---------------------------------------------------------------------------
# /full include_review=full audit-array stripping
# ---------------------------------------------------------------------------


def test_full_audit_array_omits_record_id_by_default(client, db_session):
    a = make_species(db_session, smiles="C#CCOCO", inchi_key=next_inchi_key("PD_AR"))
    b = make_species(db_session, smiles="C#CCNCN", inchi_key=next_inchi_key("PD_AR2"))
    chem = make_chem_reaction(db_session, reactants=[a], products=[b])
    re = make_reaction_entry(
        db_session,
        reaction=chem,
        reactant_entries=[make_species_entry(db_session, a)],
        product_entries=[make_species_entry(db_session, b)],
    )
    set_review(
        db_session,
        record_type=SubmissionRecordType.reaction_entry,
        record_id=re.id,
        status=RecordReviewStatus.approved,
    )
    resp = client.get(
        f"/api/v1/scientific/reaction-entries/{re.public_ref}/full"
        "?include_review=full"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["review_records"] is not None
    for entry in body["review_records"]:
        # record_id is an internal PK with no ref sibling; Phase D hides it.
        assert "record_id" not in entry


def test_full_audit_array_keeps_record_id_with_internal_ids(
    client, db_session, allow_internal_ids
):
    a = make_species(db_session, smiles="C#CCOCN", inchi_key=next_inchi_key("PD_AK"))
    b = make_species(db_session, smiles="C#CCNNC", inchi_key=next_inchi_key("PD_AK2"))
    chem = make_chem_reaction(db_session, reactants=[a], products=[b])
    re = make_reaction_entry(
        db_session,
        reaction=chem,
        reactant_entries=[make_species_entry(db_session, a)],
        product_entries=[make_species_entry(db_session, b)],
    )
    set_review(
        db_session,
        record_type=SubmissionRecordType.reaction_entry,
        record_id=re.id,
        status=RecordReviewStatus.approved,
    )
    resp = client.get(
        f"/api/v1/scientific/reaction-entries/{re.public_ref}/full"
        "?include_review=full&include=internal_ids"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert any(
        e.get("record_id") == re.id for e in body["review_records"]
    )


# ---------------------------------------------------------------------------
# Request echo / route input compatibility
# ---------------------------------------------------------------------------


def test_request_filter_echo_does_not_leak_resolved_id_from_ref(
    client, db_session
):
    """Caller supplies species_ref; resolved species_id is NOT echoed."""
    species = make_species(
        db_session, smiles="C#CCONCC", inchi_key=next_inchi_key("PD_RE")
    )
    make_species_entry(db_session, species)
    resp = client.get(
        "/api/v1/scientific/species/search",
        params={"species_ref": species.public_ref},
    )
    assert resp.status_code == 200
    body = resp.json()
    filter_echo = body["request"]["filter"]
    # The ref the caller actually supplied is echoed.
    assert filter_echo.get("species_ref") == species.public_ref
    # The integer id resolved from the ref is NOT echoed.
    assert "species_id" not in filter_echo


def test_caller_supplied_id_filter_is_echoed_back(client, db_session):
    """Caller supplies level_of_theory_id; echo keeps it verbatim."""
    species = make_species(
        db_session, smiles="C#CCONOC", inchi_key=next_inchi_key("PD_IE")
    )
    entry = make_species_entry(db_session, species)
    lot = make_lot(db_session)
    make_calculation(
        db_session,
        type=CalculationType.sp,
        species_entry_id=entry.id,
        lot_id=lot.id,
    )
    resp = client.get(
        "/api/v1/scientific/species-calculations/search",
        params={"smiles": "C#CCONOC", "level_of_theory_id": lot.id},
    )
    assert resp.status_code == 200
    body = resp.json()
    # Caller-supplied integer id is part of the request echo even though
    # the response body itself hides resolved IDs.
    assert body["request"]["filter"]["level_of_theory_id"] == lot.id


def test_integer_path_handle_still_works_after_phase_d(client, db_session):
    """Phase D hides response IDs, but route inputs remain unchanged."""
    species = make_species(
        db_session, smiles="C#CCONOCC", inchi_key=next_inchi_key("PD_IP")
    )
    entry = make_species_entry(db_session, species)
    make_thermo_scalar(db_session, species_entry=entry)
    resp = client.get(
        f"/api/v1/scientific/species-entries/{entry.id}/thermo"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["species_entry_ref"] == entry.public_ref
    _assert_no_internal_id_keys(body)


def test_ref_path_handle_still_works_after_phase_d(client, db_session):
    species = make_species(
        db_session, smiles="C#CCONOOC", inchi_key=next_inchi_key("PD_RP")
    )
    entry = make_species_entry(db_session, species)
    make_thermo_scalar(db_session, species_entry=entry)
    resp = client.get(
        f"/api/v1/scientific/species-entries/{entry.public_ref}/thermo"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["species_entry_ref"] == entry.public_ref
    _assert_no_internal_id_keys(body)
