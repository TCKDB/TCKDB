from __future__ import annotations

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


def fetch_doi_metadata(doi: str) -> dict[str, Any] | None:
    """Fetch literature metadata from Crossref for a DOI.

    :param doi: DOI in canonical or raw form.
    :returns: Normalized metadata dictionary, or ``None`` when unavailable.
    """

    normalized_doi = normalize_doi(doi)
    if normalized_doi is None:
        return None

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
    """Fetch literature metadata from an ``isbnlib``-compatible provider.

    :param isbn: ISBN in canonical or raw form.
    :returns: Normalized metadata dictionary, or ``None`` when unavailable.
    """

    normalized_isbn = normalize_isbn(isbn)
    if normalized_isbn is None:
        return None

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
