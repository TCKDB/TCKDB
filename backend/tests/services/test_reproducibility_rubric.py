"""Focused tests for the conservative reproducibility rubric."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import pytest

from app.db.models.calculation import (
    Calculation,
    CalculationArtifact,
    CalculationDependency,
    CalculationParameter,
    CalculationScanCoordinate,
    CalculationSPResult,
)
from app.db.models.common import (
    ArtifactKind,
    CalculationDependencyRole,
    CalculationType,
    CoordinateUnit,
    MoleculeKind,
    ParameterSource,
    ReproducibilityAssessorKind,
    ReproducibilityGrade,
    ScanCoordinateKind,
    ScientificOriginKind,
    SoftwareReconciliationStatus,
    StereoKind,
    SubmissionRecordType,
    ThermoCalculationRole,
)
from app.db.models.geometry import Geometry, GeometryAtom
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.reaction import ChemReaction
from app.db.models.software import Software, SoftwareRelease
from app.db.models.species import Species, SpeciesEntry
from app.db.models.thermo import Thermo, ThermoSourceCalculation
from app.schemas.fragments.execution_environment import ExecutionEnvironmentManifestPayload
from app.services.artifact_storage import ArtifactIntegrityError, ArtifactStorageUnavailable
from app.services.calculation_resolution import resolve_execution_environment_manifest
from app.services.execution_environment_integrity import manifest_integrity_evidence
from app.services.reproducibility_assessment import SUPPORTED_REPRODUCIBILITY_RECORD_TYPES
from app.services.reproducibility_rubric import (
    MAX_ARTIFACT_VERIFICATION_BYTES,
    MAX_ARTIFACT_VERIFICATION_COUNT,
    MAX_ARTIFACT_VERIFICATION_TOTAL_BYTES,
    _geometry_snapshot,
    _rows,
    evaluate_and_append_reproducibility,
    evaluate_reproducibility,
)
from app.services.scientific_read.calculations import _build_execution_environment_summary
from tests.services.scientific_read._factories import (
    make_chem_reaction,
    make_kinetics,
    make_network,
    make_network_solve,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_statmech,
    make_thermo_scalar,
    make_transport,
    next_inchi_key,
)


def _snapshot_hash(snapshot) -> str:
    encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _verified_loader(objects: dict[str, bytes]):
    def load(sha256: str, *, expected_bytes: int | None = None) -> bytes:
        if sha256 not in objects:
            raise ArtifactStorageUnavailable("missing mocked object")
        content = objects[sha256]
        if not hmac.compare_digest(hashlib.sha256(content).hexdigest(), sha256):
            raise ArtifactIntegrityError("mocked digest mismatch")
        if expected_bytes is not None and len(content) != expected_bytes:
            raise ArtifactIntegrityError("mocked size mismatch")
        return content

    return load


def _calculation(
    db_session,
    *,
    complete: bool,
    created_by: int,
    objects: dict[str, bytes],
    typed_output: bool = True,
) -> Calculation:
    token = str(created_by) + str(db_session.query(Calculation).count())
    species = Species(
        kind=MoleculeKind.molecule,
        smiles=f"repro-{token}",
        inchi_key=hashlib.sha256(token.encode()).hexdigest()[:27].upper(),
        charge=0,
        multiplicity=1,
        stereo_kind=StereoKind.achiral,
    )
    db_session.add(species)
    db_session.flush()
    entry = SpeciesEntry(species_id=species.id, created_by=created_by)
    software = Software(name=f"repro-software-{token}")
    lot = LevelOfTheory(
        method="wb97xd",
        basis="def2tzvp",
        lot_hash=hashlib.sha256(f"lot-{token}".encode()).hexdigest(),
    )
    db_session.add_all([entry, software, lot])
    db_session.flush()
    release = SoftwareRelease(software_id=software.id, version="1.0")
    db_session.add(release)
    db_session.flush()
    calculation = Calculation(
        type=CalculationType.sp,
        species_entry_id=entry.id,
        software_release_id=release.id,
        lot_id=lot.id,
        created_by=created_by,
    )
    db_session.add(calculation)
    db_session.flush()
    output = f"Entering Gaussian System output {token}".encode()
    output_sha = hashlib.sha256(output).hexdigest()
    objects[output_sha] = output
    db_session.add(
        CalculationArtifact(
            calculation_id=calculation.id,
            kind=ArtifactKind.output_log,
            uri=f"s3://repro/{token}.log",
            sha256=output_sha,
            bytes=len(output),
            filename="job.log",
            created_by=created_by,
        )
    )
    if typed_output:
        db_session.add(
            CalculationSPResult(
                calculation_id=calculation.id,
                electronic_energy_hartree=-40.0,
            )
        )
    if complete:
        input_bytes = f"# wb97xd/def2tzvp input {token}".encode()
        input_sha = hashlib.sha256(input_bytes).hexdigest()
        objects[input_sha] = input_bytes
        db_session.add_all(
            [
                CalculationArtifact(
                    calculation_id=calculation.id,
                    kind=ArtifactKind.input,
                    uri=f"s3://repro/{token}.inp",
                    sha256=input_sha,
                    bytes=len(input_bytes),
                    filename="job.inp",
                    created_by=created_by,
                ),
                CalculationParameter(
                    calculation_id=calculation.id,
                    raw_key="scf_convergence",
                    raw_value="tight",
                    source=ParameterSource.upload,
                ),
            ]
        )
    db_session.flush()
    db_session.refresh(calculation)
    return calculation


def _evaluate(db_session, calculation: Calculation, objects: dict[str, bytes]):
    return evaluate_reproducibility(
        db_session,
        record_type="calculation",
        record_id=calculation.id,
        artifact_loader=_verified_loader(objects),
    )


def test_below_described_result_is_persisted_as_insufficient(db_session) -> None:
    reaction = ChemReaction(reversible=True)
    db_session.add(reaction)
    db_session.flush()

    row = evaluate_and_append_reproducibility(
        db_session,
        record_type=SubmissionRecordType.reaction,
        record_id=reaction.id,
    )

    assert row.grade is ReproducibilityGrade.insufficient
    assert row.assessor_kind is ReproducibilityAssessorKind.system
    assert {item["name"] for item in row.missing_json} >= {"scientific_context"}


def test_complete_deposited_evidence_is_rerunnable_without_a_manifest(
    db_session,
    _api_test_user,
) -> None:
    """The ordinary shared-cluster upload can reach the top grade.

    ``rerunnable`` grades the completeness of deposited evidence, so a
    calculation with preserved inputs, parameters, dependencies, and readable
    output bytes reaches it without anyone supplying a byte digest for a
    site-installed binary.
    """
    objects: dict[str, bytes] = {}
    calculation = _calculation(
        db_session,
        complete=True,
        created_by=_api_test_user,
        objects=objects,
    )

    result = _evaluate(db_session, calculation, objects)

    assert result.grade is ReproducibilityGrade.rerunnable
    assert "verified_output_artifact_bytes" in {item["name"] for item in result.passed}
    # The manifest is recorded provenance, never a check.
    assert "execution_environment_manifest" not in {item["name"] for item in result.missing}
    assert result.context_json["execution_environment"] == {"recorded": False}


def _execution_environment_payload() -> dict:
    return {
        "schema_version": "tckdb.execution-environment.v1", "software_release": {"name": "Gaussian", "version": "16"},
        "runtime": {"runtime_kind": "container", "image": "registry.example/arc@sha256:" + "a" * 64},
        "executable": {"locator": "file:///opt/arc/bin/arc", "digest": "sha256:" + "b" * 64},
        "closure": [
            {"role": "runtime", "locator": "registry.example/arc@sha256:" + "a" * 64, "digest": "sha256:" + "a" * 64},
            {"role": "executable", "locator": "file:///opt/arc/bin/arc", "digest": "sha256:" + "b" * 64},
        ],
    }


def _attach_execution_environment(db_session, calculation: Calculation) -> None:
    payload = _execution_environment_payload()
    payload["software_release"] = {"name": calculation.software_release.software.name, "version": calculation.software_release.version, "revision": calculation.software_release.revision, "build": calculation.software_release.build}
    calculation.execution_environment_manifest = resolve_execution_environment_manifest(db_session, ExecutionEnvironmentManifestPayload.model_validate(payload))


def test_manifest_is_recorded_provenance_and_never_changes_the_grade(db_session, _api_test_user) -> None:
    """Supplying a manifest enriches the snapshot; it does not buy a grade."""
    objects: dict[str, bytes] = {}
    calculation = _calculation(db_session, complete=True, created_by=_api_test_user, objects=objects)
    without_manifest = _evaluate(db_session, calculation, objects)
    assert without_manifest.grade is ReproducibilityGrade.rerunnable
    assert without_manifest.context_json["execution_environment"] == {"recorded": False}

    _attach_execution_environment(db_session, calculation)
    db_session.flush()
    with_manifest = _evaluate(db_session, calculation, objects)
    assert with_manifest.grade is without_manifest.grade
    environment = with_manifest.context_json["execution_environment"]
    assert environment["recorded"] is True
    assert environment["revalidates"] is True
    assert environment["tier"] == "container"
    # It is provenance, so it must not appear as a check in either direction.
    check_names = {item["name"] for item in with_manifest.passed + with_manifest.missing}
    assert "execution_environment_manifest" not in check_names

    stored = evaluate_and_append_reproducibility(db_session, record_type="calculation", record_id=calculation.id,
                                                    artifact_loader=_verified_loader(objects))
    assert stored.rubric_version == "v1"
    assert stored.grade is ReproducibilityGrade.rerunnable


def test_corrupt_or_unavailable_output_blocks_rerunnable(db_session, _api_test_user) -> None:
    objects: dict[str, bytes] = {}
    calculation = _calculation(db_session, complete=True, created_by=_api_test_user, objects=objects)
    _attach_execution_environment(db_session, calculation)
    calculation.artifacts.append(
        CalculationArtifact(
            kind=ArtifactKind.output_log,
            uri="file:///missing.log",
            filename="missing.log",
            sha256="d" * 64,
            bytes=4,
        )
    )
    db_session.flush()
    result = evaluate_reproducibility(
        db_session, record_type="calculation", record_id=calculation.id, artifact_loader=_verified_loader(objects)
    )
    assert result.grade is not ReproducibilityGrade.rerunnable
    assert "evidence_warnings_clear" in {item["name"] for item in result.missing}


@pytest.mark.parametrize("mutation", ["canonical", "normalized", "closure", "binding"])
def test_manifest_corruption_fails_closed(db_session, _api_test_user, mutation) -> None:
    objects: dict[str, bytes] = {}
    calculation = _calculation(db_session, complete=True, created_by=_api_test_user, objects=objects)
    _attach_execution_environment(db_session, calculation)
    manifest = calculation.execution_environment_manifest
    db_session.flush()
    # The database trigger deliberately makes production rows immutable; keep
    # this intentionally corrupt in-memory projection out of autoflush.
    if mutation == "canonical":
        manifest.canonical_json = {"broken": True}
    elif mutation == "normalized":
        manifest.runtime_locator = "registry.example/other@sha256:" + "e" * 64
    elif mutation == "binding":
        manifest.software_release_id += 1
    else:
        manifest.closure_json = [{"role": "runtime", "locator": "bad", "digest": "sha256:" + "c" * 64}]
    with db_session.no_autoflush:
        valid, evidence = manifest_integrity_evidence(manifest, calculation=calculation)
        assert valid is False
        if mutation == "canonical":
            assert evidence["reason"] == "execution_environment_manifest_invalid"
        else:
            assert evidence["release_bindings_valid"] is (mutation != "binding")
        assert _build_execution_environment_summary(calculation) is None
        result = evaluate_reproducibility(
            db_session,
            record_type="calculation",
            record_id=calculation.id,
            artifact_loader=_verified_loader(objects),
        )
    # Corruption is a storage-drift signal recorded on the snapshot. It does not
    # penalise the uploader's grade, which rests on deposited evidence only.
    assert result.grade is ReproducibilityGrade.rerunnable
    assert result.context_json["execution_environment"]["revalidates"] is False
    check_names = {item["name"] for item in result.passed + result.missing}
    assert "execution_environment_manifest" not in check_names


@pytest.mark.parametrize(
    ("record_type", "factory"),
    [
        ("thermo", "thermo"),
        ("kinetics", "kinetics"),
        ("statmech", "statmech"),
        ("transport", "transport"),
        ("network", "network"),
        ("network_solve", "network_solve"),
    ],
)
def test_never_awards_rerunnable_to_product_records(db_session, record_type, factory) -> None:
    """A closed source runtime never turns a derived product into a rerun claim."""
    species = make_species(db_session, inchi_key=next_inchi_key("PRODUCT"))
    entry = make_species_entry(db_session, species)
    if factory == "thermo":
        target = make_thermo_scalar(db_session, species_entry=entry)
    elif factory == "kinetics":
        reaction = make_chem_reaction(db_session, reactants=[species], products=[species])
        target = make_kinetics(
            db_session,
            reaction_entry=make_reaction_entry(
                db_session, reaction=reaction, reactant_entries=[entry], product_entries=[entry]
            ),
        )
    elif factory == "statmech":
        target = make_statmech(db_session, species_entry=entry)
    elif factory == "transport":
        target = make_transport(db_session, species_entry=entry)
    else:
        network = make_network(db_session, name=f"v2-product-{factory}-{entry.id}")
        target = network if factory == "network" else make_network_solve(db_session, network=network)

    result = evaluate_reproducibility(db_session, record_type=record_type, record_id=target.id)
    assert result.grade is not ReproducibilityGrade.rerunnable
    assert result.context_json["rubric"]["version"] == "v1"


@pytest.mark.parametrize("failure", ["unavailable", "integrity"])
def test_artifact_verification_failure_fails_closed(
    db_session,
    _api_test_user,
    failure,
) -> None:
    objects: dict[str, bytes] = {}
    calculation = _calculation(
        db_session,
        complete=False,
        created_by=_api_test_user,
        objects=objects,
    )

    def failing_loader(_sha256: str, *, expected_bytes: int | None = None) -> bytes:
        del expected_bytes
        if failure == "unavailable":
            raise ArtifactStorageUnavailable("mock unavailable")
        raise ArtifactIntegrityError("mock corrupt")

    result = evaluate_reproducibility(
        db_session,
        record_type="calculation",
        record_id=calculation.id,
        artifact_loader=failing_loader,
    )

    assert result.grade is ReproducibilityGrade.described
    assert "verified_output_artifact_bytes" in {item["name"] for item in result.missing}
    assert (
        result.warnings[0]["code"]
        == f"artifact_{'storage_unavailable' if failure == 'unavailable' else 'integrity_failed'}"
    )


def test_typed_output_absence_caps_calculation_at_described(
    db_session,
    _api_test_user,
) -> None:
    objects: dict[str, bytes] = {}
    calculation = _calculation(
        db_session,
        complete=False,
        created_by=_api_test_user,
        objects=objects,
        typed_output=False,
    )

    result = _evaluate(db_session, calculation, objects)

    assert result.grade is ReproducibilityGrade.described
    assert "typed_output_evidence" in {item["name"] for item in result.missing}


def test_software_reconciliation_mismatch_caps_calculation_at_described(
    db_session,
    _api_test_user,
) -> None:
    objects: dict[str, bytes] = {}
    calculation = _calculation(
        db_session,
        complete=True,
        created_by=_api_test_user,
        objects=objects,
    )
    calculation.software_reconciliation_status = SoftwareReconciliationStatus.mismatch
    db_session.flush()

    result = _evaluate(db_session, calculation, objects)

    assert result.grade is ReproducibilityGrade.described
    metadata = next(item for item in result.missing if item["name"] == "calculation_metadata")
    assert metadata["evidence"]["software_reconciliation_status"] == "mismatch"
    assert metadata["evidence"]["nonconflicting_software_identity"] is False


def test_whitespace_only_release_token_is_not_exact(
    db_session,
    _api_test_user,
) -> None:
    objects: dict[str, bytes] = {}
    calculation = _calculation(
        db_session,
        complete=True,
        created_by=_api_test_user,
        objects=objects,
    )
    calculation.software_release.version = "   "
    db_session.flush()

    result = _evaluate(db_session, calculation, objects)

    assert result.grade is ReproducibilityGrade.described
    metadata = next(item for item in result.missing if item["name"] == "calculation_metadata")
    assert metadata["evidence"]["exact_release_token"] is False


def test_oversized_output_fails_closed_without_loading(
    db_session,
    _api_test_user,
) -> None:
    objects: dict[str, bytes] = {}
    calculation = _calculation(
        db_session,
        complete=False,
        created_by=_api_test_user,
        objects=objects,
    )
    calculation.artifacts[0].bytes = MAX_ARTIFACT_VERIFICATION_BYTES + 1
    db_session.flush()
    calls = 0

    def loader(_sha256: str, *, expected_bytes: int | None = None) -> bytes:
        nonlocal calls
        calls += 1
        raise AssertionError(expected_bytes)

    result = evaluate_reproducibility(
        db_session,
        record_type="calculation",
        record_id=calculation.id,
        artifact_loader=loader,
    )

    assert calls == 0
    assert result.grade is ReproducibilityGrade.described
    assert result.warnings[0]["code"] == "artifact_verification_size_limit"


def test_multiple_outputs_respect_count_budget_and_keep_auditable(
    db_session,
    _api_test_user,
) -> None:
    objects: dict[str, bytes] = {}
    calculation = _calculation(
        db_session,
        complete=False,
        created_by=_api_test_user,
        objects=objects,
    )
    for index in range(MAX_ARTIFACT_VERIFICATION_COUNT + 1):
        db_session.add(
            CalculationArtifact(
                calculation_id=calculation.id,
                kind=ArtifactKind.output_log,
                uri=f"s3://repro/extra-{index}.log",
                sha256=hashlib.sha256(f"extra-{index}".encode()).hexdigest(),
                bytes=1,
                filename=f"extra-{index}.log",
                created_by=_api_test_user,
            )
        )
    db_session.flush()
    calls: list[int] = []

    def loader(_sha256: str, *, expected_bytes: int | None = None) -> bytes:
        calls.append(expected_bytes or 0)
        return b"verified"

    result = evaluate_reproducibility(
        db_session,
        record_type="calculation",
        record_id=calculation.id,
        artifact_loader=loader,
    )

    assert len(calls) == MAX_ARTIFACT_VERIFICATION_COUNT
    assert sum(calls) <= MAX_ARTIFACT_VERIFICATION_TOTAL_BYTES
    assert result.grade is ReproducibilityGrade.auditable
    assert any(warning["code"] == "artifact_verification_count_budget" for warning in result.warnings)


def test_multiple_outputs_respect_aggregate_byte_budget(
    db_session,
    _api_test_user,
) -> None:
    objects: dict[str, bytes] = {}
    calculation = _calculation(
        db_session,
        complete=False,
        created_by=_api_test_user,
        objects=objects,
    )
    for index in range(2):
        db_session.add(
            CalculationArtifact(
                calculation_id=calculation.id,
                kind=ArtifactKind.output_log,
                uri=f"s3://repro/large-{index}.log",
                sha256=hashlib.sha256(f"large-{index}".encode()).hexdigest(),
                bytes=30 * 1024 * 1024,
                filename=f"large-{index}.log",
                created_by=_api_test_user,
            )
        )
    db_session.flush()
    calls: list[int] = []

    def loader(_sha256: str, *, expected_bytes: int | None = None) -> bytes:
        calls.append(expected_bytes or 0)
        return b"verified"

    result = evaluate_reproducibility(
        db_session,
        record_type="calculation",
        record_id=calculation.id,
        artifact_loader=loader,
    )

    assert sum(calls) <= MAX_ARTIFACT_VERIFICATION_TOTAL_BYTES
    assert result.grade is ReproducibilityGrade.auditable
    assert any(warning["code"] == "artifact_verification_aggregate_budget" for warning in result.warnings)


def test_non_calculation_is_capped_at_described_and_preserves_source_roles(
    db_session,
    _api_test_user,
) -> None:
    objects: dict[str, bytes] = {}
    calculation = _calculation(
        db_session,
        complete=True,
        created_by=_api_test_user,
        objects=objects,
    )
    thermo = Thermo(
        species_entry_id=calculation.species_entry_id,
        scientific_origin=ScientificOriginKind.computed,
        h298_kj_mol=-10.0,
        created_by=_api_test_user,
    )
    db_session.add(thermo)
    db_session.flush()
    db_session.add(
        ThermoSourceCalculation(
            thermo_id=thermo.id,
            calculation_id=calculation.id,
            role=ThermoCalculationRole.sp,
        )
    )
    db_session.flush()

    calls = 0

    def loader(_sha256: str, *, expected_bytes: int | None = None) -> bytes:
        nonlocal calls
        calls += 1
        raise AssertionError(expected_bytes)

    result = evaluate_reproducibility(
        db_session,
        record_type="thermo",
        record_id=thermo.id,
        artifact_loader=loader,
    )

    assert calls == 0
    assert result.grade is ReproducibilityGrade.described
    assert result.context_json["source_roles"] == [
        {"calculation_id": calculation.id, "role": ThermoCalculationRole.sp.value}
    ]
    assert "record_type_audit_policy" in {item["name"] for item in result.missing}


def test_target_and_direct_source_mutations_change_context_hash(
    db_session,
    _api_test_user,
) -> None:
    objects: dict[str, bytes] = {}
    calculation = _calculation(
        db_session,
        complete=True,
        created_by=_api_test_user,
        objects=objects,
    )
    thermo = Thermo(
        species_entry_id=calculation.species_entry_id,
        scientific_origin=ScientificOriginKind.computed,
        h298_kj_mol=-10.0,
        created_by=_api_test_user,
    )
    db_session.add(thermo)
    db_session.flush()
    db_session.add(
        ThermoSourceCalculation(
            thermo_id=thermo.id,
            calculation_id=calculation.id,
            role=ThermoCalculationRole.sp,
        )
    )
    db_session.flush()
    loader = _verified_loader(objects)

    first = evaluate_and_append_reproducibility(
        db_session,
        record_type="thermo",
        record_id=thermo.id,
        artifact_loader=loader,
    )
    thermo.h298_kj_mol = -11.0
    db_session.flush()
    target_changed = evaluate_and_append_reproducibility(
        db_session,
        record_type="thermo",
        record_id=thermo.id,
        artifact_loader=loader,
    )
    calculation.parameters[0].raw_value = "very_tight"
    db_session.flush()
    source_changed = evaluate_and_append_reproducibility(
        db_session,
        record_type="thermo",
        record_id=thermo.id,
        artifact_loader=loader,
    )

    assert first.context_hash != target_changed.context_hash
    assert target_changed.context_hash != source_changed.context_hash


def test_transitive_parent_mutation_changes_context_hash(
    db_session,
    _api_test_user,
) -> None:
    objects: dict[str, bytes] = {}
    parent = _calculation(
        db_session,
        complete=True,
        created_by=_api_test_user,
        objects=objects,
    )
    child = _calculation(
        db_session,
        complete=True,
        created_by=_api_test_user,
        objects=objects,
    )
    db_session.add(
        CalculationDependency(
            parent_calculation_id=parent.id,
            child_calculation_id=child.id,
            dependency_role=CalculationDependencyRole.single_point_on,
        )
    )
    db_session.flush()
    loader = _verified_loader(objects)

    first = evaluate_and_append_reproducibility(
        db_session,
        record_type="calculation",
        record_id=child.id,
        artifact_loader=loader,
    )
    parent.parameters[0].raw_value = "ultratight"
    db_session.flush()
    second = evaluate_and_append_reproducibility(
        db_session,
        record_type="calculation",
        record_id=child.id,
        artifact_loader=loader,
    )

    # The grade is unchanged by the mutation; the point is that the snapshotted
    # evidence hash moves, which is what marks the stored assessment stale.
    assert first.grade is ReproducibilityGrade.rerunnable
    assert second.grade is first.grade
    assert first.context_hash != second.context_hash


def test_geometry_atom_coordinate_mutation_changes_snapshot_hash() -> None:
    geometry = Geometry(
        id=1,
        public_ref="geom_test",
        natoms=1,
        geom_hash="a" * 64,
        xyz_text="1\nH\nH 0 0 0",
    )
    atom = GeometryAtom(geometry_id=1, atom_index=1, element="H", x=0.0, y=0.0, z=0.0)
    geometry.atoms.append(atom)

    before = _snapshot_hash(_geometry_snapshot(geometry))
    atom.x = 0.125
    after = _snapshot_hash(_geometry_snapshot(geometry))

    assert before != after


def test_scan_coordinate_mutation_changes_snapshot_hash() -> None:
    coordinate = CalculationScanCoordinate(
        calculation_id=1,
        coordinate_index=1,
        coordinate_kind=ScanCoordinateKind.bond,
        atom1_index=1,
        atom2_index=2,
        step_count=8,
        step_size=0.1,
        value_unit=CoordinateUnit.angstrom,
    )

    before = _snapshot_hash(_rows([coordinate]))
    coordinate.step_size = 0.2
    after = _snapshot_hash(_rows([coordinate]))

    assert before != after


def test_registry_covers_all_addressable_assessment_types() -> None:
    assert SUPPORTED_REPRODUCIBILITY_RECORD_TYPES == frozenset(SubmissionRecordType)


def test_missing_record_fails_closed(db_session) -> None:
    with pytest.raises(ValueError, match="calculation record 999999999 does not exist"):
        evaluate_reproducibility(
            db_session,
            record_type="calculation",
            record_id=999_999_999,
        )


def test_insufficient_grade_migration_layers_on_current_head() -> None:
    migration = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "e9a3c5f7b1d2_add_insufficient_reproducibility_grade.py"
    ).read_text()

    assert 'down_revision: Union[str, Sequence[str], None] = "b8e3f1a9c2d4"' in migration
    assert "ADD VALUE IF NOT EXISTS 'insufficient' BEFORE 'described'" in migration
