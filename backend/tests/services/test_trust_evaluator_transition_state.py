"""Tests for ``computed_transition_state_v1``.

These tests pin the contract of the deterministic transition-state
trust/evidence evaluator (spec:
``backend/docs/specs/transition_state_trust_rubric.md``). Each test uses
the per-test rollback transaction in ``conftest.py`` and direct ORM
inserts; no upload pipeline is exercised.
"""

from __future__ import annotations

import hashlib
import itertools

import pytest
from sqlalchemy.orm import Session

from app.db.models.calculation import (
    Calculation,
    CalculationArtifact,
    CalculationDependency,
    CalculationFreqResult,
    CalculationGeometryValidation,
    CalculationInputGeometry,
    CalculationIRCResult,
    CalculationOptResult,
    CalculationOutputGeometry,
    CalculationPathSearchResult,
    CalculationSPResult,
)
from app.db.models.common import (
    ArtifactKind,
    CalculationDependencyRole,
    CalculationGeometryRole,
    CalculationQuality,
    CalculationType,
    IRCDirection,
    MoleculeKind,
    PathSearchMethod,
    ReactionRole,
    StereoKind,
    TransitionStateEntryStatus,
    ValidationStatus,
)
from app.db.models.geometry import Geometry
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.reaction import (
    ChemReaction,
    ReactionEntry,
    ReactionEntryStructureParticipant,
)
from app.db.models.software import Software, SoftwareRelease
from app.db.models.species import Species, SpeciesEntry
from app.db.models.transition_state import TransitionState, TransitionStateEntry
from app.services.trust import (
    COMPUTED_TRANSITION_STATE_V1,
    EvidenceBadge,
    HardFailReason,
    evaluate_computed_transition_state_entry,
    evaluate_loaded_transition_state_entry,
)

_TS_COUNTER = itertools.count(1)


def _next_token() -> str:
    return f"ts-trust-{next(_TS_COUNTER)}"


def _next_inchi_key() -> str:
    return f"INCHIKEY{next(_TS_COUNTER):04d}-AAA"


def _next_geom_hash() -> str:
    return hashlib.sha256(_next_token().encode()).hexdigest()


def _make_species(db_session: Session, smiles: str | None = None) -> Species:
    # Species identity is (smiles, charge, multiplicity) (DR-0031), so each
    # fixture species needs a distinct smiles or it collides with sibling /
    # cross-test species on the shared DB. Trust evaluation never parses
    # smiles, so a unique placeholder is sufficient here.
    sp = Species(
        kind=MoleculeKind.molecule,
        smiles=smiles if smiles is not None else _next_token(),
        inchi_key=_next_inchi_key(),
        charge=0,
        multiplicity=1,
        stereo_kind=StereoKind.achiral,
    )
    db_session.add(sp)
    db_session.flush()
    return sp


def _make_species_entry(db_session: Session, species: Species) -> SpeciesEntry:
    entry = SpeciesEntry(species_id=species.id, unmapped_smiles=species.smiles)
    db_session.add(entry)
    db_session.flush()
    return entry


def _make_lot(db_session: Session) -> LevelOfTheory:
    raw = _next_token()
    lot = LevelOfTheory(
        method="wb97xd",
        basis="def2tzvp",
        lot_hash=hashlib.sha256(raw.encode()).hexdigest(),
    )
    db_session.add(lot)
    db_session.flush()
    return lot


def _make_software_release(db_session: Session) -> SoftwareRelease:
    sw = Software(name=_next_token())
    db_session.add(sw)
    db_session.flush()
    release = SoftwareRelease(software_id=sw.id, version="1.0")
    db_session.add(release)
    db_session.flush()
    return release


def _make_geometry(db_session: Session) -> Geometry:
    g = Geometry(natoms=3, geom_hash=_next_geom_hash(), xyz_text="dummy")
    db_session.add(g)
    db_session.flush()
    return g


def _make_reaction_entry(db_session: Session) -> ReactionEntry:
    reactant_species = _make_species(db_session)
    product_species = _make_species(db_session)
    reactant_entry = _make_species_entry(db_session, reactant_species)
    product_entry = _make_species_entry(db_session, product_species)
    reaction = ChemReaction(reversible=False)
    db_session.add(reaction)
    db_session.flush()
    entry = ReactionEntry(reaction_id=reaction.id)
    db_session.add(entry)
    db_session.flush()
    db_session.add_all(
        [
            ReactionEntryStructureParticipant(
                reaction_entry_id=entry.id,
                species_entry_id=reactant_entry.id,
                role=ReactionRole.reactant,
                participant_index=1,
            ),
            ReactionEntryStructureParticipant(
                reaction_entry_id=entry.id,
                species_entry_id=product_entry.id,
                role=ReactionRole.product,
                participant_index=1,
            ),
        ]
    )
    db_session.flush()
    return entry


def _make_ts_entry(
    db_session: Session,
    *,
    status: TransitionStateEntryStatus = TransitionStateEntryStatus.optimized,
    charge: int = 0,
    multiplicity: int = 2,
    unmapped_smiles: str | None = "[CH3]...[H]...[OH]",
) -> TransitionStateEntry:
    reaction_entry = _make_reaction_entry(db_session)
    ts = TransitionState(reaction_entry_id=reaction_entry.id)
    db_session.add(ts)
    db_session.flush()
    ts_entry = TransitionStateEntry(
        transition_state_id=ts.id,
        charge=charge,
        multiplicity=multiplicity,
        status=status,
        unmapped_smiles=unmapped_smiles,
    )
    db_session.add(ts_entry)
    db_session.flush()
    return ts_entry


def _attach_ts_opt_calc(
    db_session: Session,
    ts_entry: TransitionStateEntry,
    *,
    geom_validation: ValidationStatus | None = ValidationStatus.passed,
    artifacts: bool = True,
    workflow_tool: bool = False,
) -> Calculation:
    lot = _make_lot(db_session)
    release = _make_software_release(db_session)
    calc = Calculation(
        type=CalculationType.opt,
        quality=CalculationQuality.raw,
        transition_state_entry_id=ts_entry.id,
        lot_id=lot.id,
        software_release_id=release.id,
    )
    db_session.add(calc)
    db_session.flush()
    db_session.add(
        CalculationInputGeometry(
            calculation_id=calc.id,
            geometry_id=_make_geometry(db_session).id,
            input_order=1,
        )
    )
    db_session.add(
        CalculationOutputGeometry(
            calculation_id=calc.id,
            geometry_id=_make_geometry(db_session).id,
            output_order=1,
            role=CalculationGeometryRole.final,
        )
    )
    db_session.add(
        CalculationOptResult(
            calculation_id=calc.id,
            final_energy_hartree=-100.0,
            converged=True,
        )
    )
    if artifacts:
        db_session.add(
            CalculationArtifact(
                calculation_id=calc.id,
                kind=ArtifactKind.output_log,
                uri="s3://test/opt-log",
                filename="opt.log",
            )
        )
    if geom_validation is not None:
        db_session.add(
            CalculationGeometryValidation(
                calculation_id=calc.id,
                validation_status=geom_validation,
                species_smiles="CCO",
                is_isomorphic=True,
            )
        )
    db_session.flush()
    db_session.refresh(calc)
    return calc


def _attach_ts_freq_calc(
    db_session: Session,
    ts_entry: TransitionStateEntry,
    opt_calc: Calculation | None = None,
    *,
    n_imag: int | None = 1,
    imag_freq_cm1: float | None = -550.0,
) -> Calculation:
    lot = _make_lot(db_session)
    release = _make_software_release(db_session)
    calc = Calculation(
        type=CalculationType.freq,
        quality=CalculationQuality.raw,
        transition_state_entry_id=ts_entry.id,
        lot_id=lot.id,
        software_release_id=release.id,
    )
    db_session.add(calc)
    db_session.flush()
    db_session.add(
        CalculationInputGeometry(
            calculation_id=calc.id,
            geometry_id=_make_geometry(db_session).id,
            input_order=1,
        )
    )
    db_session.add(
        CalculationFreqResult(
            calculation_id=calc.id,
            n_imag=n_imag,
            imag_freq_cm1=imag_freq_cm1,
            zpe_hartree=0.05,
        )
    )
    if opt_calc is not None:
        db_session.add(
            CalculationDependency(
                parent_calculation_id=opt_calc.id,
                child_calculation_id=calc.id,
                dependency_role=CalculationDependencyRole.freq_on,
            )
        )
    db_session.flush()
    db_session.refresh(calc)
    return calc


def _attach_ts_sp_calc(
    db_session: Session,
    ts_entry: TransitionStateEntry,
    opt_calc: Calculation,
) -> Calculation:
    lot = _make_lot(db_session)
    release = _make_software_release(db_session)
    calc = Calculation(
        type=CalculationType.sp,
        quality=CalculationQuality.raw,
        transition_state_entry_id=ts_entry.id,
        lot_id=lot.id,
        software_release_id=release.id,
    )
    db_session.add(calc)
    db_session.flush()
    db_session.add(
        CalculationInputGeometry(
            calculation_id=calc.id,
            geometry_id=_make_geometry(db_session).id,
            input_order=1,
        )
    )
    db_session.add(
        CalculationSPResult(
            calculation_id=calc.id,
            electronic_energy_hartree=-100.5,
        )
    )
    db_session.add(
        CalculationDependency(
            parent_calculation_id=opt_calc.id,
            child_calculation_id=calc.id,
            dependency_role=CalculationDependencyRole.single_point_on,
        )
    )
    db_session.flush()
    db_session.refresh(calc)
    return calc


def _attach_ts_irc_calc(
    db_session: Session,
    ts_entry: TransitionStateEntry,
    opt_calc: Calculation,
) -> Calculation:
    lot = _make_lot(db_session)
    release = _make_software_release(db_session)
    calc = Calculation(
        type=CalculationType.irc,
        quality=CalculationQuality.raw,
        transition_state_entry_id=ts_entry.id,
        lot_id=lot.id,
        software_release_id=release.id,
    )
    db_session.add(calc)
    db_session.flush()
    db_session.add(
        CalculationInputGeometry(
            calculation_id=calc.id,
            geometry_id=_make_geometry(db_session).id,
            input_order=1,
        )
    )
    db_session.add(
        CalculationIRCResult(
            calculation_id=calc.id,
            direction=IRCDirection.both,
            has_forward=True,
            has_reverse=True,
        )
    )
    db_session.add(
        CalculationDependency(
            parent_calculation_id=opt_calc.id,
            child_calculation_id=calc.id,
            dependency_role=CalculationDependencyRole.irc_start,
        )
    )
    db_session.flush()
    db_session.refresh(calc)
    return calc


def _attach_path_search_parent(
    db_session: Session,
    ts_entry: TransitionStateEntry,
    opt_calc: Calculation,
) -> Calculation:
    """A path_search calc that produced the TS opt; linked via optimized_from."""
    lot = _make_lot(db_session)
    release = _make_software_release(db_session)
    calc = Calculation(
        type=CalculationType.path_search,
        quality=CalculationQuality.raw,
        transition_state_entry_id=ts_entry.id,
        lot_id=lot.id,
        software_release_id=release.id,
    )
    db_session.add(calc)
    db_session.flush()
    db_session.add(
        CalculationInputGeometry(
            calculation_id=calc.id,
            geometry_id=_make_geometry(db_session).id,
            input_order=1,
        )
    )
    db_session.add(
        CalculationPathSearchResult(
            calculation_id=calc.id,
            method=PathSearchMethod.neb,
            converged=True,
        )
    )
    # TS opt was optimized_from the path_search parent.
    db_session.add(
        CalculationDependency(
            parent_calculation_id=calc.id,
            child_calculation_id=opt_calc.id,
            dependency_role=CalculationDependencyRole.optimized_from,
        )
    )
    db_session.flush()
    db_session.refresh(calc)
    return calc


# ---------- Tests ----------


def test_none_input_hard_fails():
    result = evaluate_loaded_transition_state_entry(None)
    assert result.label is EvidenceBadge.hard_failed
    assert result.hard_fail_reason is HardFailReason.transition_state_entry_missing
    assert result.rubric == "computed_transition_state"
    assert result.rubric_version == 1
    assert result.record_type == "transition_state_entry"
    assert result.record_id is None


def test_rejected_status_hard_fails(db_session: Session):
    ts_entry = _make_ts_entry(
        db_session, status=TransitionStateEntryStatus.rejected
    )
    result = evaluate_loaded_transition_state_entry(ts_entry)
    assert result.label is EvidenceBadge.hard_failed
    assert result.hard_fail_reason is HardFailReason.ts_entry_status_rejected
    # The ts_status_not_rejected check still ran and reports the contradiction.
    assert "ts_status_not_rejected" in result.missing_checks


def test_invalid_multiplicity_hard_fails(db_session: Session):
    """The DB CheckConstraint blocks invalid multiplicity from ever flushing,
    so this case is intentionally tested at the unit level with a constructed
    (unflushed) object graph. The rubric mirrors the constraint so a future
    relaxation surfaces as a hard fail rather than a 500.
    """
    reaction_entry = _make_reaction_entry(db_session)
    ts = TransitionState(reaction_entry_id=reaction_entry.id)
    db_session.add(ts)
    db_session.flush()
    # Construct without flushing — the in-memory graph is fully usable by the
    # pure loaded evaluator without ever hitting the CheckConstraint.
    ts_entry = TransitionStateEntry(
        transition_state_id=ts.id,
        transition_state=ts,
        charge=0,
        multiplicity=0,
        status=TransitionStateEntryStatus.optimized,
        unmapped_smiles=None,
    )
    with db_session.no_autoflush:
        result = evaluate_loaded_transition_state_entry(ts_entry)
    assert result.label is EvidenceBadge.hard_failed
    assert result.hard_fail_reason is HardFailReason.multiplicity_invalid


def test_sparse_entry_low_completeness_no_hard_fail(db_session: Session):
    ts_entry = _make_ts_entry(
        db_session, status=TransitionStateEntryStatus.guess, unmapped_smiles=None
    )
    result = evaluate_loaded_transition_state_entry(ts_entry)
    assert result.hard_fail_reason is None
    assert result.label is not EvidenceBadge.hard_failed
    # Identity facts pass; supporting calculations missing → low ratio.
    assert "transition_state_parent_present" in result.passed_checks
    assert "supporting_calculations_present" in result.missing_checks
    assert result.label in {
        EvidenceBadge.unsupported,
        EvidenceBadge.sparse,
        EvidenceBadge.partial,
    }


def test_guess_with_n_imag_zero_warns_not_hard_fail(db_session: Session):
    ts_entry = _make_ts_entry(db_session, status=TransitionStateEntryStatus.guess)
    opt = _attach_ts_opt_calc(db_session, ts_entry)
    _attach_ts_freq_calc(db_session, ts_entry, opt, n_imag=0, imag_freq_cm1=None)
    db_session.refresh(ts_entry)

    result = evaluate_loaded_transition_state_entry(ts_entry)
    assert result.hard_fail_reason is None
    # n_imag != 1 means single_imaginary_frequency_for_ts is missing, but the
    # rubric does not collapse to hard_failed for guess-stage entries.
    assert "single_imaginary_frequency_for_ts" in result.missing_checks


def test_guess_with_n_imag_two_warns_not_hard_fail(db_session: Session):
    ts_entry = _make_ts_entry(db_session, status=TransitionStateEntryStatus.guess)
    opt = _attach_ts_opt_calc(db_session, ts_entry)
    _attach_ts_freq_calc(db_session, ts_entry, opt, n_imag=2, imag_freq_cm1=-300.0)
    db_session.refresh(ts_entry)

    result = evaluate_loaded_transition_state_entry(ts_entry)
    assert result.hard_fail_reason is None


@pytest.mark.parametrize(
    "status",
    [TransitionStateEntryStatus.optimized, TransitionStateEntryStatus.validated],
)
def test_validated_with_n_imag_one_passes_freq_check(
    db_session: Session, status: TransitionStateEntryStatus
):
    ts_entry = _make_ts_entry(db_session, status=status)
    opt = _attach_ts_opt_calc(db_session, ts_entry)
    _attach_ts_freq_calc(db_session, ts_entry, opt, n_imag=1, imag_freq_cm1=-550.0)
    db_session.refresh(ts_entry)

    result = evaluate_loaded_transition_state_entry(ts_entry)
    assert result.hard_fail_reason is None
    assert "single_imaginary_frequency_for_ts" in result.passed_checks
    assert "imaginary_frequency_count_recorded" in result.passed_checks
    assert "imaginary_frequency_value_present" in result.passed_checks


def test_validated_with_n_imag_zero_hard_fails(db_session: Session):
    ts_entry = _make_ts_entry(
        db_session, status=TransitionStateEntryStatus.validated
    )
    opt = _attach_ts_opt_calc(db_session, ts_entry)
    _attach_ts_freq_calc(db_session, ts_entry, opt, n_imag=0, imag_freq_cm1=None)
    db_session.refresh(ts_entry)

    result = evaluate_loaded_transition_state_entry(ts_entry)
    assert result.label is EvidenceBadge.hard_failed
    assert (
        result.hard_fail_reason
        is HardFailReason.frequency_source_has_zero_imaginary_modes_for_validated_ts
    )


def test_validated_with_n_imag_multiple_hard_fails(db_session: Session):
    ts_entry = _make_ts_entry(
        db_session, status=TransitionStateEntryStatus.optimized
    )
    opt = _attach_ts_opt_calc(db_session, ts_entry)
    _attach_ts_freq_calc(db_session, ts_entry, opt, n_imag=3, imag_freq_cm1=-100.0)
    db_session.refresh(ts_entry)

    result = evaluate_loaded_transition_state_entry(ts_entry)
    assert result.label is EvidenceBadge.hard_failed
    assert (
        result.hard_fail_reason
        is HardFailReason.frequency_source_has_multiple_imaginary_modes_for_validated_ts
    )


def test_missing_freq_is_missing_not_hard_fail(db_session: Session):
    ts_entry = _make_ts_entry(
        db_session, status=TransitionStateEntryStatus.optimized
    )
    _attach_ts_opt_calc(db_session, ts_entry)
    db_session.refresh(ts_entry)

    result = evaluate_loaded_transition_state_entry(ts_entry)
    assert result.hard_fail_reason is None
    assert "ts_frequency_present" in result.missing_checks
    # n_imag checks become not_applicable when no freq result is in the set.
    assert "single_imaginary_frequency_for_ts" in result.not_applicable_checks


def test_irc_raises_completeness(db_session: Session):
    # Baseline: opt + freq.
    ts_entry_base = _make_ts_entry(db_session)
    opt_base = _attach_ts_opt_calc(db_session, ts_entry_base)
    _attach_ts_freq_calc(db_session, ts_entry_base, opt_base)
    db_session.refresh(ts_entry_base)
    baseline = evaluate_loaded_transition_state_entry(ts_entry_base)

    # With IRC: same identity + opt + freq + irc.
    ts_entry_irc = _make_ts_entry(db_session)
    opt_irc = _attach_ts_opt_calc(db_session, ts_entry_irc)
    _attach_ts_freq_calc(db_session, ts_entry_irc, opt_irc)
    _attach_ts_irc_calc(db_session, ts_entry_irc, opt_irc)
    db_session.refresh(ts_entry_irc)
    with_irc = evaluate_loaded_transition_state_entry(ts_entry_irc)

    assert with_irc.evidence_completeness > baseline.evidence_completeness
    assert "irc_evidence_present" in with_irc.passed_checks


def test_path_search_raises_completeness(db_session: Session):
    ts_entry_base = _make_ts_entry(db_session)
    opt_base = _attach_ts_opt_calc(db_session, ts_entry_base)
    _attach_ts_freq_calc(db_session, ts_entry_base, opt_base)
    db_session.refresh(ts_entry_base)
    baseline = evaluate_loaded_transition_state_entry(ts_entry_base)

    ts_entry_ps = _make_ts_entry(db_session)
    opt_ps = _attach_ts_opt_calc(db_session, ts_entry_ps)
    _attach_ts_freq_calc(db_session, ts_entry_ps, opt_ps)
    _attach_path_search_parent(db_session, ts_entry_ps, opt_ps)
    db_session.refresh(ts_entry_ps)
    with_ps = evaluate_loaded_transition_state_entry(ts_entry_ps)

    assert with_ps.evidence_completeness > baseline.evidence_completeness
    assert "path_search_evidence_present" in with_ps.passed_checks


def test_no_irc_no_path_search_not_hard_fail(db_session: Session):
    ts_entry = _make_ts_entry(db_session)
    opt = _attach_ts_opt_calc(db_session, ts_entry)
    _attach_ts_freq_calc(db_session, ts_entry, opt)
    db_session.refresh(ts_entry)

    result = evaluate_loaded_transition_state_entry(ts_entry)
    assert result.hard_fail_reason is None
    assert "irc_evidence_present" in result.missing_checks
    assert "path_search_evidence_present" in result.missing_checks
    # At least partial because identity, supporting calcs, lot/software all pass.
    assert result.label in {
        EvidenceBadge.partial,
        EvidenceBadge.mostly_supported,
        EvidenceBadge.well_supported,
    }


def test_source_calc_artifacts_lot_software(db_session: Session):
    ts_entry = _make_ts_entry(db_session)
    opt = _attach_ts_opt_calc(db_session, ts_entry, artifacts=True)
    _attach_ts_freq_calc(db_session, ts_entry, opt)
    _attach_ts_sp_calc(db_session, ts_entry, opt)
    db_session.refresh(ts_entry)

    result = evaluate_loaded_transition_state_entry(ts_entry)
    assert "source_calculation_lot_present" in result.passed_checks
    assert "source_calculation_software_present" in result.passed_checks
    assert "source_calculation_artifacts_present" in result.passed_checks


def test_source_calc_failed_geometry_validation_hard_fails(db_session: Session):
    ts_entry = _make_ts_entry(db_session)
    _attach_ts_opt_calc(db_session, ts_entry, geom_validation=ValidationStatus.fail)
    db_session.refresh(ts_entry)

    result = evaluate_loaded_transition_state_entry(ts_entry)
    assert result.label is EvidenceBadge.hard_failed
    assert (
        result.hard_fail_reason
        is HardFailReason.geometry_validation_failed_for_source_calculation
    )
    # The warning check is suppressed to not_applicable when the hard-fail fires.
    assert (
        "geometry_validation_not_failed_for_source_calculations"
        in result.not_applicable_checks
    )


def test_all_source_calcs_hard_failed_collapses_to_hard_fail(db_session: Session):
    ts_entry = _make_ts_entry(db_session)
    # Single source calc, rejected → calc-level hard fail.
    opt = _attach_ts_opt_calc(db_session, ts_entry, geom_validation=None)
    opt.quality = CalculationQuality.rejected
    db_session.flush()
    db_session.refresh(ts_entry)

    result = evaluate_loaded_transition_state_entry(ts_entry)
    assert result.label is EvidenceBadge.hard_failed
    assert (
        result.hard_fail_reason
        is HardFailReason.all_source_calculations_hard_failed
    )


def test_loaded_matches_session_wrapper(db_session: Session):
    ts_entry = _make_ts_entry(db_session)
    opt = _attach_ts_opt_calc(db_session, ts_entry)
    _attach_ts_freq_calc(db_session, ts_entry, opt)
    db_session.refresh(ts_entry)

    loaded = evaluate_loaded_transition_state_entry(ts_entry)
    via_wrapper = evaluate_computed_transition_state_entry(db_session, ts_entry.id)

    # Compare the public envelope (check_results contain non-comparable runner
    # references via the spec, but the bucket lists / counts / label are the
    # public contract).
    assert loaded.label is via_wrapper.label
    assert loaded.hard_fail_reason is via_wrapper.hard_fail_reason
    assert loaded.evidence_completeness == via_wrapper.evidence_completeness
    assert loaded.passed_checks == via_wrapper.passed_checks
    assert loaded.missing_checks == via_wrapper.missing_checks
    assert loaded.warning_checks == via_wrapper.warning_checks
    assert loaded.not_applicable_checks == via_wrapper.not_applicable_checks


def test_evaluator_does_not_mutate_records(db_session: Session):
    ts_entry = _make_ts_entry(db_session)
    opt = _attach_ts_opt_calc(db_session, ts_entry)
    _attach_ts_freq_calc(db_session, ts_entry, opt)
    db_session.refresh(ts_entry)

    pre_status = ts_entry.status
    pre_multiplicity = ts_entry.multiplicity
    pre_charge = ts_entry.charge
    pre_calc_quality = opt.quality

    evaluate_loaded_transition_state_entry(ts_entry)

    assert ts_entry.status is pre_status
    assert ts_entry.multiplicity == pre_multiplicity
    assert ts_entry.charge == pre_charge
    assert opt.quality is pre_calc_quality


def test_session_wrapper_returns_hard_fail_for_missing_id(db_session: Session):
    # An id that no row matches.
    result = evaluate_computed_transition_state_entry(db_session, 9_999_999)
    assert result.label is EvidenceBadge.hard_failed
    assert result.hard_fail_reason is HardFailReason.transition_state_entry_missing
    assert result.record_id == 9_999_999


def test_rubric_metadata_pinned():
    """Pin the public contract of the rubric metadata."""
    assert COMPUTED_TRANSITION_STATE_V1.name == "computed_transition_state"
    assert COMPUTED_TRANSITION_STATE_V1.version == 1
    assert COMPUTED_TRANSITION_STATE_V1.record_type == "transition_state_entry"
    assert len(COMPUTED_TRANSITION_STATE_V1.checks) == 28


def test_calculation_dependencies_check_passes_when_freq_linked(db_session: Session):
    ts_entry = _make_ts_entry(db_session)
    opt = _attach_ts_opt_calc(db_session, ts_entry)
    _attach_ts_freq_calc(db_session, ts_entry, opt)
    db_session.refresh(ts_entry)

    result = evaluate_loaded_transition_state_entry(ts_entry)
    assert "calculation_dependencies_present" in result.passed_checks


def test_freq_representative_picks_latest_by_id(db_session: Session):
    """When multiple freq calcs exist, the latest by id wins (tie-break rule)."""
    ts_entry = _make_ts_entry(
        db_session, status=TransitionStateEntryStatus.validated
    )
    opt = _attach_ts_opt_calc(db_session, ts_entry)
    # Earlier freq calc with n_imag=1 (good).
    _attach_ts_freq_calc(db_session, ts_entry, opt, n_imag=1, imag_freq_cm1=-500.0)
    # Later freq calc with n_imag=0 (would-be contradiction for validated).
    later_freq = _attach_ts_freq_calc(
        db_session, ts_entry, opt, n_imag=0, imag_freq_cm1=None
    )
    db_session.refresh(ts_entry)

    # The latest is the contradiction → hard-fail under validated status.
    result = evaluate_loaded_transition_state_entry(ts_entry)
    assert result.label is EvidenceBadge.hard_failed
    assert (
        result.hard_fail_reason
        is HardFailReason.frequency_source_has_zero_imaginary_modes_for_validated_ts
    )
    # Sanity: the later one has the larger id.
    assert later_freq.id > opt.id
