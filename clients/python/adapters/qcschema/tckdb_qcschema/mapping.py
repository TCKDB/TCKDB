"""``QCRecord`` -> ``ConformerUploadRequest``-shaped dict (C-Q1).

Implements the C1 mapping table from
``docs/research/tckdb-phase-c-implementation-plan.md``. One QCSchema
document (already family-dispatched and strictly validated by
:mod:`tckdb_qcschema.reader`) becomes exactly one primary calculation:

* driver ``energy``                -> ``sp``
* driver ``gradient``               -> ``sp`` (energy from
  ``properties.return_energy``; the gradient array is retained-only)
* driver ``hessian``                -> ``freq`` (``HessianPayload`` with
  ``source=uploaded``; no ``freq_result``, no derived modes)
* ``OptimizationResult``            -> ``opt``

No QCSchema field name leaks into the emitted payload except inside the
``parameters_json["tckdb_qcschema"]`` namespace (sovereignty: TCKDB's own
vocabulary everywhere else). The full field-path accounting lives in the
returned :class:`MappingReport`, mirrored into
``parameters_json["tckdb_qcschema"]["mapping_report"]``.

``rejected`` is always empty for this adapter's own report: every rejection
this module can make is a whole-document refusal (a raised
``QCSchemaAdapterError``, before any payload is built) rather than a
partial per-field drop, so there is never a "mapping produced a payload
that quietly dropped a fatally-important field" state to report on. The
bucket exists in the shape because :class:`MappingReport` is shared
vocabulary with the ThermoML importer (C-E2), which does have partial
per-row rejections within one article.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import qcelemental

from tckdb_schemas.enums import (
    CalculationType,
    HessianSource,
    ScientificOriginKind,
    SpinTreatment,
    StationaryPointKind,
)
from tckdb_schemas.fragments.calculation import (
    CalculationParameterObservation,
    HessianPayload,
    OptResultPayload,
    OutputGeometryEntry,
    SPResultPayload,
)
from tckdb_schemas.enums import CalculationGeometryRole
from tckdb_schemas.fragments.refs import LevelOfTheoryRef, SoftwareReleaseRef
from tckdb_schemas.workflows.conformer_upload import (
    ConformerCalculationIn,
    ConformerUploadRequest,
)

from . import __version__ as _ADAPTER_VERSION
from .errors import E_ENERGY_CONTRADICTION, E_SP_ENERGY_UNAVAILABLE, QCSchemaAdapterError
from .hessian import pack_lower_triangle
from .molecule import BOHR_TO_ANGSTROM, resolve_identity, to_geometry_payload
from .reader import QCRecord

#: Absolute tolerance, hartree, for comparing an independently supplied
#: energy against ``properties.return_energy`` for the same record.
_ENERGY_TOLERANCE_HARTREE = 1e-9

_SPIN_TREATMENT_BY_REFERENCE = {
    "rhf": SpinTreatment.restricted,
    "rks": SpinTreatment.restricted,
    "rghf": SpinTreatment.restricted,
    "uhf": SpinTreatment.unrestricted,
    "uks": SpinTreatment.unrestricted,
    "rohf": SpinTreatment.restricted_open,
    "roks": SpinTreatment.restricted_open,
}


@dataclass
class MappingReport:
    """Field-path accounting for one mapped QCSchema document."""

    transformed: list[str] = field(default_factory=list)
    retained_only: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "transformed": list(self.transformed),
            "retained_only": list(self.retained_only),
            "unsupported": list(self.unsupported),
            "rejected": list(self.rejected),
        }


def _enum_value(x) -> str:
    return x.value if hasattr(x, "value") else str(x)


def _dict_of(model) -> dict:
    if model is None:
        return {}
    if hasattr(model, "model_dump"):
        return model.model_dump(exclude_none=True)
    if hasattr(model, "dict"):
        return model.dict(exclude_none=True)
    return dict(model)


def _atomic_view(record: QCRecord) -> dict:
    """Family-normalised view over an ``AtomicResult`` in either family."""
    r = record.result
    if record.family == "v1":
        return dict(
            molecule=r.molecule,
            driver=_enum_value(r.driver),
            method=r.model.method,
            basis=getattr(r.model, "basis", None),
            keywords=dict(r.keywords or {}),
            provenance=_dict_of(r.provenance),
            return_result=r.return_result,
            return_energy=getattr(r.properties, "return_energy", None),
        )
    spec = r.input_data.specification
    return dict(
        molecule=r.molecule,
        driver=_enum_value(spec.driver),
        method=spec.model.method,
        basis=getattr(spec.model, "basis", None),
        keywords=dict(spec.keywords or {}),
        provenance=_dict_of(r.provenance),
        return_result=r.return_result,
        return_energy=getattr(r.properties, "return_energy", None),
    )


def _optimization_view(record: QCRecord) -> dict:
    """Family-normalised view over an ``OptimizationResult`` in either family."""
    r = record.result
    if record.family == "v1":
        spec = r.input_specification
        energies = list(r.energies or [])
        return dict(
            initial_molecule=r.initial_molecule,
            final_molecule=r.final_molecule,
            n_steps=len(r.trajectory or []),
            final_energy=energies[-1] if energies else None,
            step_energies=energies,
            method=spec.model.method,
            basis=getattr(spec.model, "basis", None),
            keywords=dict(spec.keywords or {}),
            provenance=_dict_of(r.provenance),
        )
    inner_spec = r.input_data.specification.specification
    step_props = list(r.trajectory_properties or [])
    step_energies = [getattr(p, "return_energy", None) for p in step_props]
    return dict(
        initial_molecule=r.input_data.initial_molecule,
        final_molecule=r.final_molecule,
        n_steps=len(r.trajectory_results or []),
        final_energy=step_energies[-1] if step_energies else None,
        step_energies=step_energies,
        method=inner_spec.model.method,
        basis=getattr(inner_spec.model, "basis", None),
        keywords=dict(inner_spec.keywords or {}),
        provenance=_dict_of(r.provenance),
    )


def _spin_treatment(keywords: dict) -> SpinTreatment | None:
    reference = keywords.get("reference")
    if not isinstance(reference, str):
        return None
    return _SPIN_TREATMENT_BY_REFERENCE.get(reference.strip().lower())


def _parameter_observations(keywords: dict) -> list[CalculationParameterObservation]:
    return [
        CalculationParameterObservation(
            raw_key=str(key),
            raw_value=str(value),
            section="qcschema.keywords",
            canonical_key=None,
        )
        for key, value in keywords.items()
    ]


def _unsupported_fields(raw_document: dict, *, prefix: str = "") -> list[str]:
    """Field paths QCSchema carries that this adapter never maps.

    Only reported when actually present (and non-null/non-empty) in the
    raw document -- the report describes this deposit's data, not the
    class of fields the adapter is structurally silent on.
    """
    out = []
    for name in ("wavefunction", "native_files", "stdout", "stderr"):
        value = raw_document.get(name)
        if value:
            out.append(f"{prefix}{name}")
    return out


def _software_release(provenance: dict) -> SoftwareReleaseRef | None:
    creator = provenance.get("creator")
    if not creator:
        return None
    return SoftwareReleaseRef(name=creator, version=provenance.get("version"))


def _level_of_theory(method: str, basis: str | None, keywords: dict) -> LevelOfTheoryRef:
    return LevelOfTheoryRef(
        method=method,
        basis=basis,
        spin_treatment=_spin_treatment(keywords),
    )


def _tckdb_origin_block(record: QCRecord, driver: str | None) -> dict:
    return {
        "origin_kind": "imported",
        "independent_ess_job": True,
        "producer": f"tckdb-qcschema/{_ADAPTER_VERSION}",
        "reason": (
            f"Imported from a MolSSI QCSchema {record.record_kind} document "
            f"(family={record.family}, driver={driver or 'n/a'})."
        ),
    }


def _tckdb_qcschema_block(
    record: QCRecord,
    *,
    driver: str | None,
    identity_source: str,
    provenance: dict,
    raw_artifact_sha256: str,
    raw_artifact_filename: str,
    report: MappingReport,
) -> dict:
    return {
        "adapter_version": _ADAPTER_VERSION,
        "qcelemental_version": qcelemental.__version__,
        "model_family": record.family,
        "schema_name": record.schema_name,
        "schema_version": record.schema_version,
        "record_kind": record.record_kind,
        "driver": driver,
        "raw_artifact_sha256": raw_artifact_sha256,
        "raw_artifact_filename": raw_artifact_filename,
        "canonical_document_sha256": record.canonical_sha256,
        "bohr_to_angstrom": BOHR_TO_ANGSTROM,
        "identity_source": identity_source,
        "provenance": provenance,
        "mapping_report": report.to_dict(),
    }


def _parser_version() -> str:
    return f"tckdb-qcschema/{_ADAPTER_VERSION};qcelemental/{qcelemental.__version__}"


def build_conformer_upload_payload(
    record: QCRecord,
    *,
    raw_bytes: bytes,
    raw_artifact_filename: str,
    raw_artifact_sha256: str,
    declared_smiles: str | None = None,
    species_entry_kind: StationaryPointKind = StationaryPointKind.minimum,
) -> tuple[dict, MappingReport]:
    """Map one validated :class:`QCRecord` to a ``ConformerUploadRequest`` dict.

    :param raw_bytes: The exact bytes of the source ``.json`` file
        (unused directly here beyond size bookkeeping -- the caller
        already hashed it into ``raw_artifact_sha256``; kept as a
        parameter so callers cannot pass a filename/hash pair that was
        never actually checked against the bytes).
    :returns: ``(payload_dict, report)``. ``payload_dict`` has already
        been validated against ``ConformerUploadRequest`` (and,
        standalone, against ``HessianPayload`` when a Hessian is
        present) before being returned.
    :raises QCSchemaAdapterError: any of the refusal codes in
        :mod:`tckdb_qcschema.errors`.
    """
    if raw_artifact_sha256 != __import__("hashlib").sha256(raw_bytes).hexdigest():
        raise ValueError("raw_artifact_sha256 does not match raw_bytes.")

    report = MappingReport()

    if record.record_kind == "atomic":
        payload, driver = _build_atomic(record, report)
    else:
        payload, driver = _build_optimization(record, report)

    provenance = payload.pop("_provenance")

    # Identity resolution happens once, from the molecule that anchors the
    # whole record (the single AtomicResult molecule, or the optimization's
    # final molecule -- see _build_atomic / _build_optimization).
    molecule = payload.pop("_identity_molecule")
    identity = resolve_identity(molecule, declared_smiles=declared_smiles)
    payload["species_entry"]["smiles"] = identity.smiles
    payload["species_entry"]["charge"] = identity.charge
    payload["species_entry"]["multiplicity"] = identity.multiplicity
    payload["species_entry"]["species_entry_kind"] = species_entry_kind
    report.transformed.append(
        "identifiers.smiles" if identity.source == "identifiers_smiles" else "--smiles"
    )
    report.transformed.extend(["molecule.molecular_charge", "molecule.molecular_multiplicity"])

    unsupported = _unsupported_fields(record.raw_document)
    report.unsupported.extend(unsupported)

    payload["calculation"]["parameters_json"] = {
        "tckdb_origin": _tckdb_origin_block(record, driver),
        "tckdb_qcschema": _tckdb_qcschema_block(
            record,
            driver=driver,
            identity_source=identity.source,
            provenance=provenance,
            raw_artifact_sha256=raw_artifact_sha256,
            raw_artifact_filename=raw_artifact_filename,
            report=report,
        ),
    }
    payload["calculation"]["parameters_parser_version"] = _parser_version()
    payload["calculation"]["parameters_extracted_at"] = datetime.now(
        timezone.utc
    ).isoformat()

    validated = ConformerUploadRequest.model_validate(payload)
    wire_payload = validated.model_dump(mode="json", exclude_none=True)
    return wire_payload, report


def _base_species_entry() -> dict:
    return {"molecule_kind": "molecule"}


def _build_atomic(record: QCRecord, report: MappingReport) -> tuple[dict, str | None]:
    view = _atomic_view(record)
    driver = view["driver"]
    molecule = view["molecule"]
    method, basis, keywords = view["method"], view["basis"], view["keywords"]
    provenance = view["provenance"]

    geometry = to_geometry_payload(molecule)
    report.transformed.append("molecule.geometry")
    if geometry.isotopes:
        report.transformed.append("molecule.mass_numbers")

    software_release = _software_release(provenance)
    if software_release is not None:
        report.transformed.extend(["provenance.creator", "provenance.version"])
    level_of_theory = _level_of_theory(method, basis, keywords)
    report.transformed.extend(["model.method", "model.basis"])
    if level_of_theory.spin_treatment is not None:
        report.transformed.append("keywords.reference")

    calc_kwargs: dict = dict(
        type=None,
        software_release=software_release,
        level_of_theory=level_of_theory,
        input_geometries=[geometry],
        parameters=_parameter_observations(keywords) or None,
    )
    if keywords:
        report.transformed.append("keywords")

    if driver == "energy":
        return_result = float(view["return_result"])
        return_energy = view["return_energy"]
        return_energy = float(return_energy) if return_energy is not None else None
        if return_energy is not None and abs(return_result - return_energy) > _ENERGY_TOLERANCE_HARTREE:
            raise QCSchemaAdapterError(
                E_ENERGY_CONTRADICTION,
                f"driver=energy return_result={return_result!r} disagrees "
                f"with properties.return_energy={return_energy!r} beyond "
                f"tolerance {_ENERGY_TOLERANCE_HARTREE} hartree.",
                return_result=return_result,
                return_energy=return_energy,
            )
        calc_kwargs["type"] = CalculationType.sp
        calc_kwargs["sp_result"] = SPResultPayload(electronic_energy_hartree=return_result)
        report.transformed.append("return_result")

    elif driver == "gradient":
        return_energy = view["return_energy"]
        return_energy = float(return_energy) if return_energy is not None else None
        if return_energy is None:
            raise QCSchemaAdapterError(
                E_SP_ENERGY_UNAVAILABLE,
                "driver=gradient document has no properties.return_energy "
                "to map as the record's sp energy.",
            )
        calc_kwargs["type"] = CalculationType.sp
        calc_kwargs["sp_result"] = SPResultPayload(electronic_energy_hartree=return_energy)
        report.transformed.append("properties.return_energy")
        report.retained_only.append("return_result")

    elif driver == "hessian":
        natoms = len(list(molecule.symbols))
        lower_triangle = pack_lower_triangle(list(view["return_result"]), natoms)
        calc_kwargs["type"] = CalculationType.freq
        calc_kwargs["hessian"] = HessianPayload(
            geometry=geometry,
            lower_triangle_hartree_bohr2=lower_triangle,
            source=HessianSource.uploaded,
        )
        report.transformed.append("return_result")

    else:
        raise QCSchemaAdapterError(
            "unsupported_driver",
            f"driver={driver!r} is not one of energy, gradient, hessian; "
            f"scans, IRC, and other driver kinds are out of scope for C-Q1.",
        )

    calc = ConformerCalculationIn(**{k: v for k, v in calc_kwargs.items() if v is not None or k == "type"})
    payload = {
        "species_entry": _base_species_entry(),
        "geometry": geometry,
        "calculation": calc.model_dump(mode="python", exclude_none=True),
        "scientific_origin": ScientificOriginKind.computed,
        "_identity_molecule": molecule,
        "_provenance": provenance,
    }
    return payload, driver


def _build_optimization(record: QCRecord, report: MappingReport) -> tuple[dict, str | None]:
    view = _optimization_view(record)
    method, basis, keywords = view["method"], view["basis"], view["keywords"]
    provenance = view["provenance"]

    initial_geometry = to_geometry_payload(view["initial_molecule"])
    final_geometry = to_geometry_payload(view["final_molecule"])
    report.transformed.extend(["initial_molecule.geometry", "final_molecule.geometry"])
    if initial_geometry.isotopes or final_geometry.isotopes:
        report.transformed.append("molecule.mass_numbers")

    software_release = _software_release(provenance)
    if software_release is not None:
        report.transformed.extend(["provenance.creator", "provenance.version"])
    level_of_theory = _level_of_theory(method, basis, keywords)
    report.transformed.extend(["model.method", "model.basis"])
    if level_of_theory.spin_treatment is not None:
        report.transformed.append("keywords.reference")

    if view["final_energy"] is None:
        raise QCSchemaAdapterError(
            E_SP_ENERGY_UNAVAILABLE,
            "OptimizationResult has no per-step energies to report as the "
            "final electronic energy.",
        )

    opt_result = OptResultPayload(
        converged=bool(record.result.success),
        n_steps=view["n_steps"],
        final_energy_hartree=float(view["final_energy"]),
    )
    report.transformed.extend(["success", "trajectory (length)"])
    report.transformed.append("trajectory[-1].properties.return_energy")
    if view["step_energies"]:
        report.retained_only.append("energies (per-step)")
    report.retained_only.append("trajectory")

    calc_kwargs = dict(
        type=CalculationType.opt,
        software_release=software_release,
        level_of_theory=level_of_theory,
        opt_result=opt_result,
        input_geometries=[initial_geometry],
        output_geometries=[
            OutputGeometryEntry(geometry=final_geometry, role=CalculationGeometryRole.final)
        ],
        parameters=_parameter_observations(keywords) or None,
    )
    if keywords:
        report.transformed.append("keywords")

    calc = ConformerCalculationIn(**{k: v for k, v in calc_kwargs.items() if v is not None or k == "type"})
    payload = {
        "species_entry": _base_species_entry(),
        "geometry": final_geometry,
        "calculation": calc.model_dump(mode="python", exclude_none=True),
        "scientific_origin": ScientificOriginKind.computed,
        "_identity_molecule": view["final_molecule"],
        "_provenance": provenance,
    }
    return payload, None


__all__ = ["MappingReport", "build_conformer_upload_payload"]
