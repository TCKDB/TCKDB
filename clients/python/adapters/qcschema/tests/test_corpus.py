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

import pytest

from tckdb_qcschema.errors import QCSchemaAdapterError
from tckdb_qcschema.mapping import build_conformer_upload_payload
from tckdb_qcschema.reader import read_document
from tckdb_qcschema.uploader import sha256_bytes
from tckdb_schemas.workflows.conformer_upload import ConformerUploadRequest

from conftest import discover_corpus_cases, load_case

CASES = discover_corpus_cases()


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


@pytest.mark.parametrize("case", CASES)
def test_corpus_case(case: str) -> None:
    raw, meta = load_case(case)

    raw_sha256 = sha256_bytes(raw)
    assert raw_sha256 == meta["raw_sha256"], (
        f"{case}: document.json bytes do not match the sha256 pinned in "
        f"meta.json -- the fixture drifted without meta.json being updated."
    )

    smiles_arg = meta.get("smiles_arg", "O")

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
