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

import pytest
from sqlalchemy import event

import app.services.review_queue as review_queue
from app.api.error_contract import CodedValueError
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
    make_kinetics,
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
        assert subject.chemistry.unmapped_smiles is None

    def test_a_recorded_but_unparseable_smiles_is_distinguishable_from_never_recorded(
        self, db_session
    ):
        """Review #492, finding 7: `unmapped_smiles` is the candidate's OWN
        structural SMILES, not a reaction SMILES, and it can be set while
        `formula` is still null -- a reaction-shaped string
        (``"[CH3].[H]>>C"``) is not one molecule RDKit's single-molecule
        parser will accept. The service must carry the raw value through so
        a caller can tell "recorded, but no formula could be derived" apart
        from "never recorded" -- the previous version collapsed both into
        one null `formula` field with nothing to distinguish them.
        """
        reactant = make_species(db_session, smiles="[F]")
        product = make_species(db_session, smiles="[Cl]")
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
            db_session, transition_state=ts, unmapped_smiles="[CH3].[H]>>C"
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
        assert subject.chemistry.unmapped_smiles == "[CH3].[H]>>C"


class TestReactionEntrySubjectCarriesItsEquation:
    """Review #492, finding 6: a kinetics reviewer saw "Reaction entry
    rxe_..." and nothing else -- the species side of this module named
    subjects well and the reaction side named nothing, same defect seen
    from the other direction.
    """

    def test_kinetics_groups_under_a_reaction_entry_with_its_own_equation(
        self, db_session
    ):
        reactant = make_species(db_session, smiles="[CH3]")
        product = make_species(db_session, smiles="C")
        reaction = make_chem_reaction(
            db_session, reactants=[reactant], products=[product], reversible=True
        )
        reactant_entry = make_species_entry(db_session, reactant)
        product_entry = make_species_entry(db_session, product)
        reaction_entry = make_reaction_entry(
            db_session,
            reaction=reaction,
            reactant_entries=[reactant_entry],
            product_entries=[product_entry],
        )
        kinetics = make_kinetics(db_session, reaction_entry=reaction_entry)
        _review(
            db_session, record_type=SubmissionRecordType.kinetics, record_id=kinetics.id
        )

        result = list_review_queue(
            db_session, status=RecordReviewStatus.not_reviewed, limit=50, offset=0
        )
        subject = next(
            s for s in result.subjects if s.subject_ref == reaction_entry.public_ref
        )
        assert subject.subject_type is SubmissionRecordType.reaction_entry
        assert subject.reaction is not None
        assert subject.reaction.reversible is True
        assert [p.species_entry_ref for p in subject.reaction.reactants] == [
            reactant_entry.public_ref
        ]
        assert [p.species_entry_ref for p in subject.reaction.products] == [
            product_entry.public_ref
        ]
        assert subject.reaction.reactants[0].formula == "CH3"
        assert subject.reaction.products[0].formula == "CH4"

    def test_the_reaction_entrys_own_review_row_folds_into_the_same_subject_as_its_kinetics(
        self, db_session
    ):
        """Review #492 (second round), finding 1: every reaction workflow
        also writes a review row for the reaction_entry ITSELF (two
        `RecordRef(reaction_entry)` sites in `app/workflows`), which used
        to group under `reaction` -- a second, nameless block ("Reaction
        rxn_...") repeating the SAME `rxe_...` ref the kinetics block
        already showed. `reaction_entry` is now in `_SELF_SUBJECT_TYPES`,
        so both review rows must land in ONE subject.
        """
        reactant = make_species(db_session, smiles="[OH]")
        product = make_species(db_session, smiles="O", multiplicity=1)
        reaction = make_chem_reaction(
            db_session, reactants=[reactant], products=[product], reversible=False
        )
        reactant_entry = make_species_entry(db_session, reactant)
        product_entry = make_species_entry(db_session, product)
        reaction_entry = make_reaction_entry(
            db_session,
            reaction=reaction,
            reactant_entries=[reactant_entry],
            product_entries=[product_entry],
        )
        kinetics = make_kinetics(db_session, reaction_entry=reaction_entry)
        _review(
            db_session,
            record_type=SubmissionRecordType.reaction_entry,
            record_id=reaction_entry.id,
        )
        _review(
            db_session, record_type=SubmissionRecordType.kinetics, record_id=kinetics.id
        )

        result = list_review_queue(
            db_session, status=RecordReviewStatus.not_reviewed, limit=50, offset=0
        )

        matching = [
            s for s in result.subjects if s.subject_ref == reaction_entry.public_ref
        ]
        assert len(matching) == 1, "one reaction rendered as two subjects"
        subject = matching[0]
        assert subject.subject_type is SubmissionRecordType.reaction_entry
        # Both review rows are here -- the reaction_entry's own, and the
        # kinetics record's.
        record_types = {row.record_type for row in subject.rows}
        assert record_types == {
            SubmissionRecordType.reaction_entry,
            SubmissionRecordType.kinetics,
        }
        # And it still carries its equation -- the fold-in did not cost
        # the chemistry this class's other test already covers.
        assert subject.reaction is not None
        assert result.subject_total == 1

    def test_a_subject_with_no_reaction_data_carries_no_reaction_field(
        self, db_session
    ):
        entry = make_species_entry(db_session, make_species(db_session))
        thermo = make_thermo_scalar(db_session, species_entry=entry, h298_kj_mol=1.0)
        _review(
            db_session, record_type=SubmissionRecordType.thermo, record_id=thermo.id
        )

        result = list_review_queue(
            db_session, status=RecordReviewStatus.not_reviewed, limit=50, offset=0
        )
        subject = next(s for s in result.subjects if s.subject_ref == entry.public_ref)
        assert subject.reaction is None


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


class TestRowResolutionCap:
    """Review of #492, mutation 3: ``if False:`` on the cap check passed
    all 15 previously-shipped tests -- ``TestQueryCost`` above pins a
    DIFFERENT property (query count does not grow with row count) and
    never seeds enough rows to cross any cap, so it cannot see the cap
    being disabled entirely. Monkeypatching the module's cap constant
    down to a size a test can actually seed is what makes this path
    reachable without seeding 5000+ real rows.
    """

    def test_more_rows_than_the_cap_refuses_rather_than_grouping_them(
        self, db_session, monkeypatch
    ):
        monkeypatch.setattr(review_queue, "_ROW_RESOLUTION_CAP", 2)

        for i in range(3):
            entry = make_species_entry(db_session, make_species(db_session))
            thermo = make_thermo_scalar(
                db_session, species_entry=entry, h298_kj_mol=float(i)
            )
            _review(
                db_session, record_type=SubmissionRecordType.thermo, record_id=thermo.id
            )

        with pytest.raises(CodedValueError) as excinfo:
            list_review_queue(
                db_session, status=RecordReviewStatus.not_reviewed, limit=50, offset=0
            )
        assert excinfo.value.code == "composed_search_candidate_limit_exceeded"
        # The message must not tell a curator to do the thing they already
        # did (narrow by status, when status is exactly what was asked
        # for) -- see the route's own comment on why the wording changed.
        assert "filter by status to narrow" not in str(excinfo.value)

    def test_at_or_under_the_cap_still_groups_normally(self, db_session, monkeypatch):
        monkeypatch.setattr(review_queue, "_ROW_RESOLUTION_CAP", 2)

        for i in range(2):
            entry = make_species_entry(db_session, make_species(db_session))
            thermo = make_thermo_scalar(
                db_session, species_entry=entry, h298_kj_mol=float(i)
            )
            _review(
                db_session, record_type=SubmissionRecordType.thermo, record_id=thermo.id
            )

        result = list_review_queue(
            db_session, status=RecordReviewStatus.not_reviewed, limit=50, offset=0
        )
        assert result.subject_total == 2
