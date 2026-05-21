"""Session-aware POST resolver for CCCBDB form-only experimental pages.

Reproduces, programmatically, the human workflow:

    1. GET an entry page like ``ea1x.asp`` (atomization energy form).
    2. Discover the form (action URL + formula input field name).
    3. POST ``formula=<symbol>`` with the session cookies from step 1.
    4. Classify the returned HTML.
    5. Archive the page; parse it if the classification is workflow-ready.

The transport is :class:`requests.Session` by default — CCCBDB's form
flow only needs cookie propagation, not JavaScript or a real browser.
Tests inject a fake ``Session`` via the :class:`SessionLike` protocol
so the unit suite never touches the network.

This module is intentionally narrow:

* **One target_kind per call.** The queue's per-record ``target_kind``
  decides which result parser to use (today, only ``atomization_energy``).
* **No catalog expansion.** The resolver consumes an explicit queue
  file written by a maintainer. There is no auto-discovery from
  inchix.asp or any other source.
* **No DB writes.** All output lands as raw HTML + parsed JSON +
  manifest entries on disk.
* **Cloudflare-aware but not Cloudflare-evading.** A rate-limit page
  classification stops the queue after ``stop_after_rate_limit_errors``
  consecutive hits — the resolver does NOT retry or rotate identities.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Protocol
from urllib.parse import urljoin

from app.importers.cccbdb import (
    PARSER_VERSION,
    SOURCE_DATABASE_DOI,
    SOURCE_NAME,
    SOURCE_RELEASE,
)
from app.importers.cccbdb.diagnostics.classifier import (
    Classification,
    classify_html,
)
from app.importers.cccbdb.parsers import (
    CCCBDBSelectionCandidate,
    CCCBDBSpeciesSelectionPage,
    SUPPORTED_TARGET_KINDS,
    canonicalize_cas,
    parse_form_result_page,
    parse_species_selection_page,
)
from app.importers.cccbdb.parsers.species_selection import (
    structural_to_hill_formula,
)

_logger = logging.getLogger(__name__)


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (compatible; TCKDB-cccbdb-importer/0.1; "
    "+https://github.com/anthropics/tckdb)"
)


# ---------------------------------------------------------------------------
# Queue + result dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FormQueueRecord:
    """One queue entry — what the maintainer wants the resolver to fetch."""

    species_key: str
    formula: str
    target_kind: str
    entry_url: str
    name: str | None = None
    cas_number: str | None = None
    inchikey: str | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "FormQueueRecord":
        missing = [
            k for k in ("species_key", "formula", "target_kind", "entry_url")
            if not d.get(k)
        ]
        if missing:
            raise ValueError(f"queue record missing required fields: {missing}")
        return cls(
            species_key=d["species_key"],
            formula=d["formula"],
            target_kind=d["target_kind"],
            entry_url=d["entry_url"],
            name=d.get("name"),
            cas_number=d.get("cas_number"),
            inchikey=d.get("inchikey"),
        )


@dataclass
class FormResolveResult:
    """Per-record resolver outcome (mirrors snapshot manifest entry)."""

    species_key: str
    formula: str
    name: str | None
    target_kind: str
    entry_url: str
    form_action_url: str | None = None
    final_url: str | None = None
    resolver_strategy: str = "requests_session_form_post"
    classification: str | None = None
    classification_reason: str | None = None
    accepted_as_data: bool = False
    content_sha256: str | None = None
    raw_html_path: str | None = None
    parsed_json_path: str | None = None
    rejected_html_path: str | None = None
    parser_warnings: list[str] = field(default_factory=list)
    resolver_warnings: list[str] = field(default_factory=list)
    retrieved_at: str | None = None
    # Selection metadata (populated only when the request hit a
    # ``choosex.asp`` page).
    selection_policy: str | None = None
    selection_status: str | None = None
    selection_match_basis: str | None = None
    selection_candidate_count: int | None = None
    selected_name: str | None = None
    selected_cas_number: str | None = None
    selection_warnings: list[str] = field(default_factory=list)

    def to_manifest_entry(self) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "species_key": self.species_key,
            "formula": self.formula,
            "name": self.name,
            "target_kind": self.target_kind,
            "entry_url": self.entry_url,
            "form_action_url": self.form_action_url,
            "final_url": self.final_url,
            "resolver_strategy": self.resolver_strategy,
            "classification": self.classification,
            "classification_reason": self.classification_reason,
            "accepted_as_data": self.accepted_as_data,
            "content_sha256": self.content_sha256,
            "raw_html_path": self.raw_html_path,
            "parsed_json_path": self.parsed_json_path,
            "rejected_html_path": self.rejected_html_path,
            "parser_warnings": list(self.parser_warnings),
            "resolver_warnings": list(self.resolver_warnings),
            "retrieved_at": self.retrieved_at,
        }
        # Only emit selection_* keys when the resolver actually
        # engaged the selection branch — otherwise the manifest stays
        # backwards-compatible with prior runs.
        if self.selection_policy is not None:
            entry["selection_policy"] = self.selection_policy
            entry["selection_status"] = self.selection_status
            entry["selection_match_basis"] = self.selection_match_basis
            entry["selection_candidate_count"] = self.selection_candidate_count
            entry["selected_name"] = self.selected_name
            entry["selected_cas_number"] = self.selected_cas_number
            entry["selection_warnings"] = list(self.selection_warnings)
        return entry


# ---------------------------------------------------------------------------
# Session abstraction
# ---------------------------------------------------------------------------


@dataclass
class SessionResponse:
    """Minimal response shape we depend on (text + status + final URL)."""

    text: str
    status_code: int
    url: str


class SessionLike(Protocol):
    """Subset of ``requests.Session`` we depend on.

    The real session is a thin wrapper over ``requests.Session``; the
    test fakes implement ``get``/``post`` directly without needing
    ``requests`` installed.
    """

    def get(self, url: str, *, timeout: float | None = ...) -> SessionResponse: ...
    def post(
        self,
        url: str,
        *,
        data: dict[str, str],
        timeout: float | None = ...,
    ) -> SessionResponse: ...


@dataclass
class RequestsSession:
    """Production session — thin shim over :class:`requests.Session`."""

    user_agent: str = DEFAULT_USER_AGENT
    _session: Any = None

    def __post_init__(self) -> None:
        import requests  # local import: tests inject SessionLike directly

        self._session = requests.Session()
        self._session.headers.update({"User-Agent": self.user_agent})

    def get(self, url: str, *, timeout: float | None = 30.0) -> SessionResponse:
        r = self._session.get(url, timeout=timeout, allow_redirects=True)
        return SessionResponse(text=r.text, status_code=r.status_code, url=r.url)

    def post(
        self,
        url: str,
        *,
        data: dict[str, str],
        timeout: float | None = 30.0,
    ) -> SessionResponse:
        r = self._session.post(
            url, data=data, timeout=timeout, allow_redirects=True
        )
        return SessionResponse(text=r.text, status_code=r.status_code, url=r.url)


# ---------------------------------------------------------------------------
# Form discovery
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DiscoveredForm:
    """One ``<form>`` discovered on the entry page."""

    action_url: str
    method: str
    formula_field: str  # name of the text input for the formula


class _FormDiscoveryParser(HTMLParser):
    """Find the first ``<form>`` whose action contains ``getformx.asp``
    AND that holds a text input named ``formula``. Returns the form's
    action and the formula field name."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: list[dict[str, Any]] = []
        self._current: dict[str, Any] | None = None

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attrs_d = {k.lower(): (v or "") for k, v in attrs}
        if tag == "form":
            self._current = {
                "action": attrs_d.get("action", ""),
                "method": attrs_d.get("method", "GET").upper(),
                "inputs": [],
            }
        elif tag == "input" and self._current is not None:
            self._current["inputs"].append(
                {
                    "type": attrs_d.get("type", "text").lower(),
                    "name": attrs_d.get("name", ""),
                }
            )

    def handle_endtag(self, tag):
        if tag.lower() == "form" and self._current is not None:
            self.forms.append(self._current)
            self._current = None


def discover_form(html: str, base_url: str) -> DiscoveredForm | None:
    """Return the first ``<form>`` on ``html`` whose action looks like
    a CCCBDB form submission AND that holds a text input named
    ``formula``. Returns ``None`` when no such form exists."""

    parser = _FormDiscoveryParser()
    parser.feed(html)
    parser.close()

    for form in parser.forms:
        action = form["action"] or ""
        action_lc = action.lower()
        if "getformx.asp" not in action_lc:
            # Filter out the menu's own form-clear / submit forms.
            continue
        formula_input = next(
            (
                i for i in form["inputs"]
                if i["type"] in {"text", ""} and i["name"].lower() == "formula"
            ),
            None,
        )
        if formula_input is None:
            continue
        action_abs = urljoin(base_url, action)
        return DiscoveredForm(
            action_url=action_abs,
            method=form["method"] or "POST",
            formula_field=formula_input["name"],
        )
    return None


# ---------------------------------------------------------------------------
# Selection-page policy
# ---------------------------------------------------------------------------


class SelectionPolicy(str, Enum):
    """How the resolver handles ``choosex.asp`` species-selection pages.

    * :attr:`REJECT_AMBIGUOUS` — the conservative default. Any
      ``choosex.asp`` response is treated as a rejection. No POST to
      ``fixchoicex.asp`` is attempted.
    * :attr:`EXACT_MATCH` — parse the candidates and select exactly one
      *only* if the queue record carries enough identity to match on
      ``formula+name``, ``formula+cas_number``, or ``formula+inchikey``.
      Zero or multiple unrelated matches → reject as ambiguous.
      Multiple matches that resolve to the SAME form-field value
      (e.g. two conformers of the same species sharing one CAS) are
      treated as a single unambiguous selection.

    Formula-only matching is NEVER allowed. Picking the first
    candidate is NEVER allowed.
    """

    REJECT_AMBIGUOUS = "reject_ambiguous"
    EXACT_MATCH = "exact_match"


@dataclass(frozen=True)
class SelectionOutcome:
    """Result of applying a :class:`SelectionPolicy` to a parsed
    ``choosex.asp`` page against one queue record."""

    selected: CCCBDBSelectionCandidate | None
    status: str  # "selected" | "ambiguous_or_no_match" | "no_candidates"
    match_basis: str | None  # e.g. "formula+name"
    candidate_count: int
    matched_candidate_count: int
    matched_choice_values: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


_PUNCT_RE = re.compile(r"[\s\-_,.]+")


def _normalize_name(name: str | None) -> str | None:
    if not name:
        return None
    folded = name.casefold().strip()
    # Strip trailing punctuation CCCBDB sometimes appends to display
    # names ("Ethanol ", "Dimethyl ether ", etc.).
    return _PUNCT_RE.sub(" ", folded).strip() or None


def _normalize_formula(formula: str | None) -> str | None:
    if not formula:
        return None
    # Strip whitespace + lowercase; we trust the parser to have
    # already collapsed ``<sub>2</sub>`` → ``2`` etc.
    return _PUNCT_RE.sub("", formula.strip()).lower() or None


def _normalize_inchikey(key: str | None) -> str | None:
    if not key:
        return None
    return key.strip().upper() or None


def _candidate_matches(
    candidate: CCCBDBSelectionCandidate, record: "FormQueueRecord"
) -> str | None:
    """Return the match-basis label (e.g. ``"formula+name"``) if the
    candidate unambiguously matches ``record``, else ``None``.

    Formula is required on both sides; one of (name, cas, inchikey)
    must match in addition. Order is deterministic — CAS beats name
    when both match, because CAS is the stronger identifier; name
    beats inchikey when CCCBDB exposes name but not inchikey.

    Formula comparison is forgiving in one specific way: CCCBDB's
    ``choosex.asp`` table shows the *structural* formula
    (``CH3CH2OH``) while queue records typically carry the *molecular*
    formula (``C2H6O``). The matcher therefore compares the queue
    formula against the candidate's literal formula AND against its
    derived Hill-system molecular formula. Both must agree on the
    same canonical form (Hill is preferred when derivable).
    """

    rec_formula = _normalize_formula(record.formula)
    cand_formula = _normalize_formula(candidate.formula)
    if rec_formula is None or cand_formula is None:
        return None
    if rec_formula != cand_formula:
        # Try the structural→Hill derivation.
        cand_hill = _normalize_formula(
            structural_to_hill_formula(candidate.formula)
        )
        rec_hill = _normalize_formula(
            structural_to_hill_formula(record.formula)
        )
        if not (
            (cand_hill is not None and rec_formula == cand_hill)
            or (rec_hill is not None and rec_hill == cand_formula)
            or (
                cand_hill is not None
                and rec_hill is not None
                and rec_hill == cand_hill
            )
        ):
            return None

    # CAS first (strongest identifier).
    rec_cas = canonicalize_cas(record.cas_number)
    cand_cas = canonicalize_cas(candidate.cas_number)
    if rec_cas is not None and cand_cas is not None and rec_cas == cand_cas:
        return "formula+cas"

    # Name second.
    rec_name = _normalize_name(record.name)
    cand_name = _normalize_name(candidate.name)
    if rec_name is not None and cand_name is not None and rec_name == cand_name:
        return "formula+name"

    # InChIKey last (choosex.asp does not surface it today, but the
    # matcher supports it for forward compatibility / future per-row
    # enrichment).
    rec_key = _normalize_inchikey(record.inchikey)
    cand_key = _normalize_inchikey(candidate.inchikey)
    if rec_key is not None and cand_key is not None and rec_key == cand_key:
        return "formula+inchikey"

    return None


def select_candidate(
    page: CCCBDBSpeciesSelectionPage,
    record: "FormQueueRecord",
    policy: SelectionPolicy,
) -> SelectionOutcome:
    """Apply the configured selection policy to a parsed selection page.

    With :attr:`SelectionPolicy.REJECT_AMBIGUOUS`, every selection
    page is rejected outright — no parsing of candidates required.
    With :attr:`SelectionPolicy.EXACT_MATCH`, candidates are filtered
    by :func:`_candidate_matches`; if exactly one (deduplicated by
    form-field value) survives, it's the selection.
    """

    if policy == SelectionPolicy.REJECT_AMBIGUOUS:
        return SelectionOutcome(
            selected=None,
            status="ambiguous_or_no_match",
            match_basis=None,
            candidate_count=len(page.candidates),
            matched_candidate_count=0,
            warnings=(
                "selection_policy=reject_ambiguous; selection page rejected "
                "without parsing candidates",
            ),
        )

    if not page.candidates:
        return SelectionOutcome(
            selected=None,
            status="no_candidates",
            match_basis=None,
            candidate_count=0,
            matched_candidate_count=0,
            warnings=("selection page contains no candidate rows",),
        )

    matches: list[tuple[CCCBDBSelectionCandidate, str]] = []
    for candidate in page.candidates:
        basis = _candidate_matches(candidate, record)
        if basis is not None:
            matches.append((candidate, basis))

    if not matches:
        return SelectionOutcome(
            selected=None,
            status="ambiguous_or_no_match",
            match_basis=None,
            candidate_count=len(page.candidates),
            matched_candidate_count=0,
            warnings=(
                "no candidate matched on formula+name, formula+cas, or "
                "formula+inchikey; formula-only selection is not allowed",
            ),
        )

    # Deduplicate by form-field value: if every match maps to the
    # same POST payload, they're really one selection (e.g. two
    # conformers of the same species sharing one CAS on CCCBDB).
    distinct_values = {c.form_field_value for c, _ in matches}
    if len(distinct_values) != 1:
        return SelectionOutcome(
            selected=None,
            status="ambiguous_or_no_match",
            match_basis=None,
            candidate_count=len(page.candidates),
            matched_candidate_count=len(matches),
            matched_choice_values=tuple(sorted(distinct_values)),
            warnings=(
                f"{len(matches)} candidates matched the queue record but "
                f"resolved to {len(distinct_values)} distinct choice values; "
                "selection rejected as ambiguous",
            ),
        )

    selected, basis = matches[0]
    return SelectionOutcome(
        selected=selected,
        status="selected",
        match_basis=basis,
        candidate_count=len(page.candidates),
        matched_candidate_count=len(matches),
        matched_choice_values=(selected.form_field_value,),
    )


# ---------------------------------------------------------------------------
# Resolver config + main class
# ---------------------------------------------------------------------------


SessionFactory = Callable[[], SessionLike]


@dataclass
class FormResolverConfig:
    """Runtime knobs for a queue run."""

    output_dir: Path
    session_factory: SessionFactory | None = None
    sleep_seconds: float = 15.0
    max_pages: int = 3
    stop_after_rate_limit_errors: int = 1
    save_rejected_html: bool = False
    allow_unknown: bool = False
    selection_policy: SelectionPolicy = SelectionPolicy.REJECT_AMBIGUOUS
    user_agent: str = DEFAULT_USER_AGENT


_ACCEPTED_CLASSIFICATIONS: frozenset[str] = frozenset({
    Classification.form_result_data_page,
    Classification.molecule_data_page,
})


@dataclass
class FormResolverRunSummary:
    records_seen: int = 0
    accepted: int = 0
    rejected: int = 0
    stopped_after_rate_limit: bool = False
    results: list[FormResolveResult] = field(default_factory=list)


def run_form_resolver_queue(
    records: list[FormQueueRecord],
    config: FormResolverConfig,
) -> FormResolverRunSummary:
    """Resolve every queue record, archiving + parsing accepted pages
    and updating ``manifest.json`` under ``config.output_dir``.

    The function is reusable from both the CLI and tests. It never
    fetches the network directly — :class:`SessionLike` is the only
    transport seam.
    """

    config.output_dir.mkdir(parents=True, exist_ok=True)
    (config.output_dir / "raw_html").mkdir(exist_ok=True)
    (config.output_dir / "parsed").mkdir(exist_ok=True)
    if config.save_rejected_html:
        (config.output_dir / "rejected_html").mkdir(exist_ok=True)

    session_factory = config.session_factory or (lambda: RequestsSession(
        user_agent=config.user_agent
    ))
    session = session_factory()

    summary = FormResolverRunSummary()
    rate_limit_strikes = 0
    pages_attempted = 0

    for record in records:
        if pages_attempted >= config.max_pages:
            _logger.info(
                "Reached max_pages=%d; stopping queue", config.max_pages
            )
            break
        if rate_limit_strikes >= config.stop_after_rate_limit_errors:
            _logger.warning(
                "Rate-limit threshold reached (%d); stopping queue",
                rate_limit_strikes,
            )
            summary.stopped_after_rate_limit = True
            break

        # Polite delay between non-first requests.
        if pages_attempted > 0 and config.sleep_seconds > 0:
            time.sleep(config.sleep_seconds)

        result = resolve_one_record(record, config, session)
        summary.results.append(result)
        summary.records_seen += 1
        pages_attempted += 1

        if result.accepted_as_data:
            summary.accepted += 1
        else:
            summary.rejected += 1
            if (
                result.classification
                == Classification.rate_limit_or_error_page
            ):
                rate_limit_strikes += 1

    _merge_manifest(config.output_dir, summary.results)
    return summary


def resolve_one_record(
    record: FormQueueRecord,
    config: FormResolverConfig,
    session: SessionLike,
) -> FormResolveResult:
    """Resolve a single queue record using ``session``.

    Sequence:

    1. GET ``record.entry_url`` (form-bearing entry page).
    2. Discover the form (action URL + formula input name).
    3. POST formula to action URL.
    4. Classify result; accept if classification is in
       :data:`_ACCEPTED_CLASSIFICATIONS`.
    5. Archive and (if accepted + target supported) parse.
    """

    retrieved_at = datetime.now(timezone.utc).isoformat()
    result = FormResolveResult(
        species_key=record.species_key,
        formula=record.formula,
        name=record.name,
        target_kind=record.target_kind,
        entry_url=record.entry_url,
        retrieved_at=retrieved_at,
    )

    try:
        entry_resp = session.get(record.entry_url)
    except Exception as exc:  # noqa: BLE001 — keep queue going
        result.resolver_warnings.append(
            f"GET entry_url failed: {type(exc).__name__}: {exc}"
        )
        return result

    if entry_resp.status_code != 200:
        result.resolver_warnings.append(
            f"GET {record.entry_url} returned HTTP {entry_resp.status_code}"
        )
        return result

    form = discover_form(entry_resp.text, record.entry_url)
    if form is None:
        result.resolver_warnings.append(
            "no getformx-form discovered on entry page; "
            "either the page shape changed or the URL is not a form-entry page"
        )
        # Classify the entry page anyway so the maintainer sees what came back.
        verdict = classify_html(
            entry_resp.text,
            attempted_url=record.entry_url,
            final_url=entry_resp.url,
        )
        result.classification = verdict.classification.value
        result.classification_reason = verdict.reason
        return result

    result.form_action_url = form.action_url

    try:
        post_resp = session.post(
            form.action_url, data={form.formula_field: record.formula}
        )
    except Exception as exc:  # noqa: BLE001
        result.resolver_warnings.append(
            f"POST {form.action_url} failed: {type(exc).__name__}: {exc}"
        )
        return result

    if post_resp.status_code != 200:
        result.resolver_warnings.append(
            f"POST {form.action_url} returned HTTP {post_resp.status_code}"
        )
        return result

    result.final_url = post_resp.url
    result.content_sha256 = hashlib.sha256(
        post_resp.text.encode("utf-8")
    ).hexdigest()

    verdict = classify_html(
        post_resp.text,
        attempted_url=form.action_url,
        final_url=post_resp.url,
    )
    result.classification = verdict.classification.value
    result.classification_reason = verdict.reason

    # Selection branch: species_selection_page may be re-resolved by
    # following the choosex.asp → fixchoicex.asp POST when the
    # selection policy allows AND the queue record carries enough
    # identity to match a single candidate unambiguously.
    if verdict.classification == Classification.species_selection_page:
        selection_page = parse_species_selection_page(
            post_resp.text, base_url=post_resp.url
        )
        outcome = select_candidate(
            selection_page, record, config.selection_policy
        )
        result.selection_policy = config.selection_policy.value
        result.selection_status = outcome.status
        result.selection_match_basis = outcome.match_basis
        result.selection_candidate_count = outcome.candidate_count
        if outcome.selected is not None:
            result.selected_name = outcome.selected.name
            result.selected_cas_number = outcome.selected.cas_number
        result.selection_warnings.extend(outcome.warnings)

        if outcome.selected is None:
            # Selection rejected — leave the original page as the
            # archived artifact (rejected_html if --save-rejected-html).
            if config.save_rejected_html:
                result.rejected_html_path = _archive_rejected(
                    config.output_dir, record, result, post_resp.text
                )
            return result

        # Selection accepted — POST the selected candidate to the
        # form's action URL (fixchoicex.asp), then carry on as if the
        # response had come back directly.
        if selection_page.form_action_url is None:
            result.resolver_warnings.append(
                "selection candidate matched but no form action URL "
                "discovered on the page; cannot POST selection"
            )
            return result
        try:
            selection_resp = session.post(
                selection_page.form_action_url,
                data=outcome.selected.form_fields(),
            )
        except Exception as exc:  # noqa: BLE001
            result.resolver_warnings.append(
                f"POST {selection_page.form_action_url} failed: "
                f"{type(exc).__name__}: {exc}"
            )
            return result
        if selection_resp.status_code != 200:
            result.resolver_warnings.append(
                f"selection POST returned HTTP {selection_resp.status_code}"
            )
            return result

        # Re-point the result to the selection follow-up page.
        result.final_url = selection_resp.url
        result.content_sha256 = hashlib.sha256(
            selection_resp.text.encode("utf-8")
        ).hexdigest()
        verdict = classify_html(
            selection_resp.text,
            attempted_url=selection_page.form_action_url,
            final_url=selection_resp.url,
        )
        result.classification = verdict.classification.value
        result.classification_reason = verdict.reason
        # Reassign the local POST response so the archive/parse branch
        # below works against the selection-follow-up page, not the
        # choosex.asp page.
        post_resp = selection_resp

    accept = verdict.classification.value in _ACCEPTED_CLASSIFICATIONS
    if not accept and config.allow_unknown and \
            verdict.classification == Classification.unknown:
        accept = True
        result.resolver_warnings.append(
            "accepted unknown classification because --allow-unknown is set"
        )

    if accept:
        result.accepted_as_data = True
        result.raw_html_path = _archive_accepted(
            config.output_dir, record, result, post_resp.text
        )
        if record.target_kind in SUPPORTED_TARGET_KINDS:
            parsed = parse_form_result_page(
                post_resp.text,
                target_kind=record.target_kind,
                source_url=record.entry_url,
                final_url=post_resp.url,
            )
            result.parser_warnings.extend(parsed.warnings)
            result.parsed_json_path = _archive_parsed(
                config.output_dir, record, result, parsed
            )
        else:
            result.parser_warnings.append(
                f"target_kind {record.target_kind!r} is not yet parsed; "
                "raw HTML archived only"
            )
    elif config.save_rejected_html:
        result.rejected_html_path = _archive_rejected(
            config.output_dir, record, result, post_resp.text
        )

    return result


# ---------------------------------------------------------------------------
# Archive + manifest helpers
# ---------------------------------------------------------------------------


_FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def _safe_token(text: str) -> str:
    return _FILENAME_SAFE_RE.sub("_", text).strip("_") or "x"


def _archive_filename(
    target_kind: str, species_key: str, sha256: str
) -> str:
    sha12 = sha256[:12]
    return (
        f"form_{_safe_token(target_kind)}_{_safe_token(species_key)}_"
        f"{sha12}.html"
    )


def _archive_accepted(
    output_dir: Path,
    record: FormQueueRecord,
    result: FormResolveResult,
    html: str,
) -> str:
    raw_dir = output_dir / "raw_html"
    fname = _archive_filename(
        record.target_kind, record.species_key, result.content_sha256 or ""
    )
    path = raw_dir / fname
    path.write_text(html, encoding="utf-8")
    return f"raw_html/{fname}"


def _archive_rejected(
    output_dir: Path,
    record: FormQueueRecord,
    result: FormResolveResult,
    html: str,
) -> str:
    rej_dir = output_dir / "rejected_html"
    rej_dir.mkdir(exist_ok=True)
    sha = result.content_sha256 or hashlib.sha256(
        html.encode("utf-8")
    ).hexdigest()
    fname = _archive_filename(record.target_kind, record.species_key, sha)
    path = rej_dir / fname
    path.write_text(html, encoding="utf-8")
    return f"rejected_html/{fname}"


def _archive_parsed(
    output_dir: Path,
    record: FormQueueRecord,
    result: FormResolveResult,
    parsed,  # CCCBDBFormResultTable; avoid forward import for typing
) -> str:
    parsed_dir = output_dir / "parsed"
    fname = (
        f"form_{_safe_token(record.target_kind)}_"
        f"{_safe_token(record.species_key)}_"
        f"{(result.content_sha256 or '')[:12]}.json"
    )
    path = parsed_dir / fname
    payload = {
        "target_kind": parsed.target_kind,
        "title": parsed.title,
        "column_names": list(parsed.column_names),
        "raw_units": parsed.raw_units,
        "source_url": parsed.source_url,
        "final_url": parsed.final_url,
        "content_sha256": parsed.content_sha256,
        "source_metadata": {
            "source": SOURCE_NAME,
            "source_release": SOURCE_RELEASE,
            "source_database_doi": SOURCE_DATABASE_DOI,
            "parser_version": PARSER_VERSION,
            "resolver_strategy": result.resolver_strategy,
            "species_key": record.species_key,
            "queue_formula": record.formula,
            "queue_name": record.name,
        },
        "rows": [
            {
                "row_index": r.row_index,
                "formula": r.formula,
                "name": r.name,
                "value": r.value,
                "unit": r.unit,
                "uncertainty": r.uncertainty,
                "secondary_values": dict(r.secondary_values),
                "raw_row": dict(r.raw_row),
                "reference_label": r.reference_label,
                "reference_comment": r.reference_comment,
                "warnings": list(r.warnings),
            }
            for r in parsed.rows
        ],
        "warnings": list(parsed.warnings),
    }
    # Preserve the selection-stage metadata on disk so the downstream
    # form-payload builder can carry it forward into
    # ``raw_payload_json["selection"]`` without re-running the resolver.
    if result.selection_policy is not None:
        payload["selection"] = {
            "selection_policy": result.selection_policy,
            "selection_status": result.selection_status,
            "selection_match_basis": result.selection_match_basis,
            "selection_candidate_count": result.selection_candidate_count,
            "selected_name": result.selected_name,
            "selected_cas_number": result.selected_cas_number,
            "selection_warnings": list(result.selection_warnings),
        }
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return f"parsed/{fname}"


def _merge_manifest(
    output_dir: Path, results: list[FormResolveResult]
) -> None:
    """Merge new resolver records into ``manifest.json``.

    Uses the same shape the snapshot manifest writes: a ``records``
    list keyed by ``(species_key, target_kind, content_sha256)``.
    Existing entries with the same key are replaced; entries from
    earlier runs (different sha) are preserved.
    """

    path = output_dir / "manifest.json"
    existing: dict[str, Any] = {
        "builder_version": "cccbdb-form-resolver/0.1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": SOURCE_NAME,
        "source_release": SOURCE_RELEASE,
        "source_database_doi": SOURCE_DATABASE_DOI,
        "parser_version": PARSER_VERSION,
        "records": [],
    }
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            existing.setdefault("records", [])
        except json.JSONDecodeError:
            _logger.warning(
                "manifest.json was malformed; rewriting from scratch"
            )

    by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for r in existing.get("records", []):
        key = (
            r.get("species_key") or "",
            r.get("target_kind") or r.get("page_kind") or "",
            r.get("content_sha256") or "",
        )
        by_key[key] = r

    for res in results:
        entry = res.to_manifest_entry()
        key = (res.species_key, res.target_kind, res.content_sha256 or "")
        by_key[key] = entry

    existing["records"] = list(by_key.values())
    path.write_text(
        json.dumps(existing, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Queue loading
# ---------------------------------------------------------------------------


def load_queue_file(path: Path) -> list[FormQueueRecord]:
    """Parse a queue JSON file into a list of :class:`FormQueueRecord`.

    Expected shape::

        {"records": [{...}, {...}]}
    """

    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"queue file root must be an object, got {type(raw)}")
    records = raw.get("records")
    if not isinstance(records, list):
        raise ValueError("queue file must have a 'records' list")
    return [FormQueueRecord.from_dict(r) for r in records]


__all__ = [
    "DEFAULT_USER_AGENT",
    "DiscoveredForm",
    "FormQueueRecord",
    "FormResolveResult",
    "FormResolverConfig",
    "FormResolverRunSummary",
    "RequestsSession",
    "SelectionPolicy",
    "SessionLike",
    "SessionResponse",
    "discover_form",
    "load_queue_file",
    "resolve_one_record",
    "run_form_resolver_queue",
]
