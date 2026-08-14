"""A calculation's citation is content, not a primary key.

``CalculationIn`` — the shared calculation block behind the
computed-reaction bundle and the network-PDep upload — accepted
``literature_id: int``. A depositor cannot know that number. Supplying it
requires having already queried this database for the row, which is the
definition of a field only an insider can use, and it is the exact defect
#118 removed from the standalone statmech route
(``tests/workflows/test_statmech_upload.TestStatmechUploadRejectsRawFKs``).

The species bundle never had it: ``CalculationInBundle.literature`` has
always taken an inline ``LiteratureUploadRequest`` that the workflow
resolves. The reaction bundle now takes the same fragment, so the two
roots agree and ``.claude/rules/schema-rules.md``'s "No FK IDs in upload
schemas" holds without an exemption.

This is a **breaking** wire change: ``literature_id`` no longer
validates. ``SchemaBase`` is ``extra="forbid"``, so an old payload gets a
422 naming the field rather than being accepted and silently ignored —
the failure mode worth having, since silently ignoring it would drop the
citation.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models.calculation import Calculation
from app.db.models.literature import Literature

_SOFTWARE = {"name": "Gaussian", "version": "16"}
_LOT = {"method": "wb97xd", "basis": "def2tzvp"}

_XYZ_H = "1\nH atom\nH 0.0 0.0 0.0"
_XYZ_H2 = "2\nH2\nH 0.0 0.0 0.0\nH 0.0 0.0 0.74"

_LITERATURE = {
    "kind": "article",
    "title": "A barrier height for H + H",
    "journal": "J. Test",
    "year": 2026,
    "doi": "10.1000/tckdb.calc.literature.inline",
}


def _species(key: str, smiles: str, multiplicity: int, xyz: str) -> dict:
    return {
        "key": key,
        "species_entry": {
            "smiles": smiles,
            "charge": 0,
            "multiplicity": multiplicity,
        },
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
        "calculations": [
            {
                "key": f"{key}-sp",
                "type": "sp",
                "geometry_key": f"{key}-geom",
                "software_release": _SOFTWARE,
                "level_of_theory": _LOT,
                "sp_electronic_energy_hartree": -0.5,
            },
        ],
    }


def _bundle(**overrides) -> dict:
    base: dict = {
        "species": [
            _species("h", "[H]", 2, _XYZ_H),
            _species("h2", "[H][H]", 1, _XYZ_H2),
        ],
        "reversible": True,
        "reactant_keys": ["h", "h"],
        "product_keys": ["h2"],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# The new contract
# ---------------------------------------------------------------------------


def test_a_bundle_calculation_cites_literature_inline(
    client: TestClient, db_session
):
    """The citation a depositor can actually supply, end to end.

    Asserts the resolved row is attached to *the calculation named in the
    payload* rather than merely that some literature row was created —
    a workflow that resolved the fragment and then forgot to bind it
    would pass the weaker check.
    """
    bundle = _bundle()
    bundle["species"][0]["calculations"][0]["literature"] = dict(_LITERATURE)

    resp = client.post("/api/v1/uploads/computed-reaction", json=bundle)
    assert resp.status_code == 201, resp.text[:1200]
    body = resp.json()

    calc_id = body["calculation_keys"]["h-sp"]
    calc = db_session.get(Calculation, calc_id)
    assert calc.literature_id is not None, (
        "the bundle cited literature on this calculation and the workflow "
        "did not attach it"
    )
    lit = db_session.get(Literature, calc.literature_id)
    assert lit.doi == _LITERATURE["doi"]

    # And only the calculation that cited it got it.
    sibling = db_session.get(Calculation, body["calculation_keys"]["h2-sp"])
    assert sibling.literature_id is None


def test_two_calculations_citing_the_same_paper_share_one_row(
    client: TestClient, db_session
):
    """Resolution deduplicates, so the inline fragment is not a row-per-mention.

    The reason ``literature_id`` felt necessary was the fear of
    duplicating a paper on every calculation. It is not: the workflow
    routes through ``resolve_or_create_literature`` exactly as the species
    bundle and every thermo/statmech block already do.
    """
    bundle = _bundle()
    bundle["species"][0]["calculations"][0]["literature"] = dict(_LITERATURE)
    bundle["species"][1]["calculations"][0]["literature"] = dict(_LITERATURE)

    resp = client.post("/api/v1/uploads/computed-reaction", json=bundle)
    assert resp.status_code == 201, resp.text[:1200]
    body = resp.json()

    ids = {
        db_session.get(Calculation, body["calculation_keys"][key]).literature_id
        for key in ("h-sp", "h2-sp")
    }
    assert None not in ids
    assert len(ids) == 1, f"the same DOI produced {len(ids)} literature rows"

    rows = db_session.scalars(
        select(Literature).where(Literature.doi == _LITERATURE["doi"])
    ).all()
    assert len(rows) == 1


def test_a_calculation_may_still_cite_nothing(client: TestClient, db_session):
    """Omission stays legal — this is provenance, not a required field."""
    resp = client.post("/api/v1/uploads/computed-reaction", json=_bundle())
    assert resp.status_code == 201, resp.text[:1200]
    body = resp.json()
    calc = db_session.get(Calculation, body["calculation_keys"]["h-sp"])
    assert calc.literature_id is None


# ---------------------------------------------------------------------------
# The removed contract
# ---------------------------------------------------------------------------


def test_a_raw_literature_id_is_refused_by_the_route(client: TestClient):
    """Refused loudly, at the boundary, naming the field.

    ``extra="forbid"`` is what makes this a 422 rather than a 201 with the
    citation quietly dropped. Asserting on the field name (not just the
    status) is deliberate: a 422 for some unrelated reason would satisfy a
    bare status check.
    """
    bundle = _bundle()
    bundle["species"][0]["calculations"][0]["literature_id"] = 42

    resp = client.post("/api/v1/uploads/computed-reaction", json=bundle)
    assert resp.status_code == 422, resp.text[:600]
    assert "literature_id" in resp.text


def test_the_shared_calculation_model_no_longer_declares_literature_id():
    """The field is gone from the model, not merely unreachable by one route.

    ``CalculationIn`` is shared with the network-PDep upload, whose no-FK
    gate carried a standing exemption for this exact field
    (``tests/workflows/test_network_pdep_upload``). Pinning the model
    directly is what stops the field reappearing on a route this file does
    not post to.
    """
    from tckdb_schemas.shared.calculation_in import CalculationIn
    from tckdb_schemas.workflows.computed_reaction_upload import (
        ComputedReactionCalculationIn,
    )

    for model in (CalculationIn, ComputedReactionCalculationIn):
        assert "literature_id" not in model.model_fields, model.__name__
        assert "literature" in model.model_fields, model.__name__


def test_the_adapter_refuses_to_drop_a_citation_it_was_not_given_an_id_for():
    """The guard on the seam that could otherwise lose a citation silently.

    ``calculation_in_to_with_results_payload`` cannot resolve literature —
    the wire package has no database. If it defaulted the id to ``None``,
    a workflow that forgot to resolve would produce a valid payload with
    the citation missing and nothing would fail. It raises instead.
    """
    from tckdb_schemas.shared.calculation_in import (
        CalculationIn,
        calculation_in_to_with_results_payload,
    )

    calc = CalculationIn(
        key="c1",
        type="sp",
        software_release=_SOFTWARE,
        level_of_theory=_LOT,
        literature=_LITERATURE,
    )
    with pytest.raises(ValueError, match="literature_id"):
        calculation_in_to_with_results_payload(calc)

    # With the id supplied it converts, and carries the id through.
    payload = calculation_in_to_with_results_payload(calc, literature_id=7)
    assert payload.literature_id == 7
