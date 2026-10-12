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
    attach_statmech_source_calculation,
    make_calculation,
    make_chem_reaction,
    make_conformer_group,
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


# --- (c) statmech include=conformers ----------------------------------------------------------------------------


def _conformer_world(db_session):
    entry = _entry(db_session, "CNF")
    sm = make_statmech(db_session, species_entry=entry)
    shown = make_conformer_group(db_session, entry, label="shown")
    hidden = make_conformer_group(db_session, entry, label="hidden")
    _review(db_session, SubmissionRecordType.conformer_group, shown)
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
