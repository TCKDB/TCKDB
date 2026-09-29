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

#: The freq path's Hessian read (``GET /calculations/{id}/hessian``)
#: reached this deployment's legacy-read auth gate and was rejected (401/403)
#: rather than returning a genuine 404. Distinct from
#: ``export_hessian_unavailable``: that code means the calculation has no
#: stored Hessian at all; this one means the read was never actually
#: evaluated because the caller carried no (or an invalid) credential for a
#: hosted deployment with ``legacy_reads_require_auth`` enabled. Configure
#: an API key on the client and retry rather than treating this as "no
#: Hessian".
E_EXPORT_HESSIAN_UNAUTHORIZED = "export_hessian_unauthorized"

#: The per-atom isotope read this adapter needs to export honest
#: ``mass_numbers`` could not be completed. ``export_calculation`` reads
#: atom-resolved isotopes from the legacy, internal-id ``GET /geometries``
#: surface (``GeometryAtomRead.isotope_mass_number``) -- the scientific
#: geometry read (``GET /scientific/geometries/{handle}``) carries no such
#: field at all, see the module docstring's "Isotopes" section. This code
#: covers every way that legacy read can fail to produce a usable row: the
#: deployment's legacy-read auth gate rejected it (401/403, e.g. a hosted
#: deployment with no API key configured), the geometry id/hash was not
#: found (404, or a 200 with an empty ``items`` list for a ``geom_hash``
#: query), or the call raised for any other reason. Never worked around by
#: silently defaulting every atom to its standard nuclide -- that is
#: exactly the guess this code exists to refuse instead of making.
E_EXPORT_ISOTOPES_UNAVAILABLE = "export_isotopes_unavailable"

#: The legacy per-atom isotope read (see ``export_isotopes_unavailable``)
#: returned atoms that do not line up, element-for-element in
#: ``atom_index`` order, with the atoms already read from the scientific
#: geometry route that supplies the exported molecule's coordinates. Two
#: reads of what should be the identical stored geometry disagreeing is
#: refused rather than silently exporting mismatched coordinates and
#: isotopes.
E_EXPORT_GEOMETRY_MISMATCH = "export_geometry_mismatch"

#: The mapping report does not account for every field of the imported
#: document (issue #573): a field this adapter's own mapping branch must
#: classify (``return_result``, ``properties.return_energy``, a mapped
#: molecule's geometry, ...) was left unclassified, or one field landed in
#: more than one bucket. This is a defect in the adapter, never in the
#: document, and it is raised instead of posting a deposit whose report
#: would silently omit what it dropped. See
#: :mod:`tckdb_qcschema.report_coverage`.
E_MAPPING_REPORT_INCOMPLETE = "mapping_report_incomplete"

# ---------------------------------------------------------------------------
# Scans: TorsionDrive import and export
# ---------------------------------------------------------------------------

#: A ``TorsionDriveResult`` was handed to the import without the
#: optimization it was driven from. TCKDB stores a scan only as a
#: calculation attached to a conformer, and a conformer is anchored by an
#: unconstrained ``opt`` (``ConformerInBundle.primary_calculation`` must be
#: ``opt``). The torsion drive holds only *constrained* optimizations, and
#: none of them is that minimum. Pass the optimization document with
#: ``--parent-opt``; nothing is posted without it.
E_SCAN_PARENT_OPT_REQUIRED = "scan_parent_opt_required"

#: The ``--parent-opt`` document is not an ``OptimizationResult``, or it is
#: not the optimization this torsion drive started from: the atoms differ,
#: the charge or multiplicity differs, or no ``initial_molecule`` of the
#: drive matches the optimization's ``final_molecule`` geometry.
E_SCAN_PARENT_MISMATCH = "scan_parent_mismatch"

#: The drive's grid does not hold together: a grid key that is not one
#: number per dihedral, ``grid_spacing`` or ``dihedral_ranges`` whose
#: length is not the number of dihedrals, a grid point with a final
#: molecule but no energy (or the reverse), or a drive with no grid points.
E_SCAN_GRID_INVALID = "scan_grid_invalid"

#: A grid point's final geometry does not hold the dihedral its grid key
#: says it does (recomputed from the Cartesian coordinates, compared modulo
#: 360 degrees). TCKDB stores the coordinate value as the coordinate
#: itself (ADR 0020), so a key that disagrees with its own geometry cannot
#: be stored as that value.
E_SCAN_COORDINATE_GEOMETRY_MISMATCH = "scan_coordinate_geometry_mismatch"

#: The drive's optimizer keywords carry extra ``constraints`` (frozen or
#: set coordinates beyond the scanned dihedrals). They change which surface
#: was scanned, and this adapter does not translate optimizer-specific
#: constraint syntax into TCKDB constraint rows. Refused rather than stored
#: as if the drive were unconstrained.
E_SCAN_EXTRA_CONSTRAINTS_UNSUPPORTED = "scan_extra_constraints_unsupported"

#: The stored scan scanned something other than proper dihedrals (a bond,
#: an angle, or an improper torsion). QCSchema's only scan model is the
#: TorsionDrive, whose coordinates are dihedrals by definition; writing a
#: bond scan into one would mislabel it.
E_EXPORT_SCAN_NOT_TORSION_DRIVE = "export_scan_not_torsion_drive"

#: The stored scan is rigid (``is_relaxed`` false) or does not say
#: (``null``). A TorsionDrive is a constrained optimization at every grid
#: point; exporting a rigid scan as one would claim optimizations that
#: never ran.
E_EXPORT_SCAN_NOT_RELAXED = "export_scan_not_relaxed"

#: A stored scan point cannot become a TorsionDrive grid point: it has no
#: energy, no geometry, not exactly one value per coordinate, or two points
#: land on the same grid point.
E_EXPORT_SCAN_POINT_INCOMPLETE = "export_scan_point_incomplete"

#: A stored coordinate value, or the coordinate's grid spacing, is not a
#: whole number of degrees. TorsionDrive grid points and ``grid_spacing``
#: are integers (qcelemental types ``grid_spacing`` as ``List[int]``;
#: qcengine sets each constraint with ``int(angle)``); rounding a stored
#: value to fit would change it.
E_EXPORT_SCAN_GRID_NOT_INTEGRAL = "export_scan_grid_not_integral"

#: A stored coordinate value disagrees with the dihedral recomputed from
#: that point's own stored geometry. ADR 0020 fixes the value as the
#: coordinate itself; the scans deposited before it hold a sweep relative
#: to the first point instead, and exporting those as TorsionDrive grid
#: angles would label every point with the wrong angle.
E_EXPORT_SCAN_COORDINATE_NONCONFORMING = "export_scan_coordinate_nonconforming"
