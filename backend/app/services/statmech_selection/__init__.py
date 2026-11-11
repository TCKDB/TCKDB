"""Statmech selection (service layer): which supplied statistical model supports a declared use.

A statmech record is an interpretation container, not a basin, a determination or a model of the whole species.
This package qualifies *supplied* interpretations for a stated claim and lists the applicable whole recipes in
display order; it ranks none above another. It never assembles a favourable mixture of records and never computes a
partition function, a thermochemical value, a population or a rate.

"""

from app.services.statmech_selection.models import (
    ADMIN_KEY_VERSION,
    ASSESSMENT_VERSION,
    DECISION_VERSION,
    NORMALIZER_VERSION,
    SelectionBounds,
    StatmechRequest,
)
from app.services.statmech_selection.vocabulary import CONTRACT_VERSION, Outcome, Task

__all__ = [
    "ADMIN_KEY_VERSION",
    "ASSESSMENT_VERSION",
    "CONTRACT_VERSION",
    "DECISION_VERSION",
    "NORMALIZER_VERSION",
    "Outcome",
    "SelectionBounds",
    "StatmechRequest",
    "Task",
]
