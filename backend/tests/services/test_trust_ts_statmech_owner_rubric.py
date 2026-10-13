"""The owner-scoped statmech rubric ``computed_ts_statmech`` v1.

A transition-state-owned statmech record used to be graded by the species rubric, whose first
check asks for a species entry, and so read ``hard_failed`` / ``species_entry_missing`` however
complete it was. It is now graded by ``computed_ts_statmech``: the species rubric with exactly
one check swapped ("attached to a transition-state entry" for "attached to a species entry").

What these tests protect, in the order the owner stated the conditions:

1. Generic completeness stays non-certifying (``is_certified`` is false on a well-supported record).
2. An automated geometry-validation fail stays advisory (a ``warning``, never a hard fail).
3. The badge describes evidence completeness, not validity: the TS rubric adds no TS-specific check.
4. Task assessment does not inherit the generic required-role checks: the statmech selection
   package never imports the trust package.

And the rollout rules:

* species-owned records keep ``computed_statmech`` v2, their checks, badge and stored reviews;
* a stored review is never rewritten, and the latest TS-owned review reads stale (never silently
  re-reviewed) because its context was hashed under the species rubric.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.db.models.calculation import CalculationGeometryValidation
from app.db.models.common import (
    CalculationQuality,
    RigidRotorKind,
    ScientificOriginKind,
    StatmechCalculationRole,
    StatmechTreatmentKind,
    SubmissionRecordType,
    ValidationStatus,
)
from app.db.models.record_machine_review import RecordMachineReviewRow
from app.db.models.statmech import Statmech
from app.services.machine_review.admin_trigger import (
    active_rubric_versions_for_fragment,
    active_rubric_versions_for_record_type,
    resolve_record_trust_fragment,
    run_admin_fake_machine_review,
)
from app.services.machine_review.context_adapter import build_machine_review_evidence_context_from_trust
from app.services.machine_review.context_hash import build_machine_review_context_hash
from app.services.machine_review.currency import MachineReviewCurrencyState, MachineReviewStaleReason
from app.services.machine_review.persistence import create_record_machine_review_row
from app.services.machine_review.query import get_record_machine_review_currency_for_record
from app.services.machine_review.read_model import RecordMachineReview
from app.services.machine_review.recipe import (
    ACTIVE_MACHINE_REVIEW_PROMPT_VERSION,
    ACTIVE_MACHINE_REVIEW_RUBRIC_VERSIONS,
    public_rubric_name,
)
from app.services.machine_review.schemas import MachineReviewStatus
from app.services.trust import (
    COMPUTED_STATMECH_V2,
    COMPUTED_TS_STATMECH_V1,
    OWNER_SCOPED_RUBRICS,
    RUBRIC_REGISTRY,
    EvidenceBadge,
    EvidenceOutcome,
    HardFailReason,
    build_trust_fragment,
    evaluate_computed_statmech,
    evaluate_loaded_statmech,
    evaluate_loaded_statmech_under,
    statmech_rubric_for,
)
from tests.services.test_trust_evaluator import _link_statmech_source, _make_frequency_scale_factor
from tests.services.test_trust_evaluator_transition_state import (
    _attach_ts_freq_calc,
    _attach_ts_opt_calc,
    _attach_ts_sp_calc,
    _make_ts_entry,
)
from tests.services.test_trust_statmech_species_frozen import SCENARIOS

BACKEND = Path(__file__).resolve().parents[2]


def _make_ts_statmech(db, *, sources: bool = True, **overrides) -> Statmech:
    """A transition-state-owned, fully described computed statmech with TS opt/freq/sp sources."""
    ts_entry = _make_ts_entry(db)
    fields = {
        "scientific_origin": ScientificOriginKind.computed,
        "external_symmetry": 3,
        "point_group": "C3v",
        "is_linear": False,
        "rigid_rotor_kind": RigidRotorKind.asymmetric_top,
        "statmech_treatment": StatmechTreatmentKind.rrho,
        "uses_projected_frequencies": True,
    }
    fields.update(overrides)
    statmech = Statmech(transition_state_entry_id=ts_entry.id, **fields)
    db.add(statmech)
    db.flush()
    if sources:
        opt = _attach_ts_opt_calc(db, ts_entry)
        freq = _attach_ts_freq_calc(db, ts_entry, opt)
        sp = _attach_ts_sp_calc(db, ts_entry, opt)
        for calc, role in (
            (opt, StatmechCalculationRole.opt),
            (freq, StatmechCalculationRole.freq),
            (sp, StatmechCalculationRole.sp),
        ):
            _link_statmech_source(db, statmech=statmech, calculation=calc, role=role)
    db.refresh(statmech)
    return statmech


def _link(db, statmech, calc, role):
    _link_statmech_source(db, statmech=statmech, calculation=calc, role=role)


# ---------------------------------------------------------------------------
# The rubric itself
# ---------------------------------------------------------------------------


def _signature(spec):
    return (spec.name, spec.kind, spec.weight, spec.runner)


def test_the_ts_rubric_is_the_species_rubric_with_only_the_subject_check_swapped():
    ts_tail = [_signature(spec) for spec in COMPUTED_TS_STATMECH_V1.checks[1:]]
    species_tail = [_signature(spec) for spec in COMPUTED_STATMECH_V2.checks[1:]]
    assert ts_tail, "an empty tail would make the equality below vacuous"
    assert ts_tail == species_tail  # same names, kinds, weights and the very same runner functions
    assert len(COMPUTED_TS_STATMECH_V1.checks) == len(COMPUTED_STATMECH_V2.checks)

    ts_first, species_first = COMPUTED_TS_STATMECH_V1.checks[0], COMPUTED_STATMECH_V2.checks[0]
    assert (species_first.name, ts_first.name) == ("species_entry_present", "transition_state_entry_present")
    assert ts_first.kind is species_first.kind
    assert ts_first.runner is not species_first.runner
    assert (COMPUTED_TS_STATMECH_V1.name, COMPUTED_TS_STATMECH_V1.version) == ("computed_ts_statmech", 1)
    assert COMPUTED_TS_STATMECH_V1.record_type == COMPUTED_STATMECH_V2.record_type == "statmech"


def test_the_ts_rubric_adds_no_ts_specific_check():
    """Owner condition 3: the badge reports completeness, not whether the transverse model is suitable."""
    forbidden = ("imaginary", "reaction_coordinate", "irc", "n_imag")
    names = [spec.name for spec in COMPUTED_TS_STATMECH_V1.checks]
    assert names and not [n for n in names if any(word in n for word in forbidden)]
    assert set(names) - {spec.name for spec in COMPUTED_STATMECH_V2.checks} == {"transition_state_entry_present"}


def test_the_species_rubric_and_the_registry_default_are_untouched():
    assert RUBRIC_REGISTRY["statmech"] is COMPUTED_STATMECH_V2
    assert (COMPUTED_STATMECH_V2.name, COMPUTED_STATMECH_V2.version) == ("computed_statmech", 2)
    assert COMPUTED_STATMECH_V2.checks[0].name == "species_entry_present"
    assert OWNER_SCOPED_RUBRICS == (("statmech", COMPUTED_TS_STATMECH_V1),)
    assert COMPUTED_TS_STATMECH_V1 not in RUBRIC_REGISTRY.values()


def test_the_router_chooses_by_owner():
    assert statmech_rubric_for(Statmech(species_entry_id=1)) is COMPUTED_STATMECH_V2
    assert statmech_rubric_for(Statmech(transition_state_entry_id=1)) is COMPUTED_TS_STATMECH_V1
    # Impossible under the CHECK; the backstop keeps today's behaviour rather than guessing.
    assert statmech_rubric_for(Statmech()) is COMPUTED_STATMECH_V2
    assert statmech_rubric_for(Statmech(species_entry_id=1, transition_state_entry_id=1)) is COMPUTED_STATMECH_V2


# ---------------------------------------------------------------------------
# Evaluating a transition-state-owned record
# ---------------------------------------------------------------------------


def test_a_ts_owned_statmech_is_graded_by_the_ts_rubric_and_is_not_hard_failed(db_session):
    statmech = _make_ts_statmech(db_session)
    evaluation = evaluate_computed_statmech(db_session, statmech.id)

    assert (evaluation.rubric, evaluation.rubric_version) == ("computed_ts_statmech", 1)
    assert evaluation.hard_fail_reason is None
    assert evaluation.label is not EvidenceBadge.hard_failed
    assert evaluation.checks["transition_state_entry_present"] is EvidenceOutcome.passed
    assert "species_entry_present" not in evaluation.checks


def test_a_well_supported_ts_record_is_still_not_certified(db_session):
    """Owner condition 1."""
    scale_factor = _make_frequency_scale_factor(db_session)
    statmech = _make_ts_statmech(db_session, frequency_scale_factor_id=scale_factor.id)
    evaluation = evaluate_computed_statmech(db_session, statmech.id)
    # Exactly the top badge, so a certificate derived from it would show.
    assert evaluation.label is EvidenceBadge.well_supported
    assert evaluation.is_certified is False
    assert build_trust_fragment(evaluation).is_certified is False


def test_a_ts_statmech_whose_entry_is_not_loaded_is_a_structural_failure(db_session):
    transient = Statmech(
        transition_state_entry_id=123, scientific_origin=ScientificOriginKind.computed, transition_state_entry=None
    )
    evaluation = evaluate_loaded_statmech(transient)
    assert (evaluation.rubric, evaluation.rubric_version) == ("computed_ts_statmech", 1)
    assert evaluation.label is EvidenceBadge.hard_failed
    assert evaluation.hard_fail_reason is HardFailReason.transition_state_entry_missing
    assert evaluation.checks["transition_state_entry_present"] is EvidenceOutcome.missing


def test_a_rejected_required_role_calculation_still_hard_fails_a_ts_record(db_session):
    statmech = _make_ts_statmech(db_session, sources=False)
    ts_entry = statmech.transition_state_entry
    opt = _attach_ts_opt_calc(db_session, ts_entry)
    freq = _attach_ts_freq_calc(db_session, ts_entry, opt)
    freq.quality = CalculationQuality.rejected
    db_session.flush()
    _link(db_session, statmech, freq, StatmechCalculationRole.freq)

    evaluation = evaluate_computed_statmech(db_session, statmech.id)
    assert evaluation.label is EvidenceBadge.hard_failed
    assert evaluation.hard_fail_reason is HardFailReason.source_calculation_hard_failed_for_required_role


def test_an_automated_geometry_validation_fail_is_advisory_on_a_ts_record(db_session):
    """Owner condition 2: a failed geometry validation warns; it never hard-fails or lowers the badge."""
    statmech = _make_ts_statmech(db_session, sources=False)
    ts_entry = statmech.transition_state_entry
    opt = _attach_ts_opt_calc(db_session, ts_entry, geom_validation=None)
    db_session.add(
        CalculationGeometryValidation(
            calculation_id=opt.id, validation_status=ValidationStatus.fail, species_smiles="CCO", is_isomorphic=False
        )
    )
    db_session.flush()
    db_session.refresh(opt)
    _link(db_session, statmech, opt, StatmechCalculationRole.opt)

    evaluation = evaluate_computed_statmech(db_session, statmech.id)
    assert evaluation.hard_fail_reason is None
    assert evaluation.label is not EvidenceBadge.hard_failed
    assert evaluation.checks["geometry_validation_not_failed_for_source_calculations"] is EvidenceOutcome.warning


def test_a_later_version_of_the_ts_rubric_keeps_the_transition_state_owner_check(db_session):
    """The owner check follows the rubric's name, not the v1 object.

    A stand-in ``computed_ts_statmech`` v2 (same checks, higher version) must still be asked for a
    transition-state entry, not a species entry. If the check were keyed on the v1 object, a future
    v2 would silently bring back ``species_entry_missing`` on every TS-owned record.
    """
    stand_in = COMPUTED_TS_STATMECH_V1.model_copy(update={"version": 2})
    assert stand_in is not COMPUTED_TS_STATMECH_V1
    assert (stand_in.name, stand_in.version) == ("computed_ts_statmech", 2)

    statmech = _make_ts_statmech(db_session)
    evaluation = evaluate_loaded_statmech_under(statmech, stand_in)
    assert (evaluation.rubric, evaluation.rubric_version) == ("computed_ts_statmech", 2)
    assert evaluation.hard_fail_reason is None
    assert evaluation.label is not EvidenceBadge.hard_failed

    transient = Statmech(
        transition_state_entry_id=123, scientific_origin=ScientificOriginKind.computed, transition_state_entry=None
    )
    missing = evaluate_loaded_statmech_under(transient, stand_in)
    assert missing.hard_fail_reason is HardFailReason.transition_state_entry_missing


def test_the_ts_record_forced_through_the_species_rubric_reads_as_it_did_before(db_session):
    """The reconstruction the report relies on: the legacy path is still reachable and still wrong for TS."""
    statmech = _make_ts_statmech(db_session)
    evaluation = evaluate_loaded_statmech_under(statmech, COMPUTED_STATMECH_V2)
    assert (evaluation.rubric, evaluation.rubric_version) == ("computed_statmech", 2)
    assert evaluation.label is EvidenceBadge.hard_failed
    assert evaluation.hard_fail_reason is HardFailReason.species_entry_missing
    assert evaluation.checks["species_entry_present"] is EvidenceOutcome.missing


def test_a_missing_statmech_row_is_still_graded_by_the_species_default(db_session):
    evaluation = evaluate_computed_statmech(db_session, 2_000_000_000)
    assert (evaluation.rubric, evaluation.rubric_version) == ("computed_statmech", 2)
    assert evaluation.hard_fail_reason is HardFailReason.statmech_missing


# ---------------------------------------------------------------------------
# Species-owned records do not move
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_live_matches_the_species_rubric_run_directly(db_session, name):
    """Evaluation, public fragment and machine-review context hash are identical to the species rubric's."""
    assert SCENARIOS, "an empty scenario list would pass having verified nothing"
    statmech = SCENARIOS[name](db_session)
    live = evaluate_computed_statmech(db_session, statmech.id)
    direct = evaluate_loaded_statmech_under(statmech, COMPUTED_STATMECH_V2)

    assert live.model_dump(mode="json") == direct.model_dump(mode="json")
    assert (live.rubric, live.rubric_version) == ("computed_statmech", 2)
    live_fragment, direct_fragment = build_trust_fragment(live), build_trust_fragment(direct)
    assert live_fragment == direct_fragment

    def digest(fragment):
        context = build_machine_review_evidence_context_from_trust(
            record_type="statmech", record_ref=str(statmech.id), trust_fragment=fragment
        )
        return build_machine_review_context_hash(context)

    assert digest(live_fragment) == digest(direct_fragment)
    assert active_rubric_versions_for_fragment("statmech", live_fragment) == {"computed_statmech_v2": "2"}


def test_a_species_review_stored_before_the_change_is_current_after_it_and_nothing_is_rewritten(db_session):
    statmech = SCENARIOS["opt_freq_sp"](db_session)
    # The review as it was stored before PR 6.6: legacy evaluation, legacy (species) recipe.
    legacy = build_trust_fragment(evaluate_loaded_statmech_under(statmech, COMPUTED_STATMECH_V2))
    row = _store_review(db_session, statmech, legacy, {"computed_statmech_v2": "2"})
    before = _row_snapshot(db_session)

    classification = _live_currency(db_session, statmech)
    assert classification.state is MachineReviewCurrencyState.current
    assert classification.stale_reasons == ()
    assert _row_snapshot(db_session) == before
    assert row.id in before


def test_a_ts_review_stored_with_the_species_recipe_reads_stale_for_both_reasons_and_is_not_rewritten(db_session):
    statmech = _make_ts_statmech(db_session)
    legacy = build_trust_fragment(evaluate_loaded_statmech_under(statmech, COMPUTED_STATMECH_V2))
    row = _store_review(db_session, statmech, legacy, {"computed_statmech_v2": "2"})
    before = _row_snapshot(db_session)

    classification = _live_currency(db_session, statmech)
    assert classification.state is MachineReviewCurrencyState.stale
    assert set(classification.stale_reasons) == {
        MachineReviewStaleReason.context_hash_mismatch,
        MachineReviewStaleReason.rubric_versions_mismatch,
    }
    # Read-only: the row is still there, unchanged, and no row was appended.
    assert _row_snapshot(db_session) == before
    assert row.id in before and len(before) == 1


def test_running_the_admin_trigger_appends_one_row_and_never_edits_the_stale_one(db_session):
    statmech = _make_ts_statmech(db_session)
    legacy = build_trust_fragment(evaluate_loaded_statmech_under(statmech, COMPUTED_STATMECH_V2))
    old = _store_review(db_session, statmech, legacy, {"computed_statmech_v2": "2"})
    old_snapshot = _row_snapshot(db_session)[old.id]

    result = run_admin_fake_machine_review(
        db_session, record_type="statmech", record_id=statmech.id, reviewed_at=datetime(2026, 10, 12, 12)
    )
    db_session.flush()
    assert result.rubric_versions == {"computed_ts_statmech_v1": "1"}
    snapshots = _row_snapshot(db_session)
    assert len(snapshots) == 2
    assert snapshots[old.id] == old_snapshot  # the historical row is byte-identical
    assert _live_currency(db_session, statmech).state is MachineReviewCurrencyState.current


# ---------------------------------------------------------------------------
# The machine-review recipe follows the rubric that ran
# ---------------------------------------------------------------------------


def test_the_admin_trigger_stamps_the_rubric_that_actually_evaluated_the_record(db_session):
    ts = _make_ts_statmech(db_session)
    species = SCENARIOS["opt_freq_sp"](db_session)
    stamped = {}
    for label, record in (("ts", ts), ("species", species)):
        result = run_admin_fake_machine_review(
            db_session, record_type="statmech", record_id=record.id, reviewed_at=datetime(2026, 10, 12, 12)
        )
        stamped[label] = result.rubric_versions
        row = db_session.scalars(
            select(RecordMachineReviewRow).where(RecordMachineReviewRow.record_id == record.id)
        ).one()
        assert row.rubric_versions_json == result.rubric_versions
    assert stamped == {"ts": {"computed_ts_statmech_v1": "1"}, "species": {"computed_statmech_v2": "2"}}


@pytest.mark.parametrize("record_type", ["calculation", "kinetics", "thermo", "statmech", "transport", "transition_state_entry"])
def test_the_fragment_derived_recipe_equals_the_record_type_recipe_wherever_one_rubric_exists(record_type):
    """Deriving the recipe from the evaluated rubric must not change any other record type's recipe."""
    from app.services.trust.models import TrustFragment
    from app.services.trust.rubrics import RUBRIC_REGISTRY as registry

    rubric = registry[record_type]
    fragment = TrustFragment(trust_status="partial", evidence={"rubric": public_rubric_name(rubric)})
    assert active_rubric_versions_for_fragment(record_type, fragment) == active_rubric_versions_for_record_type(
        record_type
    )


def test_an_unlisted_rubric_fails_loudly_instead_of_falling_back():
    from app.services.trust.models import TrustFragment

    for evidence in ({"rubric": "computed_made_up_v9"}, {}):
        with pytest.raises(RuntimeError, match="no entry in the active machine-review rubric table"):
            active_rubric_versions_for_fragment("statmech", TrustFragment(trust_status="partial", evidence=evidence))


def test_every_rubric_an_evaluator_can_emit_is_in_the_active_recipe_table(db_session):
    assert OWNER_SCOPED_RUBRICS and RUBRIC_REGISTRY
    emit = [*RUBRIC_REGISTRY.values(), *(rubric for _, rubric in OWNER_SCOPED_RUBRICS)]
    for rubric in emit:
        assert public_rubric_name(rubric) in ACTIVE_MACHINE_REVIEW_RUBRIC_VERSIONS, rubric.name

    # And the rubrics real evaluations actually name, one per owner kind.
    for record in (_make_ts_statmech(db_session), SCENARIOS["sparse"](db_session)):
        fragment = build_trust_fragment(evaluate_computed_statmech(db_session, record.id))
        assert fragment.evidence["rubric"] in ACTIVE_MACHINE_REVIEW_RUBRIC_VERSIONS


# ---------------------------------------------------------------------------
# Task assessment does not inherit the badge
# ---------------------------------------------------------------------------


def test_the_statmech_selection_package_never_imports_the_trust_package():
    """Owner condition 4: task assessment applies its own role requirements, not the badge's generic ones."""
    package = BACKEND / "app/services/statmech_selection"
    modules = sorted(package.rglob("*.py"))
    assert modules, "no modules scanned"
    offenders = []
    for path in modules:
        for node in ast.walk(ast.parse(path.read_text())):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module] + [f"{node.module}.{alias.name}" for alias in node.names]
            offenders += [(path.name, n) for n in names if n.startswith("app.services.trust")]
    assert offenders == []


def test_importing_the_statmech_selection_package_does_not_load_the_trust_package_transitively():
    code = (
        "import sys, app.services.statmech_selection as s\n"
        "print('app.services.trust' in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, cwd=BACKEND, timeout=120
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _store_review(db, statmech, fragment, rubric_versions):
    context = build_machine_review_evidence_context_from_trust(
        record_type="statmech", record_ref=str(statmech.id), trust_fragment=fragment
    )
    row = create_record_machine_review_row(
        db,
        record_type="statmech",
        record_id=statmech.id,
        review=RecordMachineReview(
            record_type="statmech",
            record_ref=statmech.public_ref,
            record_id=statmech.id,
            status=MachineReviewStatus.machine_screened_pass,
            reviewed_at=datetime(2026, 10, 1, 12),
        ),
        context_digest=build_machine_review_context_hash(context),
        prompt_version=ACTIVE_MACHINE_REVIEW_PROMPT_VERSION,
        rubric_versions=rubric_versions,
    )
    db.flush()
    return row


def _live_currency(db, statmech):
    fragment = resolve_record_trust_fragment(db, record_type="statmech", record_id=statmech.id)
    context = build_machine_review_evidence_context_from_trust(
        record_type="statmech", record_ref=str(statmech.id), trust_fragment=fragment
    )
    return get_record_machine_review_currency_for_record(
        db,
        record_type="statmech",
        record_id=statmech.id,
        current_context=build_machine_review_context_hash(context),
        active_prompt_version=ACTIVE_MACHINE_REVIEW_PROMPT_VERSION,
        active_rubric_versions=active_rubric_versions_for_fragment("statmech", fragment),
    )


def _row_snapshot(db) -> dict[int, tuple]:
    """Every column of every stored statmech review row, keyed by id."""
    db.flush()
    rows = db.scalars(
        select(RecordMachineReviewRow).where(RecordMachineReviewRow.record_type == SubmissionRecordType.statmech)
    ).all()
    columns = [column.key for column in RecordMachineReviewRow.__table__.columns]
    count = db.scalar(select(func.count()).select_from(RecordMachineReviewRow))
    assert count == len(rows)
    return {row.id: tuple(str(getattr(row, key)) for key in columns) for row in rows}
