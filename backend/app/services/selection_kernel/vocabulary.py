"""Plain values every selector shares: the applicability and outcome vocabularies."""

from __future__ import annotations

from enum import Enum


class Applicability(str, Enum):
    """Whether one record can supply the requested quantity.

    ``applicable``   every requirement is established.
    ``incompatible`` something is known to be wrong for this request (another phase, target,
                     direction or pressure; a domain that excludes the request; a defective fit).
    ``unsupported``  the record may hold the answer but in a form this release does not evaluate.
    ``unresolved``   a required fact was never recorded. Never guessed.
    Precedence when several apply: incompatible, unsupported, unresolved.
    """

    applicable = "applicable"
    incompatible = "incompatible"
    unsupported = "unsupported"
    unresolved = "unresolved"


class Outcome(str, Enum):
    """What a selection found over its eligible candidates.

    TCKDB carries no method-versus-method ranking, so a selection can only say that one candidate was eligible,
    that several were (and none is preferred), or that none was.
    """

    incomparable_alternatives = "incomparable_alternatives"
    sole_eligible_candidate = "sole_eligible_candidate"
    no_applicable_candidate = "no_applicable_candidate"
    #: The visible population exceeded the cap, so nothing was assessed and nothing is selected.
    bounded_search_exceeded = "bounded_search_exceeded"
