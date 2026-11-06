"""Build a read model from data the server itself produced.

``Model.model_validate(row)`` on a database row raises pydantic's
``ValidationError`` when the row does not fit the model. That is a
``ValueError``, which the API maps to 422, so a failure of the *server's own*
data read as the caller's fault and echoed an ORM object repr. Routes build
their responses with :func:`build_read` instead: the failure becomes
:class:`ServerReadModelError`, which is handled like a ``response_model``
failure -- 500 ``data_integrity_error``, fixed text, errors logged.

Only for server data. A caller's payload is validated by FastAPI, and a manual
``model_validate`` on one stays a plain ``ValidationError`` (422).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

M = TypeVar("M", bound=BaseModel)


class ServerReadModelError(Exception):
    """A read model rejected data the server read from its own store."""

    def __init__(self, model: type[BaseModel], cause: ValidationError) -> None:
        super().__init__(f"{model.__name__} rejected server data")
        self.model = model
        self.cause = cause

    def errors(self) -> list[Any]:
        return list(self.cause.errors())


def build_read(model: type[M], obj: Any) -> M:
    """``model.model_validate(obj)``, with a failure reported as the server's."""
    try:
        return model.model_validate(obj)
    except ValidationError as exc:
        raise ServerReadModelError(model, exc) from exc


def read_many(model: type[M], objs: Iterable[Any]) -> list[M]:
    """:func:`build_read` over each item."""
    return [build_read(model, o) for o in objs]
