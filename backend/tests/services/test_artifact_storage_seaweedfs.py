"""SeaweedFS capacity: the arithmetic, the parsing, and the "no opinion" contract.

Every payload here is a **recording**, not a hand-written shape. The files in
``tests/fixtures/seaweedfs_4_47/`` are the verbatim bodies of the master's
``GET /`` and the volume server's ``GET /status``, captured from
``chrislusf/seaweedfs@sha256:ce9e796f...`` (release 4.47, ``weed mini``, the
digest ``docker-compose.yml`` pins) while filling it through signed S3 with
4 MiB objects on size-capped tmpfs (#545):

``empty``
    256 MiB disk, nothing written. ``Max: 3`` slots of a ``64MB`` volume
    size limit, all free.
``slots_nearly_full``
    The same store at 160 MiB written: ``Free: 0`` slots, two of the
    bucket's volumes at the limit and the third at 32 MiB. Every write still
    succeeded; this is the state the headroom warning exists to catch.
``slots_exhausted``
    The same store at the instant the write at 192 MiB was refused
    (``InternalError``/500): ``Free: 0``, all three volumes at 67,109,056
    bytes, and **66,736,128 bytes of disk still free**.
``disk_full``
    128 MiB disk with ``-volume.max=10``: the write at 124 MiB was refused
    with ``Free: 7`` slots and 3,833,856 bytes of disk free, less than the
    4,194,304 being written.
``below_min_free``
    1 GiB disk with ``-volume.max=100``, filled to 0.77 % free (8,265,728
    bytes) and held past ``weed mini``'s 60 s minimum-free check: every
    volume ``ReadOnly``, the master's ``Max`` down from 100 to 16 with
    ``Free: 0``, and both a 4 MiB and a 1 KiB write refused. Recorded after
    the review of #547 found this regime blamed on slots.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app.services import artifact_storage
from app.services import artifact_storage_seaweedfs as seaweedfs

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "seaweedfs_4_47"
BUCKET = "tckdb-artifacts"
MIB = 1024 * 1024
LIMIT = 64 * MIB
#: The size of each refused write in the recordings.
REFUSED_WRITE = 4 * MIB


def _recorded(name: str) -> tuple[str, dict]:
    page = (FIXTURES / f"{name}.master.html").read_text()
    status = json.loads((FIXTURES / f"{name}.volume_status.json").read_text())
    return page, status


def _capacity(name: str) -> seaweedfs.SeaweedCapacity:
    page, status = _recorded(name)
    capacity = seaweedfs.capacity_from_status(
        master_page=page, volume_status=status, bucket=BUCKET
    )
    assert capacity is not None, name
    return capacity


# ---------------------------------------------------------------------------
# The recorded states
# ---------------------------------------------------------------------------


def test_the_master_page_reads_as_limit_free_and_max() -> None:
    page, _ = _recorded("empty")
    assert seaweedfs.parse_master_page(page) == (LIMIT, 3, 3)
    page, _ = _recorded("disk_full")
    assert seaweedfs.parse_master_page(page) == (LIMIT, 7, 10)


def _reserve(total: int) -> int:
    """weed mini's 1 % minimum free, rounded up."""
    return -(-total // 100)


def test_an_empty_store_has_room_in_both_arms() -> None:
    capacity = _capacity("empty")
    assert capacity.disk_free_bytes == 268_120_064
    assert capacity.disk_room_bytes == 268_120_064 - _reserve(268_435_456)
    assert capacity.below_min_free is False
    assert capacity.slot_room_bytes == 3 * LIMIT
    assert capacity.room_bytes == 3 * LIMIT
    assert not seaweedfs.refusal_is_full(capacity, REFUSED_WRITE)


def test_exhausted_slots_are_full_with_disk_to_spare() -> None:
    """The case disk free space alone gets wrong.

    Measured: refused with 63.6 MiB of disk free. Without the slot arm this
    store reads as having room for fifteen more 4 MiB writes.
    """
    capacity = _capacity("slots_exhausted")
    assert capacity.disk_free_bytes == 66_736_128
    assert capacity.free_slots == 0
    assert capacity.slot_room_bytes == 0
    assert capacity.room_bytes == 0
    assert capacity.limiting_arm == "volume_slots"
    assert seaweedfs.refusal_is_full(capacity, REFUSED_WRITE)
    # Also with no size known: no room at all explains any refusal.
    assert seaweedfs.refusal_is_full(capacity, None)


def test_a_full_disk_is_full_with_slots_to_spare() -> None:
    capacity = _capacity("disk_full")
    assert capacity.free_slots == 7
    assert capacity.disk_free_bytes == 3_833_856
    # 1 % of 128 MiB is kept free, so usable room is smaller than "free".
    assert capacity.disk_room_bytes == 3_833_856 - _reserve(134_217_728)
    assert capacity.below_min_free is False
    assert capacity.slot_room_bytes > capacity.disk_room_bytes
    assert capacity.limiting_arm == "free_space"
    assert capacity.room_bytes == capacity.disk_room_bytes
    assert seaweedfs.refusal_is_full(capacity, REFUSED_WRITE)
    # Some bytes are usable, so a refusal of unknown size is not explained.
    assert not seaweedfs.refusal_is_full(capacity, None)


def test_below_the_one_percent_minimum_is_full_and_blamed_on_disk() -> None:
    """The regime that matters on a real disk, and the one that was misread.

    Measured: 8.3 MB free on 1 GiB, every volume read-only, 1 KiB refused.
    The master's slot count collapses at the same moment (``Max`` 100 -> 16,
    ``Free`` 0), so the slot arm reads 0 too. Blaming slots would send an
    operator to raise ``-volume.max``, which does nothing here.
    """
    capacity = _capacity("below_min_free")
    assert capacity.disk_free_bytes == 8_265_728
    assert capacity.below_min_free is True
    assert capacity.disk_room_bytes == 0
    assert (capacity.free_slots, capacity.max_slots) == (0, 16)
    assert capacity.slot_room_bytes == 0
    assert capacity.room_bytes == 0
    assert capacity.limiting_arm == "free_space"
    assert seaweedfs.refusal_is_full(capacity, 1024)
    assert seaweedfs.refusal_is_full(capacity, None)


def test_a_large_disk_is_full_with_gigabytes_still_free() -> None:
    """500 GB at 0.9 % free: 4.5 GB "free" and no room at all."""
    page, _ = _recorded("empty")
    total = 500 * 10**9
    status = {
        "DiskStatuses": [{"all": total, "free": 4_500_000_000, "percent_free": 0.9}],
        "Volumes": [],
    }
    capacity = seaweedfs.capacity_from_status(
        master_page=page, volume_status=status, bucket=BUCKET
    )
    assert capacity is not None
    assert capacity.below_min_free is True
    assert capacity.disk_room_bytes == 0
    assert seaweedfs.refusal_is_full(capacity, REFUSED_WRITE)


def test_a_nearly_full_store_warns_but_does_not_explain_a_small_refusal() -> None:
    """Before the fill: low headroom, and a 4 MiB refusal still unexplained.

    Free slots are already zero here, which is also true of a *healthy*
    store whose bucket has grown its volumes in advance -- measured on an
    empty store after one write. So ``Free == 0`` is never "full" by
    itself; the room left inside the bucket's writable volumes decides.
    """
    capacity = _capacity("slots_nearly_full")
    assert capacity.free_slots == 0
    assert capacity.slot_room_bytes == LIMIT - 33_554_528
    assert capacity.room_bytes < artifact_storage.MAX_ARTIFACT_BYTES
    assert not seaweedfs.refusal_is_full(capacity, REFUSED_WRITE)


def test_other_collections_and_read_only_volumes_contribute_no_room() -> None:
    page, status = _recorded("empty")
    status = dict(status)
    status["Volumes"] = [
        {"Collection": "someone-else", "Size": 0, "ReadOnly": False},
        {"Collection": BUCKET, "Size": 0, "ReadOnly": True},
        {"Collection": BUCKET, "Size": LIMIT - 10, "ReadOnly": False},
    ]
    capacity = seaweedfs.capacity_from_status(
        master_page=page, volume_status=status, bucket=BUCKET
    )
    assert capacity is not None
    assert capacity.slot_room_bytes == 3 * LIMIT + 10


# ---------------------------------------------------------------------------
# Unfamiliar shapes are no opinion, never a guess
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mangle",
    [
        lambda html: html.replace("<th>Free</th>", "<th>Spare</th>"),
        lambda html: html.replace("<th>Max</th>", "<th>Most</th>"),
        lambda html: html.replace("<td>64MB</td>", "<td>64GB</td>"),
        lambda html: html.replace("<th>Volume Size Limit</th>", ""),
        lambda html: "",
    ],
    ids=["no-free", "no-max", "unknown-unit", "no-limit", "empty"],
)
def test_a_master_page_it_half_understands_is_no_opinion(mangle) -> None:
    page, status = _recorded("empty")
    assert seaweedfs.parse_master_page(mangle(page)) is None
    assert (
        seaweedfs.capacity_from_status(
            master_page=mangle(page), volume_status=status, bucket=BUCKET
        )
        is None
    )


@pytest.mark.parametrize(
    "status",
    [
        None,
        [],
        {},
        {"DiskStatuses": []},
        {"DiskStatuses": [{"dir": "/data"}]},
        {"DiskStatuses": [{"free": True, "all": 100}]},
        {"DiskStatuses": [{"free": 10}]},
        {"DiskStatuses": [{"free": 10, "all": 100}], "Volumes": [{"Collection": BUCKET, "Size": "big"}]},
    ],
    ids=["none", "list", "empty", "no-disks", "no-free", "bool-free", "no-all", "str-size"],
)
def test_a_volume_status_it_does_not_recognise_is_no_opinion(status) -> None:
    page, _ = _recorded("empty")
    assert (
        seaweedfs.capacity_from_status(master_page=page, volume_status=status, bucket=BUCKET)
        is None
    )


def test_no_opinion_never_classifies_a_refusal() -> None:
    assert seaweedfs.refusal_is_full(None, REFUSED_WRITE) is False
    assert seaweedfs.refusal_is_full(None, None) is False


# ---------------------------------------------------------------------------
# The address derivation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("master_url", "expected"),
    [
        ("http://seaweedfs:9333", "http://seaweedfs:9340/status"),
        ("http://seaweedfs:9333/", "http://seaweedfs:9340/status"),
        ("http://127.0.0.1:9333", "http://127.0.0.1:9340/status"),
        ("https://store.example:443", "https://store.example:9340/status"),
        ("http://[::1]:9333", "http://[::1]:9340/status"),
        ("seaweedfs:9333", None),
        ("", None),
        ("ftp://seaweedfs:9333", None),
    ],
)
def test_the_volume_server_is_the_master_host_on_9340(master_url, expected) -> None:
    assert seaweedfs.volume_status_url(master_url) == expected


# ---------------------------------------------------------------------------
# The client: bounded, and every failure is None
# ---------------------------------------------------------------------------


def _serve(monkeypatch, answers: dict):
    """Script ``_get``: URL -> bytes, or an exception to raise."""
    calls: list[tuple[str, float]] = []

    def _get(url, timeout):
        calls.append((url, timeout))
        answer = answers[url]
        if isinstance(answer, BaseException):
            raise answer
        return answer

    monkeypatch.setattr(seaweedfs, "_get", _get)
    return calls


def _recorded_answers(name: str) -> dict:
    return {
        "http://seaweedfs:9333/": (FIXTURES / f"{name}.master.html").read_bytes(),
        "http://seaweedfs:9340/status": (FIXTURES / f"{name}.volume_status.json").read_bytes(),
    }


def test_the_client_asks_both_servers_with_a_short_timeout(monkeypatch) -> None:
    calls = _serve(monkeypatch, _recorded_answers("slots_exhausted"))
    capacity = seaweedfs.report_capacity(master_url="http://seaweedfs:9333", bucket=BUCKET)
    assert capacity is not None and capacity.room_bytes == 0
    assert sorted(url for url, _ in calls) == [
        "http://seaweedfs:9333/",
        "http://seaweedfs:9340/status",
    ]
    assert all(timeout <= 2.0 for _, timeout in calls)
    assert seaweedfs._TIMEOUT_SECONDS <= 2.0


def test_unset_means_no_request_at_all(monkeypatch) -> None:
    calls = _serve(monkeypatch, {})
    assert seaweedfs.report_capacity(master_url="", bucket=BUCKET) is None
    assert calls == []


@pytest.mark.parametrize(
    "failure",
    [
        # Measured: what /dir/status answers once the guard whitelist is on.
        # The master page and /status do not, but a future image might.
        urllib.error.HTTPError("http://seaweedfs:9333/", 401, "Unauthorized", {}, None),
        urllib.error.URLError("connection refused"),
        TimeoutError("timed out"),
        # Not a failure any client library documents -- the point is that
        # an unanticipated one is absorbed too.
        RuntimeError("something nobody anticipated"),
    ],
    ids=["http-401", "refused", "timeout", "unanticipated"],
)
def test_every_failure_is_no_opinion_and_nothing_raises(monkeypatch, failure) -> None:
    answers = _recorded_answers("slots_exhausted")
    answers["http://seaweedfs:9333/"] = failure
    _serve(monkeypatch, answers)
    assert seaweedfs.report_capacity(master_url="http://seaweedfs:9333", bucket=BUCKET) is None


def test_a_volume_status_that_is_not_json_is_no_opinion(monkeypatch) -> None:
    answers = _recorded_answers("empty")
    answers["http://seaweedfs:9340/status"] = b"<html>not json</html>"
    _serve(monkeypatch, answers)
    assert seaweedfs.report_capacity(master_url="http://seaweedfs:9333", bucket=BUCKET) is None


class _Drip(BaseHTTPRequestHandler):
    """Headers at once, then one body byte every 0.4 s, forever-ish."""

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", "1000")
        self.end_headers()
        try:
            for _ in range(1000):
                self.wfile.write(b"x")
                self.wfile.flush()
                time.sleep(0.4)
        except OSError:
            pass

    def log_message(self, *_args):
        pass


def test_a_dripping_server_cannot_hold_the_probe_past_its_deadline() -> None:
    """Each byte arrives inside the socket timeout, so only a deadline on the
    whole probe bounds it. Measured before the deadline: 9 s held."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Drip)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        started = time.monotonic()
        answer = seaweedfs.report_capacity(
            master_url=f"http://127.0.0.1:{server.server_address[1]}",
            bucket=BUCKET,
            timeout=1.0,
            deadline=1.5,
        )
        elapsed = time.monotonic() - started
    finally:
        server.shutdown()
        server.server_close()
    assert answer is None
    assert elapsed < 2.5, elapsed
    assert seaweedfs._DEADLINE_SECONDS <= 2 * seaweedfs._TIMEOUT_SECONDS
