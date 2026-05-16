"""Dispatch tests for ``TCKDBClient.upload``.

The single ``upload(...)`` method supports two structurally-distinct
forms: the legacy ``upload(endpoint, payload_dict)`` and the new
builder ``upload(builder_object)``. The tests below enforce that the
two forms stay distinct and that ambiguous inputs are rejected
loudly rather than silently dispatched to the wrong endpoint.
"""

from __future__ import annotations

import json

import httpx
import pytest

from tckdb_client.builders import (
    Calculation,
    ChemReaction,
    ComputedReactionUpload,
    ComputedSpeciesUpload,
    Geometry,
    Kinetics,
    LevelOfTheory,
    Species,
    SoftwareRelease,
    TransitionState,
)

from conftest import make_client


def _ok(payload: dict | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json=payload or {"ok": True})

    return handler


def _make_water_upload() -> ComputedSpeciesUpload:
    g = Geometry.from_xyz("1\nh\nH 0 0 0")
    sr = SoftwareRelease(software="Gaussian", version="16")
    lot = LevelOfTheory(method="B3LYP", basis="6-31G(d)")
    opt = Calculation.opt(sr, lot, output_geometry=g, converged=True)
    return ComputedSpeciesUpload(
        species=Species(smiles="[H]", charge=0, multiplicity=2),
        calculations=[opt],
    )


def test_builder_upload_dispatches_to_computed_species_endpoint():
    client, recorder = make_client(_ok({"species_entry_id": 7}))
    upload = _make_water_upload()
    result = client.upload(upload)
    assert result == {"species_entry_id": 7}
    assert recorder.last.url.endswith("/uploads/computed-species")
    body = json.loads(recorder.last.content.decode())
    assert body == upload.to_payload()


def test_builder_upload_forwards_idempotency_key():
    client, recorder = make_client(_ok())
    upload = _make_water_upload()
    client.upload(upload, idempotency_key="builder:run-1:v0")
    assert recorder.last.headers.get("idempotency-key") == "builder:run-1:v0"


def test_raw_dict_form_still_works():
    """The legacy ``upload(endpoint, payload_dict)`` form is unchanged."""
    client, recorder = make_client(_ok())
    client.upload("thermo", {"hello": "world"})
    assert recorder.last.url.endswith("/uploads/thermo")
    assert json.loads(recorder.last.content.decode()) == {"hello": "world"}


def test_raw_dict_in_single_argument_form_raises():
    client, _ = make_client(_ok())
    with pytest.raises(TypeError):
        client.upload({"hello": "world"})


def test_builder_in_two_argument_form_raises():
    client, _ = make_client(_ok())
    upload = _make_water_upload()
    with pytest.raises(TypeError):
        client.upload(upload, {"hello": "world"})


def test_unknown_upload_kind_raises():
    client, _ = make_client(_ok())

    class Fake:
        upload_kind = "not_a_real_endpoint"

        def to_payload(self) -> dict:
            return {}

    with pytest.raises(TypeError):
        client.upload(Fake())


def test_object_missing_upload_kind_raises():
    client, _ = make_client(_ok())

    class Fake:
        def to_payload(self) -> dict:
            return {}

    with pytest.raises(TypeError):
        client.upload(Fake())


def _make_reaction_upload() -> ComputedReactionUpload:
    g = Geometry.from_xyz("3\nts\nC 0 0 0\nH 0 0 0.8\nH 0 0 -1.0")
    sr = SoftwareRelease(software="Gaussian", version="16")
    lot = LevelOfTheory(method="wb97xd", basis="def2tzvp")
    ts_opt = Calculation.opt(sr, lot, output_geometry=g, converged=True)
    ch3 = Species(smiles="[CH3]", charge=0, multiplicity=2)
    ch4 = Species(smiles="C", charge=0, multiplicity=1)
    kin = Kinetics.modified_arrhenius(
        A=1e10, A_units="per_s", n=0, Ea=0,
        source_calculations={"ts_energy": ts_opt},
    )
    rxn = ChemReaction(
        reactants=[ch3], products=[ch4],
        transition_state=TransitionState(charge=0, multiplicity=2, geometry=g),
        kinetics=[kin],
    )
    return ComputedReactionUpload(reaction=rxn, calculations=[ts_opt])


def test_computed_reaction_builder_dispatches_to_endpoint():
    client, recorder = make_client(_ok({"reaction_id": 11}))
    upload = _make_reaction_upload()
    result = client.upload(upload)
    assert result == {"reaction_id": 11}
    assert recorder.last.url.endswith("/uploads/computed-reaction")
    body = json.loads(recorder.last.content.decode())
    assert body == upload.to_payload()


def test_computed_reaction_builder_forwards_idempotency_key():
    client, recorder = make_client(_ok())
    upload = _make_reaction_upload()
    client.upload(upload, idempotency_key="rxn:run-1:v0:builder")
    assert recorder.last.headers.get("idempotency-key") == "rxn:run-1:v0:builder"


def test_to_payload_returning_non_dict_raises():
    client, _ = make_client(_ok())

    class Fake:
        upload_kind = "computed_species"

        def to_payload(self):
            return "not a dict"

    with pytest.raises(TypeError):
        client.upload(Fake())
