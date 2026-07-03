"""Resolver-diagnostic runner.

Walks a tiny allowlist of molecules through several candidate URL/form
strategies, classifies each response, and writes a single JSON report.
The runner never crawls links, never fetches downstream pages from a
successful response, and never saves CCCBDB HTML as a snapshot.

The transport layer is a :class:`Transport` Protocol so tests can
drive the runner with hand-rolled fake responses; production uses
a thin ``requests.Session`` wrapper that shares cookies across calls
within one diagnostic run (CCCBDB may use ASP session state).
"""

from __future__ import annotations

import hashlib
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from app.importers.cccbdb.diagnostics.classifier import (
    Classification,
    classify_html,
    extract_title,
)
from app.importers.cccbdb.diagnostics.form_discovery import (
    DiscoveredForm,
    discover_forms,
)

DIAGNOSTIC_VERSION = "cccbdb-resolver-diagnostics/0.1.0"
_DEFAULT_USER_AGENT = (
    "tckdb-cccbdb-resolver-diagnostics/0.1 "
    "(+https://github.com/TCKDB/TCKDB; "
    "mailto:calvin.p@campus.technion.ac.il) "
    "phase=resolver-debug"
)


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TransportResponse:
    """One HTTP exchange's outcome.

    ``error`` is non-None when the transport failed before a body
    was returned (DNS, connection, timeout).
    """

    status_code: int | None
    final_url: str | None
    text: str | None
    error: str | None = None


class Transport(Protocol):
    def get(
        self, url: str, *, params: dict[str, str] | None = None
    ) -> TransportResponse: ...  # pragma: no cover

    def post(
        self, url: str, *, data: dict[str, str]
    ) -> TransportResponse: ...  # pragma: no cover


class RequestsTransport:
    """Production transport using a single ``requests.Session``.

    The session persists cookies for the duration of one diagnostic
    run — CCCBDB's per-species data flow likely relies on ASP
    session state, so a fresh session per request would defeat the
    investigation.
    """

    def __init__(
        self,
        *,
        user_agent: str = _DEFAULT_USER_AGENT,
        timeout_seconds: float = 20.0,
    ) -> None:
        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds
        self._session: Any | None = None

    def _ensure_session(self) -> Any:
        if self._session is None:
            import requests

            session = requests.Session()
            session.headers.update({"User-Agent": self.user_agent})
            self._session = session
        return self._session

    def get(
        self, url: str, *, params: dict[str, str] | None = None
    ) -> TransportResponse:
        try:
            session = self._ensure_session()
            resp = session.get(
                url, params=params, timeout=self.timeout_seconds
            )
            return TransportResponse(
                status_code=resp.status_code,
                final_url=resp.url,
                text=resp.text,
                error=None,
            )
        except Exception as exc:
            return TransportResponse(
                status_code=None,
                final_url=None,
                text=None,
                error=f"{type(exc).__name__}: {exc}",
            )

    def post(self, url: str, *, data: dict[str, str]) -> TransportResponse:
        try:
            session = self._ensure_session()
            resp = session.post(
                url, data=data, timeout=self.timeout_seconds
            )
            return TransportResponse(
                status_code=resp.status_code,
                final_url=resp.url,
                text=resp.text,
                error=None,
            )
        except Exception as exc:
            return TransportResponse(
                status_code=None,
                final_url=None,
                text=None,
                error=f"{type(exc).__name__}: {exc}",
            )


# ---------------------------------------------------------------------------
# Target allowlist
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DiagnosticTarget:
    """One molecule the runner will probe.

    Identifiers are independently optional; not every strategy uses
    every identifier.
    """

    molecule_key: str
    formula: str | None = None
    name: str | None = None
    casno: str | None = None  # digits only, e.g. "7732185" (no dashes)
    inchikey: str | None = None


# Hand-curated tiny allowlist (the prompt is explicit about staying small).
PILOT_TARGETS: tuple[DiagnosticTarget, ...] = (
    DiagnosticTarget(
        molecule_key="h2o",
        formula="H2O",
        name="Water",
        casno="7732185",
        inchikey="XLYOFNOQVPJJNP-UHFFFAOYSA-N",
    ),
    DiagnosticTarget(
        molecule_key="h2",
        formula="H2",
        name="Hydrogen diatomic",
        casno="1333740",
        inchikey="UFHFLCQGNIYNRP-UHFFFAOYSA-N",
    ),
    DiagnosticTarget(
        molecule_key="ch4",
        formula="CH4",
        name="Methane",
        casno="74828",
        inchikey="VNWKTOKETHGBQD-UHFFFAOYSA-N",
    ),
    DiagnosticTarget(
        molecule_key="benzene",
        formula="C6H6",
        name="Benzene",
        casno="71432",
        inchikey="UHOVQNZJYSORNB-UHFFFAOYSA-N",
    ),
    DiagnosticTarget(
        molecule_key="ethanol",
        formula="C2H6O",
        name="Ethanol",
        casno="64175",
        inchikey="LFQSCWFLJHTTHZ-UHFFFAOYSA-N",
    ),
)


# ---------------------------------------------------------------------------
# Result + report
# ---------------------------------------------------------------------------


@dataclass
class DiagnosticResult:
    """One (target, strategy) probe outcome."""

    molecule_key: str
    strategy: str
    input: dict[str, str]
    attempted_url: str
    http_status: int | None
    final_url: str | None
    classification: Classification
    diagnostic_reason: str
    content_sha256: str | None
    title: str | None
    error: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "molecule_key": self.molecule_key,
            "strategy": self.strategy,
            "input": self.input,
            "attempted_url": self.attempted_url,
            "http_status": self.http_status,
            "final_url": self.final_url,
            "classification": self.classification.value,
            "diagnostic_reason": self.diagnostic_reason,
            "content_sha256": self.content_sha256,
            "title": self.title,
            "error": self.error,
        }


@dataclass
class DiagnosticReport:
    created_at: str
    user_agent: str
    diagnostic_version: str
    records: list[DiagnosticResult] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "created_at": self.created_at,
            "user_agent": self.user_agent,
            "diagnostic_version": self.diagnostic_version,
            "records": [r.to_json() for r in self.records],
        }


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------


_BASE = "https://cccbdb.nist.gov"
_EXP1X = f"{_BASE}/exp1x.asp"
_ALLDATA2X = f"{_BASE}/alldata2x.asp"


def _sha(text: str | None) -> str | None:
    if text is None:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _classify_response(
    response: TransportResponse,
    *,
    attempted_url: str,
) -> tuple[Classification, str, str | None]:
    """Return (classification, reason, title). Defensive on empty bodies."""

    if response.text is None:
        return (
            Classification.rate_limit_or_error_page
            if response.status_code and response.status_code >= 400
            else Classification.unknown,
            f"empty body (status={response.status_code}, error={response.error!r})",
            None,
        )
    result = classify_html(
        response.text,
        attempted_url=attempted_url,
        final_url=response.final_url,
    )
    return result.classification, result.reason, extract_title(response.text)


def _result_from_response(
    *,
    molecule_key: str,
    strategy: str,
    input_payload: dict[str, str],
    attempted_url: str,
    response: TransportResponse,
) -> DiagnosticResult:
    classification, reason, title = _classify_response(
        response, attempted_url=attempted_url
    )
    return DiagnosticResult(
        molecule_key=molecule_key,
        strategy=strategy,
        input=input_payload,
        attempted_url=attempted_url,
        http_status=response.status_code,
        final_url=response.final_url,
        classification=classification,
        diagnostic_reason=reason,
        content_sha256=_sha(response.text),
        title=title,
        error=response.error,
    )


def _strategy_direct_alldata2x_casno(
    target: DiagnosticTarget, transport: Transport
) -> DiagnosticResult | None:
    if not target.casno:
        return None
    attempted = f"{_ALLDATA2X}?casno={urllib.parse.quote(target.casno)}"
    resp = transport.get(_ALLDATA2X, params={"casno": target.casno})
    return _result_from_response(
        molecule_key=target.molecule_key,
        strategy="direct_alldata2x_casno",
        input_payload={"casno": target.casno},
        attempted_url=attempted,
        response=resp,
    )


def _strategy_exp1x_get_with_formula(
    target: DiagnosticTarget, transport: Transport
) -> DiagnosticResult | None:
    if not target.formula:
        return None
    attempted = (
        f"{_EXP1X}?formula={urllib.parse.quote(target.formula)}"
    )
    resp = transport.get(_EXP1X, params={"formula": target.formula})
    return _result_from_response(
        molecule_key=target.molecule_key,
        strategy="exp1x_get_with_formula",
        input_payload={"formula": target.formula},
        attempted_url=attempted,
        response=resp,
    )


def _strategy_exp1x_form_post(
    target: DiagnosticTarget,
    transport: Transport,
    *,
    discovered_forms: list[DiscoveredForm],
) -> DiagnosticResult | None:
    """POST to the form on ``exp1x.asp`` using whatever field names
    the actual page advertises. Only fires when the discovery step
    found exactly one POST form on ``exp1x.asp`` with at least one
    text/hidden input."""

    if not target.formula:
        return None
    posty = [f for f in discovered_forms if f.method.upper() == "POST"]
    if not posty:
        return None
    form = posty[0]
    # Resolve the form action relative to the exp1x.asp URL.
    action = form.action or _EXP1X
    if action.startswith("/") or not action.startswith("http"):
        action = urllib.parse.urljoin(_EXP1X, action)
    # Pick the first text input we don't recognize as a submit-button
    # and stuff the formula into it. This is fragile by design — the
    # whole point of the diagnostic is to observe what CCCBDB does.
    text_inputs = [
        f
        for f in form.fields
        if f.kind == "input"
        and (f.input_type or "text") in {"text", "search"}
        and f.name
    ]
    if not text_inputs:
        return None
    data: dict[str, str] = {}
    # Include every hidden field's default value so the server's ASP
    # state machine is happy.
    for f in form.fields:
        if f.kind == "input" and f.input_type == "hidden" and f.name:
            data[f.name] = f.default_value or ""
    data[text_inputs[0].name or "formula"] = target.formula
    resp = transport.post(action, data=data)
    return _result_from_response(
        molecule_key=target.molecule_key,
        strategy="exp1x_form_post",
        input_payload=dict(data),
        attempted_url=f"{action} (POST)",
        response=resp,
    )


def _strategy_exp1x_form_post_with_name(
    target: DiagnosticTarget,
    transport: Transport,
    *,
    discovered_forms: list[DiscoveredForm],
) -> DiagnosticResult | None:
    """Variant of the form POST that uses the molecule name instead
    of the formula. Some CCCBDB forms accept either."""

    if not target.name:
        return None
    posty = [f for f in discovered_forms if f.method.upper() == "POST"]
    if not posty:
        return None
    form = posty[0]
    action = form.action or _EXP1X
    if action.startswith("/") or not action.startswith("http"):
        action = urllib.parse.urljoin(_EXP1X, action)
    text_inputs = [
        f
        for f in form.fields
        if f.kind == "input"
        and (f.input_type or "text") in {"text", "search"}
        and f.name
    ]
    if not text_inputs:
        return None
    data: dict[str, str] = {}
    for f in form.fields:
        if f.kind == "input" and f.input_type == "hidden" and f.name:
            data[f.name] = f.default_value or ""
    data[text_inputs[0].name or "formula"] = target.name
    resp = transport.post(action, data=data)
    return _result_from_response(
        molecule_key=target.molecule_key,
        strategy="exp1x_form_post_with_name",
        input_payload=dict(data),
        attempted_url=f"{action} (POST name)",
        response=resp,
    )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def run_diagnostics(
    targets: tuple[DiagnosticTarget, ...],
    transport: Transport,
    *,
    sleep_seconds: float = 2.0,
    user_agent: str = _DEFAULT_USER_AGENT,
    discover_form_on: str = _EXP1X,
) -> DiagnosticReport:
    """Probe each target with every applicable strategy.

    The runner first hits ``exp1x.asp`` once to discover the form
    layout; subsequent POST-strategy calls reuse that discovery for
    every target (no per-target re-fetches of the form page).

    ``sleep_seconds`` is interpreted between *records*, not between
    sub-strategies of the same target.
    """

    report = DiagnosticReport(
        created_at=datetime.now(timezone.utc).isoformat(),
        user_agent=user_agent,
        diagnostic_version=DIAGNOSTIC_VERSION,
    )

    # One-shot form discovery. We do NOT classify this response into
    # the report — it's setup for the POST strategies.
    form_resp = transport.get(discover_form_on)
    discovered_forms = (
        discover_forms(form_resp.text) if form_resp.text else []
    )

    for i, target in enumerate(targets):
        if i > 0 and sleep_seconds > 0:
            time.sleep(sleep_seconds)
        for strategy_callable in (
            _strategy_direct_alldata2x_casno,
            _strategy_exp1x_get_with_formula,
        ):
            result = strategy_callable(target, transport)
            if result is not None:
                report.records.append(result)
        # Form-POST strategies share the one-shot discovery.
        for strategy_callable_with_forms in (
            _strategy_exp1x_form_post,
            _strategy_exp1x_form_post_with_name,
        ):
            result = strategy_callable_with_forms(
                target, transport, discovered_forms=discovered_forms
            )
            if result is not None:
                report.records.append(result)

    return report


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI shim
    """CLI entry point. ``--output-json`` is required so live runs
    leave a deterministic audit trail; the script is intentionally
    not silent-fire-and-forget."""

    import argparse
    import json
    import logging
    from pathlib import Path

    parser = argparse.ArgumentParser(
        prog="cccbdb_resolver_diagnostics",
        description=(
            "Probe CCCBDB per-species data resolution paths and "
            "write a JSON diagnostic report. NOT a production "
            "crawler — see the module docstring."
        ),
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        required=True,
        help="Where to write the JSON diagnostic report.",
    )
    parser.add_argument("--sleep-seconds", type=float, default=2.0)
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    parser.add_argument("--user-agent", default=_DEFAULT_USER_AGENT)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logger = logging.getLogger(__name__)
    logger.info(
        "Running CCCBDB resolver diagnostics against %d targets...",
        len(PILOT_TARGETS),
    )

    transport = RequestsTransport(
        user_agent=args.user_agent,
        timeout_seconds=args.timeout_seconds,
    )
    report = run_diagnostics(
        PILOT_TARGETS,
        transport,
        sleep_seconds=args.sleep_seconds,
        user_agent=args.user_agent,
    )
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(report.to_json(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    logger.info(
        "Wrote %d diagnostic records to %s",
        len(report.records),
        args.output_json,
    )

    # Compact stdout summary so an operator can see at a glance which
    # strategies actually returned molecule data.
    counts: dict[str, int] = {}
    for rec in report.records:
        key = f"{rec.strategy} -> {rec.classification.value}"
        counts[key] = counts.get(key, 0) + 1
    for key in sorted(counts):
        logger.info("  %s : %d", key, counts[key])
    return 0
