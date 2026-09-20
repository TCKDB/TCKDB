"""Family dispatch and validation for MolSSI QCSchema documents (C-Q1).

A QCSchema document is either "family v1" or "family v2" -- two disjoint
sets of pydantic classes qcelemental ships as ``qcelemental.models.v1`` and
``qcelemental.models.v2``. They serialise the *same physical result* very
differently (v2 nests the original request under ``input_data``; v1 keeps
``driver``/``model``/``keywords`` at the top level), and each family has its
own paired ``schema_name``/``schema_version`` values. Pinned against
qcelemental==0.51.2, measured directly against the installed classes:

    =========================== ============================ ==============
    Class                        schema_name                  schema_version
    =========================== ============================ ==============
    v1 AtomicResult               qcschema_output               1
    v1 OptimizationResult         qcschema_optimization_output  1
    v2 AtomicResult                qcschema_atomic_result        2
    v2 OptimizationResult          qcschema_optimization_result  2
    =========================== ============================ ==============

The version trap
-----------------
``schema_version`` is an integer a producer sets, and nothing stops a
document from carrying ``schema_version: 1`` while otherwise being shaped
like a v2 document (or vice versa). If family were selected from that
integer, a document could smuggle itself past validation for the *other*
family's class, which types the reader would then apply the wrong field
paths to (v1's top-level ``driver`` vs v2's ``input_data.specification.driver``,
for instance) -- silently misreading real science.

So family is dispatched from *shape*, once, before any class is chosen:
family is v2 **iff** the top-level document carries ``input_data`` --
that key exists on nothing in family v1 and is required (non-optional) on
every Result class in family v2. Once dispatched, the document's own
``schema_name``/``schema_version`` (when present) must be a valid pair
*for that family* -- checked structurally, on the raw dict, before any
class is even tried -- or the document is refused with
``schema_version_family_mismatch``. A document that claims v2 shape (has
``input_data``) but declares ``schema_version: 1`` inside it is exactly
the case this catches. Only once that gate has passed is the document
validated with that family's own ``AtomicResult``/``OptimizationResult``
class -- it is never retried against the other family.

(This structural pre-check is load-bearing, not merely a nicer error
message: qcelemental itself types both fields as an exact ``Literal`` per
the table above, so skipping straight to class validation would turn a
genuinely mismatched pair into an opaque generic validation failure
instead of this specific code -- see the comment in :func:`read_document`.)

Failed jobs
-----------
Real qcengine failures are not a Result document with ``success=false`` --
they are a *different* top-level schema, ``FailedOperation``, which has no
``schema_name``/``schema_version``/``driver``/``model`` at all (only ``id``,
``input_data``, ``success``, ``error``, ``extras``; measured by running a
genuine nonexistent-basis Psi4 job through qcengine, see
``tests/fixtures/README.md``). A schema-valid Result document whose own
``success`` field happens to be ``false`` is also possible in principle.
Both are real "the job did not produce usable evidence" states, so
``success`` is read directly off the parsed JSON dict *before* any
family-specific class validation is attempted, and ``False`` there refuses
``job_failed`` immediately, uniformly, for either shape. Only once that
gate has passed does the reader attempt strict class validation.

TCKDB exports (C-Q3)
---------------------
:mod:`tckdb_qcschema.exporter` turns a stored calculation back into a
QCSchema document with top-level ``provenance.creator="TCKDB"``. Such a
document is refused with ``tckdb_export_reimport_refused``, checked the
same way and at the same point as the ``job_failed`` gate above -- on the
raw dict, before family dispatch -- so TCKDB's own reformatted numbers can
never be re-imported and read back as a second, independent ESS job.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal

import qcelemental.models.v1 as qcel_v1
import qcelemental.models.v2 as qcel_v2
from pydantic import ValidationError as PydanticV2ValidationError

try:  # v1 classes are pydantic.v1 (bundled compat layer) models.
    from pydantic.v1 import ValidationError as PydanticV1ValidationError
except ImportError:  # pragma: no cover - defensive; pydantic<2 not supported here
    from pydantic import ValidationError as PydanticV1ValidationError

from .errors import (
    E_DOCUMENT_INVALID,
    E_JOB_FAILED,
    E_SCHEMA_VERSION_FAMILY_MISMATCH,
    E_TCKDB_EXPORT_REIMPORT_REFUSED,
    QCSchemaAdapterError,
)

Family = Literal["v1", "v2"]
RecordKind = Literal["atomic", "optimization"]

#: Declared (schema_name, schema_version) pair per (family, record_kind),
#: measured directly against the pinned qcelemental==0.51.2 classes -- see
#: the module docstring's table. Never derived from the integer alone.
_DECLARED_PAIRS: dict[tuple[Family, RecordKind], tuple[str, int]] = {
    ("v1", "atomic"): ("qcschema_output", 1),
    ("v1", "optimization"): ("qcschema_optimization_output", 1),
    ("v2", "atomic"): ("qcschema_atomic_result", 2),
    ("v2", "optimization"): ("qcschema_optimization_result", 2),
}

#: Both record kinds within one family share one ``schema_version`` integer.
_FAMILY_VERSION: dict[Family, int] = {"v1": 1, "v2": 2}

#: The two ``schema_name`` values a document in this family may legally
#: declare (one per record kind).
_FAMILY_VALID_NAMES: dict[Family, frozenset[str]] = {
    family: frozenset(
        {_DECLARED_PAIRS[(family, "atomic")][0], _DECLARED_PAIRS[(family, "optimization")][0]}
    )
    for family in ("v1", "v2")
}

_FAMILY_MODULES: dict[Family, Any] = {"v1": qcel_v1, "v2": qcel_v2}

_VALIDATION_ERRORS = (PydanticV1ValidationError, PydanticV2ValidationError)


@dataclass(frozen=True)
class QCRecord:
    """One successfully validated, family-and-kind-resolved QCSchema record.

    :param family: ``"v1"`` or ``"v2"`` -- which qcelemental namespace
        validated this document.
    :param record_kind: ``"atomic"`` (an ``AtomicResult``) or
        ``"optimization"`` (an ``OptimizationResult``).
    :param schema_name: The document's own declared ``schema_name``.
    :param schema_version: The document's own declared ``schema_version``.
    :param result: The validated qcelemental model instance (family- and
        kind-specific type; callers narrow via ``record_kind``/``family``).
    :param raw_document: The original parsed JSON as a plain dict, exactly
        as it appeared on disk (key order and formatting are not
        preserved by dict, but no value is altered).
    :param canonical_json: Deterministic re-serialisation of
        ``raw_document`` (sorted keys, no whitespace) -- the input to the
        idempotency-key and duplicate-detection hashes, so a
        re-serialised copy of the same logical document hashes the same.
    :param canonical_sha256: ``sha256(canonical_json).hexdigest()``.
    """

    family: Family
    record_kind: RecordKind
    schema_name: str
    schema_version: int
    result: Any
    raw_document: dict
    canonical_json: bytes
    canonical_sha256: str


def canonicalize(document: dict) -> tuple[bytes, str]:
    """Deterministic (sorted-key, no-whitespace) JSON bytes and their sha256.

    Two byte-different re-serialisations of the same logical document
    (different key order, different whitespace) canonicalise identically,
    which is what makes the idempotency keys in ``uploader.py`` stable
    across a re-serialised copy.
    """
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return canonical, hashlib.sha256(canonical).hexdigest()


def _family_of(document: dict) -> Family:
    """THE version trap, resolved: shape only, never the integer."""
    return "v2" if "input_data" in document else "v1"


def _try_validate(model_cls: Any, document: dict) -> Any | None:
    try:
        if hasattr(model_cls, "model_validate"):
            return model_cls.model_validate(document)
        return model_cls.parse_obj(document)  # pydantic.v1 API
    except _VALIDATION_ERRORS:
        return None


def read_document(raw_bytes: bytes) -> QCRecord:
    """Parse, dispatch, and strictly validate one QCSchema document.

    :param raw_bytes: The exact bytes of the uploaded ``.json`` file.
    :raises QCSchemaAdapterError: ``document_invalid`` (not a JSON
        object, or validates against neither Result class in its
        dispatched family), ``job_failed`` (``success`` is ``False`` on
        the raw document), or ``schema_version_family_mismatch`` (the
        validated document's own declared pair does not match its
        dispatched family).
    """
    try:
        document = json.loads(raw_bytes)
    except json.JSONDecodeError as exc:
        raise QCSchemaAdapterError(
            E_DOCUMENT_INVALID, f"not valid JSON: {exc}"
        ) from exc
    if not isinstance(document, dict):
        raise QCSchemaAdapterError(
            E_DOCUMENT_INVALID,
            f"top-level document must be a JSON object, got {type(document).__name__}",
        )

    # Checked on the raw dict, before any class validation is attempted,
    # so both real failure shapes (FailedOperation and a schema-valid
    # Result with success=false) are caught uniformly. See the module
    # docstring's "Failed jobs" section.
    if document.get("success") is False:
        raise QCSchemaAdapterError(
            E_JOB_FAILED,
            "document declares success=false; nothing is mapped or posted.",
        )

    # A document this adapter itself exported (see exporter.py) carries
    # provenance.creator="TCKDB" at the top level, on every family and
    # record kind (v1 and v2 both type a top-level ``provenance`` on
    # AtomicResult/OptimizationResult; v2's is distinct from
    # ``input_data.provenance``, which is the *original* request's, not
    # what this check reads). Refused before any family dispatch or class
    # validation, on the raw dict, so an export can never be re-imported
    # and read back as independent evidence of a second ESS job -- it is
    # TCKDB's own stored numbers reformatted, not a new document.
    exported_creator = (document.get("provenance") or {}).get("creator")
    if exported_creator == "TCKDB":
        raise QCSchemaAdapterError(
            E_TCKDB_EXPORT_REIMPORT_REFUSED,
            "document declares provenance.creator='TCKDB': this is a "
            "document tckdb-qcschema itself exported, not independent "
            "evidence. Re-importing it would let TCKDB's own stored "
            "numbers return as a second, apparently-independent "
            "calculation of the same result. Refused; nothing is mapped "
            "or posted.",
        )

    family = _family_of(document)
    module = _FAMILY_MODULES[family]

    # Structural pair check, on the raw dict, *before* class validation is
    # attempted. This is load-bearing, not cosmetic: qcelemental types both
    # fields as an exact ``Literal`` (measured against 0.51.2 -- e.g. v2
    # AtomicResult's schema_version is ``Literal[2]``), so a document
    # dispatched to family v2 by shape but declaring schema_version=1 would
    # otherwise simply fail *class* validation with an opaque pydantic
    # error (document_invalid) rather than the specific, actionable
    # schema_version_family_mismatch this refusal exists to name. Absent
    # fields are not flagged here -- qcelemental fills its own correct
    # default when a field is omitted, so only a *present and wrong* value
    # is a mismatch.
    declared_version = document.get("schema_version")
    declared_name = document.get("schema_name")
    version_wrong = declared_version is not None and declared_version != _FAMILY_VERSION[family]
    name_wrong = declared_name is not None and declared_name not in _FAMILY_VALID_NAMES[family]
    if version_wrong or name_wrong:
        raise QCSchemaAdapterError(
            E_SCHEMA_VERSION_FAMILY_MISMATCH,
            f"document dispatched to family {family!r} (by the presence "
            f"of top-level 'input_data') but declares "
            f"schema_name={declared_name!r}, schema_version={declared_version!r}, "
            f"which is not a valid pair for that family "
            f"(valid schema_name values: {sorted(_FAMILY_VALID_NAMES[family])}, "
            f"schema_version must be {_FAMILY_VERSION[family]}). The family "
            f"is chosen from document shape, never from this integer, and "
            f"a document must be internally consistent with the family its "
            f"shape dispatched it to.",
            family=family,
            declared_name=declared_name,
            declared_version=declared_version,
        )

    result = _try_validate(module.AtomicResult, document)
    record_kind: RecordKind | None = "atomic" if result is not None else None
    if result is None:
        result = _try_validate(module.OptimizationResult, document)
        record_kind = "optimization" if result is not None else None

    if result is None or record_kind is None:
        raise QCSchemaAdapterError(
            E_DOCUMENT_INVALID,
            f"document did not validate as either AtomicResult or "
            f"OptimizationResult in family {family!r} (dispatched by the "
            f"presence of top-level 'input_data').",
        )

    # A second, belt-and-braces success check on the *validated* model,
    # in case a class default ever papered over an absent field the raw
    # dict genuinely lacked (defensive; the raw-dict check above already
    # covers every real document this adapter has been given).
    if getattr(result, "success", True) is False:
        raise QCSchemaAdapterError(
            E_JOB_FAILED,
            "validated document has success=false; nothing is mapped or posted.",
        )

    # Defense-in-depth, not the primary gate (that is the pre-validation
    # structural check above, which is what actually fires for a
    # mismatched document -- qcelemental's own Literal-typed fields make
    # this branch effectively unreachable today, but it costs nothing to
    # keep as a second line of defence against a future qcelemental
    # release that loosens the constraint).
    schema_name = getattr(result, "schema_name", None)
    schema_version = getattr(result, "schema_version", None)
    declared_name, declared_version = _DECLARED_PAIRS[(family, record_kind)]
    if (schema_name, schema_version) != (declared_name, declared_version):
        raise QCSchemaAdapterError(  # pragma: no cover - see comment above
            E_SCHEMA_VERSION_FAMILY_MISMATCH,
            f"document dispatched to family {family!r} record_kind "
            f"{record_kind!r} (declared pair "
            f"{(declared_name, declared_version)!r}) but itself declares "
            f"schema_name={schema_name!r}, schema_version={schema_version!r}. "
            f"The family is chosen from document shape, never from this "
            f"integer, and a document must be internally consistent with "
            f"the family its shape dispatched it to.",
            expected=(declared_name, declared_version),
            actual=(schema_name, schema_version),
        )

    canonical_json, canonical_sha256 = canonicalize(document)
    return QCRecord(
        family=family,
        record_kind=record_kind,
        schema_name=schema_name,
        schema_version=schema_version,
        result=result,
        raw_document=document,
        canonical_json=canonical_json,
        canonical_sha256=canonical_sha256,
    )


__all__ = ["QCRecord", "Family", "RecordKind", "canonicalize", "read_document"]
