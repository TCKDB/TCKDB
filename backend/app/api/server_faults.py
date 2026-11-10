"""Tell a server-side pydantic failure from a caller's, at the route.

A pydantic ``ValidationError`` is a ``ValueError``, which the API maps to 422.
FastAPI validates path, query and body input *before* an endpoint runs and
reports that as ``RequestValidationError``. A ``ValidationError`` that escapes
an endpoint can still be either side's fault: a read model built from a row
that does not fit it is the server's, but endpoint, service and workflow code
also re-validates parts of what the caller sent (a lookup identity, an upload
sub-object checked by a stricter internal model). Reported as a 422, the
server's own failure blames the caller and echoes an object repr.

:class:`ServerFaultRoute` decides by the failing model: when the error's
``title`` names a model in the server-model set (every route's
``response_model``, walked through its nested fields, plus the read models),
it raises :class:`ServerFaultError`, which the API returns as a 500
``server_response_invalid`` and logs. Any other model keeps today's 422.

The rule is by class name and global. Some request and query models are also
in the set (as response echoes), and a new echo or nested field can pull more
in. So any code that validates *caller* data by hand must go through
:func:`caller_input`, which raises :class:`CallerInputError` -- a ``ValueError``
that is not a pydantic ``ValidationError`` -- so it keeps its 422 whatever the
set later holds.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
import typing
from collections.abc import Callable
from typing import Any, TypeVar

from fastapi.routing import APIRoute
from pydantic import BaseModel, ValidationError

T = TypeVar("T")


class ServerFaultError(Exception):
    """A pydantic validation failed on data the server produced."""

    def __init__(self, cause: ValidationError) -> None:
        super().__init__(f"{cause.title} rejected server data")
        self.cause = cause
        #: The pydantic model that rejected the data.
        self.model = cause.title

    def errors(self) -> list[Any]:
        return list(self.cause.errors())


class CallerInputError(ValueError):
    """A pydantic validation failed on data the caller sent: a 422, as before.

    Carries the same ``errors()`` and ``str()`` as the ``ValidationError`` it
    wraps, so the 422 body is unchanged.
    """

    def __init__(self, cause: ValidationError) -> None:
        super().__init__(str(cause))
        self.cause = cause

    def errors(self) -> list[Any]:
        return list(self.cause.errors())


def caller_input(build: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """``build(*args, **kwargs)`` on caller-supplied data; a failure stays a 422.

    ``build`` is a model class or ``Model.model_validate``.
    """
    try:
        return build(*args, **kwargs)
    except ValidationError as exc:
        raise CallerInputError(exc) from exc


_SCHEMA_PACKAGE = "app.schemas"
_READS_PACKAGE = "app.schemas.reads"


def _collect(annotation: Any, found: set[str], seen: set[int]) -> None:
    """Add every pydantic model reachable from a type annotation."""
    if inspect.isclass(annotation) and issubclass(annotation, BaseModel):
        if id(annotation) in seen:
            return
        seen.add(id(annotation))
        found.add(annotation.__name__)
        origin = annotation.__pydantic_generic_metadata__.get("origin")
        if origin is not None:
            found.add(origin.__name__)
        for field in annotation.model_fields.values():
            _collect(field.annotation, found, seen)
        return
    for arg in typing.get_args(annotation):
        _collect(arg, found, seen)


def _schema_classes() -> set[str]:
    """Every class defined in ``app.schemas.reads`` and every ``*Read`` in ``app.schemas``."""
    found: set[str] = set()
    package = importlib.import_module(_SCHEMA_PACKAGE)
    for info in pkgutil.walk_packages(package.__path__, prefix=f"{_SCHEMA_PACKAGE}."):
        module = importlib.import_module(info.name)
        in_reads = info.name == _READS_PACKAGE or info.name.startswith(f"{_READS_PACKAGE}.")
        for name, obj in vars(module).items():
            if (
                inspect.isclass(obj)
                and issubclass(obj, BaseModel)
                and obj.__module__ == info.name
                and (in_reads or name.endswith("Read"))
            ):
                found.add(obj.__name__)
    return found


def server_model_names(app: Any) -> frozenset[str]:
    """Names of the models the server builds responses from, computed once per app.

    Every route's ``response_model`` walked through nested fields, list/dict/
    union arguments and generic envelopes, plus the schema classes above.
    """
    cached = getattr(app.state, "server_model_names", None)
    if cached is None:
        found = _schema_classes()
        seen: set[int] = set()
        for route in app.routes:
            if isinstance(route, APIRoute) and route.response_model is not None:
                _collect(route.response_model, found, seen)
        cached = frozenset(found)
        app.state.server_model_names = cached
    return cached


def classify_validation_error(exc: ValidationError, app: Any) -> Exception:
    """``ServerFaultError`` for a response or read model, else ``exc`` itself (a 422)."""
    if exc.title in server_model_names(app):
        return ServerFaultError(exc)
    return exc


class ServerFaultRoute(APIRoute):
    """Route class: a response or read model failing inside the endpoint is a server fault."""

    def get_route_handler(self) -> Callable[..., Any]:
        handler = super().get_route_handler()

        async def guarded(request: Any) -> Any:
            try:
                return await handler(request)
            except ValidationError as exc:
                classified = classify_validation_error(exc, request.app)
                if classified is exc:
                    raise
                raise classified from exc

        return guarded
