"""Species-owned statmech badges are frozen: the owner-scoped TS rubric must not move a single one.

The trust correction adds an owner-scoped rubric for transition-state-owned records. Every
species-owned record keeps ``computed_statmech``, its check map, its badge and its
completeness, byte for byte. This file pins that in two independent ways:

* ``FROZEN`` holds literals captured from the evaluator as it stood on ``main`` before the change.
  They do not move with the code: a test that compared the live evaluator only with itself would
  stay green under any edit of the species rubric.
* ``test_live_matches_the_species_rubric_run_directly``, in ``test_trust_ts_statmech_owner_rubric.py``,
  compares the live router with the species rubric driven explicitly, over these same scenarios,
  including the context hash a stored machine review is keyed on. That one guards the routing; the
  literals here are what guard the check code itself.

The scenario list is asserted non-empty and every scenario is asserted to have run: a parametrised
guard over an empty list passes having verified nothing.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable

import pytest
from sqlalchemy.orm import Session

from app.db.models.common import (
    CalculationQuality,
    StatmechCalculationRole,
    StatmechTreatmentKind,
    ValidationStatus,
)
from app.db.models.statmech import Statmech
from app.services.trust import EvidenceEvaluation, evaluate_computed_statmech
from tests.services.test_trust_evaluator import (
    _add_statmech_torsion,
    _link_statmech_source,
    _make_minimal_freq_calc,
    _make_minimal_opt_calc,
    _make_statmech,
)


def _sparse(db: Session) -> Statmech:
    return _make_statmech(db, metadata=False)


def _full_metadata_no_sources(db: Session) -> Statmech:
    return _make_statmech(db)


def _opt_freq_sp(db: Session) -> Statmech:
    statmech = _make_statmech(db, frequency_scale_factor=True)
    for calc, role in (
        (_make_minimal_opt_calc(db), StatmechCalculationRole.opt),
        (_make_minimal_freq_calc(db), StatmechCalculationRole.freq),
        (_make_minimal_opt_calc(db), StatmechCalculationRole.sp),
    ):
        _link_statmech_source(db, statmech=statmech, calculation=calc, role=role)
    return statmech


def _torsion_bearing_with_scan(db: Session) -> Statmech:
    statmech = _make_statmech(db, treatment=StatmechTreatmentKind.rrho_1d)
    scan = _make_minimal_opt_calc(db)
    _link_statmech_source(db, statmech=statmech, calculation=scan, role=StatmechCalculationRole.scan)
    _add_statmech_torsion(db, statmech=statmech, source_scan=scan)
    return statmech


def _rejected_required_role(db: Session) -> Statmech:
    statmech = _make_statmech(db)
    bad = _make_minimal_opt_calc(db, quality=CalculationQuality.rejected)
    _link_statmech_source(db, statmech=statmech, calculation=bad, role=StatmechCalculationRole.opt)
    return statmech


def _geometry_validation_failed(db: Session) -> Statmech:
    statmech = _make_statmech(db)
    calc = _make_minimal_opt_calc(db, geom_validation=ValidationStatus.fail)
    _link_statmech_source(db, statmech=statmech, calculation=calc, role=StatmechCalculationRole.opt)
    return statmech


SCENARIOS: dict[str, Callable[[Session], Statmech]] = {
    "sparse": _sparse,
    "full_metadata_no_sources": _full_metadata_no_sources,
    "opt_freq_sp": _opt_freq_sp,
    "torsion_bearing_with_scan": _torsion_bearing_with_scan,
    "rejected_required_role": _rejected_required_role,
    "geometry_validation_failed": _geometry_validation_failed,
}


def snapshot(evaluation: EvidenceEvaluation) -> dict:
    return {
        "rubric": evaluation.rubric,
        "rubric_version": evaluation.rubric_version,
        "label": evaluation.label.value,
        "hard_fail": evaluation.hard_fail_reason.value if evaluation.hard_fail_reason else None,
        "completeness": round(evaluation.evidence_completeness, 6),
        "is_certified": evaluation.is_certified,
        "checks": [(r.name, r.outcome.value, r.kind.value, r.weight) for r in evaluation.check_results],
    }


def digest(evaluation: EvidenceEvaluation) -> str:
    """SHA-256 of the canonical snapshot: rubric, version, label, completeness and the ordered check map."""
    return hashlib.sha256(json.dumps(snapshot(evaluation), sort_keys=True).encode()).hexdigest()


#: ``(label, hard_fail, completeness, digest)`` per scenario, captured from ``main`` before the owner-scoped rubric.
#: The digests were re-captured once, in versioning PR-4, because the digest covers ``rubric_version`` and the rubric
#: restarted at 1 (it was 2): label, hard-fail reason, completeness and the whole check map are byte-identical.
FROZEN: dict[str, tuple[str, str | None, float, str]] = {
    "sparse": ("unsupported", None, 0.1111, "24196551ded32dac4f9a8c951dedd8fd5e866d62f530a5eaca05a0382ed3f6ef"),
    "full_metadata_no_sources": ("sparse", None, 0.4444, "e078edab4701f5172a81c8f5e33642c9348091705e2aefadf4c3a81f9471ce25"),
    "opt_freq_sp": ("well_supported", None, 0.9, "fff9fc20326b56fc9a1bb2cefebc5fc9c1b2df1bd62e6aa6f9ef088f8425038e"),
    "torsion_bearing_with_scan": ("mostly_supported", None, 0.8261, "548f503e0ccc4da814784a7ab203466e746d3e73773a92f2a198ecadfa52b991"),
    "rejected_required_role": ("hard_failed", "source_calculation_hard_failed_for_required_role", 0.7895, "99a8d263ff28aa7c57308b49389a65b094e00da8307563a2fe3ac8b78c4bbb67"),
    "geometry_validation_failed": ("mostly_supported", None, 0.8421, "b2e3d5781c17390016b4ad5c5642ce97ea241cd48fae544db35c28e3d6aceeaa"),
}


def test_the_scenario_list_is_not_empty_and_every_scenario_has_a_frozen_expectation():
    assert SCENARIOS
    assert set(SCENARIOS) == set(FROZEN)


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_a_species_owned_statmech_evaluates_exactly_as_it_did_before_the_change(db_session, name):
    statmech = SCENARIOS[name](db_session)
    assert statmech.species_entry_id is not None and statmech.transition_state_entry_id is None
    evaluation = evaluate_computed_statmech(db_session, statmech.id)
    label, hard_fail, completeness, expected_digest = FROZEN[name]
    assert evaluation.label.value == label
    assert (evaluation.hard_fail_reason.value if evaluation.hard_fail_reason else None) == hard_fail
    assert round(evaluation.evidence_completeness, 6) == completeness
    assert (evaluation.rubric, evaluation.rubric_version) == ("computed_statmech", 1)
    assert digest(evaluation) == expected_digest
