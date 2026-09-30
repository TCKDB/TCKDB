"""``t0_k`` is stored and served by both kinetics routes (#620).

A deposit that names a reference temperature must come back naming it: stored
on the ``kinetics`` row, and on the read model a consumer evaluates k(T) from
(``parameters.T0_k``). A deposit that does not name one is 1 K.

The two routes are driven with the same T0 and checked at the same three
places (HTTP response accepted, row column, read model), so a route that
accepts the field and drops it is caught by the row, and a row that stores it
and a read model that forgets it is caught by the read.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.models.common import ArrheniusAUnits
from app.db.models.kinetics import Kinetics

_KINETICS = "/api/v1/uploads/kinetics"
_BUNDLE = "/api/v1/uploads/computed-reaction"

_SOFTWARE = {"name": "Gaussian", "version": "16"}
_LOT = {"method": "wb97xd", "basis": "def2tzvp"}
_XYZ_H = "1\nH atom\nH 0.0 0.0 0.0"
_XYZ_H2 = "2\nH2\nH 0.0 0.0 0.0\nH 0.0 0.0 0.74"

_REACTION = {
    "reversible": False,
    "reactants": [
        {"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}},
        {"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}},
    ],
    "products": [{"species_entry": {"smiles": "[H][H]", "charge": 0, "multiplicity": 1}}],
}


def _standalone(**overrides) -> dict:
    body = {
        "reaction": _REACTION,
        "scientific_origin": "computed",
        "model_kind": "modified_arrhenius",
        "a": 1.0e10,
        "a_units": "cm3_mol_s",
        "n": 2.0,
        "reported_ea": 10.0,
        "reported_ea_units": "kj_mol",
        "tmin_k": 300.0,
        "tmax_k": 2000.0,
    }
    body.update(overrides)
    return body


def _species(key: str, smiles: str, multiplicity: int, xyz: str) -> dict:
    return {
        "key": key,
        "species_entry": {"smiles": smiles, "charge": 0, "multiplicity": multiplicity},
        "conformers": [
            {
                "key": f"{key}-conf",
                "geometry": {"key": f"{key}-geom", "xyz_text": xyz},
                "calculation": {
                    "key": f"{key}-opt",
                    "type": "opt",
                    "software_release": _SOFTWARE,
                    "level_of_theory": _LOT,
                    "opt_converged": True,
                },
            }
        ],
        "calculations": [],
    }


def _bundle(**kinetics_overrides) -> dict:
    kinetics = {
        "scientific_origin": "computed",
        "model_kind": "modified_arrhenius",
        "a": 1.0e10,
        "a_units": "cm3_mol_s",
        "n": 2.0,
        "reported_ea": 10.0,
        "reported_ea_units": "kj_mol",
        "tmin_k": 300.0,
        "tmax_k": 2000.0,
        "reactant_keys": ["h", "h"],
        "product_keys": ["h2"],
    }
    kinetics.update(kinetics_overrides)
    return {
        "species": [
            _species("h", "[H]", 2, _XYZ_H),
            _species("h2", "[H][H]", 1, _XYZ_H2),
        ],
        "reversible": True,
        "reactant_keys": ["h", "h"],
        "product_keys": ["h2"],
        "kinetics": [kinetics],
    }


def _post_standalone(client, **overrides):
    return client.post(_KINETICS, json=_standalone(**overrides))


def _post_bundle(client, **overrides):
    return client.post(_BUNDLE, json=_bundle(**overrides))


_ROUTES = pytest.mark.parametrize("post", [_post_standalone, _post_bundle], ids=["standalone", "bundle"])


def _latest_kinetics(db_session) -> Kinetics:
    return db_session.scalars(select(Kinetics).order_by(Kinetics.id.desc()).limit(1)).one()


def _served_parameters(client, db_session, kinetics: Kinetics) -> dict:
    response = client.get(
        f"/api/v1/scientific/reaction-entries/{kinetics.reaction_entry_id}/kinetics"
    )
    assert response.status_code == 200, response.text[:600]
    records = [r for r in response.json()["records"] if r["kinetics_ref"] == kinetics.public_ref]
    assert len(records) == 1, response.text[:600]
    return records[0]["parameters"]


@_ROUTES
def test_a_deposited_t0_is_stored_and_served(client, db_session, post):
    """The value sent is the value on the row and the value read back."""
    response = post(client, t0_k=298.15)
    assert response.status_code == 201, response.text[:800]

    row = _latest_kinetics(db_session)
    assert row.t0_k == 298.15
    assert row.a == 1.0e10, "a is stored as deposited: it is A at T0, not A rescaled"
    assert _served_parameters(client, db_session, row)["T0_k"] == 298.15


@_ROUTES
def test_an_omitted_t0_is_one_kelvin_on_the_row_and_on_the_read(client, db_session, post):
    response = post(client)
    assert response.status_code == 201, response.text[:800]

    row = _latest_kinetics(db_session)
    assert row.t0_k == 1.0
    assert _served_parameters(client, db_session, row)["T0_k"] == 1.0


@_ROUTES
@pytest.mark.parametrize("bad", [0.0, -298.0])
def test_a_non_positive_t0_is_a_422_and_stores_nothing(client, db_session, post, bad):
    # The accepted neighbour first: on a server that did not know the field at
    # all, ``extra="forbid"`` would also answer 422 and "t0_k" would be in the
    # message, so the refusal alone proves nothing about the bound.
    accepted = post(client, t0_k=298.15)
    assert accepted.status_code == 201, accepted.text[:800]

    before = db_session.scalars(select(Kinetics.id)).all()
    response = post(client, t0_k=bad)
    assert response.status_code == 422, response.text[:800]
    assert "t0_k" in response.text
    assert db_session.scalars(select(Kinetics.id)).all() == before


def test_the_database_refuses_a_non_positive_t0_even_if_the_application_did_not(db_session):
    """The CHECK is the backstop for a hand-written row, a script or a restore."""
    from sqlalchemy.exc import IntegrityError

    from tests.services.scientific_read._factories import (
        make_chem_reaction,
        make_reaction_entry,
        make_species,
        make_species_entry,
        next_inchi_key,
    )

    reactant = make_species(db_session, smiles="A", inchi_key=next_inchi_key("T0A"))
    product = make_species(db_session, smiles="B", inchi_key=next_inchi_key("T0B"))
    entry = make_reaction_entry(
        db_session,
        reaction=make_chem_reaction(db_session, reactants=[reactant], products=[product]),
        reactant_entries=[make_species_entry(db_session, reactant)],
        product_entries=[make_species_entry(db_session, product)],
    )
    with db_session.begin_nested():
        db_session.add(
            Kinetics(reaction_entry_id=entry.id, scientific_origin="computed", t0_k=298.0)
        )
        db_session.flush()  # a positive value is accepted

    for bad in (0.0, -1.0, float("inf")):
        with pytest.raises(IntegrityError, match="t0_k_finite_positive"):
            with db_session.begin_nested():
                db_session.add(
                    Kinetics(reaction_entry_id=entry.id, scientific_origin="computed", t0_k=bad)
                )
                db_session.flush()


# ---------------------------------------------------------------------------
# The other surfaces that hand a rate to a consumer
# ---------------------------------------------------------------------------


def _stored_rate(db_session, *, t0_k: float):
    from tests.services.scientific_read._factories import (
        make_chem_reaction,
        make_kinetics,
        make_reaction_entry,
        make_species,
        make_species_entry,
        next_inchi_key,
    )

    reactant = make_species(db_session, smiles="A", inchi_key=next_inchi_key("T0R"))
    product = make_species(db_session, smiles="B", inchi_key=next_inchi_key("T0P"))
    entry = make_reaction_entry(
        db_session,
        reaction=make_chem_reaction(db_session, reactants=[reactant], products=[product]),
        reactant_entries=[make_species_entry(db_session, reactant)],
        product_entries=[make_species_entry(db_session, product)],
    )
    return make_kinetics(
        db_session, reaction_entry=entry, a=1.0e10, a_units=ArrheniusAUnits.per_s, n=2.0, t0_k=t0_k
    )


@pytest.mark.parametrize("t0_k", [1.0, 298.15])
def test_the_analytics_kinetics_records_carry_t0_k(client, db_session, t0_k):
    """A dataset built from the analytics surface evaluates k(T) from ``a``,
    ``n`` and ``ea_kj_mol``; without ``t0_k`` beside them it is wrong by T0^n."""
    kinetics = _stored_rate(db_session, t0_k=t0_k)
    response = client.get("/api/v1/scientific/analytics/kinetics")
    assert response.status_code == 200, response.text[:600]
    record = next(r for r in response.json()["records"] if r["kinetics_ref"] == kinetics.public_ref)
    assert record["t0_k"] == t0_k


def test_the_exported_contribution_bundle_carries_t0_k_and_reingests_it(db_session):
    """Export is the route by which a rate leaves one instance for another."""
    from app.schemas.workflows.contribution_bundle import ContributionBundleV0
    from app.services.contribution_bundle_export import export_kinetics_bundle

    kinetics = _stored_rate(db_session, t0_k=298.15)
    bundle = export_kinetics_bundle(
        db_session,
        kinetics_ids=[kinetics.id],
        title="T0",
        summary="Reference temperature round trip.",
        exporter_label="tester",
    )
    reingested = ContributionBundleV0.model_validate(bundle.model_dump(mode="json"))
    assert reingested.records.kinetics_uploads[0].t0_k == 298.15
