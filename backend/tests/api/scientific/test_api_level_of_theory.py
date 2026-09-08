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
    # ``software`` (methods-surface plan §5.3) is now a legal public token
    # and expands under ``all`` like every other non-internal section.
    assert "software" in echo


def test_lot_detail_include_software_unknown_token_still_rejects_typos(
    client, db_session
):
    """``software`` is legal now; a near-miss token must still 422."""
    lot = make_lot(db_session, method="b3lyp", basis="def2tzvp")
    resp = client.get(_detail_url(lot.public_ref, include="softwares"))
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
# include=software -- LOT-scoped software/workflow-tool usage aggregation
# (methods-surface plan §5.3)
# ===========================================================================


def test_lot_detail_include_software_returns_package_and_calculation_count(
    client, db_session
):
    lot = make_lot(db_session, method="b3lyp-sw", basis="def2tzvp")
    species = make_species(db_session, smiles="C", inchi_key=next_inchi_key("LOTSWA"))
    entry = make_species_entry(db_session, species)
    gaussian = make_software_release(db_session, name="gaussian16-a", version="16")
    for _ in range(3):
        make_calculation(
            db_session,
            species_entry_id=entry.id,
            lot_id=lot.id,
            software_release_id=gaussian.id,
        )

    body = client.get(_detail_url(lot.public_ref, include="software")).json()
    software = body["record"]["software"]["software"]
    assert software == [
        {"software": "gaussian16-a", "version": "16", "calculation_count": 3}
    ]


def test_lot_detail_include_software_excludes_zero_calculation_package(
    client, db_session
):
    """Mutation target (a): a software package with zero calculations at
    *this* level of theory must be absent from the list, not present with
    a zero count.

    ``orca`` is registered (a ``SoftwareRelease`` row exists) but never
    cited by any calculation at ``lot`` -- only ``gaussian`` is. If the
    join were ever loosened from ``INNER`` to ``OUTER`` (mirroring the
    2026-08 vocabulary-dropdown defect this repo already fixed once,
    where 110 of 125 families could never return a result), ``orca``
    would leak back in with ``calculation_count: 0``.
    """
    lot = make_lot(db_session, method="b3lyp-sw-zero", basis="def2tzvp")
    species = make_species(db_session, smiles="C", inchi_key=next_inchi_key("LOTSWB"))
    entry = make_species_entry(db_session, species)
    gaussian = make_software_release(db_session, name="gaussian16-b", version="16")
    make_software_release(db_session, name="orca-unused-b", version="5.0")
    make_calculation(
        db_session,
        species_entry_id=entry.id,
        lot_id=lot.id,
        software_release_id=gaussian.id,
    )

    body = client.get(_detail_url(lot.public_ref, include="software")).json()
    names = {row["software"] for row in body["record"]["software"]["software"]}
    assert names == {"gaussian16-b"}
    assert "orca-unused-b" not in names


def test_lot_detail_include_software_scoped_to_correct_lot(client, db_session):
    """Mutation target (b): counts must come from *this* level of theory,
    not a sibling one.

    Two LOTs, two different software packages, one calculation each.
    Asking LOT A must never return LOT B's package or count.
    """
    lot_a = make_lot(db_session, method="lot-a-scope", basis="def2tzvp")
    lot_b = make_lot(db_session, method="lot-b-scope", basis="def2tzvp")
    species = make_species(db_session, smiles="C", inchi_key=next_inchi_key("LOTSWC"))
    entry = make_species_entry(db_session, species)
    gaussian = make_software_release(db_session, name="gaussian-scope-a", version="16")
    orca = make_software_release(db_session, name="orca-scope-b", version="5.0")
    make_calculation(
        db_session,
        species_entry_id=entry.id,
        lot_id=lot_a.id,
        software_release_id=gaussian.id,
    )
    make_calculation(
        db_session,
        species_entry_id=entry.id,
        lot_id=lot_b.id,
        software_release_id=orca.id,
    )

    body_a = client.get(_detail_url(lot_a.public_ref, include="software")).json()
    body_b = client.get(_detail_url(lot_b.public_ref, include="software")).json()
    names_a = {row["software"] for row in body_a["record"]["software"]["software"]}
    names_b = {row["software"] for row in body_b["record"]["software"]["software"]}
    assert names_a == {"gaussian-scope-a"}
    assert names_b == {"orca-scope-b"}


def test_lot_detail_include_software_unrecorded_version_is_null(client, db_session):
    """Mutation target (c): an unrecorded version must read as ``null``,
    never as ``""``. Absence is stated, not implied (this archive's
    house rule) -- rendering it blank would read as "we asked and there
    was nothing there" rather than "no one recorded it"."""
    lot = make_lot(db_session, method="b3lyp-sw-noversion", basis="def2tzvp")
    species = make_species(db_session, smiles="C", inchi_key=next_inchi_key("LOTSWD"))
    entry = make_species_entry(db_session, species)
    molpro = make_software_release(db_session, name="molpro-noversion", version=None)
    make_calculation(
        db_session,
        species_entry_id=entry.id,
        lot_id=lot.id,
        software_release_id=molpro.id,
    )

    body = client.get(_detail_url(lot.public_ref, include="software")).json()
    row = body["record"]["software"]["software"][0]
    assert row["version"] is None
    assert row["calculation_count"] == 1


def test_lot_detail_include_software_carries_workflow_tool_breakdown(
    client, db_session
):
    lot = make_lot(db_session, method="b3lyp-sw-wt", basis="def2tzvp")
    species = make_species(db_session, smiles="C", inchi_key=next_inchi_key("LOTSWE"))
    entry = make_species_entry(db_session, species)
    arc = make_workflow_tool_release(db_session, name="arc-sw-wt", version=None)
    gaussian = make_software_release(db_session, name="gaussian-sw-wt", version="16")
    make_calculation(
        db_session,
        species_entry_id=entry.id,
        lot_id=lot.id,
        software_release_id=gaussian.id,
        workflow_tool_release_id=arc.id,
    )

    body = client.get(_detail_url(lot.public_ref, include="software")).json()
    wt = body["record"]["software"]["workflow_tools"]
    assert wt == [
        {"workflow_tool": "arc-sw-wt", "version": None, "calculation_count": 1}
    ]


def test_lot_detail_evidence_distinct_software_count_matches_software_breakdown(
    client, db_session
):
    """PR 1's ``distinct_software_count`` scalar and ``include=software``'s
    breakdown are built from the same query (see
    ``build_level_of_theory_record`` in
    ``app/services/scientific_read/level_of_theory.py``) and must always
    agree. Two software packages, two versions of one of them -- the
    scalar counts *distinct packages* (2), not distinct (package,
    version) rows (3)."""
    lot = make_lot(db_session, method="b3lyp-sw-consistency", basis="def2tzvp")
    species = make_species(db_session, smiles="C", inchi_key=next_inchi_key("LOTSWF"))
    entry = make_species_entry(db_session, species)
    gaussian_v1 = make_software_release(
        db_session, name="gaussian-consistency", version="09"
    )
    gaussian_v2 = make_software_release(
        db_session, name="gaussian-consistency", version="16"
    )
    orca = make_software_release(db_session, name="orca-consistency", version="5.0")
    for release in (gaussian_v1, gaussian_v2, orca):
        make_calculation(
            db_session,
            species_entry_id=entry.id,
            lot_id=lot.id,
            software_release_id=release.id,
        )

    body = client.get(_detail_url(lot.public_ref, include="software")).json()
    breakdown = body["record"]["software"]["software"]
    distinct_names_in_breakdown = {row["software"] for row in breakdown}
    assert len(breakdown) == 3  # 3 distinct (package, version) rows
    assert distinct_names_in_breakdown == {"gaussian-consistency", "orca-consistency"}
    assert body["record"]["evidence_summary"]["distinct_software_count"] == len(
        distinct_names_in_breakdown
    )


def test_lot_detail_available_sections_has_software(client, db_session):
    lot_with = make_lot(db_session, method="b3lyp-sw-avail-yes", basis="def2tzvp")
    lot_without = make_lot(db_session, method="b3lyp-sw-avail-no", basis="def2tzvp")
    _with_usage(db_session, lot_without, method_suffix="AVAILNO")
    species = make_species(db_session, smiles="C", inchi_key=next_inchi_key("LOTSWG"))
    entry = make_species_entry(db_session, species)
    gaussian = make_software_release(db_session, name="gaussian-avail", version="16")
    make_calculation(
        db_session,
        species_entry_id=entry.id,
        lot_id=lot_with.id,
        software_release_id=gaussian.id,
    )

    body_with = client.get(_detail_url(lot_with.public_ref)).json()
    body_without = client.get(_detail_url(lot_without.public_ref)).json()
    assert body_with["record"]["available_sections"]["has_software"] is True
    assert body_without["record"]["available_sections"]["has_software"] is False


def test_lot_detail_include_software_live_shaped_fixture(client, db_session):
    """The archive-shaped fixture the methods-surface plan names directly
    (§5.3's PR 2 paragraph): 4 levels of theory, 3 distinct software
    packages, one 1:1:1 LOT-to-package mapping, plus the one workflow
    tool (ARC) alongside the b3lyp/def2tzvp package. Counts are scaled
    down from the live 416/78/39/39 for test speed; the *shape* --
    exactly one package per LOT, absent everywhere else -- is what this
    pins.
    """
    species = make_species(db_session, smiles="C", inchi_key=next_inchi_key("LOTSWH"))
    entry = make_species_entry(db_session, species)

    lot_b3lyp = make_lot(db_session, method="b3lyp-live", basis="def2tzvp")
    lot_wb97xd = make_lot(db_session, method="wb97xd-live", basis="def2tzvp")
    lot_ccsdt = make_lot(db_session, method="ccsd(t)-f12-live", basis="cc-pvtz-f12")
    lot_mrci = make_lot(
        db_session, method="mrci+davidson-live", basis="aug-cc-pv(t+d)z"
    )

    gaussian16 = make_software_release(db_session, name="Gaussian 16", version=None)
    gaussian09 = make_software_release(db_session, name="Gaussian 09", version=None)
    orca = make_software_release(db_session, name="ORCA", version=None)
    molpro = make_software_release(db_session, name="Molpro", version=None)
    arc = make_workflow_tool_release(db_session, name="ARC", version=None)

    mapping = {
        lot_b3lyp: (gaussian16, "Gaussian 16", 4, arc),
        lot_wb97xd: (gaussian09, "Gaussian 09", 3, None),
        lot_ccsdt: (orca, "ORCA", 2, None),
        lot_mrci: (molpro, "Molpro", 2, None),
    }
    for lot, (release, _name, count, workflow_release) in mapping.items():
        for _ in range(count):
            make_calculation(
                db_session,
                species_entry_id=entry.id,
                lot_id=lot.id,
                software_release_id=release.id,
                workflow_tool_release_id=(
                    workflow_release.id if workflow_release else None
                ),
            )

    reported: dict[str, dict] = {}
    for lot, (_release, name, count, _workflow_release) in mapping.items():
        body = client.get(_detail_url(lot.public_ref, include="software")).json()
        software = body["record"]["software"]["software"]
        assert len(software) == 1, f"{lot.method}: expected exactly one package"
        assert software[0]["software"] == name
        assert software[0]["calculation_count"] == count
        assert body["record"]["evidence_summary"]["distinct_software_count"] == 1
        reported[lot.method] = body["record"]["software"]

    # Only the b3lyp LOT carries the ARC workflow-tool usage.
    assert reported["b3lyp-live"]["workflow_tools"] == [
        {"workflow_tool": "ARC", "version": None, "calculation_count": 4}
    ]
    assert reported["wb97xd-live"]["workflow_tools"] == []
    assert reported["ccsd(t)-f12-live"]["workflow_tools"] == []
    assert reported["mrci+davidson-live"]["workflow_tools"] == []


def test_lot_detail_include_software_statement_count_is_flat_per_package(
    client, db_session
):
    """Mutation target (d): a per-package query inside a loop must be
    caught here.

    Six software packages at one level of theory. The number of SQL
    statements the ``include=software`` resolver issues must not grow
    with the number of distinct packages -- it is two bulk, grouped
    queries (software side, workflow-tool side) regardless of how many
    rows either returns. A regression that re-introduces a per-package
    query (looping over distinct names and querying each one, the exact
    shape the 12-manual-call gap this endpoint closes had before it
    existed) would make this scale with package count instead of staying
    flat.
    """
    from sqlalchemy import event

    lot = make_lot(db_session, method="b3lyp-sw-n1", basis="def2tzvp")
    species = make_species(db_session, smiles="C", inchi_key=next_inchi_key("LOTSWI"))
    entry = make_species_entry(db_session, species)
    for i in range(6):
        release = make_software_release(
            db_session, name=f"package-n1-{i}", version=None
        )
        make_calculation(
            db_session,
            species_entry_id=entry.id,
            lot_id=lot.id,
            software_release_id=release.id,
        )

    from app.services.scientific_read.level_of_theory import get_level_of_theory

    engine = db_session.connection().engine
    count = 0

    def _before(conn, cursor, statement, parameters, context, executemany):
        nonlocal count
        count += 1

    event.listen(engine, "before_cursor_execute", _before)
    try:
        response = get_level_of_theory(
            db_session,
            level_of_theory_handle=lot.public_ref,
            include=["software"],
        )
    finally:
        event.remove(engine, "before_cursor_execute", _before)

    assert len(response.record.software.software) == 6
    # Measured baseline is 6 statements (handle resolution, core row,
    # calc-usage, has_schemes, has_fsf, and the bulk software/workflow-tool
    # aggregation). A per-package loop over 6 distinct packages adds 6 more
    # (measured: 12 with the loop reintroduced). The ceiling sits well
    # above the real baseline for headroom, and well below what a
    # per-package loop over 6 packages would produce -- what matters is
    # that six distinct packages did not add six statements.
    assert count <= 8, f"expected a flat statement count, got {count}"


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
