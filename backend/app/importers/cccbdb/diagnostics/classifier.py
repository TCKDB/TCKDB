"""Conservative HTML classifier for CCCBDB resolver diagnostics.

The classifier looks at one page's text (lowercased) and returns
which broad shape it most closely resembles. We accept some false
negatives (``unknown`` over a wrong-but-plausible label) because the
diagnostic's job is to describe what came back, not to guess what
the site is doing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class Classification(str, Enum):
    formula_entry_page = "formula_entry_page"
    molecule_data_page = "molecule_data_page"
    property_table_page = "property_table_page"
    rate_limit_or_error_page = "rate_limit_or_error_page"
    redirect_landing_page = "redirect_landing_page"
    unknown = "unknown"


@dataclass(frozen=True)
class ClassificationResult:
    """The classifier's verdict + a short human-readable reason."""

    classification: Classification
    reason: str


# Anything that smells like Cloudflare/Akamai/access-denied static
# content lands here. Cheap regex screen before more expensive checks.
_RATE_LIMIT_PATTERNS = (
    "you are being rate limited",
    "error 1015",
    "cloudflare",
    "access denied",
    "request blocked",
    "request unsuccessful",
    "too many requests",
    "service unavailable",
    "captcha",
)

# Hallmarks of CCCBDB's formula-entry form (Phase 2b WebFetch survey).
_FORMULA_ENTRY_PATTERNS = (
    "please enter the chemical formula",
    "enter a sequence of element symbols followed by numbers",
)

# Phase 5b: *strong* formula-entry signals. These outrank every
# molecule-data heading (including the deceptive
# ``<TITLE>CCCBDB All data for one molecule</TITLE>`` on the form
# page itself, which was the bug this hardening fixes). Any of
# these substrings is sufficient on its own.
#
# Each pattern is matched against the lowercased HTML body. We
# include both the literal form-attribute and the form-text idioms
# so we catch markup variants like ``ACTION = "getformx.asp"``
# (with whitespace around ``=``) as well as plain-text "Rules for
# chemical formula" sidebars.
_STRONG_FORMULA_ENTRY_PATTERNS = (
    "select a species by entering a chemical formula",
    "getformx.asp",
    'name="formula"',
    'name=formula',  # CCCBDB occasionally omits the quotes
    "rules for chemical formula",
)

# Hallmarks of an actual molecule data page. We no longer accept
# "all data for one species" as a sufficient heading — CCCBDB's
# form-entry page *also* carries that title. Instead, a real
# molecule-data page must show evidence of *populated identifier
# values*: a real InChI string, a real InChIKey (14-10-1 caps), or
# a real CAS-number pattern. Identifier *labels* like the bare word
# "CAS" no longer count.
_MOLECULE_DATA_HEADINGS = (
    "all data for one species",
    "all experimental data for one molecule",
    "experimental species data",
    "experimental data for",
)

# Strict identifier-value patterns: each one is a smoking gun that
# only appears on real per-species pages.
_REAL_INCHI_RE = re.compile(r"inchi=1s/[\w/\-+,]+", re.IGNORECASE)
_REAL_INCHIKEY_RE = re.compile(r"\b[A-Z]{14}-[A-Z]{10}-[A-Z]\b")
_REAL_CAS_NUMBER_RE = re.compile(r"\b\d{2,7}-\d{2}-\d\b")

# Property-table hallmarks (Phase 3a-style cross-species pages).
_PROPERTY_TABLE_HEADINGS = (
    "experimental dipoles",
    "species with enthalpy of formation",
    "species with well-known enthalpies of formation",
    "experimental diatomic data",
)

# Hf etc. unit hints commonly seen at the top of cross-species tables.
_PROPERTY_TABLE_UNIT_HINTS = (
    "dipole moments in debye",
    "enthalpies in kj mol",
    "values in cm^-1",
    "values in cm-1",
)


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(n in text for n in needles)


_TITLE_RE = re.compile(r"<title>([^<]+)</title>", re.IGNORECASE)


def extract_title(html: str) -> str | None:
    """Return the first ``<title>`` text, whitespace-collapsed, if any."""

    match = _TITLE_RE.search(html)
    if not match:
        return None
    title = re.sub(r"\s+", " ", match.group(1)).strip()
    return title or None


def classify_html(
    html: str,
    *,
    attempted_url: str,
    final_url: str | None = None,
) -> ClassificationResult:
    """Best-effort classification of one fetched page.

    :param html: Raw HTML body.
    :param attempted_url: The URL the diagnostic *requested*.
    :param final_url: The URL the response was served from after
        redirects. ``None`` indicates the transport did not surface
        a final URL.
    :returns: A :class:`ClassificationResult` with a short reason.
    """

    lowered = html.lower()

    # 1) Rate-limit / error: cheap screen, do this first.
    if _contains_any(lowered, _RATE_LIMIT_PATTERNS):
        hit = next(p for p in _RATE_LIMIT_PATTERNS if p in lowered)
        return ClassificationResult(
            Classification.rate_limit_or_error_page,
            f"matched rate-limit/error marker {hit!r}",
        )

    # 2) Property-table page: a strong heading or unit hint plus a
    # populated <table>.
    if _contains_any(lowered, _PROPERTY_TABLE_HEADINGS) or _contains_any(
        lowered, _PROPERTY_TABLE_UNIT_HINTS
    ):
        if "<table" in lowered:
            return ClassificationResult(
                Classification.property_table_page,
                "property-table heading/units + populated <table>",
            )

    # 3) Formula-entry page: must outrank molecule-data because the
    # CCCBDB form page carries the deceptive title
    # ``CCCBDB All data for one molecule``. ANY strong signal is
    # enough — none of these appear on real per-species pages.
    formula_entry_hit = next(
        (p for p in _STRONG_FORMULA_ENTRY_PATTERNS if p in lowered),
        None,
    )
    if formula_entry_hit is None:
        formula_entry_hit = next(
            (p for p in _FORMULA_ENTRY_PATTERNS if p in lowered), None
        )
    if formula_entry_hit is not None:
        if final_url is not None and final_url != attempted_url:
            return ClassificationResult(
                Classification.redirect_landing_page,
                f"served formula-entry form ({formula_entry_hit!r}) at "
                f"{final_url!r} (attempted {attempted_url!r})",
            )
        return ClassificationResult(
            Classification.formula_entry_page,
            f"matched strong formula-entry signal {formula_entry_hit!r}",
        )

    # 4) Molecule data page: a per-species heading AND evidence of
    # populated identifier *values* (real InChI, real InChIKey, real
    # CAS number pattern). The previous check accepted bare labels
    # like "CAS" or "InChIKey" — that fired on the form page's menu
    # and was the bug this hardening fixes.
    has_data_heading = _contains_any(lowered, _MOLECULE_DATA_HEADINGS)
    has_real_inchi = bool(_REAL_INCHI_RE.search(html))
    has_real_inchikey = bool(_REAL_INCHIKEY_RE.search(html))
    has_real_cas = bool(_REAL_CAS_NUMBER_RE.search(html))
    has_specific_identifier = (
        has_real_inchi or has_real_inchikey or has_real_cas
    )
    if has_data_heading and has_specific_identifier:
        which = (
            "real InChI"
            if has_real_inchi
            else "real InChIKey"
            if has_real_inchikey
            else "real CAS number"
        )
        return ClassificationResult(
            Classification.molecule_data_page,
            f"per-species heading + {which} pattern in body",
        )

    # 5) Generic redirect landing: the URL moved AND content is
    # generic-looking (no identifiers, no property heading, no
    # formula-entry text).
    if final_url is not None and final_url != attempted_url:
        return ClassificationResult(
            Classification.redirect_landing_page,
            f"final_url {final_url!r} differs from attempted_url",
        )

    return ClassificationResult(
        Classification.unknown,
        "no diagnostic markers matched",
    )
