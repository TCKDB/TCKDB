"""Hosted/public OpenAPI projection for machine consumers.

Internal Pydantic models retain integer primary keys because local/debug
deployments may opt into them.  Hosted JSON removes those keys at the response
seam.  This module projects the generated schema through the same policy
without duplicating every response model.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

from fastapi import FastAPI

from app.services.scientific_read.internal_ids import is_internal_id_key

_SCIENTIFIC_PATH_PREFIX = "/api/v1/scientific/"
_SUCCESS_PREFIXES = ("2",)


def _component_refs(node: object) -> Iterator[str]:
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            yield ref.rsplit("/", 1)[-1]
        for value in node.values():
            yield from _component_refs(value)
    elif isinstance(node, list):
        for value in node:
            yield from _component_refs(value)


def _scientific_response_components(schema: dict[str, Any]) -> set[str]:
    components = schema.get("components", {}).get("schemas", {})
    pending: list[str] = []
    for path, path_item in schema.get("paths", {}).items():
        if not path.startswith(_SCIENTIFIC_PATH_PREFIX):
            continue
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue
            for status, response in operation.get("responses", {}).items():
                if str(status).startswith(_SUCCESS_PREFIXES):
                    pending.extend(_component_refs(response))

    discovered: set[str] = set()
    while pending:
        name = pending.pop()
        if name in discovered:
            continue
        discovered.add(name)
        component = components.get(name)
        if component is not None:
            pending.extend(_component_refs(component))
    return discovered


def _make_internal_ids_optional(node: object) -> None:
    """Project policy-hidden IDs through nested and inline schemas."""

    if not isinstance(node, dict):
        return

    required = node.get("required")
    if isinstance(required, list):
        node["required"] = [name for name in required if not is_internal_id_key(name)]
        if not node["required"]:
            node.pop("required")

    properties = node.get("properties")
    if isinstance(properties, dict):
        for name, property_schema in properties.items():
            if is_internal_id_key(name) and isinstance(property_schema, dict):
                property_schema["x-tckdb-policy-hidden"] = True

    for value in node.values():
        if isinstance(value, dict):
            _make_internal_ids_optional(value)
        elif isinstance(value, list):
            for item in value:
                _make_internal_ids_optional(item)


def project_hosted_openapi(schema: dict[str, Any]) -> dict[str, Any]:
    """Mutate and return generated OpenAPI as the hosted public contract."""

    component_schemas = schema.setdefault("components", {}).setdefault("schemas", {})
    for name in _scientific_response_components(schema):
        component = component_schemas.get(name)
        if isinstance(component, dict):
            _make_internal_ids_optional(component)

    validation_error = component_schemas.get("HTTPValidationError")
    if isinstance(validation_error, dict):
        validation_error.clear()
        validation_error.update(
            {
                "title": "MachineErrorEnvelope",
                "type": "object",
                "required": ["code", "detail", "context"],
                "properties": {
                    "code": {"type": "string"},
                    "detail": {
                        "anyOf": [
                            {"type": "string"},
                            {"type": "array", "items": {}},
                            {"type": "object", "additionalProperties": True},
                        ]
                    },
                    "context": {
                        "type": "object",
                        "additionalProperties": True,
                    },
                },
            }
        )
    return schema


def install_hosted_openapi(app: FastAPI) -> None:
    """Install one cached public-schema adapter on *app*."""

    generated_openapi: Callable[[], dict[str, Any]] = app.openapi

    def hosted_openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            app.openapi_schema = project_hosted_openapi(generated_openapi())
        return app.openapi_schema

    app.openapi = hosted_openapi


__all__ = ["install_hosted_openapi", "project_hosted_openapi"]
