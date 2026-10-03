"""Read gaps the composite phases left open on the level-of-theory surfaces (ADR 0021, P7a).

On ``main`` (fea447ec):

* ``GET /levels-of-theory`` (``LevelOfTheoryRead``) returned neither ``core_treatment`` (P4) nor
  ``spin_treatment`` (DR-0034), though both are part of a level's identity;
* the ML-dataset export's level-of-theory block carried ``spin_treatment`` and not ``core_treatment``;
* level-of-theory search could not filter on ``core_treatment``;
* a correction scheme's frequency level (P6) was on the scheme read; the level-of-theory detail's
  ``include=correction_schemes`` is checked here to show it too.

``core_treatment`` filters match only levels that *state* it. A level whose producer did not say is
never "frozen core by default", so it matches neither value.
"""

from __future__ import annotations

import hashlib

import pytest

from app.db.models.common import CoreTreatment, SpinTreatment
from app.db.models.level_of_theory import LevelOfTheory
from app.schemas.workflows.energy_correction_upload import EnergyCorrectionSchemeRef
from app.services.energy_correction_resolution import resolve_or_create_scheme
from app.services.scientific_read.ml_dataset import _lot_block
from tests.api.scientific.test_api_level_of_theory import _with_usage

FC, AE = CoreTreatment.frozen_core, CoreTreatment.all_electron


def _lot(db_session, tag: str, *, core: CoreTreatment | None, spin: SpinTreatment | None = None) -> LevelOfTheory:
    lot = LevelOfTheory(
        method=f"ccsd(t)-{tag}",
        basis="cc-pcvtz",
        core_treatment=core,
        spin_treatment=spin,
        lot_hash=hashlib.sha256(f"{tag}|{core}|{spin}".encode()).hexdigest(),
    )
    db_session.add(lot)
    db_session.flush()
    return lot


# ---------------------------------------------------------------------------
# LevelOfTheoryRead
# ---------------------------------------------------------------------------


def _by_ref(client, **params) -> dict[str, dict]:
    resp = client.get("/api/v1/levels-of-theory", params={"limit": 200, **params})
    assert resp.status_code == 200, resp.text[:600]
    return {item["lot_hash"]: item for item in resp.json()["items"]}


def test_the_list_and_the_detail_read_carry_core_and_spin_treatment(client, db_session):
    fc = _lot(db_session, "rd1", core=FC, spin=SpinTreatment.unrestricted)
    ae = _lot(db_session, "rd2", core=AE)
    bare = _lot(db_session, "rd3", core=None)
    items = _by_ref(client)
    assert (items[fc.lot_hash]["core_treatment"], items[fc.lot_hash]["spin_treatment"]) == ("frozen_core", "unrestricted")
    assert (items[ae.lot_hash]["core_treatment"], items[ae.lot_hash]["spin_treatment"]) == ("all_electron", None)
    # Unstated is null, never a default.
    assert (items[bare.lot_hash]["core_treatment"], items[bare.lot_hash]["spin_treatment"]) == (None, None)
    detail = client.get(f"/api/v1/levels-of-theory/{fc.id}").json()
    assert (detail["core_treatment"], detail["spin_treatment"]) == ("frozen_core", "unrestricted")


def test_the_list_filters_on_core_treatment_and_unstated_matches_neither_value(client, db_session):
    fc = _lot(db_session, "fl1", core=FC)
    ae = _lot(db_session, "fl2", core=AE)
    bare = _lot(db_session, "fl3", core=None)
    only_fc = _by_ref(client, core_treatment="frozen_core")
    assert fc.lot_hash in only_fc and ae.lot_hash not in only_fc and bare.lot_hash not in only_fc
    assert {item["core_treatment"] for item in only_fc.values()} == {"frozen_core"}
    only_ae = _by_ref(client, core_treatment="all_electron")
    assert ae.lot_hash in only_ae and fc.lot_hash not in only_ae and bare.lot_hash not in only_ae
    everything = _by_ref(client)
    assert {fc.lot_hash, ae.lot_hash, bare.lot_hash} <= set(everything)


def test_an_unknown_core_treatment_filter_is_refused(client):
    resp = client.get("/api/v1/levels-of-theory", params={"core_treatment": "windowed"})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Scientific search, POST and browse
# ---------------------------------------------------------------------------


def _used(db_session, tag: str, core: CoreTreatment | None) -> LevelOfTheory:
    lot = _lot(db_session, tag, core=core)
    _with_usage(db_session, lot, method_suffix=tag.upper())
    return lot


def _refs(response) -> set[str]:
    assert response.status_code == 200, response.text[:600]
    return {r["level_of_theory"]["level_of_theory_ref"] for r in response.json()["records"]}


def test_search_filters_on_core_treatment_by_get_and_by_post(client, db_session):
    fc = _used(db_session, "sr1", FC)
    ae = _used(db_session, "sr2", AE)
    bare = _used(db_session, "sr3", None)
    got = _refs(client.get("/api/v1/scientific/level-of-theories/search", params={"core_treatment": "frozen_core"}))
    assert got == {fc.public_ref}
    assert ae.public_ref not in got and bare.public_ref not in got
    posted = _refs(client.post("/api/v1/scientific/level-of-theories/search", json={"core_treatment": "all_electron"}))
    assert posted == {ae.public_ref}


def test_core_treatment_alone_is_a_meaningful_search_filter(client, db_session):
    """It satisfies the 'at least one filter' gate, so the call is not a 422 ``missing_filter``."""
    _used(db_session, "sr4", FC)
    resp = client.get("/api/v1/scientific/level-of-theories/search", params={"core_treatment": "frozen_core"})
    assert resp.status_code == 200, resp.text[:400]
    assert resp.json()["request"]["filter"]["core_treatment"] == "frozen_core"


def test_browse_filters_on_core_treatment(client, db_session):
    fc = _used(db_session, "br1", FC)
    ae = _used(db_session, "br2", AE)
    bare = _used(db_session, "br3", None)
    got = _refs(client.get("/api/v1/scientific/level-of-theories/browse", params={"core_treatment": "all_electron"}))
    assert got == {ae.public_ref}
    everything = _refs(client.get("/api/v1/scientific/level-of-theories/browse"))
    assert {fc.public_ref, ae.public_ref, bare.public_ref} <= everything


def test_the_search_record_reports_the_treatment_it_was_filtered_on(client, db_session):
    fc = _used(db_session, "sr5", FC)
    body = client.get("/api/v1/scientific/level-of-theories/search", params={"core_treatment": "frozen_core"}).json()
    (record,) = body["records"]
    assert record["level_of_theory"]["level_of_theory_ref"] == fc.public_ref
    assert record["level_of_theory"]["core_treatment"] == "frozen_core"


# ---------------------------------------------------------------------------
# The ML-dataset level-of-theory block
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("core", "expected"), [(FC, "frozen_core"), (AE, "all_electron"), (None, None)])
def test_the_ml_dataset_block_carries_core_treatment(db_session, core, expected):
    lot = _lot(db_session, f"ml-{expected}", core=core, spin=SpinTreatment.restricted)
    block = _lot_block(db_session, lot.id, {})
    assert "core_treatment" in block
    assert block["core_treatment"] == expected
    assert block["spin_treatment"] == "restricted"  # unchanged beside it
    assert block["level_of_theory_ref"] == lot.public_ref


def test_the_ml_dataset_block_of_no_level_is_none(db_session):
    assert _lot_block(db_session, None, {}) is None


# ---------------------------------------------------------------------------
# The correction scheme's frequency level on the level-of-theory detail
# ---------------------------------------------------------------------------


def test_the_level_detail_shows_the_frequency_level_of_its_correction_schemes(client, db_session):
    scheme = resolve_or_create_scheme(
        db_session,
        EnergyCorrectionSchemeRef(
            kind="bac_petersson",
            name="Petersson BAC detail test",
            level_of_theory={"method": "CCSD(T)-F12", "basis": "cc-pVTZ-F12"},
            frequency_level_of_theory={"method": "B3LYP", "basis": "6-311G(d,p)"},
            units="kcal_mol",
            bond_params=[{"bond_key": "C-H", "value": 0.1}],
        ),
    )
    bare = resolve_or_create_scheme(
        db_session,
        EnergyCorrectionSchemeRef(
            kind="bac_petersson",
            name="Petersson BAC detail test bare",
            level_of_theory={"method": "CCSD(T)-F12", "basis": "cc-pVTZ-F12"},
            units="kcal_mol",
            bond_params=[{"bond_key": "C-H", "value": 0.1}],
        ),
    )
    detail = client.get(
        f"/api/v1/scientific/level-of-theories/{scheme.level_of_theory.public_ref}",
        params={"include": "correction_schemes"},
    )
    assert detail.status_code == 200, detail.text[:600]
    schemes = {s["energy_correction_scheme"]["energy_correction_scheme_ref"]: s for s in detail.json()["record"]["correction_schemes"]}
    assert schemes[scheme.public_ref]["frequency_level_of_theory"]["method"].lower() == "b3lyp"
    assert schemes[scheme.public_ref]["frequency_level_of_theory"]["basis"] == "6-311G(d,p)"
    assert schemes[bare.public_ref]["frequency_level_of_theory"] is None
