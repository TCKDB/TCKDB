"""A job keeps the warnings that parsing the request earned (#670).

What was wrong
--------------
``POST /jobs/<kind>`` stored ``request.model_dump(mode="json")`` and the worker
parsed that stored payload again. A warning raised while parsing the *original*
request survived only if parsing left the field alone. A banner-style software
version such as ``"Gaussian 16, Revision C.01"`` is split on first parse into
``version='16', revision='C.01'``, so the stored payload no longer looked like a
banner and the second parse had nothing to warn about: the direct route
returned ``software_release_version_is_composite`` and the job did not.

How these tests pin it
----------------------
Every job kind is given a payload whose software releases all carry a banner
version; the direct route's warnings must contain the composite warning (so an
empty comparison cannot pass), and the job result must carry the same warnings
in the same order.
"""

from __future__ import annotations

import copy
import json

import pytest

import app.workers.upload_worker as upload_worker
from app.db.models.common import UploadJobKind
from app.db.models.upload_job import UploadJob
from tests.workers.test_upload_worker_warnings import CASES, _direct, _doi_metadata  # noqa: F401

BANNER = "Gaussian 16, Revision C.01"
COMPOSITE = "software_release_version_is_composite"


def _with_banner_versions(node):
    """Rewrite every ``software_release`` in ``node`` to carry a banner version."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "software_release" and isinstance(value, dict):
                value["name"] = "gaussian"
                value["version"] = BANNER
                value.pop("revision", None)
            else:
                _with_banner_versions(value)
    elif isinstance(node, list):
        for item in node:
            _with_banner_versions(item)
    return node


#: Kinds whose request carries no software release to give a banner version.
#: ``test_the_exempt_kinds_really_carry_no_software_release`` keeps this honest.
NO_SOFTWARE_RELEASE = {UploadJobKind.reaction, UploadJobKind.network}
BANNER_KINDS = [k for k in CASES if k not in NO_SOFTWARE_RELEASE]


@pytest.mark.parametrize("kind", sorted(NO_SOFTWARE_RELEASE, key=lambda k: k.value), ids=lambda k: k.value)
def test_the_exempt_kinds_really_carry_no_software_release(kind) -> None:
    assert "software_release" not in json.dumps(CASES[kind][0]())


@pytest.mark.parametrize("kind", BANNER_KINDS, ids=lambda k: k.value)
def test_a_job_keeps_the_warning_for_a_rewritten_software_version(client, kind) -> None:
    build, direct_url, job_url, _required = CASES[kind]
    payload = _with_banner_versions(copy.deepcopy(build()))
    expected = _direct(client, direct_url, payload)
    assert COMPOSITE in {w["code"] for w in expected}, expected

    resp = client.post(job_url, json=payload)
    assert resp.status_code == 202, resp.text[:600]
    job = client._db_session.get(UploadJob, resp.json()["job_id"])
    upload_worker.run_one_job(client._db_session, job)

    read = client.get(f"/api/v1/jobs/{job.id}")
    assert read.status_code == 200, read.text[:600]
    result = read.json()["result"]
    assert "result_unavailable" not in result
    assert result["warnings"] == expected


def test_a_job_enqueued_before_the_column_still_derives_its_warnings(client) -> None:
    """``request_warnings IS NULL`` is a legacy row: derive from the payload as before."""
    build, _direct_url, job_url, required = CASES[UploadJobKind.thermo]
    resp = client.post(job_url, json=build())
    job = client._db_session.get(UploadJob, resp.json()["job_id"])
    assert job.request_warnings is not None
    job.request_warnings = None
    client._db_session.flush()

    upload_worker.run_one_job(client._db_session, job)

    assert set(required) <= {w["code"] for w in job.result["warnings"]}
