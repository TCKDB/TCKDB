"""Species-owned statmech badges are frozen: PR 6.6 must not move a single one.

The trust correction adds an owner-scoped rubric for transition-state-owned records. Every
species-owned record keeps ``computed_statmech`` v2, its check map, its badge and its
completeness, byte for byte. This file pins that in two independent ways:

* ``FROZEN`` holds literals captured from the evaluator as it stood on ``main`` before the change.
  They do not move with the code: a test that compared the live evaluator only with itself would
  stay green under any edit of the species rubric.
* ``test_live_matches_the_species_rubric_run_directly`` compares the live router with the species
  rubric driven explicitly, over the same scenarios, including the context hash a stored machine
  review is keyed on.

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


#: ``(label, hard_fail, completeness, digest)`` per scenario, captured from ``main`` before PR 6.6.
FROZEN: dict[str, tuple[str, str | None, float, str]] = {
    "sparse": ("unsupported", None, 0.1111, "f01d345b26b08236f4ef27de5729458489ebaa0d981c24fcd453b29b1f55b4b2"),
    "full_metadata_no_sources": ("sparse", None, 0.4444, "29c2d83fe81982a06e1415387533d49482eaba0370c196e3bf4003538a3020e5"),
    "opt_freq_sp": ("well_supported", None, 0.9, "44862735edf9e1c373a523221d4e7e0b08c38ba52674ef22a3c388ff377ea351"),
    "torsion_bearing_with_scan": ("mostly_supported", None, 0.8261, "26e196497a08726cc6912dd04b09a3ebd23704b661a4dcab7d90053758f808ee"),
    "rejected_required_role": ("hard_failed", "source_calculation_hard_failed_for_required_role", 0.7895, "d5d7d89d023c4bbbf2e121f31eae12a72a8443b1dcb6d940d962d12168753d13"),
    "geometry_validation_failed": ("mostly_supported", None, 0.8421, "c3206664eeae40460bebd4c2209536321f4f67588443e64ffd024f63e68da3e2"),
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
    assert (evaluation.rubric, evaluation.rubric_version) == ("computed_statmech", 2)
    assert digest(evaluation) == expected_digest
