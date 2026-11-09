"""Source 3: the N2H3 pressure-dependent network (Keslin, Kaplan and Grinberg Dana 2025).

NOT IMPLEMENTED IN THIS PHASE. Behind configuration, like ``source_arc``:
absent entry means skipped, an enabled entry stops the build.

The intended shape::

    pdep_network:
      enabled: true
      arkane_dir: ~/code/michal-keslin/N2H3_shared/Final_Final_CCSDT_F12_Aug_F12
      # input.py / output.py / species/ are read from there; chem.inp is NOT used
      # (the folder's assessment calls the hand-made chem.inp files unreliable).

``deposit`` will drive ``backend/scripts/pdep_ingestion/`` (which posts through
the API), adapted only if this network needs it. It waits on a separate
provenance check (that this folder is the run the paper reports) and, if it
fails, is reported and the build carries on: the network is SI material.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from common import Report, Session

SOURCE = "pdep_network"


def deposit(session: Session, cfg: dict[str, Any], report: Report, work_dir: Path) -> None:
    raise NotImplementedError(
        "source 'pdep_network' is configured but its deposit step is not implemented in this phase "
        "(waits on the provenance check); remove the entry or set enabled: false"
    )


def validate(cfg: dict[str, Any]) -> list[str]:
    """An enabled entry is a configuration error until the deposit step exists."""
    return [SOURCE + ": configured and enabled, but its deposit step is not implemented in this phase"]


def describe_inputs(cfg: dict[str, Any]) -> dict[str, Any]:
    return {}
