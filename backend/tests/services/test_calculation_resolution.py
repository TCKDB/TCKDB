from __future__ import annotations

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session
from tckdb_schemas.stationary_point import (
    TAU_ANALYTIC_DEFAULT_CM1,
    TAU_FINITE_DIFFERENCE_ENERGY_CM1,
    TAU_PROTOCOL_NOT_RECORDED_CM1,
    TauBasis,
)

from app.db.models.app_user import AppUser
from app.db.models.calculation import Calculation, CalculationConstraint, CalculationFreqResult
from app.db.models.common import (
    AppUserRole,
    CalculationDependencyRole,
    CalculationType,
    ConstraintKind,
    RecordReviewStatus,
    SubmissionRecordType,
)
from app.db.models.execution_environment import ExecutionEnvironmentManifest
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.software import Software, SoftwareRelease
from app.db.models.workflow import WorkflowTool, WorkflowToolRelease
from app.schemas.fragments.calculation import (
    CalculationCreateRequest,
    CalculationWithResultsPayload,
)
from app.schemas.fragments.execution_environment import ExecutionEnvironmentManifestPayload
from app.services.calculation_resolution import (
    ExecutionEnvironmentManifestIntegrityError,
    assert_dependency_role_type_compatible,
    persist_calculation,
    resolve_and_persist_calculation_with_results,
    resolve_calculation_create_request,
    resolve_execution_environment_manifest,
)
from app.services.record_review import ensure_record_review, set_record_review_status


def _execution_environment() -> dict:
    return {
        "schema_version": "tckdb.execution-environment.v1",
        "software_release": {"name": "Gaussian", "version": "16"},
        "runtime": {"runtime_kind": "container", "image": "registry.example/arc@sha256:" + "a" * 64},
        "executable": {"locator": "file:///opt/arc/bin/arc", "digest": "sha256:" + "b" * 64},
        "closure": [
            {"role": "runtime", "locator": "registry.example/arc@sha256:" + "a" * 64, "digest": "sha256:" + "a" * 64},
            {"role": "executable", "locator": "file:///opt/arc/bin/arc", "digest": "sha256:" + "b" * 64},
        ],
    }


def _create_species(connection, *, inchi_key: str, smiles: str | None = None) -> int:
    return connection.execute(
        text("""
            INSERT INTO species (kind, smiles, inchi_key, charge, multiplicity, stereo_kind)
            VALUES ('molecule', :smiles, :inchi_key, 0, 1, 'achiral')
            RETURNING id
            """),
        {"smiles": smiles or inchi_key, "inchi_key": inchi_key},
    ).scalar_one()


def _create_species_entry(connection, species_id: int) -> int:
    return connection.execute(
        text("""
            INSERT INTO species_entry (species_id)
            VALUES (:species_id)
            RETURNING id
            """),
        {"species_id": species_id},
    ).scalar_one()


_INCHI_COUNTER = 0


def _next_inchi_key(prefix: str) -> str:
    global _INCHI_COUNTER
    _INCHI_COUNTER += 1
    stem = f"{prefix}{_INCHI_COUNTER:0>21}"
    return stem[:27]


def test_resolve_calculation_create_request_creates_and_reuses_refs(db_conn) -> None:
    with Session(db_conn) as session:
        with session.begin():
            species_id = _create_species(
                session.connection(), inchi_key=_next_inchi_key("CALCRESOLVE")
            )
            species_entry_id = _create_species_entry(session.connection(), species_id)

            request = CalculationCreateRequest(
                type="sp",
                species_entry_id=species_entry_id,
                software_release={
                    "name": "gaussian",
                    "version": "16",
                    "revision": "C.01",
                },
                workflow_tool_release={
                    "name": "ARC",
                    "version": "1.0",
                    "git_commit": "abc123",
                },
                level_of_theory={
                    "method": "wB97X-D",
                    "basis": "def2-TZVP",
                },
            )

            first = resolve_calculation_create_request(session, request)
            second = resolve_calculation_create_request(session, request)

            assert first.software_release_id == second.software_release_id
            assert first.workflow_tool_release_id == second.workflow_tool_release_id
            assert first.lot_id == second.lot_id

            assert session.scalar(select(Software).where(Software.name == "Gaussian"))
            assert session.scalar(
                select(SoftwareRelease).where(
                    SoftwareRelease.id == first.software_release_id
                )
            )
            assert session.scalar(
                select(WorkflowTool).where(WorkflowTool.name == "ARC")
            )
            assert session.scalar(
                select(WorkflowToolRelease).where(
                    WorkflowToolRelease.id == first.workflow_tool_release_id
                )
            )
            assert session.scalar(
                select(LevelOfTheory).where(LevelOfTheory.id == first.lot_id)
            )


def test_persist_calculation_persists_calculation(db_conn) -> None:
    with Session(db_conn) as session:
        with session.begin():
            species_id = _create_species(
                session.connection(), inchi_key=_next_inchi_key("CALCCREATE")
            )
            species_entry_id = _create_species_entry(session.connection(), species_id)

            request = CalculationCreateRequest(
                type="freq",
                species_entry_id=species_entry_id,
                software_release={"name": "ORCA", "version": "5.0.4"},
                level_of_theory={"method": "B3LYP", "basis": "6-31G(d)"},
            )
            resolved = resolve_calculation_create_request(session, request)
            calculation = persist_calculation(session, resolved)

            stored = session.scalar(
                select(Calculation).where(Calculation.id == calculation.id)
            )

            assert stored is not None
            assert stored.type.value == "freq"
            assert stored.species_entry_id == species_entry_id
            assert stored.software_release_id == resolved.software_release_id


def test_calculation_persistence_keeps_optional_environment_and_deduplicates(db_conn) -> None:
    """Alias-equivalent release declarations resolve to one shared manifest."""
    with Session(db_conn) as session:
        with session.begin():
            species_id = _create_species(session.connection(), inchi_key=_next_inchi_key("CALCENV"))
            species_entry_id = _create_species_entry(session.connection(), species_id)
            common = {
                "type": "sp", "species_entry_id": species_entry_id,
                "software_release": {"name": "gaussian", "version": "16"},
                "level_of_theory": {"method": "wb97xd", "basis": "def2-svp"},
            }
            absent = persist_calculation(session, resolve_calculation_create_request(session, CalculationCreateRequest(**common)))
            with_environment = CalculationCreateRequest(**common, execution_environment=_execution_environment())
            first = persist_calculation(session, resolve_calculation_create_request(session, with_environment))
            reordered = _execution_environment()
            reordered["software_release"] = {"name": "gaussian", "version": "16"}
            reordered["closure"].reverse()
            second = persist_calculation(
                session,
                resolve_calculation_create_request(
                    session,
                    CalculationCreateRequest(
                        **{**common, "software_release": {"name": "Gaussian", "version": "16"}},
                        execution_environment=reordered,
                    ),
                ),
            )
            assert absent.execution_environment_manifest_id is None
            assert first.execution_environment_manifest_id == second.execution_environment_manifest_id
            assert len(session.scalars(select(ExecutionEnvironmentManifest)).all()) == 1
            releases = session.scalars(
                select(SoftwareRelease)
                .join(Software)
                .where(
                    Software.name == "Gaussian",
                    SoftwareRelease.version == "16",
                    SoftwareRelease.revision.is_(None),
                    SoftwareRelease.build.is_(None),
                )
            ).all()
            assert len(releases) == 1
            assert releases[0].software.name == "Gaussian"


@pytest.mark.parametrize("field, value", [("software_release", {"name": "ORCA", "version": "5"}), ("workflow_tool_release", {"name": "ARC", "version": "1"})])
def test_calculation_resolution_rejects_manifest_release_binding_mismatch(db_conn, field, value) -> None:
    with Session(db_conn) as session, session.begin():
        species_id = _create_species(session.connection(), inchi_key=_next_inchi_key("CMM"))
        species_entry_id = _create_species_entry(session.connection(), species_id)
        environment = _execution_environment()
        environment[field] = value
        with pytest.raises(ExecutionEnvironmentManifestIntegrityError, match="release bindings"):
            resolve_calculation_create_request(session, CalculationCreateRequest(type="sp", species_entry_id=species_entry_id, software_release={"name": "Gaussian", "version": "16"}, level_of_theory={"method": "wb97xd"}, execution_environment=environment))


class _FakePsycopgDiagnostics:
    def __init__(self, constraint_name: str) -> None:
        self.constraint_name = constraint_name


class _FakePsycopgUniqueViolation(Exception):
    sqlstate = "23505"

    def __init__(self, constraint_name: str) -> None:
        self.diag = _FakePsycopgDiagnostics(constraint_name)


def _unique_violation(constraint_name: str) -> IntegrityError:
    return IntegrityError("insert", {}, _FakePsycopgUniqueViolation(constraint_name))


def test_execution_environment_resolver_recovers_exact_digest_unique_race(db_conn, monkeypatch) -> None:
    """A unique-race path uses a savepoint and leaves the outer transaction usable."""
    with Session(db_conn) as session:
        with session.begin():
            payload = ExecutionEnvironmentManifestPayload.model_validate(_execution_environment())
            existing = resolve_execution_environment_manifest(session, payload)
            assert existing is not None
            # Simulate the narrow post-select race.  The existing row is still
            # returned and subsequent work in the caller's transaction works.
            original_scalar = session.scalar
            calls = 0

            def race_scalar(statement, *args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 1:
                    return None
                return original_scalar(statement, *args, **kwargs)

            monkeypatch.setattr(session, "scalar", race_scalar)
            # The pre-existing row supplies a real psycopg unique violation
            # (with SQLSTATE and diagnostic constraint name) at the explicit
            # INSERT flush after the forced stale first lookup.
            recovered = resolve_execution_environment_manifest(session, payload)
            assert recovered.id == existing.id
            assert not session.new
            assert session.connection().execute(text("SELECT 1")).scalar_one() == 1


def test_execution_environment_resolver_reraises_exact_digest_unique_race_without_winner(db_conn, monkeypatch) -> None:
    with Session(db_conn) as session:
        with session.begin():
            payload = ExecutionEnvironmentManifestPayload.model_validate(_execution_environment())
            original_scalar = session.scalar
            def no_manifest_winner(statement, *args, **kwargs):
                entities = getattr(statement, "column_descriptions", ())
                if any(item.get("entity") is ExecutionEnvironmentManifest for item in entities):
                    return None
                return original_scalar(statement, *args, **kwargs)
            monkeypatch.setattr(session, "scalar", no_manifest_winner)
            monkeypatch.setattr(
                session,
                "flush",
                lambda *args, **kwargs: (_ for _ in ()).throw(
                    _unique_violation("uq_execution_environment_manifest_content_digest")
                ),
            )
            with pytest.raises(IntegrityError):
                resolve_execution_environment_manifest(session, payload)
            assert not session.new
            assert session.connection().execute(text("SELECT 1")).scalar_one() == 1


def test_execution_environment_resolver_reraises_unrelated_unique_violation_even_with_winner(
    db_conn, monkeypatch
) -> None:
    with Session(db_conn) as session:
        with session.begin():
            payload = ExecutionEnvironmentManifestPayload.model_validate(_execution_environment())
            existing = resolve_execution_environment_manifest(session, payload)
            assert existing is not None
            original_scalar = session.scalar
            calls = 0

            def race_scalar(statement, *args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 1:
                    return None
                return original_scalar(statement, *args, **kwargs)

            monkeypatch.setattr(session, "scalar", race_scalar)
            monkeypatch.setattr(
                session,
                "flush",
                lambda *args, **kwargs: (_ for _ in ()).throw(_unique_violation("uq_unrelated")),
            )
            with pytest.raises(IntegrityError):
                resolve_execution_environment_manifest(session, payload)
            assert not session.new
            assert session.connection().execute(text("SELECT 1")).scalar_one() == 1


def test_execution_environment_resolver_rejects_corrupt_digest_match(db_conn) -> None:
    with Session(db_conn) as session:
        try:
            payload = ExecutionEnvironmentManifestPayload.model_validate(_execution_environment())
            manifest = resolve_execution_environment_manifest(session, payload)
            manifest.runtime_locator = "registry.example/other@sha256:" + "e" * 64
            with session.no_autoflush, pytest.raises(ExecutionEnvironmentManifestIntegrityError):
                resolve_execution_environment_manifest(session, payload)
        finally:
            session.rollback()


def test_execution_environment_manifest_is_database_immutable_without_calculation_reference(db_conn) -> None:
    """The registry trigger protects orphan rows as well as shared rows."""
    with Session(db_conn) as session:
        with session.begin():
            manifest = resolve_execution_environment_manifest(
                session, ExecutionEnvironmentManifestPayload.model_validate(_execution_environment())
            )
            with pytest.raises(DBAPIError), session.begin_nested():
                session.execute(
                    text("UPDATE execution_environment_manifest SET runtime_locator = 'mutated' WHERE id = :id"),
                    {"id": manifest.id},
                )
            with pytest.raises(DBAPIError), session.begin_nested():
                session.execute(text("DELETE FROM execution_environment_manifest WHERE id = :id"), {"id": manifest.id})
            stored = session.get(ExecutionEnvironmentManifest, manifest.id)
            assert stored.runtime_locator == "registry.example/arc@sha256:" + "a" * 64


def test_unapproved_calculation_can_attach_and_replace_execution_environment(db_conn) -> None:
    with Session(db_conn) as session:
        with session.begin():
            species_id = _create_species(session.connection(), inchi_key=_next_inchi_key("CALCENVRAW"))
            entry_id = _create_species_entry(session.connection(), species_id)
            calc = persist_calculation(
                session,
                resolve_calculation_create_request(
                    session,
                    CalculationCreateRequest(
                        type="sp",
                        species_entry_id=entry_id,
                        software_release={"name": "Gaussian", "version": "16"},
                        level_of_theory={"method": "wb97xd", "basis": "def2-svp"},
                    ),
                ),
            )
            first = resolve_execution_environment_manifest(
                session, ExecutionEnvironmentManifestPayload.model_validate(_execution_environment())
            )
            second_data = _execution_environment()
            second_data["runtime"]["image"] = "registry.example/arc@sha256:" + "c" * 64
            second_data["closure"][0] = {"role": "runtime", "locator": "registry.example/arc@sha256:" + "c" * 64, "digest": "sha256:" + "c" * 64}
            second = resolve_execution_environment_manifest(
                session, ExecutionEnvironmentManifestPayload.model_validate(second_data)
            )
            calc.execution_environment_manifest_id = first.id
            session.flush()
            calc.execution_environment_manifest_id = second.id
            session.flush()
            assert calc.execution_environment_manifest_id == second.id


def test_database_rejects_manifest_binding_mismatch_on_attach_and_release_update(db_conn) -> None:
    with Session(db_conn) as session, session.begin():
        species_id = _create_species(session.connection(), inchi_key=_next_inchi_key("CALCENVDB"))
        entry_id = _create_species_entry(session.connection(), species_id)
        calc = persist_calculation(session, resolve_calculation_create_request(session, CalculationCreateRequest(type="sp", species_entry_id=entry_id, software_release={"name": "Gaussian", "version": "16"}, level_of_theory={"method": "wb97xd"})))
        mismatched_data = _execution_environment()
        mismatched_data["software_release"] = {"name": "ORCA", "version": "5"}
        manifest = resolve_execution_environment_manifest(session, ExecutionEnvironmentManifestPayload.model_validate(mismatched_data))
        with pytest.raises(DBAPIError), session.begin_nested():
            calc.execution_environment_manifest_id = manifest.id
            session.flush()
        matching = resolve_execution_environment_manifest(session, ExecutionEnvironmentManifestPayload.model_validate(_execution_environment()))
        calc.execution_environment_manifest_id = matching.id
        session.flush()
        with pytest.raises(DBAPIError), session.begin_nested():
            calc.software_release_id = manifest.software_release_id
            session.flush()


def test_approved_calculation_cannot_change_or_remove_execution_environment(db_conn) -> None:
    with Session(db_conn) as session:
        with session.begin():
            species_id = _create_species(session.connection(), inchi_key=_next_inchi_key("CALCENVAPP"))
            entry_id = _create_species_entry(session.connection(), species_id)
            manifest = resolve_execution_environment_manifest(
                session, ExecutionEnvironmentManifestPayload.model_validate(_execution_environment())
            )
            replacement_data = _execution_environment()
            replacement_data["runtime"]["image"] = "registry.example/arc@sha256:" + "c" * 64
            replacement_data["closure"][0] = {
                "role": "runtime",
                "locator": replacement_data["runtime"]["image"],
                "digest": "sha256:" + "c" * 64,
            }
            replacement = resolve_execution_environment_manifest(
                session, ExecutionEnvironmentManifestPayload.model_validate(replacement_data)
            )
            assert replacement.id != manifest.id
            calc = persist_calculation(
                session,
                resolve_calculation_create_request(
                    session,
                    CalculationCreateRequest(
                        type="sp",
                        species_entry_id=entry_id,
                        software_release={"name": "Gaussian", "version": "16"},
                        level_of_theory={"method": "wb97xd", "basis": "def2-svp"},
                        execution_environment=_execution_environment(),
                    ),
                ),
            )
            actor = AppUser(username=f"calc-env-{calc.id}", role=AppUserRole.curator)
            session.add(actor)
            session.flush()
            ensure_record_review(session, record_type=SubmissionRecordType.calculation, record_id=calc.id)
            set_record_review_status(session, record_type=SubmissionRecordType.calculation, record_id=calc.id,
                                     status=RecordReviewStatus.approved, actor=actor)
            with pytest.raises(DBAPIError), session.begin_nested():
                calc.execution_environment_manifest_id = None
                session.flush()
            with pytest.raises(DBAPIError), session.begin_nested():
                calc.execution_environment_manifest_id = replacement.id
                session.flush()


def test_resolve_and_persist_writes_constraints_for_non_scan_calc(db_conn) -> None:
    """Generic non-scan constraints persist via persist_calculation_result.

    Confirms the writer-path generalization: a constrained opt (no
    scan_result) carries constraints on the top-level
    ``CalculationWithResultsPayload.constraints`` field and lands rows
    in the ``calculation_constraint`` table.
    """
    with Session(db_conn) as session:
        with session.begin():
            species_id = _create_species(
                session.connection(), inchi_key=_next_inchi_key("CALCCONSTR")
            )
            species_entry_id = _create_species_entry(session.connection(), species_id)

            calc_upload = CalculationWithResultsPayload(
                type="opt",
                software_release={"name": "Gaussian", "version": "16"},
                level_of_theory={"method": "wB97X-D", "basis": "def2-TZVP"},
                opt_result={
                    "converged": True,
                    "n_steps": 12,
                    "final_energy_hartree": -76.4,
                },
                constraints=[
                    {
                        "constraint_index": 1,
                        "constraint_kind": "bond",
                        "atom1_index": 1,
                        "atom2_index": 2,
                        "target_value": 1.45,
                    },
                    {
                        "constraint_index": 2,
                        "constraint_kind": "dihedral",
                        "atom1_index": 1,
                        "atom2_index": 2,
                        "atom3_index": 3,
                        "atom4_index": 4,
                        "target_value": 60.0,
                    },
                ],
            )

            calc = resolve_and_persist_calculation_with_results(
                session,
                calc_upload,
                species_entry_id=species_entry_id,
            )
            session.flush()

            stored_constraints = session.scalars(
                select(CalculationConstraint)
                .where(CalculationConstraint.calculation_id == calc.id)
                .order_by(CalculationConstraint.constraint_index)
            ).all()

            assert len(stored_constraints) == 2
            assert stored_constraints[0].constraint_kind is ConstraintKind.bond
            assert stored_constraints[0].target_value == 1.45
            assert stored_constraints[1].constraint_kind is ConstraintKind.dihedral
            assert stored_constraints[1].atom4_index == 4


def test_resolve_and_persist_no_constraints_writes_no_rows(db_conn) -> None:
    """A non-scan calc with empty constraints list writes zero rows."""
    with Session(db_conn) as session:
        with session.begin():
            species_id = _create_species(
                session.connection(), inchi_key=_next_inchi_key("CALCNOCON")
            )
            species_entry_id = _create_species_entry(session.connection(), species_id)

            calc_upload = CalculationWithResultsPayload(
                type="freq",
                software_release={"name": "ORCA", "version": "5.0.4"},
                level_of_theory={"method": "B3LYP", "basis": "6-31G(d)"},
            )
            calc = resolve_and_persist_calculation_with_results(
                session,
                calc_upload,
                species_entry_id=species_entry_id,
            )
            session.flush()

            rows = session.scalars(
                select(CalculationConstraint).where(
                    CalculationConstraint.calculation_id == calc.id
                )
            ).all()
            assert rows == []


# ---------------------------------------------------------------------------
# ADR 0012's 2026-09-04 amendment: assumed Hessian method at persistence
# ---------------------------------------------------------------------------


def _freq_calc_upload(*, software_release: dict, method: str, parameters=None):
    return CalculationWithResultsPayload(
        type="freq",
        software_release=software_release,
        level_of_theory={"method": method, "basis": "def2-TZVP"},
        freq_result={"n_imag": 0},
        parameters=parameters,
    )


def _persist_freq(session, calc_upload) -> CalculationFreqResult:
    species_id = _create_species(
        session.connection(), inchi_key=_next_inchi_key("TAUWIRE")
    )
    species_entry_id = _create_species_entry(session.connection(), species_id)
    calc = resolve_and_persist_calculation_with_results(
        session, calc_upload, species_entry_id=species_entry_id
    )
    session.flush()
    return session.get(CalculationFreqResult, calc.id)


def test_a_recorded_hessian_method_is_never_overridden_by_an_assumption(db_conn) -> None:
    """A recorded ``freq.hessian_method`` always wins over the table.

    Gaussian + CCSD(T) would otherwise be assumed
    ``assumed_finite_difference_energy``; recording the method explicitly
    (here, as an analytic statement -- unrealistic chemistry, deliberately,
    so the assertion cannot be confused with what the table would have
    assumed anyway) must leave the *recorded* basis in place untouched.
    """
    with Session(db_conn) as session:
        with session.begin():
            calc_upload = _freq_calc_upload(
                software_release={"name": "Gaussian", "version": "16"},
                method="ccsd(t)",
                parameters=[
                    {
                        "raw_key": "freq",
                        "raw_value": "analytic",
                        "canonical_key": "freq.hessian_method",
                        "canonical_value": "analytic",
                    }
                ],
            )
            stored = _persist_freq(session, calc_upload)
            assert stored.imaginary_mode_tau_basis == TauBasis.analytic_default.value
            assert stored.imaginary_mode_tau_cm1 == TAU_ANALYTIC_DEFAULT_CM1


def test_absent_gaussian_b3lyp_assumes_analytic_default(db_conn) -> None:
    with Session(db_conn) as session:
        with session.begin():
            calc_upload = _freq_calc_upload(
                software_release={"name": "Gaussian", "version": "16"},
                method="b3lyp",
            )
            stored = _persist_freq(session, calc_upload)
            assert (
                stored.imaginary_mode_tau_basis
                == TauBasis.assumed_analytic_default.value
            )
            assert stored.imaginary_mode_tau_cm1 == TAU_ANALYTIC_DEFAULT_CM1


def test_absent_gaussian_ccsd_t_assumes_finite_difference_energy(db_conn) -> None:
    with Session(db_conn) as session:
        with session.begin():
            calc_upload = _freq_calc_upload(
                software_release={"name": "Gaussian", "version": "16"},
                method="ccsd(t)",
            )
            stored = _persist_freq(session, calc_upload)
            assert (
                stored.imaginary_mode_tau_basis
                == TauBasis.assumed_finite_difference_energy.value
            )
            assert stored.imaginary_mode_tau_cm1 == TAU_FINITE_DIFFERENCE_ENERGY_CM1


def test_absent_molpro_stays_protocol_not_recorded(db_conn) -> None:
    """Molpro is not in the assumption table, so nothing is assumed."""
    with Session(db_conn) as session:
        with session.begin():
            calc_upload = _freq_calc_upload(
                software_release={"name": "Molpro", "version": "2022.1"},
                method="b3lyp",
            )
            stored = _persist_freq(session, calc_upload)
            assert (
                stored.imaginary_mode_tau_basis
                == TauBasis.protocol_not_recorded.value
            )
            assert stored.imaginary_mode_tau_cm1 == TAU_PROTOCOL_NOT_RECORDED_CM1


class _DummyCalc:
    """Stand-in for an ORM ``Calculation`` row carrying only ``type``."""

    def __init__(self, calc_type: CalculationType) -> None:
        self.type = calc_type


def test_dependency_role_type_compatible_rejects_wire_optimized_from_with_freq_parent() -> None:
    """``optimized_from`` parent must be opt or path_search, even when the
    role is the wire-mirror enum class from ``tckdb_schemas.enums``.

    Regression: this comparison used ``is`` before the fix, which silently
    returned False for the wire-enum member and skipped the parent-type
    check in bundle workflows.
    """
    from tckdb_schemas.enums import (
        CalculationDependencyRole as WireCalculationDependencyRole,
    )

    parent_calc = _DummyCalc(calc_type=CalculationType.freq)

    with pytest.raises(ValueError, match="optimized_from"):
        assert_dependency_role_type_compatible(
            parent_calc=parent_calc,
            role=WireCalculationDependencyRole.optimized_from,
            context="test",
        )


def test_dependency_role_type_compatible_accepts_wire_optimized_from_with_opt_parent() -> None:
    """Happy path with the wire-enum role: opt parent is allowed."""
    from tckdb_schemas.enums import (
        CalculationDependencyRole as WireCalculationDependencyRole,
    )

    parent_calc = _DummyCalc(calc_type=CalculationType.opt)

    assert_dependency_role_type_compatible(
        parent_calc=parent_calc,
        role=WireCalculationDependencyRole.optimized_from,
        context="test",
    )


def test_dependency_role_type_compatible_accepts_wire_optimized_from_with_path_search_parent() -> None:
    """Happy path with the wire-enum role: path_search parent is allowed."""
    from tckdb_schemas.enums import (
        CalculationDependencyRole as WireCalculationDependencyRole,
    )

    parent_calc = _DummyCalc(calc_type=CalculationType.path_search)

    assert_dependency_role_type_compatible(
        parent_calc=parent_calc,
        role=WireCalculationDependencyRole.optimized_from,
        context="test",
    )


def test_dependency_role_type_compatible_accepts_backend_optimized_from_with_opt_parent() -> None:
    """The fix preserves backend-enum-role behavior: opt parent still
    allowed when the role is the backend enum class."""
    parent_calc = _DummyCalc(calc_type=CalculationType.opt)

    assert_dependency_role_type_compatible(
        parent_calc=parent_calc,
        role=CalculationDependencyRole.optimized_from,
        context="test",
    )
