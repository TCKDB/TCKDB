"""The one place that says which table a ``SubmissionRecordType`` names.

``SubmissionRecordType`` is a controlled vocabulary shared by
``submission_record_link``, ``record_review``, ``record_machine_review``,
``record_reproducibility_assessment`` and the curator-task queue. Several of
those need to get from a member of that vocabulary to the ORM class it stands
for, and each one that writes its own mapping is another sixteen lines where a
single wrong entry sends a caller to the wrong table -- silently, because the
lookup still succeeds and still returns a row.

So the mapping lives here once. Consumers that care about a *subset* still
declare their own subset, but they derive the membership from a property of the
model rather than retyping the pairs (see
:mod:`app.services.record_refs`, which filters on ``public_ref``). Subsets that
encode a policy rather than a property -- which types v1 immutability covers
(``accepted_science``), which can carry a supersession notice -- stay written
out, because there the list *is* the decision.

``backend/tests/services/test_record_models.py`` pins every entry against an
independently written table of ``__tablename__`` values. Two spellings of the
same fact, which is the only way a mapping table can be checked: a test that
reads the mapping to decide what to expect would agree with any typo.
"""

from __future__ import annotations

from typing import Any

from app.db.models.calculation import Calculation, CalculationArtifact
from app.db.models.common import SubmissionRecordType
from app.db.models.energy_correction import AppliedEnergyCorrection
from app.db.models.kinetics import Kinetics
from app.db.models.network import Network
from app.db.models.network_pdep import NetworkSolve
from app.db.models.reaction import ChemReaction, ReactionEntry
from app.db.models.species import (
    ConformerGroup,
    ConformerObservation,
    Species,
    SpeciesEntry,
)
from app.db.models.statmech import Statmech
from app.db.models.thermo import Thermo
from app.db.models.transition_state import TransitionState, TransitionStateEntry
from app.db.models.transport import Transport

#: Every ``SubmissionRecordType`` member and the ORM class it names. Complete by
#: construction -- the test asserts the key set is the whole enum, so a new
#: member cannot be added without landing here.
RECORD_MODELS: dict[SubmissionRecordType, type[Any]] = {
    SubmissionRecordType.species: Species,
    SubmissionRecordType.species_entry: SpeciesEntry,
    SubmissionRecordType.conformer_group: ConformerGroup,
    SubmissionRecordType.conformer_observation: ConformerObservation,
    SubmissionRecordType.reaction: ChemReaction,
    SubmissionRecordType.reaction_entry: ReactionEntry,
    SubmissionRecordType.transition_state: TransitionState,
    SubmissionRecordType.transition_state_entry: TransitionStateEntry,
    SubmissionRecordType.calculation: Calculation,
    SubmissionRecordType.statmech: Statmech,
    SubmissionRecordType.thermo: Thermo,
    SubmissionRecordType.kinetics: Kinetics,
    SubmissionRecordType.transport: Transport,
    SubmissionRecordType.network: Network,
    SubmissionRecordType.network_solve: NetworkSolve,
    SubmissionRecordType.applied_energy_correction: AppliedEnergyCorrection,
    SubmissionRecordType.artifact: CalculationArtifact,
}

__all__ = ["RECORD_MODELS"]
