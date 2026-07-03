"""CCCBDB resolver diagnostics.

A debugging tool for characterizing how CCCBDB actually serves
per-species data. **Not** production crawling, **not** a snapshot
producer — the JSON it writes is human-facing diagnostic material, not
TCKDB upload data.

Use this module when:

* A URL pattern that looks reasonable (``alldata2x.asp?casno=...``)
  appears broken or redirects to a formula-entry form.
* The browser flow works but no obvious GET URL is available.
* You need to decide whether to invest in a session-aware POST
  resolver before committing to one.

Out of scope:

* Database writes.
* Saving fetched HTML as CCCBDB snapshots (that's
  :mod:`app.importers.cccbdb.snapshot`).
* Full crawl, downstream-link traversal, computed pages.
"""

from app.importers.cccbdb.diagnostics.classifier import (
    Classification,
    classify_html,
)
from app.importers.cccbdb.diagnostics.form_discovery import (
    DiscoveredForm,
    FormField,
    discover_forms,
)
from app.importers.cccbdb.diagnostics.runner import (
    PILOT_TARGETS,
    DiagnosticReport,
    DiagnosticResult,
    DiagnosticTarget,
    Transport,
    TransportResponse,
    run_diagnostics,
)

__all__ = [
    "PILOT_TARGETS",
    "Classification",
    "DiagnosticReport",
    "DiagnosticResult",
    "DiagnosticTarget",
    "DiscoveredForm",
    "FormField",
    "Transport",
    "TransportResponse",
    "classify_html",
    "discover_forms",
    "run_diagnostics",
]
