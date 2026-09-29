"""Stored ``scan`` calculation -> QCSchema v2 ``TorsionDriveResult``.

The inverse of :mod:`tckdb_qcschema.scan`. :func:`export_scan` reads a
stored scan through the public read API only (``tckdb-client``): the
calculation detail (level of theory, releases, owner, input geometries,
parameter observations), the paginated
``GET /scientific/calculations/{ref}/scan`` points, each point's geometry,
and each geometry's per-atom isotopes from the legacy ``GET /geometries``
surface (the same honesty rule :mod:`tckdb_qcschema.exporter` documents).
It never re-derives a stored number.

What exports, and what is refused
---------------------------------
QCSchema's only scan model is the TorsionDrive, so a stored scan exports
only when it *is* one:

* every coordinate is a proper ``dihedral`` -- a ``bond``, ``angle`` or
  ``improper`` scan is refused ``export_scan_not_torsion_drive`` rather
  than mislabelled;
* ``is_relaxed`` is true -- a rigid (or unstated) scan is refused
  ``export_scan_not_relaxed``: a TorsionDrive is a constrained optimization
  at every grid point;
* every point has an energy, a geometry and exactly one value per
  coordinate (``export_scan_point_incomplete``);
* every coordinate value and grid spacing is a whole number of degrees
  (``export_scan_grid_not_integral``) -- TorsionDrive grids are integer;
* every coordinate value is the dihedral its own stored geometry holds,
  modulo 360 degrees, within
  :data:`tckdb_qcschema.scan.DIHEDRAL_TOLERANCE_DEGREES`
  (``export_scan_coordinate_nonconforming``). This is ADR 0020's contract;
  the scans deposited before it store a sweep relative to the first point
  and are refused here rather than exported under the wrong angles. The ADR
  0020 absolute-angle migration would fix their values, but not make them
  exportable: their angles start at the measured dihedral (not a whole
  degree) and a full 360-degree sweep repeats its first grid point, so they
  would still be refused, as ``export_scan_grid_not_integral`` or
  ``export_scan_point_incomplete``.

Grid keys are torsiondrive's own grid ids (``"-90"``, ``"180,-60"``) with
each angle wrapped into (-180, 180]; two points that wrap to the same key
are refused. ``scan_results`` is empty with ``protocols.scan_results =
"none"``, which is QCSchema's own way of saying the per-point optimization
histories are not carried -- TCKDB does not store them.

Everything the stored scan holds that the document does not carry, and
everything a TorsionDrive normally holds that TCKDB never stored, is listed
in ``extras["tckdb"]["export_report"]["not_carried"]``. The document is
validated with ``qcelemental.models.v2.TorsionDriveResult`` before it is
returned, and carries ``provenance.creator="TCKDB"`` so the importer
refuses it as a re-import.
"""

from __future__ import annotations

import math
from typing import Any

import qcelemental as qcel
import qcelemental.models.v2 as qcel_v2

from . import __version__ as _ADAPTER_VERSION
from .errors import (
    E_EXPORT_GEOMETRY_UNAVAILABLE,
    E_EXPORT_IDENTITY_UNAVAILABLE,
    E_EXPORT_ISOTOPES_UNAVAILABLE,
    E_EXPORT_SCAN_COORDINATE_NONCONFORMING,
    E_EXPORT_SCAN_GRID_NOT_INTEGRAL,
    E_EXPORT_SCAN_NOT_RELAXED,
    E_EXPORT_SCAN_NOT_TORSION_DRIVE,
    E_EXPORT_SCAN_POINT_INCOMPLETE,
    E_EXPORT_UNSUPPORTED_TYPE,
    QCSchemaAdapterError,
)
from .exporter import (
    _client_version,
    _level_of_theory,
    _mass_numbers_from_isotope_atoms,
    _resolve_nuclide_symbol,
)
from .molecule import BOHR_TO_ANGSTROM
from .scan import DIHEDRAL_TOLERANCE_DEGREES, dihedral_degrees, wrap_degrees

#: The read API's page-size ceiling for ``/scan`` points.
_PAGE_LIMIT = 200

#: Parameter-observation sections the importer writes (see
#: ``tckdb_qcschema.scan._parameters``) and the exporter reads back.
_OPTIMIZER_SECTION = "qcschema.optimization_spec"
_DRIVE_SECTION = "qcschema.torsiondrive"
_DRIVE_KEYWORD_SECTION = "qcschema.torsiondrive.keywords"


def grid_angle(value: float) -> int:
    """A whole-degree coordinate value wrapped into (-180, 180]."""
    wrapped = value - 360.0 * math.ceil((value - 180.0) / 360.0)
    return int(wrapped)


def grid_key(angles: list[int]) -> str:
    """torsiondrive's own grid id: angles joined by commas."""
    return ",".join(str(a) for a in angles)


def _owner_identity(record: dict) -> tuple[int, int]:
    owner = record.get("owner") or {}
    kind = owner.get("kind")
    block = owner.get(kind) if kind else None
    if not block or block.get("charge") is None or block.get("multiplicity") is None:
        raise QCSchemaAdapterError(
            E_EXPORT_IDENTITY_UNAVAILABLE,
            "the scan calculation's owner carries no charge/multiplicity.",
        )
    return int(block["charge"]), int(block["multiplicity"])


def _isotope_atoms(client: Any, geom_hash: str | None) -> list[dict]:
    """Per-atom isotopes for one geometry (legacy ``GET /geometries``)."""
    if not geom_hash:
        raise QCSchemaAdapterError(
            E_EXPORT_ISOTOPES_UNAVAILABLE,
            "a scan geometry link carries no geom_hash to look up per-atom "
            "isotopes on the legacy GET /geometries route.",
        )
    try:
        response = client.get_json(f"/geometries?geom_hash={geom_hash}")
    except Exception as exc:  # noqa: BLE001 - any failure here is refused
        raise QCSchemaAdapterError(
            E_EXPORT_ISOTOPES_UNAVAILABLE,
            "could not read per-atom isotopes from the legacy GET "
            f"/geometries route ({exc.__class__.__name__}: {exc}); refused "
            "rather than defaulting every atom to its standard nuclide.",
        ) from exc
    items = response.get("items") if isinstance(response, dict) else None
    atoms = items[0].get("atoms") if items else None
    if not atoms:
        raise QCSchemaAdapterError(
            E_EXPORT_ISOTOPES_UNAVAILABLE,
            "GET /geometries?geom_hash=<hash> returned no atoms for a scan geometry.",
        )
    return atoms


def _molecule(client: Any, geometry_ref: str, geom_hash: str | None, charge: int, multiplicity: int):
    """One exported ``Molecule`` plus its Angstrom coordinates.

    Charge and multiplicity come from the calculation's owner: a scan
    point's geometry is linked to the calculation only through
    ``calc_scan_point``, which the scientific geometry read does not count
    as an owner, so the geometry's own ``identity`` block is empty.
    """
    geometry = client.get_geometry(geometry_ref)
    atoms = sorted((geometry or {}).get("atoms") or [], key=lambda a: a["atom_index"])
    if not atoms:
        raise QCSchemaAdapterError(
            E_EXPORT_GEOMETRY_UNAVAILABLE,
            f"geometry {geometry_ref} carries no atoms to export.",
        )
    raw_symbols = [a["element"] for a in atoms]
    angstrom = [(a["x"], a["y"], a["z"]) for a in atoms]
    mass_numbers = _mass_numbers_from_isotope_atoms(raw_symbols, _isotope_atoms(client, geom_hash))
    molecule = qcel_v2.Molecule(
        symbols=[_resolve_nuclide_symbol(s) for s in raw_symbols],
        geometry=[v / BOHR_TO_ANGSTROM for xyz in angstrom for v in xyz],
        molecular_charge=charge,
        molecular_multiplicity=multiplicity,
        mass_numbers=mass_numbers,
        # See exporter._build_molecule: qcelemental's default 8-decimal
        # rounding would dominate the importer's 10-decimal-Angstrom bound.
        geometry_noise=14,
        # fix_com / fix_orientation stay at qcelemental's false default,
        # unlike a Hessian export (exporter._FRAME_DEPENDENT_DRIVERS): a
        # TorsionDrive export carries geometries and energies only -- no
        # gradient or Hessian expressed in these axes -- and a dihedral is
        # unchanged by any rigid motion of the molecule.
    )
    return molecule, angstrom


def _all_points(client: Any, calculation_ref_or_id: str | int) -> tuple[dict, list[dict]]:
    offset = 0
    first: dict | None = None
    points: list[dict] = []
    while True:
        page = client.get_calculation_scan(
            calculation_ref_or_id, include_geometries=True, offset=offset, limit=_PAGE_LIMIT
        )
        if first is None:
            first = page
        batch = page.get("points") or []
        points.extend(batch)
        total = (page.get("pagination") or {}).get("total", len(points))
        offset += len(batch)
        if not batch or offset >= total:
            break
    return first or {}, points


def _whole_degrees(value: float | None, *, what: str) -> int:
    if value is None or not math.isfinite(value) or not float(value).is_integer():
        raise QCSchemaAdapterError(
            E_EXPORT_SCAN_GRID_NOT_INTEGRAL,
            f"{what} is {value!r}, not a whole number of degrees; a "
            f"TorsionDrive grid is integer and rounding would change it.",
        )
    return int(value)


def _parameter(parameters: list[dict], section: str, key: str) -> str | None:
    for p in parameters:
        if p.get("section") == section and p.get("raw_key") == key:
            return p.get("raw_value")
    return None


def export_scan(client: Any, calculation_ref_or_id: str | int) -> tuple[dict, dict]:
    """Read one stored scan and export it as a v2 ``TorsionDriveResult``.

    :returns: ``(document, export_report)``. The document is a plain dict
        already validated with
        ``qcelemental.models.v2.TorsionDriveResult.model_validate``; the
        report (also embedded at ``extras["tckdb"]["export_report"]``)
        lists what was not carried.
    :raises QCSchemaAdapterError: one of the ``export_*`` codes.
    """
    detail = client.get_calculation(
        calculation_ref_or_id, include=["results", "input_geometries", "parameters"]
    )
    record = detail["record"]
    calc_type = record["calculation"]["type"]
    if calc_type != "scan":
        raise QCSchemaAdapterError(
            E_EXPORT_UNSUPPORTED_TYPE,
            f"export_scan exports scan calculations, not {calc_type!r}.",
            calculation_type=calc_type,
        )
    method, basis = _level_of_theory(record)
    charge, multiplicity = _owner_identity(record)
    parameters = record.get("parameters") or []
    not_carried: list[str] = []

    scan_page, points = _all_points(client, calculation_ref_or_id)
    scan = scan_page.get("scan") or {}
    coordinates = sorted(scan_page.get("coordinates") or [], key=lambda c: c["coordinate_index"])

    # --- is this a TorsionDrive at all? ------------------------------------
    kinds = [c.get("coordinate_kind") for c in coordinates]
    if not coordinates or any(kind != "dihedral" for kind in kinds):
        raise QCSchemaAdapterError(
            E_EXPORT_SCAN_NOT_TORSION_DRIVE,
            f"the scan's coordinates are {kinds}; QCSchema's only scan model "
            f"is the TorsionDrive, whose coordinates are proper dihedrals. "
            f"Exporting this scan as one would mislabel it.",
            coordinate_kinds=kinds,
        )
    if scan.get("is_relaxed") is not True:
        raise QCSchemaAdapterError(
            E_EXPORT_SCAN_NOT_RELAXED,
            f"the scan's is_relaxed is {scan.get('is_relaxed')!r}; a "
            f"TorsionDrive is a constrained optimization at every grid point, "
            f"and a rigid (or unstated) scan would be exported as optimizations "
            f"that never ran.",
        )
    if scan.get("dimension") != len(coordinates):
        raise QCSchemaAdapterError(
            E_EXPORT_SCAN_POINT_INCOMPLETE,
            f"the scan declares dimension {scan.get('dimension')} but carries "
            f"{len(coordinates)} coordinate(s).",
        )
    if not points:
        raise QCSchemaAdapterError(E_EXPORT_SCAN_POINT_INCOMPLETE, "the scan has no points.")

    dihedrals = [tuple(int(i) - 1 for i in c["atom_indices"]) for c in coordinates]
    grid_spacing = []
    for c in coordinates:
        step = c.get("step_size") if c.get("step_size") is not None else c.get("resolution_degrees")
        spacing = _whole_degrees(step, what=f"coordinate {c['coordinate_index']}'s grid spacing")
        if spacing <= 0:
            raise QCSchemaAdapterError(
                E_EXPORT_SCAN_GRID_NOT_INTEGRAL,
                f"coordinate {c['coordinate_index']}'s grid spacing is {spacing}.",
            )
        grid_spacing.append(spacing)
    ranges = [(c.get("start_value"), c.get("end_value")) for c in coordinates]
    dihedral_ranges = None
    if all(lo is not None and hi is not None for lo, hi in ranges):
        if all(float(lo).is_integer() and float(hi).is_integer() for lo, hi in ranges):
            dihedral_ranges = [(int(lo), int(hi)) for lo, hi in ranges]
        else:
            not_carried.append(
                "calc_scan_coordinate.start_value/end_value (not whole degrees; "
                "TorsionDrive dihedral_ranges are integer)"
            )
    elif any(lo is not None or hi is not None for lo, hi in ranges):
        not_carried.append(
            "calc_scan_coordinate.start_value/end_value (not set on every coordinate)"
        )
    for name in ("step_count", "symmetry_number"):
        if any(c.get(name) is not None for c in coordinates):
            not_carried.append(f"calc_scan_coordinate.{name} (no TorsionDrive field)")

    # --- points ---------------------------------------------------------
    final_molecules: dict[str, Any] = {}
    scan_properties: dict[str, Any] = {}
    normalised = False
    relative_energies = False
    for point in sorted(points, key=lambda p: p["point_index"]):
        index = point["point_index"]
        energy = point.get("electronic_energy_hartree")
        link = point.get("geometry_link") or {}
        geometry_ref = point.get("geometry_ref") or link.get("geometry_ref")
        values = {v["coordinate_index"]: v for v in point.get("coordinate_values") or []}
        if energy is None or geometry_ref is None or sorted(values) != [c["coordinate_index"] for c in coordinates]:
            raise QCSchemaAdapterError(
                E_EXPORT_SCAN_POINT_INCOMPLETE,
                f"scan point {index} has no energy, no geometry, or not exactly "
                f"one value per coordinate; it cannot become a grid point.",
                point_index=index,
            )
        if point.get("relative_energy_kj_mol") is not None:
            relative_energies = True
        molecule, angstrom = _molecule(client, geometry_ref, link.get("geom_hash"), charge, multiplicity)
        angles = []
        for c, atoms in zip(coordinates, dihedrals):
            stored = float(values[c["coordinate_index"]]["coordinate_value"])
            try:
                measured = dihedral_degrees(angstrom, atoms)
            except ValueError as exc:
                raise QCSchemaAdapterError(
                    E_EXPORT_SCAN_COORDINATE_NONCONFORMING,
                    f"scan point {index}: dihedral {c['coordinate_index']} is "
                    f"near-collinear in its stored geometry and cannot be checked.",
                ) from exc
            residual = wrap_degrees(measured - stored)
            if abs(residual) > DIHEDRAL_TOLERANCE_DEGREES:
                raise QCSchemaAdapterError(
                    E_EXPORT_SCAN_COORDINATE_NONCONFORMING,
                    f"scan point {index}: coordinate {c['coordinate_index']} is "
                    f"stored as {stored!r} degrees but the point's own geometry "
                    f"holds {measured:.6f} (off by {residual:.3e}). ADR 0020 fixes "
                    f"the stored value as the coordinate itself, and this series "
                    f"does not conform (the pre-ADR-0020 deposits hold a sweep "
                    f"relative to the first point); it needs the ADR 0020 "
                    f"absolute-angle migration. Even migrated, such a series "
                    f"would still not fit a TorsionDrive: its angles are not whole "
                    f"degrees and a full sweep repeats its first grid point.",
                    point_index=index,
                    stored=stored,
                    measured=measured,
                )
            whole = _whole_degrees(stored, what=f"scan point {index}'s coordinate {c['coordinate_index']}")
            angle = grid_angle(whole)
            normalised = normalised or angle != whole
            angles.append(angle)
        key = grid_key(angles)
        if key in final_molecules:
            raise QCSchemaAdapterError(
                E_EXPORT_SCAN_POINT_INCOMPLETE,
                f"scan point {index} lands on grid point {key!r}, which an "
                f"earlier point already holds (a sweep that returns to its "
                f"start); a TorsionDrive has one result per grid point.",
                point_index=index,
            )
        final_molecules[key] = molecule
        scan_properties[key] = qcel_v2.OptimizationProperties(return_energy=float(energy))

    if normalised:
        not_carried.append(
            "coordinate values outside (-180, 180] (wrapped into it for the grid key; "
            "the same physical angle)"
        )
    if relative_energies or scan.get("zero_energy_reference_hartree") is not None:
        not_carried.append(
            "relative_energy_kj_mol / zero_energy_reference_hartree (derived; "
            "TorsionDrive carries absolute energies only)"
        )

    # --- initial molecules ------------------------------------------------
    input_links = sorted(
        record.get("input_geometries") or [], key=lambda g: g.get("input_order") or 0
    )
    if not input_links:
        raise QCSchemaAdapterError(
            E_EXPORT_GEOMETRY_UNAVAILABLE,
            "the scan calculation has no input geometry to export as the "
            "TorsionDrive's initial_molecule (at least one is required).",
        )
    initial_molecules = [
        _molecule(client, link["geometry_ref"], link.get("geom_hash"), charge, multiplicity)[0]
        for link in input_links
    ]

    # --- specification ----------------------------------------------------
    software_release = record.get("software_release") or {}
    workflow_tool_release = record.get("workflow_tool_release") or {}
    optimizer_program = _parameter(parameters, _OPTIMIZER_SECTION, "program")
    drive_program = _parameter(parameters, _DRIVE_SECTION, "program")
    thresholds = {
        name: float(value)
        for name in ("energy_decrease_thresh", "energy_upper_limit")
        if (value := _parameter(parameters, _DRIVE_KEYWORD_SECTION, name)) is not None
    }
    if optimizer_program is None:
        not_carried.append("optimization specification program (not recorded)")
    if drive_program is None:
        not_carried.append("torsion drive specification program (not recorded)")
    if any(p.get("section") in ("qcschema.keywords", "qcschema.optimization_spec.keywords") for p in parameters):
        not_carried.append(
            "ESS and optimizer keywords (stored as text observations; not re-typed into the document)"
        )
    not_carried.extend(
        [
            "scan_results (per-grid-point optimization histories are not stored; "
            "protocols.scan_results='none')",
            "scan_properties[*] other than return_energy (gradients, nuclear "
            "repulsion energy and iteration counts are not stored)",
            "software_release and workflow_tool_release versions (in extras.tckdb only; "
            "specification.program is the stored software name)",
        ]
    )

    atomic_spec = qcel_v2.AtomicSpecification(
        driver="gradient",
        model=qcel_v2.Model(method=method, basis=basis),
        program=software_release.get("software") or "",
    )
    optimization_spec = qcel_v2.OptimizationSpecification(
        program=optimizer_program or "", specification=atomic_spec
    )
    drive_spec = qcel_v2.TorsionDriveSpecification(
        program=drive_program or "",
        keywords=qcel_v2.TorsionDriveKeywords(
            dihedrals=dihedrals,
            grid_spacing=grid_spacing,
            dihedral_ranges=dihedral_ranges,
            **thresholds,
        ),
        protocols=qcel_v2.TorsionDriveProtocols(scan_results="none"),
        specification=optimization_spec,
    )
    input_data = qcel_v2.TorsionDriveInput(initial_molecule=initial_molecules, specification=drive_spec)

    api_version, api_version_source = _client_version(client)
    report = {"not_carried": not_carried}
    extras = {
        "tckdb": {
            "calculation_ref": record["calculation"].get("calculation_ref"),
            "software_release": (
                f"{software_release.get('software')}/{software_release.get('version')}"
                if software_release.get("software")
                else None
            ),
            "workflow_tool_release": (
                f"{workflow_tool_release.get('workflow_tool')}/{workflow_tool_release.get('version')}"
                if workflow_tool_release.get("workflow_tool")
                else None
            ),
            "level_of_theory": (record.get("level_of_theory") or {}).get("level_of_theory_ref"),
            "grid_key_format": "torsiondrive grid id: whole-degree angles in (-180, 180], comma-joined",
            "export_adapter_version": _ADAPTER_VERSION,
            "qcelemental_version": qcel.__version__,
            "api_version_source": api_version_source,
            "export_report": report,
        }
    }
    result = qcel_v2.TorsionDriveResult(
        input_data=input_data,
        final_molecules=final_molecules,
        scan_properties=scan_properties,
        scan_results={},
        properties=qcel_v2.TorsionDriveProperties(calcinfo_ngrid=len(final_molecules)),
        success=True,
        provenance=qcel_v2.Provenance(
            creator="TCKDB",
            version=api_version,
            routine=f"tckdb-qcschema export/{_ADAPTER_VERSION}",
        ),
        extras=extras,
    )
    document = result.model_dump(mode="json", exclude_none=True)
    qcel_v2.TorsionDriveResult.model_validate(document)
    return document, report


__all__ = ["export_scan", "grid_angle", "grid_key"]
