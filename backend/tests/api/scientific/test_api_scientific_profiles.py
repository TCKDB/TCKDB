"""The read profile must hold on *every* scientific endpoint, not most of them.

A profile that only some endpoints honour is worse than no profile, because it
teaches consumers to trust it. These tests walk the live app rather than a
hand-written list of endpoints, so an endpoint added later cannot quietly opt
out.
"""

from __future__ import annotations

import json

import pytest
from fastapi.routing import APIRoute

from app.api.app import create_app
from app.db.models.common import RecordReviewStatus, SubmissionRecordType
from app.services.record_review import set_record_review_status
from tests.services.scientific_read._factories import (
    make_species,
    make_species_entry,
    make_thermo_scalar,
)

SCIENTIFIC_PREFIX = "/api/v1/scientific"


def _scientific_routes() -> list[APIRoute]:
    app = create_app()
    return [
        route
        for route in app.routes
        if isinstance(route, APIRoute) and route.path.startswith(SCIENTIFIC_PREFIX)
    ]


# ---------------------------------------------------------------------------
# Universality of the knob itself
# ---------------------------------------------------------------------------


def test_every_scientific_operation_declares_profile_and_release():
    """Static contract: the knob exists on all of them, in the OpenAPI too."""
    app = create_app()
    schema = app.openapi()
    missing = []
    checked = 0
    for path, operations in schema["paths"].items():
        if not path.startswith(SCIENTIFIC_PREFIX):
            continue
        for method, operation in operations.items():
            checked += 1
            names = {p["name"] for p in operation.get("parameters", [])}
            if "profile" not in names or "release" not in names:
                missing.append(f"{method.upper()} {path}")
    assert checked > 50, "route inventory looks wrong; did the router change?"
    assert missing == [], f"scientific operations without a profile knob: {missing}"


def test_every_enveloped_response_model_declares_the_profile_echo():
    """Static contract: the echo is part of the published response shape."""
    offenders = []
    for route in _scientific_routes():
        model = route.response_model
        if model is None:
            continue
        fields = getattr(model, "model_fields", {})
        echo = fields.get("request")
        if echo is None:
            continue
        echo_fields = getattr(echo.annotation, "model_fields", {})
        if not {
            "profile",
            "profile_recommendation",
            "profile_release_ref",
        } <= set(echo_fields):
            offenders.append(f"{route.path} -> {model.__name__}")
    assert offenders == [], f"response envelopes missing the profile echo: {offenders}"


# ---------------------------------------------------------------------------
# Runtime echo on every reachable GET
# ---------------------------------------------------------------------------


def _reachable_gets() -> list[str]:
    """Scientific GETs with no path parameters — callable with no fixtures."""
    paths = []
    for route in _scientific_routes():
        if "GET" not in route.methods or "{" in route.path:
            continue
        paths.append(route.path)
    return sorted(set(paths))


# Search endpoints refuse an unscoped query (``missing_filter``), by design.
# This ladder supplies the cheapest filter each family accepts; the first
# query string that yields 200 is the one whose echo gets checked. Adding a
# new search endpoint that none of these satisfies fails the test loudly
# rather than silently skipping it.
_FILTER_LADDER = (
    "",
    "formula=CH4",
    "method=B3LYP",
    "reactants=CH4",
    "query_smiles=CCO",
    # Bulk exports take a seed, not a filter.
    "all=true",
)


@pytest.mark.parametrize("path", _reachable_gets())
def test_every_parameterless_scientific_get_echoes_the_resolved_profile(
    client, login_as, _api_curator_user, path
):
    """Runtime contract: ask for curated, and the answer says curated.

    Runs as a curator so the curator-gated exports are exercised too — those
    stream NDJSON and carry the echo in their manifest line rather than in a
    JSON envelope, and that path needs covering just as much.
    """
    login_as(_api_curator_user)

    response = None
    for extra in _FILTER_LADDER:
        query = f"profile=curated&{extra}" if extra else "profile=curated"
        response = client.get(f"{path}?{query}")
        if response.status_code == 200:
            break
    assert response is not None and response.status_code == 200, (
        f"{path} could not be exercised with any filter in the ladder: "
        f"{response.text[:300] if response is not None else 'no response'}"
    )

    if "ndjson" in response.headers.get("content-type", ""):
        manifest = json.loads(response.text.splitlines()[0])
        assert manifest["record_type"] == "manifest", path
        assert manifest["profile"] == "curated", path
        assert "profile_recommendation" in manifest, path
        return

    body = response.json()
    assert "request" in body, f"{path} returned no request echo"
    echo = body["request"]
    assert echo["profile"] == "curated", path
    assert "profile_recommendation" in echo, path
    assert "profile_release_ref" in echo, path


def test_default_profile_is_exploratory_and_disclaims_recommendation(client):
    echo = client.get("/api/v1/scientific/thermo/search?formula=CH4").json()["request"]
    assert echo["profile"] == "exploratory"
    assert echo["profile_recommendation"] == "none"
    assert echo["profile_release_ref"] is None


def test_meta_endpoints_echo_the_profile_too(client):
    """These return a bare results list and would be the easy one to forget."""
    for path in (
        "/api/v1/scientific/meta/methods",
        "/api/v1/scientific/meta/basis-sets",
        "/api/v1/scientific/meta/software",
        "/api/v1/scientific/meta/reaction-families",
    ):
        body = client.get(f"{path}?profile=curated").json()
        assert body["request"]["profile"] == "curated", path


def test_post_search_endpoints_accept_the_profile_query_key(client):
    """The POST guard rejects stray query keys; profile must be allowed."""
    response = client.post(
        "/api/v1/scientific/thermo/search?profile=curated", json={"formula": "CH4"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["request"]["profile"] == "curated"


def test_profile_values_are_machine_tokens_not_prose(client):
    echo = client.get(
        "/api/v1/scientific/thermo/search?formula=CH4&profile=curated"
    ).json()["request"]
    for key in ("profile", "profile_recommendation"):
        value = echo[key]
        assert " " not in value and value == value.lower(), (key, value)


def test_unknown_profile_value_is_rejected(client):
    assert (
        client.get(
            "/api/v1/scientific/thermo/search?formula=CH4&profile=best"
        ).status_code
        == 422
    )


def test_release_pin_is_rejected_rather_than_accepted_and_ignored(client):
    """``?release=`` used to be accepted, resolved, echoed — and never applied.

    It looked like scoping and did nothing: the resolved release id was set and
    read by no code path at all. Refusing it with a pointer to the endpoints
    that *do* answer the question is the honest behaviour.
    """
    for query in (
        "formula=CH4&release=2026.07.0",
        "formula=CH4&profile=curated&release=2026.07.0",
        "formula=CH4&profile=curated&release=rel_whatever",
    ):
        response = client.get(f"/api/v1/scientific/thermo/search?{query}")
        assert response.status_code == 422, query
        assert "release_scoping_not_implemented" in response.text
        # …and it says where to go instead.
        assert "releases/{tag}/selections" in response.text


# ---------------------------------------------------------------------------
# The profile actually changes what comes back
# ---------------------------------------------------------------------------


@pytest.fixture
def approved_and_draft_thermo(db_session, _api_curator_user):
    """One approved thermo record and one left under review."""
    from app.db.models.app_user import AppUser

    curator = db_session.get(AppUser, _api_curator_user)
    entry = make_species_entry(
        db_session, species=make_species(db_session, smiles="CCO")
    )
    approved = make_thermo_scalar(
        db_session, species_entry=entry, h298_kj_mol=-234.5, s298_j_mol_k=281.6
    )
    unreviewed = make_thermo_scalar(
        db_session, species_entry=entry, h298_kj_mol=-111.1, s298_j_mol_k=200.0
    )
    set_record_review_status(
        db_session,
        record_type=SubmissionRecordType.thermo,
        record_id=approved.id,
        status=RecordReviewStatus.approved,
        actor=curator,
    )
    db_session.flush()
    return entry, approved, unreviewed


def test_curated_hides_unapproved_candidates_that_exploratory_shows(
    client, approved_and_draft_thermo
):
    entry, approved, unreviewed = approved_and_draft_thermo
    base = f"/api/v1/scientific/species-entries/{entry.public_ref}/thermo"

    exploratory = client.get(base).json()
    refs = {r["thermo_ref"] for r in exploratory["records"]}
    assert {approved.public_ref, unreviewed.public_ref} <= refs

    curated = client.get(f"{base}?profile=curated").json()
    curated_refs = {r["thermo_ref"] for r in curated["records"]}
    assert approved.public_ref in curated_refs
    assert unreviewed.public_ref not in curated_refs
    assert curated["request"]["profile"] == "curated"


def test_curated_overrides_an_attempt_to_include_rejected_records(
    client, approved_and_draft_thermo, db_session, _api_curator_user
):
    """"Show me what TCKDB stands behind, plus the rejected ones" is incoherent."""
    from app.db.models.app_user import AppUser

    entry, _approved, unreviewed = approved_and_draft_thermo
    curator = db_session.get(AppUser, _api_curator_user)
    set_record_review_status(
        db_session,
        record_type=SubmissionRecordType.thermo,
        record_id=unreviewed.id,
        status=RecordReviewStatus.rejected,
        actor=curator,
    )
    db_session.flush()

    base = f"/api/v1/scientific/species-entries/{entry.public_ref}/thermo"
    body = client.get(f"{base}?profile=curated&include_rejected=true").json()
    assert unreviewed.public_ref not in {r["thermo_ref"] for r in body["records"]}


def test_curated_does_not_widen_a_stricter_caller_filter(
    client, approved_and_draft_thermo
):
    """The profile floor narrows; it never relaxes what the caller asked for."""
    entry, approved, _unreviewed = approved_and_draft_thermo
    base = f"/api/v1/scientific/species-entries/{entry.public_ref}/thermo"
    body = client.get(f"{base}?profile=curated&min_review_status=approved").json()
    assert {r["thermo_ref"] for r in body["records"]} == {approved.public_ref}


def test_curated_never_claims_release_backing_on_the_general_read_surface(
    client, approved_and_draft_thermo
):
    """The false endorsement that failed review.

    ``tckdb_curated_release`` means "an attributed selection names *these*
    records". No per-record annotation exists, and the release used to be
    resolved per database, so any curated response claimed backing once any
    release was published — including for records a curator had explicitly
    passed over. The honest token here is ``approved_floor_only``.
    """
    entry, _approved, _unreviewed = approved_and_draft_thermo
    base = f"/api/v1/scientific/species-entries/{entry.public_ref}/thermo"
    echo = client.get(f"{base}?profile=curated").json()["request"]
    assert echo["profile"] == "curated"
    assert echo["profile_recommendation"] == "approved_floor_only"
    assert echo["profile_release_ref"] is None


def test_curated_applies_the_floor_to_detail_by_ref_reads(
    client, db_session, _api_curator_user
):
    """Roughly half the read services never call ``visible_statuses``.

    A never-reviewed statmech used to come back under ``profile=curated`` with
    a curated echo. The floor is now applied at handle resolution, which every
    detail-by-ref read passes through, so it cannot be forgotten.
    """
    from app.db.models.app_user import AppUser
    from tests.services.scientific_read._factories import make_statmech

    curator = db_session.get(AppUser, _api_curator_user)
    entry = make_species_entry(
        db_session, species=make_species(db_session, smiles="CCO")
    )
    unreviewed = make_statmech(db_session, species_entry=entry)
    approved = make_statmech(db_session, species_entry=entry)
    set_record_review_status(
        db_session,
        record_type=SubmissionRecordType.statmech,
        record_id=approved.id,
        status=RecordReviewStatus.approved,
        actor=curator,
    )
    db_session.flush()

    # Exploratory: both addressable.
    assert (
        client.get(f"/api/v1/scientific/statmech/{unreviewed.public_ref}").status_code
        == 200
    )
    # Curated: the unapproved one is simply not part of the surface.
    assert (
        client.get(
            f"/api/v1/scientific/statmech/{unreviewed.public_ref}?profile=curated"
        ).status_code
        == 404
    )
    curated = client.get(
        f"/api/v1/scientific/statmech/{approved.public_ref}?profile=curated"
    )
    assert curated.status_code == 200
    assert curated.json()["request"]["profile_recommendation"] == "approved_floor_only"


# ---------------------------------------------------------------------------
# Non-enveloped surfaces
# ---------------------------------------------------------------------------


def test_export_manifest_line_carries_the_profile(client, db_session, _api_curator_user, login_as):
    """"Echoed in every dataset manifest" includes the streaming projections."""
    entry = make_species_entry(
        db_session, species=make_species(db_session, smiles="CCO")
    )
    login_as(_api_curator_user)
    response = client.get(
        "/api/v1/scientific/export/ndjson"
        f"?species_ref={entry.public_ref}&profile=curated&min_review_status=not_reviewed"
    )
    assert response.status_code == 200, response.text
    manifest = json.loads(response.text.splitlines()[0])
    assert manifest["record_type"] == "manifest"
    assert manifest["profile"] == "curated"
    assert manifest["profile_recommendation"] == "approved_floor_only"
    # And it still says what it is not.
    assert manifest["contract"]["citable_release"] is False
    assert manifest["contract"]["lossless"] is False


def test_ml_export_manifest_line_carries_the_profile(
    client, db_session, _api_curator_user, login_as
):
    entry = make_species_entry(
        db_session, species=make_species(db_session, smiles="CCO")
    )
    login_as(_api_curator_user)
    response = client.get(
        "/api/v1/scientific/export/ml/species.ndjson"
        f"?species_ref={entry.public_ref}&profile=curated&min_review_status=not_reviewed"
    )
    assert response.status_code == 200, response.text
    manifest = json.loads(response.text.splitlines()[0])
    assert manifest["record_type"] == "manifest"
    assert manifest["profile"] == "curated"


# ---------------------------------------------------------------------------
# The curated floor covers records whose review lives on a parent
# ---------------------------------------------------------------------------


def test_network_kinetics_inherits_the_curated_floor_from_its_solve(
    client, db_session, _api_curator_user
):
    """``approved_floor_only`` must be true for *every* endpoint that echoes it.

    ``network_kinetics`` has no ``record_review`` row in principle — a set of
    k(T,P) coefficients is reviewed as part of the ``network_solve`` that
    produced it. Being absent from the gate map entirely, it returned 200 with
    an ``approved_floor_only`` echo while its own parent correctly 404'd, so the
    response made a false machine-readable claim.
    """
    from app.db.models.app_user import AppUser
    from app.db.models.common import (
        NetworkChannelKind,
        NetworkKineticsModelKind,
        NetworkStateKind,
    )
    from tests.services.scientific_read._factories import (
        make_network,
        make_network_channel,
        make_network_kinetics,
        make_network_solve,
        make_network_state,
    )

    curator = db_session.get(AppUser, _api_curator_user)
    network = make_network(db_session)
    state_a = make_network_state(
        db_session, network=network, kind=NetworkStateKind.well, composition_hash="c" * 64
    )
    state_b = make_network_state(
        db_session, network=network, kind=NetworkStateKind.well, composition_hash="d" * 64
    )
    channel = make_network_channel(
        db_session,
        network=network,
        source_state=state_a,
        sink_state=state_b,
        kind=NetworkChannelKind.isomerization,
    )
    solve = make_network_solve(db_session, network=network)
    kinetics = make_network_kinetics(
        db_session,
        channel=channel,
        solve=solve,
        model_kind=NetworkKineticsModelKind.chebyshev,
    )
    db_session.flush()

    detail = f"/api/v1/scientific/network-kinetics/{kinetics.public_ref}"
    parent = f"/api/v1/scientific/network-solves/{solve.public_ref}"

    # Exploratory: both addressable.
    assert client.get(detail).status_code == 200
    assert client.get(parent).status_code == 200

    # Curated, nothing reviewed: the child must 404 exactly like its parent.
    assert client.get(f"{parent}?profile=curated").status_code == 404
    assert client.get(f"{detail}?profile=curated").status_code == 404

    # Approving the parent admits the child.
    set_record_review_status(
        db_session,
        record_type=SubmissionRecordType.network_solve,
        record_id=solve.id,
        status=RecordReviewStatus.approved,
        actor=curator,
    )
    db_session.flush()

    response = client.get(f"{detail}?profile=curated")
    assert response.status_code == 200, response.text
    assert response.json()["request"]["profile_recommendation"] == "approved_floor_only"


def test_every_handle_resolver_is_classified_for_the_curated_floor():
    """A new detail resolver must not slip past the floor unnoticed.

    ``network_kinetics`` did exactly that. Every ORM class reaching
    ``resolve_path_handle`` must be gated directly, gated through a parent, or
    be a type the floor deliberately does not cover — and the third case is
    enumerated here so adding a resolver forces a decision.
    """
    import re
    from pathlib import Path

    from app.services.scientific_read import handles

    source = Path(handles.__file__).read_text()
    reaching = set(re.findall(r"resolve_path_handle\(\s*\n?\s*session,\s*(\w+),", source))
    assert reaching, "resolver scan found nothing; did the call shape change?"

    gated = {cls.__name__ for cls in handles._REVIEWABLE_HANDLE_TYPES}
    parent_derived = {cls.__name__ for cls in handles._PARENT_DERIVED_HANDLE_TYPES}

    # Deliberately ungated, with the reason recorded next to the name.
    ungated = {
        # Structure, vocabulary and provenance: no reviewable claim of their
        # own, and gating them would 404 the level of theory a curated record
        # cites.
        "Geometry",
        "Literature",
        "FrequencyScaleFactor",
        "EnergyCorrectionScheme",
        # Scoping parents: they say which records to look under, not what is
        # returned. The floor is applied to the products by visible_statuses.
        "SpeciesEntry",
        "ReactionEntry",
    }

    unclassified = reaching - gated - parent_derived - ungated
    assert unclassified == set(), (
        f"handle resolvers not classified for the curated floor: {unclassified}. "
        "Add each to _REVIEWABLE_HANDLE_TYPES, to _PARENT_DERIVED_HANDLE_TYPES "
        "if its review state lives on a parent, or to this test's `ungated` set "
        "with the reason it makes no reviewable claim."
    )
    assert not (gated & parent_derived)
