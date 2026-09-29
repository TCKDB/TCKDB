"""The read-only measurement for DR-0028 edge/role mismatches (#581)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.db.models.common import CalculationDependencyRole, CalculationType
from tests.services.scientific_read._factories import (
    attach_dependency,
    make_calculation,
    make_lot,
    make_species,
    make_species_entry,
)

_SCRIPT = (
    Path(__file__).parents[2] / "scripts" / "ops" / "count_mismatched_dependency_edges.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("count_mismatched", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_counts_only_edges_whose_parent_type_breaks_the_role(db_session):
    module = _load()
    entry = make_species_entry(db_session, species=make_species(db_session, smiles="CCO"))
    lot = make_lot(db_session)

    def calc(kind: CalculationType):
        return make_calculation(
            db_session, type=kind, species_entry_id=entry.id, lot_id=lot.id
        )

    opt, sp, freq, path = (
        calc(CalculationType.opt),
        calc(CalculationType.sp),
        calc(CalculationType.freq),
        calc(CalculationType.path_search),
    )
    sp_child, freq_child, opt_child, opt_child2 = (
        calc(CalculationType.sp),
        calc(CalculationType.freq),
        calc(CalculationType.opt),
        calc(CalculationType.opt),
    )
    # Legal: freq on opt, and optimized_from a path search.
    attach_dependency(
        db_session, parent=opt, child=freq, role=CalculationDependencyRole.freq_on
    )
    attach_dependency(
        db_session,
        parent=path,
        child=opt_child,
        role=CalculationDependencyRole.optimized_from,
    )
    # Forbidden: optimized_from under an sp (only opt / path_search allowed),
    # single_point_on under an sp, freq_on under a freq.
    attach_dependency(
        db_session,
        parent=sp,
        child=opt_child2,
        role=CalculationDependencyRole.optimized_from,
    )
    attach_dependency(
        db_session,
        parent=sp,
        child=sp_child,
        role=CalculationDependencyRole.single_point_on,
    )
    attach_dependency(
        db_session,
        parent=freq,
        child=freq_child,
        role=CalculationDependencyRole.freq_on,
    )
    db_session.flush()

    total, rows = module.count_mismatched(db_session)

    assert total == 5
    assert sorted(rows) == [
        ("freq_on", "freq", "freq", 1),
        ("optimized_from", "sp", "opt", 1),
        ("single_point_on", "sp", "sp", 1),
    ]


def test_a_clean_database_reports_nothing(db_session):
    module = _load()
    total, rows = module.count_mismatched(db_session)
    assert (total, rows) == (0, [])


def test_the_measurement_transaction_cannot_write(db_engine):
    module = _load()
    with Session(bind=db_engine) as session:
        module.count_mismatched_read_only(session)
        with pytest.raises(Exception, match="read-only"):
            session.execute(text("CREATE TEMP TABLE must_not_exist (i int)"))


def _patch_session_local(monkeypatch, factory):
    import app.api.deps as deps

    monkeypatch.setattr(deps, "SessionLocal", factory)


def test_main_exits_zero_on_a_clean_database(db_engine, monkeypatch, capsys):
    module = _load()
    _patch_session_local(monkeypatch, lambda: Session(bind=db_engine))
    assert module.main() == module.EXIT_OK
    assert "mismatched edges:             0" in capsys.readouterr().out


def test_main_exits_one_when_edges_are_mismatched(db_engine, monkeypatch, capsys):
    module = _load()
    _patch_session_local(monkeypatch, lambda: Session(bind=db_engine))
    monkeypatch.setattr(
        module,
        "count_mismatched_read_only",
        lambda session: (3, [("freq_on", "sp", "freq", 2)]),
    )
    assert module.main() == module.EXIT_MISMATCHED == 1
    assert "role=freq_on parent=sp child=freq: 2" in capsys.readouterr().out


def test_main_exits_two_when_the_database_cannot_be_reached(monkeypatch, capsys):
    module = _load()

    def unreachable():
        raise OperationalError("connect", {}, Exception("refused"))

    _patch_session_local(monkeypatch, unreachable)
    assert module.main() == module.EXIT_ERROR == 2
    assert "could not run" in capsys.readouterr().err
