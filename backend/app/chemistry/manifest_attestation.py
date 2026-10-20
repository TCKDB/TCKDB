"""Tell a manifest parsed from pinned bytes from a copy of one that someone edited afterwards.

Every audited rule manifest (the E1 thermo rule, the XYG3 barrier rule, the network and the structure rule
candidates) is pinned: a rule refuses to run over a manifest whose ``sha256`` is not the digest of the reviewed
file. That ``sha256`` is a field of the loaded dataclass, so it says which bytes the manifest *claims* to come from,
not what it now contains. ``dataclasses.replace(manifest, activation_approved=True, activation_blockers=())`` keeps
the field and changes the content, and a rule that read only the field would report an approval nobody gave.

The fix is to compare what the manifest holds with what the pinned bytes held. When a bytes parser has checked the
bytes and built the manifest, it :func:`attest`\\ s it: this module remembers, per byte digest, a digest of the
manifest's whole content (every field except ``sha256`` itself, nested entries included). :func:`is_attested` recomputes
that digest from the object it is given, so a manifest edited in any field, however it was edited, no longer matches
and a rule refuses it. A manifest built from a parsed document alone has no byte digest and is never attested.

The check runs when a rule is constructed, including the no-argument form over the cached default manifest (the production
path), so a cached manifest whose nested dicts were changed in place is refused at the next construction. A manifest changed
*after* its rule exists is not detected.

This is a guard against an edited copy, not against code that calls :func:`attest` itself; nothing outside the four
bytes parsers does. Moving a pin to the digest of edited bytes (a real approval is a new manifest and a new pin) still
works, because the parser attests whatever bytes it was given and the rule then checks the pin.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from typing import Any

#: SHA-256 of the manifest bytes a parser accepted -> digest of the content it built from them.
_ATTESTED: dict[str, str] = {}


def _plain(value: Any) -> Any:
    """The value as JSON-able plain data with string keys, so the digest does not depend on dict key types."""
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [_plain(v) for v in value]
        return sorted(items, key=repr) if isinstance(value, (set, frozenset)) else items
    return value


def content_digest(manifest: Any) -> str:
    """SHA-256 of the manifest's content: every dataclass field except ``sha256``, nested entries included."""
    fields = dataclasses.asdict(manifest)
    fields.pop("sha256", None)
    canonical = json.dumps(_plain(fields), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def attest(manifest: Any) -> Any:
    """Record that ``manifest`` was parsed from the bytes whose digest it carries. For the bytes parsers only."""
    if not manifest.sha256:
        raise ValueError("only a manifest parsed from bytes can be attested")
    _ATTESTED[manifest.sha256] = content_digest(manifest)
    return manifest


def is_attested(manifest: Any) -> bool:
    """Whether the manifest still holds exactly what the bytes it names produced."""
    sha256 = getattr(manifest, "sha256", None)
    if not isinstance(sha256, str) or not sha256:
        return False
    return _ATTESTED.get(sha256) == content_digest(manifest)


def require_integer_version(value: Any, *, where: str, error: type[Exception]) -> int:
    """*value* if it is exactly an integer of at least 1, else ``error(...)`` raised.

    The version of a manifest or of a rule is an integer. A text version (``"1"``, ``"0.2.0"``), a float and a ``True``
    are all refused: a version that arrives as text is the first step back to a name with a version glued on.
    """
    if type(value) is not int or value < 1:
        raise error(f"{where}: version must be an integer of at least 1, not {value!r}")
    return value


def require_manifest_name(value: Any, *, where: str, error: type[Exception]) -> str:
    """*value* if it is a non-empty string that does not embed a version, else ``error(...)`` raised."""
    from tckdb_schemas.fragments.version import embeds_version

    if not isinstance(value, str) or not value.strip() or embeds_version(value):
        raise error(f"{where}: name must be a non-empty string with no version in it, not {value!r}")
    return value
