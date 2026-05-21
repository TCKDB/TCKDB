"""API tests for the scientific frequency-scale-factor and
energy-correction-scheme detail + search endpoints.
"""

from __future__ import annotations

import pytest

from app.db.models.common import (
    EnergyCorrectionApplicationRole,
    EnergyCorrectionSchemeKind,
    EnergyUnit,
    FrequencyScaleKind,
    MeliusBacComponentKind,
)
from tests.services.scientific_read._factories import (
    attach_ecs_atom_param,
    attach_ecs_bond_param,
    attach_ecs_component_param,
    make_applied_energy_correction,
    make_energy_correction_scheme,
    make_frequency_scale_factor,
    make_literature,
    make_lot,
    make_software,
    make_species,
    make_species_entry,
    make_statmech,
    make_workflow_tool_release,
    next_inchi_key,
)


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------


def _fsf_detail_url(handle: str, **params) -> str:
    base = f"/api/v1/scientific/frequency-scale-factors/{handle}"
    if not params:
        return base
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{base}?{qs}"


def _fsf_search_url(**params) -> str:
    base = "/api/v1/scientific/frequency-scale-factors/search"
    if not params:
        return base
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{base}?{qs}"


def _ecs_detail_url(handle: str, **params) -> str:
    base = f"/api/v1/scientific/energy-correction-schemes/{handle}"
    if not params:
        return base
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{base}?{qs}"


def _ecs_search_url(**params) -> str:
    base = "/api/v1/scientific/energy-correction-schemes/search"
    if not params:
        return base
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{base}?{qs}"


# ---------------------------------------------------------------------------
# Forbidden-payload recursive walker
# ---------------------------------------------------------------------------


_FORBIDDEN_KEYS: frozenset[str] = frozenset(
    {
        "body",
        "content",
        "data",
        "presigned_url",
        "download_url",
        "coordinates",
        "geometry",
    }
)


def _walk_forbidden(value, *, path: str = "") -> list[str]:
    """Walk a JSON payload looking for forbidden payload keys.

    ``geometry`` and ``coordinates`` are flagged anywhere — the
    correction surfaces should not surface geometry data.
    """
    violations: list[str] = []
    if isinstance(value, dict):
        for k, v in value.items():
            here = f"{path}.{k}" if path else k
            if k in _FORBIDDEN_KEYS:
                violations.append(here)
            violations.extend(_walk_forbidden(v, path=here))
    elif isinstance(value, list):
        for i, item in enumerate(value):
            here = f"{path}[{i}]"
            violations.extend(_walk_forbidden(item, path=here))
    return violations


# ===========================================================================
# Frequency scale factor — detail
# ===========================================================================


def test_fsf_detail_by_ref_returns_record(client, db_session):
    fsf = make_frequency_scale_factor(db_session)
    resp = client.get(_fsf_detail_url(fsf.public_ref))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (
        body["record"]["frequency_scale_factor"]["frequency_scale_factor_ref"]
        == fsf.public_ref
    )


def test_fsf_detail_by_integer_id_works(client, db_session, allow_internal_ids):
    fsf = make_frequency_scale_factor(db_session)
    resp = client.get(
        _fsf_detail_url(str(fsf.id), include="internal_ids")
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (
        body["record"]["frequency_scale_factor"]["frequency_scale_factor_id"]
        == fsf.id
    )


def test_fsf_detail_unknown_ref_returns_404(client, db_session):
    resp = client.get(_fsf_detail_url("fsf_doesnotexist00000"))
    assert resp.status_code == 404
    assert "frequency_scale_factor not found" in resp.text


def test_fsf_detail_wrong_prefix_returns_422(client, db_session):
    resp = client.get(_fsf_detail_url("ecs_abcdef0123456789"))
    assert resp.status_code == 422
    assert "handle_type_mismatch" in resp.text


def test_fsf_detail_malformed_handle_returns_422(client, db_session):
    resp = client.get(_fsf_detail_url("not-a-handle"))
    assert resp.status_code == 422
    assert "invalid_handle" in resp.text


def test_fsf_detail_default_response_shape(client, db_session):
    fsf = make_frequency_scale_factor(db_session)
    body = client.get(_fsf_detail_url(fsf.public_ref)).json()
    record = body["record"]
    for key in (
        "frequency_scale_factor",
        "evidence_summary",
        "available_sections",
    ):
        assert key in record
    assert record["used_by"] is None
    # FSF is non-reviewable; the summary is always empty.
    assert body["review_summary"]["total"] == 0


def test_fsf_detail_default_strips_internal_ids(client, db_session):
    fsf = make_frequency_scale_factor(db_session)
    body = client.get(_fsf_detail_url(fsf.public_ref)).json()
    core = body["record"]["frequency_scale_factor"]
    assert "frequency_scale_factor_id" not in core
    assert "frequency_scale_factor_ref" in core


def test_fsf_detail_lot_summary_present(client, db_session):
    lot = make_lot(db_session, method="b3lyp", basis="6-31g(d)")
    fsf = make_frequency_scale_factor(db_session, lot=lot)
    body = client.get(_fsf_detail_url(fsf.public_ref)).json()
    summary = body["record"]["level_of_theory"]
    assert summary is not None
    assert summary["method"] == "b3lyp"


def test_fsf_detail_include_used_by_with_statmech(client, db_session):
    fsf = make_frequency_scale_factor(db_session)
    species = make_species(db_session, smiles="C", inchi_key=next_inchi_key("FSF1"))
    entry = make_species_entry(db_session, species)
    sm = make_statmech(
        db_session, species_entry=entry, frequency_scale_factor_id=fsf.id
    )
    body = client.get(
        _fsf_detail_url(fsf.public_ref, include="used_by")
    ).json()
    used = body["record"]["used_by"]
    assert used is not None and len(used) == 1
    assert used[0]["record_type"] == "statmech"
    assert used[0]["record_ref"] == sm.public_ref
    assert used[0]["endpoint"].endswith(sm.public_ref)


def test_fsf_detail_include_literature(client, db_session):
    lit = make_literature(db_session)
    fsf = make_frequency_scale_factor(db_session, source_literature=lit)
    body = client.get(
        _fsf_detail_url(fsf.public_ref, include="literature")
    ).json()
    assert body["record"]["literature"]["literature_ref"] == lit.public_ref


def test_fsf_detail_include_all_expands_to_public_tokens(client, db_session):
    lit = make_literature(db_session)
    fsf = make_frequency_scale_factor(db_session, source_literature=lit)
    body = client.get(_fsf_detail_url(fsf.public_ref, include="all")).json()
    echo = body["request"]["include"]
    # ``all`` excludes ``internal_ids`` per policy.
    assert "internal_ids" not in echo
    assert "used_by" in echo
    assert "literature" in echo


def test_fsf_detail_include_all_does_not_restore_internal_ids(
    client, db_session
):
    fsf = make_frequency_scale_factor(db_session)
    body = client.get(_fsf_detail_url(fsf.public_ref, include="all")).json()
    assert "frequency_scale_factor_id" not in body["record"]["frequency_scale_factor"]


def test_fsf_detail_include_all_and_internal_ids_obeys_policy(
    client, db_session, allow_internal_ids
):
    fsf = make_frequency_scale_factor(db_session)
    body = client.get(
        _fsf_detail_url(fsf.public_ref, include="all,internal_ids")
    ).json()
    assert (
        body["record"]["frequency_scale_factor"]["frequency_scale_factor_id"]
        == fsf.id
    )


def test_fsf_detail_evidence_summary_with_statmech(client, db_session):
    fsf = make_frequency_scale_factor(db_session)
    species = make_species(db_session, smiles="C", inchi_key=next_inchi_key("FSF2"))
    entry = make_species_entry(db_session, species)
    make_statmech(
        db_session, species_entry=entry, frequency_scale_factor_id=fsf.id
    )
    body = client.get(_fsf_detail_url(fsf.public_ref)).json()
    ev = body["record"]["evidence_summary"]
    assert ev["statmech_usage_count"] == 1
    assert ev["has_statmech_usage"] is True


def test_fsf_detail_forbidden_payload_walk(client, db_session):
    fsf = make_frequency_scale_factor(db_session)
    body = client.get(_fsf_detail_url(fsf.public_ref, include="all")).json()
    violations = _walk_forbidden(body)
    assert violations == [], f"forbidden keys in FSF detail: {violations}"


# ===========================================================================
# Frequency scale factor — search
# ===========================================================================


def test_fsf_search_missing_filter_returns_422(client, db_session):
    resp = client.get(_fsf_search_url())
    assert resp.status_code == 422
    assert "missing_filter" in resp.text


def test_fsf_search_by_ref_finds_row(client, db_session):
    fsf = make_frequency_scale_factor(db_session)
    body = client.get(
        _fsf_search_url(frequency_scale_factor_ref=fsf.public_ref)
    ).json()
    assert body["pagination"]["total"] == 1
    assert (
        body["records"][0]["frequency_scale_factor"][
            "frequency_scale_factor_ref"
        ]
        == fsf.public_ref
    )


def test_fsf_search_by_scale_kind(client, db_session):
    fsf = make_frequency_scale_factor(
        db_session, scale_kind=FrequencyScaleKind.zpe
    )
    body = client.get(_fsf_search_url(scale_kind="zpe")).json()
    refs = {
        r["frequency_scale_factor"]["frequency_scale_factor_ref"]
        for r in body["records"]
    }
    assert fsf.public_ref in refs


def test_fsf_search_by_value_range(client, db_session):
    a = make_frequency_scale_factor(db_session, value=0.95)
    b = make_frequency_scale_factor(db_session, value=0.99)
    body = client.get(_fsf_search_url(value_min=0.97)).json()
    refs = {
        r["frequency_scale_factor"]["frequency_scale_factor_ref"]
        for r in body["records"]
    }
    assert b.public_ref in refs
    assert a.public_ref not in refs


def test_fsf_search_by_method(client, db_session):
    lot = make_lot(db_session, method="ccsd(t)", basis="cc-pvtz")
    fsf = make_frequency_scale_factor(db_session, lot=lot)
    body = client.get(_fsf_search_url(method="ccsd(t)")).json()
    refs = {
        r["frequency_scale_factor"]["frequency_scale_factor_ref"]
        for r in body["records"]
    }
    assert fsf.public_ref in refs


def test_fsf_search_by_software(client, db_session):
    sw = make_software(db_session, name="qchem")
    fsf = make_frequency_scale_factor(db_session, software=sw)
    body = client.get(_fsf_search_url(software="qchem")).json()
    refs = {
        r["frequency_scale_factor"]["frequency_scale_factor_ref"]
        for r in body["records"]
    }
    assert fsf.public_ref in refs


def test_fsf_search_by_literature_ref(client, db_session):
    lit = make_literature(db_session)
    fsf = make_frequency_scale_factor(db_session, source_literature=lit)
    body = client.get(_fsf_search_url(literature_ref=lit.public_ref)).json()
    refs = {
        r["frequency_scale_factor"]["frequency_scale_factor_ref"]
        for r in body["records"]
    }
    assert fsf.public_ref in refs


def test_fsf_search_by_used_by_statmech_true_and_false(client, db_session):
    fsf_used = make_frequency_scale_factor(db_session)
    fsf_unused = make_frequency_scale_factor(db_session)
    species = make_species(db_session, smiles="C", inchi_key=next_inchi_key("FSFS"))
    entry = make_species_entry(db_session, species)
    make_statmech(
        db_session,
        species_entry=entry,
        frequency_scale_factor_id=fsf_used.id,
    )
    body_true = client.get(
        _fsf_search_url(used_by_statmech="true")
    ).json()
    refs_true = {
        r["frequency_scale_factor"]["frequency_scale_factor_ref"]
        for r in body_true["records"]
    }
    assert fsf_used.public_ref in refs_true
    assert fsf_unused.public_ref not in refs_true

    body_false = client.get(
        _fsf_search_url(used_by_statmech="false")
    ).json()
    refs_false = {
        r["frequency_scale_factor"]["frequency_scale_factor_ref"]
        for r in body_false["records"]
    }
    assert fsf_unused.public_ref in refs_false
    assert fsf_used.public_ref not in refs_false


def test_fsf_search_get_post_parity(client, db_session):
    fsf = make_frequency_scale_factor(db_session)
    get_body = client.get(
        _fsf_search_url(frequency_scale_factor_ref=fsf.public_ref)
    ).json()
    post_body = client.post(
        _fsf_search_url(),
        json={"frequency_scale_factor_ref": fsf.public_ref},
    ).json()
    assert get_body["pagination"]["total"] == post_body["pagination"]["total"]
    assert (
        get_body["records"][0]["frequency_scale_factor"][
            "frequency_scale_factor_ref"
        ]
        == post_body["records"][0]["frequency_scale_factor"][
            "frequency_scale_factor_ref"
        ]
    )


def test_fsf_search_client_sort_rejected(client, db_session):
    fsf = make_frequency_scale_factor(db_session)
    resp = client.get(
        _fsf_search_url(
            frequency_scale_factor_ref=fsf.public_ref, sort="value:asc"
        )
    )
    assert resp.status_code == 422
    assert "client_sort_not_supported" in resp.text


def test_fsf_search_pagination_envelope(client, db_session):
    fsf = make_frequency_scale_factor(db_session)
    body = client.get(
        _fsf_search_url(frequency_scale_factor_ref=fsf.public_ref)
    ).json()
    pag = body["pagination"]
    for key in ("offset", "limit", "returned", "total"):
        assert key in pag


def test_fsf_search_record_shape_matches_detail(client, db_session):
    fsf = make_frequency_scale_factor(db_session)
    detail = client.get(_fsf_detail_url(fsf.public_ref)).json()["record"]
    search = client.get(
        _fsf_search_url(frequency_scale_factor_ref=fsf.public_ref)
    ).json()["records"][0]
    assert set(detail.keys()) == set(search.keys())


def test_fsf_search_forbidden_payload_walk(client, db_session):
    fsf = make_frequency_scale_factor(db_session)
    body = client.get(
        _fsf_search_url(
            frequency_scale_factor_ref=fsf.public_ref, include="all"
        )
    ).json()
    violations = _walk_forbidden(body)
    assert violations == [], f"forbidden keys in FSF search: {violations}"


# ===========================================================================
# Energy correction scheme — detail
# ===========================================================================


def test_ecs_detail_by_ref_returns_record(client, db_session):
    ecs = make_energy_correction_scheme(db_session)
    resp = client.get(_ecs_detail_url(ecs.public_ref))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (
        body["record"]["energy_correction_scheme"][
            "energy_correction_scheme_ref"
        ]
        == ecs.public_ref
    )


def test_ecs_detail_by_integer_id_works(client, db_session, allow_internal_ids):
    ecs = make_energy_correction_scheme(db_session)
    resp = client.get(
        _ecs_detail_url(str(ecs.id), include="internal_ids")
    )
    assert resp.status_code == 200
    body = resp.json()
    assert (
        body["record"]["energy_correction_scheme"][
            "energy_correction_scheme_id"
        ]
        == ecs.id
    )


def test_ecs_detail_unknown_ref_returns_404(client, db_session):
    resp = client.get(_ecs_detail_url("ecs_doesnotexist00000"))
    assert resp.status_code == 404
    assert "energy_correction_scheme not found" in resp.text


def test_ecs_detail_wrong_prefix_returns_422(client, db_session):
    resp = client.get(_ecs_detail_url("fsf_abcdef0123456789"))
    assert resp.status_code == 422
    assert "handle_type_mismatch" in resp.text


def test_ecs_detail_malformed_handle_returns_422(client, db_session):
    resp = client.get(_ecs_detail_url("not-a-handle"))
    assert resp.status_code == 422
    assert "invalid_handle" in resp.text


def test_ecs_detail_default_response_shape(client, db_session):
    ecs = make_energy_correction_scheme(db_session)
    body = client.get(_ecs_detail_url(ecs.public_ref)).json()
    record = body["record"]
    for key in (
        "energy_correction_scheme",
        "evidence_summary",
        "available_sections",
    ):
        assert key in record
    assert record["corrections"] is None
    assert record["used_by"] is None


def test_ecs_detail_include_corrections_all_kinds(client, db_session):
    ecs = make_energy_correction_scheme(
        db_session, kind=EnergyCorrectionSchemeKind.bac_melius
    )
    attach_ecs_atom_param(db_session, scheme=ecs, element="C", value=-1.5)
    attach_ecs_bond_param(db_session, scheme=ecs, bond_key="C-H", value=-0.1)
    attach_ecs_component_param(
        db_session,
        scheme=ecs,
        component_kind=MeliusBacComponentKind.mol_corr,
        key="alpha",
        value=0.5,
    )
    body = client.get(
        _ecs_detail_url(ecs.public_ref, include="corrections")
    ).json()
    terms = body["record"]["corrections"]
    assert terms is not None
    kinds = {t["correction_kind"] for t in terms}
    assert kinds == {"atom", "bond", "component"}


def test_ecs_detail_include_used_by_via_applied(client, db_session):
    ecs = make_energy_correction_scheme(db_session)
    species = make_species(db_session, smiles="O", inchi_key=next_inchi_key("ECS1"))
    entry = make_species_entry(db_session, species)
    make_applied_energy_correction(
        db_session,
        target_species_entry=entry,
        scheme=ecs,
        application_role=EnergyCorrectionApplicationRole.bac_total,
    )
    body = client.get(_ecs_detail_url(ecs.public_ref, include="used_by")).json()
    used = body["record"]["used_by"]
    assert used is not None and len(used) == 1
    assert used[0]["record_type"] == "species_entry"
    assert used[0]["record_ref"] == entry.public_ref


def test_ecs_detail_include_literature(client, db_session):
    lit = make_literature(db_session)
    ecs = make_energy_correction_scheme(db_session, source_literature=lit)
    body = client.get(
        _ecs_detail_url(ecs.public_ref, include="literature")
    ).json()
    assert body["record"]["literature"]["literature_ref"] == lit.public_ref


def test_ecs_detail_include_all_excludes_internal_ids(client, db_session):
    ecs = make_energy_correction_scheme(db_session)
    body = client.get(_ecs_detail_url(ecs.public_ref, include="all")).json()
    assert "internal_ids" not in body["request"]["include"]
    assert (
        "energy_correction_scheme_id"
        not in body["record"]["energy_correction_scheme"]
    )


def test_ecs_detail_include_all_and_internal_ids_obeys_policy(
    client, db_session, allow_internal_ids
):
    ecs = make_energy_correction_scheme(db_session)
    body = client.get(
        _ecs_detail_url(ecs.public_ref, include="all,internal_ids")
    ).json()
    assert (
        body["record"]["energy_correction_scheme"][
            "energy_correction_scheme_id"
        ]
        == ecs.id
    )


def test_ecs_detail_forbidden_payload_walk(client, db_session):
    ecs = make_energy_correction_scheme(db_session)
    attach_ecs_bond_param(db_session, scheme=ecs, bond_key="O-H", value=-0.05)
    body = client.get(_ecs_detail_url(ecs.public_ref, include="all")).json()
    violations = _walk_forbidden(body)
    assert violations == [], f"forbidden keys in ECS detail: {violations}"


# ===========================================================================
# Energy correction scheme — search
# ===========================================================================


def test_ecs_search_missing_filter_returns_422(client, db_session):
    resp = client.get(_ecs_search_url())
    assert resp.status_code == 422
    assert "missing_filter" in resp.text


def test_ecs_search_by_ref(client, db_session):
    ecs = make_energy_correction_scheme(db_session)
    body = client.get(
        _ecs_search_url(energy_correction_scheme_ref=ecs.public_ref)
    ).json()
    assert body["pagination"]["total"] == 1


def test_ecs_search_by_name(client, db_session):
    ecs = make_energy_correction_scheme(db_session, name="unique_scheme_xyz")
    body = client.get(_ecs_search_url(name="unique_scheme_xyz")).json()
    refs = {
        r["energy_correction_scheme"]["energy_correction_scheme_ref"]
        for r in body["records"]
    }
    assert ecs.public_ref in refs


def test_ecs_search_by_version(client, db_session):
    ecs = make_energy_correction_scheme(db_session, version="v2")
    body = client.get(_ecs_search_url(version="v2")).json()
    refs = {
        r["energy_correction_scheme"]["energy_correction_scheme_ref"]
        for r in body["records"]
    }
    assert ecs.public_ref in refs


def test_ecs_search_by_scheme_kind(client, db_session):
    ecs = make_energy_correction_scheme(
        db_session, kind=EnergyCorrectionSchemeKind.bac_melius
    )
    body = client.get(_ecs_search_url(scheme_kind="bac_melius")).json()
    refs = {
        r["energy_correction_scheme"]["energy_correction_scheme_ref"]
        for r in body["records"]
    }
    assert ecs.public_ref in refs


def test_ecs_search_by_method(client, db_session):
    lot = make_lot(db_session, method="m06-2x", basis="cc-pvdz")
    ecs = make_energy_correction_scheme(db_session, lot=lot)
    body = client.get(_ecs_search_url(method="m06-2x")).json()
    refs = {
        r["energy_correction_scheme"]["energy_correction_scheme_ref"]
        for r in body["records"]
    }
    assert ecs.public_ref in refs


def test_ecs_search_by_literature_ref(client, db_session):
    lit = make_literature(db_session)
    ecs = make_energy_correction_scheme(db_session, source_literature=lit)
    body = client.get(_ecs_search_url(literature_ref=lit.public_ref)).json()
    refs = {
        r["energy_correction_scheme"]["energy_correction_scheme_ref"]
        for r in body["records"]
    }
    assert ecs.public_ref in refs


def test_ecs_search_has_corrections_true_and_false(client, db_session):
    ecs_with = make_energy_correction_scheme(db_session, name="with_terms")
    attach_ecs_atom_param(db_session, scheme=ecs_with, element="N", value=-1.0)
    ecs_without = make_energy_correction_scheme(
        db_session, name="without_terms"
    )
    body_true = client.get(_ecs_search_url(has_corrections="true")).json()
    refs_true = {
        r["energy_correction_scheme"]["energy_correction_scheme_ref"]
        for r in body_true["records"]
    }
    assert ecs_with.public_ref in refs_true
    assert ecs_without.public_ref not in refs_true

    body_false = client.get(_ecs_search_url(has_corrections="false")).json()
    refs_false = {
        r["energy_correction_scheme"]["energy_correction_scheme_ref"]
        for r in body_false["records"]
    }
    assert ecs_without.public_ref in refs_false
    assert ecs_with.public_ref not in refs_false


def test_ecs_search_get_post_parity(client, db_session):
    ecs = make_energy_correction_scheme(db_session)
    get_body = client.get(
        _ecs_search_url(energy_correction_scheme_ref=ecs.public_ref)
    ).json()
    post_body = client.post(
        _ecs_search_url(),
        json={"energy_correction_scheme_ref": ecs.public_ref},
    ).json()
    assert get_body["pagination"]["total"] == post_body["pagination"]["total"]


def test_ecs_search_client_sort_rejected(client, db_session):
    ecs = make_energy_correction_scheme(db_session)
    resp = client.get(
        _ecs_search_url(
            energy_correction_scheme_ref=ecs.public_ref, sort="name:asc"
        )
    )
    assert resp.status_code == 422
    assert "client_sort_not_supported" in resp.text


def test_ecs_search_pagination_envelope(client, db_session):
    ecs = make_energy_correction_scheme(db_session)
    body = client.get(
        _ecs_search_url(energy_correction_scheme_ref=ecs.public_ref)
    ).json()
    pag = body["pagination"]
    for key in ("offset", "limit", "returned", "total"):
        assert key in pag


def test_ecs_search_record_shape_matches_detail(client, db_session):
    ecs = make_energy_correction_scheme(db_session)
    detail = client.get(_ecs_detail_url(ecs.public_ref)).json()["record"]
    search = client.get(
        _ecs_search_url(energy_correction_scheme_ref=ecs.public_ref)
    ).json()["records"][0]
    assert set(detail.keys()) == set(search.keys())


def test_ecs_search_forbidden_payload_walk(client, db_session):
    ecs = make_energy_correction_scheme(db_session)
    body = client.get(
        _ecs_search_url(
            energy_correction_scheme_ref=ecs.public_ref, include="all"
        )
    ).json()
    violations = _walk_forbidden(body)
    assert violations == [], f"forbidden keys in ECS search: {violations}"
