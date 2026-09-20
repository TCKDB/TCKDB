#!/usr/bin/env python
"""Emit the backend acceptance corpus (C-Q2) from the adapter's own fixtures.

Runs the real ``tckdb_qcschema`` mapper (:func:`tckdb_qcschema.reader.read_document`
+ :func:`tckdb_qcschema.mapping.build_conformer_upload_payload`) -- the exact
code path ``tckdb-qcschema import`` uses -- over the eight route-outcome
fixtures under ``clients/python/adapters/qcschema/tests/fixtures/`` (energy,
gradient, hessian, optimization; each in QCSchema families v1 and v2), and
writes what it produces under ``backend/tests/fixtures/qcschema/<case>/``:

    document.json   byte-identical copy of the source fixture (for reference
                     and drift detection -- never re-derived)
    payload.json    the exact ConformerUploadRequest body the adapter would
                     POST to /api/v1/uploads/conformers
    artifact.json   the ArtifactIn body the adapter would POST to
                     /api/v1/calculations/{id}/artifacts (kind=ancillary,
                     filename=<case>.qcschema.json, base64 content, declared
                     sha256 + bytes)
    meta.json        expected calculation type + numeric pins (copied from
                     the source meta.json) plus the adapter/qcelemental
                     versions this corpus was generated with

Only the eight ``outcome: route`` cases are emitted -- refusal cases are the
adapter's own business (already covered by ``tests/test_corpus.py``) and
have nothing for a backend route to accept.

Intentionally NOT wired into any test run: this is a generator, invoked by
hand (see docs/research/tckdb-phase-c-implementation-plan.md, C-Q2) when the
adapter's fixtures change. Requires the adapter's own dependencies
(qcelemental==0.51.2 pinned) -- run it in the ``tckdb_qcschema_psi4`` conda
environment:

    conda run -n tckdb_qcschema_psi4 python \\
        clients/python/adapters/qcschema/scripts/emit_backend_corpus.py
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import qcelemental

from tckdb_qcschema import __version__ as ADAPTER_VERSION
from tckdb_qcschema.mapping import build_conformer_upload_payload
from tckdb_qcschema.reader import read_document
from tckdb_qcschema.uploader import sha256_bytes

ADAPTER_FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
REPO_ROOT = Path(__file__).resolve().parents[5]
BACKEND_CORPUS = REPO_ROOT / "backend" / "tests" / "fixtures" / "qcschema"

#: The eight route cases named in the plan: energy/gradient/hessian/
#: optimization crossed with QCSchema families v1/v2. Deliberately a fixed
#: list, not "every outcome:route case discovered" -- refusal cases living
#: beside them in the adapter's own corpus are the adapter's business, not
#: a backend route's, and an accidental future addition there must not
#: silently grow this corpus without a deliberate edit here.
ROUTE_CASES = (
    "energy_v1",
    "energy_v2",
    "gradient_v1",
    "gradient_v2",
    "hessian_v1",
    "hessian_v2",
    "optimization_v1",
    "optimization_v2",
)


def _emit_case(case: str) -> None:
    src_dir = ADAPTER_FIXTURES / case
    raw_bytes = (src_dir / "document.json").read_bytes()
    source_meta = json.loads((src_dir / "meta.json").read_text())

    if source_meta.get("outcome") != "route":
        raise ValueError(f"{case}: source meta.json outcome is not 'route'")

    raw_sha256 = sha256_bytes(raw_bytes)
    if raw_sha256 != source_meta["raw_sha256"]:
        raise ValueError(
            f"{case}: document.json bytes do not match the sha256 pinned in "
            f"the adapter's own meta.json -- fixture drifted."
        )

    artifact_filename = f"{case}.qcschema.json"
    smiles_arg = source_meta.get("smiles_arg", "O")

    record = read_document(raw_bytes)
    payload, _report = build_conformer_upload_payload(
        record,
        raw_bytes=raw_bytes,
        raw_artifact_filename=artifact_filename,
        raw_artifact_sha256=raw_sha256,
        declared_smiles=smiles_arg,
    )

    artifact = {
        "kind": "ancillary",
        "filename": artifact_filename,
        "content_base64": base64.b64encode(raw_bytes).decode("ascii"),
        "sha256": raw_sha256,
        "bytes": len(raw_bytes),
    }

    out_dir = BACKEND_CORPUS / case
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "document.json").write_bytes(raw_bytes)
    (out_dir / "payload.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    (out_dir / "artifact.json").write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")

    meta = {
        "expected_calculation_type": source_meta["expected"],
        "family": source_meta["family"],
        "driver": source_meta["driver"],
        "raw_sha256": raw_sha256,
        "pins": source_meta.get("pins", {}),
        "generator": {
            "adapter_version": ADAPTER_VERSION,
            "qcelemental_version": qcelemental.__version__,
            "source_fixture": f"clients/python/adapters/qcschema/tests/fixtures/{case}",
        },
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
    print(f"emitted {case} -> {out_dir}")


def main() -> int:
    if not ADAPTER_FIXTURES.exists():
        print(f"adapter fixtures directory not found: {ADAPTER_FIXTURES}", file=sys.stderr)
        return 1
    for case in ROUTE_CASES:
        _emit_case(case)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
