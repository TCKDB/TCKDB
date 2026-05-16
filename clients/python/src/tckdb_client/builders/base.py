"""Builder-layer base types: upload protocol + deterministic key minter.

The :class:`UploadObject` protocol is the contract every top-level
upload class must satisfy so :func:`tckdb_client.client.TCKDBClient.upload`
can dispatch to the right endpoint without sniffing payload shape.

The key minter assigns bundle-local string keys (e.g. ``species_1``,
``calc_2``) deterministically based on insertion order. The payload
emitted by ``to_payload()`` must be byte-identical across repeated
calls on the same object graph — see
``clients/python/docs/builder_api_mvp.md`` §10.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from tckdb_client.builders.validation import (
    TCKDBBuilderValidationError,
    slugify_label,
)

__all__ = [
    "UploadObject",
    "KeyMinter",
]


@runtime_checkable
class UploadObject(Protocol):
    """Top-level upload object contract.

    Implementations expose:

    - ``upload_kind`` — a short name matching a key in
      :data:`tckdb_client.client.UPLOAD_ENDPOINTS`.
    - ``to_payload()`` — a pure method that emits the JSON payload dict
      the server expects for that endpoint.
    """

    upload_kind: str

    def to_payload(self) -> dict[str, Any]: ...


class KeyMinter:
    """Assign deterministic local keys based on insertion order.

    A minter is scoped to one ``to_payload()`` walk. Builder objects
    are looked up by ``is``-identity so frozen dataclasses with
    structural equality still get distinct keys when distinct.

    The optional ``label`` from a builder object becomes the base
    slug; colliding slugs receive ``_2``, ``_3``, … suffixes in
    insertion order. Absent labels fall back to ``<prefix>_<n>``.
    """

    def __init__(self, prefix: str) -> None:
        self._prefix = prefix
        self._counter = 0
        self._taken: set[str] = set()
        # Identity-based mapping: a list of (obj, key) pairs walked
        # in insertion order. We avoid building a dict keyed by
        # ``id(obj)`` to honor the §11 ban on memory-address use in
        # payload generation. The list is small (one entry per
        # builder per upload), so linear scan is fine.
        self._entries: list[tuple[Any, str]] = []

    def mint(self, obj: Any, *, label: str | None = None) -> str:
        """Return the key for ``obj``, minting it on first sight."""
        for known, key in self._entries:
            if known is obj:
                return key

        if label is not None:
            base = slugify_label(label)
            candidate = base
            suffix = 2
            while candidate in self._taken:
                candidate = f"{base}_{suffix}"
                suffix += 1
            key = candidate
        else:
            self._counter += 1
            key = f"{self._prefix}_{self._counter}"
            # If a labelled object already claimed our generated
            # name, advance past it deterministically.
            while key in self._taken:
                self._counter += 1
                key = f"{self._prefix}_{self._counter}"

        self._taken.add(key)
        self._entries.append((obj, key))
        return key

    def lookup(self, obj: Any) -> str:
        """Return a previously-minted key, or raise."""
        for known, key in self._entries:
            if known is obj:
                return key
        raise TCKDBBuilderValidationError(
            f"{self._prefix}: object has not been registered with the "
            "key minter; this is a builder bug."
        )
