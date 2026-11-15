"""The User-Agent TCKDB sends on outbound HTTP requests."""

from __future__ import annotations

import os
import re
from importlib import metadata

PROJECT_URL = "https://github.com/TCKDB/TCKDB"
CONTACT_EMAIL_ENV = "TCKDB_CONTACT_EMAIL"


def _backend_version() -> str:
    try:
        return metadata.version("tckdb-backend")
    except metadata.PackageNotFoundError:
        return "unknown"


def _contact_part() -> str:
    contact = (os.environ.get(CONTACT_EMAIL_ENV) or "").strip()
    return f"; mailto:{contact}" if contact else ""


def tckdb_user_agent(suffix: str | None = None) -> str:
    """Build ``TCKDB/<version> (+<project url>[; mailto:<addr>])``, with an optional trailing ``suffix``.

    The mailto part is present only when ``TCKDB_CONTACT_EMAIL`` is set to a non-blank value.
    """
    agent = f"TCKDB/{_backend_version()} (+{PROJECT_URL}{_contact_part()})"
    return f"{agent} {suffix}" if suffix else agent


def browser_compatible_user_agent(mode: str) -> str:
    """Build ``Mozilla/5.0 (compatible; TCKDB/<version>; +<url>; <mode>[; mailto:<addr>])``.

    For sites that flag agents without a browser-style product token. The mailto part follows the same rule
    as :func:`tckdb_user_agent`.
    """
    return f"Mozilla/5.0 (compatible; TCKDB/{_backend_version()}; +{PROJECT_URL}; {mode}{_contact_part()})"


_CONTACT = re.compile(r";?\s*mailto:\s*[^)\s;]*", re.IGNORECASE)


def without_contact(user_agent: str) -> str:
    """``user_agent`` with any ``mailto:<addr>`` part removed, for anything that is stored or published."""
    out = _CONTACT.sub("", user_agent)
    out = re.sub(r"\(\s*;\s*", "(", out)
    out = re.sub(r"\(\s*\)", "", out)
    return re.sub(r"\s{2,}", " ", out).strip()
