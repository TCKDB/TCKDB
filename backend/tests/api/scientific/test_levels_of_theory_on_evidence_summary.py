"""``evidence_summary.levels_of_theory`` — what it says and what it refuses to.

The evidence summaries count. ``evidence_coverage``'s own docstring says a
count is honest about coverage and not about consistency, and names the
workaround: go read the per-calculation level of theory under
``include=calculations``. This block removes that round trip, and these
tests pin the three decisions that make it worth having.

**Lists, not scalars.** Measured on the deployed database on 2026-08-24, 12
of 34 transition-state entries carry two distinct levels of theory. They are
correct records — optimise and take frequencies cheaply, then one expensive
single point at that geometry — so "the level of theory of this record" is
not a value that exists on a third of the corpus. The justifying test below
is exactly that record.

**Report, never judge.** There is no ``levels_consistent`` flag here and
these tests assert that the block adds no derived quality signal, because
such a signal would mark those 12 correct records as suspect.

**Absent and empty are different facts.** A missing key says no calculation
of that type is attached; a key with an empty list says calculations exist
and none names a level. The direct query over the deployed corpus
(``GROUP BY transition_state_entry_id, type HAVING COUNT(DISTINCT lot_id) >
1``) returns zero rows for transition-state entries and zero for conformer
observations — but 27 for species entries, so the within-type case is
already deposited data and not a hypothetical the shape is being bent for.
"""

from __future__ import annotations

from app.db.models.common import (
    CalculationType,
    TransitionStateEntryStatus,
)
from tests.services.scientific_read._factories import (
    make_calculation,
    make_calculation_with_conformer,
    make_chem_reaction,
    make_conformer_group,
    make_conformer_observation,
    make_lot,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_transition_state,
    make_transition_state_entry,
    next_inchi_key,
)

_TS_ENTRY_URL = "/api/v1/scientific/transition-state-entries/{}"
_TS_URL = "/api/v1/scientific/transition-states/{}"
_CG_URL = "/api/v1/scientific/conformer-groups/{}"
_CO_URL = "/api/v1/scientific/conformer-observations/{}"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _ts_entry(db_session, *, n_entries: int = 1):
    """A reaction → TS → *n_entries* chain. Returns ``(ts, entries)``."""
    sp_a = make_species(db_session, inchi_key=next_inchi_key("LOA"))
    sp_b = make_species(db_session, inchi_key=next_inchi_key("LOB"))
    sp_c = make_species(db_session, inchi_key=next_inchi_key("LOC"))
    sp_d = make_species(db_session, inchi_key=next_inchi_key("LOD"))
    entries_in = [make_species_entry(db_session, s) for s in (sp_a, sp_b)]
    entries_out = [make_species_entry(db_session, s) for s in (sp_c, sp_d)]
    chem = make_chem_reaction(
        db_session, reactants=[sp_a, sp_b], products=[sp_c, sp_d]
    )
    rxe = make_reaction_entry(
        db_session,
        reaction=chem,
        reactant_entries=entries_in,
        product_entries=entries_out,
    )
    ts = make_transition_state(db_session, reaction_entry=rxe, label="lot-ts")
    entries = [
        make_transition_state_entry(
            db_session,
            transition_state=ts,
            status=TransitionStateEntryStatus.optimized,
        )
        for _ in range(n_entries)
    ]
    return ts, entries


def _conformer_group(db_session, *, n_observations: int = 1):
    species = make_species(db_session, inchi_key=next_inchi_key("LOG"))
    entry = make_species_entry(db_session, species)
    group = make_conformer_group(db_session, entry, label="lot-basin")
    observations = [
        make_conformer_observation(db_session, conformer_group=group)
        for _ in range(n_observations)
    ]
    return entry, group, observations


# ---------------------------------------------------------------------------
# The justifying case: one record, two levels, reported per type
# ---------------------------------------------------------------------------


def test_a_composite_workflow_reports_both_of_its_levels(client, db_session):
    """The case an ``evidence_summary.level_of_theory`` field could not express.

    ``opt`` and ``freq`` at wb97xd/def2tzvp, ``sp`` at
    MRCI+Davidson/aug-cc-pV(T+d)Z — the exact shape of 12 of the 34 TS
    entries on the deployed database. Both levels come back, each under the
    calculation type it was actually run for, and no field anywhere claims
    the record has *a* level.
    """
    _, (entry,) = _ts_entry(db_session)
    cheap = make_lot(db_session, method="wb97xd", basis="def2tzvp")
    dear = make_lot(db_session, method="MRCI+Davidson", basis="aug-cc-pV(T+d)Z")
    for calc_type in (CalculationType.opt, CalculationType.freq):
        make_calculation(
            db_session,
            type=calc_type,
            transition_state_entry_id=entry.id,
            lot_id=cheap.id,
        )
    make_calculation(
        db_session,
        type=CalculationType.sp,
        transition_state_entry_id=entry.id,
        lot_id=dear.id,
    )

    resp = client.get(_TS_ENTRY_URL.format(entry.public_ref))
    assert resp.status_code == 200, resp.text
    levels = resp.json()["record"]["evidence_summary"]["levels_of_theory"]

    assert set(levels) == {"opt", "freq", "sp"}
    assert [lot["display"] for lot in levels["opt"]] == ["wb97xd/def2tzvp"]
    assert [lot["display"] for lot in levels["freq"]] == ["wb97xd/def2tzvp"]
    assert [lot["display"] for lot in levels["sp"]] == [
        "MRCI+Davidson/aug-cc-pV(T+d)Z"
    ]
    # The two levels are genuinely different rows, not the same one twice.
    assert (
        levels["opt"][0]["level_of_theory_ref"]
        != levels["sp"][0]["level_of_theory_ref"]
    )


def test_the_block_states_the_multiplicity_and_does_not_rule_on_it(
    client, db_session
):
    """No derived quality signal rides along, and that is deliberate.

    A ``levels_consistent`` boolean, a ``comparable`` flag or any other
    verdict would mark 12 of 34 correct deployed records as suspect. The
    map states what was used; comparability is the reader's call, and
    TCKDB's place for such calls is the versioned, opt-in trust rubrics.
    """
    _, (entry,) = _ts_entry(db_session)
    cheap = make_lot(db_session, method="wb97xd", basis="def2tzvp")
    dear = make_lot(db_session, method="CCSD(T)-F12", basis="cc-pVTZ-F12")
    make_calculation(
        db_session,
        type=CalculationType.opt,
        transition_state_entry_id=entry.id,
        lot_id=cheap.id,
    )
    make_calculation(
        db_session,
        type=CalculationType.sp,
        transition_state_entry_id=entry.id,
        lot_id=dear.id,
    )

    summary = client.get(_TS_ENTRY_URL.format(entry.public_ref)).json()[
        "record"
    ]["evidence_summary"]

    assert len(summary["levels_of_theory"]) == 2, "the fixture must be mixed"
    verdicts = [
        key
        for key in summary
        if key
        in {
            "levels_consistent",
            "level_of_theory",
            "levels_comparable",
            "comparable",
            "primary_level_of_theory",
        }
    ]
    assert verdicts == [], (
        f"{verdicts} on the evidence summary: this block reports what was "
        "used and never rules on whether it is comparable"
    )


def test_one_level_throughout_still_comes_back_as_lists(client, db_session):
    """22 of 34 deployed entries look like this, and they are lists too.

    A scalar-when-single shape would force every consumer to branch on
    cardinality before reading, and would break the day a second level
    appeared. One level is a list of one.
    """
    _, (entry,) = _ts_entry(db_session)
    lot = make_lot(db_session, method="b3lyp", basis="def2tzvp")
    for calc_type in (
        CalculationType.opt,
        CalculationType.freq,
        CalculationType.sp,
        CalculationType.irc,
    ):
        make_calculation(
            db_session,
            type=calc_type,
            transition_state_entry_id=entry.id,
            lot_id=lot.id,
        )

    levels = client.get(_TS_ENTRY_URL.format(entry.public_ref)).json()[
        "record"
    ]["evidence_summary"]["levels_of_theory"]

    assert set(levels) == {"opt", "freq", "sp", "irc"}
    for key, entries in levels.items():
        assert isinstance(entries, list), f"{key} is {type(entries).__name__}"
        assert len(entries) == 1
        assert entries[0]["display"] == "b3lyp/def2tzvp"


def test_two_levels_for_one_calculation_type_are_both_reported(
    client, db_session
):
    """The within-type case: two ``sp`` jobs on one entry, at two levels.

    The direct query finds none of these among transition-state entries
    today — but 27 (species entry, type) pairs already have it, so this is
    deposited data's shape and not a hypothetical. It is the case a scalar
    field cannot represent at all, and the reason the value is a list rather
    than a scalar-or-list union.
    """
    _, (entry,) = _ts_entry(db_session)
    for method in ("b3lyp", "wb97xd"):
        make_calculation(
            db_session,
            type=CalculationType.sp,
            transition_state_entry_id=entry.id,
            lot_id=make_lot(db_session, method=method, basis="def2tzvp").id,
        )

    levels = client.get(_TS_ENTRY_URL.format(entry.public_ref)).json()[
        "record"
    ]["evidence_summary"]["levels_of_theory"]

    assert [lot["display"] for lot in levels["sp"]] == [
        "b3lyp/def2tzvp",
        "wb97xd/def2tzvp",
    ]


# ---------------------------------------------------------------------------
# Absent key vs empty list
# ---------------------------------------------------------------------------


def test_a_calculation_type_with_no_calculation_has_no_key(
    client, db_session
):
    """No ``sp`` job means no ``sp`` key — not ``null``, not ``[]``.

    On the owner's own record there is no single point, and the missing key
    is precisely what should make that gap visible. ``null`` would say the
    same thing an unattributed level says, and ``[]`` is reserved for that
    other meaning.
    """
    _, (entry,) = _ts_entry(db_session)
    lot = make_lot(db_session, method="b3lyp", basis="def2tzvp")
    make_calculation(
        db_session,
        type=CalculationType.opt,
        transition_state_entry_id=entry.id,
        lot_id=lot.id,
    )

    body = client.get(_TS_ENTRY_URL.format(entry.public_ref)).json()
    summary = body["record"]["evidence_summary"]
    levels = summary["levels_of_theory"]

    assert "sp" not in levels
    assert list(levels) == ["opt"]
    # The absent key and the ``has_*`` boolean tell the same story.
    assert summary["has_sp"] is False
    assert summary["has_opt"] is True


def test_a_calculation_naming_no_level_gives_the_type_an_empty_list(
    client, db_session
):
    """``lot_id IS NULL`` is a provenance gap, and it must be visible as one.

    "No calculation of this type" and "a calculation of this type exists and
    none of them names a level" are different facts. The first is an absent
    key; this is the second, and it gets the key with an empty list. There
    are no such calculations on the deployed database today — the column is
    nullable, so there can be tomorrow.
    """
    _, (entry,) = _ts_entry(db_session)
    make_calculation(
        db_session,
        type=CalculationType.freq,
        transition_state_entry_id=entry.id,
        lot_id=None,
    )

    body = client.get(_TS_ENTRY_URL.format(entry.public_ref)).json()
    summary = body["record"]["evidence_summary"]

    assert summary["levels_of_theory"] == {"freq": []}
    assert summary["has_freq"] is True, (
        "the calculation exists — the empty list is about its level, not "
        "about the calculation"
    )


def test_a_record_with_no_calculations_gets_an_empty_map(client, db_session):
    _, (entry,) = _ts_entry(db_session)

    summary = client.get(_TS_ENTRY_URL.format(entry.public_ref)).json()[
        "record"
    ]["evidence_summary"]

    assert summary["levels_of_theory"] == {}
    assert summary["calculation_count"] == 0


def test_a_partly_attributed_type_reports_only_the_attributed_levels(
    client, db_session
):
    """One ``sp`` names a level, another does not: the list holds the one.

    The empty list is for the case where *nothing* of that type is
    attributed. A partial gap is not representable in this block and should
    not be faked — the map says which levels were used, and a calculation
    that names none contributes none.
    """
    _, (entry,) = _ts_entry(db_session)
    lot = make_lot(db_session, method="b3lyp", basis="def2tzvp")
    make_calculation(
        db_session,
        type=CalculationType.sp,
        transition_state_entry_id=entry.id,
        lot_id=lot.id,
    )
    make_calculation(
        db_session,
        type=CalculationType.sp,
        transition_state_entry_id=entry.id,
        lot_id=None,
    )

    levels = client.get(_TS_ENTRY_URL.format(entry.public_ref)).json()[
        "record"
    ]["evidence_summary"]["levels_of_theory"]

    assert [lot["display"] for lot in levels["sp"]] == ["b3lyp/def2tzvp"]


# ---------------------------------------------------------------------------
# ``display``
# ---------------------------------------------------------------------------


def test_display_renders_method_alone_when_there_is_no_basis(
    client, db_session
):
    """``"AM1"``, never ``"AM1/"``. A composite method has no basis set."""
    _, (entry,) = _ts_entry(db_session)
    lot = make_lot(db_session, method="AM1", basis=None)
    make_calculation(
        db_session,
        type=CalculationType.opt,
        transition_state_entry_id=entry.id,
        lot_id=lot.id,
    )

    levels = client.get(_TS_ENTRY_URL.format(entry.public_ref)).json()[
        "record"
    ]["evidence_summary"]["levels_of_theory"]

    assert levels["opt"][0]["display"] == "AM1"
    assert levels["opt"][0]["basis"] is None


def test_display_lands_on_every_existing_level_of_theory_summary(
    client, db_session
):
    """It was added to the shared shape, so the 13 other call sites get it.

    Adding a second LoT shape for this one block would have drifted from the
    shared one within a release. Proving the field is on a *different*
    surface is what says it was added to the shape and not to this block.
    """
    _, (entry,) = _ts_entry(db_session)
    lot = make_lot(db_session, method="wb97xd", basis="def2tzvp")
    make_calculation(
        db_session,
        type=CalculationType.opt,
        transition_state_entry_id=entry.id,
        lot_id=lot.id,
    )

    body = client.get(
        _TS_ENTRY_URL.format(entry.public_ref) + "?include=calculations"
    ).json()
    calcs = body["record"]["calculations"]
    assert calcs, "the include must actually return a calculation"
    assert calcs[0]["level_of_theory"]["display"] == "wb97xd/def2tzvp"


# ---------------------------------------------------------------------------
# Pooled grain: TS concept and conformer group
# ---------------------------------------------------------------------------


def test_a_ts_concept_pools_the_levels_of_all_its_entries(client, db_session):
    """The union, and the union is the honest answer at pooled grain.

    Which entry used which is answerable from ``include=entries``; what the
    concept block can say is "these are the levels used somewhere under this
    TS", which is exactly what a reader comparing two concepts needs.
    """
    ts, (first, second) = _ts_entry(db_session, n_entries=2)
    make_calculation(
        db_session,
        type=CalculationType.opt,
        transition_state_entry_id=first.id,
        lot_id=make_lot(db_session, method="b3lyp", basis="def2tzvp").id,
    )
    make_calculation(
        db_session,
        type=CalculationType.opt,
        transition_state_entry_id=second.id,
        lot_id=make_lot(db_session, method="wb97xd", basis="def2tzvp").id,
    )

    body = client.get(_TS_URL.format(ts.public_ref)).json()
    levels = body["record"]["evidence_summary"]["levels_of_theory"]

    assert [lot["display"] for lot in levels["opt"]] == [
        "b3lyp/def2tzvp",
        "wb97xd/def2tzvp",
    ]
    # ``evidence_coverage`` counts entries; the map does not double-count a
    # level two entries share.
    assert body["record"]["evidence_summary"]["evidence_coverage"]["opt"] == 2


def test_a_shared_level_is_pooled_once_not_once_per_entry(client, db_session):
    ts, entries = _ts_entry(db_session, n_entries=3)
    lot = make_lot(db_session, method="b3lyp", basis="def2tzvp")
    for entry in entries:
        make_calculation(
            db_session,
            type=CalculationType.freq,
            transition_state_entry_id=entry.id,
            lot_id=lot.id,
        )

    levels = client.get(_TS_URL.format(ts.public_ref)).json()["record"][
        "evidence_summary"
    ]["levels_of_theory"]

    assert len(levels["freq"]) == 1


def test_a_conformer_basin_reports_the_levels_its_coverage_cannot(
    client, db_session
):
    """``freq == observation_count`` with two levels under ``freq``.

    Complete coverage, two different levels — the exact case
    ``ConformerEvidenceCoverage``'s docstring describes and then says no
    number in that block can stand in for. The map states it. It still does
    not assert the two are comparable.
    """
    entry, group, (first, second) = _conformer_group(
        db_session, n_observations=2
    )
    make_calculation_with_conformer(
        db_session,
        species_entry=entry,
        conformer_observation=first,
        type=CalculationType.freq,
        lot_id=make_lot(db_session, method="b3lyp", basis="def2tzvp").id,
    )
    make_calculation_with_conformer(
        db_session,
        species_entry=entry,
        conformer_observation=second,
        type=CalculationType.freq,
        lot_id=make_lot(db_session, method="wb97xd", basis="def2tzvp").id,
    )

    summary = client.get(_CG_URL.format(group.public_ref)).json()["record"][
        "evidence_summary"
    ]

    assert summary["evidence_coverage"]["freq"] == summary["observation_count"]
    assert [lot["display"] for lot in summary["levels_of_theory"]["freq"]] == [
        "b3lyp/def2tzvp",
        "wb97xd/def2tzvp",
    ]


def test_a_conformer_observation_reports_only_its_own_levels(
    client, db_session
):
    """Observation scope, not basin scope: the sibling's level is not here."""
    entry, _, (first, second) = _conformer_group(db_session, n_observations=2)
    make_calculation_with_conformer(
        db_session,
        species_entry=entry,
        conformer_observation=first,
        type=CalculationType.sp,
        lot_id=make_lot(db_session, method="b3lyp", basis="def2tzvp").id,
    )
    make_calculation_with_conformer(
        db_session,
        species_entry=entry,
        conformer_observation=second,
        type=CalculationType.sp,
        lot_id=make_lot(db_session, method="wb97xd", basis="def2tzvp").id,
    )

    levels = client.get(_CO_URL.format(first.public_ref)).json()["record"][
        "evidence_summary"
    ]["levels_of_theory"]

    assert [lot["display"] for lot in levels["sp"]] == ["b3lyp/def2tzvp"]


# ---------------------------------------------------------------------------
# Search and detail agree
# ---------------------------------------------------------------------------


def test_search_and_detail_report_the_same_map(client, db_session):
    """The batched page query and the single-record one must not diverge.

    Search resolves the whole page in one statement; detail resolves one
    record. Two code paths to the same field is how a page-only bug hides,
    so the two are asserted equal on the same record.
    """
    _, (entry,) = _ts_entry(db_session)
    make_calculation(
        db_session,
        type=CalculationType.opt,
        transition_state_entry_id=entry.id,
        lot_id=make_lot(db_session, method="wb97xd", basis="def2tzvp").id,
    )
    make_calculation(
        db_session,
        type=CalculationType.sp,
        transition_state_entry_id=entry.id,
        lot_id=make_lot(db_session, method="CCSD(T)-F12", basis="cc-pVTZ-F12").id,
    )

    detail = client.get(_TS_ENTRY_URL.format(entry.public_ref)).json()[
        "record"
    ]["evidence_summary"]["levels_of_theory"]
    found = client.get(
        "/api/v1/scientific/transition-states/search"
        f"?transition_state_entry_ref={entry.public_ref}"
    ).json()
    assert found["records"], "the search must return the record"
    assert found["records"][0]["evidence_summary"]["levels_of_theory"] == detail
    assert set(detail) == {"opt", "sp"}


def test_conformer_search_and_detail_report_the_same_map(client, db_session):
    entry, group, (observation,) = _conformer_group(db_session)
    make_calculation_with_conformer(
        db_session,
        species_entry=entry,
        conformer_observation=observation,
        type=CalculationType.opt,
        lot_id=make_lot(db_session, method="wb97xd", basis="def2tzvp").id,
    )

    detail = client.get(_CG_URL.format(group.public_ref)).json()["record"][
        "evidence_summary"
    ]["levels_of_theory"]
    found = client.get(
        "/api/v1/scientific/conformers/search"
        f"?conformer_group_ref={group.public_ref}"
    ).json()
    assert found["records"], "the search must return the record"
    assert found["records"][0]["evidence_summary"]["levels_of_theory"] == detail
    assert [lot["display"] for lot in detail["opt"]] == ["wb97xd/def2tzvp"]


# ---------------------------------------------------------------------------
# Not include-gated
# ---------------------------------------------------------------------------


def test_the_map_is_present_without_any_include_token(client, db_session):
    """It is in ``evidence_summary``, whose contract is unconditional presence.

    Gating it would restate the complaint that produced it — the level of
    theory absent by default — one layer up, and would put a conditionally
    present field inside a block that is always present.
    """
    _, (entry,) = _ts_entry(db_session)
    make_calculation(
        db_session,
        type=CalculationType.opt,
        transition_state_entry_id=entry.id,
        lot_id=make_lot(db_session, method="b3lyp", basis="def2tzvp").id,
    )

    body = client.get(_TS_ENTRY_URL.format(entry.public_ref)).json()
    assert body["request"]["include"] == []
    assert "levels_of_theory" in body["record"]["evidence_summary"]
