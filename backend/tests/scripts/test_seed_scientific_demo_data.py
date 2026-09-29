"""``seed_scientific_demo_data.py`` runs end to end on a database at head.

The script writes straight through the ORM, so every guard the schema has
gained since it was written applies to it, and nothing else would notice
when one starts refusing its rows. That happened: the enthalpy-reference
trigger from #520 refused its undeclared ``h298_kj_mol`` and the script
stopped working (#564), found only by hand while verifying something else.
No test ran it at all.

So this test runs it the way a person does -- as a subprocess, with
``--yes``, committing into a database made by nothing but
``alembic upgrade head`` -- and then reads back what landed. The row counts
are exact, and are checked against the database rather than against the
counts the script prints, which are hand-maintained and could drift from
what it writes.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, selectinload
from tckdb_schemas.enthalpy_reference import enthalpy_reference_error

from app.db.models.calculation import Calculation, CalculationOptResult, CalculationSPResult
from app.db.models.common import EnthalpyReferenceKind
from app.db.models.geometry import Geometry
from app.db.models.kinetics import Kinetics
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.reaction import ChemReaction, ReactionEntry
from app.db.models.species import ConformerGroup, ConformerObservation, Species, SpeciesEntry
from app.db.models.thermo import Thermo, ThermoNASA
from tests import conftest
from tests.conftest import scratch_database_name

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_ROOT.parent

#: What one run of the script must leave in an empty database, per table.
EXPECTED_ROWS = {
    LevelOfTheory: 1,
    Species: 6,
    SpeciesEntry: 6,
    Thermo: 2,
    ThermoNASA: 1,
    Geometry: 2,
    Calculation: 4,
    CalculationOptResult: 3,
    CalculationSPResult: 1,
    ConformerGroup: 1,
    ConformerObservation: 1,
    ChemReaction: 2,
    ReactionEntry: 2,
    Kinetics: 2,
}


def _subprocess_env(db_name: str) -> dict[str, str]:
    """Environment pointing at ``db_name``, importing **this checkout**.

    ``app`` and ``tckdb_schemas`` are installed editable from whichever
    checkout ran ``pip install -e``; inside a worktree that is the other one.
    Putting this checkout first is what makes the subprocess run the script
    under review.
    """
    env = conftest._db_env(db_name)
    schemas = REPO_ROOT / "schemas" / "python" / "tckdb-schemas"
    rest = [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p]
    env["PYTHONPATH"] = os.pathsep.join([str(BACKEND_ROOT), str(schemas), *rest])
    return env


@pytest.fixture
def scratch_db():
    """A database made by nothing but ``alembic upgrade head``; dropped afterwards."""
    db_name = scratch_database_name("seed_demo_data")
    conftest._recreate_test_database(db_name)
    try:
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=BACKEND_ROOT,
            env=_subprocess_env(db_name),
            check=True,
            capture_output=True,
            text=True,
        )
        yield db_name
    finally:
        conftest._drop_test_database(db_name)


def _has_enthalpy_content(thermo: Thermo) -> bool:
    return bool(
        thermo.h298_kj_mol is not None
        or thermo.nasa is not None
        or thermo.nasa9_intervals
        or (thermo.wilhoit is not None and thermo.wilhoit.h0_kj_mol is not None)
        or any(p.h_kj_mol is not None or p.g_kj_mol is not None for p in thermo.points)
    )


def test_seed_script_commits_the_demo_dataset_on_a_database_at_head(scratch_db):
    result = subprocess.run(
        [sys.executable, "-m", "scripts.seed_scientific_demo_data", "--yes"],
        cwd=BACKEND_ROOT,
        env=_subprocess_env(scratch_db),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"seed script exited {result.returncode}\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
    assert f"/{scratch_db}" in result.stdout, "the script wrote to some other database"

    engine = create_engine(conftest._database_url(scratch_db), future=True)
    try:
        with Session(engine) as session:
            landed = {
                model: session.scalar(select(func.count()).select_from(model))
                for model in EXPECTED_ROWS
            }
            thermos = session.scalars(
                select(Thermo).options(
                    selectinload(Thermo.nasa),
                    selectinload(Thermo.nasa9_intervals),
                    selectinload(Thermo.wilhoit),
                    selectinload(Thermo.points),
                )
            ).all()

            assert {m.__tablename__: n for m, n in landed.items()} == {
                m.__tablename__: n for m, n in EXPECTED_ROWS.items()
            }

            # Both seeded rows carry enthalpy content (an h298; one also a
            # NASA fit). Counting them first keeps the loop from passing on
            # an empty list.
            with_content = [t for t in thermos if _has_enthalpy_content(t)]
            assert len(with_content) == EXPECTED_ROWS[Thermo]
            for thermo in with_content:
                assert thermo.enthalpy_reference_kind is EnthalpyReferenceKind.formation_298k
                assert thermo.reference_pressure_bar == 1.0
                assert thermo.note == "TCKDB demo data"
            # And the shared rule the upload path applies agrees with every
            # stored row, including one that would carry a declaration with
            # no enthalpy content behind it.
            assert [enthalpy_reference_error(t) for t in thermos] == [None] * len(thermos)
    finally:
        engine.dispose()
