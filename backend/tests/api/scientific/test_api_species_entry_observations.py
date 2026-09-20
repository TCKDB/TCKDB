"""API tests for GET /api/v1/scientific/species-entries/{id}/observations.

Phase C-E5: the first public read of ``molecular_property_observation``.
Mirrors ``test_api_species_transport.py`` for handle resolution, pagination,
review filtering, and the internal-id policy; this record type carries no
``collapse`` / ``selection_policy`` / ``trust`` (see the schema's module
docstring), so those sections of the transport test have no counterpart
here.
"""

from __future__ import annotations

from app.api.config import settings
from app.db.models.common import (
    MolecularPropertyKind,
    ObservedUncertaintyAssessor,
    ObservedUncertaintyKind,
    RecordReviewStatus,
    ScientificOriginKind,
    SubmissionRecordType,
)
from tests.services.scientific_read._factories import (
    make_external_source,
    make_external_source_record,
    make_literature,
    make_observation,
    make_species,
    make_species_entry,
    next_inchi_key,
    set_review,
)


def _entry(db_session):
    species = make_species(
        db_session, smiles="CCO", inchi_key=next_inchi_key("SEOBS")
    )
    return species, make_species_entry(db_session, species)


def _url(handle, **params) -> str:
    base = f"/api/v1/scientific/species-entries/{handle}/observations"
    if not params:
        return base
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{base}?{qs}"


# ---------------------------------------------------------------------------
# Basics + handle resolution
# ---------------------------------------------------------------------------


def test_returns_200_for_valid_species_entry_id(client, db_session):
    _, entry = _entry(db_session)
    obs = make_observation(db_session, species_entry=entry)

    resp = client.get(_url(entry.id))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["request"]["filter"]["species_entry_ref"] == entry.public_ref
    assert body["pagination"]["total"] == 1
    assert len(body["records"]) == 1
    assert body["records"][0]["observation_ref"] == obs.public_ref


def test_resolves_species_entry_ref_handle(client, db_session):
    _, entry = _entry(db_session)
    make_observation(db_session, species_entry=entry)

    resp = client.get(_url(entry.public_ref))
    assert resp.status_code == 200, resp.text
    assert resp.json()["pagination"]["total"] == 1


def test_returns_404_for_missing_species_entry(client, db_session):
    resp = client.get(_url(999999))
    assert resp.status_code == 404
    assert "handle_not_found" in resp.text or "not found" in resp.text


def test_wrong_prefix_handle_returns_422(client, db_session):
    resp = client.get(_url("trn_abcdef0123456789"))
    assert resp.status_code == 422
    assert "handle_type_mismatch" in resp.text


def test_empty_when_entry_has_no_observations(client, db_session):
    _, entry = _entry(db_session)
    body = client.get(_url(entry.id)).json()
    assert body["pagination"]["total"] == 0
    assert body["records"] == []


def test_only_returns_observations_for_the_requested_entry(client, db_session):
    """The service's query pins ``species_entry_id`` -- deleting that filter
    left every route test green (all 21 of them use exactly one entry with
    observations). Three rows: one on the requested entry, one on an
    unrelated second entry, and one identity-unresolved (``species_entry_id
    IS NULL``, structurally unreachable). Only the first may come back.
    """
    _, entry = _entry(db_session)
    other_species = make_species(
        db_session, smiles="CCN", inchi_key=next_inchi_key("SEOBSOTHER")
    )
    other_entry = make_species_entry(db_session, other_species)

    wanted = make_observation(db_session, species_entry=entry)
    make_observation(db_session, species_entry=other_entry)
    make_observation(db_session, species_entry=None)

    resp = client.get(_url(entry.id))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["pagination"]["total"] == 1
    assert len(body["records"]) == 1
    assert body["records"][0]["observation_ref"] == wanted.public_ref


def test_rejects_client_sort(client, db_session):
    _, entry = _entry(db_session)
    resp = client.get(_url(entry.id, sort="created_at"))
    assert resp.status_code == 422
    assert "client_sort_not_supported" in resp.text


def test_unknown_include_token_returns_422(client, db_session):
    _, entry = _entry(db_session)
    resp = client.get(_url(entry.id, include="banana"))
    assert resp.status_code == 422
    assert "unknown_include_token" in resp.text


def test_rejects_a_limit_above_the_service_cap(client, db_session, monkeypatch):
    _, entry = _entry(db_session)

    outer = client.get(_url(entry.id, limit=999))
    assert outer.status_code == 422
    assert outer.json()["code"] == "request_validation_error"

    monkeypatch.setattr(settings, "public_max_limit", 10)
    inner = client.get(_url(entry.id, limit=50))
    assert inner.status_code == 422, inner.text
    assert inner.json()["code"] == "limit_too_large"


# ---------------------------------------------------------------------------
# property_kind filter
# ---------------------------------------------------------------------------


def test_property_kind_filter(client, db_session):
    _, entry = _entry(db_session)
    dipole = make_observation(
        db_session,
        species_entry=entry,
        property_kind=MolecularPropertyKind.dipole_moment,
    )
    make_observation(
        db_session,
        species_entry=entry,
        property_kind=MolecularPropertyKind.ionization_energy,
        scalar_value=10.5,
        scalar_unit="eV",
    )

    body = client.get(
        _url(entry.id, property_kind="dipole_moment")
    ).json()
    assert body["pagination"]["total"] == 1
    assert body["records"][0]["observation_ref"] == dipole.public_ref
    assert body["request"]["filter"]["property_kind"] == "dipole_moment"


def test_invalid_property_kind_returns_422(client, db_session):
    _, entry = _entry(db_session)
    resp = client.get(_url(entry.id, property_kind="not_a_real_kind"))
    assert resp.status_code == 422
    assert "invalid_property_kind" in resp.text


# ---------------------------------------------------------------------------
# Field content: values, uncertainty, external source, literature, review
# ---------------------------------------------------------------------------


def test_record_carries_typed_fields(client, db_session):
    _, entry = _entry(db_session)
    lit = make_literature(db_session, title="A Dipole Paper", year=1990)
    obs = make_observation(
        db_session,
        species_entry=entry,
        property_kind=MolecularPropertyKind.dipole_moment,
        scalar_value=1.85,
        scalar_unit="D",
        scalar_uncertainty=0.02,
        uncertainty_kind=ObservedUncertaintyKind.expanded,
        uncertainty_coverage_factor=2.0,
        uncertainty_level_of_confidence_pct=95.0,
        uncertainty_assessor=ObservedUncertaintyAssessor.source_author,
        scientific_origin=ScientificOriginKind.experimental,
        temperature_k=298.15,
        pressure_bar=1.0,
        method_note="microwave spectroscopy",
        literature_id=lit.id,
        reference_label="Gurvich",
    )

    body = client.get(_url(entry.id)).json()
    rec = body["records"][0]
    assert rec["observation_ref"] == obs.public_ref
    assert rec["property_kind"] == "dipole_moment"
    assert rec["scalar_value"] == 1.85
    assert rec["scalar_unit"] == "D"
    assert rec["uncertainty"] == {
        "value": 0.02,
        "kind": "expanded",
        "coverage_factor": 2.0,
        "level_of_confidence_pct": 95.0,
        "assessor": "source_author",
    }
    assert rec["temperature_k"] == 298.15
    assert rec["pressure_bar"] == 1.0
    assert rec["method_note"] == "microwave spectroscopy"
    assert rec["scientific_origin"] == "experimental"
    assert rec["literature_ref"] == lit.public_ref
    assert rec["reference_label"] == "Gurvich"
    assert rec["review"]["status"] == "not_reviewed"


def test_legacy_flattened_external_source(client, db_session):
    _, entry = _entry(db_session)
    make_observation(
        db_session,
        species_entry=entry,
        external_source_name="CCCBDB",
        external_source_release="Release 22, May 2022",
    )
    body = client.get(_url(entry.id)).json()
    src = body["records"][0]["external_source"]
    assert src["name"] == "CCCBDB"
    assert src["release"] == "Release 22, May 2022"
    assert src["schema_label"] is None


def test_custody_external_source_record(client, db_session):
    _, entry = _entry(db_session)
    source = make_external_source(db_session)
    esr = make_external_source_record(
        db_session,
        external_source=source,
        source_record_key="doc-42",
        content_sha256="b" * 64,
        schema_id="thermoml.v1",
        parser_version="2.0.0",
        mapping_version="3.0.0",
    )
    make_observation(
        db_session,
        species_entry=entry,
        external_source_record_id=esr.id,
    )
    body = client.get(_url(entry.id)).json()
    src = body["records"][0]["external_source"]
    assert src["name"] == source.source_name
    assert src["release"] == source.source_release
    assert src["record_key"] == "doc-42"
    assert src["content_sha256"] == "b" * 64
    assert src["schema_label"] == "thermoml.v1"
    assert src["parser_version"] == "2.0.0"
    assert src["mapping_version"] == "3.0.0"


def test_no_external_source_is_null(client, db_session):
    _, entry = _entry(db_session)
    make_observation(db_session, species_entry=entry)
    body = client.get(_url(entry.id)).json()
    assert body["records"][0]["external_source"] is None


def test_no_uncertainty_is_null(client, db_session):
    _, entry = _entry(db_session)
    make_observation(db_session, species_entry=entry, scalar_uncertainty=None)
    body = client.get(_url(entry.id)).json()
    assert body["records"][0]["uncertainty"] is None


# ---------------------------------------------------------------------------
# Internal-ID policy: no id/*_id keys leak
# ---------------------------------------------------------------------------


def test_no_internal_id_keys_leak_by_default(client, db_session):
    _, entry = _entry(db_session)
    make_observation(db_session, species_entry=entry)
    body = client.get(_url(entry.id)).json()
    rec = body["records"][0]

    def _walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                assert k != "id" and not k.endswith("_id") and not k.endswith(
                    "_ids"
                ), f"leaked internal id key {k!r} in {node!r}"
                _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(rec)


def test_internal_ids_restored_when_policy_allows(
    client, db_session, allow_internal_ids
):
    _, entry = _entry(db_session)
    obs = make_observation(db_session, species_entry=entry)
    body = client.get(_url(entry.id, include="internal_ids")).json()
    # observation_ref stays a ref; internal ids surface via species_entry_id
    # in the (passthrough) request.filter only when explicitly asked for --
    # the record itself carries no bare id in this schema, so this call
    # mainly proves the include token round-trips without a 422.
    assert body["records"][0]["observation_ref"] == obs.public_ref
    assert body["request"]["include"] == ["internal_ids"]


# ---------------------------------------------------------------------------
# Review filtering / ordering
# ---------------------------------------------------------------------------


def test_default_hides_rejected(client, db_session):
    _, entry = _entry(db_session)
    obs_a = make_observation(db_session, species_entry=entry)
    obs_b = make_observation(db_session, species_entry=entry, scalar_value=2.0)
    set_review(
        db_session,
        record_type=SubmissionRecordType.molecular_property_observation,
        record_id=obs_b.id,
        status=RecordReviewStatus.rejected,
    )
    body = client.get(_url(entry.id)).json()
    refs = {r["observation_ref"] for r in body["records"]}
    assert obs_a.public_ref in refs
    assert obs_b.public_ref not in refs


def test_include_rejected_sorts_them_last(client, db_session):
    _, entry = _entry(db_session)
    obs_a = make_observation(db_session, species_entry=entry)
    obs_b = make_observation(db_session, species_entry=entry, scalar_value=2.0)
    set_review(
        db_session,
        record_type=SubmissionRecordType.molecular_property_observation,
        record_id=obs_b.id,
        status=RecordReviewStatus.rejected,
    )
    body = client.get(_url(entry.id, include_rejected="true")).json()
    refs = [r["observation_ref"] for r in body["records"]]
    assert obs_a.public_ref in refs and obs_b.public_ref in refs
    assert refs[-1] == obs_b.public_ref


def test_pagination_envelope(client, db_session):
    _, entry = _entry(db_session)
    for i in range(4):
        make_observation(db_session, species_entry=entry, scalar_value=float(i))
    body = client.get(_url(entry.id, limit=2, offset=0)).json()
    p = body["pagination"]
    assert (p["limit"], p["offset"], p["returned"], p["total"]) == (2, 0, 2, 4)


# ---------------------------------------------------------------------------
# No mutation
# ---------------------------------------------------------------------------


def test_read_does_not_mutate_observation(client, db_session):
    _, entry = _entry(db_session)
    obs = make_observation(db_session, species_entry=entry)
    before = (obs.scalar_value, obs.scalar_unit)
    resp = client.get(_url(entry.id))
    assert resp.status_code == 200, resp.text
    db_session.refresh(obs)
    after = (obs.scalar_value, obs.scalar_unit)
    assert after == before
