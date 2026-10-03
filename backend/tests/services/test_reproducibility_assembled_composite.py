"""An assembled composite is reproducible only if its inputs are (ADR 0021, P7a).

On ``main`` (fea447ec) the reproducibility rubric graded an assembled composite on the evidence of a
program run it never had: no software, no output log, no preserved inputs, no parameters. It could
never reach ``auditable``; with no literature either it was ``insufficient`` whatever its inputs were.
Now its evidence *is* its inputs':

* each input calculation is graded by this same rubric, and the composite reaches a level only when
  every input does (``composite_inputs_auditable`` / ``composite_inputs_rerunnable``);
* its stated total must follow from the stored inputs, recomputed now
  (``composite_total_follows_from_inputs``), or it stays below ``auditable``;
* nothing else changes: a program-run composite, a single point, every other record keep exactly the
  checks they had.

The grade moves when an input does, because the inputs are graded on every evaluation.
"""

from __future__ import annotations

import hashlib

import pytest

from app.db.models.calculation import (
    Calculation,
    CalculationArtifact,
    CalculationParameter,
    CalculationSPResult,
)
from app.db.models.common import ArtifactKind, ParameterSource, ReproducibilityGrade
from app.services.reproducibility_assessment import (
    is_reproducibility_assessment_context_current,
)
from app.services.reproducibility_rubric import evaluate_and_append_reproducibility, evaluate_reproducibility
from tests import composite_p5_fixtures as f
from tests.api.test_api_composite_assembled import _calc_by_label, _ok
from tests.services.test_reproducibility_rubric import _evaluate, _integrity_seam, _verified_loader

G = ReproducibilityGrade


def _make_rerunnable(db_session, calculation: Calculation, objects: dict[str, bytes], created_by: int) -> None:
    """Give a deposited single point the evidence of a complete one: a readable log, an input and parameters."""
    token = f"p7a-{calculation.id}"
    output = f"Entering Gaussian System output {token}".encode()
    deck = f"# input {token}".encode()
    for kind, content, name in ((ArtifactKind.output_log, output, "job.log"), (ArtifactKind.input, deck, "job.inp")):
        sha = hashlib.sha256(content).hexdigest()
        objects[sha] = content
        db_session.add(
            CalculationArtifact(
                calculation_id=calculation.id,
                kind=kind,
                uri=f"s3://repro/{token}/{name}",
                sha256=sha,
                bytes=len(content),
                filename=name,
                created_by=created_by,
            )
        )
    db_session.add(
        CalculationParameter(
            calculation_id=calculation.id, raw_key="scf_convergence", raw_value="tight", source=ParameterSource.upload
        )
    )
    db_session.flush()
    db_session.refresh(calculation)


@pytest.fixture
def deposit(client, db_session, _api_test_user):
    """worked payload (b): ``cbs`` assembled over ``spt`` and ``spq``; a loader for whatever gets preserved."""
    body = _ok(client, f.bundle_b())
    calcs = {key: _calc_by_label(db_session, body, key) for key in ("cbs", "spt", "spq")}
    objects: dict[str, bytes] = {}
    return calcs, objects, _api_test_user


def _names(items) -> set[str]:
    return {item["name"] for item in items}


def test_inputs_that_are_rerunnable_make_the_composite_rerunnable(db_session, deposit):
    calcs, objects, user = deposit
    for key in ("spt", "spq"):
        _make_rerunnable(db_session, calcs[key], objects, user)
    assert _evaluate(db_session, calcs["spq"], objects).grade is G.rerunnable  # the control: the input is, alone
    result = _evaluate(db_session, calcs["cbs"], objects)
    assert result.grade is G.rerunnable
    assert {"composite_inputs_auditable", "composite_inputs_rerunnable", "composite_total_follows_from_inputs"} <= _names(
        result.passed
    )
    # The checks that would need a program run of its own are not asked of it.
    names = _names(result.passed) | _names(result.missing)
    assert not names & {"verified_output_artifact_bytes", "preserved_input_artifacts"}


def test_the_weakest_input_decides_the_grade(db_session, deposit):
    calcs, objects, user = deposit
    _make_rerunnable(db_session, calcs["spq"], objects, user)
    # spt has no preserved evidence at all: graded alone it is below auditable.
    spt_grade = _evaluate(db_session, calcs["spt"], objects).grade
    assert spt_grade in (G.insufficient, G.described)
    result = _evaluate(db_session, calcs["cbs"], objects)
    assert result.grade is G.described
    assert "composite_inputs_auditable" in _names(result.missing)
    # The input that is complete is not what fails it.
    failing = next(item for item in result.missing if item["name"] == "composite_inputs_auditable")
    grades = {entry["grade"] for entry in failing["evidence"]["inputs"]}
    assert grades == {G.rerunnable.value, spt_grade.value}


def test_an_input_that_is_auditable_but_not_rerunnable_caps_the_composite_at_auditable(db_session, deposit):
    calcs, objects, user = deposit
    for key in ("spt", "spq"):
        _make_rerunnable(db_session, calcs[key], objects, user)
    # Drop spt's parameters and input deck: still auditable (log verified), no longer rerunnable.
    spt = calcs["spt"]
    for row in list(spt.parameters):
        db_session.delete(row)
    for row in [a for a in spt.artifacts if a.kind is ArtifactKind.input]:
        db_session.delete(row)
    db_session.flush()
    db_session.refresh(spt)
    assert _evaluate(db_session, spt, objects).grade is G.auditable
    result = _evaluate(db_session, calcs["cbs"], objects)
    assert result.grade is G.auditable
    assert "composite_inputs_rerunnable" in _names(result.missing)
    assert "composite_inputs_auditable" in _names(result.passed)


def test_the_grade_follows_an_input_that_is_completed_later(db_session, deposit):
    calcs, objects, user = deposit
    _make_rerunnable(db_session, calcs["spq"], objects, user)
    assert _evaluate(db_session, calcs["cbs"], objects).grade is G.described
    _make_rerunnable(db_session, calcs["spt"], objects, user)
    assert _evaluate(db_session, calcs["cbs"], objects).grade is G.rerunnable


def test_a_total_the_inputs_no_longer_give_blocks_auditable_even_when_every_input_is_rerunnable(db_session, deposit):
    calcs, objects, user = deposit
    for key in ("spt", "spq"):
        _make_rerunnable(db_session, calcs[key], objects, user)
    assert _evaluate(db_session, calcs["cbs"], objects).grade is G.rerunnable
    # Move the QZ energy and its reference together (the stored split still adds up): the inputs are
    # as reproducible as ever, but the stated total no longer follows from them.
    from sqlalchemy import select

    from app.db.models.calculation import CalculationSPEnergyComponent

    db_session.get(CalculationSPResult, calcs["spq"].id).electronic_energy_hartree += 1e-3
    db_session.scalars(
        select(CalculationSPEnergyComponent).where(
            CalculationSPEnergyComponent.calculation_id == calcs["spq"].id,
            CalculationSPEnergyComponent.component == "reference",
        )
    ).one().value_hartree += 1e-3
    db_session.flush()
    result = _evaluate(db_session, calcs["cbs"], objects)
    assert result.grade is G.described
    assert "composite_total_follows_from_inputs" in _names(result.missing)
    assert "composite_inputs_auditable" in _names(result.passed)


def test_an_unverifiable_total_does_not_count_as_following_from_the_inputs(db_session, deposit):
    calcs, objects, user = deposit
    for key in ("spt", "spq"):
        _make_rerunnable(db_session, calcs[key], objects, user)
    db_session.get(CalculationSPResult, calcs["spt"].id).electronic_energy_hartree = None
    db_session.flush()
    result = _evaluate(db_session, calcs["cbs"], objects)
    assert "composite_total_follows_from_inputs" in _names(result.missing)
    assert result.grade is not G.rerunnable


def test_an_input_whose_log_cannot_be_read_blocks_the_composite(db_session, deposit):
    calcs, objects, user = deposit
    for key in ("spt", "spq"):
        _make_rerunnable(db_session, calcs[key], objects, user)
    log = next(a for a in calcs["spt"].artifacts if a.kind is ArtifactKind.output_log)
    del objects[log.sha256]  # the store no longer answers for it
    result = _evaluate(db_session, calcs["cbs"], objects)
    assert result.grade is not G.rerunnable
    assert "composite_inputs_auditable" in _names(result.missing)


def test_the_input_grades_are_recorded_in_the_snapshot(db_session, deposit):
    calcs, objects, user = deposit
    _make_rerunnable(db_session, calcs["spq"], objects, user)
    result = _evaluate(db_session, calcs["cbs"], objects)
    check = next(c for c in result.missing if c["name"] == "composite_inputs_auditable")
    by_id = {entry["calculation_id"]: entry for entry in check["evidence"]["inputs"]}
    assert set(by_id) == {calcs["spt"].id, calcs["spq"].id}
    assert by_id[calcs["spq"].id]["grade"] == "rerunnable"
    assert [(s["term_position"], s["slot"]) for s in by_id[calcs["spq"].id]["slots"]] == [(0, "value"), (1, "cardinal")]


def test_an_assessment_goes_stale_when_an_input_changes(db_session, deposit):
    calcs, objects, user = deposit
    for key in ("spt", "spq"):
        _make_rerunnable(db_session, calcs[key], objects, user)
    loader = _verified_loader(objects)
    stored = evaluate_and_append_reproducibility(
        db_session, record_type="calculation", record_id=calcs["cbs"].id, artifact_loader=loader
    )
    assert stored.grade is G.rerunnable

    def fresh():
        return evaluate_reproducibility(
            db_session,
            record_type="calculation",
            record_id=calcs["cbs"].id,
            artifact_loader=loader,
            **_integrity_seam(db_session),
        )

    assert is_reproducibility_assessment_context_current(stored, current_context_json=fresh().context_json)
    for row in list(calcs["spt"].parameters):
        db_session.delete(row)
    db_session.flush()
    db_session.refresh(calcs["spt"])
    assert not is_reproducibility_assessment_context_current(stored, current_context_json=fresh().context_json)


def test_the_assembled_checks_replace_the_program_run_ones_only_for_an_assembled_composite(
    client, db_session, deposit
):
    calcs, objects, user = deposit
    for key in ("spt", "spq"):
        _make_rerunnable(db_session, calcs[key], objects, user)
    assembled = _evaluate(db_session, calcs["cbs"], objects)
    single_point = _evaluate(db_session, calcs["spq"], objects)
    own = {"composite_inputs_auditable", "composite_inputs_rerunnable", "composite_total_follows_from_inputs"}
    every = lambda r: _names(r.passed) | _names(r.missing)  # noqa: E731
    assert own <= every(assembled)
    assert not own & every(single_point)
    assert {"verified_output_artifact_bytes", "preserved_input_artifacts"} <= every(single_point)

    # A program-run composite keeps the program-run checks: it has a log and software of its own.
    from tests.api.test_api_composite_log_reconciliation import _composite, _deposit

    program_run = db_session.get(Calculation, _deposit(client, _composite()))
    result = _evaluate(db_session, program_run, objects)
    assert not own & every(result)
    assert {"verified_output_artifact_bytes", "preserved_input_artifacts"} <= every(result)


def test_an_assembled_composite_with_no_inputs_is_not_attributed(db_session, deposit):
    """Direct: remove every input row. Nothing is cited, so there is no evidence to attribute it to."""
    from app.db.models.calculation import CalculationCompositeInput

    calcs, objects, user = deposit
    for key in ("spt", "spq"):
        _make_rerunnable(db_session, calcs[key], objects, user)
    db_session.query(CalculationCompositeInput).filter(CalculationCompositeInput.calculation_id == calcs["cbs"].id).delete()
    db_session.flush()
    db_session.refresh(calcs["cbs"])
    result = _evaluate(db_session, calcs["cbs"], objects)
    assert result.grade is G.insufficient
    assert "source_attribution" in _names(result.missing)
