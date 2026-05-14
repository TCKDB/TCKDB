"""API tests for ``GET /api/v1/scientific/calculations/{calculation_ref_or_id}``.

First-slice coverage: default response shape, owner branches, review
badge, internal-id policy, available_sections, and include validation
(including the ``include_not_implemented_yet`` guard for heavy tokens).
"""

from __future__ import annotations

from app.db.models.calculation import (
    CalculationArtifact,
    CalculationConstraint,
    CalculationDependency,
    CalculationParameter,
)
from app.db.models.common import (
    ArtifactKind,
    CalculationDependencyRole,
    CalculationGeometryRole,
    CalculationQuality,
    CalculationType,
    ConstraintKind,
    ParameterSource,
    RecordReviewStatus,
    SCFStabilityStatus,
    SubmissionRecordType,
    TransitionStateEntryStatus,
    ValidationStatus,
)
from app.db.models.reaction import ChemReaction, ReactionEntry
from app.db.models.transition_state import TransitionState, TransitionStateEntry
from tests.services.scientific_read._factories import (
    attach_artifact,
    attach_geometry_validation,
    attach_opt_result,
    attach_output_geometry,
    attach_scf_stability,
    attach_sp_result,
    make_calculation,
    make_geometry,
    make_lot,
    make_species,
    make_species_entry,
    next_inchi_key,
    set_review,
)


# ---------------------------------------------------------------------------
# Local fixture helpers
# ---------------------------------------------------------------------------


def _make_ts_entry(db_session) -> TransitionStateEntry:
    """Build a ChemReaction → ReactionEntry → TransitionState → entry chain.

    The factories module doesn't expose a TS-entry builder; this helper
    is local to the tests.
    """
    rxn = ChemReaction(reversible=True)
    db_session.add(rxn)
    db_session.flush()

    rxe = ReactionEntry(reaction_id=rxn.id)
    db_session.add(rxe)
    db_session.flush()

    ts = TransitionState(reaction_entry_id=rxe.id, label="ts1")
    db_session.add(ts)
    db_session.flush()

    tse = TransitionStateEntry(
        transition_state_id=ts.id,
        charge=0,
        multiplicity=2,
        unmapped_smiles="[CH2]",
        status=TransitionStateEntryStatus.optimized,
    )
    db_session.add(tse)
    db_session.flush()
    return tse


def _make_species_owned_calc(db_session, **kwargs):
    species = make_species(
        db_session, smiles="CCO", inchi_key=next_inchi_key("CALC")
    )
    entry = make_species_entry(db_session, species)
    calc = make_calculation(
        db_session,
        type=kwargs.get("calc_type", CalculationType.opt),
        species_entry_id=entry.id,
        lot_id=kwargs.get("lot_id"),
    )
    return species, entry, calc


def _make_ts_owned_calc(db_session, **kwargs):
    tse = _make_ts_entry(db_session)
    calc = make_calculation(
        db_session,
        type=kwargs.get("calc_type", CalculationType.sp),
        transition_state_entry_id=tse.id,
        lot_id=kwargs.get("lot_id"),
    )
    return tse, calc


# ---------------------------------------------------------------------------
# Happy path + path-handle inputs
# ---------------------------------------------------------------------------


def test_detail_by_calculation_ref_returns_record(client, db_session):
    _, entry, calc = _make_species_owned_calc(db_session)
    resp = client.get(f"/api/v1/scientific/calculations/{calc.public_ref}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["record"]["calculation"]["calculation_ref"] == calc.public_ref
    assert body["record"]["calculation"]["type"] == "opt"
    assert body["record"]["calculation"]["quality"] == "raw"
    # Phase D default: integer calculation_id stripped.
    assert "calculation_id" not in body["record"]["calculation"]


def test_detail_by_integer_id_still_works(client, db_session):
    _, entry, calc = _make_species_owned_calc(db_session)
    resp = client.get(f"/api/v1/scientific/calculations/{calc.id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Integer-path input is honored; response identifies the row by ref.
    assert body["record"]["calculation"]["calculation_ref"] == calc.public_ref


def test_detail_default_response_carries_summaries_and_review(
    client, db_session
):
    species, entry, calc = _make_species_owned_calc(
        db_session, lot_id=make_lot(db_session).id
    )
    set_review(
        db_session,
        record_type=SubmissionRecordType.calculation,
        record_id=calc.id,
        status=RecordReviewStatus.approved,
    )

    resp = client.get(f"/api/v1/scientific/calculations/{calc.public_ref}")
    body = resp.json()

    assert body["request"]["include"] == []
    assert body["review_summary"]["approved"] == 1
    assert body["review_summary"]["total"] == 1

    record = body["record"]
    assert record["calculation"]["review"]["status"] == "approved"
    assert record["calculation"]["review"]["reviewer_kind"] == "human"

    assert record["owner"]["kind"] == "species_entry"
    assert record["owner"]["transition_state_entry"] is None

    assert record["level_of_theory"]["method"] == "wb97xd"
    assert record["level_of_theory"]["basis"] == "def2tzvp"
    assert "level_of_theory_ref" in record["level_of_theory"]

    # Software / workflow / literature default to null when the calc
    # doesn't reference them.
    assert record["software_release"] is None
    assert record["workflow_tool_release"] is None
    assert record["literature"] is None

    assert "available_sections" in record
    # No result row yet → has_result is false in provenance.
    assert record["provenance"]["has_result"] is False
    assert record["provenance"]["geometry_validation_status"] == "not_present"
    assert record["provenance"]["scf_stability_status"] == "not_present"
    # Phase D default: integer submission_id is stripped; submission_ref
    # remains visible (null when there is no submission link).
    assert "submission_id" not in record["provenance"]
    assert record["provenance"]["submission_ref"] is None


# ---------------------------------------------------------------------------
# Owner branches
# ---------------------------------------------------------------------------


def test_detail_species_owned_calculation_owner_block(client, db_session):
    species, entry, calc = _make_species_owned_calc(db_session)
    resp = client.get(f"/api/v1/scientific/calculations/{calc.public_ref}")
    owner = resp.json()["record"]["owner"]
    assert owner["kind"] == "species_entry"
    assert owner["transition_state_entry"] is None
    se = owner["species_entry"]
    assert se["species_ref"] == species.public_ref
    assert se["species_entry_ref"] == entry.public_ref
    assert se["canonical_smiles"] == "CCO"
    # Species.inchi_key is stored as CHAR(27); the synthetic test key
    # is shorter than 27 chars, so the column comes back trailing-padded.
    # Real InChI keys are exactly 27 chars and avoid this. Compare on
    # the trimmed values so the test isn't sensitive to that padding.
    assert se["inchi_key"].strip() == species.inchi_key.strip()
    assert se["charge"] == 0
    assert se["multiplicity"] == 1
    assert se["species_entry_kind"] == "minimum"
    assert se["electronic_state_kind"] == "ground"
    # Phase D default: ids stripped from the owner block.
    assert "species_id" not in se
    assert "species_entry_id" not in se


def test_detail_transition_state_owned_calculation_owner_block(
    client, db_session
):
    tse, calc = _make_ts_owned_calc(db_session)
    resp = client.get(f"/api/v1/scientific/calculations/{calc.public_ref}")
    owner = resp.json()["record"]["owner"]
    assert owner["kind"] == "transition_state_entry"
    assert owner["species_entry"] is None
    ts = owner["transition_state_entry"]
    assert ts["transition_state_entry_ref"] == tse.public_ref
    assert ts["transition_state_ref"] == tse.transition_state.public_ref
    assert ts["label"] == "ts1"
    assert ts["charge"] == 0
    assert ts["multiplicity"] == 2
    assert ts["status"] == "optimized"
    assert "transition_state_id" not in ts
    assert "transition_state_entry_id" not in ts
    # Reaction-entry pointers should be present.
    assert "reaction_entry_ref" in ts
    assert ts["reaction_entry_ref"].startswith("rxe_")


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_detail_unknown_ref_returns_404(client, db_session):
    resp = client.get(
        "/api/v1/scientific/calculations/calc_neverexistsabcdefxyzqr"
    )
    assert resp.status_code == 404
    assert "calculation not found" in resp.text


def test_detail_unknown_integer_id_returns_404(client, db_session):
    # Use a deliberately-large id that no fixture creates.
    resp = client.get("/api/v1/scientific/calculations/9999999999")
    assert resp.status_code == 404
    # Generic detail; the integer id must not be echoed in the body.
    assert "9999999999" not in resp.text


def test_detail_wrong_prefix_ref_returns_422(client, db_session):
    resp = client.get(
        "/api/v1/scientific/calculations/spe_abcdef0123456789"
    )
    assert resp.status_code == 422
    assert "handle_type_mismatch" in resp.text


def test_detail_malformed_handle_returns_422(client, db_session):
    resp = client.get("/api/v1/scientific/calculations/not-a-handle")
    assert resp.status_code == 422


def test_detail_unknown_include_token_returns_422(client, db_session):
    _, _, calc = _make_species_owned_calc(db_session)
    resp = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}?include=banana"
    )
    assert resp.status_code == 422
    assert "unknown_include_token" in resp.text


# ---------------------------------------------------------------------------
# Phase D internal-ID visibility
# ---------------------------------------------------------------------------


def test_detail_default_omits_calculation_id(client, db_session):
    _, _, calc = _make_species_owned_calc(db_session)
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
    ).json()
    assert "calculation_id" not in body["record"]["calculation"]
    assert body["request"]["include"] == []


def test_detail_internal_ids_silently_dropped_when_disallowed(
    client, db_session
):
    _, _, calc = _make_species_owned_calc(db_session)
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=internal_ids"
    ).json()
    assert "internal_ids" not in body["request"]["include"]
    assert "calculation_id" not in body["record"]["calculation"]


def test_detail_internal_ids_restored_when_allowed(
    client, db_session, allow_internal_ids
):
    species, entry, calc = _make_species_owned_calc(db_session)
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=internal_ids"
    ).json()
    assert "internal_ids" in body["request"]["include"]
    assert body["record"]["calculation"]["calculation_id"] == calc.id
    se = body["record"]["owner"]["species_entry"]
    assert se["species_id"] == species.id
    assert se["species_entry_id"] == entry.id


def test_detail_include_all_does_not_restore_ids(client, db_session):
    _, _, calc = _make_species_owned_calc(db_session)
    # ``all`` is rejected here because every legal token under it is a
    # heavy not-yet-implemented section. The request still must not
    # restore internal ids: ``all`` never expands to ``internal_ids``.
    resp = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}?include=all"
    )
    assert resp.status_code == 422
    assert "include_not_implemented_yet" in resp.text


# ---------------------------------------------------------------------------
# Heavy-include guard (first-slice only ships the default response)
# ---------------------------------------------------------------------------


def test_detail_heavy_include_results_returns_422(client, db_session):
    _, _, calc = _make_species_owned_calc(db_session)
    resp = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}?include=results"
    )
    assert resp.status_code == 422
    assert "include_not_implemented_yet" in resp.text


def test_detail_heavy_include_dependencies_returns_422(client, db_session):
    _, _, calc = _make_species_owned_calc(db_session)
    resp = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=dependencies"
    )
    assert resp.status_code == 422
    assert "include_not_implemented_yet" in resp.text


# ---------------------------------------------------------------------------
# available_sections + provenance summary
# ---------------------------------------------------------------------------


def test_available_sections_all_false_for_bare_calculation(
    client, db_session
):
    _, _, calc = _make_species_owned_calc(db_session)
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
    ).json()
    sections = body["record"]["available_sections"]
    assert sections == {
        "has_results": False,
        "has_dependencies": False,
        "has_parameters": False,
        "has_constraints": False,
        "has_artifacts": False,
        "has_input_geometries": False,
        "has_output_geometries": False,
        "has_geometry_validation": False,
        "has_scf_stability": False,
        "has_scan": False,
        "has_irc": False,
        "has_path_search": False,
    }


def test_available_sections_reflect_attached_children(client, db_session):
    """An opt calc with output geometry, validation, scf stability,
    artifact, parameter, constraint, and a parent dependency should
    surface every matching boolean as True (and ``has_result`` true
    because the opt result row exists)."""
    _, entry, calc = _make_species_owned_calc(
        db_session, calc_type=CalculationType.opt
    )
    geom = make_geometry(db_session, natoms=3)
    attach_opt_result(db_session, calculation=calc, final_energy_hartree=-1.5)
    attach_output_geometry(
        db_session,
        calculation=calc,
        geometry=geom,
        role=CalculationGeometryRole.final,
    )
    attach_geometry_validation(
        db_session, calculation=calc, status=ValidationStatus.passed
    )
    attach_scf_stability(
        db_session, calculation=calc, status=SCFStabilityStatus.stable
    )
    attach_artifact(
        db_session, calculation=calc, kind=ArtifactKind.output_log
    )

    db_session.add(
        CalculationParameter(
            calculation_id=calc.id,
            raw_key="scf_convergence",
            raw_value="1e-8",
            source=ParameterSource.upload,
        )
    )
    db_session.add(
        CalculationConstraint(
            calculation_id=calc.id,
            constraint_index=1,
            constraint_kind=ConstraintKind.bond,
            atom1_index=1,
            atom2_index=2,
        )
    )
    parent_calc = make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry.id
    )
    db_session.add(
        CalculationDependency(
            parent_calculation_id=parent_calc.id,
            child_calculation_id=calc.id,
            dependency_role=CalculationDependencyRole.optimized_from,
        )
    )
    db_session.flush()

    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
    ).json()
    sections = body["record"]["available_sections"]
    provenance = body["record"]["provenance"]

    assert sections["has_results"] is True
    assert sections["has_dependencies"] is True
    assert sections["has_parameters"] is True
    assert sections["has_constraints"] is True
    assert sections["has_artifacts"] is True
    assert sections["has_output_geometries"] is True
    assert sections["has_geometry_validation"] is True
    assert sections["has_scf_stability"] is True
    # No input geometry / scan / irc / path_search attached.
    assert sections["has_input_geometries"] is False
    assert sections["has_scan"] is False
    assert sections["has_irc"] is False
    assert sections["has_path_search"] is False

    assert provenance["has_result"] is True
    assert provenance["converged"] is True
    assert provenance["geometry_validation_status"] == "passed"
    assert provenance["scf_stability_status"] == "stable"


def test_provenance_converged_null_for_sp_calc_without_opt(client, db_session):
    species, entry, calc = _make_species_owned_calc(
        db_session, calc_type=CalculationType.sp
    )
    attach_sp_result(
        db_session, calculation=calc, electronic_energy_hartree=-1.0
    )
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
    ).json()
    provenance = body["record"]["provenance"]
    assert provenance["has_result"] is True
    # SP calcs don't carry a convergence flag in our model.
    assert provenance["converged"] is None


# ---------------------------------------------------------------------------
# Review status visibility
# ---------------------------------------------------------------------------


def test_detail_returns_rejected_calculation(client, db_session):
    """Detail reads must surface the record with its trust state visible
    even when it is rejected; default-trust filtering only applies to
    list/search endpoints."""
    _, _, calc = _make_species_owned_calc(db_session)
    set_review(
        db_session,
        record_type=SubmissionRecordType.calculation,
        record_id=calc.id,
        status=RecordReviewStatus.rejected,
    )
    resp = client.get(f"/api/v1/scientific/calculations/{calc.public_ref}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["record"]["calculation"]["review"]["status"] == "rejected"
    assert body["review_summary"]["rejected"] == 1
    assert body["review_summary"]["total"] == 1
