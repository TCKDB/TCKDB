"""Service-layer tests for get_reaction_kinetics."""

from __future__ import annotations

import pytest

from app.api.errors import NotFoundError
from app.db.models.common import (
    KineticsModelKind,
    RecordReviewStatus,
    ScientificOriginKind,
    SubmissionRecordType,
)
from app.schemas.reads.scientific_kinetics import KineticsReadRequest
from app.services.scientific_read.kinetics import get_reaction_kinetics
from tests.services.scientific_read._factories import (
    make_chem_reaction,
    make_kinetics,
    make_reaction_entry,
    make_species,
    make_species_entry,
    next_inchi_key,
    set_review,
)


def _setup_entry(db_session):
    """Create a reaction entry with one reactant, one product, no kinetics yet."""
    rs = make_species(db_session, smiles="A", inchi_key=next_inchi_key("KA"))
    ps = make_species(db_session, smiles="B", inchi_key=next_inchi_key("KB"))
    chem = make_chem_reaction(db_session, reactants=[rs], products=[ps])
    return make_reaction_entry(
        db_session,
        reaction=chem,
        reactant_entries=[make_species_entry(db_session, rs)],
        product_entries=[make_species_entry(db_session, ps)],
    )


# ---------------------------------------------------------------------------
# Path scope + 404
# ---------------------------------------------------------------------------


def test_unknown_reaction_entry_id_raises_not_found(db_session):
    with pytest.raises(NotFoundError, match="reaction_entry not found"):
        get_reaction_kinetics(
            db_session,
            reaction_entry_id=999_999,
            request=KineticsReadRequest(),
        )


def test_empty_kinetics_returns_200_empty_records(db_session):
    entry = _setup_entry(db_session)
    response = get_reaction_kinetics(
        db_session,
        reaction_entry_id=entry.id,
        request=KineticsReadRequest(),
    )
    assert response.records == []
    assert response.pagination.total == 0
    assert response.reaction_entry_id == entry.id


# ---------------------------------------------------------------------------
# Temperature coverage + D9 ordering
# ---------------------------------------------------------------------------


def test_temperature_coverage_metadata_returned(db_session):
    entry = _setup_entry(db_session)
    make_kinetics(
        db_session,
        reaction_entry=entry,
        tmin_k=300.0,
        tmax_k=1500.0,
    )

    response = get_reaction_kinetics(
        db_session,
        reaction_entry_id=entry.id,
        request=KineticsReadRequest(temperature_min=300.0, temperature_max=2000.0),
    )

    cov = response.records[0].temperature_coverage
    assert cov is not None
    assert cov.requested_min_k == 300.0
    assert cov.requested_max_k == 2000.0
    assert cov.record_min_k == 300.0
    assert cov.record_max_k == 1500.0
    assert cov.covers_requested_range is False
    assert cov.extrapolation_distance_k == 500.0


def test_d9_ordering_full_coverage_wins_over_partial(db_session):
    entry = _setup_entry(db_session)
    # Partial cover (lower tmax)
    k_partial = make_kinetics(
        db_session, reaction_entry=entry, tmin_k=300.0, tmax_k=1500.0
    )
    # Full cover
    k_full = make_kinetics(
        db_session, reaction_entry=entry, tmin_k=300.0, tmax_k=2500.0
    )

    response = get_reaction_kinetics(
        db_session,
        reaction_entry_id=entry.id,
        request=KineticsReadRequest(temperature_min=300.0, temperature_max=2000.0),
    )

    ordered_ids = [r.kinetics_id for r in response.records]
    assert ordered_ids.index(k_full.id) < ordered_ids.index(k_partial.id)


def test_d9_ordering_extrapolation_distance_breaks_partial_ties(db_session):
    entry = _setup_entry(db_session)
    # Both partial; one closer to fully covering.
    k_close = make_kinetics(
        db_session, reaction_entry=entry, tmin_k=300.0, tmax_k=1900.0
    )
    k_far = make_kinetics(
        db_session, reaction_entry=entry, tmin_k=300.0, tmax_k=1000.0
    )

    response = get_reaction_kinetics(
        db_session,
        reaction_entry_id=entry.id,
        request=KineticsReadRequest(temperature_min=300.0, temperature_max=2000.0),
    )
    ordered_ids = [r.kinetics_id for r in response.records]
    assert ordered_ids.index(k_close.id) < ordered_ids.index(k_far.id)


# ---------------------------------------------------------------------------
# Review (shallow)
# ---------------------------------------------------------------------------


def test_min_review_status_uses_shallow_kinetics_review(db_session):
    """Approved kinetics record passes filter even if its hypothetical TS calc
    would not pass — the spec D7 rule is shallow."""
    entry = _setup_entry(db_session)
    k_approved = make_kinetics(db_session, reaction_entry=entry)
    k_under = make_kinetics(db_session, reaction_entry=entry, ea_kj_mol=20.0)
    set_review(
        db_session,
        record_type=SubmissionRecordType.kinetics,
        record_id=k_approved.id,
        status=RecordReviewStatus.approved,
    )
    set_review(
        db_session,
        record_type=SubmissionRecordType.kinetics,
        record_id=k_under.id,
        status=RecordReviewStatus.under_review,
    )

    response = get_reaction_kinetics(
        db_session,
        reaction_entry_id=entry.id,
        request=KineticsReadRequest(min_review_status=RecordReviewStatus.approved),
    )
    assert [r.kinetics_id for r in response.records] == [k_approved.id]


def test_default_excludes_rejected_kinetics(db_session):
    entry = _setup_entry(db_session)
    k_rejected = make_kinetics(db_session, reaction_entry=entry)
    set_review(
        db_session,
        record_type=SubmissionRecordType.kinetics,
        record_id=k_rejected.id,
        status=RecordReviewStatus.rejected,
    )

    response = get_reaction_kinetics(
        db_session,
        reaction_entry_id=entry.id,
        request=KineticsReadRequest(),
    )
    assert response.records == []


# ---------------------------------------------------------------------------
# Evidence completeness
# ---------------------------------------------------------------------------


def test_evidence_completeness_returned_with_score_and_checklist(db_session):
    entry = _setup_entry(db_session)
    make_kinetics(db_session, reaction_entry=entry)

    response = get_reaction_kinetics(
        db_session,
        reaction_entry_id=entry.id,
        request=KineticsReadRequest(),
    )

    breakdown = response.records[0].evidence_completeness
    expected_keys = {
        "has_source_calculations",
        "has_transition_state_entry",
        "has_ts_opt_evidence",
        "has_ts_freq_evidence",
        "has_ts_sp_evidence",
        "has_path_search_or_irc_evidence",
        "has_uncertainty",
        "has_geometry_validation",
        "has_scf_stability",
    }
    assert set(breakdown.checklist.keys()) == expected_keys
    assert breakdown.max == 9
    assert 0 <= breakdown.score <= 9


def test_evidence_completeness_includes_uncertainty_when_present(db_session):
    from app.db.models.common import KineticsUncertaintyKind

    entry = _setup_entry(db_session)
    make_kinetics(db_session, reaction_entry=entry)
    # Make a second kinetics with uncertainty
    k_with_unc = make_kinetics(db_session, reaction_entry=entry, ea_kj_mol=20.0)
    k_with_unc.a_uncertainty = 1.5
    k_with_unc.a_uncertainty_kind = KineticsUncertaintyKind.multiplicative
    db_session.flush()

    response = get_reaction_kinetics(
        db_session,
        reaction_entry_id=entry.id,
        request=KineticsReadRequest(),
    )

    by_id = {r.kinetics_id: r for r in response.records}
    assert by_id[k_with_unc.id].evidence_completeness.checklist["has_uncertainty"] is True


# ---------------------------------------------------------------------------
# Non-TS-backed (Phase 2.2)
# ---------------------------------------------------------------------------


def test_non_ts_backed_kinetics_has_null_ts_provenance(db_session):
    entry = _setup_entry(db_session)
    make_kinetics(
        db_session,
        reaction_entry=entry,
        scientific_origin=ScientificOriginKind.experimental,
    )

    response = get_reaction_kinetics(
        db_session,
        reaction_entry_id=entry.id,
        request=KineticsReadRequest(),
    )
    record = response.records[0]
    assert record.scientific_origin == ScientificOriginKind.experimental
    p = record.provenance
    assert p.transition_state_entry_id is None
    assert p.ts_opt_calculation_id is None
    assert p.ts_freq_calculation_id is None
    assert p.ts_sp_calculation_id is None
    assert p.path_search is None
    assert p.irc is None
    assert p.geometry_validation is None
    assert p.scf_stability is None
    # Non-TS provenance keys are still present (just None).
    assert p.literature is None
    assert p.software_release is None
    assert p.workflow_tool_release is None


def test_non_ts_backed_kinetics_not_rejected_by_ts_evidence_false(db_session):
    """Non-TS-backed kinetics with all TS predicates false is still returned
    by min_review_status=approved when it has direct review approval."""
    entry = _setup_entry(db_session)
    k = make_kinetics(
        db_session,
        reaction_entry=entry,
        scientific_origin=ScientificOriginKind.experimental,
    )
    set_review(
        db_session,
        record_type=SubmissionRecordType.kinetics,
        record_id=k.id,
        status=RecordReviewStatus.approved,
    )

    response = get_reaction_kinetics(
        db_session,
        reaction_entry_id=entry.id,
        request=KineticsReadRequest(min_review_status=RecordReviewStatus.approved),
    )

    assert len(response.records) == 1
    record = response.records[0]
    assert record.evidence_completeness.checklist["has_transition_state_entry"] is False
    assert record.evidence_completeness.checklist["has_ts_opt_evidence"] is False


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_client_supplied_sort_rejected(db_session):
    entry = _setup_entry(db_session)
    with pytest.raises(ValueError, match="client_sort_not_supported"):
        get_reaction_kinetics(
            db_session,
            reaction_entry_id=entry.id,
            request=KineticsReadRequest(sort="anything"),
        )


def test_unknown_include_token_rejected(db_session):
    entry = _setup_entry(db_session)
    with pytest.raises(ValueError, match="unknown_include_token"):
        get_reaction_kinetics(
            db_session,
            reaction_entry_id=entry.id,
            request=KineticsReadRequest(include=["banana"]),
        )


def test_temperature_min_greater_than_max_rejected(db_session):
    entry = _setup_entry(db_session)
    with pytest.raises(ValueError, match="invalid_temperature_range"):
        get_reaction_kinetics(
            db_session,
            reaction_entry_id=entry.id,
            request=KineticsReadRequest(temperature_min=2000.0, temperature_max=300.0),
        )


def test_limit_max_enforced(db_session):
    entry = _setup_entry(db_session)
    with pytest.raises(ValueError, match="invalid_pagination"):
        get_reaction_kinetics(
            db_session,
            reaction_entry_id=entry.id,
            request=KineticsReadRequest(limit=999),
        )


def test_model_kind_filter_excludes_other_kinds(db_session):
    entry = _setup_entry(db_session)
    make_kinetics(
        db_session,
        reaction_entry=entry,
        model_kind=KineticsModelKind.arrhenius,
        n=None,
    )
    make_kinetics(
        db_session,
        reaction_entry=entry,
        model_kind=KineticsModelKind.modified_arrhenius,
    )

    response = get_reaction_kinetics(
        db_session,
        reaction_entry_id=entry.id,
        request=KineticsReadRequest(model_kind=KineticsModelKind.arrhenius),
    )
    assert all(r.model_kind == KineticsModelKind.arrhenius for r in response.records)
    assert len(response.records) == 1


def test_sort_is_deterministic(db_session):
    entry = _setup_entry(db_session)
    make_kinetics(db_session, reaction_entry=entry)
    make_kinetics(db_session, reaction_entry=entry, ea_kj_mol=20.0)

    r1 = get_reaction_kinetics(
        db_session, reaction_entry_id=entry.id, request=KineticsReadRequest()
    )
    r2 = get_reaction_kinetics(
        db_session, reaction_entry_id=entry.id, request=KineticsReadRequest()
    )
    assert r1.model_dump() == r2.model_dump()
