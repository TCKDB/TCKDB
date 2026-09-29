from __future__ import annotations

import copy
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

# Note: the runtime import path remains `isbnlib`, but in this project we
# install the replacement package via `pip install isbnlib2`.

_CROSSREF_BASE_URL = "https://api.crossref.org/works/"
_CROSSREF_USER_AGENT = (
    "tckdb-literature/1.0 "
    "(https://github.com/TCKDB/TCKDB; "
    "mailto:calvin.p@campus.technion.ac.il)"
)


def normalize_doi(doi: str | None) -> str | None:
    """Normalize DOI text into a canonical stored form.

    :param doi: Raw DOI input or URL-like DOI string.
    :returns: Canonical DOI string, or ``None`` when the input is empty.
    """

    if doi is None:
        return None

    normalized = doi.strip()
    if not normalized:
        return None

    normalized = normalized.removeprefix("DOI:")
    normalized = normalized.removeprefix("doi:")
    normalized = normalized.removeprefix("https://doi.org/")
    normalized = normalized.removeprefix("http://doi.org/")
    return normalized.strip().lower()


def normalize_isbn(isbn: str | None) -> str | None:
    """Validate and normalize ISBN text to canonical ISBN-13.

    :param isbn: Raw ISBN string.
    :returns:
        Canonical ISBN-13 without hyphens, or ``None`` when the input is empty,
        invalid, or the ISBN provider is unavailable.
    """

    if isbn is None:
        return None

    normalized = isbn.strip()
    if not normalized:
        return None

    normalized = normalized.replace("-", "").replace(" ", "")

    try:
        import isbnlib
    except ImportError:
        return None

    if not isbnlib.is_isbn10(normalized) and not isbnlib.is_isbn13(normalized):
        return None

    try:
        return isbnlib.to_isbn13(normalized)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# In-process metadata cache
# ---------------------------------------------------------------------------
#
# A bundle dry run rehearses submit (#577), so without a cache every dry run
# of a bundle citing a new DOI repeated the same Crossref request -- and made
# it while holding the rehearsal's row locks. The cache holds only what the
# provider returned (no database ids, nothing instance-specific), keyed on
# the normalized identifier, bounded in size and aged out, so a correction
# upstream is seen within the TTL. A failed or empty lookup is *not* cached:
# the next caller retries it, exactly as before.

METADATA_CACHE_TTL_S = 24 * 60 * 60
METADATA_CACHE_MAX_ENTRIES = 1024

_cache: OrderedDict[tuple[str, str], tuple[float, dict[str, Any]]] = OrderedDict()
_cache_lock = threading.Lock()


def clear_metadata_cache() -> None:
    """Forget every cached lookup."""
    with _cache_lock:
        _cache.clear()


def _cached(
    kind: str, key: str, fetch: Callable[[str], dict[str, Any] | None]
) -> dict[str, Any] | None:
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get((kind, key))
        if hit is not None and now - hit[0] < METADATA_CACHE_TTL_S:
            _cache.move_to_end((kind, key))
            return copy.deepcopy(hit[1])
    value = fetch(key)
    if value is None:
        return None
    with _cache_lock:
        _cache[(kind, key)] = (now, copy.deepcopy(value))
        _cache.move_to_end((kind, key))
        while len(_cache) > METADATA_CACHE_MAX_ENTRIES:
            _cache.popitem(last=False)
    return value


def fetch_doi_metadata(doi: str) -> dict[str, Any] | None:
    """Fetch literature metadata from Crossref for a DOI, through the cache.

    :param doi: DOI in canonical or raw form.
    :returns: Normalized metadata dictionary, or ``None`` when unavailable.
    """

    normalized_doi = normalize_doi(doi)
    if normalized_doi is None:
        return None
    return _cached("doi", normalized_doi, _fetch_doi_metadata_uncached)


def _fetch_doi_metadata_uncached(normalized_doi: str) -> dict[str, Any] | None:
    try:
        import requests
    except ImportError:
        return None

    headers = {"User-Agent": _CROSSREF_USER_AGENT}
    try:
        response = requests.get(
            f"{_CROSSREF_BASE_URL}{normalized_doi}",
            headers=headers,
            timeout=10,
        )
        response.raise_for_status()
    except requests.RequestException:
        return None

    api_data = response.json()
    metadata = api_data.get("message")
    if metadata is None:
        return None

    return {
        "DOI": metadata.get("DOI"),
        "ISSN": metadata.get("ISSN"),
        "URL": metadata.get("URL"),
        "abstract": metadata.get("abstract"),
        "author": metadata.get("author"),
        "container-title": metadata.get("container-title"),
        "issued": metadata.get("issued", {}).get("date-parts", [[None]])[0][0],
        "publisher": metadata.get("publisher"),
        "page": metadata.get("page"),
        "volume": metadata.get("volume"),
        "issue": metadata.get("issue"),
        "title": metadata.get("title", [None])[0],
        "language": metadata.get("language"),
    }


def fetch_isbn_metadata(isbn: str) -> dict[str, Any] | None:
    """Fetch literature metadata from an ``isbnlib``-compatible provider, cached.

    :param isbn: ISBN in canonical or raw form.
    :returns: Normalized metadata dictionary, or ``None`` when unavailable.
    """

    normalized_isbn = normalize_isbn(isbn)
    if normalized_isbn is None:
        return None
    return _cached("isbn", normalized_isbn, _fetch_isbn_metadata_uncached)


def _fetch_isbn_metadata_uncached(normalized_isbn: str) -> dict[str, Any] | None:
    try:
        import isbnlib
    except ImportError:
        return None

    try:
        metadata = isbnlib.meta(normalized_isbn)
    except Exception:
        return None

    if not metadata:
        return None

    year_raw = metadata.get("Year")
    return {
        "Title": metadata.get("Title"),
        "Authors": metadata.get("Authors"),
        "Publisher": metadata.get("Publisher"),
        "Year": int(year_raw) if year_raw else None,
        "Language": metadata.get("Language"),
        "ISBN": metadata.get("ISBN"),
    }
