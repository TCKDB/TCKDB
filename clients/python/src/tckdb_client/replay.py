"""Offline payload bundle replay engine.

Walks a TCKDB Offline Payload Bundle directory (DR-0027), dispatches
each sidecar by ``payload_kind``, and POSTs the saved payload via the
existing ``TCKDBClient``. Sidecar status updates are atomic. The engine
is chemistry-blind: it reads the bundle layout and the dispatch table
only — it never inspects payload contents.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tckdb_client.client import TCKDBClient, TCKDBResponse
from tckdb_client.errors import TCKDBConnectionError, TCKDBHTTPError

logger = logging.getLogger(__name__)

ClientFactory = Callable[[str], TCKDBClient]
"""A factory that returns a :class:`TCKDBClient` bound to a base URL.

The CLI builds a closure capturing ``api_key`` and ``timeout``; the
engine calls the factory with the *effective* base URL for each
sidecar. The engine therefore never holds a long-lived client tied to
a placeholder URL; base-URL precedence is enforced at the engine
boundary.
"""

RESPONSE_BODY_CAP = 4096
SIDECAR_GLOB = "*.meta.json"
DEFAULT_FORMAT_VERSIONS: tuple[str, ...] = ("0",)

# Terminal sidecar status: a calculation_artifact sidecar whose target
# /calculations/{id} no longer exists on the server (typically after a
# DB reset or fresh re-upload of the parent bundle). The integer
# calculation_id baked into these sidecars at producer time is stale
# and the only fix is regeneration from output.yml. We keep replay
# idempotent by skipping this status on every subsequent walk instead
# of retrying forever.
STATUS_NEEDS_REGENERATION = "needs_regeneration"


@dataclass(frozen=True)
class ReplayFailure:
    """A single sidecar that the engine could not upload.

    ``sidecar_path`` is captured as a string so the summary stays
    serializable; ``payload_kind`` is ``"__unparseable__"`` for sidecars
    that failed to JSON-decode and ``"__unknown__"`` for sidecars that
    parsed but had no ``payload_kind`` field.
    """

    sidecar_path: str
    payload_kind: str
    last_error: str


@dataclass(frozen=True)
class ReplaySummary:
    total: int
    uploaded: int
    skipped_already_uploaded: int
    skipped_marked_skipped: int
    skipped_failed_due_to_only_pending: int
    skipped_needs_regeneration: int
    failed: int
    dry_run: int
    by_kind: dict[str, dict[str, int]] = field(default_factory=dict)
    failures: tuple[ReplayFailure, ...] = field(default_factory=tuple)


class _LocalReplayError(Exception):
    """Raised by a dispatch handler when no HTTP call should be made.

    Distinct from :class:`TCKDBHTTPError` so the engine can record
    ``response_status_code=None`` and a clear local message instead of
    pretending an HTTP call happened.
    """


def replay_bundle(
    bundle_dir: str | Path,
    *,
    client_factory: ClientFactory,
    base_url_override: str | None = None,
    only_pending: bool = False,
    dry_run: bool = False,
    supported_format_versions: tuple[str, ...] = DEFAULT_FORMAT_VERSIONS,
) -> ReplaySummary:
    """Walk ``bundle_dir`` and replay every actionable sidecar.

    Returns a :class:`ReplaySummary` with per-kind counts. Per-sidecar
    failures are recorded in the summary and on the sidecar itself —
    they never abort the walk.
    """
    root = Path(bundle_dir)
    if not root.exists() or not root.is_dir():
        raise NotADirectoryError(f"bundle_dir is not a directory: {root}")

    sidecars = sorted(root.rglob(SIDECAR_GLOB))
    counts = _CountAccumulator()

    for sidecar_path in sidecars:
        counts.total += 1
        _process_one(
            sidecar_path,
            client_factory=client_factory,
            base_url_override=base_url_override,
            only_pending=only_pending,
            dry_run=dry_run,
            supported_format_versions=supported_format_versions,
            counts=counts,
        )

    return counts.to_summary()


def _process_one(
    sidecar_path: Path,
    *,
    client_factory: ClientFactory,
    base_url_override: str | None,
    only_pending: bool,
    dry_run: bool,
    supported_format_versions: tuple[str, ...],
    counts: _CountAccumulator,
) -> None:
    try:
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        # Don't overwrite a corrupt file the operator may need to recover.
        msg = f"sidecar parse failure: {exc}"
        logger.warning("%s: %s", sidecar_path, msg)
        counts.record_failure(sidecar_path, "__unparseable__", msg)
        return

    if not isinstance(sidecar, dict):
        msg = "sidecar root is not a JSON object"
        logger.warning("%s: %s", sidecar_path, msg)
        counts.record_failure(sidecar_path, "__unparseable__", msg)
        return

    kind = sidecar.get("payload_kind") or "__unknown__"

    version = sidecar.get("bundle_format_version")
    if version not in supported_format_versions:
        last_error = f"unsupported bundle_format_version: {version!r}"
        _mark_failed(
            sidecar_path,
            sidecar,
            last_error=last_error,
            dry_run=dry_run,
        )
        counts.record_failure(sidecar_path, kind, last_error)
        return

    status = sidecar.get("status")
    if status == "uploaded":
        counts.skipped_already_uploaded += 1
        counts.bump(kind, "skipped_already_uploaded")
        return
    if status == "skipped":
        counts.skipped_marked_skipped += 1
        counts.bump(kind, "skipped_marked_skipped")
        return
    if status == STATUS_NEEDS_REGENERATION:
        # Terminal status. The sidecar's calculation_id is stale (the
        # parent calc no longer exists on this server) and only a fresh
        # regeneration from output.yml can fix it — retrying the POST
        # would just produce another 404. Skip silently every walk so
        # the count stays meaningful but no HTTP traffic is generated.
        counts.skipped_needs_regeneration += 1
        counts.bump(kind, "skipped_needs_regeneration")
        return
    if only_pending and status == "failed":
        # Distinct counter: the sidecar is *not* uploaded — it's a
        # known-failed sidecar deliberately deferred by --only-pending.
        # Conflating with "already uploaded" would hide unfinished work.
        counts.skipped_failed_due_to_only_pending += 1
        counts.bump(kind, "skipped_failed_due_to_only_pending")
        return

    effective_base_url = base_url_override or sidecar.get("base_url")
    if not effective_base_url:
        last_error = (
            "no base_url available: --base-url not provided and "
            "sidecar.base_url is missing"
        )
        _mark_failed(
            sidecar_path,
            sidecar,
            last_error=last_error,
            dry_run=dry_run,
        )
        counts.record_failure(sidecar_path, kind, last_error)
        return

    handler = _DISPATCH.get(kind)
    if handler is None:
        last_error = f"unknown payload_kind: {kind!r}"
        _mark_failed(
            sidecar_path,
            sidecar,
            last_error=last_error,
            dry_run=dry_run,
        )
        counts.record_failure(sidecar_path, kind, last_error)
        return

    if dry_run:
        counts.dry_run += 1
        counts.bump(kind, "dry_run")
        return

    client = client_factory(effective_base_url)
    try:
        try:
            response = handler(sidecar, client=client, sidecar_dir=sidecar_path.parent)
        except _LocalReplayError as exc:
            last_error = str(exc)
            _mark_failed(
                sidecar_path,
                sidecar,
                last_error=last_error,
                dry_run=False,
            )
            counts.record_failure(sidecar_path, kind, last_error)
            return
        except TCKDBHTTPError as exc:
            last_error = _format_http_error(exc)
            response_payload = (
                exc.response_json
                if exc.response_json is not None
                else exc.response_text
            )
            # 404 on a calculation_artifact sidecar means the parent
            # calculation no longer exists on the server (DB reset or
            # fresh re-upload), making the sidecar's baked-in
            # ``calculation_id`` permanently stale. Mark it terminal so
            # subsequent runs skip silently instead of cascading 404s.
            if (
                kind == "calculation_artifact"
                and exc.status_code == 404
            ):
                _mark_needs_regeneration(
                    sidecar_path,
                    sidecar,
                    last_error=last_error,
                    response_status_code=exc.status_code,
                    response_payload=response_payload,
                )
                counts.skipped_needs_regeneration += 1
                counts.bump(kind, "skipped_needs_regeneration")
                return
            _mark_failed(
                sidecar_path,
                sidecar,
                last_error=last_error,
                response_status_code=exc.status_code,
                response_payload=response_payload,
                dry_run=False,
            )
            counts.record_failure(sidecar_path, kind, last_error)
            return
        except TCKDBConnectionError as exc:
            last_error = f"connection error: {exc}"
            _mark_failed(
                sidecar_path,
                sidecar,
                last_error=last_error,
                dry_run=False,
            )
            counts.record_failure(sidecar_path, kind, last_error)
            return
        except Exception as exc:  # defensive — never abort the walk
            last_error = f"unexpected error: {exc!r}"
            _mark_failed(
                sidecar_path,
                sidecar,
                last_error=last_error,
                dry_run=False,
            )
            counts.record_failure(sidecar_path, kind, last_error)
            return

        _mark_uploaded(sidecar_path, sidecar, response)
        counts.uploaded += 1
        counts.bump(kind, "uploaded")
    finally:
        try:
            client.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Dispatch handlers
# ---------------------------------------------------------------------------


def _resolve(sidecar_dir: Path, value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else (sidecar_dir / p)


def _replay_json_payload(
    sidecar: dict,
    *,
    client: TCKDBClient,
    sidecar_dir: Path,
) -> TCKDBResponse:
    """Generic ``payload_file``-based replay.

    Used by every payload kind whose offline form is "saved JSON, POSTed
    unchanged to ``endpoint``" (``conformer_calculation``,
    ``computed_species``, future bundle kinds). The handler is
    chemistry-blind: it never inspects payload contents.
    """
    payload_file = sidecar.get("payload_file")
    if not payload_file:
        raise _LocalReplayError("sidecar is missing required field: payload_file")
    payload_path = _resolve(sidecar_dir, payload_file)
    if not payload_path.exists():
        raise _LocalReplayError(f"payload_file does not exist: {payload_path}")

    try:
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise _LocalReplayError(f"failed to read payload_file {payload_path}: {exc}") from exc

    endpoint = sidecar.get("endpoint")
    if not endpoint:
        raise _LocalReplayError("sidecar is missing required field: endpoint")
    idempotency_key = sidecar.get("idempotency_key")
    if not idempotency_key:
        raise _LocalReplayError("sidecar is missing required field: idempotency_key")

    return client.request_json(
        "POST",
        endpoint,
        json=payload,
        idempotency_key=idempotency_key,
    )


def _replay_calculation_artifact(
    sidecar: dict,
    *,
    client: TCKDBClient,
    sidecar_dir: Path,
) -> TCKDBResponse:
    source_path_value = sidecar.get("source_path")
    if not source_path_value:
        raise _LocalReplayError("sidecar is missing required field: source_path")
    source_path = _resolve(sidecar_dir, source_path_value)
    if not source_path.exists():
        raise _LocalReplayError(f"source_path does not exist: {source_path}")

    try:
        content = source_path.read_bytes()
    except OSError as exc:
        raise _LocalReplayError(f"failed to read source_path {source_path}: {exc}") from exc

    fresh_sha256 = hashlib.sha256(content).hexdigest()
    fresh_bytes = len(content)

    declared_sha = sidecar.get("sha256")
    declared_bytes = sidecar.get("bytes")
    if declared_sha is not None and declared_sha.lower() != fresh_sha256:
        raise _LocalReplayError(
            f"file drift: sidecar.sha256={declared_sha!r} does not match "
            f"current source_path.sha256={fresh_sha256!r}"
        )
    if declared_bytes is not None and declared_bytes != fresh_bytes:
        raise _LocalReplayError(
            f"file drift: sidecar.bytes={declared_bytes} does not match "
            f"current source_path.bytes={fresh_bytes}"
        )

    endpoint = sidecar.get("endpoint")
    if not endpoint:
        raise _LocalReplayError("sidecar is missing required field: endpoint")
    idempotency_key = sidecar.get("idempotency_key")
    if not idempotency_key:
        raise _LocalReplayError("sidecar is missing required field: idempotency_key")
    artifact_kind = sidecar.get("kind")
    if not artifact_kind:
        raise _LocalReplayError("sidecar is missing required field: kind")

    filename = sidecar.get("filename") or source_path.name

    body = {
        "artifacts": [
            {
                "kind": artifact_kind,
                "filename": filename,
                "content_base64": base64.b64encode(content).decode("ascii"),
                "sha256": fresh_sha256,
                "bytes": fresh_bytes,
            }
        ]
    }
    return client.request_json(
        "POST",
        endpoint,
        json=body,
        idempotency_key=idempotency_key,
    )


_DISPATCH: dict[str, Callable[..., TCKDBResponse]] = {
    "conformer_calculation": _replay_json_payload,
    "computed_species": _replay_json_payload,
    "computed_reaction": _replay_json_payload,
    "calculation_artifact": _replay_calculation_artifact,
}

#: Public list of payload kinds the engine knows how to replay. Useful
#: for CLI help text, validation, and documentation. Order matches
#: dispatch table iteration so user-facing surfaces stay stable.
SUPPORTED_PAYLOAD_KINDS: tuple[str, ...] = tuple(_DISPATCH)


# ---------------------------------------------------------------------------
# Sidecar mutation helpers
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _bound_response_body(value: Any) -> dict[str, Any]:
    """Apply the 4096-char bounded-storage policy.

    Returns a dict with three keys, suitable for splat into the sidecar
    update::

        {
          "response_body": <parsed JSON | truncated string | None>,
          "response_body_truncated": bool,
          "response_body_original_length": <int | None>,
        }
    """
    if value is None:
        return {
            "response_body": None,
            "response_body_truncated": False,
            "response_body_original_length": None,
        }

    if isinstance(value, str):
        if len(value) <= RESPONSE_BODY_CAP:
            return {
                "response_body": value,
                "response_body_truncated": False,
                "response_body_original_length": None,
            }
        return {
            "response_body": value[:RESPONSE_BODY_CAP],
            "response_body_truncated": True,
            "response_body_original_length": len(value),
        }

    try:
        serialized = json.dumps(value, sort_keys=True, default=str)
    except (TypeError, ValueError):
        serialized = repr(value)
        if len(serialized) <= RESPONSE_BODY_CAP:
            return {
                "response_body": serialized,
                "response_body_truncated": False,
                "response_body_original_length": None,
            }
        return {
            "response_body": serialized[:RESPONSE_BODY_CAP],
            "response_body_truncated": True,
            "response_body_original_length": len(serialized),
        }

    if len(serialized) <= RESPONSE_BODY_CAP:
        # Keep structured form when it fits; sidecars stay machine-readable.
        return {
            "response_body": value,
            "response_body_truncated": False,
            "response_body_original_length": None,
        }
    return {
        "response_body": serialized[:RESPONSE_BODY_CAP],
        "response_body_truncated": True,
        "response_body_original_length": len(serialized),
    }


def _format_http_error(exc: TCKDBHTTPError) -> str:
    bits = [f"HTTP {exc.status_code}" if exc.status_code is not None else "HTTP error"]
    if exc.code:
        bits.append(f"code={exc.code}")
    msg = str(exc)
    if msg and msg not in bits[0]:
        bits.append(msg)
    return ": ".join(bits)


def _mark_uploaded(
    sidecar_path: Path,
    sidecar: dict,
    response: TCKDBResponse,
) -> None:
    updated = dict(sidecar)
    updated.update(
        {
            "bundle_format_version": sidecar.get("bundle_format_version", "0"),
            "status": "uploaded",
            "uploaded_at": _utc_now_iso(),
            "response_status_code": response.status_code,
            "idempotency_replayed": response.idempotency_replayed,
            "last_error": None,
        }
    )
    updated.update(_bound_response_body(response.data))
    try:
        _atomic_write_sidecar(sidecar_path, updated)
    except OSError as exc:
        # The HTTP upload happened; we just couldn't persist that fact.
        # Idempotency makes a re-run safe — log loudly and let the next
        # replay reconcile the on-disk status.
        logger.error(
            "atomic sidecar write failed for %s after successful upload: %s",
            sidecar_path,
            exc,
        )


def _mark_failed(
    sidecar_path: Path,
    sidecar: dict,
    *,
    last_error: str,
    response_status_code: int | None = None,
    response_payload: Any = None,
    dry_run: bool,
) -> None:
    if dry_run:
        return
    updated = dict(sidecar)
    updated.update(
        {
            "bundle_format_version": sidecar.get("bundle_format_version", "0"),
            "status": "failed",
            "last_attempted_at": _utc_now_iso(),
            "last_error": last_error,
            "response_status_code": response_status_code,
        }
    )
    updated.update(_bound_response_body(response_payload))
    try:
        _atomic_write_sidecar(sidecar_path, updated)
    except OSError as exc:
        logger.error("atomic sidecar write failed for %s: %s", sidecar_path, exc)


def _mark_needs_regeneration(
    sidecar_path: Path,
    sidecar: dict,
    *,
    last_error: str,
    response_status_code: int | None,
    response_payload: Any,
) -> None:
    """Persist the terminal ``needs_regeneration`` status on a sidecar.

    Same shape as ``_mark_failed`` but writes the terminal status, which
    the next replay walk recognizes and skips without an HTTP call.
    Never runs under ``dry_run`` — the caller only invokes us after a
    real HTTP response was received, so there's no dry-run path here.
    """
    updated = dict(sidecar)
    updated.update(
        {
            "bundle_format_version": sidecar.get("bundle_format_version", "0"),
            "status": STATUS_NEEDS_REGENERATION,
            "last_attempted_at": _utc_now_iso(),
            "last_error": last_error,
            "response_status_code": response_status_code,
        }
    )
    updated.update(_bound_response_body(response_payload))
    try:
        _atomic_write_sidecar(sidecar_path, updated)
    except OSError as exc:
        logger.error("atomic sidecar write failed for %s: %s", sidecar_path, exc)


def _atomic_write_sidecar(sidecar_path: Path, sidecar: dict) -> None:
    """Write ``sidecar`` JSON via tempfile + os.replace.

    Targets POSIX atomic-rename semantics. On non-POSIX filesystems the
    rename is best-effort; the caller should treat leftover ``.tmp``
    files as a janitorial concern, not an error.
    """
    sidecar_dir = sidecar_path.parent
    fd, tmp_path = tempfile.mkstemp(
        prefix=sidecar_path.name + ".",
        suffix=".tmp",
        dir=str(sidecar_dir),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(sidecar, f, indent=2, sort_keys=True, default=str)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp_path, sidecar_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Internal accumulator
# ---------------------------------------------------------------------------


class _CountAccumulator:
    __slots__ = (
        "total",
        "uploaded",
        "skipped_already_uploaded",
        "skipped_marked_skipped",
        "skipped_failed_due_to_only_pending",
        "skipped_needs_regeneration",
        "failed",
        "dry_run",
        "_by_kind",
        "failures",
    )

    def __init__(self) -> None:
        self.total = 0
        self.uploaded = 0
        self.skipped_already_uploaded = 0
        self.skipped_marked_skipped = 0
        self.skipped_failed_due_to_only_pending = 0
        self.skipped_needs_regeneration = 0
        self.failed = 0
        self.dry_run = 0
        self._by_kind: dict[str, dict[str, int]] = {}
        self.failures: list[ReplayFailure] = []

    def record_failure(
        self,
        sidecar_path: Path,
        kind: str,
        last_error: str,
    ) -> None:
        self.failed += 1
        self.bump(kind, "failed")
        self.failures.append(
            ReplayFailure(
                sidecar_path=str(sidecar_path),
                payload_kind=kind,
                last_error=last_error,
            )
        )

    def bump(self, kind: str, bucket: str) -> None:
        d = self._by_kind.setdefault(kind, {})
        d[bucket] = d.get(bucket, 0) + 1

    def to_summary(self) -> ReplaySummary:
        return ReplaySummary(
            total=self.total,
            uploaded=self.uploaded,
            skipped_already_uploaded=self.skipped_already_uploaded,
            skipped_marked_skipped=self.skipped_marked_skipped,
            skipped_failed_due_to_only_pending=(
                self.skipped_failed_due_to_only_pending
            ),
            skipped_needs_regeneration=self.skipped_needs_regeneration,
            failed=self.failed,
            dry_run=self.dry_run,
            by_kind={k: dict(v) for k, v in self._by_kind.items()},
            failures=tuple(self.failures),
        )


__all__ = [
    "ClientFactory",
    "ReplayFailure",
    "ReplaySummary",
    "replay_bundle",
    "RESPONSE_BODY_CAP",
    "STATUS_NEEDS_REGENERATION",
    "SUPPORTED_PAYLOAD_KINDS",
]
