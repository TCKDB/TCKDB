"""Coded refusals for the QCSchema adapter.

Every refusal the adapter can produce carries a short machine-readable
``code`` (asserted by the corpus test's ``meta.json`` sidecars) alongside a
human-readable ``message``. Nothing is ever silently dropped or guessed --
see the module docstring in :mod:`tckdb_qcschema.reader` for the "reject,
don't guess" rule this exists to enforce.
"""

from __future__ import annotations

from typing import Any


class QCSchemaAdapterError(Exception):
    """A refusal with a stable ``code`` a caller (or a test) can match on."""

    def __init__(self, code: str, message: str, **context: Any) -> None:
        self.code = code
        self.message = message
        self.context = context
        super().__init__(f"[{code}] {message}")

    def __repr__(self) -> str:  # pragma: no cover - diagnostic only
        return f"QCSchemaAdapterError(code={self.code!r}, message={self.message!r})"


#: Document does not carry a JSON object, or fails to validate against
#: *either* Result class in its own dispatched family.
E_DOCUMENT_INVALID = "document_invalid"

#: ``success`` is explicitly ``False`` on the raw document -- either a
#: genuine ``FailedOperation`` (the real shape qcengine emits for a job
#: that never produced a Result) or a schema-valid Result document whose
#: own ``success`` field is false. Checked structurally before any
#: family-specific class validation is attempted, so both shapes are
#: caught uniformly. Nothing is posted.
E_JOB_FAILED = "job_failed"

#: The document validated against a family's Result class, but its
#: ``schema_name``/``schema_version`` pair does not match that family's
#: declared pair. The version integer is *never* used to select the
#: family in the first place -- see the "version trap" note in
#: ``reader.py``.
E_SCHEMA_VERSION_FAMILY_MISMATCH = "schema_version_family_mismatch"

#: A driver-gradient or driver-hessian ``properties.return_energy`` value
#: contradicts an independently supplied energy for the same record.
E_ENERGY_CONTRADICTION = "energy_contradiction"

#: A driver-gradient document has no ``properties.return_energy`` to map
#: as the record's ``sp`` energy.
E_SP_ENERGY_UNAVAILABLE = "sp_energy_unavailable"

#: A declared atomic mass does not match qcelemental's tabulated mass for
#: the declared nuclide (symbol + mass number) within 1e-6 amu.
E_NONSTANDARD_MASS = "nonstandard_mass"

#: One or more atoms are ``real=false`` (a ghost/dummy atom for
#: counterpoise or similar procedures TCKDB does not model).
E_GHOST_ATOMS_UNSUPPORTED = "ghost_atoms_unsupported"

#: The molecule carries more than one fragment.
E_MULTI_FRAGMENT_UNSUPPORTED = "multi_fragment_unsupported"

#: Neither ``--smiles`` nor ``identifiers.smiles`` supplied a graph
#: identity, and TCKDB never perceives one from 3D coordinates.
E_IDENTITY_UNAVAILABLE = "identity_unavailable"

#: ``molecular_charge`` or ``molecular_multiplicity`` is not integral.
#: QCSchema types both as float; TCKDB's species identity requires int.
E_NON_INTEGER_IDENTITY = "non_integer_identity"

#: The recovered Hessian is not numerically symmetric within tolerance.
E_HESSIAN_ASYMMETRIC = "hessian_asymmetric"

#: Raw artifact bytes exceed the 50 MB cap.
E_ARTIFACT_TOO_LARGE = "artifact_too_large"

#: The artifact-search pre-check found an existing artifact with the same
#: raw-file SHA-256 and ``--allow-duplicate`` was not passed.
E_ALREADY_IMPORTED = "already_imported"
