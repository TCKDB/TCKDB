"""Source 3: the N2H3 pressure-dependent network (not implemented).

No entry in the sources file means the step is skipped; an enabled entry is a configuration error. Intended entry::

    pdep_network:
      enabled: true
      arkane_dir: /path/to/Final_Final_CCSDT_F12_Aug_F12
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from common import Report, Session

SOURCE = "pdep_network"


def deposit(session: Session, cfg: dict[str, Any], report: Report, work_dir: Path) -> None:
    raise NotImplementedError(f"source {SOURCE!r} has no deposit step yet")


def validate(cfg: dict[str, Any]) -> list[str]:
    """Report an enabled entry as a configuration error."""
    return [SOURCE + ": enabled, but there is no deposit step yet"]


def describe_inputs(cfg: dict[str, Any]) -> dict[str, Any]:
    return {}
