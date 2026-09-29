from __future__ import annotations

import copy
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
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
# upstream is seen within the TTL. A failed or empty lookup is *not* cached
# for other callers: the next one retries it, exactly as before.
#
# One narrow exception (#592 item 4), scoped to a single request. The bundle
# dry run wraps its prefetch and its rehearsal in :func:`failure_scope`. A
# lookup that fails inside that scope is remembered *for that scope only*, so
# the rehearsal's own lookup -- made while it holds row locks, and able to
# take ``_REQUEST_TIMEOUT_S`` to fail again -- reuses the failure instead of
# repeating the request. The memory lives in a ``ContextVar`` that the dry
# run sets and resets around its own work: no other caller, request or user
# can ever see it, it is not timed (so a slow failure cannot expire it), and
# it ends with the request. A general negative cache was not adopted:
# Crossref timeouts and 5xx answers are not "this DOI has no metadata", and
# remembering one across callers would make a real submit store a literature
# row without the metadata it would otherwise have fetched, permanently.

METADATA_CACHE_TTL_S = 24 * 60 * 60
METADATA_CACHE_MAX_ENTRIES = 1024
_REQUEST_TIMEOUT_S = 10

#: The failures seen inside the current :func:`failure_scope`, or ``None``
#: outside one (which is everywhere except a dry run's own request).
_scope_failures: ContextVar[set[tuple[str, str]] | None] = ContextVar(
    "literature_metadata_scope_failures", default=None
)

_cache: OrderedDict[tuple[str, str], tuple[float, dict[str, Any]]] = OrderedDict()
_cache_lock = threading.Lock()


def clear_metadata_cache() -> None:
    """Forget every cached lookup."""
    with _cache_lock:
        _cache.clear()


@contextmanager
def failure_scope() -> Iterator[None]:
    """Remember failed lookups for the duration of this block, and this block only."""
    token = _scope_failures.set(set())
    try:
        yield
    finally:
        _scope_failures.reset(token)


def _cached(
    kind: str, key: str, fetch: Callable[[str], dict[str, Any] | None]
) -> dict[str, Any] | None:
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get((kind, key))
        if hit is not None and now - hit[0] < METADATA_CACHE_TTL_S:
            _cache.move_to_end((kind, key))
            return copy.deepcopy(hit[1])
    failures = _scope_failures.get()
    if failures is not None and (kind, key) in failures:
        return None
    value = fetch(key)
    if value is None:
        if failures is not None:
            failures.add((kind, key))
        return None
    with _cache_lock:
        _cache[(kind, key)] = (time.monotonic(), copy.deepcopy(value))
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
            timeout=_REQUEST_TIMEOUT_S,
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
