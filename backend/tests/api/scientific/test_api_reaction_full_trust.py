"""API tests for trust propagation on /api/v1/scientific/reaction-entries/{id}/full.

Covers the slice that lets ``?include=trust`` carry ``computed_kinetics_v1``,
``computed_calculation_v1``, and ``computed_transition_state_v1`` fragments
down to the embedded kinetics, calculation, and transition-state-entry
records inside the composite ``/full`` response. The standalone kinetics,
calculation, and TS-entry detail surfaces are exercised elsewhere; here we
verify the composite read mirrors that behavior.
"""

from __future__ import annotations

from app.db.models.calculation import (
    CalculationDependency,
    CalculationFreqResult,
)
from app.db.models.common import (
    CalculationDependencyRole,
    CalculationQuality,
    CalculationType,
    KineticsCalculationRole,
    RecordReviewStatus,
    SubmissionRecordType,
    TransitionStateEntryStatus,
    ValidationStatus,
)
from app.db.models.kinetics import KineticsSourceCalculation
from app.db.models.software import SoftwareRelease
from tests.services.scientific_read._factories import (
    attach_artifact,
    attach_geometry_validation,
    attach_opt_result,
    make_calculation,
    make_chem_reaction,
    make_kinetics,
    make_lot,
    make_reaction_entry,
    make_software,
    make_species,
    make_species_entry,
    make_transition_state,
    make_transition_state_entry,
    next_inchi_key,
    set_review,
)


def _entry(db_session, *, tag: str = "RXFT"):
    rs = make_species(db_session, smiles="A", inchi_key=next_inchi_key(tag + "1"))
    ps = make_species(db_session, smiles="B", inchi_key=next_inchi_key(tag + "2"))
    chem = make_chem_reaction(db_session, reactants=[rs], products=[ps])
    return make_reaction_entry(
        db_session,
        reaction=chem,
        reactant_entries=[make_species_entry(db_session, rs)],
        product_entries=[make_species_entry(db_session, ps)],
    )


def _ts_calc(db_session, *, reaction_entry, tag: str):
    ts = make_transition_state(
        db_session, reaction_entry=reaction_entry, label=f"ts_{tag}"
    )
    tse = make_transition_state_entry(
        db_session,
        transition_state=ts,
        status=TransitionStateEntryStatus.optimized,
    )
    software = make_software(db_session, name=f"full-trust-sw-{tag}")
    release = SoftwareRelease(software_id=software.id, version="1.0")
    db_session.add(release)
    db_session.flush()
    calc = make_calculation(
        db_session,
        type=CalculationType.opt,
        transition_state_entry_id=tse.id,
        lot_id=make_lot(db_session).id,
    )
    calc.quality = CalculationQuality.curated
    calc.software_release_id = release.id
    attach_opt_result(db_session, calculation=calc, final_energy_hartree=-10.0)
    attach_geometry_validation(
        db_session, calculation=calc, status=ValidationStatus.passed
    )
    attach_artifact(db_session, calculation=calc)
    db_session.flush()
    db_session.refresh(calc)
    return calc


def _link_source(db_session, *, kinetics, calculation, role):
    db_session.add(
        KineticsSourceCalculation(
            kinetics_id=kinetics.id,
            calculation_id=calculation.id,
            role=role,
        )
    )
    db_session.flush()
    db_session.refresh(kinetics)


def _ts_entry(
    db_session,
    *,
    reaction_entry,
    tag: str,
    status: TransitionStateEntryStatus = TransitionStateEntryStatus.optimized,
    with_freq: bool = False,
    n_imag: int = 1,
):
    """Build a TS + TS-entry under *reaction_entry*, optionally with an
    opt+freq calculation graph so the ``computed_transition_state_v1``
    frequency policy has evidence to evaluate.
    """
    ts = make_transition_state(
        db_session, reaction_entry=reaction_entry, label=f"ts_{tag}"
    )
    tse = make_transition_state_entry(
        db_session, transition_state=ts, status=status
    )
    if with_freq:
        opt = make_calculation(
            db_session,
            type=CalculationType.opt,
            transition_state_entry_id=tse.id,
            lot_id=make_lot(db_session).id,
        )
        attach_opt_result(db_session, calculation=opt, final_energy_hartree=-10.0)
        freq = make_calculation(
            db_session,
            type=CalculationType.freq,
            transition_state_entry_id=tse.id,
            lot_id=make_lot(db_session).id,
        )
        db_session.add(
            CalculationFreqResult(
                calculation_id=freq.id,
                n_imag=n_imag,
                imag_freq_cm1=-550.0,
                zpe_hartree=0.05,
            )
        )
        db_session.add(
            CalculationDependency(
                parent_calculation_id=opt.id,
                child_calculation_id=freq.id,
                dependency_role=CalculationDependencyRole.freq_on,
            )
        )
        db_session.flush()
    db_session.refresh(tse)
    return tse


def _ts_trust_in_full(body, *, tse_ref):
    """Pull the embedded TS-entry trust block by ref from a /full body."""
    return next(
        ts["trust"]
        for ts in body["transition_states"]
        if ts["transition_state_entry_ref"] == tse_ref
    )


# ---------------------------------------------------------------------------
# Default behavior — no trust without opt-in
# ---------------------------------------------------------------------------


def test_full_default_omits_embedded_trust(client, db_session):
    entry = _entry(db_session, tag="DFLT")
    make_kinetics(db_session, reaction_entry=entry)

    body = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.public_ref}/full"
    ).json()

    assert body["kinetics"], "default /full should include kinetics section"
    for record in body["kinetics"]:
        assert "trust" not in record


def test_full_include_all_excludes_trust(client, db_session):
    entry = _entry(db_session, tag="ALL")
    make_kinetics(db_session, reaction_entry=entry)
    _ts_calc(db_session, reaction_entry=entry, tag="all")

    body = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.public_ref}/full"
        "?include=all"
    ).json()

    assert "trust" not in body["request"]["include"]
    for record in body.get("kinetics", []) or []:
        assert "trust" not in record
    for record in body.get("calculations", []) or []:
        assert "trust" not in record


# ---------------------------------------------------------------------------
# include=trust — embedded kinetics
# ---------------------------------------------------------------------------


def test_full_include_trust_attaches_kinetics_trust(client, db_session):
    entry = _entry(db_session, tag="KTR")
    kinetics = make_kinetics(db_session, reaction_entry=entry)
    set_review(
        db_session,
        record_type=SubmissionRecordType.kinetics,
        record_id=kinetics.id,
        status=RecordReviewStatus.approved,
    )

    body = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.public_ref}/full"
        "?include=trust"
    ).json()

    assert "trust" in body["request"]["include"]
    assert body["kinetics"], "/full kinetics section must still be present"
    trust = body["kinetics"][0]["trust"]
    assert trust is not None
    assert trust["review_status"] == "approved"
    assert trust["evidence"]["rubric"] == "computed_kinetics_v1"
    assert trust["evidence"]["rubric_version"] == 1
    assert trust["llm_precheck"] == {
        "enabled": False,
        "label": "not_run",
        "summary": None,
    }
    assert "record_id" not in trust["evidence"]


def test_full_include_trust_matches_standalone_kinetics_trust(client, db_session):
    """The trust block embedded in /full equals the standalone read for the same record."""
    entry = _entry(db_session, tag="MTC")
    kinetics = make_kinetics(db_session, reaction_entry=entry, ea_kj_mol=42.0)

    standalone = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.public_ref}/kinetics"
        "?include=trust"
    ).json()
    full = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.public_ref}/full"
        "?include=trust"
    ).json()

    by_ref_standalone = {
        r["kinetics_ref"]: r["trust"] for r in standalone["records"]
    }
    by_ref_full = {r["kinetics_ref"]: r["trust"] for r in full["kinetics"]}

    assert kinetics.public_ref in by_ref_standalone
    assert kinetics.public_ref in by_ref_full

    stable_keys = (
        "rubric",
        "rubric_version",
        "label",
        "passed_checks",
        "missing_checks",
        "warning_checks",
        "not_applicable_checks",
        "evidence_completeness",
        "hard_fail_reason",
    )
    s_evidence = by_ref_standalone[kinetics.public_ref]["evidence"]
    f_evidence = by_ref_full[kinetics.public_ref]["evidence"]
    for key in stable_keys:
        assert s_evidence.get(key) == f_evidence.get(key), (
            f"trust evidence diverged for key {key!r}"
        )


# ---------------------------------------------------------------------------
# include=trust — embedded calculations
# ---------------------------------------------------------------------------


def test_full_include_trust_attaches_calculation_trust(client, db_session):
    entry = _entry(db_session, tag="CTR")
    calc = _ts_calc(db_session, reaction_entry=entry, tag="ctr")
    set_review(
        db_session,
        record_type=SubmissionRecordType.calculation,
        record_id=calc.id,
        status=RecordReviewStatus.approved,
    )

    body = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.public_ref}/full"
        "?include=calculations,trust"
    ).json()

    assert body["calculations"], "calculations section should populate"
    calc_records = [
        c for c in body["calculations"] if c["calculation_ref"] == calc.public_ref
    ]
    assert calc_records, "the TS-reachable calculation must be present"
    trust = calc_records[0]["trust"]
    assert trust is not None
    assert trust["review_status"] == "approved"
    assert trust["evidence"]["rubric"] == "computed_calculation_v1"
    assert trust["evidence"]["rubric_version"] == 1
    assert trust["llm_precheck"]["enabled"] is False
    assert trust["llm_precheck"]["label"] == "not_run"
    assert "record_id" not in trust["evidence"]


def test_full_include_trust_calculation_matches_standalone(client, db_session):
    entry = _entry(db_session, tag="CMT")
    calc = _ts_calc(db_session, reaction_entry=entry, tag="cmt")

    standalone = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}?include=trust"
    ).json()
    full = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.public_ref}/full"
        "?include=calculations,trust"
    ).json()

    full_trust = next(
        c["trust"]
        for c in full["calculations"]
        if c["calculation_ref"] == calc.public_ref
    )
    standalone_trust = standalone["record"]["trust"]

    stable_keys = (
        "rubric",
        "rubric_version",
        "label",
        "passed_checks",
        "missing_checks",
        "warning_checks",
        "not_applicable_checks",
        "evidence_completeness",
        "hard_fail_reason",
    )
    for key in stable_keys:
        assert (
            standalone_trust["evidence"].get(key)
            == full_trust["evidence"].get(key)
        ), f"calculation trust diverged for key {key!r}"


# ---------------------------------------------------------------------------
# Internal-IDs policy on embedded trust
# ---------------------------------------------------------------------------


def test_full_include_trust_hides_record_id_by_default(client, db_session):
    entry = _entry(db_session, tag="IDH")
    make_kinetics(db_session, reaction_entry=entry)
    _ts_calc(db_session, reaction_entry=entry, tag="idh")

    body = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.public_ref}/full"
        "?include=kinetics,calculations,trust"
    ).json()

    for record in body["kinetics"]:
        assert "record_id" not in record["trust"]["evidence"]
    for record in body["calculations"]:
        assert "record_id" not in record["trust"]["evidence"]


def test_full_include_trust_exposes_record_id_when_internal_ids_allowed(
    client, db_session, allow_internal_ids
):
    entry = _entry(db_session, tag="IDA")
    kinetics = make_kinetics(db_session, reaction_entry=entry)
    calc = _ts_calc(db_session, reaction_entry=entry, tag="ida")

    body = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.public_ref}/full"
        "?include=kinetics,calculations,trust,internal_ids"
    ).json()

    kinetics_trust = next(
        r["trust"]
        for r in body["kinetics"]
        if r["kinetics_ref"] == kinetics.public_ref
    )
    calc_trust = next(
        c["trust"]
        for c in body["calculations"]
        if c["calculation_ref"] == calc.public_ref
    )
    assert kinetics_trust["evidence"]["record_id"] == kinetics.id
    assert calc_trust["evidence"]["record_id"] == calc.id


# ---------------------------------------------------------------------------
# Quality / mutation guarantees
# ---------------------------------------------------------------------------


def test_full_include_trust_does_not_mutate_records(client, db_session):
    entry = _entry(db_session, tag="MUT")
    kinetics = make_kinetics(db_session, reaction_entry=entry)
    calc = _ts_calc(db_session, reaction_entry=entry, tag="mut")
    before_kinetics = (
        kinetics.a,
        kinetics.a_units,
        kinetics.n,
        kinetics.ea_kj_mol,
        kinetics.tmin_k,
        kinetics.tmax_k,
    )
    before_calc = (calc.type, calc.lot_id, calc.software_release_id)

    resp = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.public_ref}/full"
        "?include=calculations,trust"
    )
    assert resp.status_code == 200, resp.text

    db_session.refresh(kinetics)
    db_session.refresh(calc)
    assert (
        kinetics.a,
        kinetics.a_units,
        kinetics.n,
        kinetics.ea_kj_mol,
        kinetics.tmin_k,
        kinetics.tmax_k,
    ) == before_kinetics
    assert (calc.type, calc.lot_id, calc.software_release_id) == before_calc


def test_full_include_trust_alone_keeps_default_sections(client, db_session):
    """``?include=trust`` is a modifier; default sections should still appear."""
    entry = _entry(db_session, tag="MOD")
    make_kinetics(db_session, reaction_entry=entry)

    body = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.public_ref}/full"
        "?include=trust"
    ).json()
    assert body["species"] is not None
    assert body["kinetics"] is not None
    assert body["transition_states"] is not None
    # Calculations not in default include set; still omitted.
    assert body["calculations"] is None


# ---------------------------------------------------------------------------
# include=trust — embedded transition-state entries
# ---------------------------------------------------------------------------


def test_full_default_omits_embedded_ts_trust(client, db_session):
    entry = _entry(db_session, tag="TSDF")
    _ts_entry(db_session, reaction_entry=entry, tag="tsdf")

    body = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.public_ref}/full"
    ).json()

    assert body["transition_states"], "default /full should include TS section"
    for ts in body["transition_states"]:
        assert "trust" not in ts


def test_full_include_all_excludes_ts_trust(client, db_session):
    entry = _entry(db_session, tag="TSAL")
    _ts_entry(db_session, reaction_entry=entry, tag="tsal")

    body = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.public_ref}/full"
        "?include=all"
    ).json()

    assert "trust" not in body["request"]["include"]
    for ts in body.get("transition_states", []) or []:
        assert "trust" not in ts


def test_full_include_trust_attaches_ts_trust(client, db_session):
    entry = _entry(db_session, tag="TSTR")
    tse = _ts_entry(db_session, reaction_entry=entry, tag="tstr")

    body = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.public_ref}/full"
        "?include=trust"
    ).json()

    assert "trust" in body["request"]["include"]
    assert body["transition_states"], "/full TS section must still be present"
    trust = _ts_trust_in_full(body, tse_ref=tse.public_ref)
    assert trust is not None
    # No review row → not_reviewed default.
    assert trust["review_status"] == "not_reviewed"
    assert trust["is_certified"] is False
    assert trust["evidence"]["record_type"] == "transition_state_entry"
    assert trust["evidence"]["rubric"] == "computed_transition_state_v1"
    assert trust["evidence"]["rubric_version"] == 1
    assert "passed_checks" in trust["evidence"]
    assert "missing_checks" in trust["evidence"]
    assert "warning_checks" in trust["evidence"]
    assert "not_applicable_checks" in trust["evidence"]


def test_full_include_trust_ts_llm_precheck_disabled(client, db_session):
    entry = _entry(db_session, tag="TSLL")
    tse = _ts_entry(db_session, reaction_entry=entry, tag="tsll")

    body = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.public_ref}/full"
        "?include=trust"
    ).json()

    trust = _ts_trust_in_full(body, tse_ref=tse.public_ref)
    assert trust["llm_precheck"] == {
        "enabled": False,
        "label": "not_run",
        "summary": None,
    }


def test_full_include_trust_ts_uses_review_badge(client, db_session):
    entry = _entry(db_session, tag="TSRV")
    tse = _ts_entry(db_session, reaction_entry=entry, tag="tsrv")
    set_review(
        db_session,
        record_type=SubmissionRecordType.transition_state_entry,
        record_id=tse.id,
        status=RecordReviewStatus.approved,
    )

    body = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.public_ref}/full"
        "?include=trust"
    ).json()

    trust = _ts_trust_in_full(body, tse_ref=tse.public_ref)
    assert trust["review_status"] == "approved"


def test_full_include_trust_ts_hides_record_id_by_default(client, db_session):
    entry = _entry(db_session, tag="TSIH")
    tse = _ts_entry(db_session, reaction_entry=entry, tag="tsih")

    body = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.public_ref}/full"
        "?include=trust"
    ).json()

    trust = _ts_trust_in_full(body, tse_ref=tse.public_ref)
    assert "record_id" not in trust["evidence"]


def test_full_include_trust_ts_exposes_record_id_when_internal_ids_allowed(
    client, db_session, allow_internal_ids
):
    entry = _entry(db_session, tag="TSIA")
    tse = _ts_entry(db_session, reaction_entry=entry, tag="tsia")

    body = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.public_ref}/full"
        "?include=trust,internal_ids"
    ).json()

    trust = _ts_trust_in_full(body, tse_ref=tse.public_ref)
    assert trust["evidence"]["record_id"] == tse.id


def test_full_include_trust_ts_matches_standalone(client, db_session):
    """The TS-entry trust embedded in /full equals the standalone read."""
    entry = _entry(db_session, tag="TSMT")
    tse = _ts_entry(
        db_session, reaction_entry=entry, tag="tsmt", with_freq=True
    )

    standalone = client.get(
        f"/api/v1/scientific/transition-state-entries/{tse.public_ref}"
        "?include=trust"
    ).json()
    full = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.public_ref}/full"
        "?include=trust"
    ).json()

    standalone_trust = standalone["record"]["trust"]
    full_trust = _ts_trust_in_full(full, tse_ref=tse.public_ref)

    stable_keys = (
        "rubric",
        "rubric_version",
        "label",
        "passed_checks",
        "missing_checks",
        "warning_checks",
        "not_applicable_checks",
        "evidence_completeness",
        "hard_fail_reason",
    )
    for key in stable_keys:
        assert (
            standalone_trust["evidence"].get(key)
            == full_trust["evidence"].get(key)
        ), f"TS-entry trust diverged for key {key!r}"


def test_full_include_trust_ts_frequency_policy_for_optimized(client, db_session):
    """Optimized TS-entry with a single imaginary mode passes the freq checks."""
    entry = _entry(db_session, tag="TSFQ")
    tse = _ts_entry(
        db_session,
        reaction_entry=entry,
        tag="tsfq",
        status=TransitionStateEntryStatus.optimized,
        with_freq=True,
        n_imag=1,
    )

    body = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.public_ref}/full"
        "?include=trust"
    ).json()

    trust = _ts_trust_in_full(body, tse_ref=tse.public_ref)
    passed = trust["evidence"]["passed_checks"]
    assert "imaginary_frequency_count_recorded" in passed
    assert "single_imaginary_frequency_for_ts" in passed
    assert trust["trust_status"] != "hard_failed"


def test_full_include_trust_ts_does_not_mutate_record(client, db_session):
    entry = _entry(db_session, tag="TSMU")
    tse = _ts_entry(
        db_session, reaction_entry=entry, tag="tsmu", with_freq=True
    )
    before = (tse.charge, tse.multiplicity, tse.status, tse.unmapped_smiles)

    resp = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.public_ref}/full"
        "?include=trust"
    )
    assert resp.status_code == 200, resp.text

    db_session.refresh(tse)
    assert (tse.charge, tse.multiplicity, tse.status, tse.unmapped_smiles) == before
