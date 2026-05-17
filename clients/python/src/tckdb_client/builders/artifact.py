"""Artifact builder + planned-upload value type.

The artifact subsystem is intentionally two-phase:

1. The scientific upload (``client.upload(upload_object)``) creates the
   calculation rows on the server and returns an upload result.
2. ``upload.artifact_plan(result)`` resolves each builder-attached
   artifact to its server-assigned ``calculation.id`` and produces a
   list of :class:`PlannedArtifactUpload` records.
3. ``client.upload_artifacts(plan)`` posts each file to
   ``POST /api/v1/calculations/{calculation_id}/artifacts``.

Artifacts are **never** embedded in the scientific upload payload —
the bundle endpoints don't accept inline artifact bytes today, and
mixing scientific resolution with file transport is what we want to
avoid anyway. ``ComputedSpeciesUpload.to_payload()`` and
``ComputedReactionUpload.to_payload()`` produce identical bytes
whether or not their calculations have artifacts attached.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

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
]


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
    """

    calculation_key: str
    calculation_id: int
    path: Path
    kind: str
    label: str | None
    sha256: str | None
    bytes: int | None


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
