"""The selection layer is an opinion about the science, never part of it.

These tests pin the three properties the Stage 3 review asked for by name:

* a release selection never mutates the record it selects;
* superseding appends a row rather than editing one;
* the append-only contract holds against direct SQL, not only against the
  service layer.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select, text

from app.db.models.common import (
    DatasetReleaseStatus,
    RecordReviewStatus,
    ReleaseSelectionAction,
    SubmissionRecordType,
)
from app.db.models.dataset_release import ReleaseSelection
from app.db.models.thermo import Thermo
from app.services.record_review import set_record_review_status
from app.services.release.curation import (
    ReleaseCurationError,
    add_selection,
    create_release,
    current_selection,
    publish_release,
    record_doi,
    resolve_curation_policy,
    resolve_selectable_record,
    supersede_selection,
    withdraw_release,
    withdraw_selection,
)
from app.services.scientific_read.releases import get_release_selections
from app.services.scientific_record_supersession import supersede_scientific_record
from tests.services.scientific_read._factories import make_thermo_scalar


def _snapshot(session, thermo_id: int) -> dict:
    """Every stored column of a thermo row, as raw values."""
    row = session.execute(
        select(Thermo.__table__).where(Thermo.__table__.c.id == thermo_id)
    ).mappings().one()
    return dict(row)


def _select_first(session, release, curator, thermo, species_entry):
    return add_selection(
        session,
        release=release,
        record_type=SubmissionRecordType.thermo,
        record_id=thermo.id,
        subject_type=SubmissionRecordType.species_entry,
        subject_id=species_entry.id,
        rationale="CCSD(T)-F12 composite; all frequencies real.",
        selected_by=curator.id,
    )


# ---------------------------------------------------------------------------
# The selection does not touch the science
# ---------------------------------------------------------------------------


def test_selection_does_not_mutate_the_selected_record(
    db_session, draft_release, curator, thermo_candidates, species_entry
):
    chosen, _other = thermo_candidates
    before = _snapshot(db_session, chosen.id)

    _select_first(db_session, draft_release, curator, chosen, species_entry)
    db_session.flush()

    assert _snapshot(db_session, chosen.id) == before


def test_superseding_does_not_mutate_either_record(
    db_session, draft_release, curator, second_curator, thermo_candidates, species_entry
):
    first, second = thermo_candidates
    before = {t.id: _snapshot(db_session, t.id) for t in (first, second)}

    original = _select_first(db_session, draft_release, curator, first, species_entry)
    supersede_selection(
        db_session,
        superseded=original,
        record_id=second.id,
        rationale="New W1X-2 result is closer to the ATcT reference.",
        selected_by=second_curator.id,
    )
    db_session.flush()

    for thermo_id, snapshot in before.items():
        assert _snapshot(db_session, thermo_id) == snapshot


def test_thermo_table_has_no_selected_or_preferred_column():
    """The whole design rests on there being no ``is_best`` flag. Pin it."""
    columns = set(Thermo.__table__.c.keys())
    forbidden = {
        "is_best",
        "is_preferred",
        "is_selected",
        "is_canonical",
        "is_default",
        "release_selection_id",
        "dataset_release_id",
    }
    assert not (columns & forbidden)


# ---------------------------------------------------------------------------
# Superseding appends
# ---------------------------------------------------------------------------


def test_supersede_appends_a_row_and_leaves_the_original_intact(
    db_session, draft_release, curator, second_curator, thermo_candidates, species_entry
):
    first, second = thermo_candidates
    original = _select_first(db_session, draft_release, curator, first, species_entry)
    original_ref = original.public_ref
    original_rationale = original.rationale
    original_created_at = original.created_at
    original_curator = original.selected_by

    replacement = supersede_selection(
        db_session,
        superseded=original,
        record_id=second.id,
        rationale="Superseded: the earlier candidate used an unscaled frequency set.",
        selected_by=second_curator.id,
    )

    rows = list(
        db_session.scalars(
            select(ReleaseSelection)
            .where(ReleaseSelection.dataset_release_id == draft_release.id)
            .order_by(ReleaseSelection.id)
        )
    )
    assert len(rows) == 2, "superseding must append, not edit"

    # The superseded row is byte-for-byte what the first curator wrote.
    kept = rows[0]
    assert kept.public_ref == original_ref
    assert kept.rationale == original_rationale
    assert kept.created_at == original_created_at
    assert kept.selected_by == original_curator
    assert kept.action is ReleaseSelectionAction.select
    assert kept.record_id == first.id

    assert replacement.action is ReleaseSelectionAction.supersede
    assert replacement.supersedes_selection_id == kept.id
    assert replacement.record_id == second.id
    assert replacement.selected_by == second_curator.id


def test_current_selection_follows_the_chain_head(
    db_session, draft_release, curator, second_curator, thermo_candidates, species_entry
):
    first, second = thermo_candidates
    original = _select_first(db_session, draft_release, curator, first, species_entry)
    assert (
        current_selection(
            db_session,
            release=draft_release,
            subject_type=SubmissionRecordType.species_entry,
            subject_id=species_entry.id,
            record_type=SubmissionRecordType.thermo,
        ).id
        == original.id
    )

    replacement = supersede_selection(
        db_session,
        superseded=original,
        record_id=second.id,
        rationale="Better level of theory.",
        selected_by=second_curator.id,
    )
    assert (
        current_selection(
            db_session,
            release=draft_release,
            subject_type=SubmissionRecordType.species_entry,
            subject_id=species_entry.id,
            record_type=SubmissionRecordType.thermo,
        ).id
        == replacement.id
    )


def test_withdrawal_leaves_no_standing_selection_but_keeps_the_ledger(
    db_session, draft_release, curator, thermo_candidates, species_entry
):
    first, _second = thermo_candidates
    original = _select_first(db_session, draft_release, curator, first, species_entry)

    withdrawn = withdraw_selection(
        db_session,
        superseded=original,
        rationale="Both candidates disagree with the shock-tube measurement.",
        selected_by=curator.id,
    )

    assert withdrawn.action is ReleaseSelectionAction.withdraw
    assert withdrawn.record_id == original.record_id
    assert (
        current_selection(
            db_session,
            release=draft_release,
            subject_type=SubmissionRecordType.species_entry,
            subject_id=species_entry.id,
            record_type=SubmissionRecordType.thermo,
        )
        is None
    )
    # The reasoning is still on the record.
    rows = db_session.scalars(
        select(ReleaseSelection).where(
            ReleaseSelection.dataset_release_id == draft_release.id
        )
    ).all()
    assert len(rows) == 2


def test_a_selection_can_only_be_superseded_once(
    db_session, draft_release, curator, thermo_candidates, species_entry
):
    first, second = thermo_candidates
    original = _select_first(db_session, draft_release, curator, first, species_entry)
    supersede_selection(
        db_session,
        superseded=original,
        record_id=second.id,
        rationale="First replacement.",
        selected_by=curator.id,
    )
    with pytest.raises(ReleaseCurationError, match="already_superseded"):
        supersede_selection(
            db_session,
            superseded=original,
            record_id=second.id,
            rationale="Second replacement of the same row.",
            selected_by=curator.id,
        )


# ---------------------------------------------------------------------------
# Append-only holds below the service layer
# ---------------------------------------------------------------------------


def test_direct_update_of_a_selection_is_rejected_by_the_database(
    db_session, draft_release, curator, thermo_candidates, species_entry
):
    first, _second = thermo_candidates
    selection = _select_first(db_session, draft_release, curator, first, species_entry)
    db_session.flush()

    with pytest.raises(Exception):
        db_session.execute(
            text("UPDATE release_selection SET rationale = :r WHERE id = :i"),
            {"r": "rewritten history", "i": selection.id},
        )
    db_session.rollback()


def test_direct_delete_of_a_selection_is_rejected_by_the_database(
    db_session, draft_release, curator, thermo_candidates, species_entry
):
    first, _second = thermo_candidates
    selection = _select_first(db_session, draft_release, curator, first, species_entry)
    db_session.flush()

    with pytest.raises(Exception):
        db_session.execute(
            text("DELETE FROM release_selection WHERE id = :i"), {"i": selection.id}
        )
    db_session.rollback()


# ---------------------------------------------------------------------------
# Coherence guards
# ---------------------------------------------------------------------------


def test_selection_rejects_a_record_that_belongs_to_a_different_subject(
    db_session, draft_release, curator, thermo_candidates, species_entry
):
    from tests.services.scientific_read._factories import (
        make_species,
        make_species_entry,
    )

    first, _second = thermo_candidates
    other_entry = make_species_entry(
        db_session, species=make_species(db_session, smiles="CC")
    )
    with pytest.raises(ReleaseCurationError, match="record_subject_mismatch"):
        add_selection(
            db_session,
            release=draft_release,
            record_type=SubmissionRecordType.thermo,
            record_id=first.id,
            subject_type=SubmissionRecordType.species_entry,
            subject_id=other_entry.id,
            rationale="Wrong subject.",
            selected_by=curator.id,
        )


def test_a_second_standing_selection_for_one_subject_is_refused(
    db_session, draft_release, curator, thermo_candidates, species_entry
):
    first, second = thermo_candidates
    _select_first(db_session, draft_release, curator, first, species_entry)
    with pytest.raises(ReleaseCurationError, match="selection_already_stands"):
        add_selection(
            db_session,
            release=draft_release,
            record_type=SubmissionRecordType.thermo,
            record_id=second.id,
            subject_type=SubmissionRecordType.species_entry,
            subject_id=species_entry.id,
            rationale="Second opinion, added rather than superseding.",
            selected_by=curator.id,
        )


def test_selections_cannot_be_appended_to_a_published_release(
    db_session, draft_release, curator, thermo_candidates, species_entry
):
    first, _second = thermo_candidates
    publish_release(db_session, draft_release)
    with pytest.raises(ReleaseCurationError, match="release_not_draft"):
        _select_first(db_session, draft_release, curator, first, species_entry)


def test_rationale_is_mandatory(
    db_session, draft_release, curator, thermo_candidates, species_entry
):
    first, _second = thermo_candidates
    with pytest.raises(ReleaseCurationError, match="rationale_required"):
        add_selection(
            db_session,
            release=draft_release,
            record_type=SubmissionRecordType.thermo,
            record_id=first.id,
            subject_type=SubmissionRecordType.species_entry,
            subject_id=species_entry.id,
            rationale="   ",
            selected_by=curator.id,
        )


def test_resolve_selectable_record_derives_the_subject(
    db_session, thermo_candidates, species_entry
):
    first, _second = thermo_candidates
    resolved = resolve_selectable_record(db_session, first.public_ref)
    assert resolved.record_type is SubmissionRecordType.thermo
    assert resolved.record_id == first.id
    assert resolved.subject_type is SubmissionRecordType.species_entry
    assert resolved.subject_id == species_entry.id


def test_resolve_selectable_record_rejects_a_non_selectable_prefix(db_session):
    with pytest.raises(ReleaseCurationError, match="record_ref_not_selectable"):
        resolve_selectable_record(db_session, "calc_0123456789abcdefghijklmnop")


# ---------------------------------------------------------------------------
# Policy and release lifecycle
# ---------------------------------------------------------------------------


def test_policy_version_cannot_be_rewritten(db_session, curator, policy):
    # Same content resolves to the same row.
    again = resolve_curation_policy(
        db_session,
        name=policy.name,
        version=policy.version,
        description=policy.description,
        criteria=dict(policy.criteria_json),
        created_by=curator.id,
    )
    assert again.id == policy.id

    with pytest.raises(ReleaseCurationError, match="version_conflict"):
        resolve_curation_policy(
            db_session,
            name=policy.name,
            version=policy.version,
            description="Quietly different rubric.",
            created_by=curator.id,
        )


def test_release_tag_is_unique(db_session, policy, curator, draft_release):
    with pytest.raises(ReleaseCurationError, match="release_tag_taken"):
        create_release(
            db_session,
            tag=draft_release.tag,
            title="Duplicate",
            curation_policy=policy,
            data_license="CC-BY-4.0",
            code_license="MIT",
            citation_text="x",
            contact="x@example.org",
            created_by=curator.id,
        )


def test_withdrawn_release_keeps_its_row_and_reason(db_session, draft_release):
    publish_release(db_session, draft_release)
    withdraw_release(db_session, draft_release, reason="Systematic AEC error.")
    assert draft_release.status is DatasetReleaseStatus.withdrawn
    assert draft_release.withdrawn_reason == "Systematic AEC error."
    assert draft_release.published_at is not None, "a citation must not dangle"


def test_doi_is_never_minted_and_cannot_be_repointed(db_session, draft_release):
    assert draft_release.doi is None, "TCKDB must not mint a DOI speculatively"
    with pytest.raises(ReleaseCurationError, match="release_not_published"):
        record_doi(db_session, draft_release, doi="10.5281/zenodo.1")

    publish_release(db_session, draft_release)
    record_doi(db_session, draft_release, doi="10.5281/zenodo.1")
    assert draft_release.doi == "10.5281/zenodo.1"
    # Idempotent for the same DOI, refused for a different one.
    record_doi(db_session, draft_release, doi="10.5281/zenodo.1")
    with pytest.raises(ReleaseCurationError, match="doi_already_recorded"):
        record_doi(db_session, draft_release, doi="10.5281/zenodo.2")


# ---------------------------------------------------------------------------
# Drift guards
# ---------------------------------------------------------------------------


def test_selectable_record_types_match_the_migration_check_constraint():
    """The ORM CHECK and the migration's literal list must not diverge."""
    import re
    from pathlib import Path

    from app.db.models.dataset_release import SELECTABLE_RECORD_TYPES

    revision = (
        Path(__file__).resolve().parents[3]
        / "alembic"
        / "versions"
        / "e3f4a5b6c7d8_stage3_curated_release_semantics.py"
    ).read_text()
    block = re.search(
        r"_SELECTABLE_RECORD_TYPES = \((.*?)\)", revision, re.S
    ).group(1)
    in_migration = set(re.findall(r'"([a-z_]+)"', block))
    assert in_migration == {t.value for t in SELECTABLE_RECORD_TYPES}


def test_selectable_ref_prefixes_match_the_public_ref_registry():
    """A wrong prefix here would silently make a record unselectable."""
    from app.db.models.dataset_release import SELECTABLE_RECORD_TYPES
    from app.services.public_refs import PREFIXES
    from app.services.release.curation import SELECTABLE_REF_PREFIXES
    from app.services.release.records import CANDIDATE_SOURCES

    assert set(SELECTABLE_REF_PREFIXES.values()) == SELECTABLE_RECORD_TYPES
    for prefix, record_type in SELECTABLE_REF_PREFIXES.items():
        model, _fk, _subject = CANDIDATE_SOURCES[record_type]
        assert PREFIXES[model.__name__] == prefix, record_type


def test_every_selectable_type_has_a_value_table_map():
    from app.db.models.dataset_release import SELECTABLE_RECORD_TYPES
    from app.services.release.records import (
        CANDIDATE_SOURCES,
        RECORD_TABLES,
        RECORD_VALUE_TABLES,
    )

    assert set(RECORD_TABLES) == SELECTABLE_RECORD_TYPES
    assert set(RECORD_VALUE_TABLES) == SELECTABLE_RECORD_TYPES
    assert set(CANDIDATE_SOURCES) == SELECTABLE_RECORD_TYPES


def test_every_child_table_is_either_shipped_or_deliberately_excluded():
    """Enumerating keys is not enough — the *contents* have to be complete.

    ``applied_group_additivity`` was missing from thermo, so a Benson-group
    estimate would have shipped in a citable release looking exactly like a
    computed value. An earlier version of this test asserted only that every
    selectable type had *some* entry.

    Two properties are enforced:

    1. Every child of a shipped table is accounted for **under that parent**.
       The exclusion registry is keyed on ``(parent, child)`` because four
       tables are legitimately shipped under one parent and excused under
       another; a global excuse would let a refactor drop ``network_kinetics``
       from ``network_solve`` and still pass, silently dropping k(T,P) from
       every PDep release.
    2. The walk descends into *shipped children*, not just the six record
       tables, so grandchildren are covered rather than being accounted for by
       luck.
    """
    from app.db.base import Base
    from app.services.release.records import (
        RECORD_CHILD_EXCLUSIONS,
        RECORD_TABLES,
        RECORD_VALUE_TABLES,
    )

    def children_of(table_name: str) -> set[str]:
        target = Base.metadata.tables[table_name]
        return {
            table.name
            for table in Base.metadata.tables.values()
            for fk in table.foreign_keys
            if fk.column.table is target
        }

    unaccounted: dict[str, set[str]] = {}
    for record_type, root in RECORD_TABLES.items():
        # (parent, direct children shipped under it), walked breadth-first so
        # grandchildren of shipped tables are checked too.
        frontier = [(root, RECORD_VALUE_TABLES[record_type])]
        seen: set[str] = set()
        while frontier:
            parent, specs = frontier.pop()
            if parent in seen:
                continue
            seen.add(parent)
            shipped_here = {spec.table for spec in specs}
            missing = {
                child
                for child in children_of(parent)
                if child not in shipped_here
                and (parent, child) not in RECORD_CHILD_EXCLUSIONS
            }
            if missing:
                unaccounted.setdefault(parent, set()).update(missing)
            for spec in specs:
                frontier.append((spec.table, spec.children))

    assert unaccounted == {}, (
        "child tables neither shipped in a release nor excused under their "
        f"parent: {unaccounted}. Add them to RECORD_VALUE_TABLES under that "
        "parent, or to RECORD_CHILD_EXCLUSIONS keyed on (parent, child) with "
        "the reason they are not scientific content of that record."
    )


def test_exclusions_are_keyed_per_parent_not_globally():
    """A table shipped under one parent may be excused under another.

    Pins the reason the registry is a pair key: if these entries could be
    written as bare table names, an excuse earned in one place would silence
    the guard everywhere.
    """
    from app.services.release.records import (
        RECORD_CHILD_EXCLUSIONS,
        RECORD_VALUE_TABLES,
    )

    def shipped(specs) -> set[str]:
        out = set()
        for spec in specs:
            out.add(spec.table)
            out |= shipped(spec.children)
        return out

    everything_shipped = set()
    for specs in RECORD_VALUE_TABLES.values():
        everything_shipped |= shipped(specs)

    assert all(
        isinstance(key, tuple) and len(key) == 2 for key in RECORD_CHILD_EXCLUSIONS
    )
    both = {
        child for (_parent, child) in RECORD_CHILD_EXCLUSIONS if child in everything_shipped
    }
    assert both, (
        "expected at least one table that is shipped under one parent and "
        "excused under another — that is why the key is a pair"
    )
    assert "network_kinetics" in both


def test_dropping_a_shipped_child_is_caught_even_if_excused_elsewhere():
    """The exact refactor the pair key exists to catch.

    Simulates a maintainer removing ``network_kinetics`` from
    ``network_solve``. Under a global excuse this passed and every PDep
    release silently stopped shipping its rate coefficients.
    """
    from app.db.base import Base
    from app.db.models.common import SubmissionRecordType
    from app.services.release.records import (
        RECORD_CHILD_EXCLUSIONS,
        RECORD_VALUE_TABLES,
    )

    damaged = tuple(
        spec
        for spec in RECORD_VALUE_TABLES[SubmissionRecordType.network_solve]
        if spec.table != "network_kinetics"
    )
    shipped_here = {spec.table for spec in damaged}
    target = Base.metadata.tables["network_solve"]
    children = {
        table.name
        for table in Base.metadata.tables.values()
        for fk in table.foreign_keys
        if fk.column.table is target
    }
    missing = {
        child
        for child in children
        if child not in shipped_here
        and ("network_solve", child) not in RECORD_CHILD_EXCLUSIONS
    }
    assert "network_kinetics" in missing


def test_kinetics_interpretation_assignment_ships_with_kinetics():
    """It changes how a released A-factor must be read, so it is not optional.

    ``standard_state_convention`` and ``ensemble_policy`` are interpretation
    provenance: a rate coefficient reported under a different standard state is
    a different number. It was previously excused as a "curation overlay",
    which was simply the wrong reason.
    """
    from app.db.base import Base
    from app.db.models.common import SubmissionRecordType
    from app.services.release.records import RECORD_VALUE_TABLES

    shipped = {
        spec.table for spec in RECORD_VALUE_TABLES[SubmissionRecordType.kinetics]
    }
    assert "kinetics_interpretation_assignment" in shipped
    columns = Base.metadata.tables["kinetics_interpretation_assignment"].c.keys()
    assert {"standard_state_convention", "ensemble_policy"} <= set(columns)


def test_declared_child_tables_and_columns_exist():
    """A typo in the map would silently ship an empty list for that child."""
    from app.db.base import Base
    from app.services.release.records import RECORD_VALUE_TABLES

    def check(spec) -> None:
        table = Base.metadata.tables[spec.table]
        assert spec.fk_column in table.c, f"{spec.table}.{spec.fk_column}"
        for child in spec.children:
            check(child)

    for specs in RECORD_VALUE_TABLES.values():
        for spec in specs:
            check(spec)


# ---------------------------------------------------------------------------
# A release may only recommend records a human approved
# ---------------------------------------------------------------------------


def test_selecting_an_unreviewed_record_is_refused(
    db_session, draft_release, curator, species_entry
):
    """The finding that failed review, at its root.

    An unapproved product row is *not* covered by the accepted-science
    immutability trigger, so a release that selected one could have the value
    under its own recommendation edited after publication. Restricting
    selections to approved records is what makes "a released value cannot drift
    by edit" true rather than accidentally true — and it removes the
    contradiction of ``profile=curated`` refusing to show a record that a
    published release recommends.
    """
    from tests.services.scientific_read._factories import make_thermo_scalar

    unreviewed = make_thermo_scalar(
        db_session, species_entry=species_entry, h298_kj_mol=-1.0, s298_j_mol_k=1.0
    )
    with pytest.raises(ReleaseCurationError, match="record_not_approved"):
        add_selection(
            db_session,
            release=draft_release,
            record_type=SubmissionRecordType.thermo,
            record_id=unreviewed.id,
            subject_type=SubmissionRecordType.species_entry,
            subject_id=species_entry.id,
            rationale="Looks fine to me.",
            selected_by=curator.id,
        )


def test_selecting_a_rejected_record_is_refused(
    db_session, draft_release, curator, species_entry
):
    from app.db.models.common import RecordReviewStatus
    from app.services.record_review import set_record_review_status
    from tests.services.scientific_read._factories import make_thermo_scalar

    rejected = make_thermo_scalar(
        db_session, species_entry=species_entry, h298_kj_mol=-1.0, s298_j_mol_k=1.0
    )
    set_record_review_status(
        db_session,
        record_type=SubmissionRecordType.thermo,
        record_id=rejected.id,
        status=RecordReviewStatus.rejected,
        actor=curator,
    )
    with pytest.raises(ReleaseCurationError, match="record_not_approved"):
        add_selection(
            db_session,
            release=draft_release,
            record_type=SubmissionRecordType.thermo,
            record_id=rejected.id,
            subject_type=SubmissionRecordType.species_entry,
            subject_id=species_entry.id,
            rationale="Rejected but I like it.",
            selected_by=curator.id,
        )


def test_superseding_with_an_unapproved_record_is_refused(
    db_session, draft_release, curator, thermo_candidates, species_entry
):
    from tests.services.scientific_read._factories import make_thermo_scalar

    first, _second = thermo_candidates
    original = _select_first(db_session, draft_release, curator, first, species_entry)
    unreviewed = make_thermo_scalar(
        db_session, species_entry=species_entry, h298_kj_mol=-2.0, s298_j_mol_k=2.0
    )
    with pytest.raises(ReleaseCurationError, match="record_not_approved"):
        supersede_selection(
            db_session,
            superseded=original,
            record_id=unreviewed.id,
            rationale="Newer, unreviewed.",
            selected_by=curator.id,
        )


def test_publishing_re_checks_approval_after_a_demotion(
    db_session, draft_release, curator, thermo_candidates, species_entry
):
    """Review moves between selecting and publishing; the check must run twice."""
    from app.db.models.common import RecordReviewStatus
    from app.services.record_review import set_record_review_status

    first, _second = thermo_candidates
    _select_first(db_session, draft_release, curator, first, species_entry)

    set_record_review_status(
        db_session,
        record_type=SubmissionRecordType.thermo,
        record_id=first.id,
        status=RecordReviewStatus.deprecated,
        actor=curator,
    )
    db_session.flush()

    with pytest.raises(ReleaseCurationError, match="selection_no_longer_approved"):
        publish_release(db_session, draft_release)


def test_an_approved_selected_record_is_frozen_by_the_science_trigger(
    db_session, draft_release, curator, thermo_candidates, species_entry
):
    """The claim the approval rule exists to make true, asserted directly."""
    first, _second = thermo_candidates
    _select_first(db_session, draft_release, curator, first, species_entry)
    db_session.flush()

    with pytest.raises(Exception, match="immutable"):
        db_session.execute(
            text("UPDATE thermo SET h298_kj_mol = :v WHERE id = :i"),
            {"v": -999.0, "i": first.id},
        )
    db_session.rollback()


# ---------------------------------------------------------------------------
# A published release must announce post-cut supersession of its selections
# ---------------------------------------------------------------------------


def test_the_selection_ledger_announces_a_post_cut_record_supersession(
    db_session, draft_release, curator, species_entry, thermo_candidates
):
    """A standing selection whose selected record was later corrected.

    This is the state that can mislead someone *outside* the project: a
    DOI-bearing release points at a number, the citation resolves cleanly, and
    nothing says the number has since been replaced.

    ``live_divergence`` is not this answer. It compares per-file byte digests
    and reports "the database has moved" — advisory, routinely ``true``, and
    unable to name a record. Three links again, so "immediate successor" and
    "head" cannot be confused.
    """
    first, second = thermo_candidates
    third = make_thermo_scalar(
        db_session, species_entry=species_entry, h298_kj_mol=-236.4
    )
    set_record_review_status(
        db_session,
        record_type=SubmissionRecordType.thermo,
        record_id=third.id,
        status=RecordReviewStatus.approved,
        actor=curator,
        note="third candidate",
    )
    selection = _select_first(
        db_session, draft_release, curator, first, species_entry
    )
    before = _snapshot(db_session, first.id)

    for older, newer in ((first, second), (second, third)):
        supersede_scientific_record(
            db_session,
            record_type=SubmissionRecordType.thermo,
            superseded_record_id=older.id,
            superseding_record_id=newer.id,
            actor=curator,
            reason=f"refit {older.id} -> {newer.id}",
        )
    db_session.flush()

    ledger = get_release_selections(db_session, draft_release.tag)
    row = next(
        rec for rec in ledger.records if rec.selection_ref == selection.public_ref
    )

    assert row.stands is True, (
        "the curator never revised their opinion; only the science moved"
    )
    assert row.supersedes_selection_ref is None
    notice = row.record_supersession
    assert notice is not None, (
        "a standing selection of a superseded record must say so"
    )
    assert notice.superseded_by == second.public_ref
    assert notice.current == third.public_ref
    assert notice.superseded_by != notice.current
    assert notice.chain_length == 2
    assert notice.reason == f"refit {first.id} -> {second.id}"

    # The two supersessions are different questions and must not be conflated.
    assert row.record_supersession is not None
    assert row.supersedes_selection_ref is None

    # And reading the ledger changed nothing about the selected record.
    assert _snapshot(db_session, first.id) == before


def test_a_selection_of_a_current_record_reports_no_notice(
    db_session, draft_release, curator, species_entry, thermo_candidates
):
    first, _second = thermo_candidates
    selection = _select_first(
        db_session, draft_release, curator, first, species_entry
    )

    ledger = get_release_selections(db_session, draft_release.tag)
    row = next(
        rec for rec in ledger.records if rec.selection_ref == selection.public_ref
    )
    assert row.record_supersession is None
