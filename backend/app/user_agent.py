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


def tckdb_user_agent(suffix: str | None = None) -> str:
    """Build ``TCKDB/<version> (+<project url>[; mailto:<addr>])``, with an optional trailing ``suffix``.

    The mailto part is present only when ``TCKDB_CONTACT_EMAIL`` is set to a non-blank value.
    """
    contact = (os.environ.get(CONTACT_EMAIL_ENV) or "").strip()
    agent = f"TCKDB/{_backend_version()} (+{PROJECT_URL}"
    if contact:
        agent += f"; mailto:{contact}"
    agent += ")"
    return f"{agent} {suffix}" if suffix else agent


_CONTACT = re.compile(r";\s*mailto:[^)\s;]*")


def without_contact(user_agent: str) -> str:
    """``user_agent`` with any ``; mailto:<addr>`` part removed, for anything that is stored or published."""
    return _CONTACT.sub("", user_agent)
