"""Artifact builder + planned-upload value type.

Artifacts travel **inline** by default: ``ComputedSpeciesUpload.to_payload()``
and ``ComputedReactionUpload.to_payload()`` read each attached file and embed
it (``content_base64``) in its calculation's ``artifacts`` list, which both
bundle schemas accept. The server stores the files in the same request and
runs its evidence checks (for example ``converged_opt_no_usable_energy``)
after they are stored, so a deposit does not carry warnings that a second
request would immediately make stale.

A file is sent in a second phase only when it cannot go inline:

* The upload sets ``inline_artifacts=False``.
* Adding it would take the request past :data:`INLINE_TOTAL_BUDGET_BYTES`.
  The server caps one upload at 200 MB of decoded files, but a reverse proxy
  in front of it commonly caps the request body near 100 MiB, and base64
  adds a third. The budget keeps the whole request under that, with room for
  the scientific JSON. Files over budget are not an error: they stay out of
  the payload and appear in ``upload.artifact_plan(result)``, which then
  lists only those files, to be posted with ``client.upload_artifacts(plan)``
  to ``POST /calculations/{calculation_ref}/artifacts`` as before.

A single file over :data:`MAX_ARTIFACT_BYTES` (the server's per-artifact cap,
which the two-phase route enforces too) is refused when the payload is built,
naming the file, rather than after a long transfer.
"""

from __future__ import annotations

import base64
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

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
]

#: The server's per-artifact cap (``MAX_ARTIFACT_BYTES`` in
#: ``backend/app/services/artifact_storage.py``): 50 MiB decoded.
MAX_ARTIFACT_BYTES = 50 * 1024 * 1024

#: Decoded bytes of files one upload request may carry inline. Below the
#: server's 200 MB per-upload cap on purpose: see the module docstring.
INLINE_TOTAL_BUDGET_BYTES = 64 * 1024 * 1024


# Backend ``ArtifactKind`` enum — see ``app/db/models/common.py``.
ARTIFACT_KINDS: frozenset[str] = frozenset(
    {"input", "output_log", "checkpoint", "formatted_checkpoint", "ancillary"}
)


_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(eq=False)
class Artifact:
    """A file attached to a :class:`Calculation` for later upload.

    File existence is **not** checked at construction. Producers
    typically build a manifest of artifacts ahead of time and only
    open the files when the plan executes — that keeps
    :class:`Calculation` construction cheap and avoids spurious
    failures when manifests are reused across machines. The path is
    materialised when the planned upload is executed (see
    :func:`tckdb_client.builders.artifact.PlannedArtifactUpload`).
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


def plan_inline_artifacts(calculations: Iterable[Any]) -> set[int]:
    """Choose which attached artifacts travel inline.

    Walks ``calculations`` in order and returns ``id()`` of each
    :class:`Artifact` that fits :data:`INLINE_TOTAL_BUDGET_BYTES` (a file that
    does not fit is skipped and a later, smaller one may still fit). Sizes come
    from the files on disk.

    :raises TCKDBBuilderValidationError: a file is missing or not a regular
        file, or is larger than :data:`MAX_ARTIFACT_BYTES`.
    """
    chosen: set[int] = set()
    used = 0
    for calc in calculations:
        for art in calc.artifacts:
            try:
                st = art.path.stat()
            except OSError as exc:
                raise TCKDBBuilderValidationError(
                    f"artifact file cannot be read: {art.path} ({exc.strerror or exc}). "
                    "Artifacts are sent inline with the upload, so the file must exist "
                    "when to_payload() runs; pass inline_artifacts=False to defer them."
                ) from exc
            if not stat.S_ISREG(st.st_mode):
                raise TCKDBBuilderValidationError(
                    f"artifact path is not a file: {art.path}"
                )
            if st.st_size > MAX_ARTIFACT_BYTES:
                raise TCKDBBuilderValidationError(
                    f"artifact {art.path} is {st.st_size:,} bytes; the server accepts at "
                    f"most {MAX_ARTIFACT_BYTES:,} bytes per artifact."
                )
            if used + st.st_size <= INLINE_TOTAL_BUDGET_BYTES:
                chosen.add(id(art))
                used += st.st_size
    return chosen


def artifact_wire(art: Artifact) -> dict[str, Any]:
    """The ``ArtifactIn`` dict for one attached artifact (reads the file)."""
    out: dict[str, Any] = {
        "kind": art.kind,
        "filename": art.path.name,
        "content_base64": base64.b64encode(art.path.read_bytes()).decode("ascii"),
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
