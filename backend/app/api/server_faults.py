"""Tell a server-side pydantic failure from a caller's, at the route.

A pydantic ``ValidationError`` is a ``ValueError``, which the API maps to 422.
FastAPI validates path, query and body input *before* an endpoint runs and
reports that as ``RequestValidationError``. So a ``ValidationError`` that
escapes an endpoint is, by default, the server's own: a read model built from a
row that does not fit it, a stored manifest that no longer parses. Reported as
a 422 it blames the caller and echoes an object repr.

:class:`ServerFaultRoute` turns such an escape into :class:`ServerFaultError`,
which the API returns as a 500 ``server_response_invalid`` and logs.

The exception is endpoint or service code that validates *caller* input
itself (a search request built from query parameters, a declaration the
caller sent). Those sites call :func:`caller_input`, which raises
:class:`CallerInputError` -- a ``ValueError`` that is not a pydantic
``ValidationError`` -- so they keep their 422 and the route class leaves them
alone.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from fastapi.routing import APIRoute
from pydantic import ValidationError

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


class ServerFaultRoute(APIRoute):
    """Route class: a ``ValidationError`` escaping the endpoint is a server fault."""

    def get_route_handler(self) -> Callable[..., Any]:
        handler = super().get_route_handler()

        async def guarded(request: Any) -> Any:
            try:
                return await handler(request)
            except ValidationError as exc:
                raise ServerFaultError(exc) from exc

        return guarded
