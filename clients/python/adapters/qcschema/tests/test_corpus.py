"""Parametrized corpus test over ``tests/fixtures/<case>/`` (C-Q1).

Every case is one real or hand-derived QCSchema document plus a
``meta.json`` naming its expected outcome. This is the mutation-checked
test named throughout the plan: it must fail on an empty corpus (so a
directory-discovery bug or an accidentally-emptied fixtures tree cannot
pass vacuously), and each mutation table entry in the PR is a real edit to
adapter source, landed, confirmed to turn one or more of these cases red,
then reverted.
"""

from __future__ import annotations

import json

import pytest

from tckdb_qcschema.errors import QCSchemaAdapterError
from tckdb_qcschema.mapping import build_conformer_upload_payload
from tckdb_qcschema.reader import read_document
from tckdb_qcschema.scan import build_scan_bundle_payload
from tckdb_qcschema.uploader import sha256_bytes
from tckdb_schemas.workflows.computed_species_upload import ComputedSpeciesUploadRequest
from tckdb_schemas.workflows.conformer_upload import ConformerUploadRequest

from conftest import discover_corpus_cases, load_case

CASES = discover_corpus_cases()
ROUTE_CASES = [
    case for case in CASES if load_case(case)[1]["outcome"] == "route"
]


def test_corpus_is_not_empty() -> None:
    """The corpus test must be red, never vacuously green, on an empty tree.

    If fixture discovery ever silently returns nothing (a renamed
    directory, a wiped tests/fixtures/, a broken glob), this fails loudly
    instead of letting the parametrized test below collect zero cases and
    report a hollow green.
    """
    assert CASES, "no fixtures discovered under tests/fixtures/ -- see README.md"
    # A floor, not a magic number: 8 real-Psi4 route cases (energy/gradient/
    # hessian/optimization x v1/v2) + at least a handful of refusals.
    assert len(CASES) >= 12, f"expected at least 12 corpus cases, found {len(CASES)}: {CASES}"


def _map_scan_case(case: str, raw: bytes, meta: dict):
    """A TorsionDrive case maps together with its parent optimization case."""
    parent_raw, _parent_meta = load_case(meta["parent_opt_case"])
    record = read_document(raw)
    parent_record = read_document(parent_raw)
    return build_scan_bundle_payload(
        record,
        raw_bytes=raw,
        raw_artifact_filename=f"{case}.qcschema.json",
        raw_artifact_sha256=sha256_bytes(raw),
        parent_record=parent_record,
        parent_raw_bytes=parent_raw,
        parent_artifact_filename=f"{meta['parent_opt_case']}.qcschema.json",
        parent_artifact_sha256=sha256_bytes(parent_raw),
        declared_smiles=meta.get("smiles_arg", "O"),
    )


def _assert_scan_pins(case: str, meta: dict, payload: dict) -> None:
    """Exact counts and exact values: never "not empty", never approx."""
    pins = meta["pins"]
    (conformer,) = payload["conformers"]
    assert conformer["primary_calculation"]["type"] == "opt"
    (scan_calc,) = conformer["additional_calculations"]
    assert scan_calc["type"] == "scan"
    assert scan_calc["depends_on"] == [
        {"parent_calculation_key": conformer["primary_calculation"]["key"], "role": "scan_parent"}
    ]
    scan = scan_calc["scan_result"]
    assert scan["dimension"] == pins["dimension"]
    assert scan["is_relaxed"] is True
    assert len(scan["coordinates"]) == pins["dimension"]
    assert [c["coordinate_kind"] for c in scan["coordinates"]] == ["dihedral"] * pins["dimension"]
    assert len(scan["points"]) == pins["point_count"], (
        f"{case}: {len(scan['points'])} points, expected exactly {pins['point_count']}"
    )
    for point, pinned in zip(scan["points"], pins["points"], strict=True):
        assert point["electronic_energy_hartree"] == pinned["electronic_energy_hartree"], (
            f"{case}: point {point['point_index']} energy is not the document's own number"
        )
        assert [v["coordinate_value"] for v in point["coordinate_values"]] == pinned[
            "coordinate_values"
        ], f"{case}: point {point['point_index']} coordinate values"
        assert [v["coordinate_index"] for v in point["coordinate_values"]] == list(
            range(1, pins["dimension"] + 1)
        )
    # Both raw documents travel inline, byte-exact, as ancillary artifacts.
    (artifact,) = scan_calc["artifacts"]
    assert artifact["kind"] == "ancillary" and artifact["sha256"] == meta["raw_sha256"]
    (parent_artifact,) = conformer["primary_calculation"]["artifacts"]
    assert parent_artifact["sha256"] == load_case(meta["parent_opt_case"])[1]["raw_sha256"]


@pytest.mark.parametrize("case", CASES)
def test_corpus_case(case: str) -> None:
    raw, meta = load_case(case)

    raw_sha256 = sha256_bytes(raw)
    assert raw_sha256 == meta["raw_sha256"], (
        f"{case}: document.json bytes do not match the sha256 pinned in "
        f"meta.json -- the fixture drifted without meta.json being updated."
    )

    smiles_arg = meta.get("smiles_arg", "O")

    if meta.get("parent_opt_case"):
        if meta["outcome"] == "route":
            bundle = _map_scan_case(case, raw, meta)
            ComputedSpeciesUploadRequest.model_validate(bundle.payload)
            assert bundle.point_count == meta["pins"]["point_count"]
            _assert_scan_pins(case, meta, bundle.payload)
            return
        with pytest.raises(QCSchemaAdapterError) as excinfo:
            _map_scan_case(case, raw, meta)
        assert excinfo.value.code == meta["expected"], (
            f"{case}: expected refusal code {meta['expected']!r}, got "
            f"{excinfo.value.code!r} ({excinfo.value.message})"
        )
        return

    if meta["outcome"] == "route":
        record = read_document(raw)
        payload, report = build_conformer_upload_payload(
            record,
            raw_bytes=raw,
            raw_artifact_filename=f"{case}.qcschema.json",
            raw_artifact_sha256=raw_sha256,
            declared_smiles=smiles_arg,
        )
        assert payload["calculation"]["type"] == meta["expected"], (
            f"{case}: expected calculation.type={meta['expected']!r}, "
            f"got {payload['calculation']['type']!r}"
        )
        # Re-validate independently -- build_conformer_upload_payload
        # already validates internally, but the corpus test must not take
        # that validation on faith.
        ConformerUploadRequest.model_validate(payload)
        _assert_pins(case, meta, payload)
        return

    if meta["outcome"] == "refusal":
        with pytest.raises(QCSchemaAdapterError) as excinfo:
            record = read_document(raw)
            build_conformer_upload_payload(
                record,
                raw_bytes=raw,
                raw_artifact_filename=f"{case}.qcschema.json",
                raw_artifact_sha256=raw_sha256,
                declared_smiles=smiles_arg,
            )
        assert excinfo.value.code == meta["expected"], (
            f"{case}: expected refusal code {meta['expected']!r}, got "
            f"{excinfo.value.code!r} ({excinfo.value.message})"
        )
        return

    pytest.fail(f"{case}: unknown meta['outcome']={meta['outcome']!r}")


def _assert_pins(case: str, meta: dict, payload: dict) -> None:
    """Assert the numeric/text pins named in ``meta["pins"]``, when present.

    Makes the corpus test value-aware, not merely shape-aware: without
    this, a mapped payload could have the right ``calculation.type`` and
    still carry a silently wrong number (a shifted unit constant, a
    mis-packed Hessian entry) for every real-Psi4 route case, and nothing
    above would notice. Pins are optional per-case (``meta.get("pins")``)
    so hand-derived and refusal fixtures need not carry any.
    """
    pins = meta.get("pins")
    if not pins:
        return
    calc = payload["calculation"]

    if "electronic_energy_hartree" in pins:
        assert calc["sp_result"]["electronic_energy_hartree"] == pytest.approx(
            pins["electronic_energy_hartree"], abs=1e-12
        ), f"{case}: sp_result.electronic_energy_hartree pin mismatch"

    if "final_energy_hartree" in pins:
        assert calc["opt_result"]["final_energy_hartree"] == pytest.approx(
            pins["final_energy_hartree"], abs=1e-12
        ), f"{case}: opt_result.final_energy_hartree pin mismatch"

    if "first_xyz_line" in pins:
        xyz = calc["input_geometries"][0]["xyz_text"]
        first_line = xyz.splitlines()[2]  # line 0 = atom count, line 1 = blank
        assert first_line == pins["first_xyz_line"], (
            f"{case}: input_geometries[0] first atom line pin mismatch: "
            f"{first_line!r} != {pins['first_xyz_line']!r}"
        )

    if "output_first_xyz_line" in pins:
        xyz = calc["output_geometries"][0]["geometry"]["xyz_text"]
        first_line = xyz.splitlines()[2]
        assert first_line == pins["output_first_xyz_line"], (
            f"{case}: output_geometries[0] first atom line pin mismatch: "
            f"{first_line!r} != {pins['output_first_xyz_line']!r}"
        )

    if "hessian_length" in pins:
        lower_triangle = calc["hessian"]["lower_triangle_hartree_bohr2"]
        assert len(lower_triangle) == pins["hessian_length"], (
            f"{case}: hessian lower-triangle length pin mismatch"
        )
        assert lower_triangle[0] == pytest.approx(
            pins["hessian_first_value"], abs=1e-15
        ), f"{case}: hessian lower-triangle first value pin mismatch"


@pytest.mark.parametrize("case", ROUTE_CASES)
def test_route_case_payload_is_deterministic(case: str) -> None:
    """Two independent builds of the same document produce byte-identical JSON.

    Regression guard for a payload that is secretly a function of *when*
    it was built rather than *what* it was built from (the wall-clock
    ``parameters_extracted_at`` bug this replaces -- see
    ``tests/test_uploader.py``'s partial-run-recovery test for the
    idempotency-conflict consequence). The backend hashes the whole
    canonical request body per idempotency key
    (``backend/app/api/idempotency.py``), so two builds of the identical
    document must be canonically identical or a same-key rerun is refused
    ``idempotency_conflict`` instead of replaying.
    """
    raw, meta = load_case(case)
    raw_sha256 = sha256_bytes(raw)
    smiles_arg = meta.get("smiles_arg", "O")

    def _build() -> dict:
        if meta.get("parent_opt_case"):
            return _map_scan_case(case, raw, meta).payload
        record = read_document(raw)  # fresh QCRecord each time, not shared state
        payload, _report = build_conformer_upload_payload(
            record,
            raw_bytes=raw,
            raw_artifact_filename=f"{case}.qcschema.json",
            raw_artifact_sha256=raw_sha256,
            declared_smiles=smiles_arg,
        )
        return payload

    first = json.dumps(_build(), sort_keys=True, separators=(",", ":"))
    second = json.dumps(_build(), sort_keys=True, separators=(",", ":"))
    assert first == second, f"{case}: two builds of the same document diverged"
