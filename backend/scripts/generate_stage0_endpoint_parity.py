#!/usr/bin/env python
"""Generate the Stage 0 endpoint/client parity baseline.

Usage (from ``backend``)::

    conda run -n tckdb_env python scripts/generate_stage0_endpoint_parity.py

The input is the committed OpenAPI golden snapshot, not a network request.
Hosted production intentionally disables ``/openapi.json``. The output is a
reviewable Markdown and JSON inventory under ``docs/reviews``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
OPENAPI = BACKEND / "tests/api/golden/openapi.json"
MARKDOWN = ROOT / "docs/reviews/tckdb_stage0_endpoint_parity_2026-07-30.md"
JSON_OUTPUT = ROOT / "docs/reviews/tckdb_stage0_endpoint_parity_2026-07-30.json"
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}


def _audience(path: str) -> str:
    if path.startswith("/api/v1/scientific/export/"):
        return "curator/admin"
    if path == "/api/v1/scientific/artifacts/{sha256}/download":
        return "authenticated"
    if path.startswith("/api/v1/scientific/"):
        return "anonymous public"
    if path.startswith("/api/v1/uploads/") or path.startswith("/api/v1/bundles/"):
        return "authenticated contributor"
    if path.startswith("/api/v1/jobs/"):
        return "authenticated (authorization gate incomplete)"
    if path.startswith("/api/v1/admin/") or path.startswith("/api/v1/curation/"):
        return "admin/curator"
    if path.startswith("/api/v1/submissions/") or path.startswith("/api/v1/record-reviews/"):
        return "authenticated / role-specific"
    if path.startswith("/api/v1/"):
        return "deployment/configuration dependent or endpoint-specific"
    return "outside API v1"


def _surface(path: str, method: str) -> str:
    if path.startswith("/api/v1/scientific/export/"):
        return "export"
    if path.startswith("/api/v1/scientific/"):
        return "query" if method in {"get", "post"} else "scientific"
    if path.startswith("/api/v1/uploads/"):
        return "sync ingestion"
    if path.startswith("/api/v1/bundles/"):
        return "bundle ingestion v0 (thermo/kinetics only)"
    if path.startswith("/api/v1/jobs/"):
        return "async ingestion"
    return "other API"


def _typed_client_method(path: str, method: str) -> str | None:
    mappings = {
        "/api/v1/scientific/species/search": "search_species, iter_species",
        "/api/v1/scientific/reactions/search": "search_reactions, iter_reactions",
        "/api/v1/scientific/reaction-entries/{reaction_entry_id}/kinetics": "get_reaction_kinetics",
        "/api/v1/scientific/reaction-entries/{reaction_entry_id}/full": "get_reaction_full",
        "/api/v1/scientific/species-entries/{species_entry_id}/thermo": "get_species_thermo",
        "/api/v1/scientific/thermo/search": "search_thermo, iter_thermo",
        "/api/v1/scientific/kinetics/search": "search_kinetics, iter_kinetics",
        "/api/v1/scientific/species-calculations/search": "search_species_calculations, iter_species_calculations",
        "/api/v1/scientific/calculations/search": "search_calculations",
        "/api/v1/scientific/calculations/{calculation_ref_or_id}": "get_calculation",
        "/api/v1/scientific/geometries/{geometry_handle}": "get_geometry",
        "/api/v1/scientific/networks/search": "search_networks, iter_networks",
        "/api/v1/scientific/network-solves/search": "search_network_solves, iter_network_solves",
        "/api/v1/scientific/network-solves/{network_solve_ref_or_id}": "get_network_solve",
        "/api/v1/scientific/network-kinetics/search": "search_network_kinetics, iter_network_kinetics",
        "/api/v1/scientific/statmech/search": "search_statmech, iter_statmech",
        "/api/v1/scientific/transport/search": "search_transport, iter_transport",
        "/api/v1/scientific/artifacts/search": "search_artifacts, iter_artifacts",
    }
    return mappings.get(path)


def _client_coverage(path: str, method: str) -> tuple[str, str]:
    typed = _typed_client_method(path, method)
    if typed:
        return typed, "typed convenience"
    if path.startswith("/api/v1/uploads/"):
        return "upload (kind dispatch)" if method == "post" else "—", "typed convenience"
    if path == "/api/v1/bundles/dry-run":
        return "bundle_dry_run", "typed convenience"
    if path == "/api/v1/bundles/submit":
        return "bundle_submit", "typed convenience"
    if method == "get":
        return "get_json / request_json", "raw HTTP helper only"
    if method == "post":
        return "post_json / request_json", "raw HTTP helper only"
    return "request_json", "raw HTTP helper only"


def _rows(schema: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for path, path_item in sorted(schema["paths"].items()):
        for method, operation in sorted(path_item.items()):
            if method not in HTTP_METHODS:
                continue
            client_method, client_coverage = _client_coverage(path, method)
            rows.append(
                {
                    "method": method.upper(),
                    "path": path,
                    "operation_id": operation.get("operationId", ""),
                    "tags": ", ".join(operation.get("tags", [])),
                    "surface": _surface(path, method),
                    "audience": _audience(path),
                    "typed_client": client_method,
                    "client_coverage": client_coverage,
                }
            )
    return rows


def _escape(value: str) -> str:
    return value.replace("|", "\\|")


def main() -> None:
    schema = json.loads(OPENAPI.read_text())
    rows = _rows(schema)
    payload = {
        "generated_from": str(OPENAPI.relative_to(ROOT)),
        "generation_command": "conda run -n tckdb_env python backend/scripts/generate_stage0_endpoint_parity.py",
        "operation_count": len(rows),
        "rows": rows,
    }
    JSON_OUTPUT.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")

    lines = [
        "# Generated Stage 0 endpoint/client parity matrix",
        "",
        "Generated by `backend/scripts/generate_stage0_endpoint_parity.py` from the committed OpenAPI golden snapshot.",
        "This is a source/CI contract inventory; hosted production intentionally disables `/openapi.json`.",
        "`typed convenience` is a first-class `TCKDBClient` method; `raw HTTP helper only` means the client can call",
        "the endpoint through `get_json`, `post_json`, or `request_json` but has no endpoint-specific convenience method.",
        "Audience labels are source-policy classifications and must not be inferred from the OpenAPI security section alone.",
        "",
        f"**Operations:** {len(rows)}",
        f"**Input:** `{OPENAPI.relative_to(ROOT)}`",
        "",
        "| Method | Path | Surface | Audience | Typed client / raw helper | Coverage | Operation ID |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            "| {method} | `{path}` | {surface} | {audience} | `{typed_client}` | {client_coverage} | `{operation_id}` |".format(
                **{key: _escape(value) for key, value in row.items()}
            )
        )
    MARKDOWN.write_text("\n".join(lines) + "\n")
    print(f"Wrote {MARKDOWN.relative_to(ROOT)} and {JSON_OUTPUT.relative_to(ROOT)} ({len(rows)} operations)")


if __name__ == "__main__":
    main()
