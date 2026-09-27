"""D5 Kirchhoff fixtures (D5-1 .. D5-8) on rows the upload path persists.

Every record is written by the real thermo upload workflow
(``persist_thermo_upload``: species through ``resolve_species_entry``,
enthalpy-declaration rule, schema validation), except where a test says
it needs a row that workflow now refuses -- an undeclared legacy row or a
NASA-9 set with a gap -- and then the service layer beneath it writes the
row (``resolve_thermo_upload``/``persist_thermo``), as it did before those
rules existed. Expectations come from hand formulas with the SI R, never
from the production evaluator. Every iterating test first asserts the
exact number of findings it is about to check.
"""
import json

import pytest

from app.db.models.common import ScientificOriginKind
from app.schemas.entities.thermo import ThermoCreate
from app.schemas.fragments.identity import SpeciesEntryIdentityPayload
from app.schemas.workflows.thermo_upload import ThermoUploadRequest
from app.services.consistency.kirchhoff import compare_kirchhoff
from app.services.consistency.service import compare
from app.services.species_resolution import resolve_species_entry
from app.services.thermo_resolution import persist_thermo, resolve_thermo_upload
from app.workflows.thermo import persist_thermo_upload
from tests.services.test_phase_d_foundation import HIGH, LOW, _h_nasa7_kj

R = 8.31446261815324  # SI, independent of Cantera and the production module
T298 = 298.15


def _h9(a3, a8, t):
    """NASA-9 H/R = -a1/T + a2 ln T + a3 T + ... + a8 with only a3 and a8 non-zero, in kJ/mol."""
    return R * (a3 * t + a8) / 1000.0


def nasa7_block(low=LOW, high=HIGH, t_mid=1000.0):
    block = {"t_low": 200.0, "t_mid": t_mid, "t_high": 3000.0}
    block.update({f"a{i}": low[i - 1] for i in range(1, 8)})
    block.update({f"b{i}": high[i - 1] for i in range(1, 8)})
    return block


def constant_cp_nasa7(cp_r, a6=0.0, b6=0.0):
    return nasa7_block(low=(cp_r, 0, 0, 0, 0, a6, 0.0), high=(cp_r, 0, 0, 0, 0, b6, 0.0))


def nasa9_interval(index, t_min, t_max, a3, a8=0.0):
    row = {f"a{i}": 0.0 for i in range(1, 10)}
    row.update(interval_index=index, t_min_k=t_min, t_max_k=t_max, a3=a3, a8=a8)
    return row


def species(smiles="C", charge=0, multiplicity=1):
    return {"smiles": smiles, "charge": charge, "multiplicity": multiplicity}


def upload(session, *, smiles="C", charge=0, **fields):
    """Persist through the thermo upload workflow, as a depositor's request is."""
    fields.setdefault("enthalpy_reference_kind", "formation_298k")
    fields.setdefault("phase", "gas")
    request = ThermoUploadRequest(species_entry=species(smiles, charge),
                                  scientific_origin=ScientificOriginKind.experimental, **fields)
    thermo = persist_thermo_upload(session, request, review_policy=None)
    session.flush()
    session.expire(thermo)
    return thermo


def legacy_row(session, *, smiles="C", nasa9_intervals=(), **fields):
    """A row the workflow now refuses (undeclared enthalpy, or a NASA-9 gap).

    Written by the service layer beneath the workflow, the way rows were
    written before the declaration rule and the NASA-9 contiguity rule.
    Species still resolve through ``resolve_species_entry``.
    """
    fields.setdefault("phase", "gas")
    entry = resolve_species_entry(session, SpeciesEntryIdentityPayload(**species(smiles)))
    request = ThermoUploadRequest(species_entry=species(smiles),
                                  scientific_origin=ScientificOriginKind.experimental, **fields)
    create = resolve_thermo_upload(session, request, species_entry_id=entry.id)
    if nasa9_intervals:
        create = ThermoCreate(**{**create.model_dump(), "nasa9_intervals": list(nasa9_intervals),
                                 "model_kind": "nasa9"})
    thermo = persist_thermo(session, create)
    session.flush()
    session.expire(thermo)
    return thermo


def rows(result, kind):
    return [r for r in (json.loads(f.message) for f in result.findings) if r.get("kind") == kind]


def evaluated(result, kind):
    return [r for r in rows(result, kind) if r["reason"] is None]


# --------------------------------------------------------------------------- D5-1


def test_d5_1_nasa7_jump_at_t_mid_is_r_times_the_constant_difference(db_session):
    thermo = upload(db_session, nasa=nasa7_block(low=(*LOW[:5], 20.0, LOW[6]), high=(*LOW[:5], 120.0, LOW[6])))
    result = compare_kirchhoff(thermo)
    jumps = rows(result, "boundary_jump")
    assert len(jumps) == 1
    jump = jumps[0]
    assert (jump["reason"], jump["representation"], jump["boundary_k"]) == (None, "nasa7", 1000.0)
    assert jump["lower"]["value"] == pytest.approx(_h_nasa7_kj((*LOW[:5], 20.0), 1000.0), rel=1e-12)
    assert jump["upper"]["value"] == pytest.approx(_h_nasa7_kj((*LOW[:5], 120.0), 1000.0), rel=1e-12)
    assert jump["residual"] == pytest.approx(R * 100 / 1000, rel=1e-9)
    # One fit and nothing else: nothing to anchor or integrate against, stated.
    assert [(r["kind"], r["reason"]) for r in rows(result, "anchor") + rows(result, "increment")] == [
        ("anchor", "no_comparison_pairs_or_temperatures"), ("increment", "no_comparison_pairs_or_temperatures")]


def test_d5_1_nasa9_jump_at_a_shared_boundary(db_session):
    thermo = upload(db_session, nasa9_intervals=[nasa9_interval(1, 200.0, 1000.0, 3.5, 20.0),
                                                 nasa9_interval(2, 1000.0, 3000.0, 3.5, 120.0)])
    jumps = rows(compare_kirchhoff(thermo), "boundary_jump")
    assert len(jumps) == 1
    assert (jumps[0]["reason"], jumps[0]["boundary_k"]) == (None, 1000.0)
    assert (jumps[0]["lower"]["segment"], jumps[0]["upper"]["segment"]) == (1, 2)
    assert jumps[0]["residual"] == pytest.approx(R * 100 / 1000, rel=1e-9)


# --------------------------------------------------------------------------- D5-2


def _d5_2(session, **fields):
    return upload(session, nasa=nasa7_block(), points=[
        {"temperature_k": T298, "h_kj_mol": _h_nasa7_kj(LOW, T298) + 3.0},
        {"temperature_k": 600.0, "h_kj_mol": _h_nasa7_kj(LOW, 600.0) + 5.0},
    ], **fields)


def test_d5_2_point_anchors_and_their_increment(db_session):
    result = compare_kirchhoff(_d5_2(db_session))
    anchors = rows(result, "anchor")
    assert len(anchors) == 2
    assert all((a["left"]["representation"], a["right"]["representation"]) == ("point", "nasa7") for a in anchors)
    by_t = {a["temperature_k"]: a for a in anchors}
    assert by_t[T298]["reason"] is None and by_t[600.0]["reason"] is None
    assert by_t[T298]["residual"] == pytest.approx(3.0, abs=1e-9)
    assert by_t[600.0]["residual"] == pytest.approx(5.0, abs=1e-9)
    assert all(a["element_reference_cancellation"] == "same_record" for a in anchors)
    increments = rows(result, "increment")
    assert len(increments) == 1
    k = increments[0]
    assert (k["reason"], k["temperature_k"], k["increment_from_k"]) == (None, 600.0, T298)
    assert k["integral"]["segments"] == [{"segment": "low", "from_k": T298, "to_k": 600.0}]
    assert k["integral"]["value"] == pytest.approx(_h_nasa7_kj(LOW, 600.0) - _h_nasa7_kj(LOW, T298), rel=1e-12)
    assert k["residual"] == pytest.approx(2.0, abs=1e-9)


# --------------------------------------------------------------------------- D5-3


def test_d5_3_h298_anchor_and_h298_is_a_value_only_at_298_15(db_session):
    thermo = upload(db_session, nasa=nasa7_block(), h298_kj_mol=_h_nasa7_kj(LOW, T298) - 1.5)
    anchors = rows(compare_kirchhoff(thermo, temperature_grid=[600.0]), "anchor")
    assert len(anchors) == 2
    by_t = {a["temperature_k"]: a for a in anchors}
    assert (by_t[T298]["left"]["representation"], by_t[T298]["right"]["representation"]) == ("h298", "nasa7")
    assert by_t[T298]["reason"] is None
    assert by_t[T298]["residual"] == pytest.approx(-1.5, abs=1e-9)
    assert (by_t[600.0]["reason"], by_t[600.0]["residual"]) == ("no_exact_h298_value", None)


# --------------------------------------------------------------------------- D5-4


def test_d5_4_nasa9_increment_against_a_neighbours_nasa7_integral(db_session):
    target = upload(db_session, nasa9_intervals=[nasa9_interval(1, 200.0, 3000.0, 2.5, 50.0)])
    neighbour = upload(db_session, nasa=constant_cp_nasa7(3.5, a6=10.0, b6=10.0))
    result = compare_kirchhoff(target, comparison=neighbour, temperature_grid=[1000.0])
    increments = rows(result, "increment")
    assert len(increments) == 2
    by_direction = {(k["enthalpy"]["representation"], k["integral"]["representation"]): k for k in increments}
    forward, backward = by_direction["nasa9", "nasa7"], by_direction["nasa7", "nasa9"]
    for k in (forward, backward):
        assert (k["reason"], k["temperature_k"], k["increment_from_k"]) == (None, 1000.0, T298)
    assert forward["residual"] == pytest.approx(-R * 701.85 / 1000, rel=1e-9)
    assert backward["residual"] == pytest.approx(R * 701.85 / 1000, rel=1e-9)
    anchors = rows(result, "anchor")
    assert len(anchors) == 2
    for a in anchors:
        t = a["temperature_k"]
        assert a["reason"] is None
        assert a["residual"] == pytest.approx(_h9(2.5, 50.0, t) - R * (3.5 * t + 10.0) / 1000, rel=1e-9)
        # Decision 4: cross-record anchors run, and say what they assume.
        assert (a["element_reference_compilation"], a["element_reference_cancellation"]) == (
            "not_recorded", "assumed")


# --------------------------------------------------------------------------- D5-5


def test_d5_5_the_integral_excludes_the_fits_own_jump(db_session):
    target = upload(db_session, nasa9_intervals=[nasa9_interval(1, 200.0, 3000.0, 3.5)])
    neighbour = upload(db_session, nasa=constant_cp_nasa7(3.5, a6=20.0, b6=120.0))
    result = compare_kirchhoff(target, comparison=neighbour, temperature_grid=[1500.0])
    increments = rows(result, "increment")
    assert len(increments) == 2
    by_direction = {(k["enthalpy"]["representation"], k["integral"]["representation"]): k for k in increments}
    across = by_direction["nasa9", "nasa7"]
    assert across["reason"] is None
    assert across["integral"]["segments"] == [{"segment": "low", "from_k": T298, "to_k": 1000.0},
                                              {"segment": "high", "from_k": 1000.0, "to_k": 1500.0}]
    assert across["integral"]["value"] == pytest.approx(R * 3.5 * (1500.0 - T298) / 1000, rel=1e-12)
    assert across["residual"] == pytest.approx(0.0, abs=1e-9)
    # The same jump, seen from the other side: the NASA-7 record's own stated
    # enthalpy change carries it, and the NASA-9 integral does not.
    assert by_direction["nasa7", "nasa9"]["residual"] == pytest.approx(R * 100 / 1000, rel=1e-9)
    jumps = rows(result, "boundary_jump")
    assert len(jumps) == 1
    assert (jumps[0]["input"], jumps[0]["reason"]) == (neighbour.public_ref, None)
    assert jumps[0]["residual"] == pytest.approx(R * 100 / 1000, rel=1e-9)


# --------------------------------------------------------------------------- D5-6


def test_d5_6_nasa9_gap_is_reported_and_never_integrated_across(db_session):
    points = [{"temperature_k": t, "h_kj_mol": _h9(3.5, 0.0, t)} for t in (T298, 800.0, 1000.0)]
    thermo = legacy_row(db_session, enthalpy_reference_kind="formation_298k", points=points,
                        nasa9_intervals=[nasa9_interval(1, 200.0, 700.0, 3.5), nasa9_interval(2, 900.0, 3000.0, 3.5)])
    result = compare_kirchhoff(thermo)
    jumps = rows(result, "boundary_jump")
    assert len(jumps) == 1
    assert (jumps[0]["reason"], jumps[0]["boundary_k"], jumps[0]["gap_k"]) == (
        "nasa9_gap_no_shared_boundary", None, [700.0, 900.0])
    increments = rows(result, "increment")
    assert len(increments) == 2
    by_t = {k["temperature_k"]: k for k in increments}
    assert (by_t[1000.0]["reason"], by_t[1000.0]["residual"]) == ("integration_path_crosses_nasa9_gap", None)
    assert by_t[800.0]["reason"] == "temperature_outside_fit_or_in_gap"
    anchors = rows(result, "anchor")
    assert len(anchors) == 3
    assert {a["temperature_k"]: a["reason"] for a in anchors} == {
        T298: None, 800.0: "temperature_outside_fit_or_in_gap", 1000.0: None}


def test_the_upload_workflow_refuses_a_nasa9_gap(db_session):
    """Why D5-6 needs the service layer: the workflow cannot create a gap."""
    with pytest.raises(ValueError, match="meet exactly"):
        ThermoUploadRequest(species_entry=species(), enthalpy_reference_kind="formation_298k", nasa9_intervals=[
            nasa9_interval(1, 200.0, 700.0, 3.5), nasa9_interval(2, 900.0, 3000.0, 3.5)])


# --------------------------------------------------------------------------- D5-7


def test_d5_7_increment_starts_at_the_lowest_common_grid_temperature(db_session):
    thermo = upload(db_session, nasa=nasa7_block(), points=[
        {"temperature_k": 400.0, "h_kj_mol": _h_nasa7_kj(LOW, 400.0) + 1.0},
        {"temperature_k": 700.0, "h_kj_mol": _h_nasa7_kj(LOW, 700.0) + 4.0},
    ])
    increments = rows(compare_kirchhoff(thermo), "increment")
    assert len(increments) == 2
    assert all(k["increment_from_k"] == 400.0 for k in increments)
    by_t = {k["temperature_k"]: k for k in increments}
    assert by_t[T298]["reason"] == "no_exact_matching_point"
    assert by_t[700.0]["reason"] is None
    assert by_t[700.0]["residual"] == pytest.approx(3.0, abs=1e-9)


def test_d5_7_no_common_temperature_is_a_named_reason(db_session):
    thermo = upload(db_session, nasa=nasa7_block(), points=[
        {"temperature_k": 3500.0, "h_kj_mol": 1.0}, {"temperature_k": 4000.0, "h_kj_mol": 2.0}])
    increments = rows(compare_kirchhoff(thermo), "increment")
    assert len(increments) == 1
    assert (increments[0]["reason"], increments[0]["increment_from_k"]) == (
        "increment_reference_temperature_unavailable", None)


# --------------------------------------------------------------------------- D5-8


def test_d5_8_missing_reference_pressure_is_shown_and_never_a_reason(db_session):
    unrecorded = compare_kirchhoff(_d5_2(db_session))
    recorded = compare_kirchhoff(_d5_2(db_session, reference_pressure_bar=1.0))
    payloads = [json.loads(f.message) for f in unrecorded.findings]
    assert len(payloads) == 5  # input + 2 anchors + 1 increment + 1 jump
    assert all(list(p["reference_pressure_bar"].values()) == [None] for p in payloads)
    measured = [p for p in payloads if "residual" in p]
    assert len(measured) == 4
    assert all(p["reason"] is None for p in measured)
    assert [p["residual"] for p in measured] == [
        json.loads(f.message)["residual"] for f in recorded.findings if "residual" in json.loads(f.message)]


# --------------------------------------------------------------------------- reasons


def _all_measurements(result):
    return [p for p in (json.loads(f.message) for f in result.findings) if "residual" in p]


def test_an_undeclared_row_is_excluded_everywhere(db_session):
    thermo = legacy_row(db_session, nasa=nasa7_block(), points=[{"temperature_k": 600.0, "h_kj_mol": 1.0}])
    assert thermo.enthalpy_reference_kind is None
    measured = _all_measurements(compare_kirchhoff(thermo))
    assert len(measured) == 4  # 2 anchors + 1 increment pair + 1 jump
    assert all((p["reason"], p["residual"]) == ("enthalpy_reference_unrecorded", None) for p in measured)


def test_a_neighbour_with_an_undeclared_or_different_basis_is_never_compared(db_session):
    target = upload(db_session, nasa=nasa7_block())
    legacy = legacy_row(db_session, nasa=nasa7_block())
    measured = [p for p in _all_measurements(compare_kirchhoff(target, comparison=legacy, temperature_grid=[500.0]))
                if p["kind"] != "boundary_jump"]
    assert len(measured) == 4  # anchor pair at 298.15 and 500, one increment pair each way
    assert all(p["reason"] == "enthalpy_reference_unrecorded" for p in measured)
    # A second declared basis does not exist yet, so this row cannot be stored;
    # set in memory only, to pin the token the rule will return when it does.
    other = upload(db_session, nasa=nasa7_block())
    with db_session.no_autoflush:
        other.enthalpy_reference_kind = "some_future_basis"
        mixed = [p for p in _all_measurements(compare_kirchhoff(target, comparison=other, temperature_grid=[500.0]))
                 if p["kind"] != "boundary_jump"]
        assert len(mixed) == 4
        assert all(p["reason"] == "enthalpy_reference_mixed" for p in mixed)
        db_session.expunge(other)


def test_a_neighbour_must_be_the_same_species_entry_and_needs_temperatures(db_session):
    target = upload(db_session, nasa=nasa7_block())
    ethane = upload(db_session, smiles="CC", nasa=nasa7_block())
    with pytest.raises(ValueError, match="explicit temperatures"):
        compare_kirchhoff(target, comparison=ethane)
    measured = [p for p in _all_measurements(compare_kirchhoff(target, comparison=ethane, temperature_grid=[500.0]))
                if p["kind"] != "boundary_jump"]
    assert len(measured) == 4
    assert all(p["reason"] == "different_species_entries" for p in measured)


@pytest.mark.parametrize("smiles,charge,expected", [
    ("[2H]C([2H])([2H])[2H]", 0, "isotope_labelled_species_out_of_scope"),
    ("[NH4+]", 1, "charged_species_out_of_scope"),
])
def test_species_out_of_scope_is_named(db_session, smiles, charge, expected):
    thermo = upload(db_session, smiles=smiles, charge=charge, nasa=nasa7_block(),
                    points=[{"temperature_k": 600.0, "h_kj_mol": 1.0}])
    result = compare_kirchhoff(thermo)
    measured = _all_measurements(result)
    assert len(measured) == 4
    assert all(p["reason"] == expected for p in measured)
    assert rows(result, "increment")[0]["temperature_k"] is None


def test_service_resolves_public_refs_for_the_kirchhoff_check(db_session):
    thermo = _d5_2(db_session)
    result = compare(db_session, check="kirchhoff", target_ref=thermo.public_ref)
    assert result.runner == "kirchhoff_consistency"
    assert len(evaluated(result, "anchor")) == 2
    with pytest.raises(ValueError, match="thm_"):
        compare(db_session, check="kirchhoff", target_ref=thermo.public_ref, comparison_thermo_ref="spe_x")


def test_cli_offers_kirchhoff_and_dry_runs_by_default(db_session, monkeypatch, capsys):
    from app.api import deps
    from scripts import run_consistency_check as cli

    thermo = _d5_2(db_session)

    class SessionProxy:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def __getattr__(self, name):
            return getattr(db_session, name)

        def commit(self):
            raise AssertionError("dry run must not commit")

        def rollback(self):
            pass

    monkeypatch.setattr(deps, "SessionLocal", SessionProxy)
    assert cli.main(["--check", "kirchhoff", "--target-ref", thermo.public_ref]) == 0
    output = json.loads(capsys.readouterr().out)
    assert (output["check"], output["committed"]) == ("kirchhoff", False)
    assert len(output["findings"]) == 5
