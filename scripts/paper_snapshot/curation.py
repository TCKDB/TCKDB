"""Approval of the deposits by a curator account the build script creates.

This is a demonstration, not a review. The snapshot has one author and one
build script; the curator account exists so that the curation overlay of the
database (review state, submission approval, audit events) is populated and a
reader can see what an approved record looks like. The approval summary
written on every submission says so, and the snapshot record repeats it.

Account creation is the one step that is not a public route: no endpoint
grants the curator role, so the operator CLI (``backend/scripts/bootstrap_admin.py``)
does it, exactly as the deployment runbook does for a real curator. The
approvals themselves are ordinary ``POST /submissions/{id}/approve`` calls made
with the curator's session.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
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


def approve_all(session: Session, username: str, password: str, report: Report, summary: str = APPROVAL_SUMMARY) -> dict[str, Any]:
    """Log in as the curator and approve every pending submission, oldest first."""
    approved = 0
    failed: list[dict[str, Any]] = []
    gave_up: set[int] = set()
    with httpx.Client(timeout=120.0) as http:
        login = http.post(session.base + "/auth/login", json={"username": username, "password": password})
        login.raise_for_status()
        auth = session.session_header(login)
        while True:
            page = http.get(session.base + "/submissions/for-review", params={"limit": 200}, headers=auth)
            page.raise_for_status()
            rows = page.json()
            pending = [r for r in rows if r.get("status") not in ("approved", "rejected") and r["id"] not in gave_up]
            if not pending:
                break
            progressed = 0
            for row in sorted(pending, key=lambda r: r["id"]):
                response = http.post(
                    f"{session.base}/submissions/{row['id']}/approve",
                    json={"summary": summary},
                    headers=auth,
                )
                if response.status_code == 200:
                    approved += 1
                    progressed += 1
                else:
                    gave_up.add(row["id"])
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
            if not progressed:
                break
    return {"approved_submissions": approved, "failed": failed, "summary_written": summary}
