"""Artifact builder + planned-upload value type.

Attached files travel inline in the upload payload (each calculation's
``artifacts`` carries ``content_base64``). A file stays out of the payload, and
appears in ``upload.artifact_plan(result)``, when the upload sets
``inline_artifacts=False`` or when it would exceed
:data:`INLINE_TOTAL_BUDGET_BYTES`; ``output_log`` and ``input`` files are
admitted first. A file the server would refuse (missing, empty, over
:data:`MAX_ARTIFACT_BYTES`, kind/extension not allowed, declared hash or size
mismatch) raises :class:`TCKDBBuilderValidationError` when the payload is
built. The output-log program signature is not checked client-side.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

from tckdb_schemas.fragments.artifact import ArtifactIn

from tckdb_client.builders.validation import (
    TCKDBBuilderValidationError,
    ensure_non_empty_str,
    ensure_optional_non_empty_str,
)

if TYPE_CHECKING:  # pragma: no cover — type-only import
    pass


__all__ = [
    "Artifact",
    "PlannedArtifactUpload",
    "ARTIFACT_KINDS",
    "MAX_ARTIFACT_BYTES",
    "INLINE_TOTAL_BUDGET_BYTES",
    "INLINE_PRIORITY_KINDS",
]

#: The server's per-artifact cap: 50 MiB decoded.
MAX_ARTIFACT_BYTES = 50 * 1024 * 1024

#: Decoded bytes of files one upload request may carry inline (about 89 MB once encoded).
INLINE_TOTAL_BUDGET_BYTES = 64 * 1024 * 1024


# Backend ``ArtifactKind`` enum — see ``app/db/models/common.py``.
ARTIFACT_KINDS: frozenset[str] = frozenset(
    {"input", "output_log", "checkpoint", "formatted_checkpoint", "ancillary"}
)


_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(eq=False)
class Artifact:
    """A file attached to a :class:`Calculation`, uploaded with the deposit.

    File existence is **not** checked at construction. Producers
    typically build a manifest of artifacts ahead of time — that keeps
    :class:`Calculation` construction cheap and avoids spurious
    failures when manifests are reused across machines. The file is read
    when the upload's ``to_payload()`` runs (it travels inline) or, for a
    file deferred to the second phase, when the planned upload is executed
    (see :class:`PlannedArtifactUpload`). The builders read the bytes to
    send them; they never parse them.
    """

    path: Path
    kind: str
    label: str | None = None
    sha256: str | None = None
    bytes: int | None = None

    def __post_init__(self) -> None:
        # Accept str or Path, normalise to Path internally.
        if isinstance(self.path, str):
            if not self.path.strip():
                raise TCKDBBuilderValidationError(
                    "Artifact.path must be non-empty."
                )
            self.path = Path(self.path)
        elif isinstance(self.path, Path):
            if not str(self.path).strip():
                raise TCKDBBuilderValidationError(
                    "Artifact.path must be non-empty."
                )
        else:
            raise TCKDBBuilderValidationError(
                "Artifact.path must be a str or pathlib.Path, got "
                f"{type(self.path).__name__}."
            )

        if not isinstance(self.kind, str) or self.kind not in ARTIFACT_KINDS:
            raise TCKDBBuilderValidationError(
                f"Artifact.kind must be one of {sorted(ARTIFACT_KINDS)}, "
                f"got {self.kind!r}."
            )

        self.label = ensure_optional_non_empty_str(self.label, field="label")

        if self.sha256 is not None:
            if not isinstance(self.sha256, str) or not _SHA256_HEX_RE.match(
                self.sha256
            ):
                raise TCKDBBuilderValidationError(
                    "Artifact.sha256 must be 64 lowercase hex characters, "
                    f"got {self.sha256!r}."
                )

        if self.bytes is not None:
            # ``bool`` subclasses ``int`` — reject it explicitly so a
            # caller passing ``True`` doesn't sneak past type checking.
            if isinstance(self.bytes, bool) or not isinstance(self.bytes, int):
                raise TCKDBBuilderValidationError(
                    "Artifact.bytes must be an int, got "
                    f"{type(self.bytes).__name__}."
                )
            if self.bytes < 0:
                raise TCKDBBuilderValidationError(
                    f"Artifact.bytes must be >= 0, got {self.bytes}."
                )


@dataclass(frozen=True)
class PlannedArtifactUpload:
    """One server-targeted artifact upload, ready to execute.

    Produced by :meth:`tckdb_client.builders.uploads.ComputedSpeciesUpload.artifact_plan`
    and the matching method on :class:`ComputedReactionUpload`. Frozen
    so callers can safely store, sort, or de-duplicate plans across
    multiple uploads without worrying about mutation.

    ``calculation_id`` is the server-assigned id resolved from the
    upload result; ``calculation_key`` is the bundle-local key the
    builder minted (kept for diagnostics and logging).

    ``calculation_ref`` is the calculation's public ``calc_`` ref from the
    same response. :meth:`TCKDBClient.upload_artifacts` addresses the
    calculation by it and refuses an item without one: the server takes no row
    id for an artifact upload. It is ``None`` only for a plan built against a
    server that predates the ref, or an offline preview.
    """

    calculation_key: str
    calculation_id: int
    path: Path
    kind: str
    label: str | None
    sha256: str | None
    bytes: int | None
    calculation_ref: str | None = None


#: Kinds admitted to the inline budget first.
INLINE_PRIORITY_KINDS: frozenset[str] = frozenset({"output_log", "input"})


def _check_file(art: Artifact) -> int:
    """Return the file's size, or raise naming the file if the server would refuse it."""
    try:
        st = art.path.stat()
    except OSError as exc:
        raise TCKDBBuilderValidationError(
            f"artifact file cannot be read: {art.path} ({exc.strerror or exc}). "
            "Artifacts are sent inline with the upload, so the file must exist "
            "when to_payload() runs; pass inline_artifacts=False to defer them."
        ) from exc
    if not stat.S_ISREG(st.st_mode):
        raise TCKDBBuilderValidationError(f"artifact path is not a file: {art.path}")
    if st.st_size == 0:
        raise TCKDBBuilderValidationError(f"artifact {art.path} is empty; the server refuses a zero-byte file.")
    if st.st_size > MAX_ARTIFACT_BYTES:
        raise TCKDBBuilderValidationError(
            f"artifact {art.path} is {st.st_size:,} bytes; the server accepts at "
            f"most {MAX_ARTIFACT_BYTES:,} bytes per artifact."
        )
    try:
        # The server's own model, with a placeholder body standing in for the content.
        ArtifactIn(
            kind=art.kind, filename=art.path.name, content_base64="AA==", sha256=art.sha256, bytes=art.bytes
        )
    except ValueError as exc:  # pydantic's ValidationError is a ValueError
        raise TCKDBBuilderValidationError(f"artifact {art.path} would be refused by the server: {exc}") from exc
    return st.st_size


def plan_inline_artifacts(calculations: Iterable[Any]) -> set[int]:
    """Choose which attached artifacts travel inline.

    Returns ``id()`` of each :class:`Artifact` admitted within
    :data:`INLINE_TOTAL_BUDGET_BYTES`, ``output_log``/``input`` first.

    :raises TCKDBBuilderValidationError: a file is missing, empty, over the
        per-artifact cap, or has a kind, extension or declared hash the server
        would refuse.
    """
    arts = [art for calc in calculations for art in calc.artifacts]
    sizes = {id(art): _check_file(art) for art in arts}
    chosen: set[int] = set()
    used = 0
    for priority in (True, False):
        for art in arts:
            if (art.kind in INLINE_PRIORITY_KINDS) is priority and used + sizes[id(art)] <= INLINE_TOTAL_BUDGET_BYTES:
                chosen.add(id(art))
                used += sizes[id(art)]
    return chosen


def file_sha256(path: Path) -> str | None:
    """SHA-256 hex of the file's bytes, or ``None`` if it cannot be read."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def posted_artifact_keys(payload: Any) -> set[tuple[str, str, str, str]]:
    """``(calculation key, kind, filename, sha256)`` of every artifact already inline in ``payload``.

    The hash is of the decoded ``content_base64``, so two different files that
    share a name and kind on one calculation are told apart. Walks a payload
    built by ``to_payload()`` (either upload class): any dict with a ``key``
    and an ``artifacts`` list is a calculation.
    """
    found: set[tuple[str, str, str, str]] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            arts = node.get("artifacts")
            if isinstance(node.get("key"), str) and isinstance(arts, list):
                for a in arts:
                    if isinstance(a, dict):
                        try:
                            digest = hashlib.sha256(base64.b64decode(a.get("content_base64") or "")).hexdigest()
                        except ValueError:
                            digest = ""
                        found.add((node["key"], str(a.get("kind")), str(a.get("filename")), digest))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(payload)
    return found


def artifact_wire(art: Artifact) -> dict[str, Any]:
    """The ``ArtifactIn`` dict for one attached artifact (reads the file).

    :raises TCKDBBuilderValidationError: a declared ``bytes`` or ``sha256``
        does not match the bytes just read (the server would refuse it).
    """
    content = art.path.read_bytes()
    if art.bytes is not None and art.bytes != len(content):
        raise TCKDBBuilderValidationError(
            f"artifact {art.path}: declared bytes={art.bytes:,} but the file holds {len(content):,}."
        )
    if art.sha256 is not None and art.sha256 != hashlib.sha256(content).hexdigest():
        raise TCKDBBuilderValidationError(
            f"artifact {art.path}: declared sha256 does not match the file's content."
        )
    out: dict[str, Any] = {
        "kind": art.kind,
        "filename": art.path.name,
        "content_base64": base64.b64encode(content).decode("ascii"),
    }
    if art.sha256 is not None:
        out["sha256"] = art.sha256
    if art.bytes is not None:
        out["bytes"] = art.bytes
    return out


def _ensure_kind(kind: str) -> str:
    if not isinstance(kind, str) or kind not in ARTIFACT_KINDS:
        raise TCKDBBuilderValidationError(
            f"Artifact.kind must be one of {sorted(ARTIFACT_KINDS)}, "
            f"got {kind!r}."
        )
    return kind


def _ensure_path(value) -> Path:
    if isinstance(value, str):
        if not value.strip():
            raise TCKDBBuilderValidationError(
                "Artifact.path must be non-empty."
            )
        return Path(value)
    if isinstance(value, Path):
        if not str(value).strip():
            raise TCKDBBuilderValidationError(
                "Artifact.path must be non-empty."
            )
        return value
    raise TCKDBBuilderValidationError(
        "Artifact.path must be a str or pathlib.Path, got "
        f"{type(value).__name__}."
    )
