"""A parameter observation as a parser produces it: the wire shape plus what the row says about the setting.

``CalculationParameterObservation`` is the upload contract and does not carry ``statement_kind``; a typed parameter
is never a report. Parser rows do: ``requested`` for what a deck or an echoed input asks for, ``reported`` for a
value an output log prints as used.
"""

from __future__ import annotations

from app.db.models.common import ParameterStatementKind
from app.schemas.fragments.calculation import CalculationParameterObservation


class ParserObservation(CalculationParameterObservation):
    statement_kind: ParameterStatementKind = ParameterStatementKind.requested
