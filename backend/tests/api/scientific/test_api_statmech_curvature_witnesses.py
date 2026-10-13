"""Chunk 6.3: curvature witnesses beside the preserved frequency pointer.

Design: ``statmech_chunk6_design_2026-10-12.md`` (PR 6.3). Each test names the
mutation it kills. Before this PR a source calculation that carries a frequency
result or a Hessian under any role other than ``freq`` was invisible on
``include=frequencies``.
"""

from __future__ import annotations

from app.db.models.common import (
    CalculationType,
    RecordReviewStatus,
    ScientificOriginKind,
    StatmechCalculationRole,
    StatmechTreatmentKind,
    SubmissionRecordType,
)
from app.db.models.statmech import Statmech
from tests.services.scientific_read._factories import (
    attach_freq_result,
    attach_hessian,
    attach_statmech_source_calculation,
    make_calculation,
    make_chem_reaction,
    make_geometry,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_statmech,
    make_transition_state,
    make_transition_state_entry,
    next_inchi_key,
    set_review,
)

R = StatmechCalculationRole
T = CalculationType


def _world(db_session):
    species = make_species(db_session, inchi_key=next_inchi_key("CURV"))
    entry = make_species_entry(db_session, species)
    return entry, make_statmech(db_session, species_entry=entry)


def _link(db_session, sm, entry, *, role, ctype, freq=False, hessian=False):
    calc = make_calculation(db_session, type=ctype, species_entry_id=entry.id)
    if freq:
        attach_freq_result(db_session, calculation=calc, frequencies_cm1=[100.0, 200.0, 300.0])
    if hessian:
        attach_hessian(db_session, calculation=calc, geometry=make_geometry(db_session), natoms=3)
    attach_statmech_source_calculation(db_session, statmech=sm, calculation=calc, role=role)
    return calc


def _read(client, sm):
    body = client.get(f"/api/v1/scientific/statmech/{sm.public_ref}", params={"include": "frequencies"}).json()
    return body["record"]


def test_opt_with_frequency_result_is_a_witness_and_pointer_is_unchanged(client, db_session):
    """Kills: witnesses filtered to ``role == freq``; flags redefined as eligibility."""
    entry, sm = _world(db_session)
    calc = _link(db_session, sm, entry, role=R.opt, ctype=T.opt, freq=True)
    rec = _read(client, sm)
    f = rec["frequencies"]
    assert f["source_freq_calculation_refs"] == []
    assert f["curvature_witnesses"] == [
        {
            "calculation_ref": calc.public_ref,
            "role": "opt",
            "calculation_type": "opt",
            "has_frequency_result": True,
            "has_hessian": False,
        }
    ]
    assert rec["available_sections"]["has_frequencies"] is False
    assert rec["evidence_summary"]["has_freq_calculation"] is False


def test_composite_with_only_a_hessian_is_a_witness(client, db_session):
    """Kills: the Hessian table not being consulted."""
    entry, sm = _world(db_session)
    calc = _link(db_session, sm, entry, role=R.composite, ctype=T.composite, hessian=True)
    (w,) = _read(client, sm)["frequencies"]["curvature_witnesses"]
    assert w["calculation_ref"] == calc.public_ref
    assert (w["role"], w["calculation_type"]) == ("composite", "composite")
    assert (w["has_frequency_result"], w["has_hessian"]) == (False, True)


def test_link_without_curvature_is_not_a_witness(client, db_session):
    """Kills: every linked calculation being emitted as a witness."""
    entry, sm = _world(db_session)
    _link(db_session, sm, entry, role=R.sp, ctype=T.sp)
    assert _read(client, sm)["frequencies"]["curvature_witnesses"] == []


def test_freq_role_pointer_is_identical_and_witness_is_added(client, db_session):
    """Kills: redefining the pointer (golden dict for a freq-role link)."""
    entry, sm = _world(db_session)
    calc = _link(db_session, sm, entry, role=R.freq, ctype=T.freq, freq=True, hessian=True)
    rec = _read(client, sm)
    f = rec["frequencies"]
    assert f["source_freq_calculation_refs"] == [calc.public_ref]
    assert f["frequency_scale_factor_value"] is None
    assert f["curvature_witnesses"] == [
        {
            "calculation_ref": calc.public_ref,
            "role": "freq",
            "calculation_type": "freq",
            "has_frequency_result": True,
            "has_hessian": True,
        }
    ]
    assert rec["available_sections"]["has_frequencies"] is True


def test_freq_role_link_without_stored_curvature_keeps_pointer_but_has_no_witness(client, db_session):
    """Kills: the pointer being derived from the witnesses (link presence vs result presence)."""
    entry, sm = _world(db_session)
    calc = _link(db_session, sm, entry, role=R.freq, ctype=T.freq)
    f = _read(client, sm)["frequencies"]
    assert f["source_freq_calculation_refs"] == [calc.public_ref]
    assert f["curvature_witnesses"] == []


def test_unlinked_calculation_is_never_borrowed(client, db_session):
    """Kills: widening the query from the record's links to the owner's calculations."""
    entry, sm = _world(db_session)
    unlinked = make_calculation(db_session, type=T.freq, species_entry_id=entry.id)
    attach_freq_result(db_session, calculation=unlinked, frequencies_cm1=[100.0])
    _link(db_session, sm, entry, role=R.sp, ctype=T.sp)
    assert _read(client, sm)["frequencies"]["curvature_witnesses"] == []


def test_witnesses_are_per_role_calculation_pair_in_role_then_id_order(client, db_session):
    """Kills: de-duplicating by calculation, and unordered output."""
    entry, sm = _world(db_session)
    c_comp = _link(db_session, sm, entry, role=R.composite, ctype=T.composite, freq=True)
    c_opt_a = _link(db_session, sm, entry, role=R.opt, ctype=T.opt, freq=True)
    c_opt_b = _link(db_session, sm, entry, role=R.opt, ctype=T.opt, hessian=True)
    # The same calculation linked under a second role is a second pair.
    attach_statmech_source_calculation(db_session, statmech=sm, calculation=c_opt_a, role=R.freq)
    got = [(w["role"], w["calculation_ref"]) for w in _read(client, sm)["frequencies"]["curvature_witnesses"]]
    assert got == [
        ("opt", c_opt_a.public_ref),
        ("opt", c_opt_b.public_ref),
        ("freq", c_opt_a.public_ref),
        ("composite", c_comp.public_ref),
    ]


def test_ts_owned_record_lists_the_ts_freq_calculation(client, db_session):
    """Kills: an owner-specific (species-only) code path."""
    r1, r2, p = (
        make_species_entry(db_session, make_species(db_session, inchi_key=next_inchi_key(tag)))
        for tag in ("CVR1", "CVR2", "CVP")
    )
    reaction = make_chem_reaction(
        db_session, reactants=[r1.species, r2.species], products=[p.species], reversible=False
    )
    rxn_entry = make_reaction_entry(
        db_session, reaction=reaction, reactant_entries=[r1, r2], product_entries=[p]
    )
    ts = make_transition_state_entry(
        db_session, transition_state=make_transition_state(db_session, reaction_entry=rxn_entry), multiplicity=2
    )
    sm = Statmech(
        transition_state_entry_id=ts.id,
        scientific_origin=ScientificOriginKind.computed,
        statmech_treatment=StatmechTreatmentKind.rrho,
    )
    db_session.add(sm)
    db_session.flush()
    calc = make_calculation(db_session, type=T.freq, transition_state_entry_id=ts.id)
    attach_freq_result(db_session, calculation=calc, frequencies_cm1=[-500.0, 100.0])
    attach_statmech_source_calculation(db_session, statmech=sm, calculation=calc, role=R.freq)
    f = _read(client, sm)["frequencies"]
    assert f["source_freq_calculation_refs"] == [calc.public_ref]
    assert [w["calculation_ref"] for w in f["curvature_witnesses"]] == [calc.public_ref]
    assert f["curvature_witnesses"][0]["has_frequency_result"] is True


def test_witness_carries_no_internal_id_or_verdict_field(client, db_session):
    """Kills: leaking ids or adding an eligibility field to a witness."""
    entry, sm = _world(db_session)
    _link(db_session, sm, entry, role=R.opt, ctype=T.opt, freq=True)
    (w,) = _read(client, sm)["frequencies"]["curvature_witnesses"]
    assert set(w) == {"calculation_ref", "role", "calculation_type", "has_frequency_result", "has_hessian"}


def _curated_world(db_session, *, calc_status):
    entry, sm = _world(db_session)
    hidden = _link(db_session, sm, entry, role=R.composite, ctype=T.composite, hessian=True)
    if calc_status is not None:
        set_review(db_session, record_type=SubmissionRecordType.calculation, record_id=hidden.id, status=calc_status)
    # Approve last: an approved statmech is immutable, so its links must exist first.
    set_review(db_session, record_type=SubmissionRecordType.statmech, record_id=sm.id, status=RecordReviewStatus.approved)
    return sm, hidden


def _read_profile(client, sm, profile):
    r = client.get(
        f"/api/v1/scientific/statmech/{sm.public_ref}", params={"include": "frequencies", "profile": profile}
    )
    assert r.status_code == 200
    return r.json()["record"]["frequencies"]["curvature_witnesses"]


def test_curated_profile_does_not_reveal_a_calculation_it_hides(client, db_session):
    """Kills: witnesses ignoring the profile's calculation visibility (leaking has_hessian of a 404 calc)."""
    sm, hidden = _curated_world(db_session, calc_status=None)  # not_reviewed
    # The calculation read itself hides it under curated; the witness list must agree.
    assert client.get(f"/api/v1/scientific/calculations/{hidden.public_ref}", params={"profile": "curated"}).status_code == 404
    assert _read_profile(client, sm, "curated") == []
    # Exploratory still lists it (the non-vacuous side of the same fixture).
    (w,) = _read_profile(client, sm, "exploratory")
    assert w["calculation_ref"] == hidden.public_ref and w["has_hessian"] is True


def test_curated_profile_lists_an_approved_calculation(client, db_session):
    """Kills: dropping every witness under curated."""
    sm, hidden = _curated_world(db_session, calc_status=RecordReviewStatus.approved)
    assert [w["calculation_ref"] for w in _read_profile(client, sm, "curated")] == [hidden.public_ref]
