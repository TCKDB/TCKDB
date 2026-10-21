"""A registry of versioned things, keyed by (name, version).

TCKDB identifies a versioned thing it owns -- a rubric, a rule, a document
format, a parser -- by a stable ``name`` plus a separate integer ``version``
(:class:`tckdb_schemas.fragments.version.VersionRef`), never by a name with
the version glued on. This module holds the implementations: one
:class:`VersionRegistry` per family of things, mapping ``(name, version)`` to
the object that implements it, with exactly one *current* version per name.

The pattern for a function whose behaviour depends on a version
-----------------------------------------------------------------
Take ``version: int | None = None`` and look the implementation up here::

    def render(snapshot, *, version: int | None = None) -> str:
        return FORMATS.get("tckdb.dataset_release", version)(snapshot)

``None`` means the current version. A reader of a *stored* artifact passes the
version the artifact recorded, explicitly, and never relies on the default: the
default answers "what would we write now", the recorded version answers "what
was this written under".

Three behaviours are mechanisms, not conveniences, and are covered by tests:

* an unknown ``(name, version)`` raises :class:`UnknownVersion` -- nothing
  falls back to the current version, because a replay that silently used a
  different implementation than the one that produced the record is wrong;
* a stored version that is no longer the current one is *stale*
  (:meth:`VersionRegistry.is_current`), which is how an assessment made under
  an older rubric reads as out of date without being rewritten;
* a name that already contains a version is refused at registration, by the
  same patterns the repository's guard test scans with.

This module imports nothing from ``app`` besides the ref type's package, so any
layer can use it without creating an import cycle.
"""

from __future__ import annotations

import weakref
from collections.abc import Iterable
from typing import Generic, TypeVar

from tckdb_schemas.fragments.version import VersionRef

T = TypeVar("T")

#: Registries that opted in to the startup check, held weakly so a registry a
#: test builds and drops never lingers into the next ``create_app()``.
_STARTUP_REGISTRIES: weakref.WeakSet[VersionRegistry] = weakref.WeakSet()


class UnknownVersion(LookupError):
    """The registry holds no implementation for the requested (name, version).

    Carries what was asked for (``family``, ``name``, ``version``; ``version``
    is ``None`` when the *current* version of a name was requested) and what is
    known (``known``: the registered versions of ``name``, or empty when the
    name itself is unknown), so a caller can word a refusal precisely and a
    test can assert on structure instead of on message text.
    """

    def __init__(
        self,
        family: str,
        name: str,
        version: int | None,
        known: Iterable[int] = (),
        *,
        detail: str | None = None,
    ) -> None:
        self.family = family
        self.name = name
        self.version = version
        self.known: tuple[int, ...] = tuple(sorted(known))
        if detail is None:
            wanted = "a current version" if version is None else f"version {version}"
            if self.known:
                held = ", ".join(str(v) for v in self.known)
                detail = f"has no {wanted}; registered versions: {held}"
            else:
                detail = f"has no {wanted}; the name is not registered"
        super().__init__(f"{family}: {name!r} {detail}")


class VersionRegistry(Generic[T]):
    """``(name, version) -> implementation``, one current version per name."""

    def __init__(self, family: str, *, startup_checked: bool = False) -> None:
        """*startup_checked*: include this registry in :func:`validate_startup_registries`.

        Every registry that production code reads sets it, so a name registered
        without a current version stops the boot (``app/api/startup_checks.py``)
        instead of failing on the first request that asks for that name.
        """
        self.family = family
        self.startup_checked = startup_checked
        self._impls: dict[str, dict[int, T]] = {}
        self._current: dict[str, int] = {}
        if startup_checked:
            _STARTUP_REGISTRIES.add(self)

    def register(self, name: str, version: int, impl: T, *, current: bool) -> T:
        """Add *impl* under ``(name, version)`` and return it.

        Refused (``ValueError``) when the name embeds a version or the version
        is not an integer of at least 1 (both checked by building the
        :class:`VersionRef`), when the pair is already registered, or when
        *current* is true and the name already has a current version. A name
        that never gets a current version is caught by :meth:`validate`.
        """
        VersionRef(name=name, version=version)  # raises pydantic.ValidationError (a ValueError)
        versions = self._impls.setdefault(name, {})
        if version in versions:
            raise ValueError(f"{self.family}: {name!r} version {version} is already registered")
        if current and name in self._current:
            raise ValueError(
                f"{self.family}: {name!r} already has current version {self._current[name]}; "
                f"cannot also make version {version} current"
            )
        versions[version] = impl
        if current:
            self._current[name] = version
        return impl

    def validate(self) -> None:
        """Raise ``ValueError`` if any registered name has no current version.

        Call once after the module's registrations, so a registry that is
        malformed fails at import and not on the first request that reaches it.
        """
        missing = sorted(set(self._impls) - set(self._current))
        if missing:
            raise ValueError(f"{self.family}: no current version for {', '.join(missing)}")

    def get(self, name: str, version: int | None = None) -> T:
        """The implementation for ``(name, version)``; ``None`` means current.

        Raises :class:`UnknownVersion` for an unregistered name or version, and
        ``TypeError`` for a *version* that is not exactly an ``int`` (``True``
        and ``1.0`` would otherwise find version 1 through dict equality).
        """
        if version is not None and (type(version) is not int):
            raise TypeError(f"{self.family}: version must be an int or None, not {type(version).__name__}")
        versions = self._impls.get(name)
        if versions is None:
            raise UnknownVersion(self.family, name, version)
        if version is None:
            if name not in self._current:
                raise UnknownVersion(
                    self.family, name, None, versions, detail="has no current version"
                )
            version = self._current[name]
        try:
            return versions[version]
        except KeyError:
            raise UnknownVersion(self.family, name, version, versions) from None

    def current(self, name: str) -> VersionRef:
        """The ref of *name*'s current version."""
        self.get(name)  # raises UnknownVersion for an unknown name or no current
        return VersionRef(name=name, version=self._current[name])

    def is_current(self, ref: VersionRef) -> bool:
        """True only if *ref* is registered and is its name's current version.

        A ref this registry does not hold -- an older version, a name since
        removed -- is simply not current. It does not raise: "made under
        something that is no longer current" is exactly the stale reading.
        """
        return self._current.get(ref.name) == ref.version and ref.version in self._impls.get(
            ref.name, {}
        )

    def refs(self) -> tuple[VersionRef, ...]:
        """Every registered ref, ordered by name then version."""
        return tuple(
            VersionRef(name=name, version=version)
            for name in sorted(self._impls)
            for version in sorted(self._impls[name])
        )


def same_value(recorded: object, expected: object) -> bool:
    """Equality that does not let ``True`` pass for ``1`` or ``1.0`` for ``1``, all the way down.

    A manifest records the versions it was made under; a replay compares them with the running ones. Plain ``==`` (and
    ``!=``, and ``in``) says ``True == 1`` and ``1.0 == 1``, so a manifest recording ``true`` as a version would be read
    as version 1. Here the types must match as well as the values: through dicts (same keys), lists and tuples
    (same length, element by element), and for every scalar. Use this, never ``==``, to compare a recorded version or a
    recorded ``{"name", "version"}`` block with the running one.
    """
    if isinstance(expected, dict):
        return (
            isinstance(recorded, dict)
            and set(recorded) == set(expected)
            and all(same_value(recorded[k], expected[k]) for k in expected)
        )
    if isinstance(expected, (list, tuple)):
        return (
            isinstance(recorded, (list, tuple))
            and len(recorded) == len(expected)
            and all(same_value(r, e) for r, e in zip(recorded, expected, strict=True))
        )
    return type(recorded) is type(expected) and recorded == expected


def validate_startup_registries() -> None:
    """Run :meth:`VersionRegistry.validate` on every startup-checked registry.

    Called from ``create_app()``. Raises ``ValueError`` naming the first
    registry that holds a name with no current version.
    """
    for registry in sorted(_STARTUP_REGISTRIES, key=lambda r: r.family):
        registry.validate()
