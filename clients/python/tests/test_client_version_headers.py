"""Outgoing-header tests for the tckdb-client/server compat handshake.

Every request must carry the ``X-TCKDB-Client-Name`` and
``X-TCKDB-Client-Version`` headers so the server can enforce a minimum
supported client version on writes (see
``backend/app/api/client_version.py``).
"""

from __future__ import annotations

import httpx

from tckdb_client import __version__
from tckdb_client.client import (
    CLIENT_NAME,
    CLIENT_NAME_HEADER,
    CLIENT_VERSION_HEADER,
)

from conftest import make_client


def _ok_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"ok": True})


def test_get_attaches_client_identity_headers():
    client, recorder = make_client(_ok_handler)
    client.health()

    sent = recorder.last.headers
    assert sent[CLIENT_NAME_HEADER.lower()] == CLIENT_NAME
    assert sent[CLIENT_VERSION_HEADER.lower()] == __version__


def test_post_attaches_client_identity_headers():
    client, recorder = make_client(_ok_handler)
    client.post_json("/uploads/thermo", {"hello": "world"})

    sent = recorder.last.headers
    assert sent[CLIENT_NAME_HEADER.lower()] == CLIENT_NAME
    assert sent[CLIENT_VERSION_HEADER.lower()] == __version__


def test_client_version_header_is_current_package_version():
    """The client always advertises the installed package version.

    Bumping ``pyproject.toml`` is enough to change what the client
    sends — no header constant needs to be edited.
    """
    client, recorder = make_client(_ok_handler)
    client.health()
    assert recorder.last.headers[CLIENT_VERSION_HEADER.lower()] == __version__


def test_extra_headers_do_not_strip_client_identity():
    """Caller-supplied headers must not silently remove the compat headers.

    A future user-supplied ``extra_headers`` dict could accidentally
    re-set Content-Type or similar; the client must defensively
    preserve its own identity headers so server-side compat checks
    still see them.
    """
    client, recorder = make_client(_ok_handler)
    client.request_json(
        "POST",
        "/uploads/thermo",
        json={"hello": "world"},
        extra_headers={"X-Custom-Header": "x"},
    )

    sent = recorder.last.headers
    assert sent[CLIENT_NAME_HEADER.lower()] == CLIENT_NAME
    assert sent[CLIENT_VERSION_HEADER.lower()] == __version__
    assert sent["x-custom-header"] == "x"


def test_extra_headers_cannot_override_client_identity():
    """Even an explicit override of the identity headers is restored.

    Letting a caller spoof ``X-TCKDB-Client-Name`` would defeat the
    server-side compat gate (raw HTTP callers are intentionally
    unpoliced, so a tckdb-client masquerading as raw HTTP would
    bypass the version check).
    """
    client, recorder = make_client(_ok_handler)
    client.request_json(
        "POST",
        "/uploads/thermo",
        json={"hello": "world"},
        extra_headers={
            CLIENT_NAME_HEADER: "evil-client",
            CLIENT_VERSION_HEADER: "999.999.999",
        },
    )

    sent = recorder.last.headers
    assert sent[CLIENT_NAME_HEADER.lower()] == CLIENT_NAME
    assert sent[CLIENT_VERSION_HEADER.lower()] == __version__
