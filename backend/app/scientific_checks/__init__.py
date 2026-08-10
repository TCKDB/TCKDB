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
used to live inside f-string message bodies, discoverable only by reading
and reportable to nobody. They now travel as attributes of the exception
that carries the refusal, which is a change this package forced: the
register asked "which checks emit a code?" and the honest answer, once
somebody looked, was "none of them, to any consumer". The
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
        channel=CodeChannel.error_envelope,
        tier_rationale="...",
        adr="0008",
        enforced_by=(PythonCheck(validate_reaction_elemental_balance),),
        escape_hatch="...",
    )

A check that declares a code must raise it, not spell it — the code goes
to :class:`~tckdb_schemas.coded_error.CodedValidationError` as its first
argument, and ``message_prefix=False`` where an existing message must not
move. A code written only into the sentence reaches no client.

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
* every entry declares a :class:`CodeChannel`, and a code and a channel
  imply each other — a blocking check enforced from Python must reach
  the error envelope, because a refusal a client cannot identify is a
  refusal it has to string-match English to understand;
* every ``error_envelope`` code is passed as the *first argument* of a
  ``CodedValidationError`` in a module that defines an enforcing
  function, which is what separates a code the module reports from a
  code it merely mentions inside a sentence;
* every ``error_envelope`` code is named by an end-to-end test that
  asserts it against a real HTTP response body, because none of the
  above proves it survives Pydantic, the exception handler and the JSON
  encoder — and that path is exactly where the codes were being lost;
* every file in the repository containing ``ScientificCheck(`` is
  listed in ``_DECLARING_MODULES``;
* every :class:`ProvenanceThreshold` resolves to a real callable, and
  every canonical parameter key it reads exists in
  ``calculation_parameter_vocab`` in the live schema — without which the
  threshold would resolve to its fallback forever while the register
  went on claiming it keys on that provenance;
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

    #: Labels a stored record at read time without refusing anything —
    #: a :class:`~app.services.trust.models.HardFailReason` in the trust
    #: evaluator. ADR 0008 names this consequence in prose ("labelling")
    #: alongside refusal, annotation and referral, but the enum only ever
    #: carried three of the four, so a position enforced solely at read
    #: time had no honest tier to declare. It is not ``structural``: the
    #: record can be represented, it is simply labelled once TCKDB knows
    #: something about it. Reserved for facts TCKDB observes about a
    #: record *after* it was accepted, where refusing at upload was never
    #: possible because the fact did not exist yet.
    label = "label"

    #: Referred to ``machine_review`` under a versioned rubric. ADR 0008
    #: puts every cross-check against external reference data here.
    review = "review"

    #: Not an ADR 0008 consequence tier. The position is enforced by the
    #: *shape* of the schema — a NOT NULL discriminator, a check
    #: constraint, a trigger — so there is no runtime check to place in a
    #: tier, and a record violating it cannot be represented at all.
    structural = "structural"


class CodeChannel(str, Enum):
    """Where a check's code actually surfaces to a client.

    ``code`` alone was never enough. An entry could name a code that
    nothing carried anywhere a consumer could read it, and for eleven of
    the register's entries — and, more quietly, for *every* blocking
    chemistry check — that is exactly what had happened: the code was
    spelled inside an English sentence, ``"Reaction is not
    element-balanced (reaction_mass_balance_failed)."``, and the response
    body's own ``code`` field said ``validation_error``. Reading the
    register you would have concluded a client could branch on mass
    balance. It could not.

    Naming the channel is what makes that checkable. The guards in
    ``backend/tests/db/test_scientific_check_register.py`` hold each
    channel to its own obligation, so "declared but unreachable" fails CI
    instead of being found by reading.
    """

    #: The ``code`` field of the 422 error envelope, carried there by a
    #: :class:`~tckdb_schemas.coded_error.CodedValidationError`. Guarded
    #: twice: the enforcing module must construct the coded error with
    #: this code, and an end-to-end test must assert the code arrives in
    #: an HTTP response body.
    error_envelope = "error_envelope"

    #: The ``code`` field of an
    #: :class:`~tckdb_schemas.upload_warning.UploadWarning` returned
    #: alongside a *successful* upload. The warn tier's surface; it has
    #: been machine-readable since the class was written.
    upload_warning = "upload_warning"

    #: A read-time label — a ``HardFailReason`` from the trust evaluator.
    #: Reaches a consumer through the trust payload of a record, not
    #: through any request that could have been refused.
    trust_label = "trust_label"

    #: Enforced only by PostgreSQL, so the refusal arrives through the
    #: integrity handler as a 409 whose code names the SQLSTATE class
    #: (``state_conflict`` and friends) rather than the scientific claim.
    #: An honest "the client is told *something*, but not which check".
    database_constraint = "database_constraint"

    #: No machine-readable code reaches anybody. Recorded rather than
    #: papered over: an entry declaring no code must also declare that
    #: nothing carries one.
    none = "none"


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
        """``path/to/module.py::qualified_name``, resolved from the live object.

        A qualified name rather than a line number, and that is the whole
        point of this property.

        The register is a generated artifact that CI compares against the
        committed copy, so whatever this returns becomes an input to that
        comparison. A line number makes the document a build artifact of
        *every line above every declared check*: adding a comment to
        ``app/services/geometry_validation.py`` shifted one check from 120 to
        121 and turned the gate red on main, and the entire drift was that one
        digit.

        The cost is not the regeneration. It is that a gate which fires on
        cosmetic movement teaches everyone to regenerate reflexively without
        reading the diff, which is exactly how a real drift eventually gets
        committed unnoticed. The guard exists to catch a renamed check, a
        removed code, a vocab key that no longer resolves -- and a qualified
        name still moves for every one of those, because it is derived from
        the same live object.

        What is given up is the click-to-line convenience. ``file::qualname``
        is greppable and unique, which is enough to find a function, and it
        does not change when someone edits the docstring above it.
        """
        source_file = inspect.getsourcefile(self.func)
        if source_file is None:  # pragma: no cover - builtins only
            return f"{self.func.__module__}.{self.func.__qualname__}"
        return f"{_repo_relative(source_file)}::{self.func.__qualname__}"

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
# Where a check's threshold comes from
# ---------------------------------------------------------------------------
#
# Until ADR 0012 every numeric line in the register was a literal in
# Python, so naming it in ``tier_rationale`` was honest. ADR 0012's tau
# is not: it is *read from the execution provenance the record carries*,
# and two deposits of the same structure legitimately get different
# values. A register that rendered it as "roughly 50 cm-1" would state a
# constant the code does not have, which is precisely the drift this
# package exists to prevent — and would also hide the failure mode that
# matters, which is what happens when the provenance is missing.
#
# So a check may declare its thresholds, and the two kinds are different
# types rather than a flag, because they carry different obligations: a
# constant needs a value, a provenance-derived one needs the keys it
# reads and what it does when they are absent.


@dataclass(frozen=True)
class ConstantThreshold:
    """A numeric line fixed in code.

    :param name: Identifier a reader can grep for.
    :param value: The number.
    :param unit: Its unit, spelled the way a chemist writes it.
    :param rationale: Why this number, and what tuning it would change.
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

    The resolver is held as a callable for the same reason
    :class:`PythonCheck` is: renaming or deleting it breaks the import of
    the declaring module, so the register cannot keep describing a
    resolution that no longer exists.

    :param name: Identifier a reader can grep for.
    :param unit: Unit of the resolved value.
    :param resolver: The function that performs the resolution.
    :param parameter_keys: Canonical ``calculation_parameter`` keys the
        resolver reads. Verified against ``calculation_parameter_vocab``
        in the live schema by the drift guard, so a key that is renamed
        or never seeded fails CI rather than silently resolving to the
        fallback forever.
    :param values: The resolved value per recorded protocol, as
        ``(protocol, value)`` pairs, for the register table.
    :param fallback: What is used when the provenance is absent, and why
        that is the safe direction. Load-bearing: a threshold that
        degrades silently is worse than a constant.
    :param rationale: Why the threshold cannot be a constant.
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
        """``path/to/module.py::qualified_name``, resolved from the live object.

        Same rule, and for the same reason, as
        :attr:`PythonCheck.location` -- see the argument there. Both feed the
        generated register, so a line number in either one is enough to make
        the drift gate fire on a comment.
        """
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
    :param channel: Where the code surfaces to a client — see
        :class:`CodeChannel`. Must be :attr:`CodeChannel.none` exactly
        when the entry declares no code, so "this check has a code" and
        "a client can act on it" can never drift apart again.
    :param divergence: A recorded disagreement between the check's
        documentation and its behaviour. Reported, never silently fixed.
    :param thresholds: The numeric lines the check fires on, each
        declared as a :class:`ConstantThreshold` or a
        :class:`ProvenanceThreshold`. Empty where the check is purely
        structural. Declaring the *kind* is the point: a register that
        printed ADR 0012's τ as a number would be claiming the code
        holds a constant it does not have, and would hide what happens
        when the provenance τ is read from is missing.
    """

    code: str | tuple[str, ...] | None
    asserts: str
    tier: CheckTier
    tier_rationale: str
    adr: str
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
            raise ValueError(
                f"ScientificCheck({self.code!r}) declares no enforcement site; "
                "an entry that names no code, constraint or position cannot be "
                "drift-guarded and is a comment, not a register entry."
            )
        if bool(self.codes) != (self.channel is not CodeChannel.none):
            raise ValueError(
                f"ScientificCheck({self.code!r}) declares "
                f"{len(self.codes)} code(s) and channel={self.channel.value}. "
                "A code with no channel is a code nothing carries, and a "
                "channel with no code is a channel carrying nothing; the "
                "register must not be able to say either."
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
    "CodeChannel",
    "ConstantThreshold",
    "DatabaseConstraint",
    "DesignPosition",
    "Enforcement",
    "ProvenanceThreshold",
    "PythonCheck",
    "ScientificCheck",
    "Threshold",
    "collect_registered_checks",
]
