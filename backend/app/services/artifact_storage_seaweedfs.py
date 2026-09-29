"""Ask a SeaweedFS store how much room it has left, without writing anything.

Why this exists
---------------
SeaweedFS answers a full store with ``InternalError`` at HTTP 500, the same
code it gives any internal fault (measured against 4.47, #541). The S3 answer
alone therefore cannot say "full", so :mod:`app.services.artifact_storage`
leaves ``InternalError`` unclassified. This module is the second opinion the
write path asks for **after** a write was refused, and the number the
``/status`` headroom warning reads before one is. It is the SeaweedFS
counterpart of :mod:`app.services.artifact_storage_admin`, which does the
same for MinIO, and it follows that module's contract: every failure is
``None``, meaning *no opinion*, and nothing here raises.

It is off unless ``S3_SEAWEEDFS_MASTER_URL`` is set. Unset, every function
here answers ``None`` without making a request, which is exactly the
behaviour before this module existed, so AWS S3, GCS and MinIO deployments
are unaffected.

Three ways to run out of room, all measured
-------------------------------------------
Against ``chrislusf/seaweedfs:4.47`` (``weed mini``) on size-capped tmpfs
volumes, writing 4 MiB objects through signed S3 until one was refused:

**Volume slots.** 256 MiB disk: the volume server allowed ``Max: 3`` volumes
of the 64 MiB volume size limit. The write at 192 MiB was refused
(``InternalError``/500) with **66,736,128 bytes (63.6 MiB) of disk still
free**. At that instant the master reported ``Free: 0`` slots and all three
of the bucket's volumes read ``Size: 67109056``, just over the 67,108,864-byte
limit. Disk free space alone would have called this store healthy.

**Disk.** 128 MiB disk with ``-volume.max=10`` (slots above what the disk
holds): the write at 124 MiB was refused with ``Free: 7`` slots and
**3,833,856 bytes** of disk free, less than the 4 MiB being written. A
following 1 MiB write was refused with 684,032 bytes free. On a disk this
small the write that does not fit arrives before the next regime does.

**Below 1 % free.** ``weed mini`` hard-codes a minimum free space of 1 % of
the disk and checks it every 60 s. Measured on a 1 GiB disk filled to
0.77 % free (8,265,728 bytes): within 75 s **every volume turned
``ReadOnly``**, the master's ``Max`` dropped from 100 to the 16 volumes in
use so ``Free`` read 0, and both a 4 MiB and a 1 KiB write were refused. So
``ReadOnly`` *is* a signal, of this regime; and because the slot numbers
collapse with it, they would blame slots for what is a disk problem. On a
large disk this is the regime that matters: a 500 GB disk stops taking
writes with about 5 GB still free.

So room is the smaller of two numbers, and a refusal is "full" when that
room is smaller than the write that was refused::

    disk_room = sum(max(0, free - ceil(1% * all)) per disk)          volume :9340/status
    slot_room = Free * limit
              + sum(max(0, limit - Size) for the bucket's volumes)  master UI + volume /status
    room      = min(disk_room, slot_room)

and a disk under 1 % free is attributed to disk (``free_space``) whatever the
slot numbers say. The bucket's volumes are those whose ``Collection`` is the
bucket name (measured: an S3 bucket's objects land in a collection of the
same name). A ``ReadOnly`` volume contributes no room.

**The volume size limit is soft.** The master stops *assigning* writes to a
volume once it reaches the limit, but writes already assigned still land:
volumes were measured at 67,109,056 and 75,497,688 bytes under a 64 MiB
(67,108,864-byte) limit. ``limit - Size`` therefore under-counts slightly,
which makes "room < size" conservative -- it can call a store full a little
early, never late. SeaweedFS also compresses compressible content, which
errs the same way.

Where the numbers come from, and why one of them is HTML
--------------------------------------------------------
* **Volume server ``GET /status``** (JSON): ``DiskStatuses[].free`` and
  ``Volumes[]`` with ``Collection``, ``Size`` and ``ReadOnly``.
* **Master ``GET /``** (the master's status page, HTML): the rows
  ``Volume Size Limit`` (e.g. ``64MB``), ``Free`` and ``Max``.

The master's JSON ``GET /dir/status`` carries ``Free`` and ``Max`` too, and
was the first choice. It cannot be used. Closing the store's unauthenticated
side door (``docker-compose.yml``: ``WEED_GUARD_WHITE_LIST``) is what refuses
master admin mutations such as ``/col/delete`` and ``/vol/grow`` from other
containers, and the same guard wraps ``/dir/status`` and ``/vol/status``:
measured, both answer **401** from the API's container once the guard is on,
while ``GET /`` and the volume server's ``/status`` still answer 200. The
volume size limit is not in any JSON the master serves over HTTP at all; its
status page is the only place it appears. The page is parsed strictly, three
labelled cells or no opinion, and the live test in
``tests/api/test_api_status_live_store.py`` reads it from the pinned image on
every CI run, so an image bump that changes the page fails loudly instead of
going quiet.

A bucket quota is a fourth way, and it is readable over S3
----------------------------------------------------------
An enforced bucket quota refuses every write (a 1-byte and a 0-byte ``PUT``
too) with the same ``InternalError``/500 as any fault, and free disk and free
slots cannot see it. An earlier reading of this problem concluded the quota
could not be read from the API container; that was wrong. SeaweedFS 4.47
answers two SigV4-signed S3 requests with the credentials the API already
holds (both refuse an unsigned request with 403), measured with a 20 MiB
quota, 30 MiB used and writes refused:

* ``GET /{bucket}?seaweedfs-quota`` returns
  ``{"quota_size":20971520,"quota_unit":"B","quota_enabled":true}``. With no
  quota set: ``{"quota_size":0,"quota_unit":"B","quota_enabled":false}``.
* ``GET /{bucket}/.system-d26a9498-cb7c-4a87-a44a-8ae204f5ba6c/capacity.xml``
  (SeaweedFS's SOSAPI capacity object) returns
  ``<CapacityInfo><Capacity>20971520</Capacity><Available>0</Available>
  <Used>31457840</Used></CapacityInfo>``. With no quota set it reports the
  cluster's capacity instead, which is why the two answers are cross-checked
  (``Capacity`` must equal the quota) and anything else is no opinion.

Two facts about the numbers. ``Used`` is the gross volume size, while the
enforcer compares the *logical* size, so ``Available`` can read 0 for up to
about 60 s after a deletion or a raised quota while the bucket is already
writable again. That can only mislabel a refusal that has some other cause in
that window, and never misses a real quota refusal, because gross is never
smaller than logical. And 4.47 enforces on a 60 s sweep of its own, with
nobody running ``s3.bucket.quota.enforce``: a quota set and written past
starts refusing about a minute later, and raising it clears within about a
minute.

The wire code is a 4.47 fact. Upstream maps a read-only bucket to
``AccessDenied``/403 and the ``InternalError``/500 is the error arriving
flattened from the volume assign; the write path therefore asks this question
for either code (see :func:`report_quota`).

Addresses
---------
One setting, ``S3_SEAWEEDFS_MASTER_URL`` (e.g. ``http://seaweedfs:9333``).
The volume server is derived from it: **same scheme and host, port 9340**,
which is where ``weed mini`` runs its volume server in the same process as
the master. :func:`volume_status_url` is the derivation, and it is tested.
``weed mini`` is a single volume server, so "the volume server" is exact
there. On a multi-node cluster it would read one node's disk against the
whole cluster's slots, which is neither number, so do not set the variable
for one.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote, urlsplit, urlunsplit

from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials

logger = logging.getLogger(__name__)

#: ``weed mini``'s volume server port (``-volume.port``, default 9340). The
#: volume server runs in the master's process, so it shares the master's host.
VOLUME_SERVER_PORT = 9340

#: Per socket operation. The write-path check runs after a refusal, inside an
#: upload request that has already spent botocore's retries; the headroom
#: check runs inline on ``/status``. The same figure as the MinIO admin probe,
#: pinned alongside it by a test.
_TIMEOUT_SECONDS = 2.0

#: The whole probe, both requests together. A socket timeout bounds each
#: *operation*, not the request: a server that drips one byte every 1.5 s
#: held an earlier version for 9 s. The fetch runs on a daemon thread and the
#: caller stops waiting at this deadline, answering "no opinion"; the thread
#: is abandoned and ends when its socket does.
_DEADLINE_SECONDS = 2 * _TIMEOUT_SECONDS

#: ``weed mini`` 4.47 hard-codes a 1 % minimum free space and re-checks it
#: every 60 s; below it every volume turns read-only (measured, see above).
_MIN_FREE_PERCENT = 1

#: The master status page's rows, as rendered by 4.47:
#: ``<th>Volume Size Limit</th>\n<td>64MB</td>``. ``\s*`` absorbs the
#: template's indentation; nothing else is tolerated.
_ROW = r"<th>{label}</th>\s*<td>\s*([^<]*?)\s*</td>"
_LIMIT_ROW = re.compile(_ROW.format(label="Volume Size Limit"))
_FREE_ROW = re.compile(_ROW.format(label="Free"))
_MAX_ROW = re.compile(_ROW.format(label="Max"))
#: The limit cell is ``{{ .VolumeSizeLimitMB }}MB`` -- mebibytes, measured:
#: a volume stopped taking writes at 67,109,056 bytes under "64MB", just past
#: 64 * 1024 * 1024 = 67,108,864.
_LIMIT_VALUE = re.compile(r"^(\d+)MB$")
_MEBIBYTE = 1024 * 1024


@dataclass(frozen=True)
class SeaweedCapacity:
    """What the store says about its room, per arm, in bytes."""

    #: Sum of ``DiskStatuses[].free`` on the volume server, as reported.
    disk_free_bytes: int
    #: What the disk can still take: ``free`` less ``weed mini``'s 1 %
    #: minimum, per disk, clamped at zero. This is the disk arm.
    disk_room_bytes: int
    #: Any disk is below the 1 % minimum, so the store refuses every write
    #: and the slot numbers have collapsed with it.
    below_min_free: bool
    #: Room left in volume slots for this bucket: empty slots times the
    #: volume size limit, plus what the bucket's writable volumes can still
    #: take before they reach the limit.
    slot_room_bytes: int
    #: The master's ``Volume Size Limit``, in bytes.
    volume_size_limit_bytes: int
    #: The master's ``Free`` and ``Max`` volume-slot counts.
    free_slots: int
    max_slots: int

    @property
    def room_bytes(self) -> int:
        """The smaller arm: the most the store can take before refusing."""
        return min(self.disk_room_bytes, self.slot_room_bytes)

    @property
    def limiting_arm(self) -> str:
        """``"free_space"`` or ``"volume_slots"``.

        Disk below its minimum is always ``free_space``: the slot count
        drops to the volumes in use at the same moment, and "add slots"
        would be the wrong remedy. Disk also wins a tie.
        """
        if self.below_min_free or self.disk_room_bytes <= self.slot_room_bytes:
            return "free_space"
        return "volume_slots"


def volume_status_url(master_url: str) -> Optional[str]:
    """The volume server's ``/status`` URL for a ``weed mini`` master URL.

    Same scheme and host as the master, port :data:`VOLUME_SERVER_PORT`.
    ``None`` for anything that is not an ``http(s)://host`` URL.
    """
    try:
        parts = urlsplit(master_url.strip())
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return None
        host = parts.hostname
        if ":" in host:  # an IPv6 literal needs its brackets back
            host = f"[{host}]"
        return urlunsplit((parts.scheme, f"{host}:{VOLUME_SERVER_PORT}", "/status", "", ""))
    except ValueError:
        return None


def parse_master_page(html: str) -> Optional[tuple[int, int, int]]:
    """``(volume_size_limit_bytes, free_slots, max_slots)`` or ``None``.

    All three rows must be present and well-formed, or there is no opinion:
    a page this function half-understands is a page it does not understand.
    """
    try:
        limit_cell = _LIMIT_ROW.search(html)
        free_cell = _FREE_ROW.search(html)
        max_cell = _MAX_ROW.search(html)
        if limit_cell is None or free_cell is None or max_cell is None:
            return None
        limit = _LIMIT_VALUE.match(limit_cell.group(1))
        if limit is None:
            return None
        limit_bytes = int(limit.group(1)) * _MEBIBYTE
        free_slots = int(free_cell.group(1))
        max_slots = int(max_cell.group(1))
    except (TypeError, ValueError):
        return None
    if limit_bytes <= 0 or free_slots < 0 or max_slots < 0:
        return None
    return limit_bytes, free_slots, max_slots


def capacity_from_status(
    *, master_page: str, volume_status: object, bucket: str
) -> Optional[SeaweedCapacity]:
    """Combine the two answers into a :class:`SeaweedCapacity`, or ``None``.

    Pure, so the arithmetic is tested against the recorded payloads without
    a server.
    """
    parsed = parse_master_page(master_page)
    if parsed is None or not isinstance(volume_status, dict):
        return None
    limit_bytes, free_slots, max_slots = parsed

    disks = volume_status.get("DiskStatuses")
    if not isinstance(disks, list) or not disks:
        return None
    frees: list[int] = []
    rooms: list[int] = []
    below_min_free = False
    for disk in disks:
        if not isinstance(disk, dict):
            return None
        free, total = disk.get("free"), disk.get("all")
        # ``bool`` is an ``int`` and would sail through as one byte.
        if any(not isinstance(v, int) or isinstance(v, bool) for v in (free, total)):
            return None
        assert isinstance(free, int) and isinstance(total, int)
        reserve = -(-total * _MIN_FREE_PERCENT // 100)  # ceil
        frees.append(free)
        rooms.append(max(0, free - reserve))
        # "Any disk" is exact for ``weed mini``, which has one data dir. With
        # several dirs, one full disk would label the whole store
        # ``free_space`` while the others still took writes, and the label
        # could then misname the limit. Not a case TCKDB ships.
        below_min_free = below_min_free or free < reserve

    volumes = volume_status.get("Volumes") or []
    if not isinstance(volumes, list):
        return None
    in_volumes = 0
    for volume in volumes:
        if not isinstance(volume, dict) or volume.get("Collection") != bucket:
            continue
        size = volume.get("Size")
        if not isinstance(size, int) or isinstance(size, bool):
            return None
        if volume.get("ReadOnly") is True:
            continue
        in_volumes += max(0, limit_bytes - size)

    return SeaweedCapacity(
        disk_free_bytes=sum(frees),
        disk_room_bytes=sum(rooms),
        below_min_free=below_min_free,
        slot_room_bytes=free_slots * limit_bytes + in_volumes,
        volume_size_limit_bytes=limit_bytes,
        free_slots=free_slots,
        max_slots=max_slots,
    )


def _get(url: str, timeout: float) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, method="GET"), timeout=timeout) as response:
        return response.read()


def _fetch_within_deadline(
    master_url: str, volume_url: str, timeout: float, deadline: float
) -> tuple[str, object]:
    """Both answers, or an exception; the caller stops waiting at ``deadline``."""
    outcome: dict[str, object] = {}

    def _fetch() -> None:
        try:
            page = _get(master_url.rstrip("/") + "/", timeout).decode("utf-8", "replace")
            outcome["value"] = (page, json.loads(_get(volume_url, timeout)))
        except Exception as exc:  # handed to the caller, which absorbs it
            outcome["error"] = exc

    worker = threading.Thread(target=_fetch, name="seaweedfs-capacity-probe", daemon=True)
    worker.start()
    worker.join(deadline)
    if worker.is_alive():
        raise TimeoutError(f"no complete answer within {deadline}s")
    if "error" in outcome:
        raise outcome["error"]  # type: ignore[misc]
    return outcome["value"]  # type: ignore[return-value]


def report_capacity(
    *,
    master_url: str,
    bucket: str,
    timeout: float = _TIMEOUT_SECONDS,
    deadline: float = _DEADLINE_SECONDS,
) -> Optional[SeaweedCapacity]:
    """Ask the store. ``None`` means *no opinion*; never raises.

    ``None`` when ``master_url`` is empty (the feature is off), when either
    request fails for any reason, and when either answer is not the shape
    measured against 4.47.
    """
    try:
        if not master_url or not bucket:
            return None
        volume_url = volume_status_url(master_url)
        if volume_url is None:
            logger.debug("seaweedfs capacity probe: unusable master URL; no opinion")
            return None
        master_page, volume_status = _fetch_within_deadline(
            master_url, volume_url, timeout, deadline
        )
        capacity = capacity_from_status(
            master_page=master_page, volume_status=volume_status, bucket=bucket
        )
        if capacity is None:
            logger.debug("seaweedfs capacity probe: unfamiliar response shape; no opinion")
        return capacity
    except urllib.error.HTTPError as exc:
        logger.debug(
            "seaweedfs capacity probe: %s answered HTTP %s; no opinion",
            _sanitized(master_url),
            exc.code,
        )
        return None
    except Exception as exc:
        logger.debug(
            "seaweedfs capacity probe: %s did not answer (%s); no opinion",
            _sanitized(master_url),
            type(exc).__name__,
        )
        return None


#: What a refusal that is explained by an enforced bucket quota is recorded
#: as. Not a code the store sent (it sent ``InternalError``); TCKDB's own
#: name for the fact it established, so the recorded refusal is honest about
#: who classified it and is one of
#: :data:`app.services.artifact_storage_capacity._QUOTA_CODES`, which keeps a
#: free-space report from ever clearing it.
QUOTA_EXCEEDED_CODE = "SeaweedFSBucketQuotaExceeded"

#: SeaweedFS's SOSAPI capacity object, a fixed key in every bucket.
_CAPACITY_KEY = ".system-d26a9498-cb7c-4a87-a44a-8ae204f5ba6c/capacity.xml"

#: Neither answer is large; a store answering with more is not the one
#: measured.
_MAX_BODY_BYTES = 64 * 1024

_CAPACITY_XML = re.compile(
    r"^\s*(?:<\?xml[^>]*\?>\s*)?<CapacityInfo>\s*"
    r"<Capacity>(\d+)</Capacity>\s*<Available>(\d+)</Available>\s*<Used>(\d+)</Used>\s*"
    r"</CapacityInfo>\s*$"
)


@dataclass(frozen=True)
class SeaweedQuota:
    """An enabled bucket quota and what the store says is left of it."""

    quota_bytes: int
    #: ``Available`` from the SOSAPI capacity object: quota minus gross use,
    #: clamped at zero.
    available_bytes: int
    #: ``Used`` from the same object (gross volume size).
    used_bytes: int


def parse_quota_answers(quota_body: bytes, capacity_body: bytes) -> Optional[SeaweedQuota]:
    """A :class:`SeaweedQuota` from the two recorded answers, or ``None``.

    ``None`` unless the quota is enabled, in bytes, and the capacity object
    is exactly the measured shape and agrees with it (``Capacity`` equal to
    the quota). No quota set is also ``None``: there is nothing to exceed.
    """
    try:
        quota = json.loads(quota_body.decode("utf-8"))
        if not isinstance(quota, dict):
            return None
        size = quota.get("quota_size")
        unit = quota.get("quota_unit")
        enabled = quota.get("quota_enabled")
        # ``bool`` is an ``int`` and would sail through as one byte.
        if enabled is not True or unit != "B" or not isinstance(size, int) or isinstance(size, bool):
            return None
        if size <= 0:
            return None
        matched = _CAPACITY_XML.match(capacity_body.decode("utf-8"))
        if matched is None:
            return None
        capacity, available, used = (int(g) for g in matched.groups())
    except (ValueError, UnicodeDecodeError):
        return None
    if capacity != size or available > capacity:
        return None
    return SeaweedQuota(quota_bytes=size, available_bytes=available, used_bytes=used)


def _get_signed(url: str, timeout: float, *, access_key: str, secret_key: str, region: str) -> bytes:
    request = AWSRequest(method="GET", url=url)
    SigV4Auth(Credentials(access_key, secret_key), "s3", region).add_auth(request)
    prepared = urllib.request.Request(url, headers=dict(request.headers.items()), method="GET")
    with urllib.request.urlopen(prepared, timeout=timeout) as response:
        body = response.read(_MAX_BODY_BYTES + 1)
    if len(body) > _MAX_BODY_BYTES:
        raise ValueError("answer larger than any SeaweedFS quota answer")
    return body


def _quota_fetch_within_deadline(
    endpoint_url: str,
    bucket: str,
    timeout: float,
    deadline: float,
    credentials: dict[str, str],
) -> tuple[bytes, bytes]:
    outcome: dict[str, object] = {}
    base = endpoint_url.rstrip("/") + "/" + quote(bucket, safe="")

    def _fetch() -> None:
        try:
            quota = _get_signed(base + "?seaweedfs-quota", timeout, **credentials)
            capacity = _get_signed(base + "/" + _CAPACITY_KEY, timeout, **credentials)
            outcome["value"] = (quota, capacity)
        except Exception as exc:  # handed to the caller, which absorbs it
            outcome["error"] = exc

    worker = threading.Thread(target=_fetch, name="seaweedfs-quota-probe", daemon=True)
    worker.start()
    worker.join(deadline)
    if worker.is_alive():
        raise TimeoutError(f"no complete answer within {deadline}s")
    if "error" in outcome:
        raise outcome["error"]  # type: ignore[misc]
    return outcome["value"]  # type: ignore[return-value]


def report_quota(
    *,
    endpoint_url: str,
    access_key: str,
    secret_key: str,
    region: str,
    bucket: str,
    timeout: float = _TIMEOUT_SECONDS,
    deadline: float = _DEADLINE_SECONDS,
) -> Optional[SeaweedQuota]:
    """Ask the store, over signed S3, about the bucket's quota. Never raises.

    Reads only (two ``GET``s), with the credentials TCKDB already writes
    with, so it opens no new access and mutates nothing. ``None`` means *no
    opinion*: an unset endpoint or credential, any failure (a 403 included:
    if the credentials are bad, the refusal that prompted this is theirs and
    not a quota), a quota that is not enabled, or an answer that is not the
    shape measured against 4.47. The same 2 s per socket operation and 4 s
    overall deadline as :func:`report_capacity`.
    """
    try:
        if not (endpoint_url and access_key and secret_key and region and bucket):
            return None
        quota_body, capacity_body = _quota_fetch_within_deadline(
            endpoint_url,
            bucket,
            timeout,
            deadline,
            {"access_key": access_key, "secret_key": secret_key, "region": region},
        )
        quota = parse_quota_answers(quota_body, capacity_body)
        if quota is None:
            logger.debug("seaweedfs quota probe: no enabled quota, or unfamiliar shape; no opinion")
        return quota
    except urllib.error.HTTPError as exc:
        logger.debug(
            "seaweedfs quota probe: %s answered HTTP %s; no opinion",
            _sanitized(endpoint_url),
            exc.code,
        )
        return None
    except Exception as exc:
        logger.debug(
            "seaweedfs quota probe: %s did not answer (%s); no opinion",
            _sanitized(endpoint_url),
            type(exc).__name__,
        )
        return None


def refusal_is_over_quota(quota: Optional[SeaweedQuota], attempted_bytes: Optional[int]) -> bool:
    """Whether a refused write is explained by an enabled bucket quota.

    True only when the quota leaves less room than the write needed; a
    write of unknown size is explained only by no room left at all. No
    opinion is never an explanation.
    """
    if quota is None:
        return False
    if attempted_bytes is None:
        return quota.available_bytes <= 0
    return quota.available_bytes < attempted_bytes


def refusal_is_full(capacity: Optional[SeaweedCapacity], attempted_bytes: Optional[int]) -> bool:
    """Whether a refused write is explained by the store having no room.

    True only when the store reports less room than the write needed. A
    write of unknown size is explained only by no room at all, which is
    what exhausted volume slots read as (``slot_room_bytes == 0``). No
    opinion is never full.
    """
    if capacity is None:
        return False
    if attempted_bytes is None:
        return capacity.room_bytes <= 0
    return capacity.room_bytes < attempted_bytes


def _sanitized(url: str) -> str:
    """``scheme://host:port``, so a credential in the URL cannot be logged."""
    try:
        parts = urlsplit(url)
        if parts.scheme and parts.hostname:
            port = f":{parts.port}" if parts.port else ""
            return f"{parts.scheme}://{parts.hostname}{port}"
    except ValueError:
        pass
    return "(unparseable endpoint)"


__all__ = [
    "QUOTA_EXCEEDED_CODE",
    "VOLUME_SERVER_PORT",
    "SeaweedCapacity",
    "SeaweedQuota",
    "capacity_from_status",
    "parse_master_page",
    "parse_quota_answers",
    "refusal_is_full",
    "refusal_is_over_quota",
    "report_capacity",
    "report_quota",
    "volume_status_url",
]
