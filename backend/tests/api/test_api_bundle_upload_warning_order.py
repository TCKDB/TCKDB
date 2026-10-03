"""The order of upload warnings, and bundles with more than one upload (#647).

``test_api_bundle_upload_warnings.py`` compares a bundle with the direct route,
so it cannot notice both reordering the same way. These tests pin the direct
routes' own order for one multi-warning record per kind, and then check that
each upload in a multi-upload bundle reports its own warnings under its own
``local_ref`` and ``record_type``.
"""

from __future__ import annotations

import pytest

from tests.api.test_api_bundle_upload_warnings import (
    DRY_RUN,
    SUBMIT,
    _bundle_warnings,
    _direct_warnings,
    _doi_metadata,  # noqa: F401  (autouse fixture)
    _example,
    _kinetics_bundle,
    _kinetics_with_literature_bundle,
    _thermo_record,
)


def _golden_thermo_record() -> dict:
    """One warning from each source, so the order they come in is the test:
    identity reconciliation, provenance, the ref walk, then the workflow."""
    record = _thermo_record()
    record["species_entry"]["term_symbol"] = "3P"
    record["software_release"] = {"name": "gaussian", "version": "Gaussian 09, Revision D.01"}
    return record


def _golden_kinetics_record() -> dict:
    """Provenance, content, the ref walk, then the workflow's literature check."""
    record = _kinetics_with_literature_bundle()["records"]["kinetics_uploads"][0]
    record["software_release"] = {"name": "gaussian", "version": "gaussian 09"}
    return record


def _codes_and_fields(client, url: str, record: dict) -> list[tuple[str, str]]:
    return [(code, field) for code, _message, field in _direct_warnings(client, url, record)]


def test_the_direct_thermo_route_orders_its_warnings_reconciliation_provenance_refs_workflow(client) -> None:
    assert _codes_and_fields(client, "/api/v1/uploads/thermo", _golden_thermo_record()) == [
        ("term_symbol_contradicts_multiplicity", "species_entry.term_symbol"),
        ("missing_workflow_tool_provenance", "workflow_tool_release"),
        ("software_release_version_is_composite", "software_release.version"),
        ("named_composite_deposited_as_sp", "level_of_theory"),
        ("missing_literature_provenance", "scheme.source_literature"),
        ("missing_energy_correction_scheme_software", "scheme.software"),
        ("composite_delta_prefer_scheme_terms", "application_role"),
    ]


def test_the_direct_kinetics_route_orders_its_warnings_provenance_content_refs_workflow(client) -> None:
    assert _codes_and_fields(client, "/api/v1/uploads/kinetics", _golden_kinetics_record()) == [
        ("missing_level_of_theory_provenance", "energy_level_of_theory"),
        ("missing_kinetics_interpretation_assignments", "interpretation_assignments"),
        ("missing_tunneling_application_evidence", "tunneling_application"),
        ("software_release_version_is_composite", "software_release.version"),
        ("literature_title_mismatch", "literature.title"),
    ]


def _multi_thermo_bundle() -> dict:
    """Three uploads with different warning counts, so a hard-coded ``local_ref``
    or a merged list cannot match: one warning, none, then seven."""
    first = _example("thermo")["records"]["thermo_uploads"][0]
    first["species_entry"]["term_symbol"] = "3P"
    clean = _example("thermo")["records"]["thermo_uploads"][0]
    clean["species_entry"] = {"smiles": "C", "charge": 0, "multiplicity": 1}
    clean["software_release"] = {"name": "gaussian", "version": "16"}
    clean["workflow_tool_release"] = {"name": "ARC", "version": "1.0.0"}
    bundle = _example("thermo")
    bundle["records"]["thermo_uploads"] = [first, clean, _golden_thermo_record()]
    return bundle


def _multi_kinetics_bundle() -> dict:
    bundle = _kinetics_bundle()
    bundle["records"]["kinetics_uploads"] = [
        bundle["records"]["kinetics_uploads"][0],
        _golden_kinetics_record(),
    ]
    return bundle


MULTI = {
    "thermo": (_multi_thermo_bundle, "/api/v1/uploads/thermo", "thermo_uploads"),
    "kinetics": (_multi_kinetics_bundle, "/api/v1/uploads/kinetics", "kinetics_uploads"),
}


@pytest.mark.parametrize("route", [SUBMIT, DRY_RUN], ids=["submit", "dry_run"])
@pytest.mark.parametrize("kind", sorted(MULTI))
def test_each_upload_in_a_bundle_reports_its_own_warnings_under_its_own_local_ref(
    client, kind: str, route: str
) -> None:
    build, url, collection = MULTI[kind]
    bundle = build()
    expected = [_direct_warnings(client, url, record) for record in bundle["records"][collection]]
    assert sum(1 for e in expected if e) >= 2 and len({len(e) for e in expected}) >= 2, expected

    resp = client.post(route, json=bundle)
    assert resp.status_code in (200, 201), resp.text[:600]
    messages = resp.json()["messages"]

    for index, want in enumerate(expected):
        assert _bundle_warnings(messages, f"{collection}[{index}]") == want
    upload_warnings = [
        m for m in messages if m["level"] == "warning" and (m.get("local_ref") or "").startswith(collection)
    ]
    assert {m["record_type"] for m in upload_warnings} == {kind}
    # Uploads are reported in upload order, each one's warnings together.
    indexes = [int(m["local_ref"].split("[")[1].rstrip("]")) for m in upload_warnings]
    assert indexes == sorted(indexes)
