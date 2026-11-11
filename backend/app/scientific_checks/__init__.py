"""Self-declaration for TCKDB's scientific checks.

A scientific check enforces chemistry (elemental balance, charge conservation,
the imaginary-mode count of a saddle point, the bijectivity of an atom map)
rather than plumbing such as string lengths or enum membership. Each one is
declared as a :class:`ScientificCheck` and collected into the register
rendered at ``docs/guides/scientific_check_register.md``.

How to add one
--------------
Construct a :class:`ScientificCheck` at module level, immediately below the
check it describes::

    CHECK_REACTION_ELEMENTAL_BALANCE = ScientificCheck(
        code="reaction_mass_balance_failed",
        asserts="The reactant and product sides of a reaction contain "
                "the same number of atoms of every element.",
        tier=CheckTier.block,
        channel=CodeChannel.error_envelope,
        enforced_by=(PythonCheck(validate_reaction_elemental_balance),),
        escape_hatch="Declare a participant with ``molecule_kind: pseudo``.",
    )

A check that declares a code must raise it: the code goes to
:class:`~tckdb_schemas.coded_error.CodedValidationError` as its first
argument. Then add the module to ``DECLARING_MODULES`` in :mod:`.declarations`
and regenerate the register with
``backend/scripts/generate_scientific_check_register.py``.

A declaration is a frozen dataclass: it wraps nothing and registers nothing at
import time. :func:`collect_registered_checks` walks the declaring modules'
namespaces. PostgreSQL constraints and wire-schema checks, which have no
backend object to sit beside, are declared in :mod:`.declarations`.

What the drift guard enforces
-----------------------------
``backend/tests/db/test_scientific_check_register.py`` checks that:

* every :class:`PythonCheck` resolves to a real callable;
* every :class:`DatabaseConstraint` exists in the live PostgreSQL schema;
* every code declared as ``emitted=True`` appears in the source of the module
  that defines the enforcing function;
* every entry declares a :class:`CodeChannel`, and a code and a channel imply
  each other;
* every ``error_envelope`` code is the first argument of a
  ``CodedValidationError`` and is asserted by an end-to-end test;
* every file containing ``ScientificCheck(`` is listed in
  ``DECLARING_MODULES``;
* every :class:`ProvenanceThreshold` resolves to a real callable and reads
  parameter keys that exist in ``calculation_parameter_vocab``;
* codes are unique across the register, and the printed entry numbers do not
  change.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import ModuleType


class CheckTier(str, Enum):
    """Consequence of a check firing.

    ``block`` and ``warn`` carry the same string values as
    :class:`tckdb_schemas.stationary_point.ValidationTier`; this enum is
    documentation, that one is control flow.
    """

    #: Refuse the payload. A check may block only if no correct calculation
    #: could produce the record it refuses.
    block = "block"

    #: Accept the payload and record a machine-readable
    #: :class:`~tckdb_schemas.upload_warning.UploadWarning`.
    warn = "warn"

    #: Label a stored record at read time without refusing anything, as a
    #: :class:`~app.services.trust.models.HardFailReason` in the trust
    #: evaluator.
    label = "label"

    #: Refer the record to ``machine_review``. Every cross-check against
    #: external reference data is here.
    review = "review"

    #: Enforced by the shape of the schema (a NOT NULL discriminator, a check
    #: constraint, a trigger), so a violating record cannot be represented.
    structural = "structural"


class CodeChannel(str, Enum):
    """Where a check's code surfaces to a client."""

    #: The ``code`` field of the 422 error envelope, carried by a
    #: :class:`~tckdb_schemas.coded_error.CodedValidationError`. The enforcing
    #: module must construct the coded error with this code, and an
    #: end-to-end test must assert the code arrives in an HTTP response body.
    error_envelope = "error_envelope"

    #: The ``code`` field of an
    #: :class:`~tckdb_schemas.upload_warning.UploadWarning` returned alongside a
    #: successful upload. The deposit also stores each warning beside the
    #: record it concerns (``deposit_warning``), so the same code reaches a
    #: later reader in ``record_warnings`` (or ``deposit_warnings``) on that
    #: record's detail reads and on the per-parent kinetics and thermo reads.
    upload_warning = "upload_warning"

    #: A read-time label, a ``HardFailReason`` from the trust evaluator,
    #: carried in the trust payload of a record.
    trust_label = "trust_label"

    #: Enforced only by PostgreSQL, so the refusal arrives through the
    #: integrity handler as a 409 rather than a 422. A constraint that declares
    #: :attr:`DatabaseConstraint.rejection_code` is reported under that code.
    database_constraint = "database_constraint"

    #: No machine-readable code reaches anybody.
    none = "none"


# ---------------------------------------------------------------------------
# Where a check is enforced
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PythonCheck:
    """A check enforced by Python code, identified by the function object.

    Holding the callable rather than a dotted string means renaming or
    deleting the function breaks the import of the declaring module.

    :param func: The function that performs the check.
    """

    func: Callable[..., object]

    @property
    def location(self) -> str:
        """``path/to/module.py::qualified_name``, resolved from the live object.

        A qualified name rather than a line number, so editing code above a
        check does not change the generated register.
        """
        source_file = inspect.getsourcefile(self.func)
        if source_file is None:  # pragma: no cover - builtins only
            return f"{self.func.__module__}.{self.func.__qualname__}"
        return f"{_repo_relative(source_file)}::{self.func.__qualname__}"

    @property
    def label(self) -> str:
        return f"``{self.func.__qualname__}``"


#: Constraint kinds for which PostgreSQL populates ``diag.constraint_name`` on
#: the error it raises, which is what the integrity handler keys on. A NOT NULL
#: violation (SQLSTATE 23502) arrives with ``constraint_name = None``.
_NAMED_BY_POSTGRES = frozenset({"check", "unique", "foreign_key", "exclusion"})


@dataclass(frozen=True)
class DatabaseConstraint:
    """A check enforced by PostgreSQL, identified by its schema object name.

    The name must be the expanded one that exists in the database
    (``ck_reaction_atom_map_pair_element_matches``), not the short form written
    in ``__table_args__``. The drift guard verifies it against live schema
    metadata.

    :param table: Table the object is attached to.
    :param name: Expanded object name as PostgreSQL holds it.
    :param kind: ``check``, ``unique``, ``foreign_key`` or ``trigger``.
    :param definition: The SQL predicate, for the register document.
    :param rejection_code: The code the 409 body carries when this object is
        the one that fired. ``None`` means the refusal arrives as the
        SQLSTATE-derived category.
    :param rejection_detail: The sanitized public sentence sent with
        ``rejection_code``. ASCII only: it is a runtime string.
    """

    table: str
    name: str
    kind: str
    definition: str | None = None
    rejection_code: str | None = None
    rejection_detail: str | None = None

    def __post_init__(self) -> None:
        if bool(self.rejection_code) != bool(self.rejection_detail):
            raise ValueError(
                f"DatabaseConstraint({self.name!r}) declares "
                f"rejection_code={self.rejection_code!r} and "
                f"rejection_detail={self.rejection_detail!r}; declare both or neither."
            )
        if self.rejection_code and self.kind not in _NAMED_BY_POSTGRES:
            raise ValueError(
                f"DatabaseConstraint({self.name!r}) is a {self.kind!r} and "
                f"declares rejection_code={self.rejection_code!r}, but the "
                "handler keys on ``diag.constraint_name`` and PostgreSQL "
                f"populates it only for {sorted(_NAMED_BY_POSTGRES)}."
            )

    @property
    def location(self) -> str:
        return f"{self.table}.{self.name}"

    @property
    def label(self) -> str:
        return f"``{self.name}`` ({self.kind} on ``{self.table}``)"


@dataclass(frozen=True)
class DesignPosition:
    """A scientific position enforced by schema shape rather than a check.

    :param where: Description of what carries the position, with paths where
        they help.
    """

    where: str

    @property
    def location(self) -> str:
        return self.where

    @property
    def label(self) -> str:
        return self.where


Enforcement = PythonCheck | DatabaseConstraint | DesignPosition


# ---------------------------------------------------------------------------
# Where a check's threshold comes from
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConstantThreshold:
    """A numeric line fixed in code.

    :param name: Identifier a reader can grep for.
    :param value: The number.
    :param unit: Its unit.
    :param rationale: One plain sentence on what the number means physically.
    """

    name: str
    value: float
    unit: str
    rationale: str

    @property
    def summary(self) -> str:
        return f"{self.value:g} {self.unit}"


@dataclass(frozen=True)
class ProvenanceThreshold:
    """A numeric line resolved from what the record records about its own execution.

    :param name: Identifier a reader can grep for.
    :param unit: Unit of the resolved value.
    :param resolver: The function that performs the resolution.
    :param parameter_keys: Canonical ``calculation_parameter`` keys the
        resolver reads, verified against ``calculation_parameter_vocab``.
    :param values: The resolved value per recorded protocol, as
        ``(protocol, value)`` pairs.
    :param fallback: What is used when the provenance is absent.
    :param rationale: One plain sentence on why the threshold is not a constant.
    """

    name: str
    unit: str
    resolver: Callable[..., object]
    parameter_keys: tuple[str, ...]
    values: tuple[tuple[str, str], ...]
    fallback: str
    rationale: str

    @property
    def location(self) -> str:
        """``path/to/module.py::qualified_name`` of the resolver."""
        source_file = inspect.getsourcefile(self.resolver)
        if source_file is None:  # pragma: no cover - builtins only
            return f"{self.resolver.__module__}.{self.resolver.__qualname__}"
        return f"{_repo_relative(source_file)}::{self.resolver.__qualname__}"

    @property
    def summary(self) -> str:
        return f"resolved per record, in {self.unit}"


Threshold = ConstantThreshold | ProvenanceThreshold


# ---------------------------------------------------------------------------
# The declaration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScientificCheck:
    """One scientific guarantee TCKDB makes about chemistry.

    The same claim may be enforced in more than one place, for example once at
    the wire boundary and once as a check constraint.

    :param code: Machine-readable code, or a tuple of them where one claim is
        reported under more than one code. ``None`` means the check raises
        with prose only.
    :param asserts: One plain sentence saying what the check requires.
    :param tier: Consequence tier.
    :param enforced_by: One or more enforcement sites.
    :param escape_hatch: One plain sentence on how legitimate chemistry the
        check would otherwise refuse is deposited instead. ``None`` where none
        exists.
    :param emitted: Whether the code reaches a producer. The drift guard
        asserts that the code appears in the source of the module defining the
        enforcing function.
    :param channel: Where the code surfaces to a client; see
        :class:`CodeChannel`. Must be :attr:`CodeChannel.none` exactly when
        the entry declares no code.
    :param divergence: One plain sentence on a limit of the check, where its
        guarantee is narrower than its name suggests.
    :param thresholds: The numeric lines the check fires on. Empty where the
        check is purely structural.
    :param group: Register group the entry is printed under.
    :param sort_key: Position within the group.
    """

    code: str | tuple[str, ...] | None
    asserts: str
    tier: CheckTier
    enforced_by: tuple[Enforcement, ...]
    escape_hatch: str | None = None
    emitted: bool = True
    channel: CodeChannel = CodeChannel.none
    divergence: str | None = None
    thresholds: tuple[Threshold, ...] = ()
    group: str = "Uncategorised"
    sort_key: int = 0

    @property
    def codes(self) -> tuple[str, ...]:
        """Every machine-readable code this entry reports under."""
        if self.code is None:
            return ()
        if isinstance(self.code, str):
            return (self.code,)
        return self.code

    def __post_init__(self) -> None:
        if not self.enforced_by:
            raise ValueError(f"ScientificCheck({self.code!r}) declares no enforcement site.")
        if bool(self.codes) != (self.channel is not CodeChannel.none):
            raise ValueError(
                f"ScientificCheck({self.code!r}) declares "
                f"{len(self.codes)} code(s) and channel={self.channel.value}; "
                "a code needs a channel and a channel needs a code."
            )


def _repo_relative(path: str) -> str:
    """Render an absolute source path relative to the repository root."""
    resolved = Path(path).resolve()
    for parent in resolved.parents:
        if (parent / ".git").exists():
            return str(resolved.relative_to(parent))
    return resolved.name


def collect_registered_checks(modules: tuple[ModuleType, ...]) -> list[ScientificCheck]:
    """Gather every module-level :class:`ScientificCheck` in ``modules``.

    Collection reads namespaces rather than relying on registration side
    effects, so a declaration is inert until something asks for it and
    two imports of the same module cannot double-register.

    :param modules: Already-imported modules to scan.
    :returns: Checks, deduplicated by identity and ordered by group then
        declared ``sort_key`` then code, so the generated document is
        stable across runs.
    """

    seen: dict[int, ScientificCheck] = {}
    for module in modules:
        for value in vars(module).values():
            if isinstance(value, ScientificCheck):
                seen.setdefault(id(value), value)
    return sorted(
        seen.values(),
        key=lambda check: (check.group, check.sort_key, check.codes),
    )


def collect_constraint_rejections(
    checks: list[ScientificCheck],
) -> dict[str, tuple[str, str]]:
    """Map PostgreSQL constraint name to the ``(code, detail)`` a 409 carries.

    The integrity handler builds its lookup from this.

    :param checks: The register, as :func:`collect_registered_checks`
        returns it.
    :returns: Constraint name to ``(code, public detail)``.
    :raises ValueError: If two entries claim the same constraint name
        with different codes, which would make the refusal depend on
        dictionary order.
    """

    rejections: dict[str, tuple[str, str]] = {}
    for check in checks:
        for site in check.enforced_by:
            if not isinstance(site, DatabaseConstraint) or not site.rejection_code:
                continue
            assert site.rejection_detail is not None  # enforced in __post_init__
            entry = (site.rejection_code, site.rejection_detail)
            existing = rejections.setdefault(site.name, entry)
            if existing != entry:
                raise ValueError(
                    f"Constraint {site.name!r} is declared with two different "
                    f"rejections, {existing!r} and {entry!r}. One violation "
                    "cannot report two codes; the same object named by two "
                    "entries must agree on what a client is told."
                )
    return rejections


__all__ = [
    "CheckTier",
    "CodeChannel",
    "ConstantThreshold",
    "DatabaseConstraint",
    "DesignPosition",
    "Enforcement",
    "ProvenanceThreshold",
    "PythonCheck",
    "ScientificCheck",
    "Threshold",
    "collect_constraint_rejections",
    "collect_registered_checks",
]
