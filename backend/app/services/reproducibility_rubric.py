"""Deterministic, fail-closed reproducibility rubric v1.

Version 1 can award ``auditable`` only to calculations. It verifies preserved
output bytes through the artifact read path, snapshots every evaluated datum,
and deliberately cannot award ``rerunnable`` until TCKDB has a typed execution
environment manifest and dependency closure contract.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session

from app.db.models.calculation import Calculation, CalculationArtifact
from app.db.models.common import (
    ArtifactKind,
    CalculationType,
    ReproducibilityAssessorKind,
    ReproducibilityGrade,
    SubmissionRecordType,
)
from app.db.models.energy_correction import AppliedEnergyCorrection
from app.db.models.kinetics import Kinetics
from app.db.models.network import Network
from app.db.models.network_pdep import NetworkSolve
from app.db.models.reaction import ChemReaction, ReactionEntry
from app.db.models.reproducibility_assessment import RecordReproducibilityAssessment
from app.db.models.species import ConformerGroup, ConformerObservation, Species, SpeciesEntry
from app.db.models.statmech import Statmech
from app.db.models.thermo import Thermo
from app.db.models.transition_state import TransitionState, TransitionStateEntry
from app.db.models.transport import Transport
from app.services.artifact_storage import (
    ArtifactIntegrityError,
    ArtifactStorageUnavailable,
    load_artifact_bytes,
)
from app.services.reproducibility_assessment import (
    append_reproducibility_assessment,
    resolve_reproducibility_record_model,
)

RUBRIC_NAME = "tckdb_reproducibility"
RUBRIC_VERSION = "v1"
#: Maximum output bytes fetched during one verification (50 MiB, matching the
#: repository's per-artifact upload ceiling).
MAX_ARTIFACT_VERIFICATION_BYTES = 50 * 1024 * 1024
MAX_ARTIFACT_VERIFICATION_COUNT = 8
MAX_ARTIFACT_VERIFICATION_TOTAL_BYTES = 50 * 1024 * 1024

ArtifactLoader = Callable[..., bytes]


class CheckLevel(str, Enum):
    described = "described"
    auditable = "auditable"
    rerunnable = "rerunnable"


class CheckOutcome(str, Enum):
    passed = "passed"
    missing = "missing"
    not_applicable = "not_applicable"


class ArtifactVerificationStatus(str, Enum):
    verified = "verified"
    not_requested = "not_requested"
    unavailable = "unavailable"
    integrity_failed = "integrity_failed"
    size_limit_exceeded = "size_limit_exceeded"
    count_budget_exceeded = "count_budget_exceeded"
    aggregate_budget_exceeded = "aggregate_budget_exceeded"


@dataclass(frozen=True)
class ReproducibilityCheck:
    """One typed rubric result with the exact evidence used by the check."""

    name: str
    level: CheckLevel
    outcome: CheckOutcome
    evidence: dict[str, Any]

    def snapshot(self) -> dict[str, Any]:
        return _json_value(asdict(self))


@dataclass(frozen=True)
class ReproducibilityWarning:
    code: str
    evidence: dict[str, Any]

    def snapshot(self) -> dict[str, Any]:
        return _json_value(asdict(self))


@dataclass(frozen=True)
class ReproducibilityEvaluation:
    record_type: SubmissionRecordType
    record_id: int
    grade: ReproducibilityGrade
    context_json: dict[str, Any]
    passed: tuple[dict[str, Any], ...]
    missing: tuple[dict[str, Any], ...]
    warnings: tuple[dict[str, Any], ...]


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _nonblank_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _mapped_columns(row: Any) -> dict[str, Any]:
    """Snapshot all persisted scalar columns on an ORM row."""
    mapper = sa_inspect(type(row))
    return {column.key: _json_value(getattr(row, column.key)) for column in mapper.columns}


def _rows(rows: Any) -> list[dict[str, Any]]:
    snapshots = [_mapped_columns(row) for row in rows]
    return sorted(snapshots, key=lambda item: tuple(str(value) for value in item.values()))


def _geometry_snapshot(geometry: Any | None) -> dict[str, Any] | None:
    """Snapshot geometry identity, serialized coordinates, and atom rows."""
    if geometry is None:
        return None
    return {
        "columns": _mapped_columns(geometry),
        "atoms": _rows(geometry.atoms),
    }


def _geometry_link_snapshot(link: Any) -> dict[str, Any]:
    return {
        "columns": _mapped_columns(link),
        "geometry": _geometry_snapshot(link.geometry),
    }


def _point_geometry_snapshot(point: Any) -> dict[str, Any]:
    snapshot = {
        "columns": _mapped_columns(point),
        "geometry": _geometry_snapshot(point.geometry),
    }
    if hasattr(point, "coordinate_values"):
        snapshot["coordinate_values"] = _rows(point.coordinate_values)
    return snapshot


_TARGET_RELATIONSHIPS: dict[type[Any], tuple[str, ...]] = {
    ChemReaction: ("participants",),
    ReactionEntry: ("structure_participants",),
    Thermo: ("points", "nasa9_intervals"),
    Kinetics: ("arrhenius_entries", "plog_entries", "third_body_efficiencies"),
    Statmech: ("torsions", "electronic_levels"),
    Network: ("reactions", "species_links", "states", "channels"),
    NetworkSolve: ("bath_gases", "energy_transfers"),
}

_TARGET_SINGLE_RELATIONSHIPS: dict[type[Any], tuple[str, ...]] = {
    Thermo: ("nasa", "wilhoit"),
    Kinetics: ("falloff", "chebyshev"),
}


def _target_snapshot(target: Any, record_type: SubmissionRecordType) -> dict[str, Any]:
    reference = (
        f"sha256:{target.sha256}"
        if isinstance(target, CalculationArtifact)
        else getattr(target, "public_ref", None) or f"{record_type.value}:{target.id}"
    )
    relationships: dict[str, Any] = {}
    for model, names in _TARGET_RELATIONSHIPS.items():
        if isinstance(target, model):
            relationships.update({name: _rows(getattr(target, name)) for name in names})
    for model, names in _TARGET_SINGLE_RELATIONSHIPS.items():
        if isinstance(target, model):
            for name in names:
                row = getattr(target, name)
                relationships[name] = None if row is None else _mapped_columns(row)
    if isinstance(target, Statmech):
        relationships["torsions"] = [
            {
                "columns": _mapped_columns(torsion),
                "coordinates": _rows(torsion.coordinates),
            }
            for torsion in sorted(target.torsions, key=lambda row: row.torsion_index)
        ]
    if isinstance(target, Network):
        relationships["states"] = [
            {
                "columns": _mapped_columns(state),
                "participants": _rows(state.participants),
            }
            for state in sorted(target.states, key=lambda row: row.id)
        ]
    if isinstance(target, NetworkSolve):
        relationships["network"] = {
            "columns": _mapped_columns(target.network),
            "reactions": _rows(target.network.reactions),
            "species_links": _rows(target.network.species_links),
            "states": [
                {
                    "columns": _mapped_columns(state),
                    "participants": _rows(state.participants),
                }
                for state in sorted(target.network.states, key=lambda row: row.id)
            ],
            "channels": _rows(target.network.channels),
        }
    return {
        "record_type": record_type.value,
        "record_id": target.id,
        "reference": reference,
        "columns": _mapped_columns(target),
        "relationships": relationships,
    }


def _source_refs(target: Any) -> list[tuple[Calculation, str]]:
    refs: list[tuple[Calculation, str]] = []
    if isinstance(target, Calculation):
        return [(target, "self")]
    if isinstance(target, CalculationArtifact):
        return [(target.calculation, "artifact_calculation")]
    if isinstance(target, (ConformerObservation, TransitionStateEntry)):
        refs.extend((calculation, "attached_calculation") for calculation in target.calculations)
    elif isinstance(target, AppliedEnergyCorrection):
        if target.source_calculation is not None:
            refs.append((target.source_calculation, "source_calculation"))
        if target.source_conformer_observation is not None:
            refs.extend(
                (calculation, "source_conformer_observation_calculation")
                for calculation in target.source_conformer_observation.calculations
            )
    elif hasattr(target, "source_calculations"):
        refs.extend(
            (link.calculation, getattr(getattr(link, "role", None), "value", "unspecified"))
            for link in target.source_calculations
            if getattr(link, "calculation", None) is not None
        )
    unique = {(calculation.id, role): (calculation, role) for calculation, role in refs}
    return [unique[key] for key in sorted(unique)]


def _calculation_closure(roots: list[Calculation]) -> tuple[list[Calculation], bool]:
    rows: dict[int, Calculation] = {}
    visiting: set[int] = set()
    cycle = False

    def visit(calculation: Calculation) -> None:
        nonlocal cycle
        if calculation.id in visiting:
            cycle = True
            return
        if calculation.id in rows:
            return
        visiting.add(calculation.id)
        rows[calculation.id] = calculation
        for edge in sorted(
            calculation.child_dependencies,
            key=lambda item: (item.dependency_role.value, item.parent_calculation_id),
        ):
            visit(edge.parent_calculation)
        visiting.remove(calculation.id)

    for root in roots:
        visit(root)
    return [rows[key] for key in sorted(rows)], cycle


def _typed_output_snapshot(calculation: Calculation) -> tuple[bool, dict[str, Any]]:
    attr_by_type = {
        CalculationType.sp: "sp_result",
        CalculationType.opt: "opt_result",
        CalculationType.freq: "freq_result",
        CalculationType.scan: "scan_result",
        CalculationType.irc: "irc_result",
        CalculationType.path_search: "path_search_result",
    }
    attr = attr_by_type.get(calculation.type)
    if attr is None:
        return False, {"calculation_type": calculation.type.value, "reason": "no_typed_result_contract_v1"}
    result = getattr(calculation, attr)
    if result is None:
        return False, {"calculation_type": calculation.type.value, "result": None}

    snapshot: dict[str, Any] = {"calculation_type": calculation.type.value, "result": _mapped_columns(result)}
    if calculation.type is CalculationType.freq:
        snapshot["modes"] = _rows(result.modes)
        meaningful = result.n_imag is not None or result.zpe_hartree is not None or bool(result.modes)
    elif calculation.type is CalculationType.scan:
        snapshot["coordinates"] = _rows(result.coordinates)
        snapshot["constraints"] = _rows(result.constraints)
        snapshot["points"] = [_point_geometry_snapshot(point) for point in result.points]
        meaningful = bool(result.points) or result.zero_energy_reference_hartree is not None
    elif calculation.type is CalculationType.irc:
        snapshot["points"] = [_point_geometry_snapshot(point) for point in result.points]
        meaningful = bool(result.points) or result.has_forward or result.has_reverse
    elif calculation.type is CalculationType.path_search:
        snapshot["points"] = [_point_geometry_snapshot(point) for point in result.points]
        meaningful = bool(result.points) or result.converged is not None
    elif calculation.type is CalculationType.sp:
        meaningful = result.electronic_energy_hartree is not None
    else:
        meaningful = result.final_energy_hartree is not None or result.converged is not None
    return bool(meaningful), snapshot


def _verify_artifact(
    artifact: CalculationArtifact,
    *,
    artifact_loader: ArtifactLoader,
    verify_output: bool,
) -> tuple[dict[str, Any], ReproducibilityWarning | None]:
    snapshot = _mapped_columns(artifact)
    if artifact.kind is not ArtifactKind.output_log or not verify_output:
        snapshot["verification"] = ArtifactVerificationStatus.not_requested.value
        return snapshot, None
    if artifact.bytes > MAX_ARTIFACT_VERIFICATION_BYTES:
        snapshot["verification"] = ArtifactVerificationStatus.size_limit_exceeded.value
        return snapshot, ReproducibilityWarning(
            code="artifact_verification_size_limit",
            evidence={
                "artifact_id": artifact.id,
                "persisted_bytes": artifact.bytes,
                "verification_limit_bytes": MAX_ARTIFACT_VERIFICATION_BYTES,
            },
        )
    try:
        content = artifact_loader(artifact.sha256, expected_bytes=artifact.bytes)
    except ArtifactStorageUnavailable:
        snapshot["verification"] = ArtifactVerificationStatus.unavailable.value
        return snapshot, ReproducibilityWarning(
            code="artifact_storage_unavailable",
            evidence={"artifact_id": artifact.id, "sha256": artifact.sha256},
        )
    except ArtifactIntegrityError:
        snapshot["verification"] = ArtifactVerificationStatus.integrity_failed.value
        return snapshot, ReproducibilityWarning(
            code="artifact_integrity_failed",
            evidence={"artifact_id": artifact.id, "sha256": artifact.sha256},
        )
    snapshot["verification"] = ArtifactVerificationStatus.verified.value
    snapshot["verified_bytes"] = len(content)
    return snapshot, None


def _calculation_snapshot(
    calculation: Calculation,
    *,
    artifact_loader: ArtifactLoader,
    verify_output_artifacts: bool,
) -> tuple[dict[str, Any], list[ReproducibilityWarning]]:
    artifact_rows: list[dict[str, Any]] = []
    warnings: list[ReproducibilityWarning] = []
    verification_count = 0
    verification_bytes = 0
    for artifact in sorted(calculation.artifacts, key=lambda row: (row.kind.value, row.sha256, row.id)):
        budget_status: ArtifactVerificationStatus | None = None
        budget_warning: ReproducibilityWarning | None = None
        should_verify = verify_output_artifacts and artifact.kind is ArtifactKind.output_log
        if should_verify and artifact.bytes <= MAX_ARTIFACT_VERIFICATION_BYTES:
            if verification_count >= MAX_ARTIFACT_VERIFICATION_COUNT:
                budget_status = ArtifactVerificationStatus.count_budget_exceeded
                budget_warning = ReproducibilityWarning(
                    code="artifact_verification_count_budget",
                    evidence={
                        "artifact_id": artifact.id,
                        "verification_count_limit": MAX_ARTIFACT_VERIFICATION_COUNT,
                    },
                )
            elif verification_bytes + artifact.bytes > MAX_ARTIFACT_VERIFICATION_TOTAL_BYTES:
                budget_status = ArtifactVerificationStatus.aggregate_budget_exceeded
                budget_warning = ReproducibilityWarning(
                    code="artifact_verification_aggregate_budget",
                    evidence={
                        "artifact_id": artifact.id,
                        "verification_total_bytes": verification_bytes,
                        "persisted_bytes": artifact.bytes,
                        "verification_total_limit_bytes": MAX_ARTIFACT_VERIFICATION_TOTAL_BYTES,
                    },
                )
            else:
                verification_count += 1
                verification_bytes += artifact.bytes
        if budget_status is not None:
            snapshot = _mapped_columns(artifact)
            snapshot["verification"] = budget_status.value
            artifact_rows.append(snapshot)
            warnings.append(budget_warning)
            continue
        snapshot, warning = _verify_artifact(
            artifact,
            artifact_loader=artifact_loader,
            verify_output=should_verify,
        )
        artifact_rows.append(snapshot)
        if warning is not None:
            warnings.append(warning)
    typed_present, typed_output = _typed_output_snapshot(calculation)
    return (
        {
            "columns": _mapped_columns(calculation),
            "software_release": (
                None
                if calculation.software_release is None
                else {
                    "columns": _mapped_columns(calculation.software_release),
                    "software": _mapped_columns(calculation.software_release.software),
                }
            ),
            "workflow_tool_release": None
            if calculation.workflow_tool_release is None
            else _mapped_columns(calculation.workflow_tool_release),
            "level_of_theory": None if calculation.lot is None else _mapped_columns(calculation.lot),
            "parameters_json": _json_value(calculation.parameters_json),
            "parameters": _rows(calculation.parameters),
            "input_geometries": [_geometry_link_snapshot(link) for link in calculation.input_geometries],
            "output_geometries": [_geometry_link_snapshot(link) for link in calculation.output_geometries],
            "scan_coordinates": _rows(calculation.scan_coordinates),
            "constraints": _rows(calculation.constraints),
            "scan_points": [_point_geometry_snapshot(point) for point in calculation.scan_points],
            "irc_points": [_point_geometry_snapshot(point) for point in calculation.irc_points],
            "path_search_points": [_point_geometry_snapshot(point) for point in calculation.path_search_points],
            "typed_output_present": typed_present,
            "typed_output": typed_output,
            "artifacts": artifact_rows,
            "upstream_dependencies": _rows(calculation.child_dependencies),
        },
        warnings,
    )


def _scientific_context(target: Any, snapshot: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    columns = snapshot["columns"]
    relationships = snapshot["relationships"]
    if isinstance(target, Species):
        passed = bool(target.smiles and target.inchi_key and target.multiplicity >= 1)
    elif isinstance(target, SpeciesEntry):
        passed = bool(target.species_id and target.kind)
    elif isinstance(target, ConformerGroup):
        passed = bool(target.species_entry_id)
    elif isinstance(target, ConformerObservation):
        passed = bool(target.conformer_group_id and target.scientific_origin)
    elif isinstance(target, ChemReaction):
        roles = {row["role"] for row in relationships["participants"]}
        passed = {"reactant", "product"}.issubset(roles)
    elif isinstance(target, ReactionEntry):
        passed = bool(target.reaction_id and relationships["structure_participants"])
    elif isinstance(target, TransitionState):
        passed = bool(target.reaction_entry_id)
    elif isinstance(target, TransitionStateEntry):
        passed = bool(target.transition_state_id and target.multiplicity >= 1)
    elif isinstance(target, Calculation):
        passed = bool(target.type and ((target.species_entry_id is None) != (target.transition_state_entry_id is None)))
    elif isinstance(target, Thermo):
        representation = any(
            value is not None
            for value in (target.h298_kj_mol, target.s298_j_mol_k, target.enthalpy_formation_0k_kj_mol)
        ) or any(relationships.get(name) for name in ("points", "nasa9_intervals", "nasa", "wilhoit"))
        passed = bool(target.species_entry_id and target.scientific_origin and representation)
    elif isinstance(target, Kinetics):
        representation = target.a is not None or any(
            relationships.get(name) for name in ("arrhenius_entries", "plog_entries", "falloff", "chebyshev")
        )
        passed = bool(target.reaction_entry_id and target.scientific_origin and target.model_kind and representation)
    elif isinstance(target, Statmech):
        passed = bool(
            target.species_entry_id and target.scientific_origin and (target.external_symmetry or target.point_group)
        )
    elif isinstance(target, Transport):
        passed = bool(
            target.species_entry_id
            and target.scientific_origin
            and any(
                value is not None
                for value in (
                    target.sigma_angstrom,
                    target.epsilon_over_k_k,
                    target.dipole_debye,
                    target.polarizability_angstrom3,
                    target.rotational_relaxation,
                )
            )
        )
    elif isinstance(target, Network):
        passed = bool(relationships["reactions"] and relationships["species_links"])
    elif isinstance(target, NetworkSolve):
        passed = bool(
            target.network_id
            and target.me_method
            and all(value is not None for value in (target.tmin_k, target.tmax_k, target.pmin_bar, target.pmax_bar))
        )
    elif isinstance(target, AppliedEnergyCorrection):
        passed = bool(target.application_role and target.value_unit)
    elif isinstance(target, CalculationArtifact):
        passed = bool(target.calculation_id and target.filename and target.bytes > 0)
    else:
        passed = False
    return passed, {"columns": columns, "relationships": relationships}


def _source_attribution(
    target: Any,
    source_refs: list[tuple[Calculation, str]],
) -> tuple[CheckOutcome, dict[str, Any]]:
    refs = [{"calculation_id": calculation.id, "role": role} for calculation, role in source_refs]
    if isinstance(
        target,
        (
            Species,
            SpeciesEntry,
            ChemReaction,
            ReactionEntry,
            TransitionState,
        ),
    ):
        return CheckOutcome.not_applicable, {"reason": "canonical_identity"}
    if isinstance(target, Calculation):
        passed = target.literature_id is not None or (
            target.software_release_id is not None and target.lot_id is not None
        )
        return (CheckOutcome.passed if passed else CheckOutcome.missing), {
            "literature_id": target.literature_id,
            "software_release_id": target.software_release_id,
            "level_of_theory_id": target.lot_id,
        }
    if isinstance(target, (ConformerGroup, Network)):
        return CheckOutcome.missing, {
            "reason": "selected_collection_requires_scientific_provenance_v1",
            "created_by_excluded": getattr(target, "created_by", None),
        }

    origin = getattr(getattr(target, "scientific_origin", None), "value", None)
    literature_id = getattr(target, "literature_id", None)
    if origin == "experimental":
        passed = literature_id is not None
    elif origin == "estimated":
        passed = literature_id is not None or getattr(target, "applied_group_additivity", None) is not None
    elif origin == "computed":
        passed = bool(refs and all(ref["role"] != "unspecified" for ref in refs))
    else:
        passed = literature_id is not None or bool(refs)
    return (CheckOutcome.passed if passed else CheckOutcome.missing), {
        "scientific_origin": origin,
        "literature_id": literature_id,
        "source_calculations": refs,
    }


def _check(
    name: str,
    level: CheckLevel,
    passed: bool,
    evidence: dict[str, Any],
) -> ReproducibilityCheck:
    return ReproducibilityCheck(
        name=name,
        level=level,
        outcome=CheckOutcome.passed if passed else CheckOutcome.missing,
        evidence=evidence,
    )


def evaluate_reproducibility_v1(
    session: Session,
    *,
    record_type: str | SubmissionRecordType,
    record_id: int,
    artifact_loader: ArtifactLoader = load_artifact_bytes,
) -> ReproducibilityEvaluation:
    """Evaluate one record and snapshot all evidence consumed by rubric v1."""
    resolved_type, model = resolve_reproducibility_record_model(record_type)
    if record_id <= 0:
        raise ValueError("record_id must be positive")
    target = session.get(model, record_id)
    if target is None:
        raise ValueError(f"{resolved_type.value} record {record_id} does not exist")

    target_evidence = _target_snapshot(target, resolved_type)
    source_refs = _source_refs(target)
    roots = sorted({calculation.id: calculation for calculation, _ in source_refs}.values(), key=lambda row: row.id)
    closure, dependency_cycle = _calculation_closure(roots)
    calculations: dict[str, Any] = {}
    warnings: list[ReproducibilityWarning] = []
    for calculation in closure:
        snapshot, calculation_warnings = _calculation_snapshot(
            calculation,
            artifact_loader=artifact_loader,
            verify_output_artifacts=(isinstance(target, Calculation) and calculation.id == target.id),
        )
        calculations[str(calculation.id)] = snapshot
        warnings.extend(calculation_warnings)

    context_present, context_detail = _scientific_context(target, target_evidence)
    attribution_outcome, attribution_detail = _source_attribution(target, source_refs)
    direct_calculation = target if isinstance(target, Calculation) else None
    direct_snapshot = calculations.get(str(target.id)) if direct_calculation is not None else None
    typed_output_present = bool(direct_snapshot and direct_snapshot["typed_output_present"])
    verified_outputs = (
        []
        if direct_snapshot is None
        else [
            artifact
            for artifact in direct_snapshot["artifacts"]
            if artifact["kind"] == ArtifactKind.output_log.value
            and artifact["verification"] == ArtifactVerificationStatus.verified.value
        ]
    )
    output_artifacts = (
        []
        if direct_snapshot is None
        else [
            artifact for artifact in direct_snapshot["artifacts"] if artifact["kind"] == ArtifactKind.output_log.value
        ]
    )
    input_artifacts = (
        []
        if direct_snapshot is None
        else [artifact for artifact in direct_snapshot["artifacts"] if artifact["kind"] == ArtifactKind.input.value]
    )
    software_release = None if direct_snapshot is None else direct_snapshot["software_release"]
    level_of_theory = None if direct_snapshot is None else direct_snapshot["level_of_theory"]
    release_columns = None if software_release is None else software_release["columns"]
    software_columns = None if software_release is None else software_release["software"]
    exact_release_token = bool(
        release_columns
        and any(_nonblank_text(release_columns.get(field)) for field in ("version", "revision", "build"))
    )
    reconciliation_status = (
        None
        if direct_calculation is None
        else getattr(direct_calculation.software_reconciliation_status, "value", None)
    )
    nonconflicting_software_identity = bool(
        software_columns
        and _nonblank_text(software_columns.get("name"))
        and exact_release_token
        and reconciliation_status != "mismatch"
    )

    checks = [
        _check("target_identity", CheckLevel.described, bool(target_evidence["reference"]), target_evidence),
        _check("scientific_context", CheckLevel.described, context_present, context_detail),
        ReproducibilityCheck(
            name="source_attribution",
            level=CheckLevel.described,
            outcome=attribution_outcome,
            evidence=attribution_detail,
        ),
        _check(
            "record_type_audit_policy_v1",
            CheckLevel.auditable,
            direct_calculation is not None,
            {"supported_record_type": SubmissionRecordType.calculation.value},
        ),
        _check(
            "calculation_metadata",
            CheckLevel.auditable,
            bool(level_of_theory and nonconflicting_software_identity),
            {
                "level_of_theory": level_of_theory,
                "software_release": software_release,
                "exact_release_token": exact_release_token,
                "software_reconciliation_status": reconciliation_status,
                "nonconflicting_software_identity": nonconflicting_software_identity,
            },
        ),
        _check(
            "typed_output_evidence",
            CheckLevel.auditable,
            typed_output_present,
            {} if direct_snapshot is None else direct_snapshot["typed_output"],
        ),
        _check(
            "verified_output_artifact_bytes",
            CheckLevel.auditable,
            bool(verified_outputs),
            {"output_artifacts": output_artifacts},
        ),
        ReproducibilityCheck(
            name="source_role_preservation",
            level=CheckLevel.auditable,
            outcome=CheckOutcome.not_applicable if direct_calculation is not None else CheckOutcome.missing,
            evidence={
                "reason": "calculation_is_the_evidence_root"
                if direct_calculation is not None
                else "product_policy_deferred_v1",
                "source_calculations": attribution_detail.get("source_calculations", []),
            },
        ),
        _check(
            "preserved_input_artifacts",
            CheckLevel.rerunnable,
            bool(input_artifacts),
            {"input_artifacts": input_artifacts},
        ),
        _check(
            "execution_parameter_snapshot",
            CheckLevel.rerunnable,
            bool(direct_snapshot and (direct_snapshot["parameters_json"] or direct_snapshot["parameters"])),
            {
                "parameters_json": None if direct_snapshot is None else direct_snapshot["parameters_json"],
                "parameters": [] if direct_snapshot is None else direct_snapshot["parameters"],
            },
        ),
        _check(
            "upstream_dependency_snapshot",
            CheckLevel.rerunnable,
            bool(direct_calculation is not None and not dependency_cycle),
            {"cycle_detected": dependency_cycle, "calculation_ids": sorted(calculations)},
        ),
        _check(
            "execution_environment_manifest",
            CheckLevel.rerunnable,
            False,
            {"reason": "typed_execution_environment_manifest_not_supported_by_v1"},
        ),
    ]

    def level_passed(level: CheckLevel) -> bool:
        return all(check.outcome is not CheckOutcome.missing for check in checks if check.level is level)

    if not level_passed(CheckLevel.described):
        grade = ReproducibilityGrade.insufficient
    elif not level_passed(CheckLevel.auditable):
        grade = ReproducibilityGrade.described
    elif not level_passed(CheckLevel.rerunnable):
        grade = ReproducibilityGrade.auditable
    else:  # pragma: no cover - v1's environment-manifest check is intentionally missing
        grade = ReproducibilityGrade.rerunnable

    check_snapshots = tuple(check.snapshot() for check in checks)
    warning_snapshots = tuple(warning.snapshot() for warning in warnings)
    context = {
        "rubric": {"name": RUBRIC_NAME, "version": RUBRIC_VERSION},
        "target": target_evidence,
        "source_roles": [{"calculation_id": calculation.id, "role": role} for calculation, role in source_refs],
        "calculation_evidence": calculations,
        "checks": list(check_snapshots),
        "warnings": list(warning_snapshots),
    }
    return ReproducibilityEvaluation(
        record_type=resolved_type,
        record_id=record_id,
        grade=grade,
        context_json=context,
        passed=tuple(check for check in check_snapshots if check["outcome"] in {"passed", "not_applicable"}),
        missing=tuple(check for check in check_snapshots if check["outcome"] == "missing"),
        warnings=warning_snapshots,
    )


def evaluate_and_append_reproducibility_v1(
    session: Session,
    *,
    record_type: str | SubmissionRecordType,
    record_id: int,
    artifact_loader: ArtifactLoader = load_artifact_bytes,
) -> RecordReproducibilityAssessment:
    """Derive and append a system-owned v1 assessment; callers provide no claims."""
    evaluation = evaluate_reproducibility_v1(
        session,
        record_type=record_type,
        record_id=record_id,
        artifact_loader=artifact_loader,
    )
    return append_reproducibility_assessment(
        session,
        record_type=evaluation.record_type,
        record_id=evaluation.record_id,
        grade=evaluation.grade,
        rubric_name=RUBRIC_NAME,
        rubric_version=RUBRIC_VERSION,
        context_json=evaluation.context_json,
        passed=evaluation.passed,
        missing=evaluation.missing,
        warnings=evaluation.warnings,
        assessor_kind=ReproducibilityAssessorKind.system,
    )
