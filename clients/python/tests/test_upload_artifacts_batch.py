"""Tests for ``client.upload_artifacts(..., batch_by_calculation=True)``.

Pinned contract:

- The default remains one POST per artifact (existing tests in
  ``test_builder_artifacts.py`` cover the legacy path; one
  default-mode regression assertion lives here too).
- Batch mode groups planned items by ``calculation_id``, preserves
  caller order within each group, sends one POST per group to
  ``/calculations/{id}/artifacts``, and returns one
  :class:`ArtifactUploadBatchResult` per group.
- Pre-dispatch validation is exhaustive: any malformed item — bad
  ``calculation_id``, missing path, non-file path, empty ``kind`` —
  raises before the first HTTP request fires.
- Idempotency keys in batch mode are
  ``{prefix}:{first_calculation_key}:artifact-batch`` (one per
  batch, deterministic across runs).
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx
import pytest

from tckdb_client import ArtifactUploadBatchResult
from tckdb_client.builders import PlannedArtifactUpload

from conftest import make_client  # type: ignore[import-not-found]


# ---------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------


@pytest.fixture
def files(tmp_path: Path) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for name in ("opt.log", "freq.log", "sp.log", "scan.log"):
        p = tmp_path / name
        p.write_bytes(f"contents of {name}\n".encode())
        paths[name] = p
    return paths


def _plan_item(*, key: str, calc_id: int, path: Path, kind: str = "output_log"):
    return PlannedArtifactUpload(
        calculation_key=key,
        calculation_id=calc_id,
        path=path,
        kind=kind,
        label=None,
        sha256=None,
        bytes=None,
    )


def _batch_handler(captured: list):
    """httpx MockTransport handler that records every POST as a dict
    and replies with a synthetic 201 ArtifactsUploadResult body."""
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        url = str(request.url)
        calc_id = int(url.rsplit("/", 2)[-2])
        captured.append({
            "url": url,
            "calc_id": calc_id,
            "artifacts": body["artifacts"],
            "idem": request.headers.get("idempotency-key", ""),
        })
        return httpx.Response(
            201,
            json={
                "calculation_id": calc_id,
                "artifacts": [
                    {"id": i, "kind": a["kind"], "filename": a["filename"]}
                    for i, a in enumerate(body["artifacts"], start=1)
                ],
                "warnings": [],
            },
        )

    return handler


# =====================================================================
# Default mode regression — one POST per artifact, list-of-responses.
# =====================================================================


def test_default_mode_still_one_post_per_artifact(files):
    captured: list = []
    client, _ = make_client(_batch_handler(captured))
    plan = [
        _plan_item(key="a", calc_id=10, path=files["opt.log"]),
        _plan_item(key="b", calc_id=10, path=files["freq.log"]),
        _plan_item(key="c", calc_id=20, path=files["sp.log"]),
    ]
    results = client.upload_artifacts(plan)
    # 3 plan items → 3 POSTs by default.
    assert len(captured) == 3
    # Per-artifact responses, not batch results.
    assert all(isinstance(r, dict) for r in results)
    assert len(results) == 3


# =====================================================================
# Batch mode — grouping, order, single POST per calculation_id.
# =====================================================================


def test_batch_mode_groups_by_calculation_id(files):
    captured: list = []
    client, _ = make_client(_batch_handler(captured))
    plan = [
        _plan_item(key="a", calc_id=10, path=files["opt.log"]),
        _plan_item(key="b", calc_id=10, path=files["freq.log"]),
        _plan_item(key="c", calc_id=20, path=files["sp.log"]),
        _plan_item(key="d", calc_id=10, path=files["scan.log"]),
    ]
    results = client.upload_artifacts(plan, batch_by_calculation=True)
    # Two distinct calculation_ids → two POSTs.
    assert [c["calc_id"] for c in captured] == [10, 20]
    # Calc 10's POST holds opt + freq + scan (caller-order preserved).
    assert [a["filename"] for a in captured[0]["artifacts"]] == [
        "opt.log", "freq.log", "scan.log",
    ]
    # Calc 20's POST holds the lone sp entry.
    assert [a["filename"] for a in captured[1]["artifacts"]] == ["sp.log"]
    # One ArtifactUploadBatchResult per group, in group order.
    assert [r.calculation_id for r in results] == [10, 20]
    assert [r.artifact_count for r in results] == [3, 1]


def test_batch_mode_preserves_caller_order_within_group(files):
    captured: list = []
    client, _ = make_client(_batch_handler(captured))
    plan = [
        _plan_item(key="freq", calc_id=42, path=files["freq.log"]),
        _plan_item(key="opt",  calc_id=42, path=files["opt.log"]),
        _plan_item(key="sp",   calc_id=42, path=files["sp.log"]),
    ]
    client.upload_artifacts(plan, batch_by_calculation=True)
    # Caller order — freq, opt, sp — must survive batching.
    assert [a["filename"] for a in captured[0]["artifacts"]] == [
        "freq.log", "opt.log", "sp.log",
    ]


def test_batch_mode_single_artifact_still_works(files):
    captured: list = []
    client, _ = make_client(_batch_handler(captured))
    plan = [_plan_item(key="only", calc_id=99, path=files["opt.log"])]
    results = client.upload_artifacts(plan, batch_by_calculation=True)
    assert len(captured) == 1
    assert captured[0]["calc_id"] == 99
    assert len(captured[0]["artifacts"]) == 1
    assert len(results) == 1
    assert results[0].calculation_id == 99
    assert results[0].artifact_count == 1
    assert results[0].calculation_keys == ("only",)


def test_batch_mode_mixed_ids_produce_multiple_batches(files):
    captured: list = []
    client, _ = make_client(_batch_handler(captured))
    plan = [
        _plan_item(key="a1", calc_id=1, path=files["opt.log"]),
        _plan_item(key="b1", calc_id=2, path=files["freq.log"]),
        _plan_item(key="c1", calc_id=3, path=files["sp.log"]),
    ]
    results = client.upload_artifacts(plan, batch_by_calculation=True)
    # Three distinct ids → three POSTs.
    assert [c["calc_id"] for c in captured] == [1, 2, 3]
    assert all(len(c["artifacts"]) == 1 for c in captured)
    assert len(results) == 3


def test_batch_mode_empty_plan_no_http(files):
    captured: list = []
    client, _ = make_client(_batch_handler(captured))
    results = client.upload_artifacts([], batch_by_calculation=True)
    assert results == []
    assert captured == []


# =====================================================================
# Batch mode — return shape.
# =====================================================================


def test_batch_mode_returns_documented_result_shape(files):
    captured: list = []
    client, _ = make_client(_batch_handler(captured))
    plan = [
        _plan_item(key="a", calc_id=10, path=files["opt.log"]),
        _plan_item(key="b", calc_id=10, path=files["freq.log"]),
    ]
    results = client.upload_artifacts(plan, batch_by_calculation=True)
    assert len(results) == 1
    r = results[0]
    assert isinstance(r, ArtifactUploadBatchResult)
    assert r.calculation_id == 10
    assert r.calculation_keys == ("a", "b")
    assert r.artifact_count == 2
    assert isinstance(r.response, dict)
    # The synthetic response is forwarded verbatim, so the caller can
    # introspect it without a re-walk of the server reply.
    assert r.response["calculation_id"] == 10


def test_batch_result_is_frozen(files):
    captured: list = []
    client, _ = make_client(_batch_handler(captured))
    plan = [_plan_item(key="a", calc_id=10, path=files["opt.log"])]
    results = client.upload_artifacts(plan, batch_by_calculation=True)
    with pytest.raises(Exception):  # FrozenInstanceError → AttributeError
        results[0].calculation_id = 999  # type: ignore[misc]


# =====================================================================
# Pre-dispatch validation — every error raises *before* HTTP.
# =====================================================================


def test_batch_mode_validates_all_paths_before_any_http(files, tmp_path):
    captured: list = []
    client, _ = make_client(_batch_handler(captured))
    bad_path = tmp_path / "missing.log"  # never written
    plan = [
        _plan_item(key="a", calc_id=10, path=files["opt.log"]),
        _plan_item(key="b", calc_id=10, path=bad_path),  # bad item
        _plan_item(key="c", calc_id=10, path=files["sp.log"]),
    ]
    with pytest.raises(ValueError, match="does not exist"):
        client.upload_artifacts(plan, batch_by_calculation=True)
    # Critically: no HTTP request fired because validation is up front.
    assert captured == []


def test_batch_mode_raises_before_dispatch_on_missing_path(files, tmp_path):
    captured: list = []
    client, _ = make_client(_batch_handler(captured))
    plan = [
        _plan_item(
            key="a", calc_id=10, path=tmp_path / "nope.log",
        ),
    ]
    with pytest.raises(ValueError, match="does not exist"):
        client.upload_artifacts(plan, batch_by_calculation=True)
    assert captured == []


def test_batch_mode_raises_on_directory_path(files, tmp_path):
    captured: list = []
    client, _ = make_client(_batch_handler(captured))
    plan = [_plan_item(key="a", calc_id=10, path=tmp_path)]
    with pytest.raises(ValueError, match="not a regular file"):
        client.upload_artifacts(plan, batch_by_calculation=True)
    assert captured == []


def test_batch_mode_rejects_bad_calculation_id(files):
    captured: list = []
    client, _ = make_client(_batch_handler(captured))

    class BadItem:
        calculation_id = "ten"  # not an int
        path = files["opt.log"]
        kind = "output_log"
        calculation_key = "a"

    with pytest.raises(TypeError, match="calculation_id"):
        client.upload_artifacts([BadItem()], batch_by_calculation=True)
    assert captured == []


def test_batch_mode_rejects_bool_calculation_id(files):
    """Pre-dispatch validation must reject ``bool`` masquerading as
    ``int`` (Python's ``isinstance(True, int)`` is True). Bools are
    almost certainly accidents in this position."""
    captured: list = []
    client, _ = make_client(_batch_handler(captured))

    class BadItem:
        calculation_id = True
        path = files["opt.log"]
        kind = "output_log"
        calculation_key = "a"

    with pytest.raises(TypeError, match="calculation_id"):
        client.upload_artifacts([BadItem()], batch_by_calculation=True)


def test_batch_mode_rejects_empty_kind(files):
    captured: list = []
    client, _ = make_client(_batch_handler(captured))

    class BadItem:
        calculation_id = 10
        path = files["opt.log"]
        kind = ""  # empty
        calculation_key = "a"

    with pytest.raises(ValueError, match="kind"):
        client.upload_artifacts([BadItem()], batch_by_calculation=True)
    assert captured == []


def test_default_mode_also_validates_pre_dispatch(files, tmp_path):
    """Pre-dispatch validation applies to both modes; this guard is the
    safety property that was previously only enforced inside the
    per-artifact loop."""
    captured: list = []
    client, _ = make_client(_batch_handler(captured))
    plan = [
        _plan_item(key="a", calc_id=10, path=files["opt.log"]),
        _plan_item(key="b", calc_id=10, path=tmp_path / "missing.log"),
    ]
    with pytest.raises(ValueError, match="does not exist"):
        client.upload_artifacts(plan)
    # No POST fired — *neither* artifact uploaded — because validation
    # is up front, not interleaved.
    assert captured == []


# =====================================================================
# Idempotency keys — deterministic per batch, one per group.
# =====================================================================


def test_batch_mode_idempotency_keys_are_per_batch(files):
    captured: list = []
    client, _ = make_client(_batch_handler(captured))
    plan = [
        _plan_item(key="opt_key",  calc_id=10, path=files["opt.log"]),
        _plan_item(key="freq_key", calc_id=10, path=files["freq.log"]),
        _plan_item(key="sp_key",   calc_id=20, path=files["sp.log"]),
    ]
    client.upload_artifacts(
        plan, idempotency_key_prefix="run-A",
        batch_by_calculation=True,
    )
    # One idempotency key per batch POST.
    assert len(captured) == 2
    # First-calc-key is the seed, suffix is ``artifact-batch``.
    assert captured[0]["idem"] == "run-A:opt_key:artifact-batch"
    assert captured[1]["idem"] == "run-A:sp_key:artifact-batch"


def test_batch_mode_idempotency_keys_are_deterministic(files):
    """Same plan, same prefix → same keys across runs. The deterministic
    property is the whole point of the second-phase idempotency
    contract."""
    captured1: list = []
    client1, _ = make_client(_batch_handler(captured1))
    captured2: list = []
    client2, _ = make_client(_batch_handler(captured2))
    plan_factory = lambda: [
        _plan_item(key="a", calc_id=1, path=files["opt.log"]),
        _plan_item(key="b", calc_id=1, path=files["freq.log"]),
        _plan_item(key="c", calc_id=2, path=files["sp.log"]),
    ]
    client1.upload_artifacts(
        plan_factory(), idempotency_key_prefix="adapter-x:run-7",
        batch_by_calculation=True,
    )
    client2.upload_artifacts(
        plan_factory(), idempotency_key_prefix="adapter-x:run-7",
        batch_by_calculation=True,
    )
    assert [c["idem"] for c in captured1] == [c["idem"] for c in captured2]


def test_batch_mode_no_idempotency_prefix_no_header(files):
    captured: list = []
    client, _ = make_client(_batch_handler(captured))
    plan = [_plan_item(key="a", calc_id=10, path=files["opt.log"])]
    client.upload_artifacts(plan, batch_by_calculation=True)
    # No prefix supplied → no idempotency header.
    assert captured[0]["idem"] == ""


# =====================================================================
# Wire shape — content_base64 round-trips through the batch.
# =====================================================================


def test_batch_mode_payload_round_trips_content(files):
    captured: list = []
    client, _ = make_client(_batch_handler(captured))
    plan = [
        _plan_item(key="a", calc_id=10, path=files["opt.log"]),
        _plan_item(key="b", calc_id=10, path=files["freq.log"]),
    ]
    client.upload_artifacts(plan, batch_by_calculation=True)
    arts = captured[0]["artifacts"]
    assert [a["filename"] for a in arts] == ["opt.log", "freq.log"]
    for art, src in zip(arts, ["opt.log", "freq.log"]):
        decoded = base64.b64decode(art["content_base64"])
        assert decoded == files[src].read_bytes()
