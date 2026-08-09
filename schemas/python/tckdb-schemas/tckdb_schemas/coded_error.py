"""A refusal that carries its own machine-readable code.

:class:`UploadWarning` gave the *warn* tier a ``code`` field years before
the *block* tier had one. A warning arrives as structured data with a code
a consumer can switch on; a refusal arrived as English prose, and the only
codes it carried were spelled inside the sentence — ``"Reaction is not
element-balanced (reaction_mass_balance_failed)."`` — where nothing parses
them. A client that has to tell "your geometry disagrees with your SMILES"
(fix the payload and retry) from "your reaction does not balance" (stop and
think) was left matching English substrings, which breaks the first time
somebody improves a sentence.

:class:`CodedValidationError` is the block-tier counterpart of
``UploadWarning``: the code travels as an attribute of the exception, never
as a substring of its message, so improving the prose cannot change the
contract and translating the prose cannot break it.

Why this lives in the wire package
----------------------------------
``schemas/python/tckdb-schemas/tests/test_import_boundaries.py`` forbids
``tckdb_schemas`` from importing anything under ``app``, so a wire-schema
check — the atom-map rules, the stationary-point rules — cannot reach the
backend's error type. The code a refusal reports is part of the wire
contract in the same sense a field name is, so the type belongs here and
the backend subclasses it (``app.api.error_contract.CodedValueError``).

How the code reaches a client
-----------------------------
Two paths, both typed:

* raised inside a Pydantic validator — the exception object itself is
  preserved in ``ValidationError.errors()[i]["ctx"]["error"]``, and the
  backend's ``validation_detail_code`` reads ``.code`` straight off it;
* raised from service code — the backend's ``ValueError`` handler sees the
  exception directly and reads ``.code``.

Neither path inspects the message. ``message_prefix`` exists only so that
attaching a code to an *existing* refusal can leave its published prose
byte-identical: the machine-readable ``code`` field of the response body is
new information, and no consumer already matching on the sentence is
disturbed by it.
"""

from __future__ import annotations

from typing import Any

__all__ = ["CodedValidationError"]


class CodedValidationError(ValueError):
    """A refusal carrying a stable code and machine-readable context.

    :param code: Stable ``snake_case`` identifier for *what was wrong*.
        This is the contract; the message is not.
    :param detail: Human-readable explanation, already formatted.
    :param context: Optional structured facts about the refusal.
    :param message_prefix: Whether ``str(exc)`` repeats the code ahead of
        the detail. ``True`` keeps the legacy ``"code: detail"`` shape that
        the read API's published details already carry. ``False`` makes
        ``str(exc)`` exactly *detail*, which is what an existing refusal
        gaining a code for the first time wants: the ``code`` field appears,
        the ``detail`` field does not move.
    """

    def __init__(
        self,
        code: str,
        detail: str,
        *,
        context: dict[str, Any] | None = None,
        message_prefix: bool = True,
    ) -> None:
        self.code = code
        self.detail = detail
        self.context = dict(context or {})
        super().__init__(f"{code}: {detail}" if message_prefix else detail)
