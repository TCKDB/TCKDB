"""Under ``profile=curated`` three more reads must not name records the curated profile hides.

Same rule as the statmech source blocks (#765): under a review floor a ref whose own detail route answers 404 is
never named; exploratory output is unchanged; record-level facts (levels, evidence, availability flags) are not
filtered.

  (a) transition-state-entry ``include=statmech`` -> ``statmech[].source_calculations``
  (b) species-entry thermo provenance -> the statmech fallback's primary / freq / sp calculations (and the conformer
      traced through them)
  (c) statmech ``include=conformers`` -> ``conformer_group_ref``

Each test names the mutation it kills.
"""

from __future__ import annotations

import pytest
from sqlalchemy import event

from app.db.models.common import (
    CalculationType,
    RecordReviewStatus,
    ScientificOriginKind,
    StatmechCalculationRole,
    StatmechTreatmentKind,
    SubmissionRecordType,
    ThermoCalculationRole,
    ThermoTargetKind,
)
from app.db.models.statmech import Statmech
from tests.services.scientific_read._factories import (
    attach_statmech_source_calculation,
    attach_thermo_source_calculation,
    make_calculation,
    make_chem_reaction,
    make_conformer_group,
    make_conformer_observation,
    make_lot,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_statmech,
    make_thermo_scalar,
    make_transition_state,
    make_transition_state_entry,
    next_inchi_key,
    set_review,
)

R = StatmechCalculationRole
T = CalculationType
APPROVED = RecordReviewStatus.approved
BASE = "/api/v1/scientific"

EXPECTED_THERMO_LIST_STATEMENTS = 20  # measured identical on origin/main
EXPECTED_STATMECH_SEARCH_CONFORMERS_STATEMENTS = 60  # measured identical on origin/main, n=8
EXPECTED_CURATED_EXTRA_STATEMENTS = 0
#: The central ref guard resolves every ref of a response in one batched statement (independent of the page size), on
#: the route families it is enabled for.
EXPECTED_REF_GUARD_STATEMENTS = 1


def _review(db_session, kind, row, status=APPROVED):
    set_review(db_session, record_type=kind, record_id=row.id, status=status)


def _entry(db_session, tag):
    return make_species_entry(db_session, make_species(db_session, inchi_key=next_inchi_key(tag)))


# --- (a) transition-state entry include=statmech ---------------------------------------------------------------


def _ts_world(db_session):
    r1, r2, p = _entry(db_session, "RSA1"), _entry(db_session, "RSA2"), _entry(db_session, "RSAP")
    reaction = make_chem_reaction(db_session, reactants=[r1.species, r2.species], products=[p.species], reversible=False)
    rxn_entry = make_reaction_entry(db_session, reaction=reaction, reactant_entries=[r1, r2], product_entries=[p])
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
    shown = make_calculation(db_session, type=T.opt, transition_state_entry_id=ts.id)
    hidden = make_calculation(db_session, type=T.freq, transition_state_entry_id=ts.id)
    attach_statmech_source_calculation(db_session, statmech=sm, calculation=shown, role=R.opt)
    attach_statmech_source_calculation(db_session, statmech=sm, calculation=hidden, role=R.freq)
    _review(db_session, SubmissionRecordType.calculation, shown)
    _review(db_session, SubmissionRecordType.transition_state_entry, ts)
    _review(db_session, SubmissionRecordType.statmech, sm)  # approved last: its links exist first
    return ts, sm, shown, hidden


def _ts_sources(client, ts, profile):
    r = client.get(f"{BASE}/transition-state-entries/{ts.public_ref}", params={"include": "statmech", "profile": profile})
    assert r.status_code == 200, r.text
    (sm,) = r.json()["record"]["statmech"]
    return sm


def test_ts_entry_statmech_curated_omits_hidden_calculation(client, db_session):
    """Kills: no profile filtering in ``_build_statmech_summaries`` (today's leak)."""
    ts, sm, shown, hidden = _ts_world(db_session)
    assert client.get(f"{BASE}/calculations/{hidden.public_ref}", params={"profile": "curated"}).status_code == 404
    summary = _ts_sources(client, ts, "curated")
    assert [c["calculation_ref"] for c in summary["source_calculations"]] == [shown.public_ref]
    assert hidden.public_ref not in str(summary)


def test_ts_entry_statmech_exploratory_lists_everything(client, db_session):
    """Kills: filtering regardless of profile; also pins default == exploratory."""
    ts, sm, shown, hidden = _ts_world(db_session)
    explicit = _ts_sources(client, ts, "exploratory")
    default = client.get(f"{BASE}/transition-state-entries/{ts.public_ref}", params={"include": "statmech"}).json()["record"]["statmech"][0]
    assert explicit == default
    assert {c["calculation_ref"] for c in explicit["source_calculations"]} == {shown.public_ref, hidden.public_ref}


def _ts_world_with_hidden_statmech(db_session):
    ts, sm, shown, hidden = _ts_world(db_session)
    # A second statmech on the same entry that is not approved; the first (with its calculations) is.
    hidden_sm = Statmech(
        transition_state_entry_id=ts.id,
        scientific_origin=ScientificOriginKind.computed,
        statmech_treatment=StatmechTreatmentKind.rrho,
    )
    db_session.add(hidden_sm)
    db_session.flush()
    _review(db_session, SubmissionRecordType.transition_state, ts.transition_state)
    return ts, sm, hidden_sm


def test_ts_entry_include_statmech_omits_hidden_statmech_records(client, db_session):
    """Kills: listing every owned statmech under a floor (only their source calculations were filtered)."""
    ts, sm, hidden_sm = _ts_world_with_hidden_statmech(db_session)
    assert client.get(f"{BASE}/statmech/{hidden_sm.public_ref}", params={"profile": "curated"}).status_code == 404
    r = client.get(f"{BASE}/transition-state-entries/{ts.public_ref}", params={"include": "statmech", "profile": "curated"})
    assert r.status_code == 200, r.text
    assert [x["statmech_ref"] for x in r.json()["record"]["statmech"]] == [sm.public_ref]
    x = client.get(f"{BASE}/transition-state-entries/{ts.public_ref}", params={"include": "statmech", "profile": "exploratory"})
    assert sorted(m["statmech_ref"] for m in x.json()["record"]["statmech"]) == sorted([sm.public_ref, hidden_sm.public_ref])


def test_ts_concept_include_statmech_omits_hidden_statmech_records(client, db_session):
    """Kills: the same leak on the transition-state concept route."""
    ts, sm, hidden_sm = _ts_world_with_hidden_statmech(db_session)
    r = client.get(
        f"{BASE}/transition-states/{ts.transition_state.public_ref}", params={"include": "statmech", "profile": "curated"}
    )
    assert r.status_code == 200, r.text
    (entry_block,) = r.json()["record"]["statmech"]
    assert [m["statmech_ref"] for m in entry_block["statmech"]] == [sm.public_ref]
    x = client.get(
        f"{BASE}/transition-states/{ts.transition_state.public_ref}", params={"include": "statmech", "profile": "exploratory"}
    )
    (entry_x,) = x.json()["record"]["statmech"]
    assert len(entry_x["statmech"]) == 2


# --- (b) thermo provenance statmech fallback --------------------------------------------------------------------


def _thermo_world(db_session, *, freq_status):
    entry = _entry(db_session, "THF")
    sm = make_statmech(db_session, species_entry=entry)
    sp = make_calculation(db_session, type=T.sp, species_entry_id=entry.id)
    freq = make_calculation(db_session, type=T.freq, species_entry_id=entry.id, lot_id=make_lot(db_session).id)
    attach_statmech_source_calculation(db_session, statmech=sm, calculation=sp, role=R.sp)
    attach_statmech_source_calculation(db_session, statmech=sm, calculation=freq, role=R.freq)
    _review(db_session, SubmissionRecordType.calculation, sp)
    if freq_status is not None:
        _review(db_session, SubmissionRecordType.calculation, freq, freq_status)
    thermo = make_thermo_scalar(db_session, species_entry=entry, statmech_id=sm.id)
    _review(db_session, SubmissionRecordType.statmech, sm)
    _review(db_session, SubmissionRecordType.thermo, thermo)
    return entry, sm, sp, freq


def _provenance(client, entry, profile):
    r = client.get(f"{BASE}/species-entries/{entry.public_ref}/thermo", params={"profile": profile})
    assert r.status_code == 200, r.text
    (rec,) = r.json()["records"]
    return rec["provenance"], rec


def test_thermo_curated_does_not_name_a_hidden_statmech_freq_calculation(client, db_session):
    """Kills: building the provenance fallback from every statmech link under a review floor."""
    entry, sm, sp, freq = _thermo_world(db_session, freq_status=None)
    assert client.get(f"{BASE}/calculations/{freq.public_ref}", params={"profile": "curated"}).status_code == 404
    prov, rec = _provenance(client, entry, "curated")
    assert prov["freq_calculation_ref"] is None
    assert prov.get("freq_calculation_id") is None
    assert freq.public_ref not in str(rec)
    # The visible sp calculation is still named (non-vacuous), and the statmech link itself is.
    assert prov["sp_calculation_ref"] == sp.public_ref
    assert prov["statmech_ref"] == sm.public_ref


def test_thermo_exploratory_still_names_the_freq_calculation(client, db_session):
    """Kills: filtering regardless of profile."""
    entry, sm, sp, freq = _thermo_world(db_session, freq_status=None)
    prov, _ = _provenance(client, entry, "exploratory")
    assert prov["freq_calculation_ref"] == freq.public_ref
    default = client.get(f"{BASE}/species-entries/{entry.public_ref}/thermo").json()["records"][0]["provenance"]
    assert default == prov


def test_thermo_curated_keeps_an_approved_freq_calculation(client, db_session):
    """Kills: dropping every statmech-derived calculation under curated."""
    entry, sm, sp, freq = _thermo_world(db_session, freq_status=APPROVED)
    prov, _ = _provenance(client, entry, "curated")
    assert prov["freq_calculation_ref"] == freq.public_ref


def test_thermo_curated_record_level_levels_do_not_vary(client, db_session):
    """Kills: filtering the statmech sources that feed levels/evidence (pins the scope decision)."""
    entry, *_ = _thermo_world(db_session, freq_status=None)
    _, curated = _provenance(client, entry, "curated")
    _, exploratory = _provenance(client, entry, "exploratory")
    assert curated["levels"] == exploratory["levels"]
    # Non-vacuous: the hidden freq calculation's level of theory is still reported as the record's frequency level.
    assert curated["levels"]["frequency"] is not None


# --- (b2) thermo's OWN source calculations ----------------------------------------------------------------------


def _own_sources_world(db_session, hidden_status=None):
    entry = _entry(db_session, "THO")
    thermo = make_thermo_scalar(db_session, species_entry=entry)
    hidden_sp = make_calculation(db_session, type=T.sp, species_entry_id=entry.id, lot_id=make_lot(db_session).id)
    shown_freq = make_calculation(db_session, type=T.freq, species_entry_id=entry.id)
    attach_thermo_source_calculation(db_session, thermo=thermo, calculation=hidden_sp, role=ThermoCalculationRole.sp)
    attach_thermo_source_calculation(db_session, thermo=thermo, calculation=shown_freq, role=ThermoCalculationRole.freq)
    _review(db_session, SubmissionRecordType.calculation, shown_freq)
    if hidden_status is not None:
        _review(db_session, SubmissionRecordType.calculation, hidden_sp, hidden_status)
    _review(db_session, SubmissionRecordType.thermo, thermo)
    return entry, hidden_sp, shown_freq


def test_thermo_curated_does_not_name_its_own_hidden_source_calculation(client, db_session):
    """Kills: filtering only the statmech fallback and not the thermo's own source rows."""
    entry, hidden_sp, shown_freq = _own_sources_world(db_session)
    assert client.get(f"{BASE}/calculations/{hidden_sp.public_ref}", params={"profile": "curated"}).status_code == 404
    prov, rec = _provenance(client, entry, "curated")
    assert prov["sp_calculation_ref"] is None
    assert hidden_sp.public_ref not in str(rec)
    # Non-vacuous: the approved freq source is still named.
    assert prov["freq_calculation_ref"] == shown_freq.public_ref


def test_thermo_own_sources_exploratory_unchanged_and_levels_do_not_vary(client, db_session):
    """Kills: filtering regardless of profile, or feeding the filter into record-level levels/evidence."""
    entry, hidden_sp, shown_freq = _own_sources_world(db_session)
    prov, rec = _provenance(client, entry, "exploratory")
    assert prov["sp_calculation_ref"] == hidden_sp.public_ref
    default = client.get(f"{BASE}/species-entries/{entry.public_ref}/thermo").json()["records"][0]
    assert default["provenance"] == prov
    _, curated = _provenance(client, entry, "curated")
    assert curated["levels"] == rec["levels"]
    assert curated["levels"]["energy"] is not None  # the hidden sp's level still describes the record


def test_thermo_search_surface_is_filtered_too(client, db_session):
    """Kills: filtering only the species thermo list and not the search surface that reuses the builder."""
    entry, hidden_sp, shown_freq = _own_sources_world(db_session)
    # Curated search also needs the species and its entry to clear the floor.
    _review(db_session, SubmissionRecordType.species_entry, entry)
    _review(db_session, SubmissionRecordType.species, entry.species)
    r = client.post(f"{BASE}/thermo/search?profile=curated", json={"inchi_key": entry.species.inchi_key})
    assert r.status_code == 200, r.text
    assert r.json()["records"], "the approved thermo must be returned"
    assert hidden_sp.public_ref not in str(r.json()["records"])
    assert shown_freq.public_ref in str(r.json()["records"])


# --- (b3) thermo's other review-gated refs: target group, statmech, traced conformer ----------------------------


def _gated_refs_world(db_session, *, group_status=None, observation_status=None, statmech_status=None):
    entry = _entry(db_session, "THG")
    sm = make_statmech(db_session, species_entry=entry)
    group = make_conformer_group(db_session, entry, label="g")
    obs = make_conformer_observation(db_session, conformer_group=group)
    calc = make_calculation(db_session, type=T.sp, species_entry_id=entry.id, conformer_observation_id=obs.id)
    thermo = make_thermo_scalar(db_session, species_entry=entry, statmech_id=sm.id)
    attach_thermo_source_calculation(db_session, thermo=thermo, calculation=calc, role=ThermoCalculationRole.sp)
    thermo.thermodynamic_target_kind = ThermoTargetKind.single_conformer
    thermo.target_conformer_group_id = group.id
    db_session.flush()
    _review(db_session, SubmissionRecordType.calculation, calc)
    for kind, row, status in (
        (SubmissionRecordType.conformer_group, group, group_status),
        (SubmissionRecordType.conformer_observation, obs, observation_status),
        (SubmissionRecordType.statmech, sm, statmech_status),
    ):
        if status is not None:
            _review(db_session, kind, row, status)
    _review(db_session, SubmissionRecordType.thermo, thermo)
    return entry, sm, group, obs


def test_thermo_curated_does_not_name_a_hidden_target_group_statmech_or_traced_conformer(client, db_session):
    """Kills: leaving thermodynamic_target / provenance.statmech_ref / provenance conformer refs unfiltered."""
    entry, sm, group, obs = _gated_refs_world(db_session)  # none of the three is approved
    for path, row in (("conformer-groups", group), ("conformer-observations", obs), ("statmech", sm)):
        assert client.get(f"{BASE}/{path}/{row.public_ref}", params={"profile": "curated"}).status_code == 404
    prov, rec = _provenance(client, entry, "curated")
    assert rec["thermodynamic_target"] == {"kind": "single_conformer", "conformer_group_ref": None}
    assert prov["statmech_ref"] is None
    assert prov["conformer_observation_ref"] is None
    assert prov["conformer_group_ref"] is None
    for hidden in (group.public_ref, obs.public_ref, sm.public_ref):
        assert hidden not in str(rec)


def test_thermo_exploratory_still_names_those_refs(client, db_session):
    """Kills: filtering regardless of profile; pins default == exploratory."""
    entry, sm, group, obs = _gated_refs_world(db_session)
    prov, rec = _provenance(client, entry, "exploratory")
    assert rec["thermodynamic_target"]["conformer_group_ref"] == group.public_ref
    assert (prov["statmech_ref"], prov["conformer_observation_ref"], prov["conformer_group_ref"]) == (
        sm.public_ref,
        obs.public_ref,
        group.public_ref,
    )
    default = client.get(f"{BASE}/species-entries/{entry.public_ref}/thermo").json()["records"][0]
    assert default["provenance"] == prov and default["thermodynamic_target"] == rec["thermodynamic_target"]


def test_thermo_curated_names_each_approved_ref_independently(client, db_session):
    """Kills: nulling all gated refs together (an approved group/statmech with an unapproved observation)."""
    entry, sm, group, obs = _gated_refs_world(
        db_session, group_status=APPROVED, observation_status=None, statmech_status=APPROVED
    )
    prov, rec = _provenance(client, entry, "curated")
    assert rec["thermodynamic_target"]["conformer_group_ref"] == group.public_ref
    assert prov["conformer_group_ref"] == group.public_ref
    assert prov["statmech_ref"] == sm.public_ref
    assert prov["conformer_observation_ref"] is None  # the one unapproved ref


# --- (c) statmech include=conformers ----------------------------------------------------------------------------


def _conformer_world(db_session, hidden_status=None):
    entry = _entry(db_session, "CNF")
    sm = make_statmech(db_session, species_entry=entry)
    shown = make_conformer_group(db_session, entry, label="shown")
    hidden = make_conformer_group(db_session, entry, label="hidden")
    _review(db_session, SubmissionRecordType.conformer_group, shown)
    if hidden_status is not None:
        _review(db_session, SubmissionRecordType.conformer_group, hidden, hidden_status)
    _review(db_session, SubmissionRecordType.statmech, sm)
    return sm, shown, hidden


def _conformer_refs(client, sm, profile):
    r = client.get(f"{BASE}/statmech/{sm.public_ref}", params={"include": "conformers", "profile": profile})
    assert r.status_code == 200, r.text
    rec = r.json()["record"]
    return [c["conformer_group_ref"] for c in rec["conformers"]], rec


def test_statmech_conformers_curated_omits_hidden_group(client, db_session):
    """Kills: no profile filtering in ``_build_conformer_context`` (today's leak)."""
    sm, shown, hidden = _conformer_world(db_session)
    assert client.get(f"{BASE}/conformer-groups/{hidden.public_ref}", params={"profile": "curated"}).status_code == 404
    refs, rec = _conformer_refs(client, sm, "curated")
    assert refs == [shown.public_ref]
    # Record-level fact: the species entry still has conformer context.
    assert rec["available_sections"]["has_conformers"] is True


def test_statmech_conformers_exploratory_lists_every_group(client, db_session):
    """Kills: filtering regardless of profile; pins default == exploratory."""
    sm, shown, hidden = _conformer_world(db_session)
    refs, _ = _conformer_refs(client, sm, "exploratory")
    assert sorted(refs) == sorted([shown.public_ref, hidden.public_ref])
    default = client.get(f"{BASE}/statmech/{sm.public_ref}", params={"include": "conformers"}).json()["record"]["conformers"]
    assert [c["conformer_group_ref"] for c in default] == refs


# --- review statuses: every status below the floor is hidden, not only ``not_reviewed`` -------------------------

BELOW_FLOOR = [RecordReviewStatus.under_review, RecordReviewStatus.rejected, RecordReviewStatus.deprecated]


@pytest.mark.parametrize("status", BELOW_FLOOR)
def test_thermo_own_source_hidden_for_every_status_below_the_floor(client, db_session, status):
    """Kills: hiding only ``not_reviewed`` (the visibility check must be the floor, not one status)."""
    entry, hidden_sp, shown_freq = _own_sources_world(db_session, hidden_status=status)
    assert client.get(f"{BASE}/calculations/{hidden_sp.public_ref}", params={"profile": "curated"}).status_code == 404
    prov, rec = _provenance(client, entry, "curated")
    assert prov["sp_calculation_ref"] is None
    assert hidden_sp.public_ref not in str(rec)
    assert prov["freq_calculation_ref"] == shown_freq.public_ref


@pytest.mark.parametrize("status", BELOW_FLOOR)
def test_statmech_conformer_group_hidden_for_every_status_below_the_floor(client, db_session, status):
    """Kills: hiding only ``not_reviewed`` conformer groups."""
    sm, shown, hidden = _conformer_world(db_session, hidden_status=status)
    assert client.get(f"{BASE}/conformer-groups/{hidden.public_ref}", params={"profile": "curated"}).status_code == 404
    refs, _ = _conformer_refs(client, sm, "curated")
    assert refs == [shown.public_ref]


# --- provenance slots are picked from every link, then a hidden pick is left unnamed --------------------------------


def _substitution_world(db_session):
    """Own sp (hidden) at one level, own freq (approved) at another, a linked approved statmech with its own sp."""
    entry = _entry(db_session, "THS")
    lot_sp, lot_freq = make_lot(db_session, method="ccsd(t)", basis="cc-pvtz"), make_lot(db_session, method="b3lyp", basis="6-31g")
    own_sp = make_calculation(db_session, type=T.sp, species_entry_id=entry.id, lot_id=lot_sp.id)
    own_freq = make_calculation(db_session, type=T.freq, species_entry_id=entry.id, lot_id=lot_freq.id)
    sm = make_statmech(db_session, species_entry=entry)
    sm_sp = make_calculation(db_session, type=T.sp, species_entry_id=entry.id, lot_id=lot_sp.id)
    attach_statmech_source_calculation(db_session, statmech=sm, calculation=sm_sp, role=R.sp)
    thermo = make_thermo_scalar(db_session, species_entry=entry, statmech_id=sm.id)
    attach_thermo_source_calculation(db_session, thermo=thermo, calculation=own_sp, role=ThermoCalculationRole.sp)
    attach_thermo_source_calculation(db_session, thermo=thermo, calculation=own_freq, role=ThermoCalculationRole.freq)
    for calc in (own_freq, sm_sp):
        _review(db_session, SubmissionRecordType.calculation, calc)
    _review(db_session, SubmissionRecordType.statmech, sm)
    _review(db_session, SubmissionRecordType.thermo, thermo)
    return entry, own_sp, own_freq, sm_sp


def test_thermo_curated_does_not_substitute_another_calculation_for_a_hidden_pick(client, db_session):
    """Kills: filtering the links BEFORE the picker (it falls through to the next, visible, candidate)."""
    entry, own_sp, own_freq, sm_sp = _substitution_world(db_session)
    prov_x, rec_x = _provenance(client, entry, "exploratory")
    assert prov_x["primary_calculation"]["calculation_ref"] == own_sp.public_ref  # the true primary
    prov_c, rec_c = _provenance(client, entry, "curated")
    # The record-level level of theory is the true primary's, whatever the profile.
    assert prov_c["level_of_theory"] == prov_x["level_of_theory"]
    assert prov_c["level_of_theory"]["method"] == "ccsd(t)"
    # The hidden primary and sp are left unnamed, and nothing else is promoted into their place.
    assert prov_c["primary_calculation"] is None
    assert prov_c["sp_calculation_ref"] is None
    assert own_sp.public_ref not in str(rec_c)
    # The approved freq stays the freq; it is never the primary, and the statmech's sp is never the sp.
    assert prov_c["freq_calculation_ref"] == own_freq.public_ref
    assert sm_sp.public_ref not in str(prov_c)
    assert rec_c["levels"] == rec_x["levels"]


# --- statement counts: nothing extra without a floor, and no per-record cost for the conformer filter ------------


def _count_statements(db_session, call):
    seen: list[str] = []

    def hook(conn, cursor, statement, *a):
        seen.append(statement)

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", hook)
    try:
        call()
    finally:
        event.remove(bind, "before_cursor_execute", hook)
    return len(seen)


def test_thermo_list_statement_count_is_unchanged_without_a_floor(client, db_session):
    """Kills: running the review lookups when there is no floor (exploratory must cost what it cost before)."""
    entry, *_ = _substitution_world(db_session)
    url = f"{BASE}/species-entries/{entry.public_ref}/thermo"
    client.get(url)  # warm any one-time lookups
    n = _count_statements(db_session, lambda: client.get(url, params={"profile": "exploratory"}))
    assert n == EXPECTED_THERMO_LIST_STATEMENTS


def _n_statmech_with_groups(db_session, n=8):
    sms = []
    for i in range(n):
        entry = _entry(db_session, f"SC{i}")
        sm = make_statmech(db_session, species_entry=entry)
        group = make_conformer_group(db_session, entry, label=f"g{i}")
        _review(db_session, SubmissionRecordType.conformer_group, group)
        _review(db_session, SubmissionRecordType.statmech, sm)
        sms.append(sm)
    return sms


def test_statmech_search_conformers_statement_count(client, db_session):
    """Kills: a per-record review lookup in the conformer context (curated must cost the same as exploratory),
    and review lookups with no floor (exploratory pinned to its pre-change count)."""
    _n_statmech_with_groups(db_session, 8)
    body = {"model_kind": "rrho", "include": ["conformers"]}
    url = f"{BASE}/statmech/search"
    client.post(url, json=body)  # warm
    exploratory = _count_statements(db_session, lambda: client.post(url + "?profile=exploratory", json=body))
    curated = _count_statements(db_session, lambda: client.post(url + "?profile=curated", json=body))
    assert exploratory == EXPECTED_STATMECH_SEARCH_CONFORMERS_STATEMENTS
    assert curated == exploratory + EXPECTED_CURATED_EXTRA_STATEMENTS + EXPECTED_REF_GUARD_STATEMENTS
