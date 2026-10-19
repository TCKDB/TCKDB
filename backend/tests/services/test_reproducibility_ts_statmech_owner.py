"""The owner-scoped reproducibility rubric ``tckdb_reproducibility_ts_statmech`` v1 (#760, PR-2).

A statmech record belongs to a species entry or to a transition-state entry, never both. The shared rubric used to
ask of every statmech record whether it is attached to a species entry, so a TS-owned record read ``insufficient``
whatever its evidence. A TS-owned record is now graded by a sibling rubric that asks the same question about a
transition-state entry and nothing else. Species-owned records keep the shared rubric byte for byte (pinned by
``test_reproducibility_statmech_species_frozen``).

These tests pin: the one changed check, the stamped identity, the router, the read-time currency of rows stored
before the change, the read-only report, and that the rubric does not import the trust tier.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import func, select, text

from app.db.models.common import (
    CalculationType,
    ReproducibilityAssessorKind,
    ReproducibilityGrade,
    ScientificOriginKind,
    StatmechCalculationRole,
    SubmissionRecordType,
)
from app.db.models.reproducibility_assessment import RecordReproducibilityAssessment
from app.db.models.statmech import Statmech
from app.services.read_only_report import print_read_only_report
from app.services.reproducibility_assessment import append_reproducibility_assessment
from app.services.reproducibility_owner_report import reproducibility_owner_report
from app.services.reproducibility_rubric import (
    SHARED,
    TS_STATMECH,
    evaluate_and_append_reproducibility,
    evaluate_reproducibility,
    reproducibility_rubric_for,
)
from tests.services.scientific_read._factories import (
    attach_statmech_source_calculation,
    make_calculation,
    make_lot,
    make_software_release,
)
from tests.services.test_reproducibility_rubric import _integrity_seam
from tests.services.test_reproducibility_statmech_species_frozen import SCENARIOS, _ts_entry


def _ts_statmech(db_session, *, symmetry: bool = True, sources: bool = True) -> Statmech:
    ts_entry = _ts_entry(db_session)
    statmech = Statmech(
        transition_state_entry_id=ts_entry.id,
        scientific_origin=ScientificOriginKind.computed,
        external_symmetry=1 if symmetry else None,
        point_group="C1" if symmetry else None,
    )
    db_session.add(statmech)
    db_session.flush()
    if sources:
        calculation = make_calculation(
            db_session,
            type=CalculationType.freq,
            transition_state_entry_id=ts_entry.id,
            lot_id=make_lot(db_session).id,
            software_release_id=make_software_release(db_session).id,
        )
        attach_statmech_source_calculation(
            db_session, statmech=statmech, calculation=calculation, role=StatmechCalculationRole.freq
        )
    db_session.refresh(statmech)
    return statmech


def _species_statmech(db_session, user) -> Statmech:
    return SCENARIOS["computed_opt_freq_sp"](db_session, user)


def _evaluate(db_session, statmech, **kwargs):
    return evaluate_reproducibility(
        db_session,
        record_type=SubmissionRecordType.statmech,
        record_id=statmech.id,
        **_integrity_seam(db_session),
        **kwargs,
    )


def _outcomes(evaluation) -> dict[str, str]:
    return {check["name"]: check["outcome"] for check in evaluation.context_json["checks"]}


def _append(db_session, evaluation, *, rubric_name=None, rubric_version=None, context_json=None):
    return append_reproducibility_assessment(
        db_session,
        record_type=evaluation.record_type,
        record_id=evaluation.record_id,
        grade=evaluation.grade,
        rubric_name=rubric_name or evaluation.rubric_name,
        rubric_version=rubric_version or evaluation.rubric_version,
        context_json=context_json if context_json is not None else evaluation.context_json,
        passed=evaluation.passed,
        missing=evaluation.missing,
        warnings=evaluation.warnings,
        assessor_kind=ReproducibilityAssessorKind.system,
    )


def _state(client, statmech) -> dict:
    response = client.get(f"/api/v1/scientific/statmech/{statmech.public_ref}", params={"include": "assessments"})
    assert response.status_code == 200, response.text
    return response.json()["record"]["assessments"]["reproducibility"]


# ---------------------------------------------------------------------------
# T1, T2: the graded evidence
# ---------------------------------------------------------------------------


def test_t1_a_ts_owned_computed_statmech_with_symmetry_and_a_source_grades_described(db_session):
    result = _evaluate(db_session, _ts_statmech(db_session))
    outcomes = _outcomes(result)
    assert outcomes["scientific_context"] == "passed"
    assert outcomes["source_attribution"] == "passed"
    assert result.grade is ReproducibilityGrade.described


def test_t2_a_ts_owned_statmech_without_symmetry_or_point_group_stays_insufficient(db_session):
    result = _evaluate(db_session, _ts_statmech(db_session, symmetry=False))
    assert _outcomes(result)["scientific_context"] == "missing"
    assert result.grade is ReproducibilityGrade.insufficient


def test_a_ts_owned_computed_statmech_with_no_source_stays_insufficient_for_its_own_reason(db_session):
    result = _evaluate(db_session, _ts_statmech(db_session, sources=False))
    outcomes = _outcomes(result)
    assert outcomes["scientific_context"] == "passed"
    assert outcomes["source_attribution"] == "missing"
    assert result.grade is ReproducibilityGrade.insufficient


def test_a_ts_owned_statmech_never_exceeds_described(db_session):
    result = _evaluate(db_session, _ts_statmech(db_session))
    assert result.grade is not ReproducibilityGrade.auditable and result.grade is not ReproducibilityGrade.rerunnable
    outcomes = _outcomes(result)
    assert outcomes["record_type_audit_policy"] == "missing"


def test_no_ts_specific_check_is_added(db_session, _api_test_user):
    """Same check names, in the same order, as the species-owned record: only the owner question differs."""
    ts = [c["name"] for c in _evaluate(db_session, _ts_statmech(db_session)).context_json["checks"]]
    species = [
        c["name"] for c in _evaluate(db_session, _species_statmech(db_session, _api_test_user)).context_json["checks"]
    ]
    assert ts == species


# ---------------------------------------------------------------------------
# T3: the owner check is explicit and exclusive
# ---------------------------------------------------------------------------


def test_t3_the_owner_question_follows_the_rubric_not_the_data(db_session, _api_test_user):
    species = _species_statmech(db_session, _api_test_user)
    ts = _ts_statmech(db_session)

    assert _outcomes(_evaluate(db_session, species, rubric=SHARED))["scientific_context"] == "passed"
    assert _outcomes(_evaluate(db_session, species, rubric=TS_STATMECH))["scientific_context"] == "missing"
    assert _outcomes(_evaluate(db_session, ts, rubric=TS_STATMECH))["scientific_context"] == "passed"
    assert _outcomes(_evaluate(db_session, ts, rubric=SHARED))["scientific_context"] == "missing"


# ---------------------------------------------------------------------------
# T4: stamped identity
# ---------------------------------------------------------------------------


def test_t4_the_stamped_identity_follows_the_owner(db_session, _api_test_user):
    ts = _ts_statmech(db_session)
    species = _species_statmech(db_session, _api_test_user)

    ts_row = evaluate_and_append_reproducibility(
        db_session, record_type=SubmissionRecordType.statmech, record_id=ts.id, **_integrity_seam(db_session)
    )
    assert (ts_row.rubric_name, ts_row.rubric_version) == ("tckdb_reproducibility_ts_statmech", "v1")
    assert ts_row.context_json["rubric"] == {"name": "tckdb_reproducibility_ts_statmech", "version": "v1"}

    species_row = evaluate_and_append_reproducibility(
        db_session, record_type=SubmissionRecordType.statmech, record_id=species.id, **_integrity_seam(db_session)
    )
    assert (species_row.rubric_name, species_row.rubric_version) == ("tckdb_reproducibility", "v1")
    assert species_row.context_json["rubric"] == {"name": "tckdb_reproducibility", "version": "v1"}


# ---------------------------------------------------------------------------
# T5: router parity with the trust tier's owner test
# ---------------------------------------------------------------------------


def test_t5_the_router_agrees_with_the_trust_tiers_owner_test():
    from app.services.trust import COMPUTED_TS_STATMECH_V1, statmech_rubric_for

    cases = {
        "species": Statmech(species_entry_id=1, transition_state_entry_id=None),
        "ts": Statmech(species_entry_id=None, transition_state_entry_id=2),
        "neither": Statmech(species_entry_id=None, transition_state_entry_id=None),
        "both": Statmech(species_entry_id=1, transition_state_entry_id=2),
    }
    for name, record in cases.items():
        is_ts_here = reproducibility_rubric_for(record) == TS_STATMECH
        is_ts_there = statmech_rubric_for(record) is COMPUTED_TS_STATMECH_V1
        assert is_ts_here == is_ts_there, name
    assert reproducibility_rubric_for(cases["ts"]) == TS_STATMECH
    assert all(reproducibility_rubric_for(cases[n]) == SHARED for n in ("species", "neither", "both"))
    assert reproducibility_rubric_for(object()) == SHARED


# ---------------------------------------------------------------------------
# T6, T7: read-time currency
# ---------------------------------------------------------------------------


def test_t6_a_row_stored_before_the_change_reads_stale_and_a_fresh_one_reads_current(client, db_session):
    old = _ts_statmech(db_session)
    fresh = _ts_statmech(db_session)
    # The bytes the code on main wrote: graded under the shared rubric.
    _append(db_session, _evaluate(db_session, old, rubric=SHARED))
    evaluate_and_append_reproducibility(
        db_session, record_type=SubmissionRecordType.statmech, record_id=fresh.id, **_integrity_seam(db_session)
    )

    stale = _state(client, old)
    assert stale["state"] == "stale"
    assert stale["rubric"] == "tckdb_reproducibility"  # the historical claim keeps its own label
    assert stale["grade"] == "insufficient"
    current = _state(client, fresh)
    assert current["state"] == "current"
    assert current["rubric"] == "tckdb_reproducibility_ts_statmech"
    assert current["grade"] == "described"


def test_a_species_row_stored_under_the_shared_rubric_reads_current(client, db_session, _api_test_user):
    species = _species_statmech(db_session, _api_test_user)
    _append(db_session, _evaluate(db_session, species, rubric=SHARED))
    assert _state(client, species)["state"] == "current"


def test_t7_a_forged_identity_with_a_matching_hash_reads_stale(client, db_session):
    ts = _ts_statmech(db_session)
    fresh = _evaluate(db_session, ts)
    # Context equals the fresh TS context, so the hash matches; only the identity comparison can catch the label.
    _append(db_session, fresh, rubric_name="tckdb_reproducibility", rubric_version="v1")
    assert _state(client, ts)["state"] == "stale"


def test_the_stored_history_is_not_rewritten_by_reading(client, db_session):
    ts = _ts_statmech(db_session)
    _append(db_session, _evaluate(db_session, ts, rubric=SHARED))
    before = db_session.scalar(select(func.count()).select_from(RecordReproducibilityAssessment))
    _state(client, ts)
    _state(client, ts)
    assert db_session.scalar(select(func.count()).select_from(RecordReproducibilityAssessment)) == before


# ---------------------------------------------------------------------------
# T8: the report
# ---------------------------------------------------------------------------


def _seed(db_session, user):
    species = _species_statmech(db_session, user)
    ts = _ts_statmech(db_session)
    _append(db_session, _evaluate(db_session, species, rubric=SHARED))
    _append(db_session, _evaluate(db_session, ts, rubric=SHARED))
    return species, ts


def test_t8_the_report_names_currency_transitions_and_species_stability(db_session, _api_test_user):
    _seed(db_session, _api_test_user)
    report = reproducibility_owner_report(db_session)

    assert report["by_owner"] == {"species_entry": {"n": 1}, "transition_state_entry": {"n": 1}}
    assert report["unowned_statmech"] == 0
    assert report["species_entry_unchanged"] is True
    assert report["species_entry_records_compared"] == 1
    assert report["statmech_with_assessment"] == {"species_entry": 1, "transition_state_entry": 1, "unowned": 0}
    assert report["assessment_currency"] == [
        {"owner": "species_entry", "state": "current", "stale_reasons": [], "count": 1},
        {
            "owner": "transition_state_entry",
            "state": "stale",
            "stale_reasons": ["rubric_identity_mismatch", "context_hash_mismatch"],
            "count": 1,
        },
    ]
    transitions = {(t["owner"], t["from"], t["to"]): t["count"] for t in report["grade_transitions"]}
    assert transitions[("transition_state_entry", "insufficient", "described")] == 1
    assert transitions[("species_entry", "described", "described")] == 1
    assert "stale_transition_state_records" not in report


def test_the_stale_list_is_opt_in_and_names_only_ts_owned_records(db_session, _api_test_user):
    _, ts = _seed(db_session, _api_test_user)
    report = reproducibility_owner_report(db_session, include_stale_list=True)
    (entry,) = report["stale_transition_state_records"]
    assert entry["statmech_id"] == ts.id
    assert entry["stored_grade"] == "insufficient" and entry["fresh_grade"] == "described"
    assert entry["stored_rubric"] == ["tckdb_reproducibility", "v1"]


def test_t8_the_report_flags_a_species_record_that_moved(db_session, _api_test_user, monkeypatch):
    _seed(db_session, _api_test_user)
    import app.services.reproducibility_rubric as rubric

    monkeypatch.setattr(rubric, "reproducibility_rubric_for", lambda target: TS_STATMECH)
    report = reproducibility_owner_report(db_session)
    assert report["species_entry_unchanged"] is False


def test_t8_the_report_writes_nothing(db_session, _api_test_user):
    _seed(db_session, _api_test_user)
    db_session.flush()
    rows = db_session.scalar(select(func.count()).select_from(RecordReproducibilityAssessment))
    reproducibility_owner_report(db_session, include_stale_list=True)
    assert not db_session.new and not db_session.dirty and not db_session.deleted
    assert db_session.scalar(select(func.count()).select_from(RecordReproducibilityAssessment)) == rows == 2


def test_an_empty_database_reports_zero_everywhere(db_session):
    report = reproducibility_owner_report(db_session)
    assert report["by_owner"] == {"species_entry": {"n": 0}, "transition_state_entry": {"n": 0}}
    assert report["assessment_currency"] == [] and report["grade_transitions"] == []
    assert report["species_entry_records_compared"] == 0


def test_t8_the_script_transaction_is_read_only_and_names_its_database(db_engine, capsys):
    url = db_engine.url.render_as_string(hide_password=False)

    def attempts_a_write(session):
        session.execute(text("UPDATE statmech SET note = note WHERE false"))
        return {}

    with pytest.raises(Exception) as caught:
        print_read_only_report(url, attempts_a_write)
    assert "read-only transaction" in str(caught.value)

    script = Path(__file__).resolve().parents[2] / "scripts/ops/reproducibility_ts_statmech_report.py"
    import importlib.util

    spec = importlib.util.spec_from_file_location("repro_ts_report_script", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.main(["--database-url", url, "--list-stale"])
    out = json.loads(capsys.readouterr().out)
    assert out["by_owner"]["species_entry"] == {"n": 0}
    assert out["stale_transition_state_records"] == []

    with pytest.raises(SystemExit) as refused:
        module.main([])
    assert refused.value.code == 2


# ---------------------------------------------------------------------------
# T9: the tiers stay separate
# ---------------------------------------------------------------------------


def test_t9_the_rubric_does_not_import_the_trust_tier():
    source = (Path(__file__).resolve().parents[2] / "app/services/reproducibility_rubric.py").read_text()
    imported = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
        elif isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
    assert imported, "the module imports nothing: the walk did not read it"
    assert not [m for m in imported if m == "app.services.trust" or m.startswith("app.services.trust.")]

    code = (
        "import sys, app.services.reproducibility_rubric;"
        "sys.exit(1 if any(m == 'app.services.trust' or m.startswith('app.services.trust.') for m in sys.modules) else 0)"
    )
    backend = Path(__file__).resolve().parents[2]
    result = subprocess.run([sys.executable, "-c", code], cwd=backend, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
