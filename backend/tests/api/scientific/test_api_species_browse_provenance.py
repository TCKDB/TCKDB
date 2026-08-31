"""API tests for the calculation-provenance filters on GET /species/browse.

Covers ``method`` / ``basis`` / ``software`` / ``software_version`` /
``workflow_tool`` / ``workflow_tool_version``.

Before this endpoint gained these six declared query parameters, FastAPI
silently dropped them: ``?method=TOTAL_NONSENSE`` returned every species
in the corpus with HTTP 200, indistinguishable from "everything matches".
Every test below therefore pairs a *matching* value (must return a
strict subset) with a *non-matching* value (must return zero) — the
non-matching case is the one that used to silently return everything,
so it is the one that actually exercises the fix. Two species at two
different levels of theory are seeded in every test so a filter that
was accidentally deleted (i.e. one that matches unconditionally) fails
by returning both instead of one.
"""

from __future__ import annotations

from app.db.models.calculation import Calculation
from app.db.models.software import Software, SoftwareRelease
from app.db.models.workflow import WorkflowTool, WorkflowToolRelease
from tests.services.scientific_read._factories import (
    make_calculation,
    make_lot,
    make_species,
    make_species_entry,
    next_inchi_key,
    unique_smiles,
)

_URL = "/api/v1/scientific/species/browse"


def _make_species_with_entry(db_session, *, prefix: str):
    species = make_species(
        db_session, smiles=unique_smiles(), inchi_key=next_inchi_key(prefix)
    )
    entry = make_species_entry(db_session, species)
    return species, entry


def _attach_calc(
    db_session,
    *,
    entry,
    lot=None,
    software_release=None,
    workflow_tool_release=None,
) -> Calculation:
    calc = make_calculation(
        db_session,
        species_entry_id=entry.id,
        lot_id=lot.id if lot is not None else None,
    )
    if software_release is not None:
        calc.software_release_id = software_release.id
    if workflow_tool_release is not None:
        calc.workflow_tool_release_id = workflow_tool_release.id
    db_session.flush()
    return calc


def _make_software_release(db_session, *, name, version):
    sw = db_session.query(Software).filter(Software.name == name).one_or_none()
    if sw is None:
        sw = Software(name=name)
        db_session.add(sw)
        db_session.flush()
    sr = (
        db_session.query(SoftwareRelease)
        .filter(
            SoftwareRelease.software_id == sw.id,
            SoftwareRelease.version == version,
        )
        .one_or_none()
    )
    if sr is None:
        sr = SoftwareRelease(software_id=sw.id, version=version)
        db_session.add(sr)
        db_session.flush()
    return sw, sr


def _make_workflow_tool_release(db_session, *, name, version):
    wt = (
        db_session.query(WorkflowTool).filter(WorkflowTool.name == name).one_or_none()
    )
    if wt is None:
        wt = WorkflowTool(name=name)
        db_session.add(wt)
        db_session.flush()
    wtr = (
        db_session.query(WorkflowToolRelease)
        .filter(
            WorkflowToolRelease.workflow_tool_id == wt.id,
            WorkflowToolRelease.version == version,
        )
        .one_or_none()
    )
    if wtr is None:
        wtr = WorkflowToolRelease(workflow_tool_id=wt.id, version=version)
        db_session.add(wtr)
        db_session.flush()
    return wt, wtr


def _refs(body) -> set[str]:
    return {r["species_ref"] for r in body["records"]}


# ---------------------------------------------------------------------------
# method / basis
# ---------------------------------------------------------------------------


def test_browse_by_method_returns_strict_subset(client, db_session):
    species_a, entry_a = _make_species_with_entry(db_session, prefix="SPMETHA")
    species_b, entry_b = _make_species_with_entry(db_session, prefix="SPMETHB")
    lot_a = make_lot(db_session, method="wb97xd", basis="def2tzvp")
    lot_b = make_lot(db_session, method="b3lyp", basis="6-31g")
    _attach_calc(db_session, entry=entry_a, lot=lot_a)
    _attach_calc(db_session, entry=entry_b, lot=lot_b)

    resp = client.get(_URL, params={"method": "wb97xd"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    refs = _refs(body)
    assert species_a.public_ref in refs
    assert species_b.public_ref not in refs
    # Strict subset: the unfiltered pair both exist, but only one matches.
    assert refs < {species_a.public_ref, species_b.public_ref}
    assert body["request"]["filter"]["method"] == "wb97xd"


def test_browse_by_method_matching_nothing_returns_zero(client, db_session):
    """The exact TOTAL_NONSENSE case the bug report measured against /search."""
    species_a, entry_a = _make_species_with_entry(db_session, prefix="SPMETHZ")
    lot_a = make_lot(db_session, method="wb97xd", basis="def2tzvp")
    _attach_calc(db_session, entry=entry_a, lot=lot_a)

    resp = client.get(_URL, params={"method": "TOTAL_NONSENSE"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["records"] == []
    assert body["pagination"]["total"] == 0


def test_browse_by_method_and_basis_and_combine(client, db_session):
    species_a, entry_a = _make_species_with_entry(db_session, prefix="SPMBA")
    species_b, entry_b = _make_species_with_entry(db_session, prefix="SPMBB")
    lot_a = make_lot(db_session, method="wb97xd", basis="def2tzvp")
    lot_b = make_lot(db_session, method="wb97xd", basis="6-31g")
    _attach_calc(db_session, entry=entry_a, lot=lot_a)
    _attach_calc(db_session, entry=entry_b, lot=lot_b)

    resp = client.get(_URL, params={"method": "wb97xd", "basis": "def2tzvp"})

    assert resp.status_code == 200, resp.text
    refs = _refs(resp.json())
    assert refs == {species_a.public_ref}


def test_browse_by_basis_matching_nothing_returns_zero(client, db_session):
    species_a, entry_a = _make_species_with_entry(db_session, prefix="SPBASZ")
    lot_a = make_lot(db_session, method="wb97xd", basis="def2tzvp")
    _attach_calc(db_session, entry=entry_a, lot=lot_a)

    resp = client.get(_URL, params={"basis": "TOTAL_NONSENSE"})

    body = resp.json()
    assert body["records"] == []
    assert body["pagination"]["total"] == 0


# ---------------------------------------------------------------------------
# software / software_version
# ---------------------------------------------------------------------------


def test_browse_by_software_returns_strict_subset(client, db_session):
    species_a, entry_a = _make_species_with_entry(db_session, prefix="SPSWA")
    species_b, entry_b = _make_species_with_entry(db_session, prefix="SPSWB")
    _, sr_a = _make_software_release(db_session, name="gaussian", version="g16.a03")
    _, sr_b = _make_software_release(db_session, name="orca", version="5.0.4")
    _attach_calc(db_session, entry=entry_a, software_release=sr_a)
    _attach_calc(db_session, entry=entry_b, software_release=sr_b)

    resp = client.get(_URL, params={"software": "gaussian"})

    refs = _refs(resp.json())
    assert species_a.public_ref in refs
    assert species_b.public_ref not in refs


def test_browse_by_software_matching_nothing_returns_zero(client, db_session):
    species_a, entry_a = _make_species_with_entry(db_session, prefix="SPSWZ")
    _, sr_a = _make_software_release(db_session, name="gaussian", version="g16.a03")
    _attach_calc(db_session, entry=entry_a, software_release=sr_a)

    resp = client.get(_URL, params={"software": "NOPE"})

    body = resp.json()
    assert body["records"] == []
    assert body["pagination"]["total"] == 0


def test_browse_by_software_and_version_returns_strict_subset(client, db_session):
    species_a, entry_a = _make_species_with_entry(db_session, prefix="SPSWVA")
    species_b, entry_b = _make_species_with_entry(db_session, prefix="SPSWVB")
    _, sr_a = _make_software_release(db_session, name="gaussian", version="g16.a03")
    _, sr_b = _make_software_release(db_session, name="gaussian", version="g16.b01")
    _attach_calc(db_session, entry=entry_a, software_release=sr_a)
    _attach_calc(db_session, entry=entry_b, software_release=sr_b)

    resp = client.get(
        _URL, params={"software": "gaussian", "software_version": "g16.a03"}
    )

    refs = _refs(resp.json())
    assert refs == {species_a.public_ref}


def test_browse_by_software_version_matching_nothing_returns_zero(client, db_session):
    species_a, entry_a = _make_species_with_entry(db_session, prefix="SPSWVZ")
    _, sr_a = _make_software_release(db_session, name="gaussian", version="g16.a03")
    _attach_calc(db_session, entry=entry_a, software_release=sr_a)

    resp = client.get(
        _URL, params={"software": "gaussian", "software_version": "TOTAL_NONSENSE"}
    )

    body = resp.json()
    assert body["records"] == []
    assert body["pagination"]["total"] == 0


def test_browse_software_version_without_software_is_missing_version_parent(
    client, db_session
):
    resp = client.get(_URL, params={"software_version": "16"})

    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "missing_version_parent"


# ---------------------------------------------------------------------------
# workflow_tool / workflow_tool_version
# ---------------------------------------------------------------------------


def test_browse_by_workflow_tool_returns_strict_subset(client, db_session):
    species_a, entry_a = _make_species_with_entry(db_session, prefix="SPWTA")
    species_b, entry_b = _make_species_with_entry(db_session, prefix="SPWTB")
    _, wtr_a = _make_workflow_tool_release(db_session, name="arc", version="1.2.3")
    _, wtr_b = _make_workflow_tool_release(db_session, name="qcelemental", version="0.27.0")
    _attach_calc(db_session, entry=entry_a, workflow_tool_release=wtr_a)
    _attach_calc(db_session, entry=entry_b, workflow_tool_release=wtr_b)

    resp = client.get(_URL, params={"workflow_tool": "arc"})

    refs = _refs(resp.json())
    assert species_a.public_ref in refs
    assert species_b.public_ref not in refs


def test_browse_by_workflow_tool_matching_nothing_returns_zero(client, db_session):
    species_a, entry_a = _make_species_with_entry(db_session, prefix="SPWTZ")
    _, wtr_a = _make_workflow_tool_release(db_session, name="arc", version="1.2.3")
    _attach_calc(db_session, entry=entry_a, workflow_tool_release=wtr_a)

    resp = client.get(_URL, params={"workflow_tool": "NOPE"})

    body = resp.json()
    assert body["records"] == []
    assert body["pagination"]["total"] == 0


def test_browse_by_workflow_tool_and_version_returns_strict_subset(client, db_session):
    species_a, entry_a = _make_species_with_entry(db_session, prefix="SPWTVA")
    species_b, entry_b = _make_species_with_entry(db_session, prefix="SPWTVB")
    _, wtr_a = _make_workflow_tool_release(db_session, name="arc", version="1.2.3")
    _, wtr_b = _make_workflow_tool_release(db_session, name="arc", version="1.3.0")
    _attach_calc(db_session, entry=entry_a, workflow_tool_release=wtr_a)
    _attach_calc(db_session, entry=entry_b, workflow_tool_release=wtr_b)

    resp = client.get(
        _URL, params={"workflow_tool": "arc", "workflow_tool_version": "1.2.3"}
    )

    refs = _refs(resp.json())
    assert refs == {species_a.public_ref}


def test_browse_by_workflow_tool_version_matching_nothing_returns_zero(
    client, db_session
):
    species_a, entry_a = _make_species_with_entry(db_session, prefix="SPWTVZ")
    _, wtr_a = _make_workflow_tool_release(db_session, name="arc", version="1.2.3")
    _attach_calc(db_session, entry=entry_a, workflow_tool_release=wtr_a)

    resp = client.get(
        _URL,
        params={"workflow_tool": "arc", "workflow_tool_version": "TOTAL_NONSENSE"},
    )

    body = resp.json()
    assert body["records"] == []
    assert body["pagination"]["total"] == 0


def test_browse_workflow_tool_version_without_workflow_tool_is_missing_version_parent(
    client, db_session
):
    resp = client.get(_URL, params={"workflow_tool_version": "1.2.3"})

    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "missing_version_parent"


# ---------------------------------------------------------------------------
# any-across-calculation semantics
# ---------------------------------------------------------------------------


def test_browse_by_method_matches_species_with_any_matching_calculation(
    client, db_session
):
    """One species with calculations at two different methods: documented
    ``method=`` semantics is OR-across-calculation, so the species must
    match a filter naming *either* of its methods, not require both.
    """
    species_a, entry_a = _make_species_with_entry(db_session, prefix="SPANYA")
    species_b, entry_b = _make_species_with_entry(db_session, prefix="SPANYB")
    lot_wb97xd = make_lot(db_session, method="wb97xd", basis="def2tzvp")
    lot_b3lyp = make_lot(db_session, method="b3lyp", basis="6-31g")
    # species_a has calculations at BOTH methods.
    _attach_calc(db_session, entry=entry_a, lot=lot_wb97xd)
    _attach_calc(db_session, entry=entry_a, lot=lot_b3lyp)
    # species_b has a calculation at neither -- a distinct third method.
    lot_other = make_lot(db_session, method="ccsd(t)", basis="cc-pvtz")
    _attach_calc(db_session, entry=entry_b, lot=lot_other)

    resp_wb97xd = client.get(_URL, params={"method": "wb97xd"})
    resp_b3lyp = client.get(_URL, params={"method": "b3lyp"})

    refs_wb97xd = _refs(resp_wb97xd.json())
    refs_b3lyp = _refs(resp_b3lyp.json())
    # species_a matches BOTH single-method queries -- "any", not "all".
    assert species_a.public_ref in refs_wb97xd
    assert species_a.public_ref in refs_b3lyp
    assert species_b.public_ref not in refs_wb97xd
    assert species_b.public_ref not in refs_b3lyp
