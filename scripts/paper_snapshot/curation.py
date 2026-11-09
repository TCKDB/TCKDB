"""Curator approval of the deposits.

A curator account is created with the operator CLI (no public route grants the role) and approves every pending
submission through ``POST /submissions/{id}/approve``. The summary written on each approval states that the build
script approved it as a demonstration, not an independent review.
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Any

import httpx

from common import REPO_ROOT, Report, Session

APPROVAL_SUMMARY = (
    "Approved by the paper-snapshot build script as a demonstration of the curation overlay. "
    "This is not an independent review; the depositor and the build script are the same project."
)


def create_curator(db_env: dict[str, str], username: str, password: str, email: str, full_name: str) -> str:
    """Create (or leave in place) a curator-role user through the operator CLI."""
    env = dict(os.environ)
    env.update(db_env)
    env["TCKDB_BOOTSTRAP_PASSWORD"] = password
    done = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "backend" / "scripts" / "bootstrap_admin.py"),
            "--username", username, "--email", email, "--full-name", full_name, "--role", "curator",
        ],
        env=env, check=True, capture_output=True, text=True,
    )
    return done.stdout.strip()


PAGE = 200


def _pending(http: httpx.Client, session: Session, auth: dict[str, str]) -> list[dict[str, Any]]:
    """Every submission awaiting review, paged until the listing is exhausted."""
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        response = http.get(
            session.base + "/submissions/for-review", params={"limit": PAGE, "offset": offset}, headers=auth
        )
        response.raise_for_status()
        page = response.json()
        if not page:
            return rows
        rows.extend(page)
        offset += len(page)


def approve_all(
    session: Session, username: str, password: str, report: Report, summary: str = APPROVAL_SUMMARY
) -> dict[str, Any]:
    """Log in as the curator and approve every pending submission, oldest first.

    The pending list is read to its end (not just the first page), each is approved once, and the list is read
    again at the end: anything still pending, or any approval that failed, stops the build with an error.
    """
    approved = 0
    failed: list[dict[str, Any]] = []
    with httpx.Client(timeout=120.0) as http:
        login = http.post(session.base + "/auth/login", json={"username": username, "password": password})
        login.raise_for_status()
        auth = session.session_header(login)
        for row in sorted(_pending(http, session, auth), key=lambda r: r["id"]):
            response = http.post(
                f"{session.base}/submissions/{row['id']}/approve", json={"summary": summary}, headers=auth
            )
            if response.status_code == 200:
                approved += 1
                continue
            failed.append({"submission_ref": row.get("public_ref"), "status": response.status_code, "body": response.text[:300]})
            report.refusals.append(
                {
                    "source": "curation",
                    "where": f"approve {row.get('public_ref')}",
                    "code": "submission_approve_" + str(response.status_code),
                    "status": response.status_code,
                    "message": response.text[:300],
                }
            )
        still_pending = _pending(http, session, auth)
    if failed or still_pending:
        raise RuntimeError(
            f"approval incomplete: {approved} approved, {len(failed)} approval(s) failed, "
            f"{len(still_pending)} submission(s) still pending; first failure: {failed[:1]}"
        )
    return {"approved_submissions": approved, "failed": failed, "pending_after": 0, "summary_written": summary}
