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

#: A driver-``energy`` document's ``properties.return_energy`` contradicts
#: its own ``return_result`` for the same record (both are present, and
#: disagree beyond ``_ENERGY_TOLERANCE_HARTREE``). Driver ``gradient`` and
#: driver ``hessian`` never reach this check -- gradient has no independent
#: energy to contradict return_result with (it maps ``return_energy``
#: itself as the sp energy, or refuses ``sp_energy_unavailable`` if that is
#: absent -- see below), and hessian carries no ``properties.return_energy``
#: check at all today.
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

#: An ``OptimizationResult`` names no ESS software: its trajectory was
#: dropped by protocols (empty) *and* the optimizer's own input carries no
#: ``program`` keyword to fall back to. There is no field left to name the
#: program that actually computed the energies/gradients the optimizer
#: consumed.
E_ESS_PROVENANCE_UNAVAILABLE = "ess_provenance_unavailable"

#: Raw artifact bytes exceed the 50 MB cap.
E_ARTIFACT_TOO_LARGE = "artifact_too_large"

#: The artifact-search pre-check found an existing artifact with the same
#: raw-file SHA-256 and ``--allow-duplicate`` was not passed.
E_ALREADY_IMPORTED = "already_imported"

# ---------------------------------------------------------------------------
# Export (C-Q3)
# ---------------------------------------------------------------------------

#: A document whose top-level ``provenance.creator`` is exactly ``"TCKDB"``
#: -- i.e. one this adapter itself exported -- is refused on import. Without
#: this, an export could be re-imported and treated as independent evidence
#: of a second ESS job, which it is not: it is TCKDB's own stored numbers
#: reformatted. See ``exporter.py``'s ``provenance.creator="TCKDB"`` and the
#: "reimport refused" note in the C-Q3 brief.
E_TCKDB_EXPORT_REIMPORT_REFUSED = "tckdb_export_reimport_refused"

#: The calculation's ``type`` is not one this adapter can export --
#: currently only ``sp`` and ``freq`` (with a stored Hessian) are supported.
#: ``opt`` is refused deliberately: no trajectory is stored, so relabelling
#: a final energy as a single point would be a claim the record does not
#: support.
E_EXPORT_UNSUPPORTED_TYPE = "export_unsupported_type"

#: An ``sp`` calculation has no recorded ``electronic_energy_hartree`` to
#: export as ``return_result``.
E_EXPORT_ENERGY_UNAVAILABLE = "export_energy_unavailable"

#: A ``freq`` calculation carries no stored Hessian
#: (``GET /calculations/{id}/hessian`` returned 404).
E_EXPORT_HESSIAN_UNAVAILABLE = "export_hessian_unavailable"

#: The calculation has no geometry this adapter can export a molecule from.
E_EXPORT_GEOMETRY_UNAVAILABLE = "export_geometry_unavailable"

#: The geometry's owning entry carries no resolvable charge/multiplicity
#: (identity ``null`` or ambiguous across owners).
E_EXPORT_IDENTITY_UNAVAILABLE = "export_identity_unavailable"

#: The calculation carries no level of theory (method/basis) to export.
E_EXPORT_LEVEL_OF_THEORY_UNAVAILABLE = "export_level_of_theory_unavailable"

#: Exporting a ``freq`` record requires the integer ``calculation_id`` to
#: call ``GET /calculations/{id}/hessian`` (a plain, non-scientific route
#: that takes only the integer id, never a public ref). On a deployment
#: whose internal-id visibility policy hides ``calculation_id`` from the
#: scientific read, and when the caller supplied a ref rather than an int,
#: there is no way to recover it -- refused rather than guessed. Pass the
#: integer id directly (``export_calculation(client, 123)``) to avoid this.
E_EXPORT_CALCULATION_ID_UNAVAILABLE = "export_calculation_id_unavailable"
