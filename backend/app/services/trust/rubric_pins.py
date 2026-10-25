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

**Out of scope.** Anything that runs outside Python is not fingerprinted: DB-side functions and triggers, server defaults,
column defaults that are SQL, and migrations. Python code runs in this process; SQL a rubric reads is fingerprinted as
text when it is a module-level expression, but what the database does with it is not.

The fingerprint comes from ``ast.dump`` and the pins record the Python minor version they were made under.
"""

from __future__ import annotations

import ast
import dataclasses
import enum
import hashlib
import importlib
import inspect
import json
import os
import re
import sys
import textwrap
from collections.abc import Callable
from functools import partial
from pathlib import Path, PurePath
from types import FunctionType, ModuleType
from typing import Any

from sqlalchemy import Column
from sqlalchemy.ext.hybrid import hybrid_method, hybrid_property
from sqlalchemy.orm import configure_mappers
from sqlalchemy.sql import ClauseElement

from app.services.trust.models import EvidenceRubric
from app.services.trust.rubrics import RUBRICS

#: Bumped when the fingerprinting itself changes (what it follows), which moves every fingerprint without any rubric
#: changing. The generator then re-pins all of them; a rubric change in the same commit would hide in that re-pin, so a
#: commit that changes the algorithm should change nothing else.
ALGORITHM = 6

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
    # The read paths call ``evaluate_loaded_statmech``, which picks the rubric by owner (``statmech_rubric_for``) and then
    # grades under it: the chooser is part of what decides which rubric judged a record.
    "computed_statmech": (
        "app.services.trust.evaluator:evaluate_loaded_statmech",
        "app.services.trust.evaluator:evaluate_loaded_statmech_under",
        "app.services.trust.rubrics:statmech_rubric_for",
    ),
    "computed_ts_statmech": (
        "app.services.trust.evaluator:evaluate_loaded_statmech",
        "app.services.trust.evaluator:evaluate_loaded_statmech_under",
        "app.services.trust.rubrics:statmech_rubric_for",
    ),
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

#: Calls to ``getattr``/``setattr`` with a computed attribute name, each reviewed to read or write a *data* attribute
#: (a column, a relationship, a model field), never to choose which function runs. The key is ``module:qualname :: call``,
#: the exact call as ``ast.unparse`` prints it, so a second computed call in the same function still fails the guard.
DYNAMIC_ATTRIBUTE_READS: dict[str, str] = {
    "app.services.reproducibility_rubric:_mapped_columns :: getattr(row, column.key)":
        "reads each mapped column of a row; the names come from the table",
    "app.services.reproducibility_rubric:_target_snapshot :: getattr(target, name)":
        "reads relationship names from fixed per-record-type tuples",
    "app.services.reproducibility_rubric:_typed_output_snapshot :: getattr(calculation, attr)":
        "reads the result relationship named by a fixed table",
    "app.services.scientific_read.ref_guard:_apply :: setattr(action.container, action.key, value)":
        "writes a withheld ref into the response tree being walked; a data edit, never a function choice",
    "app.services.scientific_read.ref_guard:_children :: getattr(node, name, None)":
        "walks a response model's fields generically (data traversal)",
    "app.services.scientific_read.ref_guard:_transform :: getattr(node, f.name)":
        "reads each declared field of a response dataclass (data traversal)",
    "app.services.scientific_read.ref_guard:_transform :: getattr(node, name)":
        "reads each field of a response model (data traversal)",
    "app.services.scientific_read.ref_guard:_review_statuses :: getattr(model, gated.parent_fk)":
        "reads the foreign-key column a fixed declaration names",
    "app.services.scientific_read.trust_floor:_swapped :: getattr(obj, attr)":
        "swaps loaded collections named by the callers' literal tuples",
    "app.services.trust.evaluator:_thermo_has_representation :: getattr(thermo.nasa, field)":
        "reads NASA coefficient columns from a fixed tuple",
    "app.services.trust.rubrics:_check_result_block_present :: getattr(calc, attr_name)":
        "reads one result attribute; each caller binds a literal name",
    "app.services.trust.rubrics:_thermo_has_nasa_representation :: getattr(nasa, field)":
        "reads NASA coefficient columns from a fixed tuple",
    "tckdb_schemas.structure_declarations:ActualProtocolDeclaration :: getattr(self, name)":
        "reads the declaration's own fields by the names of a fixed tuple",
    "tckdb_schemas.structure_declarations:ActualProtocolDeclaration.validate_content :: getattr(self, name)":
        "reads the declaration's own fields by the names of a fixed tuple",
}

#: Filled by :func:`discover_dynamic_attribute_sites` only; ``None`` in normal operation.
_DISCOVERED: list[str] | None = None
_DISCOVERED_ENV: list[str] | None = None

#: Environment or settings reads a verdict is allowed to depend on, each with the reason. Empty: none does.
DECLARED_INPUTS: dict[str, str] = {}

#: Module-level constants that are read from the environment or settings at import (a storage endpoint, a bucket). Each
#: differs between machines and is configuration, not a rule, so it is declared here with the reason rather than
#: fingerprinted. A module constant derived from the environment and absent from this table fails the guard.
DECLARED_ENVIRONMENT_CONSTANTS: dict[str, str] = {
    "app.services.artifact_storage.S3_ACCESS_KEY": "storage credential, deployment configuration",
    "app.services.artifact_storage.S3_BUCKET": "storage bucket name, deployment configuration",
    "app.services.artifact_storage.S3_ENDPOINT_URL": "storage endpoint, deployment configuration",
    "app.services.artifact_storage.S3_REGION": "storage region, deployment configuration",
    "app.services.artifact_storage.S3_SECRET_KEY": "storage credential, deployment configuration",
}

_ENV_NAMES = {"environ", "getenv", "environb", "settings"}
_Facts = tuple[dict[str, tuple[str, tuple[str, ...]]], set[str], dict[str, tuple[str, str]]]
_ENV_CACHE: dict[str, _Facts] = {}


def _module_env_facts(module_name: str) -> _Facts:
    """``({env-derived name: (source expression, helper functions it calls)}, env-reading functions, imports)``.

    A module constant is env-derived when its assignment mentions the environment or settings, or calls a function that
    does: one defined in the module, or one imported from an in-scope module (``from app.x import env_float``).
    """
    cached = _ENV_CACHE.get(module_name)
    if cached is not None:
        return cached
    env: dict[str, tuple[str, tuple[str, ...]]] = {}
    imported: dict[str, tuple[str, str]] = {}
    module = sys.modules.get(module_name)
    try:
        tree = ast.parse(inspect.getsource(module)) if module is not None else None  # type: ignore[arg-type]
    except (OSError, TypeError, SyntaxError):
        tree = None
    body = tree.body if tree is not None else []

    def mentions_env(node: ast.AST) -> bool:
        return any(
            (isinstance(n, ast.Attribute) and n.attr in _ENV_NAMES) or (isinstance(n, ast.Name) and n.id in _ENV_NAMES)
            for n in ast.walk(node)
        )

    for node in body:
        if isinstance(node, ast.ImportFrom) and node.module and _in_scope(node.module) and not node.level:
            for alias in node.names:
                imported[alias.asname or alias.name] = (node.module, alias.name)
    env_functions = {n.name for n in body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and mentions_env(n)}
    _ENV_CACHE[module_name] = (env, env_functions, imported)  # registered early: an import cycle sees a partial answer
    for local, (origin_module, origin_name) in imported.items():
        try:
            importlib.import_module(origin_module)
        except ImportError:
            continue
        if _is_env_function(origin_module, origin_name):
            env_functions.add(local)
    for node in body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            calls = [n.func.id for n in ast.walk(node.value) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in env_functions]
            if mentions_env(node.value) or calls:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for t in targets:
                    if isinstance(t, ast.Name):
                        env[t.id] = (ast.unparse(node.value), tuple(calls))
    return env, env_functions, imported


def _is_env_function(module_name: str, name: str, seen: set[tuple[str, str]] | None = None) -> bool:
    seen = seen if seen is not None else set()
    if (module_name, name) in seen:
        return False
    seen.add((module_name, name))
    _, env_functions, imported = _module_env_facts(module_name)
    if name in env_functions:
        return True
    if name in imported:
        origin_module, origin_name = imported[name]
        return _is_env_function(origin_module, origin_name, seen)
    return False


def _env_origin(module_name: str, name: str, seen: set[tuple[str, str]] | None = None) -> tuple[str, str, tuple[str, ...], str] | None:
    """``(module.name, expression, helpers, defining module)`` when *name* of *module_name* is env-derived, else ``None``."""
    seen = seen if seen is not None else set()
    if (module_name, name) in seen:
        return None
    seen.add((module_name, name))
    env, _, imported = _module_env_facts(module_name)
    if name in env:
        expression, helpers = env[name]
        return f"{module_name}.{name}", expression, helpers, module_name
    if name in imported:
        origin_module, origin_name = imported[name]
        importlib.import_module(origin_module)
        return _env_origin(origin_module, origin_name, seen)
    return None


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


_FIELD_DOCUMENTATION = {"description", "title", "examples", "json_schema_extra"}
_MESSAGE_KEYWORDS = {"detail", "message", "msg"}


def _is_text(node: ast.AST) -> bool:
    return isinstance(node, ast.JoinedStr) or (isinstance(node, ast.Constant) and isinstance(node.value, str))


_CODE_PREFIX = re.compile(r"\s*([a-z][a-z0-9_]*):")


def _text_head(node: ast.AST) -> str | None:
    """The leading text of a string or f-string, or ``None`` when *node* is not text."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        first = node.values[0] if node.values else None
        return first.value if isinstance(first, ast.Constant) and isinstance(first.value, str) else ""
    return None


def _blank_message(node: ast.AST) -> ast.AST:
    """The message with its wording removed but any leading ``code:`` kept: an error code is part of the contract."""
    head = _text_head(node)
    match = _CODE_PREFIX.match(head) if head else None
    return ast.Constant(f"<message {match.group(1)}>" if match else "<message>")


class _Normalise(ast.NodeTransformer):
    """Remove wording that cannot change a verdict: documentation keywords of pydantic ``Field`` and the plain-text
    message of a ``raise``. An error code stays: the first positional argument of a ``Coded*`` error, any argument named
    like ``code``, and a ``code:`` prefix of a message. Every non-text argument stays."""

    def visit_Call(self, node: ast.Call) -> ast.AST:
        self.generic_visit(node)
        func = node.func
        if (isinstance(func, ast.Name) and func.id == "Field") or (isinstance(func, ast.Attribute) and func.attr == "Field"):
            node.keywords = [k for k in node.keywords if k.arg not in _FIELD_DOCUMENTATION]
        return node

    def visit_Raise(self, node: ast.Raise) -> ast.AST:
        exc = node.exc
        if isinstance(exc, ast.Call):
            name = exc.func.id if isinstance(exc.func, ast.Name) else (exc.func.attr if isinstance(exc.func, ast.Attribute) else "")
            coded = name.startswith("Coded")
            new_args: list[ast.expr] = []
            for index, arg in enumerate(exc.args):
                if _text_head(arg) is None:
                    new_args.append(self.visit(arg))
                elif coded and index == 0:
                    new_args.append(arg)  # the code itself
                else:
                    new_args.append(_blank_message(arg))  # type: ignore[arg-type]
            exc.args = new_args
            for keyword in exc.keywords:
                if _text_head(keyword.value) is None:
                    keyword.value = self.visit(keyword.value)
                elif keyword.arg and "code" in keyword.arg:
                    continue
                elif keyword.arg in _MESSAGE_KEYWORDS:
                    keyword.value = _blank_message(keyword.value)  # type: ignore[assignment]
        return node


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
        names, text, imports = self._function_text(fn)
        key = f"{fn.__module__}:{fn.__qualname__}"
        if fn.__name__ == "<lambda>":
            key += "#" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]
        if key in self.code:
            return
        self.code[key] = text
        scope: dict[str, Any] = dict(fn.__globals__)
        if fn.__closure__:
            for name, cell in zip(fn.__code__.co_freevars, fn.__closure__, strict=True):
                try:
                    scope[name] = cell.cell_contents
                except ValueError:
                    continue
        self._bind_local_imports(imports, scope, fn)
        for default in (*(fn.__defaults__ or ()), *(fn.__kwdefaults__ or {}).values()):
            self.constants[f"{key}#default"] = self._canon(default, f"default of {key}")
        for chain in names:
            self._resolve_chain(chain, scope, key)

    @staticmethod
    def _bind_local_imports(imports: list[ast.stmt], scope: dict[str, Any], fn: FunctionType) -> None:
        """Make the names a function imports in its body resolvable, as module globals are. Out-of-scope modules are
        left out; an in-scope module that cannot be imported is refused."""
        for node in imports:
            try:
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if _in_scope(alias.name):
                            top = importlib.import_module(alias.name.split(".")[0])
                            scope[alias.asname or alias.name.split(".")[0]] = (
                                importlib.import_module(alias.name) if alias.asname else top
                            )
                elif isinstance(node, ast.ImportFrom):
                    if node.level:
                        package = fn.__module__.rsplit(".", node.level)[0] if node.level else fn.__module__
                        module_name = f"{package}.{node.module}" if node.module else package
                    else:
                        module_name = node.module or ""
                    if not _in_scope(module_name):
                        continue
                    module = importlib.import_module(module_name)
                    for alias in node.names:
                        if alias.name == "*":
                            raise Unfingerprintable(f"{fn.__module__}:{fn.__qualname__} uses `from {module_name} import *`")
                        if hasattr(module, alias.name):
                            scope[alias.asname or alias.name] = getattr(module, alias.name)
                        else:
                            scope[alias.asname or alias.name] = importlib.import_module(f"{module_name}.{alias.name}")
            except ImportError as exc:
                raise Unfingerprintable(
                    f"{fn.__module__}:{fn.__qualname__} imports {ast.unparse(node)!r}, which cannot be imported: {exc}"
                ) from exc

    def _function_text(self, fn: FunctionType) -> tuple[list[tuple[str, ...]], str, list[ast.stmt]]:
        """The normalised code of *fn*, the dotted names it uses (each a tuple of parts) and its local imports."""
        tree = None
        if fn.__name__ != "<lambda>":
            try:
                tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
            except (OSError, TypeError, SyntaxError):
                tree = None
        if tree is None:
            # A lambda (or source the parser cannot take on its own): read the code object instead.
            return self._code_object_names(fn.__code__), self._code_object_text(fn.__code__, fn.__doc__), []
        _strip_docstrings(tree)
        tree = ast.fix_missing_locations(_Normalise().visit(tree))
        chains: list[tuple[str, ...]] = []
        imports: list[ast.stmt] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                imports.append(node)
            if isinstance(node, ast.Call):
                self._check_call(node, fn)
            self._check_lookup(node, fn)
            if isinstance(node, ast.Attribute):
                parts = self._attr_parts(node)
                if parts:
                    chains.append(parts)
            elif isinstance(node, ast.Name):
                chains.append((node.id,))
        return chains, ast.dump(tree, annotate_fields=False, include_attributes=False), imports

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
                call_key = f"{where} :: {ast.unparse(node)}"
                if _DISCOVERED is not None:
                    _DISCOVERED.append(call_key)
                    return
                if call_key in DYNAMIC_ATTRIBUTE_READS:
                    return
                raise Unfingerprintable(
                    f"{where} calls {name}() with a computed attribute name ({ast.unparse(node)}): what it dispatches "
                    "to cannot be followed statically. Use a literal name or an explicit table, or list this exact call "
                    "in DYNAMIC_ATTRIBUTE_READS."
                )
        if name in {"import_module", "attrgetter", "methodcaller"}:
            raise Unfingerprintable(f"{where} calls {name}(): dynamic dispatch the fingerprint cannot follow")

    @staticmethod
    def _check_lookup(node: ast.AST, fn: FunctionType) -> None:
        """Refuse reaching code through ``sys.modules`` or an object's ``__dict__``: what it finds cannot be followed."""
        where = f"{fn.__module__}:{fn.__qualname__}"

        def is_dict(value: ast.AST) -> bool:
            return isinstance(value, ast.Attribute) and value.attr == "__dict__"

        by_name = (
            (isinstance(node, ast.Subscript) and is_dict(node.value))
            or (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"get", "pop", "setdefault", "__getitem__"}
                and is_dict(node.func.value)
            )
            or (isinstance(node, ast.Attribute) and node.attr == "modules" and isinstance(node.value, ast.Name) and node.value.id == "sys")
        )
        if by_name:
            raise Unfingerprintable(f"{where} reads {ast.unparse(node)}: a lookup by name the fingerprint cannot follow")

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
        module_name = where.split(":")[0]
        label = f"{module_name}.{base_name}"
        found = _env_origin(module_name, base_name) if isinstance(value, _SCALARS) else None
        if found is not None:
            origin, expression, helpers, defining_module = found
            if _DISCOVERED_ENV is not None:
                _DISCOVERED_ENV.append(origin)
            if origin not in DECLARED_ENVIRONMENT_CONSTANTS:
                if _DISCOVERED_ENV is not None:
                    return
                raise Unfingerprintable(
                    f"{where} reads {origin}, a module constant taken from the environment or settings at import: it "
                    "differs between machines and the fingerprint cannot see what it is. Declare it in "
                    "DECLARED_ENVIRONMENT_CONSTANTS with the reason it is configuration and not a rule."
                )
            # What a deployment sets is configuration, but how the constant is derived (the default, a conversion, a
            # clamp) is code: the declared assignment's expression and the helpers it calls are fingerprinted.
            self.constants[f"{origin}#environment"] = DECLARED_ENVIRONMENT_CONSTANTS[origin]
            self.constants[f"{origin}#expression"] = expression
            defined = sys.modules[defining_module].__dict__
            for helper in helpers:
                function = inspect.unwrap(defined[helper]) if helper in defined else None
                if isinstance(function, FunctionType):
                    # Its source, not its environment read: that read is the configuration being declared.
                    self.code[f"{function.__module__}:{function.__qualname__}"] = self._function_text(function)[1]
            return
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
            if any(c.__name__ == "BaseSettings" for c in value.__mro__):
                self._refuse_input(label, where, "constructs settings (reads the environment)")
                return
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
            tree = ast.fix_missing_locations(_Normalise().visit(tree))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    self._check_call(node, _Stub(cls))  # type: ignore[arg-type]
                self._check_lookup(node, _Stub(cls))  # type: ignore[arg-type]
            self.code[f"class {key}"] = ast.dump(tree, annotate_fields=False, include_attributes=False)
        if orm:
            self._visit_orm_graph(cls, key)
        for member_name, member in vars(cls).items():
            if isinstance(member, (hybrid_property, hybrid_method)):
                for accessor in (member.func if isinstance(member, hybrid_method) else member.fget,
                                 getattr(member, "fset", None), getattr(member, "expr", None)):
                    if isinstance(accessor, FunctionType):
                        self.visit(accessor)
            elif isinstance(member, (FunctionType, staticmethod, classmethod, property)):
                self.visit(member)
            elif not orm and not member_name.startswith("__") and not (
                issubclass(cls, enum.Enum) and member_name.startswith("_") and member_name.endswith("_")
            ):
                canon = self._canon(member, f"{key}.{member_name}")
                if canon is not _IGNORED and callable(member) is False:
                    self.constants[f"{key}.{member_name}"] = canon
        for base in cls.__mro__[1:]:
            if base is not object and _in_scope(base.__module__):
                self._visit_class(base)

    def _visit_orm_graph(self, cls: type, key: str) -> None:
        """An ORM instance carries behaviour in its enum columns and in the models it relates to: follow both, and
        fingerprint any SQL-expression column (``column_property``)."""
        configure_mappers()
        mapper = cls.__mapper__  # type: ignore[attr-defined]
        for column in mapper.columns:
            enum_class = getattr(column.type, "enum_class", None)
            if enum_class is not None:
                self._visit_class(enum_class)
        for prop in mapper.column_attrs:
            if any(not isinstance(c, Column) for c in prop.columns):
                for c in prop.columns:
                    if not isinstance(c, Column):
                        self.constants[f"{key}.{prop.key}#column_property"] = str(c)
        for relationship in mapper.relationships:
            self._visit_class(relationship.mapper.class_)

    # ---- constants -------------------------------------------------------------------------------------------

    def _canon(self, value: Any, label: str, top: bool = True) -> Any:
        """A JSON-stable form of *value*, visiting any callable inside it; ``_IGNORED`` for out-of-scope objects."""
        if isinstance(value, enum.Enum):
            self._visit_class(type(value))
            return {"enum": f"{type(value).__name__}.{value.name}", "value": self._canon(value.value, label, False)}
        if isinstance(value, _SCALARS):
            return value.hex() if isinstance(value, bytes) else value
        if isinstance(value, re.Pattern):
            return {"re": value.pattern, "flags": value.flags}
        if isinstance(value, (tuple, list)):
            return [self._canon(v, label, False) for v in value]
        if isinstance(value, (set, frozenset)):
            parts = [self._canon(v, label, False) for v in value]
            return sorted(parts, key=lambda p: json.dumps(p, sort_keys=True, default=str))
        if isinstance(value, dict):
            items = {
                json.dumps(self._canon(k, label, False), sort_keys=True, default=str): self._canon(v, label, False)
                for k, v in value.items()
            }
            return dict(sorted(items.items()))
        if isinstance(value, (FunctionType, partial, staticmethod, classmethod, property)) or inspect.isclass(value):
            self._reference(label, value, label)
            return f"callable:{getattr(value, '__qualname__', type(value).__name__)}"
        if callable(value) and getattr(value, "__wrapped__", None) is not None:
            self._reference(label, value, label)
            return f"callable:{getattr(value, '__qualname__', type(value).__name__)}"
        if isinstance(value, ClauseElement):
            try:
                return {"sql": str(value.compile(compile_kwargs={"literal_binds": True}))}
            except Exception:
                return {"sql": str(value)}
        module = type(value).__module__
        if not _in_scope(module):
            if top:
                return _IGNORED
            text = repr(value)
            return {
                "out_of_scope": f"{module}.{type(value).__qualname__}",
                "repr": None if (" at 0x" in text or len(text) > 200 or isinstance(value, PurePath)) else text,
            }
        if _is_opaque(value):
            return {"opaque": type(value).__name__}
        self._visit_class(type(value))
        if dataclasses.is_dataclass(value):
            return {"dataclass": type(value).__name__, "fields": self._canon(dataclasses.asdict(value), label, False)}
        if hasattr(value, "model_dump"):
            return {"model": type(value).__name__, "fields": self._canon(value.model_dump(mode="python"), label, False)}
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


def stable_dump_for_tests(rubric_name: str) -> str:
    """The JSON the fingerprint of a reproducibility rubric is hashed from (a test asserts it holds no object address)."""
    walker = _Walker()
    for dotted in REPRODUCIBILITY_ENTRYPOINTS:
        walker.visit(_resolve(dotted))
    return json.dumps({"name": rubric_name, "code": walker.code, "constants": walker.constants}, sort_keys=True, default=str)


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


def discover_environment_constants() -> list[str]:
    """Every environment-derived module constant reachable from the pinned entrypoints (a maintenance aid)."""
    global _DISCOVERED_ENV
    _DISCOVERED_ENV = []
    try:
        current_fingerprints()
        return sorted(set(_DISCOVERED_ENV))
    finally:
        _DISCOVERED_ENV = None


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
