"""API tests for GET /api/v1/scientific/level-of-theories/browse.

Companion to ``test_api_level_of_theory.py``'s search-surface tests.
Reuses that module's fixture helpers (``_with_usage``) directly rather
than duplicating them, since the two surfaces share the exact same
underlying candidate query and record shape (see
``app/services/scientific_read/level_of_theory_search.py::_run_lot_query``)
-- only "no filter required" is new.
"""

from __future__ import annotations

from app.db.models.common import (
    EnergyCorrectionSchemeKind,
    SpinTreatment,
)
from tests.api.scientific.test_api_level_of_theory import (
    _detail_url,
    _walk_forbidden,
    _with_usage,
)
from tests.services.scientific_read._factories import (
    make_energy_correction_scheme,
    make_frequency_scale_factor,
    make_lot,
)


def _browse_url(**params) -> str:
    base = "/api/v1/scientific/level-of-theories/browse"
    if not params:
        return base
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{base}?{qs}"


# ---------------------------------------------------------------------------
# The headline defect this route exists to fix
# ---------------------------------------------------------------------------


def test_get_with_no_query_params_returns_200_not_422(client, db_session):
    """The exact call that 422s on /search today must 200 here."""
    lot = make_lot(db_session, method="browse-headline", basis="def2tzvp")
    _with_usage(db_session, lot, method_suffix="BR-HEAD")

    search_resp = client.get("/api/v1/scientific/level-of-theories/search")
    assert search_resp.status_code == 422
    assert "missing_filter" in search_resp.text

    browse_resp = client.get(_browse_url())
    assert browse_resp.status_code == 200


def test_unfiltered_browse_returns_every_used_lot(client, db_session):
    lot_a = make_lot(db_session, method="browse-all-a", basis="def2tzvp")
    lot_b = make_lot(db_session, method="browse-all-b", basis="cc-pvtz")
    _with_usage(db_session, lot_a, method_suffix="BR-ALL-A")
    _with_usage(db_session, lot_b, method_suffix="BR-ALL-B")

    body = client.get(_browse_url()).json()
    refs = {r["level_of_theory"]["level_of_theory_ref"] for r in body["records"]}
    assert lot_a.public_ref in refs
    assert lot_b.public_ref in refs


def test_browse_returns_the_same_record_shape_as_search(client, db_session):
    lot = make_lot(db_session, method="browse-shape", basis="def2tzvp")
    _with_usage(db_session, lot, method_suffix="BR-SHAPE")

    search_body = client.get(
        f"/api/v1/scientific/level-of-theories/search?level_of_theory_ref={lot.public_ref}"
    ).json()
    browse_body = client.get(_browse_url()).json()

    search_record = search_body["records"][0]
    browse_record = next(
        r
        for r in browse_body["records"]
        if r["level_of_theory"]["level_of_theory_ref"] == lot.public_ref
    )
    assert set(search_record.keys()) == set(browse_record.keys())
    assert search_record == browse_record


# ---------------------------------------------------------------------------
# Filters -- every one search accepts, level_of_theory_ref included
# ---------------------------------------------------------------------------


def test_browse_by_level_of_theory_ref_narrows(client, db_session):
    lot = make_lot(db_session, method="browse-ref", basis="def2tzvp")
    other = make_lot(db_session, method="browse-ref-other", basis="def2tzvp")
    _with_usage(db_session, lot, method_suffix="BR-REF-A")
    _with_usage(db_session, other, method_suffix="BR-REF-B")

    body = client.get(_browse_url(level_of_theory_ref=lot.public_ref)).json()
    refs = {r["level_of_theory"]["level_of_theory_ref"] for r in body["records"]}
    assert refs == {lot.public_ref}


def test_browse_by_method(client, db_session):
    lot = make_lot(db_session, method="unique-method-browse", basis="def2tzvp")
    _with_usage(db_session, lot, method_suffix="BR-METHOD")
    body = client.get(_browse_url(method="unique-method-browse")).json()
    refs = {r["level_of_theory"]["level_of_theory_ref"] for r in body["records"]}
    assert lot.public_ref in refs


def test_browse_by_basis(client, db_session):
    lot = make_lot(db_session, method="ccsd(t)", basis="unique-basis-browse")
    _with_usage(db_session, lot, method_suffix="BR-BASIS")
    body = client.get(_browse_url(basis="unique-basis-browse")).json()
    refs = {r["level_of_theory"]["level_of_theory_ref"] for r in body["records"]}
    assert lot.public_ref in refs


def test_browse_by_dispersion(client, db_session):
    lot = make_lot(db_session, method="wb97xd-disp-browse", basis="def2tzvp")
    lot.dispersion = "d3bj"
    db_session.flush()
    _with_usage(db_session, lot, method_suffix="BR-DISP")
    body = client.get(_browse_url(dispersion="d3bj")).json()
    refs = {r["level_of_theory"]["level_of_theory_ref"] for r in body["records"]}
    assert lot.public_ref in refs


def test_browse_by_solvent(client, db_session):
    lot = make_lot(db_session, method="b3lyp-solv-browse", basis="def2tzvp")
    lot.solvent = "water"
    db_session.flush()
    _with_usage(db_session, lot, method_suffix="BR-SOLV")
    body = client.get(_browse_url(solvent="water")).json()
    refs = {r["level_of_theory"]["level_of_theory_ref"] for r in body["records"]}
    assert lot.public_ref in refs


def test_browse_by_spin_treatment(client, db_session):
    lot = make_lot(
        db_session,
        method="ub3lyp-browse",
        basis="def2tzvp",
        spin_treatment=SpinTreatment.unrestricted,
    )
    _with_usage(db_session, lot, method_suffix="BR-SPIN")
    body = client.get(_browse_url(spin_treatment="unrestricted")).json()
    refs = {r["level_of_theory"]["level_of_theory_ref"] for r in body["records"]}
    assert lot.public_ref in refs


def test_browse_has_correction_schemes_true_and_false(client, db_session):
    lot_with = make_lot(db_session, method="br-hascheme-yes", basis="def2tzvp")
    lot_without = make_lot(db_session, method="br-hascheme-no", basis="def2tzvp")
    _with_usage(db_session, lot_with, method_suffix="BR-SCHEME-A")
    _with_usage(db_session, lot_without, method_suffix="BR-SCHEME-B")
    make_energy_correction_scheme(
        db_session,
        name="br_hascheme_scheme",
        kind=EnergyCorrectionSchemeKind.atom_energy,
        lot=lot_with,
    )

    body_true = client.get(
        _browse_url(method=lot_with.method, has_correction_schemes="true")
    ).json()
    refs_true = {
        r["level_of_theory"]["level_of_theory_ref"] for r in body_true["records"]
    }
    assert lot_with.public_ref in refs_true

    body_false = client.get(
        _browse_url(method=lot_without.method, has_correction_schemes="false")
    ).json()
    refs_false = {
        r["level_of_theory"]["level_of_theory_ref"] for r in body_false["records"]
    }
    assert lot_without.public_ref in refs_false


def test_browse_has_frequency_scale_factors_true_and_false(client, db_session):
    lot_with = make_lot(db_session, method="br-hasfsf-yes", basis="def2tzvp")
    lot_without = make_lot(db_session, method="br-hasfsf-no", basis="def2tzvp")
    _with_usage(db_session, lot_with, method_suffix="BR-FSF-A")
    _with_usage(db_session, lot_without, method_suffix="BR-FSF-B")
    make_frequency_scale_factor(db_session, lot=lot_with, value=0.97)

    body_true = client.get(
        _browse_url(method=lot_with.method, has_frequency_scale_factors="true")
    ).json()
    refs_true = {
        r["level_of_theory"]["level_of_theory_ref"] for r in body_true["records"]
    }
    assert lot_with.public_ref in refs_true

    body_false = client.get(
        _browse_url(method=lot_without.method, has_frequency_scale_factors="false")
    ).json()
    refs_false = {
        r["level_of_theory"]["level_of_theory_ref"] for r in body_false["records"]
    }
    assert lot_without.public_ref in refs_false


def test_browse_excludes_zero_calculation_lot(client, db_session):
    """Mutation target (b): let a zero-calculation LOT leak into browse.

    A level of theory with no attributing calculation is seeded but
    never used by any calculation. It must not appear in browse either
    -- usage-derived, not a registry dump, same discipline as search
    (see ``test_lot_search_excludes_zero_calculation_lot``).
    """
    unused_lot = make_lot(
        db_session, method="browse-unused-method-zero-calc", basis="x"
    )
    # Deliberately no make_calculation() call for unused_lot.
    body = client.get(_browse_url(method="browse-unused-method-zero-calc")).json()
    assert body["pagination"]["total"] == 0
    refs = {r["level_of_theory"]["level_of_theory_ref"] for r in body["records"]}
    assert unused_lot.public_ref not in refs


# ---------------------------------------------------------------------------
# Sort / pagination / shape
# ---------------------------------------------------------------------------


def test_browse_client_sort_rejected(client, db_session):
    lot = make_lot(db_session, method="browse-sort", basis="def2tzvp")
    _with_usage(db_session, lot, method_suffix="BR-SORT")
    resp = client.get(_browse_url(sort="method:asc"))
    assert resp.status_code == 422
    assert "client_sort_not_supported" in resp.text


def test_browse_pagination_envelope(client, db_session):
    lot = make_lot(db_session, method="browse-pag", basis="def2tzvp")
    _with_usage(db_session, lot, method_suffix="BR-PAG")
    body = client.get(_browse_url()).json()
    pag = body["pagination"]
    for key in ("offset", "limit", "returned", "total"):
        assert key in pag


def test_browse_record_shape_matches_detail(client, db_session):
    lot = make_lot(db_session, method="browse-detail-shape", basis="def2tzvp")
    _with_usage(db_session, lot, method_suffix="BR-DETAIL")
    detail = client.get(_detail_url(lot.public_ref)).json()["record"]
    browse = client.get(
        _browse_url(level_of_theory_ref=lot.public_ref)
    ).json()["records"][0]
    assert set(detail.keys()) == set(browse.keys())


def test_browse_forbidden_payload_walk(client, db_session):
    lot = make_lot(db_session, method="browse-forbidden", basis="def2tzvp")
    _with_usage(db_session, lot, method_suffix="BR-FORBID")
    body = client.get(_browse_url(method=lot.method, include="all")).json()
    violations = _walk_forbidden(body)
    assert violations == [], f"forbidden keys in LOT browse: {violations}"


def test_browse_include_used_by(client, db_session):
    lot = make_lot(db_session, method="browse-used-by", basis="def2tzvp")
    calc = _with_usage(db_session, lot, method_suffix="BR-USEDBY")
    body = client.get(
        _browse_url(level_of_theory_ref=lot.public_ref, include="used_by")
    ).json()
    record = body["records"][0]
    assert record["used_by"] is not None
    refs = {row["calculation_ref"] for row in record["used_by"]}
    assert calc.public_ref in refs
