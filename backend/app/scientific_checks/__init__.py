"""Self-declaration for TCKDB's *scientific* checks.

TCKDB runs roughly 326 Pydantic validators and 225 database check
constraints. Almost all of them enforce plumbing — string lengths, enum
membership, foreign-key existence, ``multiplicity >= 1``. A small
minority enforce **theoretical chemistry**: elemental balance, charge
conservation, the imaginary-mode count that defines a saddle point, the
bijectivity of an atom map. Those are the ones a referee could disagree
with, and they are the ones this package makes enumerable.

Why a registry has to exist at all
----------------------------------
Nothing in the code distinguishes the two populations. A generator
cannot tell :func:`validate_reaction_charge_conservation` from
``max_length=64`` by inspection, and the machine-readable codes the
scientific checks emit — ``reaction_mass_balance_failed`` and the rest —
live inside f-string message bodies, discoverable only by reading. The
earlier hand-written audit at ``docs/reviews/validation_check_audit.md``
hit exactly this wall: it covers the Pydantic tier only, and every
conservation law in the database is absent from it.

The inclusion test
------------------
**Could this check be wrong in an interesting way?** Elemental balance
is a position a referee could argue with; ``max_length=64`` is not, and
``multiplicity >= 1`` is arithmetic rather than chemistry. Membership in
the register *is* the claim that a check encodes a scientific decision,
so there is no "paper-worthy" flag — adding an entry that fails the test
dilutes every other entry.

How to add one
--------------
Construct a :class:`ScientificCheck` at module level, immediately below
the check it describes::

    def validate_reaction_elemental_balance(...) -> None:
        ...

    CHECK_REACTION_ELEMENTAL_BALANCE = ScientificCheck(
        code="reaction_mass_balance_failed",
        asserts="The reactant and product sides of a reaction contain "
                "the same number of atoms of every element.",
        tier=CheckTier.block,
        tier_rationale="...",
        adr="0008",
        enforced_by=(PythonCheck(validate_reaction_elemental_balance),),
        escape_hatch="...",
    )

then add the module to ``_DECLARING_MODULES`` in :mod:`.declarations`
(a guard test fails if you forget). Regenerate the register document
with ``backend/scripts/generate_scientific_check_register.py``.

Why a plain dataclass rather than a decorator
---------------------------------------------
A decorator sits in the call path of a check whose behaviour must not
change. A frozen dataclass constructed beside the function is inert: it
is data, it wraps nothing, and it cannot alter a signature, a traceback
or a hot loop. It also works for the two populations a decorator cannot
reach — a PostgreSQL constraint has no Python object to decorate, and
``tckdb_schemas`` is forbidden by
``schemas/python/tckdb-schemas/tests/test_import_boundaries.py`` from
importing anything under ``app``, so a wire-schema check cannot
reference a backend decorator. Those two are declared in
:mod:`.declarations` instead, and say so.

There is deliberately no import-time side effect. Collection walks the
declaring modules' namespaces (see :func:`collect_registered_checks`),
so importing a service module registers nothing, mutates no global, and
cannot fail in a way that takes a check down with it.

What the drift guard enforces
-----------------------------
``backend/tests/db/test_scientific_check_register.py``:

* every :class:`PythonCheck` resolves to a real callable (a rename or a
  deletion breaks the import, so CI goes red before review does);
* every :class:`DatabaseConstraint` exists in the live PostgreSQL
  schema, queried from ``pg_constraint`` / ``pg_trigger`` rather than
  trusted as a string;
* every code declared as ``emitted=True`` appears verbatim in the
  source of the function said to emit it;
* every file in the repository containing ``ScientificCheck(`` is
  listed in ``_DECLARING_MODULES``;
* codes are unique across the register.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import ModuleType


class CheckTier(str, Enum):
    """Consequence of a check firing, in ADR 0008's vocabulary.

    ``block`` and ``warn`` carry the same string values as
    :class:`tckdb_schemas.stationary_point.ValidationTier`, which is the
    runtime tier type for that module's findings; they are kept as
    separate enums because this one is documentation and that one is
    control flow, and the register must be able to name tiers that no
    ``StationaryPointFinding`` can have.
    """

    #: Refuse the payload. ADR 0008 reserves this for definitions and
    #: contracts: a check may block only if no correct calculation could
    #: produce the record it refuses.
    block = "block"

    #: Accept the payload and record a machine-readable
    #: :class:`~tckdb_schemas.upload_warning.UploadWarning`. The tier for
    #: expectations and for absences.
    warn = "warn"

    #: Referred to ``machine_review`` under a versioned rubric. ADR 0008
    #: puts every cross-check against external reference data here.
    review = "review"

    #: Not an ADR 0008 consequence tier. The position is enforced by the
    #: *shape* of the schema — a NOT NULL discriminator, a check
    #: constraint, a trigger — so there is no runtime check to place in a
    #: tier, and a record violating it cannot be represented at all.
    structural = "structural"


# ---------------------------------------------------------------------------
# Where a check is enforced
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PythonCheck:
    """A check enforced by Python code, identified by the function object.

    Holding the callable rather than a dotted string is the whole point:
    renaming or deleting the function breaks the import of the declaring
    module, so drift is caught at collection time instead of surviving
    as a stale string in a document.

    :param func: The function that performs the check.
    :param note: Optional scope qualifier — which upload paths reach it,
        what it deliberately does not cover.
    """

    func: Callable[..., object]
    note: str | None = None

    @property
    def location(self) -> str:
        """``path/to/module.py:LINE``, resolved from the live object."""
        source_file = inspect.getsourcefile(self.func)
        if source_file is None:  # pragma: no cover - builtins only
            return f"{self.func.__module__}.{self.func.__qualname__}"
        _lines, first = inspect.getsourcelines(self.func)
        return f"{_repo_relative(source_file)}:{first}"

    @property
    def label(self) -> str:
        return f"``{self.func.__qualname__}``"


@dataclass(frozen=True)
class DatabaseConstraint:
    """A check enforced by PostgreSQL, identified by its schema object name.

    A constraint cannot self-declare in Python, so the register names it
    and the drift guard verifies the name against live schema metadata.
    The name must be the **expanded** one that exists in the database
    (``ck_reaction_atom_map_pair_element_matches``), not the short form
    written in ``__table_args__`` (``element_matches``) that
    ``NAMING_CONVENTION`` expands.

    :param table: Table the object is attached to.
    :param name: Expanded object name as PostgreSQL holds it.
    :param kind: ``check``, ``unique``, ``foreign_key`` or ``trigger``.
    :param definition: The SQL predicate, for the register document.
    """

    table: str
    name: str
    kind: str
    definition: str | None = None

    @property
    def location(self) -> str:
        return f"{self.table}.{self.name}"

    @property
    def label(self) -> str:
        return f"``{self.name}`` ({self.kind} on ``{self.table}``)"


@dataclass(frozen=True)
class DesignPosition:
    """A scientific position enforced by schema shape rather than a check.

    Used where the register should record what TCKDB guarantees but
    there is no single callable or constraint to point at — the
    reproducibility rubric, for instance, or a discriminator whose
    meaning is the guarantee.

    :param where: Human-readable description of what carries the
        position, with paths where they help.
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
# The declaration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScientificCheck:
    """One scientific guarantee TCKDB makes about chemistry.

    An entry is a *claim*, not a code location: the same claim may be
    enforced in more than one place, and ADR 0008 cares which. The atom
    map's "an element does not change across a reaction" is stated once
    at the wire boundary and once as a check constraint, and listing
    both in one entry is what lets a reader see that a second write path
    cannot get around it.

    :param code: Machine-readable code, or a tuple of them where one
        claim is reported under more than one code. ``None`` means the
        check raises with prose only — recorded honestly rather than
        invented, because a code that appears in no message is a code no
        client can match on.
    :param asserts: What the check asserts, in one sentence a chemist
        would recognise. Not a description of the implementation.
    :param tier: ADR 0008 consequence tier.
    :param tier_rationale: Why that tier, in ADR 0008's terms — for
        ``block``, why no correct calculation could produce the record;
        for ``warn``, what correct novel result it could otherwise fire
        on.
    :param adr: Governing ADR number(s), e.g. ``"0008"`` or
        ``"0008, 0011"``.
    :param enforced_by: One or more enforcement sites.
    :param escape_hatch: How legitimate chemistry that the check would
        otherwise refuse is deposited instead. ``None`` where none
        exists. This column is load-bearing: charge conservation is only
        defensible as a blocking check because an electron can be
        declared as a participant.
    :param emitted: Whether the code reaches a producer. The drift guard
        asserts, for each code, that the literal string appears in the
        source of the module **defining the enforcing function** — not
        the module declaring the entry, which would be tautological. So
        renaming ``reaction_mass_balance_failed`` in
        ``reaction_resolution.py`` fails the guard even though the
        declaration sits in the same file, and renaming a
        ``tckdb_schemas`` code fails it from across the package
        boundary.
    :param divergence: A recorded disagreement between the check's
        documentation and its behaviour. Reported, never silently fixed.
    """

    code: str | tuple[str, ...] | None
    asserts: str
    tier: CheckTier
    tier_rationale: str
    adr: str
    enforced_by: tuple[Enforcement, ...]
    escape_hatch: str | None = None
    emitted: bool = True
    divergence: str | None = None
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
            raise ValueError(
                f"ScientificCheck({self.code!r}) declares no enforcement site; "
                "an entry that names no code, constraint or position cannot be "
                "drift-guarded and is a comment, not a register entry."
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


__all__ = [
    "CheckTier",
    "DatabaseConstraint",
    "DesignPosition",
    "Enforcement",
    "PythonCheck",
    "ScientificCheck",
    "collect_registered_checks",
]
