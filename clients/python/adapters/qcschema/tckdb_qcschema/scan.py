"""QCSchema ``TorsionDriveResult`` -> a TCKDB scan calculation.

What QCSchema offers (pinned ``qcelemental==0.51.2``)
------------------------------------------------------
The only scan model in either family is the TorsionDrive:

* family v1 -- ``qcelemental.models.v1.TorsionDriveResult``
  (``schema_name="qcschema_torsion_drive_output"``, ``schema_version=1``),
  with the grid in ``keywords`` (``TDKeywords``), the per-point results in
  ``final_energies`` / ``final_molecules`` and the per-point optimizations in
  ``optimization_history``;
* family v2 -- ``qcelemental.models.v2.TorsionDriveResult``
  (``"qcschema_torsion_drive_result"``, ``2``), with the grid in
  ``input_data.specification.keywords`` (``TorsionDriveKeywords``), the
  per-point results in ``scan_properties`` / ``final_molecules`` and the
  per-point optimizations in ``scan_results``.

Both describe the same thing: one or more *proper dihedrals* (four atom
indices each), an integer ``grid_spacing`` in degrees per dihedral, and at
every grid point a *constrained optimization* (the driver must be
``gradient``; qcelemental enforces it) whose lowest-energy final geometry and
energy are the grid point's result. A grid over several dihedrals is a
multi-dimensional scan. There is no bond or angle scan, no rigid scan, and no
IRC, NEB or other reaction-path model in the package.

How it lands in TCKDB
---------------------
TCKDB stores a scan as a ``scan`` calculation attached to a conformer
(``POST /uploads/computed-species``, ``CalculationInBundle.scan_result``),
and a conformer is anchored by an unconstrained ``opt``. The drive holds only
constrained optimizations, so the optimization it started from is a second
document the depositor supplies (``--parent-opt``). It is checked to be that
optimization -- same atoms, same charge and multiplicity, and its
``final_molecule`` is one of the drive's ``initial_molecule`` geometries --
and becomes the conformer's primary ``opt``. The scan becomes an additional
calculation with a ``scan_parent`` edge to it, the same shape ARC deposits.

================================= ==========================================
TorsionDrive                       TCKDB
================================= ==========================================
``keywords.dihedrals[i]``          ``calc_scan_coordinate`` *i+1*, kind
                                    ``dihedral``, atoms 1-based
``keywords.grid_spacing[i]``       ``step_size`` and ``resolution_degrees``
``keywords.dihedral_ranges[i]``    ``start_value`` / ``end_value``
grid point (sorted by its angles)  ``calc_scan_point`` 1..N
grid key angles                    ``calc_scan_point_coordinate_value``
                                    (degrees; checked against the geometry)
final energy                       ``electronic_energy_hartree`` (exact)
``final_molecules[key]``           point geometry (bohr -> Angstrom)
``initial_molecule[*]``            scan ``input_geometries``
procedure (constrained opts)       ``is_relaxed = true``
``model.method`` / ``basis``       level of theory
per-point ESS provenance           ``software_release``
top-level ``provenance``           ``workflow_tool_release`` (the driver)
per-point optimizations            retained only (raw artifact)
``energy_decrease_thresh`` etc.    parameter observations
================================= ==========================================

The stored coordinate value is the grid angle the document names for the
point, and it is only stored after the dihedral recomputed from that point's
own final geometry agrees with it (modulo 360 degrees) within
:data:`DIHEDRAL_TOLERANCE_DEGREES`: ADR 0020 fixes the stored value as the
coordinate itself, never a displacement.

Relative energies are not in the document and are not computed here.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from tckdb_schemas.enums import (
    CalculationDependencyRole,
    CalculationType,
    CoordinateUnit,
    StationaryPointKind,
)
from tckdb_schemas.fragments.calculation import CalculationParameterObservation
from tckdb_schemas.fragments.refs import SoftwareReleaseRef, WorkflowToolReleaseRef
from tckdb_schemas.workflows.computed_species_upload import ComputedSpeciesUploadRequest

from .errors import (
    E_ESS_PROVENANCE_UNAVAILABLE,
    E_SCAN_COORDINATE_GEOMETRY_MISMATCH,
    E_SCAN_EXTRA_CONSTRAINTS_UNSUPPORTED,
    E_SCAN_GRID_INVALID,
    E_SCAN_PARENT_MISMATCH,
    E_SCAN_PARENT_OPT_REQUIRED,
    QCSchemaAdapterError,
)
from .mapping import (
    MappingReport,
    _dict_of,
    _level_of_theory,
    _parameter_observations,
    _parameters_extracted_at,
    _parser_version,
    _tckdb_origin_block,
    _tckdb_qcschema_block,
    _unsupported_fields,
    build_conformer_upload_payload,
)
from .molecule import _flatten, check_no_ghost_atoms, check_single_fragment, to_geometry_payload
from .reader import QCRecord
from .report_coverage import _MOLECULE_RULES, complete_mapping_report

#: A recomputed dihedral must agree with the grid angle stored for it within
#: this many degrees (after wrapping the difference into [-180, 180)). The
#: value is the floor ADR 0020 derives for the read-time conformance check
#: (``TOLERANCE_FLOOR_DEGREES`` in
#: ``backend/app/services/scan_coordinate_conformance.py``). Measured on the
#: real hydrogen-peroxide drive in this corpus: the largest disagreement is
#: 4.5e-7 degrees.
DIHEDRAL_TOLERANCE_DEGREES = 1e-3

#: A dihedral whose flanking bond angles are this close to collinear
#: (``min(sin(theta_123), sin(theta_234))`` below it) is not a usable
#: coordinate (ADR 0020), so it cannot be checked, and is refused rather
#: than stored unchecked.
DIHEDRAL_MIN_SIN_THETA = 0.05

#: An ``initial_molecule`` matches the parent optimization's
#: ``final_molecule`` when every Cartesian component agrees within this
#: many bohr. qcelemental itself rounds geometry to 1e-8 bohr on
#: construction, so this admits only that rounding, never a different
#: structure.
PARENT_GEOMETRY_TOLERANCE_BOHR = 1e-6

#: Local keys inside the emitted bundle.
CONFORMER_KEY = "qcschema_conformer"
OPT_CALCULATION_KEY = "qcschema_opt"
SCAN_CALCULATION_KEY = "qcschema_scan"


def wrap_degrees(value: float) -> float:
    """``value`` wrapped into [-180, 180)."""
    return ((value + 180.0) % 360.0) - 180.0


def dihedral_degrees(coords: Any, atoms: tuple[int, int, int, int]) -> float:
    """The proper dihedral over four 0-based atoms, in degrees (-180, 180].

    Units of ``coords`` do not matter (an angle is scale-free).

    :raises ValueError: when either flanking bond angle is within
        :data:`DIHEDRAL_MIN_SIN_THETA` of collinear.
    """
    xyz = np.asarray(coords, dtype=float).reshape(-1, 3)
    p0, p1, p2, p3 = (xyz[i] for i in atoms)
    b0, b1, b2 = p0 - p1, p2 - p1, p3 - p2

    def _sin(u, v):
        nu, nv = np.linalg.norm(u), np.linalg.norm(v)
        if nu == 0.0 or nv == 0.0:
            return 0.0
        return float(np.linalg.norm(np.cross(u, v)) / (nu * nv))

    if min(_sin(b0, b1), _sin(-b1, b2)) < DIHEDRAL_MIN_SIN_THETA:
        raise ValueError("near-collinear dihedral")
    b1n = b1 / np.linalg.norm(b1)
    v = b0 - np.dot(b0, b1n) * b1n
    w = b2 - np.dot(b2, b1n) * b1n
    return float(np.degrees(np.arctan2(np.dot(np.cross(b1n, v), w), np.dot(v, w))))


def parse_grid_key(key: str, n_dihedrals: int) -> tuple[float, ...]:
    """The grid angles a TorsionDrive grid key names.

    Three spellings are in use and all name the same thing: torsiondrive's
    own ``"180,-60"``, the whitespace-separated ``"180 -60"`` qcengine's
    harness splits on, and the JSON list ``"[180, -60]"`` QCFractal writes.
    Every entry must be a whole number of degrees (torsiondrive grids are
    integer; qcengine sets each constraint with ``int(angle)``).

    :raises QCSchemaAdapterError: ``scan_grid_invalid``.
    """
    text = key.strip()
    try:
        if text.startswith("["):
            parts = json.loads(text)
            if not isinstance(parts, list):
                raise ValueError(text)
        else:
            parts = text.replace(",", " ").split()
        values = [float(p) for p in parts]
    except (ValueError, TypeError) as exc:
        raise QCSchemaAdapterError(
            E_SCAN_GRID_INVALID, f"grid key {key!r} is not a list of angles.", key=key
        ) from exc
    if len(values) != n_dihedrals:
        raise QCSchemaAdapterError(
            E_SCAN_GRID_INVALID,
            f"grid key {key!r} names {len(values)} angle(s) but the drive "
            f"scans {n_dihedrals} dihedral(s).",
            key=key,
        )
    if not all(math.isfinite(v) and float(v).is_integer() for v in values):
        raise QCSchemaAdapterError(
            E_SCAN_GRID_INVALID,
            f"grid key {key!r} is not a whole number of degrees per dihedral.",
            key=key,
        )
    return tuple(values)


@dataclass
class _DriveView:
    """Family-normalised view over a ``TorsionDriveResult``."""

    dihedrals: list[tuple[int, int, int, int]]
    grid_spacing: list[int]
    dihedral_ranges: list[tuple[int, int]] | None
    energy_decrease_thresh: float | None
    energy_upper_limit: float | None
    initial_molecules: list[Any]
    final_molecules: dict[str, Any]
    final_energies: dict[str, float | None]
    histories: dict[str, list[Any]]
    method: str
    basis: str | None
    ess_keywords: dict
    ess_program: str | None
    optimizer_program: str | None
    optimizer_keywords: dict
    drive_program: str | None
    provenance: dict
    field_paths: dict[str, str] = field(default_factory=dict)


def _drive_view(record: QCRecord) -> _DriveView:
    r = record.result
    if record.family == "v1":
        kw = r.keywords
        opt_kw = dict(r.optimization_spec.keywords or {})
        return _DriveView(
            dihedrals=[tuple(d) for d in kw.dihedrals],
            grid_spacing=list(kw.grid_spacing),
            dihedral_ranges=[tuple(x) for x in kw.dihedral_ranges] if kw.dihedral_ranges else None,
            energy_decrease_thresh=kw.energy_decrease_thresh,
            energy_upper_limit=kw.energy_upper_limit,
            initial_molecules=list(r.initial_molecule),
            final_molecules=dict(r.final_molecules),
            final_energies=dict(r.final_energies),
            histories={k: list(v) for k, v in (r.optimization_history or {}).items()},
            method=r.input_specification.model.method,
            basis=getattr(r.input_specification.model, "basis", None),
            ess_keywords=dict(r.input_specification.keywords or {}),
            ess_program=opt_kw.get("program"),
            optimizer_program=r.optimization_spec.procedure,
            optimizer_keywords=opt_kw,
            drive_program=None,
            provenance=_dict_of(r.provenance),
            field_paths={
                "keywords": "keywords",
                "energies": "final_energies",
                "histories": "optimization_history",
                "initial": "initial_molecule",
                "model": "input_specification.model",
                "ess_keywords": "input_specification.keywords",
                "ess_program": "optimization_spec.keywords.program",
                "optimizer": "optimization_spec",
                "optimizer_program": "optimization_spec.procedure",
            },
        )
    spec = r.input_data.specification
    kw = spec.keywords
    optspec = spec.specification
    atomic = optspec.specification
    return _DriveView(
        dihedrals=[tuple(d) for d in kw.dihedrals],
        grid_spacing=list(kw.grid_spacing),
        dihedral_ranges=[tuple(x) for x in kw.dihedral_ranges] if kw.dihedral_ranges else None,
        energy_decrease_thresh=kw.energy_decrease_thresh,
        energy_upper_limit=kw.energy_upper_limit,
        initial_molecules=list(r.input_data.initial_molecule),
        final_molecules=dict(r.final_molecules),
        final_energies={k: p.return_energy for k, p in r.scan_properties.items()},
        histories={k: list(v) for k, v in (r.scan_results or {}).items()},
        method=atomic.model.method,
        basis=getattr(atomic.model, "basis", None),
        ess_keywords=dict(atomic.keywords or {}),
        ess_program=getattr(atomic, "program", None) or None,
        optimizer_program=getattr(optspec, "program", None) or None,
        optimizer_keywords=dict(optspec.keywords or {}),
        drive_program=getattr(spec, "program", None) or None,
        provenance=_dict_of(r.provenance),
        field_paths={
            "keywords": "input_data.specification.keywords",
            "energies": "scan_properties",
            "histories": "scan_results",
            "initial": "input_data.initial_molecule",
            "model": "input_data.specification.specification.specification.model",
            "ess_keywords": "input_data.specification.specification.specification.keywords",
            "ess_program": "input_data.specification.specification.specification.program",
            "optimizer": "input_data.specification.specification",
            "optimizer_program": "input_data.specification.specification.program",
            "drive_program": "input_data.specification.program",
        },
    )


def _last_step_provenance(optimization: Any) -> dict | None:
    trajectory = getattr(optimization, "trajectory", None)
    if trajectory is None:
        trajectory = getattr(optimization, "trajectory_results", None)
    trajectory = list(trajectory or [])
    return _dict_of(trajectory[-1].provenance) if trajectory else None


def _ess_software_release(view: _DriveView, report: MappingReport) -> tuple[SoftwareReleaseRef, str]:
    """The ESS that computed every grid point's energies and gradients.

    Read from the last trajectory step of every per-point optimization; all
    of them must name the same program and version. When the producer's
    protocols dropped every trajectory, the program named in the drive's
    own specification is used, with no version.
    """
    seen: set[tuple[str, str | None]] = set()
    for optimizations in view.histories.values():
        for optimization in optimizations:
            prov = _last_step_provenance(optimization)
            if prov and prov.get("creator"):
                seen.add((prov["creator"], prov.get("version")))
    if len(seen) > 1:
        raise QCSchemaAdapterError(
            E_ESS_PROVENANCE_UNAVAILABLE,
            f"the drive's per-point optimizations name more than one ESS "
            f"release ({sorted(seen, key=str)}); one scan calculation carries "
            f"one software_release.",
        )
    if seen:
        # Read from inside the per-point optimizations, which as a whole are
        # retained_only (no TCKDB table holds them); software_source in
        # parameters_json names where the release came from.
        (creator, version), = seen
        return SoftwareReleaseRef(name=creator, version=version), "per-point trajectory provenance"
    if view.ess_program:
        report.transformed.append(view.field_paths["ess_program"])
        report.unsupported.append(
            "software_release.version (no per-point trajectory survives to name it)"
        )
        return SoftwareReleaseRef(name=view.ess_program, version=None), "specification program"
    raise QCSchemaAdapterError(
        E_ESS_PROVENANCE_UNAVAILABLE,
        "the TorsionDriveResult names no ESS: no per-point trajectory "
        "survives and the specification names no program.",
    )


def _present(value: Any) -> bool:
    return value is not None and value != "" and value != [] and value != {}


#: Molecule fields the drive's mapping reads on every grid point's final
#: molecule (on top of the rules shared with the other record kinds).
_GRID_MOLECULE_READ = {"geometry", "molecular_charge", "molecular_multiplicity"}


def _classify_grid(record: QCRecord, report: MappingReport) -> None:
    """Name every present field of the grid-keyed parts of the document.

    The keys are the document's own grid points, so no static rule can name
    these paths: each final molecule's fields (``geometry``, charge and
    multiplicity read; the rest by the shared molecule rules; an unknown
    key or an ``extras`` entry ``unsupported``), and each grid point's
    energy (``transformed``) and, in family v2, its other optimization
    properties (``retained_only``).
    """
    raw = record.raw_document
    rules = dict(_MOLECULE_RULES)
    for key, molecule in (raw.get("final_molecules") or {}).items():
        prefix = f"final_molecules.{key}"
        for name, value in molecule.items():
            if not _present(value):
                continue
            if name == "extras":
                report.unsupported.extend(
                    f"{prefix}.extras.{sub}" for sub, v in value.items() if _present(v)
                )
                continue
            if name in _GRID_MOLECULE_READ:
                bucket = "transformed"
            else:
                bucket = rules.get(name, "unsupported")
            getattr(report, bucket).append(f"{prefix}.{name}")
    if record.family == "v1":
        report.transformed.append("final_energies")
        return
    for key, properties in (raw.get("scan_properties") or {}).items():
        for name, value in properties.items():
            if _present(value):
                bucket = report.transformed if name == "return_energy" else report.retained_only
                bucket.append(f"scan_properties.{key}.{name}")


def _molecule_xyz_bohr(molecule: Any) -> np.ndarray:
    return np.asarray(_flatten(molecule.geometry), dtype=float).reshape(-1, 3)


def _check_same_atoms(molecule: Any, reference: Any, *, code: str, what: str) -> None:
    if list(molecule.symbols) != list(reference.symbols) or [
        int(x) for x in molecule.mass_numbers
    ] != [int(x) for x in reference.mass_numbers]:
        raise QCSchemaAdapterError(
            code,
            f"{what} does not have the same atoms, in the same order, as "
            f"the reference molecule ({list(molecule.symbols)} vs "
            f"{list(reference.symbols)}).",
        )


def _integral_identity(molecule: Any) -> tuple[float, float]:
    return float(molecule.molecular_charge), float(molecule.molecular_multiplicity)


def _parameters(view: _DriveView, report: MappingReport) -> list[CalculationParameterObservation]:
    observations = list(_parameter_observations(view.ess_keywords))
    if view.ess_keywords:
        report.transformed.append(view.field_paths["ess_keywords"])
    for key, value in view.optimizer_keywords.items():
        observations.append(
            CalculationParameterObservation(
                raw_key=str(key),
                raw_value=str(value),
                section="qcschema.optimization_spec.keywords",
                canonical_key=None,
            )
        )
    if view.optimizer_keywords:
        report.transformed.append(f"{view.field_paths['optimizer']}.keywords")
    if view.optimizer_program:
        observations.append(
            CalculationParameterObservation(
                raw_key="program",
                raw_value=str(view.optimizer_program),
                section="qcschema.optimization_spec",
                canonical_key=None,
            )
        )
        report.transformed.append(view.field_paths["optimizer_program"])
    if view.drive_program:
        observations.append(
            CalculationParameterObservation(
                raw_key="program",
                raw_value=str(view.drive_program),
                section="qcschema.torsiondrive",
                canonical_key=None,
            )
        )
        report.transformed.append(view.field_paths["drive_program"])
    for name in ("energy_decrease_thresh", "energy_upper_limit"):
        value = getattr(view, name)
        if value is not None:
            observations.append(
                CalculationParameterObservation(
                    raw_key=name,
                    raw_value=repr(float(value)),
                    section="qcschema.torsiondrive.keywords",
                    canonical_key=None,
                )
            )
            report.transformed.append(f"{view.field_paths['keywords']}.{name}")
    return observations


@dataclass(frozen=True)
class ScanBundle:
    """One mapped TorsionDrive plus the optimization it started from."""

    payload: dict
    report: MappingReport
    parent_report: MappingReport
    dimension: int
    point_count: int


def _artifact(raw_bytes: bytes, filename: str, sha256: str) -> dict:
    return {
        "kind": "ancillary",
        "filename": filename,
        "content_base64": base64.b64encode(raw_bytes).decode("ascii"),
        "sha256": sha256,
        "bytes": len(raw_bytes),
    }


def build_scan_bundle_payload(
    record: QCRecord,
    *,
    raw_bytes: bytes,
    raw_artifact_filename: str,
    raw_artifact_sha256: str,
    parent_record: QCRecord | None,
    parent_raw_bytes: bytes | None = None,
    parent_artifact_filename: str | None = None,
    parent_artifact_sha256: str | None = None,
    declared_smiles: str | None = None,
    species_entry_kind: StationaryPointKind = StationaryPointKind.minimum,
) -> ScanBundle:
    """Map a TorsionDrive and its parent optimization to one bundle.

    :returns: a :class:`ScanBundle` whose ``payload`` is a
        ``ComputedSpeciesUploadRequest`` body, validated before it is
        returned, with both raw documents inline as ``ancillary`` artifacts.
    :raises QCSchemaAdapterError: any of the refusal codes in
        :mod:`tckdb_qcschema.errors`.
    """
    if hashlib.sha256(raw_bytes).hexdigest() != raw_artifact_sha256:
        raise ValueError("raw_artifact_sha256 does not match raw_bytes.")
    if record.record_kind != "torsion_drive":
        raise ValueError(f"expected a torsion_drive record, got {record.record_kind!r}.")
    if parent_record is None:
        raise QCSchemaAdapterError(
            E_SCAN_PARENT_OPT_REQUIRED,
            "a TorsionDriveResult needs the optimization it started from "
            "(--parent-opt): TCKDB attaches a scan to a conformer, and a "
            "conformer is anchored by that unconstrained optimization.",
        )
    if parent_record.record_kind != "optimization":
        raise QCSchemaAdapterError(
            E_SCAN_PARENT_MISMATCH,
            f"--parent-opt must be an OptimizationResult, got a "
            f"{parent_record.record_kind} document.",
        )
    if parent_raw_bytes is None or parent_artifact_sha256 is None or parent_artifact_filename is None:
        raise ValueError("parent_raw_bytes, parent_artifact_filename and parent_artifact_sha256 are required.")

    report = MappingReport()
    view = _drive_view(record)
    # The reader refuses success=false before this point; a stored scan
    # rests on it.
    report.transformed.append("success")

    # --- the parent optimization (also validates its own identity) ------
    parent_payload, parent_report = build_conformer_upload_payload(
        parent_record,
        raw_bytes=parent_raw_bytes,
        raw_artifact_filename=parent_artifact_filename,
        raw_artifact_sha256=parent_artifact_sha256,
        declared_smiles=declared_smiles,
        species_entry_kind=species_entry_kind,
    )
    parent_final = parent_record.result.final_molecule
    species_entry = parent_payload["species_entry"]

    # --- grid shape ------------------------------------------------------
    n = len(view.dihedrals)
    if n == 0:
        raise QCSchemaAdapterError(E_SCAN_GRID_INVALID, "the drive scans no dihedral.")
    if len(view.grid_spacing) != n:
        raise QCSchemaAdapterError(
            E_SCAN_GRID_INVALID,
            f"grid_spacing has {len(view.grid_spacing)} entries for {n} dihedral(s).",
        )
    if view.dihedral_ranges is not None and len(view.dihedral_ranges) != n:
        raise QCSchemaAdapterError(
            E_SCAN_GRID_INVALID,
            f"dihedral_ranges has {len(view.dihedral_ranges)} entries for {n} dihedral(s).",
        )
    if set(view.final_molecules) != set(view.final_energies):
        raise QCSchemaAdapterError(
            E_SCAN_GRID_INVALID,
            "the grid points with a final molecule and the grid points with "
            "an energy are not the same set.",
        )
    if not view.final_molecules:
        raise QCSchemaAdapterError(E_SCAN_GRID_INVALID, "the drive has no grid points.")
    if view.optimizer_keywords.get("constraints"):
        raise QCSchemaAdapterError(
            E_SCAN_EXTRA_CONSTRAINTS_UNSUPPORTED,
            "the optimizer keywords carry extra constraints "
            f"({view.optimizer_keywords['constraints']!r}); the surface they "
            "define is not the one the scan coordinates alone describe.",
        )

    # --- every molecule is one real species ------------------------------
    # Before anything compares molecules: a ghost atom or a second fragment
    # is its own refusal, not a "mismatch".
    for molecule in [*view.initial_molecules, *view.final_molecules.values()]:
        check_no_ghost_atoms(molecule)
        check_single_fragment(molecule)

    # --- the parent is the optimization this drive started from ---------
    reference = view.initial_molecules[0]
    _check_same_atoms(reference, parent_final, code=E_SCAN_PARENT_MISMATCH, what="the drive's initial_molecule[0]")
    parent_charge_mult = _integral_identity(parent_final)
    for i, molecule in enumerate(view.initial_molecules):
        _check_same_atoms(molecule, reference, code=E_SCAN_GRID_INVALID, what=f"initial_molecule[{i}]")
        if _integral_identity(molecule) != parent_charge_mult:
            raise QCSchemaAdapterError(
                E_SCAN_PARENT_MISMATCH,
                f"initial_molecule[{i}] has charge/multiplicity "
                f"{_integral_identity(molecule)}, the parent optimization "
                f"{parent_charge_mult}.",
            )
    parent_xyz = _molecule_xyz_bohr(parent_final)
    matched = [
        i
        for i, molecule in enumerate(view.initial_molecules)
        if np.max(np.abs(_molecule_xyz_bohr(molecule) - parent_xyz)) <= PARENT_GEOMETRY_TOLERANCE_BOHR
    ]
    if not matched:
        raise QCSchemaAdapterError(
            E_SCAN_PARENT_MISMATCH,
            "no initial_molecule of the drive is the parent optimization's "
            f"final_molecule (within {PARENT_GEOMETRY_TOLERANCE_BOHR} bohr); "
            "this drive did not start from that optimization.",
        )
    drive_smiles = {
        getattr(getattr(m, "identifiers", None), "smiles", None) for m in view.initial_molecules
    } - {None}
    if not declared_smiles and drive_smiles and drive_smiles != {species_entry["smiles"]}:
        raise QCSchemaAdapterError(
            E_SCAN_PARENT_MISMATCH,
            f"the drive declares identifiers.smiles {sorted(drive_smiles)}, the "
            f"parent optimization resolves to {species_entry['smiles']!r}.",
        )

    # --- points ----------------------------------------------------------
    grid = []
    for key in view.final_molecules:
        grid.append((parse_grid_key(key, n), key))
    grid.sort()
    if len({angles for angles, _ in grid}) != len(grid):
        raise QCSchemaAdapterError(E_SCAN_GRID_INVALID, "two grid keys name the same grid point.")

    points = []
    for point_index, (angles, key) in enumerate(grid, start=1):
        molecule = view.final_molecules[key]
        energy = view.final_energies[key]
        if energy is None:
            raise QCSchemaAdapterError(
                E_SCAN_GRID_INVALID, f"grid point {key!r} has no final energy."
            )
        _check_same_atoms(molecule, reference, code=E_SCAN_GRID_INVALID, what=f"final_molecules[{key!r}]")
        if _integral_identity(molecule) != parent_charge_mult:
            raise QCSchemaAdapterError(
                E_SCAN_GRID_INVALID,
                f"final_molecules[{key!r}] has a different charge/multiplicity "
                f"from the drive's initial molecule.",
            )
        xyz = _molecule_xyz_bohr(molecule)
        for c, (atoms, angle) in enumerate(zip(view.dihedrals, angles), start=1):
            try:
                measured = dihedral_degrees(xyz, atoms)
            except ValueError as exc:
                raise QCSchemaAdapterError(
                    E_SCAN_COORDINATE_GEOMETRY_MISMATCH,
                    f"grid point {key!r}: dihedral {c} {list(atoms)} is "
                    f"near-collinear in its final geometry and cannot be checked.",
                ) from exc
            residual = wrap_degrees(measured - angle)
            if abs(residual) > DIHEDRAL_TOLERANCE_DEGREES:
                raise QCSchemaAdapterError(
                    E_SCAN_COORDINATE_GEOMETRY_MISMATCH,
                    f"grid point {key!r}: dihedral {c} {list(atoms)} is "
                    f"{measured:.6f} degrees in its final geometry, not the "
                    f"{angle:g} its grid key names (off by {residual:.3e}, "
                    f"tolerance {DIHEDRAL_TOLERANCE_DEGREES}).",
                    grid_key=key,
                    measured=measured,
                    grid_value=angle,
                )
        points.append(
            {
                "point_index": point_index,
                "electronic_energy_hartree": float(energy),
                "geometry": to_geometry_payload(molecule).model_dump(mode="json", exclude_none=True),
                "coordinate_values": [
                    {
                        "coordinate_index": c,
                        "coordinate_value": float(angle),
                        "value_unit": CoordinateUnit.degree.value,
                    }
                    for c, angle in enumerate(angles, start=1)
                ],
            }
        )
    report.transformed.extend(
        [
            f"{view.field_paths['keywords']}.dihedrals",
            f"{view.field_paths['keywords']}.grid_spacing",
        ]
    )
    _classify_grid(record, report)
    if view.dihedral_ranges is not None:
        report.transformed.append(f"{view.field_paths['keywords']}.dihedral_ranges")

    coordinates = []
    for c, (atoms, spacing) in enumerate(zip(view.dihedrals, view.grid_spacing), start=1):
        coordinate = {
            "coordinate_index": c,
            "coordinate_kind": "dihedral",
            "atom1_index": atoms[0] + 1,
            "atom2_index": atoms[1] + 1,
            "atom3_index": atoms[2] + 1,
            "atom4_index": atoms[3] + 1,
            "step_size": float(spacing),
            "resolution_degrees": float(spacing),
            "value_unit": CoordinateUnit.degree.value,
        }
        if view.dihedral_ranges is not None:
            lower, upper = view.dihedral_ranges[c - 1]
            coordinate["start_value"] = float(lower)
            coordinate["end_value"] = float(upper)
        coordinates.append(coordinate)

    initial_geometries = [
        to_geometry_payload(m).model_dump(mode="json", exclude_none=True) for m in view.initial_molecules
    ]
    report.transformed.append(view.field_paths["initial"])

    # --- provenance --------------------------------------------------------
    software_release, software_source = _ess_software_release(view, report)
    workflow_tool_release = None
    if view.provenance.get("creator"):
        workflow_tool_release = WorkflowToolReleaseRef(
            name=view.provenance["creator"], version=view.provenance.get("version")
        )
        report.transformed.extend(["provenance.creator", "provenance.version"])
    level_of_theory = _level_of_theory(view.method, view.basis, view.ess_keywords)
    report.transformed.extend([f"{view.field_paths['model']}.method", f"{view.field_paths['model']}.basis"])
    # A spin treatment comes from the ESS keywords, which _parameters marks
    # transformed as a whole.
    parameters = _parameters(view, report)

    # --- what TCKDB has no place for ---------------------------------------
    # Every grid point's constrained optimizations and their trajectories:
    # no TCKDB table holds them; the raw artifact keeps them.
    if view.histories:
        report.retained_only.append(view.field_paths["histories"])
    optimizer_provenance = sorted(
        {
            (p.get("creator"), p.get("version"))
            for optimizations in view.histories.values()
            for p in (_dict_of(o.provenance) for o in optimizations)
            if p.get("creator")
        },
        key=str,
    )
    report.unsupported.extend(_unsupported_fields(record.raw_document))

    scan_result = {
        "dimension": n,
        "is_relaxed": True,
        "coordinates": coordinates,
        "points": points,
    }

    qcschema_block = _tckdb_qcschema_block(
        record,
        driver=None,
        identity_source=parent_payload["calculation"]["parameters_json"]["tckdb_qcschema"]["identity_source"],
        software_source=software_source,
        provenance=view.provenance,
        raw_artifact_sha256=raw_artifact_sha256,
        raw_artifact_filename=raw_artifact_filename,
        report=report,
    )
    qcschema_block["parent_opt_canonical_document_sha256"] = parent_record.canonical_sha256
    qcschema_block["parent_opt_raw_artifact_sha256"] = parent_artifact_sha256
    qcschema_block["parent_matches_initial_molecule_index"] = matched[0]
    qcschema_block["grid_points"] = [key for _angles, key in grid]
    # The optimizer that ran each grid point (geomeTRIC, ...): a calculation
    # carries one workflow_tool_release, and it names the torsion driver.
    qcschema_block["optimizer_releases"] = [
        f"{creator}/{version}" for creator, version in optimizer_provenance
    ]
    # Every field of the document in exactly one bucket, or refuse (#573).
    complete_mapping_report(record, report)
    qcschema_block["mapping_report"] = report.to_dict()
    parameters_json = {
        "tckdb_origin": _tckdb_origin_block(record, None),
        "tckdb_qcschema": qcschema_block,
    }

    scan_calc: dict = {
        "key": SCAN_CALCULATION_KEY,
        "type": CalculationType.scan.value,
        "software_release": software_release.model_dump(mode="json", exclude_none=True),
        "level_of_theory": level_of_theory.model_dump(mode="json", exclude_none=True),
        "parameters": [p.model_dump(mode="json", exclude_none=True) for p in parameters] or None,
        "parameters_json": parameters_json,
        "parameters_parser_version": _parser_version(),
        "scan_result": scan_result,
        "input_geometries": initial_geometries,
        "depends_on": [
            {
                "parent_calculation_key": OPT_CALCULATION_KEY,
                "role": CalculationDependencyRole.scan_parent.value,
            }
        ],
        "artifacts": [_artifact(raw_bytes, raw_artifact_filename, raw_artifact_sha256)],
    }
    if workflow_tool_release is not None:
        scan_calc["workflow_tool_release"] = workflow_tool_release.model_dump(mode="json", exclude_none=True)
    extracted_at = _parameters_extracted_at(view.provenance)
    if extracted_at is not None:
        scan_calc["parameters_extracted_at"] = extracted_at
    scan_calc = {k: v for k, v in scan_calc.items() if v is not None}

    opt_calc = dict(parent_payload["calculation"])
    opt_calc["key"] = OPT_CALCULATION_KEY
    opt_calc["artifacts"] = [_artifact(parent_raw_bytes, parent_artifact_filename, parent_artifact_sha256)]

    bundle = {
        "species_entry": species_entry,
        "conformers": [
            {
                "key": CONFORMER_KEY,
                "geometry": parent_payload["geometry"],
                "primary_calculation": opt_calc,
                "additional_calculations": [scan_calc],
            }
        ],
    }
    validated = ComputedSpeciesUploadRequest.model_validate(bundle)
    wire = validated.model_dump(mode="json", exclude_none=True)
    return ScanBundle(
        payload=wire,
        report=report,
        parent_report=parent_report,
        dimension=n,
        point_count=len(points),
    )


__all__ = [
    "DIHEDRAL_TOLERANCE_DEGREES",
    "ScanBundle",
    "build_scan_bundle_payload",
    "dihedral_degrees",
    "parse_grid_key",
    "wrap_degrees",
]
