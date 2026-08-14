"""An upload's 201 must name every record it wrote, not most of them.

``persist_computed_reaction_upload`` builds a result dict with eleven
keys. ``ComputedReactionUploadResult`` declared nine fields, and
``pydantic.BaseModel`` ignores extras by default — so
``ComputedReactionUploadResult(**result_dict)`` silently discarded
``statmech_ids`` and ``atom_map_id`` on every reaction-bundle upload.

Nothing was lost in the database; the rows were written. What was lost
was any way for the depositor to *name* them. A bundle carrying kinetics,
thermo and statmech got a 201 that listed the first two, which reads as a
deliberate contract rather than an omission. The cost was already being
paid in this repo:
``tests/api/test_api_bundle_provenance_warnings._statmech_rows`` reads
statmech back through ``species_entry_ids`` and says in its docstring
that it does so only because the response has no ``statmech_ids``.

Two kinds of test live here:

* **Per-field** — post a real bundle through the real route and assert
  the response names the rows the database actually holds. These would
  have gone red on ``main``.
* **Structural** — ``extra="forbid"`` turns the *next* such omission from
  a silent drop into a loud failure at construction time, and
  :func:`test_an_unknown_workflow_key_cannot_be_silently_dropped` proves
  that guard is live rather than merely declared.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.routes.uploads import ComputedReactionUploadResult
from app.db.models.reaction_atom_map import ReactionAtomMap
from app.db.models.statmech import Statmech

_SOFTWARE = {"name": "Gaussian", "version": "16"}
_LOT = {"method": "wb97xd", "basis": "def2tzvp"}

_XYZ_H = "1\nH atom\nH 0.0 0.0 0.0"
_XYZ_H2 = "2\nH2\nH 0.0 0.0 0.0\nH 0.0 0.0 0.74"


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
                "key": f"{key}-freq",
                "type": "freq",
                "geometry_key": f"{key}-geom",
                "software_release": _SOFTWARE,
                "level_of_theory": _LOT,
                "freq_n_imag": 0,
                "freq_zpe_hartree": 0.01,
            },
        ],
    }


def _bundle(**overrides) -> dict:
    """``H + H -> H2``. Balanced, no transition state."""
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


_STATMECH = {
    "scientific_origin": "computed",
    "statmech_treatment": "rrho",
    "external_symmetry": 1,
}


# ---------------------------------------------------------------------------
# statmech_ids
# ---------------------------------------------------------------------------


def test_reaction_bundle_result_names_the_statmech_it_wrote(
    client: TestClient, db_session
):
    """The response must list the statmech rows, not just kinetics/thermo.

    Both species carry statmech so the assertion is on a *set of two*:
    a response that named only one, or that collapsed the two into a
    single id, fails here where a truthy ``statmech_ids`` check would
    not.
    """
    bundle = _bundle()
    bundle["species"][0]["statmech"] = dict(_STATMECH)
    bundle["species"][1]["statmech"] = dict(_STATMECH)

    resp = client.post("/api/v1/uploads/computed-reaction", json=bundle)
    assert resp.status_code == 201, resp.text[:800]
    body = resp.json()

    assert "statmech_ids" in body, (
        "the reaction bundle wrote statmech rows and the 201 does not name "
        f"them; body keys were {sorted(body)}"
    )

    persisted = set(
        db_session.scalars(
            select(Statmech.id).where(
                Statmech.species_entry_id.in_(body["species_entry_ids"])
            )
        ).all()
    )
    assert len(persisted) == 2, persisted
    assert set(body["statmech_ids"]) == persisted


def test_a_bundle_without_statmech_reports_an_empty_list_not_a_missing_key(
    client: TestClient,
):
    """Absence is spelled ``[]``, the same way ``thermo_ids`` spells it.

    A depositor reading ``body["statmech_ids"]`` must not have to guess
    whether a missing key means "none written" or "this server is too old
    to say".
    """
    resp = client.post("/api/v1/uploads/computed-reaction", json=_bundle())
    assert resp.status_code == 201, resp.text[:800]
    assert resp.json()["statmech_ids"] == []


# ---------------------------------------------------------------------------
# atom_map_id
# ---------------------------------------------------------------------------


_XYZ_CH3 = (
    "4\nmethyl\n"
    "C  0.000  0.000  0.000\n"
    "H  1.080  0.000  0.000\n"
    "H -0.540  0.935  0.000\n"
    "H -0.540 -0.935  0.000"
)
_XYZ_CH4 = (
    "5\nmethane\n"
    "C  0.000  0.000  0.000\n"
    "H  0.629  0.629  0.629\n"
    "H -0.629 -0.629  0.629\n"
    "H -0.629  0.629 -0.629\n"
    "H  0.629 -0.629 -0.629"
)
_XYZ_TS = (
    "5\nTS for CH3 + H -> CH4\n"
    "C  0.000  0.000  0.000\n"
    "H  0.629  0.629  0.629\n"
    "H -0.629 -0.629  0.629\n"
    "H -0.629  0.629 -0.629\n"
    "H  0.000  0.000  1.400"
)


def _mapped_bundle(with_map: bool) -> dict:
    """``CH3 + H -> CH4`` with a transition state, the shape an atom map needs.

    Mirrors ``tests/api/scientific/test_api_reaction_atom_map`` rather
    than inventing a payload, so this test exercises the same write path
    the atom-map suite does — only reading the id from the 201 instead of
    querying it back.
    """
    bundle: dict = {
        "species": [
            _species("ch3", "[CH3]", 2, _XYZ_CH3),
            _species("h", "[H]", 2, _XYZ_H),
            _species("ch4", "C", 1, _XYZ_CH4),
        ],
        "reversible": True,
        "reactant_keys": ["ch3", "h"],
        "product_keys": ["ch4"],
        "transition_state": {
            "charge": 0,
            "multiplicity": 2,
            "geometry": {"key": "ts-geom", "xyz_text": _XYZ_TS},
            "calculation": {
                "key": "ts-opt",
                "type": "opt",
                "software_release": _SOFTWARE,
                "level_of_theory": _LOT,
                "opt_converged": True,
            },
            "calculations": [
                {
                    "key": "ts-freq",
                    "type": "freq",
                    "geometry_key": "ts-geom",
                    "software_release": _SOFTWARE,
                    "level_of_theory": _LOT,
                    "freq_n_imag": 1,
                    "freq_imag_freq_cm1": -1500.0,
                }
            ],
        },
    }
    if with_map:
        bundle["atom_map"] = {
            "source": "declared",
            "ts_geometry_key": "ts-geom",
            "participants": [
                {
                    "side": "reactant",
                    "species_key": "ch3",
                    "participant_index": 1,
                    "geometry_key": "ch3-geom",
                    "atom_to_ts": {1: 1, 2: 2, 3: 3, 4: 4},
                },
                {
                    "side": "reactant",
                    "species_key": "h",
                    "participant_index": 2,
                    "geometry_key": "h-geom",
                    "atom_to_ts": {1: 5},
                },
                {
                    "side": "product",
                    "species_key": "ch4",
                    "participant_index": 1,
                    "geometry_key": "ch4-geom",
                    "atom_to_ts": {1: 1, 2: 2, 3: 3, 4: 4, 5: 5},
                },
            ],
        }
    return bundle


def test_reaction_bundle_result_names_the_atom_map_it_wrote(
    client: TestClient, db_session
):
    """``atom_map_id`` was computed by the workflow and then dropped.

    Found while fixing ``statmech_ids``: the same ``extra="ignore"``
    default discarded it. Every atom-map test in the tree reads the map
    back through ``/scientific/reaction-entries/{id}/full`` instead,
    which is why nothing noticed.
    """
    resp = client.post(
        "/api/v1/uploads/computed-reaction", json=_mapped_bundle(with_map=True)
    )
    assert resp.status_code == 201, resp.text[:800]
    body = resp.json()

    assert "atom_map_id" in body, (
        "the bundle wrote a reaction_atom_map row and the 201 does not name "
        f"it; body keys were {sorted(body)}"
    )
    assert body["atom_map_id"] is not None
    row = db_session.get(ReactionAtomMap, body["atom_map_id"])
    assert row is not None, "the 201 named an atom_map_id that does not exist"
    assert row.reaction_entry_id == body["reaction_entry_id"]


def test_a_bundle_without_an_atom_map_reports_none(client: TestClient):
    """``None`` means "no map was written", and must not mean "not said".

    Paired with the test above so the field cannot be satisfied by
    hard-coding either answer.
    """
    resp = client.post(
        "/api/v1/uploads/computed-reaction", json=_mapped_bundle(with_map=False)
    )
    assert resp.status_code == 201, resp.text[:800]
    assert resp.json()["atom_map_id"] is None


# ---------------------------------------------------------------------------
# The structural guard
# ---------------------------------------------------------------------------


def test_an_unknown_workflow_key_cannot_be_silently_dropped():
    """The guard that makes the *next* omission of this kind impossible.

    ``ComputedReactionUploadResult(**result_dict)`` is the seam where
    ``statmech_ids`` and ``atom_map_id`` were lost. Under pydantic's
    default ``extra="ignore"`` that seam cannot fail, so no test anywhere
    could have caught either. With ``extra="forbid"`` a workflow key with
    no matching response field raises at construction, in every existing
    reaction-bundle test at once.

    This test exists because a config flag that is set but never
    exercised is indistinguishable from one that was reverted.
    """
    with pytest.raises(Exception) as excinfo:
        ComputedReactionUploadResult(
            reaction_entry_id=1,
            reaction_id=1,
            transition_state_entry_id=None,
            atom_map_id=None,
            kinetics_ids=[],
            thermo_ids=[],
            statmech_ids=[],
            species_entry_ids=[],
            species_count=0,
            a_product_type_invented_tomorrow=[7],
        )
    assert "a_product_type_invented_tomorrow" in str(excinfo.value)


def test_the_result_model_declares_every_key_the_workflow_returns():
    """Names the contract in one place a reader can check by eye.

    The ``extra="forbid"`` test above proves the seam is loud; this one
    states which keys are expected to cross it, so removing a field from
    the response model fails with a message that says what is missing
    rather than a KeyError somewhere downstream.
    """
    expected = {
        "reaction_entry_id",
        "reaction_id",
        "transition_state_entry_id",
        "atom_map_id",
        "kinetics_ids",
        "thermo_ids",
        "statmech_ids",
        "species_entry_ids",
        "species_count",
        "calculation_keys",
        "warnings",
    }
    declared = set(ComputedReactionUploadResult.model_fields)
    assert expected <= declared, f"response model is missing {expected - declared}"
