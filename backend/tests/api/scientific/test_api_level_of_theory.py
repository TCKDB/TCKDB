"""API tests for the scientific level-of-theory detail + search endpoints.

Mirrors ``test_api_scientific_corrections.py``'s coverage matrix (detail
by ref/id, 404/422 handle errors, default shape, internal-id stripping,
include gating, search filters, missing-filter 422, sort rejection,
get/post parity, pagination envelope, record-shape parity, forbidden
payload walk) plus two invariants specific to this surface:

- A level of theory with zero attributing calculations must not appear
  in search (usage-derived, not a registry dump).
- ``include=correction_schemes`` joins on ``level_of_theory_id`` (the
  FK), never on method/basis text -- two LOTs can share a method/basis
  pair and differ only in ``spin_treatment`` (DR-0034).
"""

from __future__ import annotations

from app.db.models.common import (
    EnergyCorrectionSchemeKind,
    FrequencyScaleKind,
    SpinTreatment,
)
from tests.services.scientific_read._factories import (
    make_calculation,
    make_energy_correction_scheme,
    make_frequency_scale_factor,
    make_lot,
    make_software_release,
    make_species,
    make_species_entry,
    make_workflow_tool_release,
    next_inchi_key,
)

# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------


def _detail_url(handle: str, **params) -> str:
    base = f"/api/v1/scientific/level-of-theories/{handle}"
    if not params:
        return base
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{base}?{qs}"


def _search_url(**params) -> str:
    base = "/api/v1/scientific/level-of-theories/search"
    if not params:
        return base
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{base}?{qs}"


def _with_usage(session, lot, *, method_suffix: str = "USE"):
    """Attach one calculation to *lot* so it clears the usage-derived gate."""
    species = make_species(
        session, smiles="C", inchi_key=next_inchi_key(f"LOT{method_suffix}")
    )
    entry = make_species_entry(session, species)
    return make_calculation(session, species_entry_id=entry.id, lot_id=lot.id)


# ---------------------------------------------------------------------------
# Forbidden-payload recursive walker (mirrors test_api_scientific_corrections.py)
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
# Detail
# ===========================================================================


def test_lot_detail_by_ref_returns_record(client, db_session):
    lot = make_lot(db_session, method="b3lyp", basis="def2tzvp")
    _with_usage(db_session, lot, method_suffix="A")
    resp = client.get(_detail_url(lot.public_ref))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["record"]["level_of_theory"]["level_of_theory_ref"] == lot.public_ref


def test_lot_detail_by_integer_id_works(client, db_session, allow_internal_ids):
    lot = make_lot(db_session, method="b3lyp", basis="def2tzvp")
    resp = client.get(_detail_url(str(lot.id), include="internal_ids"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["record"]["level_of_theory"]["level_of_theory_id"] == lot.id


def test_lot_detail_unknown_ref_returns_404(client, db_session):
    resp = client.get(_detail_url("lot_doesnotexist000000"))
    assert resp.status_code == 404
    assert "level_of_theory not found" in resp.text


def test_lot_detail_wrong_prefix_returns_422(client, db_session):
    resp = client.get(_detail_url("ecs_abcdef0123456789"))
    assert resp.status_code == 422
    assert "handle_type_mismatch" in resp.text


def test_lot_detail_malformed_handle_returns_422(client, db_session):
    resp = client.get(_detail_url("not-a-handle"))
    assert resp.status_code == 422
    assert "invalid_handle" in resp.text


def test_lot_detail_default_response_shape(client, db_session):
    lot = make_lot(db_session, method="b3lyp", basis="def2tzvp")
    body = client.get(_detail_url(lot.public_ref)).json()
    record = body["record"]
    for key in ("level_of_theory", "evidence_summary", "available_sections"):
        assert key in record
    assert "correction_schemes" not in record
    assert "frequency_scale_factors" not in record
    assert "used_by" not in record
    # LOT is non-reviewable; the summary is always empty.
    assert body["review_summary"]["total"] == 0


def test_lot_detail_default_strips_internal_ids(client, db_session):
    lot = make_lot(db_session, method="b3lyp", basis="def2tzvp")
    body = client.get(_detail_url(lot.public_ref)).json()
    core = body["record"]["level_of_theory"]
    assert "level_of_theory_id" not in core
    assert "level_of_theory_ref" in core


def test_lot_detail_core_block_reports_spin_treatment(client, db_session):
    """Mutation target (c): drop ``spin_treatment`` from the summary."""
    lot = make_lot(
        db_session,
        method="ub3lyp",
        basis="def2tzvp",
        spin_treatment=SpinTreatment.unrestricted,
    )
    body = client.get(_detail_url(lot.public_ref)).json()
    assert (
        body["record"]["level_of_theory"]["spin_treatment"]
        == SpinTreatment.unrestricted.value
    )


def test_lot_detail_core_block_reports_hash_and_identity_fields(client, db_session):
    lot = make_lot(db_session, method="wb97xd", basis="def2tzvp")
    body = client.get(_detail_url(lot.public_ref)).json()
    core = body["record"]["level_of_theory"]
    assert core["lot_hash"] == lot.lot_hash
    assert core["method"] == "wb97xd"
    assert core["basis"] == "def2tzvp"


def test_lot_detail_evidence_summary_calculation_usage_count(client, db_session):
    lot = make_lot(db_session, method="b3lyp", basis="def2tzvp")
    _with_usage(db_session, lot, method_suffix="B")
    _with_usage(db_session, lot, method_suffix="C")
    body = client.get(_detail_url(lot.public_ref)).json()
    ev = body["record"]["evidence_summary"]
    assert ev["calculation_usage_count"] == 2


def test_lot_detail_evidence_summary_no_usage_is_zero(client, db_session):
    lot = make_lot(db_session, method="ccsd(t)", basis="cc-pvtz")
    body = client.get(_detail_url(lot.public_ref)).json()
    ev = body["record"]["evidence_summary"]
    assert ev["calculation_usage_count"] == 0


def test_lot_detail_include_all_expands_to_public_tokens(client, db_session):
    lot = make_lot(db_session, method="b3lyp", basis="def2tzvp")
    body = client.get(_detail_url(lot.public_ref, include="all")).json()
    echo = body["request"]["include"]
    assert "internal_ids" not in echo
    assert "correction_schemes" in echo
    assert "frequency_scale_factors" in echo
    assert "used_by" in echo
    # ``software`` is not yet a legal token (methods-surface plan §5.3,
    # not yet built) -- it must not appear even under ``all``.
    assert "software" not in echo


def test_lot_detail_include_software_is_rejected(client, db_session):
    """Asking for the not-yet-built LOT-scoped software breakdown 422s
    rather than silently answering with nothing."""
    lot = make_lot(db_session, method="b3lyp", basis="def2tzvp")
    resp = client.get(_detail_url(lot.public_ref, include="software"))
    assert resp.status_code == 422
    assert "unknown_include_token" in resp.text


def test_lot_detail_forbidden_payload_walk(client, db_session):
    lot = make_lot(db_session, method="b3lyp", basis="def2tzvp")
    body = client.get(_detail_url(lot.public_ref, include="all")).json()
    violations = _walk_forbidden(body)
    assert violations == [], f"forbidden keys in LOT detail: {violations}"


# ===========================================================================
# include=correction_schemes -- the FK-not-text join trap
# ===========================================================================


def test_lot_detail_include_correction_schemes_join_on_fk_not_text(
    client, db_session
):
    """Mutation target (a): join correction schemes on method/basis text.

    Two LOTs share the exact same method/basis pair and differ only in
    ``spin_treatment`` (DR-0034's own example -- UB3LYP vs ROB3LYP). Only
    one carries an energy-correction scheme. A join on
    ``level_of_theory_id`` (correct) must return the scheme for its own
    LOT and nothing for the sibling; a join on method/basis text (the
    mutation) would return the scheme for both.
    """
    lot_with_scheme = make_lot(
        db_session,
        method="b3lyp",
        basis="def2tzvp",
        spin_treatment=SpinTreatment.unrestricted,
    )
    lot_without_scheme = make_lot(
        db_session,
        method="b3lyp",
        basis="def2tzvp",
        spin_treatment=SpinTreatment.restricted_open,
    )
    assert lot_with_scheme.id != lot_without_scheme.id
    assert lot_with_scheme.method == lot_without_scheme.method
    assert lot_with_scheme.basis == lot_without_scheme.basis

    scheme = make_energy_correction_scheme(
        db_session,
        name="atom_energy_test",
        kind=EnergyCorrectionSchemeKind.atom_energy,
        lot=lot_with_scheme,
    )

    body_with = client.get(
        _detail_url(lot_with_scheme.public_ref, include="correction_schemes")
    ).json()
    refs_with = {
        r["energy_correction_scheme"]["energy_correction_scheme_ref"]
        for r in body_with["record"]["correction_schemes"]
    }
    assert refs_with == {scheme.public_ref}

    body_without = client.get(
        _detail_url(lot_without_scheme.public_ref, include="correction_schemes")
    ).json()
    assert body_without["record"]["correction_schemes"] == []


def test_lot_detail_include_correction_schemes_carries_parameter_table(
    client, db_session
):
    lot = make_lot(db_session, method="b3lyp", basis="def2tzvp")
    scheme = make_energy_correction_scheme(
        db_session,
        name="atom_energy_test2",
        kind=EnergyCorrectionSchemeKind.atom_energy,
        lot=lot,
    )
    from tests.services.scientific_read._factories import attach_ecs_atom_param

    attach_ecs_atom_param(db_session, scheme=scheme, element="H", value=-0.5)
    attach_ecs_atom_param(db_session, scheme=scheme, element="C", value=-37.8)

    body = client.get(
        _detail_url(lot.public_ref, include="correction_schemes")
    ).json()
    schemes = body["record"]["correction_schemes"]
    assert len(schemes) == 1
    corrections = schemes[0]["corrections"]
    assert corrections is not None
    assert len(corrections) == 2


def test_lot_detail_evidence_has_correction_schemes(client, db_session):
    lot = make_lot(db_session, method="b3lyp", basis="def2tzvp")
    make_energy_correction_scheme(
        db_session,
        name="atom_energy_test3",
        kind=EnergyCorrectionSchemeKind.atom_energy,
        lot=lot,
    )
    body = client.get(_detail_url(lot.public_ref)).json()
    assert body["record"]["evidence_summary"]["has_correction_schemes"] is True
    assert body["record"]["available_sections"]["has_correction_schemes"] is True


# ===========================================================================
# include=frequency_scale_factors -- dedup without collapsing provenance
# ===========================================================================


def test_lot_detail_include_frequency_scale_factors_dedups_by_value(
    client, db_session
):
    lot = make_lot(db_session, method="b3lyp", basis="def2tzvp")
    wtr_a = make_workflow_tool_release(db_session, name="arc", version="1.0.0")
    wtr_b = make_workflow_tool_release(db_session, name="arc", version="1.1.0")
    make_frequency_scale_factor(
        db_session,
        lot=lot,
        scale_kind=FrequencyScaleKind.fundamental,
        value=0.999,
        workflow_tool_release=wtr_a,
    )
    make_frequency_scale_factor(
        db_session,
        lot=lot,
        scale_kind=FrequencyScaleKind.fundamental,
        value=0.999,
        workflow_tool_release=wtr_b,
    )

    body = client.get(
        _detail_url(lot.public_ref, include="frequency_scale_factors")
    ).json()
    groups = body["record"]["frequency_scale_factors"]
    assert len(groups) == 1
    group = groups[0]
    assert group["value"] == 0.999
    assert group["frequency_scale_factor_count"] == 2
    refs = {f["frequency_scale_factor_ref"] for f in group["frequency_scale_factors"]}
    assert len(refs) == 2
    versions = {
        f["workflow_tool_release"]["version"]
        for f in group["frequency_scale_factors"]
    }
    assert versions == {"1.0.0", "1.1.0"}


def test_lot_detail_include_frequency_scale_factors_distinct_values_separate_groups(
    client, db_session
):
    lot = make_lot(db_session, method="wb97xd", basis="def2tzvp")
    make_frequency_scale_factor(
        db_session, lot=lot, scale_kind=FrequencyScaleKind.fundamental, value=0.98
    )
    make_frequency_scale_factor(
        db_session, lot=lot, scale_kind=FrequencyScaleKind.zpe, value=0.96
    )
    body = client.get(
        _detail_url(lot.public_ref, include="frequency_scale_factors")
    ).json()
    groups = body["record"]["frequency_scale_factors"]
    assert len(groups) == 2


def test_lot_detail_evidence_has_frequency_scale_factors(client, db_session):
    lot = make_lot(db_session, method="b3lyp", basis="def2tzvp")
    make_frequency_scale_factor(db_session, lot=lot, value=0.99)
    body = client.get(_detail_url(lot.public_ref)).json()
    assert body["record"]["evidence_summary"]["has_frequency_scale_factors"] is True


def test_lot_detail_no_frequency_scale_factor_deposited(client, db_session):
    lot = make_lot(db_session, method="mrci+davidson", basis="aug-cc-pv(t+d)z")
    body = client.get(_detail_url(lot.public_ref)).json()
    assert body["record"]["evidence_summary"]["has_frequency_scale_factors"] is False
    assert body["record"]["available_sections"]["has_frequency_scale_factors"] is False


# ===========================================================================
# include=used_by
# ===========================================================================


def test_lot_detail_include_used_by(client, db_session):
    lot = make_lot(db_session, method="b3lyp", basis="def2tzvp")
    calc = _with_usage(db_session, lot, method_suffix="D")
    body = client.get(_detail_url(lot.public_ref, include="used_by")).json()
    used = body["record"]["used_by"]
    assert used is not None and len(used) == 1
    assert used[0]["calculation_ref"] == calc.public_ref
    assert used[0]["endpoint"].endswith(calc.public_ref)
    assert used[0]["record_type"] == "species_entry"


def test_lot_detail_distinct_software_count(client, db_session):
    lot = make_lot(db_session, method="b3lyp", basis="def2tzvp")
    species = make_species(db_session, smiles="C", inchi_key=next_inchi_key("LOTSW"))
    entry = make_species_entry(db_session, species)
    gaussian = make_software_release(db_session, name="gaussian", version="16")
    orca = make_software_release(db_session, name="orca", version="5.0")
    make_calculation(
        db_session,
        species_entry_id=entry.id,
        lot_id=lot.id,
        software_release_id=gaussian.id,
    )
    make_calculation(
        db_session,
        species_entry_id=entry.id,
        lot_id=lot.id,
        software_release_id=orca.id,
    )
    body = client.get(_detail_url(lot.public_ref)).json()
    assert body["record"]["evidence_summary"]["distinct_software_count"] == 2


# ===========================================================================
# Search
# ===========================================================================


def test_lot_search_missing_filter_returns_422(client, db_session):
    resp = client.get(_search_url())
    assert resp.status_code == 422
    assert "missing_filter" in resp.text


def test_lot_search_by_ref_finds_row(client, db_session):
    lot = make_lot(db_session, method="b3lyp", basis="def2tzvp")
    _with_usage(db_session, lot, method_suffix="E")
    body = client.get(_search_url(level_of_theory_ref=lot.public_ref)).json()
    assert body["pagination"]["total"] == 1
    assert (
        body["records"][0]["level_of_theory"]["level_of_theory_ref"]
        == lot.public_ref
    )


def test_lot_search_by_method(client, db_session):
    lot = make_lot(db_session, method="unique-method-search", basis="def2tzvp")
    _with_usage(db_session, lot, method_suffix="F")
    body = client.get(_search_url(method="unique-method-search")).json()
    refs = {r["level_of_theory"]["level_of_theory_ref"] for r in body["records"]}
    assert lot.public_ref in refs


def test_lot_search_by_basis(client, db_session):
    lot = make_lot(db_session, method="ccsd(t)", basis="unique-basis-search")
    _with_usage(db_session, lot, method_suffix="G")
    body = client.get(_search_url(basis="unique-basis-search")).json()
    refs = {r["level_of_theory"]["level_of_theory_ref"] for r in body["records"]}
    assert lot.public_ref in refs


def test_lot_search_by_dispersion(client, db_session):
    lot = make_lot(db_session, method="wb97xd-disp", basis="def2tzvp")
    lot.dispersion = "d3bj"
    db_session.flush()
    _with_usage(db_session, lot, method_suffix="H")
    body = client.get(_search_url(dispersion="d3bj")).json()
    refs = {r["level_of_theory"]["level_of_theory_ref"] for r in body["records"]}
    assert lot.public_ref in refs


def test_lot_search_by_solvent(client, db_session):
    lot = make_lot(db_session, method="b3lyp-solv", basis="def2tzvp")
    lot.solvent = "water"
    db_session.flush()
    _with_usage(db_session, lot, method_suffix="I")
    body = client.get(_search_url(solvent="water")).json()
    refs = {r["level_of_theory"]["level_of_theory_ref"] for r in body["records"]}
    assert lot.public_ref in refs


def test_lot_search_by_spin_treatment(client, db_session):
    lot = make_lot(
        db_session,
        method="ub3lyp-search",
        basis="def2tzvp",
        spin_treatment=SpinTreatment.unrestricted,
    )
    _with_usage(db_session, lot, method_suffix="J")
    body = client.get(_search_url(spin_treatment="unrestricted")).json()
    refs = {r["level_of_theory"]["level_of_theory_ref"] for r in body["records"]}
    assert lot.public_ref in refs


def test_lot_search_has_correction_schemes_true_and_false(client, db_session):
    lot_with = make_lot(db_session, method="hascheme-yes", basis="def2tzvp")
    lot_without = make_lot(db_session, method="hascheme-no", basis="def2tzvp")
    _with_usage(db_session, lot_with, method_suffix="K")
    _with_usage(db_session, lot_without, method_suffix="L")
    make_energy_correction_scheme(
        db_session,
        name="hascheme_scheme",
        kind=EnergyCorrectionSchemeKind.atom_energy,
        lot=lot_with,
    )

    body_true = client.get(
        _search_url(method=lot_with.method, has_correction_schemes="true")
    ).json()
    refs_true = {r["level_of_theory"]["level_of_theory_ref"] for r in body_true["records"]}
    assert lot_with.public_ref in refs_true

    body_false = client.get(
        _search_url(method=lot_without.method, has_correction_schemes="false")
    ).json()
    refs_false = {
        r["level_of_theory"]["level_of_theory_ref"] for r in body_false["records"]
    }
    assert lot_without.public_ref in refs_false


def test_lot_search_has_frequency_scale_factors_true_and_false(client, db_session):
    lot_with = make_lot(db_session, method="hasfsf-yes", basis="def2tzvp")
    lot_without = make_lot(db_session, method="hasfsf-no", basis="def2tzvp")
    _with_usage(db_session, lot_with, method_suffix="M")
    _with_usage(db_session, lot_without, method_suffix="N")
    make_frequency_scale_factor(db_session, lot=lot_with, value=0.97)

    body_true = client.get(
        _search_url(method=lot_with.method, has_frequency_scale_factors="true")
    ).json()
    refs_true = {r["level_of_theory"]["level_of_theory_ref"] for r in body_true["records"]}
    assert lot_with.public_ref in refs_true

    body_false = client.get(
        _search_url(method=lot_without.method, has_frequency_scale_factors="false")
    ).json()
    refs_false = {
        r["level_of_theory"]["level_of_theory_ref"] for r in body_false["records"]
    }
    assert lot_without.public_ref in refs_false


def test_lot_search_excludes_zero_calculation_lot(client, db_session):
    """Mutation target (b): make the search join OUTER.

    A level of theory with no attributing calculation is seeded but never
    used by any calculation. It must not appear in search results even
    though it matches the filter exactly -- usage-derived, not a registry
    dump. An ``OUTER`` join from ``level_of_theory`` (the mutation) would
    let it leak back in.
    """
    unused_lot = make_lot(db_session, method="unused-method-zero-calc", basis="x")
    # Deliberately no make_calculation() call for unused_lot.
    body = client.get(_search_url(method="unused-method-zero-calc")).json()
    assert body["pagination"]["total"] == 0
    refs = {r["level_of_theory"]["level_of_theory_ref"] for r in body["records"]}
    assert unused_lot.public_ref not in refs


def test_lot_search_get_post_parity(client, db_session):
    lot = make_lot(db_session, method="b3lyp", basis="def2tzvp")
    _with_usage(db_session, lot, method_suffix="O")
    get_body = client.get(
        _search_url(level_of_theory_ref=lot.public_ref)
    ).json()
    post_body = client.post(
        _search_url(), json={"level_of_theory_ref": lot.public_ref}
    ).json()
    assert get_body["pagination"]["total"] == post_body["pagination"]["total"]
    assert (
        get_body["records"][0]["level_of_theory"]["level_of_theory_ref"]
        == post_body["records"][0]["level_of_theory"]["level_of_theory_ref"]
    )


def test_lot_search_client_sort_rejected(client, db_session):
    lot = make_lot(db_session, method="b3lyp", basis="def2tzvp")
    _with_usage(db_session, lot, method_suffix="P")
    resp = client.get(
        _search_url(level_of_theory_ref=lot.public_ref, sort="method:asc")
    )
    assert resp.status_code == 422
    assert "client_sort_not_supported" in resp.text


def test_lot_search_pagination_envelope(client, db_session):
    lot = make_lot(db_session, method="b3lyp", basis="def2tzvp")
    _with_usage(db_session, lot, method_suffix="Q")
    body = client.get(_search_url(level_of_theory_ref=lot.public_ref)).json()
    pag = body["pagination"]
    for key in ("offset", "limit", "returned", "total"):
        assert key in pag


def test_lot_search_record_shape_matches_detail(client, db_session):
    lot = make_lot(db_session, method="b3lyp", basis="def2tzvp")
    _with_usage(db_session, lot, method_suffix="R")
    detail = client.get(_detail_url(lot.public_ref)).json()["record"]
    search = client.get(
        _search_url(level_of_theory_ref=lot.public_ref)
    ).json()["records"][0]
    assert set(detail.keys()) == set(search.keys())


def test_lot_search_forbidden_payload_walk(client, db_session):
    lot = make_lot(db_session, method="b3lyp", basis="def2tzvp")
    _with_usage(db_session, lot, method_suffix="S")
    body = client.get(
        _search_url(level_of_theory_ref=lot.public_ref, include="all")
    ).json()
    violations = _walk_forbidden(body)
    assert violations == [], f"forbidden keys in LOT search: {violations}"
