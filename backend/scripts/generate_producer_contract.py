#!/usr/bin/env python
"""Generate the producer contract shipped inside ``tckdb-schemas``.

Usage::

    conda run -n tckdb_env python backend/scripts/generate_producer_contract.py
    conda run -n tckdb_env python backend/scripts/generate_producer_contract.py --check

Writes, under ``schemas/python/tckdb-schemas/tckdb_schemas/contract/``:

* ``PRODUCER_CONTRACT.md`` -- what TCKDB accepts today, organised by what a
  producer wants to send;
* ``schemas/<model>.schema.json`` -- one JSON Schema per upload payload.

``--check`` writes nothing and exits 1 if any committed file differs from a
fresh render (or a schema file exists that no route accepts any more),
printing the diff and the exact fix.

Why this exists
---------------
A producer adapter lives in another repository and is written against the
contract it was told about. On 2026-09-23 (#520) a thermo block carrying an
enthalpy began to require ``enthalpy_reference_kind: "formation_298k"``, and
on 2026-09-24 (#536) ``reference_pressure_bar`` stopped being defaulted.
The ARC adapter learned neither, and every thermo deposit it made was
refused -- because the only places either rule was written down were a
workflow function and a code comment. This file is the producer-facing
place, and it cannot fall behind because it is rendered from the code that
enforces the rules and compared against the committed copy in CI.

Where each fact comes from
--------------------------
Nothing below the hand-written preamble (:data:`PREAMBLE`) is typed by
hand. In particular:

* **Surfaces** are the routes of ``create_app()`` that take a request body,
  require an authenticated caller, and are not role-gated to curators or
  admins (:func:`classify_route`). Every other write route is listed with
  the reason it was left out, so an omission is visible rather than silent.
* **Fields** come from the Pydantic models the routes declare: type,
  required/default, constraints (``gt``, ``min_length``, ``pattern`` ...),
  enum members, the field's ``description=`` or the model's own
  ``:param name:`` docstring entry, and a unit read from the field name's
  suffix (the unit policy's fixed-unit column names).
* **Payload rules** are the ``model_validator``/``field_validator``
  functions on every model the payload can contain. The text is the
  validator's docstring; where it has none, the refusal messages its own
  ``raise`` statements carry. A validator with neither is a generation
  error, so the next undocumented rule fails CI rather than printing an
  empty bullet.
* **Workflow rules** are found by tracing each route's handler through its
  direct calls (:class:`Tracer`). Every function reached that is either
  marked with :func:`tckdb_schemas.producer_rule.producer_rule` or declared
  as a :class:`~app.scientific_checks.PythonCheck` in the scientific check
  register is printed for that route -- the marked function's docstring, or
  the register's ``asserts``/``escape_hatch``.
* **Refusal codes** are the entries of :mod:`app.api.code_catalogue` whose
  literal (or a module constant holding it) appears in a function that the
  route's handler, dependencies or payload validators reach. The trace is
  static: it follows plain calls and module/class attributes, not dynamic
  dispatch, so it can miss a code (the reference section lists every
  client-facing code, traced or not) and a listed code is *reachable*, not
  necessarily reachable for every payload. Database-constraint codes are
  mapped by a stated heuristic: the route's code path names the ORM class
  of the table that carries the constraint.
* **Examples** are the ``examples`` each payload model declares in its
  ``json_schema_extra``. They are validated against the model and
  round-tripped here, and a model without one fails generation.
* **What changed** is ``schemas/python/tckdb-schemas/CHANGELOG.md``.

The commit stamp, and why there is none
---------------------------------------
The file is stamped with the ``tckdb-schemas`` version and deliberately not
with a git commit. A file cannot contain the hash of the commit that
contains it, so a commit stamp would either be the *previous* commit (wrong)
or change on every commit (making ``--check`` fail on any unrelated change).
Stamping at wheel-build time would need a custom build backend and a git
checkout at build time, which an sdist install does not have. The version is
enough: ``check_package_version_bump.py`` counts these files as distributed
content, so one version number can only ever name one contract, and this
generator's ``--check`` guarantees that the contract at a version is what
the source at that version renders.
"""

from __future__ import annotations

import argparse
import ast
import builtins
import dataclasses
import datetime as _dt
import difflib
import enum
import inspect
import json
import re
import sys
import textwrap
import types
import typing
import uuid
from collections.abc import Callable, Iterable
from decimal import Decimal
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def _refuse_a_foreign_schemas_package() -> None:
    """Refuse to render another checkout's wire models under this version.

    In a git worktree the editable ``tckdb-schemas`` install resolves to the
    checkout it was installed from, so without this the contract would describe
    that checkout's models. Same comparison as ``backend/tests/conftest.py``.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "tckdb_schemas_checkout", BACKEND_ROOT / "scripts" / "lib" / "schemas_checkout.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    message = module.foreign_schemas_message(REPO_ROOT)
    if message is not None:
        raise SystemExit(f"Cannot generate the producer contract: {message}")


_refuse_a_foreign_schemas_package()

from fastapi.dependencies.models import Dependant  # noqa: E402
from fastapi.routing import APIRoute  # noqa: E402
from pydantic import BaseModel  # noqa: E402
from pydantic.fields import FieldInfo  # noqa: E402
from pydantic_core import PydanticUndefined, to_jsonable_python  # noqa: E402
from tckdb_schemas.producer_rule import is_producer_rule  # noqa: E402

from app.api.app import create_app  # noqa: E402
from app.api.code_catalogue import CATALOGUE, ApiCode, Shape  # noqa: E402
from app.db.models.common import AppUserRole  # noqa: E402
from app.scientific_checks import DatabaseConstraint, PythonCheck, ScientificCheck  # noqa: E402
from app.scientific_checks.declarations import register  # noqa: E402

GENERATOR = "backend/scripts/generate_producer_contract.py"
PACKAGE_ROOT = REPO_ROOT / "schemas" / "python" / "tckdb-schemas"
PYPROJECT = PACKAGE_ROOT / "pyproject.toml"
CHANGELOG = PACKAGE_ROOT / "CHANGELOG.md"
CONTRACT_DIR = PACKAGE_ROOT / "tckdb_schemas" / "contract"
MARKDOWN_NAME = "PRODUCER_CONTRACT.md"
SCHEMA_SUBDIR = "schemas"
SCHEMA_SUFFIX = ".schema.json"

REGENERATE = f"conda run -n tckdb_env python {GENERATOR}"

#: Dependencies that gate a route to a role. A producer route has none.
ROLE_GATES: dict[str, str] = {
    "require_admin": "admin",
    "require_curator_or_admin": "curator or admin",
    "require_session_user": "a browser session (not an API key)",
}
#: The dependency that makes a route require an authenticated caller.
AUTHENTICATED = "get_current_user"
IDEMPOTENCY = "idempotency_dependency"
WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: Longest-suffix-first map from a fixed-unit column name to its unit. This is
#: the unit policy's naming convention read back, not a list of fields: a new
#: field named ``*_kj_mol`` is labelled without anyone touching this table.
UNIT_SUFFIXES: tuple[tuple[str, str], ...] = (
    ("_hartree_bohr2", "hartree/bohr^2"),
    ("_mdyne_angstrom", "mdyn/angstrom"),
    ("_angstrom3", "angstrom^3"),
    ("_j_mol_k", "J/(mol*K)"),
    ("_kj_mol", "kJ/mol"),
    ("_km_mol", "km/mol"),
    ("_hartree", "hartree"),
    ("_angstrom", "angstrom"),
    ("_cm_inv", "cm^-1"),
    ("_degrees", "degrees"),
    ("_debye", "debye"),
    ("_bytes", "bytes"),
    ("_cm1", "cm^-1"),
    ("_amu", "amu"),
    ("_bar", "bar"),
    ("_k", "K"),
)


# ---------------------------------------------------------------------------
# Small rendering helpers
# ---------------------------------------------------------------------------


def _cell(text: object) -> str:
    """Make ``text`` safe for one markdown table cell."""
    if text is None:
        return ""
    return " ".join(str(text).split()).replace("|", "\\|")


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _anchor(*parts: str) -> str:
    raw = "-".join(parts).lower()
    return re.sub(r"[^a-z0-9]+", "-", raw).strip("-")


def _first_paragraph(doc: str | None) -> str:
    if not doc:
        return ""
    return _one_line(inspect.cleandoc(doc).split("\n\n", 1)[0])


def _own_doc(obj: object) -> str | None:
    """The object's *own* docstring (``inspect.getdoc`` would inherit one)."""
    doc = obj.__dict__.get("__doc__") if isinstance(obj, type) else getattr(obj, "__doc__", None)
    if isinstance(doc, str) and doc.strip():
        return inspect.cleandoc(doc)
    return None


def _in_scope(obj: object) -> bool:
    module = getattr(obj, "__module__", None) or ""
    return module == "app" or module.startswith("app.") or module.split(".", 1)[0] == "tckdb_schemas"


def _unit_for(name: str) -> str:
    for suffix, unit in UNIT_SUFFIXES:
        if name.endswith(suffix):
            return unit
    return ""


def _json(value: object) -> str:
    return json.dumps(to_jsonable_python(value), sort_keys=True, ensure_ascii=False)


# ---------------------------------------------------------------------------
# String expressions: rendering source-level messages
# ---------------------------------------------------------------------------


def _render_str_expr(node: ast.AST) -> str | None:
    """Render a string-valued expression as text, ``{expr}`` for placeholders."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                parts.append("{" + ast.unparse(value.value) + "}")
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _render_str_expr(node.left), _render_str_expr(node.right)
        if left is not None and right is not None:
            return left + right
    return None


_CODE_PREFIX = re.compile(r"^([a-z][a-z0-9_]*): ")


# ---------------------------------------------------------------------------
# Static tracing: which functions, codes, tables and rules a root reaches
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class _Facts:
    """What one function's own body references."""

    callees: list[object]
    codes: set[str]
    tables: set[str]


@dataclasses.dataclass
class Trace:
    """Everything reachable from a set of roots."""

    functions: dict[str, Callable[..., object]] = dataclasses.field(default_factory=dict)
    code_sites: dict[str, set[str]] = dataclasses.field(default_factory=dict)
    tables: set[str] = dataclasses.field(default_factory=set)

    def merge(self, other: Trace) -> None:
        self.functions.update(other.functions)
        for code, sites in other.code_sites.items():
            self.code_sites.setdefault(code, set()).update(sites)
        self.tables |= other.tables


def _key(func: Callable[..., object]) -> str:
    return f"{func.__module__}:{func.__qualname__}"


def _owner_class(func: Callable[..., object]) -> type | None:
    parts = func.__qualname__.split(".")[:-1]
    if not parts or "<locals>" in parts:
        return None
    obj: object = sys.modules.get(func.__module__)
    for part in parts:
        obj = getattr(obj, part, None)
        if obj is None:
            return None
    return obj if isinstance(obj, type) else None


def _pydantic_validator_funcs(model: type[BaseModel]) -> list[Callable[..., object]]:
    decorators = model.__pydantic_decorators__
    funcs = []
    for group in (decorators.model_validators, decorators.field_validators):
        for dec in group.values():
            func = inspect.unwrap(getattr(dec.func, "__func__", dec.func))
            funcs.append(func)
    return funcs


class Tracer:
    """Follow direct calls from a root through ``app`` and ``tckdb_schemas``.

    Resolution is by *runtime* object, not by name: a ``Name`` in a function
    body is looked up in that function's ``__globals__``, an ``Attribute`` is
    followed only through modules and classes, and ``self``/``cls`` resolve
    to the class the method is defined on. So a callee the trace names is a
    real object the code refers to. What it cannot see -- a function held in
    a variable, a method called on an instance whose class the body does not
    name -- is simply absent, which is why every consumer of a trace says
    "reachable" and never "complete".
    """

    def __init__(self, codes: frozenset[str]) -> None:
        self._codes = codes
        self._facts: dict[str, _Facts] = {}

    # -- per-function facts -------------------------------------------------

    def _code_hits(self, value: object) -> set[str]:
        if isinstance(value, str):
            if value in self._codes:
                return {value}
            match = _CODE_PREFIX.match(value)
            if match and match.group(1) in self._codes:
                return {match.group(1)}
            return set()
        # A small literal collection (``_CODES = ("a", "b")``) names its codes; a
        # large one is a registry (the catalogue's own code sets), and reading
        # it as "this function raises every code in the registry" would
        # attribute the whole catalogue to one handler.
        if isinstance(value, (tuple, list, set, frozenset)) and 0 < len(value) <= _SMALL_COLLECTION:
            hits: set[str] = set()
            for item in value:
                if isinstance(item, str):
                    hits |= self._code_hits(item)
            return hits
        return set()

    def _resolve(self, node: ast.AST, func: Callable[..., object], owner: type | None) -> object:
        if isinstance(node, ast.Name):
            if node.id in {"self", "cls"} and owner is not None:
                return owner
            return func.__globals__.get(node.id, vars(builtins).get(node.id, _MISSING))
        if isinstance(node, ast.Attribute):
            base = self._resolve(node.value, func, owner)
            if isinstance(base, (types.ModuleType, type)):
                try:
                    return inspect.getattr_static(base, node.attr)
                except AttributeError:
                    return _MISSING
            return _MISSING
        return _MISSING

    def facts(self, func: Callable[..., object]) -> _Facts:
        key = _key(func)
        cached = self._facts.get(key)
        if cached is not None:
            return cached
        facts = _Facts(callees=[], codes=set(), tables=set())
        self._facts[key] = facts
        try:
            source = textwrap.dedent(inspect.getsource(func))
            tree = ast.parse(source)
        except (OSError, TypeError, SyntaxError, IndentationError):
            return facts
        owner = _owner_class(func)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant):
                facts.codes |= self._code_hits(node.value)
            elif isinstance(node, (ast.Name, ast.Attribute)) and isinstance(getattr(node, "ctx", None), ast.Load):
                target = self._resolve(node, func, owner)
                if target is _MISSING:
                    continue
                facts.codes |= self._code_hits(target)
                self._classify_target(target, facts)
        return facts

    def _classify_target(self, target: object, facts: _Facts) -> None:
        if isinstance(target, (staticmethod, classmethod)):
            target = target.__func__
        if isinstance(target, types.MethodType):
            target = target.__func__
        if isinstance(target, types.FunctionType):
            target = inspect.unwrap(target)
            if _in_scope(target):
                facts.callees.append(target)
            return
        if isinstance(target, type) and _in_scope(target):
            tablename = target.__dict__.get("__tablename__")
            if isinstance(tablename, str):
                facts.tables.add(tablename)
                return
            if issubclass(target, BaseModel):
                facts.callees.extend(_pydantic_validator_funcs(target))
                return
            for name in ("__init__", "__post_init__", "__call__", "dispatch"):
                member = target.__dict__.get(name)
                if isinstance(member, types.FunctionType):
                    facts.callees.append(inspect.unwrap(member))

    # -- closure ------------------------------------------------------------

    def trace(self, roots: Iterable[Callable[..., object]]) -> Trace:
        result = Trace()
        stack = [inspect.unwrap(root) for root in roots]
        while stack:
            func = stack.pop()
            key = _key(func)
            if key in result.functions:
                continue
            result.functions[key] = func
            facts = self.facts(func)
            for code in facts.codes:
                result.code_sites.setdefault(code, set()).add(key)
            result.tables |= facts.tables
            stack.extend(facts.callees)
        return result


_MISSING = object()
_SMALL_COLLECTION = 8


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class HeaderParam:
    name: str
    required: bool
    source: str
    purpose: str


@dataclasses.dataclass
class RouteInfo:
    method: str
    path: str
    status_code: int | None
    route: APIRoute
    dependencies: tuple[Callable[..., object], ...]
    dependency_names: frozenset[str]
    body_models: tuple[object, ...]
    category: str
    reason: str

    @property
    def label(self) -> str:
        return f"{self.method} {self.path}"


def _walk_dependencies(dependant: Dependant) -> list[Dependant]:
    found: list[Dependant] = []
    stack = list(dependant.dependencies)
    while stack:
        sub = stack.pop(0)
        found.append(sub)
        stack.extend(sub.dependencies)
    return found


def classify_route(route: APIRoute) -> RouteInfo:
    """Decide whether a route is a producer surface, and say why if not.

    A producer surface is a write route that takes a request body, requires
    an authenticated caller (``get_current_user``) and is not role-gated.
    The rule is mechanical on purpose: a new upload route is covered the day
    it is registered, and a route that is left out is listed with the rule
    that left it out.
    """
    deps = _walk_dependencies(route.dependant)
    callables = tuple(dep.call for dep in deps if dep.call is not None)
    names = frozenset(getattr(call, "__name__", "") for call in callables)
    bodies = tuple(bp.field_info.annotation for bp in route.dependant.body_params)
    method = ",".join(sorted(route.methods & WRITE_METHODS)) or ",".join(sorted(route.methods))
    gates = sorted(ROLE_GATES[name] for name in names if name in ROLE_GATES)
    if not route.methods & WRITE_METHODS:
        category, reason = "read", "not a write method"
    elif route.path.startswith("/api/v1/auth/"):
        category, reason = "account", "account and session management, not scientific content"
    elif gates:
        category, reason = "role-gated", f"role-gated to {', '.join(gates)}"
    elif AUTHENTICATED not in names:
        category, reason = "anonymous", "does not require an authenticated caller (a query, not a deposit)"
    elif not bodies:
        category, reason = "no-body", "takes no request body"
    else:
        category, reason = "producer", "authenticated write with a request body"
    return RouteInfo(
        method=method,
        path=route.path,
        status_code=route.status_code,
        route=route,
        dependencies=callables,
        dependency_names=names,
        body_models=bodies,
        category=category,
        reason=reason,
    )


def discover_routes(app: Any | None = None) -> list[RouteInfo]:
    """Every ``APIRoute`` of the live app, classified."""
    app = app or create_app()
    return [classify_route(route) for route in app.routes if isinstance(route, APIRoute)]


def _header_params(route: APIRoute) -> list[HeaderParam]:
    params: list[HeaderParam] = []
    seen: set[str] = set()

    def visit(dependant: Dependant, source: str, purpose: str) -> None:
        for param in dependant.header_params:
            alias = param.alias or param.name
            if alias.lower() in seen:
                continue
            seen.add(alias.lower())
            params.append(HeaderParam(alias, param.field_info.is_required(), source, purpose))
        for cookie in dependant.cookie_params:
            alias = f"cookie {cookie.alias or cookie.name}"
            if alias in seen:
                continue
            seen.add(alias)
            params.append(HeaderParam(alias, cookie.field_info.is_required(), source, purpose))

    visit(route.dependant, "route", _first_paragraph(_own_doc(route.endpoint)))
    for dep in _walk_dependencies(route.dependant):
        call = dep.call
        name = getattr(call, "__name__", "?")
        visit(dep, name, _first_paragraph(_own_doc(call)) if call is not None else "")
    return params


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


def _nested_models(annotation: object) -> list[type[BaseModel]]:
    found: list[type[BaseModel]] = []
    stack = [annotation]
    while stack:
        current = stack.pop(0)
        if isinstance(current, type) and issubclass(current, BaseModel):
            found.append(current)
            continue
        stack.extend(typing.get_args(current))
    return found


def model_closure(root: type[BaseModel]) -> list[type[BaseModel]]:
    """``root`` and every model its fields can contain, breadth-first in field order."""
    order: list[type[BaseModel]] = []
    queue = [root]
    while queue:
        model = queue.pop(0)
        if model in order:
            continue
        order.append(model)
        for field in model.model_fields.values():
            queue.extend(sub for sub in _nested_models(field.annotation) if sub not in order)
    return order


class ModelNames:
    """Display names and anchors for models, unique across the document."""

    def __init__(self, models: Iterable[type[BaseModel]]) -> None:
        by_name: dict[str, set[type[BaseModel]]] = {}
        for model in models:
            by_name.setdefault(model.__name__, set()).add(model)
        self._ambiguous = {name for name, found in by_name.items() if len(found) > 1}

    def display(self, model: type) -> str:
        if model.__name__ in self._ambiguous:
            return f"{model.__name__} ({model.__module__.rsplit('.', 1)[-1]})"
        return model.__name__

    def anchor(self, model: type) -> str:
        if model.__name__ in self._ambiguous:
            return _anchor("m", model.__name__, model.__module__.rsplit(".", 1)[-1])
        return _anchor("m", model.__name__)

    def link(self, model: type) -> str:
        return f"[`{self.display(model)}`](#{self.anchor(model)})"


_JSON_SCALARS: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    bytes: "string (base64 bytes)",
    Decimal: "number",
    _dt.datetime: "string (date-time)",
    _dt.date: "string (date)",
    uuid.UUID: "string (uuid)",
    dict: "object",
    list: "array",
}


def format_type(annotation: object, names: ModelNames) -> str:
    """Render an annotation in JSON terms, linking nested models."""
    if annotation is type(None) or annotation is None:
        return "null"
    if annotation is Any:
        return "any"
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if origin is typing.Annotated:
        return format_type(args[0], names)
    if origin in (typing.Union, types.UnionType):
        return " | ".join(format_type(arg, names) for arg in args)
    if origin is typing.Literal:
        return " | ".join(json.dumps(to_jsonable_python(arg)) for arg in args)
    if origin in (list, set, frozenset, tuple) or (origin is not None and getattr(origin, "__name__", "") in {"Sequence", "Iterable"}):
        inner = ", ".join(format_type(arg, names) for arg in args if arg is not Ellipsis) or "any"
        return f"array of {inner}"
    if origin is dict:
        if len(args) == 2:
            return f"object ({format_type(args[0], names)} -> {format_type(args[1], names)})"
        return "object"
    if isinstance(annotation, type):
        if issubclass(annotation, BaseModel):
            return names.link(annotation)
        if issubclass(annotation, enum.Enum):
            return f"`{annotation.__name__}`"
        for scalar, label in _JSON_SCALARS.items():
            if issubclass(annotation, scalar) and not (scalar is int and annotation is bool):
                if annotation is bool:
                    return "boolean"
                return label
        return f"`{annotation.__name__}`"
    return f"`{getattr(annotation, '__name__', str(annotation))}`"


def _enums_in(annotation: object) -> list[type[enum.Enum]]:
    found: list[type[enum.Enum]] = []
    stack = [annotation]
    while stack:
        current = stack.pop(0)
        if isinstance(current, type) and issubclass(current, enum.Enum):
            if current not in found:
                found.append(current)
            continue
        if isinstance(current, type) and issubclass(current, BaseModel):
            continue
        stack.extend(typing.get_args(current))
    return found


#: An enum with more members than this is printed once, in the enum section,
#: and linked from each field; a smaller one is spelled out in the field row.
ENUM_INLINE_MAX = 8


def enum_anchor(enum_class: type[enum.Enum]) -> str:
    return _anchor("e", enum_class.__name__)


def enum_values_text(enum_class: type[enum.Enum]) -> str:
    if len(enum_class) > ENUM_INLINE_MAX:
        return f"[`{enum_class.__name__}`](#{enum_anchor(enum_class)}) ({len(enum_class)} values)"
    return ", ".join(f"`{member.value}`" for member in enum_class)


def _literal_values(annotation: object) -> list[object]:
    values: list[object] = []
    stack = [annotation]
    while stack:
        current = stack.pop(0)
        if typing.get_origin(current) is typing.Literal:
            values.extend(typing.get_args(current))
            continue
        if isinstance(current, type):
            continue
        stack.extend(typing.get_args(current))
    return values


_CONSTRAINT_ATTRS: tuple[tuple[str, str], ...] = (
    ("gt", "> {}"),
    ("ge", ">= {}"),
    ("lt", "< {}"),
    ("le", "<= {}"),
    ("multiple_of", "multiple of {}"),
    ("min_length", "length >= {}"),
    ("max_length", "length <= {}"),
    ("pattern", "pattern `{}`"),
    ("max_digits", "max digits {}"),
    ("decimal_places", "decimal places {}"),
    ("strict", "strict"),
)


def _metadata_items(field: FieldInfo) -> list[object]:
    items = list(field.metadata)
    stack = [field.annotation]
    while stack:
        current = stack.pop(0)
        if typing.get_origin(current) is typing.Annotated:
            items.extend(current.__metadata__)
            stack.append(typing.get_args(current)[0])
        elif typing.get_origin(current) in (typing.Union, types.UnionType):
            stack.extend(typing.get_args(current))
    return items


def field_constraints(field: FieldInfo) -> list[str]:
    rendered: list[str] = []
    for item in _metadata_items(field):
        for attr, template in _CONSTRAINT_ATTRS:
            value = getattr(item, attr, None)
            if value is None or value is False:
                continue
            text = template.format(value if attr == "pattern" else _json(value)) if attr != "strict" else "strict"
            if text not in rendered:
                rendered.append(text)
    return rendered


_PARAM_LINE = re.compile(r"^:param\s+([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$")


def param_docs(model: type) -> dict[str, str]:
    """``:param name:`` entries from the model's own and its bases' docstrings."""
    found: dict[str, str] = {}
    for base in model.__mro__:
        if not _in_scope(base):
            continue
        doc = _own_doc(base)
        if not doc:
            continue
        lines = doc.splitlines()
        index = 0
        while index < len(lines):
            match = _PARAM_LINE.match(lines[index].strip())
            if not match:
                index += 1
                continue
            name, text = match.groups()
            parts = [text]
            index += 1
            while index < len(lines) and lines[index].startswith((" ", "\t")) and not lines[index].strip().startswith(":"):
                parts.append(lines[index].strip())
                index += 1
            found.setdefault(name, _one_line(" ".join(parts)))
    return found


def _default_text(field: FieldInfo) -> str:
    if field.is_required():
        return ""
    if field.default_factory is not None:
        try:
            value = field.default_factory()  # type: ignore[call-arg]
        except TypeError:
            return "computed"
        return _json(value)
    if field.default is PydanticUndefined:
        return ""
    return _json(field.default)


@dataclasses.dataclass(frozen=True)
class FieldRow:
    wire_name: str
    type_text: str
    required: bool
    default: str
    unit: str
    values: str
    description: str


def field_rows(model: type[BaseModel], names: ModelNames) -> list[FieldRow]:
    docs = param_docs(model)
    rows: list[FieldRow] = []
    for name, field in model.model_fields.items():
        enums = _enums_in(field.annotation)
        literals = _literal_values(field.annotation)
        values_parts = [enum_values_text(e) for e in enums]
        if literals:
            values_parts.append(", ".join(f"`{to_jsonable_python(v)}`" for v in literals))
        values_parts.extend(field_constraints(field))
        rows.append(
            FieldRow(
                wire_name=field.alias or name,
                type_text=format_type(field.annotation, names),
                required=field.is_required(),
                default=_default_text(field),
                unit=_unit_for(name),
                values="; ".join(values_parts),
                description=_one_line(field.description or docs.get(name, "")),
            )
        )
    return rows


def render_field_table(model: type[BaseModel], names: ModelNames) -> list[str]:
    rows = field_rows(model, names)
    extra = model.model_config.get("extra")
    unknown = {
        "forbid": "Unknown keys are refused.",
        "allow": "Unknown keys are accepted and kept.",
    }.get(str(extra), "Unknown keys are silently ignored.")
    if model.model_config.get("allow_inf_nan") is False:
        unknown += " Non-finite numbers (NaN, Infinity) are refused."
    lines = [
        unknown,
        "",
        "| Field | Type | Req | Default | Unit | Values / constraints | Description |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{row.wire_name}`",
                    _cell(row.type_text),
                    "yes" if row.required else "no",
                    _cell(f"`{row.default}`" if row.default else ""),
                    _cell(row.unit),
                    _cell(row.values),
                    _cell(row.description),
                ]
            )
            + " |"
        )
    if not rows:
        lines.append("| (no fields) | | | | | | |")
    return lines


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


def _function_tree(func: Callable[..., object]) -> ast.AST | None:
    try:
        return ast.parse(textwrap.dedent(inspect.getsource(func)))
    except (OSError, TypeError, SyntaxError, IndentationError):
        return None


def raise_messages(func: Callable[..., object]) -> list[str]:
    """The literal messages of the ``raise`` statements in ``func``'s own body."""
    tree = _function_tree(func)
    if tree is None:
        return []
    messages: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Raise) or not isinstance(node.exc, ast.Call):
            continue
        for arg in [*node.exc.args, *(kw.value for kw in node.exc.keywords if kw.arg in {"message", "detail", "msg"})]:
            text = _render_str_expr(arg)
            if text is None:
                continue
            match = _CODE_PREFIX.match(text)
            text = text[match.end():] if match else text
            if len(text.split()) >= 3 and _one_line(text) not in messages:
                messages.append(_one_line(text))
                break
    return messages


def helper_summaries(func: Callable[..., object], fields: tuple[str, ...]) -> list[str]:
    """Describe a validator by the documented functions its body calls.

    ``self.note = normalize_optional_text(self.note)`` is fully described by
    the helper's own docstring plus the attribute it is applied to, so a
    validator that only delegates needs no docstring of its own: the text
    printed is the helper's, read from the helper.
    """
    tree = _function_tree(func)
    if tree is None:
        return []
    applied: dict[str, list[str]] = {}
    order: list[Callable[..., object]] = []

    def helper_of(call: ast.Call) -> Callable[..., object] | None:
        target = call.func
        if isinstance(target, ast.Name):
            found = func.__globals__.get(target.id)
            if isinstance(found, types.FunctionType) and _in_scope(found):
                return inspect.unwrap(found)
        return None

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            helper = helper_of(node.value)
            if helper is None:
                continue
            for target in node.targets:
                if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "self":
                    applied.setdefault(_key(helper), []).append(target.attr)
            if helper not in order:
                order.append(helper)
        elif isinstance(node, ast.Call):
            helper = helper_of(node)
            if helper is not None and helper not in order:
                order.append(helper)
    lines: list[str] = []
    for helper in order:
        summary = _first_paragraph(_own_doc(helper)) or "; ".join(raise_messages(helper))
        if not summary:
            continue
        targets = applied.get(_key(helper)) or list(fields)
        where = f" to {', '.join(f'`{name}`' for name in targets)}" if targets else ""
        lines.append(f"applies `{helper.__name__}`{where}: {summary}")
    return lines


#: Exception types that reach a producer as a refusal: what Pydantic turns into
#: a 422 inside a validator (``ValueError``, ``AssertionError``,
#: ``PydanticCustomError``) and what the API's handlers turn into a 4xx.
def _refusal_types() -> tuple[type[BaseException], ...]:
    from fastapi import HTTPException
    from pydantic_core import PydanticCustomError

    from app.api.errors import DataIntegrityError, DomainError, NotFoundError

    return (ValueError, AssertionError, PydanticCustomError, HTTPException, DomainError, NotFoundError, DataIntegrityError)


#: How far :func:`refusal_site` follows calls. Deep enough for every chain in
#: the payload models today (the deepest measured is 4); bounded so a cycle or
#: a sprawling service graph cannot make generation unbounded.
REFUSAL_SEARCH_DEPTH = 8


def _is_refusal_class(obj: object) -> bool:
    return isinstance(obj, type) and issubclass(obj, _refusal_types())


def _raises_a_refusal(func: Callable[..., object], tracer: Tracer) -> bool:
    """Whether ``func``'s own body raises (or asserts) something that refuses.

    ``raise X(...)`` counts when ``X`` resolves to a refusal class, and
    ``raise make_error(...)`` when ``make_error``'s return annotation does. A
    bare re-raise, or raising a local variable, is not counted: nothing here
    can say what it holds, and "can refuse" is printed only when it is shown.
    """
    tree = _function_tree(func)
    if tree is None:
        return False
    owner = _owner_class(func)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            return True
        if isinstance(node, ast.Call):
            # Validating input against a model refuses whatever the model
            # refuses: ``Model(...)`` or ``Model.model_validate(...)``.
            callee = node.func
            base = callee.value if isinstance(callee, ast.Attribute) else callee
            model = tracer._resolve(base, func, owner)
            validates = isinstance(callee, ast.Name) or (
                isinstance(callee, ast.Attribute) and callee.attr.startswith("model_validate")
            )
            if validates and isinstance(model, type) and issubclass(model, BaseModel) and model.model_fields:
                return True
            continue
        if not isinstance(node, ast.Raise) or node.exc is None:
            continue
        exc = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
        target = tracer._resolve(exc, func, owner)
        if _is_refusal_class(target):
            return True
        if isinstance(target, types.FunctionType):
            try:
                returned = typing.get_type_hints(target).get("return")
            except Exception:
                returned = None
            if _is_refusal_class(returned):
                return True
    return False


def _called(func: Callable[..., object], tracer: Tracer) -> list[Callable[..., object]]:
    """Functions ``func`` *calls* (not merely names), in this repository's code."""
    tree = _function_tree(func)
    if tree is None:
        return []
    owner = _owner_class(func)
    found: list[Callable[..., object]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = tracer._resolve(node.func, func, owner)
        if target is _MISSING:
            continue
        facts = _Facts(callees=[], codes=set(), tables=set())
        tracer._classify_target(target, facts)
        found.extend(facts.callees)
    return found


def refusal_site(func: Callable[..., object], tracer: Tracer | None = None) -> str | None:
    """The first function on a call path from ``func`` that raises a refusal.

    Breadth-first through calls into ``app``/``tckdb_schemas`` code, to
    :data:`REFUSAL_SEARCH_DEPTH`, with a visited set. ``None`` means no such
    path was *found*, which is not a proof that none exists (a call through a
    variable is invisible here) -- so the contract prints "can refuse" for a
    hit and says nothing for a miss.
    """
    tracer = tracer or Tracer(frozenset())
    frontier = [inspect.unwrap(func)]
    seen: set[str] = set()
    for _ in range(REFUSAL_SEARCH_DEPTH + 1):
        following: list[Callable[..., object]] = []
        for current in frontier:
            key = _key(current)
            if key in seen:
                continue
            seen.add(key)
            if _raises_a_refusal(current, tracer):
                return key
            following.extend(_called(current, tracer))
        if not following:
            return None
        frontier = following
    return None


@dataclasses.dataclass(frozen=True)
class ValidatorRule:
    model: type[BaseModel]
    name: str
    kind: str
    fields: tuple[str, ...]
    refusal_site: str | None
    text: str
    text_source: str
    func: Callable[..., object]


_RULE_TRACER = Tracer(frozenset())


class UndocumentedRule(Exception):
    """A validator with neither a docstring nor a literal refusal message."""


def validator_rules(model: type[BaseModel]) -> list[ValidatorRule]:
    decorators = model.__pydantic_decorators__
    rules: list[ValidatorRule] = []
    groups = (("model", decorators.model_validators), ("field", decorators.field_validators))
    for kind, group in groups:
        for name, dec in group.items():
            func = inspect.unwrap(getattr(dec.func, "__func__", dec.func))
            fields = tuple(getattr(dec.info, "fields", ()) or ())
            doc = _own_doc(func)
            messages = [] if doc else raise_messages(func)
            helpers = [] if doc or messages else helper_summaries(func, fields)
            if doc:
                text, source = doc, "docstring"
            elif messages:
                text = "\n".join(f"- {message}" for message in messages)
                source = "refusal messages"
            elif helpers:
                text = "\n".join(f"- {line}" for line in helpers)
                source = "documented helpers it calls"
            else:
                raise UndocumentedRule(f"{func.__module__}:{func.__qualname__}")
            rules.append(
                ValidatorRule(
                    model=model,
                    name=name,
                    kind=f"{kind}, {getattr(dec.info, 'mode', 'after')}",
                    fields=fields,
                    refusal_site=refusal_site(func, _RULE_TRACER),
                    text=text,
                    text_source=source,
                    func=func,
                )
            )
    return rules


def _indent_block(text: str, prefix: str = "  ") -> list[str]:
    return [prefix + line if line.strip() else "" for line in text.splitlines()]


def render_validator(rule: ValidatorRule, names: ModelNames) -> list[str]:
    """One rule: a bold header, then its text.

    The header says whether a refusal was *found* on a call path from the
    validator (:func:`refusal_site`). When none was found it says nothing:
    absence of a found raise is not evidence that the check cannot refuse.
    """
    target = f" on `{', '.join(rule.fields)}`" if rule.fields else ""
    if rule.refusal_site is None:
        effect = ""
    elif rule.refusal_site == _key(rule.func):
        effect = "; can refuse"
    else:
        effect = f"; can refuse via `{rule.refusal_site.split(':', 1)[1]}`"
    head = f"- **{names.display(rule.model)}.{rule.name}** ({rule.kind}{target}{effect}):"
    lines = rule.text.splitlines()
    if len(lines) == 1:
        single = lines[0][2:] if lines[0].startswith("- ") else lines[0]
        return [f"{head} {single}"]
    return [head, "", *_indent_block(rule.text), ""]


# ---------------------------------------------------------------------------
# Codes
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class CodeFacts:
    entries: list[ApiCode]
    check: ScientificCheck | None
    constraint_detail: str | None
    message: str | None

    @property
    def code(self) -> str:
        return self.entries[0].code

    @property
    def statuses(self) -> list[int]:
        return sorted({entry.status for entry in self.entries})

    @property
    def client_facing(self) -> bool:
        return any(entry.is_client_facing for entry in self.entries)


def _module_for_origin(origin: str) -> types.ModuleType | None:
    path = origin
    for prefix in ("backend/", "schemas/python/tckdb-schemas/"):
        if path.startswith(prefix):
            path = path[len(prefix):]
            break
    if not path.endswith(".py"):
        return None
    dotted = path[: -len(".py")].replace("/", ".")
    if dotted.endswith(".__init__"):
        dotted = dotted[: -len(".__init__")]
    return sys.modules.get(dotted)


def _message_near_code(tree: ast.AST, code: str, namespace: dict[str, object]) -> str | None:
    """The first string expression written beside ``code`` in ``tree``."""

    def is_code(node: ast.AST) -> bool:
        if isinstance(node, ast.Constant):
            return node.value == code
        if isinstance(node, ast.Name):
            return namespace.get(node.id) == code
        return False

    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    for node in ast.walk(tree):
        text: str | None = None
        if isinstance(node, (ast.Constant, ast.JoinedStr)):
            rendered = _render_str_expr(node)
            if rendered and rendered.startswith(f"{code}: "):
                text = rendered[len(code) + 2:]
        elif isinstance(node, (ast.Call, ast.Tuple)):
            if isinstance(node, ast.Call):
                # Only a call that builds or raises a refusal: an exception
                # class, an ``*_error`` factory, a ``raise_*``/``reject``
                # helper. A declaration that merely *names* the code
                # (``ScientificCheck(code=..., group="...")``) has strings
                # beside it that are not the message.
                callee = node.func.id if isinstance(node.func, ast.Name) else (
                    node.func.attr if isinstance(node.func, ast.Attribute) else ""
                )
                lowered = callee.lower().lstrip("_")
                if not (lowered.endswith(("error", "exception")) or lowered.startswith(("raise", "reject"))):
                    continue
                elements = list(node.args) + [
                    kw.value for kw in node.keywords if kw.arg in {"code", "message", "detail", "msg", "reason"}
                ]
            else:
                if not isinstance(parents.get(node), ast.Return):
                    continue
                elements = list(node.elts)
            if any(is_code(element) for element in elements):
                for element in elements:
                    if is_code(element):
                        continue
                    rendered = _render_str_expr(element)
                    if rendered and len(rendered.split()) >= 3:
                        text = rendered
                        break
        if text:
            return _one_line(text)
    return None


def code_message(code: str, entries: list[ApiCode], sites: Iterable[Callable[..., object]]) -> str | None:
    """Best-effort: the sentence the raise site writes beside the code."""
    for entry in entries:
        module = _module_for_origin(entry.origin)
        path = REPO_ROOT / entry.origin
        if module is None or not path.exists():
            continue
        text = _message_near_code(ast.parse(path.read_text()), code, vars(module))
        if text:
            return text
    for func in sorted(sites, key=_key):
        tree = _function_tree(func)
        if tree is None:
            continue
        text = _message_near_code(tree, code, func.__globals__)
        if text:
            return text
    return None


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------

PREAMBLE = """\
## Conventions a producer must know

This section is the only hand-written part of this file. Everything after it
is generated from the code that enforces it.

**Identity, provenance, result.** TCKDB separates *what a thing is*
(identity: a species, a reaction, a transition state; deduplicated, so the
same molecule deposited twice resolves to one row), *how a number was
produced* (provenance: software release, level of theory, workflow tool,
literature; append-only), and *the number itself* (result: a calculation's
energy, a thermo record, a rate; append-only, never edited in place). A
deposit describes all three, and TCKDB resolves identity and provenance for
you. See `docs/guides/core_concepts.md`.

**Local keys, not database ids.** Inside one payload, one part names another
by a key you choose (`key`, `calculation_key`, `species_key`,
`source_calculation_key`, ...). Keys are scoped to the request and never
reach the database. The one sanctioned exception is `existing_*_id`
(`existing_calculation_id`, `existing_statmech_id`): an id TCKDB returned to
*you* in an earlier response, used to chain a later deposit onto it. It is
checked for ownership and role like a local key. Anything else shaped like a
database id is not part of the contract; describe the molecule, not the row.

**Units are in the field name.** A quantity with one physical unit is stored
in a column that says so: `h298_kj_mol` is kJ/mol, `s298_j_mol_k` is
J/(mol*K), `temperature_k` is K, `electronic_energy_hartree` is hartree,
`reference_pressure_bar` is bar. Convert before you send; TCKDB never
converts a bare number. Where the unit genuinely varies (an Arrhenius `a`),
a sibling enum field (`a_units`) names it. The Unit column below is read
from those suffixes.

**The enthalpy reference.** Every enthalpy on a thermo record is a standard
enthalpy of formation: formed from the elements in their reference states,
pinned at 298.15 K, with the species' own sensible increment added above
298.15 K. That convention is declared, never assumed:
`enthalpy_reference_kind: "formation_298k"` is required on any thermo block
that carries enthalpy content -- `h298_kj_mol`, a NASA-7 or NASA-9 fit, a
Wilhoit `h0_kj_mol`, or any tabulated point with `h_kj_mol` **or
`g_kj_mol`** -- and refused on a block that carries none. It is the only
accepted value. If your source does not state that convention, do not
declare it; a sensible increment or an absolute quantum-chemistry enthalpy
belongs in a molecular property observation, not in thermo. The generated
thermo sections below carry the enforcing rule verbatim. See
`docs/guides/depositing_a_thermo_record.md`.

**Reference pressure.** State the standard-state pressure your entropy was
computed at, in bar, in `reference_pressure_bar`. It is never defaulted: an
omitted value is stored as "not stated", which makes the record's entropy
incomparable with others. 1 atm is `1.01325` bar, not `1.0`; many
statistical-mechanics codes (ARC and RMG among them) compute entropy at
1 atm. Never write `1.0` because it looks standard.

**Transition-state evidence and statmech.** A saddle point may carry up to
three validation records, at most one per `kind`, every one optional:

- `irc`: the reconstructed path connects the declared reactants and products
  (optionally with participant-to-atom mappings).
- `energy_ordering`: the saddle point lies above both wells. Each compared
  energy is `{participant, energy_kind, energy_hartree, source_calculation_key}`
  where `participant` is `ts`, `reactant:N` or `product:N`, and `energy_kind`
  is `electronic` or `e0` (electronic energy plus zero-point energy). The two
  kinds are different quantities and are never compared with each other, so a
  record that gives both gives a complete set of each. Give every participant
  its own energy, never a pre-summed side: a side is compared by the sum, and
  a total has no single calculation to name. Each `source_calculation_key` must
  name a calculation of the thing the energy is of, and of the right type: an
  `electronic` energy comes from an `sp` or `opt`, an `e0` from a `freq`
  (which carries the zero-point energy); `irc`, `scan` and `path_search`
  report points along a path and are never a source. Energies are absolute,
  finite and not positive, in hartree (zero is allowed: it is exact for the
  bare proton); a positive value is refused as a slip. A
  passing record whose *stated* numbers do not put the saddle point above each
  side is refused. Each stated energy is also held against what TCKDB stores
  for the calculation it cites, within the printed-precision tolerance
  `max(1e-6, 5e-7 * n)` hartree: an `electronic` energy against the cited
  `sp`'s electronic energy (or the cited `opt`'s final energy), `n` = 2; an
  `e0` against the stored electronic energy of the same participant's
  `electronic` entry plus the cited `freq`'s zero-point energy, `n` = 3, and
  only when those two calculations are at one geometry. TCKDB stores your
  zero-point energy unscaled, so an `e0` built with a scaled one
  (`E0 = E_electronic + s * ZPE`) must state `zpe_scale_factor` (`s`, positive,
  `e0` entries only); the E0 is then held to `electronic + s * ZPE`, with a
  tolerance that assumes `s` has at least four decimals, so state it exactly as
  multiplied (0.954 for a true 0.953649 can be refused). A contradiction is
  refused with `ts_energy_ordering_stated_energy_mismatch` (`context`: the
  participant, kind, both values, tolerance, and for an E0 the stored ZPE and
  factor). With no factor, an `e0` equal to `electronic + ZPE` agrees; any
  other is **not refused** (a scaled ZPE cannot be told from a wrong number)
  but stored as `not_compared`, reason `zpe_scaling_unstated`. An energy that
  cannot be compared at all (a stored energy or ZPE not stated, no electronic
  entry to pair an E0 with, geometries that cannot be paired) is accepted,
  stored as `not_compared` with a reason, and reported with a
  `transition_state_energy_ordering_not_compared` warning, never read as
  agreement. Taking one
  `energy_kind` from calculations at more than one level of theory is accepted
  with a `transition_state_energy_ordering_mixed_levels` warning. It is
  accepted on the computed-reaction and pressure-dependent bundles; the
  standalone transition-state upload refuses it, because it carries no
  calculations for the wells.
- `imaginary_mode`: what the frequency calculation found. `imaginary_frequency_count`,
  `imaginary_frequency_cm1` (negative) and `mode_displacement_agrees`, your
  normal-mode-displacement verdict, all optional; a record that passes with a
  count of zero is refused, and so is a count or frequency that disagrees with
  the frequency result it cites (1 cm^-1 tolerance). More than one imaginary
  mode is accepted on a pass only when that frequency result designates the
  reaction coordinate. `source_calculation_key` names a `freq` calculation
  of the saddle point, or is omitted on the standalone upload, which binds to
  its single `freq` calculation.

Only a passing `irc` record silences the
`transition_state_missing_irc_evidence` warning: the other two kinds are true of
a saddle point that connects some other pair of minima. On the computed-reaction
bundle a transition state may also carry a `statmech` block, the same block a
species carries, whose `source_calculations` and torsion scans must be the
saddle point's own calculations. The standalone transition-state upload accepts
`scan` calculations (with `scan_result`), `applied_energy_corrections`, and an
`atom_map`; the map counts into a `geometry` given on each participant (named
by the participant's `key`) and names the saddle-point geometry by the request's
`geometry_key`.

**Not stated is not false.** An IRC result's `direction`, `has_forward` and
`has_reverse`, and an imaginary mode's `mode_displacement_agrees`, are each
optional. Leave one out when your source does not say; it is stored as null and
read back as null. Writing `false` is a claim ("there is no such branch", "the
displacement disagrees"), and TCKDB keeps it apart from not knowing.

**Idempotency keys.** Send an `Idempotency-Key` header on every deposit you
might retry. A retry with the same key and the same body replays the stored
response instead of depositing twice; the same key with a different body is
refused. Some routes require the header -- the route tables below say which.
See `docs/specs/upload-idempotency-key-spec.md`.

**Refuse, don't guess.** TCKDB refuses a payload it cannot interpret rather
than filling a plausible default, and a producer should do the same: if your
source does not say which convention a number follows, leave the field out
(or refuse to build the block) rather than guessing. A refusal comes back as
an error body `{"code": ..., "detail": ..., "context": {...}}`. Branch on
`code`, never on the prose in `detail`. The HTTP status says what happened
to your write:

| Status | Meaning | What to do |
|---|---|---|
| 422 | The payload was refused before anything was written. | Fix the payload and resend; the same idempotency key may be reused. |
| 409 | The write reached the database and a stored rule or an idempotency record refused it. | Read `code` and `context`; do not resend unchanged. |
| 401 / 403 | Missing or insufficient credentials. | Authenticate (API key header) or use an account with the role the route names. |
| 404 | A record the payload or path names does not exist (or is not yours). | Check the reference. |
| 426 | Your `tckdb-client` is older than the server accepts. | Upgrade the client. |
| 429 | Rate limited. | Wait and retry the identical request. |
| 5xx | The server or a store failed; your request may be fine. | Retry with backoff unless the code is marked as never succeeding on replay. |

Further reading: `backend/docs/specs/ingestion_submission_model.md`
(submissions), `docs/contribution-bundles/v0-format.md` (bundles),
`docs/adr/0008-validation-tiers-definitions-block-expectations-warn.md`
(why some checks refuse and others only warn),
`docs/guides/scientific_check_register.md` (the chemistry positions TCKDB
enforces), `docs/guides/api_vocabulary.md` (every code and token).
"""


@dataclasses.dataclass
class Surface:
    model: type[BaseModel]
    routes: list[RouteInfo]
    closure: list[type[BaseModel]]
    handler_trace: Trace
    dependency_trace: Trace
    payload_trace: Trace
    workflow_rules: list[tuple[str, Callable[..., object], ScientificCheck | None]]
    rules_by_route: dict[str, set[str]]

    @property
    def title(self) -> str:
        return self.model.__name__

    @property
    def anchor(self) -> str:
        return _anchor("s", self.model.__name__)

    @property
    def schema_file(self) -> str:
        return f"{SCHEMA_SUBDIR}/{self.model.__name__}{SCHEMA_SUFFIX}"


def _route_sort_key(info: RouteInfo) -> tuple[int, str]:
    # The upload routes first, then everything else by path; within a
    # surface, the synchronous route before its queued /jobs/ twin.
    if info.path.startswith("/api/v1/uploads/"):
        rank = 0
    elif info.path.startswith("/api/v1/jobs/"):
        rank = 2
    else:
        rank = 1
    return (rank, info.path)


def _package_version() -> str:
    match = re.search(r'^version\s*=\s*"([^"]+)"', PYPROJECT.read_text(), flags=re.MULTILINE)
    if match is None:
        raise SystemExit(f"{PYPROJECT} declares no version")
    return match.group(1)


@dataclasses.dataclass(frozen=True)
class ChangelogEntry:
    heading: str
    version: str
    body: str


def changelog_entries(text: str | None = None) -> list[ChangelogEntry]:
    text = CHANGELOG.read_text() if text is None else text
    entries: list[ChangelogEntry] = []
    for block in re.split(r"(?m)^## ", text)[1:]:
        heading, _, body = block.partition("\n")
        version = heading.split()[0] if heading.split() else ""
        entries.append(ChangelogEntry(heading.strip(), version, body.strip()))
    return entries


def _examples(model: type[BaseModel]) -> list[dict[str, Any]]:
    extra = model.model_config.get("json_schema_extra")
    if isinstance(extra, dict):
        examples = extra.get("examples")
        if isinstance(examples, list):
            return [example for example in examples if isinstance(example, dict)]
    return []


class ExampleError(Exception):
    pass


class ChangelogError(Exception):
    """The package version has no CHANGELOG entry."""


#: How many refusals a surface's "Will be refused if" list prints.
TOP_REFUSALS = 8

#: A field description that says the field is never defaulted is surfaced in
#: the "At a glance" table: omitting such a field records "not stated".
_NEVER_DEFAULTED = re.compile(r"\bnever defaulted\b", re.IGNORECASE)

#: A route docstring paragraph opening with one of these is written for the
#: people maintaining the route, not for a producer, and is not quoted.
_INTERNAL_PARAGRAPH = re.compile(r"^(notes? for (future )?maintainers|implementation notes?|internal)\b", re.IGNORECASE)


def producer_facing_doc(doc: str | None) -> str | None:
    """``doc`` up to its first paragraph addressed to maintainers."""
    if not doc:
        return None
    kept: list[str] = []
    for paragraph in doc.split("\n\n"):
        if _INTERNAL_PARAGRAPH.match(paragraph.strip()):
            break
        kept.append(paragraph)
    return "\n\n".join(kept).strip() or None


def _first_sentence(text: str) -> str:
    text = _one_line(text)
    match = re.search(r"(?<=[.!?])\s+(?=[A-Z`'\"(])", text)
    return text[: match.start()] if match else text


#: ``json_schema_extra`` key a model uses to say its example cannot be
#: accepted on its own -- its route names an existing record in the path --
#: and what it needs. The contract then prints the example as "shape only".
EXAMPLE_REQUIRES_KEY = "x-tckdb-example-requires"


def example_requires(model: type[BaseModel]) -> str | None:
    """What the model's example needs before the server can accept it, if anything."""
    extra = model.model_config.get("json_schema_extra")
    if isinstance(extra, dict):
        value = extra.get(EXAMPLE_REQUIRES_KEY)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def checked_example(model: type[BaseModel]) -> dict[str, Any]:
    """The model's first declared example, validated and round-tripped."""
    examples = _examples(model)
    if not examples:
        raise ExampleError(
            f"{model.__module__}.{model.__name__} declares no example. Add a minimal valid payload as "
            "model_config = ConfigDict(json_schema_extra={'examples': [...]}) on the model."
        )
    example = examples[0]
    try:
        instance = model.model_validate(example)
    except Exception as exc:
        raise ExampleError(f"{model.__name__}'s example does not validate: {exc}") from exc
    dumped = instance.model_dump(mode="json", by_alias=True, exclude_unset=True)
    again = model.model_validate(dumped)
    if again.model_dump(mode="json", by_alias=True) != instance.model_dump(mode="json", by_alias=True):
        raise ExampleError(f"{model.__name__}'s example does not round-trip through model_dump/model_validate")
    return example


def json_schema_text(model: type[BaseModel]) -> str:
    schema = model.model_json_schema(mode="validation")
    schema = {"$schema": "https://json-schema.org/draft/2020-12/schema", **schema}
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _compact_json(value: Any, indent: int = 0, width: int = 110) -> str:
    """JSON with every object or list that fits in ``width`` columns written on one line.

    The worked composite payloads are long; one line per input or per level keeps the
    contract readable and small, and the text is still valid JSON.
    """
    flat = json.dumps(value, ensure_ascii=False)
    if len(flat) + indent <= width or not isinstance(value, (dict, list)) or not value:
        return flat
    pad = " " * (indent + 2)
    if isinstance(value, dict):
        items = [f'{pad}{json.dumps(k, ensure_ascii=False)}: {_compact_json(v, indent + 2, width)}' for k, v in value.items()]
        return "{\n" + ",\n".join(items) + "\n" + " " * indent + "}"
    items = [f"{pad}{_compact_json(v, indent + 2, width)}" for v in value]
    return "[\n" + ",\n".join(items) + "\n" + " " * indent + "]"


class ContractBuilder:
    """Collects every fact once, then renders the markdown and the schemas."""

    def __init__(self, app: Any | None = None, routes: list[RouteInfo] | None = None) -> None:
        self.app = app or create_app()
        self.routes = routes if routes is not None else discover_routes(self.app)
        self.catalogue: dict[str, list[ApiCode]] = {}
        for entry in CATALOGUE:
            self.catalogue.setdefault(entry.code, []).append(entry)
        self.tracer = Tracer(frozenset(self.catalogue))
        self.register = register()
        self.check_by_func: dict[str, list[ScientificCheck]] = {}
        self.check_by_code: dict[str, ScientificCheck] = {}
        self.constraints_by_table: dict[str, list[DatabaseConstraint]] = {}
        for check in self.register:
            for code in check.codes:
                self.check_by_code.setdefault(code, check)
            for site in check.enforced_by:
                if isinstance(site, PythonCheck):
                    funcs = self.check_by_func.setdefault(_key(inspect.unwrap(site.func)), [])
                    if check not in funcs:
                        funcs.append(check)
                elif isinstance(site, DatabaseConstraint) and site.rejection_code:
                    self.constraints_by_table.setdefault(site.table, []).append(site)
                    self.check_by_code.setdefault(site.rejection_code, check)
        self.producer_routes = sorted(
            (info for info in self.routes if info.category == "producer"), key=_route_sort_key
        )
        self.global_trace = self._global_trace()
        self.surfaces = self._surfaces()
        all_models: list[type[BaseModel]] = []
        for surface in self.surfaces:
            for model in surface.closure:
                if model not in all_models:
                    all_models.append(model)
        self.models = sorted(all_models, key=lambda m: (m.__name__, m.__module__))
        self.names = ModelNames(self.models)

    # -- tracing ------------------------------------------------------------

    def _global_trace(self) -> Trace:
        roots: list[Callable[..., object]] = []
        for handler in self.app.exception_handlers.values():
            if isinstance(handler, types.FunctionType):
                roots.append(handler)
        for middleware in self.app.user_middleware:
            cls = getattr(middleware, "cls", None)
            if isinstance(cls, type) and _in_scope(cls):
                for name in ("__init__", "__call__", "dispatch"):
                    member = cls.__dict__.get(name)
                    if isinstance(member, types.FunctionType):
                        roots.append(member)
        return self.tracer.trace(roots)

    def _surfaces(self) -> list[Surface]:
        grouped: dict[object, list[RouteInfo]] = {}
        for info in self.producer_routes:
            for body in info.body_models:
                grouped.setdefault(body, []).append(info)
        surfaces: list[Surface] = []
        for model, infos in grouped.items():
            if not (isinstance(model, type) and issubclass(model, BaseModel)):
                raise SystemExit(f"producer route body {model!r} is not a Pydantic model; cannot document it")
            closure = model_closure(model)
            handler_trace, dependency_trace = Trace(), Trace()
            rules_by_route: dict[str, set[str]] = {}
            for info in infos:
                handler = self.tracer.trace([info.route.endpoint])
                handler_trace.merge(handler)
                dependency_trace.merge(self.tracer.trace(info.dependencies))
                rules_by_route[info.label] = set(handler.functions)
            validator_funcs: list[Callable[..., object]] = []
            for sub in closure:
                validator_funcs.extend(_pydantic_validator_funcs(sub))
            payload_trace = self.tracer.trace(validator_funcs)
            workflow_rules: list[tuple[str, Callable[..., object], ScientificCheck | None]] = []
            for key in sorted(handler_trace.functions):
                func = handler_trace.functions[key]
                if key in payload_trace.functions:
                    continue
                checks = self.check_by_func.get(key, [])
                if is_producer_rule(func):
                    # Printed once in full, whatever number of checks it also carries.
                    workflow_rules.append((key, func, checks[-1] if checks else None))
                else:
                    # One entry per check: a function may enforce several (the TS evidence seam
                    # enforces five), and a single-valued map kept only the last one registered.
                    workflow_rules.extend((key, func, check) for check in checks)
            surfaces.append(
                Surface(
                    model=model,
                    routes=sorted(infos, key=_route_sort_key),
                    closure=closure,
                    handler_trace=handler_trace,
                    dependency_trace=dependency_trace,
                    payload_trace=payload_trace,
                    workflow_rules=workflow_rules,
                    rules_by_route=rules_by_route,
                )
            )
        surfaces.sort(key=lambda s: _route_sort_key(s.routes[0]))
        names = [s.model.__name__ for s in surfaces]
        duplicates = sorted({n for n in names if names.count(n) > 1})
        if duplicates:
            raise SystemExit(f"two upload payload models share a name {duplicates}; schema file names would collide")
        return surfaces

    # -- codes --------------------------------------------------------------

    def surface_codes(self, surface: Surface) -> dict[str, list[str]]:
        """code -> how it was traced, for one surface."""
        traced: dict[str, list[str]] = {}
        for label, trace in (
            ("payload validation", surface.payload_trace),
            ("route handler", surface.handler_trace),
            ("route dependency", surface.dependency_trace),
        ):
            for code in trace.code_sites:
                traced.setdefault(code, []).append(label)
        for table in sorted(surface.handler_trace.tables):
            for site in self.constraints_by_table.get(table, []):
                code = site.rejection_code
                assert code is not None
                traced.setdefault(code, []).append(f"heuristic: the handler names table `{table}`, constraint `{site.name}`")
        return traced

    def code_facts(self, code: str) -> CodeFacts:
        entries = self.catalogue[code]
        sites: list[Callable[..., object]] = []
        for trace in self._all_traces():
            for key in trace.code_sites.get(code, ()):
                func = trace.functions.get(key)
                if func is not None and func not in sites:
                    sites.append(func)
        constraint_detail = None
        for constraints in self.constraints_by_table.values():
            for site in constraints:
                if site.rejection_code == code and site.rejection_detail:
                    constraint_detail = site.rejection_detail
        message = constraint_detail or code_message(code, entries, sites)
        return CodeFacts(entries=entries, check=self.check_by_code.get(code), constraint_detail=constraint_detail, message=message)

    def _all_traces(self) -> list[Trace]:
        traces = [self.global_trace]
        for surface in self.surfaces:
            traces.extend([surface.payload_trace, surface.handler_trace, surface.dependency_trace])
        return traces

    def traced_codes(self) -> dict[str, list[str]]:
        """code -> surfaces that trace it."""
        found: dict[str, list[str]] = {}
        for surface in self.surfaces:
            for code in self.surface_codes(surface):
                found.setdefault(code, []).append(surface.title)
        return found

    # -- ranking and grouping ---------------------------------------------------

    def _surfaces_by_code(self) -> dict[str, list[str]]:
        if not hasattr(self, "_by_code_cache"):
            self._by_code_cache = self.traced_codes()
        return self._by_code_cache

    #: A refusal code traced on at least this many surfaces is printed once, in a shared
    #: table, instead of in every surface's own table: ten surfaces carry a level of theory
    #: and a calculation, so every rule about those repeated verbatim ten times.
    SHARED_CODE_MIN_SURFACES = 8

    def widely_shared_codes(self) -> dict[str, int]:
        """code -> number of surfaces that can return it, for codes on enough surfaces to print once."""
        common = set(self.global_trace.code_sites)
        return {
            code: len(titles)
            for code, titles in self._surfaces_by_code().items()
            if code not in common and len(titles) >= self.SHARED_CODE_MIN_SURFACES
        }

    def shared_checks(self) -> dict[str, tuple[list[ScientificCheck], list[str]]]:
        """Functions two or more surfaces' workflows reach, with every register check each enforces: printed once.

        Keyed by the function, because a function may enforce several checks (the transition-state
        evidence seam enforces four) and all of them are reached whenever it is.
        """
        reached: dict[str, list[str]] = {}
        checks: dict[str, list[ScientificCheck]] = {}
        for surface in self.surfaces:
            for key, func, check in surface.workflow_rules:
                if check is None or is_producer_rule(func):
                    continue
                if surface.title not in reached.setdefault(key, []):
                    reached[key].append(surface.title)
                if check not in checks.setdefault(key, []):
                    checks[key].append(check)
        return {key: (checks[key], titles) for key, titles in sorted(reached.items()) if len(titles) > 1}

    def top_refusals(self, surface: Surface, limit: int = TOP_REFUSALS) -> list[str]:
        """The surface's most specific client-facing refusals, most specific first.

        Ranked, in order: codes raised by a ``@producer_rule`` the workflow
        reaches; codes of scientific checks it reaches; codes raised by the
        root model's own validators; then every other traced code. Within a
        rank, a code traced on fewer surfaces comes first -- it says more
        about *this* payload -- then alphabetical. Codes every request can
        receive are left out, and codes only a route dependency raises (the
        client-version check) rank last.
        """
        traced = self.surface_codes(surface)
        common = set(self.global_trace.code_sites)
        by_code = self._surfaces_by_code()
        producer_keys = {key for key, func, _ in surface.workflow_rules if is_producer_rule(func)}
        check_codes = {code for _, _, check in surface.workflow_rules if check is not None for code in check.codes}
        root_prefix = f"{surface.model.__module__}:{surface.model.__qualname__}."

        def rank(code: str) -> tuple[int, int, str]:
            sites = surface.handler_trace.code_sites.get(code, set())
            payload_sites = surface.payload_trace.code_sites.get(code, set())
            if sites & producer_keys:
                tier = 0
            elif code in check_codes:
                tier = 1
            elif any(site.startswith(root_prefix) for site in payload_sites):
                tier = 2
            elif sites or payload_sites:
                tier = 3
            else:
                # Only a route dependency (client version, idempotency) raises it.
                tier = 4
            return (tier, len(by_code.get(code, [])), code)

        candidates = [
            code
            for code in traced
            if code not in common and any(entry.is_client_facing for entry in self.catalogue[code])
        ]
        return sorted(candidates, key=rank)[:limit]

    def _refusal_line(self, code: str) -> str:
        facts = self.code_facts(code)
        statuses = "/".join(str(s) for s in facts.statuses)
        if facts.message:
            said = _first_sentence(facts.message)
        elif facts.check is not None:
            said = _first_sentence(facts.check.asserts)
        else:
            said = "see the code reference"
        return f"- [`{code}`](#{_anchor('c', code)}) ({statuses}): {said}"

    # -- rendering ----------------------------------------------------------

    def render_markdown(self) -> str:
        version = _package_version()
        entries = changelog_entries()
        if not entries or entries[0].version != version:
            newest = entries[0].version if entries else "none"
            raise ChangelogError(
                f"tckdb-schemas is version {version} but the newest CHANGELOG.md entry is {newest}. "
                f"Add a '## {version} - <date>' entry to {CHANGELOG.relative_to(REPO_ROOT)} saying what a "
                "producer now sees differently, then regenerate."
            )
        out: list[str] = [
            "# TCKDB producer contract",
            "",
            "<!-- GENERATED FILE. Do not edit. Regenerate with:",
            f"     {REGENERATE}",
            "     and verify with --check. -->",
            "",
            f"Generated by `{GENERATOR}` from the live API routes, the upload models, the",
            "refusal-code catalogue and the scientific check register. **tckdb-schemas version"
            f" `{version}`** ({len(self.producer_routes)} producer routes, {len(self.surfaces)} surfaces,"
            f" {len(self.models)} models). No source commit is stamped: one version names one contract.",
            "",
            "Read it in pieces: `python -m tckdb_schemas.contract --print` for all of it,"
            " `--since <version>` for what changed, `--schemas` for the JSON Schema files.",
            "",
        ]
        out += self._render_glance()
        out += [
            "## Contents",
            "",
            "- [Conventions a producer must know](#conventions-a-producer-must-know)",
            "- [What changed](#what-changed)",
            "- [Every producer route](#every-producer-route)",
            "- [Checks several surfaces apply](#checks-several-surfaces-apply)",
            "- [Worked payloads: user-built composite energies](#worked-payloads-user-built-composite-energies)",
            *[f"- [Surface `{surface.title}`](#{surface.anchor})" for surface in self.surfaces],
            "- [Model reference](#model-reference)",
            "- [Enums](#enums)",
            "- [Refusal code reference](#refusal-code-reference)",
            "- [Write routes that are not producer surfaces](#write-routes-that-are-not-producer-surfaces)",
            "",
            PREAMBLE,
        ]
        out += self._render_changes(entries)
        out += self._render_common()
        out += self._render_shared_checks()
        out += self._render_composite_worked_payloads()
        for surface in self.surfaces:
            out += self._render_surface(surface)
        out += self._render_model_reference()
        out += self._render_enums()
        out += self._render_code_reference()
        out += self._render_route_classification()
        text = "\n".join(out)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.rstrip("\n") + "\n"

    def _render_glance(self) -> list[str]:
        out = [
            "## At a glance",
            "",
            "| Surface | Endpoints | Key rules | Most specific refusals |",
            "|---|---|---|---|",
        ]
        for surface in self.surfaces:
            endpoints = "<br>".join(f"`{info.label}`" for info in surface.routes)
            rules = [
                _first_paragraph(_own_doc(func))
                for _, func, _ in surface.workflow_rules
                if is_producer_rule(func)
            ]
            never = [
                f"`{row.wire_name}`"
                for row in field_rows(surface.model, self.names)
                if _NEVER_DEFAULTED.search(row.description)
            ]
            if never:
                rules.append("Never defaulted: " + ", ".join(never) + ".")
            refusals = ", ".join(f"`{code}`" for code in self.top_refusals(surface, 3)) or "none traced"
            out.append(
                f"| [`{surface.title}`](#{surface.anchor}) | {endpoints} | "
                f"{_cell(' '.join(rules)) or '-'} | {refusals} |"
            )
        out.append("")
        return out

    def _render_changes(self, entries: list[ChangelogEntry]) -> list[str]:
        out = [
            "## What changed",
            "",
            "Every entry of `schemas/python/tckdb-schemas/CHANGELOG.md`, newest first,"
            " copied verbatim. `python -m tckdb_schemas.contract --since <version>` prints"
            " only the entries newer than the version your adapter targets.",
            "",
        ]
        for entry in entries:
            # A heading inside an entry is demoted below the entry's own
            # level, so it can never end the section it is quoted in.
            body = re.sub(r"(?m)^(#+) ", lambda m: "###" + m.group(1) + " ", entry.body)
            out += [f"### {entry.heading}", "", body, ""]
        return out

    def _render_common(self) -> list[str]:
        roles = ", ".join(f"`{role.value}`" for role in AppUserRole)
        out = [
            "## Every producer route",
            "",
            f"Every producer route requires an authenticated caller (`{AUTHENTICATED}`) and"
            f" accepts any account role ({roles}); none is role-gated. Headers:",
            "",
            "| Header | Required | Declared by | What the declaring dependency says |",
            "|---|---|---|---|",
        ]
        headers: dict[str, HeaderParam] = {}
        required_on: dict[str, list[str]] = {}
        for info in self.producer_routes:
            for header in _header_params(info.route):
                headers.setdefault(header.name, header)
                if header.required:
                    required_on.setdefault(header.name, []).append(info.label)
        for name in sorted(headers, key=str.lower):
            header = headers[name]
            required = "on " + ", ".join(f"`{label}`" for label in required_on[name]) if name in required_on else "no"
            out.append(f"| `{name}` | {_cell(required)} | `{header.source}` | {_cell(header.purpose)} |")
        out += [
            "",
            "Codes any request can receive, traced from the application's exception handlers and middleware:",
            "",
            *[self._refusal_line(code) for code in sorted(self.global_trace.code_sites)],
            "",
        ]
        return out

    def _render_composite_worked_payloads(self) -> list[str]:
        """Two complete composite bundles, validated against the live model before they are printed."""
        from tckdb_schemas.composite_worked_examples import worked_payload_b, worked_payload_c
        from tckdb_schemas.workflows.computed_species_upload import ComputedSpeciesUploadRequest

        out = [
            "## Worked payloads: user-built composite energies",
            "",
            "A composite energy you build yourself (a CCSD(T)/CBS extrapolation, a focal-point sum) is sent as an"
            " **assembled** composite: the other calculations are ordinary single points, and the composite is a"
            " calculation of type `composite` whose `level_of_theory` carries the recipe inline as"
            " `composite_scheme` (send `method` **or** `composite_scheme`, never both) and whose"
            " `composite_result.inputs` name, by bundle-local `key` or by `calc_...` ref, the calculation that fills"
            " each slot. The server names the level of theory itself and never stores a total it computed:"
            " it recomputes `electronic_energy_hartree` from the stored energies of the inputs to **check** yours,"
            " blocking beyond `max(1e-6, 5e-7 * n)` hartree (`composite_total_mismatch`) and warning"
            " (`composite_total_unverifiable`) when an input energy or component is not stated.",
            "",
            "What a scheme says. The total is the sum of its terms in order. A `value` or `base` term reads one"
            " input; an `extrapolation` term applies `formula` (`inverse_power` with `exponent`,"
            " `inverse_power_shifted_half` with `exponent`, `karton_martin_scf`, `exponential_three_point`) to inputs"
            " at declared `cardinal_number`s; a `difference` term is its `high` input minus its `low` input."
            " `energy_component` says which part of the input's energy is read. A `correlation` term means the"
            " whole correlation energy, (T) included: for an input whose stored `correlation` is the CCSD part"
            " with `triples` separate (Molpro), the server reads `correlation + triples`; for one whose"
            " `correlation` already includes (T) (ORCA), `correlation` alone. It decides by which sum equals the"
            " stored energy, and warns rather than guesses when it cannot. Formula, exponent, cardinal numbers,"
            " the input levels (including `core_treatment`) and the order of the terms are the scheme's identity;"
            " the term `key`s and the literature are not. An input level is an ordinary level: a nested composite,"
            " or a named composite method such as CBS-QB3, is refused (`composite_scheme_nested`).",
            "",
        ]
        for title, build, note in (
            (
                "(b) CCSD(T)/CBS from a TZ/QZ pair",
                worked_payload_b,
                "Reference energy at QZ, correlation energy extrapolated over cardinal numbers 3 and 4 with an"
                " inverse-power law of exponent 3. The energies are the water values printed in the ORCA 6.1 manual.",
            ),
            (
                "(c) Focal-point additive",
                worked_payload_c,
                "A QZ base plus a core-valence difference (the same level, `all_electron` against `frozen_core`),"
                " a higher-order difference, a relativistic difference and a DBOC value.",
            ),
        ):
            payload = build()
            ComputedSpeciesUploadRequest.model_validate(payload)
            out += [f"### {title}", "", note, "", "```json", _compact_json(payload), "```", ""]
        return out

    def _render_shared_checks(self) -> list[str]:
        out = [
            "## Checks several surfaces apply",
            "",
            "Scientific checks the workflows of two or more surfaces reach, printed once and"
            " linked from each surface. A marked `@producer_rule` is never moved here: it is"
            " printed in full on every surface that reaches it.",
            "",
        ]
        for key, (func_checks, titles) in self.shared_checks().items():
            applied = "Applied on: " + ", ".join(f"[`{t}`](#{_anchor('s', t)})" for t in titles) + "."
            out += [f'<a id="{_anchor("k", key)}"></a>', "", f"### `{key.split(':', 1)[1]}`", ""]
            if len(func_checks) == 1:
                (check,) = func_checks
                codes = ", ".join(f"[`{code}`](#{_anchor('c', code)})" for code in check.codes) or "none"
                out += [f"`{key}`. {check.tier.value}; codes {codes}. {applied}", "", _one_line(check.asserts), ""]
                if check.escape_hatch:
                    out += [f"If your chemistry is legitimate: {_one_line(check.escape_hatch)}", ""]
                continue
            out += [f"`{key}` enforces {len(func_checks)} checks. {applied}", ""]
            for check in func_checks:
                codes = ", ".join(f"[`{code}`](#{_anchor('c', code)})" for code in check.codes) or "none"
                out += [f"- **{check.tier.value}; codes {codes}.** {_one_line(check.asserts)}"]
                if check.escape_hatch:
                    out += [f"  If your chemistry is legitimate: {_one_line(check.escape_hatch)}"]
            out.append("")
        shared = self.widely_shared_codes()
        total = len(self.surfaces)
        out += [
            '<a id="codes-most-surfaces-share"></a>',
            "",
            "### Codes most surfaces share",
            "",
            f"Refusal codes that {self.SHARED_CODE_MIN_SURFACES} or more of the {total} surfaces can return, printed"
            " here once instead of in each surface's own table. They come from rules every payload that carries a"
            " calculation or a level of theory meets. Each links to its entry in the"
            " [refusal code reference](#refusal-code-reference); `traced` counts the surfaces.",
            "",
            "| Code | Status | Surfaces traced |",
            "|---|---|---|",
        ]
        for code in sorted(shared):
            statuses = ", ".join(str(s) for s in sorted({e.status for e in self.catalogue[code]}))
            out.append(f"| [`{code}`](#{_anchor('c', code)}) | {statuses} | {shared[code]} of {total} |")
        out.append("")
        return out

    def _render_surface(self, surface: Surface) -> list[str]:
        names = self.names
        model = surface.model
        requires = example_requires(model)
        out = [
            f'<a id="{surface.anchor}"></a>',
            "",
            f"## Surface `{surface.title}`",
            "",
            _first_paragraph(_own_doc(model)) or "(the model has no docstring)",
            "",
            f"Payload model `{model.__module__}.{model.__qualname__}`; JSON Schema"
            f" `tckdb_schemas/contract/{surface.schema_file}`.",
            "",
            "### Will be refused if",
            "",
            "The most specific refusals traced for this surface (ranking in the"
            " [code reference](#refusal-code-reference) intro):",
            "",
            *[self._refusal_line(code) for code in self.top_refusals(surface)],
            "",
            "### Routes",
            "",
            "| Method and path | Success | Idempotency-Key | Path parameters | Handler |",
            "|---|---|---|---|---|",
        ]
        for info in surface.routes:
            idem = "not accepted"
            for header in _header_params(info.route):
                if header.name.lower() == "idempotency-key":
                    idem = "required" if header.required else "optional"
            path_params = ", ".join(
                f"`{param.name}` ({format_type(param.field_info.annotation, names)})"
                for param in info.route.dependant.path_params
            ) or "none"
            handler = inspect.unwrap(info.route.endpoint)
            out.append(
                f"| `{info.label}` | {info.status_code} | {idem} | {_cell(path_params)} | "
                f"`{handler.__module__}.{handler.__qualname__}` |"
            )
        out.append("")
        for info in surface.routes:
            doc = producer_facing_doc(_own_doc(inspect.unwrap(info.route.endpoint)))
            if doc:
                out += [f"`{info.label}` says:", "", *[f"> {line}" if line else ">" for line in doc.splitlines()], ""]

        out += ["### Payload fields", ""]
        out += render_field_table(model, names)
        out.append("")
        nested = surface.closure[1:]
        if nested:
            out += [
                f"Nested models ({len(nested)}; fields and rules in the [model reference](#model-reference)):",
                "",
                *[f"- {names.link(sub)}" for sub in nested],
                "",
            ]

        out += ["### Rules the payload model enforces", ""]
        root_rules = validator_rules(model)
        if root_rules:
            for rule in root_rules:
                out += render_validator(rule, names)
        else:
            out += ["The root model declares no validators.", ""]

        shared = self.shared_checks()
        out += [
            "### Rules the workflow applies",
            "",
            "Found by tracing each route's handler through its direct calls: every function"
            " reached that is marked `@producer_rule` (printed in full) or declared in the"
            " scientific check register.",
            "",
        ]
        if not surface.workflow_rules:
            out += ["No marked rule or register check is reached from these handlers.", ""]
        listed_shared: set[str] = set()
        for key, func, check in surface.workflow_rules:
            routes = [label for label, reached in sorted(surface.rules_by_route.items()) if key in reached]
            via = ", ".join(f"`{label}`" for label in routes)
            if is_producer_rule(func):
                out += [f"- **`{key}`** (reached from {via}):", "", *_indent_block(_own_doc(func) or ""), ""]
            elif check is not None and key in shared:
                if key in listed_shared:
                    continue  # one bullet per function, however many checks it enforces
                listed_shared.add(key)
                func_checks = shared[key][0]
                tiers = "/".join(dict.fromkeys(c.tier.value for c in func_checks))
                codes = ", ".join(f"`{code}`" for code in dict.fromkeys(c for ch in func_checks for c in ch.codes)) or "no code"
                out.append(f"- [`{key.split(':', 1)[1]}`](#{_anchor('k', key)}) ({tiers}; {codes})")
            elif check is not None:
                codes = ", ".join(f"`{code}`" for code in check.codes) or "no code"
                out.append(f"- **`{key}`** (reached from {via}; {check.tier.value}; {codes}): {_one_line(check.asserts)}")
        out.append("")

        out += self._render_surface_codes(surface)
        out += self._render_example(surface, requires)
        return out

    def _render_surface_codes(self, surface: Surface) -> list[str]:
        traced = self.surface_codes(surface)
        common = set(self.global_trace.code_sites)
        shared = self.widely_shared_codes()
        specific = {code: how for code, how in traced.items() if code not in common and code not in shared}
        out = [
            "### Refusal codes this surface can return",
            "",
            "Traced statically from the payload validators, route handlers and route dependencies:"
            " reachable from the route, not necessarily for every payload; a code raised through"
            " dynamic dispatch can be missing. Codes every request can receive are listed"
            " [once](#every-producer-route); codes most surfaces can return (calculation, level of"
            " theory and composite rules) are listed [once](#codes-most-surfaces-share).",
            "",
        ]
        if not specific:
            return [*out, "No surface-specific code traced.", ""]
        out += ["| Code | Status | Traced via |", "|---|---|---|"]
        for code in sorted(specific):
            statuses = ", ".join(str(s) for s in sorted({e.status for e in self.catalogue[code]}))
            how = "; ".join(sorted(set(specific[code])))
            out.append(f"| [`{code}`](#{_anchor('c', code)}) | {statuses} | {_cell(how)} |")
        out.append("")
        return out

    def _render_example(self, surface: Surface, requires: str | None) -> list[str]:
        example = checked_example(surface.model)
        if requires:
            head = [
                "### Example (shape only)",
                "",
                f"Validated against `{surface.title}` when this file was generated, but not accepted"
                f" on its own: it needs {requires}.",
            ]
        else:
            head = [
                "### Minimal valid example",
                "",
                f"Validated against `{surface.title}` when this file was generated, and accepted by"
                " every route above in the test suite.",
            ]
        return [*head, "", "```json", json.dumps(example, indent=2, ensure_ascii=False), "```", ""]

    def _render_model_reference(self) -> list[str]:
        names = self.names
        roots = {surface.model: surface for surface in self.surfaces}
        out = [
            "## Model reference",
            "",
            "Every model a producer payload can contain, alphabetically. A payload's root model is"
            " specified in its surface section. Field descriptions come from the field's"
            " `description=` or the model's `:param name:` docstring entry; a blank description"
            " means the source has neither.",
            "",
        ]
        for model in self.models:
            out += [f'<a id="{names.anchor(model)}"></a>', "", f"### `{names.display(model)}`", ""]
            if model in roots:
                out += [f"The root payload of [surface `{roots[model].title}`](#{roots[model].anchor}).", ""]
                continue
            out.append(f"`{model.__module__}`.")
            summary = _first_paragraph(_own_doc(model))
            if summary:
                out += ["", summary]
            out.append("")
            out += render_field_table(model, names)
            out.append("")
            for rule in validator_rules(model):
                out += render_validator(rule, names)
            out.append("")
        return out

    def _render_enums(self) -> list[str]:
        used: dict[str, type[enum.Enum]] = {}
        for model in self.models:
            for field in model.model_fields.values():
                for enum_class in _enums_in(field.annotation):
                    if len(enum_class) > ENUM_INLINE_MAX:
                        used.setdefault(enum_class.__name__, enum_class)
        out = [
            "## Enums",
            "",
            f"Enums with more than {ENUM_INLINE_MAX} values, linked from the fields that take them.",
            "",
        ]
        for name in sorted(used):
            values = ", ".join(f"`{member.value}`" for member in used[name])
            out += [f'<a id="{enum_anchor(used[name])}"></a>', "", f"### `{name}`", "", values, ""]
        return out

    def _render_code_reference(self) -> list[str]:
        traced = self.traced_codes()
        global_codes = set(self.global_trace.code_sites)
        client_facing = sorted({entry.code for entry in CATALOGUE if entry.is_client_facing})
        documented = sorted(set(traced) | global_codes)
        shared = self.shared_checks()
        out = [
            "## Refusal code reference",
            "",
            "Every code a producer route was traced to. `Message` is the sentence written beside the"
            " code at its raise site, found by a static search, with `{placeholders}` for the parts"
            " filled in at run time; where the search finds none it says so. A surface's \"Will be"
            " refused if\" list ranks codes from a `@producer_rule`, then scientific checks, then the"
            " root model's validators, then the rest, the most specific first.",
            "",
        ]
        for code in documented:
            facts = self.code_facts(code)
            entries = facts.entries
            out += [f'<a id="{_anchor("c", code)}"></a>', "", f"#### `{code}`", ""]
            out.append(
                f"- Status: {', '.join(str(s) for s in facts.statuses)}; "
                + ("client-facing" if facts.client_facing else "not a client refusal")
                + "; arrives as: "
                + ", ".join(sorted({e.surface.value for e in entries}))
                + "; defined in "
                + ", ".join(sorted({f"`{e.origin}`" for e in entries}))
                + "."
            )
            if code in global_codes:
                out.append("- Any request can receive it.")
            if any(e.shape is Shape.relationship for e in entries):
                out.append("- The body's `context` names the things involved.")
            if any(e.is_replay_futile for e in entries):
                out.append("- Never succeeds on replay; do not retry the identical request.")
            out.append(f"- Message: {facts.message!r}" if facts.message else "- Message: not found by the static search.")
            for note in sorted({e.note for e in entries if e.note}):
                out.append(f"- Note: {_first_sentence(note)}")
            shared_key = next((key for key, (checks, _) in shared.items() if facts.check in checks), None)
            if shared_key is not None:
                out.append(f"- Scientific check: [`{shared_key.split(':', 1)[1]}`](#{_anchor('k', shared_key)}).")
            elif facts.check is not None:
                out.append(f"- Scientific check ({facts.check.tier.value}): {_one_line(facts.check.asserts)}")
                if facts.check.escape_hatch:
                    out.append(f"- If your chemistry is legitimate: {_one_line(facts.check.escape_hatch)}")
            out.append("")
        untraced = [code for code in client_facing if code not in documented]
        out += [
            "### Client-facing codes not traced to any producer route",
            "",
            "Refusals a client can receive that no producer route's code path was traced to: read-API,"
            " curation and account refusals, and any code raised through a path the static trace"
            " cannot follow.",
            "",
            *[
                f"- `{code}` ({'/'.join(str(s) for s in sorted({e.status for e in self.catalogue[code]}))})"
                for code in untraced
            ],
            "",
        ]
        return out

    def _render_route_classification(self) -> list[str]:
        out = [
            "## Write routes that are not producer surfaces",
            "",
            "Every write route of the live application that the rule left out, with the reason. A"
            " producer surface is an authenticated write with a request body that is not role-gated.",
            "",
            "| Route | Why it is not a producer surface |",
            "|---|---|",
        ]
        for info in sorted(self.routes, key=lambda i: (i.path, i.method)):
            if info.category in {"producer", "read"}:
                continue
            out.append(f"| `{info.label}` | {_cell(info.reason)} |")
        out.append("")
        return out

    # -- files ---------------------------------------------------------------

    def render_files(self) -> dict[str, str]:
        files = {MARKDOWN_NAME: self.render_markdown()}
        for surface in self.surfaces:
            files[surface.schema_file] = json_schema_text(surface.model)
        return files


def render(app: Any | None = None, routes: list[RouteInfo] | None = None) -> dict[str, str]:
    """Every generated file, keyed by its path relative to the contract directory."""
    return ContractBuilder(app=app, routes=routes).render_files()


def committed_files() -> dict[str, str]:
    files: dict[str, str] = {}
    markdown = CONTRACT_DIR / MARKDOWN_NAME
    if markdown.exists():
        files[MARKDOWN_NAME] = markdown.read_text()
    schema_dir = CONTRACT_DIR / SCHEMA_SUBDIR
    if schema_dir.exists():
        for path in sorted(schema_dir.glob(f"*{SCHEMA_SUFFIX}")):
            files[f"{SCHEMA_SUBDIR}/{path.name}"] = path.read_text()
    return files


def diff_files(rendered: dict[str, str], committed: dict[str, str]) -> str:
    """A unified diff over every file that differs, is missing, or is stale."""
    chunks: list[str] = []
    for name in sorted(set(rendered) | set(committed)):
        new, old = rendered.get(name), committed.get(name)
        if new == old:
            continue
        chunks.append(
            "".join(
                difflib.unified_diff(
                    (old or "").splitlines(keepends=True),
                    (new or "").splitlines(keepends=True),
                    fromfile=f"{name} (committed)" if old is not None else f"{name} (absent)",
                    tofile=f"{name} (rendered)" if new is not None else f"{name} (no route accepts it: delete)",
                    n=1,
                )
            )
        )
    return "".join(chunks)


FIX_INSTRUCTIONS = f"""\
The producer contract is out of date with the code it documents.

Fix, in this order:
  1. If this branch has not already bumped it, raise `version` in
     schemas/python/tckdb-schemas/pyproject.toml and add a CHANGELOG.md entry
     saying what a producer now sees differently. The contract ships inside
     tckdb-schemas, so check_package_version_bump.py requires a new version
     whenever these files change -- and the contract is stamped with that
     version, so bump first or you will regenerate twice.
  2. Regenerate:  {REGENERATE}
  3. Commit the regenerated files under
     schemas/python/tckdb-schemas/tckdb_schemas/contract/.
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--check", action="store_true", help="write nothing; exit 1 if the committed files are stale")
    args = parser.parse_args(argv)

    try:
        rendered = render()
    except (ExampleError, UndocumentedRule, ChangelogError) as exc:
        print(f"Cannot generate the producer contract: {exc}", file=sys.stderr)
        if isinstance(exc, UndocumentedRule):
            print(
                "The validator has neither a docstring nor a literal refusal message. Add a docstring "
                "stating the rule in a producer's terms; it is published verbatim.",
                file=sys.stderr,
            )
        return 1

    if args.check:
        diff = diff_files(rendered, committed_files())
        if not diff:
            print(f"{CONTRACT_DIR} is in sync ({len(rendered)} files).")
            return 0
        print(diff[:20000], file=sys.stderr)
        print(FIX_INSTRUCTIONS, file=sys.stderr)
        return 1

    schema_dir = CONTRACT_DIR / SCHEMA_SUBDIR
    schema_dir.mkdir(parents=True, exist_ok=True)
    for stale in sorted(schema_dir.glob(f"*{SCHEMA_SUFFIX}")):
        if f"{SCHEMA_SUBDIR}/{stale.name}" not in rendered:
            stale.unlink()
            print(f"Removed {stale} (no route accepts it any more)")
    for name, text in rendered.items():
        path = CONTRACT_DIR / name
        path.write_text(text)
    print(f"Wrote {len(rendered)} files under {CONTRACT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
