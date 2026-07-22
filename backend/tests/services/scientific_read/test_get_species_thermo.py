"""Service-layer tests for get_species_thermo."""

from __future__ import annotations

import pytest

from app.api.errors import NotFoundError
from app.db.models.common import RecordReviewStatus, SubmissionRecordType
from app.schemas.reads.scientific_common import CollapseMode
from app.schemas.reads.scientific_thermo import (
    ThermoModelKindQuery,
    ThermoReadRequest,
)
from app.services.scientific_read.thermo import get_species_thermo
from tests.services.scientific_read._factories import (
    attach_thermo_nasa,
    attach_thermo_nasa9,
    attach_thermo_points,
    attach_thermo_wilhoit,
    make_species,
    make_species_entry,
    make_thermo_scalar,
    next_inchi_key,
    set_review,
)


def _entry_with_smiles(db_session, smiles: str | None = None):
    species = make_species(
        db_session, smiles=smiles, inchi_key=next_inchi_key("TH")
    )
    return make_species_entry(db_session, species)


# ---------------------------------------------------------------------------
# Path scope + 404
# ---------------------------------------------------------------------------


def test_unknown_species_entry_id_raises_not_found(db_session):
    with pytest.raises(NotFoundError):
        get_species_thermo(
            db_session,
            species_entry_id=999_999,
            request=ThermoReadRequest(),
        )


def test_empty_thermo_returns_empty_records(db_session):
    entry = _entry_with_smiles(db_session)
    response = get_species_thermo(
        db_session,
        species_entry_id=entry.id,
        request=ThermoReadRequest(),
    )
    assert response.records == []
    assert response.species_entry_id == entry.id


def test_collapse_first_applies_before_offset(db_session):
    entry = _entry_with_smiles(db_session)
    make_thermo_scalar(db_session, species_entry=entry)

    response = get_species_thermo(
        db_session,
        species_entry_id=entry.id,
        request=ThermoReadRequest(collapse=CollapseMode.first, offset=1),
    )

    assert response.records == []
    assert response.pagination.total == 1
    assert response.pagination.returned == 0


# ---------------------------------------------------------------------------
# Model shapes
# ---------------------------------------------------------------------------


def test_scalar_thermo_returns_scalar_block_only(db_session):
    entry = _entry_with_smiles(db_session)
    make_thermo_scalar(db_session, species_entry=entry)

    response = get_species_thermo(
        db_session, species_entry_id=entry.id, request=ThermoReadRequest()
    )
    record = response.records[0]
    assert record.model_kind == ThermoModelKindQuery.scalar
    assert record.h298_kj_mol == -12.3
    assert record.s298_j_mol_k == 250.1
    assert record.nasa is None
    assert record.points is None


def test_nasa_thermo_returns_nasa_block(db_session):
    entry = _entry_with_smiles(db_session)
    thermo = make_thermo_scalar(db_session, species_entry=entry)
    attach_thermo_nasa(db_session, thermo=thermo)

    response = get_species_thermo(
        db_session, species_entry_id=entry.id, request=ThermoReadRequest()
    )
    record = response.records[0]
    assert record.model_kind == ThermoModelKindQuery.nasa
    assert record.nasa is not None
    assert record.nasa.t_low == 200.0
    assert record.nasa.t_high == 6000.0
    assert len(record.nasa.low_temperature_coefficients) == 7
    assert len(record.nasa.high_temperature_coefficients) == 7


def test_points_thermo_returns_points_array(db_session):
    entry = _entry_with_smiles(db_session)
    thermo = make_thermo_scalar(db_session, species_entry=entry)
    attach_thermo_points(db_session, thermo=thermo, temperatures_k=[300.0, 400.0, 500.0])

    response = get_species_thermo(
        db_session, species_entry_id=entry.id, request=ThermoReadRequest()
    )
    record = response.records[0]
    assert record.model_kind == ThermoModelKindQuery.points
    assert record.points is not None
    assert [p.temperature_k for p in record.points] == [300.0, 400.0, 500.0]


def test_model_kind_filter_excludes_other_shapes(db_session):
    entry = _entry_with_smiles(db_session)
    # NASA record + scalar record on same entry — not fully realistic but valid.
    nasa_thermo = make_thermo_scalar(db_session, species_entry=entry)
    attach_thermo_nasa(db_session, thermo=nasa_thermo)
    make_thermo_scalar(db_session, species_entry=entry, h298_kj_mol=-99.0)

    response = get_species_thermo(
        db_session,
        species_entry_id=entry.id,
        request=ThermoReadRequest(model_kind=ThermoModelKindQuery.nasa),
    )
    assert len(response.records) == 1
    assert response.records[0].thermo_id == nasa_thermo.id


# ---------------------------------------------------------------------------
# Review (shallow)
# ---------------------------------------------------------------------------


def test_default_excludes_deprecated_thermo(db_session):
    entry = _entry_with_smiles(db_session)
    t = make_thermo_scalar(db_session, species_entry=entry)
    set_review(
        db_session,
        record_type=SubmissionRecordType.thermo,
        record_id=t.id,
        status=RecordReviewStatus.deprecated,
    )

    response = get_species_thermo(
        db_session, species_entry_id=entry.id, request=ThermoReadRequest()
    )
    assert response.records == []


def test_min_review_status_uses_shallow_thermo_review(db_session):
    entry = _entry_with_smiles(db_session)
    t_approved = make_thermo_scalar(db_session, species_entry=entry)
    t_under = make_thermo_scalar(
        db_session, species_entry=entry, h298_kj_mol=-99.0
    )
    set_review(
        db_session,
        record_type=SubmissionRecordType.thermo,
        record_id=t_approved.id,
        status=RecordReviewStatus.approved,
    )
    set_review(
        db_session,
        record_type=SubmissionRecordType.thermo,
        record_id=t_under.id,
        status=RecordReviewStatus.under_review,
    )

    response = get_species_thermo(
        db_session,
        species_entry_id=entry.id,
        request=ThermoReadRequest(min_review_status=RecordReviewStatus.approved),
    )
    assert [r.thermo_id for r in response.records] == [t_approved.id]


# ---------------------------------------------------------------------------
# Evidence completeness
# ---------------------------------------------------------------------------


def test_evidence_completeness_returns_score_and_checklist(db_session):
    entry = _entry_with_smiles(db_session)
    make_thermo_scalar(db_session, species_entry=entry)

    response = get_species_thermo(
        db_session, species_entry_id=entry.id, request=ThermoReadRequest()
    )
    breakdown = response.records[0].evidence_completeness
    expected = {
        "has_source_calculations",
        "has_statmech_source",
        "has_frequency_evidence",
        "has_sp_or_energy_evidence",
        "has_temperature_dependent_model",
        "has_uncertainty",
        "has_geometry_validation",
        "has_scf_stability",
    }
    assert set(breakdown.checklist.keys()) == expected
    assert breakdown.max == 8


def test_evidence_completeness_temperature_model_predicate_true_for_nasa(db_session):
    entry = _entry_with_smiles(db_session)
    thermo = make_thermo_scalar(db_session, species_entry=entry)
    attach_thermo_nasa(db_session, thermo=thermo)

    response = get_species_thermo(
        db_session, species_entry_id=entry.id, request=ThermoReadRequest()
    )
    cl = response.records[0].evidence_completeness.checklist
    assert cl["has_temperature_dependent_model"] is True


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_client_supplied_sort_rejected(db_session):
    entry = _entry_with_smiles(db_session)
    with pytest.raises(ValueError, match="client_sort_not_supported"):
        get_species_thermo(
            db_session,
            species_entry_id=entry.id,
            request=ThermoReadRequest(sort="anything"),
        )


def test_unknown_include_token_rejected(db_session):
    entry = _entry_with_smiles(db_session)
    with pytest.raises(ValueError, match="unknown_include_token"):
        get_species_thermo(
            db_session,
            species_entry_id=entry.id,
            request=ThermoReadRequest(include=["banana"]),
        )


def test_temperature_min_greater_than_max_rejected(db_session):
    entry = _entry_with_smiles(db_session)
    with pytest.raises(ValueError, match="invalid_temperature_range"):
        get_species_thermo(
            db_session,
            species_entry_id=entry.id,
            request=ThermoReadRequest(temperature_min=2000.0, temperature_max=300.0),
        )


def test_sort_is_deterministic(db_session):
    entry = _entry_with_smiles(db_session)
    make_thermo_scalar(db_session, species_entry=entry)
    make_thermo_scalar(db_session, species_entry=entry, h298_kj_mol=-99.0)

    r1 = get_species_thermo(
        db_session, species_entry_id=entry.id, request=ThermoReadRequest()
    )
    r2 = get_species_thermo(
        db_session, species_entry_id=entry.id, request=ThermoReadRequest()
    )
    assert r1.model_dump() == r2.model_dump()


# ---------------------------------------------------------------------------
# Phase 2 audit: statmech-source-calculation fallback for thermo provenance
# (see docs/audits/thermo_provenance_geometry_audit.md).
#
# When a thermo's own ThermoSourceCalculation rows do not cover the freq /
# SP / opt roles, the read service falls back to the picked statmech's
# StatmechSourceCalculation rows so that ``provenance.freq_calculation_ref``,
# ``sp_calculation_ref``, ``primary_calculation``, ``level_of_theory``, and
# ``software`` populate from real, persisted data — matching the actual
# computed-thermo derivation path.
# ---------------------------------------------------------------------------


def _seed_thermo_with_statmech_sources(db_session, *, with_thermo_sources: bool):
    """Build a species_entry + thermo + statmech with freq + SP statmech
    source-calculation rows. When ``with_thermo_sources`` is True, also
    persist ThermoSourceCalculation rows on the thermo so we can verify
    that explicit thermo sources take precedence over the statmech
    fallback.
    """
    from app.db.models.common import (
        CalculationType,
        ScientificOriginKind,
        StatmechCalculationRole,
        ThermoCalculationRole,
    )
    from app.db.models.statmech import Statmech, StatmechSourceCalculation
    from app.db.models.thermo import ThermoSourceCalculation
    from tests.services.scientific_read._factories import (
        make_calculation,
        make_lot,
    )

    entry = _entry_with_smiles(db_session, smiles="C#CCNCN")
    thermo = make_thermo_scalar(db_session, species_entry=entry)
    attach_thermo_nasa(db_session, thermo=thermo)

    lot = make_lot(db_session, method="wb97xd", basis="def2tzvp")
    freq_calc = make_calculation(
        db_session,
        type=CalculationType.freq,
        species_entry_id=entry.id,
        lot_id=lot.id,
    )
    sp_calc = make_calculation(
        db_session,
        type=CalculationType.sp,
        species_entry_id=entry.id,
        lot_id=lot.id,
    )

    statmech = Statmech(
        species_entry_id=entry.id,
        scientific_origin=ScientificOriginKind.computed,
    )
    db_session.add(statmech)
    db_session.flush()
    db_session.add(
        StatmechSourceCalculation(
            statmech_id=statmech.id,
            calculation_id=freq_calc.id,
            role=StatmechCalculationRole.freq,
        )
    )
    db_session.add(
        StatmechSourceCalculation(
            statmech_id=statmech.id,
            calculation_id=sp_calc.id,
            role=StatmechCalculationRole.sp,
        )
    )

    if with_thermo_sources:
        # An explicit thermo-source row that should win over the statmech
        # fallback. Tag the link with the ``composite`` thermo role so
        # the primary-calc picker (sp → composite → freq → opt) returns
        # this calc rather than the statmech-derived SP. The underlying
        # CalculationType doesn't have to match the role label.
        composite_calc = make_calculation(
            db_session,
            type=CalculationType.sp,
            species_entry_id=entry.id,
            lot_id=lot.id,
        )
        db_session.add(
            ThermoSourceCalculation(
                thermo_id=thermo.id,
                calculation_id=composite_calc.id,
                role=ThermoCalculationRole.composite,
            )
        )
        db_session.flush()
        return entry, thermo, freq_calc, sp_calc, composite_calc, lot
    db_session.flush()
    return entry, thermo, freq_calc, sp_calc, None, lot


def test_provenance_falls_back_to_statmech_freq_sp_when_thermo_sources_empty(
    db_session,
):
    entry, _thermo, freq_calc, sp_calc, _none, _lot = (
        _seed_thermo_with_statmech_sources(db_session, with_thermo_sources=False)
    )

    response = get_species_thermo(
        db_session, species_entry_id=entry.id, request=ThermoReadRequest()
    )
    record = response.records[0]
    prov = record.provenance

    # Statmech-linked freq/SP calcs now surface in the thermo provenance.
    assert prov.freq_calculation_ref == freq_calc.public_ref
    assert prov.sp_calculation_ref == sp_calc.public_ref
    # statmech_ref still points at the picked statmech.
    assert prov.statmech_ref is not None


def test_provenance_falls_back_to_statmech_lot_and_primary_calc(db_session):
    entry, _thermo, _freq, sp_calc, _none, lot = (
        _seed_thermo_with_statmech_sources(db_session, with_thermo_sources=False)
    )

    response = get_species_thermo(
        db_session, species_entry_id=entry.id, request=ThermoReadRequest()
    )
    prov = response.records[0].provenance

    # primary_calculation falls back to the SP per role priority.
    assert prov.primary_calculation is not None
    assert prov.primary_calculation.calculation_ref == sp_calc.public_ref
    # LoT and software summaries now project the primary calc's metadata.
    assert prov.level_of_theory is not None
    assert prov.level_of_theory.level_of_theory_ref == lot.public_ref


def test_thermo_source_calcs_take_precedence_over_statmech_fallback(db_session):
    entry, _thermo, _freq, _sp_stat, composite_calc, _lot = (
        _seed_thermo_with_statmech_sources(db_session, with_thermo_sources=True)
    )

    response = get_species_thermo(
        db_session, species_entry_id=entry.id, request=ThermoReadRequest()
    )
    prov = response.records[0].provenance

    # The explicit thermo composite source wins over the statmech-based
    # primary picker. The composite role has higher priority than freq
    # for the primary calc.
    assert prov.primary_calculation is not None
    assert (
        prov.primary_calculation.calculation_ref == composite_calc.public_ref
    )


def test_evidence_completeness_counts_statmech_freq_sp_when_thermo_sources_empty(
    db_session,
):
    entry, _thermo, _freq, _sp, _none, _lot = (
        _seed_thermo_with_statmech_sources(db_session, with_thermo_sources=False)
    )

    response = get_species_thermo(
        db_session, species_entry_id=entry.id, request=ThermoReadRequest()
    )
    checklist = response.records[0].evidence_completeness.checklist

    assert checklist["has_statmech_source"] is True
    # Phase 2 audit: these used to be False because the predicates only
    # looked at ThermoSourceCalculation. They now OR-in the picked
    # statmech's source roles.
    assert checklist["has_source_calculations"] is True
    assert checklist["has_frequency_evidence"] is True
    assert checklist["has_sp_or_energy_evidence"] is True


def test_collapse_first_named_policy_selects_explicitly(db_session):
    """collapse=first with an explicit named selection_policy picks one
    candidate by that policy; collapse=all is unaffected. 'default' ranks
    by the standard thermo order (review status wins here); 'latest' picks
    the most recent regardless of review status.
    """
    from app.db.models.common import RecordReviewStatus, SubmissionRecordType
    from app.schemas.reads.scientific_common import CollapseMode, SelectionPolicy

    entry = _entry_with_smiles(db_session)
    older_approved = make_thermo_scalar(db_session, species_entry=entry)
    newer = make_thermo_scalar(db_session, species_entry=entry)
    set_review(
        db_session,
        record_type=SubmissionRecordType.thermo,
        record_id=older_approved.id,
        status=RecordReviewStatus.approved,
    )

    # collapse=all returns both candidates (non-canonical default).
    all_resp = get_species_thermo(
        db_session, species_entry_id=entry.id, request=ThermoReadRequest()
    )
    assert len(all_resp.records) == 2
    assert all_resp.request.selection_policy == SelectionPolicy.default

    # default policy → review status wins → the approved (older) record.
    default_resp = get_species_thermo(
        db_session,
        species_entry_id=entry.id,
        request=ThermoReadRequest(collapse=CollapseMode.first),
    )
    assert len(default_resp.records) == 1
    assert default_resp.records[0].thermo_id == older_approved.id

    # latest policy → newest record regardless of review status.
    latest_resp = get_species_thermo(
        db_session,
        species_entry_id=entry.id,
        request=ThermoReadRequest(
            collapse=CollapseMode.first,
            selection_policy=SelectionPolicy.latest,
        ),
    )
    assert len(latest_resp.records) == 1
    assert latest_resp.records[0].thermo_id == newer.id
    assert latest_resp.request.selection_policy == SelectionPolicy.latest


def test_statmech_fallback_pick_is_deterministic_with_multiple_statmech(db_session):
    """Multiple coexisting statmech records on one species_entry are equal
    candidates. The thermo provenance fallback must pick deterministically
    (lowest statmech id) rather than depend on set-iteration order, so the
    read never silently treats an arbitrary candidate as canonical and the
    same response is reproducible across calls.

    Regression for the product-selection audit: ``get_species_thermo`` and
    ``_build_provenance`` previously both used ``next(iter(set))``, which could
    surface one statmech's ref while borrowing a different statmech's source
    calcs, non-deterministically.
    """
    from app.db.models.common import (
        CalculationType,
        ScientificOriginKind,
        StatmechCalculationRole,
    )
    from app.db.models.statmech import Statmech, StatmechSourceCalculation
    from tests.services.scientific_read._factories import (
        make_calculation,
        make_lot,
    )

    entry = _entry_with_smiles(db_session, smiles="C#CCNCNC")
    # No ThermoSourceCalculation rows → provenance falls back to a statmech.
    make_thermo_scalar(db_session, species_entry=entry)
    lot = make_lot(db_session, method="wb97xd", basis="def2tzvp")

    def _add_statmech_with_freq() -> tuple[Statmech, object]:
        freq_calc = make_calculation(
            db_session,
            type=CalculationType.freq,
            species_entry_id=entry.id,
            lot_id=lot.id,
        )
        statmech = Statmech(
            species_entry_id=entry.id,
            scientific_origin=ScientificOriginKind.computed,
        )
        db_session.add(statmech)
        db_session.flush()
        db_session.add(
            StatmechSourceCalculation(
                statmech_id=statmech.id,
                calculation_id=freq_calc.id,
                role=StatmechCalculationRole.freq,
            )
        )
        db_session.flush()
        return statmech, freq_calc

    first_stat, first_freq = _add_statmech_with_freq()
    second_stat, _second_freq = _add_statmech_with_freq()
    assert first_stat.id < second_stat.id

    # Call twice — the pick must be stable, not order-dependent.
    for _ in range(2):
        prov = (
            get_species_thermo(
                db_session, species_entry_id=entry.id, request=ThermoReadRequest()
            )
            .records[0]
            .provenance
        )
        # Lowest-id statmech is surfaced AND supplies the borrowed freq calc.
        assert prov.statmech_ref == first_stat.public_ref
        assert prov.freq_calculation_ref == first_freq.public_ref


# ---------------------------------------------------------------------------
# Audit finding #3b (read half): per-record statmech attribution.
#
# The read path must attribute a computed thermo's statmech basis from the
# record's OWN ``thermo.statmech_id`` FK (populated by the write fix), not a
# per-entry ``min(statmech_ids)`` fallback. The min is retained only as the
# fallback for records whose ``statmech_id`` is NULL.
# ---------------------------------------------------------------------------


def _add_statmech_with_freq_sp(db_session, entry, lot):
    """Persist one Statmech on ``entry`` with a distinct freq + SP source
    calc, and return ``(statmech, freq_calc, sp_calc)``.
    """
    from app.db.models.common import (
        CalculationType,
        ScientificOriginKind,
        StatmechCalculationRole,
    )
    from app.db.models.statmech import Statmech, StatmechSourceCalculation
    from tests.services.scientific_read._factories import make_calculation

    freq_calc = make_calculation(
        db_session,
        type=CalculationType.freq,
        species_entry_id=entry.id,
        lot_id=lot.id,
    )
    sp_calc = make_calculation(
        db_session,
        type=CalculationType.sp,
        species_entry_id=entry.id,
        lot_id=lot.id,
    )
    statmech = Statmech(
        species_entry_id=entry.id,
        scientific_origin=ScientificOriginKind.computed,
    )
    db_session.add(statmech)
    db_session.flush()
    db_session.add(
        StatmechSourceCalculation(
            statmech_id=statmech.id,
            calculation_id=freq_calc.id,
            role=StatmechCalculationRole.freq,
        )
    )
    db_session.add(
        StatmechSourceCalculation(
            statmech_id=statmech.id,
            calculation_id=sp_calc.id,
            role=StatmechCalculationRole.sp,
        )
    )
    db_session.flush()
    return statmech, freq_calc, sp_calc


def test_provenance_uses_record_statmech_id_not_entry_min(db_session):
    """A computed thermo linked to the HIGHER-id statmech B (via its own
    ``statmech_id`` FK) must surface B's ref and borrow B's freq/SP calcs —
    not statmech A (=min). This is the mis-attribution fix.
    """
    from tests.services.scientific_read._factories import make_lot

    entry = _entry_with_smiles(db_session, smiles="C#CCNCNCC")
    lot = make_lot(db_session, method="wb97xd", basis="def2tzvp")

    stat_a, freq_a, sp_a = _add_statmech_with_freq_sp(db_session, entry, lot)
    stat_b, freq_b, sp_b = _add_statmech_with_freq_sp(db_session, entry, lot)
    assert stat_a.id < stat_b.id  # A is the min

    # Thermo derives from B (the higher id), with no thermo-source rows.
    thermo = make_thermo_scalar(db_session, species_entry=entry)
    thermo.statmech_id = stat_b.id
    db_session.flush()

    prov = (
        get_species_thermo(
            db_session, species_entry_id=entry.id, request=ThermoReadRequest()
        )
        .records[0]
        .provenance
    )

    # Surfaces B's ref, NOT A/min.
    assert prov.statmech_ref == stat_b.public_ref
    assert prov.statmech_ref != stat_a.public_ref
    # Borrows B's source calcs, NOT A's.
    assert prov.freq_calculation_ref == freq_b.public_ref
    assert prov.sp_calculation_ref == sp_b.public_ref
    assert prov.freq_calculation_ref != freq_a.public_ref
    assert prov.sp_calculation_ref != sp_a.public_ref


def test_provenance_null_statmech_id_falls_back_to_entry_min(db_session):
    """A thermo with ``statmech_id`` NULL on an entry with statmech A + B
    still falls back to the entry-min (A) — unchanged legacy behavior.
    """
    from tests.services.scientific_read._factories import make_lot

    entry = _entry_with_smiles(db_session, smiles="C#CCNCNCCC")
    lot = make_lot(db_session, method="wb97xd", basis="def2tzvp")

    stat_a, freq_a, _sp_a = _add_statmech_with_freq_sp(db_session, entry, lot)
    stat_b, _freq_b, _sp_b = _add_statmech_with_freq_sp(db_session, entry, lot)
    assert stat_a.id < stat_b.id

    # No statmech_id on the thermo → NULL → fall back to min (A).
    thermo = make_thermo_scalar(db_session, species_entry=entry)
    assert thermo.statmech_id is None

    prov = (
        get_species_thermo(
            db_session, species_entry_id=entry.id, request=ThermoReadRequest()
        )
        .records[0]
        .provenance
    )

    assert prov.statmech_ref == stat_a.public_ref
    assert prov.freq_calculation_ref == freq_a.public_ref


def test_provenance_single_statmech_linked_surfaces_it(db_session):
    """An entry with a single statmech and a thermo linked to it surfaces
    that statmech and borrows its source calcs (behavior unchanged).
    """
    from tests.services.scientific_read._factories import make_lot

    entry = _entry_with_smiles(db_session, smiles="C#CCNCNCCCC")
    lot = make_lot(db_session, method="wb97xd", basis="def2tzvp")

    stat, freq_calc, sp_calc = _add_statmech_with_freq_sp(db_session, entry, lot)

    thermo = make_thermo_scalar(db_session, species_entry=entry)
    thermo.statmech_id = stat.id
    db_session.flush()

    prov = (
        get_species_thermo(
            db_session, species_entry_id=entry.id, request=ThermoReadRequest()
        )
        .records[0]
        .provenance
    )

    assert prov.statmech_ref == stat.public_ref
    assert prov.freq_calculation_ref == freq_calc.public_ref
    assert prov.sp_calculation_ref == sp_calc.public_ref


# ---------------------------------------------------------------------------
# Temperature coverage for nasa9 / wilhoit records (child-derived range)
# ---------------------------------------------------------------------------


def test_nasa9_coverage_uses_interval_span_when_row_bounds_null(db_session):
    """A nasa9-only record with NULL row-level tmin/tmax derives its coverage
    range from the NASA-9 interval span, not the NULL row bounds.

    The factory intervals span 200-6000 K. So the record COVERS a requested
    1000 K (inside the span) and does NOT cover 8000 K (above the 6000 K max).
    Under the old row-level-only logic the NULL bounds would make coverage
    False for every requested temperature.
    """
    entry = _entry_with_smiles(db_session)
    thermo = make_thermo_scalar(
        db_session, species_entry=entry, tmin_k=None, tmax_k=None
    )
    attach_thermo_nasa9(db_session, thermo=thermo)

    covered = get_species_thermo(
        db_session,
        species_entry_id=entry.id,
        request=ThermoReadRequest(temperature_min=1000.0, temperature_max=1000.0),
    )
    assert covered.records[0].model_kind == ThermoModelKindQuery.nasa9
    assert covered.records[0].temperature_coverage is not None
    assert covered.records[0].temperature_coverage.covers_requested_range is True

    uncovered = get_species_thermo(
        db_session,
        species_entry_id=entry.id,
        request=ThermoReadRequest(temperature_min=8000.0, temperature_max=8000.0),
    )
    assert uncovered.records[0].temperature_coverage is not None
    assert uncovered.records[0].temperature_coverage.covers_requested_range is False


def test_wilhoit_coverage_falls_through_to_row_level_bounds(db_session):
    """ThermoWilhoit carries no temperature-bound columns of its own, so a
    wilhoit record's coverage range comes from the row-level thermo.tmin_k /
    tmax_k fall-through branch. Here the 300-2500 K row bounds cover 1000 K
    and do not cover 4000 K.
    """
    entry = _entry_with_smiles(db_session)
    thermo = make_thermo_scalar(
        db_session, species_entry=entry, tmin_k=300.0, tmax_k=2500.0
    )
    attach_thermo_wilhoit(db_session, thermo=thermo)

    covered = get_species_thermo(
        db_session,
        species_entry_id=entry.id,
        request=ThermoReadRequest(temperature_min=1000.0, temperature_max=1000.0),
    )
    assert covered.records[0].model_kind == ThermoModelKindQuery.wilhoit
    assert covered.records[0].temperature_coverage is not None
    assert covered.records[0].temperature_coverage.covers_requested_range is True

    uncovered = get_species_thermo(
        db_session,
        species_entry_id=entry.id,
        request=ThermoReadRequest(temperature_min=4000.0, temperature_max=4000.0),
    )
    assert uncovered.records[0].temperature_coverage is not None
    assert uncovered.records[0].temperature_coverage.covers_requested_range is False
