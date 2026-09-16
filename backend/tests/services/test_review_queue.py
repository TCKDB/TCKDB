"""Tests for :mod:`app.services.review_queue` (task #269).

The owner rejected the flat record-review queue: two corrections on one
species render as two lines with nothing telling them apart, "applied_energy_
correction cannot be named" leaks a schema gap into a reviewer's face, and
paging by record can split one subject across a page boundary. This module
groups review rows by the record a curator actually judges as one unit --
see its docstring for the container-is-the-subject rule and the two
exceptions (``species_entry``, ``transition_state_entry``).
"""

from __future__ import annotations

from sqlalchemy import event

from app.db.models.common import (
    RecordReviewStatus,
    SubmissionRecordType,
)
from app.db.models.record_review import RecordReview
from app.services.review_queue import list_review_queue
from tests.services.scientific_read._factories import (
    make_applied_energy_correction,
    make_calculation,
    make_chem_reaction,
    make_conformer_group,
    make_conformer_observation,
    make_energy_correction_scheme,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_statmech,
    make_thermo_scalar,
    make_transition_state,
    make_transition_state_entry,
)


def _review(session, *, record_type: SubmissionRecordType, record_id: int, status=RecordReviewStatus.not_reviewed):
    row = RecordReview(record_type=record_type, record_id=record_id, status=status)
    session.add(row)
    session.flush()
    return row


class TestTwoCorrectionsCollapseToOneSubject:
    """The defect that reads as a duplicate: two corrections, one species."""

    def test_an_atom_energy_and_a_bond_additivity_correction_share_one_subject(
        self, db_session
    ):
        entry = make_species_entry(db_session, make_species(db_session))
        scheme = make_energy_correction_scheme(db_session, name="collapse_test")
        atom = make_applied_energy_correction(
            db_session, target_species_entry=entry, scheme=scheme
        )
        bond = make_applied_energy_correction(
            db_session,
            target_species_entry=entry,
            scheme=make_energy_correction_scheme(db_session, name="collapse_test_2"),
        )
        _review(
            db_session,
            record_type=SubmissionRecordType.applied_energy_correction,
            record_id=atom.id,
        )
        _review(
            db_session,
            record_type=SubmissionRecordType.applied_energy_correction,
            record_id=bond.id,
        )

        result = list_review_queue(
            db_session, status=RecordReviewStatus.not_reviewed, limit=50, offset=0
        )

        matching = [
            s
            for s in result.subjects
            if s.subject_type is SubmissionRecordType.species_entry
            and s.subject_ref == entry.public_ref
        ]
        assert len(matching) == 1, "the two corrections rendered as two subjects"
        assert len(matching[0].rows) == 2
        assert result.subject_total == 1
        assert result.record_total == 2


class TestSpeciesEntryIsAlwaysItsOwnSubject:
    """The container-is-the-subject rule's one deliberate exception."""

    def test_a_species_entrys_own_row_is_not_merged_under_its_species(self, db_session):
        species = make_species(db_session)
        entry = make_species_entry(db_session, species)
        _review(
            db_session,
            record_type=SubmissionRecordType.species_entry,
            record_id=entry.id,
        )

        result = list_review_queue(
            db_session, status=RecordReviewStatus.not_reviewed, limit=50, offset=0
        )

        subject = next(
            s for s in result.subjects if s.subject_ref == entry.public_ref
        )
        # Not grouped under "species" -- it IS the subject.
        assert subject.subject_type is SubmissionRecordType.species_entry

    def test_two_entries_of_one_species_never_merge(self, db_session):
        species = make_species(db_session)
        cis = make_species_entry(db_session, species, stereo_label="Z")
        trans = make_species_entry(db_session, species, stereo_label="E")
        _review(
            db_session, record_type=SubmissionRecordType.species_entry, record_id=cis.id
        )
        _review(
            db_session,
            record_type=SubmissionRecordType.species_entry,
            record_id=trans.id,
        )

        result = list_review_queue(
            db_session, status=RecordReviewStatus.not_reviewed, limit=50, offset=0
        )

        refs = {s.subject_ref for s in result.subjects}
        assert cis.public_ref in refs
        assert trans.public_ref in refs
        assert result.subject_total == 2, "cis- and trans- collapsed into one subject"


class TestSubjectChemistry:
    def test_species_entry_subject_carries_formula_and_facets(self, db_session):
        species = make_species(db_session, smiles="O", multiplicity=1)
        entry = make_species_entry(db_session, species, stereo_label="R")
        thermo = make_thermo_scalar(db_session, species_entry=entry, h298_kj_mol=1.0)
        _review(
            db_session, record_type=SubmissionRecordType.thermo, record_id=thermo.id
        )

        result = list_review_queue(
            db_session, status=RecordReviewStatus.not_reviewed, limit=50, offset=0
        )
        subject = next(s for s in result.subjects if s.subject_ref == entry.public_ref)
        assert subject.chemistry.formula == "H2O"
        assert subject.chemistry.multiplicity == 1
        assert subject.chemistry.stereo_label == "R"

    def test_transition_state_entry_subject_uses_its_own_unmapped_smiles(
        self, db_session
    ):
        reactant = make_species(db_session, smiles="[CH3]")
        product = make_species(db_session, smiles="C")
        reaction = make_chem_reaction(
            db_session, reactants=[reactant], products=[product]
        )
        reaction_entry = make_reaction_entry(
            db_session,
            reaction=reaction,
            reactant_entries=[make_species_entry(db_session, reactant)],
            product_entries=[make_species_entry(db_session, product)],
        )
        ts = make_transition_state(db_session, reaction_entry=reaction_entry)
        ts_entry = make_transition_state_entry(
            db_session,
            transition_state=ts,
            multiplicity=2,
            unmapped_smiles="[CH4]",
        )
        _review(
            db_session,
            record_type=SubmissionRecordType.transition_state_entry,
            record_id=ts_entry.id,
        )

        result = list_review_queue(
            db_session, status=RecordReviewStatus.not_reviewed, limit=50, offset=0
        )
        subject = next(
            s for s in result.subjects if s.subject_ref == ts_entry.public_ref
        )
        assert subject.subject_type is SubmissionRecordType.transition_state_entry
        assert subject.chemistry.multiplicity == 2
        # Not invented from the reaction it sits on -- the TS entry's own SMILES.
        assert subject.chemistry.formula is not None

    def test_transition_state_entry_with_no_smiles_reports_no_formula_honestly(
        self, db_session
    ):
        reactant = make_species(db_session, smiles="[OH]")
        product = make_species(db_session, smiles="O", multiplicity=1)
        reaction = make_chem_reaction(
            db_session, reactants=[reactant], products=[product]
        )
        reaction_entry = make_reaction_entry(
            db_session,
            reaction=reaction,
            reactant_entries=[make_species_entry(db_session, reactant)],
            product_entries=[make_species_entry(db_session, product)],
        )
        ts = make_transition_state(db_session, reaction_entry=reaction_entry)
        ts_entry = make_transition_state_entry(
            db_session, transition_state=ts, unmapped_smiles=None
        )
        _review(
            db_session,
            record_type=SubmissionRecordType.transition_state_entry,
            record_id=ts_entry.id,
        )

        result = list_review_queue(
            db_session, status=RecordReviewStatus.not_reviewed, limit=50, offset=0
        )
        subject = next(
            s for s in result.subjects if s.subject_ref == ts_entry.public_ref
        )
        assert subject.chemistry.formula is None


class TestCalculationAndConformerObservationGroupUnderTheirContainer:
    """The brief's open question: neither stands alone."""

    def test_a_calculation_groups_under_its_species_entry(self, db_session):
        entry = make_species_entry(db_session, make_species(db_session))
        calc = make_calculation(db_session, species_entry_id=entry.id)
        _review(
            db_session, record_type=SubmissionRecordType.calculation, record_id=calc.id
        )

        result = list_review_queue(
            db_session, status=RecordReviewStatus.not_reviewed, limit=50, offset=0
        )
        subject = next(s for s in result.subjects if s.subject_ref == entry.public_ref)
        assert subject.subject_type is SubmissionRecordType.species_entry
        assert len(subject.rows) == 1

    def test_a_conformer_observation_groups_under_its_conformer_group_with_no_formula(
        self, db_session
    ):
        entry = make_species_entry(db_session, make_species(db_session))
        group = make_conformer_group(db_session, entry)
        obs = make_conformer_observation(db_session, conformer_group=group)
        _review(
            db_session,
            record_type=SubmissionRecordType.conformer_observation,
            record_id=obs.id,
        )

        result = list_review_queue(
            db_session, status=RecordReviewStatus.not_reviewed, limit=50, offset=0
        )
        subject = next(s for s in result.subjects if s.subject_ref == group.public_ref)
        assert subject.subject_type is SubmissionRecordType.conformer_group
        # Honestly absent -- reaching the species would be a second container
        # hop this module deliberately does not take.
        assert subject.chemistry.formula is None


class TestPagingCountsSubjectsNotRecords:
    def test_a_subject_with_several_records_still_counts_as_one_against_limit(
        self, db_session
    ):
        # Seeded first (and so oldest -- newest-first ordering puts it on
        # page 2) so the page-1 assertion below exercises the subject with
        # TWO records, which is the behaviour under test.
        other_entry = make_species_entry(db_session, make_species(db_session))
        other_thermo = make_thermo_scalar(
            db_session, species_entry=other_entry, h298_kj_mol=3.0
        )
        _review(
            db_session,
            record_type=SubmissionRecordType.thermo,
            record_id=other_thermo.id,
        )

        entry = make_species_entry(db_session, make_species(db_session))
        thermo = make_thermo_scalar(db_session, species_entry=entry, h298_kj_mol=2.0)
        statmech = make_statmech(db_session, species_entry=entry)
        _review(
            db_session, record_type=SubmissionRecordType.thermo, record_id=thermo.id
        )
        _review(
            db_session, record_type=SubmissionRecordType.statmech, record_id=statmech.id
        )

        result = list_review_queue(
            db_session, status=RecordReviewStatus.not_reviewed, limit=1, offset=0
        )
        assert len(result.subjects) == 1
        assert result.subject_total == 2
        assert result.record_total == 3
        # The first (newest) page's one subject carries BOTH of its
        # records, not split.
        assert result.subjects[0].subject_ref == entry.public_ref
        assert len(result.subjects[0].rows) == 2


class TestOrphanRowsNeverMerge:
    def test_two_untraceable_corrections_stay_two_subjects(self, db_session):
        # A row for a record id that names nothing -- the documented
        # "review row outliving its record" case. Container resolution and
        # ref resolution both come back empty for it.
        first = _review(
            db_session,
            record_type=SubmissionRecordType.applied_energy_correction,
            record_id=999_990_001,
        )
        second = _review(
            db_session,
            record_type=SubmissionRecordType.applied_energy_correction,
            record_id=999_990_002,
        )

        result = list_review_queue(
            db_session, status=RecordReviewStatus.not_reviewed, limit=50, offset=0
        )
        orphan_subjects = [
            s
            for s in result.subjects
            if s.subject_type is SubmissionRecordType.applied_energy_correction
            and s.subject_ref is None
        ]
        assert len(orphan_subjects) == 2, "two unrelated orphans merged into one subject"
        assert {row.id for s in orphan_subjects for row in s.rows} == {
            first.id,
            second.id,
        }


class TestQueryCost:
    """The grouped resolve must not grow per row -- mirrors
    ``test_a_longer_page_does_not_cost_more_queries`` in
    ``tests/api/test_api_record_reviews.py``, at the service layer.
    """

    def test_more_subjects_costs_more_but_not_per_record(self, db_session):
        counter = iter(range(1_000_000))

        def _unique_scheme():
            return make_energy_correction_scheme(
                db_session, name=f"cost_test_{next(counter)}"
            )

        def _seed_species_with_two_records(n: int) -> None:
            for _ in range(n):
                entry = make_species_entry(db_session, make_species(db_session))
                a = make_applied_energy_correction(
                    db_session, target_species_entry=entry, scheme=_unique_scheme()
                )
                b = make_applied_energy_correction(
                    db_session,
                    target_species_entry=entry,
                    scheme=_unique_scheme(),
                )
                _review(
                    db_session,
                    record_type=SubmissionRecordType.applied_energy_correction,
                    record_id=a.id,
                )
                _review(
                    db_session,
                    record_type=SubmissionRecordType.applied_energy_correction,
                    record_id=b.id,
                )
            db_session.flush()

        def _count() -> int:
            statements = 0
            engine = db_session.connection().engine

            def _before(conn, cursor, statement, parameters, context, executemany):
                nonlocal statements
                statements += 1

            event.listen(engine, "before_cursor_execute", _before)
            try:
                list_review_queue(
                    db_session,
                    status=RecordReviewStatus.not_reviewed,
                    limit=200,
                    offset=0,
                )
            finally:
                event.remove(engine, "before_cursor_execute", _before)
            return statements

        _seed_species_with_two_records(1)
        few = _count()
        _seed_species_with_two_records(9)
        many = _count()

        # Same shape of query regardless of row count: one SELECT for the
        # rows, a fixed handful for container/ref resolution (bounded by
        # distinct types present, not row count), and one for species_entry
        # chemistry. 2 records -> 1 subject; 20 records -> 10 subjects, all
        # the same two record types, so the query count must not grow.
        assert many == few, (
            f"cost grew from {few} statements to {many} as rows grew -- "
            "something in the grouped resolve is running per row"
        )
