"""Source 2: ARC runs deposited through the ARC adapter (``tckdb_arc``).

NOT IMPLEMENTED IN THIS PHASE. The step is a placeholder behind configuration:

* No ``arc_runs`` entry in the sources file: the build skips it and says so.
* An ``arc_runs`` entry with ``enabled: true``: the build stops with an error
  rather than pretending to have deposited anything.

The intended shape, so the entry can be added without touching ``build.py``::

    arc_runs:
      enabled: true
      adapter_path: ~/code/tckdb-adapters          # the repository that holds tckdb_arc
      runs_root: ~/runs/ARC/snapshot_reruns_20261108
      runs:                                         # one directory per run, each with output/output.yml
        - rotor_scan_1
        - arc_tckdb_demo_reaction
        - ...

``deposit`` will import ``tckdb_arc`` from ``adapter_path``, read each run's
``output.yml`` and upload through ``tckdb-client``, exactly as
``tckdb-arc-upload`` does. The source waits on the re-runs on another cluster
(owner decision 2026-11-08); the use-case generator for CH4 + OH needs it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from common import Report, Session

SOURCE = "arc_runs"


def deposit(session: Session, cfg: dict[str, Any], report: Report, work_dir: Path) -> None:
    raise NotImplementedError(
        "source 'arc_runs' is configured but its deposit step is not implemented in this phase "
        "(waits on the Zeus re-runs); remove the entry or set enabled: false"
    )


def validate(cfg: dict[str, Any]) -> list[str]:
    """An enabled entry is a configuration error until the deposit step exists."""
    return [SOURCE + ": configured and enabled, but its deposit step is not implemented in this phase"]


def describe_inputs(cfg: dict[str, Any]) -> dict[str, Any]:
    return {}
