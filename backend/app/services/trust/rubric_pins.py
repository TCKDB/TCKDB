"""A fingerprint of what each rubric decides, pinned beside its version.

A rubric version means "these checks, with these rules". Stored assessments name the version they were made under,
and a reader decides *stale* by comparing that number with the current one. That only works if the number moves when
the rules move. Nothing in the language makes it move, so this module does: it computes a fingerprint of everything
that changes what a rubric decides, and ``rubric_pins.json`` records the fingerprint of every current
``(name, version)``. A test (``backend/tests/scripts/test_rubric_pins.py``) fails when the two disagree.

Which rubrics
-------------
* every trust rubric in ``RUBRICS`` that declares checks (the review-tier rubrics declared with ``checks=()`` decide
  nothing here: their rules live in the services that run them, so a pin would pass whatever those did; they are
  listed by :func:`unguarded_rubric_names`);
* both reproducibility rubrics (``SHARED`` and ``TS_STATMECH``), whose rules are the code of
  ``evaluate_reproducibility``.

What is in a fingerprint
------------------------
For a trust rubric: its record type; for each check its name, kind and weight and the code its runner executes; and the
code of the evaluator entrypoints in :data:`GRADING_ENTRYPOINTS` (hard-fail detectors, completeness aggregation, label
thresholds, the transition-state context builders). For a reproducibility rubric: its name and the code of
:data:`REPRODUCIBILITY_ENTRYPOINTS`. ``explain`` wording is excluded: it changes what a reader is told, not what a check
decides.

"The code" is followed **fail-closed**. From each entrypoint the walker reads the AST (docstrings and comments are not
part of it), and follows, transitively and across ``app`` and ``tckdb_schemas``:

* every function it names or calls, unwrapping decorators (``inspect.unwrap``: ``contextmanager``, ``functools.cache``),
  ``staticmethod``/``classmethod``/``property``, ``functools.partial`` and lambdas found as values;
* every callable inside a constant container (a dispatch dict, a tuple of ``(key, lambda)``);
* every module constant it reads, canonicalised (scalars, enum members, containers, frozen dataclasses, pydantic
  instances, compiled patterns);
* every in-scope class it references, as a whole: for an enum or any plain class the class source, so a method, property
  or class attribute counts (``ArtifactIntegrityFinding.is_break``); for an SQLAlchemy model only its methods, not its
  columns.

It **refuses** (raises) rather than skips what it cannot follow: a referenced in-scope constant it cannot canonicalise, a
callable without readable source, ``getattr`` with a computed name, ``eval``/``exec``/``globals``/``__import__``, and any
read of the environment or of ``settings`` (an input to a verdict that the fingerprint cannot see). A genuinely needed
read can be declared in :data:`DECLARED_INPUTS` with the reason.

The fingerprint comes from ``ast.dump`` and the pins record the Python minor version they were made under.
"""

from __future__ import annotations

import ast
import dataclasses
import enum
import hashlib
import inspect
import json
import os
import re
import sys
import textwrap
from collections.abc import Callable
from functools import partial
from pathlib import Path
from types import FunctionType, ModuleType
from typing import Any

from app.services.trust.models import EvidenceRubric
from app.services.trust.rubrics import RUBRICS

#: Bumped when the fingerprinting itself changes (what it follows), which moves every fingerprint without any rubric
#: changing. The generator then re-pins all of them; a rubric change in the same commit would hide in that re-pin, so a
#: commit that changes the algorithm should change nothing else.
ALGORITHM = 3

PINS_PATH = Path(__file__).with_name("rubric_pins.json")

#: Packages whose code is part of a verdict. ``tckdb_schemas`` is the wire package the backend shares with clients.
IN_SCOPE_PACKAGES = ("app", "tckdb_schemas")

#: Rubric name -> ``module:function`` of the evaluator entrypoints that grade it. A rubric that declares checks and is
#: absent here fails the guard: a new rubric has to say how it is graded, or its grading would be unguarded. Include the
#: functions that build any context the rubric's runners read (the transition-state scoped assessment).
GRADING_ENTRYPOINTS: dict[str, tuple[str, ...]] = {
    "computed_calculation": ("app.services.trust.evaluator:evaluate_loaded_calculation",),
    "computed_kinetics": (
        "app.services.trust.evaluator:evaluate_loaded_kinetics",
        "app.services.scientific_read.trust_floor:evaluate_shown_kinetics",
    ),
    "computed_thermo": ("app.services.trust.evaluator:evaluate_loaded_thermo",),
    "computed_statmech": ("app.services.trust.evaluator:evaluate_loaded_statmech_under",),
    "computed_ts_statmech": ("app.services.trust.evaluator:evaluate_loaded_statmech_under",),
    "computed_transport": ("app.services.trust.evaluator:evaluate_loaded_transport",),
    "computed_transition_state": (
        "app.services.trust.evaluator:evaluate_loaded_transition_state_entry",
        "app.services.scientific_read.trust_floor:evaluate_shown_transition_state_entry",
        "app.services.trust.ts_scoped:scoped_ts_context",
        "app.services.trust.ts_scoped:context_for",
        "app.services.trust.ts_scoped:declared_curvature_pins",
    ),
}

#: The reproducibility rubrics (not in ``RUBRICS``): name -> ``(version, entrypoints)``. Their rules are the grading
#: function and the owner router.
REPRODUCIBILITY_ENTRYPOINTS: tuple[str, ...] = (
    "app.services.reproducibility_rubric:evaluate_reproducibility",
    "app.services.reproducibility_rubric:reproducibility_rubric_for",
)

#: Functions that call ``getattr`` with a computed attribute name, each reviewed to read a *data* attribute of the record
#: being graded (a column or relationship), never to choose which function runs. ``module:qualname`` -> why that is
#: safe. A computed ``getattr`` anywhere else in the walked code fails the guard: what it dispatches to cannot be followed.
DYNAMIC_ATTRIBUTE_READS: dict[str, str] = {
    "app.services.reproducibility_rubric:_mapped_columns": "reads each mapped column of a row; the names come from the table",
    "app.services.reproducibility_rubric:_target_snapshot": "reads relationship names from fixed per-record-type tuples",
    "app.services.reproducibility_rubric:_typed_output_snapshot": "reads the result relationship named by a fixed table",
    "app.services.scientific_read.trust_floor:_swapped": "swaps loaded collections named by the callers' literal tuples",
    "app.services.trust.evaluator:_thermo_has_representation": "reads NASA coefficient columns from a fixed tuple",
    "app.services.trust.rubrics:_check_result_block_present": "reads one result attribute; each caller binds a literal name",
    "app.services.trust.rubrics:_thermo_has_nasa_representation": "reads NASA coefficient columns from a fixed tuple",
}

#: Filled by :func:`discover_dynamic_attribute_sites` only; ``None`` in normal operation.
_DISCOVERED: list[str] | None = None

#: Environment or settings reads a verdict is allowed to depend on, each with the reason. Empty: none does.
DECLARED_INPUTS: dict[str, str] = {}

_SCALARS = (int, float, str, bool, bytes, type(None))
_FORBIDDEN_CALLS = {"eval", "exec", "globals", "locals", "__import__", "vars", "compile"}


class Unfingerprintable(TypeError):
    """Something a verdict depends on that the guard cannot follow. The guard fails rather than skip it."""


#: Kept for the callers that named the first version of this error.
UnfingerprintableRunner = Unfingerprintable


def _in_scope(module_name: str | None) -> bool:
    return bool(module_name) and any(module_name == p or module_name.startswith(p + ".") for p in IN_SCOPE_PACKAGES)


def _strip_docstrings(tree: ast.AST) -> None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                node.body = body[1:] or [ast.Pass()]


def _is_orm_model(cls: type) -> bool:
    return (hasattr(cls, "__table__") and hasattr(cls, "__mapper__")) or hasattr(cls, "__tablename__")


def _is_settings(value: Any) -> bool:
    return any(c.__name__ == "BaseSettings" for c in type(value).__mro__)


#: Registry-like objects whose content is pinned on its own (or is wiring, not a rule): referenced, never expanded.
def _is_opaque(value: Any) -> bool:
    return type(value).__name__ in {"VersionRegistry", "EvidenceRubric", "EvidenceCheckSpec", "ReproducibilityRubric"}


class _Walker:
    """Collect the normalised code and referenced constants reachable from some entrypoints."""

    def __init__(self) -> None:
        self.code: dict[str, str] = {}
        self.constants: dict[str, Any] = {}
        self._classes: set[str] = set()

    # ---- entry -----------------------------------------------------------------------------------------------

    def visit(self, obj: Any) -> None:
        """Follow any callable-ish *obj*: function, partial, method wrapper, property, lambda."""
        if isinstance(obj, partial):
            self.constants[f"partial#{len(self.constants)}"] = self._canon(
                [obj.args, sorted(obj.keywords.items())], "partial arguments"
            )
            self.visit(obj.func)
            return
        if isinstance(obj, (staticmethod, classmethod)):
            self.visit(obj.__func__)
            return
        if isinstance(obj, property):
            for accessor in (obj.fget, obj.fset, obj.fdel):
                if accessor is not None:
                    self.visit(accessor)
            return
        if inspect.isclass(obj):
            self._visit_class(obj)
            return
        if not callable(obj):
            raise Unfingerprintable(f"{obj!r} is not something the guard can read the code of")
        if not isinstance(obj, FunctionType):
            # A bound method, functools.cache wrapper, builtin, or callable instance.
            target = getattr(obj, "__func__", None) or getattr(obj, "__wrapped__", None)
            if target is not None:
                self.visit(target)
                return
            module = getattr(obj, "__module__", None)
            if _in_scope(module):
                raise Unfingerprintable(f"{module}.{getattr(obj, '__qualname__', obj)!r} is a callable with no readable source")
            return
        fn: FunctionType = inspect.unwrap(obj)  # type: ignore[assignment]
        if not isinstance(fn, FunctionType):
            self.visit(fn)
            return
        if not _in_scope(fn.__module__):
            return
        self._visit_function(fn)

    # ---- functions -------------------------------------------------------------------------------------------

    def _visit_function(self, fn: FunctionType) -> None:
        if fn.__code__.co_filename.startswith("<"):
            # Synthesised (dataclass ``__init__``/``__setattr__``): it is generated from declarations that the class
            # source, fingerprinted with the class, already holds.
            return
        key = f"{fn.__module__}:{fn.__qualname__}"
        if fn.__name__ == "<lambda>":
            key += f"@{fn.__code__.co_firstlineno}:{fn.__code__.co_filename}"
        if key in self.code:
            return
        names, text = self._function_text(fn)
        self.code[key] = text
        scope: dict[str, Any] = dict(fn.__globals__)
        if fn.__closure__:
            for name, cell in zip(fn.__code__.co_freevars, fn.__closure__, strict=True):
                try:
                    scope[name] = cell.cell_contents
                except ValueError:
                    continue
        for default in (*(fn.__defaults__ or ()), *(fn.__kwdefaults__ or {}).values()):
            self.constants[f"{key}#default"] = self._canon(default, f"default of {key}")
        for chain in names:
            self._resolve_chain(chain, scope, key)

    def _function_text(self, fn: FunctionType) -> tuple[list[tuple[str, ...]], str]:
        """The normalised code of *fn* and the dotted names it uses (each as a tuple of parts)."""
        tree = None
        if fn.__name__ != "<lambda>":
            try:
                tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
            except (OSError, TypeError, SyntaxError):
                tree = None
        if tree is None:
            # A lambda (or source the parser cannot take on its own): read the code object instead.
            return self._code_object_names(fn.__code__), self._code_object_text(fn.__code__, fn.__doc__)
        _strip_docstrings(tree)
        chains: list[tuple[str, ...]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                self._check_call(node, fn)
            if isinstance(node, ast.Attribute):
                parts = self._attr_parts(node)
                if parts:
                    chains.append(parts)
            elif isinstance(node, ast.Name):
                chains.append((node.id,))
        return chains, ast.dump(tree, annotate_fields=False, include_attributes=False)

    @staticmethod
    def _attr_parts(node: ast.Attribute) -> tuple[str, ...] | None:
        parts: list[str] = [node.attr]
        value = node.value
        while isinstance(value, ast.Attribute):
            parts.append(value.attr)
            value = value.value
        if isinstance(value, ast.Name):
            parts.append(value.id)
            return tuple(reversed(parts))
        return None

    @staticmethod
    def _check_call(node: ast.Call, fn: FunctionType) -> None:
        func = node.func
        name = func.id if isinstance(func, ast.Name) else (func.attr if isinstance(func, ast.Attribute) else None)
        where = f"{fn.__module__}:{fn.__qualname__}"
        if isinstance(func, ast.Name) and func.id in _FORBIDDEN_CALLS:
            raise Unfingerprintable(f"{where} calls {func.id}(): dynamic code the fingerprint cannot follow")
        if name in {"getattr", "setattr", "hasattr"} and isinstance(func, ast.Name):
            second = node.args[1] if len(node.args) > 1 else None
            if not (isinstance(second, ast.Constant) and isinstance(second.value, str)):
                if _DISCOVERED is not None:
                    _DISCOVERED.append(where)
                    return
                if where in DYNAMIC_ATTRIBUTE_READS:
                    return
                raise Unfingerprintable(
                    f"{where} calls {name}() with a computed attribute name: what it dispatches to cannot be "
                    "followed statically. Use a literal name or an explicit table."
                )
        if name in {"import_module", "attrgetter", "methodcaller"}:
            raise Unfingerprintable(f"{where} calls {name}(): dynamic dispatch the fingerprint cannot follow")

    @staticmethod
    def _code_object_names(code: Any) -> list[tuple[str, ...]]:
        names: list[tuple[str, ...]] = [(n,) for n in code.co_names]
        for const in code.co_consts:
            if hasattr(const, "co_names"):
                names.extend(_Walker._code_object_names(const))
        return names

    @staticmethod
    def _code_object_text(code: Any, doc: str | None) -> str:
        consts = [
            _Walker._code_object_text(c, None) if hasattr(c, "co_code") else repr(c) for c in code.co_consts if c != doc
        ]
        return f"{code.co_code.hex()}|{code.co_names}|{consts}"

    # ---- names -----------------------------------------------------------------------------------------------

    def _resolve_chain(self, chain: tuple[str, ...], scope: dict[str, Any], where: str) -> None:
        base_name = chain[0]
        if base_name not in scope:
            return
        value = scope[base_name]
        label = f"{where.split(':')[0]}.{base_name}"
        self._reference(label, value, where)
        # Follow module attributes: ``module.helper`` or ``module.CONSTANT``.
        for part in chain[1:]:
            if value is os and part in {"environ", "environb", "getenv", "getenvb", "putenv"}:
                self._refuse_input(f"os.{part}", where, "reads the environment")
                return
            if isinstance(value, ModuleType) and _in_scope(value.__name__):
                if not hasattr(value, part):
                    return
                value = getattr(value, part)
                self._reference(f"{value.__module__ if hasattr(value, '__module__') else 'mod'}.{part}", value, where)
            else:
                return

    def _reference(self, label: str, value: Any, where: str) -> None:
        if value is os.environ or value is os.getenv or value is getattr(os, "environb", None):
            self._refuse_input(label, where, "reads the environment")
            return
        if isinstance(value, ModuleType):
            return
        if _is_settings(value):
            self._refuse_input(label, where, "reads settings")
            return
        if inspect.isclass(value):
            self._visit_class(value)
            return
        if isinstance(value, (FunctionType, partial, staticmethod, classmethod, property)) or (
            callable(value) and getattr(value, "__wrapped__", None) is not None
        ):
            module = getattr(inspect.unwrap(value) if not isinstance(value, (partial, staticmethod, classmethod, property)) else value, "__module__", "app")
            if isinstance(value, (partial, staticmethod, classmethod, property)) or _in_scope(module):
                self.visit(value)
            return
        if _is_opaque(value):
            self.constants[f"{label}#opaque"] = type(value).__name__
            return
        canon = self._canon(value, label)
        if canon is not _IGNORED:
            self.constants[label] = canon

    def _refuse_input(self, label: str, where: str, what: str) -> None:
        if label in DECLARED_INPUTS:
            self.constants[f"{label}#declared_input"] = DECLARED_INPUTS[label]
            return
        raise Unfingerprintable(
            f"{where} {what} ({label}): an input to a verdict the fingerprint cannot see. Declare it in "
            "DECLARED_INPUTS with the reason, or stop reading it."
        )

    # ---- classes ---------------------------------------------------------------------------------------------

    def _visit_class(self, cls: type) -> None:
        if not _in_scope(cls.__module__):
            return
        key = f"{cls.__module__}:{cls.__qualname__}"
        if key in self._classes:
            return
        self._classes.add(key)
        if issubclass(cls, enum.Enum):
            self.constants[f"{key}#members"] = [
                [m.name, self._canon(m.value, f"{key}.{m.name}")] for m in cls  # type: ignore[arg-type]
            ]
        orm = _is_orm_model(cls)
        if not orm:
            try:
                tree = ast.parse(textwrap.dedent(inspect.getsource(cls)))
            except (OSError, TypeError, SyntaxError) as exc:
                raise Unfingerprintable(f"no readable source for class {key}: {exc}") from exc
            _strip_docstrings(tree)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    self._check_call(node, _Stub(cls))  # type: ignore[arg-type]
            self.code[f"class {key}"] = ast.dump(tree, annotate_fields=False, include_attributes=False)
        for member_name, member in vars(cls).items():
            if isinstance(member, (FunctionType, staticmethod, classmethod, property)):
                self.visit(member)
            elif not orm and not member_name.startswith("__"):
                canon = self._canon(member, f"{key}.{member_name}")
                if canon is not _IGNORED and callable(member) is False:
                    self.constants[f"{key}.{member_name}"] = canon
        for base in cls.__mro__[1:]:
            if base is not object and _in_scope(base.__module__):
                self._visit_class(base)

    # ---- constants -------------------------------------------------------------------------------------------

    def _canon(self, value: Any, label: str) -> Any:
        """A JSON-stable form of *value*, visiting any callable inside it; ``_IGNORED`` for out-of-scope objects."""
        if isinstance(value, enum.Enum):
            self._visit_class(type(value))
            return {"enum": f"{type(value).__name__}.{value.name}", "value": self._canon(value.value, label)}
        if isinstance(value, _SCALARS):
            return value.hex() if isinstance(value, bytes) else value
        if isinstance(value, re.Pattern):
            return {"re": value.pattern, "flags": value.flags}
        if isinstance(value, (tuple, list)):
            return [self._canon(v, label) for v in value]
        if isinstance(value, (set, frozenset)):
            parts = [self._canon(v, label) for v in value]
            return sorted(parts, key=lambda p: json.dumps(p, sort_keys=True, default=str))
        if isinstance(value, dict):
            items = {json.dumps(self._canon(k, label), sort_keys=True, default=str): self._canon(v, label) for k, v in value.items()}
            return dict(sorted(items.items()))
        if isinstance(value, (FunctionType, partial, staticmethod, classmethod, property)) or inspect.isclass(value):
            self._reference(label, value, label)
            return f"callable:{getattr(value, '__qualname__', type(value).__name__)}"
        if callable(value) and getattr(value, "__wrapped__", None) is not None:
            self._reference(label, value, label)
            return f"callable:{getattr(value, '__qualname__', type(value).__name__)}"
        module = type(value).__module__
        if not _in_scope(module):
            return _IGNORED
        if _is_opaque(value):
            return {"opaque": type(value).__name__}
        self._visit_class(type(value))
        if dataclasses.is_dataclass(value):
            return {"dataclass": type(value).__name__, "fields": self._canon(dataclasses.asdict(value), label)}
        if hasattr(value, "model_dump"):
            return {"model": type(value).__name__, "fields": self._canon(value.model_dump(mode="python"), label)}
        raise Unfingerprintable(
            f"{label} is an instance of {type(value).__module__}.{type(value).__name__} that the guard cannot "
            "canonicalise. Make it a dataclass, an enum, a pydantic model or a plain constant."
        )


class _Stub:
    """Names a class for :meth:`_Walker._check_call` error messages."""

    def __init__(self, cls: type) -> None:
        self.__module__ = cls.__module__
        self.__qualname__ = cls.__qualname__


_IGNORED = object()


def _resolve(dotted: str) -> Callable[..., Any]:
    module_name, _, attr = dotted.partition(":")
    __import__(module_name)
    return getattr(sys.modules[module_name], attr)


def _digest(payload: Any) -> str:
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def rubric_fingerprint(rubric: EvidenceRubric) -> str:
    """The fingerprint of a trust *rubric*: everything that changes what it decides. See the module docstring."""
    checks = []
    for spec in rubric.checks:
        walker = _Walker()
        walker.visit(spec.runner)
        checks.append(
            {
                "name": spec.name,
                "kind": spec.kind.value,
                "weight": spec.weight,
                "code": walker.code,
                "constants": walker.constants,
            }
        )
    grading = _Walker()
    if rubric.checks:
        entrypoints = GRADING_ENTRYPOINTS.get(rubric.name)
        if entrypoints is None:
            raise KeyError(
                f"rubric {rubric.name!r} declares checks but has no entry in GRADING_ENTRYPOINTS "
                "(app/services/trust/rubric_pins.py): say which evaluator functions grade it"
            )
        for dotted in entrypoints:
            grading.visit(_resolve(dotted))
    return _digest(
        {
            "record_type": rubric.record_type,
            "checks": checks,
            "grading": {"code": grading.code, "constants": grading.constants},
        }
    )


def reproducibility_fingerprint(name: str) -> str:
    """The fingerprint of a reproducibility rubric: its name and the code of its grading function."""
    walker = _Walker()
    for dotted in REPRODUCIBILITY_ENTRYPOINTS:
        walker.visit(_resolve(dotted))
    return _digest({"name": name, "grading": {"code": walker.code, "constants": walker.constants}})


def current_fingerprints() -> dict[str, tuple[int, str]]:
    """``{rubric name: (current version, fingerprint)}`` for every pinned rubric."""
    from app.services.reproducibility_rubric import SHARED, TS_STATMECH

    out: dict[str, tuple[int, str]] = {}
    for ref in sorted(RUBRICS.refs(), key=lambda r: (r.name, r.version)):
        rubric = RUBRICS.get(ref.name, ref.version)
        # A rubric with no checks decides nothing here, so a pin would only restate its record type and pass
        # vacuously. It is listed by unguarded_rubric_names() instead.
        if RUBRICS.is_current(ref) and rubric.checks:
            out[ref.name] = (ref.version, rubric_fingerprint(rubric))
    for repro in (SHARED, TS_STATMECH):
        out[repro.name] = (repro.version, reproducibility_fingerprint(repro.name))
    return out


def unguarded_rubric_names() -> tuple[str, ...]:
    """Registered trust rubrics that declare no checks: their rules live in the service that runs them, not here."""
    return tuple(
        sorted(
            ref.name
            for ref in RUBRICS.refs()
            if RUBRICS.is_current(ref) and not RUBRICS.get(ref.name, ref.version).checks
        )
    )


def reachable(rubric_name: str) -> tuple[set[str], set[str]]:
    """``(code keys, constant labels)`` the guard follows for a pinned rubric (what a regression test asserts on)."""
    walker = _Walker()
    if rubric_name in {"tckdb_reproducibility", "tckdb_reproducibility_ts_statmech"}:
        entrypoints = REPRODUCIBILITY_ENTRYPOINTS
    else:
        entrypoints = GRADING_ENTRYPOINTS[rubric_name]
        for spec in RUBRICS.get(rubric_name).checks:
            walker.visit(spec.runner)
    for dotted in entrypoints:
        walker.visit(_resolve(dotted))
    return set(walker.code), set(walker.constants)


def discover_dynamic_attribute_sites() -> list[str]:
    """Every ``module:qualname`` with a computed ``getattr`` reachable from the pinned entrypoints (a maintenance aid)."""
    global _DISCOVERED
    _DISCOVERED = []
    try:
        current_fingerprints()
        return sorted(set(_DISCOVERED))
    finally:
        _DISCOVERED = None


def python_minor() -> str:
    return f"{sys.version_info.major}.{sys.version_info.minor}"


def render_pins(
    fingerprints: dict[str, tuple[int, str]], overrides: list[dict[str, Any]] | None = None, python: str | None = None
) -> str:
    """The committed pins file text. Only current versions are kept (pre-release: no history)."""
    document = {
        "comment": (
            "Generated by backend/scripts/generate_rubric_pins.py. Fingerprint of each rubric's checks and grading "
            "at its current version. Do not edit by hand. To change a rubric: bump its version, then re-run the script."
        ),
        "python": python or python_minor(),
        "algorithm": ALGORITHM,
        "pins": {name: {str(version): fp} for name, (version, fp) in fingerprints.items()},
        "unbumped_overrides": overrides or [],
    }
    return json.dumps(document, indent=2, sort_keys=False) + "\n"


def load_document(path: Path = PINS_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_pins(path: Path = PINS_PATH) -> dict[str, dict[str, str]]:
    return load_document(path)["pins"]


def pinned_python(path: Path = PINS_PATH) -> str:
    return load_document(path)["python"]


def load_overrides(path: Path = PINS_PATH) -> list[dict[str, Any]]:
    return list(load_document(path).get("unbumped_overrides", []))


def diagnose(fingerprints: dict[str, tuple[int, str]], pins: dict[str, dict[str, str]]) -> list[str]:
    """Plain-language problems between the live fingerprints and the pins; empty when they agree."""
    problems: list[str] = []
    unguarded = set(unguarded_rubric_names())
    for name, (version, fp) in fingerprints.items():
        pinned = pins.get(name)
        if pinned is None:
            problems.append(f"{name}: no pin. A new rubric needs a pin: run backend/scripts/generate_rubric_pins.py.")
        elif str(version) not in pinned:
            have = ", ".join(sorted(pinned))
            problems.append(
                f"{name}: version is now {version} but the pin is for version {have}. The version was bumped "
                "without re-pinning: run backend/scripts/generate_rubric_pins.py."
            )
        elif pinned[str(version)] != fp:
            problems.append(
                f"{name}: its checks or grading changed but its version is still {version}. Stored assessments "
                f"made under version {version} would read as current. Bump the rubric's version, then run "
                "backend/scripts/generate_rubric_pins.py."
            )
    for name in sorted(set(pins) - set(fingerprints)):
        if name in unguarded:
            problems.append(
                f"{name}: now declares no checks, so it is no longer pinned (a pin of an empty rubric guards nothing). "
                "Run backend/scripts/generate_rubric_pins.py to drop its pin."
            )
        else:
            problems.append(f"{name}: pinned but no longer registered. Run backend/scripts/generate_rubric_pins.py.")
    return problems


def unbumped_changes(fingerprints: dict[str, tuple[int, str]], pins: dict[str, dict[str, str]]) -> list[str]:
    """Rubrics whose fingerprint changed under an unchanged version: what the generator refuses to overwrite."""
    return [
        name
        for name, (version, fp) in fingerprints.items()
        if str(version) in pins.get(name, {}) and pins[name][str(version)] != fp
    ]
