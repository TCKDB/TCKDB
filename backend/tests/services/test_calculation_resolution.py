from __future__ import annotations

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.models.calculation import Calculation, CalculationConstraint
from app.db.models.common import (
    CalculationDependencyRole,
    CalculationType,
    ConstraintKind,
)
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.software import Software, SoftwareRelease
from app.db.models.workflow import WorkflowTool, WorkflowToolRelease
from app.schemas.fragments.calculation import (
    CalculationCreateRequest,
    CalculationWithResultsPayload,
)
from app.services.calculation_resolution import (
    assert_dependency_role_type_compatible,
    persist_calculation,
    resolve_and_persist_calculation_with_results,
    resolve_calculation_create_request,
)


def _create_species(connection, *, inchi_key: str, smiles: str = "[H]") -> int:
    return connection.execute(
        text("""
            INSERT INTO species (kind, smiles, inchi_key, charge, multiplicity, stereo_kind)
            VALUES ('molecule', :smiles, :inchi_key, 0, 1, 'achiral')
            RETURNING id
            """),
        {"smiles": smiles, "inchi_key": inchi_key},
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


def test_resolve_calculation_create_request_creates_and_reuses_refs(db_engine) -> None:
    with Session(db_engine) as session:
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


def test_persist_calculation_persists_calculation(db_engine) -> None:
    with Session(db_engine) as session:
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


def test_resolve_and_persist_writes_constraints_for_non_scan_calc(db_engine) -> None:
    """Generic non-scan constraints persist via persist_calculation_result.

    Confirms the writer-path generalization: a constrained opt (no
    scan_result) carries constraints on the top-level
    ``CalculationWithResultsPayload.constraints`` field and lands rows
    in the ``calculation_constraint`` table.
    """
    with Session(db_engine) as session:
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


def test_resolve_and_persist_no_constraints_writes_no_rows(db_engine) -> None:
    """A non-scan calc with empty constraints list writes zero rows."""
    with Session(db_engine) as session:
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
