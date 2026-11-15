"""Curation of the deposits, by a curator account the build creates.

A curator account is created with the operator CLI (no public route grants the role). Through the public curator routes
it then:

1. approves every pending submission (``POST /submissions/{id}/approve``) except the ones held for a record-level
   review;
2. reviews the caught-error run record by record (``caught_error`` in the sources file): a structure-evidence finding on
   its transition state (``POST /curation/structure-evidence-findings``), its transition state, kinetics and transition-state
   calculations rejected and every other record approved (``PATCH /record-reviews/{record_type}/{record_id}``);
3. cuts one dataset release (``release`` in the sources file): a curation policy, a draft, its selections, and the
   publication. A deposit the release ships without a rights basis is attested by the curator with ``source_terms`` when
   it came from a source whose terms the sources file names, and stops the build otherwise.

Every summary, note and rationale written says that the build script wrote it for a demonstration.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from typing import Any

import httpx

from common import REPO_ROOT, Report, Session

DEMONSTRATION = "Written by the paper-snapshot build script as a demonstration of the curation overlay; not an independent review."
APPROVAL_SUMMARY = (
    "Approved by the paper-snapshot build script as a demonstration of the curation overlay. "
    "This is not an independent review; the depositor and the build script are the same project."
)


def create_curator(db_env: dict[str, str], username: str, password: str, email: str, full_name: str) -> str:
    """Create a curator account with the operator CLI."""
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


class Curator:
    """The curator's authenticated HTTP session against the instance."""

    def __init__(self, session: Session, username: str, password: str) -> None:
        self.base = session.base
        self.http = httpx.Client(timeout=120.0)
        login = self.http.post(self.base + "/auth/login", json={"username": username, "password": password})
        login.raise_for_status()
        self.auth = session.session_header(login)

    def close(self) -> None:
        self.http.close()

    def call(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        return self.http.request(method, self.base + path, headers=self.auth, **kwargs)

    def ok(self, method: str, path: str, **kwargs: Any) -> Any:
        """Call and return the JSON body; raise with the server's answer on any non-2xx status."""
        response = self.call(method, path, **kwargs)
        if response.status_code >= 300:
            raise RuntimeError(f"{method} {path} -> {response.status_code}: {response.text[:500]}")
        return response.json() if response.content else None


def _pending(curator: Curator) -> list[dict[str, Any]]:
    """Return every submission awaiting review, paged to the end."""
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = curator.ok("GET", "/submissions/for-review", params={"limit": PAGE, "offset": offset})
        if not page:
            return rows
        rows.extend(page)
        offset += len(page)


def approve_all(
    curator: Curator, report: Report, hold: frozenset[str] = frozenset(), summary: str = APPROVAL_SUMMARY
) -> dict[str, Any]:
    """Approve every pending submission except those in ``hold``; raise if an approval fails or another stays pending."""
    approved = 0
    failed: list[dict[str, Any]] = []
    for row in sorted(_pending(curator), key=lambda r: r["id"]):
        if row.get("public_ref") in hold:
            continue
        response = curator.call("POST", f"/submissions/{row['id']}/approve", json={"summary": summary})
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
    still_pending = sorted(r.get("public_ref") for r in _pending(curator) if r.get("public_ref") not in hold)
    if failed or still_pending:
        raise RuntimeError(
            f"approval incomplete: {approved} approved, {len(failed)} approval(s) failed, "
            f"{len(still_pending)} submission(s) still pending; first failure: {failed[:1]}"
        )
    return {"approved_submissions": approved, "held_for_record_review": sorted(hold), "failed": failed,
            "pending_after": 0, "summary_written": summary}


# --------------------------------------------------------------------------
# deposits of the ARC source
# --------------------------------------------------------------------------


def arc_response(report: Report, run: str) -> dict[str, Any]:
    """The server's response to the one computed-reaction bundle of ``run``, as the ARC source recorded it."""
    for entry in report.source("arc_runs").get("runs") or []:
        if entry["run"] == run:
            uploaded = [p for p in entry["payloads"] if p["status"] == "uploaded" and p["payload_kind"] == "computed_reaction"]
            if len(uploaded) != 1:
                raise RuntimeError(f"curation: {run} has {len(uploaded)} uploaded computed_reaction bundles, not one")
            return uploaded[0]["response"]
    raise RuntimeError(f"curation: {run} is not among the deposited ARC runs")


def record_ref(curator: Curator, record_type: str, record_id: int) -> str:
    return curator.ok("GET", f"/record-reviews/{record_type}/{record_id}")["record_public_ref"]


def _submission_reviews(curator: Curator, submission_id: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = curator.ok("GET", "/record-reviews", params={"submission_id": submission_id, "limit": PAGE, "skip": offset})
        if not page:
            break
        rows.extend(page)
        offset += len(page)
        if len(page) < PAGE:
            break
    return rows


def _set_review(curator: Curator, row: dict[str, Any], status: str, note: str) -> dict[str, Any]:
    body = curator.ok(
        "PATCH", f"/record-reviews/{row['record_type']}/{row['record_id']}", json={"status": status, "note": note}
    )
    return {"record_type": row["record_type"], "record_ref": body.get("record_public_ref"), "status": body["status"]}


def review_caught_error(curator: Curator, report: Report, cfg: dict[str, Any]) -> dict[str, Any]:
    """Record a finding on the run's transition state, reject its transition state and kinetics, approve the rest."""
    response = arc_response(report, cfg["run"])
    calcs = response["calculation_key_refs"]
    ts_calcs = {ref for key, ref in calcs.items() if key.startswith("ts_")}
    opt = curator.ok("GET", f"/scientific/calculations/{calcs['ts_opt']}", params={"include": "output_geometries"})
    geometry_ref = opt["record"]["output_geometries"][0]["geometry_ref"]
    finding_cfg = cfg["finding"]
    finding_body = {
        "scope": "geometry",
        "subject_ref": geometry_ref,
        "kind": finding_cfg["kind"],
        "verdict": finding_cfg["verdict"],
        "rationale": f"{finding_cfg['rationale']} {DEMONSTRATION}",
        "source_calculation_ref": calcs["ts_freq"],
    }
    finding = curator.ok("POST", "/curation/structure-evidence-findings", json=finding_body)

    note_reject = f"{cfg['reject_note']} Finding {finding['finding_ref']}. {DEMONSTRATION}"
    note_approve = f"{cfg['approve_note']} {DEMONSTRATION}"
    rejected: list[dict[str, Any]] = []
    approved: list[dict[str, Any]] = []
    for row in sorted(_submission_reviews(curator, response["submission_id"]), key=lambda r: (r["record_type"], r["record_id"])):
        kind = row["record_type"]
        if kind == "artifact":
            continue
        reject = (
            kind in ("transition_state", "transition_state_entry")
            or (kind == "kinetics" and row["record_id"] in response["kinetics_ids"])
            or (kind == "calculation" and row.get("record_public_ref") in ts_calcs)
        )
        if reject:
            rejected.append(_set_review(curator, row, "rejected", note_reject))
        elif row["status"] != "approved":
            approved.append(_set_review(curator, row, "approved", note_approve))
    if not any(r["record_type"] == "kinetics" for r in rejected) or not any(
        r["record_type"] == "transition_state_entry" for r in rejected
    ):
        raise RuntimeError(f"curation: {cfg['run']}: the transition state entry and the kinetics were not both rejected")
    return {
        "run": cfg["run"],
        "submission_ref": response["submission_ref"],
        "grain": "record (PATCH /record-reviews); the submission itself stays unapproved",
        "finding_request": finding_body,
        "finding": finding,
        "rejected": rejected,
        "approved": approved,
    }


# --------------------------------------------------------------------------
# dataset release
# --------------------------------------------------------------------------

_DEPOSIT = re.compile(r"deposit (sub_[a-z2-7]{26})")


def _selection_ref(curator: Curator, report: Report, selection: dict[str, Any]) -> str:
    response = arc_response(report, selection["run"])
    kind = selection["record"]
    if kind == "kinetics":
        return record_ref(curator, "kinetics", response["kinetics_ids"][0])
    if kind == "transition_state_entry":
        return record_ref(curator, "transition_state_entry", response["transition_state_entry_id"])
    if kind == "thermo":
        mine = {record_ref(curator, "thermo", i) for i in response["thermo_ids"]}
        found = curator.ok("GET", "/scientific/thermo/search", params={"smiles": selection["smiles"], "limit": 200})
        refs = sorted(mine & {r["thermo"]["thermo_ref"] for r in found["records"]})
        if len(refs) != 1:
            raise RuntimeError(f"curation: {selection['run']} has {len(refs)} thermo records for {selection['smiles']}")
        return refs[0]
    raise RuntimeError(f"curation: unknown selection record kind {kind!r}")


def _attest(curator: Curator, report: Report, message: str, data_license: str, terms: dict[str, str]) -> dict[str, Any]:
    """Attest the deposit a refusal names, with the terms of the source that made it; raise when no source's terms apply."""
    match = _DEPOSIT.search(message)
    if match is None:
        raise RuntimeError(f"curation: a rights refusal names no deposit: {message[:300]}")
    ref = match.group(1)
    for source, text in sorted(terms.items()):
        if ref in set(report.source(source).get("submission_refs") or []):
            body = {"license": data_license, "basis": "source_terms", "source_terms": text, "note": DEMONSTRATION}
            curator.ok("POST", f"/submissions/{ref}/rights-attestations", json=body)
            return {"submission_ref": ref, "source": source}
    raise RuntimeError(f"curation: deposit {ref} has no rights basis and no source terms apply to it: {message[:300]}")


def _with_attestations(curator, report, call, data_license, terms, attested) -> Any:
    """Make ``call``; on a rights refusal, attest the deposit it names and try again."""
    for _ in range(1000):
        response = call()
        if response.status_code < 300:
            return response.json()
        text = response.text
        if response.status_code == 422 and "rights_basis_missing" in text:
            attested.append(_attest(curator, report, text, data_license, terms))
            continue
        raise RuntimeError(f"curation: release call refused ({response.status_code}): {text[:500]}")
    raise RuntimeError("curation: more than 1000 rights attestations needed")


def cut_release(curator: Curator, report: Report, cfg: dict[str, Any], env: dict[str, str]) -> dict[str, Any]:
    """Register the policy, open the release, append its selections, check custody, publish, and read the manifest."""
    policy = curator.ok("POST", "/releases/policies", json=cfg["policy"])
    create = {k: cfg[k] for k in ("tag", "title", "citation_text", "contact", "changelog_entry", "description") if k in cfg}
    create.update(curation_policy_name=cfg["policy"]["name"], curation_policy_version=cfg["policy"]["version"])
    release = curator.ok("POST", "/releases", json=create)
    handle = cfg["tag"]
    data_license = release.get("data_license") or "CC-BY-4.0"
    terms = dict(cfg.get("source_terms") or {})
    attested: list[dict[str, Any]] = []
    selections = []
    for selection in cfg["selections"]:
        ref = _selection_ref(curator, report, selection)
        body = {"record_ref": ref, "rationale": f"{selection['rationale']} {DEMONSTRATION}"}
        made = _with_attestations(
            curator, report, lambda b=body: curator.call("POST", f"/releases/{handle}/selections", json=b),
            data_license, terms, attested,
        )
        selections.append({"record": selection["record"], "record_ref": ref, "selection": made})
    custody = subprocess.run(
        [sys.executable, "scripts/ops/verify_artifact_integrity.py", "--release", release["release_ref"]],
        cwd=REPO_ROOT / "backend", env=env, capture_output=True, text=True,
    )
    if custody.returncode != 0:
        raise RuntimeError(f"curation: artifact custody check failed ({custody.returncode}): {custody.stdout[-500:]}")
    published = _with_attestations(
        curator, report, lambda: curator.call("POST", f"/releases/{handle}/publish"), data_license, terms, attested
    )
    manifest = curator.ok("GET", f"/scientific/releases/{handle}/manifest")
    return {
        "policy": policy,
        "release": published,
        "selections": selections,
        "rights_attested_by_curator": attested,
        "custody_check": custody.stdout.strip().splitlines()[-1:],
        "manifest_verified": (manifest.get("verification") or {}).get("verified"),
        "manifest_problems": (manifest.get("verification") or {}).get("problems"),
        "doi_minted": False,
    }
