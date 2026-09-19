"""Pin every ``SubmissionRecordType`` to the table it actually names.

A mapping table cannot be checked by a test that reads the mapping to decide
what to expect -- that test agrees with any typo. So the expectation below is
written out independently, as ``__tablename__`` strings, and the two spellings
must agree.

This matters more than it looks. A wrong entry here is silent at every layer:
the lookup succeeds, the query succeeds, a row comes back. It is simply the
wrong record's row, and every consumer of
:data:`~app.services.record_models.RECORD_MODELS` inherits the error --
reproducibility assessments recorded against the wrong table, and a curator
task linking to some unrelated record's page.

Measured 2026-09-14: swapping one entry (``kinetics`` -> ``Thermo``) passed all
37 tests in ``test_record_refs.py`` and
``test_admin_machine_review_curator_tasks.py`` before this file existed.
"""

from __future__ import annotations

from app.db.models.common import SubmissionRecordType
from app.services.record_models import RECORD_MODELS

#: Written by hand from the model modules, NOT derived from ``RECORD_MODELS``.
#: Three entries do not match their enum value and are the ones a careless
#: reader gets wrong: ``reaction`` is ``chem_reaction``, ``artifact`` is
#: ``calculation_artifact``, and ``reaction_entry`` is its own table rather
#: than a flavour of ``chem_reaction``.
EXPECTED_TABLES: dict[SubmissionRecordType, str] = {
    SubmissionRecordType.species: "species",
    SubmissionRecordType.species_entry: "species_entry",
    SubmissionRecordType.conformer_group: "conformer_group",
    SubmissionRecordType.conformer_observation: "conformer_observation",
    SubmissionRecordType.reaction: "chem_reaction",
    SubmissionRecordType.reaction_entry: "reaction_entry",
    SubmissionRecordType.transition_state: "transition_state",
    SubmissionRecordType.transition_state_entry: "transition_state_entry",
    SubmissionRecordType.calculation: "calculation",
    SubmissionRecordType.statmech: "statmech",
    SubmissionRecordType.thermo: "thermo",
    SubmissionRecordType.kinetics: "kinetics",
    SubmissionRecordType.transport: "transport",
    SubmissionRecordType.network: "network",
    SubmissionRecordType.network_solve: "network_solve",
    SubmissionRecordType.applied_energy_correction: "applied_energy_correction",
    SubmissionRecordType.artifact: "calculation_artifact",
    SubmissionRecordType.molecular_property_observation: (
        "molecular_property_observation"
    ),
}


def test_every_record_type_is_mapped():
    """No member may be missing, and none may be mapped that is not a member."""
    assert set(RECORD_MODELS) == set(SubmissionRecordType)
    assert set(EXPECTED_TABLES) == set(SubmissionRecordType)


def test_each_record_type_names_its_own_table():
    """The entry-by-entry check. This is what a swapped pair fails."""
    actual = {
        record_type: model.__tablename__
        for record_type, model in RECORD_MODELS.items()
    }
    assert actual == EXPECTED_TABLES


def test_no_two_record_types_share_a_model():
    """A duplicated value is the shape a copy-paste slip takes.

    Swapping ``kinetics`` to ``Thermo`` leaves ``thermo`` mapped too, so the
    registry holds one model twice. Distinctness catches that class of error
    even where the table names alone might not.
    """
    models = list(RECORD_MODELS.values())
    assert len(set(models)) == len(models)
