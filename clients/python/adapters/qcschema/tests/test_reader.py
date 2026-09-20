"""Family dispatch and validation tests (C-Q1)."""

from __future__ import annotations

import json

import pytest
import qcelemental.models.v1 as qcel_v1
import qcelemental.models.v2 as qcel_v2

from tckdb_qcschema.errors import (
    E_JOB_FAILED,
    E_SCHEMA_VERSION_FAMILY_MISMATCH,
    E_TCKDB_EXPORT_REIMPORT_REFUSED,
)
from tckdb_qcschema.reader import QCRecord, read_document
from tckdb_qcschema.errors import QCSchemaAdapterError

from conftest import load_case


@pytest.mark.parametrize(
    "case,expected_family,expected_kind",
    [
        ("energy_v1", "v1", "atomic"),
        ("energy_v2", "v2", "atomic"),
        ("optimization_v1", "v1", "optimization"),
        ("optimization_v2", "v2", "optimization"),
    ],
)
def test_family_and_record_kind_dispatch(case, expected_family, expected_kind):
    raw, _meta = load_case(case)
    record = read_document(raw)
    assert isinstance(record, QCRecord)
    assert record.family == expected_family
    assert record.record_kind == expected_kind


def test_v1_document_validates_only_with_v1_class():
    raw, _meta = load_case("energy_v1")
    document = json.loads(raw)
    # Sanity: this really is a v1-shaped document (no input_data).
    assert "input_data" not in document

    validated = qcel_v1.AtomicResult(**document)
    assert validated.schema_name == "qcschema_output"

    # It must NOT be constructible as a v2 AtomicResult -- v2 requires
    # input_data, which this document lacks.
    with pytest.raises(Exception):
        qcel_v2.AtomicResult.model_validate(document)


def test_v2_document_validates_only_with_v2_class():
    raw, _meta = load_case("energy_v2")
    document = json.loads(raw)
    assert "input_data" in document

    validated = qcel_v2.AtomicResult.model_validate(document)
    assert validated.schema_name == "qcschema_atomic_result"

    # v1's AtomicResult has no `input_data` field at all; pydantic.v1
    # models ignore unknown keys by default rather than refusing them, but
    # the *required* v1 fields this document lacks (top-level driver,
    # model) make v1 construction fail.
    with pytest.raises(Exception):
        qcel_v1.AtomicResult(**document)


def test_failed_job_refuses_job_failed():
    raw, meta = load_case("failed_job")
    with pytest.raises(QCSchemaAdapterError) as excinfo:
        read_document(raw)
    assert excinfo.value.code == E_JOB_FAILED
    assert excinfo.value.code == meta["expected"]


def test_family_drift_refuses_schema_version_family_mismatch():
    raw, meta = load_case("family_drift")
    with pytest.raises(QCSchemaAdapterError) as excinfo:
        read_document(raw)
    assert excinfo.value.code == E_SCHEMA_VERSION_FAMILY_MISMATCH
    assert excinfo.value.code == meta["expected"]


def test_non_json_document_is_refused():
    with pytest.raises(QCSchemaAdapterError) as excinfo:
        read_document(b"not json at all")
    assert excinfo.value.code == "document_invalid"


def test_json_array_top_level_is_refused():
    with pytest.raises(QCSchemaAdapterError) as excinfo:
        read_document(b"[1, 2, 3]")
    assert excinfo.value.code == "document_invalid"


@pytest.mark.parametrize("case", ["energy_v1", "energy_v2", "hessian_v1", "hessian_v2"])
def test_tckdb_export_is_refused_on_reimport(case):
    """A document whose top-level provenance.creator=='TCKDB' is refused (C-Q3).

    Mutation this catches: dropping the ``creator == "TCKDB"`` check from
    ``reader.py`` (or its equivalent, e.g. weakening it to a substring
    match that a real ESS name would never trip) turns this red -- see
    ``exporter.py``'s ``provenance.creator="TCKDB"`` and the "reimport
    refused" requirement in the C-Q3 brief.
    """
    raw, _meta = load_case(case)
    document = json.loads(raw)
    document["provenance"] = {
        "creator": "TCKDB",
        "version": "1.0",
        "routine": "tckdb-qcschema export/0.3.0",
    }
    mutated = json.dumps(document).encode("utf-8")

    with pytest.raises(QCSchemaAdapterError) as excinfo:
        read_document(mutated)
    assert excinfo.value.code == E_TCKDB_EXPORT_REIMPORT_REFUSED


def test_export_reimport_refusal_reads_only_the_top_level_provenance():
    """``input_data.provenance`` (v2's original-request provenance) must
    never trip this check -- only the top-level ``provenance``, which is
    the *result's* provenance and what ``exporter.py`` actually sets.
    """
    raw, _meta = load_case("energy_v2")
    document = json.loads(raw)
    assert document["provenance"]["creator"] != "TCKDB"
    document["input_data"]["provenance"] = {"creator": "TCKDB", "version": "1.0"}
    mutated = json.dumps(document).encode("utf-8")

    # Does not raise tckdb_export_reimport_refused -- the document is a
    # perfectly ordinary, real-ESS-provenanced result and must import as
    # such.
    record = read_document(mutated)
    assert record.family == "v2"


def test_canonical_json_is_stable_across_key_order_and_whitespace():
    from tckdb_qcschema.reader import canonicalize

    a = json.dumps({"b": 1, "a": 2}).encode()
    b = json.dumps({"a": 2, "b": 1}, indent=4).encode()
    _canon_a, sha_a = canonicalize(json.loads(a))
    _canon_b, sha_b = canonicalize(json.loads(b))
    assert sha_a == sha_b
