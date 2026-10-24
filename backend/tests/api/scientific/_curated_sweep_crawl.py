"""Route enumeration and request driver for the curated sweep."""

from __future__ import annotations

import itertools
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from fastapi.routing import APIRoute

from app.services.scientific_read import ref_guard
from tests.api.scientific._curated_sweep_inputs import (
    CRAWL_UNSUPPORTED,
    EXEMPT_ONLY_PARAMS,
    PATH_PARAM_PROVIDERS,
    ROUTE_INPUTS,
    ROUTE_PATH_PARAM_PROVIDERS,
)
from tests.api.scientific._curated_sweep_world import SweepWorld

PREFIX = "/api/v1/scientific"


@dataclass(frozen=True)
class RouteSpec:
    method: str
    path: str
    path_params: tuple[str, ...]
    query_params: frozenset[str]

    @property
    def key(self) -> tuple[str, str]:
        return (self.method, self.path)

    @property
    def exempt(self) -> bool:
        return ref_guard.route_matches(self.path, ref_guard.EXEMPT_ROUTE_PREFIXES)


@dataclass(frozen=True)
class Variant:
    label: str
    path_values: tuple[tuple[str, str], ...]
    query: tuple[tuple[str, str], ...] = ()
    body: str | None = None  # JSON text
    #: A body that depends on the profile the request is made under: ``((profile, JSON text), ...)``.
    body_by_profile: tuple[tuple[str, str], ...] = ()

    def body_for(self, profile: str) -> str | None:
        return dict(self.body_by_profile).get(profile, self.body)

    @property
    def request_text(self) -> str:
        """Everything the caller supplied: refs in here are the caller's own and may be echoed."""
        parts = [v for _, v in self.path_values] + [v for _, v in self.query]
        if self.body:
            parts.append(self.body)
        return "\n".join(parts)


@dataclass
class Result:
    status: int
    text: str
    statements: int = 0
    #: The SQL text of those statements, in order, so a count that differs can be explained and not only reported.
    statement_texts: list[str] = field(default_factory=list)
    reached: bool = False
    #: ``(model, field path, ref prefix)`` when the guard raised ``RefGuardUnplaceable`` for this request.
    unplaceable: tuple[str, str, str] | None = None
    spy_paths: list[str] = field(default_factory=list)
    #: What the guard withheld from this response (empty unless the route family is enabled).
    applied_sites: list[Any] = field(default_factory=list)
    #: Selection documents that passed the loader to engine seam in this request, and the placeholder tokens they made.
    documents: int = 0
    document_tokens: list[str] = field(default_factory=list)


def enumerate_routes(app) -> list[RouteSpec]:
    out: list[RouteSpec] = []
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith(PREFIX):
            continue
        params = tuple(p.name for p in route.dependant.path_params)
        queries = frozenset(p.name for p in route.dependant.query_params)
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            out.append(RouteSpec(method, route.path, params, queries))
    return sorted(out, key=lambda r: (r.path, r.method))


def missing_providers(specs: list[RouteSpec]) -> list[tuple[str, str]]:
    """``(route, param)`` pairs for a crawlable route whose path parameter has no provider."""
    out = []
    for spec in specs:
        if spec.exempt or spec.key in CRAWL_UNSUPPORTED:
            continue
        for param in spec.path_params:
            if param not in PATH_PARAM_PROVIDERS and param not in EXEMPT_ONLY_PARAMS:
                out.append((f"{spec.method} {spec.path}", param))
    return out


def variants_for(spec: RouteSpec, world: SweepWorld, *, with_internal_ids: bool) -> list[Variant]:
    providers = {**PATH_PARAM_PROVIDERS, **ROUTE_PATH_PARAM_PROVIDERS.get(spec.key, {})}
    value_lists = [[(p, v) for v in providers[p](world)] for p in spec.path_params]
    combos = list(itertools.product(*value_lists)) if value_lists else [()]
    # Labels are stable across runs (provider positions, never ref text) so a failure can name them.
    positions = list(itertools.product(*[range(len(vl)) for vl in value_lists])) if value_lists else [()]
    maker = ROUTE_INPUTS.get(spec.key)
    inputs = maker(world) if maker else [{}]
    out: list[Variant] = []
    for combo, pos in zip(combos, positions, strict=True):
        for n, inp in enumerate(inputs):
            query: dict[str, Any] = dict(inp.get("query", {})) if spec.method == "GET" else {}
            body = inp.get("json") if spec.method == "POST" else None
            by_profile = inp.get("json_by_profile") if spec.method == "POST" else None
            if spec.method == "POST" and body is None:
                body = {}
            if "limit" in spec.query_params and "limit" not in query:
                query["limit"] = 100
            if with_internal_ids:
                include = list(query.get("include", [])) if isinstance(query.get("include"), list) else []
                if "include" in spec.query_params:
                    query["include"] = [*include, "all", "internal_ids"]
                elif isinstance(body, dict) and "include" in body:
                    body = {**body, "include": [*body["include"], "internal_ids"]}
            qitems = []
            for k, v in query.items():
                for item in v if isinstance(v, list) else [v]:
                    qitems.append((k, str(item).lower() if isinstance(item, bool) else str(item)))
            label = "/".join(f"{p}[{i}]" for (p, _), i in zip(combo, pos, strict=True)) or "-"
            out.append(
                Variant(
                    label=f"{label}#{n}",
                    path_values=tuple(combo),
                    query=tuple(qitems),
                    body=json.dumps(body, sort_keys=True) if body is not None else None,
                    body_by_profile=tuple((k, json.dumps(v, sort_keys=True)) for k, v in (by_profile or {}).items()),
                )
            )
            # The `include=all` twin for routes that declare `include` on the query string.
            if "include" in spec.query_params and not with_internal_ids and "include" not in query:
                out.append(
                    Variant(
                        label=f"{label}#{n}+include",
                        path_values=tuple(combo),
                        query=(*tuple(qitems), ("include", "all")),
                        body=json.dumps(body, sort_keys=True) if body is not None else None,
                    )
                )
    return out


def url_for(spec: RouteSpec, variant: Variant) -> str:
    path = spec.path
    for name, value in variant.path_values:
        path = path.replace("{" + name + "}", value)
    return path


def send(client, spec: RouteSpec, variant: Variant, *, profile: str, counter: Callable | None = None):
    params = [*variant.query, ("profile", profile)]
    url = url_for(spec, variant)
    if spec.method == "GET":
        return client.get(url, params=params)
    return client.post(
        url, params=params, content=variant.body_for(profile) or "{}", headers={"content-type": "application/json"}
    )
