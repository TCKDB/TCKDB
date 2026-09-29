"""Every example the producer contract publishes is accepted by the real server.

``generate_producer_contract.py`` validates each example against its Pydantic
model, and that is not enough: a ThermoML example with an empty
``<DataReport/>`` passed ``model_validate`` and was refused by the route with
``thermoml_schema_invalid``, because the XSD, the importer and the workflow
all run after the model. An adapter author copies these examples, so each one
is POSTed here through the live app, against the test database, to every
producer route that takes it, and must come back 2xx.

An example that cannot be accepted on its own -- its route names an existing
record in the path -- must say so on the model
(``json_schema_extra["x-tckdb-example-requires"]``), and the contract prints
it as "shape only". Two of those are also exercised by first creating the
record they need, so "shape only" is not a place for a broken example to hide.
"""

from __future__ import annotations

import importlib.util
import itertools
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]
GENERATOR_PATH = BACKEND_ROOT / "scripts" / "generate_producer_contract.py"


def _load_generator():
    name = "generate_producer_contract"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, GENERATOR_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


generator = _load_generator()
ROUTES = [info for info in generator.discover_routes() if info.category == "producer"]
_KEYS = itertools.count()


def _headers() -> dict[str, str]:
    # Every route accepts an Idempotency-Key and /uploads/thermoml requires
    # one; a fresh key per request keeps each POST an independent deposit.
    return {"Idempotency-Key": f"producer-contract-example-{next(_KEYS):06d}"}


def _example(model) -> dict:
    return generator.checked_example(model)


def _body_model(info):
    (model,) = info.body_models
    return model


def _needs_existing_record(info) -> bool:
    return bool(info.route.dependant.path_params)


STANDALONE = [info for info in ROUTES if not _needs_existing_record(info)]
SHAPE_ONLY = [info for info in ROUTES if _needs_existing_record(info)]


def test_the_walk_found_the_routes() -> None:
    """Guard the parametrisation below against an empty walk."""
    assert len(STANDALONE) >= 20, [info.label for info in STANDALONE]
    assert {"POST /api/v1/uploads/thermoml", "POST /api/v1/bundles/submit"} <= {i.label for i in STANDALONE}
    assert len(SHAPE_ONLY) >= 3, [info.label for info in SHAPE_ONLY]


@pytest.mark.parametrize("info", STANDALONE, ids=lambda info: info.label)
def test_every_standalone_example_is_accepted(client, info) -> None:
    model = _body_model(info)
    assert generator.example_requires(model) is None, (
        f"{model.__name__} is labelled shape-only but {info.label} needs no existing record"
    )
    response = client.post(info.path, json=_example(model), headers=_headers())
    assert 200 <= response.status_code < 300, (info.label, response.status_code, response.text[:2000])


@pytest.mark.parametrize("info", SHAPE_ONLY, ids=lambda info: info.label)
def test_an_example_whose_route_names_a_record_is_labelled_shape_only(info) -> None:
    requires = generator.example_requires(_body_model(info))
    assert requires and len(requires.split()) >= 5, (
        f"{info.label} takes a path parameter, so its example cannot stand alone; "
        "say what it needs in json_schema_extra['x-tckdb-example-requires']"
    )


def _conformer_upload(client) -> dict:
    info = next(i for i in ROUTES if i.label == "POST /api/v1/uploads/conformers")
    response = client.post(info.path, json=_example(_body_model(info)), headers=_headers())
    assert response.status_code == 201, response.text
    return response.json()


def test_the_artifact_example_is_accepted_on_a_calculation_the_caller_deposited(client) -> None:
    deposit = _conformer_upload(client)
    calculation_id = deposit["primary_calculation"]["calculation_id"]
    info = next(i for i in ROUTES if i.path.endswith("/artifacts"))
    response = client.post(
        info.path.format(calculation_id=calculation_id),
        json=_example(_body_model(info)),
        headers=_headers(),
    )
    assert response.status_code == 201, response.text


def test_the_rights_attestation_example_is_accepted_on_the_callers_submission(client) -> None:
    deposit = _conformer_upload(client)
    submission_id = deposit["submission_id"]
    assert submission_id is not None
    info = next(i for i in ROUTES if i.path.endswith("/rights-attestations"))
    response = client.post(
        info.path.format(submission_id=submission_id),
        json=_example(_body_model(info)),
        headers=_headers(),
    )
    assert 200 <= response.status_code < 300, response.text


def test_a_refused_example_turns_this_red(client) -> None:
    """Mutation: the ThermoML example this file was written for, as it first shipped.

    An empty ``<DataReport/>`` validates against the model and is refused by
    the route; the acceptance check must see that.
    """
    import base64

    info = next(i for i in ROUTES if i.label == "POST /api/v1/uploads/thermoml")
    broken = dict(_example(_body_model(info)))
    broken["content_base64"] = base64.b64encode(
        b'<?xml version="1.0" encoding="UTF-8"?>\n<DataReport xmlns="http://www.iupac.org/namespaces/ThermoML"/>\n'
    ).decode("ascii")
    _body_model(info).model_validate(broken)
    response = client.post(info.path, json=broken, headers=_headers())
    assert not 200 <= response.status_code < 300
    assert response.json()["code"] == "thermoml_schema_invalid"
