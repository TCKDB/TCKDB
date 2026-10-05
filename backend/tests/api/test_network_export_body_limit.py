"""The export route's body cap, at the ASGI level: refused before a byte is read, and while streaming."""

from __future__ import annotations

import asyncio
import json

from app.api.export_limits import CODE_BODY_TOO_LARGE, NetworkExportBodyLimitMiddleware

PATH = "/api/v1/scientific/networks/net_x/kinetics/export-selected"


async def _run(headers, chunks, *, limit=100, path=PATH, method="POST"):
    reached = {"app": False, "received": 0}
    sent: list[dict] = []

    async def app(scope, receive, send):
        reached["app"] = True
        while True:
            message = await receive()
            if message["type"] != "http.request":
                break
            reached["received"] += len(message.get("body", b""))
            if not message.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    queue = list(chunks)

    async def receive():
        if not queue:
            return {"type": "http.disconnect"}
        body = queue.pop(0)
        return {"type": "http.request", "body": body, "more_body": bool(queue)}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": method, "path": path, "headers": headers}
    await NetworkExportBodyLimitMiddleware(app, limit=limit)(scope, receive, send)
    return reached, sent, queue


def _status(sent):
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


def _body(sent):
    return json.loads(b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body"))


def test_a_declared_length_over_the_cap_is_refused_without_reading_a_byte():
    reached, sent, left = asyncio.run(_run([(b"content-length", b"101")], [b"x" * 101]))
    assert _status(sent) == 413 and _body(sent)["code"] == CODE_BODY_TOO_LARGE
    assert reached["app"] is False and left == [b"x" * 101]  # the app never ran and nothing was read
    assert _body(sent)["context"] == {"max_bytes": 100, "given_bytes": 101}


def test_a_chunked_body_is_refused_the_moment_it_passes_the_cap():
    reached, sent, left = asyncio.run(_run([], [b"x" * 60, b"x" * 60, b"x" * 60]))
    assert _status(sent) == 413 and _body(sent)["code"] == CODE_BODY_TOO_LARGE
    assert left == [b"x" * 60]  # the rest was never read


def test_a_body_within_the_cap_and_other_routes_pass_through():
    reached, sent, _ = asyncio.run(_run([(b"content-length", b"100")], [b"x" * 100]))
    assert _status(sent) == 200 and reached["received"] == 100
    other, sent, _ = asyncio.run(_run([(b"content-length", b"5000")], [b"x" * 5000], path="/api/v1/health"))
    assert _status(sent) == 200 and other["received"] == 5000
    get, sent, _ = asyncio.run(_run([(b"content-length", b"5000")], [b"x" * 5000], method="GET"))
    assert _status(sent) == 200
