"""Source 2: ARC runs through the ARC adapter (not implemented).

No entry in the sources file means the step is skipped; an enabled entry is a configuration error. Intended entry::

    arc_runs:
      enabled: true
      adapter_path: /path/to/tckdb-adapters
      runs_root: /path/to/arc/runs
      runs: [rotor_scan_1, arc_tckdb_demo_reaction]
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from common import Report, Session

SOURCE = "arc_runs"


def deposit(session: Session, cfg: dict[str, Any], report: Report, work_dir: Path) -> None:
    raise NotImplementedError(f"source {SOURCE!r} has no deposit step yet")


def validate(cfg: dict[str, Any]) -> list[str]:
    """Report an enabled entry as a configuration error."""
    return [SOURCE + ": enabled, but there is no deposit step yet"]


def describe_inputs(cfg: dict[str, Any]) -> dict[str, Any]:
    return {}
