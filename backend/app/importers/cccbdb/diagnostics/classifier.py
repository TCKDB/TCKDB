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

# Hallmarks of an actual molecule data page (all-data-for-one-species
# style). We require BOTH a molecule-page heading idiom AND at least
# one identifier label so a plain redirect doesn't get misread.
_MOLECULE_DATA_HEADINGS = (
    "all data for one species",
    "all experimental data for one molecule",
    "experimental species data",
    "experimental data for",
)

_MOLECULE_DATA_IDENTIFIERS = (
    "inchi=",
    "inchikey",
    "cas",
    "smiles",
)

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

    # 3) Molecule data page: a per-species heading AND at least one
    # identifier label. Either alone is too weak: the formula-entry
    # form *also* mentions "CAS", and a stray "InChI" in marketing
    # text shouldn't trip the check.
    has_data_heading = _contains_any(lowered, _MOLECULE_DATA_HEADINGS)
    has_identifier = _contains_any(lowered, _MOLECULE_DATA_IDENTIFIERS)
    if has_data_heading and has_identifier:
        return ClassificationResult(
            Classification.molecule_data_page,
            "per-species heading + identifier label present",
        )

    # 4) Formula-entry page: deterministic CCCBDB form text.
    if _contains_any(lowered, _FORMULA_ENTRY_PATTERNS):
        # If the request was for something other than the form page
        # and we landed on a form, this is the redirect-landing case.
        if final_url is not None and final_url != attempted_url:
            return ClassificationResult(
                Classification.redirect_landing_page,
                "served formula-entry form at a different URL than requested",
            )
        return ClassificationResult(
            Classification.formula_entry_page,
            "matched formula-entry form text",
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
