"""API tests for ``GET /api/v1/scientific/calculations/{calculation_ref_or_id}``.

First-slice coverage: default response shape, owner branches, review
badge, internal-id policy, available_sections, and include validation
(including the ``include_not_implemented_yet`` guard for heavy tokens).
"""

from __future__ import annotations

from app.db.models.calculation import (
    CalculationConstraint,
    CalculationDependency,
    CalculationFreqResult,
    CalculationInputGeometry,
    CalculationIRCPoint,
    CalculationIRCResult,
    CalculationParameter,
    CalculationParameterVocab,
    CalculationPathSearchPoint,
    CalculationPathSearchResult,
    CalculationScanCoordinate,
    CalculationScanPoint,
    CalculationScanResult,
)
from app.db.models.common import (
    ArtifactKind,
    CalculationDependencyRole,
    CalculationGeometryRole,
    CalculationQuality,
    CalculationType,
    ConstraintKind,
    CoordinateUnit,
    IRCDirection,
    ParameterSource,
    PathSearchMethod,
    RecordReviewStatus,
    ScanCoordinateKind,
    SCFStabilityStatus,
    SubmissionRecordType,
    TransitionStateEntryStatus,
    ValidationStatus,
)
from app.db.models.reaction import ChemReaction, ReactionEntry
from app.db.models.software import Software, SoftwareRelease
from app.db.models.transition_state import TransitionState, TransitionStateEntry
from tests.services.scientific_read._factories import (
    attach_artifact,
    attach_dependency,
    attach_freq_result,
    attach_geometry_validation,
    attach_opt_result,
    attach_output_geometry,
    attach_scf_stability,
    attach_sp_result,
    attach_spin_diagnostic,
    make_calculation,
    make_geometry,
    make_lot,
    make_species,
    make_species_entry,
    next_inchi_key,
    set_review,
)

# All heavy include tokens have shipped a summary loader. The only
# include token still rejected is ``all``, which is policy-deferred
# until a separate PR explicitly enables it (see the
# ``include=all`` tests below). Per-implemented-include canary tests
# that previously combined an implemented token with a still-
# unimplemented one have been retired.


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
        db_session, inchi_key=next_inchi_key("CALC")
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


def _attach_trust_input_geometry(db_session, *, calculation):
    geometry = make_geometry(db_session)
    row = CalculationInputGeometry(
        calculation_id=calculation.id,
        geometry_id=geometry.id,
        input_order=1,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _attach_software_release(db_session, *, calculation):
    software = Software(name=f"trust-api-sw-{calculation.id}")
    db_session.add(software)
    db_session.flush()
    release = SoftwareRelease(software_id=software.id, version="1.0")
    db_session.add(release)
    db_session.flush()
    calculation.software_release_id = release.id
    db_session.flush()
    return release


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
    assert se["canonical_smiles"] == species.smiles
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
# include=trust
# ---------------------------------------------------------------------------


def test_detail_trust_omitted_when_not_requested(client, db_session):
    _, _, calc = _make_species_owned_calc(db_session)
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
    ).json()
    assert "trust" not in body["record"]


def test_detail_include_trust_returns_fragment(client, db_session):
    _, _, calc = _make_species_owned_calc(
        db_session, lot_id=make_lot(db_session).id
    )
    _attach_trust_input_geometry(db_session, calculation=calc)
    attach_opt_result(db_session, calculation=calc, final_energy_hartree=-10.0)
    set_review(
        db_session,
        record_type=SubmissionRecordType.calculation,
        record_id=calc.id,
        status=RecordReviewStatus.approved,
    )

    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}?include=trust"
    ).json()

    assert body["request"]["include"] == ["trust"]
    trust = body["record"]["trust"]
    assert trust["review_status"] == "approved"
    assert trust["trust_status"] in {
        "well_supported",
        "mostly_supported",
        "partial",
        "sparse",
        "unsupported",
        "hard_failed",
    }
    assert trust["llm_precheck"] == {
        "enabled": False,
        "label": "not_run",
        "summary": None,
    }
    evidence = trust["evidence"]
    assert evidence["record_type"] == "calculation"
    assert evidence["rubric"] == "computed_calculation_v1"
    assert evidence["rubric_version"] == 1
    assert "evidence_completeness" in evidence
    assert "passed_checks" in evidence
    assert "missing_checks" in evidence
    assert "warning_checks" in evidence
    assert "not_applicable_checks" in evidence
    # Phase D default internal-ID policy still applies inside trust evidence.
    assert "record_id" not in evidence


def test_detail_include_trust_uses_loaded_calculation_path(
    client, db_session, monkeypatch
):
    _, _, calc = _make_species_owned_calc(
        db_session, lot_id=make_lot(db_session).id
    )
    _attach_trust_input_geometry(db_session, calculation=calc)
    attach_opt_result(db_session, calculation=calc, final_energy_hartree=-10.0)

    def fail_session_id_entrypoint(*args, **kwargs):
        raise AssertionError("read trust path must use loaded calculation")

    monkeypatch.setattr(
        "app.services.trust.evaluator.evaluate_computed_calculation",
        fail_session_id_entrypoint,
    )

    resp = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}?include=trust"
    )
    assert resp.status_code == 200, resp.text
    evidence = resp.json()["record"]["trust"]["evidence"]
    assert evidence["record_type"] == "calculation"


def test_detail_include_trust_exposes_record_id_when_internal_ids_allowed(
    client, db_session, allow_internal_ids
):
    _, _, calc = _make_species_owned_calc(db_session)
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=trust,internal_ids"
    ).json()
    evidence = body["record"]["trust"]["evidence"]
    assert evidence["record_id"] == calc.id


def test_detail_include_trust_sparse_calculation_reports_missing_checks(
    client, db_session
):
    _, _, calc = _make_species_owned_calc(db_session)
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}?include=trust"
    ).json()
    evidence = body["record"]["trust"]["evidence"]

    assert evidence["label"] in {"sparse", "unsupported", "partial"}
    assert "level_of_theory_present" in evidence["missing_checks"]
    assert "input_geometry_present" in evidence["missing_checks"]
    assert "result_block_present" in evidence["missing_checks"]


def test_detail_include_trust_rich_calculation_scores_higher(
    client, db_session
):
    _, _, sparse = _make_species_owned_calc(db_session)
    _, _, rich = _make_species_owned_calc(
        db_session, lot_id=make_lot(db_session).id
    )
    rich.quality = CalculationQuality.curated
    _attach_software_release(db_session, calculation=rich)
    _attach_trust_input_geometry(db_session, calculation=rich)
    output_geometry = make_geometry(db_session)
    attach_output_geometry(
        db_session, calculation=rich, geometry=output_geometry
    )
    attach_opt_result(db_session, calculation=rich, final_energy_hartree=-10.0)
    attach_geometry_validation(
        db_session, calculation=rich, status=ValidationStatus.passed
    )
    attach_artifact(db_session, calculation=rich)
    db_session.add(
        CalculationParameter(
            calculation_id=rich.id,
            raw_key="opt",
            raw_value="tight",
            source=ParameterSource.parser,
        )
    )
    db_session.flush()

    sparse_body = client.get(
        f"/api/v1/scientific/calculations/{sparse.public_ref}?include=trust"
    ).json()
    rich_body = client.get(
        f"/api/v1/scientific/calculations/{rich.public_ref}?include=trust"
    ).json()

    sparse_score = sparse_body["record"]["trust"]["evidence"][
        "evidence_completeness"
    ]
    rich_score = rich_body["record"]["trust"]["evidence"][
        "evidence_completeness"
    ]
    assert rich_score > sparse_score


def test_detail_include_trust_rejected_calculation_hard_failed(
    client, db_session
):
    _, _, calc = _make_species_owned_calc(db_session)
    calc.quality = CalculationQuality.rejected
    db_session.flush()

    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}?include=trust"
    ).json()
    evidence = body["record"]["trust"]["evidence"]
    assert evidence["label"] == "hard_failed"
    assert evidence["hard_fail_reason"] == "calculation_rejected"


def test_detail_include_trust_geometry_validation_fail_hard_failed(
    client, db_session
):
    _, _, calc = _make_species_owned_calc(db_session)
    attach_geometry_validation(
        db_session, calculation=calc, status=ValidationStatus.fail
    )

    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}?include=trust"
    ).json()
    evidence = body["record"]["trust"]["evidence"]
    assert evidence["label"] == "hard_failed"
    assert evidence["hard_fail_reason"] == "geometry_validation_failed"


def test_detail_include_all_does_not_include_trust(client, db_session):
    _, _, calc = _make_species_owned_calc(db_session)
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}?include=all"
    ).json()
    assert "trust" not in body["request"]["include"]
    assert "trust" not in body["record"]


def test_detail_include_trust_does_not_mutate_calculation(client, db_session):
    _, _, calc = _make_species_owned_calc(db_session)
    before = (
        calc.type,
        calc.quality,
        calc.species_entry_id,
        calc.transition_state_entry_id,
        calc.lot_id,
        calc.software_release_id,
        calc.workflow_tool_release_id,
        calc.parameters_json,
    )

    resp = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}?include=trust"
    )
    assert resp.status_code == 200, resp.text
    db_session.refresh(calc)
    after = (
        calc.type,
        calc.quality,
        calc.species_entry_id,
        calc.transition_state_entry_id,
        calc.lot_id,
        calc.software_release_id,
        calc.workflow_tool_release_id,
        calc.parameters_json,
    )
    assert after == before


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
    """``include=all`` expands to every legal public token but
    deliberately excludes ``internal_ids``. Internal-ID stripping
    therefore stays in effect under the default policy."""
    _, _, calc = _make_species_owned_calc(db_session)
    resp = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}?include=all"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # ``all`` was resolved into the legal heavy tokens; ``internal_ids``
    # is intentionally absent from the resolved include set.
    assert "internal_ids" not in body["request"]["include"]
    # Phase D default: integer calculation_id stripped.
    assert "calculation_id" not in body["record"]["calculation"]


# ---------------------------------------------------------------------------
# Heavy-include guard (first-slice only ships the default response)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# include=results
# ---------------------------------------------------------------------------


def test_detail_results_omitted_when_not_requested(client, db_session):
    _, _, calc = _make_species_owned_calc(
        db_session, calc_type=CalculationType.opt
    )
    attach_opt_result(
        db_session, calculation=calc, final_energy_hartree=-1.0
    )
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
    ).json()
    # Field is dropped from the payload entirely when ``include=results``
    # was not supplied — distinguishes "did not ask" from "asked, no row".
    assert "results" not in body["record"]


def test_detail_include_results_on_sp(client, db_session):
    species, entry, calc = _make_species_owned_calc(
        db_session, calc_type=CalculationType.sp
    )
    attach_sp_result(
        db_session, calculation=calc, electronic_energy_hartree=-76.4234
    )
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}?include=results"
    ).json()
    assert body["request"]["include"] == ["results"]
    results = body["record"]["results"]
    assert results is not None
    assert results["kind"] == "sp"
    assert results["sp"]["electronic_energy_hartree"] == -76.4234
    assert results["sp"]["electronic_energy_uncertainty_hartree"] is None
    assert results["opt"] is None
    assert results["freq"] is None
    # available_sections agrees: there is a primary result row.
    assert body["record"]["available_sections"]["has_results"] is True
    # Default record fields still present and review badge still attached.
    assert body["record"]["calculation"]["calculation_ref"] == calc.public_ref
    assert body["record"]["calculation"]["review"]["status"] == "not_reviewed"


def test_detail_include_results_on_opt(client, db_session):
    _, _, calc = _make_species_owned_calc(
        db_session, calc_type=CalculationType.opt
    )
    attach_opt_result(
        db_session,
        calculation=calc,
        final_energy_hartree=-1.5,
        converged=True,
    )
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}?include=results"
    ).json()
    results = body["record"]["results"]
    assert results["kind"] == "opt"
    assert results["opt"]["converged"] is True
    assert results["opt"]["final_energy_hartree"] == -1.5
    # n_steps was not supplied by the fixture → null, not omitted.
    assert results["opt"]["n_steps"] is None


def test_detail_include_results_on_freq(client, db_session):
    _, _, calc = _make_species_owned_calc(
        db_session, calc_type=CalculationType.freq
    )
    db_session.add(
        CalculationFreqResult(
            calculation_id=calc.id,
            n_imag=0,
            zpe_hartree=0.04321,
        )
    )
    db_session.flush()

    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}?include=results"
    ).json()
    results = body["record"]["results"]
    assert results["kind"] == "freq"
    assert results["freq"]["n_imag"] == 0
    assert results["freq"]["zpe_hartree"] == 0.04321
    assert results["freq"]["imag_freq_cm1"] is None
    assert results["freq"]["zpe_uncertainty_hartree"] is None


def test_detail_include_results_no_row_returns_null(client, db_session):
    """``include=results`` on an opt calc without a result row returns
    ``results = null`` (not omitted) so callers can distinguish "asked
    but missing" from "did not ask"."""
    _, _, calc = _make_species_owned_calc(
        db_session, calc_type=CalculationType.opt
    )
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}?include=results"
    ).json()
    assert "results" in body["record"]
    assert body["record"]["results"] is None
    assert body["record"]["available_sections"]["has_results"] is False


def test_detail_include_results_on_conf_calc_returns_null(
    client, db_session
):
    """``conf`` calcs have no primary result table; the wrapper returns
    null without raising."""
    _, _, calc = _make_species_owned_calc(
        db_session, calc_type=CalculationType.conf
    )
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}?include=results"
    ).json()
    assert body["record"]["results"] is None
    assert body["record"]["available_sections"]["has_results"] is False


def test_detail_include_results_does_not_expose_internal_ids(
    client, db_session
):
    """The SP result summary intentionally carries no integer ids; even
    with the strip layer disabled it must still not contain
    ``calculation_id`` or ``*_id`` fields."""
    _, _, calc = _make_species_owned_calc(
        db_session, calc_type=CalculationType.sp
    )
    attach_sp_result(
        db_session, calculation=calc, electronic_energy_hartree=-1.0
    )
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}?include=results"
    ).json()
    results = body["record"]["results"]
    # No id-bearing keys in the result summary, in any per-type sub-block.
    for sub in ("sp", "opt", "freq", "scan", "irc", "path_search"):
        block = results[sub]
        if block is None:
            continue
        assert not any(k.endswith("_id") for k in block)
        assert "id" not in block


def test_detail_include_results_with_internal_ids_does_not_break(
    client, db_session, allow_internal_ids
):
    """``include=results,internal_ids`` keeps both behaviors: result
    summary populated AND integer ids restored on the rest of the
    record."""
    species, entry, calc = _make_species_owned_calc(
        db_session, calc_type=CalculationType.sp
    )
    attach_sp_result(
        db_session, calculation=calc, electronic_energy_hartree=-2.0
    )
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=results,internal_ids"
    ).json()
    assert body["record"]["calculation"]["calculation_id"] == calc.id
    assert body["record"]["results"]["sp"]["electronic_energy_hartree"] == -2.0


def test_available_sections_has_results_matches_include_results(
    client, db_session
):
    """Cross-check: ``available_sections.has_results`` must agree with
    whether ``include=results`` would return a non-null block."""
    # No result row → has_results false, results null.
    _, _, calc_no = _make_species_owned_calc(
        db_session, calc_type=CalculationType.sp
    )
    body_no = client.get(
        f"/api/v1/scientific/calculations/{calc_no.public_ref}?include=results"
    ).json()
    assert body_no["record"]["available_sections"]["has_results"] is False
    assert body_no["record"]["results"] is None

    # With result row → has_results true, results populated.
    _, _, calc_yes = _make_species_owned_calc(
        db_session, calc_type=CalculationType.sp
    )
    attach_sp_result(
        db_session, calculation=calc_yes, electronic_energy_hartree=-3.0
    )
    body_yes = client.get(
        f"/api/v1/scientific/calculations/{calc_yes.public_ref}"
        "?include=results"
    ).json()
    assert body_yes["record"]["available_sections"]["has_results"] is True
    assert body_yes["record"]["results"] is not None
    assert (
        body_yes["record"]["results"]["sp"]["electronic_energy_hartree"]
        == -3.0
    )


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
        "has_wavefunction_diagnostic": False,
        "has_spin_diagnostic": False,
        "has_freq_modes": False,
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


# ---------------------------------------------------------------------------
# include=dependencies
# ---------------------------------------------------------------------------


def test_detail_dependencies_omitted_when_not_requested(client, db_session):
    _, entry, calc = _make_species_owned_calc(db_session)
    other = make_calculation(
        db_session,
        type=CalculationType.sp,
        species_entry_id=entry.id,
    )
    attach_dependency(db_session, parent=other, child=calc)
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
    ).json()
    assert "dependencies" not in body["record"]


def test_detail_include_dependencies_parent_direction(client, db_session):
    """Edges where the requested calc is the *parent* surface as
    ``direction='parent'`` and put the requested calc in
    ``parent_calculation_ref``."""
    _, entry, parent_calc = _make_species_owned_calc(
        db_session, calc_type=CalculationType.opt
    )
    child_calc = make_calculation(
        db_session,
        type=CalculationType.freq,
        species_entry_id=entry.id,
    )
    attach_dependency(
        db_session,
        parent=parent_calc,
        child=child_calc,
        role=CalculationDependencyRole.freq_on,
    )
    body = client.get(
        f"/api/v1/scientific/calculations/{parent_calc.public_ref}"
        "?include=dependencies"
    ).json()
    deps = body["record"]["dependencies"]
    assert isinstance(deps, list) and len(deps) == 1
    edge = deps[0]
    assert edge["direction"] == "parent"
    assert edge["role"] == "freq_on"
    assert edge["parent_calculation_ref"] == parent_calc.public_ref
    assert edge["child_calculation_ref"] == child_calc.public_ref
    # Phase D default: integer ids stripped.
    assert "parent_calculation_id" not in edge
    assert "child_calculation_id" not in edge


def test_detail_include_dependencies_child_direction(client, db_session):
    """Edges where the requested calc is the *child* surface as
    ``direction='child'`` and put the requested calc in
    ``child_calculation_ref``."""
    _, entry, requested = _make_species_owned_calc(
        db_session, calc_type=CalculationType.freq
    )
    parent = make_calculation(
        db_session,
        type=CalculationType.opt,
        species_entry_id=entry.id,
    )
    attach_dependency(
        db_session,
        parent=parent,
        child=requested,
        role=CalculationDependencyRole.freq_on,
    )
    body = client.get(
        f"/api/v1/scientific/calculations/{requested.public_ref}"
        "?include=dependencies"
    ).json()
    deps = body["record"]["dependencies"]
    assert len(deps) == 1
    edge = deps[0]
    assert edge["direction"] == "child"
    assert edge["role"] == "freq_on"
    assert edge["child_calculation_ref"] == requested.public_ref
    assert edge["parent_calculation_ref"] == parent.public_ref


def test_detail_include_dependencies_returns_empty_list_when_none(
    client, db_session
):
    _, _, calc = _make_species_owned_calc(db_session)
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=dependencies"
    ).json()
    assert body["record"]["dependencies"] == []
    assert body["record"]["available_sections"]["has_dependencies"] is False


def test_detail_include_dependencies_returns_both_directions(
    client, db_session
):
    """A calculation that is both a parent of one edge and a child of
    another should surface both edges with the right directions."""
    _, entry, requested = _make_species_owned_calc(
        db_session, calc_type=CalculationType.opt
    )
    upstream = make_calculation(
        db_session,
        type=CalculationType.opt,
        species_entry_id=entry.id,
    )
    downstream = make_calculation(
        db_session,
        type=CalculationType.freq,
        species_entry_id=entry.id,
    )
    attach_dependency(
        db_session,
        parent=upstream,
        child=requested,
        role=CalculationDependencyRole.optimized_from,
    )
    attach_dependency(
        db_session,
        parent=requested,
        child=downstream,
        role=CalculationDependencyRole.freq_on,
    )

    body = client.get(
        f"/api/v1/scientific/calculations/{requested.public_ref}"
        "?include=dependencies"
    ).json()
    deps = body["record"]["dependencies"]
    assert len(deps) == 2
    by_role = {edge["role"]: edge for edge in deps}
    assert by_role["optimized_from"]["direction"] == "child"
    assert by_role["optimized_from"]["parent_calculation_ref"] == upstream.public_ref
    assert by_role["optimized_from"]["child_calculation_ref"] == requested.public_ref
    assert by_role["freq_on"]["direction"] == "parent"
    assert by_role["freq_on"]["parent_calculation_ref"] == requested.public_ref
    assert by_role["freq_on"]["child_calculation_ref"] == downstream.public_ref


def test_detail_include_dependencies_ordering_is_deterministic(
    client, db_session
):
    """Edges are sorted by (role, direction, parent_id, child_id).

    The schema enforces partial uniqueness on ``(child_calculation_id,
    dependency_role)`` for several roles, so the test uses distinct
    children per role to demonstrate the role-primary sort. Within a
    single role we add two parent-direction edges (which the
    constraint allows because parent_id varies) to demonstrate the
    parent_calculation_id tie-breaker.
    """
    _, entry, requested = _make_species_owned_calc(
        db_session, calc_type=CalculationType.opt
    )
    upstream = make_calculation(
        db_session, type=CalculationType.opt, species_entry_id=entry.id
    )
    downstream_a = make_calculation(
        db_session, type=CalculationType.freq, species_entry_id=entry.id
    )
    downstream_b = make_calculation(
        db_session, type=CalculationType.freq, species_entry_id=entry.id
    )
    # child-direction edge: optimized_from with requested as the child.
    attach_dependency(
        db_session,
        parent=upstream,
        child=requested,
        role=CalculationDependencyRole.optimized_from,
    )
    # Two parent-direction edges with the same role (freq_on); each
    # points at a different child so the partial-unique constraint is
    # not violated.
    attach_dependency(
        db_session,
        parent=requested,
        child=downstream_b,
        role=CalculationDependencyRole.freq_on,
    )
    attach_dependency(
        db_session,
        parent=requested,
        child=downstream_a,
        role=CalculationDependencyRole.freq_on,
    )

    body_first = client.get(
        f"/api/v1/scientific/calculations/{requested.public_ref}"
        "?include=dependencies"
    ).json()
    body_second = client.get(
        f"/api/v1/scientific/calculations/{requested.public_ref}"
        "?include=dependencies"
    ).json()
    deps_first = body_first["record"]["dependencies"]

    # Two requests yield the same order — deterministic across calls.
    assert deps_first == body_second["record"]["dependencies"]
    # Primary sort: role ASC. ``freq_on`` < ``optimized_from``.
    roles = [edge["role"] for edge in deps_first]
    assert roles == sorted(roles)
    # Tie-breaker on the same role: child_calculation_id ASC. The two
    # freq_on edges should appear with downstream_a (lower id) before
    # downstream_b (higher id) because we attached b first.
    freq_on_edges = [e for e in deps_first if e["role"] == "freq_on"]
    assert (
        freq_on_edges[0]["child_calculation_ref"] == downstream_a.public_ref
    )
    assert (
        freq_on_edges[1]["child_calculation_ref"] == downstream_b.public_ref
    )


def test_detail_include_dependencies_internal_ids_when_allowed(
    client, db_session, allow_internal_ids
):
    _, entry, parent_calc = _make_species_owned_calc(
        db_session, calc_type=CalculationType.opt
    )
    child_calc = make_calculation(
        db_session,
        type=CalculationType.freq,
        species_entry_id=entry.id,
    )
    attach_dependency(
        db_session,
        parent=parent_calc,
        child=child_calc,
        role=CalculationDependencyRole.freq_on,
    )
    body = client.get(
        f"/api/v1/scientific/calculations/{parent_calc.public_ref}"
        "?include=dependencies,internal_ids"
    ).json()
    edge = body["record"]["dependencies"][0]
    # Refs always present.
    assert edge["parent_calculation_ref"] == parent_calc.public_ref
    assert edge["child_calculation_ref"] == child_calc.public_ref
    # Integer ids restored under the policy fixture.
    assert edge["parent_calculation_id"] == parent_calc.id
    assert edge["child_calculation_id"] == child_calc.id


def test_detail_include_dependencies_combines_with_results(
    client, db_session
):
    _, entry, calc = _make_species_owned_calc(
        db_session, calc_type=CalculationType.opt
    )
    attach_opt_result(
        db_session, calculation=calc, final_energy_hartree=-10.0
    )
    child_calc = make_calculation(
        db_session,
        type=CalculationType.freq,
        species_entry_id=entry.id,
    )
    attach_dependency(
        db_session,
        parent=calc,
        child=child_calc,
        role=CalculationDependencyRole.freq_on,
    )
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=results,dependencies"
    ).json()
    record = body["record"]
    assert record["results"]["kind"] == "opt"
    assert record["results"]["opt"]["final_energy_hartree"] == -10.0
    assert len(record["dependencies"]) == 1
    assert record["dependencies"][0]["direction"] == "parent"


def test_available_sections_has_dependencies_matches_include_result(
    client, db_session
):
    """available_sections.has_dependencies must agree with what
    include=dependencies would actually return."""
    _, entry, lonely = _make_species_owned_calc(db_session)
    body_lonely = client.get(
        f"/api/v1/scientific/calculations/{lonely.public_ref}"
        "?include=dependencies"
    ).json()
    assert body_lonely["record"]["available_sections"]["has_dependencies"] is False
    assert body_lonely["record"]["dependencies"] == []

    _, entry2, connected = _make_species_owned_calc(db_session)
    other = make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry2.id
    )
    attach_dependency(db_session, parent=other, child=connected)
    body_connected = client.get(
        f"/api/v1/scientific/calculations/{connected.public_ref}"
        "?include=dependencies"
    ).json()
    assert body_connected["record"]["available_sections"]["has_dependencies"] is True
    assert len(body_connected["record"]["dependencies"]) == 1


# ---------------------------------------------------------------------------
# include=artifacts
# ---------------------------------------------------------------------------


def test_detail_artifacts_omitted_when_not_requested(client, db_session):
    _, _, calc = _make_species_owned_calc(db_session)
    attach_artifact(db_session, calculation=calc)
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
    ).json()
    assert "artifacts" not in body["record"]


def test_detail_include_artifacts_returns_metadata_rows(client, db_session):
    _, _, calc = _make_species_owned_calc(db_session)
    art = attach_artifact(
        db_session,
        calculation=calc,
        kind=ArtifactKind.output_log,
        filename="job.log",
        uri="s3://bucket/job.log",
    )
    # Backfill a couple of optional metadata fields to verify pass-through.
    art.sha256 = "a" * 64
    art.bytes = 12345
    db_session.flush()

    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=artifacts"
    ).json()
    artifacts = body["record"]["artifacts"]
    assert isinstance(artifacts, list) and len(artifacts) == 1
    row = artifacts[0]
    assert row["kind"] == "output_log"
    assert row["uri"] == "s3://bucket/job.log"
    assert row["filename"] == "job.log"
    assert row["sha256"] == "a" * 64
    assert row["bytes"] == 12345
    assert row["created_at"] is not None
    # No public ref column on calculation_artifact yet.
    assert row["artifact_ref"] is None
    # Phase D default: integer artifact_id stripped.
    assert "artifact_id" not in row
    # No body-content fields leak through.
    for forbidden in ("body", "content", "data", "presigned_url", "download_url"):
        assert forbidden not in row


def test_detail_include_artifacts_returns_empty_list_when_none(
    client, db_session
):
    _, _, calc = _make_species_owned_calc(db_session)
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}?include=artifacts"
    ).json()
    assert body["record"]["artifacts"] == []
    assert body["record"]["available_sections"]["has_artifacts"] is False


def test_detail_include_artifacts_no_body_or_content_fields(
    client, db_session
):
    """Defense-in-depth: even if the ORM grew a body/content column, the
    public artifact summary must never carry it."""
    _, _, calc = _make_species_owned_calc(db_session)
    attach_artifact(db_session, calculation=calc)
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}?include=artifacts"
    ).json()
    legal_keys = {
        "artifact_id",
        "artifact_ref",
        "kind",
        "uri",
        "filename",
        "sha256",
        "bytes",
        "created_at",
    }
    for row in body["record"]["artifacts"]:
        assert set(row.keys()).issubset(legal_keys), set(row.keys()) - legal_keys


def test_detail_include_artifacts_internal_ids_when_allowed(
    client, db_session, allow_internal_ids
):
    _, _, calc = _make_species_owned_calc(db_session)
    art = attach_artifact(db_session, calculation=calc)
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=artifacts,internal_ids"
    ).json()
    row = body["record"]["artifacts"][0]
    assert row["artifact_id"] == art.id
    assert row["artifact_ref"] is None  # still no ref column


def test_detail_include_artifacts_ordering_is_deterministic(
    client, db_session
):
    """Ordering is (kind ASC, created_at ASC NULLS LAST, id ASC).

    ``output_log`` < ``input`` lexicographically? No: ``input`` < ``output_log``.
    The test attaches one of each and checks ``input`` appears first; then
    adds two ``output_log`` rows with distinct ids and checks they appear
    in id-ascending order.
    """
    _, _, calc = _make_species_owned_calc(db_session)
    out_b = attach_artifact(
        db_session,
        calculation=calc,
        kind=ArtifactKind.output_log,
        filename="b.log",
    )
    inp = attach_artifact(
        db_session,
        calculation=calc,
        kind=ArtifactKind.input,
        filename="in.dat",
    )
    out_a = attach_artifact(
        db_session,
        calculation=calc,
        kind=ArtifactKind.output_log,
        filename="a.log",
    )

    body_first = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}?include=artifacts"
    ).json()
    body_second = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}?include=artifacts"
    ).json()
    artifacts = body_first["record"]["artifacts"]

    # Stable across calls.
    assert artifacts == body_second["record"]["artifacts"]
    # Primary sort key: kind ASC. ``input`` precedes ``output_log``.
    kinds = [r["kind"] for r in artifacts]
    assert kinds == ["input", "output_log", "output_log"]
    # Tie-breaker among the two output_log rows: id ASC. ``out_b`` was
    # inserted before ``out_a``, so ``out_b`` has the lower id and
    # appears first.
    out_logs = [r for r in artifacts if r["kind"] == "output_log"]
    assert out_logs[0]["filename"] == out_b.filename
    assert out_logs[1]["filename"] == out_a.filename
    # And the input row carries its filename through.
    assert artifacts[0]["filename"] == inp.filename


def test_available_sections_has_artifacts_matches_include_result(
    client, db_session
):
    """available_sections.has_artifacts must agree with what
    include=artifacts would actually return."""
    _, _, lonely = _make_species_owned_calc(db_session)
    body_lonely = client.get(
        f"/api/v1/scientific/calculations/{lonely.public_ref}"
        "?include=artifacts"
    ).json()
    assert body_lonely["record"]["available_sections"]["has_artifacts"] is False
    assert body_lonely["record"]["artifacts"] == []

    _, _, with_art = _make_species_owned_calc(db_session)
    attach_artifact(db_session, calculation=with_art)
    body_with = client.get(
        f"/api/v1/scientific/calculations/{with_art.public_ref}"
        "?include=artifacts"
    ).json()
    assert body_with["record"]["available_sections"]["has_artifacts"] is True
    assert len(body_with["record"]["artifacts"]) == 1


def test_detail_include_results_dependencies_artifacts_combined(
    client, db_session
):
    _, entry, calc = _make_species_owned_calc(
        db_session, calc_type=CalculationType.opt
    )
    attach_opt_result(
        db_session, calculation=calc, final_energy_hartree=-10.0
    )
    child_calc = make_calculation(
        db_session, type=CalculationType.freq, species_entry_id=entry.id
    )
    attach_dependency(
        db_session,
        parent=calc,
        child=child_calc,
        role=CalculationDependencyRole.freq_on,
    )
    attach_artifact(db_session, calculation=calc)
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=results,dependencies,artifacts"
    ).json()
    record = body["record"]
    assert record["results"]["kind"] == "opt"
    assert len(record["dependencies"]) == 1
    assert len(record["artifacts"]) == 1


# ---------------------------------------------------------------------------
# include=input_geometries / include=output_geometries
# ---------------------------------------------------------------------------


def _attach_input_geometry(db_session, *, calculation, geometry, input_order):
    """Inline helper — no factory helper exists for input-geometry links."""
    row = CalculationInputGeometry(
        calculation_id=calculation.id,
        geometry_id=geometry.id,
        input_order=input_order,
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_detail_input_geometries_omitted_when_not_requested(
    client, db_session
):
    _, _, calc = _make_species_owned_calc(db_session)
    geom = make_geometry(db_session, natoms=3)
    _attach_input_geometry(
        db_session, calculation=calc, geometry=geom, input_order=1
    )
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
    ).json()
    assert "input_geometries" not in body["record"]


def test_detail_output_geometries_omitted_when_not_requested(
    client, db_session
):
    _, _, calc = _make_species_owned_calc(db_session)
    geom = make_geometry(db_session, natoms=3)
    attach_output_geometry(
        db_session,
        calculation=calc,
        geometry=geom,
        role=CalculationGeometryRole.final,
    )
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
    ).json()
    assert "output_geometries" not in body["record"]


def test_detail_include_input_geometries_returns_link_summaries(
    client, db_session
):
    _, _, calc = _make_species_owned_calc(db_session)
    geom = make_geometry(db_session, natoms=5)
    _attach_input_geometry(
        db_session, calculation=calc, geometry=geom, input_order=1
    )
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=input_geometries"
    ).json()
    inputs = body["record"]["input_geometries"]
    assert isinstance(inputs, list) and len(inputs) == 1
    link = inputs[0]
    assert link["geometry_ref"] == geom.public_ref
    assert link["input_order"] == 1
    # Output-side fields are null on input links.
    assert link["output_order"] is None
    assert link["role"] is None
    # Cheap metadata: natoms + geom_hash exposed; no XYZ.
    assert link["natoms"] == 5
    assert link["geom_hash"] == geom.geom_hash
    # Phase D default: integer geometry_id stripped.
    assert "geometry_id" not in link


def test_detail_include_output_geometries_returns_link_summaries(
    client, db_session
):
    _, _, calc = _make_species_owned_calc(db_session)
    geom = make_geometry(db_session, natoms=4)
    attach_output_geometry(
        db_session,
        calculation=calc,
        geometry=geom,
        role=CalculationGeometryRole.final,
        output_order=1,
    )
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=output_geometries"
    ).json()
    outputs = body["record"]["output_geometries"]
    assert len(outputs) == 1
    link = outputs[0]
    assert link["geometry_ref"] == geom.public_ref
    assert link["output_order"] == 1
    assert link["role"] == "final"
    # Input-side field is null on output links.
    assert link["input_order"] is None
    assert link["natoms"] == 4
    assert "geometry_id" not in link


def test_detail_include_input_geometries_returns_empty_list_when_none(
    client, db_session
):
    _, _, calc = _make_species_owned_calc(db_session)
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=input_geometries"
    ).json()
    assert body["record"]["input_geometries"] == []
    assert body["record"]["available_sections"]["has_input_geometries"] is False


def test_detail_include_output_geometries_returns_empty_list_when_none(
    client, db_session
):
    _, _, calc = _make_species_owned_calc(db_session)
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=output_geometries"
    ).json()
    assert body["record"]["output_geometries"] == []
    assert body["record"]["available_sections"]["has_output_geometries"] is False


def test_detail_input_geometries_no_xyz_or_atom_arrays(client, db_session):
    """Defense-in-depth: link summaries must never carry inlined XYZ
    text or per-atom coordinate arrays. Those live behind
    ``/scientific/geometries/{geometry_ref}``."""
    _, _, calc = _make_species_owned_calc(db_session)
    geom = make_geometry(db_session, natoms=3, xyz_text="O 0 0 0")
    _attach_input_geometry(
        db_session, calculation=calc, geometry=geom, input_order=1
    )
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=input_geometries,output_geometries"
    ).json()
    legal_keys = {
        "geometry_id",
        "geometry_ref",
        "input_order",
        "output_order",
        "role",
        "natoms",
        "geom_hash",
    }
    for link in body["record"]["input_geometries"]:
        assert set(link.keys()).issubset(legal_keys)
        for forbidden in ("xyz_text", "atoms", "coords", "symbols"):
            assert forbidden not in link
    for link in body["record"]["output_geometries"]:
        assert set(link.keys()).issubset(legal_keys)


def test_detail_input_geometries_ordering_is_deterministic(client, db_session):
    """Input links sort by ``input_order ASC`` (composite PK)."""
    _, _, calc = _make_species_owned_calc(db_session)
    g1 = make_geometry(db_session, natoms=3)
    g2 = make_geometry(db_session, natoms=4)
    g3 = make_geometry(db_session, natoms=5)
    # Insert out of order.
    _attach_input_geometry(
        db_session, calculation=calc, geometry=g2, input_order=2
    )
    _attach_input_geometry(
        db_session, calculation=calc, geometry=g3, input_order=3
    )
    _attach_input_geometry(
        db_session, calculation=calc, geometry=g1, input_order=1
    )

    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=input_geometries"
    ).json()
    inputs = body["record"]["input_geometries"]
    assert [link["input_order"] for link in inputs] == [1, 2, 3]
    assert [link["geometry_ref"] for link in inputs] == [
        g1.public_ref,
        g2.public_ref,
        g3.public_ref,
    ]


def test_detail_output_geometries_ordering_is_deterministic(client, db_session):
    """Output links sort by ``output_order ASC``."""
    _, _, calc = _make_species_owned_calc(db_session)
    g1 = make_geometry(db_session, natoms=3)
    g2 = make_geometry(db_session, natoms=4)
    attach_output_geometry(
        db_session,
        calculation=calc,
        geometry=g2,
        role=CalculationGeometryRole.scan_point,
        output_order=2,
    )
    attach_output_geometry(
        db_session,
        calculation=calc,
        geometry=g1,
        role=CalculationGeometryRole.final,
        output_order=1,
    )
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=output_geometries"
    ).json()
    outputs = body["record"]["output_geometries"]
    assert [link["output_order"] for link in outputs] == [1, 2]
    assert [link["role"] for link in outputs] == ["final", "scan_point"]


def test_detail_geometry_links_internal_ids_when_allowed(
    client, db_session, allow_internal_ids
):
    _, _, calc = _make_species_owned_calc(db_session)
    g_in = make_geometry(db_session, natoms=3)
    g_out = make_geometry(db_session, natoms=3)
    _attach_input_geometry(
        db_session, calculation=calc, geometry=g_in, input_order=1
    )
    attach_output_geometry(
        db_session,
        calculation=calc,
        geometry=g_out,
        role=CalculationGeometryRole.final,
    )
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=input_geometries,output_geometries,internal_ids"
    ).json()
    assert body["record"]["input_geometries"][0]["geometry_id"] == g_in.id
    assert body["record"]["output_geometries"][0]["geometry_id"] == g_out.id


def test_available_sections_has_geometries_match_include_results(
    client, db_session
):
    """available_sections.has_input_geometries / has_output_geometries
    must agree with what the include would actually return."""
    _, _, lonely = _make_species_owned_calc(db_session)
    body_lonely = client.get(
        f"/api/v1/scientific/calculations/{lonely.public_ref}"
        "?include=input_geometries,output_geometries"
    ).json()
    sections = body_lonely["record"]["available_sections"]
    assert sections["has_input_geometries"] is False
    assert sections["has_output_geometries"] is False
    assert body_lonely["record"]["input_geometries"] == []
    assert body_lonely["record"]["output_geometries"] == []

    _, _, with_links = _make_species_owned_calc(db_session)
    g_in = make_geometry(db_session, natoms=3)
    g_out = make_geometry(db_session, natoms=3)
    _attach_input_geometry(
        db_session, calculation=with_links, geometry=g_in, input_order=1
    )
    attach_output_geometry(
        db_session,
        calculation=with_links,
        geometry=g_out,
        role=CalculationGeometryRole.final,
    )
    body_with = client.get(
        f"/api/v1/scientific/calculations/{with_links.public_ref}"
        "?include=input_geometries,output_geometries"
    ).json()
    sections = body_with["record"]["available_sections"]
    assert sections["has_input_geometries"] is True
    assert sections["has_output_geometries"] is True
    assert len(body_with["record"]["input_geometries"]) == 1
    assert len(body_with["record"]["output_geometries"]) == 1


def test_detail_full_implemented_include_set_works(client, db_session):
    """``include=results,dependencies,artifacts,input_geometries,
    output_geometries`` must populate every implemented heavy section
    in a single request."""
    _, entry, calc = _make_species_owned_calc(
        db_session, calc_type=CalculationType.opt
    )
    attach_opt_result(
        db_session, calculation=calc, final_energy_hartree=-10.0
    )
    child = make_calculation(
        db_session, type=CalculationType.freq, species_entry_id=entry.id
    )
    attach_dependency(
        db_session,
        parent=calc,
        child=child,
        role=CalculationDependencyRole.freq_on,
    )
    attach_artifact(db_session, calculation=calc)
    g_in = make_geometry(db_session, natoms=3)
    g_out = make_geometry(db_session, natoms=3)
    _attach_input_geometry(
        db_session, calculation=calc, geometry=g_in, input_order=1
    )
    attach_output_geometry(
        db_session,
        calculation=calc,
        geometry=g_out,
        role=CalculationGeometryRole.final,
    )
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=results,dependencies,artifacts,"
        "input_geometries,output_geometries"
    ).json()
    record = body["record"]
    assert record["results"]["kind"] == "opt"
    assert len(record["dependencies"]) == 1
    assert len(record["artifacts"]) == 1
    assert len(record["input_geometries"]) == 1
    assert len(record["output_geometries"]) == 1


# ---------------------------------------------------------------------------
# include=geometry_validation
# ---------------------------------------------------------------------------


def test_detail_geometry_validation_omitted_when_not_requested(
    client, db_session
):
    _, _, calc = _make_species_owned_calc(db_session)
    attach_geometry_validation(db_session, calculation=calc)
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
    ).json()
    assert "geometry_validation" not in body["record"]


def test_detail_include_geometry_validation_returns_summary(
    client, db_session
):
    _, _, calc = _make_species_owned_calc(db_session)
    geom_in = make_geometry(db_session, natoms=3)
    geom_out = make_geometry(db_session, natoms=3)
    row = attach_geometry_validation(
        db_session,
        calculation=calc,
        status=ValidationStatus.passed,
        species_smiles="O",
        is_isomorphic=True,
    )
    row.input_geometry_id = geom_in.id
    row.output_geometry_id = geom_out.id
    row.rmsd = 0.012
    row.n_mappings = 1
    row.validation_reason = None
    row.rmsd_warning_threshold = 0.5
    db_session.flush()

    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=geometry_validation"
    ).json()
    rows = body["record"]["geometry_validation"]
    assert isinstance(rows, list) and len(rows) == 1
    summary = rows[0]
    assert summary["validation_status"] == "passed"
    assert summary["species_smiles"] == "O"
    assert summary["is_isomorphic"] is True
    assert summary["rmsd"] == 0.012
    assert summary["n_mappings"] == 1
    assert summary["rmsd_warning_threshold"] == 0.5
    assert summary["input_geometry_ref"] == geom_in.public_ref
    assert summary["output_geometry_ref"] == geom_out.public_ref
    # Phase D default: integer ids stripped.
    assert "input_geometry_id" not in summary
    assert "output_geometry_id" not in summary
    # MVP: atom_mapping is intentionally not exposed.
    assert "atom_mapping" not in summary


def test_detail_include_geometry_validation_returns_empty_list_when_none(
    client, db_session
):
    _, _, calc = _make_species_owned_calc(db_session)
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=geometry_validation"
    ).json()
    assert body["record"]["geometry_validation"] == []
    assert (
        body["record"]["available_sections"]["has_geometry_validation"]
        is False
    )


def test_detail_geometry_validation_no_atom_mapping_field(client, db_session):
    """Defense-in-depth: even with a fully-populated row including a
    JSONB atom_mapping, the public summary must never include it."""
    _, _, calc = _make_species_owned_calc(db_session)
    row = attach_geometry_validation(db_session, calculation=calc)
    row.atom_mapping = {"0": 0, "1": 1, "2": 2}
    db_session.flush()
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=geometry_validation"
    ).json()
    legal_keys = {
        "input_geometry_id",
        "input_geometry_ref",
        "output_geometry_id",
        "output_geometry_ref",
        "species_smiles",
        "is_isomorphic",
        "rmsd",
        "n_mappings",
        "validation_status",
        "validation_reason",
        "rmsd_warning_threshold",
        "created_at",
    }
    summary = body["record"]["geometry_validation"][0]
    assert set(summary.keys()).issubset(legal_keys)
    assert "atom_mapping" not in summary


def test_detail_geometry_validation_internal_ids_when_allowed(
    client, db_session, allow_internal_ids
):
    _, _, calc = _make_species_owned_calc(db_session)
    geom_in = make_geometry(db_session, natoms=3)
    geom_out = make_geometry(db_session, natoms=3)
    row = attach_geometry_validation(db_session, calculation=calc)
    row.input_geometry_id = geom_in.id
    row.output_geometry_id = geom_out.id
    db_session.flush()
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=geometry_validation,internal_ids"
    ).json()
    summary = body["record"]["geometry_validation"][0]
    assert summary["input_geometry_id"] == geom_in.id
    assert summary["output_geometry_id"] == geom_out.id


def test_detail_geometry_validation_ordering_is_deterministic(
    client, db_session
):
    """Two requests return the geometry_validation list in the same order.

    The schema constrains at most one row per calculation so the list
    has at most one entry; this test still exercises the
    deterministic-call invariant required by the spec.
    """
    _, _, calc = _make_species_owned_calc(db_session)
    attach_geometry_validation(db_session, calculation=calc)
    body_first = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=geometry_validation"
    ).json()
    body_second = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=geometry_validation"
    ).json()
    assert body_first["record"]["geometry_validation"] == (
        body_second["record"]["geometry_validation"]
    )


def test_available_sections_has_geometry_validation_matches(client, db_session):
    _, _, lonely = _make_species_owned_calc(db_session)
    body_lonely = client.get(
        f"/api/v1/scientific/calculations/{lonely.public_ref}"
        "?include=geometry_validation"
    ).json()
    assert (
        body_lonely["record"]["available_sections"]["has_geometry_validation"]
        is False
    )
    assert body_lonely["record"]["geometry_validation"] == []

    _, _, with_row = _make_species_owned_calc(db_session)
    attach_geometry_validation(db_session, calculation=with_row)
    body_with = client.get(
        f"/api/v1/scientific/calculations/{with_row.public_ref}"
        "?include=geometry_validation"
    ).json()
    assert (
        body_with["record"]["available_sections"]["has_geometry_validation"]
        is True
    )
    assert len(body_with["record"]["geometry_validation"]) == 1


# ---------------------------------------------------------------------------
# include=scf_stability
# ---------------------------------------------------------------------------


def test_detail_scf_stability_omitted_when_not_requested(client, db_session):
    _, _, calc = _make_species_owned_calc(db_session)
    attach_scf_stability(db_session, calculation=calc)
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
    ).json()
    assert "scf_stability" not in body["record"]


def test_detail_spin_diagnostic_omitted_when_not_requested(client, db_session):
    """The ``spin_diagnostic`` key must be ABSENT (not ``null``) from a
    default calculation read that did not request it. Contract: key absent
    means "not asked"; key present + ``null`` means "asked, no row". A row
    exists here precisely to prove the omission is driven by the include
    request, not by the row's absence.
    """
    _, _, calc = _make_species_owned_calc(db_session)
    attach_spin_diagnostic(db_session, calculation=calc)
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
    ).json()
    assert "spin_diagnostic" not in body["record"]


# ---------------------------------------------------------------------------
# include=freq_modes
# ---------------------------------------------------------------------------


def test_detail_freq_modes_omitted_when_not_requested(client, db_session):
    """The ``freq_modes`` key must be ABSENT (not ``null`` or ``[]``) from
    a default read that did not request it. A calc with parsed modes is
    used precisely to prove the omission is driven by the include request,
    not by the rows' absence.
    """
    _, _, calc = _make_species_owned_calc(
        db_session, calc_type=CalculationType.freq
    )
    attach_freq_result(
        db_session, calculation=calc, frequencies_cm1=[-1200.0, 800.0, 1600.0]
    )
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
    ).json()
    assert "freq_modes" not in body["record"]


def test_detail_include_freq_modes_returns_per_mode_array(client, db_session):
    """``include=freq_modes`` surfaces the full ordered per-mode array
    matching the stored ``calc_freq_mode`` rows, including the imaginary
    flag (negative wavenumber) and the reduced-mass / force-constant
    columns."""
    _, _, calc = _make_species_owned_calc(
        db_session, calc_type=CalculationType.freq
    )
    attach_freq_result(
        db_session,
        calculation=calc,
        frequencies_cm1=[-1200.0, 800.0, 1600.0],
        reduced_masses_amu=[1.05, 2.10, None],
        force_constants_mdyne_angstrom=[0.5, None, 3.2],
    )
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=freq_modes"
    ).json()
    assert body["request"]["include"] == ["freq_modes"]
    modes = body["record"]["freq_modes"]
    assert modes == [
        {
            "mode_index": 1,
            "frequency_cm1": -1200.0,
            "is_imaginary": True,
            "reduced_mass_amu": 1.05,
            "force_constant_mdyne_angstrom": 0.5,
        },
        {
            "mode_index": 2,
            "frequency_cm1": 800.0,
            "is_imaginary": False,
            "reduced_mass_amu": 2.10,
            "force_constant_mdyne_angstrom": None,
        },
        {
            "mode_index": 3,
            "frequency_cm1": 1600.0,
            "is_imaginary": False,
            "reduced_mass_amu": None,
            "force_constant_mdyne_angstrom": 3.2,
        },
    ]
    # available_sections agrees that modes are present.
    assert body["record"]["available_sections"]["has_freq_modes"] is True


def test_detail_include_freq_modes_empty_list_when_no_modes(
    client, db_session
):
    """``include=freq_modes`` on a calc without parsed modes returns an
    empty list (present, not omitted) so callers can distinguish "asked
    but none" from "did not ask"."""
    _, _, calc = _make_species_owned_calc(
        db_session, calc_type=CalculationType.freq
    )
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=freq_modes"
    ).json()
    assert body["record"]["freq_modes"] == []
    assert body["record"]["available_sections"]["has_freq_modes"] is False


def test_detail_include_freq_modes_does_not_expose_internal_ids(
    client, db_session
):
    """The per-mode array carries no DB surrogate ids — only the
    scientific ``mode_index`` order key and the physical columns."""
    _, _, calc = _make_species_owned_calc(
        db_session, calc_type=CalculationType.freq
    )
    attach_freq_result(
        db_session, calculation=calc, frequencies_cm1=[800.0, 1600.0]
    )
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=freq_modes"
    ).json()
    for mode in body["record"]["freq_modes"]:
        assert "calculation_id" not in mode
        assert "id" not in mode


def test_detail_include_scf_stability_returns_summary(client, db_session):
    _, _, calc = _make_species_owned_calc(db_session)
    row = attach_scf_stability(
        db_session,
        calculation=calc,
        status=SCFStabilityStatus.stable,
    )
    row.lowest_eigenvalue = 0.01
    row.instability_count = 0
    row.note = "ok"
    db_session.flush()

    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=scf_stability"
    ).json()
    rows = body["record"]["scf_stability"]
    assert isinstance(rows, list) and len(rows) == 1
    summary = rows[0]
    assert summary["status"] == "stable"
    assert summary["lowest_eigenvalue"] == 0.01
    assert summary["instability_count"] == 0
    assert summary["note"] == "ok"
    assert summary["created_at"] is not None
    # No source links set; refs are null.
    assert summary["source_calculation_ref"] is None
    assert summary["source_artifact_ref"] is None
    # Phase D default: integer ids stripped.
    assert "source_calculation_id" not in summary
    assert "source_artifact_id" not in summary


def test_detail_include_scf_stability_returns_empty_list_when_none(
    client, db_session
):
    _, _, calc = _make_species_owned_calc(db_session)
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=scf_stability"
    ).json()
    assert body["record"]["scf_stability"] == []
    assert body["record"]["available_sections"]["has_scf_stability"] is False


def test_detail_scf_stability_resolves_source_calculation_ref(
    client, db_session
):
    _, entry, calc = _make_species_owned_calc(db_session)
    source_calc = make_calculation(
        db_session, type=CalculationType.opt, species_entry_id=entry.id
    )
    row = attach_scf_stability(db_session, calculation=calc)
    row.source_calculation_id = source_calc.id
    db_session.flush()

    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=scf_stability"
    ).json()
    summary = body["record"]["scf_stability"][0]
    assert summary["source_calculation_ref"] == source_calc.public_ref
    # source_artifact_ref stays null; calculation_artifact has no public_ref column.
    assert summary["source_artifact_ref"] is None


def test_detail_scf_stability_internal_ids_when_allowed(
    client, db_session, allow_internal_ids
):
    _, entry, calc = _make_species_owned_calc(db_session)
    source_calc = make_calculation(
        db_session, type=CalculationType.opt, species_entry_id=entry.id
    )
    art = attach_artifact(db_session, calculation=calc)
    row = attach_scf_stability(db_session, calculation=calc)
    row.source_calculation_id = source_calc.id
    row.source_artifact_id = art.id
    db_session.flush()
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=scf_stability,internal_ids"
    ).json()
    summary = body["record"]["scf_stability"][0]
    assert summary["source_calculation_id"] == source_calc.id
    assert summary["source_artifact_id"] == art.id


def test_detail_scf_stability_ordering_is_deterministic(client, db_session):
    """Two requests return the scf_stability list in the same order."""
    _, _, calc = _make_species_owned_calc(db_session)
    attach_scf_stability(db_session, calculation=calc)
    body_first = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=scf_stability"
    ).json()
    body_second = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=scf_stability"
    ).json()
    assert body_first["record"]["scf_stability"] == (
        body_second["record"]["scf_stability"]
    )


def test_available_sections_has_scf_stability_matches(client, db_session):
    _, _, lonely = _make_species_owned_calc(db_session)
    body_lonely = client.get(
        f"/api/v1/scientific/calculations/{lonely.public_ref}"
        "?include=scf_stability"
    ).json()
    assert (
        body_lonely["record"]["available_sections"]["has_scf_stability"]
        is False
    )
    assert body_lonely["record"]["scf_stability"] == []

    _, _, with_row = _make_species_owned_calc(db_session)
    attach_scf_stability(db_session, calculation=with_row)
    body_with = client.get(
        f"/api/v1/scientific/calculations/{with_row.public_ref}"
        "?include=scf_stability"
    ).json()
    assert (
        body_with["record"]["available_sections"]["has_scf_stability"]
        is True
    )
    assert len(body_with["record"]["scf_stability"]) == 1


# ---------------------------------------------------------------------------
# Combined includes + guards
# ---------------------------------------------------------------------------


def test_detail_include_geometry_validation_and_scf_stability_combined(
    client, db_session
):
    _, _, calc = _make_species_owned_calc(db_session)
    attach_geometry_validation(db_session, calculation=calc)
    attach_scf_stability(db_session, calculation=calc)
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=geometry_validation,scf_stability"
    ).json()
    record = body["record"]
    assert len(record["geometry_validation"]) == 1
    assert len(record["scf_stability"]) == 1


def test_detail_full_implemented_include_set_with_validation_works(
    client, db_session
):
    """All currently-implemented heavy includes in one call."""
    _, entry, calc = _make_species_owned_calc(
        db_session, calc_type=CalculationType.opt
    )
    attach_opt_result(
        db_session, calculation=calc, final_energy_hartree=-10.0
    )
    child = make_calculation(
        db_session, type=CalculationType.freq, species_entry_id=entry.id
    )
    attach_dependency(
        db_session,
        parent=calc,
        child=child,
        role=CalculationDependencyRole.freq_on,
    )
    attach_artifact(db_session, calculation=calc)
    g_in = make_geometry(db_session, natoms=3)
    g_out = make_geometry(db_session, natoms=3)
    _attach_input_geometry(
        db_session, calculation=calc, geometry=g_in, input_order=1
    )
    attach_output_geometry(
        db_session,
        calculation=calc,
        geometry=g_out,
        role=CalculationGeometryRole.final,
    )
    attach_geometry_validation(db_session, calculation=calc)
    attach_scf_stability(db_session, calculation=calc)

    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=results,dependencies,artifacts,"
        "input_geometries,output_geometries,"
        "geometry_validation,scf_stability"
    ).json()
    record = body["record"]
    assert record["results"]["kind"] == "opt"
    assert len(record["dependencies"]) == 1
    assert len(record["artifacts"]) == 1
    assert len(record["input_geometries"]) == 1
    assert len(record["output_geometries"]) == 1
    assert len(record["geometry_validation"]) == 1
    assert len(record["scf_stability"]) == 1


# ---------------------------------------------------------------------------
# include=parameters
# ---------------------------------------------------------------------------


def _ensure_param_vocab_local(db_session, canonical_key: str) -> None:
    """Idempotent vocab seed for a canonical_key.

    ``calculation_parameter.canonical_key`` has an FK to
    ``calculation_parameter_vocab.canonical_key``; tests that set a
    canonical_key must seed the vocab first.
    """
    if db_session.get(CalculationParameterVocab, canonical_key) is None:
        db_session.add(
            CalculationParameterVocab(canonical_key=canonical_key)
        )
        db_session.flush()


def _attach_calc_parameter(
    db_session,
    *,
    calculation,
    raw_key: str,
    raw_value: str,
    canonical_key: str | None = None,
    canonical_value: str | None = None,
    section: str | None = None,
    parameter_index: int | None = None,
):
    """Insert one ``calculation_parameter`` row for the given calc."""
    if canonical_key is not None:
        _ensure_param_vocab_local(db_session, canonical_key)
    row = CalculationParameter(
        calculation_id=calculation.id,
        raw_key=raw_key,
        raw_value=raw_value,
        canonical_key=canonical_key,
        canonical_value=canonical_value,
        section=section,
        parameter_index=parameter_index,
        source=ParameterSource.upload,
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_detail_parameters_omitted_when_not_requested(client, db_session):
    _, _, calc = _make_species_owned_calc(db_session)
    _attach_calc_parameter(
        db_session, calculation=calc, raw_key="Grid", raw_value="ultrafine"
    )
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
    ).json()
    assert "parameters" not in body["record"]


def test_detail_include_parameters_returns_summaries(client, db_session):
    _, _, calc = _make_species_owned_calc(db_session)
    _attach_calc_parameter(
        db_session,
        calculation=calc,
        raw_key="ScfConv",
        raw_value="1e-8",
        canonical_key="scf.convergence",
        canonical_value="1e-8",
        section="scf",
        parameter_index=1,
    )
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=parameters"
    ).json()
    rows = body["record"]["parameters"]
    assert isinstance(rows, list) and len(rows) == 1
    row = rows[0]
    assert row["raw_key"] == "ScfConv"
    assert row["raw_value"] == "1e-8"
    assert row["canonical_key"] == "scf.convergence"
    assert row["canonical_value"] == "1e-8"
    assert row["section"] == "scf"
    assert row["parameter_index"] == 1
    # Phase D default: integer parameter_id stripped.
    assert "parameter_id" not in row


def test_detail_include_parameters_returns_empty_list_when_none(
    client, db_session
):
    _, _, calc = _make_species_owned_calc(db_session)
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}?include=parameters"
    ).json()
    assert body["record"]["parameters"] == []
    assert body["record"]["available_sections"]["has_parameters"] is False


def test_detail_include_parameters_internal_ids_when_allowed(
    client, db_session, allow_internal_ids
):
    _, _, calc = _make_species_owned_calc(db_session)
    row = _attach_calc_parameter(
        db_session, calculation=calc, raw_key="Grid", raw_value="ultrafine"
    )
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=parameters,internal_ids"
    ).json()
    assert body["record"]["parameters"][0]["parameter_id"] == row.id


def test_detail_include_parameters_ordering_is_deterministic(
    client, db_session
):
    """Order: section ASC NULLS LAST, parameter_index ASC NULLS LAST,
    raw_key ASC, id ASC."""
    _, _, calc = _make_species_owned_calc(db_session)
    # Insert out of order; check the response order matches the spec.
    _attach_calc_parameter(
        db_session,
        calculation=calc,
        raw_key="GridZ",
        raw_value="2",
        section="scf",
        parameter_index=2,
    )
    _attach_calc_parameter(
        db_session,
        calculation=calc,
        raw_key="GridA",
        raw_value="1",
        section="scf",
        parameter_index=1,
    )
    _attach_calc_parameter(
        db_session,
        calculation=calc,
        raw_key="OrphanKey",
        raw_value="x",
        section=None,
        parameter_index=None,
    )
    _attach_calc_parameter(
        db_session,
        calculation=calc,
        raw_key="RouteOption",
        raw_value="y",
        section="opt",
        parameter_index=1,
    )

    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=parameters"
    ).json()
    rows = body["record"]["parameters"]
    # Section sort: opt < scf alphabetically; null section last.
    sections = [r["section"] for r in rows]
    assert sections == ["opt", "scf", "scf", None]
    # Within section=scf, parameter_index 1 then 2.
    scf_rows = [r for r in rows if r["section"] == "scf"]
    assert [r["parameter_index"] for r in scf_rows] == [1, 2]
    assert [r["raw_key"] for r in scf_rows] == ["GridA", "GridZ"]


def test_available_sections_has_parameters_matches_include_result(
    client, db_session
):
    _, _, lonely = _make_species_owned_calc(db_session)
    body_lonely = client.get(
        f"/api/v1/scientific/calculations/{lonely.public_ref}"
        "?include=parameters"
    ).json()
    assert (
        body_lonely["record"]["available_sections"]["has_parameters"] is False
    )
    assert body_lonely["record"]["parameters"] == []

    _, _, with_p = _make_species_owned_calc(db_session)
    _attach_calc_parameter(
        db_session, calculation=with_p, raw_key="Grid", raw_value="ultrafine"
    )
    body_with = client.get(
        f"/api/v1/scientific/calculations/{with_p.public_ref}"
        "?include=parameters"
    ).json()
    assert body_with["record"]["available_sections"]["has_parameters"] is True
    assert len(body_with["record"]["parameters"]) == 1


def test_detail_include_parameters_combines_with_other_includes(
    client, db_session
):
    _, entry, calc = _make_species_owned_calc(
        db_session, calc_type=CalculationType.opt
    )
    attach_opt_result(
        db_session, calculation=calc, final_energy_hartree=-10.0
    )
    child = make_calculation(
        db_session, type=CalculationType.freq, species_entry_id=entry.id
    )
    attach_dependency(
        db_session,
        parent=calc,
        child=child,
        role=CalculationDependencyRole.freq_on,
    )
    attach_artifact(db_session, calculation=calc)
    g_in = make_geometry(db_session, natoms=3)
    g_out = make_geometry(db_session, natoms=3)
    _attach_input_geometry(
        db_session, calculation=calc, geometry=g_in, input_order=1
    )
    attach_output_geometry(
        db_session,
        calculation=calc,
        geometry=g_out,
        role=CalculationGeometryRole.final,
    )
    attach_geometry_validation(db_session, calculation=calc)
    attach_scf_stability(db_session, calculation=calc)
    _attach_calc_parameter(
        db_session, calculation=calc, raw_key="Grid", raw_value="ultrafine"
    )

    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=results,dependencies,artifacts,"
        "input_geometries,output_geometries,"
        "geometry_validation,scf_stability,parameters"
    ).json()
    record = body["record"]
    assert record["results"]["kind"] == "opt"
    assert len(record["dependencies"]) == 1
    assert len(record["artifacts"]) == 1
    assert len(record["input_geometries"]) == 1
    assert len(record["output_geometries"]) == 1
    assert len(record["geometry_validation"]) == 1
    assert len(record["scf_stability"]) == 1
    assert len(record["parameters"]) == 1


# ---------------------------------------------------------------------------
# include=constraints
# ---------------------------------------------------------------------------


def _attach_calc_constraint(
    db_session,
    *,
    calculation,
    constraint_index: int,
    constraint_kind: ConstraintKind,
    atom1_index: int,
    atom2_index: int | None = None,
    atom3_index: int | None = None,
    atom4_index: int | None = None,
    target_value: float | None = None,
):
    """Insert one ``calculation_constraint`` row.

    Arity is enforced by a CHECK constraint in the schema, so callers
    must supply the right number of atom indices for each kind:

    - cartesian_atom: 1 atom
    - bond:          2 atoms
    - angle:         3 atoms
    - dihedral/improper: 4 atoms
    """
    row = CalculationConstraint(
        calculation_id=calculation.id,
        constraint_index=constraint_index,
        constraint_kind=constraint_kind,
        atom1_index=atom1_index,
        atom2_index=atom2_index,
        atom3_index=atom3_index,
        atom4_index=atom4_index,
        target_value=target_value,
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_detail_constraints_omitted_when_not_requested(client, db_session):
    _, _, calc = _make_species_owned_calc(db_session)
    _attach_calc_constraint(
        db_session,
        calculation=calc,
        constraint_index=1,
        constraint_kind=ConstraintKind.bond,
        atom1_index=1,
        atom2_index=2,
    )
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
    ).json()
    assert "constraints" not in body["record"]


def test_detail_include_constraints_returns_summaries(client, db_session):
    _, _, calc = _make_species_owned_calc(db_session)
    _attach_calc_constraint(
        db_session,
        calculation=calc,
        constraint_index=1,
        constraint_kind=ConstraintKind.bond,
        atom1_index=1,
        atom2_index=2,
        target_value=1.42,
    )
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=constraints"
    ).json()
    rows = body["record"]["constraints"]
    assert isinstance(rows, list) and len(rows) == 1
    row = rows[0]
    assert row["constraint_index"] == 1
    assert row["constraint_kind"] == "bond"
    assert row["atom1_index"] == 1
    assert row["atom2_index"] == 2
    assert row["atom3_index"] is None
    assert row["atom4_index"] is None
    assert row["atom_indices"] == [1, 2]
    assert row["target_value"] == 1.42
    # Phase D default: integer calculation_id stripped.
    assert "calculation_id" not in row


def test_detail_include_constraints_returns_empty_list_when_none(
    client, db_session
):
    _, _, calc = _make_species_owned_calc(db_session)
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=constraints"
    ).json()
    assert body["record"]["constraints"] == []
    assert body["record"]["available_sections"]["has_constraints"] is False


def test_detail_constraints_constraint_index_visible_when_ids_disabled(
    client, db_session
):
    """``constraint_index`` is order metadata, not a DB id; it must
    survive Phase D ID stripping."""
    _, _, calc = _make_species_owned_calc(db_session)
    _attach_calc_constraint(
        db_session,
        calculation=calc,
        constraint_index=7,
        constraint_kind=ConstraintKind.bond,
        atom1_index=1,
        atom2_index=2,
    )
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=constraints"
    ).json()
    row = body["record"]["constraints"][0]
    assert row["constraint_index"] == 7
    # Phase D default: calculation_id absent, but constraint_index stays.
    assert "calculation_id" not in row


def test_detail_include_constraints_internal_ids_when_allowed(
    client, db_session, allow_internal_ids
):
    _, _, calc = _make_species_owned_calc(db_session)
    _attach_calc_constraint(
        db_session,
        calculation=calc,
        constraint_index=1,
        constraint_kind=ConstraintKind.bond,
        atom1_index=1,
        atom2_index=2,
    )
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=constraints,internal_ids"
    ).json()
    row = body["record"]["constraints"][0]
    assert row["calculation_id"] == calc.id


def test_detail_constraints_atom_indices_for_each_kind(client, db_session):
    """``atom_indices`` is the non-null atom-index slots in arity order
    for each ConstraintKind."""
    _, _, calc = _make_species_owned_calc(db_session)
    _attach_calc_constraint(
        db_session,
        calculation=calc,
        constraint_index=1,
        constraint_kind=ConstraintKind.cartesian_atom,
        atom1_index=5,
    )
    _attach_calc_constraint(
        db_session,
        calculation=calc,
        constraint_index=2,
        constraint_kind=ConstraintKind.bond,
        atom1_index=1,
        atom2_index=2,
    )
    _attach_calc_constraint(
        db_session,
        calculation=calc,
        constraint_index=3,
        constraint_kind=ConstraintKind.angle,
        atom1_index=1,
        atom2_index=2,
        atom3_index=3,
    )
    _attach_calc_constraint(
        db_session,
        calculation=calc,
        constraint_index=4,
        constraint_kind=ConstraintKind.dihedral,
        atom1_index=1,
        atom2_index=2,
        atom3_index=3,
        atom4_index=4,
    )
    _attach_calc_constraint(
        db_session,
        calculation=calc,
        constraint_index=5,
        constraint_kind=ConstraintKind.improper,
        atom1_index=4,
        atom2_index=3,
        atom3_index=2,
        atom4_index=1,
    )

    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=constraints"
    ).json()
    rows = body["record"]["constraints"]
    by_idx = {r["constraint_index"]: r for r in rows}
    assert by_idx[1]["atom_indices"] == [5]
    assert by_idx[2]["atom_indices"] == [1, 2]
    assert by_idx[3]["atom_indices"] == [1, 2, 3]
    assert by_idx[4]["atom_indices"] == [1, 2, 3, 4]
    assert by_idx[5]["atom_indices"] == [4, 3, 2, 1]


def test_detail_constraints_ordering_is_deterministic(client, db_session):
    """Order: constraint_index ASC, then kind/atom-tie-breakers."""
    _, _, calc = _make_species_owned_calc(db_session)
    _attach_calc_constraint(
        db_session,
        calculation=calc,
        constraint_index=3,
        constraint_kind=ConstraintKind.bond,
        atom1_index=1,
        atom2_index=2,
    )
    _attach_calc_constraint(
        db_session,
        calculation=calc,
        constraint_index=1,
        constraint_kind=ConstraintKind.bond,
        atom1_index=1,
        atom2_index=2,
    )
    _attach_calc_constraint(
        db_session,
        calculation=calc,
        constraint_index=2,
        constraint_kind=ConstraintKind.angle,
        atom1_index=1,
        atom2_index=2,
        atom3_index=3,
    )

    body_first = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=constraints"
    ).json()
    body_second = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=constraints"
    ).json()
    assert body_first["record"]["constraints"] == (
        body_second["record"]["constraints"]
    )
    indices = [r["constraint_index"] for r in body_first["record"]["constraints"]]
    assert indices == [1, 2, 3]


def test_available_sections_has_constraints_matches_include_result(
    client, db_session
):
    _, _, lonely = _make_species_owned_calc(db_session)
    body_lonely = client.get(
        f"/api/v1/scientific/calculations/{lonely.public_ref}"
        "?include=constraints"
    ).json()
    assert (
        body_lonely["record"]["available_sections"]["has_constraints"] is False
    )
    assert body_lonely["record"]["constraints"] == []

    _, _, with_c = _make_species_owned_calc(db_session)
    _attach_calc_constraint(
        db_session,
        calculation=with_c,
        constraint_index=1,
        constraint_kind=ConstraintKind.bond,
        atom1_index=1,
        atom2_index=2,
    )
    body_with = client.get(
        f"/api/v1/scientific/calculations/{with_c.public_ref}"
        "?include=constraints"
    ).json()
    assert (
        body_with["record"]["available_sections"]["has_constraints"] is True
    )
    assert len(body_with["record"]["constraints"]) == 1


def test_detail_include_constraints_combines_with_other_includes(
    client, db_session
):
    _, entry, calc = _make_species_owned_calc(
        db_session, calc_type=CalculationType.opt
    )
    attach_opt_result(
        db_session, calculation=calc, final_energy_hartree=-10.0
    )
    child = make_calculation(
        db_session, type=CalculationType.freq, species_entry_id=entry.id
    )
    attach_dependency(
        db_session,
        parent=calc,
        child=child,
        role=CalculationDependencyRole.freq_on,
    )
    attach_artifact(db_session, calculation=calc)
    g_in = make_geometry(db_session, natoms=3)
    g_out = make_geometry(db_session, natoms=3)
    _attach_input_geometry(
        db_session, calculation=calc, geometry=g_in, input_order=1
    )
    attach_output_geometry(
        db_session,
        calculation=calc,
        geometry=g_out,
        role=CalculationGeometryRole.final,
    )
    attach_geometry_validation(db_session, calculation=calc)
    attach_scf_stability(db_session, calculation=calc)
    _attach_calc_parameter(
        db_session, calculation=calc, raw_key="Grid", raw_value="ultrafine"
    )
    _attach_calc_constraint(
        db_session,
        calculation=calc,
        constraint_index=1,
        constraint_kind=ConstraintKind.bond,
        atom1_index=1,
        atom2_index=2,
    )

    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=results,dependencies,artifacts,"
        "input_geometries,output_geometries,"
        "geometry_validation,scf_stability,parameters,constraints"
    ).json()
    record = body["record"]
    assert record["results"]["kind"] == "opt"
    assert len(record["dependencies"]) == 1
    assert len(record["artifacts"]) == 1
    assert len(record["input_geometries"]) == 1
    assert len(record["output_geometries"]) == 1
    assert len(record["geometry_validation"]) == 1
    assert len(record["scf_stability"]) == 1
    assert len(record["parameters"]) == 1
    assert len(record["constraints"]) == 1




# ---------------------------------------------------------------------------
# include=review
# ---------------------------------------------------------------------------


def test_detail_review_history_omitted_when_not_requested(
    client, db_session
):
    _, _, calc = _make_species_owned_calc(db_session)
    set_review(
        db_session,
        record_type=SubmissionRecordType.calculation,
        record_id=calc.id,
        status=RecordReviewStatus.approved,
    )
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
    ).json()
    assert "review_history" not in body["record"]


def test_detail_include_review_returns_entry(client, db_session):
    _, _, calc = _make_species_owned_calc(db_session)
    review = set_review(
        db_session,
        record_type=SubmissionRecordType.calculation,
        record_id=calc.id,
        status=RecordReviewStatus.approved,
    )
    review.note = "looks good"
    db_session.flush()

    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=review"
    ).json()
    rows = body["record"]["review_history"]
    assert isinstance(rows, list) and len(rows) == 1
    entry = rows[0]
    assert entry["status"] == "approved"
    assert entry["note"] == "looks good"
    assert entry["reviewed_at"] is not None
    # No submission link → submission_ref null.
    assert entry["submission_ref"] is None
    # Phase D default: integer ids stripped.
    assert "review_id" not in entry
    assert "reviewer_id" not in entry
    assert "submission_id" not in entry


def test_detail_include_review_returns_empty_list_when_no_row(
    client, db_session
):
    _, _, calc = _make_species_owned_calc(db_session)
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}?include=review"
    ).json()
    assert body["record"]["review_history"] == []


def test_detail_include_review_keeps_compact_badge(client, db_session):
    """The compact ``review`` badge inside ``calculation`` must remain
    present whether or not ``include=review`` is supplied."""
    _, _, calc = _make_species_owned_calc(db_session)
    set_review(
        db_session,
        record_type=SubmissionRecordType.calculation,
        record_id=calc.id,
        status=RecordReviewStatus.approved,
    )
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}?include=review"
    ).json()
    badge = body["record"]["calculation"]["review"]
    assert badge["status"] == "approved"
    assert badge["reviewer_kind"] == "human"
    # And the expanded entry is also present.
    assert len(body["record"]["review_history"]) == 1


def test_detail_include_review_internal_ids_when_allowed(
    client, db_session, allow_internal_ids
):
    _, _, calc = _make_species_owned_calc(db_session)
    review = set_review(
        db_session,
        record_type=SubmissionRecordType.calculation,
        record_id=calc.id,
        status=RecordReviewStatus.approved,
    )
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=review,internal_ids"
    ).json()
    entry = body["record"]["review_history"][0]
    assert entry["review_id"] == review.id
    assert entry["reviewer_id"] == review.reviewed_by


def test_detail_include_review_ordering_is_deterministic(
    client, db_session
):
    """The schema constrains at most one review row per record so the
    list is singleton; this test still exercises the deterministic-call
    invariant required by the spec."""
    _, _, calc = _make_species_owned_calc(db_session)
    set_review(
        db_session,
        record_type=SubmissionRecordType.calculation,
        record_id=calc.id,
        status=RecordReviewStatus.approved,
    )
    body_first = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}?include=review"
    ).json()
    body_second = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}?include=review"
    ).json()
    assert body_first["record"]["review_history"] == (
        body_second["record"]["review_history"]
    )


def test_detail_include_review_combines_with_other_includes(
    client, db_session
):
    _, entry, calc = _make_species_owned_calc(
        db_session, calc_type=CalculationType.opt
    )
    attach_opt_result(
        db_session, calculation=calc, final_energy_hartree=-10.0
    )
    child = make_calculation(
        db_session, type=CalculationType.freq, species_entry_id=entry.id
    )
    attach_dependency(
        db_session,
        parent=calc,
        child=child,
        role=CalculationDependencyRole.freq_on,
    )
    attach_artifact(db_session, calculation=calc)
    g_in = make_geometry(db_session, natoms=3)
    g_out = make_geometry(db_session, natoms=3)
    _attach_input_geometry(
        db_session, calculation=calc, geometry=g_in, input_order=1
    )
    attach_output_geometry(
        db_session,
        calculation=calc,
        geometry=g_out,
        role=CalculationGeometryRole.final,
    )
    attach_geometry_validation(db_session, calculation=calc)
    attach_scf_stability(db_session, calculation=calc)
    _attach_calc_parameter(
        db_session, calculation=calc, raw_key="Grid", raw_value="ultrafine"
    )
    _attach_calc_constraint(
        db_session,
        calculation=calc,
        constraint_index=1,
        constraint_kind=ConstraintKind.bond,
        atom1_index=1,
        atom2_index=2,
    )
    set_review(
        db_session,
        record_type=SubmissionRecordType.calculation,
        record_id=calc.id,
        status=RecordReviewStatus.approved,
    )

    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=results,dependencies,artifacts,"
        "input_geometries,output_geometries,"
        "geometry_validation,scf_stability,"
        "parameters,constraints,review"
    ).json()
    record = body["record"]
    assert record["results"]["kind"] == "opt"
    assert len(record["dependencies"]) == 1
    assert len(record["artifacts"]) == 1
    assert len(record["input_geometries"]) == 1
    assert len(record["output_geometries"]) == 1
    assert len(record["geometry_validation"]) == 1
    assert len(record["scf_stability"]) == 1
    assert len(record["parameters"]) == 1
    assert len(record["constraints"]) == 1
    assert len(record["review_history"]) == 1




# ---------------------------------------------------------------------------
# include=scan
# ---------------------------------------------------------------------------


def _make_scan_calc(db_session, *, dimension: int = 1, is_relaxed: bool = True):
    """Create a scan-type calc + scan-result row."""
    _, entry, calc = _make_species_owned_calc(
        db_session, calc_type=CalculationType.scan
    )
    db_session.add(
        CalculationScanResult(
            calculation_id=calc.id,
            dimension=dimension,
            is_relaxed=is_relaxed,
            zero_energy_reference_hartree=-100.0,
            note="scanning",
        )
    )
    db_session.flush()
    return entry, calc


def _attach_scan_coordinate(
    db_session,
    *,
    calculation,
    coordinate_index: int,
    coordinate_kind: ScanCoordinateKind,
    atom1_index: int,
    atom2_index: int,
    atom3_index: int | None = None,
    atom4_index: int | None = None,
    step_count: int | None = None,
    step_size: float | None = None,
    start_value: float | None = None,
    end_value: float | None = None,
    value_unit: CoordinateUnit | None = None,
):
    row = CalculationScanCoordinate(
        calculation_id=calculation.id,
        coordinate_index=coordinate_index,
        coordinate_kind=coordinate_kind,
        atom1_index=atom1_index,
        atom2_index=atom2_index,
        atom3_index=atom3_index,
        atom4_index=atom4_index,
        step_count=step_count,
        step_size=step_size,
        start_value=start_value,
        end_value=end_value,
        value_unit=value_unit,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _attach_scan_point(
    db_session,
    *,
    calculation,
    point_index: int,
    electronic_energy_hartree: float | None = None,
    relative_energy_kj_mol: float | None = None,
):
    row = CalculationScanPoint(
        calculation_id=calculation.id,
        point_index=point_index,
        electronic_energy_hartree=electronic_energy_hartree,
        relative_energy_kj_mol=relative_energy_kj_mol,
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_detail_scan_omitted_when_not_requested(client, db_session):
    _, calc = _make_scan_calc(db_session)
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
    ).json()
    assert "scan" not in body["record"]


def test_detail_include_scan_returns_summary(client, db_session):
    _, calc = _make_scan_calc(db_session, dimension=1)
    _attach_scan_coordinate(
        db_session,
        calculation=calc,
        coordinate_index=1,
        coordinate_kind=ScanCoordinateKind.bond,
        atom1_index=1,
        atom2_index=2,
        step_count=5,
        step_size=0.1,
        start_value=0.8,
        end_value=1.3,
        value_unit=CoordinateUnit.angstrom,
    )
    _attach_scan_point(
        db_session, calculation=calc, point_index=1,
        electronic_energy_hartree=-99.5, relative_energy_kj_mol=10.0,
    )
    _attach_scan_point(
        db_session, calculation=calc, point_index=2,
        electronic_energy_hartree=-99.8, relative_energy_kj_mol=2.0,
    )
    _attach_scan_point(
        db_session, calculation=calc, point_index=3,
        electronic_energy_hartree=-99.6, relative_energy_kj_mol=8.0,
    )

    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}?include=scan"
    ).json()
    scan = body["record"]["scan"]
    assert scan is not None
    assert scan["dimension"] == 1
    assert scan["is_relaxed"] is True
    assert scan["zero_energy_reference_hartree"] == -100.0
    assert scan["note"] == "scanning"
    assert scan["coordinate_count"] == 1
    assert scan["point_count"] == 3
    assert scan["min_electronic_energy_hartree"] == -99.8
    assert scan["max_electronic_energy_hartree"] == -99.5
    assert scan["min_relative_energy_kj_mol"] == 2.0
    assert scan["max_relative_energy_kj_mol"] == 10.0
    assert len(scan["coordinates"]) == 1
    coord = scan["coordinates"][0]
    assert coord["coordinate_index"] == 1
    assert coord["coordinate_kind"] == "bond"
    assert coord["atom1_index"] == 1
    assert coord["atom2_index"] == 2
    assert coord["atom_indices"] == [1, 2]
    assert coord["step_count"] == 5
    assert coord["step_size"] == 0.1
    assert coord["start_value"] == 0.8
    assert coord["end_value"] == 1.3
    assert coord["value_unit"] == "angstrom"


def test_detail_include_scan_returns_null_when_no_result_row(
    client, db_session
):
    _, _, calc = _make_species_owned_calc(
        db_session, calc_type=CalculationType.scan
    )
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}?include=scan"
    ).json()
    assert body["record"]["scan"] is None


def test_detail_scan_summary_atom_indices_for_each_kind(client, db_session):
    """``atom_indices`` is the non-null atom-index slots in arity order
    for each ScanCoordinateKind."""
    _, calc = _make_scan_calc(db_session, dimension=3)
    _attach_scan_coordinate(
        db_session,
        calculation=calc,
        coordinate_index=1,
        coordinate_kind=ScanCoordinateKind.bond,
        atom1_index=1,
        atom2_index=2,
    )
    _attach_scan_coordinate(
        db_session,
        calculation=calc,
        coordinate_index=2,
        coordinate_kind=ScanCoordinateKind.angle,
        atom1_index=1,
        atom2_index=2,
        atom3_index=3,
    )
    _attach_scan_coordinate(
        db_session,
        calculation=calc,
        coordinate_index=3,
        coordinate_kind=ScanCoordinateKind.dihedral,
        atom1_index=1,
        atom2_index=2,
        atom3_index=3,
        atom4_index=4,
    )
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}?include=scan"
    ).json()
    coords = body["record"]["scan"]["coordinates"]
    by_idx = {c["coordinate_index"]: c for c in coords}
    assert by_idx[1]["atom_indices"] == [1, 2]
    assert by_idx[2]["atom_indices"] == [1, 2, 3]
    assert by_idx[3]["atom_indices"] == [1, 2, 3, 4]


def test_detail_scan_coordinate_ordering_is_deterministic(client, db_session):
    """Coordinates sort by coordinate_index ASC."""
    _, calc = _make_scan_calc(db_session, dimension=3)
    # Insert out of order.
    for ci in (3, 1, 2):
        _attach_scan_coordinate(
            db_session,
            calculation=calc,
            coordinate_index=ci,
            coordinate_kind=ScanCoordinateKind.bond,
            atom1_index=1,
            atom2_index=2,
        )
    body_first = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}?include=scan"
    ).json()
    body_second = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}?include=scan"
    ).json()
    assert body_first["record"]["scan"] == body_second["record"]["scan"]
    indices = [c["coordinate_index"] for c in body_first["record"]["scan"]["coordinates"]]
    assert indices == [1, 2, 3]


def test_detail_scan_summary_does_not_expose_point_arrays(client, db_session):
    """Defense-in-depth: the summary must not carry per-point arrays
    or coordinate-value rows. Those belong to the future
    /scientific/calculations/{handle}/scan endpoint."""
    _, calc = _make_scan_calc(db_session)
    _attach_scan_coordinate(
        db_session,
        calculation=calc,
        coordinate_index=1,
        coordinate_kind=ScanCoordinateKind.bond,
        atom1_index=1,
        atom2_index=2,
    )
    _attach_scan_point(
        db_session, calculation=calc, point_index=1,
        electronic_energy_hartree=-99.0,
    )
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}?include=scan"
    ).json()
    legal_keys = {
        "dimension",
        "is_relaxed",
        "zero_energy_reference_hartree",
        "note",
        "coordinate_count",
        "point_count",
        "coordinates",
        "min_electronic_energy_hartree",
        "max_electronic_energy_hartree",
        "min_relative_energy_kj_mol",
        "max_relative_energy_kj_mol",
    }
    scan = body["record"]["scan"]
    assert set(scan.keys()).issubset(legal_keys)
    for forbidden in (
        "points",
        "scan_points",
        "point_coordinate_values",
        "geometry_id",
        "geometry_ref",
        "atoms",
        "coords",
        "xyz_text",
    ):
        assert forbidden not in scan


def test_detail_include_scan_combines_with_other_includes(client, db_session):
    _, calc = _make_scan_calc(db_session)
    _attach_scan_coordinate(
        db_session,
        calculation=calc,
        coordinate_index=1,
        coordinate_kind=ScanCoordinateKind.bond,
        atom1_index=1,
        atom2_index=2,
    )
    _attach_scan_point(
        db_session, calculation=calc, point_index=1,
        electronic_energy_hartree=-99.0,
    )
    attach_artifact(db_session, calculation=calc)
    _attach_calc_parameter(
        db_session, calculation=calc, raw_key="MaxStep", raw_value="0.1"
    )
    set_review(
        db_session,
        record_type=SubmissionRecordType.calculation,
        record_id=calc.id,
        status=RecordReviewStatus.approved,
    )

    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=scan,artifacts,parameters,review"
    ).json()
    record = body["record"]
    assert record["scan"]["coordinate_count"] == 1
    assert len(record["artifacts"]) == 1
    assert len(record["parameters"]) == 1
    assert len(record["review_history"]) == 1




# ---------------------------------------------------------------------------
# include=irc
# ---------------------------------------------------------------------------


def _make_irc_calc(
    db_session,
    *,
    direction: IRCDirection = IRCDirection.both,
    has_forward: bool = True,
    has_reverse: bool = True,
    ts_point_index: int | None = 0,
    point_count: int | None = None,
    note: str | None = None,
):
    """Create an IRC-type calc + IRC-result row."""
    _, entry, calc = _make_species_owned_calc(
        db_session, calc_type=CalculationType.irc
    )
    db_session.add(
        CalculationIRCResult(
            calculation_id=calc.id,
            direction=direction,
            has_forward=has_forward,
            has_reverse=has_reverse,
            ts_point_index=ts_point_index,
            point_count=point_count,
            zero_energy_reference_hartree=-100.0,
            note=note,
        )
    )
    db_session.flush()
    return entry, calc


def _attach_irc_point(
    db_session,
    *,
    calculation,
    point_index: int,
    direction: IRCDirection | None = None,
    is_ts: bool = False,
    reaction_coordinate: float | None = None,
    electronic_energy_hartree: float | None = None,
    relative_energy_kj_mol: float | None = None,
):
    row = CalculationIRCPoint(
        calculation_id=calculation.id,
        point_index=point_index,
        direction=direction,
        is_ts=is_ts,
        reaction_coordinate=reaction_coordinate,
        electronic_energy_hartree=electronic_energy_hartree,
        relative_energy_kj_mol=relative_energy_kj_mol,
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_detail_irc_omitted_when_not_requested(client, db_session):
    _, calc = _make_irc_calc(db_session)
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
    ).json()
    assert "irc" not in body["record"]


def test_detail_include_irc_returns_summary(client, db_session):
    _, calc = _make_irc_calc(
        db_session,
        direction=IRCDirection.both,
        has_forward=True,
        has_reverse=True,
        ts_point_index=0,
        point_count=5,
        note="orca bidirectional",
    )
    # 1 TS marker (no direction), 2 forward, 2 reverse — 5 total.
    _attach_irc_point(
        db_session, calculation=calc, point_index=0,
        direction=None, is_ts=True,
        reaction_coordinate=0.0, electronic_energy_hartree=-99.5,
        relative_energy_kj_mol=20.0,
    )
    _attach_irc_point(
        db_session, calculation=calc, point_index=1,
        direction=IRCDirection.forward,
        reaction_coordinate=0.5, electronic_energy_hartree=-99.7,
        relative_energy_kj_mol=15.0,
    )
    _attach_irc_point(
        db_session, calculation=calc, point_index=2,
        direction=IRCDirection.forward,
        reaction_coordinate=1.0, electronic_energy_hartree=-99.9,
        relative_energy_kj_mol=5.0,
    )
    _attach_irc_point(
        db_session, calculation=calc, point_index=3,
        direction=IRCDirection.reverse,
        reaction_coordinate=-0.5, electronic_energy_hartree=-99.6,
        relative_energy_kj_mol=18.0,
    )
    _attach_irc_point(
        db_session, calculation=calc, point_index=4,
        direction=IRCDirection.reverse,
        reaction_coordinate=-1.0, electronic_energy_hartree=-100.1,
        relative_energy_kj_mol=0.0,
    )

    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}?include=irc"
    ).json()
    irc = body["record"]["irc"]
    assert irc is not None
    assert irc["direction"] == "both"
    assert irc["has_forward"] is True
    assert irc["has_reverse"] is True
    assert irc["ts_point_index"] == 0
    assert irc["point_count"] == 5
    assert irc["zero_energy_reference_hartree"] == -100.0
    assert irc["note"] == "orca bidirectional"
    # Direction counting: 2 fwd + 2 rev; the direction=NULL TS marker
    # is intentionally NOT double-counted.
    assert irc["forward_point_count"] == 2
    assert irc["reverse_point_count"] == 2
    assert irc["ts_point_count"] == 1
    # Energy and reaction-coordinate envelopes.
    assert irc["min_electronic_energy_hartree"] == -100.1
    assert irc["max_electronic_energy_hartree"] == -99.5
    assert irc["min_relative_energy_kj_mol"] == 0.0
    assert irc["max_relative_energy_kj_mol"] == 20.0
    assert irc["min_reaction_coordinate"] == -1.0
    assert irc["max_reaction_coordinate"] == 1.0


def test_detail_include_irc_returns_null_when_no_result_row(
    client, db_session
):
    _, _, calc = _make_species_owned_calc(
        db_session, calc_type=CalculationType.irc
    )
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}?include=irc"
    ).json()
    assert body["record"]["irc"] is None


def test_detail_include_irc_directional_counts_forward_only(
    client, db_session
):
    """Gaussian-style single-direction IRC: every point has
    ``direction=forward``, no ``direction=both``."""
    _, calc = _make_irc_calc(
        db_session,
        direction=IRCDirection.forward,
        has_forward=True,
        has_reverse=False,
    )
    for i in range(3):
        _attach_irc_point(
            db_session, calculation=calc, point_index=i,
            direction=IRCDirection.forward,
            electronic_energy_hartree=-99.0 - i * 0.1,
        )
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}?include=irc"
    ).json()
    irc = body["record"]["irc"]
    assert irc["forward_point_count"] == 3
    assert irc["reverse_point_count"] == 0
    assert irc["ts_point_count"] == 0


def test_detail_include_irc_direction_both_rows_not_double_counted(
    client, db_session
):
    """Defense: a row with ``direction=both`` does not count toward
    ``forward_point_count`` or ``reverse_point_count``."""
    _, calc = _make_irc_calc(db_session, direction=IRCDirection.both)
    _attach_irc_point(
        db_session, calculation=calc, point_index=0,
        direction=IRCDirection.both,
        electronic_energy_hartree=-99.0,
    )
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}?include=irc"
    ).json()
    irc = body["record"]["irc"]
    assert irc["forward_point_count"] == 0
    assert irc["reverse_point_count"] == 0


def test_detail_include_irc_with_no_points_returns_zero_counts(
    client, db_session
):
    """Result row exists but no IRC points: counts are 0, energy/RC
    envelopes are null."""
    _, calc = _make_irc_calc(db_session, direction=IRCDirection.forward)
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}?include=irc"
    ).json()
    irc = body["record"]["irc"]
    assert irc["forward_point_count"] == 0
    assert irc["reverse_point_count"] == 0
    assert irc["ts_point_count"] == 0
    assert irc["min_electronic_energy_hartree"] is None
    assert irc["max_electronic_energy_hartree"] is None
    assert irc["min_reaction_coordinate"] is None
    assert irc["max_reaction_coordinate"] is None


def test_detail_irc_summary_does_not_expose_point_arrays(client, db_session):
    """Defense-in-depth: the summary must not carry per-point arrays
    or per-point geometry refs. Those belong to the future
    /scientific/calculations/{handle}/irc endpoint."""
    _, calc = _make_irc_calc(db_session)
    _attach_irc_point(
        db_session, calculation=calc, point_index=0,
        direction=IRCDirection.forward,
        electronic_energy_hartree=-99.0,
    )
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}?include=irc"
    ).json()
    legal_keys = {
        "direction",
        "has_forward",
        "has_reverse",
        "ts_point_index",
        "point_count",
        "zero_energy_reference_hartree",
        "note",
        "forward_point_count",
        "reverse_point_count",
        "ts_point_count",
        "min_electronic_energy_hartree",
        "max_electronic_energy_hartree",
        "min_relative_energy_kj_mol",
        "max_relative_energy_kj_mol",
        "min_reaction_coordinate",
        "max_reaction_coordinate",
    }
    irc = body["record"]["irc"]
    assert set(irc.keys()).issubset(legal_keys)
    for forbidden in (
        "points",
        "irc_points",
        "geometry_id",
        "geometry_ref",
        "atoms",
        "coords",
        "xyz_text",
        "reaction_coordinates",
    ):
        assert forbidden not in irc


def test_detail_include_irc_combines_with_other_includes(client, db_session):
    _, calc = _make_irc_calc(db_session)
    _attach_irc_point(
        db_session, calculation=calc, point_index=0,
        direction=IRCDirection.forward,
        electronic_energy_hartree=-99.0,
    )
    attach_artifact(db_session, calculation=calc)
    set_review(
        db_session,
        record_type=SubmissionRecordType.calculation,
        record_id=calc.id,
        status=RecordReviewStatus.approved,
    )
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=irc,artifacts,review"
    ).json()
    record = body["record"]
    assert record["irc"]["forward_point_count"] == 1
    assert len(record["artifacts"]) == 1
    assert len(record["review_history"]) == 1


def test_detail_include_scan_and_irc_combined(client, db_session):
    """``include=scan,irc`` populates both blocks for a hypothetical
    calc (in practice they'd be on different calcs, but the contract
    is the include-set is per request)."""
    _, calc = _make_irc_calc(db_session)
    # Manually attach a scan-result row to the same calc to exercise
    # the dual-include contract.
    db_session.add(
        CalculationScanResult(
            calculation_id=calc.id,
            dimension=1,
            is_relaxed=True,
        )
    )
    db_session.flush()
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}?include=scan,irc"
    ).json()
    assert body["record"]["scan"] is not None
    assert body["record"]["irc"] is not None




# ---------------------------------------------------------------------------
# include=path_search
# ---------------------------------------------------------------------------


def _make_path_search_calc(
    db_session,
    *,
    method: PathSearchMethod = PathSearchMethod.neb,
    is_double_ended: bool = True,
    converged: bool = True,
    n_points: int | None = 5,
    selected_ts_point_index: int | None = 2,
    climbing_image_index: int | None = 2,
    note: str | None = None,
):
    """Create a path-search-type calc + path-search-result row."""
    _, entry, calc = _make_species_owned_calc(
        db_session, calc_type=CalculationType.path_search
    )
    db_session.add(
        CalculationPathSearchResult(
            calculation_id=calc.id,
            method=method,
            is_double_ended=is_double_ended,
            converged=converged,
            n_points=n_points,
            selected_ts_point_index=selected_ts_point_index,
            climbing_image_index=climbing_image_index,
            source_endpoint_count=2 if is_double_ended else 1,
            zero_energy_reference_hartree=-100.0,
            note=note,
        )
    )
    db_session.flush()
    return entry, calc


def _attach_path_search_point(
    db_session,
    *,
    calculation,
    point_index: int,
    electronic_energy_hartree: float | None = None,
    relative_energy_kj_mol: float | None = None,
    path_coordinate: float | None = None,
    is_ts_guess: bool = False,
    is_climbing_image: bool = False,
):
    row = CalculationPathSearchPoint(
        calculation_id=calculation.id,
        point_index=point_index,
        electronic_energy_hartree=electronic_energy_hartree,
        relative_energy_kj_mol=relative_energy_kj_mol,
        path_coordinate=path_coordinate,
        is_ts_guess=is_ts_guess,
        is_climbing_image=is_climbing_image,
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_detail_path_search_omitted_when_not_requested(client, db_session):
    _, calc = _make_path_search_calc(db_session)
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
    ).json()
    assert "path_search" not in body["record"]


def test_detail_include_path_search_returns_summary(client, db_session):
    _, calc = _make_path_search_calc(
        db_session, method=PathSearchMethod.neb,
        n_points=5, selected_ts_point_index=2, climbing_image_index=2,
        note="neb climbing-image",
    )
    # 5 NEB images: endpoints (0, 4), interior (1, 3), climbing image (2).
    _attach_path_search_point(
        db_session, calculation=calc, point_index=0,
        electronic_energy_hartree=-100.0, relative_energy_kj_mol=0.0,
        path_coordinate=0.0,
    )
    _attach_path_search_point(
        db_session, calculation=calc, point_index=1,
        electronic_energy_hartree=-99.5, relative_energy_kj_mol=13.0,
        path_coordinate=0.25,
    )
    _attach_path_search_point(
        db_session, calculation=calc, point_index=2,
        electronic_energy_hartree=-99.2, relative_energy_kj_mol=21.0,
        path_coordinate=0.5,
        is_ts_guess=True, is_climbing_image=True,
    )
    _attach_path_search_point(
        db_session, calculation=calc, point_index=3,
        electronic_energy_hartree=-99.6, relative_energy_kj_mol=10.0,
        path_coordinate=0.75,
    )
    _attach_path_search_point(
        db_session, calculation=calc, point_index=4,
        electronic_energy_hartree=-100.2, relative_energy_kj_mol=-5.0,
        path_coordinate=1.0,
    )

    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=path_search"
    ).json()
    ps = body["record"]["path_search"]
    assert ps is not None
    assert ps["method"] == "neb"
    assert ps["is_double_ended"] is True
    assert ps["converged"] is True
    assert ps["n_points"] == 5
    assert ps["selected_ts_point_index"] == 2
    assert ps["climbing_image_index"] == 2
    assert ps["source_endpoint_count"] == 2
    assert ps["zero_energy_reference_hartree"] == -100.0
    assert ps["note"] == "neb climbing-image"
    # Aggregate counts.
    assert ps["stored_point_count"] == 5
    assert ps["ts_guess_count"] == 1
    assert ps["climbing_image_count"] == 1
    # Energy + path-coordinate envelopes.
    assert ps["min_electronic_energy_hartree"] == -100.2
    assert ps["max_electronic_energy_hartree"] == -99.2
    assert ps["min_relative_energy_kj_mol"] == -5.0
    assert ps["max_relative_energy_kj_mol"] == 21.0
    assert ps["min_path_coordinate"] == 0.0
    assert ps["max_path_coordinate"] == 1.0


def test_detail_include_path_search_returns_null_when_no_result_row(
    client, db_session
):
    _, _, calc = _make_species_owned_calc(
        db_session, calc_type=CalculationType.path_search
    )
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=path_search"
    ).json()
    assert body["record"]["path_search"] is None


def test_detail_path_search_summary_separate_marker_counts(client, db_session):
    """Defends the design: ``ts_guess_count`` and
    ``climbing_image_count`` are separate fields. A point can be
    flagged with either, both, or neither, and each count tracks its
    own marker independently."""
    _, calc = _make_path_search_calc(
        db_session, method=PathSearchMethod.neb,
    )
    # ts_guess only.
    _attach_path_search_point(
        db_session, calculation=calc, point_index=0,
        is_ts_guess=True, is_climbing_image=False,
    )
    # climbing only.
    _attach_path_search_point(
        db_session, calculation=calc, point_index=1,
        is_ts_guess=False, is_climbing_image=True,
    )
    # both.
    _attach_path_search_point(
        db_session, calculation=calc, point_index=2,
        is_ts_guess=True, is_climbing_image=True,
    )
    # neither.
    _attach_path_search_point(
        db_session, calculation=calc, point_index=3,
        is_ts_guess=False, is_climbing_image=False,
    )

    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=path_search"
    ).json()
    ps = body["record"]["path_search"]
    # 2 rows have is_ts_guess=True (indices 0 and 2).
    assert ps["ts_guess_count"] == 2
    # 2 rows have is_climbing_image=True (indices 1 and 2).
    assert ps["climbing_image_count"] == 2
    assert ps["stored_point_count"] == 4


def test_detail_include_path_search_with_no_points_returns_zero_counts(
    client, db_session
):
    """Result row exists but no path-search points: counts are 0,
    energy/path-coordinate envelopes are null."""
    _, calc = _make_path_search_calc(db_session)
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=path_search"
    ).json()
    ps = body["record"]["path_search"]
    assert ps["stored_point_count"] == 0
    assert ps["ts_guess_count"] == 0
    assert ps["climbing_image_count"] == 0
    assert ps["min_electronic_energy_hartree"] is None
    assert ps["max_electronic_energy_hartree"] is None
    assert ps["min_path_coordinate"] is None
    assert ps["max_path_coordinate"] is None


def test_detail_path_search_summary_does_not_expose_point_arrays(
    client, db_session
):
    """Defense-in-depth: the summary must not carry per-point arrays
    or per-point geometry refs."""
    _, calc = _make_path_search_calc(db_session)
    _attach_path_search_point(
        db_session, calculation=calc, point_index=0,
        electronic_energy_hartree=-99.0,
    )
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=path_search"
    ).json()
    legal_keys = {
        "method",
        "is_double_ended",
        "converged",
        "n_points",
        "selected_ts_point_index",
        "climbing_image_index",
        "source_endpoint_count",
        "zero_energy_reference_hartree",
        "note",
        "stored_point_count",
        "ts_guess_count",
        "climbing_image_count",
        "min_electronic_energy_hartree",
        "max_electronic_energy_hartree",
        "min_relative_energy_kj_mol",
        "max_relative_energy_kj_mol",
        "min_path_coordinate",
        "max_path_coordinate",
    }
    ps = body["record"]["path_search"]
    assert set(ps.keys()).issubset(legal_keys)
    for forbidden in (
        "points",
        "path_search_points",
        "geometry_id",
        "geometry_ref",
        "atoms",
        "coords",
        "xyz_text",
        "path_coordinates",
    ):
        assert forbidden not in ps


def test_detail_include_path_search_combines_with_other_includes(
    client, db_session
):
    _, calc = _make_path_search_calc(db_session)
    _attach_path_search_point(
        db_session, calculation=calc, point_index=0,
        electronic_energy_hartree=-99.0,
        is_ts_guess=True,
    )
    attach_artifact(db_session, calculation=calc)
    set_review(
        db_session,
        record_type=SubmissionRecordType.calculation,
        record_id=calc.id,
        status=RecordReviewStatus.approved,
    )

    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=path_search,artifacts,review"
    ).json()
    record = body["record"]
    assert record["path_search"]["ts_guess_count"] == 1
    assert len(record["artifacts"]) == 1
    assert len(record["review_history"]) == 1


def test_detail_include_scan_irc_path_search_combined(client, db_session):
    """``include=scan,irc,path_search`` populates all three blocks."""
    _, calc = _make_path_search_calc(db_session)
    # Attach scan + IRC result rows on the same calc to exercise
    # the dual-include contract for all three trajectory types.
    db_session.add(
        CalculationScanResult(
            calculation_id=calc.id, dimension=1, is_relaxed=True,
        )
    )
    db_session.add(
        CalculationIRCResult(
            calculation_id=calc.id,
            direction=IRCDirection.forward,
            has_forward=True,
            has_reverse=False,
        )
    )
    db_session.flush()
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=scan,irc,path_search"
    ).json()
    assert body["record"]["scan"] is not None
    assert body["record"]["irc"] is not None
    assert body["record"]["path_search"] is not None


# ---------------------------------------------------------------------------
# include=all
# ---------------------------------------------------------------------------


# The set ``include=all`` resolves to (every public heavy include
# token, internal_ids excluded). Kept in the test file so a future
# include addition doesn't silently change ``include=all``'s expansion
# without updating this list.
_ALL_EXPANSION_TOKENS = {
    "results",
    "dependencies",
    "artifacts",
    "input_geometries",
    "output_geometries",
    "geometry_validation",
    "scf_stability",
    "wavefunction_diagnostic",
    "spin_diagnostic",
    "parameters",
    "constraints",
    "review",
    "freq_modes",
    "scan",
    "irc",
    "path_search",
}


def test_detail_include_all_returns_200_with_summary_blocks(
    client, db_session
):
    """``include=all`` expands to every public heavy token and
    populates each summary slot. Per-token absent/empty semantics are
    preserved (e.g. ``scan`` is null on a non-scan calc)."""
    _, _, calc = _make_species_owned_calc(db_session)
    resp = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}?include=all"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # ``request.include`` echoes the resolved expansion (alphabetical
    # because the route sorts the resolved set deterministically).
    assert set(body["request"]["include"]) == _ALL_EXPANSION_TOKENS
    assert "internal_ids" not in body["request"]["include"]
    # Every advertised include slot is present in the record (the
    # route's omittable map drops only what wasn't requested).
    record = body["record"]
    for record_key in (
        "results",
        "dependencies",
        "artifacts",
        "input_geometries",
        "output_geometries",
        "geometry_validation",
        "scf_stability",
        "wavefunction_diagnostic",
        "spin_diagnostic",
        "parameters",
        "constraints",
        "review_history",
        "freq_modes",
        "scan",
        "irc",
        "path_search",
    ):
        assert record_key in record


def test_detail_include_all_does_not_restore_internal_ids_by_default(
    client, db_session
):
    _, _, calc = _make_species_owned_calc(db_session)
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}?include=all"
    ).json()
    assert "internal_ids" not in body["request"]["include"]
    assert "calculation_id" not in body["record"]["calculation"]


def test_detail_include_all_with_internal_ids_when_allowed(
    client, db_session, allow_internal_ids
):
    """``include=all,internal_ids`` restores integer ids when the
    deployment policy permits it. ``all`` itself never expands to
    ``internal_ids`` — the explicit token must be supplied."""
    _, _, calc = _make_species_owned_calc(db_session)
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=all,internal_ids"
    ).json()
    assert "internal_ids" in body["request"]["include"]
    assert body["record"]["calculation"]["calculation_id"] == calc.id


def test_detail_include_all_with_internal_ids_still_strips_when_disallowed(
    client, db_session
):
    """When the deployment policy disallows internal-id exposure,
    ``include=all,internal_ids`` still strips ids (the token is
    silently dropped from ``request.include``)."""
    _, _, calc = _make_species_owned_calc(db_session)
    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=all,internal_ids"
    ).json()
    assert "internal_ids" not in body["request"]["include"]
    assert "calculation_id" not in body["record"]["calculation"]


def test_detail_include_all_does_not_expose_full_point_or_xyz_payloads(
    client, db_session
):
    """Defense-in-depth: even with every summary populated,
    ``include=all`` must not inline per-point arrays, artifact body
    bytes, XYZ coordinates, or geometry-atom rows. Those live behind
    specialized endpoints (or are intentionally not exposed at all)."""
    # Build a scan calc with point + coordinate data so the loaders
    # have something concrete to project from.
    _, calc = _make_scan_calc(db_session, dimension=1)
    _attach_scan_coordinate(
        db_session, calculation=calc, coordinate_index=1,
        coordinate_kind=ScanCoordinateKind.bond,
        atom1_index=1, atom2_index=2, step_count=2, step_size=0.1,
    )
    _attach_scan_point(
        db_session, calculation=calc, point_index=1,
        electronic_energy_hartree=-99.5,
    )
    geom = make_geometry(db_session, natoms=3, xyz_text="O 0 0 0")
    _attach_input_geometry(
        db_session, calculation=calc, geometry=geom, input_order=1
    )
    attach_artifact(db_session, calculation=calc, kind=ArtifactKind.output_log)

    body = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}?include=all"
    ).json()
    # Walk the entire record dict and check none of the dangerous keys
    # appear at any depth.
    forbidden_keys = {
        "points",
        "scan_points",
        "irc_points",
        "path_search_points",
        "point_coordinate_values",
        "atoms",
        "coords",
        "xyz_text",
        "body",
        "content",
        "data",
        "presigned_url",
        "download_url",
    }

    def _walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                assert k not in forbidden_keys, (
                    f"include=all leaked forbidden key {k!r} into the response"
                )
                _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(body)


def test_detail_include_all_equivalent_to_full_enumeration(client, db_session):
    """``include=all`` must produce the same record shape as
    explicitly enumerating every summary-safe include token. Request
    echo ordering is normalized by sorting both before comparison."""
    _, _, calc = _make_species_owned_calc(db_session)
    body_all = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}?include=all"
    ).json()
    enumerated = ",".join(sorted(_ALL_EXPANSION_TOKENS))
    body_explicit = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        f"?include={enumerated}"
    ).json()
    # Resolved include set is identical regardless of how the caller
    # phrased the request.
    assert sorted(body_all["request"]["include"]) == sorted(
        body_explicit["request"]["include"]
    )
    # Records are byte-identical except possibly for the request echo
    # — which we already compared explicitly above.
    assert body_all["record"] == body_explicit["record"]
