"""The manifest formats a selection engine writes and reads, held in one registry per engine.

Lives beside the engines, not in ``selection_kernel``: the relative-energy comparison path must not import the kernel.

Each selection engine (thermo, kinetics, statmech, structure, network, relative conformer energy) writes a
manifest whose root ``format`` block (``{"name", "version"}``) says which document shape it is. The engine registers every format it
can read in a :class:`~app.versioning.VersionRegistry` under a stable name (``thermo_selection_manifest``) and an
integer version, with exactly one current:

* the **writer** asks the registry for the current format (:func:`manifest_header`);
* the **reader** looks up the version the manifest *recorded* (:func:`read_manifest_format`), and a version the
  registry does not hold is refused, never read as the current one.

A format that a future release stops writing but still has to read (a published release cites it) is registered as
a second version with ``current=False``. Nothing in this module knows how many versions there are.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, TypeVar

from tckdb_schemas.fragments.version import VersionRef

from app.versioning import UnknownVersion, VersionRegistry

E = TypeVar("E", bound=Exception)


@dataclass(frozen=True)
class ManifestFormat:
    """One readable manifest format: its stable name and its integer version."""

    name: str
    version: int


def new_format_registry(family: str, name: str, current_version: int) -> VersionRegistry[ManifestFormat]:
    """A startup-checked registry holding one manifest format, ``(name, current_version)``, as current."""
    registry: VersionRegistry[ManifestFormat] = VersionRegistry(family, startup_checked=True)
    registry.register(name, current_version, ManifestFormat(name, current_version), current=True)
    registry.validate()
    return registry


def new_current_only_registry(family: str, name: str, current_version: int) -> VersionRegistry[VersionRef]:
    """A startup-checked registry of one versioned thing, ``(name, current_version)``, as current.

    For a thing a manifest records the version of but that has no implementation to dispatch to (an assessment's
    semantics): the registry says which versions are readable, and refuses the rest.
    """
    registry: VersionRegistry[VersionRef] = VersionRegistry(family, startup_checked=True)
    registry.register(name, current_version, VersionRef(name=name, version=current_version), current=True)
    registry.validate()
    return registry


def manifest_header(formats: VersionRegistry[ManifestFormat], name: str) -> dict[str, Any]:
    """The ``format`` block a writer puts at the root of a manifest: the current format's name beside its version."""
    current = formats.current(name)
    return {"name": current.name, "version": current.version}


def read_manifest_format(
    formats: VersionRegistry[ManifestFormat],
    name: str,
    manifest: Mapping[str, Any],
    refuse: Callable[[str], E],
) -> ManifestFormat:
    """The registered format the *manifest* was written under, or ``refuse(message)`` raised.

    Reads the manifest's own root ``format`` block (``{"name", "version"}``). A missing or malformed block, a name other
    than *name*, a version that is not exactly an ``int`` (``True`` and ``2.0`` included) or one the registry does not
    hold is refused with ``unknown manifest format <block>``; it is never replaced by the current format, because
    ``registry.get(name, None)`` would answer "current" and a manifest that records no format must not be read as the
    one this release writes.
    """
    recorded = manifest.get("format")
    if (
        not isinstance(recorded, Mapping)
        or set(recorded) != {"name", "version"}
        or recorded["name"] != name
        or type(recorded["version"]) is not int
    ):
        raise refuse(f"unknown manifest format {recorded!r}")
    try:
        return formats.get(name, recorded["version"])
    except UnknownVersion:
        if recorded["version"] < formats.current(name).version:
            raise refuse(f"unknown manifest format {recorded!r}: an earlier format, not replayable") from None
        raise refuse(f"unknown manifest format {recorded!r}") from None
