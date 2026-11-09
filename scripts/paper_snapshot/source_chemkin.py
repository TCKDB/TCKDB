"""Source 4: the formic acid RMG model, deposited unmodified through the CHEMKIN adapter.

The adapter parses ``chem_annotated.inp``, resolves species through ``species_dictionary.txt`` and posts thermo and
kinetics to ``/uploads/thermo`` and ``/uploads/kinetics``. This module configures that run and records every response
warning, refusal and import warning (the adapter reports wrong ``DUPLICATE`` flags and does not repair them). The model
has no transport file, so no transport is deposited.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

from tckdb_chemkin.identity import IdentityResolver, parse_species_dictionary
from tckdb_chemkin.normalizer import normalize_mechanism
from tckdb_chemkin.parser import parse_mechanism
from tckdb_chemkin.payloads import ImportConfig, build_all_payloads
from tckdb_chemkin.uploader import upload_payloads

from common import REPO_ROOT, Report, Session, collect_response_warnings, digest_files

SOURCE = "chemkin_formic_acid"

#: The cited publication. The literature payload has no author field.
DEFAULT_LITERATURE = {
    "kind": "article",
    "title": "Using RMG Out-of-the-Box for Formic Acid Pyrolysis and Oxidation",
    "journal": "ACS Omega",
    "year": 2026,
    "volume": "11",
    "pages": "6371-6381",
    "doi": "10.1021/acsomega.5c11182",
}


class _Recorder:
    """Wraps the real client so every response the adapter's uploader gets is kept."""

    def __init__(self, client: Any, report: Report) -> None:
        self._client = client
        self._report = report
        self.submission_refs: list[str] = []
        self.codes: Counter[str] = Counter()

    def request_json(self, method: str, path: str, **kwargs: Any):
        try:
            response = self._client.request_json(method, path, **kwargs)
        except Exception as exc:
            self._report.refuse(SOURCE, f"{method} {path} {kwargs.get('idempotency_key', '')}", exc)
            raise
        data = response.data
        collect_response_warnings(self._report, SOURCE, f"{method} {path}", data)
        if isinstance(data, dict) and data.get("submission_ref"):
            self.submission_refs.append(data["submission_ref"])
        return response


def _fixture(cfg: dict[str, Any]) -> Path:
    fixture = Path(cfg.get("fixture_dir", "backend/tests/integration/fixtures/rmg_formic_acid_wu2026"))
    return fixture if fixture.is_absolute() else REPO_ROOT / fixture


def validate(cfg: dict[str, Any]) -> list[str]:
    """Everything that can be checked before a single deposit is made."""
    problems: list[str] = []
    fixture = _fixture(cfg)
    for name in (cfg.get("mechanism_file", "chem_annotated.inp"), cfg.get("species_dictionary", "species_dictionary.txt")):
        if not (fixture / name).is_file():
            problems.append(f"{SOURCE}: {name} not found in the configured fixture directory")
    if cfg.get("scientific_origin", "estimated") not in ("experimental", "estimated", "computed"):
        problems.append(f"{SOURCE}: scientific_origin must be experimental, estimated or computed")
    return problems


def describe_inputs(cfg: dict[str, Any]) -> dict[str, Any]:
    """Count and digest of the files the importer reads, for the snapshot record (no absolute paths)."""
    fixture = _fixture(cfg)
    names = (cfg.get("mechanism_file", "chem_annotated.inp"), cfg.get("species_dictionary", "species_dictionary.txt"))
    return digest_files(fixture, [fixture / n for n in names])


def _find_key(node: Any, key: str) -> Any:
    """First value stored under ``key`` anywhere in a nested JSON document, in document order."""
    if isinstance(node, dict):
        if key in node and node[key]:
            return node[key]
        for value in node.values():
            found = _find_key(value, key)
            if found:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _find_key(value, key)
            if found:
                return found
    return None


def literature_record(client: Any, literature: dict[str, Any] | None, reactant_smiles: str) -> dict[str, Any]:
    """What the instance stored for the cited DOI, and where its title came from.

    The server fetches metadata for a DOI from Crossref when it deposits a
    literature row, so the stored row (title, authors) depends on whether that
    lookup was reachable and what it returned at the time. ``title_source``
    says which: ``resolved_metadata`` (fetched) or ``depositor_supplied``
    (typed by the depositor, here the title in the sources config).

    The literature ref is read from a deposited kinetics record's provenance
    (a public read, scoped by the first reactant of the first deposited reaction),
    then the row is read by ref. Nothing is guessed and no
    error is swallowed: an authentication or network failure, a deposit that
    carries no literature ref, or a row whose DOI is not the one cited stops
    the build.
    """
    if not literature or not literature.get("doi"):
        return {"literature_supplied": False}
    found = client.search_kinetics(reactants=[reactant_smiles], limit=1)
    ref = _find_key(found.get("records") or [], "literature_ref")
    if not ref:
        raise RuntimeError("the deposited kinetics records carry no literature ref although a DOI was supplied")
    detail = client.get_literature(ref, include=["authors"])
    record = detail["record"]
    identifiers = record.get("identifiers") or {}
    if (identifiers.get("doi") or "").lower() != literature["doi"].lower():
        raise RuntimeError(f"the literature row {ref} has DOI {identifiers.get('doi')!r}, not the cited {literature['doi']!r}")
    core = record["literature"]
    return {
        "literature_supplied": True,
        "doi": identifiers.get("doi"),
        "title_stored": core.get("title"),
        "title_supplied_in_config": literature.get("title"),
        "title_source": core.get("title_source"),
        "metadata_resolved_by_the_server": core.get("title_source") == "resolved_metadata",
        "issue_stored": core.get("issue"),
        "publisher_stored": core.get("publisher"),
        "authors_stored": len(record.get("authors") or []),
        "note": "Rebuilding offline, or after the registry changes the record, can change this row.",
    }


def _duplicate_code(message: str) -> str:
    match = re.search(r"\b(orphan|undeclared)\b", message, flags=re.I)
    return "chemkin_duplicate_" + (match.group(1).lower() if match else "flag")


def deposit(session: Session, cfg: dict[str, Any], report: Report, work_dir: Path) -> None:
    fixture = _fixture(cfg)
    mechanism_file = cfg.get("mechanism_file", "chem_annotated.inp")
    dictionary_file = cfg.get("species_dictionary", "species_dictionary.txt")
    src = report.source(SOURCE)

    mechanism = parse_mechanism((fixture / mechanism_file).read_text())
    resolver = IdentityResolver(rmg_dict=parse_species_dictionary((fixture / dictionary_file).read_text()))
    config = ImportConfig(
        scientific_origin=cfg.get("scientific_origin", "estimated"),
        mechanism_name=cfg.get("mechanism_name", "RMG formic acid pyrolysis and oxidation model (Wu and Grinberg Dana 2026)"),
        mechanism_version=cfg.get("mechanism_version"),
        literature=cfg.get("literature", DEFAULT_LITERATURE),
    )
    normalized = normalize_mechanism(mechanism)
    payloads = build_all_payloads(mechanism, resolver, config, normalized=normalized)

    src["parsed"] = {"species": len(mechanism.species), "reaction_rows": len(mechanism.reactions)}
    src["payloads_built"] = payloads.counts()
    dup = Counter(_duplicate_code(w) for w in payloads.warnings if "DUPLICATE" in w.upper())
    src["adapter_import_warnings"] = {
        "total": len(payloads.warnings),
        "duplicate_flag_warnings": sum(dup.values()),
        "by_kind": dict(sorted(dup.items())),
        "messages": list(payloads.warnings),
    }
    for message in payloads.warnings:
        report.warn(SOURCE, "adapter import report", {"code": _duplicate_code(message) if "DUPLICATE" in message.upper() else "chemkin_import_warning", "message": message})

    mechanism_id = cfg.get("mechanism_id", "paper-snapshot:wu2026-formic-acid")
    client = session.client()
    try:
        recorder = _Recorder(client, report)
        upload = upload_payloads(payloads, recorder, mechanism_id=mechanism_id)
    finally:
        client.close()
    with session.client() as lit_client:
        first_reactant = payloads.kinetics[0]["reaction"]["reactants"][0]["species_entry"]["smiles"]
        src["literature"] = literature_record(lit_client, config.literature, first_reactant)
    summary = upload.summary()
    src["upload"] = summary
    src["distinct_submissions"] = len(set(recorder.submission_refs))
    src["status"] = "deposited" if summary["errored_total"] == 0 else f"deposited with {summary['errored_total']} refused records"
    for kind, counts in summary["by_kind"].items():
        report.count(SOURCE, f"{kind}_accepted", counts["ok"])
        report.count(SOURCE, f"{kind}_refused", counts["errored"])
