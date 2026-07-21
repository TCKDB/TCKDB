"""Service-layer tests for get_reaction_kinetics."""

from __future__ import annotations

import pytest

from app.api.errors import NotFoundError
from app.db.models.common import (
    KineticsDegeneracyConvention,
    KineticsModelKind,
    PressureContext,
    RecordReviewStatus,
    ScientificOriginKind,
    SubmissionRecordType,
)
from app.schemas.reads.scientific_common import CollapseMode
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


def test_collapse_first_applies_before_offset(db_session):
    entry = _setup_entry(db_session)
    make_kinetics(db_session, reaction_entry=entry)

    response = get_reaction_kinetics(
        db_session,
        reaction_entry_id=entry.id,
        request=KineticsReadRequest(collapse=CollapseMode.first, offset=1),
    )

    assert response.records == []
    assert response.pagination.total == 1
    assert response.pagination.returned == 0


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


# ---------------------------------------------------------------------------
# DR-0036: direction, sum-of-Arrhenius (multi_arrhenius), network bridge
# ---------------------------------------------------------------------------


def test_forward_and_reverse_directions_returned_distinctly(db_session):
    from app.db.models.common import ArrheniusAUnits, KineticsDirection
    from tests.services.scientific_read._factories import make_kinetics as _mk

    entry = _setup_entry(db_session)
    _mk(
        db_session,
        reaction_entry=entry,
        a=1.0e-11,
        a_units=ArrheniusAUnits.cm3_molecule_s,
        direction=KineticsDirection.forward,
    )
    _mk(
        db_session,
        reaction_entry=entry,
        a=2.0e-13,
        a_units=ArrheniusAUnits.cm3_molecule_s,
        direction=KineticsDirection.reverse,
    )

    response = get_reaction_kinetics(
        db_session, reaction_entry_id=entry.id, request=KineticsReadRequest()
    )
    directions = {r.direction for r in response.records}
    assert directions == {KineticsDirection.forward, KineticsDirection.reverse}
    assert len(response.records) == 2


def test_multi_arrhenius_terms_read_back(db_session):
    from app.db.models.common import ArrheniusAUnits, KineticsModelKind
    from tests.services.scientific_read._factories import (
        attach_kinetics_arrhenius_entry,
    )
    from tests.services.scientific_read._factories import (
        make_kinetics as _mk,
    )

    entry = _setup_entry(db_session)
    k = _mk(
        db_session,
        reaction_entry=entry,
        model_kind=KineticsModelKind.multi_arrhenius,
        a=None,  # scalar A stays unset — the terms live in child rows
        n=None,
        ea_kj_mol=None,
    )
    attach_kinetics_arrhenius_entry(
        db_session, kinetics=k, entry_index=1, a=1.0e12, n=0.0, ea_kj_mol=50.0,
        a_units=ArrheniusAUnits.cm3_mol_s,
    )
    attach_kinetics_arrhenius_entry(
        db_session, kinetics=k, entry_index=2, a=3.0e11, n=0.5, ea_kj_mol=60.0,
        a_units=ArrheniusAUnits.cm3_mol_s,
    )

    response = get_reaction_kinetics(
        db_session, reaction_entry_id=entry.id, request=KineticsReadRequest()
    )
    rec = response.records[0]
    assert rec.model_kind == KineticsModelKind.multi_arrhenius
    assert rec.parameters.A is None  # scalar block empty for a DUPLICATE sum
    assert rec.multi_arrhenius is not None
    assert [t.entry_index for t in rec.multi_arrhenius] == [1, 2]
    assert [t.A for t in rec.multi_arrhenius] == [1.0e12, 3.0e11]
    assert [t.Ea_kj_mol for t in rec.multi_arrhenius] == [50.0, 60.0]


def test_single_arrhenius_has_no_multi_block(db_session):
    entry = _setup_entry(db_session)
    make_kinetics(db_session, reaction_entry=entry)
    response = get_reaction_kinetics(
        db_session, reaction_entry_id=entry.id, request=KineticsReadRequest()
    )
    assert response.records[0].multi_arrhenius is None


def _make_network_kinetics_row(db_session, entry):
    """Build a minimal network_kinetics row to bridge to."""
    from app.db.models.common import (
        NetworkChannelKind,
        NetworkKineticsModelKind,
        NetworkStateKind,
    )
    from tests.services.scientific_read._factories import (
        make_network,
        make_network_channel,
        make_network_kinetics,
        make_network_solve,
        make_network_state,
    )

    net = make_network(db_session)
    src = make_network_state(
        db_session, network=net, kind=NetworkStateKind.well,
        composition_hash="a" * 64,
    )
    sink = make_network_state(
        db_session, network=net, kind=NetworkStateKind.bimolecular,
        composition_hash="b" * 64,
    )
    channel = make_network_channel(
        db_session, network=net, source_state=src, sink_state=sink,
        kind=NetworkChannelKind.dissociation,
    )
    solve = make_network_solve(db_session, network=net)
    return make_network_kinetics(
        db_session, channel=channel, solve=solve,
        model_kind=NetworkKineticsModelKind.plog,
    )


def test_network_bridge_ref_resolves_in_read(db_session):
    entry = _setup_entry(db_session)
    nk = _make_network_kinetics_row(db_session, entry)
    make_kinetics(
        db_session, reaction_entry=entry, network_kinetics_id=nk.id
    )

    response = get_reaction_kinetics(
        db_session, reaction_entry_id=entry.id, request=KineticsReadRequest()
    )
    prov = response.records[0].provenance
    assert prov.network_kinetics_id == nk.id
    assert prov.network_kinetics_ref == nk.public_ref


def test_no_network_bridge_is_null(db_session):
    entry = _setup_entry(db_session)
    make_kinetics(db_session, reaction_entry=entry)
    response = get_reaction_kinetics(
        db_session, reaction_entry_id=entry.id, request=KineticsReadRequest()
    )
    prov = response.records[0].provenance
    assert prov.network_kinetics_id is None
    assert prov.network_kinetics_ref is None


# ---------------------------------------------------------------------------
# DR-0032: PLOG / Chebyshev / falloff / third-body read exposure
# ---------------------------------------------------------------------------


def test_plog_kinetics_surfaces_pressure_entries(db_session):
    from app.db.models.common import ArrheniusAUnits
    from tests.services.scientific_read._factories import (
        attach_kinetics_plog_entry,
    )

    entry = _setup_entry(db_session)
    # A standalone PLOG rate: scalar Arrhenius stays unset; k(T,P) lives in
    # the per-pressure entries.
    k = make_kinetics(
        db_session,
        reaction_entry=entry,
        model_kind=KineticsModelKind.plog,
        a=None,
        n=None,
        ea_kj_mol=None,
    )
    attach_kinetics_plog_entry(
        db_session, kinetics=k, entry_index=1, pressure_bar=0.1,
        a=1.0e11, n=0.0, ea_kj_mol=30.0, a_units=ArrheniusAUnits.cm3_mol_s,
    )
    attach_kinetics_plog_entry(
        db_session, kinetics=k, entry_index=2, pressure_bar=1.0,
        a=2.5e12, n=0.4, ea_kj_mol=42.0, a_units=ArrheniusAUnits.cm3_mol_s,
    )

    response = get_reaction_kinetics(
        db_session, reaction_entry_id=entry.id, request=KineticsReadRequest()
    )
    rec = response.records[0]
    assert rec.model_kind == KineticsModelKind.plog
    assert rec.plog_entries is not None
    assert [e.entry_index for e in rec.plog_entries] == [1, 2]
    assert [e.pressure_bar for e in rec.plog_entries] == [0.1, 1.0]
    assert [e.A for e in rec.plog_entries] == [1.0e11, 2.5e12]
    assert [e.n for e in rec.plog_entries] == [0.0, 0.4]
    assert [e.Ea_kj_mol for e in rec.plog_entries] == [30.0, 42.0]
    # Other pdep blocks stay None.
    assert rec.chebyshev is None
    assert rec.falloff is None
    assert rec.third_body_efficiencies is None


def test_chebyshev_kinetics_surfaces_matrix_and_bounds(db_session):
    from tests.services.scientific_read._factories import (
        attach_kinetics_chebyshev,
    )

    entry = _setup_entry(db_session)
    coeffs = [[8.2, 0.5, -0.1], [0.3, 0.02, 0.001]]
    k = make_kinetics(
        db_session,
        reaction_entry=entry,
        model_kind=KineticsModelKind.chebyshev,
        a=None,
        n=None,
        ea_kj_mol=None,
    )
    attach_kinetics_chebyshev(
        db_session, kinetics=k, n_temperature=2, n_pressure=3,
        coefficients=coeffs, tmin_k=300.0, tmax_k=2000.0,
        pmin_bar=0.01, pmax_bar=100.0,
    )

    response = get_reaction_kinetics(
        db_session, reaction_entry_id=entry.id, request=KineticsReadRequest()
    )
    rec = response.records[0]
    assert rec.model_kind == KineticsModelKind.chebyshev
    cb = rec.chebyshev
    assert cb is not None
    assert cb.n_temperature == 2
    assert cb.n_pressure == 3
    assert cb.tmin_k == 300.0
    assert cb.tmax_k == 2000.0
    assert cb.pmin_bar == 0.01
    assert cb.pmax_bar == 100.0
    assert cb.coefficients == coeffs
    assert not hasattr(cb, "stores_log10_k")
    assert rec.plog_entries is None
    assert rec.falloff is None


def test_troe_falloff_kinetics_surfaces_low_p_and_efficiencies(db_session):
    from app.db.models.common import ArrheniusAUnits
    from tests.services.scientific_read._factories import (
        attach_kinetics_falloff,
        attach_kinetics_third_body_efficiency,
        make_species,
    )

    entry = _setup_entry(db_session)
    # High-pressure limit lives on the scalar Arrhenius; falloff carries k0.
    k = make_kinetics(
        db_session,
        reaction_entry=entry,
        model_kind=KineticsModelKind.troe,
        a=1.0e13,
        a_units=ArrheniusAUnits.cm3_mol_s,
        n=0.0,
        ea_kj_mol=0.0,
    )
    attach_kinetics_falloff(
        db_session, kinetics=k, low_a=1.0e18,
        low_a_units=ArrheniusAUnits.cm6_mol2_s, low_n=-1.0, low_ea_kj_mol=0.0,
        troe_alpha=0.5, troe_t3=100.0, troe_t1=1000.0, troe_t2=2000.0,
    )
    collider = make_species(db_session, smiles="O", inchi_key=next_inchi_key("KW"))
    attach_kinetics_third_body_efficiency(
        db_session, kinetics=k, collider_species=collider, efficiency=6.0
    )

    response = get_reaction_kinetics(
        db_session, reaction_entry_id=entry.id, request=KineticsReadRequest()
    )
    rec = response.records[0]
    fo = rec.falloff
    assert fo is not None
    assert fo.kind == KineticsModelKind.troe
    assert fo.low_A == 1.0e18
    assert fo.low_A_units == ArrheniusAUnits.cm6_mol2_s
    assert fo.low_n == -1.0
    assert fo.low_Ea_kj_mol == 0.0
    assert fo.troe_alpha == 0.5
    assert fo.troe_t3 == 100.0
    assert fo.troe_t1 == 1000.0
    assert fo.troe_t2 == 2000.0
    assert fo.sri_a is None
    # High-pressure Arrhenius still on the top-level parameters block.
    assert rec.parameters.A == 1.0e13
    tbe = rec.third_body_efficiencies
    assert tbe is not None
    assert len(tbe) == 1
    assert tbe[0].collider_ref == collider.public_ref
    assert tbe[0].efficiency == 6.0


def test_simple_third_body_flag_and_efficiencies(db_session):
    from app.db.models.common import ArrheniusAUnits
    from tests.services.scientific_read._factories import (
        attach_kinetics_third_body_efficiency,
        make_species,
    )

    entry = _setup_entry(db_session)
    k = make_kinetics(
        db_session,
        reaction_entry=entry,
        model_kind=KineticsModelKind.modified_arrhenius,
        a=1.0e15,
        a_units=ArrheniusAUnits.cm6_mol2_s,
    )
    k.is_third_body = True
    db_session.flush()
    ar = make_species(db_session, smiles="[Ar]", inchi_key=next_inchi_key("KAr"))
    attach_kinetics_third_body_efficiency(
        db_session, kinetics=k, collider_species=ar, efficiency=0.7
    )

    response = get_reaction_kinetics(
        db_session, reaction_entry_id=entry.id, request=KineticsReadRequest()
    )
    rec = response.records[0]
    assert rec.is_third_body is True
    assert rec.third_body_efficiencies is not None
    assert rec.third_body_efficiencies[0].collider_ref == ar.public_ref
    assert rec.third_body_efficiencies[0].efficiency == 0.7
    assert rec.falloff is None
    assert rec.plog_entries is None


def test_third_body_efficiencies_order_is_deterministic_by_collider_ref(db_session):
    from app.db.models.common import ArrheniusAUnits
    from tests.services.scientific_read._factories import (
        attach_kinetics_third_body_efficiency,
        make_species,
    )

    entry = _setup_entry(db_session)
    k = make_kinetics(
        db_session,
        reaction_entry=entry,
        model_kind=KineticsModelKind.modified_arrhenius,
        a=1.0e15,
        a_units=ArrheniusAUnits.cm6_mol2_s,
    )
    k.is_third_body = True
    db_session.flush()

    # Attach several colliders; insertion order is intentionally not sorted.
    c_water = make_species(db_session, smiles="O", inchi_key=next_inchi_key("KTW"))
    c_ar = make_species(db_session, smiles="[Ar]", inchi_key=next_inchi_key("KTA"))
    c_co2 = make_species(db_session, smiles="O=C=O", inchi_key=next_inchi_key("KTC"))
    attach_kinetics_third_body_efficiency(
        db_session, kinetics=k, collider_species=c_water, efficiency=6.0
    )
    attach_kinetics_third_body_efficiency(
        db_session, kinetics=k, collider_species=c_ar, efficiency=0.7
    )
    attach_kinetics_third_body_efficiency(
        db_session, kinetics=k, collider_species=c_co2, efficiency=2.0
    )

    response = get_reaction_kinetics(
        db_session, reaction_entry_id=entry.id, request=KineticsReadRequest()
    )
    tbe = response.records[0].third_body_efficiencies
    assert tbe is not None
    refs = [b.collider_ref for b in tbe]
    # Order is stable and sorted by collider_ref regardless of insertion order.
    assert refs == sorted(refs)
    assert set(refs) == {c_water.public_ref, c_ar.public_ref, c_co2.public_ref}


def test_plain_modified_arrhenius_has_null_pdep_blocks(db_session):
    entry = _setup_entry(db_session)
    make_kinetics(db_session, reaction_entry=entry)
    response = get_reaction_kinetics(
        db_session, reaction_entry_id=entry.id, request=KineticsReadRequest()
    )
    rec = response.records[0]
    # Regression: scalar Arrhenius still populated.
    assert rec.parameters.A == 1.2e-12
    assert rec.parameters.n == 2.1
    assert rec.parameters.Ea_kj_mol == 15.4
    # New pdep blocks all absent for a plain rate.
    assert rec.is_third_body is False
    assert rec.pressure_context is None
    assert rec.pressure_bar is None
    assert rec.plog_entries is None
    assert rec.chebyshev is None
    assert rec.falloff is None
    assert rec.third_body_efficiencies is None


def test_pressure_bar_filters_by_model_applicability_and_reports_coverage(db_session):
    from tests.services.scientific_read._factories import (
        attach_kinetics_chebyshev,
        attach_kinetics_plog_entry,
    )

    entry = _setup_entry(db_session)
    independent = make_kinetics(db_session, reaction_entry=entry)
    make_kinetics(
        db_session,
        reaction_entry=entry,
        pressure_context=PressureContext.high_p_limit,
    )
    exact = make_kinetics(
        db_session,
        reaction_entry=entry,
        pressure_context=PressureContext.apparent_at_pressure,
        pressure_bar=1.0,
    )
    make_kinetics(
        db_session,
        reaction_entry=entry,
        pressure_context=PressureContext.apparent_at_pressure,
        pressure_bar=10.0,
    )
    plog = make_kinetics(
        db_session,
        reaction_entry=entry,
        model_kind=KineticsModelKind.plog,
        a=None,
        n=None,
        ea_kj_mol=None,
    )
    attach_kinetics_plog_entry(
        db_session, kinetics=plog, entry_index=1, pressure_bar=0.1, a=1.0e10
    )
    attach_kinetics_plog_entry(
        db_session, kinetics=plog, entry_index=2, pressure_bar=10.0, a=1.0e12
    )
    chebyshev = make_kinetics(
        db_session,
        reaction_entry=entry,
        model_kind=KineticsModelKind.chebyshev,
        a=None,
        n=None,
        ea_kj_mol=None,
    )
    attach_kinetics_chebyshev(
        db_session,
        kinetics=chebyshev,
        n_temperature=1,
        n_pressure=1,
        coefficients=[[1.0]],
        pmin_bar=2.0,
        pmax_bar=20.0,
    )

    response = get_reaction_kinetics(
        db_session,
        reaction_entry_id=entry.id,
        request=KineticsReadRequest(pressure_bar=1.0),
    )

    by_id = {record.kinetics_id: record for record in response.records}
    assert set(by_id) == {independent.id, exact.id, plog.id}
    assert by_id[independent.id].pressure_coverage.basis == "pressure_independent"
    assert by_id[exact.id].pressure_coverage.basis == "exact_pressure"
    assert by_id[plog.id].pressure_coverage.basis == "bounded_pressure_surface"
    assert response.request.filter == {"pressure_bar": 1.0}


def test_deprecated_pressure_alias_is_canonicalized_and_conflicts_rejected():
    request = KineticsReadRequest(pressure=1.0)
    assert request.pressure_bar == 1.0

    with pytest.raises(ValueError, match="pressure_alias_conflict"):
        KineticsReadRequest(pressure_bar=1.0, pressure=10.0)


@pytest.mark.parametrize(
    ("stored_convention", "includes", "apply"),
    [
        (KineticsDegeneracyConvention.already_applied, True, False),
        (KineticsDegeneracyConvention.not_applied, False, True),
        (KineticsDegeneracyConvention.unknown, None, None),
    ],
)
def test_reaction_path_degeneracy_reports_explicit_convention(
    db_session, stored_convention, includes, apply
):
    entry = _setup_entry(db_session)
    kinetics = make_kinetics(db_session, reaction_entry=entry, degeneracy=2.0)
    kinetics.degeneracy_convention = stored_convention
    db_session.flush()

    response = get_reaction_kinetics(
        db_session, reaction_entry_id=entry.id, request=KineticsReadRequest()
    )
    convention = response.records[0].reaction_path_degeneracy
    assert convention is not None
    assert convention.value == 2.0
    assert convention.convention is stored_convention
    assert convention.reported_rate_coefficient_includes_degeneracy is includes
    assert convention.apply_to_rate_coefficient is apply
