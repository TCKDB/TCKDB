"""Persisting a program-run composite energy and its checks (ADR 0021, P3a).

The wire validators refuse most of what is tested here before a payload reaches
the service, so every service-level refusal is provoked with a payload built by
``model_construct`` (which skips validators, as ``model_copy`` does in the
adapters). That is the point of the service re-running the checks: a payload
that skipped the wire must still not be stored.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from tckdb_schemas.fragments.calculation import (
    CalculationWithResultsPayload,
    CompositeResultPayload,
    CompositeTermPayload,
)
from tckdb_schemas.fragments.refs import LevelOfTheoryRef

from app.api.error_contract import CodedValueError
from app.db.models.calculation import (
    Calculation,
    CalculationCompositeResult,
    CalculationCompositeTerm,
)
from app.db.models.common import (
    CalculationType,
    CompositeAssembly,
    CompositeSchemeKind,
    CompositeTermOperation,
    EnergyComponentKind,
)
from app.db.models.composite_scheme import CompositeScheme, CompositeSchemeTerm, LevelOfTheoryComposite
from app.services.calculation_resolution import (
    resolve_and_persist_calculation_with_results,
    resolve_level_of_theory_ref,
)
from app.services.composite_result_resolution import (
    collect_named_composite_deposit_warnings,
    persist_composite_result,
)

_SOFTWARE = {"name": "gaussian", "version": "16"}
_ELECTRONIC = -76.25
_ZPE = 0.0625
_COUNTER = 0


def _species_entry(session: Session) -> int:
    global _COUNTER
    _COUNTER += 1
    tag = f"CMPRES{_COUNTER:0>21}"[:27]
    species_id = session.connection().execute(
        text(
            "INSERT INTO species (kind, smiles, inchi_key, charge, multiplicity, stereo_kind) "
            "VALUES ('molecule', :s, :k, 0, 1, 'achiral') RETURNING id"
        ),
        {"s": tag, "k": tag},
    ).scalar_one()
    return session.connection().execute(
        text("INSERT INTO species_entry (species_id) VALUES (:s) RETURNING id"), {"s": species_id}
    ).scalar_one()


def _block(**overrides) -> CompositeResultPayload:
    values = {
        "assembly": "program_run",
        "electronic_energy_hartree": _ELECTRONIC,
        "e0_hartree": _ELECTRONIC + _ZPE,
        "recipe_zpe_hartree": _ZPE,
    }
    values.update(overrides)
    return CompositeResultPayload(**values)


def _persist(session, block, lot=None, *, calc_type=CalculationType.composite) -> Calculation:
    # ``resolve_and_persist_calculation_with_results`` reads the payload's refs as models.
    payload = CalculationWithResultsPayload(
        type=CalculationType.opt, software_release=_SOFTWARE, level_of_theory=lot or {"method": "CBS-QB3"}
    ).model_copy(update={"type": calc_type, "composite_result": block})
    return resolve_and_persist_calculation_with_results(
        session, payload, species_entry_id=_species_entry(session)
    )


def test_a_program_run_composite_is_stored_with_its_terms(db_conn) -> None:
    terms = [CompositeTermPayload(term_position=2, value_hartree=-76.0), CompositeTermPayload(term_position=0, value_hartree=-0.25)]
    with Session(db_conn) as session, session.begin():
        calc = _persist(session, _block(terms=terms))
        session.flush()
        row = session.get(CalculationCompositeResult, calc.id)
        assert row.assembly is CompositeAssembly.program_run
        assert (row.electronic_energy_hartree, row.e0_hartree, row.recipe_zpe_hartree) == (
            _ELECTRONIC,
            _ELECTRONIC + _ZPE,
            _ZPE,
        )
        stored = session.scalars(
            select(CalculationCompositeTerm)
            .where(CalculationCompositeTerm.calculation_id == calc.id)
            .order_by(CalculationCompositeTerm.term_position)
        ).all()
        assert [(t.term_position, t.value_hartree) for t in stored] == [(0, -0.25), (2, -76.0)]
        session.rollback()


def test_the_seam_refuses_a_composite_result_on_a_calculation_of_another_type(db_conn) -> None:
    with Session(db_conn) as session, session.begin():
        with pytest.raises(ValueError, match="only allowed on composite calculations"):
            _persist(session, _block(), {"method": "B3LYP", "basis": "6-31G(d)"}, calc_type=CalculationType.sp)
        session.rollback()


def test_an_assembled_composite_is_refused_until_user_schemes_exist(db_conn) -> None:
    with Session(db_conn) as session, session.begin():
        with pytest.raises(CodedValueError) as err:
            _persist(session, _block(assembly="assembled"))
        assert err.value.code == "composite_assembled_not_accepted"
        assert err.value.context["assembly"] == "assembled"
        session.rollback()


def test_a_program_run_without_software_is_refused(db_conn) -> None:
    with Session(db_conn) as session, session.begin():
        level = resolve_level_of_theory_ref(session, LevelOfTheoryRef(method="CBS-QB3"))
        calc = Calculation(
            type=CalculationType.composite,
            species_entry_id=_species_entry(session),
            lot_id=level.id,
            software_release_id=None,
        )
        session.add(calc)
        session.flush()
        with pytest.raises(CodedValueError) as err:
            persist_composite_result(session, calc, _block())
        assert err.value.code == "composite_program_run_requires_software"
        session.rollback()


def test_a_level_that_is_not_scheme_bound_is_refused_and_names_the_level(db_conn) -> None:
    with Session(db_conn) as session, session.begin():
        with pytest.raises(CodedValueError) as err:
            _persist(session, _block(), {"method": "B3LYP", "basis": "CBSB7"})
        assert err.value.code == "composite_level_not_scheme_bound"
        assert err.value.context == {"field": "level_of_theory", "level_of_theory": "B3LYP/CBSB7"}
        session.rollback()


def test_a_named_method_level_resolved_under_any_spelling_is_scheme_bound(db_conn) -> None:
    """``cbsqb3`` keys to ``cbs-qb3``: the alias reaches the same bound level."""
    with Session(db_conn) as session, session.begin():
        calc = _persist(session, _block(), {"method": "cbsqb3"})
        session.flush()
        assert session.get(LevelOfTheoryComposite, calc.lot_id) is not None
        session.rollback()


def test_the_service_reruns_the_arithmetic_for_a_payload_that_skipped_the_wire(db_conn) -> None:
    skipped = CompositeResultPayload.model_construct(
        assembly=CompositeAssembly.program_run,
        electronic_energy_hartree=_ELECTRONIC,
        e0_hartree=_ELECTRONIC + _ZPE + 1e-3,
        recipe_zpe_hartree=_ZPE,
        terms=[],
    )
    with Session(db_conn) as session, session.begin():
        with pytest.raises(Exception) as err:
            _persist(session, skipped)
        assert getattr(err.value, "code", None) == "composite_e0_inconsistent"
        session.rollback()


def test_terms_must_name_positions_the_scheme_has_when_it_has_terms(db_conn) -> None:
    with Session(db_conn) as session, session.begin():
        level = resolve_level_of_theory_ref(session, LevelOfTheoryRef(method="CBS-QB3"))
        scheme = session.scalar(
            select(CompositeScheme)
            .join(LevelOfTheoryComposite, LevelOfTheoryComposite.scheme_id == CompositeScheme.id)
            .where(LevelOfTheoryComposite.level_of_theory_id == level.id)
        )
        assert scheme.kind is CompositeSchemeKind.named_method
        for position in (0, 1):
            session.add(
                CompositeSchemeTerm(
                    scheme_id=scheme.id,
                    position=position,
                    operation=CompositeTermOperation.value,
                    energy_component=EnergyComponentKind.total,
                )
            )
        session.flush()
        known = [CompositeTermPayload(term_position=0, value_hartree=-76.0), CompositeTermPayload(term_position=1, value_hartree=-0.25)]
        calc = _persist(session, _block(terms=known))
        session.flush()
        assert session.get(CalculationCompositeResult, calc.id) is not None

        unknown = [CompositeTermPayload(term_position=0, value_hartree=-76.0), CompositeTermPayload(term_position=7, value_hartree=-0.25)]
        with pytest.raises(CodedValueError) as err:
            _persist(session, _block(terms=unknown))
        assert err.value.code == "composite_term_position_unknown"
        assert err.value.context["unknown_term_positions"] == [7]
        assert err.value.context["scheme_term_positions"] == [0, 1]
        session.rollback()


def test_a_scheme_without_terms_takes_the_producers_own_positions(db_conn) -> None:
    """A named method lists no terms, so there is nothing to key them to."""
    terms = [CompositeTermPayload(term_position=9, value_hartree=-76.0), CompositeTermPayload(term_position=3, value_hartree=-0.25)]
    with Session(db_conn) as session, session.begin():
        calc = _persist(session, _block(terms=terms))
        session.flush()
        assert len(session.get(Calculation, calc.id).composite_terms) == 2
        session.rollback()


# -- the warn tier ---------------------------------------------------------


def _calc_at(session, calc_type, lot):
    payload = CalculationWithResultsPayload(
        type=CalculationType.opt, software_release=_SOFTWARE, level_of_theory=lot
    ).model_copy(update={"type": calc_type})
    return resolve_and_persist_calculation_with_results(
        session, payload, species_entry_id=_species_entry(session)
    )


def test_an_opt_or_sp_at_a_named_method_level_each_warn_once_by_type(db_conn) -> None:
    with Session(db_conn) as session, session.begin():
        opt = _calc_at(session, CalculationType.opt, {"method": "G4"})
        sp = _calc_at(session, CalculationType.sp, {"method": "CBS-QB3"})
        freq = _calc_at(session, CalculationType.freq, {"method": "CBS-QB3"})
        plain = _calc_at(session, CalculationType.sp, {"method": "B3LYP", "basis": "6-31G(d)"})
        session.flush()
        warnings = collect_named_composite_deposit_warnings(session, [opt.id, sp.id, freq.id, plain.id])
        assert [w.code for w in warnings] == ["named_composite_deposited_as_opt", "named_composite_deposited_as_sp"]
        assert all(w.field == "level_of_theory" for w in warnings)
        assert "'G4'" in warnings[0].message and "'CBS-QB3'" in warnings[1].message
        session.rollback()


def test_only_the_calculations_asked_about_are_judged(db_conn) -> None:
    with Session(db_conn) as session, session.begin():
        asked = _calc_at(session, CalculationType.sp, {"method": "G4"})
        other = _calc_at(session, CalculationType.sp, {"method": "G4"})
        session.flush()
        assert len(collect_named_composite_deposit_warnings(session, [asked.id])) == 1
        assert len(collect_named_composite_deposit_warnings(session, [asked.id, other.id, asked.id])) == 2
        assert collect_named_composite_deposit_warnings(session, []) == []
        session.rollback()


def test_a_composite_calculation_is_never_warned_about(db_conn) -> None:
    with Session(db_conn) as session, session.begin():
        calc = _persist(session, _block())
        session.flush()
        assert collect_named_composite_deposit_warnings(session, [calc.id]) == []
        session.rollback()


# -- the database's own checks ---------------------------------------------


@pytest.mark.parametrize(
    "values",
    [
        {"recipe_zpe_hartree": -0.001},
        {"electronic_energy_hartree": float("inf")},
        {"e0_hartree": float("-inf")},
    ],
)
def test_the_result_table_refuses_what_it_can_check(db_conn, values) -> None:
    with Session(db_conn) as session, session.begin():
        calc = _persist(session, None)
        session.flush()
        with pytest.raises(IntegrityError):
            with session.begin_nested():
                session.add(
                    CalculationCompositeResult(calculation_id=calc.id, assembly=CompositeAssembly.program_run, **values)
                )
        session.rollback()


@pytest.mark.parametrize("values", [{"term_position": -1, "value_hartree": 1.0}, {"term_position": 0, "value_hartree": float("nan")}])
def test_the_term_table_refuses_what_it_can_check(db_conn, values) -> None:
    with Session(db_conn) as session, session.begin():
        calc = _persist(session, None)
        session.flush()
        with pytest.raises(IntegrityError):
            with session.begin_nested():
                session.add(CalculationCompositeTerm(calculation_id=calc.id, **values))
        session.rollback()


# -- the reproducibility digest includes the composite result ----------------


def test_the_reproducibility_snapshot_of_a_composite_calculation_carries_its_result_and_terms(db_conn) -> None:
    from app.services.reproducibility_rubric import _typed_output_snapshot

    terms = [
        CompositeTermPayload(term_position=0, value_hartree=-76.0),
        CompositeTermPayload(term_position=1, value_hartree=-0.25),
    ]
    with Session(db_conn) as session, session.begin():
        with_terms = _persist(session, _block(terms=terms))
        without_terms = _persist(session, _block())
        empty = _persist(
            session,
            _block(electronic_energy_hartree=None, e0_hartree=None, recipe_zpe_hartree=None),
        )
        session.flush()
        for calc in (with_terms, without_terms, empty):
            session.refresh(calc)

        present, snapshot = _typed_output_snapshot(with_terms)
        assert present is True
        assert snapshot["calculation_type"] == "composite"
        assert snapshot["result"]["electronic_energy_hartree"] == _ELECTRONIC
        assert [row["term_position"] for row in snapshot["terms"]] == [0, 1]

        # A different breakdown is a different snapshot, so a stored assessment goes stale.
        _, other = _typed_output_snapshot(without_terms)
        assert other["terms"] == []
        assert other != snapshot
        # No energy and no terms: the result row exists but says nothing.
        assert _typed_output_snapshot(empty)[0] is False
        session.rollback()
