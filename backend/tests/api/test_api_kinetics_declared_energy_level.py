"""A kinetics record's declared ``energy_level_of_theory`` is stored and read back.

It used to be a resolution hint on ``/uploads/kinetics`` (used to find source single points,
then thrown away) and did not exist on the bundle's kinetics. It is now persisted as
``kinetics.energy_level_of_theory_id`` and read as ``levels.declared_energy``, the same shape
thermo and statmech took in #619 (#671/#683).

Each assertion below is paired with the mutation it kills (stored level dropped, bundle field
ignored, read filled from a linked calculation, mismatch check skipped, export omitting it).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.models.kinetics import Kinetics
from app.db.models.level_of_theory import LevelOfTheory
from tests.api.test_api_bundle_provenance_warnings import (
    _kinetics as _bundle_kinetics,
)
from tests.api.test_api_bundle_provenance_warnings import (
    _reaction_bundle,
)
from tests.api.test_api_composite_kinetics_energy_level import (
    _H_ATOM,
    _METHANE,
    _METHYL,
    _OTHER,
    _deposit,
    _kinetics,
    _sp_calc,
)
from tests.api.test_api_kinetics_declarations import _round_trip
from tests.api.test_api_provenance_warnings import _LOT as _LOT_A

_LOT_B = {"method": "wB97X-D", "basis": "def2-TZVP"}
_SOFTWARE = {"name": "Gaussian", "version": "16"}
_XYZ_H = "1\nH\nH 0.0 0.0 0.0"
_REACTION = "/api/v1/uploads/computed-reaction"


def _latest(db_session) -> Kinetics:
    return db_session.scalars(select(Kinetics).order_by(Kinetics.id.desc()).limit(1)).one()


def _declared(client, kinetics: Kinetics) -> dict | None:
    response = client.get(f"/api/v1/scientific/reaction-entries/{kinetics.reaction_entry_id}/kinetics")
    assert response.status_code == 200, response.text[:600]
    (record,) = [r for r in response.json()["records"] if r["kinetics_ref"] == kinetics.public_ref]
    return record["levels"]["declared_energy"]


def _bundle_with_sp(sp_level: dict, **kinetics_overrides) -> dict:
    """``H + H -> H2`` whose H carries a single point at ``sp_level`` linked as reactant energy."""
    bundle = _reaction_bundle(kinetics=[_bundle_kinetics(**kinetics_overrides)])
    h = bundle["species"][0]
    h["calculations"].append(
        {
            "key": "h-sp",
            "type": "sp",
            "geometry_key": "h-geom",
            "software_release": _SOFTWARE,
            "level_of_theory": sp_level,
            "sp_electronic_energy_hartree": -0.5,
        }
    )
    return bundle


def _link_h_sp(bundle: dict) -> dict:
    bundle["kinetics"][0]["source_calculations"] = [{"calculation_key": "h-sp", "role": "reactant_energy"}]
    return bundle


# ---------------------------------------------------------------------------
# /uploads/kinetics
# ---------------------------------------------------------------------------


def test_standalone_declared_level_is_stored_and_read(client, db_session):
    """Kills: stored level dropped (the resolution hint used to be thrown away)."""
    for species in (_METHYL, _H_ATOM, _METHANE):
        _deposit(client, species, primary=_sp_calc(_OTHER))
    resp = _kinetics(client, energy_level=_OTHER)
    assert resp.status_code == 201, resp.text[:600]

    row = _latest(db_session)
    assert row.energy_level_of_theory_id is not None
    lot = db_session.get(LevelOfTheory, row.energy_level_of_theory_id)
    assert (lot.method, lot.basis) == ("B3LYP", "6-31G(d)")
    declared = _declared(client, row)
    assert declared["level_of_theory_ref"] == lot.public_ref
    assert declared["method"] == "B3LYP"


# ---------------------------------------------------------------------------
# /uploads/computed-reaction
# ---------------------------------------------------------------------------


def test_bundle_declared_level_is_stored_and_read(client, db_session):
    """Kills: bundle field ignored."""
    bundle = _bundle_with_sp(_LOT_B, energy_level_of_theory=_LOT_B)
    resp = client.post(_REACTION, json=_link_h_sp(bundle))
    assert resp.status_code == 201, resp.text[:800]

    row = _latest(db_session)
    assert row.energy_level_of_theory_id is not None
    assert db_session.get(LevelOfTheory, row.energy_level_of_theory_id).method == "wb97xd"
    assert _declared(client, row)["method"] == "wb97xd"


def test_declaration_with_no_linked_energy_is_stored_as_declared(client, db_session):
    resp = client.post(_REACTION, json=_reaction_bundle(kinetics=[_bundle_kinetics(energy_level_of_theory=_LOT_B)]))
    assert resp.status_code == 201, resp.text[:800]
    assert _declared(client, _latest(db_session))["method"] == "wb97xd"


def test_no_declaration_reads_null_and_is_not_filled_from_a_linked_calculation(client, db_session):
    """Kills: read fills from a linked calc (or the writer stores a derived level).

    The reactant's single point is linked and at a known level, yet nothing was declared.
    """
    bundle = _link_h_sp(_bundle_with_sp(_LOT_B))
    resp = client.post(_REACTION, json=bundle)
    assert resp.status_code == 201, resp.text[:800]

    row = _latest(db_session)
    assert row.energy_level_of_theory_id is None
    assert _declared(client, row) is None


def test_a_declared_level_that_contradicts_a_linked_energy_is_refused(client):
    """Kills: mismatch check skipped. Paired with the matching deposit above."""
    bundle = _link_h_sp(_bundle_with_sp(_LOT_B, energy_level_of_theory=_LOT_A))
    resp = client.post(_REACTION, json=bundle)
    assert resp.status_code == 422, resp.text[:800]
    assert resp.json()["code"] == "kinetics_energy_level_contradiction"


def test_a_computed_fit_with_no_declared_level_is_warned_per_fit(client):
    resp = client.post(_REACTION, json=_reaction_bundle(kinetics=[_bundle_kinetics()]))
    assert resp.status_code == 201, resp.text[:800]
    pairs = {(w["field"], w["code"]) for w in resp.json()["warnings"]}
    assert ("kinetics[0].energy_level_of_theory", "missing_level_of_theory_provenance") in pairs

    declared = client.post(_REACTION, json=_reaction_bundle(kinetics=[_bundle_kinetics(energy_level_of_theory=_LOT_B)]))
    assert declared.status_code == 201, declared.text[:800]
    assert "missing_level_of_theory_provenance" not in {w["code"] for w in declared.json()["warnings"]}


# ---------------------------------------------------------------------------
# Export and re-import
# ---------------------------------------------------------------------------


def test_the_declared_level_survives_an_export_and_re_import(client, db_session):
    """Kills: export omits the field. Re-import must not demand the exporter's calculations."""
    resp = client.post(_REACTION, json=_reaction_bundle(kinetics=[_bundle_kinetics(energy_level_of_theory=_LOT_B)]))
    assert resp.status_code == 201, resp.text[:800]
    original = _latest(db_session)

    bundle, imported = _round_trip(db_session, [original.id])

    assert bundle.records.kinetics_uploads[0].energy_level_of_theory.method == "wb97xd"
    (copy,) = imported
    assert copy.id != original.id
    assert copy.energy_level_of_theory_id == original.energy_level_of_theory_id


def test_an_undeclared_record_exports_without_a_level(client, db_session):
    resp = client.post(_REACTION, json=_reaction_bundle(kinetics=[_bundle_kinetics()]))
    assert resp.status_code == 201, resp.text[:800]
    bundle, imported = _round_trip(db_session, [_latest(db_session).id])
    assert bundle.records.kinetics_uploads[0].energy_level_of_theory is None
    assert imported[0].energy_level_of_theory_id is None


# ---------------------------------------------------------------------------
# Bundle import: the declared level is stored; source calculations are linked all-or-none, and it says so
# ---------------------------------------------------------------------------


def _source_links(db_session, kinetics_id: int) -> int:
    from app.db.models.kinetics import KineticsSourceCalculation

    return len(
        db_session.scalars(
            select(KineticsSourceCalculation).where(KineticsSourceCalculation.kinetics_id == kinetics_id)
        ).all()
    )


def _import_warnings(db_session, kinetics_id: int) -> list:
    """Re-import one exported kinetics record with the bundle policy, returning (row, warnings)."""
    from app.services.contribution_bundle_export import export_kinetics_bundle
    from app.workflows.kinetics import persist_kinetics_upload

    bundle = export_kinetics_bundle(
        db_session, kinetics_ids=[kinetics_id], title="t", summary="s", exporter_label="t"
    )
    warnings: list = []
    row = persist_kinetics_upload(
        db_session, bundle.records.kinetics_uploads[0], warnings=warnings, require_energy_sources=False
    )
    return row, warnings


@pytest.mark.parametrize(
    "ambiguous", [_METHYL, _H_ATOM, _METHANE], ids=["first_reactant", "middle_reactant", "last_product"]
)
def test_a_bundle_import_links_every_participant_or_none_and_still_stores_the_level(client, db_session, ambiguous):
    """Kills: partial linking (the ``links = []`` reset removed), wherever the failing participant sits."""
    for species in (_METHYL, _H_ATOM, _METHANE):
        _deposit(client, species, primary=_sp_calc(_OTHER))
    resp = _kinetics(client, energy_level=_OTHER)
    assert resp.status_code == 201, resp.text[:600]
    original = _latest(db_session)
    assert _source_links(db_session, original.id) == 3
    # A second single point at the same level makes that one participant ambiguous; the others still resolve,
    # so a partial link set would show up as a nonzero count.
    _deposit(client, ambiguous, primary=_sp_calc(_OTHER))

    _bundle, imported = _round_trip(db_session, [original.id])

    (copy,) = imported
    assert copy.energy_level_of_theory_id == original.energy_level_of_theory_id is not None
    assert _source_links(db_session, copy.id) == 0


def test_a_bundle_import_that_links_nothing_says_why(client, db_session):
    """Ambiguous participant, then no calculation at all: each is reported, and neither refuses the import."""
    for species in (_METHYL, _H_ATOM, _METHANE):
        _deposit(client, species, primary=_sp_calc(_OTHER))
    assert _kinetics(client, energy_level=_OTHER).status_code == 201
    original = _latest(db_session)

    linked_row, linked_warnings = _import_warnings(db_session, original.id)
    assert _source_links(db_session, linked_row.id) == 3
    assert "energy_level_stored_without_source_calculations" not in {w.code for w in linked_warnings}

    _deposit(client, _METHYL, primary=_sp_calc(_OTHER))
    ambiguous_row, ambiguous = _import_warnings(db_session, original.id)
    assert _source_links(db_session, ambiguous_row.id) == 0
    (warning,) = [w for w in ambiguous if w.code == "energy_level_stored_without_source_calculations"]
    assert "more than one" in warning.message

    resp = client.post(_REACTION, json=_reaction_bundle(kinetics=[_bundle_kinetics(energy_level_of_theory=_LOT_B)]))
    assert resp.status_code == 201, resp.text[:800]
    _row, none = _import_warnings(db_session, _latest(db_session).id)
    (warning,) = [w for w in none if w.code == "energy_level_stored_without_source_calculations"]
    assert "no single calculation" in warning.message


def test_a_contradiction_names_each_calculation_once(client):
    """H + H links the same hydrogen single point twice; the refusal lists it once."""
    # No explicit links: the legacy fallback links the hydrogen single point once per reactant key.
    bundle = _bundle_with_sp(_LOT_B, energy_level_of_theory=_LOT_A)
    resp = client.post(_REACTION, json=bundle)
    assert resp.status_code == 422, resp.text[:800]
    assert resp.json()["code"] == "kinetics_energy_level_contradiction"
    refs = resp.json()["context"]["energy_calculation_refs"]
    assert len(refs) == len(set(refs)) == 1


def test_only_a_missing_or_ambiguous_lookup_is_forgiven_on_bundle_import(client, db_session, monkeypatch):
    """Kills: ``except ValueError`` swallowing a coded refusal from the resolution itself."""
    from app.api.error_contract import CodedValueError
    from app.workflows import kinetics as workflow

    resp = client.post(_REACTION, json=_reaction_bundle(kinetics=[_bundle_kinetics(energy_level_of_theory=_LOT_B)]))
    assert resp.status_code == 201, resp.text[:800]

    def refuse(*_args, **_kwargs):
        raise CodedValueError("unknown_species_entry", "refused", message_prefix=False)

    monkeypatch.setattr(workflow, "_find_energy_calculation_for_species", refuse)
    with pytest.raises(CodedValueError):
        _import_warnings(db_session, _latest(db_session).id)
