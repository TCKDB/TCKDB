"""Service-layer tests for search_species_calculations (Phase 7)."""

from __future__ import annotations

import pytest

from app.api.error_contract import CodedValueError
from app.api.errors import NotFoundError
from app.db.models.common import (
    ArtifactKind,
    CalculationDependencyRole,
    CalculationGeometryRole,
    CalculationQuality,
    CalculationType,
    ConformerSelectionKind,
    RecordReviewStatus,
    SCFStabilityStatus,
    SubmissionRecordType,
    ValidationStatus,
)
from app.schemas.reads.scientific_common import CollapseMode
from app.schemas.reads.scientific_species_calculations import (
    CalculationRanking,
    SpeciesCalculationsSearchRequest,
)
from app.services.scientific_read.species_calculations_search import (
    search_species_calculations,
)
from tests.services.scientific_read._factories import (
    attach_artifact,
    attach_conformer_selection,
    attach_dependency,
    attach_geometry_validation,
    attach_opt_result,
    attach_output_geometry,
    attach_scf_stability,
    attach_sp_result,
    make_calculation,
    make_calculation_with_conformer,
    make_conformer_group,
    make_conformer_observation,
    make_geometry,
    make_lot,
    make_species,
    make_species_entry,
    next_inchi_key,
    set_review,
)


def _entry(db_session, *, smiles: str = "CC"):
    species = make_species(db_session, smiles=smiles, inchi_key=next_inchi_key("SC"))
    return species, make_species_entry(db_session, species)


# ---------------------------------------------------------------------------
# Identity resolution
# ---------------------------------------------------------------------------


def test_search_by_smiles_returns_calculations(db_session):
    species, entry = _entry(db_session, smiles="C[CH2]")
    calc = make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry.id
    )

    response = search_species_calculations(
        db_session,
        SpeciesCalculationsSearchRequest(smiles="C[CH2]"),
    )

    assert len(response.records) == 1
    rec = response.records[0]
    assert rec.species.species_id == species.id
    assert rec.species.species_entry_id == entry.id
    assert rec.calculation.calculation_id == calc.id
    assert rec.calculation.calculation_type == CalculationType.sp


def test_search_by_species_entry_id_handle(db_session):
    _, entry = _entry(db_session, smiles="EH")
    make_calculation(
        db_session, type=CalculationType.opt, species_entry_id=entry.id
    )

    response = search_species_calculations(
        db_session,
        SpeciesCalculationsSearchRequest(species_entry_id=entry.id),
    )

    assert len(response.records) == 1
    assert response.records[0].species.species_entry_id == entry.id


def test_explicit_entry_ref_does_not_bypass_unsupported_inchi(db_session):
    _, entry = _entry(db_session, smiles="EH_INCHI")

    with pytest.raises(CodedValueError) as exc_info:
        search_species_calculations(
            db_session,
            SpeciesCalculationsSearchRequest(
                species_entry_ref=entry.public_ref,
                inchi="InChI=1S/CH4/h1H4",
            ),
        )

    assert exc_info.value.code == "unsupported_filter"
    assert exc_info.value.context == {
        "endpoint": "/scientific/species-calculations/search",
        "filters": ["inchi"],
    }


def test_unknown_species_entry_id_handle_raises_404(db_session):
    with pytest.raises(NotFoundError, match="species_entry not found"):
        search_species_calculations(
            db_session,
            SpeciesCalculationsSearchRequest(species_entry_id=999_999),
        )


def test_unknown_chemistry_returns_empty(db_session):
    response = search_species_calculations(
        db_session,
        SpeciesCalculationsSearchRequest(smiles="DOES_NOT_EXIST"),
    )
    assert response.records == []
    assert response.pagination.total == 0


def test_no_identifier_raises_422(db_session):
    with pytest.raises(ValueError, match="missing_identifier"):
        search_species_calculations(
            db_session, SpeciesCalculationsSearchRequest()
        )


# ---------------------------------------------------------------------------
# Calculation type + LoT/software filtering
# ---------------------------------------------------------------------------


def test_calculation_type_sp_returns_only_sp(db_session):
    _, entry = _entry(db_session, smiles="CT")
    sp = make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry.id
    )
    make_calculation(
        db_session, type=CalculationType.opt, species_entry_id=entry.id
    )

    response = search_species_calculations(
        db_session,
        SpeciesCalculationsSearchRequest(
            smiles="CT", calculation_type=CalculationType.sp
        ),
    )
    assert {r.calculation.calculation_id for r in response.records} == {sp.id}


def test_level_of_theory_id_filters_calculation_lot_directly(db_session):
    _, entry = _entry(db_session, smiles="LOT1")
    lot1 = make_lot(db_session, method="wb97xd", basis="def2tzvp")
    lot2 = make_lot(db_session, method="b3lyp", basis="6-31g")
    in_lot1 = make_calculation(
        db_session,
        type=CalculationType.sp,
        species_entry_id=entry.id,
        lot_id=lot1.id,
    )
    make_calculation(
        db_session,
        type=CalculationType.sp,
        species_entry_id=entry.id,
        lot_id=lot2.id,
    )

    response = search_species_calculations(
        db_session,
        SpeciesCalculationsSearchRequest(
            smiles="LOT1", level_of_theory_id=lot1.id
        ),
    )
    assert {r.calculation.calculation_id for r in response.records} == {in_lot1.id}


def test_method_basis_filter_via_calculation_lot(db_session):
    _, entry = _entry(db_session, smiles="MB1")
    lot_a = make_lot(db_session, method="wb97xd", basis="def2tzvp")
    lot_b = make_lot(db_session, method="b3lyp", basis="6-31g")
    in_a = make_calculation(
        db_session,
        type=CalculationType.sp,
        species_entry_id=entry.id,
        lot_id=lot_a.id,
    )
    make_calculation(
        db_session,
        type=CalculationType.sp,
        species_entry_id=entry.id,
        lot_id=lot_b.id,
    )

    response = search_species_calculations(
        db_session,
        SpeciesCalculationsSearchRequest(
            smiles="MB1", method="wb97xd", basis="def2tzvp"
        ),
    )
    assert {r.calculation.calculation_id for r in response.records} == {in_a.id}


# ---------------------------------------------------------------------------
# Ranking semantics
# ---------------------------------------------------------------------------


def test_lowest_energy_ranking_for_sp_orders_by_electronic_energy_asc(db_session):
    _, entry = _entry(db_session, smiles="LE1")
    lot = make_lot(db_session)
    high = make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry.id, lot_id=lot.id
    )
    attach_sp_result(db_session, calculation=high, electronic_energy_hartree=-100.0)
    low = make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry.id, lot_id=lot.id
    )
    attach_sp_result(db_session, calculation=low, electronic_energy_hartree=-200.0)

    response = search_species_calculations(
        db_session,
        SpeciesCalculationsSearchRequest(
            species_entry_ref=entry.public_ref,
            level_of_theory_ref=lot.public_ref,
            calculation_type=CalculationType.sp,
            ranking=CalculationRanking.lowest_energy,
        ),
    )
    ordered = [r.calculation.calculation_id for r in response.records]
    assert ordered.index(low.id) < ordered.index(high.id)


def test_lowest_energy_ranking_for_opt_orders_by_final_energy_asc(db_session):
    _, entry = _entry(db_session, smiles="LE2")
    lot = make_lot(db_session)
    high = make_calculation(
        db_session, type=CalculationType.opt, species_entry_id=entry.id, lot_id=lot.id
    )
    attach_opt_result(db_session, calculation=high, final_energy_hartree=-50.0)
    low = make_calculation(
        db_session, type=CalculationType.opt, species_entry_id=entry.id, lot_id=lot.id
    )
    attach_opt_result(db_session, calculation=low, final_energy_hartree=-77.0)

    response = search_species_calculations(
        db_session,
        SpeciesCalculationsSearchRequest(
            species_entry_ref=entry.public_ref,
            level_of_theory_ref=lot.public_ref,
            calculation_type=CalculationType.opt,
            ranking=CalculationRanking.lowest_energy,
        ),
    )
    ordered = [r.calculation.calculation_id for r in response.records]
    assert ordered.index(low.id) < ordered.index(high.id)


def test_lowest_energy_ranking_with_freq_returns_422(db_session):
    with pytest.raises(
        ValueError, match="unsupported_ranking_for_calculation_type"
    ):
        search_species_calculations(
            db_session,
            SpeciesCalculationsSearchRequest(
                smiles="X",
                calculation_type=CalculationType.freq,
                ranking=CalculationRanking.lowest_energy,
            ),
        )


def test_lowest_energy_ranking_without_calculation_type_returns_422(db_session):
    with pytest.raises(
        ValueError, match="unsupported_ranking_for_calculation_type"
    ):
        search_species_calculations(
            db_session,
            SpeciesCalculationsSearchRequest(
                smiles="X", ranking=CalculationRanking.lowest_energy
            ),
        )


def test_lowest_energy_requires_exact_comparability_refs(db_session):
    with pytest.raises(ValueError, match="unsafe_lowest_energy_comparison"):
        search_species_calculations(
            db_session,
            SpeciesCalculationsSearchRequest(
                smiles="X",
                calculation_type=CalculationType.sp,
                ranking=CalculationRanking.lowest_energy,
            ),
        )


def test_ranking_latest_orders_by_created_at_desc(db_session):
    _, entry = _entry(db_session, smiles="LAT")
    older = make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry.id
    )
    newer = make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry.id
    )

    response = search_species_calculations(
        db_session,
        SpeciesCalculationsSearchRequest(
            smiles="LAT", ranking=CalculationRanking.latest
        ),
    )
    ordered = [r.calculation.calculation_id for r in response.records]
    assert ordered.index(newer.id) < ordered.index(older.id)


def test_ranking_earliest_orders_by_created_at_asc(db_session):
    _, entry = _entry(db_session, smiles="EAR")
    older = make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry.id
    )
    newer = make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry.id
    )

    response = search_species_calculations(
        db_session,
        SpeciesCalculationsSearchRequest(
            smiles="EAR", ranking=CalculationRanking.earliest
        ),
    )
    ordered = [r.calculation.calculation_id for r in response.records]
    assert ordered.index(older.id) < ordered.index(newer.id)


def test_lowest_energy_nulls_last(db_session):
    """A calc with null energy must rank below a calc with populated energy."""
    _, entry = _entry(db_session, smiles="NL")
    lot = make_lot(db_session)
    with_energy = make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry.id, lot_id=lot.id
    )
    attach_sp_result(
        db_session, calculation=with_energy, electronic_energy_hartree=-100.0
    )
    without_energy = make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry.id, lot_id=lot.id
    )
    # No SP result row attached → energy is null.

    response = search_species_calculations(
        db_session,
        SpeciesCalculationsSearchRequest(
            species_entry_ref=entry.public_ref,
            level_of_theory_ref=lot.public_ref,
            calculation_type=CalculationType.sp,
            ranking=CalculationRanking.lowest_energy,
        ),
    )
    ordered = [r.calculation.calculation_id for r in response.records]
    assert ordered.index(with_energy.id) < ordered.index(without_energy.id)


# ---------------------------------------------------------------------------
# Collapse + pagination
# ---------------------------------------------------------------------------


def test_collapse_first_preserves_plural_records_with_pre_collapse_total(db_session):
    _, entry = _entry(db_session, smiles="CO")
    make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry.id
    )
    make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry.id
    )

    response = search_species_calculations(
        db_session,
        SpeciesCalculationsSearchRequest(
            smiles="CO", collapse=CollapseMode.first
        ),
    )

    assert len(response.records) == 1
    assert response.pagination.total == 2
    assert response.pagination.returned == 1


def test_collapse_first_applies_offset_after_collapse(db_session):
    _, entry = _entry(db_session, smiles="[GeH4]")
    make_calculation(db_session, type=CalculationType.sp, species_entry_id=entry.id)
    make_calculation(db_session, type=CalculationType.sp, species_entry_id=entry.id)

    response = search_species_calculations(
        db_session,
        SpeciesCalculationsSearchRequest(
            smiles="[GeH4]",
            collapse=CollapseMode.first,
            offset=1,
        ),
    )

    assert response.records == []
    assert response.pagination.total == 2
    assert response.pagination.returned == 0


# ---------------------------------------------------------------------------
# Conformer context
# ---------------------------------------------------------------------------


def test_conformer_block_populated_when_observation_set(db_session):
    _, entry = _entry(db_session, smiles="CN1")
    group = make_conformer_group(db_session, entry, label="gauche")
    obs = make_conformer_observation(
        db_session,
        conformer_group=group,
        torsion_fingerprint_json={"hash": "abc"},
    )
    attach_conformer_selection(
        db_session,
        conformer_group=group,
        selection_kind=ConformerSelectionKind.lowest_energy,
    )
    make_calculation_with_conformer(
        db_session,
        species_entry=entry,
        conformer_observation=obs,
        type=CalculationType.sp,
    )

    response = search_species_calculations(
        db_session,
        SpeciesCalculationsSearchRequest(smiles="CN1"),
    )
    rec = response.records[0]
    assert rec.conformer is not None
    assert rec.conformer.conformer_observation_id == obs.id
    assert rec.conformer.conformer_group_id == group.id
    assert rec.conformer.conformer_group_label == "gauche"
    assert rec.conformer.torsion_fingerprint_json == {"present": True}
    assert ConformerSelectionKind.lowest_energy in rec.conformer.selection_kinds


def test_conformer_block_null_when_no_observation(db_session):
    _, entry = _entry(db_session, smiles="CN2")
    make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry.id
    )

    response = search_species_calculations(
        db_session,
        SpeciesCalculationsSearchRequest(smiles="CN2"),
    )
    assert response.records[0].conformer is None


# ---------------------------------------------------------------------------
# Geometry behavior
# ---------------------------------------------------------------------------


def test_geometry_ids_returned_by_default_xyz_omitted(db_session):
    _, entry = _entry(db_session, smiles="GE1")
    calc = make_calculation(
        db_session, type=CalculationType.opt, species_entry_id=entry.id
    )
    geom = make_geometry(db_session, natoms=3, xyz_text="H 0 0 0\nH 0 0 1\nH 0 0 2")
    attach_output_geometry(
        db_session,
        calculation=calc,
        geometry=geom,
        role=CalculationGeometryRole.final,
    )

    response = search_species_calculations(
        db_session,
        SpeciesCalculationsSearchRequest(smiles="GE1"),
    )
    geometry = response.records[0].geometry
    assert geometry.primary_output_geometry_id == geom.id
    assert geometry.primary_output_geometry_role == CalculationGeometryRole.final
    assert geom.id in geometry.output_geometry_ids
    # Geometry block has IDs only — no xyz_text leakage.
    dump = response.records[0].model_dump()
    assert "xyz_text" not in str(dump["geometry"])


def test_primary_output_geometry_resolves_to_role_final(db_session):
    _, entry = _entry(db_session, smiles="GE2")
    calc = make_calculation(
        db_session, type=CalculationType.opt, species_entry_id=entry.id
    )
    initial = make_geometry(db_session, natoms=2)
    final = make_geometry(db_session, natoms=2)
    attach_output_geometry(
        db_session,
        calculation=calc,
        geometry=initial,
        role=CalculationGeometryRole.initial,
        output_order=1,
    )
    attach_output_geometry(
        db_session,
        calculation=calc,
        geometry=final,
        role=CalculationGeometryRole.final,
        output_order=2,
    )

    response = search_species_calculations(
        db_session,
        SpeciesCalculationsSearchRequest(smiles="GE2"),
    )
    geometry = response.records[0].geometry
    assert geometry.primary_output_geometry_id == final.id


# ---------------------------------------------------------------------------
# Validation / SCF / artifacts / dependencies
# ---------------------------------------------------------------------------


def test_validation_status_uses_real_enum_vocabulary(db_session):
    _, entry = _entry(db_session, smiles="V1")
    calc = make_calculation(
        db_session, type=CalculationType.opt, species_entry_id=entry.id
    )
    attach_geometry_validation(
        db_session, calculation=calc, status=ValidationStatus.warning
    )

    response = search_species_calculations(
        db_session,
        SpeciesCalculationsSearchRequest(smiles="V1"),
    )
    val = response.records[0].validation.geometry_validation
    assert val is not None
    assert val.status == "warning"


def test_scf_stability_status_uses_real_enum_vocabulary(db_session):
    _, entry = _entry(db_session, smiles="SCF1")
    calc = make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry.id
    )
    attach_scf_stability(
        db_session, calculation=calc, status=SCFStabilityStatus.stabilized
    )

    response = search_species_calculations(
        db_session,
        SpeciesCalculationsSearchRequest(smiles="SCF1"),
    )
    scf = response.records[0].validation.scf_stability
    assert scf is not None
    assert scf.status == "stabilized"


def test_artifacts_available_flag_true_when_artifact_exists(db_session):
    _, entry = _entry(db_session, smiles="AR1")
    calc = make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry.id
    )
    attach_artifact(db_session, calculation=calc, kind=ArtifactKind.output_log)

    response = search_species_calculations(
        db_session,
        SpeciesCalculationsSearchRequest(smiles="AR1"),
    )
    assert response.records[0].provenance.artifacts_available is True


def test_supporting_calculation_ids_lists_parent_dependencies(db_session):
    _, entry = _entry(db_session, smiles="DEP1")
    parent = make_calculation(
        db_session, type=CalculationType.opt, species_entry_id=entry.id
    )
    child = make_calculation(
        db_session, type=CalculationType.freq, species_entry_id=entry.id
    )
    attach_dependency(
        db_session,
        parent=parent,
        child=child,
        role=CalculationDependencyRole.freq_on,
    )

    response = search_species_calculations(
        db_session,
        SpeciesCalculationsSearchRequest(
            smiles="DEP1", calculation_type=CalculationType.freq
        ),
    )
    rec = next(
        r for r in response.records if r.calculation.calculation_id == child.id
    )
    assert parent.id in rec.provenance.supporting_calculation_ids


# ---------------------------------------------------------------------------
# Quality + review filtering
# ---------------------------------------------------------------------------


def test_calculation_quality_rejected_excluded_by_default(db_session):
    _, entry = _entry(db_session, smiles="Q1")
    raw = make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry.id
    )
    rejected = make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry.id
    )
    rejected.quality = CalculationQuality.rejected
    db_session.flush()

    response = search_species_calculations(
        db_session,
        SpeciesCalculationsSearchRequest(smiles="Q1"),
    )
    ids = {r.calculation.calculation_id for r in response.records}
    assert raw.id in ids
    assert rejected.id not in ids


def test_include_rejected_quality_opts_in(db_session):
    _, entry = _entry(db_session, smiles="Q2")
    rejected = make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry.id
    )
    rejected.quality = CalculationQuality.rejected
    db_session.flush()

    response = search_species_calculations(
        db_session,
        SpeciesCalculationsSearchRequest(
            smiles="Q2", include_rejected_quality=True
        ),
    )
    assert {r.calculation.calculation_id for r in response.records} == {rejected.id}


def test_min_review_status_filters_calculation_review(db_session):
    _, entry = _entry(db_session, smiles="MR1")
    approved = make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry.id
    )
    set_review(
        db_session,
        record_type=SubmissionRecordType.calculation,
        record_id=approved.id,
        status=RecordReviewStatus.approved,
    )
    under = make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry.id
    )
    set_review(
        db_session,
        record_type=SubmissionRecordType.calculation,
        record_id=under.id,
        status=RecordReviewStatus.under_review,
    )

    response = search_species_calculations(
        db_session,
        SpeciesCalculationsSearchRequest(
            smiles="MR1", min_review_status=RecordReviewStatus.approved
        ),
    )
    assert {r.calculation.calculation_id for r in response.records} == {approved.id}


def test_default_excludes_review_rejected_and_deprecated(db_session):
    _, entry = _entry(db_session, smiles="R1")
    rejected_calc = make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry.id
    )
    set_review(
        db_session,
        record_type=SubmissionRecordType.calculation,
        record_id=rejected_calc.id,
        status=RecordReviewStatus.rejected,
    )

    response = search_species_calculations(
        db_session,
        SpeciesCalculationsSearchRequest(smiles="R1"),
    )
    assert response.records == []


# ---------------------------------------------------------------------------
# Validation rejections
# ---------------------------------------------------------------------------


def test_client_supplied_sort_rejected(db_session):
    with pytest.raises(ValueError, match="client_sort_not_supported"):
        search_species_calculations(
            db_session,
            SpeciesCalculationsSearchRequest(smiles="X", sort="anything"),
        )


def test_unknown_include_token_rejected(db_session):
    with pytest.raises(ValueError, match="unknown_include_token"):
        search_species_calculations(
            db_session,
            SpeciesCalculationsSearchRequest(smiles="X", include=["banana"]),
        )


def test_known_but_illegal_include_token_rejected(db_session):
    """Tokens legal at other endpoints (e.g. 'kinetics') are still rejected here."""
    with pytest.raises(ValueError, match="unknown_include_token"):
        search_species_calculations(
            db_session,
            SpeciesCalculationsSearchRequest(smiles="X", include=["kinetics"]),
        )


def test_sort_is_deterministic_across_calls(db_session):
    _, entry = _entry(db_session, smiles="DET")
    make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry.id
    )
    r1 = search_species_calculations(
        db_session, SpeciesCalculationsSearchRequest(smiles="DET")
    )
    r2 = search_species_calculations(
        db_session, SpeciesCalculationsSearchRequest(smiles="DET")
    )
    assert r1.model_dump() == r2.model_dump()


def test_sp_calc_with_no_output_geometry_returns_empty_output_geometries(
    db_session,
):
    """Phase 2 audit (thermo/geometry): an SP calc without an explicit
    output geometry is **expected** to return ``output_geometries=[]``
    and ``primary_output_geometry_ref=None``.

    The upload layer only auto-attaches an output geometry for ``opt``
    (see ``_OUTPUT_GEOMETRY_TYPES = {opt}``). SP / freq / scan / irc /
    path_search require an explicit producer declaration. This test
    locks in the behavior so a future refactor doesn't accidentally
    fabricate an output geometry for SP.
    """
    _, entry = _entry(db_session, smiles="SPNO")
    sp = make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry.id
    )
    response = search_species_calculations(
        db_session, SpeciesCalculationsSearchRequest(smiles="SPNO")
    )
    matching = [
        r
        for r in response.records
        if r.calculation.calculation_ref == sp.public_ref
    ]
    assert len(matching) == 1
    geom = matching[0].geometry
    assert geom.output_geometries == []
    assert geom.primary_output_geometry_ref is None
    assert geom.primary_output_geometry_role is None
