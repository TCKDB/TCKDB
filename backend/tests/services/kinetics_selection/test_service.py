"""Kinetics assessment against real rows: loading, the visible-population cap, grouping by
determination, the read profile's visibility, and the guarantee that nothing is written."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from tckdb_schemas.kinetics_declarations import KineticsCoefficientBasis

from app.api.error_contract import CodedValueError
from app.api.errors import NotFoundError
from app.db.models.common import (
    KineticsDeterminationTargetKind,
    KineticsDirection,
    KineticsRepresentationRole,
    NetworkChannelKind,
    NetworkKineticsModelKind,
    NetworkStateKind,
    ProfileRecommendation,
    ReadProfile,
    RecordReviewStatus,
    SubmissionRecordType,
)
from app.db.models.kinetics import Kinetics, KineticsDetermination
from app.db.models.record_review import RecordReview
from app.schemas.reads.scientific_common import SelectionPolicy
from app.services.kinetics_selection import (
    MAX_CANDIDATES,
    KineticsRequest,
    PressureKind,
    PressureRequest,
    TargetRequest,
    assess_reaction_entry_kinetics,
)
from app.services.kinetics_selection import service as service_module
from app.services.scientific_read.profile import (
    ResolvedReadProfile,
    reset_current_read_profile,
    set_current_read_profile,
)
from app.services.selection_kernel import Applicability as A
from tests.services.kinetics_selection._support import applicability
from tests.services.scientific_read._factories import (
    attach_kinetics_arrhenius_entry,
    attach_kinetics_plog_entry,
    attach_kinetics_third_body_efficiency,
    make_chem_reaction,
    make_kinetics,
    make_literature,
    make_network,
    make_network_channel,
    make_network_solve,
    make_network_state,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_transition_state,
    make_transition_state_entry,
    next_inchi_key,
    set_review,
)

REQUEST = KineticsRequest(
    direction=KineticsDirection.forward,
    target=TargetRequest(KineticsDeterminationTargetKind.whole_reaction),
    coefficient_basis=KineticsCoefficientBasis.elementary_coefficient,
    temperature_min_k=500.0,
    temperature_max_k=1500.0,
    pressure=PressureRequest(PressureKind.independent),
)


@pytest.fixture
def world(db_session):
    h = make_species_entry(db_session, make_species(db_session, smiles="[H]", multiplicity=2, inchi_key=next_inchi_key("KSA")))
    ch4 = make_species_entry(db_session, make_species(db_session, smiles="C", multiplicity=1, inchi_key=next_inchi_key("KSB")))
    h2 = make_species_entry(db_session, make_species(db_session, smiles="[H][H]", multiplicity=1, inchi_key=next_inchi_key("KSC")))
    ch3 = make_species_entry(db_session, make_species(db_session, smiles="[CH3]", multiplicity=2, inchi_key=next_inchi_key("KSD")))
    reaction = make_chem_reaction(db_session, reactants=[h.species, ch4.species], products=[h2.species, ch3.species])
    entry = make_reaction_entry(db_session, reaction=reaction, reactant_entries=[h, ch4], product_entries=[h2, ch3])
    return SimpleNamespace(entry=entry, literature=make_literature(db_session), h=h, ch4=ch4)


def determination(session, world, key, entry=None, **target):
    det = KineticsDetermination(
        reaction_entry_id=(entry or world.entry).id,
        direction=KineticsDirection.forward,
        target_kind=target.pop("target_kind", KineticsDeterminationTargetKind.whole_reaction),
        **target,
        literature_id=world.literature.id,
        determination_key=key,
        identity_hash=hashlib.sha256(f"{key}-{(entry or world.entry).id}".encode()).hexdigest(),
    )
    session.add(det)
    session.flush()
    return det


def declared(session, world, det, *, status=RecordReviewStatus.approved, block=None, children=None, role=KineticsRepresentationRole.complete, created_at=None, **kw):
    """A kinetics row that states everything the baseline request needs.

    ``children`` attaches child rows before the review is set: an approved record is frozen."""
    k = make_kinetics(session, reaction_entry=world.entry, direction=KineticsDirection.forward, **kw)
    k.literature_id = world.literature.id
    if det is not None:
        k.determination_id = det.id
        k.representation_role = role
    if created_at is not None:
        k.created_at = created_at
    k.applicability_declaration = block if block is not None else applicability()
    session.flush()
    if children is not None:
        children(k)
    if status is not RecordReviewStatus.not_reviewed:
        set_review(session, record_type=SubmissionRecordType.kinetics, record_id=k.id, status=status)
    return k


def run(session, entry, request=REQUEST):
    # The fixture session is one outer transaction that has already run statements, so it cannot become a
    # snapshot; the snapshot tests below use their own session.
    return assess_reaction_entry_kinetics(session, reaction_entry_id=entry.id, request=request, require_snapshot=False)


def by_ref(result):
    return {a.kinetics_ref: a for a in result.assessments}


def codes(assessment):
    return {r.code for r in assessment.reasons}


# -- legacy rows and honest absence -------------------------------------------------------------


def test_legacy_rows_without_any_declaration_are_unresolved_and_no_determination_is_invented(db_session, world):
    legacy = make_kinetics(db_session, reaction_entry=world.entry)  # no direction, no determination, no declarations
    set_review(db_session, record_type=SubmissionRecordType.kinetics, record_id=legacy.id, status=RecordReviewStatus.approved)
    before = db_session.scalar(select(func.count()).select_from(KineticsDetermination))
    result = run(db_session, world.entry)
    (assessment,) = result.assessments
    assert assessment.applicability is A.unresolved and not assessment.physically_eligible
    assert {"determination_not_declared", "direction_not_recorded", "applicability_not_declared"} <= codes(assessment)
    assert result.groups == () and result.unresolved_refs == (legacy.public_ref,)
    assert db_session.scalar(select(func.count()).select_from(KineticsDetermination)) == before
    assert "unresolved" in result.notes[0]


def test_an_unreadable_stored_declaration_is_unresolved_not_a_crash(db_session, world):
    det = determination(db_session, world, "d")
    declared(db_session, world, det, block={"version": 7, "phase": "gas"})
    (assessment,) = run(db_session, world.entry).assessments
    assert assessment.applicability is A.unresolved and "applicability_unreadable" in codes(assessment)


# -- grouping -----------------------------------------------------------------------------------


def test_alternate_fits_of_one_determination_are_one_candidate_and_two_determinations_are_two(db_session, world):
    d1, d2 = determination(db_session, world, "one"), determination(db_session, world, "two")
    a = declared(db_session, world, d1)
    b = declared(db_session, world, d1, a=2.0e-12)
    c = declared(db_session, world, d2)
    result = run(db_session, world.entry)
    assert all(x.physically_eligible for x in result.assessments)
    groups = {g.determination_ref: g for g in result.groups}
    assert set(groups) == {d1.public_ref, d2.public_ref}
    assert set(groups[d1.public_ref].representation_refs) == {a.public_ref, b.public_ref}
    assert groups[d2.public_ref].representation_refs == (c.public_ref,)
    assert sum(len(g.representation_refs) for g in result.groups) == 3 and len(result.groups) == 2


def test_the_representative_is_the_first_under_the_administrative_policy_and_only_that_policy(db_session, world):
    det = determination(db_session, world, "d")
    old_approved = declared(db_session, world, det, created_at=datetime(2020, 1, 1, tzinfo=UTC))
    new_unreviewed = declared(
        db_session, world, det, status=RecordReviewStatus.not_reviewed, created_at=datetime(2026, 1, 1, tzinfo=UTC)
    )
    default = run(db_session, world.entry, _with(REQUEST, admin_policy=SelectionPolicy.default))
    latest = run(db_session, world.entry, _with(REQUEST, admin_policy=SelectionPolicy.latest))
    assert default.groups[0].representative_ref == old_approved.public_ref
    assert latest.groups[0].representative_ref == new_unreviewed.public_ref
    assert set(default.groups[0].representation_refs) == set(latest.groups[0].representation_refs)


def test_additive_components_and_unqualified_records_never_enter_a_group(db_session, world):
    det = determination(db_session, world, "d")
    whole = declared(db_session, world, det)
    part = declared(db_session, world, det, role=KineticsRepresentationRole.additive_component)
    result = run(db_session, world.entry)
    assert by_ref(result)[part.public_ref].applicability is A.unsupported
    assert result.groups[0].representation_refs == (whole.public_ref,)
    assert result.unsupported_refs == (part.public_ref,)


def test_multi_arrhenius_and_plog_parents_are_one_representation_each(db_session, world):
    det = determination(db_session, world, "d")

    def terms(k):
        attach_kinetics_arrhenius_entry(db_session, kinetics=k, entry_index=1, a=1.0e-12)
        attach_kinetics_arrhenius_entry(db_session, kinetics=k, entry_index=2, a=-2.0e-13)  # a negative term is not a defect

    multi = declared(
        db_session, world, det, model_kind=_model("multi_arrhenius"), a=None, a_units=None, n=None, ea_kj_mol=None,
        children=terms,
    )
    db_session.refresh(multi)
    result = run(db_session, world.entry)
    assert by_ref(result)[multi.public_ref].applicability is A.applicable
    assert result.groups[0].representation_refs == (multi.public_ref,)


def test_loader_reads_plog_anchors_with_repeats_and_third_body_efficiencies(db_session, world):
    from app.services.kinetics_selection.loader import load_population, scan_population

    det = determination(db_session, world, "d")

    def children(k):
        for i, p in enumerate((1.0, 0.1, 1.0, 10.0), start=1):
            attach_kinetics_plog_entry(db_session, kinetics=k, entry_index=i, pressure_bar=p, a=1.0e-12)
        attach_kinetics_third_body_efficiency(db_session, kinetics=k, collider_species=world.ch4.species, efficiency=2.0)

    declared(
        db_session, world, det, model_kind=_model("plog"), a=None, a_units=None, n=None, ea_kj_mol=None,
        block=applicability(pressure_dependence="pressure_dependent", collider_kind="composition_dependent"),
        children=children,
    )
    scan = scan_population(db_session, reaction_entry_id=world.entry.id, request=REQUEST)
    (candidate,) = load_population(db_session, scan).candidates.values()
    assert candidate.plog_pressures_bar == (0.1, 1.0, 1.0, 10.0)
    assert candidate.efficiencies == {world.ch4.species.public_ref: 2.0}
    assert candidate.determination.determination_ref == det.public_ref and candidate.reactant_stoichiometries == (1, 1)


# -- the cap -------------------------------------------------------------------------------------


def _model(value):
    from app.db.models.common import KineticsModelKind

    return KineticsModelKind(value)


def _with(request, **changes):
    fields = {f: getattr(request, f) for f in request.__dataclass_fields__}
    fields.update(changes)
    return KineticsRequest(**fields)


def test_a_population_over_the_cap_is_a_coded_422_before_anything_is_assessed(db_session, world, monkeypatch):
    det = determination(db_session, world, "d")
    for _ in range(3):
        declared(db_session, world, det)

    def boom(*a, **k):  # pragma: no cover - failing here is the failure
        raise AssertionError("a record was assessed although the population was over the cap")

    monkeypatch.setattr(service_module, "assess_candidate", boom)
    with pytest.raises(CodedValueError) as refusal:
        run(db_session, world.entry, _with(REQUEST, max_candidates=2))
    assert refusal.value.code == "kinetics_selection_population_too_large"
    assert refusal.value.context == {"visible_candidates": 3, "limit": 2}
    monkeypatch.undo()
    assert len(run(db_session, world.entry, _with(REQUEST, max_candidates=3)).assessments) == 3  # exactly at the cap


def test_the_documented_cap_is_five_hundred_parents_and_five_hundred_and_one_is_refused(db_session, world):
    assert MAX_CANDIDATES == 500
    det = determination(db_session, world, "d")
    rows = [
        Kinetics(
            reaction_entry_id=world.entry.id, scientific_origin=_origin(), a=1.0e-12, direction=KineticsDirection.forward,
            literature_id=world.literature.id,
        )
        for _ in range(501)
    ]
    db_session.add_all(rows)
    db_session.flush()
    del det
    with pytest.raises(CodedValueError) as refusal:
        run(db_session, world.entry)
    assert refusal.value.context == {"visible_candidates": 501, "limit": 500}
    db_session.delete(rows[0])
    db_session.flush()
    assert len(run(db_session, world.entry).assessments) == 500


def _origin():
    from app.db.models.common import ScientificOriginKind

    return ScientificOriginKind.computed


def test_rejected_and_deprecated_rows_do_not_count_toward_the_cap(db_session, world):
    det = determination(db_session, world, "d")
    declared(db_session, world, det)
    declared(db_session, world, det, status=RecordReviewStatus.rejected)
    declared(db_session, world, det, status=RecordReviewStatus.deprecated)
    result = run(db_session, world.entry, _with(REQUEST, max_candidates=1))
    assert result.visible_candidates == 1 and result.total_rows == 3
    assert {e["reason"] for e in result.excluded_by_review} == {"terminal_review_status"}


# -- visibility under the curated profile --------------------------------------------------------


def curated():
    return set_current_read_profile(
        ResolvedReadProfile(profile=ReadProfile.curated, recommendation=ProfileRecommendation.approved_floor_only)
    )


def test_under_curated_hidden_rows_are_not_counted_listed_or_named_in_a_refusal(db_session, world):
    det = determination(db_session, world, "d")
    shown = declared(db_session, world, det)
    hidden = [declared(db_session, world, det, status=RecordReviewStatus.not_reviewed) for _ in range(5)]
    token = curated()
    try:
        result = run(db_session, world.entry)
        assert result.total_rows == 1 and result.visible_candidates == 1
        assert result.excluded_by_review == ()
        assert [a.kinetics_ref for a in result.assessments] == [shown.public_ref]
        hidden_refs = {h.public_ref for h in hidden}
        assert hidden_refs.isdisjoint({c.kinetics_ref for c in result.candidates})
        assert not _strings(_public(result)) & hidden_refs
        # Neither the cap nor a refusal reveals the hidden five: 1 visible row, limit 1 passes; limit below fails
        # naming only the visible count.
        assert len(run(db_session, world.entry, _with(REQUEST, max_candidates=1)).assessments) == 1
        declared(db_session, world, det)
        with pytest.raises(CodedValueError) as refusal:
            run(db_session, world.entry, _with(REQUEST, max_candidates=1))
        assert refusal.value.context == {"visible_candidates": 2, "limit": 1}
    finally:
        reset_current_read_profile(token)
    # The same population is fully visible without the profile.
    exploratory = run(db_session, world.entry)
    assert exploratory.total_rows == 7 and len(exploratory.assessments) == 7


def _public(result):
    return {
        "candidates": [c.to_dict() for c in result.candidates],
        "assessments": [a.to_dict() for a in result.assessments],
        "groups": [g.to_dict() for g in result.groups],
        "excluded": list(result.excluded_by_review),
        "unresolved": list(result.unresolved_refs),
        "unsupported": list(result.unsupported_refs),
        "notes": list(result.notes),
        "request": result.request.to_dict(),
    }


def _strings(value) -> set:
    if isinstance(value, str):
        return {value}
    if isinstance(value, dict):
        return set().union(*(_strings(k) | _strings(v) for k, v in value.items())) if value else set()
    if isinstance(value, (list, tuple)):
        return set().union(*(_strings(v) for v in value)) if value else set()
    return set()


def test_an_unknown_reaction_entry_is_not_found(db_session):
    with pytest.raises(NotFoundError):
        assess_reaction_entry_kinetics(db_session, reaction_entry_id=2**40, request=REQUEST, require_snapshot=False)


# -- no internal ids and no writes ---------------------------------------------------------------

#: The only integers a normalised record may carry, by exact path: ordinals and counts, never row ids.
ALLOWED_INTEGER_PATHS = {
    "id_rank",
    "arrhenius_terms",
    "reactant_stoichiometries[]",
    "product_stoichiometries[]",
    "chebyshev.n_temperature",
    "chebyshev.n_pressure",
    "applicability.version",
    "applicability.reaction_order",
    "protocol.version",
}


def _integer_paths(value, prefix=""):
    found = set()
    if isinstance(value, bool):
        return found
    if isinstance(value, int):
        return {prefix}
    if isinstance(value, dict):
        for k, v in value.items():
            found |= _integer_paths(v, f"{prefix}.{k}" if prefix else k)
    elif isinstance(value, (list, tuple)):
        for v in value:
            found |= _integer_paths(v, f"{prefix}[]")
    return found


def test_a_normalised_record_carries_public_refs_and_no_row_ids(db_session, world):
    det = determination(db_session, world, "d")
    def children(k):
        attach_kinetics_plog_entry(db_session, kinetics=k, entry_index=1, pressure_bar=1.0, a=1.0e-12)
        attach_kinetics_third_body_efficiency(db_session, kinetics=k, collider_species=world.ch4.species, efficiency=2.0)

    declared(
        db_session, world, det, model_kind=_model("plog"), a=None, a_units=None, n=None, ea_kj_mol=None,
        block=applicability(pressure_dependence="pressure_dependent", collider_kind="composition_dependent"),
        children=children,
    )
    result = run(db_session, world.entry)
    (candidate,) = (c.to_dict() for c in result.candidates)
    assert _integer_paths(candidate) <= ALLOWED_INTEGER_PATHS
    assert _integer_paths(candidate) >= {"id_rank", "arrhenius_terms", "reactant_stoichiometries[]", "applicability.version"}
    for ref in (candidate["kinetics_ref"], candidate["determination"]["determination_ref"]):
        assert isinstance(ref, str) and ref


def test_assessment_writes_nothing(db_session, world):
    det = determination(db_session, world, "d")
    declared(db_session, world, det)
    declared(db_session, world, None, block=applicability(phase=None))
    db_session.flush()
    counts = lambda: (  # noqa: E731
        db_session.scalar(select(func.count()).select_from(Kinetics)),
        db_session.scalar(select(func.count()).select_from(KineticsDetermination)),
        db_session.scalar(select(func.count()).select_from(RecordReview)),
    )
    before = counts()
    run(db_session, world.entry)
    assert counts() == before and not db_session.new and not db_session.dirty and not db_session.deleted


# -- loader facts and wiring ---------------------------------------------------------------------


def test_a_determination_on_a_transition_state_is_normalised_with_the_ts_public_ref(db_session, world):
    ts_entry = make_transition_state_entry(db_session, transition_state=make_transition_state(db_session, reaction_entry=world.entry))
    det = determination(
        db_session, world, "ts", target_kind=KineticsDeterminationTargetKind.resolved_channel,
        target_transition_state_entry_id=ts_entry.id,
    )
    declared(db_session, world, det, block=applicability(scope="resolved_channel"))
    request = _with(
        REQUEST, target=TargetRequest(KineticsDeterminationTargetKind.resolved_channel, transition_state_entry_ref=ts_entry.public_ref)
    )
    result = run(db_session, world.entry, request)
    (candidate,) = result.candidates
    assert candidate.determination.transition_state_entry_ref == ts_entry.public_ref
    assert candidate.determination.network_ref is None
    assert result.assessments[0].physically_eligible
    other = _with(REQUEST, target=TargetRequest(KineticsDeterminationTargetKind.resolved_channel, transition_state_entry_ref="tse_other"))
    assert "target_mismatch" in codes(run(db_session, world.entry, other).assessments[0])


def test_a_network_linked_fit_is_normalised_with_its_channel_and_solve_refs(db_session, world):
    from app.db.models.common import ArrheniusAUnits, PressureUnit, TemperatureUnit
    from app.db.models.network_pdep import NetworkKinetics

    network = make_network(db_session)
    source = make_network_state(db_session, network=network, kind=NetworkStateKind.well, composition_hash="a" * 64)
    sink = make_network_state(db_session, network=network, kind=NetworkStateKind.well, composition_hash="b" * 64)
    channel = make_network_channel(
        db_session, network=network, source_state=source, sink_state=sink,
        kind=NetworkChannelKind.isomerization, channel_key="k1",
    )
    solve = make_network_solve(db_session, network=network)
    nk = NetworkKinetics(
        channel_id=channel.id, solve_id=solve.id, model_kind=NetworkKineticsModelKind.chebyshev,
        tmin_k=300.0, tmax_k=2000.0, pmin_bar=0.01, pmax_bar=100.0, rate_units=ArrheniusAUnits.cm3_mol_s,
        pressure_units=PressureUnit.bar, temperature_units=TemperatureUnit.kelvin,
    )
    db_session.add(nk)
    db_session.flush()
    det = determination(
        db_session, world, "net", target_kind=KineticsDeterminationTargetKind.resolved_channel,
        target_network_channel_id=channel.id,
    )
    declared(db_session, world, det, block=applicability(scope="resolved_channel"), network_kinetics_id=nk.id)
    request = _with(
        REQUEST, target=TargetRequest(KineticsDeterminationTargetKind.resolved_channel, network_ref=network.public_ref, channel_key="k1")
    )
    (candidate,) = run(db_session, world.entry, request).candidates
    assert candidate.network_channel_ref == f"{network.public_ref}/k1" and candidate.network_solve_ref == solve.public_ref
    assert candidate.determination.network_ref == network.public_ref and candidate.determination.channel_key == "k1"
    assert candidate.reactant_stoichiometries == (1, 1)


def test_a_computed_records_evidence_is_evaluated_and_a_hard_fail_blocks_it_but_an_experimental_one_is_not(
    db_session, world, monkeypatch
):
    from app.services.trust.models import EvidenceBadge, EvidenceEvaluation, HardFailReason

    failed = EvidenceEvaluation(
        record_type="kinetics", record_id=1, rubric="computed_kinetics_v1", rubric_version="1",
        label=EvidenceBadge.hard_failed, checks={}, passed_count=0, possible_count=1, evidence_completeness=0.0,
        is_certified=False, hard_fail_reason=HardFailReason.source_calculation_hard_failed_for_required_role, check_results=(),
    )
    seen = []

    def evaluate(kinetics):
        seen.append(kinetics.public_ref)
        return failed

    monkeypatch.setattr(service_module, "evaluate_loaded_kinetics", evaluate)
    computed = declared(db_session, world, determination(db_session, world, "c"))
    experimental = declared(
        db_session, world, determination(db_session, world, "e"), scientific_origin=_origin_of("experimental")
    )
    result = run(db_session, world.entry)
    assert seen == [computed.public_ref]
    by = by_ref(result)
    assert by[computed.public_ref].applicability is A.applicable and not by[computed.public_ref].physically_eligible
    assert by[computed.public_ref].blocking and by[experimental.public_ref].physically_eligible
    assert [g.determination_ref for g in result.groups] == [determination_ref_of(db_session, experimental)]


def _origin_of(value):
    from app.db.models.common import ScientificOriginKind

    return ScientificOriginKind(value)


def determination_ref_of(session, kinetics):
    return session.get(KineticsDetermination, kinetics.determination_id).public_ref


def test_reactant_stoichiometry_is_read_from_the_reactants_not_the_products(db_session, world):
    from app.services.kinetics_selection.loader import load_population, scan_population

    h = world.h
    dimer = make_species_entry(db_session, make_species(db_session, smiles="[H][H]", multiplicity=1, inchi_key=next_inchi_key("KSE")))
    reaction = make_chem_reaction(db_session, reactants=[h.species, h.species], products=[dimer.species])
    entry = make_reaction_entry(db_session, reaction=reaction, reactant_entries=[h, h], product_entries=[dimer])
    k = make_kinetics(db_session, reaction_entry=entry, direction=KineticsDirection.forward)
    set_review(db_session, record_type=SubmissionRecordType.kinetics, record_id=k.id, status=RecordReviewStatus.approved)
    scan = scan_population(db_session, reaction_entry_id=entry.id, request=REQUEST)
    (candidate,) = load_population(db_session, scan).candidates.values()
    assert candidate.reactant_stoichiometries == (2,)


# -- alternates, negative terms, solves, the exclusion bound, the snapshot ----------------------------


def _pressure_request():
    from app.services.kinetics_selection import ColliderRequest

    return _with(
        REQUEST, pressure=PressureRequest(PressureKind.finite, 1.0, 1.0), collider=ColliderRequest(("sp_n2",))
    )


SURFACE = {"pressure_dependence": "pressure_dependent", "collider_kind": "not_dependent",
           "pressure_domain_min_bar": 0.01, "pressure_domain_max_bar": 100.0}


def test_a_troe_fit_and_a_plog_fit_of_one_determination_stay_one_candidate(db_session, world):
    from tests.services.scientific_read._factories import attach_kinetics_falloff

    det = determination(db_session, world, "one-master-equation-result")

    def falloff(k):
        attach_kinetics_falloff(db_session, kinetics=k, low_a=1.0e-30, troe_alpha=0.6, troe_t3=100.0, troe_t1=2000.0)

    def plog(k):
        for i, p in enumerate((0.1, 1.0, 10.0), start=1):
            attach_kinetics_plog_entry(db_session, kinetics=k, entry_index=i, pressure_bar=p, a=1.0e-12)

    troe = declared(db_session, world, det, model_kind=_model("troe"), block=applicability(**SURFACE), children=falloff,
                    pressure_context=_pressure_context("pressure_dependent"))
    surface = declared(db_session, world, det, model_kind=_model("plog"), a=None, a_units=None, n=None, ea_kj_mol=None,
                       block=applicability(**{**SURFACE, "pressure_domain_min_bar": None, "pressure_domain_max_bar": None}),
                       children=plog)
    result = run(db_session, world.entry, _pressure_request())
    assert all(x.physically_eligible for x in result.assessments), [(a.kinetics_ref, a.reasons) for a in result.assessments]
    (group,) = result.groups
    assert set(group.representation_refs) == {troe.public_ref, surface.public_ref}


def _pressure_context(value):
    from app.db.models.common import PressureContext

    return PressureContext(value)


def test_a_plog_term_with_a_negative_a_is_not_a_defect(db_session, world):
    det = determination(db_session, world, "d")

    def plog(k):
        attach_kinetics_plog_entry(db_session, kinetics=k, entry_index=1, pressure_bar=0.1, a=1.0e-12)
        attach_kinetics_plog_entry(db_session, kinetics=k, entry_index=2, pressure_bar=1.0, a=-3.0e-13)
        attach_kinetics_plog_entry(db_session, kinetics=k, entry_index=3, pressure_bar=10.0, a=2.0e-12)

    k = declared(db_session, world, det, model_kind=_model("plog"), a=None, a_units=None, n=None, ea_kj_mol=None,
                 block=applicability(**{**SURFACE, "pressure_domain_min_bar": None, "pressure_domain_max_bar": None}),
                 children=plog)
    result = run(db_session, world.entry, _pressure_request())
    assert by_ref(result)[k.public_ref].physically_eligible


def test_every_plog_entrys_units_are_read_from_its_own_row(db_session, world):
    from app.db.models.common import ArrheniusAUnits

    det = determination(db_session, world, "d")

    def plog(k):
        attach_kinetics_plog_entry(db_session, kinetics=k, entry_index=1, pressure_bar=0.1, a=1.0e-12)
        attach_kinetics_plog_entry(db_session, kinetics=k, entry_index=2, pressure_bar=1.0, a=1.0e-12,
                                   a_units=ArrheniusAUnits.per_s)

    k = declared(db_session, world, det, model_kind=_model("plog"), a=None, a_units=None, n=None, ea_kj_mol=None,
                 block=applicability(**{**SURFACE, "pressure_domain_min_bar": None, "pressure_domain_max_bar": None}),
                 children=plog)
    verdict = by_ref(run(db_session, world.entry, _pressure_request()))[k.public_ref]
    assert verdict.applicability is A.incompatible and "units_orders_inconsistent" in codes(verdict)


def test_a_normalised_record_carries_the_reference_temperature_and_both_sides_stoichiometry(db_session, world):
    det = determination(db_session, world, "d")
    declared(db_session, world, det, t0_k=298.0)
    (candidate,) = run(db_session, world.entry).candidates
    assert candidate.t0_k == 298.0 and candidate.to_dict()["t0_k"] == 298.0
    assert candidate.reactant_stoichiometries == (1, 1) and candidate.product_stoichiometries == (1, 1)


def _network_fit(db_session, world, det, solve, channel, name):
    from app.db.models.common import ArrheniusAUnits, PressureUnit, TemperatureUnit
    from app.db.models.network_pdep import NetworkKinetics

    nk = NetworkKinetics(
        channel_id=channel.id, solve_id=solve.id, model_kind=NetworkKineticsModelKind.chebyshev,
        tmin_k=300.0, tmax_k=2000.0, pmin_bar=0.01, pmax_bar=100.0, rate_units=ArrheniusAUnits.cm3_mol_s,
        pressure_units=PressureUnit.bar, temperature_units=TemperatureUnit.kelvin,
    )
    db_session.add(nk)
    db_session.flush()

    def plog(k):
        attach_kinetics_plog_entry(db_session, kinetics=k, entry_index=1, pressure_bar=0.1, a=1.0e-12)
        attach_kinetics_plog_entry(db_session, kinetics=k, entry_index=2, pressure_bar=10.0, a=1.0e-12)

    return declared(db_session, world, det, block=applicability(scope="resolved_channel", **SURFACE),
                    network_kinetics_id=nk.id, model_kind=_model("plog"), a=None, a_units=None, n=None, ea_kj_mol=None,
                    children=plog)


def _network_world(db_session, world, key):
    network = make_network(db_session)
    source = make_network_state(db_session, network=network, kind=NetworkStateKind.well, composition_hash="a" * 64)
    sink = make_network_state(db_session, network=network, kind=NetworkStateKind.well, composition_hash="b" * 64)
    channel = make_network_channel(db_session, network=network, source_state=source, sink_state=sink,
                                   kind=NetworkChannelKind.isomerization, channel_key="k1")
    det = determination(db_session, world, key, target_kind=KineticsDeterminationTargetKind.resolved_channel,
                        target_network_channel_id=channel.id)
    return network, channel, det


def test_network_fits_of_one_determination_from_different_solves_are_not_alternates(db_session, world):
    network, channel, det = _network_world(db_session, world, "mixed-solves")
    first = _network_fit(db_session, world, det, make_network_solve(db_session, network=network), channel, "a")
    second = _network_fit(db_session, world, det, make_network_solve(db_session, network=network), channel, "b")
    request = _with(
        _pressure_request(),
        target=TargetRequest(KineticsDeterminationTargetKind.resolved_channel, network_ref=network.public_ref, channel_key="k1"),
    )
    result = run(db_session, world.entry, request)
    verdicts = by_ref(result)
    for k in (first, second):
        assert verdicts[k.public_ref].applicability is A.incompatible
        assert "determination_spans_network_solves" in codes(verdicts[k.public_ref])
    assert result.groups == ()


def test_network_fits_of_one_determination_from_one_solve_are_alternates(db_session, world):
    network, channel, det = _network_world(db_session, world, "one-solve")
    solve = make_network_solve(db_session, network=network)
    first = _network_fit(db_session, world, det, solve, channel, "a")
    second = _network_fit(db_session, world, det, solve, channel, "b")
    request = _with(
        _pressure_request(),
        target=TargetRequest(KineticsDeterminationTargetKind.resolved_channel, network_ref=network.public_ref, channel_key="k1"),
    )
    (group,) = run(db_session, world.entry, request).groups
    assert set(group.representation_refs) == {first.public_ref, second.public_ref}


def test_the_exclusion_listing_is_bounded_and_the_total_is_always_reported(db_session, world):
    from app.services.kinetics_selection.loader import MAX_EXCLUDED_LISTED

    det = determination(db_session, world, "d")
    declared(db_session, world, det)
    for _ in range(MAX_EXCLUDED_LISTED + 5):
        declared(db_session, world, det, status=RecordReviewStatus.rejected)
    result = run(db_session, world.entry)
    assert result.excluded_count == MAX_EXCLUDED_LISTED + 5
    assert len(result.excluded_by_review) == MAX_EXCLUDED_LISTED
    assert result.visible_candidates == 1


def test_the_read_runs_in_one_read_only_repeatable_read_snapshot(db_engine, monkeypatch):
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    seen = {}
    real = service_module.scan_population

    def spy(session, **kwargs):
        seen["isolation"] = session.scalar(text("SELECT current_setting('transaction_isolation')"))
        seen["read_only"] = session.scalar(text("SELECT current_setting('transaction_read_only')"))
        return real(session, **kwargs)

    monkeypatch.setattr(service_module, "scan_population", spy)
    with Session(db_engine) as session:
        with pytest.raises(NotFoundError):
            assess_reaction_entry_kinetics(session, reaction_entry_id=2**40, request=REQUEST)
        session.rollback()
    assert seen == {"isolation": "repeatable read", "read_only": "on"}


def test_a_session_already_in_a_weaker_transaction_is_refused_unless_the_caller_accepts_it(db_session, world):
    from app.services.read_snapshot import SnapshotNotConsistentError

    declared(db_session, world, determination(db_session, world, "d"))
    with pytest.raises(SnapshotNotConsistentError, match="REPEATABLE READ"):
        assess_reaction_entry_kinetics(db_session, reaction_entry_id=world.entry.id, request=REQUEST)
    accepted = assess_reaction_entry_kinetics(
        db_session, reaction_entry_id=world.entry.id, request=REQUEST, require_snapshot=False
    )
    assert accepted.snapshot_isolation in {"read committed", "repeatable read", "serializable"}
    assert len(accepted.assessments) == 1
