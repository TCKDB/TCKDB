"""Shared pieces of the snapshot build: the run report, the client, auth helpers.

Nothing here talks to the database. Every deposit goes through the public HTTP
surface (the ``tckdb_client`` package, directly or through an adapter).
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from tckdb_client import TCKDBClient
from tckdb_client.errors import TCKDBError, TCKDBHTTPError

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Package source roots, put first on the path so a checkout runs without an install.
PACKAGE_ROOTS = (
    REPO_ROOT / "schemas" / "python" / "tckdb-schemas",
    REPO_ROOT / "clients" / "python" / "src",
    REPO_ROOT / "clients" / "python" / "adapters" / "chemkin",
    REPO_ROOT / "backend",
)


def api_base(url: str) -> str:
    """Normalise ``http://host:port`` or ``.../api/v1`` to the API root."""
    url = url.rstrip("/")
    return url if url.endswith("/api/v1") else url + "/api/v1"


def error_code(exc: BaseException) -> str:
    """The server's refusal code when it sent one, else a status-based label."""
    if isinstance(exc, TCKDBHTTPError):
        if exc.code:
            return str(exc.code)
        body = exc.response_json
        if isinstance(body, dict):
            detail = body.get("detail")
            code = body.get("code") or (detail.get("code") if isinstance(detail, dict) else None)
            if code:
                return str(code)
        return f"http_{exc.status_code}_without_code"
    return type(exc).__name__


def error_message(exc: BaseException, limit: int = 400) -> str:
    return str(exc).replace("\n", " ")[:limit]


@dataclass
class Report:
    """Deposit outcomes: per-source status, warnings and refusals."""

    sources: dict[str, dict[str, Any]] = field(default_factory=dict)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    refusals: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def source(self, name: str) -> dict[str, Any]:
        return self.sources.setdefault(name, {"status": "not run", "counts": {}})

    def count(self, source: str, key: str, n: int = 1) -> None:
        counts = self.source(source)["counts"]
        counts[key] = counts.get(key, 0) + n

    def warn(self, source: str, where: str, warning: dict[str, Any]) -> None:
        self.warnings.append(
            {
                "source": source,
                "where": where,
                "code": warning.get("code") or "no_code",
                "field": warning.get("field"),
                "message": (warning.get("message") or "")[:300],
            }
        )

    def refuse(self, source: str, where: str, exc: BaseException) -> None:
        self.refusals.append(
            {
                "source": source,
                "where": where,
                "code": error_code(exc),
                "status": getattr(exc, "status_code", None),
                "message": error_message(exc),
            }
        )

    def note(self, text: str) -> None:
        self.notes.append(text)

    def grouped(self) -> dict[str, Any]:
        def group(rows: list[dict[str, Any]]) -> dict[str, Any]:
            by_code: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for row in rows:
                by_code[row["code"]].append(row)
            return {
                code: {
                    "count": len(items),
                    "by_source": dict(sorted(Counter(i["source"] for i in items).items())),
                    "example_message": items[0]["message"],
                    "example_where": items[0]["where"],
                }
                for code, items in sorted(by_code.items())
            }

        return {
            "sources": self.sources,
            "warnings_by_code": group(self.warnings),
            "refusals_by_code": group(self.refusals),
            "notes": self.notes,
        }

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = self.grouped()
        payload["warnings"] = self.warnings
        payload["refusals"] = self.refusals
        path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def collect_response_warnings(report: Report, source: str, where: str, response: Any) -> int:
    """Record the ``warnings`` list of an upload response, wherever it sits."""
    found = 0
    if isinstance(response, dict):
        for warning in response.get("warnings") or []:
            if isinstance(warning, dict):
                report.warn(source, where, warning)
                found += 1
    return found


class Session:
    """The instance under build and the depositor's API key."""

    def __init__(self, base_url: str, timeout: float = 120.0) -> None:
        self.base = api_base(base_url)
        self.timeout = timeout
        self.depositor_key: str | None = None

    def health(self) -> dict[str, Any]:
        response = httpx.get(self.base + "/status", timeout=30.0)
        response.raise_for_status()
        return response.json()

    def client(self) -> TCKDBClient:
        return TCKDBClient(self.base, api_key=self.depositor_key, timeout=self.timeout)


    def session_header(self, response: httpx.Response) -> dict[str, str]:
        """Return the session cookie as a ``Cookie`` header (the server's cookie is ``Secure``)."""
        raw = response.headers.get("set-cookie", "")
        match = re.search(r"tckdb_session=([^;]+)", raw)
        if not match:
            raise RuntimeError(f"no session cookie in the response ({response.status_code}): {response.text[:200]}")
        return {"Cookie": f"tckdb_session={match.group(1)}"}

    def register_depositor(self, username: str, password: str, email: str, full_name: str) -> None:
        """Register the depositor (log in if it exists) and mint an API key."""
        with httpx.Client(timeout=30.0) as http:
            response = http.post(
                self.base + "/auth/register",
                json={"username": username, "password": password, "email": email, "full_name": full_name},
            )
            if response.status_code == 409:
                response = http.post(self.base + "/auth/login", json={"username": username, "password": password})
            response.raise_for_status()
            minted = http.post(
                self.base + "/auth/api-keys", json={"label": "paper-snapshot-build"}, headers=self.session_header(response)
            )
            minted.raise_for_status()
            self.depositor_key = minted.json()["key"]


def digest_files(root: Path, files: list[Path]) -> dict[str, Any]:
    """Return the file count and one SHA-256 over sorted (relative path, content digest) pairs."""
    import hashlib

    pairs = []
    for path in sorted(set(files)):
        pairs.append((path.resolve().relative_to(root.resolve()).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest()))
    outer = hashlib.sha256()
    for relative, digest in pairs:
        outer.update(f"{relative}\0{digest}\n".encode())
    return {"files": len(pairs), "sha256": outer.hexdigest()}


SAFE = re.compile(r"[^A-Za-z0-9._:-]")


def slug(text: str) -> str:
    return SAFE.sub("-", text)


__all__ = [
    "PACKAGE_ROOTS",
    "REPO_ROOT",
    "Report",
    "Session",
    "TCKDBError",
    "TCKDBHTTPError",
    "api_base",
    "collect_response_warnings",
    "digest_files",
    "error_code",
    "slug",
]
