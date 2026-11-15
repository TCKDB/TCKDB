"""Source 3: the N2H3 pressure-dependent network (Keslin, Kaplan and Grinberg Dana, PCCP 2025, 27, 2680).

The Arkane run directory is turned into one ``NetworkPDepUploadRequest`` by the PDep ingester
(``backend/scripts/pdep_ingestion``, which reads ``input.py``, ``output.py`` and ``supporting_information.csv``) and
posted to ``/uploads/networks/pdep``. The deposit cites the paper by DOI (``literature``). What the ingester could not
carry over is recorded under ``ingestion`` in the build report.

Entry::

    pdep_network:
      enabled: true
      arkane_dir: /path/to/Final_Final_CCSDT_F12_Aug_F12
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from common import Report, Session, TCKDBError, collect_response_warnings, digest_files

SOURCE = "pdep_network"
READ_FILES = ("input.py", "output.py", "supporting_information.csv")

#: The cited publication. The literature payload has no author field.
DEFAULT_LITERATURE = {
    "kind": "article",
    "title": "Pressure-dependent kinetic analysis of the N2H3 potential energy surface",
    "journal": "Physical Chemistry Chemical Physics",
    "year": 2025,
    "volume": "27",
    "pages": "2680-2691",
    "doi": "10.1039/d4cp03837a",
}


def validate(cfg: dict[str, Any]) -> list[str]:
    """Return the problems with this source's config."""
    if not cfg.get("arkane_dir"):
        return [f"{SOURCE}: 'arkane_dir' (the Arkane run directory) is required"]
    root = Path(str(cfg["arkane_dir"])).expanduser()
    return [f"{SOURCE}: {name} not found under arkane_dir" for name in READ_FILES[:2] if not (root / name).is_file()]


def describe_inputs(cfg: dict[str, Any]) -> dict[str, Any]:
    """Return the count and digest of the files the ingester reads."""
    root = Path(cfg["arkane_dir"]).expanduser()
    return digest_files(root, [root / name for name in READ_FILES if (root / name).is_file()])


def deposit(session: Session, cfg: dict[str, Any], report: Report, work_dir: Path) -> None:
    from scripts.pdep_ingestion.builder import build_network_pdep_payload

    root = Path(cfg["arkane_dir"]).expanduser()
    src = report.source(SOURCE)
    payload, gap = build_network_pdep_payload(root, include_artifacts=False, network_only=True)
    payload["literature"] = dict(cfg.get("literature") or DEFAULT_LITERATURE)
    src["ingestion"] = {
        "species_built": gap.species_built,
        "species_skipped": [list(item) for item in gap.species_skipped],
        "transition_states_with_geometry": gap.ts_built,
        "transition_states_without_geometry": gap.ts_stub_no_geometry,
        "rotor_treatment_unstated": gap.treatment_unstated,
        "micro_reactions": gap.micro_reactions,
        "channels_built": gap.channels_built,
        "channels_well_skipping": len(gap.channels_well_skipping),
        "unstorable_fields": gap.unstorable_fields,
    }
    src["payload_counts"] = {
        key: len(payload.get(key) or []) for key in ("species", "transition_states", "micro_reactions", "states", "channels")
    }
    src["payload_counts"]["channel_kinetics"] = len((payload.get("solve") or {}).get("channel_kinetics") or [])
    key = "paper-snapshot:pdep:" + str(payload.get("name") or "network")
    with session.client() as client:
        try:
            result = client.upload("network_pdep", payload, idempotency_key=key)
        except TCKDBError as exc:
            report.refuse(SOURCE, "network", exc)
            report.count(SOURCE, "uploads_refused")
            src["status"] = "refused"
            return
    collect_response_warnings(report, SOURCE, "network", result)
    report.count(SOURCE, "uploads_accepted")
    src["response"] = {k: v for k, v in (result or {}).items() if k != "warnings" and not isinstance(v, (list, dict))}
    src["status"] = "deposited"
