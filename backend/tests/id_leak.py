"""Whether a database id appears in a message as a token, and not merely as a substring of a public ref.

Public refs are random base32 (the alphabet includes the digits 2-7), so ``str(row.id) in message`` is true by chance for
a small id: about half the time for a single digit. An id leaks when it stands alone, with no letter or digit beside it.
"""

from __future__ import annotations

import re


def id_in_text(row_id: int | str, text: object) -> bool:
    """True when ``row_id`` appears in ``str(text)`` as a whole token (not inside a longer alphanumeric run)."""
    return re.search(rf"(?<![0-9A-Za-z]){re.escape(str(row_id))}(?![0-9A-Za-z])", str(text)) is not None
