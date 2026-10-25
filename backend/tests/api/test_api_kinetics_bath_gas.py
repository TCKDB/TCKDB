"""A reaction-level kinetics record states the bath gas it was obtained for.

Covers the three routes that deposit a rate (``/uploads/kinetics``, ``/uploads/computed-reaction``
and a kinetics contribution bundle), the scientific read, and the coded refusals as a client sees
them. Each test names the mutation it kills.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.models.kinetics import Kinetics, KineticsBathGas
from app.db.models.species import SpeciesEntry
from tests.api.test_api_kinetics_declarations import (
    BUNDLE,
    HE,
    KINETICS,
    N2,
    _bundle,
    _code,
    _round_trip,
    _standalone,
)

_APPARENT = {"pressure_context": "apparent_at_pressure", "pressure_bar": 1.0}


def _latest(db_session) -> Kinetics:
    return db_session.scalars(select(Kinetics).order_by(Kinetics.id.desc()).limit(1)).one()


def _stored(db_session, kinetics: Kinetics) -> dict[str, float]:
    rows = db_session.scalars(select(KineticsBathGas).where(KineticsBathGas.kinetics_id == kinetics.id)).all()
    return {db_session.get(SpeciesEntry, r.species_entry_id).species.smiles: r.mole_fraction for r in rows}


def _read(client, kinetics: Kinetics) -> dict:
    response = client.get(f"/api/v1/scientific/reaction-entries/{kinetics.reaction_entry_id}/kinetics")
    assert response.status_code == 200, response.text[:600]
    (record,) = [r for r in response.json()["records"] if r["kinetics_ref"] == kinetics.public_ref]
    return record


# ---------------------------------------------------------------------------
# /uploads/kinetics
# ---------------------------------------------------------------------------


def test_standalone_bath_gas_is_stored_and_read(client, db_session):
    """Kills: field ignored on the standalone route; rows never written; read block missing."""
    body = _standalone(**_APPARENT, bath_gas=[{"species_entry": HE, "mole_fraction": 0.5}, {"species_entry": N2, "mole_fraction": 0.5}])
    assert client.post(KINETICS, json=body).status_code == 201

    row = _latest(db_session)
    assert _stored(db_session, row) == {"[He]": 0.5, "N#N": 0.5}
    record = _read(client, row)
    assert sorted(b["mole_fraction"] for b in record["bath_gas"]) == [0.5, 0.5]
    refs = {db_session.get(SpeciesEntry, r.species_entry_id).public_ref for r in db_session.scalars(select(KineticsBathGas))}
    assert {b["species_entry_ref"] for b in record["bath_gas"]} == refs


def test_a_record_with_no_stated_bath_gas_reads_null(client, db_session):
    """Kills: a bath gas inferred or defaulted when none was stated."""
    assert client.post(KINETICS, json=_standalone()).status_code == 201
    row = _latest(db_session)
    assert _stored(db_session, row) == {}
    assert _read(client, row)["bath_gas"] is None


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        (
            {"pressure_context": "high_p_limit", "bath_gas": [{"species_entry": HE, "mole_fraction": 1.0}]},
            "kinetics_bath_gas_at_high_pressure_limit",
        ),
        (
            {**_APPARENT, "bath_gas": [{"species_entry": HE, "mole_fraction": 0.8}, {"species_entry": N2, "mole_fraction": 0.5}]},
            "kinetics_bath_gas_mole_fraction_sum",
        ),
        (
            {**_APPARENT, "bath_gas": [{"species_entry": HE, "mole_fraction": 0.3}, {"species_entry": N2, "mole_fraction": 0.3}]},
            "kinetics_bath_gas_mole_fraction_sum",
        ),
        (
            {**_APPARENT, "bath_gas": [{"species_entry": HE, "mole_fraction": 0.5}, {"species_entry": HE, "mole_fraction": 0.5}]},
            "kinetics_bath_gas_duplicate_species",
        ),
    ],
    ids=["high_p_limit", "sum_above_one", "sum_below_one", "duplicate"],
)
def test_standalone_refusals_arrive_with_their_code(client, db_session, overrides, code):
    """Kills: a rule dropped, or reported under the generic validation code."""
    before = len(db_session.scalars(select(Kinetics)).all())
    assert _code(client.post(KINETICS, json=_standalone(**overrides))) == code
    assert len(db_session.scalars(select(Kinetics)).all()) == before


def test_a_refusal_names_no_row_id(client):
    response = client.post(
        KINETICS,
        json=_standalone(
            pressure_context="high_p_limit", bath_gas=[{"species_entry": HE, "mole_fraction": 1.0}]
        ),
    )
    assert response.status_code == 422
    detail = response.text
    assert "species_entry_id" not in detail and "kinetics_id" not in detail


# ---------------------------------------------------------------------------
# /uploads/computed-reaction
# ---------------------------------------------------------------------------


def test_computed_reaction_bath_gas_is_stored_by_species_key(client, db_session):
    """Kills: the bundle fit's bath gas ignored by the workflow; key resolved to the wrong entry."""
    fit = {"pressure_context": "pressure_dependent", "bath_gas": [{"species_key": "h2", "mole_fraction": 1.0}]}
    assert client.post(BUNDLE, json=_bundle(**fit)).status_code == 201
    row = _latest(db_session)
    assert _stored(db_session, row) == {"[H][H]": 1.0}
    assert len(_read(client, row)["bath_gas"]) == 1


def test_computed_reaction_bath_gas_naming_an_undeclared_species_is_refused(client):
    fit = {"pressure_context": "pressure_dependent", "bath_gas": [{"species_key": "nope", "mole_fraction": 1.0}]}
    assert _code(client.post(BUNDLE, json=_bundle(**fit))) == "species_key_undeclared"


@pytest.mark.parametrize(
    ("bath_gas", "pressure_context", "code"),
    [
        ([{"species_key": "h2", "mole_fraction": 1.0}], "high_p_limit", "kinetics_bath_gas_at_high_pressure_limit"),
        (
            [{"species_key": "h2", "mole_fraction": 0.9}, {"species_key": "h", "mole_fraction": 0.9}],
            "pressure_dependent",
            "kinetics_bath_gas_mole_fraction_sum",
        ),
        (
            [{"species_key": "h2", "mole_fraction": 0.5}, {"species_key": "h2", "mole_fraction": 0.5}],
            "pressure_dependent",
            "kinetics_bath_gas_duplicate_species",
        ),
    ],
    ids=["high_p_limit", "sum", "duplicate"],
)
def test_computed_reaction_refusals_arrive_with_their_code(client, bath_gas, pressure_context, code):
    fit = {"pressure_context": pressure_context, "bath_gas": bath_gas}
    assert _code(client.post(BUNDLE, json=_bundle(**fit))) == code


# ---------------------------------------------------------------------------
# Contribution bundle: export, then import again
# ---------------------------------------------------------------------------


def test_bath_gas_survives_a_bundle_export_and_re_import(client, db_session):
    """Kills: export dropping the bath gas; import ignoring it; mole fractions altered on the way."""
    body = _standalone(**_APPARENT, bath_gas=[{"species_entry": HE, "mole_fraction": 0.25}, {"species_entry": N2, "mole_fraction": 0.75}])
    assert client.post(KINETICS, json=body).status_code == 201
    original = _latest(db_session)

    bundle, imported = _round_trip(db_session, [original.id])

    (upload,) = bundle.records.kinetics_uploads
    assert sorted(bg.mole_fraction for bg in upload.bath_gas) == [0.25, 0.75]
    (copy,) = imported
    assert copy.id != original.id
    assert _stored(db_session, copy) == {"[He]": 0.25, "N#N": 0.75}


def test_a_record_without_bath_gas_exports_without_one(client, db_session):
    """Kills: export inventing a bath gas for a record that stated none."""
    assert client.post(KINETICS, json=_standalone()).status_code == 201
    bundle, imported = _round_trip(db_session, [_latest(db_session).id])
    assert bundle.records.kinetics_uploads[0].bath_gas == []
    assert _stored(db_session, imported[0]) == {}
