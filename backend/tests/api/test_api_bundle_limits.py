"""Caps and a rate bucket for the bundle routes (#586).

``/bundles/dry-run`` rehearses a full submit (#584), so an unbounded bundle,
or unlimited dry runs, would let one credential make the server do a full
import's work over and over. These tests pin:

* a record-count cap and a request-body cap, refused with a coded error on
  *both* routes, the body cap before the body is parsed;
* a dry-run rate bucket that is separate from ``auth_write`` in both
  directions;
* one INFO log line per dry run that names the user, the record count and
  the duration and never the payload.

Every refusal has a control: the same request under a cap large enough to
admit it succeeds, so none of these can pass by refusing everything.
"""

from __future__ import annotations

import copy
import logging
from collections.abc import Iterator

import pytest

from app.api.config import settings
from app.api.rate_limit import reset_rate_limit_store
from app.api.routes import bundles as bundles_route
from tests.api.test_api_bundle_dry_run_submit_parity import DRY_RUN, SUBMIT, _example

_SMILES = ["C", "CC", "CCC", "CCCC"]


def _thermo_bundle(count: int = 1) -> dict:
    bundle = _example("thermo-bundle-v0.json")
    template = bundle["records"]["thermo_uploads"][0]
    records = []
    for smiles in _SMILES[:count]:
        record = copy.deepcopy(template)
        record["species_entry"] = {"smiles": smiles, "charge": 0, "multiplicity": 1}
        records.append(record)
    bundle["records"]["thermo_uploads"] = records
    return bundle


# ---------------------------------------------------------------------------
# Record-count cap
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route", [DRY_RUN, SUBMIT], ids=["dry_run", "submit"])
def test_a_bundle_over_the_record_cap_is_refused_with_a_coded_error(
    client, monkeypatch, route
) -> None:
    monkeypatch.setattr(settings, "bundle_max_records", 2)
    resp = client.post(route, json=_thermo_bundle(3))
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["code"] == "bundle_too_many_records"
    assert body["context"] == {"max_records": 2, "records": 3}


@pytest.mark.parametrize(
    ("route", "status"), [(DRY_RUN, 200), (SUBMIT, 201)], ids=["dry_run", "submit"]
)
def test_a_bundle_at_the_record_cap_is_accepted(client, monkeypatch, route, status) -> None:
    monkeypatch.setattr(settings, "bundle_max_records", 2)
    resp = client.post(route, json=_thermo_bundle(2))
    assert resp.status_code == status, resp.text


def test_the_record_cap_counts_kinetics_records_too(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "bundle_max_records", 0)  # disabled: control
    bundle = _example("kinetics-bundle-v0.json")
    assert client.post(DRY_RUN, json=bundle).status_code == 200
    monkeypatch.setattr(settings, "bundle_max_records", 1)
    assert client.post(DRY_RUN, json=bundle).status_code == 200  # one record: at the cap
    bundle["records"]["kinetics_uploads"].append(
        copy.deepcopy(bundle["records"]["kinetics_uploads"][0])
    )
    resp = client.post(DRY_RUN, json=bundle)
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "bundle_too_many_records"


# ---------------------------------------------------------------------------
# Body-size cap, refused before the body is parsed
# ---------------------------------------------------------------------------

_BODY_CAP = 500


def _chunks(payload: bytes, size: int = 100) -> Iterator[bytes]:
    for start in range(0, len(payload), size):
        yield payload[start : start + size]


@pytest.fixture
def parse_tripwire(monkeypatch) -> None:
    """Fail loudly if a refused body ever reaches the route's own work."""

    def _reached(*_args, **_kwargs):
        raise AssertionError("the route ran for a body that should have been refused")

    monkeypatch.setattr(bundles_route, "dry_run_contribution_bundle", _reached)
    monkeypatch.setattr(bundles_route, "submit_contribution_bundle", _reached)


@pytest.mark.parametrize("route", [DRY_RUN, SUBMIT], ids=["dry_run", "submit"])
def test_a_declared_oversize_body_is_refused_413_before_parsing(
    client, monkeypatch, parse_tripwire, route
) -> None:
    monkeypatch.setattr(settings, "bundle_max_body_bytes", _BODY_CAP)
    # Not JSON at all: were the body parsed first, this would be a 422.
    payload = b"{ this is not json " + b"x" * 2 * _BODY_CAP
    resp = client.post(route, content=payload, headers={"content-type": "application/json"})
    assert resp.status_code == 413, resp.text
    body = resp.json()
    assert body["code"] == "bundle_too_large"
    assert body["context"] == {"max_bytes": _BODY_CAP, "given_bytes": len(payload)}


@pytest.mark.parametrize("route", [DRY_RUN, SUBMIT], ids=["dry_run", "submit"])
def test_a_chunked_oversize_body_is_refused_413_too(
    client, monkeypatch, parse_tripwire, route
) -> None:
    monkeypatch.setattr(settings, "bundle_max_body_bytes", _BODY_CAP)
    payload = b"{ this is not json " + b"x" * 2 * _BODY_CAP
    resp = client.post(
        route, content=_chunks(payload), headers={"content-type": "application/json"}
    )
    assert resp.status_code == 413, resp.text
    body = resp.json()
    assert body["code"] == "bundle_too_large"
    assert body["context"] == {"max_bytes": _BODY_CAP}  # no length was declared


@pytest.mark.parametrize(
    ("route", "status"), [(DRY_RUN, 200), (SUBMIT, 201)], ids=["dry_run", "submit"]
)
def test_a_body_under_the_cap_is_parsed_and_handled(client, route, status) -> None:
    # The control: the cap is the default (5 MiB), and the real bundle passes.
    resp = client.post(route, json=_thermo_bundle(1))
    assert resp.status_code == status, resp.text


def test_the_body_cap_applies_to_the_bundle_routes_only(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "bundle_max_body_bytes", _BODY_CAP)
    resp = client.post(
        "/api/v1/uploads/conformers",
        content=b"{}" + b" " * 2 * _BODY_CAP,
        headers={"content-type": "application/json"},
    )
    assert resp.status_code != 413, resp.text


# ---------------------------------------------------------------------------
# The dry-run rate bucket
# ---------------------------------------------------------------------------


@pytest.fixture
def rate_limited(monkeypatch) -> Iterator[None]:
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_bundle_dry_run_per_minute", 2)
    monkeypatch.setattr(settings, "rate_limit_auth_write_per_minute", 2)
    reset_rate_limit_store()
    yield
    reset_rate_limit_store()


_KEY = {"X-API-Key": "a-credential"}


def test_dry_runs_have_their_own_bucket_and_do_not_spend_auth_write(
    client, rate_limited
) -> None:
    bundle = _thermo_bundle(1)
    for _ in range(2):
        assert client.post(DRY_RUN, json=bundle, headers=_KEY).status_code == 200
    blocked = client.post(DRY_RUN, json=bundle, headers=_KEY)
    assert blocked.status_code == 429
    assert blocked.json()["bucket"] == "bundle_dry_run"
    # Two dry runs spent nothing of auth_write: both of its budget still remain.
    for _ in range(2):
        assert client.post(SUBMIT, json=_thermo_bundle(2), headers=_KEY).status_code != 429


def test_submits_do_not_spend_the_dry_run_bucket(client, rate_limited) -> None:
    for _ in range(2):
        assert client.post(SUBMIT, json=_thermo_bundle(3), headers=_KEY).status_code != 429
    over = client.post(SUBMIT, json=_thermo_bundle(3), headers=_KEY)
    assert over.status_code == 429
    assert over.json()["bucket"] == "auth_write"
    # auth_write is empty, and the dry run is still admitted.
    assert client.post(DRY_RUN, json=_thermo_bundle(1), headers=_KEY).status_code == 200


def test_the_dry_run_bucket_is_tighter_than_auth_write_by_default() -> None:
    assert settings.rate_limit_bundle_dry_run_per_minute < settings.rate_limit_auth_write_per_minute


# ---------------------------------------------------------------------------
# One INFO line per dry run
# ---------------------------------------------------------------------------


def _dry_run_lines(caplog) -> list[logging.LogRecord]:
    return [
        r
        for r in caplog.records
        if r.name == bundles_route.logger.name and r.getMessage().startswith("bundle dry run")
    ]


def test_each_dry_run_logs_one_info_line_without_the_payload(
    client, caplog, _api_test_user
) -> None:
    bundle = _thermo_bundle(2)
    with caplog.at_level(logging.INFO, logger=bundles_route.logger.name):
        assert client.post(DRY_RUN, json=bundle).status_code == 200
    lines = _dry_run_lines(caplog)
    assert len(lines) == 1, [r.getMessage() for r in lines]
    record = lines[0]
    assert record.levelno == logging.INFO
    message = record.getMessage()
    assert f"user={_api_test_user} " in message
    assert "records=2 " in message
    assert "duration_ms=" in message
    assert "outcome=accepted" in message
    # Nothing of the payload: not the title, not a species, not a number.
    for fragment in ("Methane", "summary", "smiles", "h298", "my-lab-tckdb"):
        assert fragment not in message


def test_a_refused_dry_run_is_logged_once_too(client, monkeypatch, caplog) -> None:
    monkeypatch.setattr(settings, "bundle_max_records", 1)
    with caplog.at_level(logging.INFO, logger=bundles_route.logger.name):
        assert client.post(DRY_RUN, json=_thermo_bundle(2)).status_code == 422
    lines = _dry_run_lines(caplog)
    assert len(lines) == 1
    assert "records=2 " in lines[0].getMessage()
    assert "outcome=over_cap" in lines[0].getMessage()
