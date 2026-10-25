"""ADR 0023, step 3: counts, flags and derived levels of a returned record follow the evidence rule under ``curated``.

The record floor decides which records come back; the linked evidence of a returned record is shown with its badge, and
only ``rejected`` and ``deprecated`` evidence is withheld. Before this step a count, a flag or a level beside a named
unreviewed calculation left that calculation out (transition states, networks, levels of theory) or counted a rejected
one (conformers, statmech, thermo).

Every family has two kinds of test:

* the REPRODUCTION: an unreviewed linked calculation is named by the response, so the count, flag or level beside it
  includes it. Exploratory is the control.
* the WITHHELD TWIN (``rejected`` and ``deprecated``): a withheld calculation is absent from the count, the flag and the
  level. A level only it supplied is null together with its source, and no other calculation is put in its place.

Each test names the mutation it kills.
"""

from __future__ import annotations

import pytest

from app.db.models.common import (
    CalculationType,
    NetworkChannelKind,
    NetworkKineticsModelKind,
    NetworkSolveCalculationRole,
    NetworkSpeciesRole,
    NetworkStateKind,
    RecordReviewStatus,
    StatmechCalculationRole,
    SubmissionRecordType,
    ThermoCalculationRole,
)
from tests.services.scientific_read._factories import (
    attach_network_solve_source_calculation,
    attach_network_species,
    attach_network_state_participant,
    attach_statmech_source_calculation,
    attach_statmech_torsion,
    attach_thermo_source_calculation,
    make_calculation,
    make_chem_reaction,
    make_conformer_group,
    make_conformer_observation,
    make_lot,
    make_network,
    make_network_channel,
    make_network_kinetics,
    make_network_solve,
    make_network_state,
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

BASE = "/api/v1/scientific"
R = StatmechCalculationRole
T = CalculationType
APPROVED = RecordReviewStatus.approved
WITHHELD = pytest.mark.parametrize("status", [RecordReviewStatus.rejected, RecordReviewStatus.deprecated])


def _review(db_session, kind, row, status=APPROVED):
    set_review(db_session, record_type=kind, record_id=row.id, status=status)


def _get(client, path, profile, **params):
    r = client.get(f"{BASE}{path}", params={"profile": profile, **params})
    assert r.status_code == 200, r.text
    return r.json()


def _entry(db_session, tag):
    return make_species_entry(db_session, make_species(db_session, inchi_key=next_inchi_key(tag)))


# ---------------------------------------------------------------------------
# Transition states: entry and concept summaries, flags, coverage, levels
# ---------------------------------------------------------------------------


def _ts_world(db_session, *, irc_status=None, irc_validates=False):
    """An approved transition state with an approved entry, an approved opt and an irc at its own level.

    ``irc_status`` None leaves the irc without a review row (``not_reviewed``).
    """
    a, b = make_species(db_session, inchi_key=next_inchi_key("TSA")), make_species(db_session, inchi_key=next_inchi_key("TSB"))
    ea, eb = make_species_entry(db_session, a), make_species_entry(db_session, b)
    chem = make_chem_reaction(db_session, reactants=[a], products=[b])
    rxe = make_reaction_entry(db_session, reaction=chem, reactant_entries=[ea], product_entries=[eb])
    ts = make_transition_state(db_session, reaction_entry=rxe)
    tse = make_transition_state_entry(db_session, transition_state=ts)
    opt = make_calculation(db_session, type=T.opt, transition_state_entry_id=tse.id, lot_id=make_lot(db_session, method="optlot").id)
    irc = make_calculation(db_session, type=T.irc, transition_state_entry_id=tse.id, lot_id=make_lot(db_session, method="irclot").id)
    if irc_validates:  # before the entry is approved: an approved entry's children are immutable
        from app.db.models.transition_state import TransitionStateValidationEvidence

        db_session.add(
            TransitionStateValidationEvidence(
                transition_state_entry_id=tse.id,
                kind="irc",
                passed=True,
                rationale="endpoints match the participants",
                reconstruction_calculation_id=irc.id,
            )
        )
        db_session.flush()
    _review(db_session, SubmissionRecordType.calculation, opt)
    if irc_status is not None:
        _review(db_session, SubmissionRecordType.calculation, irc, irc_status)
    for scoping, kind in ((rxe, SubmissionRecordType.reaction_entry), (ea, SubmissionRecordType.species_entry), (eb, SubmissionRecordType.species_entry)):
        _review(db_session, kind, scoping)  # scoping parents: some lists gate on them
    _review(db_session, SubmissionRecordType.transition_state, ts)
    _review(db_session, SubmissionRecordType.transition_state_entry, tse)
    return ts, tse, opt, irc


def test_ts_entry_counts_flags_and_levels_include_an_unreviewed_calculation(client, db_session):
    """Kills: flooring linked evidence for the entry's counts, flags and levels (the old rule).

    The unreviewed irc is named by the entry's own calculation list (step 2); the count, the flag, the coverage and the
    level beside it must include it, so a curated response agrees with the exploratory one about what the entry rests on.
    """
    _, tse, _, irc = _ts_world(db_session)
    cur = _get(client, f"/transition-state-entries/{tse.public_ref}", "curated", include="all")["record"]
    exp = _get(client, f"/transition-state-entries/{tse.public_ref}", "exploratory", include="all")["record"]
    assert irc.public_ref in str(cur)  # named
    assert cur["evidence_summary"]["calculation_count"] == 2 == exp["evidence_summary"]["calculation_count"]
    assert cur["evidence_summary"]["has_irc"] is True
    assert set(cur["evidence_summary"]["levels_of_theory"]) == {"opt", "irc"}
    assert cur["available_sections"]["has_calculations"] is True


@WITHHELD
def test_ts_entry_counts_flags_and_levels_leave_out_a_withheld_calculation(client, db_session, status):
    """Kills: counting a rejected/deprecated calculation, or leaving its type in ``levels_of_theory``."""
    _, tse, opt, irc = _ts_world(db_session, irc_status=status)
    cur = _get(client, f"/transition-state-entries/{tse.public_ref}", "curated", include="all")["record"]
    assert irc.public_ref not in str(cur)
    assert cur["evidence_summary"]["calculation_count"] == 1
    assert cur["evidence_summary"]["has_irc"] is False
    assert cur["evidence_summary"]["has_opt"] is True  # non-vacuous: the shown evidence is still counted
    assert set(cur["evidence_summary"]["levels_of_theory"]) == {"opt"}
    exp = _get(client, f"/transition-state-entries/{tse.public_ref}", "exploratory", include="all")["record"]
    assert exp["evidence_summary"]["calculation_count"] == 2 and exp["evidence_summary"]["has_irc"] is True


def test_ts_concept_coverage_includes_an_unreviewed_calculation(client, db_session):
    """Kills: flooring the concept summary's per-type entry coverage and total."""
    ts, *_ = _ts_world(db_session)
    cur = _get(client, f"/transition-states/{ts.public_ref}", "curated")["record"]["evidence_summary"]
    assert cur["calculation_count"] == 2
    assert cur["evidence_coverage"]["irc"] == 1 and cur["evidence_coverage"]["opt"] == 1


@WITHHELD
def test_ts_concept_coverage_leaves_out_a_withheld_calculation(client, db_session, status):
    """Kills: counting a rejected/deprecated calculation in the concept summary."""
    ts, *_ = _ts_world(db_session, irc_status=status)
    cur = _get(client, f"/transition-states/{ts.public_ref}", "curated")["record"]["evidence_summary"]
    assert cur["calculation_count"] == 1
    assert cur["evidence_coverage"]["irc"] == 0 and cur["evidence_coverage"]["opt"] == 1


def test_ts_search_software_summary_follows_the_evidence_rule(client, db_session):
    """Kills: leaving the search page's per-type software/level maps on the record floor."""
    _, tse, _, irc = _ts_world(db_session)
    r = client.post(f"{BASE}/transition-states/search?profile=curated", json={"charge": 0})
    assert r.status_code == 200, r.text
    (rec,) = [x for x in r.json()["records"] if tse.public_ref in str(x["core"] if "core" in x else x)]
    assert rec["evidence_summary"]["calculation_count"] == 2
    assert set(rec["evidence_summary"]["levels_of_theory"]) == {"opt", "irc"}


# ---------------------------------------------------------------------------
# Conformers: evidence counts and the level map beside them follow one rule
# ---------------------------------------------------------------------------


def _conformer_world(db_session, *, freq_status=None):
    entry = _entry(db_session, "CFE")
    group = make_conformer_group(db_session, entry, label="g")
    obs = make_conformer_observation(db_session, conformer_group=group)
    opt = make_calculation(
        db_session, type=T.opt, species_entry_id=entry.id, conformer_observation_id=obs.id,
        lot_id=make_lot(db_session, method="coptlot").id,
    )
    freq = make_calculation(
        db_session, type=T.freq, species_entry_id=entry.id, conformer_observation_id=obs.id,
        lot_id=make_lot(db_session, method="cfreqlot").id,
    )
    _review(db_session, SubmissionRecordType.calculation, opt)
    if freq_status is not None:
        _review(db_session, SubmissionRecordType.calculation, freq, freq_status)
    _review(db_session, SubmissionRecordType.conformer_group, group)
    _review(db_session, SubmissionRecordType.conformer_observation, obs)
    return group, obs, freq


def test_conformer_group_counts_and_levels_include_an_unreviewed_calculation(client, db_session):
    """Kills: flooring the level map (it was) while the counts beside it stay unfloored - or the reverse."""
    group, obs, freq = _conformer_world(db_session)
    rec = _get(client, f"/conformer-groups/{group.public_ref}", "curated", include="all")["record"]
    assert freq.public_ref in str(rec)  # named
    summary = rec["evidence_summary"]
    assert summary["calculation_count"] == 2
    assert summary["evidence_coverage"]["freq"] == 1
    assert set(summary["levels_of_theory"]) == {"opt", "freq"}


@WITHHELD
def test_conformer_group_counts_and_levels_leave_out_a_withheld_calculation(client, db_session, status):
    """Kills: counting a rejected/deprecated calculation (the counts were not floored at all)."""
    group, obs, freq = _conformer_world(db_session, freq_status=status)
    rec = _get(client, f"/conformer-groups/{group.public_ref}", "curated", include="all")["record"]
    assert freq.public_ref not in str(rec)
    summary = rec["evidence_summary"]
    assert summary["calculation_count"] == 1
    assert summary["evidence_coverage"]["freq"] == 0 and summary["evidence_coverage"]["opt"] == 1
    assert set(summary["levels_of_theory"]) == {"opt"}
    exp = _get(client, f"/conformer-groups/{group.public_ref}", "exploratory", include="all")["record"]
    assert exp["evidence_summary"]["calculation_count"] == 2


@WITHHELD
def test_conformer_observation_flags_leave_out_a_withheld_calculation(client, db_session, status):
    """Kills: the observation surface counting a rejected/deprecated calculation or setting its flag."""
    group, obs, freq = _conformer_world(db_session, freq_status=status)
    rec = _get(client, f"/conformer-observations/{obs.public_ref}", "curated", include="all")["record"]
    assert rec["evidence_summary"]["calculation_count"] == 1
    assert rec["evidence_summary"]["has_freq"] is False and rec["evidence_summary"]["has_opt"] is True


def test_conformer_observation_flags_include_an_unreviewed_calculation(client, db_session):
    """Kills: flooring the observation surface's flags."""
    group, obs, freq = _conformer_world(db_session)
    rec = _get(client, f"/conformer-observations/{obs.public_ref}", "curated", include="all")["record"]
    assert rec["evidence_summary"]["calculation_count"] == 2 and rec["evidence_summary"]["has_freq"] is True


# ---------------------------------------------------------------------------
# Networks: source-calculation counts
# ---------------------------------------------------------------------------


def _network_world(db_session, *, calc_status=None):
    net = make_network(db_session, name=f"net-{next_inchi_key('CNW')}")
    entry = _entry(db_session, "CNS")
    attach_network_species(db_session, network=net, species_entry=entry, role=NetworkSpeciesRole.well)
    sa = make_network_state(db_session, network=net, kind=NetworkStateKind.well, composition_hash="a" * 64)
    sb = make_network_state(db_session, network=net, kind=NetworkStateKind.well, composition_hash="b" * 64)
    attach_network_state_participant(db_session, state=sa, species_entry=entry)
    attach_network_state_participant(db_session, state=sb, species_entry=entry)
    channel = make_network_channel(db_session, network=net, source_state=sa, sink_state=sb, kind=NetworkChannelKind.isomerization)
    solve = make_network_solve(db_session, network=net)
    make_network_kinetics(db_session, channel=channel, solve=solve, model_kind=NetworkKineticsModelKind.chebyshev)
    calc = make_calculation(db_session, type=T.sp, species_entry_id=entry.id, lot_id=make_lot(db_session, method="netlot").id)
    attach_network_solve_source_calculation(db_session, solve=solve, calculation=calc, role=NetworkSolveCalculationRole.fit_source)
    if calc_status is not None:
        _review(db_session, SubmissionRecordType.calculation, calc, calc_status)
    _review(db_session, SubmissionRecordType.network, net)
    _review(db_session, SubmissionRecordType.network_solve, solve)
    return net, solve, calc


def test_network_source_calculation_counts_include_an_unreviewed_calculation(client, db_session):
    """Kills: flooring the source-calculation count on the network, the solve summary and the solve detail."""
    net, solve, calc = _network_world(db_session)
    detail = _get(client, f"/networks/{net.public_ref}", "curated", include="all")["record"]
    assert detail["evidence_summary"]["source_calculation_count"] == 1
    solves = detail["solves"]
    assert [s["source_calculation_count"] for s in solves] == [1]
    solve_detail = _get(client, f"/network-solves/{solve.public_ref}", "curated")["record"]
    assert solve_detail["evidence_summary"]["source_calculation_count"] == 1


@WITHHELD
def test_network_source_calculation_counts_leave_out_a_withheld_calculation(client, db_session, status):
    """Kills: counting a rejected/deprecated source calculation (the solve itself stays a record, approved here)."""
    net, solve, calc = _network_world(db_session, calc_status=status)
    detail = _get(client, f"/networks/{net.public_ref}", "curated", include="all")["record"]
    assert detail["evidence_summary"]["source_calculation_count"] == 0
    assert [s["source_calculation_count"] for s in detail["solves"]] == [0]
    assert detail["evidence_summary"]["solve_count"] == 1  # the approved solve is still a record
    assert _get(client, f"/network-solves/{solve.public_ref}", "curated")["record"]["evidence_summary"][
        "source_calculation_count"
    ] == 0
    exp = _get(client, f"/networks/{net.public_ref}", "exploratory")["record"]
    assert exp["evidence_summary"]["source_calculation_count"] == 1


# ---------------------------------------------------------------------------
# Statmech: evidence summary, flags and levels
# ---------------------------------------------------------------------------


def _statmech_world(db_session, *, sp_status=None, scan_status=None, freq_status=None):
    entry = _entry(db_session, "CSM")
    sm = make_statmech(db_session, species_entry=entry)
    opt = make_calculation(db_session, type=T.opt, species_entry_id=entry.id, lot_id=make_lot(db_session, method="smopt").id)
    freq = make_calculation(db_session, type=T.freq, species_entry_id=entry.id, lot_id=make_lot(db_session, method="smfreq").id)
    sp = make_calculation(db_session, type=T.sp, species_entry_id=entry.id, lot_id=make_lot(db_session, method="smsp").id)
    scan = make_calculation(db_session, type=T.scan, species_entry_id=entry.id)
    for calc, role in ((opt, R.opt), (freq, R.freq), (sp, R.sp)):
        attach_statmech_source_calculation(db_session, statmech=sm, calculation=calc, role=role)
    attach_statmech_torsion(db_session, statmech=sm, source_scan_calculation=scan)
    _review(db_session, SubmissionRecordType.calculation, opt)
    _review(db_session, SubmissionRecordType.calculation, freq, freq_status or APPROVED)
    if sp_status is not None:
        _review(db_session, SubmissionRecordType.calculation, sp, sp_status)
    if scan_status is not None:
        _review(db_session, SubmissionRecordType.calculation, scan, scan_status)
    _review(db_session, SubmissionRecordType.statmech, sm)  # approved last: an approved statmech is immutable
    return sm, sp, scan, freq


def _statmech(client, sm, profile):
    return _get(client, f"/statmech/{sm.public_ref}", profile, include="source_calculations,torsions")["record"]


def test_statmech_counts_flags_and_levels_include_an_unreviewed_calculation(client, db_session):
    """Kills: flooring the statmech evidence summary / levels (it named the unreviewed sp but derived no energy level)."""
    sm, sp, scan, freq = _statmech_world(db_session)
    rec = _statmech(client, sm, "curated")
    assert sp.public_ref in str(rec["source_calculations"])  # named
    assert rec["evidence_summary"]["source_calculation_count"] == 3
    assert rec["evidence_summary"]["has_sp_calculation"] is True
    assert rec["evidence_summary"]["has_rotor_scans"] is True
    assert rec["levels"]["energy_source"] == "sp" and rec["levels"]["energy"]["method"] == "smsp"
    assert rec["levels"] == _statmech(client, sm, "exploratory")["levels"]


@WITHHELD
def test_statmech_counts_flags_and_levels_leave_out_a_withheld_single_point(client, db_session, status):
    """Kills: counting a withheld sp; deriving the energy level from it; or putting the opt's level in its place."""
    sm, sp, scan, freq = _statmech_world(db_session, sp_status=status)
    rec = _statmech(client, sm, "curated")
    assert sp.public_ref not in str(rec)
    assert rec["evidence_summary"]["source_calculation_count"] == 2
    assert rec["evidence_summary"]["has_sp_calculation"] is False
    assert rec["evidence_summary"]["has_opt_calculation"] is True
    # The level only the withheld sp supplied is null with its source; the opt's level is NOT put in its place.
    assert rec["levels"]["energy"] is None and rec["levels"]["energy_source"] is None
    assert rec["levels"]["geometry"]["method"] == "smopt" and rec["levels"]["geometry_source"] == "opt"
    assert rec["levels"]["frequency"]["method"] == "smfreq"
    exp = _statmech(client, sm, "exploratory")
    assert exp["evidence_summary"]["source_calculation_count"] == 3 and exp["levels"]["energy_source"] == "sp"


@WITHHELD
def test_statmech_rotor_scan_flag_leaves_out_a_withheld_scan(client, db_session, status):
    """Kills: ``has_rotor_scans`` reporting a scan calculation the evidence rule withholds."""
    sm, sp, scan, freq = _statmech_world(db_session, scan_status=status)
    assert _statmech(client, sm, "curated")["evidence_summary"]["has_rotor_scans"] is False
    assert _statmech(client, sm, "exploratory")["evidence_summary"]["has_rotor_scans"] is True


@WITHHELD
def test_statmech_frequency_flag_leaves_out_a_withheld_frequency_calculation(client, db_session, status):
    """Kills: ``has_frequencies`` / ``has_freq_calculation`` reporting a withheld frequency calculation."""
    sm, sp, scan, freq = _statmech_world(db_session, freq_status=status)
    rec = _statmech(client, sm, "curated")
    assert rec["evidence_summary"]["has_freq_calculation"] is False
    assert rec["available_sections"]["has_frequencies"] is False
    assert rec["levels"]["frequency"] is None and rec["levels"]["frequency_source"] is None


# ---------------------------------------------------------------------------
# Thermo: primary level and derived levels
# ---------------------------------------------------------------------------


def _thermo_world(db_session, *, sp_status=None):
    entry = _entry(db_session, "CTH")
    thermo = make_thermo_scalar(db_session, species_entry=entry)
    sp = make_calculation(db_session, type=T.sp, species_entry_id=entry.id, lot_id=make_lot(db_session, method="thsp").id)
    opt = make_calculation(db_session, type=T.opt, species_entry_id=entry.id, lot_id=make_lot(db_session, method="thopt").id)
    attach_thermo_source_calculation(db_session, thermo=thermo, calculation=sp, role=ThermoCalculationRole.sp)
    attach_thermo_source_calculation(db_session, thermo=thermo, calculation=opt, role=ThermoCalculationRole.opt)
    _review(db_session, SubmissionRecordType.calculation, opt)
    if sp_status is not None:
        _review(db_session, SubmissionRecordType.calculation, sp, sp_status)
    _review(db_session, SubmissionRecordType.thermo, thermo)
    return entry, sp, opt


def _thermo(client, entry, profile):
    (rec,) = _get(client, f"/species-entries/{entry.public_ref}/thermo", profile)["records"]
    return rec


def test_thermo_levels_and_primary_level_include_an_unreviewed_calculation(client, db_session):
    """Kills: dropping an unreviewed calculation's level (the energy level) under curated."""
    entry, sp, opt = _thermo_world(db_session)
    rec = _thermo(client, entry, "curated")
    assert rec["provenance"]["sp_calculation_ref"] == sp.public_ref  # named
    assert rec["levels"]["energy"]["method"] == "thsp" and rec["levels"]["energy_source"] == "sp"
    assert rec["levels"] == _thermo(client, entry, "exploratory")["levels"]


@WITHHELD
def test_thermo_levels_and_primary_level_leave_out_a_withheld_single_point(client, db_session, status):
    """Kills: deriving the energy level from a withheld sp; the opt level taking its place; the primary's level surviving
    its primary calculation."""
    entry, sp, opt = _thermo_world(db_session, sp_status=status)
    exp = _thermo(client, entry, "exploratory")
    assert exp["provenance"]["primary_calculation"]["calculation_ref"] == sp.public_ref  # the true primary
    rec = _thermo(client, entry, "curated")
    assert sp.public_ref not in str(rec)
    assert rec["provenance"]["primary_calculation"] is None
    assert rec["provenance"]["level_of_theory"] is None  # a fact about the withheld primary
    assert rec["levels"]["energy"] is None and rec["levels"]["energy_source"] is None
    assert rec["levels"]["geometry"]["method"] == "thopt"  # the shown opt still supplies the geometry level
    assert exp["levels"]["energy"]["method"] == "thsp"


# ---------------------------------------------------------------------------
# Kinetics: levels, with the substitution rule kept
# ---------------------------------------------------------------------------


def _kinetics_world(db_session, *, sp_status=None, freq_status=None):
    from app.db.models.common import KineticsCalculationRole
    from app.db.models.kinetics import KineticsSourceCalculation
    from tests.services.scientific_read._factories import make_kinetics

    a, b = make_species(db_session, smiles="CC", inchi_key=next_inchi_key("CKA")), make_species(db_session, smiles="CCC", inchi_key=next_inchi_key("CKB"))
    ea = make_species_entry(db_session, a)
    chem = make_chem_reaction(db_session, reactants=[a], products=[b])
    rxe = make_reaction_entry(db_session, reaction=chem, reactant_entries=[ea], product_entries=[make_species_entry(db_session, b)])
    ts_sp = make_calculation(db_session, type=T.sp, species_entry_id=ea.id, lot_id=make_lot(db_session, method="ksp").id)
    freq = make_calculation(db_session, type=T.freq, species_entry_id=ea.id, lot_id=make_lot(db_session, method="kfreq").id)
    k = make_kinetics(db_session, reaction_entry=rxe)
    db_session.add_all(
        [
            KineticsSourceCalculation(kinetics_id=k.id, calculation_id=ts_sp.id, role=KineticsCalculationRole.ts_energy),
            KineticsSourceCalculation(kinetics_id=k.id, calculation_id=freq.id, role=KineticsCalculationRole.freq),
        ]
    )
    db_session.flush()
    _review(db_session, SubmissionRecordType.calculation, freq, freq_status or APPROVED)
    if sp_status is not None:
        _review(db_session, SubmissionRecordType.calculation, ts_sp, sp_status)
    _review(db_session, SubmissionRecordType.kinetics, k)
    return rxe, k, ts_sp


def _kinetics(client, rxe, profile):
    (rec,) = _get(client, f"/reaction-entries/{rxe.public_ref}/kinetics", profile)["records"]
    return rec


def test_kinetics_levels_include_an_unreviewed_single_point(client, db_session):
    """Kills: nulling the level of an unreviewed ts_energy calculation (it was nulled as 'hidden')."""
    rxe, k, ts_sp = _kinetics_world(db_session)
    rec = _kinetics(client, rxe, "curated")
    assert rec["levels"]["energy"]["method"] == "ksp"
    assert rec["levels"] == _kinetics(client, rxe, "exploratory")["levels"]


@WITHHELD
def test_kinetics_levels_leave_out_a_withheld_single_point(client, db_session, status):
    """Kills: keeping the level of a rejected/deprecated sp; or filling the energy level from another calculation."""
    rxe, k, ts_sp = _kinetics_world(db_session, sp_status=status)
    rec = _kinetics(client, rxe, "curated")
    assert ts_sp.public_ref not in str(rec)
    assert rec["levels"]["energy"] is None and rec["levels"]["energy_source"] is None
    assert rec["levels"]["frequency"]["method"] == "kfreq"
    assert _kinetics(client, rxe, "exploratory")["levels"]["energy"]["method"] == "ksp"


# ---------------------------------------------------------------------------
# Analytics flags
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status,expected", [(None, True), (RecordReviewStatus.rejected, False), (RecordReviewStatus.deprecated, False)])
def test_thermo_analytics_statmech_flag_follows_the_evidence_rule(client, db_session, status, expected):
    """Kills: flooring the flag (an unreviewed statmech reads false); or counting a withheld statmech."""
    entry = _entry(db_session, "CAN")
    sm = make_statmech(db_session, species_entry=entry)
    thermo = make_thermo_scalar(db_session, species_entry=entry, statmech_id=sm.id)
    if status is not None:
        _review(db_session, SubmissionRecordType.statmech, sm, status)
    _review(db_session, SubmissionRecordType.thermo, thermo)
    rows = _get(client, "/analytics/thermo", "curated")["records"]
    (row,) = [r for r in rows if r["thermo_ref"] == thermo.public_ref]
    assert row["has_statmech_provenance"] is expected
    (control,) = [r for r in _get(client, "/analytics/thermo", "exploratory")["records"] if r["thermo_ref"] == thermo.public_ref]
    assert control["has_statmech_provenance"] is True


# ---------------------------------------------------------------------------
# Kinetics evidence checklist: calculation types reachable through the reaction's transition-state entries
# ---------------------------------------------------------------------------


def _checklist_world(db_session, *, freq_status=None):
    from tests.services.scientific_read._factories import make_kinetics

    a = make_species(db_session, smiles="CC", inchi_key=next_inchi_key("CLA"))
    b = make_species(db_session, smiles="CCC", inchi_key=next_inchi_key("CLB"))
    chem = make_chem_reaction(db_session, reactants=[a], products=[b])
    rxe = make_reaction_entry(
        db_session,
        reaction=chem,
        reactant_entries=[make_species_entry(db_session, a)],
        product_entries=[make_species_entry(db_session, b)],
    )
    ts = make_transition_state(db_session, reaction_entry=rxe)
    tse = make_transition_state_entry(db_session, transition_state=ts)
    freq = make_calculation(db_session, type=T.freq, transition_state_entry_id=tse.id)
    k = make_kinetics(db_session, reaction_entry=rxe)
    if freq_status is not None:
        _review(db_session, SubmissionRecordType.calculation, freq, freq_status)
    _review(db_session, SubmissionRecordType.kinetics, k)
    return rxe, k, freq


def _has_ts_freq(client, rxe, profile):
    (rec,) = _get(client, f"/reaction-entries/{rxe.public_ref}/kinetics", profile)["records"]
    return rec["evidence_completeness"]["checklist"]["has_ts_freq_evidence"]


def test_kinetics_checklist_counts_an_unreviewed_transition_state_calculation(client, db_session):
    """Kills: flooring the calculation types the kinetics checklist reaches through the reaction's TS entries."""
    rxe, k, freq = _checklist_world(db_session)
    assert _has_ts_freq(client, rxe, "curated") is True
    assert _has_ts_freq(client, rxe, "exploratory") is True


@WITHHELD
def test_kinetics_checklist_leaves_out_a_withheld_transition_state_calculation(client, db_session, status):
    """Kills: counting a rejected/deprecated calculation in the kinetics checklist."""
    rxe, k, freq = _checklist_world(db_session, freq_status=status)
    assert _has_ts_freq(client, rxe, "curated") is False
    assert _has_ts_freq(client, rxe, "exploratory") is True


# ---------------------------------------------------------------------------
# Transition states: the saddle-point block, the validation descriptor and flags, reaction availability
# ---------------------------------------------------------------------------


def _saddle_world(db_session, *, freq_status=None):
    from tests.services.scientific_read._factories import attach_freq_result

    ts, tse, opt, irc = _ts_world(db_session)
    freq = make_calculation(db_session, type=T.freq, transition_state_entry_id=tse.id)
    attach_freq_result(db_session, calculation=freq, frequencies_cm1=[-1500.0, 120.0, 340.0])
    if freq_status is not None:
        _review(db_session, SubmissionRecordType.calculation, freq, freq_status)
    return tse, freq


def _search_record(client, tse, profile):
    r = client.post(f"{BASE}/transition-states/search?profile={profile}", json={"charge": 0})
    assert r.status_code == 200, r.text
    (rec,) = [x for x in r.json()["records"] if tse.public_ref in str(x["transition_state_entry"])]
    return rec


def test_saddle_point_block_describes_an_unreviewed_frequency_result(client, db_session):
    """Kills: the saddle-point block keeping the record floor (it then reports no result beside a named calculation)."""
    tse, freq = _saddle_world(db_session)
    block = _search_record(client, tse, "curated")["saddle_point"]
    assert block is not None and block["n_imag"] == 1 and block["calculation_ref"] == freq.public_ref
    assert block == _search_record(client, tse, "exploratory")["saddle_point"]


@WITHHELD
def test_saddle_point_block_leaves_out_a_withheld_frequency_result(client, db_session, status):
    """Kills: describing, or naming, a rejected/deprecated frequency result in the saddle-point block."""
    tse, freq = _saddle_world(db_session, freq_status=status)
    assert _search_record(client, tse, "curated")["saddle_point"] is None
    assert _search_record(client, tse, "exploratory")["saddle_point"]["calculation_ref"] == freq.public_ref


def _validated_world(db_session, *, irc_status=None):
    ts, tse, opt, irc = _ts_world(db_session, irc_status=irc_status, irc_validates=True)
    return tse


def test_validation_descriptor_and_flag_count_evidence_resting_on_an_unreviewed_calculation(client, db_session):
    """Kills: dropping a validation evidence row whose reconstructing calculation is merely unreviewed."""
    tse = _validated_world(db_session)
    rec = _get(client, f"/transition-state-entries/{tse.public_ref}", "curated", include="all")["record"]
    assert rec["validation"]["irc"] == "present"
    assert rec["available_sections"]["has_validation_evidence"] is True


@WITHHELD
def test_validation_descriptor_and_flag_leave_out_evidence_resting_on_a_withheld_calculation(client, db_session, status):
    """Kills: counting a validation evidence row whose reconstructing calculation is rejected/deprecated."""
    tse = _validated_world(db_session, irc_status=status)
    rec = _get(client, f"/transition-state-entries/{tse.public_ref}", "curated", include="all")["record"]
    assert rec["validation"]["irc"] == "absent"
    assert rec["available_sections"]["has_validation_evidence"] is False
    exp = _get(client, f"/transition-state-entries/{tse.public_ref}", "exploratory", include="all")["record"]
    assert exp["validation"]["irc"] == "present"


def _path_search_world(db_session, *, status=None):
    ts, tse, opt, irc = _ts_world(db_session)
    path = make_calculation(db_session, type=T.path_search, transition_state_entry_id=tse.id)
    if status is not None:
        _review(db_session, SubmissionRecordType.calculation, path, status)
    from app.db.models.reaction import ReactionEntry

    return db_session.get(ReactionEntry, ts.reaction_entry_id).public_ref


def _has_path_search(client, reaction_entry_ref, profile):
    rows = _get(client, "/reactions/browse", profile, limit=100)["records"]
    (row,) = [r for r in rows if r["reaction_entry_ref"] == reaction_entry_ref]
    return row["availability"]["has_path_search"]


def test_reaction_availability_counts_an_unreviewed_path_search(client, db_session):
    """Kills: flooring the path-search calculation behind ``has_path_search``."""
    rxe_id = _path_search_world(db_session)
    assert _has_path_search(client, rxe_id, "curated") is True


@WITHHELD
def test_reaction_availability_leaves_out_a_withheld_path_search(client, db_session, status):
    """Kills: counting a rejected/deprecated path search in ``has_path_search``."""
    rxe_id = _path_search_world(db_session, status=status)
    assert _has_path_search(client, rxe_id, "curated") is False
    assert _has_path_search(client, rxe_id, "exploratory") is True


# ---------------------------------------------------------------------------
# Thermo evidence checklist
# ---------------------------------------------------------------------------


def _thermo_checklist(client, db_session, status):
    entry = _entry(db_session, "CTC")
    thermo = make_thermo_scalar(db_session, species_entry=entry)
    freq = make_calculation(db_session, type=T.freq, species_entry_id=entry.id)
    attach_thermo_source_calculation(db_session, thermo=thermo, calculation=freq, role=ThermoCalculationRole.freq)
    if status is not None:
        _review(db_session, SubmissionRecordType.calculation, freq, status)
    _review(db_session, SubmissionRecordType.thermo, thermo)
    return _thermo(client, entry, "curated")["evidence_completeness"]["checklist"]


def test_thermo_checklist_counts_an_unreviewed_source_calculation(client, db_session):
    """Kills: flooring the thermo evidence checklist's source calculations."""
    checklist = _thermo_checklist(client, db_session, None)
    assert checklist["has_source_calculations"] is True and checklist["has_frequency_evidence"] is True


@WITHHELD
def test_thermo_checklist_leaves_out_a_withheld_source_calculation(client, db_session, status):
    """Kills: counting a rejected/deprecated source calculation in the thermo checklist."""
    checklist = _thermo_checklist(client, db_session, status)
    assert checklist["has_source_calculations"] is False and checklist["has_frequency_evidence"] is False


# ---------------------------------------------------------------------------
# Network state-energy sources, and a validation entry's reference solve
# ---------------------------------------------------------------------------


def test_state_energy_sources_keep_an_unreviewed_calculation_and_stay_complete(client, db_session, login_as):
    """The sweep world's hidden calculations are merely unreviewed: they are linked evidence of the solve, so its state
    energies keep both sources and ``partial_sources`` stays false.

    Kills: ``_visible_join`` on the record floor (an unreviewed source dropped and the state then read as incomplete).
    """
    import hashlib

    from tests.api.scientific._curated_sweep_world import build_sweep_world

    world = build_sweep_world(db_session)
    login_as(world.extra["curator"].id)
    solve = world.visible["nsolve_v"].public_ref
    cur = _get(client, f"/network-solves/{solve}", "curated", include="all")["record"]
    exp = _get(client, f"/network-solves/{solve}", "exploratory", include="all")["record"]
    assert cur["evidence_summary"]["source_calculation_count"] == exp["evidence_summary"]["source_calculation_count"]
    assert sum(len(e["sources"]) for e in cur["state_energies"]) == sum(len(e["sources"]) for e in exp["state_energies"])
    for tag in ("e-state", "f-state"):
        digest = hashlib.sha256(tag.encode()).hexdigest()
        cur_e = next(e for e in cur["state_energies"] if e["state_composition_hash"] == digest)
        assert len(cur_e["sources"]) == 2 and cur_e["partial_sources"] is False, tag


@pytest.mark.parametrize(
    "status,known",
    [(None, True), (RecordReviewStatus.under_review, True), (RecordReviewStatus.rejected, False), (RecordReviewStatus.deprecated, False)],
)
def test_a_validation_reference_solve_is_known_unless_it_is_withheld(db_session, status, known):
    """A reference solve is evidence of the entry citing it (read side, ADR 0023): known unless rejected or deprecated. The
    default (upload, selection loader) keeps the record floor.

    Kills: ``as_evidence`` applying the record floor, or ignoring the withheld statuses.
    """
    from app.db.models.common import ProfileRecommendation, ReadProfile
    from app.services.scientific_read.network_declarations import solve_ref_is_visible
    from app.services.scientific_read.profile import (
        ResolvedReadProfile,
        reset_current_read_profile,
        set_current_read_profile,
    )

    net = make_network(db_session, name=f"net-{next_inchi_key('REF')}")
    solve = make_network_solve(db_session, network=net)
    if status is not None:
        set_review(db_session, record_type=SubmissionRecordType.network_solve, record_id=solve.id, status=status)
    token = set_current_read_profile(
        ResolvedReadProfile(profile=ReadProfile.curated, recommendation=ProfileRecommendation.approved_floor_only)
    )
    try:
        assert solve_ref_is_visible(db_session, solve.public_ref, as_evidence=True) is known
        assert solve_ref_is_visible(db_session, solve.public_ref) is False  # the record floor: below approved
    finally:
        reset_current_read_profile(token)


@WITHHELD
def test_kinetics_checklist_leaves_out_withheld_cited_source_calculations(client, db_session, status):
    """Kills: the kinetics checklist counting its own cited source calculations when every one is rejected/deprecated."""
    rxe, k, ts_sp = _kinetics_world(db_session, sp_status=status, freq_status=status)
    checklist = _kinetics(client, rxe, "curated")["evidence_completeness"]["checklist"]
    assert checklist["has_source_calculations"] is False
    assert checklist["has_ts_freq_evidence"] is False and checklist["has_ts_opt_evidence"] is False
    exp = _kinetics(client, rxe, "exploratory")["evidence_completeness"]["checklist"]
    assert exp["has_source_calculations"] is True and exp["has_ts_freq_evidence"] is True


def test_kinetics_checklist_counts_unreviewed_cited_source_calculations(client, db_session):
    """Kills: flooring the kinetics checklist's cited source calculations."""
    rxe, k, ts_sp = _kinetics_world(db_session, freq_status=RecordReviewStatus.under_review)
    checklist = _kinetics(client, rxe, "curated")["evidence_completeness"]["checklist"]
    assert checklist["has_source_calculations"] is True and checklist["has_ts_freq_evidence"] is True


def _statmech_thermo_world(db_session, *, freq_status=None):
    entry = _entry(db_session, "CTS")
    sm = make_statmech(db_session, species_entry=entry)
    freq = make_calculation(db_session, type=T.freq, species_entry_id=entry.id)
    attach_statmech_source_calculation(db_session, statmech=sm, calculation=freq, role=R.freq)
    thermo = make_thermo_scalar(db_session, species_entry=entry, statmech_id=sm.id)
    if freq_status is not None:
        _review(db_session, SubmissionRecordType.calculation, freq, freq_status)
    _review(db_session, SubmissionRecordType.statmech, sm)
    _review(db_session, SubmissionRecordType.thermo, thermo)
    return entry


def test_thermo_checklist_counts_an_unreviewed_statmech_source_calculation(client, db_session):
    """Kills: flooring the linked statmech's source calculations in the thermo checklist."""
    entry = _statmech_thermo_world(db_session)
    checklist = _thermo(client, entry, "curated")["evidence_completeness"]["checklist"]
    assert checklist["has_frequency_evidence"] is True and checklist["has_source_calculations"] is True


@WITHHELD
def test_thermo_checklist_leaves_out_a_withheld_statmech_source_calculation(client, db_session, status):
    """Kills: counting, in the thermo checklist, a rejected/deprecated calculation reached through the linked statmech."""
    entry = _statmech_thermo_world(db_session, freq_status=status)
    checklist = _thermo(client, entry, "curated")["evidence_completeness"]["checklist"]
    assert checklist["has_frequency_evidence"] is False and checklist["has_source_calculations"] is False
    assert checklist["has_statmech_source"] is True  # the statmech link itself is the record's own
