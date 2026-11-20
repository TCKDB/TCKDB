"""Read filters compare a software, workflow-tool or scheme name, and a DOI, by the key the public ref uses.

An upload naming ``PSI4`` lands on the row stored as ``Psi4``; a read filter for ``PSI4`` has to find that row too.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.db.models.energy_correction import EnergyCorrectionScheme
from app.db.models.literature import Literature
from tests.services.scientific_read._factories import (
    make_calculation,
    make_software_release,
    make_species,
    make_species_entry,
    make_workflow_tool_release,
    next_inchi_key,
    unique_smiles,
)

APP_DIR = Path(__file__).resolve().parents[3] / "app"


def _entry(db_session, prefix: str):
    species = make_species(db_session, smiles=unique_smiles(), inchi_key=next_inchi_key(prefix))
    return make_species_entry(db_session, species)


@pytest.fixture
def two_calcs(db_session):
    psi4 = make_software_release(db_session, name="Psi4", version="1.9.1")
    other_sw = make_software_release(db_session, name="NWChem", version="7.2")
    tool = make_workflow_tool_release(db_session, name="geomeTRIC", version="1.0")
    other_tool = make_workflow_tool_release(db_session, name="Pysisyphus", version="0.8")
    match = make_calculation(
        db_session,
        species_entry_id=_entry(db_session, "RFM").id,
        software_release_id=psi4.id,
        workflow_tool_release_id=tool.id,
    )
    other = make_calculation(
        db_session,
        species_entry_id=_entry(db_session, "RFO").id,
        software_release_id=other_sw.id,
        workflow_tool_release_id=other_tool.id,
    )
    return match, other


def _calc_refs(body: dict) -> set[str]:
    return {r["calculation"]["calculation_ref"] for r in body["records"]}


@pytest.mark.parametrize("param", ["software=PSI4", "software=%20psi4%20", "workflow_tool=GeomeTRIC"])
def test_calculation_search_matches_a_case_variant(client, two_calcs, param) -> None:
    match, other = two_calcs

    refs = _calc_refs(client.get(f"/api/v1/scientific/calculations/search?{param}").json())

    assert match.public_ref in refs
    assert other.public_ref not in refs


@pytest.mark.parametrize("param", ["software=PSI4", "software=psi4"])
def test_calculation_analytics_matches_a_case_variant(client, two_calcs, param) -> None:
    match, other = two_calcs

    body = client.get(f"/api/v1/scientific/analytics/calculations?{param}").json()
    refs = {r["calculation_ref"] for r in body["records"]}

    assert refs == {match.public_ref}


def test_meta_versions_match_a_case_variant(client, two_calcs) -> None:
    software = client.get("/api/v1/scientific/meta/software-versions", params={"software": "PSI4"}).json()
    tools = client.get("/api/v1/scientific/meta/workflow-tool-versions", params={"workflow_tool": "GEOMETRIC"}).json()

    assert {r["value"] for r in software["results"]} == {"1.9.1"}
    assert {r["value"] for r in tools["results"]} == {"1.0"}


def test_legacy_software_and_calculation_lists_match_a_case_variant(client, two_calcs) -> None:
    match, other = two_calcs

    software = client.get("/api/v1/software", params={"name": "psi4"}).json()
    calcs = client.get("/api/v1/calculations", params={"software_name": "PSI4"}).json()

    assert [s["name"] for s in software["items"]] == ["Psi4"]
    assert {c["id"] for c in calcs["items"]} == {match.id}


def test_scheme_name_filters_match_a_case_variant(client, db_session) -> None:
    scheme = EnergyCorrectionScheme(kind="atom_energy", name="CBS-QB3 AEC", units="hartree")
    other = EnergyCorrectionScheme(kind="atom_energy", name="G4 AEC", units="hartree")
    db_session.add_all([scheme, other])
    db_session.flush()

    legacy = client.get("/api/v1/energy-correction-schemes", params={"name": "cbs-qb3 aec"}).json()
    search = client.get("/api/v1/scientific/energy-correction-schemes/search", params={"name": "CBS-qb3 aec"}).json()

    assert [s["id"] for s in legacy["items"]] == [scheme.id]
    search_refs = [r["energy_correction_scheme"]["energy_correction_scheme_ref"] for r in search["records"]]
    assert search_refs == [scheme.public_ref]


def test_legacy_literature_doi_filter_matches_a_dx_doi_link(client, db_session) -> None:
    row = Literature(kind="article", title="A paper", doi="10.1000/abc")
    db_session.add(row)
    db_session.flush()

    body = client.get("/api/v1/literature", params={"doi": "https://dx.doi.org/10.1000/ABC"}).json()

    assert [r["id"] for r in body["items"]] == [row.id]


def test_no_lookup_compares_a_software_tool_or_scheme_name_or_a_doi_exactly() -> None:
    pattern = re.compile(r"\b(Software|WorkflowTool|EnergyCorrectionScheme)\.name *==|\bLiterature\.doi *==")
    allowed = {
        # The resolver matches the identity columns as a fallback after the public-ref lookup.
        ("services/energy_correction_resolution.py", "EnergyCorrectionScheme.name =="),
        # A DOI that is blank under the ref's normalisation has no content ref.
        ("services/literature_resolution.py", "Literature.doi =="),
    }
    hits = {
        (str(path.relative_to(APP_DIR)), re.sub(r" +", " ", m.group(0)))
        for path in APP_DIR.rglob("*.py")
        for m in pattern.finditer(path.read_text())
    }

    assert hits - allowed == set()
