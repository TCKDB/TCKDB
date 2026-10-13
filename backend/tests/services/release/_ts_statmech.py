"""Build a transition-state entry that owns approved, licensed statmech records."""

from __future__ import annotations

from app.db.models.common import (
    RecordReviewStatus,
    ScientificOriginKind,
    StatmechTreatmentKind,
    SubmissionRecordType,
)
from app.db.models.statmech import Statmech
from app.services.record_review import set_record_review_status
from app.services.submission import link_records
from tests.services.scientific_read._factories import (
    make_chem_reaction,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_transition_state,
    make_transition_state_entry,
)


def make_ts_entry(session):
    """A TS entry for ``CC -> C=C`` (reactant and product SMILES are asserted on)."""
    reactant = make_species_entry(session, species=make_species(session, smiles="CC"))
    product = make_species_entry(session, species=make_species(session, smiles="C=C"))
    reaction = make_chem_reaction(session, reactants=[reactant.species], products=[product.species])
    reaction_entry = make_reaction_entry(
        session, reaction=reaction, reactant_entries=[reactant], product_entries=[product]
    )
    ts = make_transition_state(session, reaction_entry=reaction_entry, label="TS0")
    return make_transition_state_entry(session, transition_state=ts)


def make_ts_statmech(session, ts_entry, *, external_symmetry: int = 1) -> Statmech:
    row = Statmech(
        species_entry_id=None,
        transition_state_entry_id=ts_entry.id,
        scientific_origin=ScientificOriginKind.computed,
        statmech_treatment=StatmechTreatmentKind.rrho,
        external_symmetry=external_symmetry,
        is_linear=False,
    )
    session.add(row)
    session.flush()
    return row


def approve_and_license(session, curator, submission, rows) -> None:
    """Approve each statmech row and link it to an attested deposit."""
    for row in rows:
        set_record_review_status(
            session,
            record_type=SubmissionRecordType.statmech,
            record_id=row.id,
            status=RecordReviewStatus.approved,
            actor=curator,
            note="approved for release fixture",
        )
    link_records(
        session,
        submission=submission,
        records=[(SubmissionRecordType.statmech, row.id, None) for row in rows],
    )
