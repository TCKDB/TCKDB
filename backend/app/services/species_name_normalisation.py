"""The one normaliser for species names, shared by writes and the name search.

``name_key`` is what is stored and what a search compares against: Unicode NFKC, case-folded, typographic apostrophes
folded to ``'``, runs of whitespace collapsed to one space, ends trimmed.
"""

from __future__ import annotations

import unicodedata

#: Typographic apostrophes and primes folded to ``'``.
_APOSTROPHE_CHARS = "\u2018\u2019\u201a\u201b\u02bc\u02b9\u2032\u02c8`\u00b4\uff07"
_APOSTROPHES = str.maketrans(_APOSTROPHE_CHARS, "'" * len(_APOSTROPHE_CHARS))

#: Punctuation a query may carry besides letters, digits and the space.
ALLOWED_PUNCTUATION = frozenset("-,'()[]+.:")

MIN_QUERY_LENGTH = 2
MAX_QUERY_LENGTH = 64
MAX_NAME_LENGTH = 128

_MAX_CODE_POINT = 0x10FFFF


def normalise_name(text: str) -> str:
    """The normalised key of a name or a query."""
    folded = unicodedata.normalize("NFKC", unicodedata.normalize("NFKC", text).casefold())
    return " ".join(folded.translate(_APOSTROPHES).split())


def _allowed_char(ch: str) -> bool:
    if ch == " " or ch in ALLOWED_PUNCTUATION:
        return True
    category = unicodedata.category(ch)
    return category[0] in "LM" or category == "Nd"


def is_valid_query_key(raw: str, key: str) -> bool:
    """Whether ``raw`` (as sent) and its normalised ``key`` are an acceptable name query.

    Control characters in the raw text (a tab aside) are refused before normalisation could turn them into spaces.
    The key must be 2 to 64 characters of letters, marks, decimal digits, space and ``- , ' ( ) [ ] + . :``.
    """
    if any(unicodedata.category(ch) == "Cc" and ch != "\t" for ch in raw):
        return False
    if not MIN_QUERY_LENGTH <= len(key) <= MAX_QUERY_LENGTH:
        return False
    return all(_allowed_char(ch) for ch in key)


def prefix_upper_bound(key: str) -> str | None:
    """The smallest string above every string that starts with ``key``, in code-point order.

    The last code point is incremented (skipping the surrogate block); trailing maximal code points are dropped first.
    ``None`` when there is no upper bound (every character is the maximum).
    """
    chars = list(key)
    while chars and ord(chars[-1]) == _MAX_CODE_POINT:
        chars.pop()
    if not chars:
        return None
    nxt = ord(chars[-1]) + 1
    if 0xD800 <= nxt <= 0xDFFF:
        nxt = 0xE000
    chars[-1] = chr(nxt)
    return "".join(chars)
