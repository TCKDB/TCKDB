"""The frontend proxy answers an over-limit body with TCKDB's coded 413, not nginx's HTML page.

``frontend/nginx.conf`` carries the JSON by hand (nginx cannot call Python), so this pins the text in the config to
``app.api.proxy_body_limit.proxy_refusal_body`` and checks the wiring: each ``/api/`` location with a
``client_max_body_size`` names an ``error_page 413`` whose named location returns that cap's body as JSON.
The live behaviour (a real nginx container answering ``application/json``) is checked by hand in the PR; this is the
static guard that keeps the config and the envelope from drifting.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.api.code_catalogue import CATALOGUE
from app.api.proxy_body_limit import (
    CODE_REQUEST_BODY_TOO_LARGE,
    DEFAULT_ROUTE_MAX_BYTES,
    UPLOAD_ROUTE_MAX_BYTES,
    proxy_refusal_body,
)

NGINX_CONF = Path(__file__).resolve().parents[3] / "frontend" / "nginx.conf"

_SIZES = {"10m": DEFAULT_ROUTE_MAX_BYTES, "100m": UPLOAD_ROUTE_MAX_BYTES}


def _text() -> str:
    return NGINX_CONF.read_text()


def _locations(text: str) -> dict[str, str]:
    """``{location pattern: block body}``; a quoted string may hold braces, comments are dropped first."""
    pattern = r"^[ ]*location\s+([^{;]+?)\s*\{((?:[^}']|'[^']*')*)\}"
    text = re.sub(r"^[ ]*#.*$", "", text, flags=re.M)  # comments hold apostrophes
    return {m.group(1).strip(): m.group(2) for m in re.finditer(pattern, text, re.M)}


def _named_location_bodies(text: str) -> dict[str, str]:
    """``{@name: the single-quoted string its ``return 413`` sends}``; other directives are allowed."""
    found = {}
    for name, block in _locations(text).items():
        if name.startswith("@"):
            match = re.search(r"return 413 '([^']*)';", block)
            if match:
                found[name] = match.group(1)
    return found


def test_every_capped_api_location_wires_a_json_413() -> None:
    text = _text()
    locations = _locations(text)
    bodies = _named_location_bodies(text)
    capped = {
        name: re.search(r"client_max_body_size (\w+);", block).group(1)
        for name, block in locations.items()
        if "/api" in name and "client_max_body_size" in block
    }
    assert set(capped.values()) == set(_SIZES), f"expected the 10m and 100m locations, got {capped}"
    for name, size in capped.items():
        match = re.search(r"error_page 413 (@\w+);", locations[name])
        assert match, f"{name} caps the body at {size} but has no `error_page 413`: a refusal would be nginx's HTML"
        target = match.group(1)
        assert target in bodies, f"error_page names {target}, which has no JSON `return 413` location"
        assert size in _SIZES, f"{name} has cap {size}, which proxy_body_limit does not know"
        body = json.loads(bodies[target])  # must parse
        assert body == json.loads(json.dumps(proxy_refusal_body(_SIZES[size]))), (
            f"{target} (cap {size}) has drifted from app.api.proxy_body_limit.proxy_refusal_body"
        )
        assert body["code"] == CODE_REQUEST_BODY_TOO_LARGE
        assert body["context"] == {"max_bytes": _SIZES[size]}
        block = locations[target]
        assert "default_type application/json;" in block
        assert "add_header X-Request-ID $request_id always;" in block


def test_the_guard_notices_a_capped_location_without_an_error_page() -> None:
    probe = "location /api/v1/exports/ { client_max_body_size 1m; proxy_pass http://x; }"
    block = _locations(probe)["/api/v1/exports/"]
    assert "client_max_body_size" in block and not re.search(r"error_page 413", block)


def test_the_caps_in_the_module_are_the_caps_in_the_config() -> None:
    text = _text()
    assert f"{DEFAULT_ROUTE_MAX_BYTES:,}" in "".join(_named_location_bodies(text).values())
    assert re.search(r"location /api/ \{[^}]*client_max_body_size 10m;", text)
    assert re.search(r"location ~ \^/api/v1/\(uploads\|jobs\)/[^{]*\{[^}]*client_max_body_size 100m;", text)


def test_the_message_is_built_from_the_code_and_the_limit_only() -> None:
    body = proxy_refusal_body(UPLOAD_ROUTE_MAX_BYTES)
    assert set(body) == {"code", "detail", "context"}
    assert isinstance(body["detail"], str)
    assert f"{UPLOAD_ROUTE_MAX_BYTES:,}" in body["detail"]


def test_the_code_is_catalogued_at_413() -> None:
    entries = [e for e in CATALOGUE if e.code == CODE_REQUEST_BODY_TOO_LARGE]
    assert [e.status for e in entries] == [413]
