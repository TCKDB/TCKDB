"""``composite_energy_verification``, end to end through the upload and read APIs (ADR 0021, P7a).

On ``main`` (fea447ec, after P5) no read said how far a composite energy had been checked:
the field did not exist. Held here, over HTTP, with the stored rows changed *after* upload where
the point is that the read recomputes:

* an assembled composite is ``recomputed`` from its stored inputs, **on read**: an input energy
  filled later is picked up (``unverifiable`` -> ``recomputed``), a stored input that changed
  afterwards is surfaced (``recompute_mismatch``), and nothing is cached or stored;
* a program run is ``log_reconciled`` only on a *recorded* confirming log check, otherwise
  ``program_reported``; a log that disagrees is named, not hidden;
* the field reaches the calculation read and, through ``levels``, the product reads.
"""

from __future__ import annotations

import base64

import pytest
from sqlalchemy import func, select

from app.db.models.calculation import (
    Calculation,
    CalculationCompositeLogCheck,
    CalculationCompositeResult,
    CalculationSPEnergyComponent,
    CalculationSPResult,
)
from app.db.models.common import CompositeLogOutcome
from tests import composite_p5_fixtures as f
from tests.api.test_api_composite_assembled import _calc_by_label, _ok
from tests.api.test_api_composite_log_reconciliation import (  # noqa: F401  (the fixture applies here too)
    _LOG,
    _composite,
    _deposit,
    _output_log,
    _stub_store_artifact,
)

_VERIFICATION_KEYS = {"state", "assembly", "reason", "difference_hartree", "tolerance_hartree"}


def _read(client, calc: Calculation) -> dict:
    resp = client.get(f"/api/v1/scientific/calculations/{calc.public_ref}")
    assert resp.status_code == 200, resp.text[:800]
    return resp.json()["record"]


def _verification(client, calc: Calculation) -> dict:
    """The block with its nulls dropped (the contract carries every key; ``None`` means nothing to add)."""
    block = _read(client, calc)["composite_energy_verification"]
    assert set(block) == _VERIFICATION_KEYS
    return {key: value for key, value in block.items() if value is not None}


def _set_sp_energy(db_session, calc: Calculation, value: float | None) -> None:
    row = db_session.get(CalculationSPResult, calc.id)
    if row is None:
        db_session.add(CalculationSPResult(calculation_id=calc.id, electronic_energy_hartree=value))
    else:
        row.electronic_energy_hartree = value
    db_session.flush()


# ---------------------------------------------------------------------------
# Assembled: recomputed on read
# ---------------------------------------------------------------------------


def test_an_assembled_composite_reads_as_recomputed_with_its_distance_from_the_total(client, db_session):
    body = _ok(client, f.bundle_b())
    composite = _calc_by_label(db_session, body, "cbs")
    verification = _verification(client, composite)
    assert verification["state"] == "recomputed"
    assert verification["assembly"] == "assembled"
    assert set(verification) <= _VERIFICATION_KEYS
    # Only the distance from the stated total is returned, never the recomputed total itself.
    assert abs(verification["difference_hartree"]) <= verification["tolerance_hartree"]
    assert verification["tolerance_hartree"] == pytest.approx(f.TOL_B, rel=1e-9)


def test_a_non_composite_calculation_has_no_verification(client, db_session):
    body = _ok(client, f.bundle_b())
    spq = _calc_by_label(db_session, body, "spq")
    record = _read(client, spq)
    assert record["composite_energy_verification"] is None


def test_an_input_energy_filled_later_is_picked_up_on_the_next_read(client, db_session):
    """Deposit with an input energy unstated; fill it afterwards; the same composite now recomputes."""
    payload = f.bundle_c()
    for conf in payload["conformers"]:
        for calc in conf["additional_calculations"]:
            if calc["key"] == "sp_base":
                calc.pop("sp_result")
    body = _ok(client, payload)
    assert "composite_total_unverifiable" in [w["code"] for w in body.get("warnings", [])]
    composite = _calc_by_label(db_session, body, "fpa")
    before = _verification(client, composite)
    assert before["state"] == "unverifiable"
    assert before["reason"] == "input_energy_not_stated"
    assert "difference_hartree" not in before and "tolerance_hartree" not in before

    _set_sp_energy(db_session, _calc_by_label(db_session, body, "sp_base"), f.E_BASE)
    after = _verification(client, composite)
    assert after["state"] == "recomputed"
    # Nothing about the composite was written: its stored total is the deposited number, as before.
    db_session.expire_all()
    assert db_session.get(CalculationCompositeResult, composite.id).electronic_energy_hartree == f.TOTAL_C


def test_an_input_that_changed_afterwards_is_a_recompute_mismatch_not_a_stale_recomputed(client, db_session):
    body = _ok(client, f.bundle_c())
    composite = _calc_by_label(db_session, body, "fpa")
    assert _verification(client, composite)["state"] == "recomputed"

    sp_ae = _calc_by_label(db_session, body, "sp_ae")
    _set_sp_energy(db_session, sp_ae, f.E_AE + 1e-3)
    changed = _verification(client, composite)
    assert changed["state"] == "recompute_mismatch"
    # The gap is reported (stated minus recomputed) and exceeds the tolerance that would have excused it.
    assert changed["difference_hartree"] == pytest.approx(-1e-3, abs=1e-9)
    assert abs(changed["difference_hartree"]) > changed["tolerance_hartree"]

    # Restoring the input restores the verdict: nothing was memoised.
    _set_sp_energy(db_session, sp_ae, f.E_AE)
    assert _verification(client, composite)["state"] == "recomputed"


def test_a_difference_just_inside_the_tolerance_is_still_recomputed(client, db_session):
    body = _ok(client, f.bundle_c())
    composite = _calc_by_label(db_session, body, "fpa")
    _set_sp_energy(db_session, _calc_by_label(db_session, body, "sp_base"), f.E_BASE + f.TOL_C * 0.5)
    assert _verification(client, composite)["state"] == "recomputed"
    _set_sp_energy(db_session, _calc_by_label(db_session, body, "sp_base"), f.E_BASE + f.TOL_C * 2)
    assert _verification(client, composite)["state"] == "recompute_mismatch"


def test_a_missing_component_makes_it_unverifiable_with_the_reason(client, db_session):
    body = _ok(client, f.bundle_b())
    composite = _calc_by_label(db_session, body, "cbs")
    spt = _calc_by_label(db_session, body, "spt")
    db_session.query(CalculationSPEnergyComponent).filter(CalculationSPEnergyComponent.calculation_id == spt.id).delete()
    db_session.flush()
    verification = _verification(client, composite)
    assert verification["state"] == "unverifiable"
    assert verification["reason"] == "component_not_stated"


def test_an_undeterminable_triples_convention_makes_it_unverifiable(client, db_session):
    """The stored correlation matches neither convention for the stored energy: not guessed, unverifiable."""
    body = _ok(client, f.bundle_b())
    composite = _calc_by_label(db_session, body, "cbs")
    spt = _calc_by_label(db_session, body, "spt")
    row = db_session.scalars(
        select(CalculationSPEnergyComponent).where(
            CalculationSPEnergyComponent.calculation_id == spt.id,
            CalculationSPEnergyComponent.component == "correlation",
        )
    ).one()
    row.value_hartree += 1e-3
    db_session.flush()
    verification = _verification(client, composite)
    assert verification["state"] == "unverifiable"
    assert verification["reason"] == "correlation_convention_undeterminable"


def test_an_assembled_composite_with_no_stored_total_is_unverifiable_not_recomputed(client, db_session):
    """Upload refuses a missing total (``composite_total_required``); a row written around it reads honestly."""
    body = _ok(client, f.bundle_b())
    composite = _calc_by_label(db_session, body, "cbs")
    db_session.get(CalculationCompositeResult, composite.id).electronic_energy_hartree = None
    db_session.flush()
    verification = _verification(client, composite)
    assert verification["state"] == "unverifiable"
    assert verification["reason"] == "no_total_deposited"


def test_removing_an_input_row_makes_it_unverifiable(client, db_session):
    from app.db.models.calculation import CalculationCompositeInput

    body = _ok(client, f.bundle_b())
    composite = _calc_by_label(db_session, body, "cbs")
    row = db_session.scalars(
        select(CalculationCompositeInput).where(
            CalculationCompositeInput.calculation_id == composite.id, CalculationCompositeInput.term_position == 0
        )
    ).one()
    db_session.delete(row)
    db_session.flush()
    assert _verification(client, composite)["state"] == "unverifiable"


def test_a_missing_input_of_a_total_term_is_unverifiable_not_a_recomputed_zero(client, db_session):
    """A term that reads an input's total: with the input row gone there is no number, and zero is not one."""
    from app.db.models.calculation import CalculationCompositeInput

    body = _ok(client, f.bundle_c())
    composite = _calc_by_label(db_session, body, "fpa")
    base = _calc_by_label(db_session, body, "sp_base")
    row = db_session.scalars(
        select(CalculationCompositeInput).where(
            CalculationCompositeInput.calculation_id == composite.id,
            CalculationCompositeInput.input_calculation_id == base.id,
        )
    ).one()
    db_session.delete(row)
    db_session.flush()
    verification = _verification(client, composite)
    assert verification["state"] == "unverifiable"
    assert verification["reason"] == "input_energy_not_stated"


# ---------------------------------------------------------------------------
# Program run: from the recorded log check, never from a log
# ---------------------------------------------------------------------------


def _program_run(client, db_session, **result) -> Calculation:
    return db_session.get(Calculation, _deposit(client, _composite(**result)))


def _attach_log(client, calc: Calculation, **kwargs):
    resp = client.post(f"/api/v1/calculations/{calc.id}/artifacts", json={"artifacts": [_output_log(**kwargs)]})
    assert resp.status_code == 201, resp.text
    return resp


def test_a_program_run_with_no_log_is_program_reported(client, db_session):
    calc = _program_run(client, db_session)
    verification = _verification(client, calc)
    assert verification == {"state": "program_reported", "assembly": "program_run"}


def test_a_program_run_with_a_confirming_log_is_log_reconciled(client, db_session):
    calc = _program_run(client, db_session)
    _attach_log(client, calc)
    assert _verification(client, calc) == {"state": "log_reconciled", "assembly": "program_run"}
    db_session.expire_all()
    rows = db_session.scalars(
        select(CalculationCompositeLogCheck).where(CalculationCompositeLogCheck.calculation_id == calc.id)
    ).all()
    assert [r.outcome for r in rows] == [CompositeLogOutcome.confirmed]


def test_the_read_never_parses_the_log(client, db_session, monkeypatch):
    """The state is read from the recorded check: a read with the parser removed is unchanged."""
    calc = _program_run(client, db_session)
    _attach_log(client, calc)

    def boom(*args, **kwargs):  # pragma: no cover - failing is the point
        raise AssertionError("a read must not parse a log")

    monkeypatch.setattr("app.services.composite_energy_reconciliation.parse_gaussian_composite_summary", boom)
    monkeypatch.setattr("app.services.artifact_storage.load_artifact_bytes", boom)
    assert _verification(client, calc)["state"] == "log_reconciled"


def test_a_log_that_disagrees_is_named_and_does_not_confirm(client, db_session):
    calc = _program_run(client, db_session, e0_hartree=-283.819775 + 0.01, electronic_energy_hartree=None)
    _attach_log(client, calc)
    verification = _verification(client, calc)
    assert verification == {"state": "program_reported", "assembly": "program_run", "reason": "log_mismatch"}


def test_a_log_of_another_method_is_named(client, db_session):
    calc = db_session.get(Calculation, _deposit(client, _composite(level="CBS-4M")))
    _attach_log(client, calc)
    assert _verification(client, calc) == {
        "state": "program_reported",
        "assembly": "program_run",
        "reason": "log_method_mismatch",
    }


def test_a_disagreement_beats_a_confirmation_from_another_log(client, db_session):
    """Two logs, one confirming and one not: a number two logs disagree about is not confirmed."""
    calc = _program_run(client, db_session)
    _attach_log(client, calc)
    assert _verification(client, calc)["state"] == "log_reconciled"
    db_session.add(CalculationCompositeLogCheck(calculation_id=calc.id, artifact_sha256="a" * 64, outcome=CompositeLogOutcome.mismatch))
    db_session.flush()
    assert _verification(client, calc) == {
        "state": "program_reported",
        "assembly": "program_run",
        "reason": "log_mismatch",
    }


def test_an_unreadable_log_leaves_it_program_reported_with_a_reason(client, db_session):
    calc = _program_run(client, db_session)
    # The head of the real log: unmistakably Gaussian, but cut before the composite summary block.
    head = {
        "kind": "output_log",
        "filename": "head.out",
        "content_base64": base64.b64encode(_LOG[:3000]).decode(),
    }
    resp = client.post(f"/api/v1/calculations/{calc.id}/artifacts", json={"artifacts": [head]})
    assert resp.status_code == 201, resp.text
    assert _verification(client, calc) == {
        "state": "program_reported",
        "assembly": "program_run",
        "reason": "log_did_not_confirm",
    }


def test_the_same_log_uploaded_twice_is_one_observation(client, db_session):
    calc = _program_run(client, db_session)
    _attach_log(client, calc)
    _attach_log(client, calc, filename="again.log")
    db_session.expire_all()
    count = db_session.scalar(
        select(func.count()).select_from(CalculationCompositeLogCheck).where(CalculationCompositeLogCheck.calculation_id == calc.id)
    )
    assert count == 1
    assert _verification(client, calc)["state"] == "log_reconciled"


def test_a_program_run_stating_no_energy_is_unverifiable_not_reported(client, db_session):
    calc = _program_run(
        client, db_session, e0_hartree=None, electronic_energy_hartree=None, recipe_zpe_hartree=None
    )
    assert _verification(client, calc) == {
        "state": "unverifiable",
        "assembly": "program_run",
        "reason": "no_energy_stated",
    }


def test_a_log_attached_to_a_non_composite_calculation_records_nothing(client, db_session):
    """The recorded check is the composite hook's: an sp with the same log gets none."""
    resp = client.post(
        "/api/v1/uploads/conformers",
        json={
            "species_entry": {"smiles": "O", "charge": 0, "multiplicity": 1},
            "geometry": {"xyz_text": f.WATER_XYZ},
            "calculation": f.sp("p", f.QZ, -76.0),
        },
    )
    assert resp.status_code == 201, resp.text
    calc_id = resp.json()["primary_calculation"]["calculation_id"]
    resp = client.post(f"/api/v1/calculations/{calc_id}/artifacts", json={"artifacts": [_output_log()]})
    assert resp.status_code == 201, resp.text
    db_session.expire_all()
    assert db_session.scalar(select(func.count()).select_from(CalculationCompositeLogCheck)) == 0


def test_the_log_outcomes_are_the_reconciliation_actions():
    """The recorded vocabulary is exactly what the reconciliation concludes; a drift would crash the hook."""
    from app.services.composite_energy_reconciliation import CompositeEnergyAction

    assert {a.value for a in CompositeEnergyAction} == {o.value for o in CompositeLogOutcome}


# ---------------------------------------------------------------------------
# Direct calls: values a service computes, on rows no API validated
# ---------------------------------------------------------------------------


def test_the_service_returns_nothing_for_a_calculation_that_is_not_a_composite(client, db_session):
    from app.services.composite_verification import verify_composite_calculation, verify_composite_calculations

    body = _ok(client, f.bundle_b())
    spq = _calc_by_label(db_session, body, "spq")
    assert verify_composite_calculation(db_session, spq.id) is None
    assert verify_composite_calculations(db_session, [None, spq.id, 10**9]) == {}
    assert verify_composite_calculations(db_session, []) == {}


def test_a_composite_calculation_with_no_result_row_is_unverifiable(client, db_session):
    from app.services.composite_verification import verify_composite_calculation

    calc = _program_run(client, db_session)
    db_session.delete(db_session.get(CalculationCompositeResult, calc.id))
    db_session.flush()
    verification = verify_composite_calculation(db_session, calc.id)
    assert verification is not None
    assert (verification.state.value, verification.reason) == ("unverifiable", "no_result_stated")


def test_an_assembled_composite_at_a_level_bound_to_no_scheme_is_unverifiable(client, db_session):
    from app.db.models.composite_scheme import LevelOfTheoryComposite
    from app.services.composite_verification import verify_composite_calculation

    body = _ok(client, f.bundle_b())
    composite = _calc_by_label(db_session, body, "cbs")
    db_session.query(LevelOfTheoryComposite).filter(LevelOfTheoryComposite.level_of_theory_id == composite.lot_id).delete()
    db_session.flush()
    verification = verify_composite_calculation(db_session, composite.id)
    assert verification is not None
    assert (verification.state.value, verification.reason) == ("unverifiable", "scheme_not_bound")


def test_the_program_run_rule_on_an_unvalidated_result():
    """``_program_run`` over a result no validator saw (every number None, or a sentinel set of outcomes)."""
    from types import SimpleNamespace

    from app.services.composite_verification import _program_run as rule

    empty = SimpleNamespace(electronic_energy_hartree=None, e0_hartree=None, recipe_zpe_hartree=None)
    stated = SimpleNamespace(electronic_energy_hartree=None, e0_hartree=-1.0, recipe_zpe_hartree=None)
    none = frozenset()
    assert rule(empty, frozenset({CompositeLogOutcome.confirmed})).state.value == "unverifiable"
    assert rule(stated, none).state.value == "program_reported"
    assert rule(stated, frozenset({CompositeLogOutcome.confirmed})).state.value == "log_reconciled"
    assert rule(stated, frozenset({CompositeLogOutcome.available})).state.value == "program_reported"
    both = frozenset({CompositeLogOutcome.confirmed, CompositeLogOutcome.mismatch})
    assert rule(stated, both).reason == "log_mismatch"
    both = frozenset({CompositeLogOutcome.confirmed, CompositeLogOutcome.method_mismatch})
    assert rule(stated, both).reason == "log_method_mismatch"


def test_verifying_many_composites_costs_the_same_statements_as_one(client, db_session):
    """Bulk: the statement count does not grow with the composites (a per-composite query would)."""
    from sqlalchemy import event

    from app.services.composite_verification import verify_composite_calculations

    ids = []
    for _ in range(3):
        ids.append(db_session.get(Calculation, _deposit(client, _composite())).id)
    body = _ok(client, f.bundle_b())
    ids.append(_calc_by_label(db_session, body, "cbs").id)

    def count(selected: list[int]) -> int:
        statements: list[str] = []

        def before(conn, cursor, statement, *args):
            statements.append(statement)

        engine = db_session.get_bind()
        event.listen(engine, "before_cursor_execute", before)
        try:
            verify_composite_calculations(db_session, selected)
        finally:
            event.remove(engine, "before_cursor_execute", before)
        return len(statements)

    assert count(ids[:1]) >= 1
    assert count(ids[:3]) == count(ids[:1])  # three program runs cost what one does
    assert count(ids) <= count(ids[:1]) + 12  # adding an assembled one costs a fixed number of statements
    assert count(ids + ids) == count(ids)
